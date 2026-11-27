/* Voksy AI — service worker кабинета: приложение на рабочем столе телефона и мгновенные переходы.
 * Ничего из /api и /ws не трогает: только статика кабинета.
 *
 * 18.11.2026 (ветка 1811-niche-admin-speed) — переходы между вкладками без ожидания сети:
 *  - страницы кабинета (HTML одинаковый для всех, данные приходят из /api) — «сначала из кэша»:
 *    показываем сохранённую страницу сразу, свежую тянем в фоне и кладём на следующий раз;
 *    старше 5 дней или нет в кэше — сначала сеть;
 *  - скрипты и стили с меткой содержимого ?v=h<хэш> (её ставит сервер в HTML), шрифты и
 *    картинки — из кэша без сети: метка меняется вместе с файлом;
 *  - остальная статика без метки — из кэша сразу + обновление в фоне;
 *  - без сети всё открывается из кэша (кабинет работает офлайн на уровне интерфейса).
 * Новый SW включается сразу (skipWaiting); при изменении логики кэша поднимайте CACHE. */
var CACHE = 'voksy-app-v5';   // v5 (26.11.2026): новый экран входа и выход не на сайт (приложение); v4 — push ИИ-управляющего; v3 — телефон, «Ещё», финансы
var PAGE_MAX_AGE = 5 * 24 * 3600 * 1000;
var SHELL = [
  '/static/dashboard.html', '/static/inbox.html', '/static/start-bot.html', '/static/agent.html',
  '/static/booking.html', '/static/start-crm.html', '/static/analytics.html', '/static/finance.html', '/static/orders.html',
  '/static/css/voksiai.css', '/static/css/fonts.css', '/static/js/sidebar.js', '/static/js/ui.js',
  '/static/js/i18n.js', '/static/icons/ui.svg'
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

function contentVersioned(url) { return /(^|[?&])v=h[0-9a-f]{10}(&|$)/.test(url.search.replace(/^\?/, '')); }
function immutable(url) {
  return contentVersioned(url) || /\.(woff2?|png|jpe?g|webp|gif|ico)$/i.test(url.pathname) ||
    /\/assets\/[^/]+-[0-9A-Za-z_-]{8,}\.(js|css)$/.test(url.pathname);   // бандл лендинга с хэшем в имени
}
function isPage(req, url) { return req.mode === 'navigate' || /\.html$/i.test(url.pathname); }
function pageKey(url) { return url.origin + url.pathname; }   // ?contact=…, #catalog — читает сама страница

function put(key, res) {
  if (!res || !res.ok || res.type !== 'basic') return;
  var copy = res.clone();
  caches.open(CACHE).then(function (c) { c.put(key, copy); });
}
function fresh(res) {
  var d = res && res.headers.get('date');
  return !d || (Date.now() - new Date(d).getTime()) < PAGE_MAX_AGE;
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== location.origin || url.pathname.indexOf('/static/') !== 0) return;
  if (url.pathname === '/static/sw.js') return;
  if (/\.(mp3|mp4|webm|m4a|ogg)$/i.test(url.pathname)) return;   // медиа — напрямую (Range-запросы)

  if (immutable(url)) {
    e.respondWith(caches.match(req).then(function (hit) {
      return hit || fetch(req).then(function (res) { put(req, res); return res; });
    }));
    return;
  }

  if (isPage(req, url)) {
    var key = pageKey(url);
    e.respondWith(caches.match(key).then(function (hit) {
      var net = fetch(req).then(function (res) { put(key, res); return res; });
      if (hit && fresh(hit)) {
        e.waitUntil(net.catch(function () { /* офлайн — останется сохранённая */ }));
        return hit;
      }
      return net.catch(function () { return hit || caches.match('/static/dashboard.html'); });
    }));
    return;
  }

  // Статика без метки версии: сразу из кэша, свежая — в фоне
  e.respondWith(caches.match(req).then(function (hit) {
    var net = fetch(req).then(function (res) { put(req, res); return res; });
    if (hit) { e.waitUntil(net.catch(function () { /* ignore */ })); return hit; }
    return net;
  }));
});

// ── Push-уведомления (Voksy Beauty 1.6б): утренний отчёт ИИ-управляющего, «нужно ваше одобрение»,
// низкая оценка визита. Сервер шлёт JSON {title, body, url, tag} (services/push_service.py).
self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : '' }; }
  var title = d.title || 'Voksy AI';
  e.waitUntil(self.registration.showNotification(title, {
    body: d.body || '',
    tag: d.tag || 'voksy',
    renotify: true,
    icon: '/static/android-chrome-192x192.png',
    badge: '/static/android-chrome-192x192.png',
    data: { url: d.url || '/static/dashboard.html' }
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/static/dashboard.html';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c.url.indexOf('/static/') !== -1 && 'focus' in c) {
        if ('navigate' in c) { try { c.navigate(url); } catch (err) { /* ignore */ } }
        return c.focus();
      }
    }
    return self.clients.openWindow ? self.clients.openWindow(url) : null;
  }));
});
