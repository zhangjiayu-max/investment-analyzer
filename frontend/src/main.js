import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import { setupVueErrorHandler } from './services/errorMonitor'

const app = createApp(App)
// P4-14：注册 Vue 全局错误处理器（组件内未捕获错误自动上报）
setupVueErrorHandler(app)
app.mount('#app')

// 注册 Service Worker（PWA 支持）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
