/**
 * Страница салона (Voksy Beauty, шаг 1.5).
 *
 * Адреса (сервер отдаёт один salon.html, подставляет <head> и data-mode):
 *   /beauty/<slug>, /p/<slug> — витрина: обложка, логотип, «открыто до…», связь (звонок, WhatsApp,
 *     Instagram, Telegram, маршрут, сайт), акции, услуги с ценами, мастера с работами, галерея,
 *     о салоне, удобства, часы работы, контакты. Запись — шторка поверх страницы.
 *   /b/<slug> — только запись (QR, ссылка из бота, кнопка-виджет на сайте: ?embed=1 внутри окна
 *     booking-widget.js — «Готово» закрывает окно через postMessage).
 * Запись по шагам: услуги (можно несколько — визит подряд) → мастер или «любой» → день и время
 * (дни без мест приглушены) → имя и телефон → «Вы записаны» (+ в календарь, WhatsApp салона).
 * Глубокие ссылки: ?service=<id>, ?master=<id>, #book — сразу открыть запись.
 * Данные: GET /api/public/booking/<slug> (+ salon), /slots (до 14 дней), POST /<slug> (одна услуга),
 * POST /<slug>/visit-slots и /<slug>/visit (несколько). Без входа, без скриптов кабинета.
 */
(function () {
  'use strict';
  var TZ = 'Asia/Bishkek';
  var WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
  var WDN = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
  var MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var MONN = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  var MODE = document.body.getAttribute('data-mode') || 'page';
  var EMBED = /[?&]embed=1/.test(location.search) && window.parent !== window;
  var QS = new URLSearchParams(location.search);
  var slug = (location.pathname.split('/').filter(Boolean)[1] || '').toLowerCase();
  var API = '/api/public/booking/' + encodeURIComponent(slug);
  var app = document.getElementById('app');
  var D = null, SAL = null;          // ответ API и витрина салона
  if (EMBED) document.body.classList.add('embed');

  // ── значки (Lucide, без спрайта: страница самостоятельная) ──
  var P = {
    calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    phone: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    wa: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    ig: '<rect x="2" y="2" width="20" height="20" rx="5"/><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"/><path d="M17.5 6.5h.01"/>',
    tg: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    pin: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    route: '<polygon points="3 11 22 2 13 21 11 13 3 11"/>',
    share: '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M16 6l-4-4-4 4M12 2v13"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    left: '<path d="m15 18-6-6 6-6"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    plus: '<path d="M5 12h14M12 5v14"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2m-7.07-14.07 1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    sunset: '<path d="M12 10V2M5.22 11.22 3.81 9.81M2 18h2m16 0h2m-1.81-8.19-1.41 1.41M22 22H2M8 6l4 4 4-4M16 18a4 4 0 0 0-8 0"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20M2 12h20"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5M12 15V3"/>',
    wallet: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
    wifi: '<path d="M12 20h.01M2 8.82a15 15 0 0 1 20 0M5 12.86a10 10 0 0 1 14 0M8.5 16.43a5 5 0 0 1 7 0"/>',
    car: '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>',
    card: '<rect width="20" height="14" x="2" y="5" rx="2"/><path d="M2 10h20"/>',
    qr: '<rect width="5" height="5" x="3" y="3" rx="1"/><rect width="5" height="5" x="16" y="3" rx="1"/><rect width="5" height="5" x="3" y="16" rx="1"/><path d="M21 16h-3a2 2 0 0 0-2 2v3M21 21v.01M12 7v3a2 2 0 0 1-2 2H7M3 12h.01M12 3h.01M12 16v.01M16 12h1M21 12v.01M12 21v-1"/>',
    coffee: '<path d="M17 8h1a4 4 0 1 1 0 8h-1"/><path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"/><path d="M6 2v2M10 2v2M14 2v2"/>',
    smile: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    wind: '<path d="M12.8 19.6A2 2 0 1 0 14 16H2M17.5 8a2.5 2.5 0 1 1 2 4H2M9.8 4.4A2 2 0 1 1 11 8H2"/>',
    heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
    scissors: '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
    access: '<circle cx="16" cy="4" r="1"/><path d="m18 19 1-7-6 1M5 8l3-3 5.5 3-2.36 3.5M4.24 14.5a5 5 0 0 0 6.88 6M13.76 17.5a5 5 0 0 0-6.88-6"/>',
    gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5"/>',
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    sparkles: '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.13-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.13a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.13 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.13a.5.5 0 0 1-.96 0z"/>'
  };
  var AMEN_IC = { wifi: 'wifi', parking: 'car', card: 'card', qr: 'qr', coffee: 'coffee', kids: 'smile', sterile: 'shield',
    aircon: 'wind', women: 'heart', men: 'scissors', access: 'access', gift: 'gift' };
  function ic(n) { return '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">' + (P[n] || '') + '</svg>'; }

  // ── мелочи ──
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function num(v) { return Number(v).toLocaleString('ru-RU'); }
  function money(v) { return v == null ? '' : num(Math.round(Number(v))) + ' сом'; }
  function durText(m) { m = +m || 0; var h = Math.floor(m / 60), r = m % 60; return h ? h + ' ч' + (r ? ' ' + r + ' мин' : '') : r + ' мин'; }
  function keyOf(d) { return d.toLocaleDateString('en-CA', { timeZone: TZ }); }
  function parseKey(k) { var p = k.split('-'); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2], 6)); }
  function addDays(k, n) { var d = parseKey(k); d.setUTCDate(d.getUTCDate() + n); return keyOf(d); }
  function hm(iso) { return new Date(iso).toLocaleTimeString('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }); }
  function dayLong(k) { var d = parseKey(k); return WD[d.getUTCDay()] + ', ' + d.getUTCDate() + ' ' + MON[d.getUTCMonth()]; }
  function digits(p) { return String(p || '').replace(/\D/g, ''); }
  function fmtPhone(p) {
    var d = digits(p);
    if (d.length === 12 && d.indexOf('996') === 0) return '+996 ' + d.slice(3, 6) + ' ' + d.slice(6, 9) + ' ' + d.slice(9);
    return p ? (String(p).charAt(0) === '+' ? p : '+' + d) : '';
  }
  function years(n) { var a = n % 10, b = n % 100; return n + ' ' + (a === 1 && b !== 11 ? 'год' : a >= 2 && a <= 4 && (b < 12 || b > 14) ? 'года' : 'лет'); }
  function plural(n, one, few, many) { var a = Math.abs(n) % 100, b = a % 10; return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many; }
  function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem(k) || 'null'); localStorage.setItem(k, JSON.stringify(v)); } catch (e) { return null; } }
  function get(path) {
    return fetch(API + path, { cache: 'no-store' }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) { var e = new Error(d.detail || 'Не получилось загрузить'); e.status = r.status; throw e; } return d; });
    });
  }
  function post(path, body) {
    return fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { var e = new Error(r.status === 429 ? 'Слишком много записей с этого устройства — попробуйте позже или позвоните нам.' : (typeof d.detail === 'string' ? d.detail : 'Не получилось записаться')); e.status = r.status; throw e; }
        return d;
      });
    });
  }
  var toastT;
  function toast(msg) {
    var t = document.querySelector('.toast');
    if (!t) { t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.textContent = msg; t.classList.add('on');
    clearTimeout(toastT); toastT = setTimeout(function () { t.classList.remove('on'); }, 2600);
  }

  // ── данные салона ──
  function services() { return (D && D.services) || []; }
  function masters() { return (D && D.resources) || []; }
  function svcById(id) { return services().filter(function (s) { return s.id === id; })[0] || null; }
  function mById(id) { return masters().filter(function (m) { return m.id === id; })[0] || null; }
  function does(m, svc) { return !svc || !m.service_ids || m.service_ids.indexOf(svc.id) >= 0; }
  function mastersFor(svc) { return masters().filter(function (m) { return does(m, svc); }); }
  function terms(svc, m) {
    var own = (m && svc && m.service_prices && m.service_prices[svc.id]) || {};
    return { price: own.price != null ? own.price : (svc ? svc.price : null), dur: own.duration_min || (svc ? svc.duration_min : 60) };
  }
  function rangeText(lo, hi) { return hi != null && hi > lo ? num(lo) + '–' + num(hi) + ' сом' : money(lo); }
  function priceFrom(svc) {
    if (!SAL.show_prices || !svc) return null;
    var ps = [];
    mastersFor(svc).forEach(function (m) { var t = terms(svc, m); if (t.price != null) ps.push(+t.price); });
    if (!ps.length && svc.price != null) ps.push(+svc.price);
    return ps.length ? Math.min.apply(null, ps) : null;
  }
  function priceText(svc) {
    if (!SAL.show_prices || !svc) return '';
    var ps = [];
    mastersFor(svc).forEach(function (m) { var t = terms(svc, m); if (t.price != null) ps.push(+t.price); });
    if (!ps.length && svc.price != null) ps.push(+svc.price);
    if (!ps.length) return '';
    var lo = Math.min.apply(null, ps), hi = Math.max.apply(null, ps);
    if (svc.price_to != null && svc.price_to > hi) return (lo === hi ? '' : 'от ') + rangeText(lo, svc.price_to);
    return lo === hi ? money(lo) : 'от ' + money(lo);
  }
  function priceWith(svc, m) {
    if (!SAL.show_prices || !svc) return '';
    if (!m) return priceText(svc);
    var t = terms(svc, m);
    if (t.price == null) return '';
    var own = m.service_prices && m.service_prices[svc.id] && m.service_prices[svc.id].price != null;
    return own ? money(t.price) : rangeText(t.price, svc.price_to);
  }
  function ava(m, cls) {
    return '<span class="ava ' + (cls || '') + '" style="--mc:' + esc(m.color || '') + '">' + (m.photo_url ? '<img src="' + esc(m.photo_url) + '" alt="" loading="lazy">' : esc(m.initials || '?')) + '</span>';
  }

  // ── «открыто сейчас» по часам салона (время Бишкека) ──
  function nowLocal() {
    var p = {};
    new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
      .formatToParts(new Date()).forEach(function (x) { p[x.type] = x.value; });
    var idx = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(p.weekday);
    return { day: idx < 0 ? 0 : idx, min: (+p.hour % 24) * 60 + +p.minute };
  }
  function toMin(t) { var p = String(t || '0:0').split(':'); return +p[0] * 60 + +p[1]; }
  function openState() {
    var h = SAL.hours || [];
    if (!h.some(function (x) { return x.open; })) return null;
    var n = nowLocal(), t = h[n.day];
    if (t && t.open && n.min >= toMin(t.open) && n.min < toMin(t.close)) return { on: true, text: 'Открыто до ' + t.close };
    if (t && t.open && n.min < toMin(t.open)) return { on: false, text: 'Откроется сегодня в ' + t.open };
    for (var i = 1; i <= 7; i++) {
      var x = h[(n.day + i) % 7];
      if (x && x.open) return { on: false, text: 'Откроется ' + (i === 1 ? 'завтра' : 'в ' + x.title) + ' в ' + x.open };
    }
    return null;
  }

  // ═════════════════════════════ витрина ═════════════════════════════
  function gallery() {
    var out = (SAL.gallery || []).map(function (g) { return { url: g.url, cap: g.caption || '' }; });
    masters().forEach(function (m) {
      (m.portfolio || []).forEach(function (it) { out.push({ url: it.url, cap: [it.caption, m.name].filter(Boolean).join(' · ') }); });
    });
    return out;
  }
  function renderPage() {
    var st = openState();
    var gal = gallery();
    var promos = SAL.promos || [];
    var ms = masters();
    var wa = SAL.whatsapp ? 'https://wa.me/' + digits(SAL.whatsapp) + '?text=' + encodeURIComponent('Здравствуйте! Хочу записаться.') : '';
    var qa = [];
    if (SAL.phone) qa.push('<a href="tel:' + esc(SAL.phone) + '">' + ic('phone') + 'Позвонить</a>');
    if (wa) qa.push('<a href="' + esc(wa) + '" target="_blank" rel="noopener">' + ic('wa') + 'WhatsApp</a>');
    if (SAL.instagram) qa.push('<a href="https://instagram.com/' + esc(SAL.instagram) + '" target="_blank" rel="noopener">' + ic('ig') + 'Instagram</a>');
    if (SAL.telegram) qa.push('<a href="https://t.me/' + esc(SAL.telegram) + '" target="_blank" rel="noopener">' + ic('tg') + 'Telegram</a>');
    if (SAL.map_url) qa.push('<a href="' + esc(SAL.map_url) + '" target="_blank" rel="noopener">' + ic('route') + 'Маршрут</a>');
    var navs = [['svc', 'Услуги']];
    if (ms.length) navs.push(['team', SAL.staff_word]);
    if (gal.length) navs.push(['works', 'Работы']);
    if (promos.length) navs.push(['promo', 'Акции']);
    navs.push(['about', 'О нас']);
    var ini = (SAL.name || '?').trim().charAt(0).toUpperCase();
    var html =
      '<div class="sp-wrap">' +
      '<header class="hero">' + (SAL.cover_url ? '<img src="' + esc(SAL.cover_url) + '" alt="" fetchpriority="high">' : '<div class="pat"></div>') +
      '<div class="hero-top"><button class="round" type="button" data-share aria-label="Поделиться">' + ic('share') + '</button></div></header>' +
      '<div class="idt"><div class="logo">' + (SAL.logo_url ? '<img src="' + esc(SAL.logo_url) + '" alt="' + esc(SAL.name) + '">' : esc(ini)) + '</div>' +
      (st ? '<span class="open' + (st.on ? ' on' : '') + '"><i></i>' + esc(st.text) + '</span>' : '') + '</div>' +
      '<h1 class="name">' + esc(SAL.name) + '</h1>' +
      (SAL.niche ? '<div class="sub">' + esc(SAL.niche) + '</div>' : '') +
      (SAL.tagline ? '<div class="tag">' + esc(SAL.tagline) + '</div>' : '') +
      (SAL.address ? '<div class="addr">' + ic('pin') + '<span>' + esc(SAL.address) + '</span></div>' : '') +
      '<div class="acts"><button class="btn wide" type="button" data-book>' + ic('calendar') + 'Записаться онлайн</button></div>' +
      (qa.length ? '<div class="qa">' + qa.join('') + '</div>' : '') +
      '<nav class="nav" id="nav">' + navs.map(function (n, i) { return '<a href="#' + n[0] + '"' + (i ? '' : ' class="on"') + '>' + esc(n[1]) + '</a>'; }).join('') + '</nav>' +
      (promos.length ? '<section class="sec" id="promo"><h2>Акции</h2><div class="promos">' + promos.map(function (p) {
        var until = p.until ? parseKey(p.until) : null;
        return '<div class="promo"><b>' + esc(p.title) + '</b>' + (p.text ? '<p>' + esc(p.text) + '</p>' : '') +
          (until ? '<small>до ' + until.getUTCDate() + ' ' + MON[until.getUTCMonth()] + '</small>' : '') + '</div>';
      }).join('') + '</div></section>' : '') +
      '<section class="sec" id="svc"><h2>Услуги <small>' + services().length + '</small></h2><div id="svc-box"></div></section>' +
      (ms.length ? '<section class="sec" id="team"><h2>' + esc(SAL.staff_word) + '</h2><div class="masters">' + ms.map(function (m) {
        return '<button class="mcard" type="button" data-master="' + esc(m.id) + '"><div class="mp" style="--mc:' + esc(m.color || '') + '">' +
          (m.photo_url ? '<img src="' + esc(m.photo_url) + '" alt="' + esc(m.name) + '" loading="lazy">' : '<span class="ini">' + esc(m.initials || '?') + '</span>') +
          (m.experience_years ? '<span class="xp">опыт ' + esc(years(m.experience_years)) + '</span>' : '') + '</div>' +
          '<b>' + esc(m.name) + '</b><span>' + esc(m.role || '') + '</span></button>';
      }).join('') + '</div></section>' : '') +
      (gal.length ? '<section class="sec" id="works"><h2>Работы <small>' + gal.length + ' фото</small></h2><div class="gal">' +
        gal.slice(0, 9).map(function (g, i) {
          return '<button type="button" data-lb="' + i + '" aria-label="Открыть фото"><img src="' + esc(g.url) + '" alt="' + esc(g.cap) + '" loading="lazy">' +
            (i === 8 && gal.length > 9 ? '<span class="cnt">+' + (gal.length - 9) + '</span>' : '') + '</button>';
        }).join('') + '</div></section>' : '') +
      '<section class="sec" id="about"><h2>О нас</h2>' + aboutHtml() + '</section>' +
      '<div class="foot">Онлайн-запись работает на <a href="https://voksyai.online" target="_blank" rel="noopener">Voksy AI</a></div>' +
      '</div>' +
      '<div class="cta" id="cta"><div class="in"><div class="t"><b>' + esc(SAL.name) + '</b>' + (st ? esc(st.text) : 'Онлайн-запись') + '</div>' +
      '<button class="btn" type="button" data-book>Записаться</button></div></div>';
    app.innerHTML = html;
    drawServices('', '');
    wirePage();
  }
  function aboutHtml() {
    var n = nowLocal();
    var rows = (SAL.hours || []).map(function (h, i) {
      return '<tr class="' + (i === n.day ? 'today' : '') + (h.open ? '' : ' off') + '"><td>' + esc(['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'][i]) + '</td>' +
        '<td>' + (h.open ? esc(h.open + ' – ' + h.close) : 'выходной') + '</td></tr>';
    }).join('');
    var c = [];
    if (SAL.address) c.push('<a href="' + esc(SAL.map_url || '#') + '" target="_blank" rel="noopener">' + ic('pin') + '<div>' + esc(SAL.address) + '<span>Открыть на карте</span></div></a>');
    if (SAL.phone) c.push('<a href="tel:' + esc(SAL.phone) + '">' + ic('phone') + '<div>' + esc(fmtPhone(SAL.phone)) + '<span>Позвонить</span></div></a>');
    if (SAL.whatsapp) c.push('<a href="https://wa.me/' + esc(digits(SAL.whatsapp)) + '" target="_blank" rel="noopener">' + ic('wa') + '<div>' + esc(fmtPhone(SAL.whatsapp)) + '<span>WhatsApp</span></div></a>');
    if (SAL.instagram) c.push('<a href="https://instagram.com/' + esc(SAL.instagram) + '" target="_blank" rel="noopener">' + ic('ig') + '<div>@' + esc(SAL.instagram) + '<span>Instagram</span></div></a>');
    if (SAL.telegram) c.push('<a href="https://t.me/' + esc(SAL.telegram) + '" target="_blank" rel="noopener">' + ic('tg') + '<div>@' + esc(SAL.telegram) + '<span>Telegram</span></div></a>');
    if (SAL.website) c.push('<a href="' + esc(SAL.website) + '" target="_blank" rel="noopener">' + ic('globe') + '<div>' + esc(SAL.website.replace(/^https?:\/\//, '').replace(/\/$/, '')) + '<span>Сайт</span></div></a>');
    return (SAL.about || (SAL.amenities || []).length ? '<div class="card">' + (SAL.about ? '<div class="about">' + esc(SAL.about) + '</div>' : '') +
        ((SAL.amenities || []).length ? '<div class="amen">' + SAL.amenities.map(function (a) { return '<span>' + ic(AMEN_IC[a.key] || 'check') + esc(a.title) + '</span>'; }).join('') + '</div>' : '') + '</div>' : '') +
      (rows && (SAL.hours || []).some(function (h) { return h.open; }) ? '<div class="card" style="margin-top:10px"><div style="font-weight:700;margin-bottom:6px;display:flex;gap:8px;align-items:center">' + ic('clock') + 'Часы работы</div>' +
        '<table class="hours">' + rows + '</table>' + (SAL.hours_note ? '<div class="hint" style="margin:8px 0 0">' + esc(SAL.hours_note) + '</div>' : '') + '</div>' : '') +
      (c.length ? '<div class="contacts">' + c.join('') + '</div>' : '');
  }
  function drawServices(cat, q) {
    var box = document.getElementById('svc-box');
    if (!box) return;
    var list = services();
    var cats = [];
    list.forEach(function (s) { var c = s.category || s.collection || ''; if (c && cats.indexOf(c) < 0) cats.push(c); });
    q = (q || '').trim().toLowerCase();
    var shown = list.filter(function (s) {
      return (!cat || (s.category || s.collection) === cat) && (!q || (s.title + ' ' + (s.description || '') + ' ' + (s.collection || '')).toLowerCase().indexOf(q) >= 0);
    });
    var html = (cats.length > 1 ? '<div class="chips">' + ['', ].concat(cats).map(function (c) {
      return '<button class="chip' + (c === cat ? ' on' : '') + '" type="button" data-cat="' + esc(c) + '">' + esc(c || 'Все') + '</button>';
    }).join('') + '</div>' : '') +
      (list.length > 10 ? '<div class="search">' + ic('search') + '<input type="search" id="svc-q" placeholder="Найти услугу" value="' + esc(q) + '" autocomplete="off"></div>' : '');
    if (!list.length) html += '<div class="empty">' + ic('sparkles') + 'Расскажите, что нужно, — подберём время.<br><button class="btn sm" type="button" data-book style="margin-top:12px">Записаться</button></div>';
    var lastC = null, lastS = null;
    shown.forEach(function (s) {
      var c = s.category || s.collection || '';
      if (!cat && cats.length > 1 && c !== lastC) { html += '<div class="sgrp">' + esc(c || 'Другое') + '</div>'; lastC = c; lastS = null; }
      if (s.subcategory && s.subcategory !== lastS) { html += '<div class="sgrp" style="text-transform:none;letter-spacing:0;font-size:13px">' + esc(s.subcategory) + '</div>'; lastS = s.subcategory; }
      html += svcRow(s, false);
    });
    if (list.length && !shown.length) html += '<div class="empty">' + ic('search') + 'Ничего не нашли — попробуйте другое слово</div>';
    box.innerHTML = html;
  }
  function svcRow(s, sel) {
    var pr = priceText(s);
    var meta = [ic('clock').replace('class="ic"', 'class="ic" style="width:13px;height:13px;vertical-align:-2px"') + ' ' + durText(s.duration_min)];
    if (s.prepay_text) meta.push('предоплата');
    return '<button class="svc' + (sel ? ' on' : '') + '" type="button" data-svc="' + esc(s.id) + '">' +
      (s.photo_url ? '<span class="ph"><img src="' + esc(s.photo_url) + '" alt="" loading="lazy"></span>' : '') +
      '<span class="bd"><b>' + esc(s.title) + '</b><span class="meta">' + meta.join('<span>·</span>') + '</span>' +
      (s.description ? '<span class="ds">' + esc(s.description) + '</span>' : '') + '</span>' +
      (pr ? '<span class="pr">' + esc(pr) + '</span>' : '') + '<span class="add">' + ic(sel ? 'check' : 'plus') + '</span></button>';
  }
  function wirePage() {
    var nav = document.getElementById('nav'), cta = document.getElementById('cta');
    var acts = app.querySelector('.acts');
    if ('IntersectionObserver' in window) {
      if (acts && cta) new IntersectionObserver(function (es) { cta.classList.toggle('on', !es[0].isIntersecting && !B.open); }).observe(acts);
      var secs = Array.prototype.slice.call(app.querySelectorAll('.sec'));
      var io = new IntersectionObserver(function (es) {
        es.forEach(function (e) {
          if (!e.isIntersecting || !nav) return;
          Array.prototype.forEach.call(nav.querySelectorAll('a'), function (a) { a.classList.toggle('on', a.getAttribute('href') === '#' + e.target.id); });
        });
      }, { rootMargin: '-45% 0px -50% 0px' });
      secs.forEach(function (s) { io.observe(s); });
    }
    if (nav) window.addEventListener('scroll', function () { nav.classList.toggle('stuck', nav.getBoundingClientRect().top <= 0); }, { passive: true });
    app.addEventListener('input', function (e) { if (e.target.id === 'svc-q') { var c = app.querySelector('.chip.on'); drawServices(c ? c.getAttribute('data-cat') : '', e.target.value); var i = document.getElementById('svc-q'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } } });
  }

  // ═════════════════════════════ запись ═════════════════════════════
  var B = { open: false, step: 1, sel: [], pick: {}, preMaster: null, day: null, slot: null, avail: {}, cache: {}, err: '', busy: false, res: null };
  var me = store('vxs_me') || {};
  B.name = me.name || ''; B.phone = me.phone || '+996 '; B.note = '';
  var sheet = null;

  function isVisit() { return B.sel.length > 1; }
  function selSvcs() { return B.sel.map(svcById).filter(Boolean); }
  function pickOf(svc) { return B.pick[svc.id] ? mById(B.pick[svc.id]) : null; }
  function needMasterStep() {
    if (!masters().length) return false;
    var list = selSvcs();
    if (!list.length) return masters().length > 1;
    return list.some(function (s) { return mastersFor(s).length > 1; });
  }
  function items() { return selSvcs().map(function (s) { return { service_id: s.id, resource_id: B.pick[s.id] || null }; }); }
  function slotKey() { return isVisit() ? 'v:' + JSON.stringify(items()) : 's:' + (B.sel[0] || '') + ':' + (B.sel[0] ? (B.pick[B.sel[0]] || '') : (B.pick._ || '')); }
  function totalDur() { return selSvcs().reduce(function (a, s) { return a + terms(s, pickOf(s)).dur; }, 0); }
  function totalPrice() {
    var list = selSvcs(), sum = 0, exact = true;
    if (!list.length || !SAL.show_prices) return '';
    for (var i = 0; i < list.length; i++) {
      var m = pickOf(list[i]), p = m ? terms(list[i], m).price : priceFrom(list[i]);
      if (p == null) return '';
      if (!m && (priceText(list[i]).indexOf('от') === 0 || list[i].price_to)) exact = false;
      if (m && list[i].price_to && !(m.service_prices && m.service_prices[list[i].id] && m.service_prices[list[i].id].price != null)) exact = false;
      sum += +p;
    }
    return (exact ? '' : 'от ') + money(sum);
  }
  function prepayInfo() {
    var sum = 0, pct = [], any = false;
    selSvcs().forEach(function (s) {
      var r = s.prepay;
      if (!r || !r.value) return;
      any = true;
      if (r.type === 'fixed') { sum += +r.value; return; }
      var m = pickOf(s), p = m ? terms(s, m).price : s.price;
      if (p != null && SAL.show_prices) sum += Math.round(p * r.value / 100); else pct.push(r.value + '% от цены «' + s.title + '»');
    });
    if (!any) return '';
    return [sum ? money(sum) : '', pct.join(', ')].filter(Boolean).join(' + ');
  }

  // Окна поверх страницы (запись, мастер) — в истории браузера: «Назад» телефона закрывает верхнее окно
  var layers = [];
  function pushLayer(n, replace) {
    if (replace && layers.length) { layers[layers.length - 1] = n; try { history.replaceState({ l: n }, ''); } catch (e) { /* ignore */ } return; }
    layers.push(n);
    try { history.pushState({ l: n }, ''); } catch (e) { /* ignore */ }
  }
  function closeLayer(n, doClose) {      // закрыть по кнопке: через history.back(), если окно — верхнее
    if (layers[layers.length - 1] === n) { try { history.back(); return; } catch (e) { layers.pop(); } }
    var i = layers.lastIndexOf(n); if (i >= 0) layers.splice(i, 1);
    doClose();
  }
  window.addEventListener('popstate', function () {
    if (lbEl && lbEl.classList.contains('on')) lbClose();
    var n = layers.pop();
    if (n === 'bk') doCloseBooking();
    if (n === 'ms') doCloseMs();
  });

  function openBooking(pre, replaceLayer) {
    pre = pre || {};
    B.err = ''; B.res = null; B.slot = null;
    if (pre.service) { if (B.sel.indexOf(pre.service) < 0) B.sel = [pre.service]; }
    if (pre.master !== undefined) {
      B.preMaster = pre.master || null;
      if (B.preMaster) { var mm = mById(B.preMaster); B.sel = B.sel.filter(function (id) { return does(mm, svcById(id)); }); }
    }
    if (!sheet) buildSheet();
    B.open = true;
    document.documentElement.classList.add('bk-open');
    var cta = document.getElementById('cta'); if (cta) cta.classList.remove('on');
    if (MODE === 'page') {
      document.documentElement.style.overflow = 'hidden';
      if (layers.indexOf('bk') < 0) pushLayer('bk', replaceLayer);
      requestAnimationFrame(function () { sheet.classList.add('on'); });
    } else sheet.classList.add('on');
    go(pre.step || 1);
  }
  function closeBooking() {
    if (!sheet || MODE !== 'page') { if (EMBED) closeWidget(); return; }
    closeLayer('bk', doCloseBooking);
  }
  function doCloseBooking() {
    if (!sheet || !B.open) return;
    B.open = false;
    document.documentElement.classList.remove('bk-open');
    sheet.classList.remove('on');
    document.documentElement.style.overflow = '';
    setTimeout(function () { if (!B.open && sheet) sheet.style.display = 'none'; }, 280);
  }
  function closeWidget() { try { window.parent.postMessage({ source: 'voksy-booking', type: 'close' }, '*'); } catch (e) { /* ignore */ } }
  function buildSheet() {
    sheet = document.createElement('div');
    sheet.className = 'bk';
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-modal', MODE === 'page' ? 'true' : 'false');
    sheet.innerHTML = '<div class="pane"><div class="bk-h"><button class="round" type="button" data-back aria-label="Назад">' + ic('left') + '</button>' +
      '<div class="ttl"><b id="bk-t">Запись</b><span id="bk-s"></span></div>' +
      (MODE === 'page' ? '<button class="round" type="button" data-close aria-label="Закрыть">' + ic('x') + '</button>' : '') + '</div>' +
      '<div class="prog" id="bk-p"></div><div class="bk-b" id="bk-b"></div><div class="bk-f" id="bk-f"></div></div>';
    if (MODE === 'page') document.body.appendChild(sheet); else { app.innerHTML = ''; app.appendChild(sheet); }
    sheet.addEventListener('click', onSheetClick);
    sheet.addEventListener('mousedown', function (e) { if (e.target === sheet && MODE === 'page') closeBooking(); });
    sheet.addEventListener('input', function (e) {
      if (e.target.id === 'f-nm') B.name = e.target.value;
      if (e.target.id === 'f-ph') B.phone = e.target.value;
      if (e.target.id === 'f-nt') B.note = e.target.value;
    });
  }
  function head(title) {
    document.getElementById('bk-t').textContent = title;
    document.getElementById('bk-s').textContent = SAL.name + (SAL.address ? ' · ' + SAL.address : '');
    var steps = needMasterStep() ? 4 : 3;
    var at = B.step === 1 ? 1 : B.step === 2 ? 2 : B.step === 3 ? (steps === 4 ? 3 : 2) : B.step === 4 ? steps : steps + 1;
    document.getElementById('bk-p').innerHTML = B.step >= 5 ? '' : new Array(steps + 1).join('.').split('').map(function (_, i) { return '<i class="' + (i < at ? 'on' : '') + '"></i>'; }).join('');
    var back = sheet.querySelector('[data-back]');
    var logoHere = MODE !== 'page' && B.step === 1;
    back.classList.toggle('lgo', logoHere);
    back.innerHTML = logoHere ? (SAL.logo_url ? '<img src="' + esc(SAL.logo_url) + '" alt="">' : esc((SAL.name || '?').trim().charAt(0).toUpperCase())) : ic('left');
    back.setAttribute('aria-label', logoHere ? SAL.name : 'Назад');
    back.style.visibility = B.step >= 5 ? 'hidden' : '';
  }
  function foot(sum, btn) {
    document.getElementById('bk-f').innerHTML = (sum ? '<div class="sum">' + sum + '</div>' : '') + (btn || '');
  }
  function body(html) { var b = document.getElementById('bk-b'); b.innerHTML = html; b.scrollTop = 0; }
  function go(step) {
    B.step = step;
    sheet.style.display = '';
    if (step === 1) return step1();
    if (step === 2) { if (!needMasterStep()) return go(3); return step2(); }
    if (step === 3) return step3();
    if (step === 4) return step4();
    if (step === 5) return step5();
  }
  function selSummary() {
    var list = selSvcs();
    if (!list.length) return '';
    var tp = totalPrice();
    return '<b>' + esc(list.length === 1 ? list[0].title : list.length + ' ' + plural(list.length, 'услуга', 'услуги', 'услуг')) + '</b>' +
      esc(durText(totalDur()) + (tp ? ' · ' + tp : ''));
  }

  // шаг 1 — услуги (можно несколько: визит подряд)
  function step1() {
    head('Выберите услугу');
    var list = services(), pm = B.preMaster ? mById(B.preMaster) : null;
    if (pm) list = list.filter(function (s) { return does(pm, s); });
    if (!services().length) {
      body('<h3>Запись на приём</h3><div class="hint">Уточним детали, когда подтвердим запись.</div>' +
        '<button class="opt on" type="button" data-nosvc><span class="ava any">' + ic('calendar') + '</span><span class="bd"><b>Записаться на приём</b><span>' + esc(SAL.name) + '</span></span></button>');
      foot('', '<button class="btn" type="button" data-next>Выбрать время</button>');
      return;
    }
    var cats = [], lastC = null, lastS = null, html = '';
    list.forEach(function (s) { var c = s.category || s.collection || ''; if (cats.indexOf(c) < 0) cats.push(c); });
    if (pm) html += '<div class="opt on" style="cursor:default">' + ava(pm) + '<span class="bd"><b>' + esc(pm.name) + '</b><span>' + esc(pm.role || '') + ' · услуги мастера</span></span>' +
      '<button class="btn sm gh" type="button" data-allm>Все</button></div><div style="height:6px"></div>';
    html += '<div class="hint">Можно выбрать несколько — запишем подряд.</div>';
    list.forEach(function (s) {
      var c = s.category || s.collection || '';
      if (cats.length > 1 && c !== lastC) { html += '<div class="sgrp">' + esc(c || 'Другое') + '</div>'; lastC = c; lastS = null; }
      if (s.subcategory && s.subcategory !== lastS) { html += '<div class="sgrp" style="text-transform:none;letter-spacing:0;font-size:13px">' + esc(s.subcategory) + '</div>'; lastS = s.subcategory; }
      html += svcRow(s, B.sel.indexOf(s.id) >= 0);
    });
    body(html);
    foot(selSummary() || 'Выберите услугу', '<button class="btn" type="button" data-next' + (B.sel.length ? '' : ' disabled') + '>Дальше</button>');
  }
  function toggleSvc(id) {
    var i = B.sel.indexOf(id);
    if (i >= 0) B.sel.splice(i, 1);
    else {
      if (B.sel.length >= 6) { toast('В одном визите — не больше 6 услуг'); return; }
      B.sel.push(id);
      if (B.preMaster && !B.pick[id]) B.pick[id] = B.preMaster;
    }
    B.slot = null;
    var el = sheet.querySelector('.svc[data-svc="' + id + '"]');
    if (el) { var on = B.sel.indexOf(id) >= 0; el.classList.toggle('on', on); el.querySelector('.add').innerHTML = ic(on ? 'check' : 'plus'); }
    foot(selSummary() || 'Выберите услугу', '<button class="btn" type="button" data-next' + (B.sel.length ? '' : ' disabled') + '>Дальше</button>');
  }

  // шаг 2 — мастер (для каждой услуги визита свой или «любой»)
  function step2() {
    head(SAL.staff_word === 'Мастера' ? 'Выберите мастера' : 'Выберите специалиста');
    var list = selSvcs(), html = '';
    if (list.length <= 1) {
      var s = list[0] || null, ms = s ? mastersFor(s) : masters(), cur = s ? B.pick[s.id] : B.pick._;
      html += '<button class="opt' + (!cur ? ' on' : '') + '" type="button" data-m=""><span class="ava any">' + ic('users') + '</span><span class="bd"><b>Любой свободный</b><span>Покажем всё свободное время</span></span>' +
        (s && priceText(s) ? '<span class="pr">' + esc(priceText(s)) + '</span>' : '') + '</button>';
      ms.forEach(function (m) {
        var sub = [m.role, m.experience_years ? 'опыт ' + years(m.experience_years) : ''].filter(Boolean).join(' · ');
        html += '<button class="opt' + (cur === m.id ? ' on' : '') + '" type="button" data-m="' + esc(m.id) + '">' + ava(m) + '<span class="bd"><b>' + esc(m.name) + '</b><span>' + esc(sub) + '</span>' +
          (m.bio ? '<span class="bio">' + esc(m.bio) + '</span>' : '') +
          ((m.portfolio || []).length ? '<span class="pf">' + m.portfolio.slice(0, 4).map(function (it) { return '<img src="' + esc(it.url) + '" alt="" loading="lazy">'; }).join('') + '</span>' : '') +
          '</span>' + (s && priceWith(s, m) ? '<span class="pr">' + esc(priceWith(s, m)) + '</span>' : '') + '</button>';
      });
    } else {
      html += '<div class="hint">Для каждой услуги — свой мастер или любой свободный.</div>';
      list.forEach(function (s) {
        var cur = B.pick[s.id] || '';
        html += '<div class="vrow"><div class="vt">' + esc(s.title) + '<span>' + esc(durText(terms(s, pickOf(s)).dur)) + '</span></div><div class="vm">' +
          '<button type="button" class="any' + (!cur ? ' on' : '') + '" data-vm="' + esc(s.id) + '" data-mid="">Любой</button>' +
          mastersFor(s).map(function (m) { return '<button type="button" class="' + (cur === m.id ? 'on' : '') + '" data-vm="' + esc(s.id) + '" data-mid="' + esc(m.id) + '">' + ava(m) + esc(String(m.name).split(' ')[0]) + '</button>'; }).join('') +
          '</div></div>';
      });
    }
    body(html);
    foot(selSummary(), '<button class="btn" type="button" data-next>Дальше</button>');
  }

  // шаг 3 — день и время
  function step3() {
    head('Выберите время');
    var today = keyOf(new Date()), horizon = Math.min((D && D.horizon_days) || 30, 60);
    if (!B.day || B.day < today) B.day = today;
    var key = slotKey();
    if (B._availKey !== key) { B._availKey = key; B.avail = {}; }
    var strip = '';
    for (var i = 0; i < horizon; i++) {
      var k = addDays(today, i), d = parseKey(k), a = B.avail[k];
      strip += '<button class="day' + (k === B.day ? ' on' : '') + (a === 0 ? ' no' : '') + '" type="button" data-day="' + k + '">' +
        '<div class="wd' + (i < 2 ? ' rel' : '') + '">' + (i === 0 ? 'сегодня' : i === 1 ? 'завтра' : WD[d.getUTCDay()]) + '</div><div class="dd">' + d.getUTCDate() + '</div>' + (a ? '<span class="dt"></span>' : '<span class="dt" style="background:transparent"></span>') + '</button>';
    }
    var dd = parseKey(B.day);
    body('<div class="mon" id="bk-mon">' + MONN[dd.getUTCMonth()] + '</div><div class="days" id="bk-days">' + strip + '</div><div id="bk-slots"><div class="sk" style="height:46px;margin-top:16px"></div><div class="sk" style="height:46px"></div></div>');
    var on = sheet.querySelector('.day.on'), strip0 = sheet.querySelector('.days');
    if (on && strip0) strip0.scrollLeft = Math.max(0, on.offsetLeft - 70);
    footTime();
    loadSlots(true);
  }
  function footTime() {
    var t = B.slot ? dayLong(B.day) + ', ' + B.slot.time + (B.slot.end_time ? '–' + B.slot.end_time : '') : 'Выберите время';
    foot('<b>' + esc(t) + '</b>' + esc(selSvcs().length ? durText(totalDur()) : ''), '<button class="btn" type="button" data-next' + (B.slot ? '' : ' disabled') + '>Дальше</button>');
  }
  function loadSlots(first) {
    var key = slotKey(), day = B.day, box = document.getElementById('bk-slots');
    var ck = key + '@' + day;
    if (B.cache[ck]) { drawSlots(B.cache[ck]); return; }
    var req;
    if (isVisit()) {
      var horizon = Math.min((D && D.horizon_days) || 30, 60), last = addDays(keyOf(new Date()), horizon - 1);
      var tryDay = function (d, left) {
        return post('/visit-slots', { items: items(), date: d }).then(function (r) {
          B.cache[key + '@' + d] = r.slots || []; B.avail[d] = B.cache[key + '@' + d].length; markDays();
          // первый заход и мест нет — смотрим следующий день, пока клиент сам не выбрал другой
          if (first && !B.avail[d] && left > 0 && B.day === d && d < last && slotKey() === key) {
            B.day = addDays(d, 1); selectDayUi(); footTime();
            return tryDay(B.day, left - 1);
          }
          return { day: d, slots: B.cache[key + '@' + d] };
        });
      };
      req = tryDay(day, 6);
    } else {
      var p = new URLSearchParams({ date: day, days: first ? '14' : '1' });
      if (B.sel[0]) p.set('service_id', B.sel[0]);
      var rid = B.sel[0] ? B.pick[B.sel[0]] : B.pick._;
      if (rid) p.set('resource_id', rid);
      req = get('/slots?' + p.toString()).then(function (d) {
        (d.days || []).forEach(function (x) { B.cache[key + '@' + x.date] = x.slots; if (B._availKey === key) B.avail[x.date] = x.slots.length; });
        if (first) markDays();
        // первый заход: если в выбранный день мест нет — встаём на ближайший свободный
        if (first && B.day === day && !(B.cache[ck] || []).length) {
          var next = (d.days || []).filter(function (x) { return x.slots.length; })[0];
          if (next && next.date !== day) { B.day = next.date; selectDayUi(); footTime(); return { day: next.date, slots: B.cache[key + '@' + next.date] }; }
        }
        return { day: day, slots: B.cache[ck] || [] };
      });
    }
    req.then(function (r) {
      if (slotKey() !== key || B.day !== r.day || B.step !== 3) return;    // пока ждали — выбрали другой день или мастера
      drawSlots(r.slots || []);
    }).catch(function (e) { if (box && B.day === day) box.innerHTML = '<div class="err">' + esc(e.message) + '</div>'; });
  }
  function markDays() {
    Array.prototype.forEach.call(sheet.querySelectorAll('.day'), function (b) {
      var a = B.avail[b.getAttribute('data-day')];
      b.classList.toggle('no', a === 0);
      var dt = b.querySelector('.dt'); if (dt) dt.style.background = a ? '' : 'transparent';
    });
  }
  function selectDayUi() {
    Array.prototype.forEach.call(sheet.querySelectorAll('.day'), function (b) { b.classList.toggle('on', b.getAttribute('data-day') === B.day); });
    var on = sheet.querySelector('.day.on'), strip = sheet.querySelector('.days');
    if (on && strip) strip.scrollTo({ left: Math.max(0, on.offsetLeft - 70), behavior: 'smooth' });
    var m = document.getElementById('bk-mon'); if (m) m.textContent = MONN[parseKey(B.day).getUTCMonth()];
  }
  function drawSlots(slots) {
    var box = document.getElementById('bk-slots');
    if (!box) return;
    if (!slots.length) {
      box.innerHTML = '<div class="empty">' + ic('calendar') + 'На этот день мест нет.<br>Выберите другой день' +
        (Object.keys(B.avail).some(function (k) { return B.avail[k] > 0; }) ? ' — дни с местами отмечены точкой.' : '.') + '</div>';
      return;
    }
    var parts = [['Утро', 'sun', 0, 12 * 60], ['День', 'sunset', 12 * 60, 17 * 60], ['Вечер', 'moon', 17 * 60, 24 * 60]];
    var wide = isVisit();
    box.innerHTML = parts.map(function (p) {
      var xs = slots.filter(function (s) { var m = toMin(s.time); return m >= p[2] && m < p[3]; });
      if (!xs.length) return '';
      return '<div class="part">' + ic(p[1]) + p[0] + '</div><div class="slots">' + xs.map(function (s) {
        return '<button class="slot' + (wide ? ' wide' : '') + (B.slot && B.slot.start === s.start ? ' on' : '') + '" type="button" data-slot="' + esc(s.start) + '" data-t="' + esc(s.time) + '" data-e="' + esc(s.end_time || '') + '">' +
          esc(s.time) + (wide && s.end_time ? '<small>до ' + esc(s.end_time) + '</small>' : '') + '</button>';
      }).join('') + '</div>';
    }).join('');
  }

  // шаг 4 — контакты и проверка
  function step4() {
    head('Ваши данные');
    var list = selSvcs(), dt = dayLong(B.day) + ', ' + B.slot.time + (B.slot.end_time ? '–' + B.slot.end_time : '');
    var rows = list.length ? list.map(function (s) {
      var m = pickOf(s);
      return '<div><span>' + esc(s.title) + '<br><small style="color:var(--t3)">' + esc(m ? m.name : 'любой мастер') + '</small></span><b>' + esc(priceWith(s, m) || durText(terms(s, m).dur)) + '</b></div>';
    }).join('') : '<div><span>Запись</span><b>' + esc(SAL.name) + '</b></div>';
    var tp = totalPrice(), pp = prepayInfo();
    body('<div class="sumc"><div><span>Когда</span><b>' + esc(dt) + '</b></div>' + rows +
      (tp && list.length > 1 ? '<div class="tot"><span>Итого</span><b>' + esc(tp) + '</b></div>' : '') + '</div>' +
      (pp ? '<div class="pp">' + ic('wallet').replace('class="ic"', 'class="ic" style="width:16px;height:16px;vertical-align:-3px;margin-right:4px"') +
        'Для записи салон просит предоплату <b>' + esc(pp) + '</b>. ' + (D.prepay_info ? 'Как оплатить: ' + esc(D.prepay_info) : 'Реквизиты пришлём после записи.') + '</div>' : '') +
      '<div class="field"><label for="f-nm">Ваше имя</label><input id="f-nm" autocomplete="name" value="' + esc(B.name) + '" placeholder="Как к вам обращаться"></div>' +
      '<div class="field"><label for="f-ph">Телефон (WhatsApp)</label><input id="f-ph" type="tel" inputmode="tel" autocomplete="tel" value="' + esc(B.phone) + '"></div>' +
      '<div class="field"><label for="f-nt">Комментарий <span style="color:var(--t4);font-weight:500">— необязательно</span></label><textarea id="f-nt" placeholder="Пожелания мастеру">' + esc(B.note) + '</textarea></div>' +
      (B.err ? '<div class="err">' + esc(B.err) + '</div>' : '') +
      '<div class="hint" style="margin:14px 0 0">Подтверждение и напоминание придут в WhatsApp. Нажимая «Записаться», вы соглашаетесь, что салон свяжется с вами по этой записи.</div>');
    foot('<b>' + esc(dt) + '</b>' + esc(tp || ''), '<button class="btn" type="button" data-submit>' + (B.busy ? 'Записываем…' : 'Записаться') + '</button>');
    if (!B.name) { var nm = document.getElementById('f-nm'); if (nm && window.matchMedia('(min-width: 761px)').matches) nm.focus(); }
  }
  function submit() {
    if (B.busy) return;
    B.name = (document.getElementById('f-nm').value || '').trim();
    B.phone = (document.getElementById('f-ph').value || '').trim();
    B.note = (document.getElementById('f-nt').value || '').trim();
    if (!B.name) { B.err = 'Укажите имя'; return step4(); }
    if (digits(B.phone).length < 9) { B.err = 'Укажите телефон: например, +996 700 123 456'; return step4(); }
    B.err = ''; B.busy = true;
    var btn = sheet.querySelector('[data-submit]'); if (btn) { btn.disabled = true; btn.textContent = 'Записываем…'; }
    var req = isVisit()
      ? post('/visit', { items: items(), start: B.slot.start, name: B.name, phone: B.phone, note: B.note || null }).then(function (d) { return { visit: d.visit }; })
      : post('', { start: B.slot.start, service_id: B.sel[0] || null, resource_id: (B.sel[0] ? B.pick[B.sel[0]] : B.pick._) || null,
        name: B.name, phone: B.phone, note: B.note || null }).then(function (d) { return { booking: d.booking }; });
    req.then(function (r) {
      B.busy = false;
      B.res = r;
      store('vxs_me', { name: B.name, phone: B.phone });
      B.cache = {}; B.avail = {}; B._availKey = null;
      go(5);
    }).catch(function (e) {
      B.busy = false;
      if (/занято|прошло|не помещается|заняты/i.test(e.message)) {
        B.slot = null; B.cache = {};
        toast(e.message.replace(/\s*—.*$/, '') + '. Выберите другое время');
        return go(3);
      }
      B.err = e.message;
      step4();
    });
  }

  // шаг 5 — готово
  function step5() {
    head('Готово');
    var r = B.res || {}, v = r.visit, b = r.booking || {};
    var start = v ? v.start : b.start, end = v ? v.end : b.end;
    var when = start ? (function () { var d = parseKey(keyOf(new Date(start))); return WDN[d.getUTCDay()] + ', ' + d.getUTCDate() + ' ' + MON[d.getUTCMonth()] + ', ' + hm(start) + (end ? '–' + hm(end) : ''); })() : (b.label || '');
    var lines = v ? v.items.map(function (it) { return '<div><span>' + esc(hm(it.start)) + ' · ' + esc(it.service || 'Услуга') + '</span><b>' + esc(it.resource || '') + '</b></div>'; }).join('')
      : '<div><span>' + esc(b.service || 'Запись') + '</span><b>' + esc(b.resource || '') + '</b></div>';
    var price = v ? v.price : b.price, prepay = v ? v.prepay_amount : b.prepay_amount, addr = (v ? v.address : b.address) || SAL.address;
    var wa = SAL.whatsapp ? 'https://wa.me/' + digits(SAL.whatsapp) + '?text=' + encodeURIComponent('Здравствуйте! Я записался(-ась) онлайн: ' + when + '. ' + B.name) : '';
    body('<div class="done"><div class="ok">' + ic('check') + '</div><h3>Вы записаны!</h3><div class="when">' + esc(when) + '</div>' +
      '<div class="sumc">' + lines + (price && SAL.show_prices ? '<div class="tot"><span>Стоимость</span><b>' + esc(money(price)) + '</b></div>' : '') + '</div>' +
      (prepay ? '<div class="pp" style="text-align:left">Внесите предоплату <b>' + esc(money(prepay)) + '</b> — запись подтвердят после оплаты. ' +
        (D.prepay_info ? 'Как оплатить: ' + esc(D.prepay_info) : 'Реквизиты пришлём в WhatsApp.') + '</div>' : '') +
      (addr ? '<div class="contacts"><a href="' + esc(SAL.map_url || '#') + '" target="_blank" rel="noopener">' + ic('pin') + '<div>' + esc(addr) + '<span>Как добраться</span></div></a></div>' : '') +
      '<div class="row"><button class="btn gh" type="button" data-ics>' + ic('download') + 'В календарь</button>' +
      (wa ? '<a class="btn gh" href="' + esc(wa) + '" target="_blank" rel="noopener">' + ic('wa') + 'Написать</a>' : '') + '</div>' +
      '<p class="hint" style="margin:16px 0 0;text-align:center">Напомним о записи в WhatsApp. Если планы изменятся — просто напишите нам.</p>' +
      '<div class="pw">Работает на <a href="https://voksyai.online" target="_blank" rel="noopener">Voksy AI</a></div></div>');
    foot('', '<button class="btn" type="button" data-finish style="flex:1">' + (MODE === 'page' || EMBED ? 'Готово' : 'Записаться ещё') + '</button>');
    B._ics = { start: start, end: end, title: (v ? v.items.map(function (x) { return x.service; }).filter(Boolean).join(', ') : b.service) || 'Запись', addr: addr };
  }
  function ics() {
    var x = B._ics; if (!x || !x.start) return;
    function z(iso) { return new Date(iso).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, ''); }
    function t(s) { return String(s || '').replace(/[\\;,]/g, function (c) { return '\\' + c; }).replace(/\n/g, '\\n'); }
    var body = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Voksy AI//Booking//RU', 'BEGIN:VEVENT',
      'UID:' + Date.now() + '-' + Math.random().toString(36).slice(2) + '@voksyai.online', 'DTSTAMP:' + z(new Date().toISOString()),
      'DTSTART:' + z(x.start), 'DTEND:' + z(x.end || x.start), 'SUMMARY:' + t(x.title + ' — ' + SAL.name),
      x.addr ? 'LOCATION:' + t(x.addr) : '', 'BEGIN:VALARM', 'TRIGGER:-PT2H', 'ACTION:DISPLAY', 'DESCRIPTION:' + t(SAL.name), 'END:VALARM',
      'END:VEVENT', 'END:VCALENDAR'].filter(Boolean).join('\r\n');
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([body], { type: 'text/calendar;charset=utf-8' }));
    a.download = 'zapis.ics';
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  function onSheetClick(e) {
    var t = e.target;
    if (t.closest('[data-close]')) return closeBooking();
    if (t.closest('[data-back]')) {
      if (B.step === 1) return MODE === 'page' ? closeBooking() : undefined;
      return go(B.step === 3 && !needMasterStep() ? 1 : B.step - 1);
    }
    var sv = t.closest('[data-svc]');
    if (sv) return toggleSvc(sv.getAttribute('data-svc'));
    if (t.closest('[data-allm]')) { B.preMaster = null; return step1(); }
    if (t.closest('[data-nosvc]')) return go(needMasterStep() ? 2 : 3);
    var m = t.closest('[data-m]');
    if (m) {
      var id = m.getAttribute('data-m') || null;
      if (B.sel[0]) B.pick[B.sel[0]] = id; else B.pick._ = id;
      B.slot = null;
      return go(3);
    }
    var vm = t.closest('[data-vm]');
    if (vm) {
      B.pick[vm.getAttribute('data-vm')] = vm.getAttribute('data-mid') || null;
      B.slot = null;
      Array.prototype.forEach.call(vm.parentNode.children, function (x) { x.classList.toggle('on', x === vm); });
      return;
    }
    var dy = t.closest('[data-day]');
    if (dy) { B.day = dy.getAttribute('data-day'); B.slot = null; selectDayUi(); footTime(); document.getElementById('bk-slots').innerHTML = '<div class="sk" style="height:46px;margin-top:16px"></div>'; return loadSlots(false); }
    var sl = t.closest('[data-slot]');
    if (sl) {
      B.slot = { start: sl.getAttribute('data-slot'), time: sl.getAttribute('data-t'), end_time: sl.getAttribute('data-e') || '' };
      Array.prototype.forEach.call(sheet.querySelectorAll('.slot'), function (x) { x.classList.toggle('on', x === sl); });
      footTime();
      if (window.matchMedia('(max-width: 760px)').matches) { var f = document.getElementById('bk-f'); if (f && f.animate) f.animate([{ transform: 'translateY(6px)' }, { transform: 'none' }], 220); }
      return;
    }
    if (t.closest('[data-next]')) { if (t.closest('[data-next]').disabled) return; return go(B.step === 1 ? (needMasterStep() ? 2 : 3) : B.step + 1); }
    if (t.closest('[data-submit]')) return submit();
    if (t.closest('[data-ics]')) return ics();
    if (t.closest('[data-finish]')) {
      if (EMBED) return closeWidget();
      if (MODE === 'page') { B.sel = []; B.pick = {}; B.slot = null; B.preMaster = null; return closeBooking(); }
      B.sel = []; B.pick = {}; B.slot = null; return go(1);
    }
  }

  // ═════════════════════════════ мастер и фото ═════════════════════════════
  var lbList = [], lbI = 0, lbEl = null;
  function lightbox(list, i) {
    lbList = list; lbI = i;
    if (!lbEl) {
      lbEl = document.createElement('div');
      lbEl.className = 'lb';
      lbEl.innerHTML = '<span class="num" id="lb-n"></span><button class="round x" type="button" data-lbx aria-label="Закрыть">' + ic('x') + '</button>' +
        '<button class="round nv p" type="button" data-lbp aria-label="Назад">' + ic('left') + '</button><button class="round nv n" type="button" data-lbn aria-label="Вперёд">' + ic('right') + '</button>' +
        '<div class="st"><img id="lb-i" alt=""></div><div class="cap" id="lb-c"></div>';
      document.body.appendChild(lbEl);
      lbEl.addEventListener('click', function (e) {
        if (e.target.closest('[data-lbx]') || e.target === lbEl || e.target.classList.contains('st')) return lbClose();
        if (e.target.closest('[data-lbp]')) return lbGo(-1);
        if (e.target.closest('[data-lbn]')) return lbGo(1);
      });
      var x0 = null;
      lbEl.addEventListener('touchstart', function (e) { x0 = e.touches[0].clientX; }, { passive: true });
      lbEl.addEventListener('touchend', function (e) { if (x0 == null) return; var dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 50) lbGo(dx < 0 ? 1 : -1); x0 = null; });
    }
    lbEl.style.display = 'flex';
    requestAnimationFrame(function () { lbEl.classList.add('on'); });
    lbShow();
  }
  function lbShow() {
    var it = lbList[lbI] || {};
    document.getElementById('lb-i').src = it.url || '';
    document.getElementById('lb-c').textContent = it.cap || '';
    document.getElementById('lb-n').textContent = lbList.length > 1 ? (lbI + 1) + ' / ' + lbList.length : '';
    lbEl.querySelector('[data-lbp]').style.display = lbEl.querySelector('[data-lbn]').style.display = lbList.length > 1 ? '' : 'none';
    var nx = lbList[(lbI + 1) % lbList.length]; if (nx) { var im = new Image(); im.src = nx.url; }
  }
  function lbGo(d) { lbI = (lbI + d + lbList.length) % lbList.length; lbShow(); }
  function lbClose() { if (!lbEl) return; lbEl.classList.remove('on'); setTimeout(function () { lbEl.style.display = 'none'; }, 200); }

  var msEl = null;
  function masterSheet(id) {
    var m = mById(id);
    if (!m) return;
    if (!msEl) {
      msEl = document.createElement('div');
      msEl.className = 'bk';
      msEl.innerHTML = '<div class="pane"><div class="bk-h"><div class="ttl"><b id="ms-t"></b></div><button class="round" type="button" data-msx aria-label="Закрыть">' + ic('x') + '</button></div>' +
        '<div class="bk-b ms" id="ms-b"></div><div class="bk-f" id="ms-f"></div></div>';
      document.body.appendChild(msEl);
      msEl.addEventListener('click', function (e) {
        if (e.target === msEl || e.target.closest('[data-msx]')) return closeLayer('ms', doCloseMs);
        var pf = e.target.closest('[data-pf]');
        if (pf) { var mm = mById(msEl.getAttribute('data-id')); return lightbox((mm.portfolio || []).map(function (it) { return { url: it.url, cap: [it.caption, mm.name].filter(Boolean).join(' · ') }; }), +pf.getAttribute('data-pf')); }
        var cf = e.target.closest('[data-cf]');
        if (cf) { var m2 = mById(msEl.getAttribute('data-id')); return lightbox((m2.certificates || []).map(function (it) { return { url: it.url, cap: it.title || 'Сертификат' }; }), +cf.getAttribute('data-cf')); }
        if (e.target.closest('[data-msbook]')) {      // окно мастера сменяется записью к нему — одна запись в истории
          var mid = msEl.getAttribute('data-id'); doCloseMs(true); B.sel = []; B.pick = {}; openBooking({ master: mid }, true);
        }
      });
    }
    msEl.setAttribute('data-id', m.id);
    document.getElementById('ms-t').textContent = SAL.staff_word === 'Мастера' ? 'Мастер' : 'Специалист';
    var svcs = services().filter(function (s) { return does(m, s); });
    document.getElementById('ms-b').innerHTML =
      '<div class="mhero">' + ava(m) + '<div><b>' + esc(m.name) + '</b><span>' + esc([m.role, m.experience_years ? 'опыт ' + years(m.experience_years) : ''].filter(Boolean).join(' · ')) + '</span></div></div>' +
      (m.bio ? '<div class="bio">' + esc(m.bio) + '</div>' : '') +
      ((m.portfolio || []).length ? '<h4>Работы</h4><div class="pfg">' + m.portfolio.map(function (it, i) { return '<button type="button" data-pf="' + i + '"><img src="' + esc(it.url) + '" alt="' + esc(it.caption || '') + '" loading="lazy"></button>'; }).join('') + '</div>' : '') +
      (svcs.length ? '<h4>Услуги</h4>' + svcs.map(function (s) { return '<div class="ml"><div>' + esc(s.title) + '</div><span>' + esc([durText(terms(s, m).dur), priceWith(s, m)].filter(Boolean).join(' · ')) + '</span></div>'; }).join('') : '') +
      ((m.certificates || []).length ? '<h4>Сертификаты</h4><div class="pfg">' + m.certificates.map(function (it, i) { return '<button type="button" data-cf="' + i + '"><img src="' + esc(it.url) + '" alt="' + esc(it.title || '') + '" loading="lazy"></button>'; }).join('') + '</div>' : '');
    document.getElementById('ms-f').innerHTML = '<button class="btn" type="button" data-msbook style="flex:1">' + ic('calendar') + 'Записаться к ' + esc(String(m.name).split(' ')[0]) + '</button>';
    msEl.style.display = 'flex';
    document.documentElement.style.overflow = 'hidden';
    pushLayer('ms');
    requestAnimationFrame(function () { msEl.classList.add('on'); });
  }
  function doCloseMs(keepScroll) {
    if (!msEl || !msEl.classList.contains('on')) return;
    msEl.classList.remove('on');
    if (!keepScroll) document.documentElement.style.overflow = '';
    setTimeout(function () { if (!msEl.classList.contains('on')) msEl.style.display = 'none'; }, 280);
  }

  function share() {
    var url = location.origin + (SAL.page_path || location.pathname);
    if (navigator.share) { navigator.share({ title: SAL.name, text: SAL.tagline || 'Онлайн-запись', url: url }).catch(function () { /* отменили */ }); return; }
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(function () { toast('Ссылка скопирована'); }).catch(function () { window.prompt('Скопируйте ссылку', url); });
  }

  // ── события страницы ──
  document.addEventListener('click', function (e) {
    if (sheet && sheet.contains(e.target)) return;
    if (msEl && msEl.contains(e.target)) return;
    if (lbEl && lbEl.contains(e.target)) return;
    var t = e.target;
    if (t.closest('[data-book]')) { B.preMaster = null; return openBooking({}); }
    if (t.closest('[data-share]')) return share();
    var mc = t.closest('[data-master]');
    if (mc) return masterSheet(mc.getAttribute('data-master'));
    var lb = t.closest('[data-lb]');
    if (lb) return lightbox(gallery(), +lb.getAttribute('data-lb'));
    var cat = t.closest('[data-cat]');
    if (cat) { var q = document.getElementById('svc-q'); return drawServices(cat.getAttribute('data-cat'), q ? q.value : ''); }
    var sv = t.closest('#svc-box [data-svc]');
    if (sv) { B.preMaster = null; B.sel = []; return openBooking({ service: sv.getAttribute('data-svc') }); }
    var a = t.closest('.nav a');
    if (a) { e.preventDefault(); var el = document.querySelector(a.getAttribute('href')); if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 58, behavior: 'smooth' }); }
  });
  document.addEventListener('keydown', function (e) {
    if (lbEl && lbEl.classList.contains('on')) { if (e.key === 'Escape') lbClose(); if (e.key === 'ArrowLeft') lbGo(-1); if (e.key === 'ArrowRight') lbGo(1); return; }
    if (e.key === 'Escape') { if (msEl && msEl.classList.contains('on')) return closeLayer('ms', doCloseMs); if (B.open && MODE === 'page') closeBooking(); }
  });

  // ── старт ──
  function notFound(msg) {
    app.innerHTML = '<div class="nf"><h1>Страница недоступна</h1><p>' + esc(msg || 'Возможно, салон выключил онлайн-запись. Свяжитесь с салоном напрямую.') + '</p>' +
      '<div class="pw">Работает на <a href="https://voksyai.online">Voksy AI</a></div></div>';
  }
  get('').then(function (d) {
    D = d; SAL = d.salon || { name: d.title, hours: [], gallery: [], promos: [], amenities: [], show_prices: true, staff_word: 'Мастера', accent: '#d6336c', address: d.address || '' };
    document.documentElement.style.setProperty('--a', SAL.accent || '#d6336c');
    var tc = document.querySelector('meta[name="theme-color"]'); if (tc) tc.setAttribute('content', SAL.accent || '#d6336c');
    var chatOn = !!(SAL.chat && SAL.chat.enabled && window.SalonChat);
    if (MODE === 'book') {
      // /b/<slug>?chat=1 — только чат (виджет «Написать администратору» на сайте владельца)
      if (QS.get('chat') === '1' && chatOn) {
        document.title = 'Чат — ' + SAL.name;
        window.SalonChat.init({ slug: slug, salon: SAL, mode: 'chat', embed: EMBED, onBook: function () {
          var c = document.querySelector('.sc'); if (c) c.remove();
          openBooking({});
        } });
        return;
      }
      document.title = 'Запись — ' + SAL.name;
      openBooking({ service: QS.get('service') || undefined, master: QS.get('master') || undefined });
      return;
    }
    renderPage();
    if (chatOn) window.SalonChat.init({ slug: slug, salon: SAL, mode: 'page', embed: EMBED, onBook: function () { B.preMaster = null; openBooking({}); } });
    if (QS.get('service') || QS.get('master') || location.hash === '#book') {
      openBooking({ service: QS.get('service') || undefined, master: QS.get('master') || undefined });
    }
  }).catch(function (e) { notFound(e.status === 404 ? '' : e.message); });
})();
