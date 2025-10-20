const CACHE_VERSION = 'myportal-static-v1';
const STATIC_CACHE = CACHE_VERSION;
const PRECACHE_URLS = [
  '/static/css/app.css',
  '/static/js/pwa.js',
  '/static/logo.svg',
  '/static/favicon.svg'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== STATIC_CACHE)
          .map((key) => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

const OFFLINE_RESPONSE = new Response(
  `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8" />` +
    `<meta name="viewport" content="width=device-width, initial-scale=1" />` +
    `<title>Offline</title>` +
    `<style>body{font-family:system-ui, sans-serif;margin:0;min-height:100vh;display:flex;` +
    `align-items:center;justify-content:center;background:#0f172a;color:#f8fafc;text-align:center;padding:2rem;}` +
    `h1{font-size:1.75rem;margin-bottom:0.5rem;}p{font-size:1rem;max-width:28rem;}` +
    `</style></head><body><main><h1>Offline</h1>` +
    `<p>The application is unavailable because your device is offline. ` +
    `Please reconnect to continue using the portal.</p></main></body></html>`,
  {
    status: 503,
    headers: {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store'
    }
  }
);

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') {
    return;
  }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => OFFLINE_RESPONSE.clone())
    );
    return;
  }

  if (request.cache === 'only-if-cached' && request.mode !== 'same-origin') {
    return;
  }

  if (PRECACHE_URLS.includes(url.pathname)) {
    event.respondWith(
      caches.match(request).then((cachedResponse) =>
        cachedResponse || fetch(request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(STATIC_CACHE).then((cache) => cache.put(request, responseClone));
          }
          return networkResponse;
        })
      )
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cachedResponse) =>
      cachedResponse || fetch(request).then((networkResponse) => {
        if (
          networkResponse &&
          networkResponse.status === 200 &&
          networkResponse.type === 'basic' &&
          request.destination !== 'document'
        ) {
          const responseClone = networkResponse.clone();
          caches.open(STATIC_CACHE).then((cache) => cache.put(request, responseClone));
        }
        return networkResponse;
      })
    )
  );
});
self.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || typeof data !== 'object') {
    return;
  }
  if (data.type === 'SKIP_WAITING') {
    self.skipWaiting();
    return;
  }
  if (data.type === 'CLEAR_CACHE') {
    event.waitUntil(
      caches.keys().then((keys) =>
        Promise.all(keys.map((key) => caches.delete(key))).then(() => {
          self.clients.matchAll().then((clients) => {
            clients.forEach((client) => client.postMessage({ type: 'CACHE_CLEARED' }));
          });
        })
      )
    );
  }
});

