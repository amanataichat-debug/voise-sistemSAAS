/* ============================================================================
 * orders/orders.js — «Заказы» (ветка 1811-niche-admin-speed): магазины, доставка еды, цветы, запчасти.
 * Компьютер: итоги дня, доска по статусам (перетаскивание карточек), список с поиском и периодом.
 * Телефон: фильтр статусов чипсами, карточки, большая кнопка следующего шага в карточке заказа.
 * Новый заказ: клиент из CRM или новый, позиции из каталога (размер/цвет с остатком) или своей строкой,
 * доставка/самовывоз, оплата. Данные — /api/orders (services/order_service.py), на StartApp (start/common.js).
 * ========================================================================== */
(function () {
  'use strict';
  var S, esc, icon;
  var COLORS = { new: '#2a5ce8', confirmed: '#6250d6', packed: '#eb6834', delivering: '#0891b2', done: '#1baf7a', cancelled: '#94a3b8' };
  var BOARD = ['new', 'confirmed', 'packed', 'delivering', 'done'];
  var PAY = [['cash', 'Наличными'], ['card', 'Картой'], ['transfer', 'Переводом'], ['qr', 'По QR']];
  var PHONE_TABS = [['new', 'Новые'], ['work', 'В работе'], ['delivering', 'В пути'], ['done', 'Выданы'], ['all', 'Все']];
  var st = { orders: [], counts: {}, titles: {}, food: false, settings: {}, enabled: true, view: 'board', period: '', q: '', tab: 'new', loading: false };
  var root, timer;

  function api(path, opts) { return S.api('/api/orders' + path, opts); }
  function title(s) { return st.titles[s] || s; }
  function money(v) { return S.fmtMoney(v || 0, 'KGS') || '0 сом'; }
  function isPhone() { return window.matchMedia && window.matchMedia('(max-width: 760px)').matches; }
  function todayKey() { return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Bishkek' }).format(new Date()); }
  function dayKey(iso) { return iso ? new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Bishkek' }).format(new Date(iso)) : ''; }
  function nextLabel(o) {
    var n = o.next_status;
    if (!n) return '';
    if (n === 'confirmed') return st.food ? 'Принять заказ' : 'Подтвердить заказ';
    if (n === 'packed') return o.delivery_type === 'pickup' ? (st.food ? 'Готов — можно забирать' : 'Собран — можно забирать') : (st.food ? 'Готовится' : 'Собран');
    if (n === 'delivering') return 'Передать курьеру';
    if (n === 'done') return o.delivery_type === 'pickup' ? 'Клиент забрал' : 'Доставлен';
    return title(n);
  }

  // ── загрузка ────────────────────────────────────────────────────────────
  function load(quiet) {
    if (st.loading) return Promise.resolve();
    st.loading = true;
    var q = '?limit=300' + (st.period ? '&period=' + st.period : '') + (st.q ? '&q=' + encodeURIComponent(st.q) : '');
    return api(q).then(function (r) {
      st.loading = false;
      S.ready();
      if (!r.ok) { if (!quiet) root.innerHTML = '<div class="od-empty"><h3>Не удалось загрузить заказы</h3><p>' + esc(S.apiError(r)) + '</p></div>'; return; }
      st.orders = r.data.orders || [];
      st.counts = r.data.counts || {};
      st.titles = r.data.titles || {};
      st.food = !!r.data.food;
      st.settings = r.data.settings || {};
      st.enabled = r.data.enabled !== false;
      render();
    });
  }

  // ── каркас ──────────────────────────────────────────────────────────────
  function render() {
    var c = st.counts, inWork = (c.confirmed || 0) + (c.packed || 0);
    var today = st.orders.filter(function (o) { return dayKey(o.created_at) === todayKey() && o.status !== 'cancelled'; });
    var revenue = today.reduce(function (a, o) { return a + (o.total || 0); }, 0);
    var html = '';
    if (!st.enabled && !S.staff()) {
      html += '<div class="st-note" style="margin:0">Заказы не включены для вашей сферы бизнеса — бот пока не оформляет их сам. ' +
        'Включить: <a href="/static/settings.html#business">Настройки → Мой бизнес → Заказы</a>.</div>';
    }
    html += '<div class="od-kpis">' +
      kpi('new', 'Новые', c.new || 0, c.new ? 'ждут подтверждения' : 'новых нет', 'shopping-cart', !!c.new) +
      kpi('work', 'В работе', inWork, (c.confirmed || 0) + ' подтверждено · ' + (c.packed || 0) + (st.food ? ' готовится' : ' собрано'), 'package', false) +
      kpi('delivering', 'В пути', c.delivering || 0, (c.done_today || 0) + ' выдано сегодня', 'truck', false) +
      kpi('today', 'Сегодня', today.length, today.length ? money(revenue) : 'заказов ещё не было', 'receipt', false) +
      '</div>';
    html += '<div class="od-bar">' +
      '<div class="od-search">' + icon('search') + '<input class="input" id="od-q" type="search" placeholder="№ заказа, имя, телефон, адрес, товар" value="' + esc(st.q) + '"></div>' +
      '<div class="od-seg period">' + [['', 'Все'], ['today', 'Сегодня'], ['7d', '7 дней'], ['30d', '30 дней']].map(function (p) {
        return '<button type="button" data-period="' + p[0] + '" class="' + (st.period === p[0] ? 'on' : '') + '">' + p[1] + '</button>';
      }).join('') + '</div>' +
      '<div class="od-seg view"><button type="button" data-view="board" class="' + (st.view === 'board' ? 'on' : '') + '">' + icon('layout-grid') + 'Доска</button>' +
      '<button type="button" data-view="list" class="' + (st.view === 'list' ? 'on' : '') + '">' + icon('list') + 'Список</button></div>' +
      '</div>';
    html += '<div class="od-phone-tabs">' + PHONE_TABS.map(function (t) {
      var n = t[0] === 'new' ? c.new : t[0] === 'work' ? inWork : t[0] === 'delivering' ? c.delivering : t[0] === 'done' ? c.done_today : null;
      return '<button type="button" data-tab="' + t[0] + '" class="' + (st.tab === t[0] ? 'on' : '') + '">' + t[1] + (n ? '<span class="c">' + n + '</span>' : '') + '</button>';
    }).join('') + '</div>';
    html += st.orders.length ? (st.view === 'board' ? board() : table()) + phoneList() : empty();
    root.innerHTML = html;
    bind();
  }

  function kpi(id, label, value, sub, ic, hot) {
    return '<button type="button" class="od-kpi' + (hot ? ' hot' : '') + '" data-kpi="' + id + '"><div class="k">' + icon(ic) + esc(label) + '</div>' +
      '<div class="v">' + esc(value) + '</div><div class="s">' + esc(sub) + '</div></button>';
  }

  function empty() {
    var word = st.food ? 'заказ еды' : 'заказ';
    return '<div class="od-empty"><div class="ic-big">' + icon('shopping-cart') + '</div>' +
      '<h3>' + (st.q || st.period ? 'Ничего не нашлось' : 'Заказов пока нет') + '</h3>' +
      '<p>' + (st.q || st.period ? 'Попробуйте другой запрос или период.' :
        'Бот оформляет ' + word + ' прямо в переписке: уточнит размер, адрес и время и пришлёт его сюда. Можно оформить и самому.') + '</p>' +
      (st.q || st.period ? '' : '<button type="button" class="btn btn-primary" data-new>' + icon('plus') + '<span>Новый заказ</span></button>') + '</div>';
  }

  function deliveryTag(o) {
    if (o.delivery_type === 'pickup') return '<span class="od-tag">' + icon('store') + 'самовывоз</span>';
    return '<span class="od-tag" title="' + esc(o.address || '') + '">' + icon('truck') + esc(o.delivery_when || 'доставка') + '</span>';
  }

  function card(o, drag, withStatus) {
    return '<div class="od-card" data-id="' + esc(o.id) + '"' + (drag ? ' draggable="true"' : '') + '>' +
      '<div class="top"><span class="num">' + esc(o.label) + '</span><span>' + esc(String(o.created_label || '').replace(/^сегодня /, '')) + '</span>' +
      (o.source === 'bot' ? '<span class="src od-tag bot">' + icon('bot') + 'бот</span>' : (o.created_by ? '<span class="src">' + icon('user') + esc(o.created_by) + '</span>' : '')) + '</div>' +
      '<div class="who">' + esc(o.client_name || S.fmtPhone(o.client_phone) || 'Клиент') + '</div>' +
      '<div class="what">' + esc(o.items_text) + '</div>' +
      '<div class="foot"><span class="sum">' + money(o.total) + '</span>' +
      (withStatus ? '<span class="od-tag" style="color:' + COLORS[o.status] + '">' + esc(o.status_title) + '</span>' : '') +
      deliveryTag(o) + (o.paid ? '<span class="od-tag paid">' + icon('check') + 'оплачен</span>' : '') + '</div></div>';
  }

  function board() {
    var today = todayKey();
    return '<div class="od-board">' + BOARD.map(function (s) {
      var list = st.orders.filter(function (o) { return o.status === s && (s !== 'done' || dayKey(o.done_at || o.created_at) === today); });
      var head = s === 'done' ? title('done') + ' сегодня' : title(s);
      return '<div class="od-col" data-col="' + s + '"><div class="od-col-h"><span class="od-dot" style="--c:' + COLORS[s] + '"></span>' + esc(head) +
        '<span class="n">' + list.length + '</span></div>' +
        (list.length ? list.map(function (o) { return card(o, true, false); }).join('') : '<div class="od-col-empty">' + (s === 'new' ? 'Новых заказов нет' : 'Пусто') + '</div>') +
        '</div>';
    }).join('') + '</div>';
  }

  function table() {
    return '<div class="od-table-wrap" style="overflow-x:auto"><table class="od-table"><thead><tr><th>№</th><th>Клиент</th><th>Состав</th><th>Сумма</th><th>Получение</th><th>Статус</th><th>Создан</th></tr></thead><tbody>' +
      st.orders.map(function (o) {
        return '<tr data-id="' + esc(o.id) + '"><td><b>' + esc(o.number) + '</b></td>' +
          '<td>' + esc(o.client_name || 'Клиент') + '<div class="muted">' + esc(S.fmtPhone(o.client_phone) || '') + '</div></td>' +
          '<td>' + esc(o.items_text) + '</td><td><b>' + money(o.total) + '</b>' + (o.paid ? '<div class="muted">оплачен</div>' : '') + '</td>' +
          '<td>' + (o.delivery_type === 'pickup' ? 'Самовывоз' : esc(o.address || 'Доставка')) + '<div class="muted">' + esc(o.delivery_when || '') + '</div></td>' +
          '<td><span class="od-st"><span class="od-dot" style="--c:' + COLORS[o.status] + '"></span>' + esc(o.status_title) + '</span></td>' +
          '<td class="muted">' + esc(o.created_label) + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  function phoneList() {
    var today = todayKey();
    var list = st.orders.filter(function (o) {
      if (st.tab === 'new') return o.status === 'new';
      if (st.tab === 'work') return o.status === 'confirmed' || o.status === 'packed';
      if (st.tab === 'delivering') return o.status === 'delivering';
      if (st.tab === 'done') return o.status === 'done' && dayKey(o.done_at || o.created_at) === today;
      return true;
    });
    return '<div class="od-mlist">' + (list.length ? list.map(function (o) { return card(o, false, st.tab === 'all'); }).join('')
      : '<div class="od-col-empty" style="padding:30px 10px">В этом списке заказов нет</div>') + '</div>';
  }

  function find(id) { return st.orders.filter(function (o) { return o.id === id; })[0]; }

  function bind() {
    var q = S.$('#od-q', root);
    if (q) q.addEventListener('input', S.debounce(function () { st.q = q.value.trim(); load(); }, 300));
    S.$$('[data-period]', root).forEach(function (b) { b.addEventListener('click', function () { st.period = b.dataset.period; load(); }); });
    S.$$('[data-view]', root).forEach(function (b) {
      b.addEventListener('click', function () { st.view = b.dataset.view; try { localStorage.setItem('vf_orders_view', st.view); } catch (e) { /* ignore */ } render(); });
    });
    S.$$('[data-tab]', root).forEach(function (b) { b.addEventListener('click', function () { st.tab = b.dataset.tab; render(); }); });
    S.$$('[data-kpi]', root).forEach(function (b) {
      b.addEventListener('click', function () {
        var k = b.dataset.kpi;
        if (k === 'today') { st.period = 'today'; st.view = 'list'; load(); return; }
        st.tab = k; st.view = 'board'; render();
        var col = S.$('[data-col="' + (k === 'work' ? 'confirmed' : k) + '"]', root);
        if (col) col.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
      });
    });
    S.$$('[data-new]', root).forEach(function (b) { b.addEventListener('click', function () { editor(null); }); });
    S.$$('.od-card, .od-table tbody tr', root).forEach(function (el) {
      el.addEventListener('click', function () { openOrder(el.dataset.id); });
    });
    // перетаскивание карточек между колонками доски
    S.$$('.od-card[draggable]', root).forEach(function (el) {
      el.addEventListener('dragstart', function (e) { e.dataTransfer.setData('text/plain', el.dataset.id); el.classList.add('dragging'); });
      el.addEventListener('dragend', function () { el.classList.remove('dragging'); });
    });
    S.$$('.od-col', root).forEach(function (col) {
      col.addEventListener('dragover', function (e) { e.preventDefault(); col.classList.add('drop'); });
      col.addEventListener('dragleave', function () { col.classList.remove('drop'); });
      col.addEventListener('drop', function (e) {
        e.preventDefault(); col.classList.remove('drop');
        var id = e.dataTransfer.getData('text/plain'), o = find(id);
        if (o && o.status !== col.dataset.col) setStatus(o, col.dataset.col);
      });
    });
  }

  // ── статус ──────────────────────────────────────────────────────────────
  function setStatus(o, status, reason, after) {
    return api('/' + o.id + '/status', { method: 'POST', body: { status: status, reason: reason || null } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return null; }
      var no = r.data.order;
      st.orders = st.orders.map(function (x) { return x.id === no.id ? no : x; });
      st.counts = r.data.counts || st.counts;
      S.toast(no.label + ': ' + no.status_title.toLowerCase() + (r.data.client_notified ? ' · клиенту отправлено сообщение' : ''), 'success');
      render();
      if (after) after(no);
      return no;
    });
  }

  function cancelDialog(o, after) {
    var d = S.dialog({ title: 'Отменить ' + o.label + '?', size: 'sm',
      body: '<div class="field"><label class="label" for="od-reason">Причина (для себя)</label><input class="input" id="od-reason" placeholder="Например: клиент передумал"></div>' +
        '<div class="hint">Товары вернутся на склад. Клиенту сообщение не уйдёт — напишите ему сами, если нужно.</div>',
      foot: '<button class="btn" type="button" data-c>Не отменять</button><button class="btn btn-danger btn-solid" type="button" data-ok>Отменить заказ</button>' });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    S.$('[data-ok]', d.el).addEventListener('click', function () {
      var reason = S.$('#od-reason', d.el).value.trim();
      d.close();
      setStatus(o, 'cancelled', reason, after);
    });
  }

  // ── карточка заказа ──────────────────────────────────────────────────────
  var drawer = null;
  function openOrder(id) {
    var o = find(id);
    if (!o) return;
    drawer = S.drawer({ title: '', onClose: function () { drawer = null; } });
    drawOrder(o);
  }

  function drawOrder(o) {
    if (!drawer) return;
    drawer.setTitle('<b>' + esc(o.label) + '</b> <span class="od-st" style="margin-left:8px"><span class="od-dot" style="--c:' + COLORS[o.status] + '"></span>' + esc(o.status_title) + '</span>');
    var by = o.source === 'bot' ? 'оформил бот' : (o.created_by ? 'оформил(а) ' + o.created_by : 'оформлен вручную');
    var ch = { whatsapp: 'WhatsApp', telegram: 'Telegram', instagram: 'Instagram', call: 'звонок' }[o.channel] || '';
    var digits = String(o.client_phone || '').replace(/\D/g, '');
    var wa = digits.length >= 11 ? 'https://wa.me/' + digits : '';
    var notified = (o.client_notified || []).map(function (s) { return title(s).toLowerCase(); });
    var items = (o.items || []).map(function (it) {
      var v = [it.size, it.color].filter(Boolean).join(', ');
      return '<div class="od-item"><div class="t">' + esc(it.title) + (v ? '<small>' + esc(v) + '</small>' : '') + '</div>' +
        '<div class="q">' + esc(it.qty) + ' × ' + money(it.price) + '</div><div class="p">' + money((it.price || 0) * (it.qty || 1)) + '</div></div>';
    }).join('');
    var body = '<div class="od-dr">' +
      '<div class="od-dr-st">' + esc(o.created_label) + ' · ' + esc(by) + (ch ? ' · ' + esc(ch) : '') + '</div>' +
      (o.next_status ? '<button type="button" class="btn btn-primary od-next" id="od-next">' + icon(o.next_status === 'delivering' ? 'truck' : o.next_status === 'done' ? 'package-check' : 'check') + '<span>' + esc(nextLabel(o)) + '</span></button>' : '') +
      '<div class="od-acts">' +
      (o.status === 'cancelled' ? '<button type="button" class="btn btn-sm" id="od-reopen">' + icon('refresh-cw') + '<span>Вернуть в работу</span></button>' :
        (o.status !== 'done' ? '<button type="button" class="btn btn-sm" id="od-edit">' + icon('pencil') + '<span>Изменить состав</span></button>' : '') +
        '<button type="button" class="btn btn-sm" id="od-pay">' + icon(o.paid ? 'check' : 'banknote') + '<span>' + (o.paid ? 'Оплачен' : 'Отметить оплату') + '</span></button>' +
        (o.status !== 'done' ? '<button type="button" class="btn btn-sm btn-danger" id="od-cancel">' + icon('circle-x') + '<span>Отменить</span></button>' : '')) +
      '</div>' +
      (o.status === 'cancelled' && o.cancel_reason ? '<div class="od-note">Причина отмены: ' + esc(o.cancel_reason) + '</div>' : '') +
      '<div class="od-sec"><h4>Состав</h4><div class="od-items">' + items +
      '<div class="od-sum"><div><span>Товары</span><span>' + money(o.subtotal) + '</span></div>' +
      (o.delivery_type === 'delivery' ? '<div><span>Доставка</span><span>' + (o.delivery_fee ? money(o.delivery_fee) : 'бесплатно') + '</span></div>' : '') +
      (o.discount ? '<div><span>Скидка</span><span>−' + money(o.discount) + '</span></div>' : '') +
      '<div class="total"><span>Итого</span><span>' + money(o.total) + '</span></div></div></div></div>' +
      '<div class="od-sec"><h4>Клиент</h4><div class="od-kv">' +
      icon('user') + '<div>' + esc(o.client_name || 'Клиент') + '</div><div class="acts">' +
      (o.contact_id && S.staffCan('inbox') ? '<a class="btn btn-sm" href="/static/inbox.html?contact=' + esc(o.contact_id) + '">' + icon('message-circle') + '<span>Чат</span></a>' : '') + '</div>' +
      (o.client_phone ? icon('phone') + '<div><a href="tel:' + esc(o.client_phone) + '">' + esc(S.fmtPhone(o.client_phone)) + '</a></div><div class="acts">' +
        (wa ? '<a class="btn btn-sm" href="' + esc(wa) + '" target="_blank" rel="noopener">WhatsApp</a>' : '') + '</div>' : '') +
      '</div></div>' +
      '<div class="od-sec"><h4>' + (o.delivery_type === 'pickup' ? 'Самовывоз' : 'Доставка') + '</h4>' +
      '<div class="od-chips" style="margin-bottom:10px"><button type="button" class="od-chip' + (o.delivery_type !== 'pickup' ? ' on' : '') + '" data-dt="delivery">' + icon('truck') + 'Доставка</button>' +
      '<button type="button" class="od-chip' + (o.delivery_type === 'pickup' ? ' on' : '') + '" data-dt="pickup">' + icon('store') + 'Самовывоз</button></div>' +
      (o.delivery_type !== 'pickup' ? '<div class="field"><label class="label" for="od-addr">Адрес</label><textarea class="textarea" id="od-addr" rows="2" placeholder="Улица, дом, квартира, ориентир">' + esc(o.address || '') + '</textarea></div>' : '') +
      '<div class="field"><label class="label" for="od-when">Когда</label><input class="input" id="od-when" value="' + esc(o.delivery_when || '') + '" placeholder="Сегодня после 18:00"></div>' +
      '</div>' +
      '<div class="od-sec"><h4>Оплата</h4><div class="od-chips">' + PAY.map(function (p) {
        return '<button type="button" class="od-chip' + (o.payment_method === p[0] ? ' on' : '') + '" data-pm="' + p[0] + '">' + p[1] + '</button>';
      }).join('') + '</div></div>' +
      (o.note ? '<div class="od-sec"><h4>Комментарий</h4><div class="od-note">' + esc(o.note) + '</div></div>' : '') +
      (notified.length ? '<div class="od-sent">' + icon('circle-check') + 'Клиенту сообщили: ' + esc(notified.join(', ')) + '</div>' : '') +
      '</div>';
    drawer.body.innerHTML = body;
    var b = drawer.body;
    var nx = S.$('#od-next', b);
    if (nx) nx.addEventListener('click', function () { nx.disabled = true; setStatus(o, o.next_status, null, drawOrder).then(function () { nx.disabled = false; }); });
    var cn = S.$('#od-cancel', b);
    if (cn) cn.addEventListener('click', function () { cancelDialog(o, drawOrder); });
    var ro = S.$('#od-reopen', b);
    if (ro) ro.addEventListener('click', function () { setStatus(o, 'new', null, drawOrder); });
    var ed = S.$('#od-edit', b);
    if (ed) ed.addEventListener('click', function () { editor(o); });
    var pay = S.$('#od-pay', b);
    if (pay) pay.addEventListener('click', function () { patch(o, { paid: !o.paid }); });
    S.$$('[data-pm]', b).forEach(function (x) { x.addEventListener('click', function () { patch(o, { payment_method: o.payment_method === x.dataset.pm ? null : x.dataset.pm }); }); });
    S.$$('[data-dt]', b).forEach(function (x) { x.addEventListener('click', function () { if (x.dataset.dt !== o.delivery_type) patch(o, { delivery_type: x.dataset.dt }); }); });
    var addr = S.$('#od-addr', b), when = S.$('#od-when', b);
    if (addr) addr.addEventListener('change', function () { patch(o, { address: addr.value.trim() }, true); });
    if (when) when.addEventListener('change', function () { patch(o, { delivery_when: when.value.trim() }, true); });
  }

  function patch(o, body, quiet) {
    return api('/' + o.id, { method: 'PATCH', body: body }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      var no = r.data.order;
      st.orders = st.orders.map(function (x) { return x.id === no.id ? no : x; });
      if (quiet) S.toast('Сохранено', 'success');
      drawOrder(no);
      render();
    });
  }

  // ── новый заказ / изменить состав ────────────────────────────────────────
  function editor(o, preset) {
    var isNew = !o;
    preset = preset || {};
    var form = {
      contact_id: o ? o.contact_id : (preset.contact_id || null), client_name: o ? o.client_name || '' : (preset.client_name || ''),
      client_phone: o ? o.client_phone || '' : (preset.client_phone || ''),
      delivery_type: o ? o.delivery_type : 'delivery', address: o ? o.address || '' : '', delivery_when: o ? o.delivery_when || '' : '',
      payment_method: o ? o.payment_method : null, paid: o ? !!o.paid : false, note: o ? o.note || '' : '',
      lines: (o ? o.items : []).map(function (it) { return Object.assign({}, it); })
    };
    var products = {};   // id → product (размеры, цвета, варианты)
    var d = S.dialog({
      title: isNew ? 'Новый заказ' : 'Состав ' + o.label, size: 'lg',
      body: '<div class="od-form">' +
        (isNew ? '<div class="label" style="margin-bottom:6px">Клиент</div><div id="od-client"></div>' : '') +
        '<div class="label" style="margin:10px 0 6px">Что заказали</div>' +
        '<div class="od-pick"><div class="od-search">' + icon('search') + '<input class="input" id="od-psearch" placeholder="Найти в каталоге: название, артикул" autocomplete="off"></div><div class="od-results" id="od-presults" hidden></div></div>' +
        '<div class="od-lines" id="od-lines"></div>' +
        '<button type="button" class="btn btn-ghost btn-sm" id="od-free">' + icon('plus') + '<span>Своя позиция (нет в каталоге)</span></button>' +
        (isNew ? '<div class="label" style="margin:14px 0 6px">Получение</div>' +
          '<div class="od-chips" style="margin-bottom:10px"><button type="button" class="od-chip" data-fdt="delivery">' + icon('truck') + 'Доставка</button>' +
          '<button type="button" class="od-chip" data-fdt="pickup">' + icon('store') + 'Самовывоз</button></div>' +
          '<div class="field" id="od-faddr-wrap"><label class="label" for="od-faddr">Адрес</label><input class="input" id="od-faddr" placeholder="Улица, дом, квартира"></div>' +
          '<div class="field"><label class="label" for="od-fwhen">Когда</label><input class="input" id="od-fwhen" placeholder="Сегодня после 18:00">' +
          '<div class="od-chips" style="margin-top:6px">' + ['Как можно скорее', 'Сегодня', 'Завтра'].map(function (w) { return '<button type="button" class="od-chip" data-when="' + w + '">' + w + '</button>'; }).join('') + '</div></div>' +
          '<div class="label" style="margin:6px 0 6px">Оплата</div><div class="od-chips">' + PAY.map(function (p) { return '<button type="button" class="od-chip" data-fpm="' + p[0] + '">' + p[1] + '</button>'; }).join('') +
          '<label class="od-chip" style="cursor:pointer"><input type="checkbox" id="od-fpaid" style="margin:0"> Уже оплачен</label></div>' +
          '<div class="field" style="margin-top:12px"><label class="label" for="od-fnote">Комментарий</label><textarea class="textarea" id="od-fnote" rows="2" placeholder="Пожелания клиента"></textarea></div>' : '') +
        '<div class="od-totalbar"><span id="od-tsub"></span><b id="od-ttotal"></b></div>' +
        '<div id="od-ferr" class="hint" style="color:var(--vf-danger);margin-top:8px" hidden></div></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="od-save">' + (isNew ? 'Оформить заказ' : 'Сохранить состав') + '</button>'
    });
    d.el.querySelector('.vf-dialog').classList.add('od-dlg');
    var el = d.el;
    S.$('[data-c]', el).addEventListener('click', d.close);

    // клиент: имя + телефон, подсказки из CRM
    function drawClient() {
      var box = S.$('#od-client', el);
      if (!box) return;
      if (form.contact_id) {
        box.innerHTML = '<div class="od-client-hit"><span class="st-avatar">' + esc(S.initial(form.client_name)) + '</span><div class="t"><b>' + esc(form.client_name || 'Клиент') + '</b>' +
          '<span>' + esc(S.fmtPhone(form.client_phone) || 'из CRM') + '</span></div><button type="button" class="btn btn-ghost btn-sm" id="od-cl-x">Другой</button></div>';
        S.$('#od-cl-x', box).addEventListener('click', function () { form.contact_id = null; form.client_name = ''; form.client_phone = ''; drawClient(); });
        return;
      }
      box.innerHTML = '<div class="grid2"><div class="field"><input class="input" id="od-cname" placeholder="Имя клиента" value="' + esc(form.client_name) + '"></div>' +
        '<div class="field"><input class="input" id="od-cphone" type="tel" placeholder="+996 700 123 456" value="' + esc(form.client_phone) + '"></div></div><div class="od-pick"><div class="od-results" id="od-cres" hidden></div></div>';
      var nm = S.$('#od-cname', box), ph = S.$('#od-cphone', box), res = S.$('#od-cres', box);
      var search = S.debounce(function (q) {
        if (!q || q.length < 2) { res.hidden = true; return; }
        S.api('/api/agent/contacts?limit=5&search=' + encodeURIComponent(q)).then(function (r) {
          var list = (r.ok && r.data.contacts) || [];
          if (!list.length) { res.hidden = true; return; }
          res.innerHTML = list.map(function (c) {
            return '<div class="od-res" data-cid="' + esc(c.id) + '"><span class="st-avatar">' + esc(S.initial(c.name || c.phone)) + '</span><div class="t"><b>' + esc(c.name || 'Без имени') + '</b><span>' + esc(S.fmtPhone(c.phone) || '') + '</span></div></div>';
          }).join('');
          res.hidden = false;
          S.$$('[data-cid]', res).forEach(function (x) {
            x.addEventListener('mousedown', function (e) {
              e.preventDefault();
              var c = list.filter(function (y) { return y.id === x.dataset.cid; })[0];
              form.contact_id = c.id; form.client_name = c.name || ''; form.client_phone = /^\+/.test(c.phone || '') ? c.phone : '';
              drawClient();
            });
          });
        });
      }, 250);
      nm.addEventListener('input', function () { form.client_name = nm.value; search(nm.value.trim()); });
      ph.addEventListener('input', function () { form.client_phone = ph.value; search(ph.value.replace(/\D/g, '').length >= 4 ? ph.value.trim() : ''); });
      [nm, ph].forEach(function (x) { x.addEventListener('blur', function () { setTimeout(function () { res.hidden = true; }, 150); }); });
    }
    drawClient();

    // позиции
    function drawLines() {
      var box = S.$('#od-lines', el);
      box.innerHTML = form.lines.length ? form.lines.map(function (l, i) {
        var p = l.entry_id ? products[l.entry_id] : null;
        var sizes = p ? uniq(p.variants.map(function (v) { return v.size; }).concat(p.variants.length ? [] : (p.sizes || []))) : [];
        var colors = p ? uniq(p.variants.map(function (v) { return v.color; })) : [];
        var left = p ? stockLeft(p, l) : null;
        var vars = (sizes.length ? '<select data-ls="' + i + '"><option value="">Размер…</option>' + sizes.map(function (s) { return '<option' + (s === l.size ? ' selected' : '') + '>' + esc(s) + '</option>'; }).join('') + '</select>' : '') +
          (colors.length ? '<select data-lc="' + i + '"><option value="">Цвет…</option>' + colors.map(function (c) { return '<option' + (c === l.color ? ' selected' : '') + '>' + esc(c) + '</option>'; }).join('') + '</select>' : '') +
          (left !== null ? '<span class="hint" style="align-self:center;' + (left < l.qty ? 'color:var(--vf-danger)' : '') + '">' + (left > 0 ? 'в наличии ' + left + ' шт.' : 'нет в наличии') + '</span>' : '');
        return '<div class="od-line"><div class="nm">' + (l.entry_id ? esc(l.title) : '<input class="input" data-lt="' + i + '" placeholder="Название" value="' + esc(l.title || '') + '">') +
          (vars ? '<div class="var">' + vars + '</div>' : '') + '</div>' +
          '<div class="od-qty"><button type="button" data-lm="' + i + '">−</button><input data-lq="' + i + '" inputmode="numeric" value="' + esc(l.qty) + '"><button type="button" data-lp="' + i + '">+</button></div>' +
          '<div class="od-price"><input class="input" data-lpr="' + i + '" inputmode="decimal" value="' + esc(l.price == null ? '' : l.price) + '" placeholder="Цена"></div>' +
          '<button type="button" class="btn btn-ghost btn-icon od-x" data-lx="' + i + '" aria-label="Убрать">' + icon('x') + '</button></div>';
      }).join('') : '<div class="hint" style="padding:6px 2px">Найдите товар в каталоге или добавьте свою позицию.</div>';
      S.$$('[data-lm]', box).forEach(function (b) { b.addEventListener('click', function () { var l = form.lines[+b.dataset.lm]; l.qty = Math.max(1, (+l.qty || 1) - 1); drawLines(); }); });
      S.$$('[data-lp]', box).forEach(function (b) { b.addEventListener('click', function () { var l = form.lines[+b.dataset.lp]; l.qty = Math.min(999, (+l.qty || 1) + 1); drawLines(); }); });
      S.$$('[data-lq]', box).forEach(function (x) { x.addEventListener('change', function () { form.lines[+x.dataset.lq].qty = Math.max(1, Math.min(999, parseInt(x.value, 10) || 1)); drawLines(); }); });
      S.$$('[data-lpr]', box).forEach(function (x) { x.addEventListener('input', function () { var v = parseFloat(String(x.value).replace(',', '.')); form.lines[+x.dataset.lpr].price = isNaN(v) ? null : v; totals(); }); });
      S.$$('[data-lt]', box).forEach(function (x) { x.addEventListener('input', function () { form.lines[+x.dataset.lt].title = x.value; }); });
      S.$$('[data-ls]', box).forEach(function (x) { x.addEventListener('change', function () { form.lines[+x.dataset.ls].size = x.value; drawLines(); }); });
      S.$$('[data-lc]', box).forEach(function (x) { x.addEventListener('change', function () { form.lines[+x.dataset.lc].color = x.value; drawLines(); }); });
      S.$$('[data-lx]', box).forEach(function (b) { b.addEventListener('click', function () { form.lines.splice(+b.dataset.lx, 1); drawLines(); }); });
      totals();
    }
    function uniq(a) { var o = []; a.forEach(function (x) { if (x && o.indexOf(x) < 0) o.push(x); }); return o; }
    function stockLeft(p, l) {
      if (p.variants && p.variants.length) {
        if (!l.size && !l.color) return null;
        var v = p.variants.filter(function (x) { return (!l.size || x.size === l.size) && (!l.color || x.color === l.color); });
        return v.reduce(function (a, x) { return a + (x.qty || 0); }, 0);
      }
      return p.stock_qty == null ? null : p.stock_qty;
    }
    function totals() {
      var sub = form.lines.reduce(function (a, l) { return a + (Number(l.price) || 0) * (Number(l.qty) || 1); }, 0);
      var fee = 0;
      if (form.delivery_type === 'delivery') {
        fee = Number(st.settings.delivery_fee) || 0;
        var free = Number(st.settings.free_delivery_from) || 0;
        if (free && sub >= free) fee = 0;
        if (!isNew) fee = o.delivery_type === 'delivery' ? (o.delivery_fee || 0) : fee;
      }
      S.$('#od-tsub', el).textContent = form.lines.length ? 'Товары ' + money(sub) + (fee ? ' + доставка ' + money(fee) : '') : 'Добавьте позиции';
      S.$('#od-ttotal', el).textContent = money(sub + fee);
      var save = S.$('#od-save', el);
      if (isNew) save.textContent = form.lines.length ? 'Оформить заказ · ' + money(sub + fee) : 'Оформить заказ';
    }
    // поиск по каталогу
    var ps = S.$('#od-psearch', el), pr = S.$('#od-presults', el);
    function findProducts(q) {
      api('/products' + (q ? '?q=' + encodeURIComponent(q) : '')).then(function (r) {
        var list = (r.ok && r.data.products) || [];
        list.forEach(function (p) { products[p.id] = p; });
        if (!list.length) { pr.innerHTML = '<div class="od-res"><div class="t"><span>Ничего не нашлось — добавьте свою позицию</span></div></div>'; pr.hidden = false; return; }
        pr.innerHTML = list.map(function (p) {
          var out = !p.in_stock;
          var vars = (p.sizes || []).length ? 'размеры: ' + p.sizes.join(', ') : (p.collection || '');
          return '<div class="od-res" data-pid="' + esc(p.id) + '"><div class="t"><b>' + esc(p.title) + '</b><span>' + esc(vars) + '</span></div>' +
            (out ? '<span class="out">нет в наличии</span>' : '') + '<span class="p">' + (p.price != null ? money(p.price) : '') + '</span></div>';
        }).join('');
        pr.hidden = false;
        S.$$('[data-pid]', pr).forEach(function (x) {
          x.addEventListener('mousedown', function (e) {
            e.preventDefault();
            var p = products[x.dataset.pid];
            var one = p.variants && p.variants.length === 1 ? p.variants[0] : null;
            form.lines.push({ entry_id: p.id, title: p.title, size: one ? one.size : '', color: one ? one.color : '', qty: 1, price: p.price || 0 });
            ps.value = ''; pr.hidden = true;
            drawLines();
          });
        });
      });
    }
    ps.addEventListener('focus', function () { findProducts(ps.value.trim()); });
    ps.addEventListener('input', S.debounce(function () { findProducts(ps.value.trim()); }, 220));
    ps.addEventListener('blur', function () { setTimeout(function () { pr.hidden = true; }, 150); });
    S.$('#od-free', el).addEventListener('click', function () { form.lines.push({ entry_id: null, title: '', qty: 1, price: null }); drawLines(); var t = S.$$('[data-lt]', el); if (t.length) t[t.length - 1].focus(); });
    // для правки: подтянуть варианты позиций из каталога
    if (!isNew) {
      form.lines.forEach(function (l) {
        if (l.entry_id) api('/products?q=' + encodeURIComponent(l.title)).then(function (r) {
          ((r.ok && r.data.products) || []).forEach(function (p) { products[p.id] = p; });
          drawLines();
        });
      });
    }
    // получение и оплата (только новый заказ: в карточке они правятся отдельно)
    function syncChips() {
      S.$$('[data-fdt]', el).forEach(function (b) { b.classList.toggle('on', b.dataset.fdt === form.delivery_type); });
      S.$$('[data-fpm]', el).forEach(function (b) { b.classList.toggle('on', b.dataset.fpm === form.payment_method); });
      var aw = S.$('#od-faddr-wrap', el); if (aw) aw.hidden = form.delivery_type === 'pickup';
      totals();
    }
    S.$$('[data-fdt]', el).forEach(function (b) { b.addEventListener('click', function () { form.delivery_type = b.dataset.fdt; syncChips(); }); });
    S.$$('[data-fpm]', el).forEach(function (b) { b.addEventListener('click', function () { form.payment_method = form.payment_method === b.dataset.fpm ? null : b.dataset.fpm; syncChips(); }); });
    S.$$('[data-when]', el).forEach(function (b) { b.addEventListener('click', function () { S.$('#od-fwhen', el).value = b.dataset.when; }); });
    if (isNew) syncChips();
    drawLines();

    S.$('#od-save', el).addEventListener('click', function () {
      var err = S.$('#od-ferr', el);
      function fail(t) { err.textContent = t; err.hidden = !t; }
      var lines = form.lines.filter(function (l) { return (l.title || '').trim() || l.entry_id; });
      if (!lines.length) { fail('Добавьте хотя бы одну позицию'); return; }
      var noPrice = lines.filter(function (l) { return !l.entry_id && (l.price == null || l.price === ''); });
      if (noPrice.length) { fail('У своей позиции укажите цену'); return; }
      var items = lines.map(function (l) { return { entry_id: l.entry_id || null, title: l.title, size: l.size || null, color: l.color || null, qty: +l.qty || 1, price: l.price }; });
      var btn = this; btn.disabled = true; fail('');
      var req;
      if (isNew) {
        if (!form.contact_id && !(form.client_name || '').trim() && !(form.client_phone || '').trim()) { btn.disabled = false; fail('Укажите имя или телефон клиента'); return; }
        req = api('', { method: 'POST', body: {
          items: items, contact_id: form.contact_id, client_name: (form.client_name || '').trim() || null, client_phone: (form.client_phone || '').trim() || null,
          delivery_type: form.delivery_type, address: (S.$('#od-faddr', el) || {}).value || null, delivery_when: (S.$('#od-fwhen', el) || {}).value || null,
          payment_method: form.payment_method, paid: !!(S.$('#od-fpaid', el) || {}).checked, note: (S.$('#od-fnote', el) || {}).value || null } });
      } else {
        req = api('/' + o.id, { method: 'PATCH', body: { items: items } });
      }
      req.then(function (r) {
        btn.disabled = false;
        if (!r.ok) { fail(S.apiError(r)); return; }
        d.close();
        var no = r.data.order;
        if (isNew) {
          S.toast(no.label + ' оформлен · ' + money(no.total), 'success');
          load().then(function () { openOrder(no.id); });
        } else {
          st.orders = st.orders.map(function (x) { return x.id === no.id ? no : x; });
          render(); drawOrder(no);
          S.toast('Состав заказа обновлён', 'success');
        }
      });
    });
  }

  // ── настройки (владелец) ──────────────────────────────────────────────────
  function settingsDialog() {
    var c = st.settings || {};
    var d = S.dialog({ title: 'Настройки заказов',
      body: '<label class="st-switch-row" style="display:flex;gap:12px;align-items:flex-start;justify-content:space-between;margin-bottom:14px">' +
        '<span><b>Сообщать клиенту о заказе</b><br><span class="hint">«Заказ № … принят», «готов, можно забирать», «передан курьеру» — в мессенджер, где клиент писал</span></span>' +
        '<span class="switch"><input type="checkbox" id="os-notify"' + (c.notify_client !== false ? ' checked' : '') + '><span class="track"></span></span></label>' +
        '<div class="grid2" style="display:grid;grid-template-columns:1fr 1fr;gap:0 12px">' +
        '<div class="field"><label class="label" for="os-fee">Доставка, сом</label><input class="input" id="os-fee" inputmode="decimal" value="' + esc(c.delivery_fee || '') + '" placeholder="0 — бесплатно"></div>' +
        '<div class="field"><label class="label" for="os-free">Бесплатно от суммы, сом</label><input class="input" id="os-free" inputmode="decimal" value="' + esc(c.free_delivery_from || '') + '" placeholder="Не нужно — пусто"></div></div>' +
        '<div class="field"><label class="label" for="os-pickup">Адрес самовывоза</label><input class="input" id="os-pickup" value="' + esc(c.pickup_address || '') + '" placeholder="Бишкек, ул. Киевская 95, ТЦ «Ала-Тоо», 2 этаж"></div>' +
        '<div class="field"><label class="label" for="os-pay">Как оплатить (уйдёт клиенту в сообщении «заказ принят»)</label><textarea class="textarea" id="os-pay" rows="2" placeholder="Перевод на Mbank +996 700 …, получатель Айгерим Т.">' + esc(c.payment_note || '') + '</textarea></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>Сохранить</button>' });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    S.$('[data-ok]', d.el).addEventListener('click', function () {
      var num = function (id) { var v = parseFloat(String(S.$(id, d.el).value).replace(',', '.')); return isNaN(v) ? 0 : v; };
      api('/settings', { method: 'PUT', body: { notify_client: S.$('#os-notify', d.el).checked, delivery_fee: num('#os-fee'), free_delivery_from: num('#os-free'),
        pickup_address: S.$('#os-pickup', d.el).value, payment_note: S.$('#os-pay', d.el).value } }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        st.settings = r.data; d.close(); S.toast('Настройки заказов сохранены', 'success');
      });
    });
  }

  // ── запуск ──────────────────────────────────────────────────────────────
  function init() {
    S = window.StartApp; esc = S.esc; icon = S.icon;
    root = document.getElementById('od');
    S.initMobileMenu();
    try { var v = localStorage.getItem('vf_orders_view'); if (v === 'list' || v === 'board') st.view = v; } catch (e) { /* ignore */ }
    var acts = document.getElementById('od-actions');
    acts.innerHTML = (S.staff() ? '' : '<button class="btn btn-sm btn-icon" type="button" id="od-set" title="Настройки заказов" aria-label="Настройки">' + icon('settings') + '</button>') +
      '<button class="btn btn-primary btn-sm" type="button" id="od-new">' + icon('plus') + '<span>Новый заказ</span></button>';
    document.getElementById('od-new').addEventListener('click', function () { editor(null); });
    var set = document.getElementById('od-set');
    if (set) set.addEventListener('click', settingsDialog);
    var fab = document.createElement('button');
    fab.className = 'od-fab'; fab.type = 'button'; fab.innerHTML = icon('plus') + 'Заказ';
    fab.addEventListener('click', function () { editor(null); });
    document.body.appendChild(fab);
    S.boot({ allowFull: true }).then(function (ok) {
      if (!ok) return;
      load().then(function () {
        var qs = new URLSearchParams(location.search);
        if (qs.get('order')) openOrder(qs.get('order'));
        // «Заказ» из карточки клиента в инбоксе / CRM: новый заказ сразу на этого клиента
        if (qs.get('new')) {
          editor(null, { contact_id: qs.get('contact'), client_name: qs.get('name') || '', client_phone: qs.get('phone') || '' });
          try { history.replaceState(null, '', location.pathname); } catch (e) { /* ignore */ }
        }
      });
      // новые заказы от бота появляются сами
      timer = setInterval(function () {
        var typing = document.activeElement && document.activeElement.id === 'od-q';
        if (!document.hidden && !typing && !document.querySelector('.vf-backdrop') && !document.querySelector('.st-drawer-wrap.open')) load(true);
      }, 30000);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
