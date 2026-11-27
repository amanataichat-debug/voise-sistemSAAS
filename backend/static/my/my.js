/* «Мои записи» (Voksy Beauty, шаг 2.5) — /m/<токен>: записи клиента в салоне без входа.
   Подтвердить визит, перенести (свободное время салона, своё время не мешает), отменить (правило салона «не позже
   чем за N ч»), записаться снова к тому же мастеру, оценить визит, убрать себя из листа ожидания, написать в чат. */
(function () {
  'use strict';
  var token = (location.pathname.split('/').filter(Boolean)[1] || '').toLowerCase();
  var API = '/api/public/client/' + encodeURIComponent(token);
  var app = document.getElementById('app');
  var MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var MON_S = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  var WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
  var WDL = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
  var S = { data: null, chat: false };
  var SH = null;              // открытая шторка
  var toastTimer = null;

  var IC = {
    phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/>',
    wa: '<path d="M3 21l1.65-3.8a9 9 0 1 1 3.4 2.9L3 21"/><path d="M9 10a.5.5 0 0 0 1 0V9a.5.5 0 0 0-1 0v1a5 5 0 0 0 5 5h1a.5.5 0 0 0 0-1h-1a.5.5 0 0 0 0 1"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    map: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    cal: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    star: '<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>',
    redo: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    hour: '<path d="M5 22h14M5 2h14M17 22v-4.2a2 2 0 0 0-.6-1.4L12 12l-4.4 4.4a2 2 0 0 0-.6 1.4V22M7 2v4.2a2 2 0 0 0 .6 1.4L12 12l4.4-4.4a2 2 0 0 0 .6-1.4V2"/>'
  };
  function ic(n) { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (IC[n] || '') + '</svg>'; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function msg(d) { var m = d && (d.message || d.detail); return typeof m === 'string' ? m : ''; }
  function req(path, method, body) {
    return fetch(API + (path || ''), { method: method || 'GET', cache: 'no-store', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) { var e = new Error(msg(d) || 'Не получилось. Попробуйте ещё раз'); e.status = r.status; throw e; }
          return d;
        });
      });
  }
  function ymd(k) { var p = String(k || '').split('-'); return p.length === 3 ? new Date(Date.UTC(+p[0], +p[1] - 1, +p[2], 6)) : null; }
  function todayKey(off) { var d = new Date(Date.now() + 6 * 3600e3 + (off || 0) * 864e5); return d.toISOString().slice(0, 10); }
  function relDay(k) { return k === todayKey(0) ? 'сегодня' : k === todayKey(1) ? 'завтра' : ''; }
  function dayLong(k) { var d = ymd(k); if (!d) return ''; var r = relDay(k); return (r ? r + ', ' : WDL[d.getUTCDay()] + ', ') + d.getUTCDate() + ' ' + MON[d.getUTCMonth()]; }
  function dayShort(k) { var d = ymd(k); return d ? WD[d.getUTCDay()] + ', ' + d.getUTCDate() + ' ' + MON_S[d.getUTCMonth()] : ''; }
  function money(v) { return v ? Math.round(v).toLocaleString('ru-RU').replace(/,/g, ' ') + ' сом' : ''; }
  function digits(p) { return String(p || '').replace(/\D/g, ''); }
  function svcList(v) { return v.items.map(function (i) { return i.service; }).join(', '); }
  function ava(m) {
    if (!m) return '';
    return '<i class="my-ava"' + (m.color ? ' style="--mc:' + esc(m.color) + '"' : '') + '>' + (m.photo_url ? '<img src="' + esc(m.photo_url) + '" alt="">' : esc(m.initials || '?')) + '</i>';
  }
  function toast(t) {
    var el = document.getElementById('toast');
    el.textContent = t; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.hidden = true; }, 3800);
  }

  // ───────────── экран ─────────────
  function head() {
    var s = S.data.salon, ini = String(s.name || '?').trim().charAt(0).toUpperCase(), acts = [];
    if (s.phone) acts.push('<a class="my-act" href="tel:' + esc(s.phone) + '">' + ic('phone') + 'Позвонить</a>');
    if (s.whatsapp) acts.push('<a class="my-act" href="https://wa.me/' + digits(s.whatsapp) + '" target="_blank" rel="noopener">' + ic('wa') + 'WhatsApp</a>');
    if (S.chat) acts.push('<button type="button" class="my-act" data-act="chat">' + ic('chat') + 'Чат</button>');
    if (s.map_url) acts.push('<a class="my-act" href="' + esc(s.map_url) + '" target="_blank" rel="noopener">' + ic('map') + 'Маршрут</a>');
    return '<section class="my-card"><div class="my-head"><div class="my-logo">' + (s.logo_url ? '<img src="' + esc(s.logo_url) + '" alt="">' : esc(ini)) + '</div>' +
      '<div><div class="my-salon">' + esc(s.name) + '</div>' + (s.address ? '<div class="my-addr">' + esc(s.address) + '</div>' : '') + '</div></div>' +
      (acts.length ? '<div class="my-acts">' + acts.join('') + '</div>' : '') + '</section>';
  }
  function hello() {
    var n = S.data.client.name, up = S.data.upcoming.length;
    return '<div class="my-hello" style="padding:4px 4px 0"><h1>' + (n ? 'Здравствуйте, ' + esc(n) + '!' : 'Ваши записи') + '</h1>' +
      '<p>' + (up ? (up === 1 ? 'У вас одна запись' : 'Записей впереди: ' + up) : 'Предстоящих записей нет') + '</p></div>';
  }
  function statusChip(v) {
    if (v.status === 'confirmed') return '<span class="my-chip ok">' + ic('check').replace('<svg', '<svg width="13" height="13"') + 'Вы подтвердили</span>';
    var r = relDay(v.date);
    return '<span class="my-chip' + (r ? ' warn' : '') + '">' + (r ? 'Ждём вас ' + r : 'Ждём вас') + '</span>';
  }
  function upcomingCard(v) {
    var s = S.data.salon, multi = v.items.length > 1, d = ymd(v.date);
    var items = v.items.map(function (i) {
      return '<li><div style="min-width:0"><div class="sv">' + esc(i.service) + '</div>' +
        (i.master ? '<div class="my-m">' + ava(i.master) + esc(i.master.name) + '</div>' : '') + '</div>' +
        (multi ? '<span class="tm">' + esc(i.time) + '</span>' : '') + '</li>';
    }).join('');
    var meta = [];
    meta.push('<span>до ' + esc(v.end_time) + '</span>');
    if (v.price) meta.push('<span>Стоимость: <b>' + esc(money(v.price)) + '</b></span>');
    if (v.prepay_amount) meta.push('<span>Предоплата ' + esc(money(v.prepay_amount)) + ': ' + (v.prepaid ? '<b style="color:var(--ok)">получена</b>' : '<b>ждём</b>') + '</span>');
    var btns = [];
    if (v.can_confirm) btns.push('<button type="button" class="my-btn wide" data-act="confirm" data-id="' + esc(v.id) + '">' + ic('check') + 'Подтвердить, приду</button>');
    if (v.can_move) btns.push('<button type="button" class="my-btn soft" data-act="move" data-id="' + esc(v.id) + '">' + ic('clock') + 'Перенести</button>');
    if (v.can_cancel) btns.push('<button type="button" class="my-btn ghost" data-act="cancel" data-id="' + esc(v.id) + '">Отменить</button>');
    // подтвердить — на всю ширину; «Перенести» и «Отменить» — в ряд; одна оставшаяся кнопка — тоже на всю ширину
    var rest = btns.length - (v.can_confirm ? 1 : 0);
    if (rest === 1) btns[btns.length - 1] = btns[btns.length - 1].replace(/class="my-btn (soft|ghost)"/, 'class="my-btn $1 wide"');
    var note = v.change_note ? '<div class="my-note">' + esc(v.change_note) + (s.phone ? ' · <a href="tel:' + esc(s.phone) + '">' + esc(s.phone) + '</a>' : '') + '</div>' : '';
    if (s.prepay_info && v.prepay_amount && !v.prepaid) note += '<div class="my-note" style="background:var(--s2);color:var(--t2)">Как внести предоплату: ' + esc(s.prepay_info) + '</div>';
    return '<section class="my-card my-bk"><div class="my-bk-top"><div class="my-when"><b>' + esc(v.time) + '</b><span>' +
      esc(relDay(v.date) || (d ? WD[d.getUTCDay()] : '')) + '<br>' + (d ? d.getUTCDate() + ' ' + MON_S[d.getUTCMonth()] : '') + '</span></div>' +
      '<div class="my-what">' + statusChip(v) + '<ul class="my-items">' + items + '</ul><div class="my-meta">' + meta.join('') + '</div></div></div>' +
      (btns.length ? '<div class="my-bk-btns">' + btns.join('') + '</div>' : '') + note + '</section>';
  }
  function emptyCard() {
    var s = S.data.salon;
    return '<section class="my-card my-empty"><div class="ic">' + ic('cal') + '</div><h2>Записей впереди нет</h2>' +
      '<p>' + (S.data.history.length ? 'Можно записаться снова к своему мастеру — кнопка ниже в истории.' : 'Выберите услугу и удобное время.') + '</p>' +
      (s.book_url ? '<a class="my-btn" href="' + esc(s.book_url) + '">Записаться</a>' : '') + '</section>';
  }
  function waitlistCard(list) {
    if (!list.length) return '';
    return '<div class="my-sec">Лист ожидания</div><section class="my-card">' + list.map(function (e) {
      return '<div class="my-wl"><div class="ic">' + ic('hour') + '</div><div style="min-width:0;flex:1"><b>' + esc(e.service) + '</b>' +
        '<span>' + esc(e.period) + ' · ' + (e.offer ? 'освободилось время' : 'ждём, когда освободится время') + '</span>' +
        (e.offer ? '<a class="my-btn offer" href="' + esc(e.offer.url) + '">Освободилось ' + esc(e.offer.label) + ' — ответить</a>' : '') +
        '<button type="button" class="my-link" data-act="wl-stop" data-id="' + esc(e.id) + '">Больше не нужно</button></div></div>';
    }).join('') + '</section>';
  }
  function historyCard(list) {
    if (!list.length) return '';
    return '<div class="my-sec">Мои визиты</div><section class="my-card my-hist">' + list.map(function (v) {
      var d = ymd(v.date), m = v.items[0] && v.items[0].master, acts = [];
      if (v.status === 'no_show') acts.push('<span class="my-pill my-miss" style="cursor:default">Не пришли</span>');
      if (v.review && v.review.score) acts.push((v.review.url ? '<a class="my-pill" href="' + esc(v.review.url) + '">' : '<span class="my-pill" style="cursor:default">') +
        '<span class="my-stars">' + '★★★★★'.slice(0, v.review.score) + '</span>' + (v.review.url ? ' изменить</a>' : '</span>'));
      else if (v.review && v.review.url) acts.push('<a class="my-pill a" href="' + esc(v.review.url) + '">' + ic('star') + 'Оценить визит</a>');
      if (v.can_repeat) acts.push('<button type="button" class="my-pill a" data-act="repeat" data-id="' + esc(v.id) + '">' + ic('redo') +
        (m ? 'Снова к ' + esc(m.name) : 'Записаться снова') + '</button>');
      return '<div class="my-h"><div class="my-h-d"><b>' + (d ? d.getUTCDate() : '') + '</b><span>' + (d ? MON_S[d.getUTCMonth()] + (d.getUTCFullYear() !== new Date().getFullYear() ? ' ' + d.getUTCFullYear() : '') : '') + '</span></div>' +
        '<div class="my-h-b"><div class="sv">' + esc(svcList(v)) + '</div><div class="sub">' + (m ? esc(m.name) + ' · ' : '') + esc(v.time) +
        (v.price ? ' · ' + esc(money(v.price)) : '') + '</div>' + (acts.length ? '<div class="my-h-acts">' + acts.join('') + '</div>' : '') + '</div></div>';
    }).join('') + '</section>';
  }
  function foot() {
    var s = S.data.salon;
    return '<div class="my-foot">' + (s.page_url ? '<a href="' + esc(s.page_url) + '">Страница салона</a>' : '') +
      (s.book_url && S.data.upcoming.length ? '<a href="' + esc(s.book_url) + '">Записаться ещё</a>' : '') + '</div>' +
      '<div class="my-pw">Ссылка личная — не пересылайте её другим. Работает на <a href="https://voksyai.online" target="_blank" rel="noopener">Voksy AI</a></div>';
  }
  function draw() {
    var d = S.data;
    app.innerHTML = head() + hello() + (d.upcoming.length ? d.upcoming.map(upcomingCard).join('') : emptyCard()) +
      waitlistCard(d.waitlist) + historyCard(d.history) + foot();
  }
  function fail(e) {
    app.innerHTML = '<section class="my-card my-errbox"><h1>' + (e && e.status === 404 ? 'Ссылка не открылась' : 'Не получилось загрузить') + '</h1>' +
      '<p>' + esc((e && e.message) || 'Попробуйте обновить страницу') + '</p>' +
      (e && e.status !== 404 ? '<button type="button" class="my-btn" style="margin-top:14px;width:100%" data-act="reload">Обновить</button>' : '') + '</section>';
  }
  function load() {
    return req('').then(function (d) {
      S.data = d;
      if (d.salon.accent) document.documentElement.style.setProperty('--a', d.salon.accent);
      var chatOn = !!(d.salon.chat && d.salon.chat.enabled && window.SalonChat);
      if (chatOn && !S.chat) {
        S.chat = true;
        try {
          window.SalonChat.init({ slug: d.salon.slug, salon: { name: d.salon.name, chat: d.salon.chat }, mode: 'page',
            onBook: function () { if (d.salon.book_url) location.href = d.salon.book_url; } });
        } catch (e) { S.chat = false; }
      }
      draw();
    }).catch(fail);
  }
  function findVisit(id, list) {
    var arr = list || S.data.upcoming.concat(S.data.history);
    for (var i = 0; i < arr.length; i++) if (arr[i].id === id) return arr[i];
    return null;
  }

  // ───────────── шторка ─────────────
  function openSheet(html) {
    closeSheet(true);
    var scrim = document.createElement('div'); scrim.className = 'my-scrim';
    var sh = document.createElement('div'); sh.className = 'my-sheet'; sh.setAttribute('role', 'dialog'); sh.setAttribute('aria-modal', 'true');
    sh.innerHTML = html;
    document.body.appendChild(scrim); document.body.appendChild(sh);
    document.documentElement.classList.add('bk-open');
    requestAnimationFrame(function () { scrim.classList.add('on'); sh.classList.add('on'); });
    scrim.addEventListener('click', function () { closeSheet(); });
    SH = { el: sh, scrim: scrim, pushed: false };
    try { history.pushState({ mySheet: 1 }, ''); SH.pushed = true; } catch (e) { /* ignore */ }
    return sh;
  }
  function closeSheet(silent) {
    if (!SH) return;
    var cur = SH; SH = null;
    cur.el.classList.remove('on'); cur.scrim.classList.remove('on');
    setTimeout(function () { cur.el.remove(); cur.scrim.remove(); }, 260);
    document.documentElement.classList.remove('bk-open');
    if (cur.pushed && !silent) { try { history.back(); } catch (e) { /* ignore */ } }
  }
  window.addEventListener('popstate', function () { if (SH) { SH.pushed = false; closeSheet(true); } });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && SH) closeSheet(); });

  function sheetHead(title, sub) {
    return '<div class="my-sh-grip"></div><div class="my-sh-head"><div><h2>' + esc(title) + '</h2>' + (sub ? '<p>' + esc(sub) + '</p>' : '') + '</div>' +
      '<button type="button" class="my-x" data-sh="close" aria-label="Закрыть">' + ic('x') + '</button></div>';
  }

  // Выбор времени: перенос (mode=move) или «записаться снова» (mode=repeat)
  function pickTime(mode, v) {
    var multi = v.items.length > 1, m = v.items[0] && v.items[0].master;
    var P = { mode: mode, v: v, any: false, days: [], day: null, start: null, busy: false, err: '', loading: true };
    var title = mode === 'move' ? 'Перенести запись' : 'Записаться снова';
    var sub = svcList(v) + (m && !multi ? ' · ' + m.name : '') + (mode === 'move' ? ' · сейчас: ' + dayShort(v.date) + ', ' + v.time : '');
    var sh = openSheet(sheetHead(title, sub) + '<div class="my-sh-body" data-sh="body"></div><div class="my-sh-foot" data-sh="foot"></div>');
    function body() { return sh.querySelector('[data-sh="body"]'); }
    function render() {
      var h = '';
      if (!multi && m) h += '<label class="my-tg"><input type="checkbox" data-sh="any"' + (P.any ? ' checked' : '') + '><span>Подойдёт любой мастер — покажем больше времени</span></label>';
      if (P.loading) h += '<div class="sk" style="height:66px;margin-top:8px"></div><div class="sk" style="height:140px"></div>';
      else {
        h += '<div class="my-days">' + P.days.map(function (d) {
          var dd = ymd(d.date), has = d.slots.length > 0;
          return '<button type="button" class="my-day' + (d.date === P.day ? ' on' : '') + (has ? '' : ' none') + '" data-day="' + esc(d.date) + '">' +
            '<span>' + esc(relDay(d.date) === 'сегодня' ? 'сег' : WD[dd.getUTCDay()]) + '</span><b>' + dd.getUTCDate() + '</b><i></i></button>';
        }).join('') + '</div>';
        var cur = P.days.filter(function (d) { return d.date === P.day; })[0];
        if (!cur || !cur.slots.length) h += '<div class="my-nos">' + (P.days.some(function (d) { return d.slots.length; }) ? 'В этот день свободного времени нет — выберите другой' :
          'Свободного времени в ближайшие дни нет.' + (S.data.salon.phone ? ' Позвоните салону: ' + esc(S.data.salon.phone) : '')) + '</div>';
        else {
          var groups = [['Утро', function (t) { return t < '12:00'; }], ['День', function (t) { return t >= '12:00' && t < '17:00'; }], ['Вечер', function (t) { return t >= '17:00'; }]];
          groups.forEach(function (g) {
            var sl = cur.slots.filter(function (s) { return g[1](s.time); });
            if (!sl.length) return;
            h += '<div class="my-part">' + g[0] + '</div><div class="my-times">' + sl.map(function (s) {
              return '<button type="button" class="my-time' + (s.start === P.start ? ' on' : '') + '" data-start="' + esc(s.start) + '">' + esc(s.time) +
                (s.end_time ? '<small>до ' + esc(s.end_time) + '</small>' : '') + '</button>';
            }).join('') + '</div>';
          });
        }
      }
      if (P.err) h += '<p class="my-err">' + esc(P.err) + '</p>';
      body().innerHTML = h;
      var sel = null; P.days.forEach(function (d) { d.slots.forEach(function (s) { if (s.start === P.start) sel = { d: d, s: s }; }); });
      sh.querySelector('[data-sh="foot"]').innerHTML = '<button type="button" class="my-btn" data-sh="go"' + (sel && !P.busy ? '' : ' disabled') + '>' +
        (P.busy ? (mode === 'move' ? 'Переносим…' : 'Записываем…') : sel ? (mode === 'move' ? 'Перенести на ' : 'Записаться на ') + dayShort(sel.d.date) + ', ' + sel.s.time :
          'Выберите время') + '</button>';
      var on = body().querySelector('.my-day.on'); if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest', inline: 'center' });
    }
    function load() {
      P.loading = true; P.err = ''; render();
      req('/slots?booking_id=' + encodeURIComponent(v.id) + '&mode=' + mode + '&days=' + (multi ? 7 : 14) + (P.any ? '&any_master=true' : ''))
        .then(function (d) {
          P.loading = false; P.days = d.days || [];
          if (!P.days.some(function (x) { return x.date === P.day && x.slots.length; })) {
            var first = P.days.filter(function (x) { return x.slots.length; })[0];
            P.day = first ? first.date : (P.days[0] && P.days[0].date);
          }
          render();
        }).catch(function (e) { P.loading = false; P.err = e.message; render(); });
    }
    sh.addEventListener('click', function (e) {
      var t = e.target.closest('[data-day],[data-start],[data-sh]');
      if (!t) return;
      if (t.dataset.day) { P.day = t.dataset.day; P.start = null; render(); return; }
      if (t.dataset.start) { P.start = t.dataset.start; render(); return; }
      if (t.dataset.sh === 'close') { closeSheet(); return; }
      if (t.dataset.sh === 'go' && P.start && !P.busy) {
        P.busy = true; P.err = ''; render();
        var call = mode === 'move' ? req('/bookings/' + encodeURIComponent(v.id) + '/reschedule', 'POST', { start: P.start, any_master: P.any })
          : req('/repeat', 'POST', { booking_id: v.id, start: P.start, any_master: P.any });
        call.then(function (r) {
          closeSheet(); toast((mode === 'move' ? 'Перенесли на ' : 'Вы записаны на ') + (r.label || '')); load_();
        }).catch(function (er) { P.busy = false; P.err = er.message; P.start = null; load(); });
      }
    });
    sh.addEventListener('change', function (e) {
      if (e.target.matches('[data-sh="any"]')) { P.any = e.target.checked; P.start = null; load(); }
    });
    load();
  }
  var load_ = load;

  function cancelSheet(v) {
    var reason = '', busy = false, err = '';
    var REASONS = ['Не успеваю', 'Заболел(а)', 'Планы изменились', 'Запишусь позже'];
    var sh = openSheet(sheetHead('Отменить запись?', dayLong(v.date) + ', ' + v.time) + '<div class="my-sh-body" data-sh="body"></div><div class="my-sh-foot" data-sh="foot"></div>');
    function render() {
      sh.querySelector('[data-sh="body"]').innerHTML = '<div class="my-sum"><b>' + esc(svcList(v)) + '</b>' +
        (v.items[0] && v.items[0].master ? esc(v.items[0].master.name) + ' · ' : '') + esc(v.time) + (v.price ? ' · ' + esc(money(v.price)) : '') + '</div>' +
        '<div class="my-part" style="margin-top:14px">Почему — если хотите</div><div class="my-reasons">' + REASONS.map(function (r) {
          return '<button type="button" class="my-reason' + (r === reason ? ' on' : '') + '" data-reason="' + esc(r) + '">' + esc(r) + '</button>';
        }).join('') + '</div>' + (v.prepay_amount && v.prepaid ? '<p style="font-size:13.5px;color:var(--t3);margin:8px 0 0">Про возврат предоплаты салон напишет вам сам.</p>' : '') +
        (err ? '<p class="my-err">' + esc(err) + '</p>' : '');
      sh.querySelector('[data-sh="foot"]').innerHTML = '<button type="button" class="my-btn danger" data-sh="go"' + (busy ? ' disabled' : '') + '>' + (busy ? 'Отменяем…' : 'Да, отменить запись') + '</button>' +
        (v.can_move ? '<button type="button" class="my-btn soft" data-sh="move">Лучше перенести</button>' : '<button type="button" class="my-btn ghost" data-sh="close">Не отменять</button>');
    }
    sh.addEventListener('click', function (e) {
      var t = e.target.closest('[data-reason],[data-sh]');
      if (!t) return;
      if (t.dataset.reason) { reason = reason === t.dataset.reason ? '' : t.dataset.reason; render(); return; }
      if (t.dataset.sh === 'close') { closeSheet(); return; }
      if (t.dataset.sh === 'move') { closeSheet(true); pickTime('move', v); return; }
      if (t.dataset.sh === 'go' && !busy) {
        busy = true; err = ''; render();
        req('/bookings/' + encodeURIComponent(v.id) + '/cancel', 'POST', { reason: reason || null })
          .then(function () { closeSheet(); toast('Запись отменена'); load(); })
          .catch(function (er) { busy = false; err = er.message; render(); });
      }
    });
    render();
  }

  // ───────────── нажатия на экране ─────────────
  app.addEventListener('click', function (e) {
    var t = e.target.closest('[data-act]');
    if (!t) return;
    var act = t.dataset.act, v = t.dataset.id ? findVisit(t.dataset.id) : null;
    if (act === 'reload') { load(); return; }
    if (act === 'chat') { try { window.SalonChat.open(); } catch (er) { /* ignore */ } return; }
    if (act === 'move' && v) { pickTime('move', v); return; }
    if (act === 'repeat' && v) { pickTime('repeat', v); return; }
    if (act === 'cancel' && v) { cancelSheet(v); return; }
    if (act === 'confirm' && v) {
      t.disabled = true;
      req('/bookings/' + encodeURIComponent(v.id) + '/confirm', 'POST').then(function () { toast('Спасибо! Ждём вас'); load(); })
        .catch(function (er) { t.disabled = false; toast(er.message); });
      return;
    }
    if (act === 'wl-stop') {
      t.disabled = true;
      req('/waitlist/' + encodeURIComponent(t.dataset.id) + '/stop', 'POST').then(function () { toast('Убрали из листа ожидания'); load(); })
        .catch(function (er) { t.disabled = false; toast(er.message); });
    }
  });

  load();
})();
