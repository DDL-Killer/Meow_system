// 数字道场 Service Worker — 离线缓存 + PWA 安装支持
const CACHE_NAME = 'dojo-v2';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

// API 路由 — 仅透传，不缓存（动态数据每次必须从服务器拉取）
const API_PREFIXES = ['/daily-quote', '/tasks', '/chronicle', '/cultivation',
                      '/voice', '/goals', '/analytics', '/sleep'];
function isApiRequest(url) {
  const path = new URL(url).pathname;
  return API_PREFIXES.some(p => path.startsWith(p));
}

// 静态资源: 网络优先，失败时回退缓存
// API 请求: 纯网络透传，绝不缓存
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  if (isApiRequest(event.request.url)) {
    // API 请求 — 不做任何缓存，直接放行到网络
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then(response => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
