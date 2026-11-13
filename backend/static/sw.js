/* Voksy AI — service worker кабинета (13.11.2026): приложение на рабочем столе телефона.
 * Ничего из /api и /ws не трогает: только статика кабинета.
 *  - шрифты, значки, картинки, файлы с ?v= — из кэша сразу (имя меняется при обновлении);
 *  - страницы, js и css без версии — сначала сеть (свежая версия после деплоя), без сети — из кэша.
 * Новый SW включается сразу (skipWaiting) — после деплоя старый кэш не держит старые страницы. */
var CACHE = 'voksy-app-v1';
var SHELL = [
  '/static/dashboard.html', '/static/inbox.html', '/static/start-bot.html',
  '/static/css/voksiai.css', '/static/css/fonts.css', '/static/js/sidebar.js', '/static/icons/ui.svg'
];

self.addEventListener('install', function (e) {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return Promise.all(SHELL.map(function (u) { return c.add(u).catch(function () { /* ignore */ }); }));
  }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function immutable(url) {
  return /\.(woff2?|png|jpe?g|webp|gif|ico)$/i.test(url.pathname) || url.pathname === '/static/icons/ui.svg' && url.search ||
    /[?&]v=/.test(url.search);
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== location.origin || url.pathname.indexOf('/static/') !== 0) return;
  if (/\.(mp3|mp4|webm|m4a|ogg)$/i.test(url.pathname)) return;   // медиа — напрямую (Range-запросы)

  if (immutable(url)) {
    e.respondWith(caches.match(req).then(function (hit) {
      return hit || fetch(req).then(function (res) {
        if (res.ok) { var copy = res.clone(); caches.open(CACHE).then(function (c) { c.put(req, copy); }); }
        return res;
      });
    }));
    return;
  }
  e.respondWith(fetch(req).then(function (res) {
    if (res.ok && res.type === 'basic') { var copy = res.clone(); caches.open(CACHE).then(function (c) { c.put(req, copy); }); }
    return res;
  }).catch(function () {
    return caches.match(req, { ignoreSearch: req.mode === 'navigate' }).then(function (hit) {
      return hit || caches.match('/static/dashboard.html');
    });
  }));
});
