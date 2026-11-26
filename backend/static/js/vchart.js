/* ============================================================================
 * /static/js/vchart.js — общие графики кабинета (ветка 1811-niche-admin-speed): страница «Аналитика»
 * и интерактивные виджеты главной рисуют одно и то же одним кодом. window.VChart:
 *
 *   render(box, o)      — столбцы (стопкой) или линия, если столбцов > 6 px не помещается;
 *                          o = { buckets:[{label,title,future}], parts:[{label,color,values}], prev:[]|null,
 *                                height, drill(i)|null, drillText, everyLabel }
 *   METRICS / KPI_METRIC — показатели графика (части стопки и цвета) и какая плитка какой показатель включает
 *   metricsAvail(rep), partValues(rep, key), metricTotal(rep, m) — по отчёту /api/analytics/report
 *   deltaHtml(kpi, compare), sparkSvg(values), valHtml(value, fmt), fmtNum, fmtAxis, fmtSec, plural, sum, cap
 *
 * Правила (skill dataviz): столбец ≤ 24 px, скруглён только верх, 2 px зазор между частями стопки,
 * серая линия — прошлый период, сетка — сплошные волоски, подписи — цветом текста, подсказка
 * на наведение / касание / стрелки клавиатуры, значения подсказки — через textContent.
 * Цвета — токены --vc-* из /static/css/vchart.css (проверены на дальтонизм).
 * ========================================================================== */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function icon(name) { return '<svg class="ic" aria-hidden="true"><use href="/static/icons/ui.svg#i-' + name + '"></use></svg>'; }
  function fmtNum(v, frac) {
    return (Number(v) || 0).toLocaleString('ru-RU', { maximumFractionDigits: frac == null ? 0 : frac });
  }
  function fmtAxis(v) {
    if (v >= 1e6) return fmtNum(v / 1e6, 1) + ' млн';
    if (v >= 1e4) return fmtNum(v / 1e3, 0) + ' тыс';
    return fmtNum(v, v % 1 ? 1 : 0);
  }
  function fmtSec(s) {
    if (s == null) return '—';
    s = Math.round(Number(s) || 0);
    if (s < 60) return s + ' с';
    if (s < 3600) { var m = Math.floor(s / 60), r = s % 60; return m + ' мин' + (r && m < 10 ? ' ' + r + ' с' : ''); }
    var h = Math.floor(s / 3600), mm = Math.round((s % 3600) / 60);
    return h + ' ч' + (mm ? ' ' + mm + ' мин' : '');
  }
  function plural(n, a, b, c) {
    n = Math.abs(n) % 100; var n1 = n % 10;
    if (n > 10 && n < 20) return c; if (n1 > 1 && n1 < 5) return b; if (n1 === 1) return a; return c;
  }
  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }
  function sum(a) { var t = 0; for (var i = 0; i < (a || []).length; i++) t += Number(a[i]) || 0; return t; }

  // Показатели графика: части стопки (ключ ряда отчёта, подпись, цвет) и ряд прошлого периода
  var METRICS = [
    { id: 'inbound', title: 'Обращения', sub: 'Сообщения клиентов и входящие звонки', need: 'bot', prev: 'inbound',
      parts: [['whatsapp_in', 'WhatsApp', 'var(--vc-wa)'], ['telegram_in', 'Telegram', 'var(--vc-tg)'],
        ['instagram_in', 'Instagram', 'var(--vc-ig)'], ['webchat_in', 'Чат на сайте', 'var(--vc-web)'],
        ['calls_in', 'Звонки', 'var(--vc-call)']] },
    { id: 'new_clients', title: 'Новые клиенты', sub: 'Сколько клиентов появилось в базе', need: 'bot', prev: 'new_clients',
      parts: [['new_clients', 'Новые клиенты', 'var(--vc-in)']] },
    { id: 'calls', title: 'Звонки', sub: 'Входящие и исходящие по телефонии', prev: 'calls',
      parts: [['calls_in', 'Входящие', 'var(--vc-in)'], ['calls_out', 'Исходящие', 'var(--vc-out)']] },
    { id: 'visits', title: 'Записи', sub: 'Визиты клиентов по дате записи', need: 'bookings', prev: 'visits',
      parts: [['arrived', 'Пришли', 'var(--vc-good)'], ['no_show', 'Не пришли', 'var(--vc-bad)'], ['_upcoming', 'Записаны', 'var(--vc-tg)']] },
    { id: 'orders', title: 'Заказы', sub: 'Заказы по дате оформления (без отменённых)', need: 'orders', prev: 'orders',
      parts: [['orders_done', 'Выданы', 'var(--vc-good)'], ['orders_open', 'В работе', 'var(--vc-book)']] },
    { id: 'replies', title: 'Ответы', sub: 'Сколько сообщений отправили клиентам бот и вы', need: 'bot',
      parts: [['bot_out', 'Бот', 'var(--vc-in)'], ['owner_out', 'Вы', 'var(--vc-out)']] },
    { id: 'dialogs', title: 'Разговоры', sub: 'Разговоры голосовых ассистентов на сайте и по телефону', need: 'nobot',
      parts: [['dialogs', 'Разговоры', 'var(--vc-in)']] }
  ];
  var KPI_METRIC = { inbound: 'inbound', new_clients: 'new_clients', calls: 'calls', visits: 'visits', revenue: 'visits',
    orders: 'orders', order_revenue: 'orders',
    reply: 'replies', dialogs: 'dialogs', won: 'new_clients', pipeline: 'new_clients', spent: 'calls' };

  function metricsAvail(rep) {
    return METRICS.filter(function (m) {
      if (m.need === 'bot') return !!rep.has_bot;
      if (m.need === 'nobot') return !rep.has_bot && !!(rep.series || {}).dialogs;
      if (m.need === 'bookings') return !!rep.bookings && !!(rep.series || {}).visits;
      if (m.need === 'orders') return !!rep.orders && !!(rep.series || {}).orders;
      return true;
    });
  }
  function metricById(rep, id) {
    var list = metricsAvail(rep);
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return list[0];
  }
  function partValues(rep, key) {
    var s = rep.series || {};
    if (key === '_upcoming') {
      return (s.visits || []).map(function (v, i) { return Math.max(0, v - ((s.arrived || [])[i] || 0) - ((s.no_show || [])[i] || 0)); });
    }
    return s[key] || [];
  }
  function metricTotal(rep, m) {
    return m.parts.reduce(function (t, p) { return t + sum(partValues(rep, p[0])); }, 0);
  }
  // Части стопки для показателя: пустые части не рисуем (кроме единственной)
  function metricParts(rep, m) {
    var parts = m.parts.map(function (p) { return { key: p[0], label: p[1], color: p[2], values: partValues(rep, p[0]) }; })
      .filter(function (p, idx, all) { return all.length === 1 || sum(p.values) > 0; });
    if (!parts.length) parts = [{ key: m.parts[0][0], label: m.parts[0][1], color: m.parts[0][2], values: partValues(rep, m.parts[0][0]) }];
    return parts;
  }

  function valHtml(v, fmt) {
    if (v == null) return '—';
    if (fmt === 'som') return fmtNum(v) + '<small>сом</small>';
    if (fmt === 'sec') return esc(fmtSec(v)).replace(/ (с|мин|ч)$/, '<small>$1</small>');
    return fmtNum(v);
  }
  // Изменение к прошлому периоду: цвет — по тому, хорошо ли это (для «времени ответа» хорошо, когда меньше)
  function deltaHtml(k) {
    var d = k && k.delta;
    if (!d) return '<span></span>';
    var was = 'Прошлый период: ' + (k.fmt === 'som' ? fmtNum(k.prev) + ' сом' : k.fmt === 'sec' ? fmtSec(k.prev) : fmtNum(k.prev));
    if (d.dir === 'same') return '<span class="vc-delta same" title="' + esc(was) + '">как раньше</span>';
    var good = (d.dir === 'up') === (k.good !== 'down');
    var txt = d.pct == null ? (d.diff > 0 ? '+' : '−') + fmtNum(Math.abs(d.diff)) : (d.dir === 'up' ? '+' : '−') + Math.abs(d.pct) + '%';
    return '<span class="vc-delta ' + (good ? 'good' : 'bad') + '" title="' + esc(was) + '">' + icon(d.dir === 'up' ? 'arrow-up' : 'arrow-down') + txt + '</span>';
  }
  function sparkSvg(vals) {
    if (!vals || vals.length < 3) return '';
    var w = 84, h = 26, n = vals.length, max = Math.max.apply(null, vals.concat([1]));
    var pts = vals.map(function (v, i) { return [(i * (w - 6) / (n - 1) + 3), (h - 4 - (v / max) * (h - 8))]; });
    var d = pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
    var last = pts[n - 1];
    return '<svg class="vc-spark" viewBox="0 0 ' + w + ' ' + h + '" aria-hidden="true"><path class="a" d="' + d + ' L' + last[0].toFixed(1) + ' ' + h +
      ' L3 ' + h + ' Z"></path><path class="l" d="' + d + '"></path><circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="3"></circle></svg>';
  }
  function legendHtml(parts, prev) {
    if (parts.length < 2 && !prev) return '';
    return parts.map(function (p) {
      return '<span><i style="background:' + p.color + '"></i>' + esc(p.label) + ' <b>' + fmtNum(sum(p.values)) + '</b></span>';
    }).join('') + (prev ? '<span><i class="line" style="background:var(--vc-prev)"></i>Прошлый период <b>' + fmtNum(sum(prev)) + '</b></span>' : '');
  }

  // ── график ─────────────────────────────────────────────────────────────
  function niceScale(max) {
    if (max <= 4) return { top: Math.max(1, Math.ceil(max)), step: 1 };
    var raw = max / 4, mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), norm = raw / mag;
    var step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
    if (step % 1) step = Math.ceil(step);
    return { top: Math.ceil(max / step) * step, step: step };
  }
  function svgEl(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    return e;
  }
  function tipRow(label, color, value) {
    var r = document.createElement('div'); r.className = 'r';
    var s = document.createElement('span');
    if (color) { var i = document.createElement('i'); i.style.background = color; s.appendChild(i); }
    s.appendChild(document.createTextNode(label));
    var b = document.createElement('b'); b.textContent = fmtNum(value, 1);
    r.appendChild(s); r.appendChild(b);
    return r;
  }
  // Столбец: скруглённый верх у последней части стопки, прямой низ
  function bar(x, yTop, w, h, color, round) {
    if (!round) return svgEl('rect', { x: x.toFixed(1), y: yTop.toFixed(1), width: w.toFixed(1), height: h.toFixed(1), style: 'fill:' + color });
    var r = Math.min(4, h, w / 2);
    var d = 'M' + x.toFixed(1) + ',' + (yTop + h).toFixed(1) + 'V' + (yTop + r).toFixed(1) + 'Q' + x.toFixed(1) + ',' + yTop.toFixed(1) + ' ' + (x + r).toFixed(1) + ',' + yTop.toFixed(1) +
      'H' + (x + w - r).toFixed(1) + 'Q' + (x + w).toFixed(1) + ',' + yTop.toFixed(1) + ' ' + (x + w).toFixed(1) + ',' + (yTop + r).toFixed(1) + 'V' + (yTop + h).toFixed(1) + 'Z';
    return svgEl('path', { d: d, style: 'fill:' + color });
  }

  function render(box, o) {
    box.innerHTML = '';
    box.classList.add('vc-chart');
    var n = o.buckets.length;
    var W = Math.max(240, box.clientWidth || 600), H = o.height || 240;
    var totals = [];
    for (var i = 0; i < n; i++) totals.push(o.parts.reduce(function (t, p) { return t + (Number(p.values[i]) || 0); }, 0));
    if (!sum(totals) && !(o.prev && sum(o.prev))) {
      box.innerHTML = '<div class="vc-empty" style="height:' + H + 'px">' + esc(o.emptyText || 'За выбранный период данных нет.') + '</div>';
      return;
    }
    var max = Math.max.apply(null, totals.concat(o.prev ? o.prev.slice(0, n) : []).concat([0]));
    var sc = niceScale(max);
    var padL = Math.max(18, String(fmtAxis(sc.top)).length * 6.6 + 8), padR = 6, padT = 10, padB = 24;
    var iw = W - padL - padR, ih = H - padT - padB;
    var band = iw / n;
    var lineMode = band < 6;
    var barW = Math.max(2, Math.min(24, band * (n > 40 ? 0.7 : 0.6)));
    var y = function (v) { return padT + ih - (v / sc.top) * ih; };
    var cx = function (k) { return padL + band * k + band / 2; };
    var svg = svgEl('svg', { width: W, height: H, viewBox: '0 0 ' + W + ' ' + H });
    for (var v = 0; v <= sc.top + 1e-9; v += sc.step) {
      var yy = Math.round(y(v)) + .5;
      svg.appendChild(svgEl('line', { 'class': v === 0 ? 'bl' : 'gl', x1: padL, x2: W - padR, y1: yy, y2: yy }));
      var t = svgEl('text', { x: padL - 6, y: yy + 3.5, 'text-anchor': 'end' });
      t.textContent = fmtAxis(v);
      svg.appendChild(t);
    }
    var maxLab = 0;
    o.buckets.forEach(function (b) { maxLab = Math.max(maxLab, String(b.label).length); });
    var every = o.everyLabel || Math.max(1, Math.ceil((maxLab * 6.4 + 10) / band));
    o.buckets.forEach(function (b, k) {
      if (o.everyLabel ? k % every !== 0 : (n - 1 - k) % every !== 0) return;
      var tx = svgEl('text', { x: cx(k), y: H - 6, 'text-anchor': 'middle' });
      tx.textContent = b.label;
      svg.appendChild(tx);
    });
    var cols = [];
    if (!lineMode) {
      for (var k = 0; k < n; k++) {
        var g = svgEl('g', { 'class': 'col' + (o.buckets[k].future ? ' future' : '') });
        g.appendChild(svgEl('rect', { 'class': 'hit', x: padL + band * k, y: padT, width: band, height: ih, rx: 4 }));
        var base = padT + ih, x0 = cx(k) - barW / 2, acc = 0;
        var visible = o.parts.filter(function (p) { return (Number(p.values[k]) || 0) > 0; });
        visible.forEach(function (p, pi) {
          var h = ((Number(p.values[k]) || 0) / sc.top) * ih;
          var gap = pi > 0 ? 2 : 0;
          g.appendChild(bar(x0, base - acc - h, barW, Math.max(1, h - gap), p.color, pi === visible.length - 1));
          acc += h;
        });
        svg.appendChild(g);
        cols.push(g);
      }
    } else {
      var col0 = o.parts.length === 1 ? o.parts[0].color : 'var(--vc-in)';
      var pts = totals.map(function (tv, k2) { return [cx(k2), y(tv)]; });
      var d = pts.map(function (p, k3) { return (k3 ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
      svg.appendChild(svgEl('path', { 'class': 'ar', d: d + ' L' + pts[n - 1][0].toFixed(1) + ' ' + (padT + ih) + ' L' + pts[0][0].toFixed(1) + ' ' + (padT + ih) + ' Z', style: 'fill:' + col0 }));
      svg.appendChild(svgEl('path', { 'class': 'ln', d: d, style: 'stroke:' + col0 }));
    }
    if (o.prev) {
      var pp = [];
      for (var q = 0; q < n; q++) if (o.prev[q] != null) pp.push((pp.length ? 'L' : 'M') + cx(q).toFixed(1) + ' ' + y(o.prev[q] || 0).toFixed(1));
      if (pp.length > 1) svg.appendChild(svgEl('path', { 'class': 'prev', d: pp.join(' ') }));
    }
    var xh = svgEl('line', { 'class': 'xh', y1: padT, y2: padT + ih, x1: -10, x2: -10 });
    xh.style.display = 'none';
    svg.appendChild(xh);
    var dot = svgEl('circle', { 'class': 'dot', r: 4, cx: -10, cy: -10 });
    dot.style.display = 'none';
    if (lineMode) { dot.style.fill = o.parts.length === 1 ? o.parts[0].color : 'var(--vc-in)'; svg.appendChild(dot); }
    box.appendChild(svg);

    var tip = document.createElement('div');
    tip.className = 'vc-tip'; tip.hidden = true;
    box.appendChild(tip);
    var active = -1, touched = -1;

    function show(k) {
      if (k < 0 || k >= n) { hide(); return; }
      active = k;
      cols.forEach(function (c, ci) { c.classList.toggle('hover', ci === k); });
      if (lineMode) {
        xh.style.display = ''; xh.setAttribute('x1', cx(k)); xh.setAttribute('x2', cx(k));
        dot.style.display = ''; dot.setAttribute('cx', cx(k)); dot.setAttribute('cy', y(totals[k]));
      }
      tip.textContent = '';
      var tt = document.createElement('div'); tt.className = 't'; tt.textContent = cap(o.buckets[k].title || o.buckets[k].label); tip.appendChild(tt);
      o.parts.forEach(function (p) { tip.appendChild(tipRow(p.label, p.color, p.values[k] || 0)); });
      if (o.parts.length > 1) tip.appendChild(tipRow('Всего', null, totals[k]));
      if (o.prev && o.prev[k] != null) tip.appendChild(tipRow('Прошлый период', 'var(--vc-prev)', o.prev[k]));
      if (o.drill && !o.buckets[k].future && totals[k]) {
        var hd = document.createElement('div'); hd.className = 'h';
        hd.textContent = touched === k ? 'Нажмите ещё раз — открыть' : (o.drillText || 'Нажмите — подробнее'); tip.appendChild(hd);
      }
      tip.hidden = false;
      var w = tip.offsetWidth;
      tip.style.left = Math.max(w / 2, Math.min(W - w / 2, cx(k))) + 'px';
      tip.style.top = Math.max(0, Math.min(y(totals[k]), padT + ih - 10)) + 'px';
    }
    function hide() {
      active = -1; tip.hidden = true;
      cols.forEach(function (c) { c.classList.remove('hover'); });
      xh.style.display = 'none'; dot.style.display = 'none';
    }
    function idxAt(ev) {
      var r = svg.getBoundingClientRect();
      var px = (ev.clientX - r.left) * (W / r.width);
      if (px < padL || px > W - padR) return -1;
      return Math.max(0, Math.min(n - 1, Math.floor((px - padL) / band)));
    }
    svg.addEventListener('pointermove', function (ev) { if (ev.pointerType === 'mouse') show(idxAt(ev)); });
    svg.addEventListener('pointerleave', function (ev) { if (ev.pointerType === 'mouse') hide(); });
    svg.addEventListener('click', function (ev) {
      var k = idxAt(ev);
      if (k < 0) return;
      var touch = ev.pointerType === 'touch' || (window.matchMedia && window.matchMedia('(hover: none)').matches);
      if (touch) {
        if (touched === k && o.drill && totals[k]) { o.drill(k); return; }
        touched = k; show(k);
        return;
      }
      if (o.drill && totals[k]) o.drill(k);
    });
    if (!box.hasAttribute('tabindex')) box.setAttribute('tabindex', '0');
    box.onkeydown = function (ev) {
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') {
        ev.preventDefault();
        var k = active < 0 ? (ev.key === 'ArrowRight' ? 0 : n - 1) : active + (ev.key === 'ArrowRight' ? 1 : -1);
        show(Math.max(0, Math.min(n - 1, k)));
      } else if (ev.key === 'Enter' && active >= 0 && o.drill && totals[active]) {
        o.drill(active);
      } else if (ev.key === 'Escape') hide();
    };
    box.onblur = hide;
  }

  window.VChart = {
    render: render, METRICS: METRICS, KPI_METRIC: KPI_METRIC, metricsAvail: metricsAvail, metricById: metricById,
    partValues: partValues, metricTotal: metricTotal, metricParts: metricParts, valHtml: valHtml, deltaHtml: deltaHtml,
    sparkSvg: sparkSvg, legendHtml: legendHtml, fmtNum: fmtNum, fmtAxis: fmtAxis, fmtSec: fmtSec, plural: plural,
    sum: sum, cap: cap, esc: esc, icon: icon
  };
})();
