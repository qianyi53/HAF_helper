// Probe: does the cloud data plane accept the .workbuddy.link origin?
const HOST = 'https://haf-license.app.workbuddy.host'
const LINK = 'https://haf-license.app.workbuddy.link'
const PK = 'wbpk_YGFKLa2QDfDKLyAjkfLitn_4FT5p3Zh7rAutBNwf3HXtriTvWPEzZ8K'
const _fetch = globalThis.fetch.bind(globalThis)
globalThis.fetch = (url, init = {}) => {
  const h = new Headers(init.headers || {})
  if (!h.has('Origin')) h.set('Origin', globalThis.__ORIGIN)
  return _fetch(url, { ...init, headers: h })
}
const { createWorkBuddyCloud } = await import('@tencent-ai/workbuddy-cloud-sdk')

async function tryCase(label, origin, endpoint) {
  globalThis.__ORIGIN = origin
  const cloud = createWorkBuddyCloud({ endpoint, publishableKey: PK })
  const r = await cloud.database.from('licenses').select('license_id', { count: 'exact', head: true })
  if (r.error || r.success === false) {
    console.log(`${label}: REJECTED ${JSON.stringify(r.error || r).slice(0, 180)}`)
    return false
  }
  console.log(`${label}: OK, count = ${r.count}`)
  return true
}

const a = await tryCase('A) origin=link  endpoint=host', LINK, HOST)
if (!a) await tryCase('B) origin=link  endpoint=link', LINK, LINK)
