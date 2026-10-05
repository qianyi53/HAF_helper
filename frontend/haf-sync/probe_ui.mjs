// Probe: verify new view columns + frontend query patterns via PostgREST
const ENDPOINT = 'https://haf-license.app.workbuddy.host'
const PK = 'wbpk_YGFKLa2QDfDKLyAjkfLitn_4FT5p3Zh7rAutBNwf3HXtriTvWPEzZ8K'
const _fetch = globalThis.fetch.bind(globalThis)
globalThis.fetch = (url, init = {}) => {
  const h = new Headers(init.headers || {})
  if (!h.has('Origin')) h.set('Origin', ENDPOINT)
  return _fetch(url, { ...init, headers: h })
}
const { createWorkBuddyCloud } = await import('@tencent-ai/workbuddy-cloud-sdk')
const cloud = createWorkBuddyCloud({ endpoint: ENDPOINT, publishableKey: PK })

async function main() {
  // 1. new columns present?
  const r1 = await cloud.database.from('view_device_index')
    .select('scope_item_id,license_id,设备种类,设备类别,设备品种,核安全级别,能力特征参数,持证单位,持证单位所在国,证字号,证件类型,许可证状态,数据生效日期,官网链接,附件链接,附件页码')
    .eq('设备类别', '阀门').limit(2)
  console.log('1) view cols:', r1.error ? 'ERR ' + JSON.stringify(r1.error).slice(0, 200) : JSON.stringify(r1.data?.[0]).slice(0, 400))

  // 2. or-ilike search pattern (device search)
  const r2 = await cloud.database.from('view_device_index')
    .select('scope_item_id,设备品种,持证单位,许可证状态')
    .or('设备种类.ilike.*阀*,设备类别.ilike.*阀*,设备品种.ilike.*阀*,持证单位.ilike.*阀*')
    .limit(3)
  console.log('2) or-ilike:', r2.error ? 'ERR ' + JSON.stringify(r2.error).slice(0, 300) : `ok ${r2.data.length} rows, e.g. ${JSON.stringify(r2.data[0]).slice(0, 200)}`)

  // 3. changes by license
  const licId = r1.data?.[0]?.license_id
  const r3 = await cloud.database.from('changes').select('change_id,变更日期,变更类型,变更摘要,官网链接,附件页码').eq('license_id', licId).order('变更日期', { ascending: false })
  console.log(`3) changes(${licId}):`, r3.error ? 'ERR ' + JSON.stringify(r3.error).slice(0, 200) : `${r3.data.length} rows`)

  // 4. licenses ilike (unit search)
  const r4 = await cloud.database.from('licenses').select('license_id,单位名称,状态,证件类型').ilike('单位名称', '%阀门%').limit(3)
  console.log('4) unit ilike:', r4.error ? 'ERR ' + JSON.stringify(r4.error).slice(0, 200) : `ok ${r4.data.length}: ${JSON.stringify(r4.data[0])}`)

  // 5. view by license_id (unit detail device items)
  if (licId) {
    const r5 = await cloud.database.from('view_device_index').select('scope_item_id,设备类别,设备品种,核安全级别').eq('license_id', licId)
    console.log(`5) view by license(${licId}):`, r5.error ? 'ERR ' + JSON.stringify(r5.error).slice(0, 200) : `${r5.data.length} items`)
  }

  // 6. chips query + data freshness
  const r6 = await cloud.database.from('view_device_index').select('设备类别').limit(3000)
  console.log('6) chips fetch:', r6.error ? 'ERR' : `ok ${r6.data.length} rows`)
  const r7 = await cloud.database.from('documents').select('爬取时间').order('爬取时间', { ascending: false }).limit(1)
  console.log('7) latest crawl:', r7.error ? 'ERR' : JSON.stringify(r7.data))
}
main().catch((e) => { console.error('PROBE ERROR:', e.message || e); process.exit(1) })
