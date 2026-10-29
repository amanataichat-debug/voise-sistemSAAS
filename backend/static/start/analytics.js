/* ============================================================================
 * start/analytics.js — Аналитика тарифа Старт
 * Страница /static/start-analytics.html. Считает на странице из /api/agent/stats,
 * /contacts и /calls (обращения = звонки + обработанные сообщения мессенджеров).
 * ========================================================================== */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var root, data = null, days = 30;

  var CH_COLORS = { whatsapp: '#25D366', telegram: '#229ED9', instagram: '#E1306C', call: '#2a5ce8', sms: '#16A34A' };

  document.addEventListener('DOMContentLoaded', function () {
    root = document.getElementById('page');
    S.initMobileMenu();
    S.boot().then(function (ok) {
      if (!ok) return;
      if (!S.agent) { root.innerHTML = S.noBotHtml(); S.ready(); return; }
      // Повторный заход: цифры рисуются сразу из кэша, свежие подтягиваются в фоне
      var cached = S.cacheGet('analytics');
      if (cached) { data = cached; draw(); S.ready(); }
      return load().then(function (fresh) {
        if (cached && JSON.stringify(fresh) === JSON.stringify(cached)) return;
        data = fresh; draw(); S.ready();
      });
    });
  });

  // calls отдаёт максимум 100 за запрос — берём до трёх страниц, все три сразу
  function loadCalls() {
    return Promise.all([0, 100, 200].map(function (offset) {
      return S.api('/calls?limit=100&offset=' + offset).then(function (r) { return r.ok ? (r.data.calls || []) : []; });
    })).then(function (pages) { return [].concat.apply([], pages); });
  }

  function load() {
    var g = S.cacheGen();
    return Promise.all([S.api('/stats'), S.api('/contacts?limit=200&offset=0'), loadCalls()]).then(function (res) {
      var fresh = {
        stats: res[0].ok ? res[0].data : {},
        contacts: res[1].ok ? (res[1].data.contacts || []) : [],
        contactsTotal: res[1].ok ? (res[1].data.total || 0) : 0,
        events: res[2]
      };
      S.cacheSet('analytics', fresh, g);
      return fresh;
    });
  }

  function since() { return Date.now() - days * 86400000; }

  function draw() {
    var from = since();
    var contacts = data.contacts, events = data.events;
    var newClients = contacts.filter(function (c) { return new Date(c.created_at) >= from; });
    var evs = events.filter(function (e) { return new Date(e.created_at) >= from; });
    var inbound = evs.filter(function (e) { return e.channel !== 'call' || e.direction === 'inbound'; });
    var calls = evs.filter(function (e) { return e.channel === 'call'; });
    var answered = calls.filter(function (e) { return e.status === 'answered'; });
    var won = contacts.filter(function (c) { return S.stageOf(c.status).kind === 'won'; });
    var open = contacts.filter(function (c) { return S.stageOf(c.status).kind === 'open'; });
    var sum = function (arr) { return arr.reduce(function (a, c) { return a + (Number(c.deal_amount) || 0); }, 0); };
    var conv = data.contactsTotal ? Math.round(won.length / Math.max(contacts.length, 1) * 100) : 0;
    var talkSec = answered.reduce(function (a, e) { return a + (Number(e.duration_seconds) || 0); }, 0);

    root.innerHTML =
      '<div class="st-head"><div><h2>Аналитика</h2><p>Как работает бот: сколько обращений, откуда клиенты и что с ними происходит.</p></div>' +
      '<div class="st-period" id="period">' + [7, 30, 90].map(function (d) {
        return '<button type="button" data-d="' + d + '" class="' + (d === days ? 'active' : '') + '">' + d + ' дн.</button>';
      }).join('') + '</div></div>' +

      '<div class="st-kpis">' +
      kpi('users', 'Клиентов в базе', data.stats.total_contacts || data.contactsTotal || 0, 'новых за период: ' + newClients.length) +
      kpi('messages-square', 'Обращений за период', inbound.length, 'сообщения и звонки клиентов') +
      kpi('phone-incoming', 'Принято звонков', calls.filter(function (e) { return e.direction === 'inbound'; }).length, answered.length ? 'разговоров: ' + S.fmtDur(talkSec) : 'пока без звонков') +
      kpi('circle-check', 'Выиграно сделок', won.length, conv ? conv + '% от клиентов' : 'пока нет') +
      kpi('banknote', 'Сумма сделок', S.fmtMoney(sum(won)) || '0 сом', 'в работе: ' + (S.fmtMoney(sum(open)) || '0 сом')) +
      '</div>' +

      '<div class="st-charts">' +
      '<div class="card"><div class="card-body"><h3 class="st-chart-title">Обращения по дням</h3>' +
      '<p class="st-chart-sub">Сколько раз клиенты писали и звонили, и сколько новых клиентов пришло</p>' +
      lineChart(evs, newClients) + '</div></div>' +
      '<div class="card"><div class="card-body"><h3 class="st-chart-title">Откуда обращения</h3>' +
      '<p class="st-chart-sub">По каналам за период</p>' + channelDonut(inbound) + '</div></div></div>' +

      '<div class="st-charts two">' +
      '<div class="card"><div class="card-body"><h3 class="st-chart-title">Воронка</h3>' +
      '<p class="st-chart-sub">Клиенты по стадиям</p>' + funnel(contacts) + '</div></div>' +
      '<div class="card"><div class="card-body"><h3 class="st-chart-title">Когда пишут</h3>' +
      '<p class="st-chart-sub">Обращения по часам суток (Бишкек)</p>' + hoursChart(inbound) + '</div></div></div>' +

      '<div class="st-charts two">' +
      '<div class="card"><div class="card-body"><h3 class="st-chart-title">Итоги звонков</h3>' +
      '<p class="st-chart-sub">Что решил бот после разговора</p>' + decisionsDonut(calls) + '</div></div>' +
      '<div class="card"><div class="card-body"><h3 class="st-chart-title">Теги клиентов</h3>' +
      '<p class="st-chart-sub">Самые частые теги в карточках</p>' + tagsBars(contacts) + '</div></div></div>' +
      (data.contactsTotal > contacts.length ? '<p class="muted small">Воронка, теги и суммы посчитаны по последним ' + contacts.length + ' клиентам из ' + data.contactsTotal + '.</p>' : '');

    S.$$('#period button').forEach(function (b) {
      b.addEventListener('click', function () { days = parseInt(b.dataset.d, 10); draw(); });
    });
  }

  function kpi(ic, label, val, sub) {
    return '<div class="card st-kpi"><div class="lbl">' + icon(ic) + esc(label) + '</div><div class="val">' + esc(val) + '</div><div class="sub">' + esc(sub || '') + '</div></div>';
  }

  // ── Графики ─────────────────────────────────────────────────────────────
  function dayList() {
    var out = [], d = new Date();
    for (var i = days - 1; i >= 0; i--) out.push(S.dayKey(new Date(d.getTime() - i * 86400000).toISOString()));
    return out;
  }
  function countBy(items, keyFn) {
    var m = {}; items.forEach(function (x) { var k = keyFn(x); m[k] = (m[k] || 0) + 1; }); return m;
  }

  function lineChart(evs, clients) {
    var keys = dayList();
    var a = countBy(evs, function (e) { return S.dayKey(e.created_at); });
    var b = countBy(clients, function (c) { return S.dayKey(c.created_at); });
    var av = keys.map(function (k) { return a[k] || 0; }), bv = keys.map(function (k) { return b[k] || 0; });
    var max = Math.max(4, Math.max.apply(null, av.concat(bv)));
    if (!evs.length && !clients.length) return '<div class="st-empty">За выбранный период обращений не было.</div>';
    var W = 640, H = 190, pl = 28, pr = 8, pt = 10, pb = 24, iw = W - pl - pr, ih = H - pt - pb;
    var x = function (i) { return pl + (keys.length === 1 ? iw / 2 : i * iw / (keys.length - 1)); };
    var y = function (v) { return pt + ih - v / max * ih; };
    var path = function (vals) { return vals.map(function (v, i) { return (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1); }).join(' '); };
    var grid = [0, .5, 1].map(function (f) {
      var yy = pt + ih - f * ih;
      return '<line class="grid" x1="' + pl + '" x2="' + (W - pr) + '" y1="' + yy + '" y2="' + yy + '"/><text class="lbl" x="' + (pl - 6) + '" y="' + (yy + 3) + '" text-anchor="end">' + Math.round(f * max) + '</text>';
    }).join('');
    var step = Math.ceil(keys.length / 7);
    var xl = keys.map(function (k, i) {
      return i % step === 0 ? '<text class="lbl" x="' + x(i) + '" y="' + (H - 6) + '" text-anchor="middle">' + k.slice(8) + '.' + k.slice(5, 7) + '</text>' : '';
    }).join('');
    var area = path(av) + ' L' + x(keys.length - 1).toFixed(1) + ' ' + (pt + ih) + ' L' + x(0).toFixed(1) + ' ' + (pt + ih) + ' Z';
    return '<svg class="st-spark" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="Обращения по дням">' + grid + xl +
      '<path d="' + area + '" style="fill:var(--vf-accent);opacity:.1"/>' +
      '<path d="' + path(av) + '" style="fill:none;stroke:var(--vf-accent);stroke-width:2.2;stroke-linejoin:round" vector-effect="non-scaling-stroke"/>' +
      '<path d="' + path(bv) + '" style="fill:none;stroke:var(--vf-success);stroke-width:2.2;stroke-linejoin:round" vector-effect="non-scaling-stroke"/></svg>' +
      '<div class="st-legend"><span><i style="--c:var(--vf-accent)"></i>Обращения</span><span><i style="--c:var(--vf-success)"></i>Новые клиенты</span></div>';
  }

  function donut(items, label) {
    var total = items.reduce(function (a, i) { return a + i.v; }, 0);
    if (!total) return '<div class="st-empty">Данных пока нет.</div>';
    var R = 70, r = 46, cx = 80, cy = 80, acc = 0;
    var arcs = items.filter(function (i) { return i.v > 0; }).map(function (i) {
      var s = acc / total * 360; acc += i.v; var e = acc / total * 360;
      if (e - s >= 359.99) e = s + 359.99;
      var p = function (rad, deg) { var a = (deg - 90) * Math.PI / 180; return [cx + rad * Math.cos(a), cy + rad * Math.sin(a)]; };
      var p1 = p(R, e), p2 = p(R, s), p3 = p(r, s), p4 = p(r, e), big = e - s > 180 ? 1 : 0;
      return '<path d="M' + p1 + ' A' + R + ' ' + R + ' 0 ' + big + ' 0 ' + p2 + ' L' + p3 + ' A' + r + ' ' + r + ' 0 ' + big + ' 1 ' + p4 + ' Z" fill="' + i.c + '"><title>' + esc(i.l) + ': ' + i.v + '</title></path>';
    }).join('');
    return '<div class="st-donut-wrap"><svg viewBox="0 0 160 160" width="160" height="160" role="img" aria-label="' + esc(label) + '">' + arcs +
      '<text x="80" y="78" text-anchor="middle" style="font-size:22px;font-weight:700;fill:var(--vf-text)">' + total + '</text>' +
      '<text x="80" y="95" text-anchor="middle" style="font-size:11px;fill:var(--vf-text-4)">' + esc(label) + '</text></svg>' +
      '<div class="st-donut-legend">' + items.map(function (i) {
        return '<div><i style="--c:' + i.c + '"></i><span>' + esc(i.l) + '</span><b>' + i.v + ' · ' + Math.round(i.v / total * 100) + '%</b></div>';
      }).join('') + '</div></div>';
  }

  function channelDonut(inbound) {
    var m = countBy(inbound, function (e) { return e.channel || 'call'; });
    var items = Object.keys(m).map(function (k) {
      return { l: (S.CHANNELS[k] || { label: k }).label, v: m[k], c: CH_COLORS[k] || '#6B7280' };
    }).sort(function (a, b) { return b.v - a.v; });
    return donut(items, 'обращений');
  }

  function decisionsDonut(calls) {
    var map = { SUCCESS: ['Успех', '#10B981'], FOLLOWUP: ['Нужен перезвон', '#3B82F6'], NO_ANSWER: ['Не ответил', '#F59E0B'], REJECTED: ['Отказ', '#EF4444'] };
    var m = countBy(calls.filter(function (e) { return e.post_call_decision; }), function (e) { return e.post_call_decision; });
    var items = Object.keys(m).map(function (k) { return { l: (map[k] || [k])[0], v: m[k], c: (map[k] || [0, '#6B7280'])[1] }; });
    return donut(items, 'звонков');
  }

  function funnel(contacts) {
    if (!contacts.length) return '<div class="st-empty">Клиентов пока нет.</div>';
    var m = countBy(contacts, function (c) { return c.status; });
    var max = Math.max.apply(null, S.stages.map(function (s) { return m[s.key] || 0; }).concat([1]));
    return '<div class="st-bars">' + S.stages.map(function (s) {
      var n = m[s.key] || 0;
      return '<div class="st-bar-row"><span class="l">' + esc(s.label) + '</span><div class="track"><div class="fill" style="width:' + (n / max * 100) + '%;--c:' + esc(s.color) + '"></div></div><span class="v">' + n + '</span></div>';
    }).join('') + '</div>';
  }

  function hoursChart(inbound) {
    if (!inbound.length) return '<div class="st-empty">Данных пока нет.</div>';
    var hours = new Array(24).fill(0);
    inbound.forEach(function (e) {
      var h = parseInt(new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Bishkek', hour: '2-digit', hour12: false }).format(new Date(e.created_at)), 10);
      if (!isNaN(h)) hours[h % 24]++;
    });
    var max = Math.max.apply(null, hours.concat([1]));
    var W = 640, H = 170, pl = 6, pb = 22, pt = 8, bw = (W - pl * 2) / 24;
    var bars = hours.map(function (v, i) {
      var h = v / max * (H - pt - pb);
      return '<rect x="' + (pl + i * bw + 2) + '" y="' + (H - pb - h) + '" width="' + (bw - 4) + '" height="' + Math.max(h, v ? 2 : 0) + '" rx="3" style="fill:var(--vf-accent);opacity:' + (v ? .85 : .15) + '"><title>' + i + ':00 — ' + v + '</title></rect>' +
        (i % 3 === 0 ? '<text class="lbl" x="' + (pl + i * bw + bw / 2) + '" y="' + (H - 6) + '" text-anchor="middle">' + i + '</text>' : '');
    }).join('');
    return '<svg class="st-spark" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="Обращения по часам">' + bars + '</svg>';
  }

  function tagsBars(contacts) {
    var m = {};
    contacts.forEach(function (c) { (c.tags || []).forEach(function (t) { m[t] = (m[t] || 0) + 1; }); });
    var keys = Object.keys(m).sort(function (a, b) { return m[b] - m[a]; }).slice(0, 8);
    if (!keys.length) return '<div class="st-empty">Тегов пока нет. Бот и вы можете ставить теги в карточке клиента.</div>';
    var max = m[keys[0]];
    return '<div class="st-bars">' + keys.map(function (k) {
      return '<div class="st-bar-row"><span class="l">' + esc(k) + '</span><div class="track"><div class="fill" style="width:' + (m[k] / max * 100) + '%"></div></div><span class="v">' + m[k] + '</span></div>';
    }).join('') + '</div>';
  }
})();
