import { onMounted, onActivated } from 'vue'

/** KeepAlive 初挂只加载一次；短时间返回复用数据，刷新期间合并请求。 */
export function usePageRefresh(refresh, ttl = 30000) {
  let inFlight = null
  let refreshedAt = -Infinity
  function run() {
    if (inFlight) return inFlight
    if (Date.now() - refreshedAt < ttl) return Promise.resolve()
    // refresh 同步发出核心请求，不把首屏串在辅助恢复之后。
    refreshedAt = Date.now()
    inFlight = Promise.resolve(refresh()).catch(error => {
      refreshedAt = -Infinity
      console.warn('页面刷新失败:', error)
    }).finally(() => { inFlight = null })
    return inFlight
  }
  onMounted(run)
  onActivated(run)
  return run
}
