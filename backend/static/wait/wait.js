/* Лист ожидания (Voksy Beauty, шаг 2.4) — /w/<токен>: «Освободилось время». Записаться / Не подходит / Больше не нужно.
   Окно не держится: пока клиент думает, его может занять кто-то другой — тогда честно говорим и ждём следующее. */
(function () {
  'use strict';
  var token = (location.pathname.split('/').filter(Boolean)[1] || '').toLowerCase();
  var API = '/api/public/waitlist/' + encodeURIComponent(token);
  var app = document.getElementById('app');
  var MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var WD = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
  var S = { data: null, busy: false, done: null, err: '' };
  var timer = null;

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function dayText(k) { var p = String(k || '').split('-'); if (p.length !== 3) return ''; var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2], 6)); return WD[d.getUTCDay()] + ', ' + (+p[2]) + ' ' + MON[+p[1] - 1]; }
  function msg(d) { var m = d && (d.message || d.detail); return typeof m === 'string' ? m : ''; }
  function req(method, body) {
    return fetch(API, { method: method, cache: 'no-store', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(msg(d) || 'Не получилось. Попробуйте ещё раз'); return d; }); });
  }

  function head() {
    var s = S.data.salon, ini = String(s.name || '?').trim().charAt(0).toUpperCase();
    return '<header class="wt-head"><div class="wt-logo">' + (s.logo_url ? '<img src="' + esc(s.logo_url) + '" alt="">' : esc(ini)) + '</div>' +
      '<div><div class="wt-salon">' + esc(s.name) + '</div>' + (s.address ? '<div class="wt-addr">' + esc(s.address) + '</div>' : '') + '</div></header>';
  }
  function foot(links) {
    var s = S.data.salon;
    return '<div class="wt-links">' + links.filter(Boolean).join('') + (s.page_url ? '<a class="wt-link" href="' + esc(s.page_url) + '">Страница салона</a>' : '') + '</div>';
  }

  function viewOffer() {
    var o = S.data.offer, e = S.data.entry, m = o.master;
    return '<div class="wt-card">' + head() +
      '<div class="wt-badge">Лист ожидания</div><h1>Освободилось время!</h1>' +
      '<div class="wt-slot"><div class="wt-when"><b>' + esc(o.time) + '</b><span>' + esc(dayText(o.date)) + '</span></div>' +
      '<div class="wt-what"><b>' + esc(e.service) + '</b>' + (m ? '<span class="wt-m"><i class="wt-ava"' + (m.color ? ' style="--mc:' + esc(m.color) + '"' : '') + '>' +
        (m.photo_url ? '<img src="' + esc(m.photo_url) + '" alt="">' : esc(m.initials || '?')) + '</i>мастер ' + esc(m.name) + '</span>' : '') + '</div></div>' +
      '<div class="wt-left" id="wt-left">' + leftText(o.minutes_left) + '</div>' +
      (S.err ? '<p class="wt-err">' + esc(S.err) + '</p>' : '') +
      '<button type="button" class="wt-btn" data-act="accept"' + (S.busy ? ' disabled' : '') + '>' + (S.busy ? 'Записываем…' : 'Записаться') + '</button>' +
      '<button type="button" class="wt-btn ghost" data-act="decline"' + (S.busy ? ' disabled' : '') + '>Не подходит — жду другое время</button>' +
      '<button type="button" class="wt-link-btn" data-act="stop"' + (S.busy ? ' disabled' : '') + '>Больше не нужно</button></div>';
  }
  function leftText(n) {
    if (n == null) return '';
    return n > 0 ? 'Ответить можно ещё ' + n + ' мин — потом предложим это время следующему в очереди' : 'Время ответа почти вышло';
  }
  function viewState() {
    var e = S.data.entry, s = S.data.salon, b = S.data.booked, d = S.done;
    var title, text, links = [];
    if (d === 'booked' || e.status === 'booked') {
      title = 'Вы записаны!'; text = (d && S.label) || (b && b.label) || ''; text = text ? text + ' · ' + e.service : e.service;
      text += '. Напоминание придёт заранее.';
    } else if (d === 'stop' || e.status === 'cancelled') {
      title = 'Убрали вас из листа ожидания'; text = 'Если снова захотите — запишитесь на странице салона.';
      if (s.book_url) links.push('<a class="wt-btn" href="' + esc(s.book_url) + '">Записаться</a>');
    } else if (d === 'decline') {
      title = 'Хорошо, ждём другое время'; text = 'Вы в листе ожидания: ' + e.service + ', ' + e.period + '. Освободится — сразу напишем.';
    } else if (e.status === 'expired') {
      title = 'Лист ожидания закрыт'; text = 'Желаемые дни прошли. Выберите новое время на странице салона.';
      if (s.book_url) links.push('<a class="wt-btn" href="' + esc(s.book_url) + '">Выбрать время</a>');
    } else {
      title = 'Вы в листе ожидания'; text = e.service + ', ' + e.period + '. Освободится подходящее время — напишем, ответить можно 30 минут.';
      links.push('<button type="button" class="wt-link-btn" data-act="stop">Больше не нужно</button>');
    }
    return '<div class="wt-card">' + head() + '<div class="wt-state' + (d === 'booked' || e.status === 'booked' ? ' ok' : '') + '"><h1>' + esc(title) + '</h1><p>' + esc(text) + '</p>' +
      (S.err ? '<p class="wt-err">' + esc(S.err) + '</p>' : '') + '</div>' + foot(links) + '</div>';
  }
  function draw() {
    clearInterval(timer);
    if (!S.done && S.data.offer) {
      app.innerHTML = viewOffer();
      var until = new Date(S.data.offer.expires_at).getTime();
      timer = setInterval(function () {
        var n = Math.max(0, Math.ceil((until - Date.now()) / 60000)), el = document.getElementById('wt-left');
        if (el) el.textContent = leftText(n);
        if (until <= Date.now()) { clearInterval(timer); load(); }
      }, 15000);
    } else app.innerHTML = viewState();
  }
  function act(a) {
    if (S.busy) return;
    S.busy = true; S.err = ''; if (S.data.offer) draw();
    req('POST', { action: a }).then(function (d) {
      S.busy = false; S.done = a === 'accept' ? 'booked' : a; S.label = d.label || '';
      draw(); window.scrollTo({ top: 0, behavior: 'smooth' });
    }).catch(function (e) {
      S.busy = false; S.err = e.message;
      return load(true);
    });
  }
  function load(keepErr) {
    return req('GET').then(function (d) {
      S.data = d; if (!keepErr) S.err = '';
      document.documentElement.style.setProperty('--a', d.salon.accent || '#d6336c');
      document.title = (d.offer ? 'Освободилось время — ' : 'Лист ожидания — ') + d.salon.name;
      draw();
    }).catch(function (e) {
      app.innerHTML = '<div class="wt-card wt-errbox"><h1>Не получилось открыть</h1><p>' + esc(e.message) + '</p><div class="wt-pw">Работает на <a href="https://voksyai.online">Voksy AI</a></div></div>';
    });
  }
  app.addEventListener('click', function (e) { var b = e.target.closest('[data-act]'); if (b && !b.disabled) act(b.getAttribute('data-act')); });
  load();
})();
