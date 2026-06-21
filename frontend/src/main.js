import { createApp } from 'vue'
import './style.css'
import App from './App.vue'

const app = createApp(App)
app.mount('#app')

// 注册 Service Worker（PWA 支持）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
