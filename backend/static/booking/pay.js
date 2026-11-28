/**
 * Касса (Voksy Beauty, шаг 3.1): блок «Оплата» в карточке записи, окно «Принять оплату», окно «Возврат».
 * window.BookingPay — журнал записи (booking.js) и страница «Касса» (cash.js).
 *
 *   block(bookingId, box, {onChange}) — «К оплате / Оплачено / Осталось», кнопки, чеки визита с «Вернуть»
 *   open(bookingId, {amount, prepay, onDone}) — сумма, способ (наличные / карта / QR / перевод), сдача,
 *                       «разделить на два способа», комментарий; наличные без смены — смена открывается тут же
 *   refund(receipt, {onDone}) — сумма (не больше оплаченного по чеку), способ, причина
 *
 * Одно окно = один ключ операции (idem_key): повторное нажатие, обрыв сети и повтор не проводят оплату дважды.
 * API: /api/cash/* (services/cashbox.py).
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var TZ = 'Asia/Bishkek';
  var METHODS = [['cash', 'Наличные', 'banknote'], ['card', 'Карта', 'credit-card'], ['qr', 'QR', 'qr-code'], ['transfer', 'Перевод', 'smartphone']];
  var MT = { cash: 'Наличные', card: 'Карта', qr: 'QR', transfer: 'Перевод' };
  var STATE = { unpaid: ['Не оплачено', 'bad'], partial: ['Оплачено частично', 'warn'], paid: ['Оплачено', 'ok'], over: ['Переплата', 'warn'] };
  var REFUND_REASONS = ['Клиент отменил', 'Ошибка при оплате', 'Скидка', 'Недоволен услугой'];

  function money(v) { return (Math.round(Number(v || 0) * 100) / 100).toLocaleString('ru-RU') + ' сом'; }
  function num(v) {
    var n = parseFloat(String(v == null ? '' : v).replace(/\s/g, '').replace(',', '.'));
    return isFinite(n) ? Math.round(n * 100) / 100 : NaN;
  }
  function newKey() {
    try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, ''); } catch (e) { /* ignore */ }
    return Date.now().toString(36) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
  }
  function hm(iso) { return new Date(iso).toLocaleTimeString('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }); }
  function dm(iso) { return new Date(iso).toLocaleDateString('ru-RU', { timeZone: TZ, day: 'numeric', month: 'short' }); }
  function errOf(r) {
    var m = r && r.data && r.data.message;
    return { code: m && typeof m === 'object' ? m.code : null, text: S.apiError(r) };
  }
  function vibrate() { try { if (navigator.vibrate) navigator.vibrate(18); } catch (e) { /* ignore */ } }

  function methodTiles(name, cur, only) {
    return '<div class="py-methods" role="radiogroup">' + METHODS.filter(function (m) { return !only || only.indexOf(m[0]) >= 0; }).map(function (m) {
      return '<button type="button" class="py-m' + (m[0] === cur ? ' on' : '') + '" role="radio" aria-checked="' + (m[0] === cur) + '" data-' + name + '="' + m[0] + '">' +
        icon(m[2]) + '<span>' + m[1] + '</span></button>';
    }).join('') + '</div>';
  }
  function linesText(rc) {
    return (rc.lines || []).map(function (l) { return esc(l.title) + ' ' + esc(money(l.amount)); }).join(' + ');
  }

  // Строка чека: оплата (+ «вернули …», «Вернуть»), возврат, внесение, изъятие
  function receiptRow(rc, opts) {
    opts = opts || {};
    var minus = rc.sign < 0;
    var kind = rc.kind === 'payment' ? (rc.prepay ? 'Предоплата' : 'Оплата') : rc.kind_title;
    var who = [opts.withDate ? dm(rc.at) + ' ' + hm(rc.at) : hm(rc.at), rc.by].filter(Boolean).join(' · ');
    var title = opts.withClient ? (rc.client_name || rc.title || rc.comment || kind) : kind;
    var sub = opts.withClient ? [kind, rc.client_name ? rc.title : ''].filter(Boolean).join(' · ') : (rc.kind === 'payment' ? '' : rc.comment);
    return '<div class="py-rc ' + esc(rc.kind) + '" data-rc="' + esc(rc.id) + '">' +
      '<span class="py-rc-ic">' + icon(rc.kind === 'refund' ? 'rotate-ccw' : rc.kind === 'cash_in' ? 'arrow-down-to-line' : rc.kind === 'cash_out' ? 'arrow-up-from-line' : 'receipt-text') + '</span>' +
      '<div class="py-rc-main"><div class="t">' + esc(title) + '</div>' +
      '<div class="s">' + esc(who) + (sub ? ' · ' + esc(sub) : '') + (rc.kind === 'payment' || rc.kind === 'refund' ? ' · ' + linesText(rc) : '') + '</div>' +
      (rc.kind === 'refund' && rc.comment && !opts.withClient ? '<div class="s">Причина: ' + esc(rc.comment) + '</div>' : '') +
      (rc.refunded ? '<div class="s warn">Вернули ' + esc(money(rc.refunded)) + '</div>' : '') + '</div>' +
      '<div class="py-rc-r"><b class="' + (minus ? 'neg' : '') + '">' + (minus ? '−' : '') + esc(money(rc.total)) + '</b>' +
      (opts.canRefund && rc.kind === 'payment' && rc.refundable > 0 ? '<button type="button" class="btn btn-ghost btn-sm" data-refund="' + esc(rc.id) + '">Вернуть</button>' : '') +
      (opts.withClient && rc.booking_id ? '<a class="py-open" href="/static/booking.html?open=' + esc(rc.booking_id) + (rc.booking_date ? '&date=' + esc(rc.booking_date) : '') + '">Запись</a>' : '') +
      '</div></div>';
  }

  // ── блок «Оплата» в карточке записи ──
  function block(bookingId, box, opts) {
    opts = opts || {};
    box.innerHTML = '<div class="py-card"><div class="skeleton" style="height:72px;border-radius:12px"></div></div>';
    var api = { money: null, reload: load };
    function load() {
      return S.api('/api/cash/booking/' + encodeURIComponent(bookingId)).then(function (r) {
        if (!r.ok) { box.innerHTML = r.status === 403 ? '' : '<div class="py-card muted small">' + esc(S.apiError(r)) + '</div>'; return null; }
        api.money = r.data; render(r.data); return r.data;
      });
    }
    function render(m) {
      var stt = STATE[m.state] || STATE.unpaid;
      var future = m.parts.some(function (p) { return (p.status === 'booked' || p.status === 'confirmed') && new Date(p.start) > new Date(); });
      var canPay = m.can_take !== false && (m.left > 0 || !m.priced) && m.parts.some(function (p) { return p.status !== 'cancelled'; });
      var showPrepay = canPay && future && m.prepay_due > 0 && m.prepay_due < m.left;
      box.innerHTML = '<div class="py-card">' +
        '<div class="py-head"><b>' + icon('wallet') + 'Оплата</b><span class="py-chip ' + stt[1] + '">' + stt[0] + '</span></div>' +
        (m.priced ? '<div class="py-sum"><div><span>К оплате</span><b>' + money(m.due) + '</b></div>' +
          '<div><span>Оплачено</span><b>' + money(m.paid) + '</b></div>' +
          '<div class="' + (m.left > 0 ? 'left' : 'done') + '"><span>Осталось</span><b>' + money(m.left) + '</b></div></div>'
          : '<div class="py-sum one"><div><span>Цена не указана</span><b>Оплачено ' + money(m.paid) + '</b></div></div>') +
        (canPay ? '<div class="py-acts"><button class="btn btn-primary btn-sm" type="button" data-pay>' + icon('banknote') +
          (future && m.paid === 0 ? 'Принять оплату' : 'Принять оплату') + (m.left > 0 ? ' · ' + money(m.left) : '') + '</button>' +
          (showPrepay ? '<button class="btn btn-sm" type="button" data-prepay>' + icon('wallet') + 'Предоплата ' + money(m.prepay_due) + '</button>' : '') + '</div>' : '') +
        (m.receipts.length ? '<div class="py-rcs">' + m.receipts.map(function (rc) { return receiptRow(rc, { canRefund: m.can_take !== false, withDate: true }); }).join('') + '</div>' : '') +
        '</div>';
      var pb = S.$('[data-pay]', box), pp = S.$('[data-prepay]', box);
      if (pb) pb.addEventListener('click', function () { open(bookingId, { money: m, onDone: done }); });
      if (pp) pp.addEventListener('click', function () { open(bookingId, { money: m, amount: m.prepay_due, prepay: true, onDone: done }); });
      S.$$('[data-refund]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          var rc = m.receipts.filter(function (x) { return x.id === b.getAttribute('data-refund'); })[0];
          if (rc) refund(rc, { onDone: done, shift: m.shift, suggest: m.suggest_opening });
        });
      });
    }
    function done(m2) {
      load();
      if (opts.onChange) opts.onChange(m2);
    }
    load();
    return api;
  }

  // Смена не открыта, а нужны наличные: поле «в кассе сейчас» прямо в окне
  function shiftBox(suggest) {
    return '<div class="py-shift" hidden><div class="h">' + icon('lock') + 'Смена не открыта</div>' +
      '<p>Наличные принимаются в открытой смене. Откроем её сейчас — сколько наличных в кассе?</p>' +
      '<div class="py-amount sm"><input class="input" id="py-open" inputmode="decimal" autocomplete="off" value="' + (suggest != null ? esc(String(suggest)) : '0') + '"><span>сом</span></div></div>';
  }
  function openShiftThen(dlgEl, fn) {
    var v = num(S.$('#py-open', dlgEl) ? S.$('#py-open', dlgEl).value : 0);
    if (!(v >= 0)) { S.toast('Сколько наличных в кассе? Укажите число', 'error'); return Promise.resolve(false); }
    return S.api('/api/cash/shifts/open', { method: 'POST', body: { opening_cash: v } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return false; }
      S.toast('Смена открыта');
      return fn();
    });
  }

  // ── окно «Принять оплату» ──
  function open(bookingId, opts) {
    opts = opts || {};
    if (!opts.money) {
      S.api('/api/cash/booking/' + encodeURIComponent(bookingId)).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        opts.money = r.data; open(bookingId, opts);
      });
      return;
    }
    var m = opts.money, idem = newKey(), busy = false;
    var live = m.parts.filter(function (p) { return p.status !== 'cancelled'; });
    var start = opts.amount != null ? opts.amount : (m.left > 0 ? m.left : '');
    var method = 'cash', method2 = 'card', split = false;
    var dlg = S.dialog({
      title: (opts.prepay ? 'Предоплата' : 'Оплата') + (m.client_name ? ' · ' + m.client_name : ''),
      body: '<div class="py-dlg">' +
        (live.length > 1 || m.paid > 0 ? '<div class="py-parts">' + live.map(function (p) {
          return '<div><span>' + esc(p.service) + (p.master ? ' · ' + esc(p.master) : '') + '</span><b>' + (p.left > 0 ? esc(money(p.left)) : '<i>оплачено</i>') + '</b></div>';
        }).join('') + (m.paid > 0 ? '<div class="paid"><span>Уже оплачено</span><b>' + esc(money(m.paid)) + '</b></div>' : '') + '</div>' : '') +
        '<label class="label" for="py-a">Сумма</label>' +
        '<div class="py-amount"><input class="input" id="py-a" inputmode="decimal" autocomplete="off" value="' + esc(String(start)) + '"><span>сом</span></div>' +
        '<div class="py-quick">' + (m.left > 0 ? '<button type="button" class="chip" data-q="' + m.left + '">Всё · ' + esc(money(m.left)) + '</button>' : '') +
        (m.prepay_due > 0 && m.prepay_due < m.left ? '<button type="button" class="chip" data-q="' + m.prepay_due + '">Предоплата · ' + esc(money(m.prepay_due)) + '</button>' : '') + '</div>' +
        '<div class="label">Способ оплаты</div>' + methodTiles('m', method) +
        '<div class="py-cash"><label class="label" for="py-got">Клиент дал наличными</label>' +
        '<div class="py-got"><div class="py-amount sm"><input class="input" id="py-got" inputmode="decimal" autocomplete="off" placeholder="—"><span>сом</span></div>' +
        '<div class="py-change" id="py-change"></div></div></div>' +
        '<div class="py-split" hidden><div class="label">Второй способ</div>' + methodTiles('m2', method2) +
        '<div class="py-split-row"><div class="py-amount sm"><input class="input" id="py-a2" inputmode="decimal" autocomplete="off" placeholder="0"><span>сом</span></div>' +
        '<div class="py-split-sum" id="py-split-sum"></div></div></div>' +
        '<button type="button" class="py-link" data-split>' + icon('plus') + 'Разделить на два способа</button>' +
        shiftBox(m.suggest_opening) +
        '<details class="py-note"><summary>Комментарий</summary><input class="input" id="py-c" maxlength="300" placeholder="Например: чек отправили в WhatsApp"></details>' +
        '</div>',
      foot: '<button class="btn" type="button" data-cancel>Отмена</button><button class="btn btn-primary" type="button" data-ok></button>'
    });
    var el = dlg.el;
    var aI = S.$('#py-a', el), a2I = S.$('#py-a2', el), gotI = S.$('#py-got', el), okB = S.$('[data-ok]', el);
    function total() { return num(aI.value); }
    function second() { return split ? num(a2I.value) : 0; }
    function cashPart() {
      var t = total(), s2 = second() || 0;
      return (method === 'cash' ? t - s2 : 0) + (split && method2 === 'cash' ? s2 : 0);
    }
    function needShift() { return !m.shift && cashPart() > 0; }
    function sync() {
      var t = total();
      okB.textContent = (needShift() ? 'Открыть смену и принять' : 'Принять') + (t > 0 ? ' ' + money(t) : '');
      S.$('.py-shift', el).hidden = !needShift();
      var cp = cashPart();
      S.$('.py-cash', el).hidden = !(cp > 0);
      var got = num(gotI.value), ch = S.$('#py-change', el);
      if (cp > 0 && got > 0) {
        var diff = Math.round((got - cp) * 100) / 100;
        ch.innerHTML = diff >= 0 ? 'Сдача: <b>' + esc(money(diff)) + '</b>' : '<span class="neg">Не хватает ' + esc(money(-diff)) + '</span>';
      } else ch.innerHTML = '';
      if (split) {
        var s2 = second() || 0;
        S.$('#py-split-sum', el).innerHTML = t > 0 ? esc(MT[method]) + ' <b>' + esc(money(Math.max(t - s2, 0))) + '</b> + ' + esc(MT[method2]) + ' <b>' + esc(money(s2)) + '</b>' : '';
      }
      S.$$('.py-quick .chip', el).forEach(function (c) { c.classList.toggle('on', num(c.getAttribute('data-q')) === t); });
    }
    function pick(attr, val) {
      S.$$('[data-' + attr + ']', el).forEach(function (b) { var on = b.getAttribute('data-' + attr) === val; b.classList.toggle('on', on); b.setAttribute('aria-checked', on); });
    }
    S.$$('[data-m]', el).forEach(function (b) { b.addEventListener('click', function () { method = b.getAttribute('data-m'); pick('m', method); sync(); }); });
    S.$$('[data-m2]', el).forEach(function (b) { b.addEventListener('click', function () { method2 = b.getAttribute('data-m2'); pick('m2', method2); sync(); }); });
    S.$$('[data-q]', el).forEach(function (c) { c.addEventListener('click', function () { aI.value = c.getAttribute('data-q'); sync(); }); });
    S.$('[data-split]', el).addEventListener('click', function (e) {
      split = !split;
      S.$('.py-split', el).hidden = !split;
      e.currentTarget.innerHTML = split ? icon('minus') + 'Одним способом' : icon('plus') + 'Разделить на два способа';
      if (split) { if (method2 === method) { method2 = method === 'cash' ? 'card' : 'cash'; pick('m2', method2); } a2I.focus(); }
      sync();
    });
    [aI, a2I, gotI].forEach(function (i) { i.addEventListener('input', sync); });
    S.$('[data-cancel]', el).addEventListener('click', function () { dlg.close(); });
    okB.addEventListener('click', submit);
    aI.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
    sync();
    setTimeout(function () { try { aI.focus(); aI.select(); } catch (e) { /* ignore */ } }, 60);

    function submit() {
      if (busy) return;
      var t = total(), s2 = second();
      if (!(t > 0)) { S.toast('Укажите сумму', 'error'); aI.focus(); return; }
      if (m.priced && m.left > 0 && t > m.left + 0.004) { S.toast('К оплате осталось ' + money(m.left), 'error'); return; }
      var lines;
      if (split) {
        if (!(s2 > 0) || s2 >= t) { S.toast('Вторая сумма — больше нуля и меньше общей', 'error'); a2I.focus(); return; }
        if (method2 === method) { S.toast('Выберите другой второй способ', 'error'); return; }
        lines = [{ method: method, amount: Math.round((t - s2) * 100) / 100 }, { method: method2, amount: s2 }];
      } else lines = [{ method: method, amount: t }];
      busy = true; okB.disabled = true;
      var go = function () {
        return S.api('/api/cash/pay', { method: 'POST', body: { booking_id: bookingId, lines: lines, idem_key: idem, prepay: opts.prepay ? true : null,
          comment: (S.$('#py-c', el).value || '').trim() || null } }).then(function (r) {
          busy = false; okB.disabled = false;
          if (!r.ok) {
            var e = errOf(r);
            if (e.code === 'shift_required') { m.shift = null; sync(); S.toast('Откройте смену, чтобы принять наличные', 'error'); return; }
            S.toast(e.text, 'error'); return;
          }
          vibrate();
          S.toast((r.data.created ? (opts.prepay ? 'Предоплата принята: ' : 'Оплата принята: ') : 'Эта оплата уже проведена: ') + money(r.data.receipt.total));
          dlg.close();
          if (opts.onDone) opts.onDone(r.data.money);
        }, function () { busy = false; okB.disabled = false; S.toast('Нет связи — нажмите ещё раз, оплата не задвоится', 'error'); });
      };
      if (needShift()) {
        openShiftThen(el, function () { m.shift = { id: 'new' }; return go(); }).then(function (okv) { if (okv === false) { busy = false; okB.disabled = false; } });
      } else go();
    }
  }

  // ── окно «Возврат» ──
  function refund(rc, opts) {
    opts = opts || {};
    var idem = newKey(), busy = false;
    var method = (rc.lines && rc.lines[0] && rc.lines[0].method) || 'cash';
    function withState(fn) {
      if (opts.shift !== undefined) return fn(opts.shift, opts.suggest);
      S.api('/api/cash/state').then(function (r) { fn(r.ok ? r.data.shift : null, r.ok ? r.data.suggest_opening : null); });
    }
    withState(function (shift, suggest) {
      var dlg = S.dialog({
        title: 'Возврат' + (rc.client_name ? ' · ' + rc.client_name : ''),
        body: '<div class="py-dlg">' +
          '<div class="py-parts"><div><span>' + esc(rc.title || 'Оплата') + ' · ' + esc(dm(rc.at)) + '</span><b>' + esc(money(rc.total)) + '</b></div>' +
          (rc.refunded ? '<div class="paid"><span>Уже вернули</span><b>' + esc(money(rc.refunded)) + '</b></div>' : '') + '</div>' +
          '<label class="label" for="rf-a">Сумма возврата</label>' +
          '<div class="py-amount"><input class="input" id="rf-a" inputmode="decimal" autocomplete="off" value="' + esc(String(rc.refundable)) + '"><span>сом</span></div>' +
          '<div class="py-hint">Можно вернуть до ' + esc(money(rc.refundable)) + '</div>' +
          '<div class="label">Как вернули</div>' + methodTiles('rm', method) +
          '<label class="label" for="rf-r">Причина</label>' +
          '<div class="py-quick">' + REFUND_REASONS.map(function (x) { return '<button type="button" class="chip" data-rr="' + esc(x) + '">' + esc(x) + '</button>'; }).join('') + '</div>' +
          '<input class="input" id="rf-r" maxlength="300" placeholder="Своими словами">' +
          shiftBox(suggest) + '</div>',
        foot: '<button class="btn" type="button" data-cancel>Отмена</button><button class="btn btn-danger btn-solid" type="button" data-ok></button>'
      });
      var el = dlg.el, aI = S.$('#rf-a', el), rI = S.$('#rf-r', el), okB = S.$('[data-ok]', el);
      function needShift() { return !shift && method === 'cash'; }
      function sync() {
        var a = num(aI.value);
        okB.textContent = (needShift() ? 'Открыть смену и вернуть' : 'Вернуть') + (a > 0 ? ' ' + money(a) : '');
        S.$('.py-shift', el).hidden = !needShift();
      }
      S.$$('[data-rm]', el).forEach(function (b) {
        b.addEventListener('click', function () {
          method = b.getAttribute('data-rm');
          S.$$('[data-rm]', el).forEach(function (x) { x.classList.toggle('on', x === b); x.setAttribute('aria-checked', x === b); });
          sync();
        });
      });
      S.$$('[data-rr]', el).forEach(function (c) {
        c.addEventListener('click', function () {
          rI.value = c.getAttribute('data-rr');
          S.$$('[data-rr]', el).forEach(function (x) { x.classList.toggle('on', x === c); });
        });
      });
      aI.addEventListener('input', sync);
      S.$('[data-cancel]', el).addEventListener('click', function () { dlg.close(); });
      okB.addEventListener('click', function () {
        if (busy) return;
        var a = num(aI.value), reason = (rI.value || '').trim();
        if (!(a > 0)) { S.toast('Укажите сумму', 'error'); return; }
        if (a > rc.refundable + 0.004) { S.toast('Вернуть можно не больше ' + money(rc.refundable), 'error'); return; }
        if (reason.length < 2) { S.toast('Напишите причину возврата', 'error'); rI.focus(); return; }
        busy = true; okB.disabled = true;
        var go = function () {
          return S.api('/api/cash/receipts/' + rc.id + '/refund', { method: 'POST', body: { amount: a, reason: reason, method: method, idem_key: idem } }).then(function (r) {
            busy = false; okB.disabled = false;
            if (!r.ok) {
              var e = errOf(r);
              if (e.code === 'shift_required') { shift = null; sync(); }
              S.toast(e.text, 'error'); return;
            }
            S.toast('Возврат проведён: ' + money(a));
            dlg.close();
            if (opts.onDone) opts.onDone(r.data);
          }, function () { busy = false; okB.disabled = false; S.toast('Нет связи — нажмите ещё раз, возврат не задвоится', 'error'); });
        };
        if (needShift()) openShiftThen(el, function () { shift = { id: 'new' }; return go(); }).then(function (okv) { if (okv === false) { busy = false; okB.disabled = false; } });
        else go();
      });
      sync();
    });
  }

  window.BookingPay = { block: block, open: open, refund: refund, receiptRow: receiptRow, money: money, num: num,
    newKey: newKey, methodTiles: methodTiles, errOf: errOf, METHODS: METHODS, MT: MT };
})();
