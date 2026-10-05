#!/usr/bin/env node
/**
 * haf-sync — haf.db (SQLite) -> WorkBuddy 云数据库 增量同步
 *
 * 用法:
 *   node sync.mjs [haf.db 路径] [--dry-run] [--full] [--table=表名 ...]
 *
 *   --dry-run        只计算差异并打印计划，不写云端
 *   --full           跳过内容比对，把本地全部行 upsert 一遍（强制对账）
 *   --table=licenses 只同步指定表（可多次出现）
 *
 * 逻辑: 按 TEXT 主键做三方 diff
 *   upsert = 本地有 & (云端没有 | 内容有变化)  → rpc haf_sync_apply (DELETE+INSERT 整行替换)
 *   delete = 云端有 & 本地没有                 → rpc haf_sync_delete
 * 写通道是带密钥校验的 SECURITY DEFINER 函数，公网页面只有只读权限。
 */
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import process from 'node:process'
import { DatabaseSync } from 'node:sqlite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const cfg = JSON.parse(readFileSync(path.join(__dirname, 'config.json'), 'utf8'))

// ---------- args ----------
const args = process.argv.slice(2)
const flags = new Set(args.filter((a) => a.startsWith('--') && !a.startsWith('--table=')))
const dryRun = flags.has('--dry-run')
const full = flags.has('--full')
const onlyTables = args.filter((a) => a.startsWith('--table=')).map((a) => a.slice('--table='.length))
const dbPath = args.find((a) => !a.startsWith('--')) || cfg.db

// ---------- cloud client (headless: inject Origin) ----------
const _fetch = globalThis.fetch.bind(globalThis)
globalThis.fetch = (url, init = {}) => {
  const h = new Headers(init.headers || {})
  if (!h.has('Origin')) h.set('Origin', cfg.endpoint)
  return _fetch(url, { ...init, headers: h })
}
const { createWorkBuddyCloud } = await import('@tencent-ai/workbuddy-cloud-sdk')
const cloud = createWorkBuddyCloud({ endpoint: cfg.endpoint, publishableKey: cfg.publishableKey })

// ---------- table specs ----------
const TABLES = [
  { name: 'documents', pk: 'doc_id', intCols: ['附件数', '已解析'] },
  { name: 'licenses', pk: 'license_id', intCols: ['版本数'] },
  { name: 'license_versions', pk: 'version_id', intCols: ['页码起', '页码止', '范围行数'] },
  { name: 'changes', pk: 'change_id', intCols: [] },
  { name: 'registry_scopes', pk: 'registry_scope_id', intCols: ['源页码'] },
  { name: 'scope_items', pk: 'scope_item_id', intCols: ['源页码'] },
]
const PAGE = 500
const CHUNK_BYTES = 400 * 1024
const CHUNK_ROWS = 400
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ---------- helpers ----------
function normalize(spec, row) {
  const out = {}
  for (const [k, v] of Object.entries(row)) {
    if (v === null || v === undefined) {
      out[k] = null
    } else if (v instanceof Uint8Array) {
      throw new Error(`${spec.name}.${k}: BLOB 值不受支持 (pk 相关行请检查源数据)`)
    } else if (spec.intCols.includes(k)) {
      const n = Number(v)
      if (v === '' || Number.isNaN(n)) throw new Error(`${spec.name}.${k}: 无法转为整数: ${JSON.stringify(v)}`)
      out[k] = n
    } else {
      out[k] = String(v)
    }
  }
  return out
}

const stable = (row) => JSON.stringify(row, Object.keys(row).sort())

function readLocalRows(spec) {
  const db = new DatabaseSync(dbPath, { readOnly: true })
  try {
    const rows = db.prepare(`SELECT * FROM "${spec.name}"`).all()
    const map = new Map()
    for (const r of rows) {
      const n = normalize(spec, r)
      if (n[spec.pk] == null) throw new Error(`${spec.name}: 存在主键为空的行`)
      map.set(n[spec.pk], n)
    }
    return map
  } finally {
    db.close()
  }
}

async function readCloudRows(spec) {
  const map = new Map()
  let from = 0
  for (;;) {
    const { data, error } = await cloud.database.from(spec.name).select('*').range(from, from + PAGE - 1)
    if (error) throw new Error(`读取云端 ${spec.name} 失败: ${JSON.stringify(error).slice(0, 300)}`)
    for (const r of data || []) map.set(r[spec.pk], normalize(spec, r))
    if (!data || data.length < PAGE) break
    from += PAGE
  }
  return map
}

async function rpcRetry(fn, params, label) {
  for (let attempt = 1; ; attempt++) {
    try {
      const r = await cloud.database.rpc(fn, params)
      if (r && r.error) throw new Error(JSON.stringify(r.error).slice(0, 300))
      return r
    } catch (e) {
      if (attempt >= 3) throw new Error(`${label} 连续 3 次失败: ${e.message}`)
      const wait = 1000 * 2 ** (attempt - 1)
      console.log(`    ! ${label} 第 ${attempt} 次失败 (${e.message.slice(0, 120)})，${wait}ms 后重试`)
      await sleep(wait)
    }
  }
}

function chunkRows(rows) {
  const chunks = []
  let cur = []
  let bytes = 0
  for (const r of rows) {
    const b = Buffer.byteLength(JSON.stringify(r), 'utf8')
    if (cur.length && (bytes + b > CHUNK_BYTES || cur.length >= CHUNK_ROWS)) {
      chunks.push(cur)
      cur = []
      bytes = 0
    }
    cur.push(r)
    bytes += b
  }
  if (cur.length) chunks.push(cur)
  return chunks
}

const chunkArr = (arr, n) => Array.from({ length: Math.ceil(arr.length / n) }, (_, i) => arr.slice(i * n, (i + 1) * n))

// ---------- main ----------
const report = { ranAt: new Date().toISOString(), db: dbPath, dryRun, full, tables: {} }
let failed = false

console.log(`haf-sync @ ${report.ranAt}`)
console.log(`sqlite : ${dbPath}`)
console.log(`cloud  : ${cfg.endpoint}`)
console.log(`mode   : ${dryRun ? 'DRY-RUN' : full ? 'FULL-UPSERT' : 'INCREMENTAL'}\n`)

for (const spec of TABLES) {
  if (onlyTables.length && !onlyTables.includes(spec.name)) continue
  process.stdout.write(`== ${spec.name} ... `)
  try {
    const local = readLocalRows(spec)
    const cloudMap = await readCloudRows(spec)

    const upserts = []
    for (const [pk, row] of local) {
      const c = cloudMap.get(pk)
      if (full || !c || stable(c) !== stable(row)) upserts.push(row)
    }
    const deletes = full ? [] : [...cloudMap.keys()].filter((pk) => !local.has(pk))

    console.log(`本地 ${local.size} / 云端 ${cloudMap.size} → 新增或更新 ${upserts.length}，删除 ${deletes.length}`)
    report.tables[spec.name] = {
      local: local.size,
      cloud: cloudMap.size,
      upsert: upserts.length,
      delete: deletes.length,
      applied: 0,
      deleted: 0,
    }

    if (dryRun) continue

    const chunks = chunkRows(upserts)
    for (let i = 0; i < chunks.length; i++) {
      const r = await rpcRetry(
        'haf_sync_apply',
        { p_table: spec.name, p_rows: chunks[i], p_secret: cfg.syncSecret },
        `${spec.name} upsert 分块 ${i + 1}/${chunks.length}`
      )
      report.tables[spec.name].applied += Number(r?.data ?? 0)
      process.stdout.write(`    upsert ${i + 1}/${chunks.length} (${chunks[i].length} 行) ok\n`)
    }
    for (let i = 0; i < deletes.length; i += 500) {
      const ids = deletes.slice(i, i + 500)
      const r = await rpcRetry(
        'haf_sync_delete',
        { p_table: spec.name, p_ids: ids, p_secret: cfg.syncSecret },
        `${spec.name} delete 分块`
      )
      report.tables[spec.name].deleted += Number(r?.data ?? 0)
      process.stdout.write(`    delete ${ids.length} 个过期 id ok\n`)
    }
  } catch (e) {
    failed = true
    console.log(`FAILED: ${e.message}`)
    report.tables[spec.name] = { error: e.message }
  }
}

writeFileSync(path.join(__dirname, 'last-sync-report.json'), JSON.stringify(report, null, 2))
console.log(`\n报告已写入 last-sync-report.json`)
if (failed) {
  console.log('结果: 部分表同步失败，请检查上方日志')
  process.exit(1)
}
console.log('结果: 同步完成' + (dryRun ? '（dry-run，未写入）' : ''))
