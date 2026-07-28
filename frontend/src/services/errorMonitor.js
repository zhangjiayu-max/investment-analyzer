/**
 * 轻量前端错误监控 + Web Vitals 采集
 * P4-14: 不引入 Sentry 重依赖，自建上报
 *
 * 采集范围：
 * - window 'error' 事件（JS 运行时错误、资源加载失败）
 * - window 'unhandledrejection' 事件（Promise 未捕获异常）
 * - Vue app.config.errorHandler（组件内错误，由 main.js 注册）
 * - Core Web Vitals（LCP / FID / CLS）通过 PerformanceObserver
 * - 手动上报（API 错误、业务错误，由 interceptors / 组件调用）
 *
 * 上报策略：
 * - 错误：sendBeacon 优先（页面卸载不丢），降级 fetch keepalive
 * - Vitals：fetch keepalive，单条上报
 * - 失败一律 catch 忽略，绝不影响用户操作
 */

const ERROR_BUFFER_SIZE = 50
const errorBuffer = []
const vitalsBuffer = {}

// ── 上报端点 ──
const ERROR_ENDPOINT = '/api/monitor/errors'
const VITALS_ENDPOINT = '/api/monitor/vitals'

// 1. 全局错误捕获（JS 运行时错误 + 资源加载失败）
window.addEventListener('error', (event) => {
  reportError({
    type: 'js_error',
    message: event.message,
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
    stack: event.error?.stack,
    timestamp: Date.now(),
  })
})

// 2. Promise 未捕获异常
window.addEventListener('unhandledrejection', (event) => {
  reportError({
    type: 'promise_rejection',
    message: event.reason?.message || String(event.reason),
    stack: event.reason?.stack,
    timestamp: Date.now(),
  })
})

// 3. Web Vitals 采集（LCP / FID / CLS）
function collectVitals() {
  // 兼容性守卫：PerformanceObserver 不支持时静默跳过
  if (typeof PerformanceObserver === 'undefined') return

  // LCP（Largest Contentful Paint）
  try {
    new PerformanceObserver((list) => {
      const entries = list.getEntries()
      const lastEntry = entries[entries.length - 1]
      if (lastEntry) {
        vitalsBuffer.lcp = lastEntry.startTime
        reportVital('lcp', lastEntry.startTime)
      }
    }).observe({ type: 'largest-contentful-paint', buffered: true })
  } catch (e) {
    // 某些浏览器不支持该 type，忽略
  }

  // FID（First Input Delay）
  try {
    new PerformanceObserver((list) => {
      const firstInput = list.getEntries()[0]
      if (firstInput) {
        vitalsBuffer.fid = firstInput.processingStart - firstInput.startTime
        reportVital('fid', vitalsBuffer.fid)
      }
    }).observe({ type: 'first-input', buffered: true })
  } catch (e) {
    // 忽略
  }

  // CLS（Cumulative Layout Shift）
  try {
    let clsValue = 0
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) {
          clsValue += entry.value
        }
      }
      vitalsBuffer.cls = clsValue
      reportVital('cls', clsValue)
    }).observe({ type: 'layout-shift', buffered: true })
  } catch (e) {
    // 忽略
  }
}

// 4. 上报函数
function reportError(error) {
  // 入缓冲区供调试面板查询
  errorBuffer.push(error)
  if (errorBuffer.length > ERROR_BUFFER_SIZE) errorBuffer.shift()

  // sendBeacon 优先（页面卸载也能发出）；用 Blob 强制 application/json 让后端解析为 dict
  const payload = JSON.stringify(error)
  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([payload], { type: 'application/json' })
      if (navigator.sendBeacon(ERROR_ENDPOINT, blob)) return
    }
  } catch (e) {
    // sendBeacon 抛错时降级 fetch
  }
  // 降级：fetch keepalive
  fetch(ERROR_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload,
    keepalive: true,
  }).catch(() => {})
}

function reportVital(name, value) {
  const payload = JSON.stringify({ name, value })
  fetch(VITALS_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload,
    keepalive: true,
  }).catch(() => {})
}

// 5. Vue 错误处理器（供 main.js 调用）
export function setupVueErrorHandler(app) {
  app.config.errorHandler = (err, instance, info) => {
    reportError({
      type: 'vue_error',
      message: err?.message || String(err),
      stack: err?.stack,
      component: instance?.$options?.name || 'unknown',
      info,
      timestamp: Date.now(),
    })
  }
}

// 6. 手动上报（供组件 / 拦截器调用）
export function reportManualError(type, message, extra = {}) {
  reportError({ type, message, ...extra, timestamp: Date.now() })
}

// 7. 获取错误缓冲（供调试面板用）
export function getErrorBuffer() {
  return [...errorBuffer]
}

// 8. 获取 Vitals 缓冲（供调试面板用）
export function getVitalsBuffer() {
  return { ...vitalsBuffer }
}

// 启动 Vitals 采集
collectVitals()
