const CACHE_NAME = 'ia-v2'
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

  event.respondWith(
    fetch(request)
      .then((response) => {
        // 缓存成功的静态资源
        if (response.ok && (request.destination === 'document' || request.destination === 'script' || request.destination === 'style' || request.destination === 'image')) {
          const clone = response.clone()
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone))
        }
        return response
      })
      .catch(() => {
        // 离线时返回缓存
        return caches.match(request).then((cached) => cached || new Response('Offline', { status: 503 }))
      })
  )
})
