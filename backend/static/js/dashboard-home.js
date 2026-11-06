/* /static/js/dashboard-home.js — главная кабинета Voksy AI.
 *
 * Один запрос GET /api/dashboard/summary → приветствие с тарифом, KPI, график активности за
 * 14 дней (звонки / сообщения бота), воронка CRM или список ассистентов, последние звонки,
 * подключения, чек-лист запуска и карточка Profi (для всех, кроме оплаченного Profi).
 * Ответ кэшируется в sessionStorage: повторный заход рисуется сразу, свежие данные — следом.
 * Модалки оплаты и блок подписки остаются в коде dashboard.html.
 */
(function () {
  'use strict';

  var CACHE_KEY = 'vf_dash_summary';
  var C_IN = '#2a5ce8';   // входящие (проверено validate_palette: CVD ΔE 22)
  var C_OUT = '#0d9488';  // исходящие
  var data = null;
  var chartKind = 'calls';

  function $(id) { return document.getElementById(id); }
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
  var isStart = function () { return data && (data.agent_mode === 'reactive' || data.agent_mode === 'full'); };

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
  function renderKpis() {
    var c = data.calls || {}, w = data.wallet || {}, d = data.dialogs || {}, crm = data.crm || {}, m = data.messages || {};
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
      tiles.push(kpi({
        label: 'Клиенты в CRM', icon: 'users', tone: 'green', value: num(crm.contacts_total),
        foot: '<b>+' + num(crm.contacts_new_week) + '</b> новых за неделю', href: '/static/' + (data.agent_mode === 'full' ? 'crm.html' : 'start-crm.html')
      }));
    } else if (isStart()) {
      tiles.push(kpi({
        label: 'Сообщения за неделю', icon: 'message-circle', tone: 'green', value: num((m.week_in || 0) + (m.week_out || 0)),
        foot: 'чат-бот ещё не создан', href: '/static/start-bot.html'
      }));
    } else {
      tiles.push(kpi({
        label: 'Диалоги за неделю', icon: 'messages-square', tone: 'green', value: num(d.week),
        foot: '<b>' + num(d.today) + '</b> сегодня', href: '/static/conversations.html'
      }));
    }
    var low = !w.free && (w.balance || 0) <= 0;
    tiles.push(kpi({
      label: 'Кошелёк', icon: 'wallet', tone: 'amber',
      value: w.free ? '∞' : num(Math.floor(w.balance || 0)) + '<small>сом</small>',
      foot: w.free ? 'без списаний' : (low ? '<span class="neg">Пополните, чтобы звонки работали</span>'
        : '≈ <b>' + num(w.minutes_left) + '</b> мин разговора · ' + (w.som_per_minute || 0).toString().replace('.', ',') + ' сом/мин')
    }));
    $('dh-kpis').innerHTML = tiles.join('');
  }

  // ------------------------------------------------------------------ график
  function series() {
    var src = chartKind === 'messages' ? (data.messages || {}).by_day : (data.calls || {}).by_day;
    return src || [];
  }
  function renderChart() {
    var rows = series();
    var isMsg = chartKind === 'messages';
    var names = isMsg ? ['От клиентов', 'Ответы бота и ваши'] : ['Входящие', 'Исходящие'];
    $('dh-chart-sub').textContent = (isMsg ? 'Сообщения WhatsApp, Telegram и Instagram' : 'Звонки') + ' по дням, время Бишкека';
    $('dh-legend').innerHTML = '<span><i style="background:' + C_IN + '"></i>' + names[0] + '</span><span><i style="background:' + C_OUT + '"></i>' + names[1] + '</span>';
    var box = $('dh-chart');
    var total = rows.reduce(function (s, r) { return s + r.inbound + r.outbound; }, 0);
    if (!rows.length || !total) {
      box.innerHTML = '<div class="dh-empty" style="padding:60px 0;text-align:center">' +
        (isMsg ? 'За 14 дней сообщений не было. Подключите WhatsApp, Telegram или Instagram в разделе <a href="/static/start-bot.html#channels">Чат-бот</a>.'
          : 'За 14 дней звонков не было. Подключите номер +996 в разделе <a href="/static/telephony.html">Телефония</a> или проверьте ассистента в браузере.') + '</div>';
      return;
    }
    var W = Math.max(280, box.clientWidth || 700), H = 220, padL = 28, padB = 24, padT = 8;
    var max = Math.max.apply(null, rows.map(function (r) { return r.inbound + r.outbound; }));
    var step = max <= 4 ? 1 : Math.ceil(max / 4);
    var top = step * Math.ceil(max / step);
    var ih = H - padB - padT, iw = W - padL;
    var bw = iw / rows.length, barW = Math.min(26, bw * 0.56);
    var y = function (v) { return padT + ih - (v / top) * ih; };
    var g = '<g class="grid">';
    for (var v = 0; v <= top; v += step) {
      g += '<line x1="' + padL + '" x2="' + W + '" y1="' + y(v) + '" y2="' + y(v) + '"></line><text x="' + (padL - 8) + '" y="' + (y(v) + 4) + '" text-anchor="end">' + v + '</text>';
    }
    g += '</g>';
    var cols = '', xl = '<g class="xl">';
    rows.forEach(function (r, i) {
      var cx = padL + bw * i + bw / 2, x = cx - barW / 2;
      var hIn = (r.inbound / top) * ih, hOut = (r.outbound / top) * ih;
      var base = padT + ih;
      var parts = '';
      // столбец: входящие снизу, исходящие сверху, 2px зазор, верхний конец скруглён
      if (r.inbound) parts += rect(x, base - hIn, barW, hIn, C_IN, !r.outbound);
      if (r.outbound) parts += rect(x, base - hIn - hOut - (r.inbound ? 2 : 0), barW, hOut, C_OUT, true);
      cols += '<g class="col" data-i="' + i + '"><rect class="hit" x="' + (padL + bw * i) + '" y="' + padT + '" width="' + bw + '" height="' + ih + '" rx="6"></rect>' + parts + '</g>';
      var every = W < 480 ? 3 : 2;
      if ((rows.length - 1 - i) % every === 0) {
        var d = new Date(r.date + 'T00:00:00');
        xl += '<text x="' + cx + '" y="' + (H - 6) + '" text-anchor="middle">' + d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }).replace('.', '') + '</text>';
      }
    });
    xl += '</g>';
    box.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" role="img" aria-label="' +
      esc((isMsg ? 'Сообщения' : 'Звонки') + ' за 14 дней, всего ' + total) + '">' + g + cols + xl + '</svg>';
    var tip = document.createElement('div'); tip.className = 'dh-tip'; tip.hidden = true; box.appendChild(tip);
    Array.prototype.forEach.call(box.querySelectorAll('.col'), function (col) {
      col.addEventListener('mouseenter', function () {
        var r = rows[+col.getAttribute('data-i')];
        var d = new Date(r.date + 'T00:00:00');
        tip.innerHTML = '<b>' + d.toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' }) + '</b><br>' +
          '<i style="background:' + C_IN + '"></i>' + names[0] + ': <b>' + r.inbound + '</b><br>' +
          '<i style="background:' + C_OUT + '"></i>' + names[1] + ': <b>' + r.outbound + '</b>';
        var bb = col.querySelector('.hit').getBoundingClientRect(), pb = box.getBoundingClientRect();
        tip.style.left = (bb.left - pb.left + bb.width / 2) + 'px';
        tip.style.top = (bb.top - pb.top + 6) + 'px';
        tip.hidden = false;
      });
      col.addEventListener('mouseleave', function () { tip.hidden = true; });
    });
  }
  function rect(x, y, w, h, color, roundTop) {
    if (h <= 0) return '';
    var r = Math.min(4, h, w / 2);
    if (!roundTop) return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" fill="' + color + '"></rect>';
    return '<path fill="' + color + '" d="M' + x + ',' + (y + h) + 'V' + (y + r) + 'Q' + x + ',' + y + ' ' + (x + r) + ',' + y +
      'H' + (x + w - r) + 'Q' + (x + w) + ',' + y + ' ' + (x + w) + ',' + (y + r) + 'V' + (y + h) + 'Z"></path>';
  }

  // ------------------------------------------------------------------ правая колонка: воронка или ассистенты
  function renderSide() {
    var crm = data.crm || {};
    var body = $('dh-side-body');
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
    if (data.agent_mode === 'full') { box.hidden = true; return; }
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
    var tabs = $('dh-chart-tabs');
    tabs.hidden = !isStart();
    if (!isStart()) chartKind = 'calls';
    renderChart();
    renderSide();
    renderRecent();
    renderConnect();
    renderSetup();
    renderAssistants();
    renderProfi();
    if (window.VF && window.VF.ready) try { window.VF.ready(); } catch (e) { /* ignore */ }
  }

  function load() {
    var token = null;
    try { token = localStorage.getItem('auth_token'); } catch (e) { /* ignore */ }
    if (!token) return;
    try {
      var cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || 'null');
      if (cached) { data = cached; render(); }
    } catch (e) { /* ignore */ }
    fetch('/api/dashboard/summary', { headers: { Authorization: 'Bearer ' + token } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var s = JSON.stringify(d);
        try { if (sessionStorage.getItem(CACHE_KEY) === s) return; sessionStorage.setItem(CACHE_KEY, s); } catch (e) { /* ignore */ }
        data = d; render();
      })
      .catch(function () { /* сеть: остаётся кэш или скелетоны */ });
  }

  function init() {
    $('dh-topup').addEventListener('click', openTopup);
    $('dh-upgrade').addEventListener('click', function () {
      var b = document.getElementById('upgrade-subscription-btn');
      if (b) b.click();
    });
    $('dh-chart-tabs').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-k]');
      if (!b) return;
      chartKind = b.getAttribute('data-k');
      Array.prototype.forEach.call(this.querySelectorAll('button'), function (x) { x.classList.toggle('active', x === b); });
      renderChart();
    });
    load();
    var rt = null;
    window.addEventListener('resize', function () {
      clearTimeout(rt);
      rt = setTimeout(function () { if (data) renderChart(); }, 150);
    });
    document.addEventListener('vf:wallet', function (e) {
      if (!data || !e.detail) return;
      data.wallet = Object.assign({}, data.wallet, { balance: e.detail.balance });
      renderKpis();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
