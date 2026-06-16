/**
 * Axios 实例配置
 * 独立文件避免循环依赖
 */
import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

export default api
