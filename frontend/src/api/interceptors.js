/**
 * API 请求/响应拦截器
 *
 * 功能：
 * - 标准协议解包（{code, message, data} → data）
 * - 统一错误处理（401/403/404/422/500）
 * - 自动注入 token（如果存在）
 */
import api from './http'

// ── 响应拦截器：标准协议解包 + 统一错误处理 ──
api.interceptors.response.use(
  (response) => {
    // 标准协议解包：{code, message, data} → data
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) {
        // 成功：解包 data，组件代码无感知
        response.data = body.data
        return response
      } else {
        // 业务失败（HTTP 200 但 code != 0）
        const msg = body.message || '操作失败'
        console.error('[API] Business error:', body.code, msg)
        return Promise.reject(new Error(msg))
      }
    }
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

    // 标准协议错误：{code, message, data}
    if (data && typeof data === 'object' && 'code' in data) {
      const msg = data.message || `请求失败 (${status})`
      console.error(`[API] ${status} Error:`, msg)
      return Promise.reject(new Error(msg))
    }

    // 兼容旧格式（过渡期）
    const msg = data?.detail || data?.message || data?.error || data?.msg || `请求失败 (${status})`

    // 401 未认证
    if (status === 401) {
      console.error('[API] 401 Unauthorized')
      return Promise.reject(new Error('登录已过期，请重新登录'))
    }

    // 403 无权限
    if (status === 403) {
      console.error('[API] 403 Forbidden:', msg)
      return Promise.reject(new Error(msg || '没有操作权限'))
    }

    // 404
    if (status === 404) {
      console.error('[API] 404 Not Found')
      return Promise.reject(new Error('请求的资源不存在'))
    }

    // 5xx 服务端错误
    if (status >= 500) {
      console.error(`[API] ${status} Server Error:`, msg)
      return Promise.reject(new Error(msg))
    }

    // 其他 4xx 错误
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
