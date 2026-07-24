const CACHE_NAME = 'ia-v4'
const STATIC_ASSETS = ['/', '/index.html', '/favicon.svg']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  )
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  )
  self.clients.claim()
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  // 只缓存 GET 请求，跳过 API 和 SSE
  if (request.method !== 'GET') return
  if (request.url.includes('/api/')) return
  if (request.headers.get('Accept')?.includes('text/event-stream')) return

  // HTML 文档：network-first（确保用户拿到最新版本，避免 SW 缓存旧 JS）
  if (request.destination === 'document') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const clone = response.clone()
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone))
          }
          return response
        })
        .catch(() => caches.match(request).then((cached) => cached || new Response('Offline', { status: 503 })))
    )
    return
  }

  // 带 hash 的静态资源（JS/CSS）：stale-while-revalidate
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const clone = response.clone()
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone))
        }
        return response
      })
      .catch(() => {
        return caches.match(request).then((cached) => cached || new Response('Offline', { status: 503 }))
      })
  )
})
