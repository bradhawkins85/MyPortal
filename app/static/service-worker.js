const CACHE_VERSION = 'myportal-static-v2';
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
    `<style>body{font-family:system-ui,sans-serif;margin:0;min-height:100vh;display:flex;` +
    `align-items:center;justify-content:center;background:#0f172a;color:#f8fafc;text-align:center;padding:2rem;}` +
    `h1{font-size:1.75rem;margin-bottom:0.5rem;}p{font-size:1rem;max-width:32rem;margin:0 auto 1rem auto;}` +
    `.retry{opacity:0.8;font-size:0.95rem;}` +
    `</style></head><body><main><h1>Offline</h1>` +
    `<p id="offline-status-detail">The portal is restarting or temporarily unavailable. ` +
    `This page will reload automatically once service is restored.</p>` +
    `<p class="retry">If this message does not disappear, refresh the page or check your connection.</p>` +
    `<script>(function(){var d=4000;var m=15;var a=0;var t=null;function u(msg){try{var el=document.getElementById('offline-status-detail');` +
    `if(el){el.textContent=msg;}}catch(e){}}function p(){a+=1;fetch(window.location.href,{method:'GET',cache:'no-store',redirect:'follow'})` +
    `.then(function(resp){if(resp&&resp.ok){window.location.reload();return;}if(a<m){t=setTimeout(p,d);}else{u('Still waiting for the portal to restart…');}})` +
    `.catch(function(){if(a<m){t=setTimeout(p,d);}else{u('Still waiting for the portal to restart…');}});}t=setTimeout(p,d);})();</script>` +
    `</main></body></html>`,
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
    event.respondWith(handleNavigationRequest(request));
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

async function handleNavigationRequest(request) {
  try {
    const response = await fetch(request);
    return response;
  } catch (error) {
    return OFFLINE_RESPONSE.clone();
  }
}

