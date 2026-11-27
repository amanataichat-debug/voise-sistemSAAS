/**
 * Онлайн-запись (ветка 1811-niche-admin-speed): /static/booking.html.
 *
 * Компьютер: день — сетка по мастерам (клик по пустому месту = записать на это время), неделя —
 * семь колонок. Телефон: лента дней и список записей дня, кнопка «Записать». Запись: клиент
 * (поиск по CRM или имя + телефон), услуга из каталога, мастер, свободное время. Карточка записи:
 * подтвердил / пришёл / не пришёл / отменить / перенести, ссылки «позвонить» и «написать».
 * Настройки: правила записи, ссылка для клиентов /b/<slug>. Мастера (фото, график, услуги и цены,
 * работы, вход в кабинет мастера) — экран «Мастера» (booking/masters.js, Voksy Beauty шаг 1.2).
 * Прайс (категории, цена «от–до», подготовка и перерыв, кто делает, предоплата) — экран «Услуги»
 * (booking/services.js, шаг 1.3). В сетке дня подготовка и перерыв видны полосками у записи.
 * Журнал v2 (шаг 1.4): виды День · Неделя · Месяц; на компьютере запись перетаскивается мышью (время и
 * мастер) и растягивается за нижний край, с «Отменить» в уведомлении; клик по пустому месту — «Записать»
 * или «Заблокировать время»; блоки (перерыв, обучение, отпуск, больничный) — серые плашки; визит из
 * нескольких услуг — «+ Ещё услуга» в окне записи; статус «Завершён»; журнал обновляется сам у всех,
 * кто его открыл (поток /api/booking/stream), опрос раз в минуту — запасной.
 * API: /api/booking/* (services/booking_service.py — одна логика с ботом, звонком и ссылкой).
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var TZ = 'Asia/Bishkek';
  var PX = 1.15;   // пикселей на минуту в сетке дня
  var WD = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
  var MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var STATUS = { booked: 'Записан', confirmed: 'Подтвердил', arrived: 'Пришёл', completed: 'Завершён', no_show: 'Не пришёл', cancelled: 'Отменена' };
  var MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  var BLOCK_KINDS = [['break', 'Перерыв'], ['block', 'Не записывать'], ['training', 'Обучение'], ['vacation', 'Отпуск'], ['sick', 'Больничный']];
  var SNAP = 5;   // шаг перетаскивания и растяжки, минуты
  function visited(s) { return s === 'arrived' || s === 'completed'; }
  function movable(b) { return b.status === 'booked' || b.status === 'confirmed' || b.status === 'arrived'; }
  // Касса (шаг 3.1): салон принимает оплату через кассу — у визита «оплачено / частично / не оплачено»
  function payState(b) {
    if (!st.data || !st.data.cash_on || !visited(b.status) || !(b.price > 0)) return '';
    var paid = b.paid || 0;
    return paid + 0.004 >= b.price ? 'ok' : paid > 0 ? 'part' : 'no';
  }
  function payMark(b) {
    var s2 = payState(b);
    if (!s2) return '';
    var t = s2 === 'ok' ? 'Оплачено' : s2 === 'part' ? 'Оплачено ' + money(b.paid) + ' из ' + money(b.price) : 'Не оплачено';
    return '<span class="bk-pp ' + s2 + '" title="' + esc(t) + '">' + icon(s2 === 'ok' ? 'check' : 'banknote') + '</span>';
  }
  var st = { day: null, view: 'day', prevView: 'day', win: null, data: null, filter: '' };
  var M = function () { return window.BookingMasters; };
  var root, esc, icon;

  // ── даты (по Бишкеку) ──
  function keyOf(d) { return d.toLocaleDateString('en-CA', { timeZone: TZ }); }
  function parseKey(k) { var p = k.split('-'); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2], 6)); }  // полдень Бишкека
  function addDays(k, n) { var d = parseKey(k); d.setUTCDate(d.getUTCDate() + n); return keyOf(d); }
  function weekday(k) { return (parseKey(k).getUTCDay() + 6) % 7; }
  function monday(k) { return addDays(k, -weekday(k)); }
  function hm(iso) { return new Date(iso).toLocaleTimeString('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }); }
  function minutesOf(iso) { var t = hm(iso).split(':'); return +t[0] * 60 + +t[1]; }
  function dayTitle(k) {
    var d = parseKey(k), today = keyOf(new Date());
    var rel = k === today ? 'Сегодня' : k === addDays(today, 1) ? 'Завтра' : k === addDays(today, -1) ? 'Вчера' : '';
    var full = WD[weekday(k)] + ', ' + d.getUTCDate() + ' ' + MON[d.getUTCMonth()];
    return { main: rel || full, sub: rel ? full : '' };
  }
  function money(v) { return v == null ? '—' : Number(v).toLocaleString('ru-RU') + ' сом'; }
  function toMin(t) { var p = String(t || '0:0').split(':'); return +p[0] * 60 + +(p[1] || 0); }
  function isPhone() { return window.matchMedia('(max-width: 760px)').matches; }

  // ── загрузка ──
  function firstOfMonth(k) { return k.slice(0, 8) + '01'; }
  function addMonths(k, n) {
    var p = k.split('-'), y = +p[0], m = +p[1] - 1 + n;
    y += Math.floor(m / 12); m = ((m % 12) + 12) % 12;
    return y + '-' + String(m + 1).padStart(2, '0') + '-01';
  }
  // Окно данных: неделя (день и неделя) или 6 недель сетки месяца
  function winOf() {
    if (st.view === 'month') return { start: monday(firstOfMonth(st.day)), days: 42 };
    return { start: monday(st.day), days: 7 };
  }
  function load(force) {
    var w = winOf(), key = w.start + ':' + w.days;
    if (!force && st.win === key && st.data) { render(); return Promise.resolve(); }
    var path = '/api/booking/overview?date=' + w.start + '&days=' + w.days;
    return S.apiSWR(path, function (r) {
      if (!r.ok) { root.innerHTML = '<div class="bk-empty">' + esc(S.apiError(r)) + '</div>'; S.ready(); return; }
      st.data = r.data; st.win = key; st.loadedAt = Date.now();
      if (!drag) render();
      S.ready();
    });
  }
  function reload() { S.bust(); return load(true); }

  function resources() { return (st.data && st.data.resources) || []; }
  function resById(id) { return resources().filter(function (r) { return r.id === id; })[0]; }
  function colorOf(b) { var r = resById(b.resource_id); return (r && r.color) || '#2a5ce8'; }
  function dayBookings(k, all) {
    return ((st.data && st.data.bookings) || []).filter(function (b) {
      return keyOf(new Date(b.start)) === k && (all || !st.filter || b.resource_id === st.filter);
    });
  }
  function bookingById(id) { return ((st.data && st.data.bookings) || []).filter(function (x) { return x.id === id; })[0]; }
  function roomName(id) { var r = ((st.data && st.data.rooms) || []).filter(function (x) { return x.id === id; })[0]; return r ? r.name : ''; }
  // Блоки дня (у мастера или общие) — в минутах дня по Бишкеку, обрезанные границами дня
  function dayBlocks(k, rid) {
    var a0 = new Date(k + 'T00:00:00+06:00').getTime(), a1 = a0 + 864e5;
    return ((st.data && st.data.blocks) || []).filter(function (x) {
      var a = new Date(x.start).getTime(), b = new Date(x.end).getTime();
      return a < a1 && b > a0 && (rid === undefined || !x.resource_id || x.resource_id === rid);
    }).map(function (x) {
      var a = Math.max(new Date(x.start).getTime(), a0), b = Math.min(new Date(x.end).getTime(), a1);
      return Object.assign({}, x, { m0: Math.round((a - a0) / 60000), m1: Math.round((b - a0) / 60000) });
    });
  }
  // Записи периода, который на экране (для показателей сверху)
  function periodBookings() {
    var all = (st.data && st.data.bookings) || [];
    var keys = {};
    if (st.view === 'week') { for (var i = 0; i < 7; i++) keys[addDays(monday(st.day), i)] = 1; }
    else if (st.view === 'month') { var f = firstOfMonth(st.day); for (var d = f; d.slice(0, 7) === f.slice(0, 7); d = addDays(d, 1)) keys[d] = 1; }
    else keys[st.day] = 1;
    return all.filter(function (b) { return keys[keyOf(new Date(b.start))] && (!st.filter || b.resource_id === st.filter); });
  }

  // ── каркас ──
  function mctx() {
    return { data: function () { return st.data; }, root: root, canSetup: canSetup(), isOwner: !S.staff(),
      back: function () { st.view = st.prevView || 'day'; render(); window.scrollTo(0, 0); },
      refresh: function () { render(); }, reload: function () { reload(); } };
  }
  function canSetup() { return S.staffCan ? S.staffCan('booking_setup') : true; }
  function canServices() { return S.staffCan ? (S.staffCan('booking_setup') || S.staffCan('catalog')) : true; }
  function openMasters() {
    if (!isScreen(st.view)) st.prevView = st.view;
    st.view = 'masters'; render(); window.scrollTo(0, 0);
  }
  function openServices() {
    if (!isScreen(st.view)) st.prevView = st.view;
    st.view = 'services'; render(); window.scrollTo(0, 0);
  }
  function syncWaitBadge() {
    var n = (st.data && st.data.waitlist) || 0, el = document.getElementById('bk-wait-n');
    if (!el) return;
    el.hidden = !n; el.textContent = n;
  }
  function isScreen(v) { return v === 'masters' || v === 'services' || v === 'page' || v === 'waitlist'; }
  // Лист ожидания (шаг 2.4): экран — booking/waitlist.js
  function openWaitlist() {
    if (!isScreen(st.view)) st.prevView = st.view;
    st.view = 'waitlist'; render(); window.scrollTo(0, 0);
  }
  function wctx() {
    return { root: root, data: function () { return st.data; },
      back: function () { st.view = st.prevView || 'day'; render(); window.scrollTo(0, 0); load(true); } };
  }
  function toWaitlist(pre) {
    if (!window.BookingWaitlist) return;
    pre.data = st.data;
    BookingWaitlist.addDialog(pre, function () { load(true); });
  }
  // Страница салона /beauty/<slug> (шаг 1.5): конструктор — booking/page.js
  function openPage() {
    if (!isScreen(st.view)) st.prevView = st.view;
    st.view = 'page'; render(); window.scrollTo(0, 0);
  }
  function pctx() {
    return { root: root, canEdit: canSetup(),
      back: function () { st.view = st.prevView || 'day'; render(); window.scrollTo(0, 0); },
      openMasters: openMasters,
      onChange: function () { S.bust(); } };
  }
  function sctx() {
    return { root: root, canEdit: canServices(),
      back: function () { st.view = st.prevView || 'day'; render(); window.scrollTo(0, 0); },
      onChange: function () { S.bust(); st.data && load(true); } };
  }
  function editMaster(id) {
    var r = resById(id);
    if (!r || !M()) return;
    if (!canSetup()) { st.filter = st.filter === id ? '' : id; render(); return; }
    M().bind(mctx()); M().editor(r);
  }
  function ava(r, size) { return M() ? M().ava(r, size) : '<span class="dot" style="background:' + esc(r.color) + '"></span>'; }

  function render() {
    if (st.view === 'masters' && M()) { document.body.classList.add('bk-masters-on'); M().open(mctx()); return; }
    if (st.view === 'services' && window.BookingServices) { document.body.classList.add('bk-masters-on'); BookingServices.open(sctx()); return; }
    if (st.view === 'page' && window.BookingPage) { document.body.classList.add('bk-masters-on'); BookingPage.open(pctx()); return; }
    if (st.view === 'waitlist' && window.BookingWaitlist) { document.body.classList.add('bk-masters-on'); BookingWaitlist.open(wctx()); return; }
    document.body.classList.remove('bk-masters-on');
    syncWaitBadge();
    var d = dayTitle(st.day);
    if (st.view === 'month' && !isPhone()) { var pk = parseKey(st.day); d = { main: MONTHS[pk.getUTCMonth()] + ' ' + pk.getUTCFullYear(), sub: '' }; }
    else if (st.view === 'week' && !isPhone()) {
      var m0 = monday(st.day), m6 = addDays(m0, 6), p0 = parseKey(m0), p6 = parseKey(m6);
      d = { main: p0.getUTCDate() + (p0.getUTCMonth() === p6.getUTCMonth() ? '' : ' ' + MON[p0.getUTCMonth()]) + ' – ' + p6.getUTCDate() + ' ' + MON[p6.getUTCMonth()], sub: 'неделя' };
    }
    var list = dayBookings(st.day);
    var plist = isPhone() ? list : periodBookings();
    var active = plist.filter(function (b) { return b.status !== 'cancelled'; });
    var arrived = plist.filter(function (b) { return visited(b.status); });
    var noshow = plist.filter(function (b) { return b.status === 'no_show'; });
    var revenue = arrived.reduce(function (s, b) { return s + (Number(b.price) || 0); }, 0);
    var html =
      '<div class="bk-bar"><div class="bk-nav">' +
      '<button class="btn btn-sm btn-icon" type="button" data-nav="-1" aria-label="Назад">' + icon('chevron-left') + '</button>' +
      '<button class="btn btn-sm" type="button" data-nav="0">Сегодня</button>' +
      '<button class="btn btn-sm btn-icon" type="button" data-nav="1" aria-label="Вперёд">' + icon('chevron-right') + '</button></div>' +
      '<div class="bk-date">' + esc(d.main) + (d.sub ? '<small>' + esc(d.sub) + '</small>' : '') + '</div>' +
      '<div class="bk-spacer"></div>' +
      (resources().length > 1 ? '<select class="select-native bk-desk" id="bk-filter" style="max-width:200px"><option value="">Все мастера</option>' +
        resources().map(function (r) { return '<option value="' + esc(r.id) + '"' + (st.filter === r.id ? ' selected' : '') + '>' + esc(r.name) + '</option>'; }).join('') + '</select>' : '') +
      '<button class="btn btn-sm btn-icon" type="button" data-blocknew title="Перерыв, отпуск, блок времени" aria-label="Заблокировать время">' + icon('circle-minus') + '</button>' +
      '<div class="bk-seg"><button type="button" data-view="day" class="' + (st.view === 'day' ? 'on' : '') + '">День</button>' +
      '<button type="button" data-view="week" class="' + (st.view === 'week' ? 'on' : '') + '">Неделя</button>' +
      '<button type="button" data-view="month" class="' + (st.view === 'month' ? 'on' : '') + '">Месяц</button></div></div>' +
      '<div class="bk-kpis">' +
      kpi('Записей', active.length) + kpi('Пришли', arrived.length, 'good') + kpi('Не пришли', noshow.length, noshow.length ? 'bad' : '') +
      kpi('Выручка', revenue ? revenue.toLocaleString('ru-RU') + ' сом' : '—') + '</div>' +
      '<div class="bk-days" id="bk-days">' + dayStrip() + '</div>' +
      (resources().length > 1 ? '<div class="bk-mchips">' + '<button type="button" class="bk-mchip' + (!st.filter ? ' on' : '') + '" data-mf=""><span class="bk-ava all" style="--s:30px">' + icon('users') + '</span><span>Все</span></button>' +
        resources().map(function (r) {
          var n = dayBookings(st.day, true).filter(function (b) { return b.resource_id === r.id && b.status !== 'cancelled'; }).length;
          return '<button type="button" class="bk-mchip' + (st.filter === r.id ? ' on' : '') + '" data-mf="' + esc(r.id) + '">' + ava(r, 30) + '<span>' + esc(String(r.name).split(' ')[0]) + '</span>' + (n ? '<i>' + n + '</i>' : '') + '</button>';
        }).join('') + '</div>' : '') +
      '<div class="bk-agenda" id="bk-agenda">' + agenda(list) + '</div>' +
      '<div class="bk-desk">' + (st.view === 'month' ? monthView() : st.view === 'week' ? weekView() : dayGrid(list)) + '</div>';
    // Перерисовка того же дня (живое обновление, правка) не сбрасывает прокрутку сетки и ленты дней
    var key = st.view + '|' + st.day + '|' + st.filter, same = st.drawn === key;
    var oldSc = root.querySelector('.bk-scroll'), oldStrip = root.querySelector('.bk-days');
    var keep = same && oldSc ? { t: oldSc.scrollTop, l: oldSc.scrollLeft } : null;
    var keepStrip = same && oldStrip ? oldStrip.scrollLeft : null;
    root.innerHTML = html;
    st.drawn = key;
    var strip = root.querySelector('.bk-days'), cur = root.querySelector('.bk-dpill.on');
    if (keepStrip != null && strip) strip.scrollLeft = keepStrip;
    else if (cur && strip) strip.scrollLeft = Math.max(0, cur.offsetLeft - (strip.clientWidth - cur.offsetWidth) / 2);
    var sc = root.querySelector('.bk-scroll');
    if (sc && keep) { sc.scrollTop = keep.t; sc.scrollLeft = keep.l; }
    else if (sc && st.view === 'day') {
      var now = root.querySelector('.bk-now'), first = root.querySelector('.bk-ev');
      var target = now || first;
      if (target) sc.scrollTop = Math.max(0, target.offsetTop - 120);
    }
  }
  function canEditBookings() { return S.staffCan ? (S.staffCan('booking') || S.staffCan('booking_setup')) : true; }
  function kpi(t, v, cls) { return '<div class="bk-kpi ' + (cls || '') + '"><div class="t">' + esc(t) + '</div><div class="v">' + esc(v) + '</div></div>'; }

  function dayStrip() {
    var out = '', start = addDays(st.day, -3);
    for (var i = 0; i < 14; i++) {
      var k = addDays(start, i), d = parseKey(k);
      var n = dayBookings(k).filter(function (b) { return b.status !== 'cancelled'; }).length;
      out += '<button type="button" class="bk-dpill' + (k === st.day ? ' on' : '') + '" data-day="' + k + '"><div class="w">' + WD[weekday(k)] + '</div><div class="d">' + d.getUTCDate() + '</div><div class="c">' + (n ? n : '') + '</div></button>';
    }
    return out;
  }

  // Лента дня на телефоне: записи и блоки времени по порядку
  function agenda(list) {
    var blocks = dayBlocks(st.day).filter(function (x) { return !st.filter || !x.resource_id || x.resource_id === st.filter; });
    if (!list.length && !blocks.length) {
      return '<div class="bk-empty">' + icon('calendar') + 'На этот день записей нет.<br>Нажмите «Записать», чтобы добавить.</div>';
    }
    var rows = list.map(function (b) { return { t: minutesOf(b.start), b: b }; })
      .concat(blocks.map(function (x) { return { t: x.m0, k: x }; }));
    rows.sort(function (a, b) { return a.t - b.t; });
    return rows.map(function (row) { return row.k ? agendaBlock(row.k) : agendaItem(row.b); }).join('');
  }
  function agendaItem(b) {
    var r = resById(b.resource_id), room = b.room_id ? roomName(b.room_id) : '';
    return '<div class="bk-item ' + esc(b.status) + '" data-bk="' + esc(b.id) + '" style="--c:' + esc(colorOf(b)) + '">' +
      '<div class="tm">' + esc(hm(b.start)) + '<small>' + b.duration_min + ' мин</small></div><div class="bar"></div>' +
      '<div class="bd"><div class="cl">' + esc(b.client_name || 'Клиент') + (b.visit_id ? ' ' + icon('link') : '') + '</div>' +
      '<div class="sv">' + esc((b.service || '—') + (room ? ' · ' + room : '')) + '</div>' +
      (r && resources().length > 1 ? '<div class="ms-who">' + ava(r, 20) + '<span>' + esc(r.name) + '</span></div>' : '') + '</div>' +
      '<span class="st ' + esc(b.status) + '">' + esc(STATUS[b.status] || b.status) +
      (payState(b) && payState(b) !== 'ok' ? '<i class="bk-paychip ' + payState(b) + '">' + (payState(b) === 'part' ? 'частично' : 'не оплачено') + '</i>' : '') + '</span></div>';
  }
  function agendaBlock(k) {
    var who = k.resource_id ? ((resById(k.resource_id) || {}).name || '') : 'все мастера';
    var whole = k.m0 <= 0 && k.m1 >= 1439;
    return '<div class="bk-item bk-item-blk" data-blk="' + esc(k.id) + '"><div class="tm">' + (whole ? 'весь<small>день</small>' : fmtM(k.m0) + '<small>до ' + fmtM(k.m1) + '</small>') + '</div><div class="bar"></div>' +
      '<div class="bd"><div class="cl">' + icon('ban') + esc(k.kind_title) + '</div><div class="sv">' + esc([who, k.note].filter(Boolean).join(' · ')) + '</div></div></div>';
  }
  function fmtM(m) { m = Math.max(0, Math.min(m, 1439)); return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); }

  function dayGrid(list) {
    var res = resources().filter(function (r) { return !st.filter || r.id === st.filter; });
    if (!res.length) return '<div class="bk-card"><div class="bk-empty">Добавьте мастера в настройках записи.</div></div>';
    var wd = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][weekday(st.day)];
    var lo = 24 * 60, hi = 0;
    res.forEach(function (r) { (r.schedule[wd] || []).forEach(function (iv) { lo = Math.min(lo, toMin(iv[0])); hi = Math.max(hi, toMin(iv[1])); }); });
    list.forEach(function (b) { lo = Math.min(lo, minutesOf(b.start)); hi = Math.max(hi, minutesOf(b.start) + b.duration_min); });
    if (hi <= lo) { lo = 9 * 60; hi = 19 * 60; }
    hi = Math.min(hi, 24 * 60);
    lo = Math.floor(lo / 60) * 60; hi = Math.ceil(hi / 60) * 60;
    var h = (hi - lo) * PX;
    var cols = '64px repeat(' + res.length + ', minmax(170px, 1fr))';
    var out = '<div class="bk-card"><div class="bk-scroll"><div class="bk-grid" style="grid-template-columns:' + cols + '">';
    out += '<div class="bk-hcell corner"></div>' + res.map(function (r) {
      var iv = r.schedule[wd] || [];
      var off = (r.days_off || []).indexOf(st.day) >= 0 || !iv.length;
      var n = list.filter(function (b) { return b.resource_id === r.id && b.status !== 'cancelled'; }).length;
      var away = dayBlocks(st.day, r.id).filter(function (x) { return (x.kind === 'vacation' || x.kind === 'sick') && x.m0 <= 0 && x.m1 >= 1439; })[0];
      var hours = away ? away.kind_title.toLowerCase() : off ? 'выходной' : iv[0][0].replace(/^0/, '') + '–' + iv[iv.length - 1][1].replace(/^0/, '');
      if (away) off = true;
      return '<button type="button" class="bk-hcell bk-mhead' + (off ? ' off' : '') + '" data-mh="' + esc(r.id) + '" style="--c:' + esc(r.color) + '" title="' + esc(canSetup() ? 'Карточка мастера' : 'Показать только этого мастера') + '">' +
        ava(r, 38) + '<span class="mh-t"><span class="n">' + esc(r.name) + '</span>' +
        '<span class="r">' + esc(hours) + (n ? ' · ' + n + ' зап.' : '') + (r.online_booking === false ? ' · ' + icon('lock') : '') + '</span></span></button>';
    }).join('');
    var times = '<div class="bk-times" style="height:' + h + 'px;position:relative">';
    for (var m = lo; m <= hi; m += 60) times += '<div class="bk-time' + (m === lo ? ' first' : '') + '" style="top:' + ((m - lo) * PX) + 'px">' + String(m / 60).padStart(2, '0') + ':00</div>';
    times += '</div>';
    out += times;
    var nowMin = keyOf(new Date()) === st.day ? minutesOf(new Date().toISOString()) : -1;
    res.forEach(function (r) {
      var off = (r.days_off || []).indexOf(st.day) >= 0;
      var work = off ? [] : (r.schedule[wd] || []);
      var shade = '', prev = lo;
      work.forEach(function (iv) {
        var a = toMin(iv[0]), b = toMin(iv[1]);
        if (a > prev) shade += '<div class="bk-off" style="top:' + ((prev - lo) * PX) + 'px;height:' + ((a - prev) * PX) + 'px"></div>';
        prev = Math.max(prev, b);
      });
      if (prev < hi) shade += '<div class="bk-off" style="top:' + ((prev - lo) * PX) + 'px;height:' + ((hi - prev) * PX) + 'px"></div>';
      dayBlocks(st.day, r.id).forEach(function (k) {
        var a = Math.max(k.m0, lo), z = Math.min(k.m1, hi);
        if (z <= a) return;
        shade += '<div class="bk-blk' + (k.resource_id ? '' : ' all') + '" data-blk="' + esc(k.id) + '" style="top:' + ((a - lo) * PX) + 'px;height:' + ((z - a) * PX) + 'px" title="' + esc(k.kind_title + (k.note ? ': ' + k.note : '')) + '">' +
          '<b>' + esc(k.kind_title) + '</b>' + (k.note && (z - a) * PX > 36 ? '<span>' + esc(k.note) + '</span>' : '') + '</div>';
      });
      var evs = list.filter(function (b) { return b.resource_id === r.id; }).map(function (b) {
        var top = (minutesOf(b.start) - lo) * PX, height = Math.max(26, b.duration_min * PX - 3);
        var badge = b.status === 'arrived' ? '✓' : b.status === 'completed' ? '✓✓' : b.status === 'no_show' ? '✕' : b.status === 'confirmed' ? '👍' : '';
        var compact = height < 46;
        var pads = b.status === 'cancelled' ? '' :
          (b.prep_min ? '<div class="bk-pad" data-pad="' + esc(b.id) + '" style="--c:' + esc(r.color) + ';top:' + (top - b.prep_min * PX) + 'px;height:' + (b.prep_min * PX) + 'px" title="Подготовка ' + b.prep_min + ' мин"></div>' : '') +
          (b.after_min ? '<div class="bk-pad" data-pad="' + esc(b.id) + '" style="--c:' + esc(r.color) + ';top:' + (top + b.duration_min * PX - 2) + 'px;height:' + (b.after_min * PX) + 'px" title="Перерыв после ' + b.after_min + ' мин"></div>' : '');
        var canDrag = movable(b) && canEditBookings();
        var extra = b.visit_id ? icon('link') : '';
        var pp = payMark(b) || (b.prepay_amount && b.status !== 'cancelled' ? '<span class="bk-pp' + (b.prepaid ? ' ok' : '') + '" title="' + (b.prepaid ? 'Предоплата получена' : 'Ждём предоплату ' + money(b.prepay_amount)) + '">' + icon('wallet') + '</span>' : '');
        var room = b.room_id ? roomName(b.room_id) : '';
        return pads + '<div class="bk-ev ' + esc(b.status) + (compact ? ' compact' : '') + (b.visit_id ? ' visit' : '') + '" data-bk="' + esc(b.id) + '"' + (canDrag ? ' data-drag="1"' : '') +
          ' style="--c:' + esc(r.color) + ';top:' + top + 'px;height:' + height + 'px" title="' + esc(hm(b.start) + '–' + hm(b.end) + ' ' + (b.client_name || '') + (b.service ? ' · ' + b.service : '') + (room ? ' · ' + room : '') + (b.visit_id ? ' · визит из нескольких услуг' : '')) + '">' +
          (badge && !compact ? '<span class="bdg">' + badge + '</span>' : '') + (!compact ? pp : '') +
          '<div class="tm">' + esc(hm(b.start)) + extra + '</div><div class="cl">' + esc(b.client_name || 'Клиент') + '</div>' +
          (height > 52 ? '<div class="sv">' + esc(b.service || '') + (room && height > 70 ? ' · ' + esc(room) : '') + '</div>' : '') +
          (canDrag ? '<i class="bk-rz" title="Потяните, чтобы изменить длительность"></i>' : '') + '</div>';
      }).join('');
      out += '<div class="bk-col" data-col="' + esc(r.id) + '" data-lo="' + lo + '" style="height:' + h + 'px;background-size:100% ' + (60 * PX) + 'px">' + shade + evs +
        (nowMin >= lo && nowMin <= hi ? '<div class="bk-now" style="top:' + ((nowMin - lo) * PX) + 'px"></div>' : '') + '</div>';
    });
    return out + '</div></div></div>';
  }

  function weekView() {
    var mon = monday(st.day), today = keyOf(new Date()), out = '<div class="bk-week">';
    for (var i = 0; i < 7; i++) {
      var k = addDays(mon, i), d = parseKey(k), list = dayBookings(k);
      var n = list.filter(function (b) { return b.status !== 'cancelled'; }).length;
      out += '<div class="bk-wday' + (k === today ? ' today' : '') + '"><h4 data-day="' + k + '" data-goday="1">' + WD[i] + ', ' + d.getUTCDate() + ' ' + MON[d.getUTCMonth()].slice(0, 3) + '<span>' + (n || '') + '</span></h4><div class="bk-wlist">' +
        (dayBlocks(k).filter(function (x) { return !st.filter || !x.resource_id || x.resource_id === st.filter; }).map(function (x) {
          var who = x.resource_id ? ((resById(x.resource_id) || {}).name || '').split(' ')[0] : 'все';
          return '<div class="bk-chip blk" data-blk="' + esc(x.id) + '">' + (x.m0 <= 0 && x.m1 >= 1439 ? '' : '<b>' + fmtM(x.m0) + '</b> ') + esc(x.kind_title) + ' · ' + esc(who) + '</div>';
        }).join('') + list.map(function (b) {
          return '<div class="bk-chip ' + esc(b.status) + '" data-bk="' + esc(b.id) + '" style="--c:' + esc(colorOf(b)) + '"><b>' + esc(hm(b.start)) + '</b> ' + esc(b.client_name || 'Клиент') + '</div>';
        }).join('') || '<div style="color:var(--vf-text-4);font-size:12.5px;padding:4px">—</div>') + '</div></div>';
    }
    return out + '</div>';
  }

  // ── месяц ──
  function monthView() {
    var f = firstOfMonth(st.day), start = monday(f), today = keyOf(new Date()), mm = f.slice(0, 7);
    var out = '<div class="bk-month"><div class="bk-mhd">' + WD.map(function (w) { return '<span>' + w + '</span>'; }).join('') + '</div><div class="bk-mgrid">';
    var maxN = 1, cells = [];
    for (var i = 0; i < 42; i++) {
      var k = addDays(start, i), list = dayBookings(k).filter(function (b) { return b.status !== 'cancelled'; });
      maxN = Math.max(maxN, list.length);
      cells.push({ k: k, list: list });
    }
    if (cells.slice(35).every(function (c) { return c.k.slice(0, 7) !== mm; })) cells = cells.slice(0, 35);
    cells.forEach(function (c) {
      var d = parseKey(c.k), inM = c.k.slice(0, 7) === mm;
      var rev = c.list.filter(function (b) { return visited(b.status); }).reduce(function (s, b) { return s + (Number(b.price) || 0); }, 0);
      var byM = {};
      c.list.forEach(function (b) { byM[b.resource_id] = (byM[b.resource_id] || 0) + 1; });
      var away = dayBlocks(c.k).filter(function (x) { return (x.kind === 'vacation' || x.kind === 'sick') && (!st.filter || x.resource_id === st.filter || !x.resource_id); });
      out += '<button type="button" class="bk-mcell' + (inM ? '' : ' out') + (c.k === today ? ' today' : '') + (c.k === st.day ? ' sel' : '') + '" data-day="' + c.k + '" data-goday="1">' +
        '<span class="dn">' + d.getUTCDate() + '</span>' +
        (c.list.length ? '<span class="cnt">' + c.list.length + ' <small>зап.</small></span>' +
          '<span class="load"><i style="width:' + Math.round(c.list.length * 100 / maxN) + '%"></i></span>' +
          '<span class="dots">' + Object.keys(byM).slice(0, 6).map(function (rid) { var r = resById(rid); return '<i style="background:' + esc((r && r.color) || '#2a5ce8') + '" title="' + esc((r && r.name) || '') + ': ' + byM[rid] + '"></i>'; }).join('') + '</span>' : '') +
        (rev ? '<span class="rev">' + rev.toLocaleString('ru-RU') + ' сом</span>' : '') +
        (away.length ? '<span class="away">' + icon('circle-minus') + esc(away.map(function (x) { return x.resource_id ? ((resById(x.resource_id) || {}).name || '').split(' ')[0] : 'все'; }).join(', ')) + '</span>' : '') +
        '</button>';
    });
    return out + '</div></div>';
  }

  // ── перетаскивание и растяжка записи (компьютер, мышь) ──
  var drag = null, justDragged = 0;
  function colAt(x, y) {
    var els = document.elementsFromPoint ? document.elementsFromPoint(x, y) : [];
    for (var i = 0; i < els.length; i++) { if (els[i].classList && els[i].classList.contains('bk-col')) return els[i]; }
    return null;
  }
  function onPointerDown(e) {
    if (e.button !== 0 || e.pointerType === 'touch' || isPhone()) return;
    var el = e.target.closest('.bk-ev[data-drag]');
    if (!el) return;
    var b = bookingById(el.getAttribute('data-bk'));
    var col = el.closest('.bk-col');
    if (!b || !col) return;
    drag = { b: b, el: el, col: col, home: col, mode: e.target.closest('.bk-rz') ? 'resize' : 'move', x0: e.clientX, y0: e.clientY,
      started: false, lo: +col.getAttribute('data-lo'), top0: parseFloat(el.style.top), h0: el.offsetHeight,
      grab: e.clientY - el.getBoundingClientRect().top, start: minutesOf(b.start), dur: b.duration_min, rid: b.resource_id,
      scroll: el.closest('.bk-scroll') };
    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', onPointerUp);
  }
  function onPointerMove(e) {
    var d = drag;
    if (!d) return;
    if (!d.started) {
      if (Math.abs(e.clientX - d.x0) + Math.abs(e.clientY - d.y0) < 5) return;
      d.started = true;
      d.el.classList.add('dragging');
      document.body.classList.add('bk-dragging');
      S.$$('[data-pad="' + d.b.id + '"]', root).forEach(function (x) { x.style.display = 'none'; });
    }
    e.preventDefault();
    var sc = d.scroll;
    if (sc) {
      var rs = sc.getBoundingClientRect();
      if (e.clientY > rs.bottom - 40) sc.scrollTop += 14; else if (e.clientY < rs.top + 60) sc.scrollTop -= 14;
    }
    var tm = d.el.querySelector('.tm');
    if (d.mode === 'resize') {
      var mins = Math.max(SNAP * 2, Math.round((d.h0 + (e.clientY - d.y0)) / PX / SNAP) * SNAP);
      d.dur = mins;
      d.el.style.height = Math.max(26, mins * PX - 3) + 'px';
      if (tm) tm.textContent = fmtM(d.start) + '–' + fmtM(d.start + mins);
      markClash(d);
      return;
    }
    var col = colAt(e.clientX, e.clientY) || d.col;
    if (col !== d.el.parentNode) { col.appendChild(d.el); d.col = col; }
    var rect = col.getBoundingClientRect();
    var m = d.lo + Math.round((e.clientY - rect.top - d.grab) / PX / SNAP) * SNAP;
    m = Math.max(d.lo, Math.min(m, 24 * 60 - d.dur));
    d.start = m; d.rid = col.getAttribute('data-col');
    d.el.style.top = ((m - d.lo) * PX) + 'px';
    var r = resById(d.rid);
    if (r) d.el.style.setProperty('--c', r.color);
    if (tm) tm.textContent = fmtM(m) + '–' + fmtM(m + d.dur);
    markClash(d);
  }
  // Место занято другой записью или блоком этого мастера — запись подсвечивается красным,
  // отпустить на занятое нельзя (точную проверку с подготовкой и перерывами делает сервер)
  function clashAt(d) {
    var a = d.start, z = d.start + d.dur;
    var hit = ((st.data && st.data.bookings) || []).some(function (x) {
      if (x.id === d.b.id || x.resource_id !== d.rid || x.status === 'cancelled' || x.status === 'no_show') return false;
      if (keyOf(new Date(x.start)) !== st.day) return false;
      var xa = minutesOf(x.start);
      return xa < z && xa + x.duration_min > a;
    });
    return hit || dayBlocks(st.day, d.rid).some(function (k) { return k.m0 < z && k.m1 > a; });
  }
  function markClash(d) { d.bad = clashAt(d); d.el.classList.toggle('bad', d.bad); }
  function onPointerUp() {
    document.removeEventListener('pointermove', onPointerMove);
    document.removeEventListener('pointerup', onPointerUp);
    var d = drag;
    drag = null;
    document.body.classList.remove('bk-dragging');
    if (!d || !d.started) return;
    justDragged = Date.now();
    var b = d.b, orig = { start: b.start, resource_id: b.resource_id, duration_min: b.duration_min };
    var body;
    if (d.mode === 'resize') {
      if (d.dur === b.duration_min) { render(); return; }
      body = { duration_min: d.dur };
    } else {
      if (d.start === minutesOf(b.start) && d.rid === b.resource_id) { render(); return; }
      body = { start: st.day + 'T' + fmtM(d.start), resource_id: d.rid };
    }
    if (d.bad) { render(); S.toast('Это время занято — запись осталась на месте', 'warning'); return; }
    S.api('/api/booking/bookings/' + b.id, { method: 'PATCH', body: body }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); render(); return; }
      var nb = r.data.booking, who = (resById(nb.resource_id) || {}).name || '';
      var msg = d.mode === 'resize' ? 'Длительность: ' + nb.duration_min + ' мин' : 'Перенесено на ' + hm(nb.start) + (nb.resource_id !== orig.resource_id ? ' · ' + who : '');
      undoToast(msg, function () {
        var back = d.mode === 'resize' ? { duration_min: orig.duration_min } : { start: orig.start, resource_id: orig.resource_id };
        S.api('/api/booking/bookings/' + b.id, { method: 'PATCH', body: back }).then(function (r2) {
          if (!r2.ok) S.toast(S.apiError(r2), 'error'); else S.toast('Вернули как было');
          reload();
        });
      });
      reload();
    });
  }
  function undoToast(msg, undo) {
    if (window.VF && VF.toast) VF.toast(msg, { type: 'success', duration: 6000, actionLabel: 'Отменить', action: undo });
    else S.toast(msg);
  }

  // ── пустое место в сетке: записать или заблокировать ──
  function slotMenu(x, y, pre) {
    closeMenu();
    var m = document.createElement('div');
    m.className = 'bk-menu'; m.id = 'bk-menu';
    var r = resById(pre.resource_id);
    m.innerHTML = '<div class="bk-menu-h">' + esc(pre.time) + (r ? ' · ' + esc(r.name) : '') + '</div>' +
      '<button type="button" data-mm="new">' + icon('calendar-plus') + 'Записать клиента</button>' +
      '<button type="button" data-mm="blk">' + icon('circle-minus') + 'Заблокировать время</button>';
    document.body.appendChild(m);
    var w = m.offsetWidth, h = m.offsetHeight;
    m.style.left = Math.min(x, window.innerWidth - w - 12) + 'px';
    m.style.top = Math.min(y, window.innerHeight - h - 12) + 'px';
    m.addEventListener('click', function (e) {
      var b = e.target.closest('[data-mm]');
      if (!b) return;
      closeMenu();
      if (b.getAttribute('data-mm') === 'new') newBooking(pre); else blockDialog(pre);
    });
    setTimeout(function () { document.addEventListener('mousedown', menuOutside); document.addEventListener('keydown', menuKey); }, 0);
  }
  function menuOutside(e) { if (!e.target.closest('#bk-menu')) closeMenu(); }
  function menuKey(e) { if (e.key === 'Escape') closeMenu(); }
  function closeMenu() {
    var m = document.getElementById('bk-menu');
    if (m) m.remove();
    document.removeEventListener('mousedown', menuOutside);
    document.removeEventListener('keydown', menuKey);
  }

  // ── события ──
  function onClick(e) {
    if (Date.now() - justDragged < 350) return;
    var t = e.target;
    var mh = t.closest('[data-mh]');
    if (mh) { editMaster(mh.getAttribute('data-mh')); return; }
    var mf = t.closest('[data-mf]');
    if (mf) { st.filter = mf.getAttribute('data-mf'); render(); return; }
    var nav = t.closest('[data-nav]');
    if (nav) {
      var n = +nav.getAttribute('data-nav');
      if (n === 0) st.day = keyOf(new Date());
      else if (st.view === 'month' && !isPhone()) st.day = addMonths(st.day, n);
      else st.day = addDays(st.day, n * (st.view === 'week' && !isPhone() ? 7 : 1));
      load(); return;
    }
    var dv = t.closest('[data-day]');
    if (dv) { st.day = dv.getAttribute('data-day'); if (dv.hasAttribute('data-goday')) st.view = 'day'; load(); return; }
    var vw = t.closest('[data-view]');
    if (vw) { st.view = vw.getAttribute('data-view'); try { localStorage.setItem('vf_booking_view', st.view); } catch (x) { /* ignore */ } load(); return; }
    var bl = t.closest('[data-blk]');
    if (bl) { openBlock(bl.getAttribute('data-blk')); return; }
    if (t.closest('[data-blocknew]')) { blockDialog({ date: st.day, resource_id: st.filter || '' }); return; }
    var bk = t.closest('[data-bk]');
    if (bk) { openBooking(bk.getAttribute('data-bk')); return; }
    var col = t.closest('.bk-col');
    if (col && !t.closest('.bk-ev')) {
      var rect = col.getBoundingClientRect();
      var mins = +col.getAttribute('data-lo') + Math.floor((e.clientY - rect.top) / PX / 15) * 15;
      slotMenu(e.clientX, e.clientY, { resource_id: col.getAttribute('data-col'), date: st.day, time: fmtM(mins) });
    }
  }

  // ── карточка записи ──
  function openBooking(id) {
    var b = ((st.data && st.data.bookings) || []).filter(function (x) { return x.id === id; })[0];
    if (!b) return;
    var r = resById(b.resource_id);
    var phone = b.client_phone && !/^(web|tg|ig|wa):/.test(b.client_phone) ? b.client_phone : '';
    var dr = S.drawer({ title: '<div style="font-weight:700">' + esc(b.client_name || 'Клиент') + '</div><div class="muted small">' + esc(b.label) + '</div>' });
    dr.body.classList.add('bk-dr');
    dr.body.innerHTML =
      '<dl class="bk-dinfo">' +
      '<dt>Когда</dt><dd>' + esc(dayTitle(keyOf(new Date(b.start))).main) + ', ' + esc(hm(b.start)) + '–' + esc(hm(b.end)) + '</dd>' +
      '<dt>Услуга</dt><dd>' + esc(b.service || '—') + '</dd>' +
      '<dt>Мастер</dt><dd>' + esc((r && r.name) || '—') + '</dd>' +
      (b.room_id ? '<dt>Кабинет</dt><dd>' + esc(roomName(b.room_id) || '—') + '</dd>' : '') +
      '<dt>Цена</dt><dd>' + esc(money(b.price)) + '</dd>' +
      (b.prepay_amount ? '<dt>Предоплата</dt><dd><span class="bk-ppline' + (b.prepaid ? ' ok' : '') + '">' + icon(b.prepaid ? 'circle-check' : 'wallet') +
        esc(money(b.prepay_amount)) + ' · ' + (b.prepaid ? 'получена' : 'ждём') + '</span> ' +
        (st.data && st.data.cash_on && canPay() ? '' :
          '<button class="btn btn-ghost btn-sm" type="button" data-prepaid="' + (b.prepaid ? '0' : '1') + '">' + (b.prepaid ? 'Отменить отметку' : 'Отметить «получена»') + '</button>') + '</dd>' : '') +
      (b.prep_min || b.after_min ? '<dt>Время мастера</dt><dd>' + (b.prep_min ? 'подготовка ' + b.prep_min + ' мин' : '') + (b.prep_min && b.after_min ? ', ' : '') + (b.after_min ? 'перерыв после ' + b.after_min + ' мин' : '') + '</dd>' : '') +
      '<dt>Статус</dt><dd><b>' + esc(STATUS[b.status] || b.status) + '</b></dd>' +
      '<dt>Откуда</dt><dd>' + esc({ owner: 'Вы записали', staff: 'Сотрудник', bot: 'Бот в переписке', call: 'Голосовой агент', online: 'Онлайн по ссылке' }[b.source] || '—') + '</dd>' +
      (phone ? '<dt>Телефон</dt><dd><a href="tel:' + esc(phone) + '">' + esc(S.fmtPhone ? S.fmtPhone(phone) : phone) + '</a></dd>' : '') +
      (b.note ? '<dt>Комментарий</dt><dd>' + esc(b.note) + '</dd>' : '') +
      '</dl>' + visitBlock(b) + (canPay() ? '<div id="bk-pay"></div>' : '') +
      '<div class="bk-actions">' +
      (b.status === 'arrived' ? '<button class="btn btn-primary btn-sm" data-st="completed">' + icon('check-check') + 'Завершить визит</button>' : '') +
      (b.status === 'booked' ? '<button class="btn btn-sm" data-st="confirmed">' + icon('check') + 'Подтвердил</button>' : '') +
      (b.status === 'booked' || b.status === 'confirmed' || b.status === 'no_show' ? '<button class="btn btn-sm" data-st="arrived" style="color:var(--vf-success)">' + icon('circle-check') + 'Пришёл</button>' : '') +
      (b.status === 'booked' || b.status === 'confirmed' || b.status === 'arrived' ? '<button class="btn btn-sm" data-st="no_show">' + icon('circle-x') + 'Не пришёл</button>' : '') +
      (b.status === 'completed' ? '<button class="btn btn-sm" data-st="arrived">' + icon('rotate-ccw') + 'Вернуть «Пришёл»</button>' : '') +
      (b.status !== 'completed' ? '<button class="btn btn-sm" data-move>' + icon('calendar') + 'Перенести</button>' : '') +
      (b.status === 'cancelled' ? '<button class="btn btn-sm" data-st="booked">Вернуть запись</button>' :
        b.status !== 'completed' && b.status !== 'no_show' ? '<button class="btn btn-sm" data-cancel style="color:var(--vf-danger)">' + icon('trash-2') + (b.visit_id ? 'Отменить услугу' : 'Отменить') + '</button>' : '') +
      '</div><div class="bk-actions">' +
      (phone ? '<a class="btn btn-ghost btn-sm" href="tel:' + esc(phone) + '">' + icon('phone') + 'Позвонить</a>' : '') +
      (b.contact_id ? '<a class="btn btn-ghost btn-sm" href="/static/inbox.html?contact=' + esc(b.contact_id) + '">' + icon('message-circle') + 'Написать</a>' : '') +
      '</div>' +
      '<div class="field" style="margin-top:14px"><label class="label">Комментарий</label><textarea class="textarea" id="bk-note" rows="2">' + esc(b.note || '') + '</textarea>' +
      '<button class="btn btn-sm" type="button" id="bk-note-save" style="margin-top:6px">Сохранить комментарий</button></div>' +
      (canHistory() ? '<div class="bk-hist" id="bk-hist"><button class="btn btn-ghost btn-sm" type="button" data-hist>' + icon('history') +
        'История записи</button><div class="bk-hist-body" hidden></div></div>' : '');
    function patch(body, ok) {
      return S.api('/api/booking/bookings/' + b.id, { method: 'PATCH', body: body }).then(function (res) {
        if (!res.ok) { S.toast(S.apiError(res), 'error'); return false; }
        S.toast(ok || 'Сохранено'); dr.close(); reload();
        return true;
      });
    }
    var payBox = S.$('#bk-pay', dr.body);
    var payApi = payBox && window.BookingPay ? window.BookingPay.block(b.id, payBox, { onChange: function () { reload(); } }) : null;
    S.$$('[data-st]', dr.body).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var s2 = btn.getAttribute('data-st');
        var left = payApi && payApi.money && payApi.money.priced ? payApi.money.left : 0;
        patch({ status: s2 }, s2 === 'completed' ? 'Визит завершён' : s2 === 'booked' ? 'Запись возвращена' : STATUS[s2]).then(function (okd) {
          // «Завершить визит» с неоплаченным остатком — сразу окно оплаты (касса, шаг 3.1)
          if (okd && s2 === 'completed' && left > 0 && window.BookingPay) window.BookingPay.open(b.id, { onDone: function () { reload(); } });
        });
      });
    });
    S.$$('[data-vb]', dr.body).forEach(function (row) {
      row.addEventListener('click', function () { var id2 = row.getAttribute('data-vb'); if (id2 !== b.id) { dr.close(); openBooking(id2); } });
    });
    var vc = S.$('[data-vcancel]', dr.body);
    if (vc) vc.addEventListener('click', function () {
      S.confirm({ title: 'Отменить весь визит?', message: (b.client_name || 'Клиент') + ': все услуги этого визита будут отменены', confirmText: 'Отменить визит', danger: true }).then(function (yes) {
        if (yes) S.api('/api/booking/visits/' + b.visit_id, { method: 'DELETE' }).then(function (res) {
          if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
          S.toast('Визит отменён: ' + res.data.cancelled + ' ' + plural(res.data.cancelled, 'услуга', 'услуги', 'услуг')); dr.close(); reload();
        });
      });
    });
    var c = S.$('[data-cancel]', dr.body);
    var mv = S.$('[data-move]', dr.body);
    if (c) c.addEventListener('click', function () {
      S.confirm({ title: 'Отменить запись?', message: (b.client_name || 'Клиент') + ', ' + b.label, confirmText: 'Отменить запись', danger: true }).then(function (yes) {
        if (yes) S.api('/api/booking/bookings/' + b.id, { method: 'DELETE' }).then(function (res) {
          if (!res.ok) { S.toast(S.apiError(res), 'error'); return; } S.toast('Запись отменена'); dr.close(); reload();
        });
      });
    });
    if (mv) mv.addEventListener('click', function () { dr.close(); newBooking({ move: b }); });
    var ppb = S.$('[data-prepaid]', dr.body);
    if (ppb) ppb.addEventListener('click', function () {
      var on = ppb.getAttribute('data-prepaid') === '1';
      patch({ prepaid: on }, on ? 'Предоплата отмечена' : 'Отметка снята');
    });
    S.$('#bk-note-save', dr.body).addEventListener('click', function () { patch({ note: S.$('#bk-note', dr.body).value }); });
    var hb = S.$('[data-hist]', dr.body);
    if (hb) hb.addEventListener('click', function () { loadHistory(b, S.$('.bk-hist-body', dr.body), hb); });
  }

  // История записи (журнал действий, шаг 1.7): кто создал, перенёс, поменял статус и цену — владельцу и управляющему
  function canHistory() { return S.staffCan ? S.staffCan('activity') : true; }
  function canPay() { return !!window.BookingPay && (S.staffCan ? S.staffCan('booking') : true); }
  var ACT_ICON = { owner: 'user', staff: 'user-round', admin: 'shield-check', ai: 'sparkles', call: 'phone', client: 'user-check', system: 'settings' };
  function loadHistory(b, box, btn) {
    if (!box) return;
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = '<div class="skeleton" style="height:48px;border-radius:10px"></div>';
    S.api('/api/beauty/activity?object_type=booking&object_id=' + encodeURIComponent(b.id) + '&limit=50').then(function (r) {
      if (!r.ok) { box.innerHTML = '<div class="muted small">' + esc(S.apiError(r)) + '</div>'; return; }
      var items = r.data.items || [];
      if (!items.length) { box.innerHTML = '<div class="muted small">Изменений пока нет. Журнал ведётся с 26.11.2026 и хранится 180 дней.</div>'; return; }
      box.innerHTML = items.map(function (x) {
        var d = new Date(x.at);
        return '<div class="bk-hrow"><span class="bk-hava k-' + esc(x.actor.kind) + '">' + icon(ACT_ICON[x.actor.kind] || 'user') + '</span>' +
          '<div class="bk-hmain"><div><b>' + esc(x.actor.name) + '</b> · ' + esc(x.action_title) + '</div>' +
          '<div class="bk-hsum">' + esc(x.summary) + '</div></div>' +
          '<span class="bk-htime">' + d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', timeZone: 'Asia/Bishkek' }) + '<br>' +
          d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bishkek' }) + '</span></div>';
      }).join('') + '<a class="dh-link small" href="/static/activity.html">Весь журнал действий →</a>';
    });
  }

  function plural(n, one, few, many) {
    var a = Math.abs(n) % 100, b = a % 10;
    return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
  }
  // Визит из нескольких услуг: все части по порядку, переход к любой, «Отменить весь визит»
  function visitBlock(b) {
    if (!b.visit_id) return '';
    var parts = ((st.data && st.data.bookings) || []).filter(function (x) { return x.visit_id === b.visit_id; })
      .sort(function (x, y) { return new Date(x.start) - new Date(y.start); });
    if (parts.length < 2) return '';
    var live = parts.filter(function (x) { return x.status !== 'cancelled'; }).length;
    return '<div class="bk-visit"><div class="h">' + icon('link') + 'Визит: ' + parts.length + ' ' + plural(parts.length, 'услуга', 'услуги', 'услуг') +
      ' · ' + esc(hm(parts[0].start)) + '–' + esc(hm(parts[parts.length - 1].end)) + '</div>' +
      parts.map(function (x) {
        var r = resById(x.resource_id);
        return '<button type="button" class="bk-vrow' + (x.id === b.id ? ' on' : '') + '" data-vb="' + esc(x.id) + '" style="--c:' + esc((r && r.color) || '#2a5ce8') + '">' +
          '<b>' + esc(hm(x.start)) + '</b><span>' + esc(x.service || 'Услуга') + '<small>' + esc((r && r.name) || '') + (x.room_id ? ' · ' + esc(roomName(x.room_id)) : '') + '</small></span>' +
          '<i class="st ' + esc(x.status) + '">' + esc(STATUS[x.status] || x.status) + '</i></button>';
      }).join('') +
      (live > 1 ? '<button class="btn btn-ghost btn-sm" type="button" data-vcancel style="color:var(--vf-danger);margin-top:6px">' + icon('trash-2') + 'Отменить весь визит</button>' : '') + '</div>';
  }

  // ── блоки времени: перерыв, обучение, отпуск, больничный ──
  function blockPeriod(k) {
    var a = new Date(k.start), z = new Date(k.end);
    var ka = keyOf(a), kz = keyOf(new Date(z.getTime() - 60000));
    var whole = hm(k.start) === '00:00' && hm(k.end) === '00:00';
    var dt = function (key) { var d = parseKey(key); return WD[weekday(key)] + ', ' + d.getUTCDate() + ' ' + MON[d.getUTCMonth()]; };
    if (whole) {
      if (ka === kz) return dt(ka) + ', весь день';
      var n = Math.round((parseKey(kz) - parseKey(ka)) / 864e5) + 1;
      return 'с ' + dt(ka) + ' по ' + dt(kz) + ' (' + n + ' ' + plural(n, 'день', 'дня', 'дней') + ')';
    }
    if (ka === keyOf(z)) return dt(ka) + ', ' + hm(k.start) + '–' + hm(k.end);
    return dt(ka) + ' ' + hm(k.start) + ' — ' + dt(keyOf(z)) + ' ' + hm(k.end);
  }
  function openBlock(id) {
    var k = ((st.data && st.data.blocks) || []).filter(function (x) { return x.id === id; })[0];
    if (!k) return;
    var who = k.resource_id ? ((resById(k.resource_id) || {}).name || 'Мастер') : 'Все мастера';
    var dr = S.drawer({ title: '<div style="font-weight:700">' + esc(k.kind_title) + '</div><div class="muted small">' + esc(who) + '</div>' });
    dr.body.classList.add('bk-dr');
    dr.body.innerHTML = '<dl class="bk-dinfo"><dt>Когда</dt><dd>' + esc(blockPeriod(k)) + '</dd><dt>Кто</dt><dd>' + esc(who) + '</dd>' +
      (k.note ? '<dt>Комментарий</dt><dd>' + esc(k.note) + '</dd>' : '') + '</dl>' +
      '<p class="muted small" style="margin:12px 0 0">В это время ' + (k.resource_id ? 'к мастеру' : 'в салон') + ' не записывают: ни бот, ни голосовой агент, ни ссылка, ни «Записать» в журнале.</p>' +
      (canEditBookings() ? '<div class="bk-actions"><button class="btn btn-sm" type="button" data-del style="color:var(--vf-danger)">' + icon('trash-2') + 'Убрать блок</button></div>' : '');
    var del = S.$('[data-del]', dr.body);
    if (del) del.addEventListener('click', function () {
      del.disabled = true;
      S.api('/api/booking/blocks/' + k.id, { method: 'DELETE' }).then(function (r) {
        del.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        dr.close();
        undoToast(k.kind_title + ' убран', function () {
          S.api('/api/booking/blocks', { method: 'POST', body: { start: k.start, end: k.end, resource_id: k.resource_id, kind: k.kind, note: k.note || null } })
            .then(function (r2) { if (!r2.ok) S.toast(S.apiError(r2), 'error'); reload(); });
        });
        reload();
      });
    });
  }
  function blockDialog(pre) {
    pre = pre || {};
    var day = pre.date || st.day;
    var t0 = pre.time || '13:00', t1 = fmtM(Math.min(toMin(t0) + 60, 1439));
    var kind = pre.kind || 'break';
    var d = S.dialog({
      title: 'Заблокировать время', size: 'lg',
      body:
        '<div class="bk-kinds" role="radiogroup">' + BLOCK_KINDS.map(function (k) {
          return '<button type="button" role="radio" data-k="' + k[0] + '" class="' + (k[0] === kind ? 'on' : '') + '" aria-checked="' + (k[0] === kind) + '">' + esc(k[1]) + '</button>';
        }).join('') + '</div>' +
        '<div class="field"><label class="label" for="bl-res">Кто</label><select class="select-native" id="bl-res"><option value="">Все мастера — салон не принимает</option>' +
        resources().map(function (r) { return '<option value="' + esc(r.id) + '"' + (pre.resource_id === r.id ? ' selected' : '') + '>' + esc(r.name) + '</option>'; }).join('') + '</select></div>' +
        tgl('whole', 'Весь день', 'Отпуск и больничный — целыми днями', false) +
        '<div class="st-grid2 bk-blrange"><div class="field"><label class="label">С</label><div class="bk-dt"><input class="input" type="date" id="bl-d0" value="' + esc(day) + '"><input class="input" type="time" id="bl-t0" step="300" value="' + esc(t0) + '"></div></div>' +
        '<div class="field"><label class="label">По</label><div class="bk-dt"><input class="input" type="date" id="bl-d1" value="' + esc(day) + '"><input class="input" type="time" id="bl-t1" step="300" value="' + esc(t1) + '"></div></div></div>' +
        '<div class="field"><label class="label" for="bl-note">Комментарий <span class="muted">(клиенты не видят)</span></label><input class="input" id="bl-note" maxlength="300" placeholder="Например: обучение по окрашиванию"></div>' +
        '<div class="bk-clash" id="bl-clash" hidden></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>Заблокировать</button>'
    });
    var $ = function (q) { return S.$(q, d.el); };
    function range() {
      var whole = $('#t-whole').checked, d0 = $('#bl-d0').value, d1 = $('#bl-d1').value || d0;
      if (!d0) return null;
      return whole ? { start: d0 + 'T00:00', end: addDays(d1, 1) + 'T00:00' } : { start: d0 + 'T' + ($('#bl-t0').value || '00:00'), end: d1 + 'T' + ($('#bl-t1').value || '00:00') };
    }
    function toUtc(local) { return new Date(local + ':00+06:00').getTime(); }
    function sync() {
      var whole = $('#t-whole').checked;
      S.$$('#bl-t0, #bl-t1', d.el).forEach(function (x) { x.hidden = whole; });
      var rg = range(), box = $('#bl-clash');
      if (!rg) { box.hidden = true; return; }
      var a = toUtc(rg.start), z = toUtc(rg.end), rid = $('#bl-res').value;
      var hits = ((st.data && st.data.bookings) || []).filter(function (b) {
        return (b.status === 'booked' || b.status === 'confirmed') && (!rid || b.resource_id === rid) &&
          new Date(b.start).getTime() < z && new Date(b.end).getTime() > a;
      });
      box.hidden = !hits.length;
      if (hits.length) {
        box.innerHTML = icon('triangle-alert') + '<div><b>В это время уже ' + hits.length + ' ' + plural(hits.length, 'запись', 'записи', 'записей') + '.</b> Блок их не отменит — перенесите или предупредите клиентов.<div class="muted small">' +
          hits.slice(0, 4).map(function (b) { return esc(dayTitle(keyOf(new Date(b.start))).main + ' ' + hm(b.start) + ' · ' + (b.client_name || 'Клиент')); }).join('<br>') + (hits.length > 4 ? '<br>…' : '') + '</div></div>';
      }
    }
    S.$$('[data-k]', d.el).forEach(function (btn) {
      btn.addEventListener('click', function () {
        kind = btn.getAttribute('data-k');
        S.$$('[data-k]', d.el).forEach(function (x) { x.classList.toggle('on', x === btn); x.setAttribute('aria-checked', String(x === btn)); });
        $('#t-whole').checked = kind === 'vacation' || kind === 'sick';
        if (kind === 'vacation' && $('#bl-d1').value === $('#bl-d0').value) $('#bl-d1').value = addDays($('#bl-d0').value, 6);
        else if (!$('#t-whole').checked) $('#bl-d1').value = $('#bl-d0').value;
        sync();
      });
    });
    $('#bl-d0').addEventListener('change', function () { if (!$('#bl-d1').value || $('#bl-d1').value < $('#bl-d0').value) $('#bl-d1').value = $('#bl-d0').value; sync(); });
    ['#t-whole', '#bl-d1', '#bl-t0', '#bl-t1', '#bl-res'].forEach(function (q) { $(q).addEventListener('change', sync); });
    sync();
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    S.$('[data-ok]', d.el).addEventListener('click', function () {
      var rg = range();
      if (!rg) { S.toast('Укажите дату', 'error'); return; }
      if (toUtc(rg.end) <= toUtc(rg.start)) { S.toast('Конец должен быть позже начала', 'error'); return; }
      var btn = S.$('[data-ok]', d.el); btn.disabled = true;
      S.api('/api/booking/blocks', { method: 'POST', body: { start: rg.start, end: rg.end, resource_id: $('#bl-res').value || null, kind: kind, note: $('#bl-note').value.trim() || null } })
        .then(function (r) {
          btn.disabled = false;
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          var n = r.data.clash || 0;
          S.toast(n ? 'Время заблокировано. В нём уже ' + n + ' ' + plural(n, 'запись', 'записи', 'записей') + ' — перенесите их' : 'Время заблокировано', n ? 'warning' : 'success');
          d.close(); st.day = rg.start.slice(0, 10); reload();
        });
    });
  }

  // ── записать / перенести ──
  function newBooking(pre) {
    pre = pre || {};
    var move = pre.move || null;
    var services = (st.data && st.data.services) || [];
    var day = pre.date || (move ? keyOf(new Date(move.start)) : st.day);
    var chosen = null;
    var more = [];    // ещё услуги в этот визит: [{service_id, resource_id}]
    function svcOptions(sel, empty) {
      return '<option value="">' + esc(empty) + '</option>' + services.map(function (s) {
        return '<option value="' + esc(s.id) + '"' + (sel === s.id ? ' selected' : '') + '>' + esc((s.subcategory || s.category ? (s.subcategory || s.category) + ' · ' : '') + s.title) + ' · ' + s.duration_min + ' мин' + (s.price_text ? ' · ' + s.price_text : '') + (s.online_booking === false ? ' · только админ' : '') + '</option>';
      }).join('');
    }
    function resOptions(sel) {
      return '<option value="">Любой свободный</option>' + resources().map(function (r) { return '<option value="' + esc(r.id) + '"' + (sel === r.id ? ' selected' : '') + '>' + esc(r.name) + '</option>'; }).join('');
    }
    var d = S.dialog({
      title: move ? 'Перенести запись' : 'Новая запись', size: 'lg',
      body:
        (move ? '<p class="muted" style="margin:0 0 10px">' + esc((move.client_name || 'Клиент') + ' · ' + (move.service || '') + ' · сейчас ' + move.label) + '</p>' :
          '<div class="st-grid2"><div class="field"><label class="label">Клиент</label><input class="input" id="nb-name" placeholder="Имя или поиск по клиентам" autocomplete="off" value="' + esc((pre.client && pre.client.name) || '') + '"><div id="nb-sugg"></div></div>' +
          '<div class="field"><label class="label">Телефон</label><input class="input" id="nb-phone" type="tel" inputmode="tel" placeholder="+996 700 123 456" value="' + esc((pre.client && pre.client.phone) || '') + '"></div></div>') +
        '<div class="st-grid2">' +
        (move ? '' : '<div class="field"><label class="label" id="nb-svc-l">Услуга</label><select class="select-native" id="nb-svc">' + svcOptions(pre.service_id || '', 'Без услуги (60 мин)') + '</select></div>') +
        '<div class="field"><label class="label">Мастер</label><select class="select-native" id="nb-res">' + resOptions(pre.resource_id || (move && move.resource_id) || '') + '</select></div>' +
        '<div class="field"><label class="label">День</label><input class="input" id="nb-day" type="date" value="' + esc(day) + '"></div></div>' +
        (move || !services.length ? '' : '<div id="nb-more" class="bk-vitems"></div>' +
          '<button class="btn btn-ghost btn-sm bk-addsvc" type="button" id="nb-add">' + icon('plus') + 'Ещё услуга в этот визит</button>') +
        '<div class="field"><label class="label">Свободное время</label><div class="bk-slots" id="nb-slots"><span class="muted small">Загружаем…</span></div>' +
        '<div class="hint" style="margin-top:6px">Нужно другое время (вне графика)? <input class="input" id="nb-time" type="time" style="display:inline-block;width:auto;height:32px;padding:0 6px" value="' + esc(pre.time || '') + '"></div></div>' +
        (move ? '' : '<div class="field"><label class="label">Комментарий</label><input class="input" id="nb-note" placeholder="Пожелания клиента"></div>'),
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>' + (move ? 'Перенести' : 'Записать') + '</button>'
    });
    var $ = function (s) { return S.$(s, d.el); };
    // Из карточки клиента (шаг 2.1): «Записать» → клиент уже выбран
    var contactId = (pre.client && pre.client.id) || null;
    function visitItems() {
      return [{ service_id: ($('#nb-svc') && $('#nb-svc').value) || null, resource_id: $('#nb-res').value || null }].concat(more);
    }
    function drawMore() {
      var box = $('#nb-more');
      if (!box) return;
      box.innerHTML = more.length ? '<div class="hint" style="margin:2px 0 8px">Услуги идут одна за другой: клиент переходит от мастера к мастеру. Время ниже — когда помещается весь визит.</div>' +
        more.map(function (it, i) {
          return '<div class="bk-vitem"><span class="n">' + (i + 2) + '</span>' +
            '<select class="select-native" data-mi="' + i + '" data-f="service_id">' + svcOptions(it.service_id || '', 'Выберите услугу') + '</select>' +
            '<select class="select-native" data-mi="' + i + '" data-f="resource_id">' + resOptions(it.resource_id || '') + '</select>' +
            '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-mrm="' + i + '" aria-label="Убрать услугу">' + icon('x') + '</button></div>';
        }).join('') : '';
      S.$$('[data-mi]', box).forEach(function (el) {
        el.addEventListener('change', function () { more[+el.getAttribute('data-mi')][el.getAttribute('data-f')] = el.value || null; loadSlots(); });
      });
      S.$$('[data-mrm]', box).forEach(function (el) {
        el.addEventListener('click', function () { more.splice(+el.getAttribute('data-mrm'), 1); drawMore(); loadSlots(); });
      });
      var add = $('#nb-add');
      if (add) add.hidden = more.length >= 5;
      var lbl = $('#nb-svc-l');
      if (lbl) lbl.textContent = more.length ? 'Услуга 1' : 'Услуга';
      var none = $('#nb-svc') && $('#nb-svc').querySelector('option[value=""]');
      if (none) none.textContent = more.length ? 'Выберите услугу' : 'Без услуги (60 мин)';
    }
    function loadVisitSlots() {
      var items = visitItems();
      $('#nb-slots').innerHTML = '<span class="muted small">Подбираем время для всего визита…</span>';
      if (items.some(function (it) { return !it.service_id; })) {
        chosen = null;
        $('#nb-slots').innerHTML = '<span class="muted small">Выберите услугу в каждой строке визита.</span>';
        return;
      }
      var reqDay = $('#nb-day').value || day;
      S.api('/api/booking/visit-slots', { method: 'POST', body: { items: items, date: reqDay } }).then(function (r) {
        if (($('#nb-day').value || day) !== reqDay) return;
        if (!r.ok) { $('#nb-slots').innerHTML = '<span class="muted small">' + esc(S.apiError(r)) + '</span>'; return; }
        var slots = r.data.slots || [];
        chosen = null;
        if (pre.time) chosen = slots.filter(function (x) { return x.time === pre.time; })[0] || null;
        $('#nb-slots').innerHTML = slots.length ? slots.map(function (x) {
          return '<button type="button" class="bk-slot wide' + (chosen && chosen.start === x.start ? ' on' : '') + '" data-s="' + esc(x.start) + '">' + esc(x.time) + '<small>до ' + esc(x.end_time) + '</small></button>';
        }).join('') : '<span class="muted small">На этот день весь визит не помещается — выберите другой день или других мастеров.</span>';
        S.$$('.bk-slot', d.el).forEach(function (btn) {
          btn.addEventListener('click', function () {
            chosen = slots.filter(function (x) { return x.start === btn.getAttribute('data-s'); })[0];
            S.$$('.bk-slot', d.el).forEach(function (x) { x.classList.toggle('on', x === btn); });
            $('#nb-time').value = '';
          });
        });
      });
    }
    // Лист ожидания (шаг 2.4): времени нет — поставить клиента в очередь, освободится — предложим сами
    function addWaitBtn() {
      var none = $('#nb-none');
      if (!none || move || !window.BookingWaitlist || S.$('#nb-wait', d.el)) return;
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'btn btn-sm'; b.id = 'nb-wait'; b.style.marginLeft = '6px';
      b.innerHTML = icon('hourglass') + '<span>В лист ожидания</span>';
      none.parentNode.insertBefore(b, none.nextSibling);
      b.addEventListener('click', function () {
        var name = $('#nb-name') ? $('#nb-name').value.trim() : '', phone = $('#nb-phone') ? $('#nb-phone').value.trim() : '';
        d.close();
        toWaitlist({ client: (contactId || name) ? { id: contactId, name: name, phone: phone } : null,
          service_id: ($('#nb-svc') && $('#nb-svc').value) || '', resource_id: $('#nb-res').value || '', date: $('#nb-day').value || day });
      });
    }
    function loadSlots() {
      if (more.length) { loadVisitSlots(); return; }
      var p = new URLSearchParams({ date: $('#nb-day').value || day, days: '1' });
      var svc = move ? move.service_id : ($('#nb-svc') && $('#nb-svc').value);
      if (svc) p.set('service_id', svc);
      if ($('#nb-res').value) p.set('resource_id', $('#nb-res').value);
      $('#nb-slots').innerHTML = '<span class="muted small">Загружаем…</span>';
      S.api('/api/booking/slots?' + p.toString()).then(function (r) {
        var slots = (r.ok && r.data.days && r.data.days[0] && r.data.days[0].slots) || [];
        chosen = null;
        if (pre.time) { var hit = slots.filter(function (s) { return s.time === pre.time; })[0]; if (hit) chosen = hit; }
        $('#nb-slots').innerHTML = slots.length ? slots.map(function (s) {
          return '<button type="button" class="bk-slot' + (chosen && chosen.start === s.start ? ' on' : '') + '" data-s="' + esc(s.start) + '">' + esc(s.time) + '</button>';
        }).join('') : '<span class="muted small" id="nb-none">Свободного времени нет — выберите другой день, мастера или укажите время вручную.</span>';
        if (!slots.length && !move) addWaitBtn();
        if (!slots.length) {
          // Подсказка: ближайший день, где есть свободное время (до недели вперёд)
          var q2 = new URLSearchParams(p.toString()); q2.set('date', addDays($('#nb-day').value || day, 1)); q2.set('days', '7');
          S.api('/api/booking/slots?' + q2.toString()).then(function (r2) {
            var next = ((r2.ok && r2.data.days) || []).filter(function (x) { return x.slots.length; })[0];
            var none = $('#nb-none'); if (!next || !none) return;
            var t = dayTitle(next.date);
            none.innerHTML = 'На этот день свободного времени нет. <button type="button" class="btn btn-sm" id="nb-next" style="margin-left:6px">Ближайшее: ' + esc(t.main.toLowerCase()) + ', ' + esc(next.slots[0].time) + '</button>';
            $('#nb-next').addEventListener('click', function () { $('#nb-day').value = next.date; pre.time = next.slots[0].time; loadSlots(); });
            addWaitBtn();
          });
        }
        S.$$('.bk-slot', d.el).forEach(function (btn) {
          btn.addEventListener('click', function () {
            chosen = slots.filter(function (s) { return s.start === btn.getAttribute('data-s'); })[0];
            S.$$('.bk-slot', d.el).forEach(function (x) { x.classList.toggle('on', x === btn); });
            $('#nb-time').value = '';
          });
        });
      });
    }
    ['#nb-day', '#nb-res', '#nb-svc'].forEach(function (s) { var el = $(s); if (el) el.addEventListener('change', loadSlots); });
    var addBtn = $('#nb-add');
    if (addBtn) addBtn.addEventListener('click', function () { more.push({ service_id: null, resource_id: null }); drawMore(); loadSlots(); var last = S.$$('[data-f="service_id"]', d.el).pop(); if (last) last.focus(); });
    loadSlots();
    if (!move) {
      var tmr;
      $('#nb-name').addEventListener('input', function () {
        contactId = null;
        clearTimeout(tmr);
        var q = $('#nb-name').value.trim();
        if (q.length < 2) { $('#nb-sugg').innerHTML = ''; return; }
        tmr = setTimeout(function () {
          S.api('/api/agent/contacts?limit=5&search=' + encodeURIComponent(q)).then(function (r) {
            var items = (r.ok && (r.data.contacts || r.data.items || [])) || [];
            $('#nb-sugg').innerHTML = items.length ? '<div class="bk-sugg">' + items.map(function (c) {
              return '<button type="button" data-cid="' + esc(c.id) + '" data-n="' + esc(c.name || '') + '" data-p="' + esc(c.phone || '') + '">' + esc(c.name || 'Без имени') + '<span>' + esc(c.phone && !/^(web|tg|ig|wa):/.test(c.phone) ? c.phone : '') + '</span></button>';
            }).join('') + '</div>' : '';
            S.$$('[data-cid]', d.el).forEach(function (btn) {
              btn.addEventListener('click', function () {
                contactId = btn.getAttribute('data-cid');
                $('#nb-name').value = btn.getAttribute('data-n');
                var p = btn.getAttribute('data-p'); if (p && !/^(web|tg|ig|wa):/.test(p)) $('#nb-phone').value = p;
                $('#nb-sugg').innerHTML = '';
              });
            });
          });
        }, 220);
      });
    }
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    S.$('[data-ok]', d.el).addEventListener('click', function () {
      var manual = $('#nb-time').value;
      var start = manual ? ($('#nb-day').value + 'T' + manual) : (chosen && chosen.start);
      if (!start) { S.toast('Выберите время', 'error'); return; }
      var btn = S.$('[data-ok]', d.el); btn.disabled = true;
      var req;
      if (!move && more.length) {
        var vname = $('#nb-name').value.trim(), vphone = $('#nb-phone').value.trim(), items = visitItems();
        if (!vname && !contactId) { S.toast('Укажите клиента', 'error'); btn.disabled = false; return; }
        if (items.some(function (it) { return !it.service_id; })) { S.toast('Выберите услугу в каждой строке визита', 'error'); btn.disabled = false; return; }
        S.api('/api/booking/visits', { method: 'POST', body: { items: items, start: start, contact_id: contactId, client_name: vname || null,
          client_phone: vphone || null, note: $('#nb-note').value.trim() || null } }).then(function (r) {
          btn.disabled = false;
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          var rows = r.data.bookings || [];
          S.toast('Клиент записан: ' + rows.length + ' ' + plural(rows.length, 'услуга', 'услуги', 'услуг') + ', ' + hm(rows[0].start) + '–' + hm(rows[rows.length - 1].end), 'success');
          d.close(); st.day = keyOf(new Date(rows[0].start)); reload();
        });
        return;
      }
      if (move) {
        req = S.api('/api/booking/bookings/' + move.id, { method: 'PATCH', body: { start: start, resource_id: $('#nb-res').value || move.resource_id } });
      } else {
        var name = $('#nb-name').value.trim(), phone = $('#nb-phone').value.trim();
        if (!name && !contactId) { S.toast('Укажите клиента', 'error'); btn.disabled = false; return; }
        req = S.api('/api/booking/bookings', { method: 'POST', body: {
          start: start, service_id: $('#nb-svc').value || null, resource_id: $('#nb-res').value || (chosen && chosen.resource_ids && chosen.resource_ids[0]) || null,
          contact_id: contactId, client_name: name || null, client_phone: phone || null, note: $('#nb-note').value.trim() || null
        } });
      }
      req.then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast(move ? 'Запись перенесена' : 'Клиент записан: ' + r.data.booking.label);
        d.close(); st.day = keyOf(new Date(r.data.booking.start)); reload();
      });
    });
  }

  // ── настройки ──
  function settings() {
    var page = (st.data && st.data.page) || {}, cfg = page.settings || {};
    var d = S.dialog({
      title: 'Настройки записи', size: 'xl',
      body: '<div class="bk-seg" style="margin-bottom:14px"><button type="button" data-tab="r" class="on">Правила</button><button type="button" data-tab="l">Ссылка для клиентов</button>' +
        '<button type="button" data-tab="k">Кабинеты</button><button type="button" data-tab="m">Мастера</button></div>' +
        '<div data-pane="r">' +
        '<div class="st-grid2">' +
        sel('step_min', 'Шаг расписания', [[15, '15 минут'], [30, '30 минут'], [60, '1 час']], cfg.step_min) +
        sel('buffer_min', 'Перерыв после записи', [[0, 'Без перерыва'], [5, '5 минут'], [10, '10 минут'], [15, '15 минут'], [30, '30 минут']], cfg.buffer_min) +
        sel('lead_min', 'Записываться не позже чем за', [[0, 'Сразу'], [30, '30 минут'], [60, '1 час'], [120, '2 часа'], [1440, 'Сутки']], cfg.lead_min) +
        sel('horizon_days', 'Насколько вперёд можно записаться', [[7, 'Неделя'], [14, '2 недели'], [30, 'Месяц'], [60, '2 месяца']], cfg.horizon_days) +
        sel('cancel_hours', 'Бот отменяет и переносит не позже чем за', [[0, 'В любое время'], [2, '2 часа'], [3, '3 часа'], [6, '6 часов'], [12, '12 часов'], [24, 'Сутки']], cfg.cancel_hours || 0) +
        '</div><div class="field"><label class="label">Адрес (уходит клиенту в подтверждении и напоминании)</label><input class="input" id="bk-addr" value="' + esc(cfg.address || '') + '" placeholder="г. Бишкек, ул. Киевская 95, 2 этаж"></div>' +
        '<div class="field"><label class="label">Название на странице записи</label><input class="input" id="bk-title" value="' + esc(cfg.title || '') + '" placeholder="Например: Салон «Айдана»"></div>' +
        tgl('reminders', 'Напоминать клиенту', 'В WhatsApp или Telegram за сутки и за 2 часа до визита', cfg.reminders) +
        tgl('thanks', '«Спасибо» после визита', 'Через час после визита — короткое сообщение и приглашение снова', cfg.thanks) +
        tgl('master_phones', 'Мастера видят телефоны клиентов', 'В своём кабинете. Выключено — мастер видит только имя: так клиентская база остаётся у салона', cfg.master_phones) +
        '<div class="field" style="margin-top:10px"><label class="label" for="bk-ppinfo">Как внести предоплату <span class="muted">(для услуг с предоплатой)</span></label>' +
        '<textarea class="textarea" id="bk-ppinfo" rows="2" maxlength="500" placeholder="Например: перевод на Mbank +996 700 123 456 (Айпери С.) — пришлите чек в WhatsApp">' + esc(cfg.prepay_info || '') + '</textarea>' +
        '<div class="hint">Клиент увидит это после онлайн-записи, бот и голосовой агент скажут при записи.</div></div>' +
        '<button class="btn btn-primary" type="button" id="bk-save-r" style="margin-top:10px">Сохранить правила</button></div>' +
        '<div data-pane="l" hidden>' +
        '<p class="muted">Клиент сам выбирает услугу, мастера и время — запись появится у вас в календаре, а вам придёт уведомление.</p>' +
        '<div class="ms-tip" style="margin-bottom:12px">' + icon('store') + '<div><b>Страница салона</b><span>Обложка, фото, мастера с работами, акции и запись — оформите в «Страница салона».</span>' +
        '<div style="margin-top:8px"><button class="btn btn-sm" type="button" data-open-page>' + icon('arrow-right') + '<span>Открыть конструктор</span></button></div></div></div>' +
        '<div class="bk-link"><span id="bk-url">' + esc(page.url || '') + '</span></div>' +
        '<div class="bk-actions"><button class="btn btn-sm" type="button" id="bk-copy">' + icon('copy') + 'Скопировать</button>' +
        '<a class="btn btn-sm" target="_blank" rel="noopener" href="' + esc(page.url || '#') + '">' + icon('external-link') + 'Открыть</a></div>' +
        '<div class="field" style="margin-top:12px"><label class="label">Адрес ссылки</label><div style="display:flex;gap:8px"><input class="input" id="bk-slug" value="' + esc(page.slug || '') + '"><button class="btn" type="button" id="bk-slug-save">Сохранить</button></div><div class="hint">Латиница и цифры, например: salon-aidana</div></div>' +
        tgl('enabled', 'Онлайн-запись по ссылке включена', 'Выключите, если временно не принимаете запись', page.enabled) +
        '<div id="bk-kit" class="bk-kit"><div class="muted">Загружаем QR-код и кнопку для сайта…</div></div>' +
        '</div>' +
        '<div data-pane="k" hidden>' +
        '<p class="muted" style="margin:0 0 12px">Кабинеты, кресла и аппараты, которых меньше, чем мастеров (например, один кабинет массажа на двух массажистов). ' +
        'Отметьте их у услуг («Услуги» → услуга → «Кабинет или кресло»): запись встанет, только когда один из них свободен, — двух клиентов в один кабинет не запишут.</p>' +
        '<div id="bk-rooms" class="bk-rooms"></div>' +
        '<div class="bk-room-add"><input class="input" id="rm-name" maxlength="120" placeholder="Например: Кабинет массажа">' +
        '<select class="select-native" id="rm-kind"><option value="room">Кабинет</option><option value="chair">Кресло</option><option value="device">Аппарат</option></select>' +
        '<button class="btn btn-primary" type="button" id="rm-add">' + icon('plus') + 'Добавить</button></div>' +
        '</div>',
      foot: '<button class="btn" type="button" data-c>Закрыть</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', function () { d.close(); reload(); });
    S.$$('[data-tab]', d.el).forEach(function (b) {
      b.addEventListener('click', function () {
        if (b.getAttribute('data-tab') === 'm') { d.close(); openMasters(); return; }
        var op = S.$('[data-open-page]', d.el);
        if (op && !op._b) { op._b = true; op.addEventListener('click', function () { d.close(); openPage(); }); }
        S.$$('[data-tab]', d.el).forEach(function (x) { x.classList.toggle('on', x === b); });
        S.$$('[data-pane]', d.el).forEach(function (p) { p.hidden = p.getAttribute('data-pane') !== b.getAttribute('data-tab'); });
        if (b.getAttribute('data-tab') === 'l') drawLinkKit(d);
        if (b.getAttribute('data-tab') === 'k') drawRooms(d);
      });
    });
    S.$('#bk-save-r', d.el).addEventListener('click', function () {
      var body = { settings: { address: S.$('#bk-addr', d.el).value.trim(), title: S.$('#bk-title', d.el).value.trim(),
        reminders: S.$('#t-reminders', d.el).checked, thanks: S.$('#t-thanks', d.el).checked,
        master_phones: S.$('#t-master_phones', d.el).checked, prepay_info: S.$('#bk-ppinfo', d.el).value.trim() } };
      S.$$('[data-set]', d.el).forEach(function (s) { body.settings[s.getAttribute('data-set')] = +s.value; });
      S.api('/api/booking/settings', { method: 'PUT', body: body }).then(function (r) { S.toast(r.ok ? 'Правила сохранены' : S.apiError(r), r.ok ? '' : 'error'); if (r.ok) st.data.page = r.data.page; });
    });
    S.$('#t-enabled', d.el).addEventListener('change', function (e) {
      S.api('/api/booking/settings', { method: 'PUT', body: { enabled: e.target.checked } }).then(function (r) { S.toast(r.ok ? (e.target.checked ? 'Запись по ссылке включена' : 'Запись по ссылке выключена') : S.apiError(r)); });
    });
    S.$('#bk-copy', d.el).addEventListener('click', function () {
      var u = S.$('#bk-url', d.el).textContent;
      (navigator.clipboard ? navigator.clipboard.writeText(u) : Promise.reject()).then(function () { S.toast('Ссылка скопирована'); }).catch(function () { window.prompt('Скопируйте ссылку', u); });
    });
    S.$('#bk-slug-save', d.el).addEventListener('click', function () {
      S.api('/api/booking/settings', { method: 'PUT', body: { slug: S.$('#bk-slug', d.el).value } }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        st.data.page = r.data.page; S.$('#bk-url', d.el).textContent = r.data.page.url; S.$('#bk-slug', d.el).value = r.data.page.slug; S.toast('Адрес ссылки сохранён');
        d._kit = false; drawLinkKit(d);
      });
    });
  }
  // Кабинеты, кресла, аппараты: название, вид, включён, удалить (с будущими записями — выключается)
  var ROOM_ICON = { room: 'door-open', chair: 'user-round', device: 'zap' };
  function drawRooms(d) {
    var box = S.$('#bk-rooms', d.el);
    if (!box) return;
    var list = (st.data && st.data.rooms) || [];
    box.innerHTML = list.length ? list.map(function (r) {
      return '<div class="bk-room' + (r.is_active ? '' : ' off') + '" data-rm="' + esc(r.id) + '"><span class="ic-w">' + icon(ROOM_ICON[r.kind] || 'door-open') + '</span>' +
        '<input class="input" data-rf="name" maxlength="120" value="' + esc(r.name) + '" aria-label="Название">' +
        '<select class="select-native" data-rf="kind" aria-label="Вид">' + [['room', 'Кабинет'], ['chair', 'Кресло'], ['device', 'Аппарат']].map(function (o) {
          return '<option value="' + o[0] + '"' + (r.kind === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
        }).join('') + '</select>' +
        '<label class="switch" title="' + (r.is_active ? 'Работает' : 'Выключен') + '"><input type="checkbox" data-rf="is_active"' + (r.is_active ? ' checked' : '') + '><span class="track"></span></label>' +
        '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-rdel aria-label="Удалить">' + icon('trash-2') + '</button></div>';
    }).join('') : '<div class="bk-empty" style="padding:18px">' + icon('door-open') + 'Кабинетов пока нет. Если каждый мастер работает на своём месте — они не нужны.</div>';
    function refresh() {
      return S.api('/api/booking/rooms').then(function (r) { if (r.ok && st.data) { st.data.rooms = r.data.rooms; drawRooms(d); } });
    }
    S.$$('[data-rm]', box).forEach(function (row) {
      var id = row.getAttribute('data-rm');
      function save(body) {
        S.api('/api/booking/rooms/' + id, { method: 'PATCH', body: body }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); refresh(); return; }
          S.toast('Сохранено'); refresh();
        });
      }
      S.$$('[data-rf]', row).forEach(function (el) {
        el.addEventListener('change', function () {
          var f = el.getAttribute('data-rf'), body = {};
          body[f] = f === 'is_active' ? el.checked : el.value;
          if (f === 'name' && !el.value.trim()) { S.toast('Укажите название', 'error'); refresh(); return; }
          save(body);
        });
      });
      S.$('[data-rdel]', row).addEventListener('click', function () {
        var name = S.$('[data-rf="name"]', row).value;
        S.confirm({ title: 'Удалить «' + name + '»?', message: 'Если на него есть будущие записи, он будет выключен, а записи останутся.', confirmText: 'Удалить', danger: true }).then(function (yes) {
          if (!yes) return;
          S.api('/api/booking/rooms/' + id, { method: 'DELETE' }).then(function (r) {
            if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
            S.toast(r.data.disabled ? 'Есть будущие записи (' + r.data.upcoming + ') — кабинет выключен, записи остались' : 'Удалено', r.data.disabled ? 'warning' : 'success');
            refresh();
          });
        });
      });
    });
    var add = S.$('#rm-add', d.el);
    if (add && !add._bound) {
      add._bound = true;
      add.addEventListener('click', function () {
        var name = S.$('#rm-name', d.el).value.trim();
        if (!name) { S.toast('Укажите название', 'error'); S.$('#rm-name', d.el).focus(); return; }
        add.disabled = true;
        S.api('/api/booking/rooms', { method: 'POST', body: { name: name, kind: S.$('#rm-kind', d.el).value } }).then(function (r) {
          add.disabled = false;
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          S.$('#rm-name', d.el).value = ''; S.toast('Добавлено: ' + name); refresh();
        });
      });
      S.$('#rm-name', d.el).addEventListener('keydown', function (e) { if (e.key === 'Enter') add.click(); });
    }
  }
  // QR-код для печати и Instagram + код кнопки «Записаться онлайн» на сайт (static/booking-widget.js)
  function drawLinkKit(d) {
    var box = S.$('#bk-kit', d.el);
    if (!box || d._kit) return;
    d._kit = true;
    S.api('/api/booking/link-kit').then(function (r) {
      if (!r.ok) { d._kit = false; box.innerHTML = '<div class="muted">' + esc(S.apiError(r)) + '</div>'; return; }
      var k = r.data;
      var slugName = (k.page && k.page.slug) || 'zapis';
      box.innerHTML =
        (k.qr ? '<div class="bk-kit-row"><img class="bk-qr" alt="QR-код записи" src="' + esc(k.qr) + '">' +
          '<div><b>QR-код</b><div class="muted">Распечатайте на ресепшн, кассу, визитку или поставьте в сторис — клиент наведёт камеру и запишется.</div>' +
          '<a class="btn btn-sm" style="margin-top:8px" download="zapis-' + esc(slugName) + '.svg" href="' + esc(k.qr) + '">' + icon('download') + 'Скачать QR</a></div></div>' : '') +
        (k.widget_code ? '<div class="field" style="margin-top:14px"><label class="label">Кнопка «Записаться онлайн» на ваш сайт</label>' +
          '<textarea class="input" id="bk-wcode" readonly rows="3" style="font:12.5px/1.4 ui-monospace,Menlo,monospace;height:auto;padding:10px">' + esc(k.widget_code) + '</textarea>' +
          '<div class="hint">Вставьте перед &lt;/body&gt; на сайте (Tilda: блок «HTML-код»; WordPress: виджет «HTML»). Справа внизу появится кнопка, запись откроется окном прямо на сайте. Свои кнопки: добавьте им атрибут <code>data-voksy-booking</code>.</div>' +
          '<div class="bk-actions"><button class="btn btn-sm" type="button" id="bk-wcopy">' + icon('copy') + 'Скопировать код</button></div></div>' : '') +
        (k.page && k.page.url ? '<div class="hint" style="margin-top:10px">Ссылку можно поставить в шапку Instagram, 2ГИС, Google Карты и автоответ WhatsApp.</div>' : '');
      var wc = S.$('#bk-wcopy', box);
      if (wc) wc.addEventListener('click', function () {
        var t = S.$('#bk-wcode', box).value;
        (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(function () { S.toast('Код скопирован'); })
          .catch(function () { S.$('#bk-wcode', box).select(); });
      });
    });
  }
  function sel(key, label, opts, val) {
    return '<div class="field"><label class="label">' + esc(label) + '</label><select class="select-native" data-set="' + key + '">' +
      opts.map(function (o) { return '<option value="' + o[0] + '"' + (+val === o[0] ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('') + '</select></div>';
  }
  function tgl(key, title, sub, on) {
    return '<div class="st-switch-row"><div class="t"><b>' + esc(title) + '</b><div>' + esc(sub) + '</div></div><label class="switch"><input type="checkbox" id="t-' + key + '"' + (on ? ' checked' : '') + '><span class="track"></span></label></div>';
  }
  // ── мгновенное обновление: поток /api/booking/stream (services/booking_stream.py) ──
  // Любое изменение журнала (у вас, у сотрудника, бота, звонка, клиента по ссылке) приходит событием
  // «changed» — журнал перезапрашивает данные и подсвечивает изменившиеся записи. Пока вы тащите запись,
  // обновление ждёт. Обрыв — переподключение с паузой 1…30 с; опрос раз в минуту — запасной путь.
  var live = { h: null, pending: false };
  function liveOn() { return !!(live.h && live.h.on()); }
  function sigOf(b) { return [b.start, b.end, b.status, b.resource_id, b.client_name, b.service, b.room_id, b.prepaid].join('|'); }
  function liveReload() {
    if (drag || document.hidden) { live.pending = true; return; }
    live.pending = false;
    var old = {};
    ((st.data && st.data.bookings) || []).forEach(function (b) { old[b.id] = sigOf(b); });
    S.bust();
    load(true).then(function () {
      var changed = ((st.data && st.data.bookings) || []).filter(function (b) { return old[b.id] !== sigOf(b); });
      if (!changed.length || changed.length > 12) return;
      changed.forEach(function (b) {
        S.$$('[data-bk="' + b.id + '"]', root).forEach(function (el) { el.classList.add('bk-flash'); setTimeout(function () { el.classList.remove('bk-flash'); }, 2400); });
      });
    });
  }
  function startLive() {
    if (!S.live) return;
    live.h = S.live('/api/booking/stream', function (ev) { if (ev === 'changed' || ev === 'resync') liveReload(); });
  }

  // ── старт ──
  function init() {
    root = document.getElementById('bk');
    esc = S.esc; icon = S.icon;
    S.initMobileMenu();
    try { var v = localStorage.getItem('vf_booking_view'); if (v === 'week' || v === 'day' || v === 'month') st.view = v; } catch (e) { /* ignore */ }
    var q = new URLSearchParams(location.search);
    st.day = q.get('date') || keyOf(new Date());
    if (q.get('view') === 'masters' || q.get('view') === 'services' || q.get('view') === 'waitlist' || (q.get('view') === 'page' && canSetup())) { st.prevView = st.view; st.view = q.get('view'); }
    var acts = document.getElementById('bk-actions');
    // Мастера, график, правила и ссылка — у владельца и сотрудника с разделом «Мастера и график»
    var setup = canSetup();
    acts.innerHTML = '<button class="btn btn-sm" type="button" id="bk-services-btn" title="Услуги: прайс, время, кто делает">' + icon('sparkles') + '<span class="lbl">Услуги</span></button>' +
      '<button class="btn btn-sm" type="button" id="bk-masters-btn" title="Мастера: фото, график, услуги и цены">' + icon('users') + '<span class="lbl">Мастера</span></button>' +
      '<button class="btn btn-sm bk-wait-btn" type="button" id="bk-wait-btn" title="Лист ожидания: освободится время — предложим сами">' + icon('hourglass') + '<span class="lbl">Лист ожидания</span><span class="bk-wait-n" id="bk-wait-n" hidden></span></button>' +
      (setup ? '<button class="btn btn-sm" type="button" id="bk-link-btn" title="Страница салона: витрина и онлайн-запись для клиентов">' + icon('store') + '<span class="lbl">Страница салона</span></button>' +
      '<button class="btn btn-sm btn-icon" type="button" id="bk-set" title="Настройки записи" aria-label="Настройки">' + icon('settings') + '</button>' : '') +
      '<button class="btn btn-primary btn-sm" type="button" id="bk-new">' + icon('plus') + '<span class="lbl">Записать</span></button>';
    document.getElementById('bk-new').addEventListener('click', function () { newBooking({}); });
    document.getElementById('bk-masters-btn').addEventListener('click', openMasters);
    document.getElementById('bk-services-btn').addEventListener('click', openServices);
    document.getElementById('bk-wait-btn').addEventListener('click', openWaitlist);
    if (setup) {
      document.getElementById('bk-set').addEventListener('click', settings);
      document.getElementById('bk-link-btn').addEventListener('click', openPage);
    }
    var fab = document.createElement('button');
    fab.className = 'bk-fab'; fab.type = 'button'; fab.innerHTML = icon('plus') + 'Записать';
    fab.addEventListener('click', function () { newBooking({ date: st.day }); });
    document.body.appendChild(fab);
    root.addEventListener('click', onClick);
    root.addEventListener('pointerdown', onPointerDown);
    root.addEventListener('change', function (e) { if (e.target.id === 'bk-filter') { st.filter = e.target.value; render(); } });
    // Ссылки с главной салона (шаг 1.7): ?open=<id> — карточка записи, ?new=1 — записать, ?block=1 — заблокировать время
    // ?new=1&contact=<id>&name=&phone=&service=&master= — «Записать» из карточки клиента (шаг 2.1)
    var deep = { open: q.get('open') || (/^#[0-9a-f-]{36}$/.test(location.hash) ? location.hash.slice(1) : ''),
                 nw: q.get('new') === '1', block: q.get('block') === '1' };
    var preClient = /^[0-9a-f-]{36}$/.test(q.get('contact') || '') ? { id: q.get('contact'), name: q.get('name') || '', phone: q.get('phone') || '' } : null;
    load().then(function () {
      if (deep.open && bookingById(deep.open)) openBooking(deep.open);
      else if (deep.nw && canEditBookings()) newBooking({ date: st.day, client: preClient, service_id: q.get('service') || '', resource_id: q.get('master') || '' });
      else if (deep.block && canEditBookings()) blockDialog({ date: st.day, resource_id: '' });
      if (deep.open || deep.nw || deep.block) {
        ['open', 'new', 'block', 'contact', 'name', 'phone', 'service', 'master'].forEach(function (k) { q.delete(k); });
        try { history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q.toString() : '')); } catch (e) { /* ignore */ }
      }
    });
    startLive();
    // Запасной опрос: раз в минуту, если потока нет; с потоком — раз в 5 минут (на случай тихого обрыва прокси)
    var lastPoll = Date.now();
    setInterval(function () {
      if (live.pending && !drag && !document.hidden) liveReload();
      var every = liveOn() ? 300000 : 60000;
      if (Date.now() - lastPoll < every || document.hidden || drag || document.querySelector('.vf-backdrop')) return;
      lastPoll = Date.now(); liveReload();
    }, 2000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden && live.pending) liveReload(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
