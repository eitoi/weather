/* Service worker: app shell cache-first, forecast data network-first with offline fallback. */
const SHELL = 'wx-shell-v3', DATA = 'wx-data-v1', TILES = 'wx-tiles-v1';
const SHELL_FILES = ['./', 'index.html', 'style.css?v=3', 'js/util.js?v=3', 'js/charts.js?v=3', 'js/point.js?v=3', 'js/outlook.js?v=3', 'js/launches.js?v=3', 'launches.json', 'js/app.js?v=3',
  'manifest.webmanifest', 'icons/icon-192.png', 'icons/apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => ![SHELL, DATA, TILES].includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.origin === location.origin && url.pathname.includes('/data/')) {
    // network first; keep the latest copy for offline use (keyed without the ?v= cache-buster)
    const key = url.origin + url.pathname;
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) { const copy = r.clone(); caches.open(DATA).then(c => c.put(key, copy)); }
      return r;
    }).catch(() => caches.open(DATA).then(c => c.match(key)).then(r => r || Response.error())));
    return;
  }
  if (url.origin === location.origin) {
    // app shell: stale-while-revalidate
    e.respondWith(caches.open(SHELL).then(async c => {
      const hit = await c.match(e.request);
      const net = fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }
  // map tiles & CDN: cache-first, opportunistic
  if (/arcgisonline|opentopomap|cdnjs/.test(url.host)) {
    e.respondWith(caches.open(TILES).then(async c => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const r = await fetch(e.request);
      if (r.ok || r.type === 'opaque') {
        c.put(e.request, r.clone());
        c.keys().then(k => { if (k.length > 3000) k.slice(0, 500).forEach(q => c.delete(q)); });  // keep the cache bounded
      }
      return r;
    }));
  }
});
