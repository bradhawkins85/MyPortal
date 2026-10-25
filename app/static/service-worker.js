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
  // Soft releases never prompt, so nothing would ever post SKIP_WAITING and
  // an installed phone app kept the old worker for as long as it stayed open.
  if (['none', 'soft'].includes(manifest.compatibility)) await self.skipWaiting();
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
    // Pages request assets as ?v=<content hash>, so only an exact match holds
    // the right bytes. Ignoring the query here served an old layout.js or
    // app.css to a new page until the worker was replaced, which on an
    // installed phone app could be indefinitely.
    const cached = await caches.match(request);
    if (cached) return cached;
    let response;
    try {
      response = await fetch(request);
    } catch (error) {
      // Offline: any cached revision beats a broken page.
      const fallback = await caches.match(request, { ignoreSearch: true });
      if (fallback) return fallback;
      throw error;
    }
    // Unversioned URLs may change under the same name, so leave them to HTTP.
    if (response.ok && response.type === 'basic' && url.searchParams.has('v')) {
      const copy = response.clone();
      event.waitUntil(loadManifest()
        .then((manifest) => caches.open(`${CACHE_PREFIX}${manifest.release}`))
        .then((cache) => cache.put(request, copy))
        .catch(() => { /* caching is best effort; the page already has its response */ }));
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
