/**
 * Лист ожидания (Voksy Beauty, шаг 2.4): экран «Лист ожидания» в /static/booking.html.
 *
 * Очередь клиентов, которым не нашлось времени: услуга, мастер (или любой), дни, время суток. Освободилось
 * подходящее окно — система сама пишет ПЕРВОМУ в очереди (30 минут на ответ, потом следующему). Здесь видно, кто
 * ждёт и под каким номером, кому что предложено и сколько осталось, кто записался; «Предложить сейчас» (найти окно
 * и написать без ожидания), «Изменить», «Убрать», позвонить / WhatsApp. Окно «В лист ожидания» — отсюда и из окна
 * новой записи («на этот день свободного времени нет»).
 * API: GET/POST /api/booking/waitlist, PATCH/DELETE /api/booking/waitlist/{id}, POST /api/booking/waitlist/{id}/offer.
 * Общее — window.BookingWaitlist {open(ctx), addDialog(pre, onDone)}.
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var ctx = null;            // {root, back, data, reload}
  var st = { tab: 'active', data: null, busy: false };
  var TABS = [['active', 'Ждут'], ['booked', 'Записались'], ['closed', 'Закрытые']];
  var TOD = [['', '', 'Любое время'], ['', '12:00', 'Утро, до 12'], ['12:00', '17:00', 'День, 12–17'], ['17:00', '', 'Вечер, после 17']];
  var MON = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

  function plural(n, a, b, c) {
    var n100 = Math.abs(n) % 100, n10 = n100 % 10;
    if (n100 > 10 && n100 < 20) return c;
    if (n10 > 1 && n10 < 5) return b;
    if (n10 === 1) return a;
    return c;
  }
  function todayKey() { return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Bishkek' }); }
  function addDays(k, n) { var p = k.split('-'), d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2] + n)); return d.toISOString().slice(0, 10); }
  function digits(p) { return String(p || '').replace(/\D/g, ''); }

  function load() {
    st.busy = true;
    return S.api('/api/booking/waitlist?status=' + st.tab).then(function (r) {
      st.busy = false;
      if (!r.ok) { ctx.root.innerHTML = '<div class="bk-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      st.data = r.data;
      draw();
    });
  }
  function open(c) {
    ctx = c;
    if (!st.data) ctx.root.innerHTML = '<div class="ms"><div class="skeleton" style="height:56px;border-radius:14px"></div><div class="skeleton" style="height:300px;border-radius:16px"></div></div>';
    else draw();
    load();
  }

  function card(it) {
    var phone = it.client_phone && !/^(web|tg|ig|wa):/.test(it.client_phone) ? it.client_phone : '';
    var status = it.status === 'offered' && it.offer
      ? '<span class="st-badge info">' + icon('send') + 'Предложено: ' + esc(it.offer.label) + (it.offer.resource ? ' · ' + esc(it.offer.resource) : '') +
        (it.offer.minutes_left != null ? ' · ждём ответа ' + it.offer.minutes_left + ' мин' : '') + '</span>'
      : '<span class="st-badge ' + ({ waiting: 'warn', booked: 'ok' }[it.status] || '') + '">' + esc(it.status_title) + '</span>';
    var acts = '';
    if (it.status === 'waiting') acts += '<button class="btn btn-sm btn-primary" type="button" data-wl-offer="' + esc(it.id) + '">' + icon('send') + '<span>Предложить сейчас</span></button>';
    if (it.status === 'waiting' || it.status === 'offered') {
      acts += '<button class="btn btn-sm" type="button" data-wl-edit="' + esc(it.id) + '">' + icon('pencil') + '<span>Изменить</span></button>' +
        '<button class="btn btn-sm btn-ghost" type="button" data-wl-del="' + esc(it.id) + '">' + icon('x') + '<span>Убрать</span></button>';
    }
    if (it.status === 'booked' && it.booking_id) acts += '<a class="btn btn-sm" href="/static/booking.html?open=' + encodeURIComponent(it.booking_id) + '">' + icon('calendar') + '<span>Открыть запись</span></a>';
    if (phone) acts += '<a class="btn btn-sm btn-ghost btn-icon" href="tel:' + esc(phone) + '" title="Позвонить" aria-label="Позвонить">' + icon('phone') + '</a>' +
      '<a class="btn btn-sm btn-ghost btn-icon" href="https://wa.me/' + digits(phone) + '" target="_blank" rel="noopener" title="WhatsApp" aria-label="WhatsApp">' + icon('message-circle') + '</a>';
    return '<div class="wl-it' + (it.status === 'offered' ? ' off' : '') + '">' +
      (it.position ? '<span class="wl-pos" title="Место в очереди">' + it.position + '</span>' : '') +
      '<div class="wl-main"><div class="wl-top"><b>' + esc(it.client_name || 'Клиент') + '</b>' +
      (phone ? '<span class="muted small">' + esc(S.fmtPhone ? S.fmtPhone(phone) : phone) + '</span>' : '') + status + '</div>' +
      '<div class="wl-what">' + icon('sparkles') + esc(it.service) + ' · ' + esc(it.resource || 'любой мастер') + '</div>' +
      '<div class="wl-what">' + icon('calendar') + esc(it.period) + '</div>' +
      (it.note ? '<div class="wl-note">«' + esc(it.note) + '»</div>' : '') +
      (it.hint && it.status === 'waiting' ? '<div class="wl-hint">' + icon('info') + esc(it.hint) + '</div>' : '') +
      '<div class="wl-meta muted small">' + esc(it.source_title) + (it.offers_count ? ' · предлагали ' + it.offers_count + ' ' + plural(it.offers_count, 'раз', 'раза', 'раз') : '') + '</div>' +
      '<div class="wl-acts">' + acts + '</div></div></div>';
  }

  function draw() {
    var d = st.data, c = d.counts || {};
    var head = '<div class="ms-head"><button class="btn btn-sm" type="button" data-wl-back>' + icon('arrow-left') + '<span>К журналу</span></button>' +
      '<div class="ms-title"><h2>Лист ожидания</h2><div class="muted small">' + (c.active ? c.active + ' ' + plural(c.active, 'клиент ждёт', 'клиента ждут', 'клиентов ждут') +
        (c.offered ? ' · предложено ' + c.offered : '') : 'Пока никого') + '</div></div><div class="bk-spacer"></div>' +
      '<button class="btn btn-primary btn-sm" type="button" data-wl-add>' + icon('plus') + '<span>В лист ожидания</span></button></div>';
    var tip = '<div class="ms-tip">' + icon('hourglass') + '<div><b>Освободится время — предложим сами</b><span>Отмена, перенос, новый график — система находит окно ' +
      'и пишет первому в очереди: ответить можно 30 минут, потом окно уходит следующему. Сообщения — с 8:00 до 21:00.</span></div></div>';
    var tabs = '<div class="st-colbar wl-tabs">' + TABS.map(function (t) {
      var n = t[0] === 'active' ? c.active : t[0] === 'booked' ? c.booked : (c.cancelled || 0) + (c.expired || 0);
      return '<button type="button" class="st-colchip' + (st.tab === t[0] ? ' on' : '') + '" data-wl-tab="' + t[0] + '">' + esc(t[1]) + (n ? ' · ' + n : '') + '</button>';
    }).join('') + '</div>';
    var items = d.items || [];
    var list = items.length ? '<div class="wl-list">' + items.map(card).join('') + '</div>'
      : '<div class="bk-card"><div class="bk-empty">' + icon('hourglass') + (st.tab === 'active'
        ? '<b style="display:block;color:var(--vf-text);margin-bottom:6px">В листе ожидания никого</b>Нет времени для клиента — нажмите «В лист ожидания»: освободится подходящее окно, мы предложим его сами. Клиенты могут встать в лист и со страницы салона.'
        : 'Здесь пока пусто.') + '</div></div>';
    ctx.root.innerHTML = '<div class="ms wl">' + head + tip + tabs + list + '</div>';
    wire();
  }

  function byId(id) { return ((st.data && st.data.items) || []).filter(function (x) { return x.id === id; })[0]; }
  function wire() {
    var r = ctx.root;
    S.$('[data-wl-back]', r).addEventListener('click', function () { ctx.back(); });
    S.$('[data-wl-add]', r).addEventListener('click', function () { addDialog({}, function () { load(); }); });
    S.$$('[data-wl-tab]', r).forEach(function (b) { b.addEventListener('click', function () { st.tab = b.getAttribute('data-wl-tab'); load(); }); });
    S.$$('[data-wl-offer]', r).forEach(function (b) {
      b.addEventListener('click', function () {
        b.disabled = true;
        S.api('/api/booking/waitlist/' + b.getAttribute('data-wl-offer') + '/offer', { method: 'POST' }).then(function (res) {
          b.disabled = false;
          if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
          S.toast(res.data.offered ? 'Предложили: ' + res.data.offer.label + ' — ждём ответа 30 минут' : res.data.reason, res.data.offered ? 'success' : 'info');
          load();
        });
      });
    });
    S.$$('[data-wl-edit]', r).forEach(function (b) { b.addEventListener('click', function () { var it = byId(b.getAttribute('data-wl-edit')); if (it) addDialog({ entry: it }, function () { load(); }); }); });
    S.$$('[data-wl-del]', r).forEach(function (b) {
      b.addEventListener('click', function () {
        var it = byId(b.getAttribute('data-wl-del'));
        S.confirm({ title: 'Убрать из листа ожидания?', message: (it ? it.client_name + ' · ' + it.service : ''), confirmText: 'Убрать' }).then(function (ok) { if (ok) del(b); });
      });
    });
  }
  function del(b) {
    S.api('/api/booking/waitlist/' + b.getAttribute('data-wl-del'), { method: 'DELETE' }).then(function (res) {
      if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
      S.toast('Убрали из листа ожидания', 'success');
      load();
    });
  }

  // ── окно «В лист ожидания» ──
  function addDialog(pre, onDone) {
    pre = pre || {};
    var data = pre.data || (ctx && ctx.data && ctx.data()) || {};
    var e = pre.entry || null;
    var services = data.services || [], resources = data.resources || [];
    var today = todayKey();
    var f = {
      contact_id: e ? e.contact_id : (pre.client && pre.client.id) || null,
      name: e ? e.client_name : (pre.client && pre.client.name) || '', phone: e ? e.client_phone : (pre.client && pre.client.phone) || '',
      service_id: e ? e.service_id : pre.service_id || (services[0] && services[0].id) || '',
      resource_id: e ? (e.resource_id || '') : pre.resource_id || '',
      date_from: e ? e.date_from : pre.date || today, date_to: e ? e.date_to : addDays(pre.date || today, pre.date ? 0 : 6),
      time_from: e ? e.time_from || '' : '', time_to: e ? e.time_to || '' : '', note: e ? e.note : ''
    };
    if (f.date_from < today) f.date_from = today;
    if (f.date_to < f.date_from) f.date_to = f.date_from;
    function svcOpts() {
      return services.map(function (s) { return '<option value="' + esc(s.id) + '"' + (s.id === f.service_id ? ' selected' : '') + '>' + esc(s.title) + '</option>'; }).join('');
    }
    function resOpts() {
      var svc = services.filter(function (s) { return s.id === f.service_id; })[0];
      return '<option value="">Любой мастер</option>' + resources.filter(function (r) { return !svc || !r.service_ids || !r.service_ids.length || r.service_ids.indexOf(svc.id) >= 0; })
        .map(function (r) { return '<option value="' + esc(r.id) + '"' + (r.id === f.resource_id ? ' selected' : '') + '>' + esc(r.name) + '</option>'; }).join('');
    }
    var d = S.dialog({
      title: e ? 'Лист ожидания — изменить' : 'В лист ожидания', size: 'lg',
      body: (e ? '<div class="wl-who"><b>' + esc(e.client_name) + '</b> · ' + esc(e.service) + '</div>' :
        '<div class="wl-grid"><label class="field" style="position:relative"><span class="label">Клиент</span><input class="input" id="wl-name" maxlength="200" autocomplete="off" placeholder="Имя или телефон из базы" value="' + esc(f.name) + '"><div id="wl-sugg"></div></label>' +
        '<label class="field"><span class="label">Телефон</span><input class="input" id="wl-phone" inputmode="tel" maxlength="40" placeholder="+996 …" value="' + esc(f.phone) + '"></label></div>' +
        '<div class="wl-grid"><label class="field"><span class="label">Услуга</span><select class="select-native" id="wl-svc">' + svcOpts() + '</select></label>') +
        (e ? '<div class="wl-grid">' : '') +
        '<label class="field"><span class="label">Мастер</span><select class="select-native" id="wl-res">' + resOpts() + '</select></label></div>' +
        '<div class="field"><span class="label">Какие дни подходят</span><div class="wl-chips" id="wl-days">' +
        [['Сегодня', 0, 0], ['3 дня', 0, 2], ['Неделя', 0, 6], ['2 недели', 0, 13]].map(function (x) {
          return '<button type="button" class="wl-chip" data-d="' + x[1] + ':' + x[2] + '">' + x[0] + '</button>';
        }).join('') + '</div><div class="wl-grid"><label class="field"><span class="label">С</span><input class="input" type="date" id="wl-from" min="' + today + '" value="' + esc(f.date_from) + '"></label>' +
        '<label class="field"><span class="label">По</span><input class="input" type="date" id="wl-to" min="' + today + '" value="' + esc(f.date_to) + '"></label></div></div>' +
        '<div class="field"><span class="label">Время</span><div class="wl-chips" id="wl-tod">' + TOD.map(function (x, i) {
          return '<button type="button" class="wl-chip' + (x[0] === f.time_from && x[1] === f.time_to ? ' on' : '') + '" data-t="' + i + '">' + x[2] + '</button>';
        }).join('') + '</div><div class="wl-grid"><label class="field"><span class="label">Не раньше</span><input class="input" type="time" id="wl-tf" value="' + esc(f.time_from) + '"></label>' +
        '<label class="field"><span class="label">Не позже (начало)</span><input class="input" type="time" id="wl-tt" value="' + esc(f.time_to) + '"></label></div></div>' +
        '<label class="field"><span class="label">Пожелание (по желанию)</span><input class="input" id="wl-note" maxlength="300" placeholder="Например: только у окна, с ребёнком" value="' + esc(f.note || '') + '"></label>' +
        '<p class="muted small" style="margin:4px 0 0">Освободится подходящее время — клиенту придёт сообщение, ответить можно 30 минут. Первым предлагаем тому, кто раньше встал в лист.</p>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>' + (e ? 'Сохранить' : 'Поставить в лист') + '</button>'
    });
    var $ = function (s) { return S.$(s, d.el); };
    function syncTod() {
      var tf = $('#wl-tf').value, tt = $('#wl-tt').value;
      S.$$('#wl-tod .wl-chip', d.el).forEach(function (b) { var x = TOD[+b.getAttribute('data-t')]; b.classList.toggle('on', x[0] === tf && x[1] === tt); });
    }
    S.$$('#wl-days .wl-chip', d.el).forEach(function (b) {
      b.addEventListener('click', function () {
        var p = b.getAttribute('data-d').split(':');
        $('#wl-from').value = addDays(today, +p[0]); $('#wl-to').value = addDays(today, +p[1]);
      });
    });
    S.$$('#wl-tod .wl-chip', d.el).forEach(function (b) {
      b.addEventListener('click', function () { var x = TOD[+b.getAttribute('data-t')]; $('#wl-tf').value = x[0]; $('#wl-tt').value = x[1]; syncTod(); });
    });
    ['#wl-tf', '#wl-tt'].forEach(function (s) { $(s).addEventListener('input', syncTod); });
    if ($('#wl-svc')) $('#wl-svc').addEventListener('change', function () { f.service_id = $('#wl-svc').value; f.resource_id = $('#wl-res').value; $('#wl-res').innerHTML = resOpts(); });
    if ($('#wl-name')) {
      var tmr;
      $('#wl-name').addEventListener('input', function () {
        f.contact_id = null; clearTimeout(tmr);
        var q = $('#wl-name').value.trim();
        if (q.length < 2) { $('#wl-sugg').innerHTML = ''; return; }
        tmr = setTimeout(function () {
          S.api('/api/agent/contacts?limit=5&search=' + encodeURIComponent(q)).then(function (r) {
            var items = (r.ok && (r.data.contacts || r.data.items || [])) || [];
            $('#wl-sugg').innerHTML = items.length ? '<div class="bk-sugg">' + items.map(function (c) {
              var ph = c.phone && !/^(web|tg|ig|wa):/.test(c.phone) ? c.phone : '';
              return '<button type="button" data-cid="' + esc(c.id) + '" data-n="' + esc(c.name || '') + '" data-p="' + esc(ph) + '">' + esc(c.name || 'Без имени') + '<span>' + esc(ph) + '</span></button>';
            }).join('') + '</div>' : '';
            S.$$('[data-cid]', d.el).forEach(function (btn) {
              btn.addEventListener('click', function () {
                f.contact_id = btn.getAttribute('data-cid'); $('#wl-name').value = btn.getAttribute('data-n');
                if (btn.getAttribute('data-p')) $('#wl-phone').value = btn.getAttribute('data-p');
                $('#wl-sugg').innerHTML = '';
              });
            });
          });
        }, 220);
      });
    }
    $('[data-c]').addEventListener('click', d.close);
    $('[data-ok]').addEventListener('click', function () {
      var btn = $('[data-ok]'); btn.disabled = true;
      var body = { resource_id: $('#wl-res').value || null, date_from: $('#wl-from').value, date_to: $('#wl-to').value,
        time_from: $('#wl-tf').value || null, time_to: $('#wl-tt').value || null, note: $('#wl-note').value.trim() || null };
      var req;
      if (e) req = S.api('/api/booking/waitlist/' + e.id, { method: 'PATCH', body: body });
      else {
        body.service_id = $('#wl-svc').value; body.contact_id = f.contact_id;
        body.client_name = $('#wl-name').value.trim() || null; body.client_phone = $('#wl-phone').value.trim() || null;
        if (!body.contact_id && !(body.client_name && body.client_phone)) { S.toast('Укажите клиента: выберите из базы или впишите имя и телефон', 'error'); btn.disabled = false; return; }
        req = S.api('/api/booking/waitlist', { method: 'POST', body: body });
      }
      req.then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast(e ? 'Сохранено' : 'В листе ожидания: ' + r.data.entry.period + ' — предложим, как только освободится', 'success');
        d.close();
        if (onDone) onDone(r.data.entry);
      });
    });
    return d;
  }

  window.BookingWaitlist = { open: open, addDialog: addDialog, reload: load };
})();
