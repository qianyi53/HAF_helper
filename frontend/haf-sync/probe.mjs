// Probe: SDK in Node + node:sqlite availability
const ENDPOINT = 'https://haf-license.app.workbuddy.host'
const PK = 'wbpk_YGFKLa2QDfDKLyAjkfLitn_4FT5p3Zh7rAutBNwf3HXtriTvWPEzZ8K'

// patch global fetch to send Origin (server enforces exact Origin match)
const _fetch = globalThis.fetch.bind(globalThis)
globalThis.fetch = (url, init = {}) => {
  const h = new Headers(init.headers || {})
  if (!h.has('Origin')) h.set('Origin', ENDPOINT)
  return _fetch(url, { ...init, headers: h })
}

async function main() {
  // 1. node:sqlite
  let DatabaseSync
  try {
    ;({ DatabaseSync } = await import('node:sqlite'))
    const db = new DatabaseSync('D:\\workspace\\HAF_app\\HAF-agent\\data\\db\\haf.db', { readOnly: true })
    const n = db.prepare('SELECT COUNT(*) AS n FROM licenses').get().n
    console.log('node:sqlite OK, licenses rows =', n)
    db.close()
  } catch (e) {
    console.log('node:sqlite FAILED:', e.message)
    process.exit(2)
  }

  // 2. SDK init + read
  const { createWorkBuddyCloud } = await import('@tencent-ai/workbuddy-cloud-sdk')
  const cloud = createWorkBuddyCloud({ endpoint: ENDPOINT, publishableKey: PK })
  const { data, error, count } = await cloud.database
    .from('licenses')
    .select('license_id', { count: 'exact', head: true })
  if (error) {
    console.log('SDK read FAILED:', JSON.stringify(error))
    process.exit(3)
  }
  console.log('SDK read OK, cloud licenses count =', count)

  // 3. rpc reachability (wrong secret must fail with HAF-SYNC-401)
  const r = await cloud.database.rpc('haf_sync_apply', {
    p_table: 'licenses',
    p_rows: [],
    p_secret: 'wrong-secret-probe',
  })
  console.log('rpc wrong-secret result:', JSON.stringify(r).slice(0, 300))

  // 4. rpc with empty rows is a no-op returning 0
}
main().catch((e) => {
  console.error('PROBE ERROR:', e.message || e)
  process.exit(1)
})
