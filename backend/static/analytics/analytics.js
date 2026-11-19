/* ============================================================================
 * /static/analytics/analytics.js — страница «Аналитика» (Старт, Profi, базовый тариф).
 *
 * Один запрос GET /api/analytics/report → вся страница (сервер считает всё сам, без выборок
 * «последних 200 клиентов» в браузере). Период, шаг и показатель графика живут в адресе
 * (?period=30d&g=week&m=calls#channels): ссылка с главной открывает нужный отчёт.
 * Повторный заход рисуется сразу из кэша вкладки, свежие данные — следом; при смене периода
 * прошлый отчёт остаётся на экране полупрозрачным, без мигания скелетонами.
 *
 * Графики — свой лёгкий SVG: столбцы ≤ 24 px со скруглённым верхом и зазором 2 px в стопке,
 * серая линия «прошлый период», подсказка на наведение / касание / стрелки клавиатуры,
 * клик по столбцу раскрывает его (год → месяц → дни → часы), у каждого графика есть таблица.
 * ========================================================================== */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var root, rep = null, seq = 0, observer = null, pop = null;

  var PERIODS = [['today', 'Сегодня'], ['yesterday', 'Вчера'], ['7d', '7 дней'], ['30d', '30 дней'],
    ['90d', '3 месяца'], ['180d', 'Полгода'], ['365d', 'Год']];
  var GRAN = { hour: 'По часам', day: 'По дням', week: 'По неделям', month: 'По месяцам' };
  var CH = {
    whatsapp: { title: 'WhatsApp', c: 'var(--vc-wa)', ic: 'message-circle' },
    telegram: { title: 'Telegram', c: 'var(--vc-tg)', ic: 'send' },
    instagram: { title: 'Instagram', c: 'var(--vc-ig)', ic: 'instagram' },
    call: { title: 'Звонки', c: 'var(--vc-call)', ic: 'phone' },
    booking: { title: 'Онлайн-запись', c: 'var(--vc-book)', ic: 'calendar' },
    manual: { title: 'Добавлены вручную', c: 'var(--vc-muted)', ic: 'user', dark: true }
  };
  var HEAT = ['#f1f4f9', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95'];
  var WD = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  var WD_FULL = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

  var V = window.VChart;
  var KPI_METRIC = V.KPI_METRIC;
  var EMPTY = {
    inbound: 'Обращений за период нет. Подключите WhatsApp, Telegram или Instagram в разделе «Чат-бот» и номер +996 в «Телефонии».',
    new_clients: 'Новых клиентов за период нет.',
    calls: 'Звонков за период не было. Подключите номер +996 в разделе «Телефония».',
    visits: 'Записей за период нет. Отправьте клиентам ссылку для записи из раздела «Записи».',
    replies: 'Ответов клиентам за период не было.',
    dialogs: 'Разговоров ассистентов за период не было.'
  };

  // ── состояние в адресе ──────────────────────────────────────────────────
  var st = readState();
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* приватный режим */ } }
  function readState() {
    var q = new URLSearchParams(location.search);
    return {
      period: q.get('period') || lsGet('an_period') || '30d',
      from: q.get('from') || '', to: q.get('to') || '',
      g: q.get('g') || '', m: q.get('m') || '',
      compare: lsGet('an_compare') !== '0',
      heat: 'all', table: false, stack: []
    };
  }
  function params(withMetric) {
    var p = new URLSearchParams();
    p.set('period', st.period);
    if (st.period === 'custom') { p.set('from', st.from); p.set('to', st.to); }
    if (st.g) p.set(withMetric === 'api' ? 'granularity' : 'g', st.g);
    if (withMetric === true && st.m) p.set('m', st.m);
    return p.toString();
  }
  function writeUrl() {
    try { history.replaceState(null, '', '?' + params(true) + location.hash); } catch (e) { /* ignore */ }
  }

  // ── форматирование (общие функции /static/js/vchart.js) ─────────────────
  var fmtNum = V.fmtNum, fmtSec = V.fmtSec, valHtml = V.valHtml, plural = V.plural, cap = V.cap, sum = V.sum;
  function fmtSom(v) { return fmtNum(v) + ' сом'; }
  function pct(a, b) { return b ? Math.round(a * 100 / b) : 0; }

  // ── загрузка ───────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    root = document.getElementById('an-root');
    S.initMobileMenu();
    var ex = document.getElementById('an-export');
    if (ex) ex.addEventListener('click', exportCsv);
    window.addEventListener('resize', S.debounce(function () { if (rep) drawCharts(); }, 160));
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closePop(); });
    load();
  });

  function strip(d) { var c = Object.assign({}, d); delete c.generated_at; return JSON.stringify(c); }

  function load() {
    var path = '/api/analytics/report?' + params('api');
    var my = ++seq;
    var cached = S.cacheGet(path);
    if (cached) { rep = cached; render(); }
    else if (rep) root.classList.add('an-busy');
    return S.api(path).then(function (r) {
      if (my !== seq) return;
      root.classList.remove('an-busy');
      if (!r.ok) {
        if (!rep) root.innerHTML = '<div class="an-empty" style="padding:80px 0">' + icon('triangle-alert') +
          '<p>Не удалось загрузить аналитику. ' + esc(S.apiError(r) || '') + '</p><button class="btn btn-primary" id="an-retry">Повторить</button></div>';
        var b = document.getElementById('an-retry');
        if (b) b.addEventListener('click', load);
        if (rep) S.toast(S.apiError(r) || 'Не удалось обновить отчёт', 'error');
        S.ready();
        return;
      }
      S.cacheSet(path, r.data);
      if (cached && strip(cached) === strip(r.data)) return;
      rep = r.data;
      render();
    });
  }

  function setPeriod(p, from, to, g, keepStack) {
    if (!keepStack) st.stack = [];
    st.period = p; st.from = from || ''; st.to = to || ''; st.g = g || '';
    if (p !== 'custom') lsSet('an_period', p);
    closePop();
    writeUrl();
    load();
  }

  function exportCsv() {
    if (!rep) return;
    S.download('/api/analytics/export.csv?' + params('api'), 'voksy-analytics-' + rep.period.from + '_' + rep.period.to + '.csv')
      .catch(function () { S.toast('Не удалось скачать таблицу', 'error'); });
  }

  // ── каркас страницы ────────────────────────────────────────────────────
  function sections() {
    var out = [{ id: 'overview', title: 'Обзор' }];
    if (rep.has_bot) out.push({ id: 'channels', title: 'Каналы' }, { id: 'clients', title: 'Клиенты' });
    out.push({ id: 'calls', title: 'Звонки' });
    if (rep.bookings) out.push({ id: 'bookings', title: 'Записи' });
    if (rep.orders) out.push({ id: 'orders', title: 'Заказы' });
    if (rep.bot) out.push({ id: 'bot', title: 'Бот' });
    if (rep.catalog) out.push({ id: 'catalog', title: (rep.niche && rep.niche.catalog_title) || 'Каталог' });
    if (rep.money) out.push({ id: 'money', title: 'Расходы' });
    return out;
  }

  function render() {
    var p = rep.period;
    var niche = rep.niche && rep.niche.title ? esc(rep.niche.title) + ' · ' : '';
    root.innerHTML =
      '<div class="an-head"><div><h2>Аналитика</h2><p>' + niche + '<b>' + esc(cap(p.label)) + '</b>' +
      (p.days > 1 ? ' · стрелки — сравнение с прошлым периодом такой же длины' : ' · сравнение с прошлым днём') + '</p></div></div>' +
      toolbarHtml() +
      '<div class="an-body" id="an-body">' + bodyHtml() + '</div>';
    bindToolbar();
    bindBody();
    drawCharts();
    observeSections();
    if (location.hash && !render.scrolled) {
      render.scrolled = true;
      var t = document.getElementById(location.hash.slice(1));
      if (t) setTimeout(function () { t.scrollIntoView({ block: 'start' }); }, 30);
    }
    S.ready();
  }

  function toolbarHtml() {
    var p = rep.period;
    var chips = '';
    if (st.stack.length) chips += '<button type="button" class="an-chip an-back" data-back>' + icon('arrow-left') + 'Назад</button>';
    chips += PERIODS.map(function (x) {
      return '<button type="button" class="an-chip' + (st.period === x[0] ? ' active' : '') + '" data-p="' + x[0] + '">' + x[1] + '</button>';
    }).join('');
    var custom = st.period === 'custom' || st.period === 'mtd';
    chips += '<button type="button" class="an-chip' + (custom ? ' active' : ' ghost') + '" data-dates aria-haspopup="dialog">' + icon('calendar') +
      '<span>' + (custom ? esc(cap(p.label)) : 'Свой период') + '</span></button>';
    var gr = p.granularities || [];
    var seg = gr.length > 1 ? '<div class="an-seg" role="group" aria-label="Шаг графика">' + gr.map(function (g) {
      return '<button type="button" data-g="' + g + '" class="' + (g === p.granularity ? 'active' : '') + '">' + GRAN[g] + '</button>';
    }).join('') + '</div>' : '';
    return '<div class="an-toolbar"><div class="an-row"><div class="an-periods" role="group" aria-label="Период">' + chips + '</div>' + seg + '</div>' +
      '<nav class="an-nav" id="an-nav" aria-label="Разделы отчёта">' + sections().map(function (s) {
        return '<a href="#' + s.id + '" data-sec="' + s.id + '">' + esc(s.title) + '</a>';
      }).join('') + '</nav></div>';
  }

  function bindToolbar() {
    S.$$('[data-p]', root).forEach(function (b) {
      b.addEventListener('click', function () { setPeriod(b.dataset.p); });
    });
    S.$$('[data-g]', root).forEach(function (b) {
      b.addEventListener('click', function () { st.g = b.dataset.g; writeUrl(); load(); });
    });
    var back = root.querySelector('[data-back]');
    if (back) back.addEventListener('click', function () {
      var prev = st.stack.pop();
      if (prev) { st.period = prev.period; st.from = prev.from; st.to = prev.to; st.g = prev.g; writeUrl(); load(); }
    });
    var dates = root.querySelector('[data-dates]');
    if (dates) dates.addEventListener('click', function (e) { e.stopPropagation(); openPop(dates); });
    S.$$('#an-nav a', root).forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        var t = document.getElementById(a.dataset.sec);
        if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
        try { history.replaceState(null, '', '?' + params(true) + '#' + a.dataset.sec); } catch (er) { /* ignore */ }
      });
    });
  }

  // Свой период: два поля даты и быстрые варианты. Окно живёт в body (панель периода прокручивается).
  function iso(d) { return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function openPop(btn) {
    if (pop) { closePop(); return; }
    var today = new Date(rep.period.today + 'T00:00:00');
    var prevM0 = new Date(today.getFullYear(), today.getMonth() - 1, 1), prevM1 = new Date(today.getFullYear(), today.getMonth(), 0);
    pop = document.createElement('div');
    pop.className = 'an-pop';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', 'Свой период');
    pop.innerHTML =
      '<div class="an-quick">' +
      '<button type="button" data-q="mtd">Этот месяц</button>' +
      '<button type="button" data-q="prev" data-f="' + iso(prevM0) + '" data-t="' + iso(prevM1) + '">Прошлый месяц</button>' +
      '<button type="button" data-q="ytd" data-f="' + today.getFullYear() + '-01-01" data-t="' + rep.period.today + '">С начала года</button></div>' +
      '<div class="an-pop-row"><label>С<input type="date" id="an-from" max="' + rep.period.today + '" value="' + esc(st.from || rep.period.from) + '"></label>' +
      '<label>По<input type="date" id="an-to" max="' + rep.period.today + '" value="' + esc(st.to || rep.period.to) + '"></label></div>' +
      '<button type="button" class="btn btn-primary" id="an-apply">Показать</button>';
    document.body.appendChild(pop);
    var r = btn.getBoundingClientRect();
    if (window.innerWidth > 760) {
      pop.style.position = 'fixed';
      pop.style.top = (r.bottom + 8) + 'px';
      pop.style.left = Math.max(12, Math.min(r.left, window.innerWidth - 302)) + 'px';
    } else {
      pop.style.top = (r.bottom + 8) + 'px';
    }
    S.$$('[data-q]', pop).forEach(function (b) {
      b.addEventListener('click', function () {
        if (b.dataset.q === 'mtd') setPeriod('mtd');
        else setPeriod('custom', b.dataset.f, b.dataset.t);
      });
    });
    pop.querySelector('#an-apply').addEventListener('click', function () {
      var f = pop.querySelector('#an-from').value, t = pop.querySelector('#an-to').value;
      if (!f || !t) { S.toast('Укажите обе даты', 'error'); return; }
      setPeriod('custom', f, t);
    });
    pop.addEventListener('click', function (e) { e.stopPropagation(); });
    setTimeout(function () { document.addEventListener('click', closePop, { once: true }); }, 0);
  }
  function closePop() { if (pop) { pop.remove(); pop = null; } }

  // Подсветка раздела в меню разделов при прокрутке
  function observeSections() {
    if (observer) observer.disconnect();
    if (!('IntersectionObserver' in window)) return;
    var links = {};
    S.$$('#an-nav a', root).forEach(function (a) { links[a.dataset.sec] = a; });
    observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        Object.keys(links).forEach(function (k) { links[k].classList.toggle('active', k === en.target.id); });
      });
    }, { rootMargin: '-35% 0px -60% 0px' });
    S.$$('.an-section', root).forEach(function (s) { observer.observe(s); });
  }

  // ── разделы ────────────────────────────────────────────────────────────
  function bodyHtml() {
    return overviewHtml() + (rep.has_bot ? channelsHtml() + clientsHtml() : '') + callsHtml() +
      (rep.bookings ? bookingsHtml() : '') + (rep.orders ? ordersHtml() : '') + (rep.bot ? botHtml() : '') + (rep.catalog ? catalogHtml() : '') +
      (rep.money ? moneyHtml() : '');
  }
  function section(id, ic, title, inner, link) {
    return '<section class="an-section" id="' + id + '"><div class="an-section-title"><h3>' + icon(ic) + esc(title) + '</h3>' +
      (link ? '<a href="' + link[0] + '">' + esc(link[1]) + ' →</a>' : '') + '</div>' + inner + '</section>';
  }
  function card(title, sub, inner, cls) {
    return '<div class="an-card' + (cls ? ' ' + cls : '') + '">' + (title ? '<div class="an-card-head"><div><h4>' + esc(title) + '</h4>' +
      (sub ? '<p>' + esc(sub) + '</p>' : '') + '</div></div>' : '') + inner + '</div>';
  }

  function overviewHtml() {
    var ins = rep.insights || [];
    var insHtml = ins.length ? card('Главное за период', '', '<div class="an-insights">' + ins.map(function (x) {
      return '<div class="an-insight ' + esc(x.tone) + '"><span class="an-insight-ic">' + icon(x.icon) + '</span><span>' + esc(x.text) + '</span></div>';
    }).join('') + '</div>', 'an-insights-card') : '';
    var kpis = '<div class="an-kpis">' + (rep.kpis || []).map(kpiHtml).join('') + '</div>';
    return '<section class="an-section" id="overview">' + kpis + mainCardHtml() + insHtml + '</section>';
  }

  function kpiHtml(k, i) {
    var m = KPI_METRIC[k.id];
    var on = m && currentMetric().id === m;
    var long = String(k.fmt === 'sec' ? fmtSec(k.value) : fmtNum(k.value)).length > 6;
    return '<button type="button" class="an-kpi' + (on ? ' on' : '') + '" data-kpi="' + i + '" aria-pressed="' + (on ? 'true' : 'false') + '">' +
      '<span class="an-kpi-label"><span>' + esc(k.label) + '</span></span>' +
      '<span class="an-kpi-value' + (long ? ' long' : '') + '">' + valHtml(k.value, k.fmt) + '</span>' +
      '<span class="an-kpi-sub">' + esc(k.sub || '') + '</span>' +
      '<span class="an-kpi-foot">' + deltaHtml(k) + (k.spark && k.spark.length > 2 ? sparkSvg(k.spark) : '') + '</span></button>';
  }
  var deltaHtml = V.deltaHtml, sparkSvg = V.sparkSvg;

  function metricsAvail() { return V.metricsAvail(rep); }
  function currentMetric() { return V.metricById(rep, st.m); }
  function metricTotal(m) { return V.metricTotal(rep, m); }

  function mainCardHtml() {
    var m = currentMetric();
    var tabs = metricsAvail().map(function (x) {
      var tot = metricTotal(x);
      return '<button type="button" data-m="' + x.id + '" class="' + (x.id === m.id ? 'active' : '') + '">' + esc(x.title) +
        '<b>' + fmtNum(tot) + '</b></button>';
    }).join('');
    var g = rep.period.granularity;
    var hint = g === 'hour' ? '' : '<div class="an-drill-hint">Нажмите на столбец, чтобы раскрыть его ' +
      (g === 'day' ? 'по часам' : 'по дням') + '</div>';
    return '<div class="an-card" id="an-main"><div class="an-main-head"><div class="an-metrics" role="tablist">' + tabs + '</div>' +
      '<div class="an-tools">' + (m.prev ? '<label class="an-toggle"><input type="checkbox" id="an-compare"' + (st.compare ? ' checked' : '') +
        '>Прошлый период</label>' : '') +
      '<button type="button" class="an-icon-btn' + (st.table ? ' on' : '') + '" id="an-table-btn" aria-pressed="' + (st.table ? 'true' : 'false') + '">' +
      icon('list') + 'Таблица</button></div></div>' +
      '<p class="an-note" style="margin:0 0 8px">' + esc(m.sub) + '</p>' +
      '<div class="vc-legend" id="an-legend"></div><div class="vc-chart an-chart" id="an-chart" tabindex="0" role="img"></div>' + hint +
      '<div id="an-table"' + (st.table ? '' : ' hidden') + '></div></div>';
  }

  // Каналы: карточка на канал, доля обращений, реклама, тепловая карта
  function channelsHtml() {
    var chs = (rep.channels || []).filter(function (c) { return c.key !== 'booking' || c.new_clients || c.messages_in; });
    var cards = chs.map(function (c) {
      var meta = CH[c.key] || CH.manual;
      var isCall = c.key === 'call', isBook = c.key === 'booking';
      var big = isCall ? (c.messages_in + c.bot_out) : c.messages_in;
      var bigLabel = isCall ? plural(big, 'звонок', 'звонка', 'звонков') : isBook ? plural(big, 'запись по ссылке', 'записи по ссылке', 'записей по ссылке')
        : plural(big, 'сообщение клиентов', 'сообщения клиентов', 'сообщений клиентов');
      var rows = [];
      if (isCall) {
        rows.push(['Входящих', fmtNum(c.messages_in)], ['Исходящих', fmtNum(c.bot_out)], ['С разговором', fmtNum(c.answered || 0)]);
        if (c.unanswered) rows.push(['Пропущено', fmtNum(c.unanswered), 'warn']);
      } else if (!isBook) {
        rows.push(['Клиентов написало', fmtNum(c.writers)], ['Ответов бота', fmtNum(c.bot_out)], ['Ваших ответов', fmtNum(c.owner_out)],
          ['Время ответа', c.median_reply_sec == null ? '—' : fmtSec(c.median_reply_sec)]);
        if (c.unanswered) rows.push(['Без ответа', fmtNum(c.unanswered), 'warn']);
      }
      rows.push(['Новых клиентов', fmtNum(c.new_clients)]);
      return '<div class="an-ch"><div class="an-ch-top"><span class="an-ch-dot" style="background:' + meta.c + '">' + icon(meta.ic) + '</span>' +
        '<span class="an-ch-name">' + esc(c.title) + '</span></div>' +
        '<div class="an-ch-big"><b>' + fmtNum(big) + '</b><span>' + esc(bigLabel) + '</span>' +
        (c.delta ? deltaHtml({ delta: c.delta, prev: c.prev_in, good: 'up' }) : '') + '</div>' +
        '<div class="an-kv">' + rows.map(function (r) {
          return '<div><span>' + esc(r[0]) + '</span><b' + (r[2] ? ' class="' + r[2] + '"' : '') + '>' + esc(r[1]) + '</b></div>';
        }).join('') + '</div></div>';
    }).join('');
    var shares = (rep.channels || []).filter(function (c) { return c.key !== 'booking'; }).map(function (c) {
      return { label: c.title, value: c.messages_in, color: (CH[c.key] || CH.manual).c };
    });
    var ad = rep.ads && rep.ads.clients ? '<div class="an-ad">' + icon('instagram') + '<span>Из рекламы в Instagram и Facebook (Click-to-WhatsApp) пришло <b>' +
      fmtNum(rep.ads.clients) + '</b> ' + plural(rep.ads.clients, 'клиент', 'клиента', 'клиентов') + '.</span></div>' : '';
    var heatChips = [['all', 'Все'], ['whatsapp', 'WhatsApp'], ['telegram', 'Telegram'], ['instagram', 'Instagram'], ['call', 'Звонки']]
      .map(function (x) { return '<button type="button" data-heat="' + x[0] + '" class="' + (st.heat === x[0] ? 'active' : '') + '">' + x[1] + '</button>'; }).join('');
    var inner = '<div class="an-channels">' + cards + '</div>' + ad +
      '<div class="an-grid-2">' +
      card('Доля обращений', 'Сообщения клиентов и входящие звонки по каналам', segbar(shares, 'обращений')) +
      '<div class="an-card"><div class="an-card-head"><div><h4>Когда пишут и звонят</h4><p>Обращения по дням недели и часам, время Бишкека</p></div>' +
      '<div class="an-seg" id="an-heat-ch">' + heatChips + '</div></div><div id="an-heat"></div></div>' +
      '</div>';
    return section('channels', 'messages-square', 'Каналы и соцсети', inner, ['/static/inbox.html', 'Открыть Инбокс']);
  }

  function clientsHtml() {
    var cl = rep.clients || {}, co = cl.cohort || {}, f = rep.funnel || {};
    var first = (cl.first_channel || []).map(function (x) {
      var meta = CH[x.key] || CH.manual;
      return { label: meta.title, value: x.count, color: meta.c };
    });
    var cohort = co.new ? segbar([
      { label: 'Купили', value: co.won, color: 'var(--vc-good)' },
      { label: 'В работе', value: co.open, color: 'var(--vc-tg)' },
      { label: 'Отказ', value: co.lost, color: 'var(--vc-muted)', dark: true }
    ], 'клиентов') + '<p class="an-note">Из ' + fmtNum(co.new) + ' новых ' + plural(co.new, 'клиента', 'клиентов', 'клиентов') + ' за период купили ' +
      fmtNum(co.won) + ' (' + pct(co.won, co.new) + '%)' + (co.won_amount ? ' на ' + fmtSom(co.won_amount) : '') + '.</p>'
      : '<div class="an-empty">Новых клиентов за период нет.</div>';
    var stages = (f.stages || []).map(function (s) {
      return { label: s.label, value: s.count, color: s.kind === 'won' ? 'var(--vc-good)' : s.kind === 'lost' ? 'var(--vc-muted)' : 'var(--vc-in)',
        sw: s.color, sub: s.amount ? fmtSom(s.amount) : '' };
    });
    var crmLink = rep.agent_mode === 'full' ? '/static/crm.html' : '/static/start-crm.html';
    var title = rep.modules && rep.modules.crm === false ? 'Клиенты' : 'Клиенты и воронка';
    var inner = '<div class="an-grid-2">' +
      card('Откуда пришли новые клиенты', 'Первое обращение клиента: мессенджер, звонок, запись по ссылке',
        first.length ? hbars(first) : '<div class="an-empty">Новых клиентов за период нет.</div>') +
      card('Что стало с новыми клиентами', 'Клиенты, появившиеся за период, — где они сейчас', cohort) +
      card('Клиенты по этапам сейчас', 'Вся база, а не только период' + (f.pipeline_amount ? ' · в работе на ' + fmtSom(f.pipeline_amount) : ''),
        stages.length ? hbars(stages) : '<div class="an-empty">Клиентов пока нет.</div>') +
      card('Причины отказа и теги', 'За период', reasonsHtml(f)) +
      '</div>';
    return section('clients', 'users', title, inner, [crmLink, 'Открыть клиентов']);
  }
  function reasonsHtml(f) {
    var r = (f.lost_reasons || []).slice(0, 6), t = (f.tags || []).slice(0, 12);
    if (!r.length && !t.length) return '<div class="an-empty">Бот записывает причину отказа и теги в карточку клиента — здесь появится сводка.</div>';
    return (r.length ? '<div class="an-list">' + r.map(function (x) {
      return '<div><span>' + esc(cap(x.text)) + '</span><b>' + fmtNum(x.count) + '</b></div>';
    }).join('') + '</div>' : '') + (t.length ? '<div class="an-tags" style="margin-top:12px">' + t.map(function (x) {
      return '<span>' + esc(x.tag) + '<b>' + fmtNum(x.count) + '</b></span>';
    }).join('') + '</div>' : '');
  }

  function callsHtml() {
    var c = rep.calls || {};
    var total = (c.in || 0) + (c.out || 0);
    if (!total) {
      return section('calls', 'phone', 'Звонки', card('', '', '<div class="an-empty">За период звонков не было. Подключите номер +996 в разделе ' +
        '<a href="/static/telephony.html">Телефония</a> — входящие примет голосовой ассистент.</div>'));
    }
    var stats = '<div class="an-stats">' +
      stat('Входящие', fmtNum(c.in), c.answer_rate != null ? 'принято ' + c.answer_rate + '%' : '') +
      stat('Исходящие', fmtNum(c.out), c.out_answer_rate != null && c.out ? 'дозвон ' + c.out_answer_rate + '%' : '') +
      stat('Пропущено', fmtNum(c.missed), c.in ? pct(c.missed, c.in) + '% входящих' : '', c.missed ? 'warn' : '') +
      stat('Разговоры', fmtNum(c.talk_min, 1) + '<small>мин</small>', 'средний ' + fmtSec(c.avg_sec), '', true) +
      stat('Разных звонивших', fmtNum(c.unique_callers), '') + '</div>';
    var out = c.outcomes || [];
    var colors = { SUCCESS: 'var(--vc-good)', FOLLOWUP: 'var(--vc-tg)', NO_ANSWER: 'var(--vc-muted)', REJECTED: 'var(--vc-bad)', OTHER: 'var(--vc-prev)' };
    var outcomes = out.length ? card('Чем закончились звонки агента', 'Итог разбора после разговора',
      segbar(out.map(function (o) { return { label: o.title, value: o.count, color: colors[o.key] || 'var(--vc-prev)', dark: o.key === 'NO_ANSWER' }; }), 'звонков')) : '';
    var inner = card('', '', stats) + '<div class="an-grid-2">' +
      card('Входящие по часам', 'Во сколько звонят клиенты, время Бишкека', '<div class="vc-chart" id="an-hours" style="min-height:170px"></div>', outcomes ? '' : 'an-span-2') +
      outcomes + '</div>';
    return section('calls', 'phone', 'Звонки', inner, ['/static/conversations.html', 'Записи разговоров']);
  }
  function stat(label, value, sub, tone, raw) {
    return '<div class="an-stat' + (tone ? ' ' + tone : '') + '"><span>' + esc(label) + '</span><b>' + (raw ? value : value) + '</b>' +
      (sub ? '<em>' + esc(sub) + '</em>' : '') + '</div>';
  }

  function bookingsHtml() {
    var b = rep.bookings;
    var stats = '<div class="an-stats">' +
      stat('Визитов', fmtNum(b.total), b.clients ? fmtNum(b.clients) + ' ' + plural(b.clients, 'клиент', 'клиента', 'клиентов') : '') +
      stat('Пришли', fmtNum(b.arrived), '', 'good') +
      stat('Не пришли', fmtNum(b.no_show), b.no_show_pct != null ? b.no_show_pct + '% неявок' : '', b.no_show ? 'warn' : '') +
      stat('Выручка', fmtNum(b.revenue) + '<small>сом</small>', b.avg_check ? 'средний чек ' + fmtSom(b.avg_check) : '', '', true) +
      stat('Повторные клиенты', fmtNum(b.repeat_clients), b.clients ? pct(b.repeat_clients, b.clients) + '% вернулись' : '') +
      (b.load_pct != null ? stat('Загрузка мастеров', b.load_pct + '<small>%</small>', 'занято от графика', '', true) : '') +
      (b.cancelled ? stat('Отменено', fmtNum(b.cancelled), '') : '') + '</div>';
    var status = segbar([
      { label: 'Пришли', value: b.arrived, color: 'var(--vc-good)' },
      { label: 'Не пришли', value: b.no_show, color: 'var(--vc-bad)' },
      { label: 'Записаны', value: b.upcoming, color: 'var(--vc-tg)' },
      { label: 'Отменили', value: b.cancelled, color: 'var(--vc-muted)', dark: true }
    ], 'записей');
    var srcNames = { online: 'По ссылке', bot: 'Бот в переписке', call: 'Голосовой агент', owner: 'Вы', staff: 'Сотрудник' };
    var srcColors = { online: 'var(--vc-book)', bot: 'var(--vc-in)', call: 'var(--vc-call)', owner: 'var(--vc-out)', staff: 'var(--vc-tg)' };
    var sources = Object.keys(b.sources || {}).sort(function (x, y) { return b.sources[y] - b.sources[x]; }).map(function (k) {
      return { label: srcNames[k] || k, value: b.sources[k], color: srcColors[k] || 'var(--vc-prev)' };
    });
    var services = (b.services || []).length ? '<div class="an-table-wrap" style="max-height:none"><table class="an-table"><thead><tr><th>Услуга</th><th>Визитов</th><th>Выручка</th></tr></thead><tbody>' +
      b.services.map(function (s) { return '<tr><td>' + esc(s.title) + '</td><td>' + fmtNum(s.count) + '</td><td>' + fmtSom(s.revenue) + '</td></tr>'; }).join('') +
      '</tbody></table></div>' : '<div class="an-empty">Записей на услуги за период нет.</div>';
    var masters = (b.masters || []).map(function (m) {
      return { label: m.name, value: m.load_pct || 0, max: 100, color: m.color || 'var(--vc-in)', suffix: '%',
        sub: fmtNum(m.count) + ' ' + plural(m.count, 'визит', 'визита', 'визитов') + ' · ' + fmtNum(Math.round(m.booked_min / 60)) + ' из ' + fmtNum(Math.round(m.capacity_min / 60)) + ' ч' };
    });
    var inner = card('', '', stats + '<div style="margin-top:12px">' + status + '</div>') +
      '<div class="an-grid-2">' + card('Популярные услуги', 'Визиты и выручка (пришедшие клиенты)', services) +
      card('Загрузка мастеров', 'Занятое время от рабочего графика за период', masters.length ? hbars(masters) : '<div class="an-empty">Мастеров пока нет.</div>') +
      card('Откуда записи', 'Кто записал клиента за период', sources.length ? hbars(sources) : '<div class="an-empty">За период новых записей нет.</div>') +
      '</div>';
    return section('bookings', 'calendar', 'Онлайн-запись', inner, ['/static/booking.html', 'Открыть календарь']);
  }

  // Заказы (магазины, доставка): сколько, выдано, отменено, выручка и средний чек, откуда, что берут
  function ordersHtml() {
    var o = rep.orders;
    var stats = '<div class="an-stats">' +
      stat('Заказов', fmtNum(o.total), o.clients ? fmtNum(o.clients) + ' ' + plural(o.clients, 'клиент', 'клиента', 'клиентов') : '') +
      stat('Выдано', fmtNum(o.done), '', 'good') +
      stat('В работе', fmtNum(o.open), '') +
      stat('Выручка', fmtNum(o.revenue) + '<small>сом</small>', o.avg_check ? 'средний чек ' + fmtSom(o.avg_check) : '', '', true) +
      stat('Оформил бот', fmtNum(o.by_bot), o.total ? pct(o.by_bot, o.total) + '% заказов' : '') +
      (o.cancelled ? stat('Отменено', fmtNum(o.cancelled), o.cancel_pct != null ? o.cancel_pct + '% заказов' : '', o.cancel_pct >= 20 ? 'warn' : '') : '') + '</div>';
    var srcNames = { bot: 'Бот в переписке', owner: 'Вы', staff: 'Сотрудники' };
    var srcColors = { bot: 'var(--vc-in)', owner: 'var(--vc-out)', staff: 'var(--vc-tg)' };
    var sources = Object.keys(o.sources || {}).sort(function (x, y) { return o.sources[y] - o.sources[x]; }).map(function (k) {
      return { label: srcNames[k] || k, value: o.sources[k], color: srcColors[k] || 'var(--vc-prev)' };
    });
    var dlv = o.delivery || {};
    var how = segbar([
      { label: 'Доставка', value: dlv.delivery || 0, color: 'var(--vc-tg)' },
      { label: 'Самовывоз', value: dlv.pickup || 0, color: 'var(--vc-book)' }
    ], 'заказов');
    var top = (o.top || []).length ? '<div class="an-table-wrap" style="max-height:none"><table class="an-table"><thead><tr><th>Позиция</th><th>Штук</th><th>Сумма</th></tr></thead><tbody>' +
      o.top.map(function (t) { return '<tr><td>' + esc(t.title) + '</td><td>' + fmtNum(t.qty) + '</td><td>' + fmtSom(t.amount) + '</td></tr>'; }).join('') +
      '</tbody></table></div>' : '<div class="an-empty">Заказов за период нет.</div>';
    var inner = card('', '', stats + '<div style="margin-top:12px">' + how + '</div>') +
      '<div class="an-grid-2">' + card('Что заказывают', 'Позиции в заказах за период (без отменённых)', top) +
      card('Кто оформил', 'Откуда заказы за период', sources.length ? hbars(sources) : '<div class="an-empty">За период заказов нет.</div>') +
      '</div>';
    return section('orders', 'shopping-cart', 'Заказы', inner, ['/static/orders.html', 'Открыть заказы']);
  }

  function botHtml() {
    var b = rep.bot;
    var stats = '<div class="an-stats">' +
      stat('Ответов бота', fmtNum(b.replies), b.bot_share != null ? b.bot_share + '% всех ответов' : '') +
      stat('Ваших ответов', fmtNum(b.owner_replies), '') +
      stat('Бот отвечает за', b.median_bot_sec == null ? '—' : esc(fmtSec(b.median_bot_sec)), 'в среднем', 'good', true) +
      stat('Вы отвечаете за', b.median_owner_sec == null ? '—' : esc(fmtSec(b.median_owner_sec)), 'в среднем', '', true) +
      stat('Вёл только бот', fmtNum(b.bot_only_clients), b.writers ? 'из ' + fmtNum(b.writers) + ' написавших' : '') +
      stat('Без ответа', fmtNum(b.unanswered), b.waiting ? 'ещё ждут: ' + fmtNum(b.waiting) : '', b.unanswered ? 'warn' : '') +
      stat('Нет ответа в базе', fmtNum(b.gaps), 'вопросов клиентов', b.gaps ? 'warn' : '') +
      (b.spam_blocked ? stat('Спам отключён', fmtNum(b.spam_blocked), 'бот вышел из чата') : '') +
      (b.paused ? stat('Бот на паузе', fmtNum(b.paused), 'клиентов сейчас') : '') + '</div>';
    var kb = rep.agent_mode === 'full' ? '/static/agent-knowledge.html#gaps' : '/static/start-bot.html#knowledge';
    var notes = [];
    if (b.unanswered) notes.push('<a href="/static/inbox.html">Откройте Инбокс</a>, папка «Ждут ответа» — там клиенты без ответа.');
    if (b.gaps) notes.push('<a href="' + kb + '">Допишите в базу знаний</a> ответы на вопросы, которые бот не нашёл.');
    return section('bot', 'bot', 'Как работает бот', card('', '', stats + (notes.length ? '<p class="an-note">' + notes.join(' ') + '</p>' : '')),
      [rep.agent_mode === 'full' ? '/static/agent.html' : '/static/start-bot.html', 'Настроить бота']);
  }

  function catalogHtml() {
    var c = rep.catalog, title = (rep.niche && rep.niche.catalog_title) || 'Каталог';
    var stats = '<div class="an-stats">' +
      stat('Позиций', fmtNum(c.active), '') +
      stat('Нет в наличии', fmtNum(c.out_of_stock), '', c.out_of_stock ? 'warn' : '') +
      stat('Заканчивается', fmtNum(c.low_stock), '1–3 шт.', c.low_stock ? 'warn' : '') +
      stat('Без фото', fmtNum(c.no_photo), 'бот не покажет товар', c.no_photo ? 'warn' : '') +
      stat('Без цены', fmtNum(c.no_price), '', c.no_price ? 'warn' : '') +
      stat('Со скидкой', fmtNum(c.on_sale), '') +
      (c.stock_value ? stat('Товара на складе', fmtNum(c.stock_value) + '<small>сом</small>', 'по цене продажи', '', true) : '') + '</div>';
    var cols = (c.collections || []).map(function (x) {
      return { label: x.title, value: x.count, color: 'var(--vc-in)', sub: x.out ? 'нет в наличии: ' + x.out : '' };
    });
    var link = rep.agent_mode === 'full' ? '/static/agent-knowledge.html#catalog' : '/static/start-bot.html#catalog';
    return section('catalog', 'package', title, '<div class="an-grid-2">' + card('Склад сейчас', '', stats) +
      card('По коллекциям', 'Сколько позиций в каждой', cols.length ? hbars(cols) : '<div class="an-empty">Коллекций пока нет.</div>') + '</div>', [link, 'Открыть каталог']);
  }

  function moneyHtml() {
    var m = rep.money;
    var stats = '<div class="an-stats">' +
      stat('Потрачено на звонки', fmtNum(m.wallet_spent, 2) + '<small>сом</small>', fmtNum(m.paid_minutes, 1) + ' мин разговора', '', true) +
      stat('Пополнено', fmtNum(m.wallet_topup, 2) + '<small>сом</small>', '', '', true) +
      stat('Баланс кошелька', fmtNum(m.wallet_balance, 2) + '<small>сом</small>', 'сейчас', m.wallet_balance <= 0 ? 'warn' : '', true) +
      (rep.has_bot ? stat('Кредитов бота потрачено', fmtNum(m.credits_spent), 'осталось ' + fmtNum(m.credits_balance)) : '') + '</div>';
    return section('money', 'wallet', 'Расходы', card('', '', stats), ['/static/settings.html#wallet', 'История кошелька']);
  }

  // ── элементы-фигуры ────────────────────────────────────────────────────
  // Часть целого — высокая сегментная полоса: доля внутри блока (если влезает), имена — в подписи ниже
  function segbar(items, unit) {
    items = items.filter(function (x) { return x.value > 0; });
    var total = items.reduce(function (t, x) { return t + x.value; }, 0);
    if (!total) return '<div class="an-empty">Данных за период нет.</div>';
    return '<div class="an-segbar" role="img" aria-label="' + esc(items.map(function (x) { return x.label + ': ' + x.value; }).join(', ')) + '">' +
      items.map(function (x) {
        var p = x.value / total * 100;
        return '<div class="' + (x.dark ? 'dark' : '') + '" style="flex:' + x.value + ' 1 0;background:' + x.color + '" title="' + esc(x.label + ': ' + fmtNum(x.value) + ' (' + Math.round(p) + '%)') + '">' +
          (p >= 9 ? '<span>' + Math.round(p) + '%</span>' : '') + '</div>';
      }).join('') + '</div><div class="an-seglegend">' + items.map(function (x) {
        return '<span><i style="background:' + x.color + '"></i>' + esc(x.label) + ' <b>' + fmtNum(x.value) + '</b></span>';
      }).join('') + '<span style="margin-left:auto;color:var(--an-ink-3)">всего ' + fmtNum(total) + ' ' + esc(unit || '') + '</span></div>';
  }
  function hbars(items) {
    var max = Math.max.apply(null, items.map(function (x) { return x.max || x.value; }).concat([1]));
    return '<div class="an-hbars">' + items.map(function (x) {
      var w = (x.max ? x.value / x.max : x.value / max) * 100;
      return '<div><div class="an-hbar-top"><span>' + (x.sw ? '<i class="an-sw" style="background:' + esc(x.sw) + '"></i>' : '') +
        '<em>' + esc(x.label) + '</em></span><b>' + fmtNum(x.value) + (x.suffix || '') + (x.sub ? '<small>' + esc(x.sub) + '</small>' : '') + '</b></div>' +
        '<div class="an-hbar-track"><div style="width:' + Math.max(0, Math.min(100, w)).toFixed(1) + '%;background:' + x.color + '"></div></div></div>';
    }).join('') + '</div>';
  }

  // ── события в теле страницы ────────────────────────────────────────────
  function bindBody() {
    S.$$('[data-kpi]', root).forEach(function (b) {
      b.addEventListener('click', function () {
        var k = rep.kpis[+b.dataset.kpi];
        var m = KPI_METRIC[k.id];
        if (m && metricsAvail().some(function (x) { return x.id === m; })) {
          st.m = m; writeUrl(); refreshMain();
          var card = document.getElementById('an-main');
          var r = card && card.getBoundingClientRect();
          if (r && (r.top < 80 || r.top > window.innerHeight * .6)) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else if (k.section) {
          var t = document.getElementById(k.section);
          if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
    });
    bindMain();
    var hc = document.getElementById('an-heat-ch');
    if (hc) hc.addEventListener('click', function (e) {
      var b = e.target.closest('[data-heat]');
      if (!b) return;
      st.heat = b.dataset.heat;
      S.$$('[data-heat]', hc).forEach(function (x) { x.classList.toggle('active', x === b); });
      drawHeat();
    });
  }
  function bindMain() {
    S.$$('[data-m]', root).forEach(function (b) {
      b.addEventListener('click', function () { st.m = b.dataset.m; writeUrl(); refreshMain(); });
    });
    var cmp = document.getElementById('an-compare');
    if (cmp) cmp.addEventListener('change', function () { st.compare = cmp.checked; lsSet('an_compare', st.compare ? '1' : '0'); drawMain(); });
    var tb = document.getElementById('an-table-btn');
    if (tb) tb.addEventListener('click', function () {
      st.table = !st.table;
      tb.classList.toggle('on', st.table); tb.setAttribute('aria-pressed', st.table ? 'true' : 'false');
      var t = document.getElementById('an-table');
      t.hidden = !st.table;
      if (st.table) drawTable();
    });
  }
  function refreshMain() {
    var old = document.getElementById('an-main');
    if (!old) return;
    var tmp = document.createElement('div');
    tmp.innerHTML = mainCardHtml();
    old.replaceWith(tmp.firstChild);
    S.$$('[data-kpi]', root).forEach(function (b) {
      var k = rep.kpis[+b.dataset.kpi];
      var on = KPI_METRIC[k.id] === currentMetric().id;
      b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    bindMain();
    drawMain();
    if (st.table) drawTable();
  }

  // Клик по столбцу: год → месяц по дням, неделя → дни, день → часы
  function drill(i) {
    var b = rep.buckets[i], g = rep.period.granularity;
    if (!b || g === 'hour' || b.future) return;
    st.stack.push({ period: st.period, from: st.from, to: st.to, g: st.g });
    setPeriod('custom', b.from, b.to, g === 'day' ? 'hour' : 'day', true);
  }

  // ── графики ────────────────────────────────────────────────────────────
  function drawCharts() {
    drawMain();
    if (st.table) drawTable();
    drawHours();
    drawHeat();
  }

  function mainSeries() {
    var m = currentMetric();
    var prev = m.prev && st.compare ? (rep.prev || {})[m.prev] || null : null;
    return { m: m, parts: V.metricParts(rep, m), prev: prev };
  }

  function drawMain() {
    var el = document.getElementById('an-chart');
    if (!el) return;
    var ms = mainSeries();
    var empty = !metricTotal(ms.m) && !(ms.prev && sum(ms.prev));
    var legend = empty ? '' : V.legendHtml(ms.parts, ms.prev);
    var hintEl = root.querySelector('.an-drill-hint');
    if (hintEl) hintEl.hidden = empty;
    document.getElementById('an-legend').innerHTML = legend;
    el.setAttribute('aria-label', ms.m.title + ' — ' + rep.period.label + ', всего ' + fmtNum(metricTotal(ms.m)) + '. Подробно — кнопка «Таблица».');
    V.render(el, {
      buckets: rep.buckets, parts: ms.parts, prev: ms.prev, height: window.innerWidth < 760 ? 210 : 260,
      drill: rep.period.granularity !== 'hour' ? drill : null,
      drillText: rep.period.granularity === 'day' ? 'Нажмите — по часам' : 'Нажмите — по дням',
      emptyText: EMPTY[ms.m.id] || 'За выбранный период данных нет.'
    });
  }

  function drawTable() {
    var box = document.getElementById('an-table');
    if (!box) return;
    var ms = mainSeries();
    var head = '<tr><th>Период</th>' + ms.parts.map(function (p) { return '<th>' + esc(p.label) + '</th>'; }).join('') +
      (ms.parts.length > 1 ? '<th>Всего</th>' : '') + (ms.prev ? '<th>Прошлый период</th>' : '') + '</tr>';
    var rows = rep.buckets.map(function (b, i) {
      var tot = ms.parts.reduce(function (t, p) { return t + (p.values[i] || 0); }, 0);
      return '<tr><td>' + esc(cap(b.title)) + '</td>' + ms.parts.map(function (p) { return '<td>' + fmtNum(p.values[i] || 0, 1) + '</td>'; }).join('') +
        (ms.parts.length > 1 ? '<td>' + fmtNum(tot, 1) + '</td>' : '') + (ms.prev ? '<td>' + fmtNum(ms.prev[i] || 0, 1) + '</td>' : '') + '</tr>';
    }).join('');
    var foot = '<tr><td>Итого</td>' + ms.parts.map(function (p) { return '<td>' + fmtNum(sum(p.values), 1) + '</td>'; }).join('') +
      (ms.parts.length > 1 ? '<td>' + fmtNum(ms.parts.reduce(function (t, p) { return t + sum(p.values); }, 0), 1) + '</td>' : '') +
      (ms.prev ? '<td>' + fmtNum(sum(ms.prev), 1) + '</td>' : '') + '</tr>';
    box.innerHTML = '<div class="an-table-wrap"><table class="an-table"><thead>' + head + '</thead><tbody>' + rows + '</tbody><tfoot>' + foot + '</tfoot></table></div>';
  }

  function drawHours() {
    var el = document.getElementById('an-hours');
    if (!el || !rep.calls) return;
    var hours = rep.calls.by_hour || [];
    var buckets = hours.map(function (_, h) { return { label: String(h), title: ('0' + h).slice(-2) + ':00–' + ('0' + h).slice(-2) + ':59' }; });
    V.render(el, { buckets: buckets, parts: [{ key: 'h', label: 'Входящие звонки', color: 'var(--vc-in)', values: hours }], height: 170, everyLabel: 3 });
  }

  function drawHeat() {
    var el = document.getElementById('an-heat');
    if (!el || !rep.heatmap) return;
    var grid = (rep.heatmap.grids || {})[st.heat] || [];
    var narrow = el.clientWidth < 520;
    var step = narrow ? 2 : 1, cols = 24 / step;
    var vals = [], max = 0, total = 0;
    for (var d = 0; d < 7; d++) {
      vals.push([]);
      for (var c = 0; c < cols; c++) {
        var v = 0;
        for (var h = c * step; h < c * step + step; h++) v += ((grid[d] || [])[h] || 0);
        vals[d].push(v); total += v; if (v > max) max = v;
      }
    }
    if (!total) { el.innerHTML = '<div class="an-empty">За период обращений в этом канале нет.</div>'; return; }
    var html = '<div class="an-heat" style="grid-template-columns:26px repeat(' + cols + ',minmax(0,1fr))"><span></span>';
    for (var c2 = 0; c2 < cols; c2++) {
      var hh = c2 * step;
      html += '<span class="hx">' + (hh % (narrow ? 4 : 3) === 0 ? hh : '') + '</span>';
    }
    for (var d2 = 0; d2 < 7; d2++) {
      html += '<span class="hl">' + WD[d2] + '</span>';
      for (var c3 = 0; c3 < cols; c3++) {
        var v2 = vals[d2][c3];
        var lvl = v2 ? Math.min(6, 1 + Math.floor((v2 / max) * 5.999)) : 0;
        var h0 = c3 * step, h1 = h0 + step;
        var label = WD_FULL[d2] + ', ' + ('0' + h0).slice(-2) + ':00–' + ('0' + h1).slice(-2) + ':00 — ' + v2 + ' ' + plural(v2, 'обращение', 'обращения', 'обращений');
        html += '<span class="c" tabindex="-1" style="background:' + HEAT[lvl] + '" data-hint="' + esc(label) + '" aria-label="' + esc(label) + '"></span>';
      }
    }
    html += '</div><div class="an-heat-legend">меньше' + HEAT.map(function (c) { return '<i style="background:' + c + '"></i>'; }).join('') +
      'больше<span style="margin-left:auto">всего ' + fmtNum(total) + '</span></div>';
    el.innerHTML = html;
    el.style.position = 'relative';
    var tip = document.createElement('div');
    tip.className = 'vc-tip'; tip.hidden = true;
    el.appendChild(tip);
    function show(cell) {
      if (!cell || !cell.dataset.hint) { tip.hidden = true; return; }
      tip.textContent = '';
      var t = document.createElement('div'); t.className = 'r'; t.style.whiteSpace = 'normal'; t.textContent = cell.dataset.hint;
      tip.appendChild(t);
      var r = cell.getBoundingClientRect(), pr = el.getBoundingClientRect();
      tip.hidden = false;
      var x = r.left - pr.left + r.width / 2, w = tip.offsetWidth;
      x = Math.max(w / 2, Math.min(pr.width - w / 2, x));
      tip.style.left = x + 'px'; tip.style.top = (r.top - pr.top) + 'px';
    }
    el.onpointerover = function (e) { show(e.target.closest('.c')); };
    el.onpointerdown = function (e) { show(e.target.closest('.c')); };
    el.onpointerleave = function () { tip.hidden = true; };
  }

})();
