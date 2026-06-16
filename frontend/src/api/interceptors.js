/**
 * API 请求/响应拦截器
 *
 * 功能：
 * - 统一错误处理（401/403/404/500）
 * - 自动注入 token（如果存在）
 * - 请求 loading 状态（可选）
 */
import api from './http'

// ── 响应拦截器：统一错误处理 ──
api.interceptors.response.use(
  (response) => {
    return response
  },
  (error) => {
    // 网络错误
    if (!error.response) {
      const msg = error.message || '网络连接失败，请检查网络'
      console.error('[API Error]', msg)
      return Promise.reject(error)
    }

    const { status, data } = error.response

    // 401 未认证
    if (status === 401) {
      console.error('[API] 401 Unauthorized')
      return Promise.reject(new Error('登录已过期，请重新登录'))
    }

    // 403 无权限
    if (status === 403) {
      console.error('[API] 403 Forbidden:', data?.detail || data?.message)
      return Promise.reject(new Error(data?.detail || data?.message || '没有操作权限'))
    }

    // 404
    if (status === 404) {
      console.error('[API] 404 Not Found')
      return Promise.reject(new Error('请求的资源不存在'))
    }

    // 5xx 服务端错误
    if (status >= 500) {
      const msg = data?.detail || data?.message || `服务器内部错误 (${status})`
      console.error(`[API] ${status} Server Error:`, msg)
      return Promise.reject(new Error(msg))
    }

    // 其他 4xx 错误
    const msg = data?.detail || data?.message || `请求失败 (${status})`
    return Promise.reject(new Error(msg))
  }
)

// ── 请求拦截器（预留 token 注入）──
api.interceptors.request.use(
  (config) => {
    // 如果后续需要 token 认证，在这里统一注入 header
    // const token = localStorage.getItem('auth_token')
    // if (token) {
    //   config.headers.Authorization = `Bearer ${token}`
    // }
    return config
  }
)

export default api
