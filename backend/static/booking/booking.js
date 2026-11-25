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
 * API: /api/booking/* (services/booking_service.py — одна логика с ботом, звонком и ссылкой).
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var TZ = 'Asia/Bishkek';
  var PX = 1.15;   // пикселей на минуту в сетке дня
  var WD = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
  var MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var STATUS = { booked: 'Записан', confirmed: 'Подтвердил', arrived: 'Пришёл', no_show: 'Не пришёл', cancelled: 'Отменена' };
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
  function load(force) {
    var win = monday(st.day);
    if (!force && st.win === win && st.data) { render(); return Promise.resolve(); }
    var path = '/api/booking/overview?date=' + win + '&days=7';
    return S.apiSWR(path, function (r) {
      if (!r.ok) { root.innerHTML = '<div class="bk-empty">' + esc(S.apiError(r)) + '</div>'; S.ready(); return; }
      st.data = r.data; st.win = win; render(); S.ready();
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

  // ── каркас ──
  function mctx() {
    return { data: function () { return st.data; }, root: root, canSetup: canSetup(), isOwner: !S.staff(),
      back: function () { st.view = st.prevView || 'day'; render(); window.scrollTo(0, 0); },
      refresh: function () { render(); }, reload: function () { reload(); } };
  }
  function canSetup() { return S.staffCan ? S.staffCan('booking_setup') : true; }
  function canServices() { return S.staffCan ? (S.staffCan('booking_setup') || S.staffCan('catalog')) : true; }
  function openMasters() {
    if (st.view !== 'masters' && st.view !== 'services') st.prevView = st.view;
    st.view = 'masters'; render(); window.scrollTo(0, 0);
  }
  function openServices() {
    if (st.view !== 'masters' && st.view !== 'services') st.prevView = st.view;
    st.view = 'services'; render(); window.scrollTo(0, 0);
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
    document.body.classList.remove('bk-masters-on');
    var d = dayTitle(st.day);
    var list = dayBookings(st.day);
    var active = list.filter(function (b) { return b.status !== 'cancelled'; });
    var arrived = list.filter(function (b) { return b.status === 'arrived'; });
    var noshow = list.filter(function (b) { return b.status === 'no_show'; });
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
      '<div class="bk-seg"><button type="button" data-view="day" class="' + (st.view === 'day' ? 'on' : '') + '">День</button>' +
      '<button type="button" data-view="week" class="' + (st.view === 'week' ? 'on' : '') + '">Неделя</button></div></div>' +
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
      '<div class="bk-desk">' + (st.view === 'week' ? weekView() : dayGrid(list)) + '</div>';
    root.innerHTML = html;
    var cur = root.querySelector('.bk-dpill.on'); if (cur && cur.scrollIntoView) cur.scrollIntoView({ inline: 'center', block: 'nearest' });
    var sc = root.querySelector('.bk-scroll');
    if (sc && st.view === 'day') {
      var now = root.querySelector('.bk-now'), first = root.querySelector('.bk-ev');
      var target = now || first;
      if (target) sc.scrollTop = Math.max(0, target.offsetTop - 120);
    }
  }
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

  function agenda(list) {
    if (!list.length) {
      return '<div class="bk-empty">' + icon('calendar') + 'На этот день записей нет.<br>Нажмите «Записать», чтобы добавить.</div>';
    }
    return list.map(function (b) {
      var r = resById(b.resource_id);
      return '<div class="bk-item ' + esc(b.status) + '" data-bk="' + esc(b.id) + '" style="--c:' + esc(colorOf(b)) + '">' +
        '<div class="tm">' + esc(hm(b.start)) + '<small>' + b.duration_min + ' мин</small></div><div class="bar"></div>' +
        '<div class="bd"><div class="cl">' + esc(b.client_name || 'Клиент') + '</div><div class="sv">' + esc(b.service || '—') + '</div>' +
        (r && resources().length > 1 ? '<div class="ms-who">' + ava(r, 20) + '<span>' + esc(r.name) + '</span></div>' : '') + '</div>' +
        '<span class="st ' + esc(b.status) + '">' + esc(STATUS[b.status] || b.status) + '</span></div>';
    }).join('');
  }

  function dayGrid(list) {
    var res = resources().filter(function (r) { return !st.filter || r.id === st.filter; });
    if (!res.length) return '<div class="bk-card"><div class="bk-empty">Добавьте мастера в настройках записи.</div></div>';
    var wd = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][weekday(st.day)];
    var lo = 24 * 60, hi = 0;
    res.forEach(function (r) { (r.schedule[wd] || []).forEach(function (iv) { lo = Math.min(lo, toMin(iv[0])); hi = Math.max(hi, toMin(iv[1])); }); });
    list.forEach(function (b) { lo = Math.min(lo, minutesOf(b.start)); hi = Math.max(hi, minutesOf(b.start) + b.duration_min); });
    if (hi <= lo) { lo = 9 * 60; hi = 19 * 60; }
    lo = Math.floor(lo / 60) * 60; hi = Math.ceil(hi / 60) * 60;
    var h = (hi - lo) * PX;
    var cols = '64px repeat(' + res.length + ', minmax(170px, 1fr))';
    var out = '<div class="bk-card"><div class="bk-scroll"><div class="bk-grid" style="grid-template-columns:' + cols + '">';
    out += '<div class="bk-hcell corner"></div>' + res.map(function (r) {
      var iv = r.schedule[wd] || [];
      var off = (r.days_off || []).indexOf(st.day) >= 0 || !iv.length;
      var n = list.filter(function (b) { return b.resource_id === r.id && b.status !== 'cancelled'; }).length;
      var hours = off ? 'выходной' : iv[0][0].replace(/^0/, '') + '–' + iv[iv.length - 1][1].replace(/^0/, '');
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
      var evs = list.filter(function (b) { return b.resource_id === r.id; }).map(function (b) {
        var top = (minutesOf(b.start) - lo) * PX, height = Math.max(26, b.duration_min * PX - 3);
        var badge = b.status === 'arrived' ? '✓' : b.status === 'no_show' ? '✕' : b.status === 'confirmed' ? '👍' : '';
        var compact = height < 46;
        var pads = b.status === 'cancelled' ? '' :
          (b.prep_min ? '<div class="bk-pad" style="--c:' + esc(r.color) + ';top:' + (top - b.prep_min * PX) + 'px;height:' + (b.prep_min * PX) + 'px" title="Подготовка ' + b.prep_min + ' мин"></div>' : '') +
          (b.after_min ? '<div class="bk-pad" style="--c:' + esc(r.color) + ';top:' + (top + b.duration_min * PX - 2) + 'px;height:' + (b.after_min * PX) + 'px" title="Перерыв после ' + b.after_min + ' мин"></div>' : '');
        var pp = b.prepay_amount && b.status !== 'cancelled' ? '<span class="bk-pp' + (b.prepaid ? ' ok' : '') + '" title="' + (b.prepaid ? 'Предоплата получена' : 'Ждём предоплату ' + money(b.prepay_amount)) + '">' + icon('wallet') + '</span>' : '';
        return pads + '<div class="bk-ev ' + esc(b.status) + (compact ? ' compact' : '') + '" data-bk="' + esc(b.id) + '" style="--c:' + esc(r.color) + ';top:' + top + 'px;height:' + height + 'px" title="' + esc(hm(b.start) + ' ' + (b.client_name || '') + (b.service ? ' · ' + b.service : '')) + '">' +
          (badge && !compact ? '<span class="bdg">' + badge + '</span>' : '') + (!compact ? pp : '') +
          '<div class="tm">' + esc(hm(b.start)) + '</div><div class="cl">' + esc(b.client_name || 'Клиент') + '</div>' +
          (height > 52 ? '<div class="sv">' + esc(b.service || '') + '</div>' : '') + '</div>';
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
        (list.map(function (b) {
          return '<div class="bk-chip ' + esc(b.status) + '" data-bk="' + esc(b.id) + '" style="--c:' + esc(colorOf(b)) + '"><b>' + esc(hm(b.start)) + '</b> ' + esc(b.client_name || 'Клиент') + '</div>';
        }).join('') || '<div style="color:var(--vf-text-4);font-size:12.5px;padding:4px">—</div>') + '</div></div>';
    }
    return out + '</div>';
  }

  // ── события ──
  function onClick(e) {
    var t = e.target;
    var mh = t.closest('[data-mh]');
    if (mh) { editMaster(mh.getAttribute('data-mh')); return; }
    var mf = t.closest('[data-mf]');
    if (mf) { st.filter = mf.getAttribute('data-mf'); render(); return; }
    var nav = t.closest('[data-nav]');
    if (nav) {
      var n = +nav.getAttribute('data-nav');
      st.day = n === 0 ? keyOf(new Date()) : addDays(st.day, n * (st.view === 'week' && !isPhone() ? 7 : 1));
      load(); return;
    }
    var dv = t.closest('[data-day]');
    if (dv) { st.day = dv.getAttribute('data-day'); if (dv.hasAttribute('data-goday')) st.view = 'day'; load(); return; }
    var vw = t.closest('[data-view]');
    if (vw) { st.view = vw.getAttribute('data-view'); try { localStorage.setItem('vf_booking_view', st.view); } catch (x) { /* ignore */ } render(); return; }
    var bk = t.closest('[data-bk]');
    if (bk) { openBooking(bk.getAttribute('data-bk')); return; }
    var col = t.closest('.bk-col');
    if (col && !t.closest('.bk-ev')) {
      var rect = col.getBoundingClientRect();
      var mins = +col.getAttribute('data-lo') + Math.floor((e.clientY - rect.top) / PX / 15) * 15;
      newBooking({ resource_id: col.getAttribute('data-col'), date: st.day, time: String(Math.floor(mins / 60)).padStart(2, '0') + ':' + String(mins % 60).padStart(2, '0') });
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
      '<dt>Цена</dt><dd>' + esc(money(b.price)) + '</dd>' +
      (b.prepay_amount ? '<dt>Предоплата</dt><dd><span class="bk-ppline' + (b.prepaid ? ' ok' : '') + '">' + icon(b.prepaid ? 'circle-check' : 'wallet') +
        esc(money(b.prepay_amount)) + ' · ' + (b.prepaid ? 'получена' : 'ждём') + '</span> ' +
        '<button class="btn btn-ghost btn-sm" type="button" data-prepaid="' + (b.prepaid ? '0' : '1') + '">' + (b.prepaid ? 'Отменить отметку' : 'Отметить «получена»') + '</button></dd>' : '') +
      (b.prep_min || b.after_min ? '<dt>Время мастера</dt><dd>' + (b.prep_min ? 'подготовка ' + b.prep_min + ' мин' : '') + (b.prep_min && b.after_min ? ', ' : '') + (b.after_min ? 'перерыв после ' + b.after_min + ' мин' : '') + '</dd>' : '') +
      '<dt>Статус</dt><dd><b>' + esc(STATUS[b.status] || b.status) + '</b></dd>' +
      '<dt>Откуда</dt><dd>' + esc({ owner: 'Вы записали', staff: 'Сотрудник', bot: 'Бот в переписке', call: 'Голосовой агент', online: 'Онлайн по ссылке' }[b.source] || '—') + '</dd>' +
      (phone ? '<dt>Телефон</dt><dd><a href="tel:' + esc(phone) + '">' + esc(S.fmtPhone ? S.fmtPhone(phone) : phone) + '</a></dd>' : '') +
      (b.note ? '<dt>Комментарий</dt><dd>' + esc(b.note) + '</dd>' : '') +
      '</dl>' +
      '<div class="bk-actions">' +
      (b.status !== 'confirmed' && b.status !== 'arrived' && b.status !== 'cancelled' ? '<button class="btn btn-sm" data-st="confirmed">' + icon('check') + 'Подтвердил</button>' : '') +
      (b.status !== 'arrived' && b.status !== 'cancelled' ? '<button class="btn btn-sm" data-st="arrived" style="color:var(--vf-success)">' + icon('circle-check') + 'Пришёл</button>' : '') +
      (b.status !== 'no_show' && b.status !== 'cancelled' ? '<button class="btn btn-sm" data-st="no_show">' + icon('circle-x') + 'Не пришёл</button>' : '') +
      '<button class="btn btn-sm" data-move>' + icon('calendar') + 'Перенести</button>' +
      (b.status !== 'cancelled' ? '<button class="btn btn-sm" data-cancel style="color:var(--vf-danger)">' + icon('trash-2') + 'Отменить</button>' : '<button class="btn btn-sm" data-st="booked">Вернуть запись</button>') +
      '</div><div class="bk-actions">' +
      (phone ? '<a class="btn btn-ghost btn-sm" href="tel:' + esc(phone) + '">' + icon('phone') + 'Позвонить</a>' : '') +
      (b.contact_id ? '<a class="btn btn-ghost btn-sm" href="/static/inbox.html?contact=' + esc(b.contact_id) + '">' + icon('message-circle') + 'Написать</a>' : '') +
      '</div>' +
      '<div class="field" style="margin-top:14px"><label class="label">Комментарий</label><textarea class="textarea" id="bk-note" rows="2">' + esc(b.note || '') + '</textarea>' +
      '<button class="btn btn-sm" type="button" id="bk-note-save" style="margin-top:6px">Сохранить комментарий</button></div>';
    function patch(body, ok) {
      return S.api('/api/booking/bookings/' + b.id, { method: 'PATCH', body: body }).then(function (res) {
        if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
        S.toast(ok || 'Сохранено'); dr.close(); reload();
      });
    }
    S.$$('[data-st]', dr.body).forEach(function (btn) {
      btn.addEventListener('click', function () { patch({ status: btn.getAttribute('data-st') }, STATUS[btn.getAttribute('data-st')]); });
    });
    var c = S.$('[data-cancel]', dr.body);
    if (c) c.addEventListener('click', function () {
      S.confirm({ title: 'Отменить запись?', message: (b.client_name || 'Клиент') + ', ' + b.label, confirmText: 'Отменить запись', danger: true }).then(function (yes) {
        if (yes) S.api('/api/booking/bookings/' + b.id, { method: 'DELETE' }).then(function (res) {
          if (!res.ok) { S.toast(S.apiError(res), 'error'); return; } S.toast('Запись отменена'); dr.close(); reload();
        });
      });
    });
    S.$('[data-move]', dr.body).addEventListener('click', function () { dr.close(); newBooking({ move: b }); });
    var ppb = S.$('[data-prepaid]', dr.body);
    if (ppb) ppb.addEventListener('click', function () {
      var on = ppb.getAttribute('data-prepaid') === '1';
      patch({ prepaid: on }, on ? 'Предоплата отмечена' : 'Отметка снята');
    });
    S.$('#bk-note-save', dr.body).addEventListener('click', function () { patch({ note: S.$('#bk-note', dr.body).value }); });
  }

  // ── записать / перенести ──
  function newBooking(pre) {
    pre = pre || {};
    var move = pre.move || null;
    var services = (st.data && st.data.services) || [];
    var day = pre.date || (move ? keyOf(new Date(move.start)) : st.day);
    var chosen = null;
    var d = S.dialog({
      title: move ? 'Перенести запись' : 'Новая запись', size: 'lg',
      body:
        (move ? '<p class="muted" style="margin:0 0 10px">' + esc((move.client_name || 'Клиент') + ' · ' + (move.service || '') + ' · сейчас ' + move.label) + '</p>' :
          '<div class="st-grid2"><div class="field"><label class="label">Клиент</label><input class="input" id="nb-name" placeholder="Имя или поиск по клиентам" autocomplete="off"><div id="nb-sugg"></div></div>' +
          '<div class="field"><label class="label">Телефон</label><input class="input" id="nb-phone" type="tel" inputmode="tel" placeholder="+996 700 123 456"></div></div>') +
        '<div class="st-grid2">' +
        (move ? '' : '<div class="field"><label class="label">Услуга</label><select class="select-native" id="nb-svc"><option value="">Без услуги (60 мин)</option>' +
          services.map(function (s) { return '<option value="' + esc(s.id) + '">' + esc((s.subcategory || s.category ? (s.subcategory || s.category) + ' · ' : '') + s.title) + ' · ' + s.duration_min + ' мин' + (s.price_text ? ' · ' + s.price_text : '') + (s.online_booking === false ? ' · только админ' : '') + '</option>'; }).join('') + '</select></div>') +
        '<div class="field"><label class="label">Мастер</label><select class="select-native" id="nb-res"><option value="">Любой свободный</option>' +
        resources().map(function (r) { return '<option value="' + esc(r.id) + '"' + ((pre.resource_id || (move && move.resource_id)) === r.id ? ' selected' : '') + '>' + esc(r.name) + '</option>'; }).join('') + '</select></div>' +
        '<div class="field"><label class="label">День</label><input class="input" id="nb-day" type="date" value="' + esc(day) + '"></div></div>' +
        '<div class="field"><label class="label">Свободное время</label><div class="bk-slots" id="nb-slots"><span class="muted small">Загружаем…</span></div>' +
        '<div class="hint" style="margin-top:6px">Нужно другое время (вне графика)? <input class="input" id="nb-time" type="time" style="display:inline-block;width:auto;height:32px;padding:0 6px" value="' + esc(pre.time || '') + '"></div></div>' +
        (move ? '' : '<div class="field"><label class="label">Комментарий</label><input class="input" id="nb-note" placeholder="Пожелания клиента"></div>'),
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>' + (move ? 'Перенести' : 'Записать') + '</button>'
    });
    var $ = function (s) { return S.$(s, d.el); };
    var contactId = null;
    function loadSlots() {
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
        if (!slots.length) {
          // Подсказка: ближайший день, где есть свободное время (до недели вперёд)
          var q2 = new URLSearchParams(p.toString()); q2.set('date', addDays($('#nb-day').value || day, 1)); q2.set('days', '7');
          S.api('/api/booking/slots?' + q2.toString()).then(function (r2) {
            var next = ((r2.ok && r2.data.days) || []).filter(function (x) { return x.slots.length; })[0];
            var none = $('#nb-none'); if (!next || !none) return;
            var t = dayTitle(next.date);
            none.innerHTML = 'На этот день свободного времени нет. <button type="button" class="btn btn-sm" id="nb-next" style="margin-left:6px">Ближайшее: ' + esc(t.main.toLowerCase()) + ', ' + esc(next.slots[0].time) + '</button>';
            $('#nb-next').addEventListener('click', function () { $('#nb-day').value = next.date; pre.time = next.slots[0].time; loadSlots(); });
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
        '<button type="button" data-tab="m">Мастера</button></div>' +
        '<div data-pane="r">' +
        '<div class="st-grid2">' +
        sel('step_min', 'Шаг расписания', [[15, '15 минут'], [30, '30 минут'], [60, '1 час']], cfg.step_min) +
        sel('buffer_min', 'Перерыв после записи', [[0, 'Без перерыва'], [5, '5 минут'], [10, '10 минут'], [15, '15 минут'], [30, '30 минут']], cfg.buffer_min) +
        sel('lead_min', 'Записываться не позже чем за', [[0, 'Сразу'], [30, '30 минут'], [60, '1 час'], [120, '2 часа'], [1440, 'Сутки']], cfg.lead_min) +
        sel('horizon_days', 'Насколько вперёд можно записаться', [[7, 'Неделя'], [14, '2 недели'], [30, 'Месяц'], [60, '2 месяца']], cfg.horizon_days) +
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
        '<div class="bk-link"><span id="bk-url">' + esc(page.url || '') + '</span></div>' +
        '<div class="bk-actions"><button class="btn btn-sm" type="button" id="bk-copy">' + icon('copy') + 'Скопировать</button>' +
        '<a class="btn btn-sm" target="_blank" rel="noopener" href="' + esc(page.url || '#') + '">' + icon('external-link') + 'Открыть</a></div>' +
        '<div class="field" style="margin-top:12px"><label class="label">Адрес ссылки</label><div style="display:flex;gap:8px"><input class="input" id="bk-slug" value="' + esc(page.slug || '') + '"><button class="btn" type="button" id="bk-slug-save">Сохранить</button></div><div class="hint">Латиница и цифры, например: salon-aidana</div></div>' +
        tgl('enabled', 'Онлайн-запись по ссылке включена', 'Выключите, если временно не принимаете запись', page.enabled) +
        '<div id="bk-kit" class="bk-kit"><div class="muted">Загружаем QR-код и кнопку для сайта…</div></div>' +
        '</div>',
      foot: '<button class="btn" type="button" data-c>Закрыть</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', function () { d.close(); reload(); });
    S.$$('[data-tab]', d.el).forEach(function (b) {
      b.addEventListener('click', function () {
        if (b.getAttribute('data-tab') === 'm') { d.close(); openMasters(); return; }
        S.$$('[data-tab]', d.el).forEach(function (x) { x.classList.toggle('on', x === b); });
        S.$$('[data-pane]', d.el).forEach(function (p) { p.hidden = p.getAttribute('data-pane') !== b.getAttribute('data-tab'); });
        if (b.getAttribute('data-tab') === 'l') drawLinkKit(d);
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
  // ── старт ──
  function init() {
    root = document.getElementById('bk');
    esc = S.esc; icon = S.icon;
    S.initMobileMenu();
    try { var v = localStorage.getItem('vf_booking_view'); if (v === 'week' || v === 'day') st.view = v; } catch (e) { /* ignore */ }
    var q = new URLSearchParams(location.search);
    st.day = q.get('date') || keyOf(new Date());
    if (q.get('view') === 'masters' || q.get('view') === 'services') { st.prevView = st.view; st.view = q.get('view'); }
    var acts = document.getElementById('bk-actions');
    // Мастера, график, правила и ссылка — у владельца и сотрудника с разделом «Мастера и график»
    var setup = canSetup();
    acts.innerHTML = '<button class="btn btn-sm" type="button" id="bk-services-btn" title="Услуги: прайс, время, кто делает">' + icon('sparkles') + '<span class="lbl">Услуги</span></button>' +
      '<button class="btn btn-sm" type="button" id="bk-masters-btn" title="Мастера: фото, график, услуги и цены">' + icon('users') + '<span class="lbl">Мастера</span></button>' +
      (setup ? '<button class="btn btn-sm" type="button" id="bk-link-btn" title="Ссылка для онлайн-записи">' + icon('link-2') + '<span class="lbl">Ссылка для клиентов</span></button>' +
      '<button class="btn btn-sm btn-icon" type="button" id="bk-set" title="Настройки записи" aria-label="Настройки">' + icon('settings') + '</button>' : '') +
      '<button class="btn btn-primary btn-sm" type="button" id="bk-new">' + icon('plus') + '<span class="lbl">Записать</span></button>';
    document.getElementById('bk-new').addEventListener('click', function () { newBooking({}); });
    document.getElementById('bk-masters-btn').addEventListener('click', openMasters);
    document.getElementById('bk-services-btn').addEventListener('click', openServices);
    if (setup) {
      document.getElementById('bk-set').addEventListener('click', settings);
      document.getElementById('bk-link-btn').addEventListener('click', function () {
        settings(); setTimeout(function () { var t = document.querySelector('[data-tab="l"]'); if (t) t.click(); }, 30);
      });
    }
    var fab = document.createElement('button');
    fab.className = 'bk-fab'; fab.type = 'button'; fab.innerHTML = icon('plus') + 'Записать';
    fab.addEventListener('click', function () { newBooking({ date: st.day }); });
    document.body.appendChild(fab);
    root.addEventListener('click', onClick);
    root.addEventListener('change', function (e) { if (e.target.id === 'bk-filter') { st.filter = e.target.value; render(); } });
    load();
    setInterval(function () { if (!document.hidden && !document.querySelector('.vf-backdrop')) { S.bust(); load(true); } }, 60000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
