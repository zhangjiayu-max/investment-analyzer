/** 匿名功能埋点：每个标签页一份会话，只记录页面标识、动作和可见停留。 */
const SESSION_KEY = 'ia_session_id'
const QUEUE_KEY = 'ia_track_queue'
const BATCH_INTERVAL = 30000
const MAX_QUEUE_SIZE = 50
let sessionId = ''
let queue = []
let initialized = false
let sending = false
const pages = new Map()

function getSessionId() {
  if (sessionId) return sessionId
  try { sessionId = sessionStorage.getItem(SESSION_KEY) || '' } catch (_) { /* 内存回退 */ }
  if (!sessionId) {
    sessionId = 's_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10)
    try { sessionStorage.setItem(SESSION_KEY, sessionId) } catch (_) { /* 内存回退 */ }
  }
  return sessionId
}
function saveQueue() {
  try { sessionStorage.setItem(QUEUE_KEY, JSON.stringify(queue)) } catch (_) { /* 保留内存队列 */ }
}
function retry(events) {
  queue.unshift(...events)
  saveQueue()
}
function flush() {
  if (!queue.length || sending) return
  const events = queue.splice(0, MAX_QUEUE_SIZE)
  saveQueue()
  const payload = JSON.stringify({ session_id: getSessionId(), events })
  try {
    if (navigator.sendBeacon?.('/api/feature-usage/track-batch', new Blob([payload], { type: 'application/json' }))) {
      if (queue.length) flush()
      return
    }
    sending = true
    fetch('/api/feature-usage/track-batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: payload, keepalive: true,
    }).then(async response => {
      const body = await response.json()
      const disabled = body.message === '埋点已关闭'
      if (!disabled && (!response.ok || !(body.code === 0 || body.ok === true))) throw new Error('tracking was not stored')
      sending = false
      if (disabled) { queue = []; saveQueue() }
      else if (queue.length) flush()
    }).catch(() => { sending = false; retry(events) })
  } catch (_) { sending = false; retry(events) }
}

function enqueue(event) {
  queue.push({ ...event, session_id: getSessionId() })
  saveQueue()
  if (queue.length >= MAX_QUEUE_SIZE) flush()
}
function finishSegment(pageKey, remove = false) {
  const start = pages.get(pageKey)
  if (start != null) enqueue({ page_key: pageKey, action_type: 'page_leave', duration_ms: Math.max(0, Date.now() - start) })
  if (remove) pages.delete(pageKey)
  else pages.set(pageKey, null)
}
function onHidden() {
  for (const key of pages.keys()) finishSegment(key)
  flush()
}
function init() {
  if (initialized) return
  initialized = true
  getSessionId()
  try {
    const stored = JSON.parse(sessionStorage.getItem(QUEUE_KEY) || '[]')
    queue = Array.isArray(stored) ? stored : []
  } catch (_) { queue = [] }
  setInterval(flush, BATCH_INTERVAL)
  window.addEventListener('beforeunload', onHidden)
  window.addEventListener('pagehide', onHidden)
  window.addEventListener('pageshow', () => {
    if (document.visibilityState !== 'hidden') for (const key of pages.keys()) pages.set(key, Date.now())
  })
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') onHidden()
    else for (const key of pages.keys()) if (pages.get(key) == null) pages.set(key, Date.now())
  })
}
export function trackPageEnter(pageKey) {
  init()
  if (pages.has(pageKey)) return
  pages.set(pageKey, document.visibilityState === 'hidden' ? null : Date.now())
  enqueue({ page_key: pageKey, action_type: 'page_enter' })
}
export function trackPageLeave(pageKey) {
  init()
  if (pages.has(pageKey)) finishSegment(pageKey, true)
}
function currentPage() { return Array.from(pages.keys()).at(-1) || 'unknown' }
export function trackFeatureClick(featureKey, pageKey = null) {
  init()
  enqueue({ page_key: pageKey || currentPage(), feature_key: featureKey, action_type: 'feature_click' })
}
export function trackFeatureComplete(featureKey, pageKey = null) {
  init()
  enqueue({ page_key: pageKey || currentPage(), feature_key: featureKey, action_type: 'feature_complete' })
}
export function _flush() { flush() }
export default { trackPageEnter, trackPageLeave, trackFeatureClick, trackFeatureComplete }
