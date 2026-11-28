/**
 * Зарплата мастеров (Voksy Beauty, шаг 3.3): /static/payroll.html.
 *
 * Ведомость за период (сегодня, неделя, половины месяца, месяц, прошлый месяц или свои даты) — салон сам выбирает,
 * как часто платит. По каждому мастеру: услуги (база процента — оплаченные услуги, до кассы — визиты «пришёл»),
 * % (свой по категориям), выходы × ставка, доля оклада, премии − штрафы = начислено; минус авансы и выплаты =
 * к выплате. «Выплатить» — наличными из кассы (изъятие в смене того, кто платит) или переводом; одно окно = один
 * ключ операции, повтор нажатия не платит дважды. Панель мастера: как посчитано, выходы, аванс / штраф / премия,
 * выплаты, «Оплата мастера» (общий %, свой % по категориям, ставка за выход, оклад).
 * Адрес: ?from=&to= — период, ?master=<id> — открыть мастера. API: /api/payroll/* (services/payroll.py).
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var P = function () { return window.BookingPay; };
  var TZ = 'Asia/Bishkek';
  var MON = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  var MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  var KINDS = [['advance', 'Аванс', 'hand-coins'], ['bonus', 'Премия', 'gift'], ['fine', 'Штраф', 'minus']];
  var KIND = { advance: 'Аванс', bonus: 'Премия', fine: 'Штраф' };
  var PAY_METHODS = [['cash', 'Наличными из кассы', 'banknote'], ['transfer', 'Переводом', 'smartphone']];
  var FINE_REASONS = ['Опоздание', 'Неявка без предупреждения', 'Жалоба клиента'];
  var BONUS_REASONS = ['Хорошие отзывы', 'План выполнен', 'Праздник'];
  var root, esc, icon;
  var st = { preset: 'month', from: null, to: null, data: null, open: null, dr: null };

  function money(v) { return (Math.round(Number(v || 0))).toLocaleString('ru-RU') + ' сом'; }
  function num(v) { return P().num(v); }
  function keyOf(d) { return d.toLocaleDateString('en-CA', { timeZone: TZ }); }
  function today() { return keyOf(new Date()); }
  function parts(k) { var p = k.split('-'); return [+p[0], +p[1], +p[2]]; }
  function mk(y, m, d) { return y + '-' + String(m).padStart(2, '0') + '-' + String(d).padStart(2, '0'); }
  function lastDay(y, m) { return new Date(Date.UTC(y, m, 0)).getUTCDate(); }
  function addDays(k, n) { var d = new Date(k + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
  function dm(k) { var p = parts(k); return p[2] + ' ' + MON[p[1] - 1]; }
  function plural(n, a, b, c) { n = Math.abs(n) % 100; var n1 = n % 10; if (n > 10 && n < 20) return c; if (n1 > 1 && n1 < 5) return b; if (n1 === 1) return a; return c; }
  function errOf(r) { return P().errOf(r); }

  // ── периоды ──
  function presets() {
    var t = today(), p = parts(t), y = p[0], m = p[1], d = p[2];
    var dow = (new Date(t + 'T12:00:00Z').getUTCDay() + 6) % 7;            // пн = 0
    var py = m === 1 ? y - 1 : y, pm = m === 1 ? 12 : m - 1;
    var out = [['today', 'Сегодня', t, t], ['week', 'Эта неделя', addDays(t, -dow), addDays(t, 6 - dow)]];
    if (d >= 16) {
      out.push(['h1', '1–15 ' + MON[m - 1], mk(y, m, 1), mk(y, m, 15)]);
      out.push(['h2', '16–' + lastDay(y, m) + ' ' + MON[m - 1], mk(y, m, 16), mk(y, m, lastDay(y, m))]);
    } else {
      out.push(['h2', '16–' + lastDay(py, pm) + ' ' + MON[pm - 1], mk(py, pm, 16), mk(py, pm, lastDay(py, pm))]);
      out.push(['h1', '1–15 ' + MON[m - 1], mk(y, m, 1), mk(y, m, 15)]);
    }
    out.push(['month', MONTHS[m - 1], mk(y, m, 1), mk(y, m, lastDay(y, m))]);
    out.push(['prev', MONTHS[pm - 1], mk(py, pm, 1), mk(py, pm, lastDay(py, pm))]);
    return out;
  }
  function setPreset(key) {
    var p = presets().filter(function (x) { return x[0] === key; })[0];
    if (!p) return false;
    st.preset = key; st.from = p[2]; st.to = p[3];
    return true;
  }
  function readUrl() {
    var q = new URLSearchParams(location.search);
    var f = q.get('from'), t = q.get('to');
    if (f && /^\d{4}-\d{2}-\d{2}$/.test(f) && t && /^\d{4}-\d{2}-\d{2}$/.test(t)) {
      var hit = presets().filter(function (x) { return x[2] === f && x[3] === t; })[0];
      st.preset = hit ? hit[0] : 'custom'; st.from = f; st.to = t;
    } else setPreset('month');
    st.open = q.get('master') || null;
  }
  function writeUrl() {
    var q = new URLSearchParams(location.search);
    q.set('from', st.from); q.set('to', st.to);
    if (st.open) q.set('master', st.open); else q.delete('master');
    try { history.replaceState(null, '', location.pathname + '?' + q.toString()); } catch (e) { /* ignore */ }
  }

  // ── загрузка ──
  function load() {
    return S.api('/api/payroll/sheet?from=' + st.from + '&to=' + st.to).then(function (r) {
      if (!r.ok) {
        st.data = null;
        root.innerHTML = periodsHtml() + '<div class="st-empty">' + esc(r.status === 403
          ? 'Зарплату ведут владелец и бухгалтер (раздел «Финансы»). Выход мастера администратор отмечает в журнале записи.'
          : S.apiError(r)) + '</div>';
        S.ready();
        return;
      }
      st.data = r.data;
      draw();
    });
  }
  function reload() {
    return load().then(function () { if (st.dr && st.open) fillDrawer(st.open); });
  }

  // ── отрисовка ──
  function periodsHtml() {
    var html = '<div class="pr-periods" role="tablist">' + presets().map(function (p) {
      return '<button type="button" class="st-colchip' + (st.preset === p[0] ? ' on' : '') + '" data-preset="' + p[0] + '">' + esc(p[1]) + '</button>';
    }).join('') + '<button type="button" class="st-colchip' + (st.preset === 'custom' ? ' on' : '') + '" data-preset="custom">' + icon('calendar') + 'Свой период</button></div>';
    if (st.preset === 'custom') {
      html += '<div class="pr-custom"><label>С <input type="date" class="input" id="pr-from" value="' + esc(st.from) + '"></label>' +
        '<label>по <input type="date" class="input" id="pr-to" value="' + esc(st.to) + '"></label>' +
        '<button class="btn btn-sm" type="button" data-apply>Показать</button></div>';
    }
    return html;
  }
  function draw() {
    var d = st.data;
    if (!d) return;
    var t = d.totals || {}, ms = d.masters || [];
    var html = periodsHtml();
    html += '<section class="pr-card pr-sum"><div class="pr-sum-main"><span>К выплате · ' + esc(d.label) + '</span>' +
      '<b class="' + (t.to_pay < -0.5 ? 'neg' : '') + '">' + esc(money(t.to_pay)) + '</b>' +
      '<small>начислено ' + esc(money(t.accrued)) + (t.advances ? ' · авансы ' + esc(money(t.advances)) : '') +
      (t.paid ? ' · выплачено ' + esc(money(t.paid)) : '') + '</small></div>' +
      '<div class="pr-sum-tiles">' +
      tile('% с услуг', t.percent_amount) + tile('Выходы', t.shifts) + tile('Оклады', t.fixed_amount) + tile('Премии − штрафы', (t.bonuses || 0) - (t.fines || 0), true) +
      '</div></section>';
    if (!ms.length) {
      html += '<section class="pr-card"><div class="pr-empty">' + icon('users') + '<b>Мастеров пока нет</b>Добавьте мастеров в журнале записи — кнопка «Мастера».' +
        '<a class="btn btn-sm" href="/static/booking.html">Открыть журнал записи</a></div></section>';
    } else {
      html += '<section class="pr-card pr-list"><div class="pr-head"><span>Мастер</span><span>Услуги</span><span>Начислено</span><span>Авансы и выплаты</span><span>К выплате</span><span></span></div>' +
        ms.map(rowHtml).join('') + '</section>';
    }
    html += basisNote();
    root.innerHTML = html;
    S.ready();
  }
  function tile(label, v, signed) {
    v = Number(v || 0);
    return '<div class="pr-tile' + (Math.abs(v) < 0.5 ? ' zero' : '') + '"><span>' + esc(label) + '</span><b>' +
      (signed && v < -0.5 ? '−' + esc(money(-v)) : (signed && v > 0.5 ? '+' : '') + esc(money(v))) + '</b></div>';
  }
  function partsLine(m) {
    var out = [];
    if (m.percent_amount) out.push('% ' + money(m.percent_amount));
    if (m.shifts.count) out.push(m.shifts.count + ' ' + plural(m.shifts.count, 'выход', 'выхода', 'выходов'));
    if (m.fixed_amount) out.push('оклад ' + money(m.fixed_amount));
    if (m.bonuses) out.push('премии +' + money(m.bonuses));
    if (m.fines) out.push('штрафы −' + money(m.fines));
    return out.join(' · ');
  }
  function rowHtml(m) {
    var given = (m.advances || 0) + (m.paid || 0);
    var act;
    if (!m.configured && !m.accrued) act = '<button class="btn btn-sm" type="button" data-scheme="' + esc(m.id) + '">' + icon('settings') + 'Настроить</button>';
    else if (m.to_pay > 0.5) act = '<button class="btn btn-primary btn-sm" type="button" data-pay="' + esc(m.id) + '">' + icon('hand-coins') + 'Выплатить</button>';
    else if (m.closed) act = '<span class="py-chip ok">Выплачено</span>';
    else if (m.to_pay < -0.5) act = '<span class="py-chip warn">Переплата</span>';
    else act = '<span class="muted small">Нечего выплачивать</span>';
    return '<div class="pr-row' + (m.active ? '' : ' off') + '">' +
      '<button type="button" class="pr-who" data-open="' + esc(m.id) + '"><span class="ava" style="background:' + esc(m.color || '#2a5ce8') + '">' + esc(S.initial(m.name)) + '</span>' +
      '<span class="pr-who-t"><b>' + esc(m.name) + (m.active ? '' : ' <i>выключен</i>') + '</b><span>' + esc(m.configured ? m.scheme_text : 'Оплата не настроена') + '</span></span></button>' +
      '<div class="pr-c"><span class="pr-l">Услуги</span><b>' + esc(money(m.base)) + '</b><small>' + m.visits + ' ' + plural(m.visits, 'визит', 'визита', 'визитов') + '</small></div>' +
      '<div class="pr-c"><span class="pr-l">Начислено</span><b>' + esc(money(m.accrued)) + '</b><small>' + esc(partsLine(m) || '—') + '</small></div>' +
      '<div class="pr-c"><span class="pr-l">Авансы и выплаты</span><b' + (given ? '' : ' class="zero"') + '>' + (given ? '−' : '') + esc(money(given)) + '</b></div>' +
      '<div class="pr-c pay"><span class="pr-l">К выплате</span><b class="' + (m.to_pay < -0.5 ? 'neg' : m.to_pay > 0.5 ? 'due' : 'zero') + '">' + esc(money(m.to_pay)) + '</b></div>' +
      '<div class="pr-act">' + act + '</div></div>';
  }
  function basisNote() {
    var d = st.data;
    return '<div class="pr-note">' + icon('info') + '<span>' + (d.basis === 'payments'
      ? 'Процент считается с оплаченных услуг мастера: деньги кассы по его записям минус возвраты (с ' + esc(dm(d.basis_since)) + '). Неоплаченный визит — долг клиента, в зарплату не идёт, пока его не оплатят.'
      : 'Пока касса не открывалась, процент считается с визитов «пришёл / завершён» по цене записи. С первой смены в кассе — с оплаченных услуг.') +
      ' Материалы из процента не вычитаются. Выход мастера отмечает администратор в журнале записи — «Кто сегодня работает».</span></div>';
  }
  function lineOf(id) { return ((st.data && st.data.masters) || []).filter(function (x) { return x.id === id; })[0]; }

  // ── панель мастера ──
  function openMaster(id, fromUrl) {
    st.open = id;
    if (!fromUrl) writeUrl();
    st.dr = S.drawer({ title: '<div style="font-weight:700">Мастер</div>', onClose: function () { st.dr = null; st.open = null; writeUrl(); } });
    st.dr.body.classList.add('pr-dr');
    st.dr.body.innerHTML = '<div class="skeleton" style="height:260px;border-radius:12px"></div>';
    fillDrawer(id);
  }
  function fillDrawer(id) {
    var dr = st.dr;
    S.api('/api/payroll/masters/' + id + '?from=' + st.from + '&to=' + st.to).then(function (r) {
      if (dr !== st.dr) return;
      if (!r.ok || !r.data.line) { dr.body.innerHTML = '<div class="muted">' + esc(r.ok ? 'За этот период мастера нет в ведомости.' : S.apiError(r)) + '</div>'; return; }
      var m = r.data.line;
      st.cats = r.data.categories || [];
      st.canCash = !!r.data.can_cash;
      dr.setTitle('<div class="pr-dr-t"><span class="ava" style="background:' + esc(m.color || '#2a5ce8') + '">' + esc(S.initial(m.name)) + '</span>' +
        '<div><div style="font-weight:700">' + esc(m.name) + '</div><div class="muted small">' + esc(r.data.label) + ' · ' + esc(m.configured ? m.scheme_text : 'оплата не настроена') + '</div></div></div>');
      dr.body.innerHTML = drawerHtml(m);
      S.$$('[data-dpay]', dr.body).forEach(function (b) { b.addEventListener('click', function () { payDialog(m); }); });
      S.$$('[data-dadj]', dr.body).forEach(function (b) { b.addEventListener('click', function () { adjDialog(m, b.getAttribute('data-dadj')); }); });
      S.$$('[data-dscheme]', dr.body).forEach(function (b) { b.addEventListener('click', function () { schemeDialog(m); }); });
      S.$$('[data-ddel]', dr.body).forEach(function (b) { b.addEventListener('click', function () { delAdj(m, b.getAttribute('data-ddel')); }); });
    });
  }
  function drawerHtml(m) {
    var html = '<div class="pr-dsum"><div><span>К выплате</span><b class="' + (m.to_pay < -0.5 ? 'neg' : '') + '">' + esc(money(m.to_pay)) + '</b>' +
      (m.to_pay < -0.5 ? '<small class="neg">Выдали больше, чем начислено: разница уйдёт в следующий период</small>' : m.closed ? '<small class="ok">За период всё выплачено</small>' : '') + '</div>' +
      (m.to_pay > 0.5 ? '<button class="btn btn-primary" type="button" data-dpay>' + icon('hand-coins') + 'Выплатить</button>' : '') + '</div>';
    html += '<div class="pr-dacts">' + KINDS.map(function (k) {
      return '<button class="btn btn-sm" type="button" data-dadj="' + k[0] + '">' + icon(k[2]) + k[1] + '</button>';
    }).join('') + '<button class="btn btn-ghost btn-sm" type="button" data-dscheme>' + icon('settings') + 'Оплата мастера</button></div>';
    // как посчитано
    html += '<h4 class="pr-dh">Как посчитано</h4><div class="py-parts pr-calc">';
    (m.by_cat || []).forEach(function (c) {
      html += '<div><span>' + esc(c.category) + ' · ' + esc(money(c.sum)) + ' × ' + esc(String(c.pct).replace('.', ',')) + '%' + (c.own ? ' <i>свой %</i>' : '') + '</span><b>' + esc(money(c.amount)) + '</b></div>';
    });
    if (!(m.by_cat || []).length) html += '<div><span>Услуги за период</span><b>' + esc(money(0)) + '</b></div>';
    if (m.shifts.rate || m.shifts.count) html += '<div><span>Выходы · ' + m.shifts.count + ' × ' + esc(money(m.shifts.rate)) + '</span><b>' + esc(money(m.shifts.amount)) + '</b></div>';
    if (m.scheme.fixed) html += '<div><span>Оклад ' + esc(money(m.scheme.fixed)) + ' в месяц · доля за период</span><b>' + esc(money(m.fixed_amount)) + '</b></div>';
    if (m.bonuses) html += '<div><span>Премии</span><b>+' + esc(money(m.bonuses)) + '</b></div>';
    if (m.fines) html += '<div><span>Штрафы</span><b class="neg">−' + esc(money(m.fines)) + '</b></div>';
    html += '<div class="tot"><span>Начислено</span><b>' + esc(money(m.accrued)) + '</b></div>';
    if (m.advances) html += '<div><span>Авансы</span><b class="neg">−' + esc(money(m.advances)) + '</b></div>';
    if (m.paid) html += '<div><span>Выплачено за период</span><b class="neg">−' + esc(money(m.paid)) + '</b></div>';
    html += '<div class="tot"><span>К выплате</span><b>' + esc(money(m.to_pay)) + '</b></div></div>';
    html += '<div class="pr-dmeta">' + m.visits + ' ' + plural(m.visits, 'визит', 'визита', 'визитов') + ' за период · услуги ' + esc(money(m.base)) + '</div>';
    // выходы
    if (m.scheme.shift || m.shifts.count) {
      html += '<h4 class="pr-dh">Выходы</h4>' + (m.shifts.count
        ? '<div class="pr-days">' + m.shifts.days.map(function (k) { return '<span class="chip">' + esc(dm(k)) + '</span>'; }).join('') + '</div>'
        : '<div class="pr-dmeta">Выходов за период не отмечено. Отмечает администратор в журнале записи — «Кто сегодня работает».</div>');
    }
    // ручные строки
    html += '<h4 class="pr-dh">Авансы, премии и штрафы</h4>';
    if (!(m.adjustments || []).length) html += '<div class="pr-dmeta">За период нет.</div>';
    else html += '<div class="pr-adjs">' + m.adjustments.map(function (a) {
      var minus = a.kind !== 'bonus';
      return '<div class="pr-adj ' + esc(a.kind) + '"><span class="pr-adj-ic">' + icon(a.kind === 'advance' ? 'hand-coins' : a.kind === 'bonus' ? 'gift' : 'minus') + '</span>' +
        '<div class="pr-adj-m"><b>' + esc(a.title) + ' · ' + esc(dm(a.day)) + '</b>' + (a.comment ? '<span>' + esc(a.comment) + '</span>' : '') + (a.by ? '<span class="muted">' + esc(a.by) + '</span>' : '') + '</div>' +
        '<b class="' + (minus ? 'neg' : 'ok') + '">' + (minus ? '−' : '+') + esc(money(a.amount)) + '</b>' +
        '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-ddel="' + esc(a.id) + '" aria-label="Удалить">' + icon('trash-2') + '</button></div>';
    }).join('') + '</div>';
    // выплаты
    html += '<h4 class="pr-dh">Выплаты</h4>';
    if (!(m.payouts || []).length) html += '<div class="pr-dmeta">За период выплат не было.</div>';
    else html += '<div class="pr-adjs">' + m.payouts.map(function (x) {
      var at = x.at ? new Date(x.at).toLocaleString('ru-RU', { timeZone: TZ, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
      return '<div class="pr-adj payout"><span class="pr-adj-ic">' + icon(x.method === 'cash' ? 'banknote' : 'smartphone') + '</span>' +
        '<div class="pr-adj-m"><b>За ' + esc(x.period) + ' · ' + esc(x.method_title.toLowerCase()) + '</b><span class="muted">' + esc([at, x.by].filter(Boolean).join(' · ')) + '</span>' +
        (x.comment ? '<span>' + esc(x.comment) + '</span>' : '') + '</div><b>' + esc(money(x.amount)) + '</b></div>';
    }).join('') + '</div>';
    return html;
  }

  // ── выплатить ──
  function payDialog(m) {
    var idem = P().newKey(), busy = false, method = st.canCash ? 'cash' : 'transfer';
    var dlg = S.dialog({
      title: 'Выплатить · ' + m.name,
      body: '<div class="py-dlg pr-dlg"><div class="py-parts"><div><span>Начислено за ' + esc(st.data.label) + '</span><b>' + esc(money(m.accrued)) + '</b></div>' +
        (m.advances ? '<div><span>Авансы</span><b>−' + esc(money(m.advances)) + '</b></div>' : '') +
        (m.paid ? '<div><span>Уже выплачено</span><b>−' + esc(money(m.paid)) + '</b></div>' : '') +
        '<div class="paid"><span>К выплате</span><b>' + esc(money(m.to_pay)) + '</b></div></div>' +
        '<label class="label" for="po-a">Сумма</label><div class="py-amount"><input class="input" id="po-a" inputmode="decimal" autocomplete="off" value="' + esc(String(Math.round(m.to_pay * 100) / 100)) + '"><span>сом</span></div>' +
        '<div class="py-hint" id="po-h"></div>' +
        '<label class="label">Как выплачиваем</label><div class="py-methods pr-methods" role="radiogroup">' + PAY_METHODS.map(function (x) {
          return '<button type="button" class="py-m' + (x[0] === method ? ' on' : '') + '" role="radio" aria-checked="' + (x[0] === method) + '" data-pm="' + x[0] + '">' + icon(x[2]) + '<span>' + x[1] + '</span></button>';
        }).join('') + '</div><div class="py-hint" id="po-mh"></div>' +
        '<label class="label" for="po-c">Комментарий</label><input class="input" id="po-c" maxlength="300" placeholder="Необязательно"></div>',
      foot: '<button class="btn" type="button" data-cancel>Отмена</button><button class="btn btn-primary" type="button" data-ok>' + icon('hand-coins') + '<span>Выплатить</span></button>'
    });
    var el = dlg.el, inp = S.$('#po-a', el);
    function upd() {
      var a = num(inp.value), ok = isFinite(a) && a > 0 && a <= m.to_pay + 0.005;
      S.$('#po-h', el).innerHTML = !isFinite(a) || a <= 0 ? '<span class="neg">Укажите сумму</span>'
        : a > m.to_pay + 0.005 ? '<span class="neg">Больше, чем к выплате (' + esc(money(m.to_pay)) + ')</span>'
          : a < m.to_pay - 0.5 ? 'Частичная выплата: останется ' + esc(money(m.to_pay - a)) : 'Вся сумма за период';
      S.$('#po-mh', el).innerHTML = method === 'cash'
        ? (st.canCash ? 'Деньги уйдут из вашей открытой смены в кассе — чек «Зарплата: ' + esc(m.name) + '». Смена не открыта — откройте её в «Кассе».'
          : '<span class="neg">Наличными выплачивает тот, кто ведёт смену в кассе. Выберите «Переводом» или попросите администратора.</span>')
        : 'Перевод на карту или по номеру — деньги кассы не трогаем, выплата сохранится в ведомости.';
      S.$('[data-ok] span', el).textContent = ok ? 'Выплатить ' + money(a) : 'Выплатить';
      S.$('[data-ok]', el).disabled = !ok || (method === 'cash' && !st.canCash);
    }
    S.$$('[data-pm]', el).forEach(function (b) {
      b.addEventListener('click', function () {
        method = b.getAttribute('data-pm');
        S.$$('[data-pm]', el).forEach(function (x) { x.classList.toggle('on', x === b); x.setAttribute('aria-checked', String(x === b)); });
        upd();
      });
    });
    inp.addEventListener('input', upd);
    upd();
    S.$('[data-cancel]', el).addEventListener('click', function () { dlg.close(); });
    S.$('[data-ok]', el).addEventListener('click', function () {
      if (busy) return;
      busy = true;
      S.api('/api/payroll/payouts', { method: 'POST', body: { resource_id: m.id, from: st.from, to: st.to, amount: num(inp.value), method: method,
        idem_key: idem, comment: S.$('#po-c', el).value.trim() || null } }).then(function (r) {
        busy = false;
        if (!r.ok) {
          var e = errOf(r);
          S.toast(e.code === 'shift_required' && st.canCash ? 'Сначала откройте смену в «Кассе»' : e.text, 'error');
          if (e.code === 'paid') { dlg.close(); reload(); }
          return;
        }
        dlg.close();
        S.toast('Выплачено ' + money(r.data.payout.amount) + ' · ' + m.name, 'success');
        reload();
      });
    });
  }

  // ── аванс / премия / штраф ──
  function adjDialog(m, kind) {
    var busy = false, t = today();
    var day = t >= st.from && t <= st.to ? t : (t > st.to ? st.to : st.from);
    var dlg = S.dialog({
      title: m.name,
      body: '<div class="py-dlg pr-dlg"><div class="py-quick pr-kinds">' + KINDS.map(function (k) {
        return '<button type="button" class="chip' + (k[0] === kind ? ' on' : '') + '" data-k="' + k[0] + '">' + esc(k[1]) + '</button>';
      }).join('') + '</div>' +
        '<label class="label" for="ad-a">Сумма</label><div class="py-amount"><input class="input" id="ad-a" inputmode="decimal" autocomplete="off" placeholder="0"><span>сом</span></div>' +
        '<label class="label" for="ad-d">Дата</label><input type="date" class="input" id="ad-d" value="' + esc(day) + '">' +
        '<label class="label" for="ad-c" id="ad-cl">Комментарий</label><div class="py-quick" id="ad-q"></div>' +
        '<input class="input" id="ad-c" maxlength="300" style="margin-top:8px">' +
        '<div class="py-hint" id="ad-h"></div></div>',
      foot: '<button class="btn" type="button" data-cancel>Отмена</button><button class="btn btn-primary" type="button" data-ok>Добавить</button>'
    });
    var el = dlg.el;
    function upd() {
      var fine = kind === 'fine', reasons = fine ? FINE_REASONS : kind === 'bonus' ? BONUS_REASONS : [];
      S.$('#ad-cl', el).textContent = fine ? 'За что штраф' : 'Комментарий';
      S.$('#ad-c', el).placeholder = fine ? 'Мастер увидит это в своём кабинете' : 'Необязательно';
      S.$('#ad-q', el).innerHTML = reasons.map(function (x) { return '<button type="button" class="chip" data-r="' + esc(x) + '">' + esc(x) + '</button>'; }).join('');
      S.$('#ad-h', el).textContent = kind === 'advance' ? 'Аванс вычтется из суммы к выплате за этот период.'
        : kind === 'bonus' ? 'Премия добавится к начисленному.' : 'Штраф вычтется из начисленного. Мастер увидит строку и причину.';
      S.$$('[data-r]', el).forEach(function (b) { b.addEventListener('click', function () { S.$('#ad-c', el).value = b.getAttribute('data-r'); }); });
    }
    S.$$('[data-k]', el).forEach(function (b) {
      b.addEventListener('click', function () {
        kind = b.getAttribute('data-k');
        S.$$('[data-k]', el).forEach(function (x) { x.classList.toggle('on', x === b); });
        upd();
      });
    });
    upd();
    setTimeout(function () { try { S.$('#ad-a', el).focus(); } catch (e) { /* ignore */ } }, 60);
    S.$('[data-cancel]', el).addEventListener('click', function () { dlg.close(); });
    S.$('[data-ok]', el).addEventListener('click', function () {
      if (busy) return;
      var a = num(S.$('#ad-a', el).value), c = S.$('#ad-c', el).value.trim();
      if (!isFinite(a) || a <= 0) { S.toast('Укажите сумму', 'error'); return; }
      if (kind === 'fine' && c.length < 2) { S.toast('Напишите, за что штраф', 'error'); return; }
      busy = true;
      S.api('/api/payroll/adjustments', { method: 'POST', body: { resource_id: m.id, kind: kind, amount: a, day: S.$('#ad-d', el).value || null, comment: c || null } })
        .then(function (r) {
          busy = false;
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          dlg.close();
          var d = r.data.day;
          S.toast(KIND[kind] + ' ' + money(a) + (d < st.from || d > st.to ? ' — дата вне периода ведомости' : ''), 'success');
          reload();
        });
    });
  }
  function delAdj(m, id) {
    var a = (m.adjustments || []).filter(function (x) { return x.id === id; })[0];
    if (!a) return;
    S.confirm({ title: 'Удалить строку?', message: a.title + ' ' + money(a.amount) + ' от ' + dm(a.day), confirmText: 'Удалить', danger: true }).then(function (ok) {
      if (!ok) return;
      S.api('/api/payroll/adjustments/' + id, { method: 'DELETE' }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast('Удалено'); reload();
      });
    });
  }

  // ── оплата мастера (схема) ──
  function schemeById(id) {
    var m = lineOf(id);
    if (!m) return;
    if (st.cats) { schemeDialog(m); return; }
    S.api('/api/payroll/masters/' + id + '?from=' + st.from + '&to=' + st.to).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      st.cats = r.data.categories || [];
      schemeDialog(r.data.line || m);
    });
  }
  function catRow(name, pct) {
    var opts = (st.cats || []).slice();
    if (name && opts.indexOf(name) < 0) opts.unshift(name);
    return '<div class="pr-crow"><select class="select-native" data-cn aria-label="Категория">' + '<option value="">Категория…</option>' + opts.map(function (c) {
      return '<option' + (c === name ? ' selected' : '') + '>' + esc(c) + '</option>';
    }).join('') + '</select><div class="py-amount sm"><input class="input" data-cp inputmode="decimal" value="' + esc(pct != null ? String(pct) : '') + '" placeholder="0"><span>%</span></div>' +
      '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-cx aria-label="Убрать">' + icon('x') + '</button></div>';
  }
  function schemeDialog(m) {
    var sc = m.scheme || { percent: 0, categories: {}, shift: 0, fixed: 0 }, busy = false;
    var cats = sc.categories || {};
    var dlg = S.dialog({
      title: 'Оплата мастера · ' + m.name,
      size: 'md',
      body: '<div class="py-dlg pr-dlg pr-sch">' +
        '<label class="label" for="sc-p">Процент с услуг</label>' +
        '<div class="py-quick">' + [30, 40, 50, 60].map(function (p) { return '<button type="button" class="chip' + (sc.percent === p ? ' on' : '') + '" data-qp="' + p + '">' + p + '%</button>'; }).join('') + '</div>' +
        '<div class="py-amount sm" style="margin-top:8px"><input class="input" id="sc-p" inputmode="decimal" value="' + esc(sc.percent ? String(sc.percent) : '') + '" placeholder="0"><span>%</span></div>' +
        '<label class="label">Свой процент по категориям</label>' +
        '<div class="py-hint" style="margin-top:0">Например, окрашивание 50%, а остальное — общий процент. Категория с подкатегориями: «Волосы» действует на все услуги волос.</div>' +
        '<div id="sc-cats">' + Object.keys(cats).map(function (k) { return catRow(k, cats[k]); }).join('') + '</div>' +
        '<div><button type="button" class="py-link" data-addcat' + ((st.cats || []).length ? '' : ' disabled title="Категорий услуг пока нет"') + '>' + icon('plus') + 'Категория со своим процентом</button></div>' +
        '<label class="label" for="sc-s">Ставка за выход</label>' +
        '<div class="py-amount sm"><input class="input" id="sc-s" inputmode="decimal" value="' + esc(sc.shift ? String(sc.shift) : '') + '" placeholder="0"><span>сом</span></div>' +
        '<div class="py-hint">За каждый день, когда администратор отметил выход в журнале записи.</div>' +
        '<label class="label" for="sc-f">Оклад в месяц</label>' +
        '<div class="py-amount sm"><input class="input" id="sc-f" inputmode="decimal" value="' + esc(sc.fixed ? String(sc.fixed) : '') + '" placeholder="0"><span>сом</span></div>' +
        '<div class="py-hint">В ведомость идёт доля по дням периода. Можно сочетать с процентом и ставкой за выход.</div></div>',
      foot: '<button class="btn" type="button" data-cancel>Отмена</button><button class="btn btn-primary" type="button" data-ok>Сохранить</button>'
    });
    var el = dlg.el;
    function bindRows() {
      S.$$('[data-cx]', el).forEach(function (b) { b.onclick = function () { b.closest('.pr-crow').remove(); }; });
    }
    bindRows();
    S.$$('[data-qp]', el).forEach(function (b) {
      b.addEventListener('click', function () {
        S.$('#sc-p', el).value = b.getAttribute('data-qp');
        S.$$('[data-qp]', el).forEach(function (x) { x.classList.toggle('on', x === b); });
      });
    });
    S.$('[data-addcat]', el).addEventListener('click', function () {
      S.$('#sc-cats', el).insertAdjacentHTML('beforeend', catRow('', null));
      bindRows();
    });
    S.$('[data-cancel]', el).addEventListener('click', function () { dlg.close(); });
    S.$('[data-ok]', el).addEventListener('click', function () {
      if (busy) return;
      var out = {};
      S.$$('.pr-crow', el).forEach(function (row) {
        var c = S.$('[data-cn]', row).value, p = S.$('[data-cp]', row).value.trim();
        if (c && p !== '') out[c] = p;
      });
      busy = true;
      S.api('/api/payroll/masters/' + m.id + '/scheme', { method: 'PUT', body: { percent: S.$('#sc-p', el).value.trim() || 0, categories: out,
        shift: S.$('#sc-s', el).value.trim() || 0, fixed: S.$('#sc-f', el).value.trim() || 0 } }).then(function (r) {
        busy = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        dlg.close();
        S.toast('Сохранено: ' + r.data.scheme_text, 'success');
        reload();
      });
    });
  }

  function onClick(e) {
    var t = e.target;
    var pr = t.closest('[data-preset]');
    if (pr) {
      var k = pr.getAttribute('data-preset');
      if (k === 'custom') { st.preset = 'custom'; draw(); if (!st.data) root.innerHTML = periodsHtml(); return; }
      setPreset(k); writeUrl(); load(); return;
    }
    if (t.closest('[data-apply]')) {
      var f = (S.$('#pr-from', root) || {}).value, to = (S.$('#pr-to', root) || {}).value;
      if (!f || !to) { S.toast('Выберите обе даты', 'error'); return; }
      if (to < f) { S.toast('Конец периода раньше начала', 'error'); return; }
      st.from = f; st.to = to; writeUrl(); load(); return;
    }
    var o = t.closest('[data-open]');
    if (o) { openMaster(o.getAttribute('data-open')); return; }
    var p = t.closest('[data-pay]');
    if (p) {
      var m = lineOf(p.getAttribute('data-pay'));
      if (!m) return;
      if (st.canCash != null) { payDialog(m); return; }
      S.api('/api/payroll/masters/' + m.id + '?from=' + st.from + '&to=' + st.to).then(function (r) {
        st.canCash = r.ok ? !!r.data.can_cash : false;
        if (r.ok) st.cats = r.data.categories || [];
        payDialog(m);
      });
      return;
    }
    var sc = t.closest('[data-scheme]');
    if (sc) schemeById(sc.getAttribute('data-scheme'));
  }

  function start() {
    root = document.getElementById('pr');
    esc = S.esc; icon = S.icon;
    if (S.initMobileMenu) S.initMobileMenu();
    readUrl();
    root.addEventListener('click', onClick);
    load().then(function () { if (st.open && st.data) openMaster(st.open, true); });
    document.addEventListener('visibilitychange', function () { if (!document.hidden && !document.querySelector('.vf-backdrop') && !st.dr) load(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
