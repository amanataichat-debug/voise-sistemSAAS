/* /static/js/dashboard-home.js — главная кабинета Voksy AI.
 *
 * Два запроса параллельно:
 *   GET /api/dashboard/summary — приветствие с тарифом, кошелёк, последние звонки, подключения, воронка,
 *                                чек-лист запуска и карточка Profi;
 *   GET /api/analytics/report?period=7d|30d|90d&sections=… — интерактивные виджеты (ветка 1811-niche-admin-speed):
 *                                плитки с изменением к прошлому периоду и мини-графиком; клик по плитке
 *                                переключает график ниже, клик по столбцу открывает полный отчёт «Аналитики»
 *                                на этом дне / неделе; «Главное» — выводы словами.
 * Ниша с онлайн-записью: справа «Записи на сегодня» (/api/booking/overview) вместо воронки.
 * Ответы кэшируются в sessionStorage: повторный заход рисуется сразу, свежие данные — следом.
 * Модалки оплаты и блок подписки остаются в коде dashboard.html. Графики — общий /static/js/vchart.js.
 */
(function () {
  'use strict';

  var CACHE_KEY = 'vf_dash_summary';
  var AN_KEY = 'vf_dash_an:';
  var C_IN = '#2a5ce8';   // входящие (проверено validate_palette: CVD ΔE 22)
  var C_OUT = '#0d9488';  // исходящие
  var V = window.VChart;
  var data = null;        // /api/dashboard/summary
  var an = null;          // /api/analytics/report за выбранный период
  var period = lsGet('dh_period') || '7d';
  var metric = lsGet('dh_metric') || '';
  var chartKind = 'calls';  // запасной график из summary, пока отчёт не пришёл
  var anSeq = 0;
  var today = null;       // записи на сегодня (ниши с онлайн-записью)

  function $(id) { return document.getElementById(id); }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* приватный режим */ } }
  function token() { try { return localStorage.getItem('auth_token'); } catch (e) { return null; } }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function ic(name) { return '<svg class="ic" aria-hidden="true"><use href="/static/icons/ui.svg#i-' + name + '"></use></svg>'; }
  function num(v) { return (Number(v) || 0).toLocaleString('ru-RU'); }
  function som(v) { return (Number(v) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' сом'; }
  function plural(n, a, b, c) {
    n = Math.abs(n) % 100; var n1 = n % 10;
    if (n > 10 && n < 20) return c; if (n1 > 1 && n1 < 5) return b; if (n1 === 1) return a; return c;
  }
  function dur(sec) {
    sec = Math.round(sec || 0);
    if (sec < 60) return sec + ' с';
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ' мин' + (s ? ' ' + s + ' с' : '');
  }
  function when(iso) {
    if (!iso) return '';
    var d = new Date(iso), now = new Date();
    var t = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === now.toDateString()) return 'сегодня, ' + t;
    var y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return 'вчера, ' + t;
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) + ', ' + t;
  }
  function fmtPhone(p) {
    var d = String(p || '').replace(/\D/g, '');
    if (d.length === 12 && d.indexOf('996') === 0) return '+996 ' + d.substr(3, 3) + ' ' + d.substr(6, 3) + ' ' + d.substr(9);
    if (d.length === 10 && d[0] === '0') return d.substr(0, 4) + ' ' + d.substr(4, 3) + ' ' + d.substr(7);
    return p || '—';
  }
  function business() { try { return JSON.parse(localStorage.getItem('vf_business') || 'null') || {}; } catch (e) { return {}; } }
  function hasBooking() { return !!((business().modules || {}).booking); }
  var isStart = function () { return data && (data.agent_mode === 'reactive' || data.agent_mode === 'full'); };
  var PERIOD_TITLE = { '7d': 'за 7 дней', '30d': 'за 30 дней', '90d': 'за 3 месяца' };

  // ------------------------------------------------------------------ hero
  function renderHero() {
    var now = new Date();
    var ds = now.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
    $('dh-date').textContent = ds.charAt(0).toUpperCase() + ds.slice(1);
    var p = data.plan || {};
    var el = $('dh-plan');
    if (p.admin) { el.innerHTML = '<span class="dot"></span>Администратор'; return; }
    if (!p.code) { el.innerHTML = '<span class="dot warn"></span>Тариф не выбран'; return; }
    var left = p.days_left;
    var dot = left == null ? '' : (left <= 0 ? ' bad' : (left <= 3 ? ' warn' : ''));
    var tail = '';
    if (left != null) {
      if (p.is_trial && left < 1 && p.hours_left > 0) tail = 'осталось ' + p.hours_left + ' ' + plural(p.hours_left, 'час', 'часа', 'часов');
      else tail = left > 0 ? 'осталось ' + left + ' ' + plural(left, 'день', 'дня', 'дней') : 'срок истёк';
    }
    el.innerHTML = '<span class="dot' + dot + '"></span>' + esc(p.is_trial ? 'Пробный период · уровень Старт' : 'Тариф ' + p.name) +
      (tail ? ' <span class="dh-muted">· ' + tail + '</span>' : '');
    if (p.is_trial) {
      $('dh-hero-sub').textContent = 'Пробный период открывает возможности тарифа Старт: ассистенты, чат-бот в мессенджерах, CRM и входящие звонки. Минуты разговора оплачиваются из кошелька.';
    }
  }

  // ------------------------------------------------------------------ KPI
  function kpi(o) {
    var tag = o.href ? 'a' : 'div';
    return '<' + tag + ' class="dh-kpi"' + (o.href ? ' href="' + o.href + '"' : '') + '>' +
      '<div class="dh-kpi-top"><span class="dh-kpi-label">' + o.label + '</span><span class="dh-kpi-ic ' + (o.tone || '') + '">' + ic(o.icon) + '</span></div>' +
      '<div class="dh-kpi-value">' + o.value + '</div>' +
      '<div class="dh-kpi-foot">' + (o.foot || '') + '</div></' + tag + '>';
  }
  function walletTile() {
    var w = data ? (data.wallet || {}) : {};
    var low = !w.free && (w.balance || 0) <= 0;
    return kpi({
      label: 'Кошелёк', icon: 'wallet', tone: 'amber',
      value: w.free ? '∞' : num(Math.floor(w.balance || 0)) + '<small>сом</small>',
      foot: w.free ? 'без списаний' : (low ? '<span class="neg">Пополните, чтобы звонки работали</span>'
        : '≈ <b>' + num(w.minutes_left) + '</b> мин разговора · ' + (w.som_per_minute || 0).toString().replace('.', ',') + ' сом/мин')
    });
  }
  var KPI_ICON = { inbound: 'messages-square', new_clients: 'users', calls: 'phone-call', reply: 'clock', visits: 'calendar',
    revenue: 'banknote', won: 'circle-check', pipeline: 'trending-up', dialogs: 'audio-lines', spent: 'coins' };
  var KPI_TONE = { new_clients: 'green', reply: 'teal', visits: 'teal', revenue: 'green', won: 'green', dialogs: 'teal' };
  // Плитки из отчёта аналитики: значение, изменение к прошлому периоду, мини-график; клик — график ниже
  function renderKpis() {
    if (an && an.kpis && an.kpis.length && V) {
      var cur = currentMetric();
      var list = an.kpis.filter(function (k) { return k.id !== 'spent'; }).slice(0, 3);
      $('dh-kpis').innerHTML = list.map(function (k, i) {
        var m = V.KPI_METRIC[k.id];
        var on = cur && m === cur.id;
        return '<button type="button" class="dh-kpi dh-kpi-btn' + (on ? ' on' : '') + '" data-kpi="' + i + '" aria-pressed="' + (on ? 'true' : 'false') + '"' +
          ' title="Показать на графике">' +
          '<div class="dh-kpi-top"><span class="dh-kpi-label">' + esc(k.label) + ' <span class="dh-kpi-per">' + esc(PERIOD_TITLE[period] || '') + '</span></span>' +
          '<span class="dh-kpi-ic ' + (KPI_TONE[k.id] || '') + '">' + ic(KPI_ICON[k.id] || 'activity') + '</span></div>' +
          '<div class="dh-kpi-value">' + V.valHtml(k.value, k.fmt) + '</div>' +
          '<div class="dh-kpi-foot dh-kpi-sub">' + esc(k.sub || '') + '</div>' +
          '<div class="dh-kpi-trend">' + V.deltaHtml(k) + V.sparkSvg(k.spark) + '</div></button>';
      }).join('') + walletTile();
      Array.prototype.forEach.call($('dh-kpis').querySelectorAll('[data-kpi]'), function (b) {
        b.addEventListener('click', function () {
          var k = list[+b.getAttribute('data-kpi')];
          var m = V.KPI_METRIC[k.id];
          if (!m || !V.metricsAvail(an).some(function (x) { return x.id === m; })) {
            location.href = '/static/analytics.html?period=' + period + '#' + (k.section || 'overview');
            return;
          }
          metric = m; lsSet('dh_metric', m);
          renderKpis(); renderChart();
        });
      });
      return;
    }
    if (!data) return;
    // запасные плитки из summary (первый заход, пока отчёт аналитики не пришёл)
    var c = data.calls || {}, d = data.dialogs || {}, crm = data.crm || {}, m2 = data.messages || {};
    var tiles = [];
    tiles.push(kpi({
      label: 'Звонки сегодня', icon: 'phone-call', value: num(c.today),
      foot: '<b>' + num(c.week) + '</b> за 7 дней · <b>' + num(c.month) + '</b> за 30', href: '/static/telephony.html'
    }));
    tiles.push(kpi({
      label: 'Минуты разговора', icon: 'clock', tone: 'teal', value: num(c.minutes_month) + '<small>мин</small>',
      foot: c.answered_month ? 'за 30 дней · в среднем <b>' + dur(c.avg_sec) + '</b>' : 'за 30 дней'
    }));
    if (isStart() && crm.has_bot) {
      tiles.push(kpi({ label: 'Клиенты в CRM', icon: 'users', tone: 'green', value: num(crm.contacts_total),
        foot: '<b>+' + num(crm.contacts_new_week) + '</b> новых за неделю', href: '/static/' + (data.agent_mode === 'full' ? 'crm.html' : 'start-crm.html') }));
    } else if (isStart()) {
      tiles.push(kpi({ label: 'Сообщения за неделю', icon: 'message-circle', tone: 'green', value: num((m2.week_in || 0) + (m2.week_out || 0)),
        foot: 'чат-бот ещё не создан', href: '/static/start-bot.html' }));
    } else {
      tiles.push(kpi({ label: 'Диалоги за неделю', icon: 'messages-square', tone: 'green', value: num(d.week),
        foot: '<b>' + num(d.today) + '</b> сегодня', href: '/static/conversations.html' }));
    }
    $('dh-kpis').innerHTML = tiles.join('') + walletTile();
  }

  // ------------------------------------------------------------------ «Главное»
  function renderInsights() {
    var box = $('dh-insights');
    var list = (an && an.insights) || [];
    if (!list.length) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = '<div class="dh-card-head"><h3 class="dh-card-title">Главное ' + esc(PERIOD_TITLE[period] || '') + '</h3>' +
      '<a class="dh-link" href="/static/analytics.html?period=' + period + '">Вся аналитика →</a></div>' +
      '<div class="dh-ins-list">' + list.slice(0, 3).map(function (x) {
        return '<div class="dh-ins ' + esc(x.tone) + '"><span class="dh-ins-ic">' + ic(x.icon) + '</span><span>' + esc(x.text) + '</span></div>';
      }).join('') + '</div>';
  }

  // ------------------------------------------------------------------ график
  function currentMetric() {
    if (!an || !V) return null;
    // Салон / клиника: по умолчанию график записей, магазин — заказов (обращения — по выбору)
    if (!metric && hasBooking() && V.metricsAvail(an).some(function (x) { return x.id === 'visits'; })) return V.metricById(an, 'visits');
    if (!metric && hasOrders() && V.metricsAvail(an).some(function (x) { return x.id === 'orders'; })) return V.metricById(an, 'orders');
    return V.metricById(an, metric);
  }
  function renderChart() {
    if (an && V) return renderChartAn();
    renderChartLegacy();
  }
  function renderChartAn() {
    var m = currentMetric();
    var tabs = $('dh-chart-tabs');
    var avail = V.metricsAvail(an);
    tabs.hidden = avail.length < 2;
    tabs.innerHTML = avail.map(function (x) {
      return '<button type="button" role="tab" data-m="' + x.id + '" class="' + (x.id === m.id ? 'active' : '') + '" aria-selected="' + (x.id === m.id) + '">' +
        esc(x.title) + '<b>' + num(V.metricTotal(an, x)) + '</b></button>';
    }).join('');
    $('dh-chart-title').textContent = m.title + ' ' + (PERIOD_TITLE[period] || '');
    $('dh-chart-sub').textContent = m.sub + ' · время Бишкека';
    var parts = V.metricParts(an, m);
    var prev = m.prev ? (an.prev || {})[m.prev] || null : null;
    $('dh-legend').innerHTML = V.legendHtml(parts, prev);
    var g = an.period.granularity;
    $('dh-chart-hint').textContent = g === 'hour' ? '' : 'Нажмите на столбец — откроется подробный отчёт ' + (g === 'day' ? 'по часам' : 'по дням');
    $('dh-full').href = '/static/analytics.html?period=' + period + '&m=' + m.id;
    V.render($('dh-chart'), {
      buckets: an.buckets, parts: parts, prev: prev, height: window.innerWidth < 720 ? 200 : 270,
      drillText: 'Нажмите — подробный отчёт',
      emptyText: m.id === 'calls' ? 'Звонков за период не было. Подключите номер +996 в разделе «Телефония».'
        : 'За период данных нет.',
      drill: function (i) {
        var b = an.buckets[i];
        location.href = '/static/analytics.html?period=custom&from=' + b.from + '&to=' + b.to + '&g=' + (b.from === b.to ? 'hour' : 'day') + '&m=' + m.id;
      }
    });
  }
  // Запасной график из summary: звонки / сообщения за 14 дней (пока отчёт аналитики не пришёл или без vchart.js)
  function renderChartLegacy() {
    if (!data) return;
    var isMsg = chartKind === 'messages';
    var rows = (isMsg ? (data.messages || {}).by_day : (data.calls || {}).by_day) || [];
    var names = isMsg ? ['От клиентов', 'Ответы бота и ваши'] : ['Входящие', 'Исходящие'];
    $('dh-chart-title').textContent = 'Активность за 14 дней';
    $('dh-chart-sub').textContent = (isMsg ? 'Сообщения WhatsApp, Telegram и Instagram' : 'Звонки') + ' по дням, время Бишкека';
    $('dh-legend').innerHTML = '<span><i style="background:' + C_IN + '"></i>' + names[0] + '</span><span><i style="background:' + C_OUT + '"></i>' + names[1] + '</span>';
    if (!V) return;
    var buckets = rows.map(function (r) {
      var d = new Date(r.date + 'T00:00:00');
      return { label: d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }).replace('.', ''),
        title: d.toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' }) };
    });
    V.render($('dh-chart'), {
      buckets: buckets, height: 220,
      parts: [{ label: names[0], color: C_IN, values: rows.map(function (r) { return r.inbound; }) },
        { label: names[1], color: C_OUT, values: rows.map(function (r) { return r.outbound; }) }],
      emptyText: isMsg ? 'За 14 дней сообщений не было.' : 'За 14 дней звонков не было.'
    });
  }

  // ------------------------------------------------------------------ правая колонка: записи, воронка или ассистенты
  function renderSide() {
    var crm = data.crm || {};
    var body = $('dh-side-body');
    if (hasBooking()) { renderToday(); return; }
    if (isStart() && crm.has_bot) {
      $('dh-side-title').textContent = 'Воронка клиентов';
      var max = Math.max.apply(null, (crm.stages || []).map(function (s) { return s.count; }).concat([1]));
      var crmUrl = data.agent_mode === 'full' ? '/static/crm.html' : '/static/start-crm.html';
      body.innerHTML = '<div class="dh-funnel">' + (crm.stages || []).map(function (s) {
        return '<div><div class="dh-stage-row"><span>' + esc(s.label) + '</span><b>' + num(s.count) + '</b></div>' +
          '<div class="dh-bar"><span style="width:' + (s.count ? Math.max(3, s.count / max * 100) : 0) + '%;background:' + esc(s.color || C_IN) + '"></span></div></div>';
      }).join('') + '</div>' +
        '<div class="dh-funnel-foot"><div>В работе на сумму<b>' + som(crm.pipeline_amount) + '</b></div>' +
        '<div style="text-align:right">Бот на паузе<b>' + num(crm.paused) + '</b></div></div>' +
        '<div style="margin-top:12px"><a class="dh-link" href="' + crmUrl + '">Открыть CRM →</a></div>';
      return;
    }
    if (isStart()) {
      $('dh-side-title').textContent = 'Чат-бот';
      body.innerHTML = '<div class="dh-empty">Бот отвечает клиентам в WhatsApp, Telegram и Instagram, ведёт карточки в CRM и подсказывает, кому ответить. Создание займёт пару минут.</div>' +
        '<a class="dh-btn dh-btn-white" style="display:inline-flex;align-items:center;margin-top:8px;background:var(--vf-accent);color:#fff;text-decoration:none" href="/static/start-bot.html">Создать чат-бота</a>';
      return;
    }
    $('dh-side-title').textContent = 'Диалоги';
    var d = data.dialogs || {};
    body.innerHTML = '<div class="dh-funnel">' +
      '<div><div class="dh-stage-row"><span>Сегодня</span><b>' + num(d.today) + '</b></div></div>' +
      '<div><div class="dh-stage-row"><span>За 7 дней</span><b>' + num(d.week) + '</b></div></div></div>' +
      '<div class="dh-empty" style="margin-top:10px">Переписка, CRM и аналитика клиентов открываются на тарифе Старт.</div>' +
      '<a class="dh-link" href="/static/conversations.html">Открыть диалоги →</a>';
  }

  // Записи на сегодня (салоны, клиники, СТО): время, клиент, услуга, мастер, статус
  var STATUS_CLS = { booked: 'wait', confirmed: 'ok', arrived: 'ok', no_show: 'miss', cancelled: 'miss' };
  function renderToday() {
    $('dh-side-title').textContent = 'Записи на сегодня';
    var body = $('dh-side-body');
    if (!today) { body.innerHTML = '<span class="dh-skel"></span>'; if (!todayLoading) loadToday(); return; }
    var rows = (today.bookings || []).filter(function (b) { return b.status !== 'cancelled'; })
      .sort(function (a, b) { return a.start < b.start ? -1 : 1; });
    var link = '<div class="dh-today-foot"><a class="dh-link" href="/static/booking.html">Открыть календарь →</a></div>';
    if (!rows.length) {
      body.innerHTML = '<div class="dh-empty">На сегодня записей нет. Отправьте клиентам ссылку для записи — они выберут время сами.</div>' +
        (today.page && today.page.url ? '<button type="button" class="dh-copy" id="dh-copy-link">' + ic('link-2') + 'Скопировать ссылку для записи</button>' : '') + link;
    } else {
      var now = Date.now();
      body.innerHTML = '<div class="dh-today">' + rows.slice(0, 7).map(function (b) {
        var t = new Date(b.start), past = new Date(b.end).getTime() < now;
        return '<a class="dh-today-row' + (past ? ' past' : '') + '" href="/static/booking.html#' + esc(b.id) + '">' +
          '<span class="dh-today-time">' + t.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bishkek' }) + '</span>' +
          '<span class="dh-today-main"><b>' + esc(b.client_name || 'Клиент') + '</b><span>' + esc([b.service, b.resource].filter(Boolean).join(' · ')) + '</span></span>' +
          '<span class="dh-badge ' + (STATUS_CLS[b.status] || 'wait') + '">' + esc(b.status_title || b.status) + '</span></a>';
      }).join('') + '</div>' + (rows.length > 7 ? '<div class="dh-empty" style="padding-top:4px">и ещё ' + (rows.length - 7) + '</div>' : '') + link;
    }
    var copy = $('dh-copy-link');
    if (copy) copy.addEventListener('click', function () {
      var url = today.page.url;
      (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(function () {
        copy.textContent = 'Ссылка скопирована';
      }).catch(function () { window.prompt('Ссылка для записи', url); });
    });
  }
  var todayLoading = false;
  function loadToday() {
    if (!hasBooking() || todayLoading) return;
    todayLoading = true;
    fetch('/api/booking/overview?days=1', { headers: { Authorization: 'Bearer ' + token() } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        todayLoading = false;
        today = d || { bookings: [] };
        if (data) renderSide();
      })
      .catch(function () { todayLoading = false; today = { bookings: [] }; if (data) renderSide(); });
  }

  // ------------------------------------------------------------------ деньги: сегодня и этот месяц
  // Салоны, клиники, магазины, доставка (19.11.2026): на главной сразу видно, сколько заработали
  // сегодня и с начала месяца, прибыль (если внесены расходы), что популярно и как работают мастера.
  function hasOrders() { return !!((business().modules || {}).orders); }
  function hasMoney() { return hasBooking() || hasOrders(); }
  var money = { today: null, month: null };
  function moneyDelta(cur, prev) {
    if (prev == null || (!cur && !prev)) return '';
    var diff = cur - prev;
    if (!diff) return '<span class="dh-md same">как раньше</span>';
    var up = diff > 0;
    var txt = prev > 0 ? (up ? '+' : '') + Math.round(diff * 100 / prev) + '%' : (up ? '+' : '−') + som(Math.abs(diff));
    return '<span class="dh-md ' + (up ? 'up' : 'down') + '">' + ic(up ? 'trending-up' : 'trending-down') + txt + '</span>';
  }
  function renderMoney() {
    var box = $('dh-money'), biz = $('dh-biz');
    if (!hasMoney()) { box.hidden = true; biz.hidden = true; document.body.classList.remove('dh-has-money'); return; }
    document.body.classList.add('dh-has-money');
    box.hidden = false;
    var t = money.today, m = money.month;
    if (!t && !m) {
      box.innerHTML = '<div class="dh-mcard"><span class="dh-skel"></span><span class="dh-skel" style="width:40%"></span></div>' +
        '<div class="dh-mcard"><span class="dh-skel"></span><span class="dh-skel" style="width:40%"></span></div>';
      return;
    }
    function rev(rep) {
      if (!rep) return { v: 0, p: 0 };
      var b = rep.bookings || {}, o = rep.orders || {};
      return { v: (b.revenue || 0) + (o.revenue || 0), p: (b.prev_revenue || 0) + (o.prev_revenue || 0) };
    }
    var tr = rev(t), mr = rev(m);
    var tb = (t && t.bookings) || null, to = (t && t.orders) || null;
    var todayLine = [];
    if (tb) {
      todayLine.push('<b>' + num(tb.arrived) + '</b> пришли');
      if (tb.upcoming) todayLine.push('<b>' + num(tb.upcoming) + '</b> ещё впереди');
      if (tb.expected_revenue) todayLine.push('ожидается ещё ' + som(tb.expected_revenue));
    }
    if (to) todayLine.push('<b>' + num(to.total) + '</b> ' + plural(to.total, 'заказ', 'заказа', 'заказов'));
    var fin = m && m.finance;
    var monthName = new Date().toLocaleDateString('ru-RU', { month: 'long' });
    var profitHtml = fin && (fin.expenses_count || fin.payroll_total)
      ? '<div class="dh-mrow"><span>Прибыль</span><b class="' + (fin.profit < 0 ? 'neg' : '') + '">' + (fin.profit < 0 ? '−' : '') + som(Math.abs(fin.profit)) + '</b></div>' +
        '<div class="dh-mrow"><span>Расходы</span><b>' + som(fin.expenses) + '</b></div>'
      : '<div class="dh-mhint">Внесите аренду и зарплаты — покажем прибыль</div>';
    box.innerHTML =
      '<a class="dh-mcard today" href="/static/analytics.html?period=today">' +
      '<div class="dh-mtop"><span>' + ic('sun') + 'Сегодня</span>' + (tb && tb.total ? '<span class="dh-md same">' + num(tb.total) + ' ' + plural(tb.total, 'запись', 'записи', 'записей') + '</span>' : '') + '</div>' +
      '<div class="dh-mval">' + som(tr.v) + '</div>' +
      '<div class="dh-msub">' + (todayLine.join(' · ') || 'выручка за сегодня') + '</div></a>' +
      '<a class="dh-mcard month" href="/static/finance.html?period=mtd">' +
      '<div class="dh-mtop"><span>' + ic('calendar') + monthName.charAt(0).toUpperCase() + monthName.slice(1) + '</span>' + moneyDelta(mr.v, m ? mr.p : null) + '</div>' +
      '<div class="dh-mval">' + som(mr.v) + '</div>' +
      '<div class="dh-msub">выручка с 1 числа' + (m && m.bookings && m.bookings.avg_check ? ' · средний чек ' + som(m.bookings.avg_check) : (m && m.orders && m.orders.avg_check ? ' · средний чек ' + som(m.orders.avg_check) : '')) + '</div>' +
      profitHtml + '</a>';
    renderBiz();
  }
  function renderBiz() {
    var m = money.month, biz = $('dh-biz');
    if (!m) return;
    var b = m.bookings || {}, o = m.orders || {};
    var top = (b.services && b.services.length) ? b.services.map(function (x) { return { t: x.title, n: x.count, a: x.revenue }; })
      : (o.top || []).map(function (x) { return { t: x.title, n: x.qty, a: x.amount }; });
    var unit = (b.services && b.services.length) ? ['запись', 'записи', 'записей'] : ['шт.', 'шт.', 'шт.'];
    var title = (b.services && b.services.length) ? 'Популярные услуги' : 'Что покупают';
    var masters = b.masters || [];
    if (!top.length && !masters.length) { biz.hidden = true; return; }
    biz.hidden = false;
    var max = Math.max.apply(null, top.map(function (x) { return x.n; }).concat([1]));
    $('dh-top-card').hidden = !top.length;
    $('dh-top-card').innerHTML = '<div class="dh-card-head"><h3 class="dh-card-title">' + title + ' <span class="dh-kpi-per">в этом месяце</span></h3>' +
      '<a class="dh-link" href="/static/analytics.html?period=mtd#' + (b.services && b.services.length ? 'bookings' : 'orders') + '">Отчёт →</a></div>' +
      '<div class="dh-top">' + top.slice(0, 5).map(function (x, i) {
        return '<div class="dh-top-row"><span class="dh-top-n">' + (i + 1) + '</span><div class="dh-top-m"><div class="dh-top-t"><span>' + esc(x.t) + '</span>' +
          '<b>' + som(x.a) + '</b></div><div class="dh-bar"><span style="width:' + Math.max(4, x.n / max * 100) + '%;background:' + C_IN + '"></span></div>' +
          '<div class="dh-top-s">' + num(x.n) + ' ' + plural(x.n, unit[0], unit[1], unit[2]) + '</div></div></div>';
      }).join('') + '</div>';
    $('dh-team-card').hidden = !masters.length;
    $('dh-team-card').innerHTML = '<div class="dh-card-head"><h3 class="dh-card-title">Мастера <span class="dh-kpi-per">в этом месяце</span></h3>' +
      '<a class="dh-link" href="/static/finance.html?period=mtd">Зарплаты →</a></div>' +
      '<div class="dh-list">' + masters.slice(0, 6).map(function (x) {
        return '<div class="dh-item"><span class="dh-ava" style="background:' + esc(x.color || C_IN) + '">' + esc(String(x.name || '?').charAt(0).toUpperCase()) + '</span>' +
          '<div class="dh-item-main"><div class="dh-item-title">' + esc(x.name) + '</div><div class="dh-item-sub">' + num(x.count) + ' ' + plural(x.count, 'запись', 'записи', 'записей') +
          (x.load_pct != null ? ' · загрузка ' + x.load_pct + '%' : '') + '</div></div><span class="dh-go dh-strong">' + som(x.revenue || 0) + '</span></div>';
      }).join('') + '</div>';
  }
  function loadMoney() {
    var t = token();
    if (!t || !hasMoney()) { renderMoney(); return; }
    var sec = 'kpis,bookings,orders,finance';
    [['today', 'today'], ['month', 'mtd']].forEach(function (p) {
      var key = 'vf_dash_money:' + p[1];
      try { var c = JSON.parse(sessionStorage.getItem(key) || 'null'); if (c) money[p[0]] = c; } catch (e) { /* ignore */ }
      fetch('/api/analytics/report?period=' + p[1] + '&sections=' + sec, { headers: { Authorization: 'Bearer ' + t } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) return;
          try { sessionStorage.setItem(key, JSON.stringify(d)); } catch (e) { /* ignore */ }
          money[p[0]] = d; renderMoney();
        }).catch(function () { /* сеть */ });
    });
    renderMoney();
  }

  // ------------------------------------------------------------------ последние звонки
  function renderRecent() {
    var rows = data.recent_calls || [];
    var box = $('dh-recent');
    if (!rows.length) {
      box.innerHTML = '<div class="dh-empty">Звонков пока нет. Когда ассистент примет или сделает первый звонок, он появится здесь вместе с записью разговора.</div>';
      return;
    }
    box.innerHTML = '<table class="dh-table"><tbody>' + rows.map(function (r) {
      var miss = !r.answered;
      var cls = miss ? 'miss' : (r.direction === 'outbound' ? 'out' : '');
      var icon = miss ? 'phone-off' : (r.direction === 'outbound' ? 'phone-outgoing' : 'phone-incoming');
      var badge = miss
        ? (/queued|dialing|ringing/.test(r.status || '') ? '<span class="dh-badge wait">идёт</span>' : '<span class="dh-badge miss">без ответа</span>')
        : '<span class="dh-badge ok">' + dur(r.duration_sec) + '</span>';
      return '<tr><td style="width:40px"><span class="dh-dir ' + cls + '">' + ic(icon) + '</span></td>' +
        '<td class="num">' + esc(fmtPhone(r.number)) + '</td>' +
        '<td class="hide-sm">' + (r.direction === 'outbound' ? 'Исходящий' : 'Входящий') + (r.has_record ? ' · запись' : '') + '</td>' +
        '<td>' + badge + '</td><td class="r">' + esc(when(r.at)) + '</td></tr>';
    }).join('') + '</tbody></table>';
  }

  // ------------------------------------------------------------------ подключения
  function item(o) {
    var tag = o.href ? 'a' : 'div';
    return '<' + tag + ' class="dh-item' + (o.cls ? ' ' + o.cls : '') + '"' + (o.href ? ' href="' + o.href + '"' : '') + '>' +
      (o.check != null ? '<span class="dh-check' + (o.check ? ' done' : '') + '">' + (o.check ? ic('check') : '') + '</span>'
        : '<span class="dh-item-ic' + (o.on ? ' on' : '') + '">' + ic(o.icon) + '</span>') +
      '<div class="dh-item-main"><div class="dh-item-title">' + o.title + '</div>' + (o.sub ? '<div class="dh-item-sub">' + o.sub + '</div>' : '') + '</div>' +
      (o.go ? '<span class="dh-go">' + o.go + '</span>' : '') + '</' + tag + '>';
  }
  function renderConnect() {
    var nums = data.numbers || [];
    var ch = (data.crm || {}).channels || {};
    var html = [];
    if (nums.length) {
      nums.slice(0, 3).forEach(function (n) {
        html.push(item({ icon: 'phone', on: n.bound && n.is_active, title: esc(fmtPhone(n.phone)),
          sub: n.bound ? (n.label ? esc(n.label) + ' · ' : '') + 'принимает звонки' : 'не привязан к ассистенту', href: '/static/telephony.html' }));
      });
    } else {
      html.push(item({ icon: 'phone', title: 'Номер +996', sub: 'не подключён', go: 'Подключить', href: '/static/telephony.html' }));
    }
    if (isStart()) {
      var bot = '/static/' + (data.agent_mode === 'full' ? 'agent.html' : 'start-bot.html#channels');
      [['whatsapp', 'message-circle', 'WhatsApp'], ['telegram', 'send', 'Telegram'], ['instagram', 'instagram', 'Instagram']].forEach(function (c) {
        var on = !!ch[c[0]];
        html.push(item({ icon: c[1], on: on, title: c[2], sub: on ? 'подключён, бот отвечает' : 'не подключён', go: on ? '' : 'Подключить', href: bot }));
      });
    }
    $('dh-connect').innerHTML = '<div class="dh-list">' + html.join('') + '</div>';
  }

  // ------------------------------------------------------------------ чек-лист запуска
  function renderSetup() {
    var a = data.assistants || {}, w = data.wallet || {}, crm = data.crm || {}, ch = crm.channels || {}, c = data.calls || {};
    var steps = [
      { done: (a.total || 0) > 0, title: 'Создайте голосового ассистента', href: '/static/voice-assistants.html' },
      { done: (data.numbers || []).some(function (n) { return n.bound; }), title: 'Подключите номер +996', href: '/static/telephony.html' },
      { done: w.free || (w.balance || 0) > 0, title: 'Пополните кошелёк минут', href: '#topup' },
    ];
    if (isStart()) {
      steps.push({ done: !!crm.has_bot, title: 'Создайте чат-бота', href: '/static/start-bot.html' });
      steps.push({ done: !!(ch.whatsapp || ch.telegram || ch.instagram), title: 'Подключите WhatsApp, Telegram или Instagram', href: '/static/start-bot.html#channels' });
    }
    steps.push({ done: (c.month || 0) > 0, title: 'Проведите первый звонок', href: '/static/voice-assistants.html' });
    var done = steps.filter(function (s) { return s.done; }).length;
    $('dh-setup-progress').textContent = done + ' из ' + steps.length;
    var card = $('dh-setup-card');
    card.hidden = done === steps.length;  // всё настроено — карточка не занимает место
    if (card.hidden) return;
    $('dh-setup').innerHTML = '<div class="dh-progress"><span style="width:' + Math.round(done / steps.length * 100) + '%"></span></div>' +
      '<div class="dh-list">' + steps.map(function (s) {
        return item({ check: s.done, cls: s.done ? 'done' : '', title: s.title, href: s.done ? null : s.href, go: s.done ? '' : '→' });
      }).join('') + '</div>';
    Array.prototype.forEach.call($('dh-setup').querySelectorAll('a[href="#topup"]'), function (el) {
      el.addEventListener('click', function (e) { e.preventDefault(); openTopup(); });
    });
  }

  // ------------------------------------------------------------------ ассистенты / задачи
  function renderAssistants() {
    var crm = data.crm || {};
    var box = $('dh-tasks'), title = $('dh-tasks-card').querySelector('.dh-card-title');
    if (crm.tasks && crm.tasks.length) {
      title.textContent = 'Ближайшие задачи';
      box.innerHTML = '<div class="dh-list">' + crm.tasks.map(function (t) {
        return item({ icon: t.channel === 'owner' ? 'bell' : (t.channel === 'call' ? 'phone-call' : 'send'), title: esc(t.title), sub: esc(when(t.at)) });
      }).join('') + '</div>';
      return;
    }
    var a = data.assistants || {};
    title.textContent = 'Ассистенты';
    if (!a.total) {
      box.innerHTML = '<div class="dh-empty">Голосовой ассистент отвечает на звонки и на сайте. <a href="/static/voice-assistants.html">Создать первого →</a></div>';
      return;
    }
    box.innerHTML = '<div class="dh-list">' + a.items.map(function (x) {
      return item({ icon: 'audio-lines', on: x.is_active, title: esc(x.name), sub: esc(x.engine) + (x.is_active ? '' : ' · выключен'), href: '/static/voice-assistants.html' });
    }).join('') + '</div>' + (a.total > a.items.length ? '<a class="dh-link" href="/static/voice-assistants.html">Ещё ' + (a.total - a.items.length) + ' →</a>' : '');
  }

  // ------------------------------------------------------------------ Profi
  function renderProfi() {
    var box = $('dh-profi');
    // Салонам (Voksy Beauty) тариф Profi не продаётся — карточку не показываем
    if (data.agent_mode === 'full' || (data.plan && data.plan.beauty)) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = '<span class="dh-profi-tag">' + ic('lock') + 'Profi</span>' +
      '<h3>Автономный агент обзвона</h3>' +
      '<p>Работает сам, без вашего участия. Доступен после оплаты тарифа Profi, пробного периода у него нет.</p>' +
      '<ul><li>' + ic('phone-outgoing') + 'Сам звонит клиентам и перезванивает</li>' +
      '<li>' + ic('send') + 'Первым пишет в WhatsApp и Telegram</li>' +
      '<li>' + ic('file-text') + 'Готовит КП и документы под клиента</li>' +
      '<li>' + ic('calendar') + 'Ставит задачи и ведёт воронку</li></ul>' +
      '<button type="button" class="dh-btn" id="dh-profi-btn">Подключить Profi</button>';
    $('dh-profi-btn').addEventListener('click', function () { location.hash = 'plans'; });
  }

  function openTopup() {
    if (window.VoksiAISidebar && window.VoksiAISidebar.openTopup) window.VoksiAISidebar.openTopup();
  }

  function render() {
    if (!data) return;
    renderHero();
    renderKpis();
    renderChart();
    renderSide();
    renderRecent();
    // Звонков нет — блок не занимает место на телефоне (на компьютере — подсказка)
    var rc = $('dh-calls-grid'); if (rc) rc.classList.toggle('dh-no-calls', !(data.recent_calls || []).length);
    renderConnect();
    renderSetup();
    renderAssistants();
    renderProfi();
    if (window.VF && window.VF.ready) try { window.VF.ready(); } catch (e) { /* ignore */ }
  }
  function renderAn() {
    renderKpis();
    renderInsights();
    renderChart();
  }

  function load() {
    var t = token();
    if (!t) return;
    try {
      var cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || 'null');
      if (cached) { data = cached; render(); }
    } catch (e) { /* ignore */ }
    loadAn();
    loadToday();
    loadMoney();
    fetch('/api/dashboard/summary', { headers: { Authorization: 'Bearer ' + t } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var s = JSON.stringify(d);
        try { if (sessionStorage.getItem(CACHE_KEY) === s) return; sessionStorage.setItem(CACHE_KEY, s); } catch (e) { /* ignore */ }
        data = d; render();
      })
      .catch(function () { /* сеть: остаётся кэш или скелетоны */ });
  }

  // Отчёт аналитики за выбранный период: из кэша вкладки сразу, свежий — следом
  function strip(d) { var c = Object.assign({}, d); delete c.generated_at; return JSON.stringify(c); }
  function loadAn() {
    var t = token();
    if (!t || !V) return;
    var key = AN_KEY + period, my = ++anSeq;
    var cached = null;
    try { cached = JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (e) { /* ignore */ }
    if (cached) { an = cached; renderAn(); }
    else if (an) $('dh-chart-card').classList.add('dh-busy');
    fetch('/api/analytics/report?period=' + period + '&sections=kpis,series,replies,calls,bot', { headers: { Authorization: 'Bearer ' + t } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (my !== anSeq) return;
        $('dh-chart-card').classList.remove('dh-busy');
        if (!d) return;
        try { sessionStorage.setItem(key, JSON.stringify(d)); } catch (e) { /* ignore */ }
        if (cached && strip(cached) === strip(d)) return;
        an = d; renderAn();
      })
      .catch(function () { $('dh-chart-card').classList.remove('dh-busy'); });
  }

  function init() {
    $('dh-topup').addEventListener('click', openTopup);
    $('dh-upgrade').addEventListener('click', function () {
      var b = document.getElementById('upgrade-subscription-btn');
      if (b) b.click();
    });
    Array.prototype.forEach.call($('dh-period').querySelectorAll('button[data-p]'), function (b) {
      b.classList.toggle('active', b.getAttribute('data-p') === period);
      b.addEventListener('click', function () {
        period = b.getAttribute('data-p'); lsSet('dh_period', period);
        Array.prototype.forEach.call($('dh-period').querySelectorAll('button'), function (x) { x.classList.toggle('active', x === b); });
        loadAn();
      });
    });
    $('dh-chart-tabs').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-m]');
      if (!b) return;
      metric = b.getAttribute('data-m'); lsSet('dh_metric', metric);
      renderKpis(); renderChart();
    });
    load();
    var rt = null, lastW = window.innerWidth;
    window.addEventListener('resize', function () {
      if (window.innerWidth === lastW) return;   // на телефоне resize при прокрутке (панель браузера) — не перерисовываем
      lastW = window.innerWidth;
      clearTimeout(rt);
      rt = setTimeout(function () { if (data || an) renderChart(); }, 150);
    });
    document.addEventListener('vf:wallet', function (e) {
      if (!data || !e.detail) return;
      data.wallet = Object.assign({}, data.wallet, { balance: e.detail.balance });
      renderKpis();
    });
    document.addEventListener('vf:business', function () { if (data) { loadToday(); renderSide(); } loadMoney(); });
    // Первый вход на этом телефоне: ниша приходит с /users/me позже, чем рисуется страница
    document.addEventListener('vf:user', function () { if (hasMoney() && !money.month) { loadMoney(); if (data) { loadToday(); renderSide(); } } });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
