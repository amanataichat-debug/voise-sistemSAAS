/**
 * Финансы (ветка 1811-niche-admin-speed, 19.11.2026): /static/finance.html — выручка, расходы, прибыль.
 *
 * Сверху период (сегодня … год), большая карточка «Прибыль» со сравнением с прошлым периодом,
 * выручка и расходы, график по дням/неделям, расходы по категориям, зарплаты мастеров (начислено по
 * ведомости — нажатие открывает «Зарплату»), список расходов (ежемесячные и разовые).
 * «+ Расход» — шторка: категория, сумма, название, разово/каждый месяц, дата.
 * API: /api/finance/* (services/finance_service.py — та же логика, что у аналитики и главной).
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var root, esc, icon;
  var PERIODS = [['today', 'Сегодня'], ['7d', '7 дней'], ['30d', '30 дней'], ['mtd', 'Этот месяц'], ['90d', '3 месяца'], ['365d', 'Год']];
  var st = { period: '30d', data: null, chart: 'revenue' };
  var CAT_COLORS = { salary: '#2a5ce8', rent: '#eb6834', supplies: '#0d9488', ads: '#e87ba4', utilities: '#6250d6', taxes: '#7a879b', other: '#b6c0cf' };

  function money(v) { return (Number(v) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' сом'; }
  function short(v) {
    v = Number(v) || 0; var a = Math.abs(v), s = v < 0 ? '−' : '';
    if (a >= 1e6) return s + (a / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' млн';
    if (a >= 1e4) return s + Math.round(a / 1e3).toLocaleString('ru-RU') + ' тыс';
    return s + Math.round(a).toLocaleString('ru-RU');
  }
  // prev ≤ 0 (прибыль была в минусе или нулевой) — проценты бессмысленны, показываем разницу в сомах
  function delta(d, goodUp, prev) {
    if (!d) return '';
    var up = d.diff > 0, good = goodUp ? up : !up;
    if (!d.diff) return '<span class="fn-d same">как раньше</span>';
    var txt = (d.pct == null || (prev != null && prev <= 0)) ? (up ? '+' : '−') + short(Math.abs(d.diff)) : (up ? '+' : '') + d.pct + '%';
    return '<span class="fn-d ' + (good ? 'good' : 'bad') + '">' + icon(up ? 'trending-up' : 'trending-down') + txt + '</span>';
  }
  var MON = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  function dshort(k) { if (!k) return ''; var p = k.split('-'); return (+p[2]) + ' ' + MON[+p[1] - 1] + (p[0] !== String(new Date().getFullYear()) ? ' ' + p[0] : ''); }

  function readUrl() {
    var q = new URLSearchParams(location.search);
    if (q.get('period')) st.period = q.get('period');
  }
  function writeUrl() {
    var q = new URLSearchParams(location.search);
    q.set('period', st.period);
    history.replaceState(null, '', location.pathname + '?' + q.toString());
  }

  function load() {
    var path = '/api/finance/summary?period=' + encodeURIComponent(st.period);
    return S.apiSWR(path, function (r) {
      if (!r.ok) { root.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>'; S.ready(); return; }
      st.data = r.data; render(); S.ready();
    });
  }
  function reload() { S.bust(); return load(); }

  function render() {
    var d = st.data; if (!d) return;
    var profitNeg = d.profit < 0;
    var revShare = d.revenue > 0 ? Math.max(0, Math.min(100, Math.round(d.expenses / d.revenue * 100))) : 0;
    var html = '<div class="fn-periods" role="tablist">' + PERIODS.map(function (p) {
      return '<button type="button" class="fn-chip' + (p[0] === st.period ? ' on' : '') + '" data-period="' + p[0] + '">' + p[1] + '</button>';
    }).join('') + '</div>';

    // Расходы не внесены — «прибыль» равнялась бы выручке; крупно показываем выручку и честно об этом говорим
    var noExp = !d.expenses_count && !d.payroll_total;
    html += '<section class="fn-hero' + (profitNeg ? ' neg' : '') + '">' +
      '<div class="fn-hero-top"><span>' + (noExp ? 'Выручка' : 'Прибыль') + ' · ' + esc(d.period.label) + '</span>' +
      (noExp ? delta(d.revenue_delta, true, d.prev_revenue) : delta(d.profit_delta, true, d.prev_profit)) + '</div>' +
      '<div class="fn-hero-val">' + (noExp ? money(d.revenue) : (profitNeg ? '−' : '') + money(Math.abs(d.profit))) + '</div>' +
      '<div class="fn-hero-sub">' + (noExp ? 'Прибыль появится, когда внесёте расходы' :
        (d.margin_pct != null ? 'Рентабельность ' + d.margin_pct + '% · ' : '') + 'было ' + money(d.prev_profit) + ' ' + esc(d.period.compare || '')) + '</div>' +
      '<div class="fn-bar" title="Доля расходов в выручке"><i style="width:' + revShare + '%"></i></div>' +
      '<div class="fn-hero-row">' +
      '<div><span class="l"><b class="dot g"></b>Выручка</span><b>' + money(d.revenue) + '</b>' + delta(d.revenue_delta, true, d.prev_revenue) + '</div>' +
      '<div><span class="l"><b class="dot r"></b>Расходы</span><b>' + money(d.expenses) + '</b>' + delta(d.expenses_delta, false, d.prev_expenses) + '</div>' +
      '</div></section>';

    if (noExp) {
      html += '<div class="fn-hint">' + icon('sparkles') + '<div><b>Добавьте расходы — увидите настоящую прибыль</b>' +
        '<span>Аренда, зарплаты, материалы, реклама. Ежемесячные вносятся один раз.' + (d.has_masters ? ' Процент мастерам задайте ниже — зарплата посчитается сама.' : '') + '</span></div></div>';
    }

    // Откуда выручка
    var rp = d.revenue_parts || {};
    if (rp.bookings && rp.orders) {
      html += '<div class="fn-split"><div><span>Записи</span><b>' + money(rp.bookings) + '</b></div><div><span>Заказы</span><b>' + money(rp.orders) + '</b></div></div>';
    }
    // Касса (шаг 3.1): с первой смены выручка записей — по оплатам, визит без оплаты в неё не входит
    if (d.revenue_basis === 'payments' && d.revenue_since) {
      var since = new Date(d.revenue_since + 'T12:00:00+06:00').toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
      html += '<div class="fn-basis">' + icon('wallet') + '<span>Выручка записей — по оплатам в <a href="/static/cash.html">кассе</a> с ' + esc(since) +
        ' (раньше — по визитам «пришёл»). Визит без оплаты в выручку не входит.</span></div>';
    }

    if (d.series && d.series.labels && d.series.labels.length > 1) {
      html += '<section class="fn-card"><div class="fn-card-h"><h3>По ' + ({ day: 'дням', week: 'неделям', month: 'месяцам' }[d.series.step] || 'дням') + '</h3>' +
        '<div class="fn-seg">' + [['revenue', 'Выручка'], ['expenses', 'Расходы']].map(function (x) {
          return '<button type="button" data-chart="' + x[0] + '" class="' + (st.chart === x[0] ? 'on' : '') + '">' + x[1] + '</button>';
        }).join('') + '</div></div><div class="fn-chart" id="fn-chart"></div></section>';
    }

    // Расходы по категориям
    html += '<section class="fn-card"><div class="fn-card-h"><h3>Куда уходят деньги</h3><span class="muted">' + money(d.expenses) + '</span></div>';
    if (d.categories && d.categories.length) {
      html += '<div class="fn-cats">' + d.categories.map(function (c) {
        return '<div class="fn-cat"><span class="ic-w" style="background:' + (CAT_COLORS[c.key] || '#b6c0cf') + '1f;color:' + (CAT_COLORS[c.key] || '#7a879b') + '">' + icon(c.icon) + '</span>' +
          '<div class="fn-cat-m"><div class="fn-cat-t"><span>' + esc(c.title) + '</span><b>' + money(c.amount) + '</b></div>' +
          '<div class="fn-cat-bar"><i style="width:' + (c.share || 0) + '%;background:' + (CAT_COLORS[c.key] || '#b6c0cf') + '"></i></div></div>' +
          '<span class="fn-cat-p">' + (c.share || 0) + '%</span></div>';
      }).join('') + '</div>';
    } else html += '<p class="muted fn-empty">Расходов за период нет.</p>';
    html += '</section>';

    // Зарплаты мастеров — начислено по ведомости (шаг 3.3); выплаты, авансы и схема оплаты — на странице «Зарплата»
    if (d.payroll && d.payroll.length) {
      html += '<section class="fn-card"><div class="fn-card-h"><h3>Зарплаты мастеров</h3><a class="fn-link" href="' + esc(payrollHref()) + '">Ведомость' + icon('chevron-right') + '</a></div>' +
        '<p class="fn-note">Начислено за период: процент с оплаченных услуг (свой по категориям), выходы, оклад, премии и штрафы. Нажмите на мастера — откроется ведомость с выплатами.</p>' +
        '<div class="fn-list">' + d.payroll.map(function (m) {
          var how = m.configured ? m.scheme_text : 'оплата не настроена';
          return '<a class="fn-row" href="' + esc(payrollHref(m.id)) + '"><span class="ava" style="background:' + esc(m.color || '#2a5ce8') + '">' + esc(S.initial(m.name)) + '</span>' +
            '<div class="fn-row-m"><b>' + esc(m.name) + '</b><span>' + m.visits + ' визит' + plural(m.visits, '', 'а', 'ов') + ' · услуги ' + short(m.revenue) + ' · ' + esc(how) + '</span></div>' +
            '<div class="fn-row-v' + (m.configured ? '' : ' muted') + '">' + (m.configured ? money(m.salary) : 'Настроить') + '</div></a>';
        }).join('') + '</div></section>';
    }

    // Список расходов
    html += '<section class="fn-card"><div class="fn-card-h"><h3>Расходы</h3><button type="button" class="btn btn-sm" data-add>' + icon('plus') + '<span>Добавить</span></button></div>' +
      '<div id="fn-exp"><div class="skeleton" style="height:120px;border-radius:12px"></div></div></section>';

    html += '<button type="button" class="fn-fab" data-add aria-label="Добавить расход">' + icon('plus') + '<span>Расход</span></button>';
    root.innerHTML = html;
    drawChart();
    loadExpenses();
  }
  function plural(n, a, b, c) { n = Math.abs(n) % 100; var n1 = n % 10; if (n > 10 && n < 20) return c; if (n1 > 1 && n1 < 5) return b; if (n1 === 1) return a; return c; }

  function drawChart() {
    var box = document.getElementById('fn-chart'), d = st.data;
    if (!box || !window.VChart || !d.series) return;
    var se = d.series;
    window.VChart.render(box, {
      buckets: se.labels.map(function (l) { return { label: l, title: l }; }),
      parts: [{ label: st.chart === 'revenue' ? 'Выручка, сом' : 'Расходы, сом', color: st.chart === 'revenue' ? 'var(--vc-good)' : 'var(--vc-bad)', values: se[st.chart] }],
      prev: null, height: 200, emptyText: st.chart === 'revenue' ? 'Выручки за период нет.' : 'Расходов за период нет.'
    });
  }

  var expCache = null;
  function loadExpenses() {
    S.apiSWR('/api/finance/expenses', function (r) {
      var box = document.getElementById('fn-exp'); if (!box) return;
      if (!r.ok) { box.innerHTML = '<p class="muted">' + esc(S.apiError(r)) + '</p>'; return; }
      expCache = r.data;
      var list = r.data.expenses || [];
      if (!list.length) { box.innerHTML = '<p class="muted fn-empty">Пока пусто. Начните с аренды и зарплат — их достаточно внести один раз.</p>'; return; }
      var month = list.filter(function (e) { return e.repeat === 'month'; }), once = list.filter(function (e) { return e.repeat !== 'month'; });
      function rows(arr) {
        return arr.map(function (e) {
          return '<button type="button" class="fn-row" data-exp="' + esc(e.id) + '"><span class="ava cat" style="background:' + (CAT_COLORS[e.category] || '#b6c0cf') + '1f;color:' + (CAT_COLORS[e.category] || '#7a879b') + '">' + icon(e.icon) + '</span>' +
            '<div class="fn-row-m"><b>' + esc(e.title) + '</b><span>' + esc(e.category_title) + ' · ' + (e.repeat === 'month' ? 'каждый месяц с ' + dshort(e.spent_on) : dshort(e.spent_on)) + (e.is_active ? '' : ' · выключен') + '</span></div>' +
            '<div class="fn-row-v">' + money(e.amount) + (e.repeat === 'month' ? '<small>/мес</small>' : '') + '</div></button>';
        }).join('');
      }
      box.innerHTML = (month.length ? '<div class="fn-sub">Каждый месяц · ' + money(month.filter(function (e) { return e.is_active; }).reduce(function (t, e) { return t + e.amount; }, 0)) + '/мес</div><div class="fn-list">' + rows(month) + '</div>' : '') +
        (once.length ? '<div class="fn-sub">Разовые</div><div class="fn-list">' + rows(once.slice(0, 60)) + '</div>' : '');
    });
  }

  // ── шторки ──
  function expenseDialog(e) {
    var cats = (expCache && expCache.categories) || (st.data && st.data.categories_all) || [];
    var cur = e || { category: 'rent', repeat: 'month', amount: '', title: '', spent_on: new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Bishkek' }) };
    var body = '<div class="fn-form">' +
      '<div class="fn-cat-pick">' + cats.map(function (c) {
        return '<button type="button" data-cat="' + c.key + '" class="' + (c.key === cur.category ? 'on' : '') + '">' + icon(c.icon) + '<span>' + esc(c.title) + '</span></button>';
      }).join('') + '</div>' +
      '<label class="fn-f"><span>Сумма, сом</span><input id="fx-amount" type="number" inputmode="decimal" min="1" step="1" value="' + esc(cur.amount) + '" placeholder="0"></label>' +
      '<label class="fn-f"><span>Название</span><input id="fx-title" type="text" maxlength="200" value="' + esc(cur.title) + '" placeholder="Например: аренда помещения"></label>' +
      '<div class="fn-seg wide" id="fx-repeat"><button type="button" data-rep="month" class="' + (cur.repeat === 'month' ? 'on' : '') + '">Каждый месяц</button>' +
      '<button type="button" data-rep="once" class="' + (cur.repeat !== 'month' ? 'on' : '') + '">Разово</button></div>' +
      '<label class="fn-f"><span id="fx-date-l">' + (cur.repeat === 'month' ? 'Начиная с' : 'Дата') + '</span><input id="fx-date" type="date" value="' + esc(cur.spent_on || '') + '"></label>' +
      '<p class="fn-note" id="fx-help">' + (cur.repeat === 'month' ? 'В отчёт за неделю или день попадёт доля: например, неделя аренды = сумма × 7 / дней в месяце.' : 'Попадёт в отчёт в выбранный день.') + '</p>' +
      '</div>';
    var foot = (e ? '<button type="button" class="btn btn-ghost fn-del" data-del>' + icon('trash-2') + '<span>Удалить</span></button>' : '') +
      '<button type="button" class="btn btn-primary" data-save>' + (e ? 'Сохранить' : 'Добавить расход') + '</button>';
    var dlg = S.dialog({ title: e ? 'Расход' : 'Новый расход', body: body, foot: foot, size: 'md' });
    var el = dlg.el, state = { category: cur.category, repeat: cur.repeat };
    el.querySelectorAll('[data-cat]').forEach(function (b) {
      b.addEventListener('click', function () {
        el.querySelectorAll('[data-cat]').forEach(function (x) { x.classList.toggle('on', x === b); });
        state.category = b.getAttribute('data-cat');
        var t = el.querySelector('#fx-title');
        if (!t.value || t.dataset.auto) { t.value = b.textContent.trim(); t.dataset.auto = '1'; }
        if (state.category === 'rent' || state.category === 'salary' || state.category === 'utilities') setRep('month');
        if (state.category === 'supplies' || state.category === 'ads') setRep('once');
      });
    });
    function setRep(v) {
      state.repeat = v;
      el.querySelectorAll('[data-rep]').forEach(function (x) { x.classList.toggle('on', x.getAttribute('data-rep') === v); });
      el.querySelector('#fx-date-l').textContent = v === 'month' ? 'Начиная с' : 'Дата';
      el.querySelector('#fx-help').textContent = v === 'month' ? 'В отчёт за неделю или день попадёт доля: например, неделя аренды = сумма × 7 / дней в месяце.' : 'Попадёт в отчёт в выбранный день.';
    }
    el.querySelectorAll('[data-rep]').forEach(function (b) { b.addEventListener('click', function () { setRep(b.getAttribute('data-rep')); }); });
    el.querySelector('#fx-title').addEventListener('input', function () { delete this.dataset.auto; });
    if (!e) setTimeout(function () { el.querySelector('#fx-amount').focus(); }, 250);
    el.querySelector('[data-save]').addEventListener('click', function () {
      var btn = this;
      var body2 = { category: state.category, repeat: state.repeat, amount: el.querySelector('#fx-amount').value,
        title: el.querySelector('#fx-title').value, spent_on: el.querySelector('#fx-date').value };
      if (!(Number(body2.amount) > 0)) { S.toast('Укажите сумму', 'error'); el.querySelector('#fx-amount').focus(); return; }
      btn.disabled = true;
      S.api(e ? '/api/finance/expenses/' + e.id : '/api/finance/expenses', { method: e ? 'PATCH' : 'POST', body: body2 }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        dlg.close(); S.toast(e ? 'Сохранено' : 'Расход добавлен', 'success'); reload();
      });
    });
    var del = el.querySelector('[data-del]');
    if (del) del.addEventListener('click', function () {
      S.confirm({ title: 'Удалить расход?', message: e.title, confirmText: 'Удалить', danger: true }).then(function (ok) {
        if (!ok) return;
        S.api('/api/finance/expenses/' + e.id, { method: 'DELETE' }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          dlg.close(); S.toast('Удалено', 'success'); reload();
        });
      });
    });
  }

  // Ведомость за тот же период (страница «Зарплата» принимает до 92 дней; длиннее — её период по умолчанию)
  function payrollHref(id) {
    var p = (st.data && st.data.period) || {}, q = new URLSearchParams();
    if (p.from && p.to && (p.days || 0) <= 92) { q.set('from', p.from); q.set('to', p.to); }
    if (id) q.set('master', id);
    var qs = q.toString();
    return '/static/payroll.html' + (qs ? '?' + qs : '');
  }

  function bind() {
    root.addEventListener('click', function (e) {
      var p = e.target.closest('[data-period]');
      if (p) { st.period = p.getAttribute('data-period'); writeUrl(); root.querySelectorAll('[data-period]').forEach(function (x) { x.classList.toggle('on', x === p); }); load(); return; }
      var c = e.target.closest('[data-chart]');
      if (c) { st.chart = c.getAttribute('data-chart'); root.querySelectorAll('[data-chart]').forEach(function (x) { x.classList.toggle('on', x === c); }); drawChart(); return; }
      if (e.target.closest('[data-add]')) { expenseDialog(null); return; }
      var x = e.target.closest('[data-exp]');
      if (x && expCache) { var id = x.getAttribute('data-exp'); expenseDialog((expCache.expenses || []).filter(function (q) { return q.id === id; })[0]); return; }
    });
    var top = document.getElementById('fn-add-top');
    if (top) top.addEventListener('click', function () { expenseDialog(null); });
    var t;
    window.addEventListener('resize', function () { clearTimeout(t); t = setTimeout(drawChart, 150); });
  }

  function start() {
    root = document.getElementById('fn');
    esc = S.esc; icon = S.icon;
    if (S.staff && S.staff()) { location.replace('/static/inbox.html'); return; }
    readUrl(); bind();
    var q = new URLSearchParams(location.search);
    var wantNew = q.get('new') === '1';     // «Расход» с главной салона (шаг 1.7)
    if (wantNew) {
      q.delete('new');
      try { history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q.toString() : '')); } catch (e) { /* ignore */ }
    }
    load();
    if (wantNew) S.api('/api/finance/expenses').then(function (r) { if (r.ok && !expCache) expCache = r.data; expenseDialog(null); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
