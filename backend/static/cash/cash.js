/**
 * Касса (Voksy Beauty, шаг 3.1): /static/cash.html.
 *
 * «Моя смена»: открыть (сколько наличных в кассе), итоги по способам, наличные в кассе по чекам, внести / изъять,
 * закрыть смену (насчитали → расхождение). Владелец и бухгалтер видят открытые смены других и закрывают их,
 * итог дня по салону. «Чеки»: оплаты, возвраты, внесения и изъятия за день, «Вернуть» по чеку, переход к записи.
 * «Смены»: история смен с расхождениями (владелец и бухгалтер — все, кассир — свои), смена целиком в панели.
 * Оплату визита принимают из карточки записи в журнале (booking/pay.js — общий код окон оплаты и возврата).
 * «Долги» (шаг 3.2): визиты, оплаченные не полностью, — «Принять оплату», «Напомнить» (одно сообщение в день, текст
 * видно до отправки), «Списать» (только владелец, с причиной). «Итог дня» — те же цифры, что в отчёте дня владельцу;
 * время отчёта выбирает владелец. API: /api/cash/* (services/cashbox.py, services/cash_debts.py).
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var P = function () { return window.BookingPay; };
  var TZ = 'Asia/Bishkek';
  var root, esc, icon;
  var st = { tab: 'shift', state: null, day: null, receipts: null, rnext: null, shifts: null, snext: null, debts: null, report: null };
  var OFF_REASONS = ['Клиент не вернётся', 'Подарок / комплимент', 'Ошибка в цене', 'Оплатил переводом владельцу'];
  var M_IC = { cash: 'banknote', card: 'credit-card', qr: 'qr-code', transfer: 'smartphone' };
  var MT = { cash: 'Наличные', card: 'Карта', qr: 'QR', transfer: 'Перевод' };
  var OUT_REASONS = ['Купили расходники', 'Забрал владелец', 'Выдали зарплату', 'Хозяйственные нужды'];
  var IN_REASONS = ['Размен', 'Внёс владелец'];

  function money(v) { return P().money(v); }
  function num(v) { return P().num(v); }
  function keyOf(d) { return d.toLocaleDateString('en-CA', { timeZone: TZ }); }
  function hm(iso) { return new Date(iso).toLocaleTimeString('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }); }
  function dayLabel(k) {
    var today = keyOf(new Date()), d = new Date(k + 'T12:00:00+06:00');
    var y = new Date(Date.now() - 864e5);
    if (k === today) return 'Сегодня';
    if (k === keyOf(y)) return 'Вчера';
    return d.toLocaleDateString('ru-RU', { timeZone: TZ, weekday: 'short', day: 'numeric', month: 'long' });
  }
  function shiftWhen(sh) {
    var a = new Date(sh.opened_at), today = keyOf(new Date());
    var d = keyOf(a) === today ? 'сегодня' : a.toLocaleDateString('ru-RU', { timeZone: TZ, day: 'numeric', month: 'short' });
    return d + ' ' + hm(sh.opened_at) + (sh.closed_at ? '–' + hm(sh.closed_at) : '');
  }
  function hoursText(h) { return h < 1 ? Math.max(1, Math.round(h * 60)) + ' мин' : String(h).replace('.', ',') + ' ч'; }
  function diffChip(d) {
    if (d == null) return '';
    if (Math.abs(d) < 0.5) return '<span class="py-chip ok">Сходится</span>';
    return d < 0 ? '<span class="py-chip bad">Не хватает ' + esc(money(-d)) + '</span>' : '<span class="py-chip warn">Лишние ' + esc(money(d)) + '</span>';
  }
  function tabs() {
    var s = st.state, out = [];
    if (s.can_take) out.push(['shift', 'Моя смена']);
    if (s.debts) out.push(['debts', 'Долги']);
    out.push(['receipts', 'Чеки']);
    out.push(['shifts', s.can_manage ? 'Смены' : 'Мои смены']);
    return out;
  }

  // ── загрузка ──
  function loadState() {
    return S.api('/api/cash/state').then(function (r) {
      if (!r.ok) {
        root.innerHTML = '<div class="st-empty">' + esc(r.status === 403 ? 'Касса открыта владельцу, администраторам (раздел «Онлайн-запись») и бухгалтеру.' : S.apiError(r)) + '</div>';
        S.ready();
        return false;
      }
      st.state = r.data;
      return true;
    });
  }
  function loadReceipts(more) {
    var q = new URLSearchParams();
    if (st.tab === 'shift' && st.state.shift) q.set('shift_id', st.state.shift.id); else q.set('date', st.day);
    if (more && st.rnext) q.set('before', st.rnext);
    return S.api('/api/cash/receipts?' + q).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      st.receipts = more ? (st.receipts || []).concat(r.data.items) : r.data.items;
      st.rnext = r.data.next;
      st.dayNums = r.data.day || null;
      st.report = r.data.report || null;
    });
  }
  function loadDebts() {
    return S.api('/api/cash/debts').then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); st.debts = { items: [], count: 0, total: 0, enabled: true }; return; }
      st.debts = r.data;
    });
  }
  function loadShifts(more) {
    var q = new URLSearchParams();
    if (more && st.snext) q.set('before', st.snext);
    return S.api('/api/cash/shifts?' + q).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      st.shifts = more ? (st.shifts || []).concat(r.data.items) : r.data.items;
      st.snext = r.data.next;
    });
  }
  function refresh() {
    return loadState().then(function (ok) {
      if (!ok) return;
      if (!st.state.can_take && st.tab === 'shift') st.tab = 'receipts';
      if (st.tab === 'debts' && !st.state.debts) st.tab = 'receipts';
      var p = st.tab === 'shifts' ? loadShifts() : st.tab === 'debts' ? loadDebts()
        : (st.tab === 'shift' && !st.state.shift) ? Promise.resolve(st.receipts = []) : loadReceipts();
      return p.then(draw);
    });
  }

  // ── отрисовка ──
  function draw() {
    var s = st.state;
    var html = '<div class="st-colbar cs-tabs">' + tabs().map(function (t) {
      return '<button type="button" class="st-colchip' + (st.tab === t[0] ? ' on' : '') + '" data-tab="' + t[0] + '">' + esc(t[1]) +
        (t[0] === 'shifts' && s.open_shifts && s.open_shifts.length ? ' <span class="cs-n">' + s.open_shifts.length + '</span>' : '') +
        (t[0] === 'debts' && s.debts && s.debts.count ? ' <span class="cs-n warn">' + s.debts.count + '</span>' : '') + '</button>';
    }).join('') + '</div>';
    if (st.tab === 'shift') html += shiftTab();
    else if (st.tab === 'debts') html += debtsTab();
    else if (st.tab === 'receipts') html += receiptsTab();
    else html += shiftsTab();
    root.innerHTML = html;
    S.ready();
  }

  function methodTiles(by) {
    return '<div class="cs-tiles">' + ['cash', 'card', 'qr', 'transfer'].map(function (k) {
      var v = (by && by[k]) || 0;
      return '<div class="cs-tile' + (v ? '' : ' zero') + '"><span>' + icon(M_IC[k]) + MT[k] + '</span><b>' + esc(money(v)) + '</b></div>';
    }).join('') + '</div>';
  }
  function basisNote() {
    var s = st.state;
    if (!s.can_manage) return '';
    return '<div class="cs-note">' + icon('info') + (s.cash_start
      ? 'Выручка в «Финансах», аналитике и на главной считается по оплатам в кассе с ' + esc(new Date(s.cash_start).toLocaleDateString('ru-RU', { timeZone: TZ, day: 'numeric', month: 'long' })) + '. Визит без оплаты — долг клиента.'
      : 'Пока смен не было, выручка считается по визитам «пришёл». С первой смены — по оплатам в кассе: визит без оплаты станет долгом клиента.') + '</div>';
  }

  function shiftTab() {
    var s = st.state, sh = s.shift, out = '';
    if (!sh) {
      out += '<section class="cs-card cs-open"><div class="cs-open-ic">' + icon('lock') + '</div>' +
        '<h2>Смена не открыта</h2>' +
        '<p>Наличные принимаются только в открытой смене: при закрытии вы пересчитаете кассу, и сразу будет видно, сходится ли. Карту, QR и перевод можно принять и без смены.</p>' +
        '<label class="label" for="cs-opening">Наличные в кассе сейчас</label>' +
        '<div class="py-amount"><input class="input" id="cs-opening" inputmode="decimal" autocomplete="off" value="' + esc(String(s.suggest_opening != null ? s.suggest_opening : 0)) + '"><span>сом</span></div>' +
        (s.suggest_opening != null ? '<div class="py-hint">В прошлый раз при закрытии оставили ' + esc(money(s.suggest_opening)) + '</div>' : '') +
        '<button class="btn btn-primary cs-big" type="button" data-open>' + icon('lock-open') + 'Открыть смену</button></section>';
    } else {
      var n = sh.numbers || {};
      out += '<section class="cs-card cs-shift">' +
        '<div class="cs-shift-h"><span class="py-chip ok">Смена открыта</span><span class="muted small">' + esc(shiftWhen(sh)) + ' · ' + esc(sh.cashier) + ' · ' + esc(hoursText(sh.hours)) + '</span></div>' +
        '<div class="cs-cashbig"><span>В кассе наличными (по чекам)</span><b>' + esc(money(n.expected_cash)) + '</b>' +
        '<small>при открытии ' + esc(money(n.opening_cash)) + ' · наличные ' + (n.by_method.cash >= 0 ? '+' : '−') + esc(money(Math.abs(n.by_method.cash))) +
        (n.cash_in ? ' · внесено +' + esc(money(n.cash_in)) : '') + (n.cash_out ? ' · изъято −' + esc(money(n.cash_out)) : '') + '</small></div>' +
        methodTiles(n.by_method) +
        '<div class="cs-line"><div><span>Выручка смены</span><b>' + esc(money(n.revenue)) + '</b></div><div><span>Чеков</span><b>' + (n.receipts || 0) + '</b></div>' +
        '<div><span>Возвраты</span><b' + (n.refunds ? ' class="neg"' : '') + '>' + (n.refunds ? '−' : '') + esc(money(n.refunds)) + '</b></div></div>' +
        '<div class="cs-acts"><button class="btn" type="button" data-move="cash_in">' + icon('arrow-down-to-line') + 'Внести</button>' +
        '<button class="btn" type="button" data-move="cash_out">' + icon('arrow-up-from-line') + 'Изъять</button>' +
        '<button class="btn btn-primary" type="button" data-close="' + esc(sh.id) + '">' + icon('lock') + 'Закрыть смену</button></div></section>';
      out += '<section class="cs-card"><h3>Чеки смены</h3>' + receiptList(st.receipts || [], false) + '</section>';
    }
    if (s.can_manage && s.open_shifts && s.open_shifts.length) {
      out += '<section class="cs-card"><h3>Открытые смены сотрудников</h3>' + s.open_shifts.map(function (o) {
        var n2 = o.numbers || {};
        return '<div class="cs-orow"><div><b>' + esc(o.cashier) + '</b><span class="muted small">с ' + esc(shiftWhen(o)) + ' · ' + esc(hoursText(o.hours)) + '</span>' +
          '<span class="small">выручка ' + esc(money(n2.revenue)) + ' · в кассе по чекам ' + esc(money(n2.expected_cash)) + '</span></div>' +
          '<button class="btn btn-sm" type="button" data-close="' + esc(o.id) + '">Закрыть</button></div>';
      }).join('') + '</section>';
    }
    if (s.can_manage && s.day) out += dayCard(s.day, true);
    else if (s.can_manage && s.today) {
      out += '<section class="cs-card"><div class="cs-card-h"><h3>Сегодня по салону</h3><button class="py-link" type="button" data-tab="receipts">Все чеки дня →</button></div>' +
        '<div class="cs-daytotal"><span>Выручка</span><b>' + esc(money(s.today.revenue)) + '</b><small>' + s.today.receipts + ' ' + plural(s.today.receipts, 'чек', 'чека', 'чеков') +
        (s.today.refunds ? ' · возвраты −' + esc(money(s.today.refunds)) : '') + '</small></div>' + methodTiles(s.today.by_method) + '</section>';
    }
    return out + basisNote();
  }

  function receiptList(items, withClient) {
    if (!items.length) return '<div class="cs-empty">' + icon('receipt-text') + 'Чеков пока нет. Оплату принимают в карточке записи: «Журнал записи» → запись → «Принять оплату».</div>';
    return '<div class="cs-rcs">' + items.map(function (rc) { return P().receiptRow(rc, { canRefund: st.state.can_take, withClient: true }); }).join('') + '</div>';
  }

  function receiptsTab() {
    var items = st.receipts || [];
    var d = st.dayNums;
    if (!d) {      // кассиру — итог по своим чекам дня
      var by = { cash: 0, card: 0, qr: 0, transfer: 0 }, rev = 0, n = 0;
      items.forEach(function (rc) {
        if (rc.kind !== 'payment' && rc.kind !== 'refund') return;
        if (rc.kind === 'payment') n++;
        (rc.lines || []).forEach(function (l) { var v = rc.kind === 'refund' ? -l.amount : l.amount; by[l.method] = (by[l.method] || 0) + v; rev += v; });
      });
      d = { by_method: by, revenue: rev, receipts: n, refunds: 0 };
    }
    return '<div class="cs-daynav"><button class="btn btn-ghost btn-icon btn-sm" type="button" data-day="-1" aria-label="Предыдущий день">' + icon('chevron-left') + '</button>' +
      '<b>' + esc(dayLabel(st.day)) + '</b>' +
      '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-day="1" aria-label="Следующий день"' + (st.day >= keyOf(new Date()) ? ' disabled' : '') + '>' + icon('chevron-right') + '</button>' +
      '<input type="date" class="input cs-date" id="cs-date" value="' + esc(st.day) + '" max="' + esc(keyOf(new Date())) + '"></div>' +
      (st.report ? dayCard(st.report, false) :
      '<section class="cs-card"><div class="cs-daytotal"><span>' + (st.state.can_manage ? 'Выручка за день' : 'Мои чеки за день') + '</span><b>' + esc(money(d.revenue)) + '</b>' +
      '<small>' + (d.receipts || 0) + ' ' + plural(d.receipts || 0, 'оплата', 'оплаты', 'оплат') + (d.refunds ? ' · возвраты −' + esc(money(d.refunds)) : '') + '</small></div>' +
      methodTiles(d.by_method) + '</section>') +
      '<section class="cs-card">' + receiptList(items, true) +
      (st.rnext ? '<button class="btn btn-ghost cs-more" type="button" data-rmore>Показать ещё</button>' : '') + '</section>';
  }

  function shiftsTab() {
    var items = st.shifts || [];
    if (!items.length) return '<div class="cs-empty">' + icon('wallet') + 'Смен ещё не было.</div>';
    return '<section class="cs-card cs-slist">' + items.map(function (sh) {
      var n = sh.numbers || {};
      return '<button type="button" class="cs-srow' + (sh.status === 'open' ? ' open' : '') + '" data-shift="' + esc(sh.id) + '">' +
        '<div class="a"><b>' + esc(sh.cashier) + '</b><span>' + esc(shiftWhen(sh)) + '</span></div>' +
        '<div class="b"><span>Выручка</span><b>' + esc(money(n.revenue)) + '</b></div>' +
        '<div class="c">' + (sh.status === 'open' ? '<span class="py-chip ok">Открыта</span>' : diffChip(sh.diff)) + '</div></button>';
    }).join('') + (st.snext ? '<button class="btn btn-ghost cs-more" type="button" data-smore>Показать ещё</button>' : '') + '</section>' + basisNote();
  }

  // ── «Итог дня» (шаг 3.2): те же цифры, что уходят владельцу отчётом дня ──
  function shiftState(x) {
    if (x.status === 'open') return '<span class="py-chip warn">смена открыта</span>';
    if (x.diff == null || Math.abs(x.diff) < 1) return '<span class="py-chip ok">сходится</span>';
    return x.diff < 0 ? '<span class="py-chip bad">не хватает ' + esc(money(-x.diff)) + '</span>' : '<span class="py-chip warn">лишние ' + esc(money(x.diff)) + '</span>';
  }
  function dayCard(r, withSettings) {
    var nd = r.new_debts || {}, ns = r.no_shows || {}, html = '<section class="cs-card cs-day">' +
      '<div class="cs-card-h"><h3>' + icon('chart-column') + 'Итог дня · ' + esc(r.day_words || dayLabel(r.date)) + '</h3>' +
      (withSettings ? '<button class="py-link" type="button" data-tab="receipts">Все чеки дня →</button>' : '') + '</div>' +
      '<div class="cs-daytotal"><span>Выручка</span><b>' + esc(money(r.revenue)) + '</b><small>' + (r.receipts || 0) + ' ' +
      plural(r.receipts || 0, 'оплата', 'оплаты', 'оплат') + (r.visits ? ' · визитов ' + r.visits : '') + '</small></div>' + methodTiles(r.by_method) +
      '<div class="cs-drows">';
    if (r.refunds) html += '<div class="cs-drow"><span>' + icon('rotate-ccw') + 'Возвраты</span><b class="neg">−' + esc(money(r.refunds)) + '</b></div>';
    (r.shifts || []).forEach(function (x) {
      html += '<div class="cs-drow"><span>' + icon('wallet') + 'Смена · ' + esc(x.cashier) + ' · ' + esc(hm(x.opened_at)) + (x.closed_at ? '–' + esc(hm(x.closed_at)) : '') + '</span>' + shiftState(x) + '</div>';
    });
    html += '<button type="button" class="cs-drow link" data-tab="debts"><span>' + icon('receipt-text') + 'Новые долги</span><b' + (nd.count ? ' class="neg"' : '') + '>' +
      (nd.count ? nd.count + ' · ' + esc(money(nd.total)) : 'нет') + '</b></button>';
    html += '<div class="cs-drow"><span>' + icon('user-x') + 'Не пришли</span><b' + (ns.count ? ' class="warn"' : '') + '>' +
      (ns.count ? ns.count + ' · ' + esc((ns.items || []).slice(0, 3).map(function (x) { return x.client + ' ' + x.time; }).join(', ')) : 'нет') + '</b></div></div>';
    if (withSettings && st.state.day_report) html += reportSettings();
    return html + '</section>';
  }
  function reportSettings() {
    var c = st.state.day_report, hours = st.state.report_hours || [17, 18, 19, 20, 21, 22, 23];
    return '<div class="cs-rep">' +
      '<label class="cs-switch"><input type="checkbox" id="cs-rep-on"' + (c.on ? ' checked' : '') + '><span></span></label>' +
      '<div class="cs-rep-t"><b>Присылать итог дня</b><span>' + (st.state.day_report_telegram ? 'В Telegram-бот и уведомлением на телефон' :
        'Уведомлением на телефон · подключите Telegram-бот агента, чтобы получать и туда') + '</span></div>' +
      '<select class="select-native cs-rep-h" id="cs-rep-h"' + (c.on ? '' : ' disabled') + ' aria-label="Время отчёта">' + hours.map(function (h) {
        return '<option value="' + h + '"' + (h === c.hour ? ' selected' : '') + '>в ' + h + ':00</option>';
      }).join('') + '</select></div>';
  }
  function saveReport(body) {
    S.api('/api/cash/day-report', { method: 'PUT', body: body }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); refresh(); return; }
      st.state.day_report = r.data.day_report;
      S.toast(r.data.day_report.on ? 'Итог дня придёт в ' + r.data.day_report.hour + ':00' : 'Итог дня выключен');
      draw();
    });
  }

  // ── «Долги» (шаг 3.2) ──
  function agoDays(k) {
    var n = Math.round((new Date(keyOf(new Date()) + 'T12:00:00+06:00') - new Date(k + 'T12:00:00+06:00')) / 864e5);
    return n <= 0 ? 'сегодня' : n === 1 ? 'вчера' : n + ' ' + plural(n, 'день', 'дня', 'дней') + ' назад';
  }
  function debtsTab() {
    var d = st.debts;
    if (!d) return '<div class="skeleton" style="height:240px;border-radius:16px"></div>';
    if (!d.enabled) return '<div class="cs-empty">' + icon('receipt-text') + 'Долги появятся с первой смены: визит «пришёл», оплаченный не полностью, станет долгом клиента.</div>';
    if (!d.items.length) return '<section class="cs-card"><div class="cs-empty">' + icon('circle-check') + '<b>Долгов нет</b>Все визиты оплачены.</div></section>';
    var html = '<section class="cs-card cs-dsum2"><div><span>Должны салону</span><b>' + esc(money(d.total)) + '</b>' +
      '<small>' + d.clients + ' ' + plural(d.clients, 'клиент', 'клиента', 'клиентов') + ' · ' + d.count + ' ' + plural(d.count, 'визит', 'визита', 'визитов') + '</small></div>' +
      '<p>Напомнить — одно сообщение клиенту в его мессенджер, не чаще раза в день.' + (d.can_write_off ? ' Списать долг может только владелец.' : '') + '</p></section>';
    html += '<section class="cs-card cs-debts">' + d.items.map(function (x) {
      var acts = '';
      if (d.can_take) acts += '<button class="btn btn-primary btn-sm" type="button" data-dpay="' + esc(x.booking_id) + '">' + icon('banknote') + 'Принять</button>';
      if (d.can_take) acts += '<button class="btn btn-sm" type="button" data-dremind="' + esc(x.booking_id) + '"' + (x.reminded_today ? ' disabled title="Сегодня уже напоминали"' : '') + '>' +
        icon('send') + (x.reminded_today ? 'Напомнили' : 'Напомнить') + '</button>';
      if (d.can_write_off) acts += '<button class="btn btn-ghost btn-sm" type="button" data-doff="' + esc(x.booking_id) + '">Списать</button>';
      return '<div class="cs-debt">' +
        '<div class="cs-debt-m"><b>' + esc(x.client_name) + '</b>' +
        (x.phone ? '<a class="cs-debt-ph" href="tel:' + esc(x.phone) + '">' + esc(x.phone) + '</a>' : '') +
        '<span>' + esc(x.services.join(' + ')) + (x.masters.length ? ' · ' + esc(x.masters.join(', ')) : '') + '</span>' +
        '<span class="muted">' + esc(x.day_words) + ' · ' + esc(agoDays(x.date)) + (x.paid ? ' · оплачено ' + esc(money(x.paid)) + ' из ' + esc(money(x.due)) : '') +
        (x.reminded_at ? ' · напомнили ' + esc(x.reminded_today ? 'сегодня' : new Date(x.reminded_at).toLocaleDateString('ru-RU', { timeZone: TZ, day: 'numeric', month: 'short' })) : '') + '</span></div>' +
        '<div class="cs-debt-r"><b>' + esc(money(x.left)) + '</b><div class="cs-debt-acts">' + acts + '</div></div></div>';
    }).join('') + '</section>';
    return html;
  }
  function findDebt(id) { return ((st.debts && st.debts.items) || []).filter(function (x) { return x.booking_id === id; })[0]; }
  function remindDialog(id) {
    var x = findDebt(id);
    S.api('/api/cash/debts/' + id + '/remind', { method: 'POST', body: { send: false } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      var p = r.data, busy = false;
      var dlg = S.dialog({
        title: 'Напомнить о долге' + (x ? ' · ' + x.client_name : ''),
        body: '<div class="py-dlg"><div class="cs-msg-h">' + icon('send') + 'Уйдёт в ' + esc(p.channel === 'webchat' ? 'чат на сайте' : p.channel_title) + (p.lang === 'ky' ? ' · по-кыргызски' : '') + '</div>' +
          '<div class="cs-msg">' + esc(p.text) + '</div>' +
          (p.later ? '<div class="py-hint">Сейчас поздно — сообщение уйдёт утром, после 8:00.</div>' : '') +
          '<div class="py-hint">Одно напоминание в день по визиту. Клиент ответит в чат — увидите в «Чатах».</div></div>',
        foot: '<button class="btn" type="button" data-cancel>Отмена</button><button class="btn btn-primary" type="button" data-ok>' + icon('send') + 'Отправить</button>'
      });
      S.$('[data-cancel]', dlg.el).addEventListener('click', function () { dlg.close(); });
      S.$('[data-ok]', dlg.el).addEventListener('click', function () {
        if (busy) return; busy = true;
        S.api('/api/cash/debts/' + id + '/remind', { method: 'POST', body: { send: true } }).then(function (r2) {
          busy = false;
          if (!r2.ok) { S.toast(S.apiError(r2), 'error'); return; }
          S.toast(r2.data.later ? 'Напоминание уйдёт утром' : 'Напоминание отправляется');
          dlg.close(); refresh();
        });
      });
    });
  }
  function writeOffDialog(id) {
    var x = findDebt(id), busy = false;
    if (!x) return;
    var dlg = S.dialog({
      title: 'Списать долг · ' + x.client_name,
      body: '<div class="py-dlg"><div class="py-parts"><div><span>' + esc(x.services.join(' + ')) + ' · ' + esc(x.day_words) + '</span><b>' + esc(money(x.left)) + '</b></div></div>' +
        '<p class="py-hint">Визит перестанет быть долгом. Выручкой эта сумма не считается — так и будет видно в «Финансах». Запись останется в журнале действий.</p>' +
        '<label class="label" for="wo-r">Почему списываем</label>' +
        '<div class="py-quick">' + OFF_REASONS.map(function (t) { return '<button type="button" class="chip" data-wr="' + esc(t) + '">' + esc(t) + '</button>'; }).join('') + '</div>' +
        '<input class="input" id="wo-r" maxlength="300" style="margin-top:8px" placeholder="Своими словами"></div>',
      foot: '<button class="btn" type="button" data-cancel>Отмена</button><button class="btn btn-primary" type="button" data-ok>Списать ' + esc(money(x.left)) + '</button>'
    });
    var el = dlg.el;
    S.$$('[data-wr]', el).forEach(function (c) { c.addEventListener('click', function () { S.$('#wo-r', el).value = c.getAttribute('data-wr'); S.$$('[data-wr]', el).forEach(function (y) { y.classList.toggle('on', y === c); }); }); });
    S.$('[data-cancel]', el).addEventListener('click', function () { dlg.close(); });
    S.$('[data-ok]', el).addEventListener('click', function () {
      var why = (S.$('#wo-r', el).value || '').trim();
      if (why.length < 3) { S.toast('Напишите причину', 'error'); S.$('#wo-r', el).focus(); return; }
      if (busy) return; busy = true;
      S.api('/api/cash/debts/' + id + '/write-off', { method: 'POST', body: { reason: why } }).then(function (r) {
        busy = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast('Долг списан: ' + money(r.data.amount));
        dlg.close(); refresh();
      });
    });
  }

  function plural(n, one, few, many) {
    var a = Math.abs(n) % 100, b = a % 10;
    return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
  }

  // ── действия ──
  function openShift() {
    var v = num(S.$('#cs-opening', root).value);
    if (!(v >= 0)) { S.toast('Сколько наличных в кассе? Укажите число', 'error'); return; }
    var b = S.$('[data-open]', root); b.disabled = true;
    S.api('/api/cash/shifts/open', { method: 'POST', body: { opening_cash: v } }).then(function (r) {
      b.disabled = false;
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      S.toast(r.data.created ? 'Смена открыта' : 'Смена уже была открыта');
      refresh();
    });
  }

  function moveDialog(kind) {
    var idem = P().newKey(), busy = false, reasons = kind === 'cash_out' ? OUT_REASONS : IN_REASONS;
    var dlg = S.dialog({
      title: kind === 'cash_out' ? 'Изъять из кассы' : 'Внести в кассу',
      body: '<div class="py-dlg"><label class="label" for="mv-a">Сумма наличными</label>' +
        '<div class="py-amount"><input class="input" id="mv-a" inputmode="decimal" autocomplete="off" placeholder="0"><span>сом</span></div>' +
        (kind === 'cash_out' ? '<div class="py-hint">В кассе по чекам ' + esc(money(st.state.shift.numbers.expected_cash)) + '</div>' : '') +
        '<label class="label" for="mv-c">' + (kind === 'cash_out' ? 'На что или кому' : 'Комментарий') + '</label>' +
        '<div class="py-quick">' + reasons.map(function (x) { return '<button type="button" class="chip" data-mr="' + esc(x) + '">' + esc(x) + '</button>'; }).join('') + '</div>' +
        '<input class="input" id="mv-c" maxlength="300" style="margin-top:8px" placeholder="' + (kind === 'cash_out' ? 'Например: вода и салфетки' : 'Необязательно') + '"></div>',
      foot: '<button class="btn" type="button" data-cancel>Отмена</button><button class="btn btn-primary" type="button" data-ok>' + (kind === 'cash_out' ? 'Изъять' : 'Внести') + '</button>'
    });
    var el = dlg.el;
    S.$$('[data-mr]', el).forEach(function (c) { c.addEventListener('click', function () { S.$('#mv-c', el).value = c.getAttribute('data-mr'); S.$$('[data-mr]', el).forEach(function (x) { x.classList.toggle('on', x === c); }); }); });
    S.$('[data-cancel]', el).addEventListener('click', function () { dlg.close(); });
    setTimeout(function () { try { S.$('#mv-a', el).focus(); } catch (e) { /* ignore */ } }, 60);
    S.$('[data-ok]', el).addEventListener('click', function () {
      if (busy) return;
      var a = num(S.$('#mv-a', el).value), c = (S.$('#mv-c', el).value || '').trim();
      if (!(a > 0)) { S.toast('Укажите сумму', 'error'); return; }
      if (kind === 'cash_out' && c.length < 2) { S.toast('Напишите, на что или кому выдали деньги', 'error'); return; }
      busy = true;
      S.api('/api/cash/moves', { method: 'POST', body: { kind: kind, amount: a, comment: c || null, idem_key: idem } }).then(function (r) {
        busy = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast((kind === 'cash_out' ? 'Изъято: ' : 'Внесено: ') + money(a));
        dlg.close(); refresh();
      });
    });
  }

  function findShift(id) {
    var s = st.state;
    if (s.shift && s.shift.id === id) return s.shift;
    return (s.open_shifts || []).concat(st.shifts || []).filter(function (x) { return x.id === id; })[0];
  }

  function closeDialog(id) {
    var sh = findShift(id);
    if (!sh) return;
    var n = sh.numbers || {}, busy = false;
    var dlg = S.dialog({
      title: 'Закрыть смену' + (sh.cashier ? ' · ' + sh.cashier : ''),
      body: '<div class="py-dlg">' +
        '<div class="py-parts"><div><span>При открытии</span><b>' + esc(money(n.opening_cash)) + '</b></div>' +
        '<div><span>Наличные оплаты (минус возвраты)</span><b>' + (n.by_method.cash < 0 ? '−' : '+') + esc(money(Math.abs(n.by_method.cash))) + '</b></div>' +
        (n.cash_in ? '<div><span>Внесено</span><b>+' + esc(money(n.cash_in)) + '</b></div>' : '') +
        (n.cash_out ? '<div><span>Изъято</span><b>−' + esc(money(n.cash_out)) + '</b></div>' : '') +
        '<div class="paid"><span>Должно быть в кассе</span><b>' + esc(money(n.expected_cash)) + '</b></div></div>' +
        '<label class="label" for="cl-c">Пересчитайте наличные в кассе</label>' +
        '<div class="py-amount"><input class="input" id="cl-c" inputmode="decimal" autocomplete="off" placeholder="0"><span>сом</span></div>' +
        '<div class="cs-diff" id="cl-diff"></div>' +
        '<label class="label" for="cl-n">Комментарий</label><input class="input" id="cl-n" maxlength="300" placeholder="Если не сходится — что случилось">' +
        '<div class="cs-closesum">За смену: ' + ['cash', 'card', 'qr', 'transfer'].filter(function (k) { return n.by_method[k]; }).map(function (k) { return MT[k] + ' ' + esc(money(n.by_method[k])); }).join(' · ') +
        ' · всего <b>' + esc(money(n.revenue)) + '</b></div></div>',
      foot: '<button class="btn" type="button" data-cancel>Отмена</button><button class="btn btn-primary" type="button" data-ok>Закрыть смену</button>'
    });
    var el = dlg.el, cI = S.$('#cl-c', el);
    function sync() {
      var c = num(cI.value), box = S.$('#cl-diff', el);
      if (!(c >= 0) || cI.value === '') { box.innerHTML = ''; return; }
      box.innerHTML = diffChip(Math.round((c - n.expected_cash) * 100) / 100);
    }
    cI.addEventListener('input', sync);
    setTimeout(function () { try { cI.focus(); } catch (e) { /* ignore */ } }, 60);
    S.$('[data-cancel]', el).addEventListener('click', function () { dlg.close(); });
    S.$('[data-ok]', el).addEventListener('click', function () {
      if (busy) return;
      var c = num(cI.value);
      if (cI.value === '' || !(c >= 0)) { S.toast('Сколько наличных в кассе? Пересчитайте и укажите', 'error'); cI.focus(); return; }
      busy = true;
      S.api('/api/cash/shifts/' + id + '/close', { method: 'POST', body: { counted_cash: c, note: (S.$('#cl-n', el).value || '').trim() || null } }).then(function (r) {
        busy = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        var d = r.data.shift.diff;
        S.toast('Смена закрыта' + (Math.abs(d) < 0.5 ? ' — касса сходится' : d < 0 ? ' — не хватает ' + money(-d) : ' — лишние ' + money(d)), Math.abs(d) < 0.5 ? undefined : 'error');
        dlg.close(); st.shifts = null; refresh();
      });
    });
  }

  function shiftDrawer(id) {
    var dr = S.drawer({ title: '<div style="font-weight:700">Смена</div>' });
    dr.body.classList.add('cs-dr');
    dr.body.innerHTML = '<div class="skeleton" style="height:200px;border-radius:12px"></div>';
    S.api('/api/cash/shifts/' + id).then(function (r) {
      if (!r.ok) { dr.body.innerHTML = '<div class="muted">' + esc(S.apiError(r)) + '</div>'; return; }
      var sh = r.data.shift, n = sh.numbers || {};
      dr.setTitle('<div style="font-weight:700">' + esc(sh.cashier) + '</div><div class="muted small">' + esc(shiftWhen(sh)) + ' · ' + esc(hoursText(sh.hours)) + '</div>');
      dr.body.innerHTML =
        '<div class="cs-dsum">' + (sh.status === 'open' ? '<span class="py-chip ok">Смена открыта</span>' : diffChip(sh.diff)) + '</div>' +
        '<div class="cs-line"><div><span>Выручка</span><b>' + esc(money(n.revenue)) + '</b></div><div><span>Чеков</span><b>' + (n.receipts || 0) + '</b></div>' +
        '<div><span>Возвраты</span><b>' + (n.refunds ? '−' : '') + esc(money(n.refunds)) + '</b></div></div>' + methodTiles(n.by_method) +
        '<div class="py-parts" style="margin-top:12px"><div><span>При открытии</span><b>' + esc(money(sh.opening_cash)) + '</b></div>' +
        (n.cash_in ? '<div><span>Внесено</span><b>+' + esc(money(n.cash_in)) + '</b></div>' : '') +
        (n.cash_out ? '<div><span>Изъято</span><b>−' + esc(money(n.cash_out)) + '</b></div>' : '') +
        '<div><span>Ожидалось наличными</span><b>' + esc(money(sh.expected_cash != null ? sh.expected_cash : n.expected_cash)) + '</b></div>' +
        (sh.counted_cash != null ? '<div class="paid"><span>Насчитали</span><b>' + esc(money(sh.counted_cash)) + '</b></div>' : '') + '</div>' +
        (sh.closed_by && sh.closed_by !== sh.cashier ? '<div class="muted small">Закрыл(а): ' + esc(sh.closed_by) + '</div>' : '') +
        (sh.note ? '<div class="cs-dnote">' + icon('message-circle') + esc(sh.note) + '</div>' : '') +
        (sh.status === 'open' && (st.state.can_manage || (st.state.shift && st.state.shift.id === sh.id)) ? '<button class="btn btn-primary" type="button" data-dclose>' + icon('lock') + 'Закрыть смену</button>' : '') +
        '<h3 class="cs-dh">Чеки</h3>' + receiptList(r.data.receipts || [], true);
      var b = S.$('[data-dclose]', dr.body);
      if (b) b.addEventListener('click', function () { dr.close(); if (!findShift(sh.id)) (st.shifts = st.shifts || []).push(sh); closeDialog(sh.id); });
      S.$$('[data-refund]', dr.body).forEach(function (x) { x.addEventListener('click', function () { refundFrom(r.data.receipts, x.getAttribute('data-refund'), function () { dr.close(); refresh(); }); }); });
    });
  }

  function refundFrom(list, id, done) {
    var rc = (list || []).filter(function (x) { return x.id === id; })[0];
    if (rc) P().refund(rc, { onDone: done || refresh, shift: st.state.shift, suggest: st.state.suggest_opening });
  }

  function onClick(e) {
    var t = e.target;
    var tb = t.closest('[data-tab]');
    if (tb) { st.tab = tb.getAttribute('data-tab'); try { history.replaceState(null, '', '#' + st.tab); } catch (x) { /* ignore */ } st.receipts = null; refresh(); return; }
    if (t.closest('[data-open]')) { openShift(); return; }
    var mv = t.closest('[data-move]');
    if (mv) { moveDialog(mv.getAttribute('data-move')); return; }
    var cl = t.closest('[data-close]');
    if (cl) { closeDialog(cl.getAttribute('data-close')); return; }
    var dd = t.closest('[data-day]');
    if (dd && !dd.disabled) {
      var d = new Date(st.day + 'T12:00:00+06:00'); d.setUTCDate(d.getUTCDate() + +dd.getAttribute('data-day'));
      st.day = keyOf(d); loadReceipts().then(draw); return;
    }
    if (t.closest('[data-rmore]')) { loadReceipts(true).then(draw); return; }
    if (t.closest('[data-smore]')) { loadShifts(true).then(draw); return; }
    var dp = t.closest('[data-dpay]');
    if (dp) { P().open(dp.getAttribute('data-dpay'), { onDone: function () { refresh(); } }); return; }
    var dr = t.closest('[data-dremind]');
    if (dr && !dr.disabled) { remindDialog(dr.getAttribute('data-dremind')); return; }
    var dof = t.closest('[data-doff]');
    if (dof) { writeOffDialog(dof.getAttribute('data-doff')); return; }
    var sr = t.closest('[data-shift]');
    if (sr) { shiftDrawer(sr.getAttribute('data-shift')); return; }
    var rf = t.closest('[data-refund]');
    if (rf) { refundFrom(st.receipts, rf.getAttribute('data-refund')); return; }
  }

  function start() {
    root = document.getElementById('cs');
    esc = S.esc; icon = S.icon;
    if (S.initMobileMenu) S.initMobileMenu();
    st.day = keyOf(new Date());
    var h = (location.hash || '').slice(1);
    if (['shift', 'debts', 'receipts', 'shifts'].indexOf(h) >= 0) st.tab = h;
    root.addEventListener('click', onClick);
    root.addEventListener('change', function (e) {
      if (e.target.id === 'cs-date' && e.target.value) { st.day = e.target.value; loadReceipts().then(draw); }
      if (e.target.id === 'cs-rep-on') saveReport({ on: e.target.checked });
      if (e.target.id === 'cs-rep-h') saveReport({ hour: +e.target.value });
    });
    refresh();
    document.addEventListener('visibilitychange', function () { if (!document.hidden && !document.querySelector('.vf-backdrop')) refresh(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
