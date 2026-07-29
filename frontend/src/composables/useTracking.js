/**
 * 功能使用埋点 composable（全局单例）
 *
 * 设计要点：
 * 1. sendBeacon 异步上报，页面关闭也能发出
 * 2. 30秒批量合并上报，减少请求
 * 3. session_id 存 localStorage，匿名标识
 * 4. 只记功能名+时长，不记录敏感内容
 *
 * 用法：
 *   import { trackPageEnter, trackPageLeave, trackFeatureClick } from '../composables/useTracking'
 *   trackPageEnter('valuation')
 *   trackPageLeave('valuation')
 *   trackFeatureClick('估值查询')
 */

const SESSION_KEY = 'ia_session_id'
const QUEUE_KEY = 'ia_track_queue'
const BATCH_INTERVAL = 30000  // 30秒批量上报
const MAX_QUEUE_SIZE = 50     // 队列上限，超出立即上报

let sessionId = ''
let queue = []
let batchTimer = null
let isInitialized = false

// 页面进入时间记录（page_key -> timestamp）
const pageEnterTimes = {}

/** 获取或生成 session_id（匿名） */
function getSessionId() {
  if (sessionId) return sessionId
  try {
    sessionId = localStorage.getItem(SESSION_KEY) || ''
    if (!sessionId) {
      sessionId = 's_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
      localStorage.setItem(SESSION_KEY, sessionId)
    }
  } catch (_) {
    sessionId = 's_' + Date.now().toString(36)
  }
  return sessionId
}

/** 从队列加载（页面刷新后继续上报） */
function loadQueue() {
  try {
    const stored = sessionStorage.getItem(QUEUE_KEY)
    if (stored) {
      queue = JSON.parse(stored) || []
    }
  } catch (_) {
    queue = []
  }
}

/** 持久化队列（防刷新丢失） */
function saveQueue() {
  try {
    sessionStorage.setItem(QUEUE_KEY, JSON.stringify(queue))
  } catch (_) {
    // sessionStorage 满了就清空
    queue = []
  }
}

/** 实际上报 */
function flush() {
  if (!queue.length) return
  const events = queue.splice(0, queue.length)
  saveQueue()
  try {
    const payload = JSON.stringify({ events })
    // sendBeacon 优先，页面关闭也能发出
    if (navigator.sendBeacon) {
      const blob = new Blob([payload], { type: 'application/json' })
      const ok = navigator.sendBeacon('/api/feature-usage/track-batch', blob)
      if (ok) return
    }
    // fallback: fetch keepalive
    fetch('/api/feature-usage/track-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Session-Id': getSessionId() },
      body: payload,
      keepalive: true,
    }).catch(() => {
      // 失败放回队列下次重试
      queue.unshift(...events)
      saveQueue()
    })
  } catch (_) {
    queue.unshift(...events)
    saveQueue()
  }
}

/** 加入队列 */
function enqueue(event) {
  queue.push({ ...event, _t: Date.now() })
  saveQueue()
  if (queue.length >= MAX_QUEUE_SIZE) {
    flush()
  }
}

/** 初始化（只执行一次） */
function init() {
  if (isInitialized) return
  isInitialized = true
  getSessionId()
  loadQueue()
  // 启动批量上报定时器
  batchTimer = setInterval(flush, BATCH_INTERVAL)
  // 页面关闭时上报
  window.addEventListener('beforeunload', flush)
  window.addEventListener('pagehide', flush)
  // visibilitychange 处理移动端切后台
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
}

// ── 对外 API ──

/** 页面进入时调用 */
export function trackPageEnter(pageKey) {
  init()
  pageEnterTimes[pageKey] = Date.now()
  enqueue({
    page_key: pageKey,
    action_type: 'page_enter',
    referrer_page: document.referrer || null,
  })
}

/** 页面离开时调用，自动计算停留时长 */
export function trackPageLeave(pageKey) {
  init()
  const enterTime = pageEnterTimes[pageKey]
  const duration = enterTime ? Date.now() - enterTime : null
  delete pageEnterTimes[pageKey]
  enqueue({
    page_key: pageKey,
    action_type: 'page_leave',
    duration_ms: duration,
  })
}

/** 功能点击埋点 */
export function trackFeatureClick(featureKey, pageKey = null) {
  init()
  enqueue({
    page_key: pageKey || _guessCurrentPage(),
    feature_key: featureKey,
    action_type: 'feature_click',
  })
}

/** 功能完成埋点（如分析完成） */
export function trackFeatureComplete(featureKey, pageKey = null) {
  init()
  enqueue({
    page_key: pageKey || _guessCurrentPage(),
    feature_key: featureKey,
    action_type: 'feature_complete',
  })
}

/** 猜测当前页面（从URL hash或location） */
function _guessCurrentPage() {
  const hash = window.location.hash.replace('#/', '').replace('#', '')
  if (hash) return hash
  return 'unknown'
}

/** 手动flush（测试用） */
export function _flush() {
  flush()
}

export default {
  trackPageEnter,
  trackPageLeave,
  trackFeatureClick,
  trackFeatureComplete,
}
