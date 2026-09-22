/* Release: __RELEASE_REVISION__ (injected by /service-worker.js). */
const MANIFEST_URL = '/release-manifest.json';
const CACHE_PREFIX = 'myportal-static-';
let activeCache = null;

async function loadManifest() {
  const response = await fetch(MANIFEST_URL, { cache: 'no-store' });
  if (!response.ok) throw new Error('Release manifest unavailable');
  return response.json();
}

async function installRelease() {
  const manifest = await loadManifest();
  activeCache = `${CACHE_PREFIX}${manifest.release}`;
  const cache = await caches.open(activeCache);
  const assets = Object.entries(manifest.assets || {}).map(([url, revision]) =>
    `${url}${url.includes('?') ? '&' : '?'}v=${encodeURIComponent(revision)}`
  );
  await cache.addAll(assets);
}

self.addEventListener('install', (event) => event.waitUntil(installRelease()));

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    if (!activeCache) {
      const manifest = await loadManifest();
      activeCache = `${CACHE_PREFIX}${manifest.release}`;
    }
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key.startsWith(CACHE_PREFIX) && key !== activeCache)
      .map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

const OFFLINE_RESPONSE = new Response(
  '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">' +
  '<title>Offline</title><body><main><h1>Portal unavailable</h1>' +
  '<p>Your work remains in this browser. Reconnect, then try again.</p></main></body></html>',
  { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } }
);

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') {
    event.respondWith(handleNavigationRequest(request));
    return;
  }
  if (!url.pathname.startsWith('/static/')) return;
  event.respondWith((async () => {
    const cached = await caches.match(request, { ignoreSearch: true });
    if (cached) return cached;
    const response = await fetch(request);
    if (response.ok && response.type === 'basic') {
      const manifest = await loadManifest();
      const cache = await caches.open(`${CACHE_PREFIX}${manifest.release}`);
      event.waitUntil(cache.put(request, response.clone()));
    }
    return response;
  })());
});

self.addEventListener('message', (event) => {
  if (!event.origin || event.origin !== self.location.origin || !event.data) return;
  if (event.data.type === 'SKIP_WAITING') self.skipWaiting();
  if (event.data.type === 'CLEAR_CACHE') {
    event.waitUntil(caches.keys().then((keys) => Promise.all(keys.map((key) => caches.delete(key)))));
  }
});

async function handleNavigationRequest(request) {
  try {
    return await fetch(request);
  } catch (_) {
    try {
      const status = await fetch('/upgrade-status', { cache: 'no-store' });
      if (status.ok && (await status.json()).maintenance) {
        return (await caches.match('/static/upgrade.html', { ignoreSearch: true })) || OFFLINE_RESPONSE.clone();
      }
    } catch (_) { /* offline */ }
    return OFFLINE_RESPONSE.clone();
  }
}
