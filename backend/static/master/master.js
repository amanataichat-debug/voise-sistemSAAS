/**
 * Кабинет мастера на телефоне (Voksy Beauty, шаг 1.2): /static/master.html.
 *
 * Мастер (сотрудник с разделом «Свой журнал», вход привязан к его карточке в журнале) видит только своё:
 *   Расписание — лента дней, записи дня и свободные окна; запись: подтвердил / пришёл / не пришёл,
 *                перенести (своё свободное время), заметка, позвонить (если владелец разрешил телефоны);
 *                «Записать» — клиента к себе;
 *   Клиенты    — кто был и будет у мастера, история визитов, «Записать снова»;
 *   Доход      — заработок сегодня / за неделю / за месяц (% с визитов + оклад) и статистика за 30 дней;
 *   Профиль    — фото, «о себе», свои работы (портфолио) — их видят клиенты на странице салона.
 * Колокольчик — новые записи и отмены за неделю. Владелец открывает ?resource_id= — «как видит мастер».
 * API: /api/booking/me* (services/master_cabinet.py). Отменить запись или увидеть чужие мастер не может.
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc, icon;
  var TZ = 'Asia/Bishkek';
  var WD = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
  var WDF = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье'];
  var MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var MONS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  var DKEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
  var STATUS = { booked: 'Записан', confirmed: 'Подтвердил', arrived: 'Пришёл', completed: 'Завершён', no_show: 'Не пришёл', cancelled: 'Отменена' };
  function visited(s) { return s === 'arrived' || s === 'completed'; }
  var TABS = [['day', 'Расписание', 'calendar'], ['clients', 'Клиенты', 'users'], ['money', 'Доход', 'wallet'], ['me', 'Профиль', 'user-round']];
  var st = { tab: 'day', day: null, win: null, data: null, clients: null, q: '', rid: null, err: null };
  var root;

  // ── даты ──
  function keyOf(d) { return d.toLocaleDateString('en-CA', { timeZone: TZ }); }
  function parseKey(k) { var p = k.split('-'); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2], 6)); }
  function addDays(k, n) { var d = parseKey(k); d.setUTCDate(d.getUTCDate() + n); return keyOf(d); }
  function weekday(k) { return (parseKey(k).getUTCDay() + 6) % 7; }
  function hm(iso) { return new Date(iso).toLocaleTimeString('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }); }
  function minOf(iso) { var t = hm(iso).split(':'); return +t[0] * 60 + +t[1]; }
  function toMin(t) { var p = String(t || '0:0').split(':'); return +p[0] * 60 + +(p[1] || 0); }
  function fmtMin(m) { return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); }
  function today() { return keyOf(new Date()); }
  function dayLong(k) {
    var d = parseKey(k), t = today();
    var rel = k === t ? 'Сегодня' : k === addDays(t, 1) ? 'Завтра' : k === addDays(t, -1) ? 'Вчера' : '';
    var full = WDF[weekday(k)] + ', ' + d.getUTCDate() + ' ' + MON[d.getUTCMonth()];
    return rel ? rel + ', ' + full : full.charAt(0).toUpperCase() + full.slice(1);
  }
  function shortDate(iso) {
    var k = keyOf(new Date(iso)), d = parseKey(k);
    return d.getUTCDate() + ' ' + MONS[d.getUTCMonth()];
  }
  function money(v) { return Math.round(Number(v) || 0).toLocaleString('ru-RU') + ' сом'; }
  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    return n + ' ' + (m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many);
  }
  function ini(name) {
    var p = String(name || '?').trim().split(/\s+/);
    return ((p[0] || '?')[0] + (p[1] ? p[1][0] : '')).toUpperCase();
  }
  function ava(m, size) {
    var s = size || 40;
    var inner = m && m.photo_url ? '<img src="' + esc(m.photo_url) + '" alt="" decoding="async">' : '<b>' + esc((m && m.initials) || ini(m && m.name)) + '</b>';
    return '<span class="bk-ava" style="--c:' + esc((m && m.color) || '#2a5ce8') + ';--s:' + s + 'px">' + inner + '</span>';
  }
  function q(path) {
    if (!st.rid) return path;
    return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'resource_id=' + encodeURIComponent(st.rid);
  }
  function vibrate() { try { if (navigator.vibrate) navigator.vibrate(25); } catch (e) { /* ignore */ } }

  // ── загрузка ──
  function load() {
    var win = addDays(st.day, -7);
    st.win = win;
    return S.api(q('/api/booking/me?date=' + win + '&days=28')).then(function (r) {
      if (!r.ok) {
        st.err = r;
        drawError(r);
        S.ready();
        return;
      }
      var prev = st.data;
      st.data = r.data; st.err = null;
      notifyNew(prev, r.data);
      render();
      S.ready();
    });
  }
  function inWindow(k) { return st.win && k >= st.win && k < addDays(st.win, 28); }
  function master() { return (st.data && st.data.master) || {}; }
  function bookingsOf(k) {
    return ((st.data && st.data.bookings) || []).filter(function (b) { return keyOf(new Date(b.start)) === k; })
      .sort(function (a, b) { return a.start < b.start ? -1 : 1; });
  }

  function drawError(r) {
    var d = r.data && r.data.detail;
    var code = d && d.error;
    var msg = S.apiError(r);
    root.innerHTML = '<div class="mc-empty">' + icon(code === 'master_not_linked' ? 'key-round' : 'circle-alert') +
      '<h3>' + (code === 'master_not_linked' ? 'Кабинет ещё не настроен' : code === 'master_disabled' ? 'Карточка мастера выключена' : 'Не получилось открыть') + '</h3>' +
      '<p>' + esc(msg) + '</p>' +
      '<button class="btn" type="button" data-retry>' + icon('refresh-cw') + '<span>Обновить</span></button></div>';
    S.$('[data-retry]', root).addEventListener('click', function () { load(); });
    navBar();
  }

  // Новое за время, пока страница открыта: короткое уведомление
  function notifyNew(prev, next) {
    if (!prev || !next) return;
    var seen = {};
    (prev.feed || []).forEach(function (f) { seen[f.id + f.kind] = 1; });
    var fresh = (next.feed || []).filter(function (f) { return !seen[f.id + f.kind]; });
    if (!fresh.length) return;
    var f = fresh[0];
    S.toast((f.kind === 'cancelled' ? 'Отменена запись: ' : 'Новая запись: ') + (f.client_name || 'клиент') + ', ' + f.label);
    vibrate();
  }

  // ── каркас ──
  function render() {
    if (!st.data) return;
    var m = master();
    S.$('#mc-title').textContent = st.data.preview && !S.staff() ? 'Кабинет мастера' : 'Мой день';
    drawActions();
    var html = (st.data.preview && !S.staff() ? '<div class="mc-preview">' + icon('eye') + '<span>Так <b>' + esc(m.name) + '</b> видит свой кабинет на телефоне.</span>' +
      '<a class="btn btn-sm" href="/static/booking.html?view=masters">К мастерам</a></div>' : '') +
      hero() +
      '<div class="mc-tabs" role="tablist">' + TABS.map(function (t) {
        return '<button type="button" role="tab" data-tab="' + t[0] + '" class="' + (st.tab === t[0] ? 'on' : '') + '">' + icon(t[2]) + '<span>' + t[1] + '</span></button>';
      }).join('') + '</div>' +
      '<div class="mc-pane" id="mc-pane"></div>';
    root.innerHTML = html;
    root.setAttribute('data-tab', st.tab);
    drawPane();
    navBar();
  }

  function hero() {
    var m = master(), e = st.data.earnings || {}, t = today();
    var list = bookingsOf(t).filter(function (b) { return b.status !== 'cancelled'; });
    var done = list.filter(function (b) { return visited(b.status); }).length;
    var next = list.filter(function (b) { return new Date(b.end) > new Date() && (b.status === 'booked' || b.status === 'confirmed'); })[0];
    var hour = +new Date().toLocaleString('en-GB', { timeZone: TZ, hour: '2-digit', hour12: false }) % 24;
    var hello = hour < 5 ? 'Доброй ночи' : hour < 12 ? 'Доброе утро' : hour < 18 ? 'Добрый день' : 'Добрый вечер';
    var pct = list.length ? Math.round(done * 100 / list.length) : 0;
    return '<section class="mc-hero" style="--c:' + esc(m.color || '#2a5ce8') + '">' +
      '<div class="mc-hero-top">' + ava(m, 52) + '<div class="mc-hello"><div class="mc-hi">' + hello + ', ' + esc(String(m.name || '').split(' ')[0]) + '</div>' +
      '<div class="mc-sub">' + esc(dayLong(t)) + (st.data.company ? ' · ' + esc(st.data.company) : '') + '</div></div></div>' +
      '<div class="mc-hero-grid">' +
      '<div class="mc-stat"><span>Записей сегодня</span><b>' + list.length + '</b><small>' + (list.length ? 'пришли ' + done + ' из ' + list.length : 'свободный день') + '</small></div>' +
      '<div class="mc-stat"><span>Заработано сегодня</span><b>' + money(e.today ? e.today.salary : 0) + '</b><small>' +
      (e.expected_today && e.expected_today.salary ? 'ещё ≈ ' + money(e.expected_today.salary) : (e.configured ? 'по вашей ставке' : 'ставку укажет администратор')) + '</small></div></div>' +
      (list.length ? '<div class="mc-bar"><i style="width:' + pct + '%"></i></div>' : '') +
      (next ? '<button type="button" class="mc-next" data-bk="' + esc(next.id) + '">' + icon('clock') + '<span>Дальше: <b>' + esc(hm(next.start)) + '</b> · ' + esc(next.client_name || 'Клиент') +
        (next.service ? ' · ' + esc(next.service) : '') + '</span>' + icon('chevron-right') + '</button>' : '') +
      '</section>';
  }

  // Когда мастер последний раз смотрел «новое». Первый заход — новым считаем только последние сутки.
  function feedSeenAt() {
    var key = 'mc_feed_seen_' + master().id, v = 0;
    try {
      v = +localStorage.getItem(key) || 0;
      if (!v) { v = Date.now() - 864e5; localStorage.setItem(key, String(v)); }
    } catch (e) { v = Date.now() - 864e5; }
    return v;
  }
  function drawActions() {
    var box = S.$('#mc-actions');
    var feed = (st.data && st.data.feed) || [];
    var seenAt = feedSeenAt();
    var unread = feed.filter(function (f) { return new Date(f.at).getTime() > seenAt; }).length;
    box.innerHTML = '<button class="btn btn-ghost btn-icon mc-bell" type="button" id="mc-bell" aria-label="Новое">' + icon('bell') +
      (unread ? '<i>' + (unread > 9 ? '9+' : unread) + '</i>' : '') + '</button>' +
      '<button class="btn btn-primary btn-sm mc-desk" type="button" id="mc-new">' + icon('plus') + '<span>Записать</span></button>';
    S.$('#mc-bell').addEventListener('click', openFeed);
    S.$('#mc-new').addEventListener('click', function () { newBooking({}); });
  }

  // своя нижняя панель на телефоне (общая панель кабинета здесь не нужна)
  function navBar() {
    var bar = document.getElementById('mc-nav');
    if (!bar) {
      bar = document.createElement('nav');
      bar.id = 'mc-nav'; bar.className = 'mc-nav'; bar.setAttribute('aria-label', 'Разделы');
      document.body.appendChild(bar);
      bar.addEventListener('click', function (e) {
        var b = e.target.closest('[data-tab]');
        if (b) { setTab(b.getAttribute('data-tab')); return; }
        if (e.target.closest('[data-fab]')) newBooking({});
      });
    }
    bar.innerHTML = TABS.slice(0, 2).map(navBtn).join('') +
      '<button type="button" class="mc-fab" data-fab aria-label="Записать клиента">' + icon('plus') + '</button>' +
      TABS.slice(2).map(navBtn).join('');
    function navBtn(t) {
      return '<button type="button" data-tab="' + t[0] + '" class="' + (st.tab === t[0] ? 'on' : '') + '">' + icon(t[2]) + '<span>' + t[1] + '</span></button>';
    }
  }
  function setTab(t) {
    st.tab = t;
    if (root) root.setAttribute('data-tab', t);
    try { sessionStorage.setItem('mc_tab', t); } catch (e) { /* ignore */ }
    if (!st.data) return;
    S.$$('.mc-tabs [data-tab]', root).forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-tab') === t); });
    S.$$('#mc-nav [data-tab]').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-tab') === t); });
    drawPane();
    var tabs = S.$('.mc-tabs', root);
    if (tabs && tabs.getBoundingClientRect().top < 0) tabs.scrollIntoView({ block: 'start' });
  }
  function drawPane() {
    var p = S.$('#mc-pane', root);
    if (!p) return;
    if (st.tab === 'day') p.innerHTML = paneDay();
    else if (st.tab === 'clients') { p.innerHTML = paneClients(); if (!st.clients) loadClients(); }
    else if (st.tab === 'money') p.innerHTML = paneMoney();
    else p.innerHTML = paneMe();
    bindPane(p);
  }

  // ── расписание ──
  function paneDay() {
    var start = addDays(today(), -2), strip = '';
    for (var i = 0; i < 16; i++) {
      var k = addDays(start, i), d = parseKey(k);
      var n = bookingsOf(k).filter(function (b) { return b.status !== 'cancelled'; }).length;
      var off = isOff(k);
      strip += '<button type="button" class="mc-dpill' + (k === st.day ? ' on' : '') + (k === today() ? ' today' : '') + (off ? ' off' : '') + '" data-day="' + k + '">' +
        '<span class="w">' + WD[weekday(k)] + '</span><span class="d">' + d.getUTCDate() + '</span><span class="c">' + (n ? n : off ? '—' : '') + '</span></button>';
    }
    return '<div class="mc-strip" id="mc-strip">' + strip + '</div>' +
      '<div class="mc-dayh"><div><b>' + esc(dayLong(st.day)) + '</b><span>' + esc(workText(st.day)) + '</span></div>' +
      '<div class="mc-dnav"><button type="button" class="btn btn-ghost btn-icon btn-sm" data-shift="-1" aria-label="Предыдущий день">' + icon('chevron-left') + '</button>' +
      '<button type="button" class="btn btn-ghost btn-icon btn-sm" data-shift="1" aria-label="Следующий день">' + icon('chevron-right') + '</button></div></div>' +
      '<div class="mc-timeline">' + timeline(st.day) + '</div>';
  }
  function workIntervals(k) {
    if (isOff(k)) return [];
    return ((master().schedule || {})[DKEYS[weekday(k)]] || []).map(function (iv) { return [toMin(iv[0]), toMin(iv[1])]; });
  }
  function isOff(k) {
    var m = master();
    return (m.days_off || []).indexOf(k) >= 0 || !((m.schedule || {})[DKEYS[weekday(k)]] || []).length;
  }
  function workText(k) {
    var iv = workIntervals(k);
    if (!iv.length) return 'Выходной';
    return 'Работаю ' + fmtMin(iv[0][0]) + '–' + fmtMin(iv[iv.length - 1][1]) + (iv.length > 1 ? ', перерыв ' + fmtMin(iv[0][1]) + '–' + fmtMin(iv[1][0]) : '');
  }
  function timeline(k) {
    var list = bookingsOf(k);
    var active = list.filter(function (b) { return b.status !== 'cancelled'; });
    var items = [];
    list.forEach(function (b) { items.push({ t: minOf(b.start), b: b }); });
    // свободные окна ≥ 30 минут в рабочее время (сегодня — только впереди)
    var nowMin = k === today() ? minOf(new Date().toISOString()) : (k < today() ? 24 * 60 : -1);
    workIntervals(k).forEach(function (iv) {
      var cur = Math.max(iv[0], nowMin);
      active.filter(function (b) { var s = minOf(b.start); return s < iv[1] && s + b.duration_min > iv[0]; }).forEach(function (b) {
        var s = minOf(b.start);
        if (s - cur >= 30) items.push({ t: cur, gap: [cur, s] });
        cur = Math.max(cur, s + b.duration_min);
      });
      if (iv[1] - cur >= 30) items.push({ t: cur, gap: [cur, iv[1]] });
    });
    items.sort(function (a, b) { return a.t - b.t || (a.gap ? 1 : -1); });
    if (!items.length) {
      return '<div class="mc-empty sm">' + icon('calendar') + '<p>' + (isOff(k) ? 'Выходной день. Отдыхайте!' : k < today() ? 'В этот день записей не было.' : 'Записей пока нет.') + '</p>' +
        (k >= today() ? '<button class="btn btn-sm" type="button" data-newday>' + icon('plus') + '<span>Записать клиента</span></button>' : '') + '</div>';
    }
    var nowLine = k === today() ? nowMin : -1, placed = false;
    return items.map(function (it) {
      var line = '';
      if (nowLine >= 0 && !placed && it.t >= nowLine) { placed = true; line = '<div class="mc-now"><span>' + fmtMin(nowLine) + '</span></div>'; }
      if (it.gap) {
        return line + '<button type="button" class="mc-gap" data-gap="' + fmtMin(it.gap[0]) + '">' +
          '<span class="t">' + fmtMin(it.gap[0]) + '</span><span class="g">Свободно ' + fmtMin(it.gap[0]) + '–' + fmtMin(it.gap[1]) + ' · ' + durText(it.gap[1] - it.gap[0]) + '</span>' +
          '<span class="add">' + icon('plus') + '</span></button>';
      }
      var b = it.b;
      return line + '<article class="mc-bk ' + esc(b.status) + '" data-bk="' + esc(b.id) + '" tabindex="0">' +
        '<div class="t"><b>' + esc(hm(b.start)) + '</b><span>' + esc(hm(b.end)) + '</span></div>' +
        '<div class="c"><div class="n">' + esc(b.client_name || 'Клиент') + '</div>' +
        '<div class="s">' + esc(b.service || 'Без услуги') + ' · ' + durText(b.duration_min) + (b.price != null ? ' · ' + money(b.price) : '') + '</div>' +
        (b.note ? '<div class="note">' + icon('sticky-note') + esc(b.note) + '</div>' : '') +
        (b.prepay_amount && b.status !== 'cancelled' ? '<div class="pp' + (b.prepaid ? ' ok' : '') + '">' + icon('wallet') +
          (b.prepaid ? 'Предоплата ' + money(b.prepay_amount) + ' получена' : 'Ждём предоплату ' + money(b.prepay_amount)) + '</div>' : '') + '</div>' +
        '<span class="chip ' + esc(b.status) + '">' + esc(STATUS[b.status] || b.status) + '</span></article>';
    }).join('') + (nowLine >= 0 && !placed && nowLine < 24 * 60 ? '<div class="mc-now"><span>' + fmtMin(nowLine) + '</span></div>' : '');
  }
  function durText(m) { return m >= 60 ? (Math.floor(m / 60) + ' ч' + (m % 60 ? ' ' + (m % 60) + ' мин' : '')) : m + ' мин'; }

  // ── клиенты ──
  function loadClients() {
    S.api(q('/api/booking/me/clients')).then(function (r) {
      st.clients = r.ok ? r.data.clients : [];
      if (st.tab === 'clients') drawPane();
    });
  }
  function paneClients() {
    if (!st.clients) return '<div class="skeleton" style="height:64px;border-radius:14px;margin-bottom:8px"></div><div class="skeleton" style="height:64px;border-radius:14px"></div>';
    var list = st.clients.filter(function (c) { return !st.q || (c.name || '').toLowerCase().indexOf(st.q) >= 0 || (c.phone || '').indexOf(st.q) >= 0; });
    return '<div class="mc-search">' + icon('search') + '<input type="search" id="mc-q" placeholder="Найти клиента" value="' + esc(st.q) + '"></div>' +
      (st.clients.length ? '<div class="mc-count">' + plural(st.clients.length, 'клиент', 'клиента', 'клиентов') + '</div>' : '') +
      (list.length ? '<div class="mc-clients">' + list.map(function (c, i) {
        return '<button type="button" class="mc-cl" data-cl="' + i + '"><span class="mc-ini">' + esc(ini(c.name)) + '</span>' +
          '<span class="b"><b>' + esc(c.name) + '</b><span>' + (c.visits ? plural(c.visits, 'визит', 'визита', 'визитов') : 'ещё не был') +
          (c.last_visit ? ' · был ' + esc(shortDate(c.last_visit)) : '') + (c.no_show ? ' · неявок ' + c.no_show : '') + '</span></span>' +
          (c.next_visit ? '<span class="nx">' + esc(shortDate(c.next_visit)) + '<small>' + esc(hm(c.next_visit)) + '</small></span>' : '') + '</button>';
      }).join('') + '</div>' : '<div class="mc-empty sm">' + icon('users') + '<p>' + (st.q ? 'Никого не нашли.' : 'Здесь появятся ваши клиенты — все, кто записывался к вам.') + '</p></div>');
  }
  function filteredClients() {
    return (st.clients || []).filter(function (c) { return !st.q || (c.name || '').toLowerCase().indexOf(st.q) >= 0 || (c.phone || '').indexOf(st.q) >= 0; });
  }
  function openClient(c) {
    var dlg = S.dialog({
      title: c.name,
      body: '<div class="mc-cstats"><div><b>' + c.visits + '</b><span>визитов</span></div><div><b>' + money(c.spent) + '</b><span>оплачено</span></div>' +
        '<div><b>' + (c.no_show || 0) + '</b><span>неявок</span></div></div>' +
        (c.phone ? '<a class="btn btn-sm" href="tel:' + esc(c.phone) + '" style="margin-bottom:12px">' + icon('phone') + '<span>' + esc(c.phone) + '</span></a>' : '') +
        (c.next_visit ? '<div class="mc-note-in">' + icon('calendar') + '<span>Следующий визит: <b>' + esc(dayLong(keyOf(new Date(c.next_visit)))) + ', ' + esc(hm(c.next_visit)) + '</b></span></div>' : '') +
        '<div class="mc-hist">' + (c.history || []).map(function (h) {
          return '<div class="mc-hrow ' + esc(h.status) + '"><span class="d">' + esc(shortDate(h.start)) + '</span><span class="b"><b>' + esc(h.service || 'Без услуги') + '</b>' +
            (h.note ? '<small>' + esc(h.note) + '</small>' : '') + '</span><span class="chip ' + esc(h.status) + '">' + esc(h.status_title) + '</span></div>';
        }).join('') + '</div>',
      foot: '<button class="btn" type="button" data-c>Закрыть</button><button class="btn btn-primary" type="button" data-again>' + icon('calendar-plus') + '<span>Записать снова</span></button>'
    });
    S.$('[data-c]', dlg.el).addEventListener('click', dlg.close);
    S.$('[data-again]', dlg.el).addEventListener('click', function () {
      dlg.close();
      newBooking({ name: c.name, phone: c.phone || '', contact_id: c.contact_id });
    });
  }

  // ── доход и статистика ──
  function paneMoney() {
    var e = st.data.earnings || {}, s = st.data.stats || {};
    function card(key, title) {
      var x = e[key] || {};
      return '<div class="mc-money"><span>' + title + '</span><b>' + money(x.salary) + '</b><small>' + plural(x.visits || 0, 'визит', 'визита', 'визитов') + ' · выручка ' + money(x.revenue) + '</small></div>';
    }
    var maxTop = Math.max.apply(null, [1].concat((s.top_services || []).map(function (t) { return t.count; })));
    return '<div class="mc-moneys">' + card('today', 'Сегодня') + card('week', 'Эта неделя') + card('month', 'Этот месяц') + '</div>' +
      (e.expected_today && e.expected_today.visits ? '<div class="mc-note-in good">' + icon('trending-up') + '<span>Сегодня ещё ' + plural(e.expected_today.visits, 'запись', 'записи', 'записей') +
        ' — это ≈ <b>' + money(e.expected_today.salary) + '</b> к заработку.</span></div>' : '') +
      payrollBlock(e.payroll) +
      '<div class="mc-how">' + icon('info') + '<span>' + (e.configured ? 'Ваша ставка: <b>' + esc(e.scheme_text) + '</b>. Процент — с оплаченных услуг (пока салон не работает через кассу — с визитов «пришёл»), материалы не вычитаются' +
        (e.pay_shift ? '; выход отмечает администратор' : '') + '.'
        : 'Администратор ещё не указал вашу ставку — заработок появится, как только он её внесёт. Выручку по визитам видно уже сейчас.') + '</span></div>' +
      '<h3 class="mc-h3">За 30 дней</h3>' +
      '<div class="mc-kpis">' +
      kpi('Визитов', s.visits || 0) + kpi('Средний чек', s.avg_check ? money(s.avg_check) : '—') +
      kpi('Вернулись', (s.returning_pct || 0) + '%', s.clients ? s.returning + ' из ' + s.clients : '') +
      kpi('Неявки', (s.no_show_pct || 0) + '%', s.no_show ? plural(s.no_show, 'раз', 'раза', 'раз') : '', s.no_show_pct > 15 ? 'bad' : '') + '</div>' +
      (s.rating ? '<h3 class="mc-h3">Отзывы клиентов</h3><div class="mc-rv"><div class="mc-rv-top"><b>' + String(s.rating.avg).replace('.', ',') +
        '</b><span class="mc-stars">' + stars(s.rating.avg) + '</span><small>' + plural(s.rating.count, 'оценка', 'оценки', 'оценок') + '</small></div>' +
        (s.reviews || []).map(function (r) {
          return '<div class="mc-rv-it"><div class="mc-rv-h"><span class="mc-stars">' + stars(r.score) + '</span><span>' + esc(r.author || 'Клиент') +
            (r.date ? ' · ' + esc(shortDate(r.date)) : '') + '</span></div><p>' + esc(r.text) + '</p></div>';
        }).join('') + '</div>' : '') +
      ((s.top_services || []).length ? '<h3 class="mc-h3">Чаще всего делаете</h3><div class="mc-top">' + s.top_services.map(function (t) {
        return '<div class="mc-trow"><span class="n">' + esc(t.title) + '</span><span class="bar"><i style="width:' + Math.round(t.count * 100 / maxTop) + '%"></i></span><b>' + t.count + '</b></div>';
      }).join('') + '</div>' : '');
  }
  // Зарплата за месяц (шаг 3.3): начислено, премии, штрафы с причиной, авансы, выплаты — «К выплате»
  function payrollBlock(p) {
    if (!p || (!p.accrued && !p.paid && !p.advances && !(p.adjustments || []).length)) return '';
    var rows = '<div><span>Начислено' + (p.label ? ' за ' + esc(p.label) : '') + '</span><b>' + money(p.accrued) + '</b></div>';
    if (p.shifts && p.shifts.count) rows += '<div class="sub"><span>в том числе выходы · ' + plural(p.shifts.count, 'день', 'дня', 'дней') + '</span><b>' + money(p.shifts.amount) + '</b></div>';
    (p.adjustments || []).forEach(function (a) {
      var minus = a.kind !== 'bonus';
      rows += '<div class="' + (a.kind === 'fine' ? 'bad' : a.kind === 'bonus' ? 'good' : '') + '"><span>' + esc(a.title) + ' · ' + esc(shortDate(a.day)) + (a.comment ? ' · ' + esc(a.comment) : '') + '</span><b>' +
        (a.kind === 'advance' ? '−' : minus ? '−' : '+') + money(a.amount) + '</b></div>';
    });
    (p.payouts || []).forEach(function (x) { rows += '<div><span>Выплачено · за ' + esc(x.period) + '</span><b>−' + money(x.amount) + '</b></div>'; });
    return '<h3 class="mc-h3">Зарплата за месяц</h3><div class="mc-pay">' + rows +
      '<div class="tot"><span>К выплате</span><b>' + money(p.to_pay) + '</b></div></div>';
  }
  function stars(v) { var n = Math.round(+v || 0), o = ''; for (var i = 1; i <= 5; i++) o += '<i class="' + (i <= n ? 'on' : '') + '">★</i>'; return o; }
  function kpi(t, v, sub, cls) { return '<div class="mc-kpi ' + (cls || '') + '"><span>' + esc(t) + '</span><b>' + esc(v) + '</b>' + (sub ? '<small>' + esc(sub) + '</small>' : '') + '</div>'; }

  // ── профиль ──
  function paneMe() {
    var m = master(), pf = m.portfolio || [];
    var staff = S.staff();
    return '<div class="mc-me">' + ava(m, 88) + '<div><h3>' + esc(m.name) + '</h3><span>' + esc(m.role || 'Мастер') + (m.experience_years ? ' · опыт ' + plural(m.experience_years, 'год', 'года', 'лет') : '') + '</span></div></div>' +
      '<div class="field"><label class="label" for="mc-bio">О себе <span class="muted">— видят клиенты на странице салона</span></label>' +
      '<textarea class="textarea" id="mc-bio" rows="4" maxlength="1500" placeholder="Чем вы особенно хороши, на каких материалах работаете, что любите делать">' + esc(m.bio || '') + '</textarea>' +
      '<button class="btn btn-sm" type="button" data-savebio style="margin-top:8px">' + icon('check') + '<span>Сохранить</span></button></div>' +
      '<div class="mc-sec-h"><div><b>Мои работы</b><span class="muted small"> · ' + pf.length + ' из 30</span></div>' +
      (pf.length < 30 ? '<button class="btn btn-sm" type="button" data-addwork>' + icon('upload') + '<span>Добавить</span></button>' : '') + '</div>' +
      (pf.length ? '<div class="mc-gal">' + pf.map(function (it) {
        return '<figure><img src="' + esc(it.url) + '" alt="' + esc(it.caption || '') + '" loading="lazy">' + (it.caption ? '<figcaption>' + esc(it.caption) + '</figcaption>' : '') +
          '<button type="button" data-rmwork="' + esc(it.id) + '" aria-label="Удалить">' + icon('trash-2') + '</button></figure>';
      }).join('') + '</div>' : '<button type="button" class="mc-drop" data-addwork>' + icon('image') + '<b>Покажите свои работы</b><span>Фото маникюра, стрижек, окрашивания — клиенты чаще записываются к мастеру с портфолио.</span></button>') +
      '<div class="mc-prog" id="mc-prog" hidden></div>' +
      '<div class="mc-acc">' +
      (staff ? '<button class="btn" type="button" data-pw>' + icon('key-round') + '<span>Сменить пароль</span></button>' : '') +
      (staff ? '<button class="btn btn-ghost" type="button" data-out style="color:var(--vf-danger)">' + icon('log-out') + '<span>Выйти</span></button>' : '') + '</div>';
  }

  // ── события на панели ──
  function bindPane(p) {
    var strip = S.$('#mc-strip', p);
    if (strip) { var on = S.$('.mc-dpill.on', strip); if (on && on.scrollIntoView) on.scrollIntoView({ inline: 'center', block: 'nearest' }); }
    var qi = S.$('#mc-q', p);
    if (qi) qi.addEventListener('input', function () {
      st.q = qi.value.trim().toLowerCase();
      var pos = qi.selectionStart;
      drawPane();
      var n = S.$('#mc-q', root); if (n) { n.focus(); try { n.setSelectionRange(pos, pos); } catch (e) { /* ignore */ } }
    });
    var bio = S.$('[data-savebio]', p);
    if (bio) bio.addEventListener('click', function () {
      bio.disabled = true;
      S.api(q('/api/booking/me/profile'), { method: 'PATCH', body: { bio: S.$('#mc-bio', p).value } }).then(function (r) {
        bio.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        st.data.master = r.data.master; S.toast('Сохранено');
      });
    });
    S.$$('[data-addwork]', p).forEach(function (b) { b.addEventListener('click', addWorks); });
    S.$$('[data-rmwork]', p).forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        S.confirm({ title: 'Удалить фото?', message: 'Фото пропадёт и со страницы салона.', confirmText: 'Удалить', danger: true }).then(function (yes) {
          if (!yes) return;
          S.api(q('/api/booking/me/media/' + b.getAttribute('data-rmwork')), { method: 'DELETE' }).then(function (r) {
            if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
            st.data.master = r.data.master; drawPane();
          });
        });
      });
    });
    var pw = S.$('[data-pw]', p);
    if (pw) pw.addEventListener('click', function () { if (window.VoksiAISidebar && VoksiAISidebar.openStaffPassword) VoksiAISidebar.openStaffPassword(); });
    var out = S.$('[data-out]', p);
    if (out) out.addEventListener('click', function () { if (window.VoksiAISidebar) VoksiAISidebar.logout(); });
  }
  function addWorks() {
    var inp = document.createElement('input');
    inp.type = 'file'; inp.accept = 'image/*'; inp.multiple = true;
    inp.addEventListener('change', function () {
      var files = Array.prototype.slice.call(inp.files || []).slice(0, 30 - ((master().portfolio || []).length));
      if (!files.length) return;
      var i = 0, ok = 0, prog = S.$('#mc-prog', root);
      (function next() {
        if (i >= files.length) {
          if (prog) prog.hidden = true;
          drawPane();
          S.toast(ok === files.length ? (ok === 1 ? 'Фото добавлено' : 'Добавлено фото: ' + ok) : 'Добавлено ' + ok + ' из ' + files.length, ok === files.length ? '' : 'error');
          return;
        }
        if (prog) { prog.hidden = false; prog.innerHTML = '<span class="mc-spin"></span>Загружаем ' + (i + 1) + ' из ' + files.length + '…'; }
        S.upload(q('/api/booking/me/media'), files[i]).then(function (r) {
          i++;
          if (r.ok) { ok++; st.data.master = r.data.master; } else if (!ok) S.toast(S.apiError(r), 'error');
          next();
        });
      })();
    });
    inp.click();
  }

  // ── запись: карточка ──
  function openBooking(id) {
    var b = ((st.data && st.data.bookings) || []).filter(function (x) { return x.id === id; })[0];
    if (!b) return;
    var past = new Date(b.end) < new Date();
    var acts = '';
    if (b.status !== 'cancelled') {
      if (b.status === 'arrived') acts += '<button class="btn mc-act good" type="button" data-st="completed">' + icon('check-check') + '<span>Завершить визит</span></button>';
      if (!visited(b.status)) acts += '<button class="btn mc-act good" type="button" data-st="arrived">' + icon('circle-check') + '<span>Пришёл</span></button>';
      if (b.status !== 'no_show' && b.status !== 'completed') acts += '<button class="btn mc-act bad" type="button" data-st="no_show">' + icon('circle-x') + '<span>Не пришёл</span></button>';
      if (b.status === 'booked' && !past) acts += '<button class="btn mc-act" type="button" data-st="confirmed">' + icon('check') + '<span>Подтвердил</span></button>';
      if (b.status === 'completed') acts += '<button class="btn mc-act" type="button" data-st="arrived">' + icon('rotate-ccw') + '<span>Вернуть «пришёл»</span></button>';
      if (b.status === 'arrived' || b.status === 'no_show') acts += '<button class="btn mc-act" type="button" data-st="booked">' + icon('rotate-ccw') + '<span>Вернуть «записан»</span></button>';
    }
    var dlg = S.dialog({
      title: b.client_name || 'Клиент',
      body: '<div class="mc-bhead ' + esc(b.status) + '"><div class="w"><b>' + esc(hm(b.start)) + '–' + esc(hm(b.end)) + '</b><span>' + esc(dayLong(keyOf(new Date(b.start)))) + '</span></div>' +
        '<span class="chip ' + esc(b.status) + '">' + esc(STATUS[b.status] || b.status) + '</span></div>' +
        '<dl class="mc-dl"><dt>Услуга</dt><dd>' + esc(b.service || 'Без услуги') + ' · ' + durText(b.duration_min) + '</dd>' +
        '<dt>Цена</dt><dd>' + (b.price != null ? money(b.price) : '—') + '</dd>' +
        (b.source_title ? '<dt>Записал</dt><dd>' + esc(b.source_title) + '</dd>' : '') +
        (b.client_phone ? '<dt>Телефон</dt><dd><a href="tel:' + esc(b.client_phone) + '">' + esc(b.client_phone) + '</a></dd>' : '') + '</dl>' +
        (acts ? '<div class="mc-acts">' + acts + '</div>' : '') +
        '<div class="field" style="margin-top:14px"><label class="label" for="mc-note">Заметка о визите</label><textarea class="textarea" id="mc-note" rows="2" maxlength="2000" placeholder="Например: оттенок 105, любит короткую длину">' + esc(b.note || '') + '</textarea></div>',
      foot: (b.client_phone ? '<a class="btn" href="tel:' + esc(b.client_phone) + '">' + icon('phone') + '<span>Позвонить</span></a>' : '') +
        (b.status !== 'cancelled' && !past ? '<button class="btn" type="button" data-move>' + icon('calendar') + '<span>Перенести</span></button>' : '') +
        '<button class="btn btn-primary" type="button" data-savenote>Сохранить</button>'
    });
    function patch(body, msg) {
      return S.api(q('/api/booking/me/bookings/' + b.id), { method: 'PATCH', body: body }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        vibrate(); S.toast(msg || 'Сохранено'); dlg.close(); load();
      });
    }
    S.$$('[data-st]', dlg.el).forEach(function (btn) {
      btn.addEventListener('click', function () { patch({ status: btn.getAttribute('data-st') }, STATUS[btn.getAttribute('data-st')]); });
    });
    S.$('[data-savenote]', dlg.el).addEventListener('click', function () { patch({ note: S.$('#mc-note', dlg.el).value }, 'Заметка сохранена'); });
    var mv = S.$('[data-move]', dlg.el);
    if (mv) mv.addEventListener('click', function () { dlg.close(); newBooking({ move: b }); });
  }

  // ── записать к себе / перенести ──
  function newBooking(pre) {
    pre = pre || {};
    var move = pre.move || null;
    var services = (st.data && st.data.services) || [];
    var day = move ? keyOf(new Date(move.start)) : (st.day < today() ? today() : st.day);
    if (!move && day === today()) {   // рабочий день уже кончился — сразу завтрашний
      var iv = workIntervals(day), nowM = minOf(new Date().toISOString());
      if (!iv.length || nowM >= iv[iv.length - 1][1] - 30) day = addDays(day, 1);
    }
    var chosen = null;
    var dlg = S.dialog({
      title: move ? 'Перенести запись' : 'Записать клиента',
      body: (move ? '<p class="muted" style="margin:0 0 12px">' + esc((move.client_name || 'Клиент') + ' · ' + (move.service || '') + ' · сейчас ' + move.label) + '</p>' :
        '<div class="field"><label class="label" for="nb-name">Клиент</label><input class="input" id="nb-name" maxlength="200" placeholder="Имя" value="' + esc(pre.name || '') + '"></div>' +
        '<div class="field"><label class="label" for="nb-phone">Телефон</label><input class="input" id="nb-phone" type="tel" inputmode="tel" maxlength="40" placeholder="+996 700 123 456" value="' + esc(pre.phone || '') + '"></div>' +
        (services.length ? '<div class="field"><label class="label" for="nb-svc">Услуга</label><select class="select-native" id="nb-svc"><option value="">Без услуги (60 мин)</option>' +
          services.map(function (s) { return '<option value="' + esc(s.id) + '">' + esc(s.title) + ' · ' + s.duration_min + ' мин' + (s.price != null ? ' · ' + money(s.price) : '') + '</option>'; }).join('') + '</select></div>' : '')) +
        '<div class="field"><label class="label" for="nb-day">День</label><input class="input" id="nb-day" type="date" min="' + today() + '" value="' + esc(day) + '"></div>' +
        '<div class="field"><label class="label">Свободное время</label><div class="bk-slots" id="nb-slots"><span class="muted small">Загружаем…</span></div></div>' +
        (move ? '' : '<div class="field"><label class="label" for="nb-note">Комментарий</label><input class="input" id="nb-note" maxlength="500" placeholder="Пожелания клиента"></div>'),
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>' + (move ? 'Перенести' : 'Записать') + '</button>'
    });
    var $ = function (s) { return S.$(s, dlg.el); };
    function loadSlots() {
      var p = new URLSearchParams({ date: $('#nb-day').value || day, days: '1' });
      var svc = move ? move.service_id : ($('#nb-svc') && $('#nb-svc').value);
      if (svc) p.set('service_id', svc);
      $('#nb-slots').innerHTML = '<span class="muted small">Загружаем…</span>';
      chosen = null;
      S.api(q('/api/booking/me/slots?' + p.toString())).then(function (r) {
        var slots = (r.ok && r.data.days && r.data.days[0] && r.data.days[0].slots) || [];
        if (pre.time) { chosen = slots.filter(function (s) { return s.time === pre.time; })[0] || null; pre.time = null; }
        $('#nb-slots').innerHTML = slots.length ? slots.map(function (s) {
          return '<button type="button" class="bk-slot' + (chosen && chosen.start === s.start ? ' on' : '') + '" data-s="' + esc(s.start) + '">' + esc(s.time) + '</button>';
        }).join('') : '<span class="muted small" id="nb-none">' + (r.ok ? 'В этот день свободного времени нет — выберите другой.' : esc(S.apiError(r))) + '</span>';
        if (r.ok && !slots.length) {   // подсказка: ближайший день со свободным временем (до двух недель)
          var q2 = new URLSearchParams(p.toString()); q2.set('date', addDays($('#nb-day').value || day, 1)); q2.set('days', '14');
          S.api(q('/api/booking/me/slots?' + q2.toString())).then(function (r2) {
            var next = ((r2.ok && r2.data.days) || []).filter(function (x) { return x.slots.length; })[0];
            var none = $('#nb-none'); if (!next || !none) return;
            none.innerHTML = 'В этот день свободного времени нет. <button type="button" class="btn btn-sm" id="nb-next">Ближайшее: ' +
              esc(dayLong(next.date).split(',')[0].toLowerCase()) + ', ' + esc(next.slots[0].time) + '</button>';
            $('#nb-next').addEventListener('click', function () { $('#nb-day').value = next.date; pre.time = next.slots[0].time; loadSlots(); });
          });
        }
        S.$$('.bk-slot', dlg.el).forEach(function (btn) {
          btn.addEventListener('click', function () {
            chosen = slots.filter(function (s) { return s.start === btn.getAttribute('data-s'); })[0];
            S.$$('.bk-slot', dlg.el).forEach(function (x) { x.classList.toggle('on', x === btn); });
          });
        });
      });
    }
    ['#nb-day', '#nb-svc'].forEach(function (s) { var el = $(s); if (el) el.addEventListener('change', loadSlots); });
    loadSlots();
    $('[data-c]').addEventListener('click', dlg.close);
    $('[data-ok]').addEventListener('click', function () {
      if (!chosen) { S.toast('Выберите время', 'error'); return; }
      var btn = $('[data-ok]'), req;
      if (move) {
        req = S.api(q('/api/booking/me/bookings/' + move.id), { method: 'PATCH', body: { start: chosen.start } });
      } else {
        var name = $('#nb-name').value.trim();
        if (!name && !pre.contact_id) { S.toast('Укажите имя клиента', 'error'); $('#nb-name').focus(); return; }
        req = S.api(q('/api/booking/me/bookings'), { method: 'POST', body: {
          start: chosen.start, service_id: ($('#nb-svc') && $('#nb-svc').value) || null, client_name: name || null,
          client_phone: $('#nb-phone').value.trim() || null, contact_id: pre.contact_id || null, note: $('#nb-note').value.trim() || null } });
      }
      btn.disabled = true;
      req.then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        vibrate();
        S.toast(move ? 'Запись перенесена' : 'Клиент записан: ' + r.data.booking.label);
        dlg.close();
        st.day = keyOf(new Date(r.data.booking.start)); st.tab = 'day'; st.clients = null;
        load();
      });
    });
  }

  // ── новое (колокольчик) ──
  function openFeed() {
    var feed = (st.data && st.data.feed) || [];
    var seenAt = feedSeenAt();
    var dlg = S.dialog({
      title: 'Новое за неделю',
      body: feed.length ? '<div class="mc-feed">' + feed.map(function (f) {
        var fresh = new Date(f.at).getTime() > seenAt;
        return '<button type="button" class="mc-fi ' + esc(f.kind) + (fresh ? ' fresh' : '') + '" data-goto="' + esc(f.id) + '" data-day="' + esc(keyOf(new Date(f.start))) + '">' +
          '<span class="ic-w">' + icon(f.kind === 'cancelled' ? 'circle-x' : 'calendar-plus') + '</span>' +
          '<span class="b"><b>' + (f.kind === 'cancelled' ? 'Отменена запись' : 'Новая запись') + ' · ' + esc(f.client_name || 'клиент') + '</b>' +
          '<span>' + esc(f.label) + (f.service ? ' · ' + esc(f.service) : '') + (f.source_title && f.kind === 'new' ? ' · ' + esc(f.source_title) : '') + '</span></span></button>';
      }).join('') + '</div>' : '<div class="mc-empty sm">' + icon('bell') + '<p>Пока тихо. Здесь появятся новые записи к вам и отмены.</p></div>'
    });
    try { localStorage.setItem('mc_feed_seen_' + master().id, String(Date.now())); } catch (e) { /* ignore */ }
    drawActions();
    S.$$('[data-goto]', dlg.el).forEach(function (b) {
      b.addEventListener('click', function () {
        dlg.close();
        var k = b.getAttribute('data-day'), id = b.getAttribute('data-goto');
        st.day = k; st.tab = 'day';
        var go = function () { render(); openBooking(id); };
        if (inWindow(k)) go(); else load().then(function () { openBooking(id); });
      });
    });
  }

  // ── клики по странице ──
  function onClick(e) {
    var t = e.target;
    var tb = t.closest('.mc-tabs [data-tab]');
    if (tb) { setTab(tb.getAttribute('data-tab')); return; }
    var dp = t.closest('[data-day]');
    if (dp && root.contains(dp)) { goDay(dp.getAttribute('data-day')); return; }
    var sh = t.closest('[data-shift]');
    if (sh) { goDay(addDays(st.day, +sh.getAttribute('data-shift'))); return; }
    var bk = t.closest('[data-bk]');
    if (bk) { openBooking(bk.getAttribute('data-bk')); return; }
    var gap = t.closest('[data-gap]');
    if (gap) { newBooking({ time: gap.getAttribute('data-gap') }); return; }
    if (t.closest('[data-newday]')) { newBooking({}); return; }
    var cl = t.closest('[data-cl]');
    if (cl) { var c = filteredClients()[+cl.getAttribute('data-cl')]; if (c) openClient(c); }
  }
  function goDay(k) {
    st.day = k;
    if (inWindow(k)) { drawPane(); return; }
    load();
  }

  function init() {
    root = document.getElementById('mc');
    esc = S.esc; icon = S.icon;
    S.initMobileMenu();
    var qs = new URLSearchParams(location.search);
    st.rid = qs.get('resource_id');
    st.day = qs.get('date') || today();
    try { var t = sessionStorage.getItem('mc_tab'); if (t && TABS.some(function (x) { return x[0] === t; })) st.tab = t; } catch (e) { /* ignore */ }
    root.addEventListener('click', onClick);
    root.addEventListener('keydown', function (e) { if (e.key === 'Enter' && e.target.matches('[data-bk]')) openBooking(e.target.getAttribute('data-bk')); });
    load();
    // Изменения журнала (администратор перенёс, бот записал) приходят сразу — поток /api/booking/stream
    var pending = false;
    function refresh() { if (document.hidden || document.querySelector('.vf-backdrop')) { pending = true; return; } pending = false; load(); }
    var live = S.live ? S.live('/api/booking/stream', function (ev) { if (ev === 'changed' || ev === 'resync') refresh(); }) : null;
    var lastPoll = Date.now();
    setInterval(function () {
      if (pending && !document.hidden && !document.querySelector('.vf-backdrop')) { refresh(); return; }
      if (Date.now() - lastPoll < (live && live.on() ? 300000 : 60000)) return;
      lastPoll = Date.now();
      if (!document.hidden && !document.querySelector('.vf-backdrop')) load();
    }, 2000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden && st.data && !document.querySelector('.vf-backdrop')) load(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
