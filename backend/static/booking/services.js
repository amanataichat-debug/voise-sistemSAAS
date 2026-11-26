/**
 * Услуги салона (Voksy Beauty, шаг 1.3): экран «Услуги» в /static/booking.html.
 *
 * Прайс по категориям и подкатегориям (категория/подкатегория — «Волосы / Окрашивание» в коллекции
 * каталога), поиск, «Категории» (порядок, переименование). Редактор услуги: название, категория,
 * описание, цена «от — до», длительность, подготовка до и перерыв после (наглядная полоска времени),
 * какие мастера делают и за сколько, онлайн-запись, предоплата.
 * Услуги — позиции каталога бота: бот, голос и онлайн-запись видят изменения сразу.
 * API: /api/booking/services* (services/beauty_services.py). Общее — window.BookingServices.
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var DUR = [15, 30, 45, 60, 90, 120, 150, 180];
  var REPEAT = [0, 14, 21, 28, 30, 45, 60, 90];
  var PADS = [0, 5, 10, 15, 20, 30];
  var ctx = null;       // {root, back, canEdit, mode, onChange}
  var st = { data: null, q: '', loading: false };

  function money(v) { return v == null || v === '' ? '' : Number(v).toLocaleString('ru-RU') + ' сом'; }
  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    return n + ' ' + (m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many);
  }
  function durText(m) { m = +m || 0; return m >= 60 ? Math.floor(m / 60) + ' ч' + (m % 60 ? ' ' + (m % 60) + ' мин' : '') : m + ' мин'; }
  function ava(m, size) { return window.BookingMasters ? BookingMasters.ava(m, size) : ''; }
  function masterById(id) { return ((st.data && st.data.masters) || []).filter(function (m) { return m.id === id; })[0]; }

  // Небольшое окно «введите название» вместо window.prompt (на телефоне — шторка, Enter — готово)
  function askText(title, value, placeholder, hint) {
    return new Promise(function (resolve) {
      var done = false;
      var d = S.dialog({
        title: title, size: 'sm',
        body: '<input class="input" id="sv-ask" maxlength="60" placeholder="' + esc(placeholder || '') + '" value="' + esc(value || '') + '">' +
          (hint ? '<div class="hint" style="margin-top:6px">' + esc(hint) + '</div>' : ''),
        foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>Готово</button>',
        onClose: function () { if (!done) resolve(null); }
      });
      var inp = S.$('#sv-ask', d.el);
      function ok() { done = true; resolve(inp.value.split('/').join(' ').trim()); d.close(); }
      S.$('[data-ok]', d.el).addEventListener('click', ok);
      S.$('[data-c]', d.el).addEventListener('click', function () { d.close(); });
      inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') ok(); });
      setTimeout(function () { inp.focus(); inp.select(); }, 60);
    });
  }

  function load() {
    st.loading = true;
    return S.api('/api/booking/services').then(function (r) {
      st.loading = false;
      if (!r.ok) { ctx.root.innerHTML = '<div class="bk-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      st.data = r.data;
      draw();
    });
  }

  function open(c) {
    ctx = c;
    if (!st.data) {
      ctx.root.innerHTML = '<div class="ms"><div class="skeleton" style="height:56px;border-radius:14px"></div><div class="skeleton" style="height:360px;border-radius:16px"></div></div>';
      load();
    } else { draw(); load(); }
  }

  // ── экран ──
  function draw() {
    var d = st.data;
    if (!d) return;
    var list = d.services || [];
    var head = '<div class="ms-head"><button class="btn btn-sm" type="button" data-sv-back>' + icon('arrow-left') + '<span>К журналу</span></button>' +
      '<div class="ms-title"><h2>Услуги</h2><div class="muted small">' + (list.length ? plural(list.length, 'услуга', 'услуги', 'услуг') + ' · ' +
        plural((d.categories || []).length, 'категория', 'категории', 'категорий') : 'Прайс салона') + '</div></div><div class="bk-spacer"></div>' +
      (ctx.canEdit && d.has_agent && list.length ? '<button class="btn btn-sm" type="button" data-sv-cats>' + icon('layers') + '<span>Категории</span></button>' : '') +
      (ctx.canEdit && d.has_agent ? '<button class="btn btn-primary btn-sm" type="button" data-sv-add>' + icon('plus') + '<span>Добавить услугу</span></button>' : '') +
      '</div>';
    var body;
    if (!d.has_agent) {
      body = '<div class="bk-card"><div class="bk-empty">' + icon('bot') + '<b style="display:block;color:var(--vf-text);margin-bottom:6px">Услуги хранятся в каталоге вашего ИИ-администратора</b>' +
        'Бот отвечает по ним клиентам и сам записывает. Создайте бота — займёт пару минут.' +
        '<div style="margin-top:14px"><a class="btn btn-primary btn-sm" href="/static/start-bot.html">' + icon('bot') + '<span>Создать бота</span></a></div></div></div>';
    } else if (!list.length) {
      body = '<div class="bk-card"><div class="bk-empty">' + icon('sparkles') + '<b style="display:block;color:var(--vf-text);margin-bottom:6px">Добавьте первую услугу</b>' +
        'Название, цена, длительность и кто делает — клиенты увидят её при записи, бот будет знать цену.' +
        (ctx.canEdit ? '<div style="margin-top:14px"><button class="btn btn-primary btn-sm" type="button" data-sv-add>' + icon('plus') + '<span>Добавить услугу</span></button></div>' : '') + '</div></div>';
    } else {
      body = '<div class="sv-search">' + icon('search') + '<input type="search" id="sv-q" placeholder="Найти услугу" value="' + esc(st.q) + '"></div>' +
        '<div id="sv-list">' + listHtml() + '</div>';
    }
    ctx.root.innerHTML = '<div class="ms sv">' + head + body + '</div>';
    bind();
  }

  function listHtml() {
    var d = st.data, q = st.q.trim().toLowerCase();
    var list = (d.services || []).filter(function (s) { return !q || (s.title || '').toLowerCase().indexOf(q) >= 0 || (s.collection || '').toLowerCase().indexOf(q) >= 0; });
    if (!list.length) return '<div class="bk-card"><div class="bk-empty">' + icon('search') + 'Ничего не нашли.</div></div>';
    var order = (d.categories || []).map(function (c) { return c.name; });
    var groups = {};
    list.forEach(function (s) {
      var c = s.category || '';
      (groups[c] = groups[c] || {})[s.subcategory || ''] = ((groups[c] || {})[s.subcategory || ''] || []).concat([s]);
    });
    var cats = Object.keys(groups).sort(function (a, b) {
      if (!a) return 1; if (!b) return -1;
      var ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || a.localeCompare(b);
    });
    return cats.map(function (c) {
      var subs = groups[c], subOrder = (((d.categories || []).filter(function (x) { return x.name === c; })[0] || {}).subs) || [];
      var keys = Object.keys(subs).sort(function (a, b) { if (!a) return -1; if (!b) return 1; return (subOrder.indexOf(a) - subOrder.indexOf(b)) || a.localeCompare(b); });
      var n = keys.reduce(function (t, k) { return t + subs[k].length; }, 0);
      return '<section class="sv-cat"><h3>' + esc(c || 'Без категории') + '<span>' + n + '</span></h3>' +
        keys.map(function (k) {
          return (k ? '<div class="sv-sub">' + esc(k) + '</div>' : '') + '<div class="sv-rows">' + subs[k].map(row).join('') + '</div>';
        }).join('') + '</section>';
    }).join('');
  }

  function roomNames(s) {
    var all = (st.data && st.data.rooms) || [];
    return (s.room_ids || []).map(function (id) { var r = all.filter(function (x) { return x.id === id; })[0]; return r ? r.name : ''; }).filter(Boolean);
  }
  function row(s) {
    var ms = (s.masters || []).map(function (m) { return masterById(m.id); }).filter(Boolean);
    var own = (s.masters || []).some(function (m) { return m.price != null || m.duration_min; });
    var pads = [];
    if (s.prep_min) pads.push('подготовка ' + s.prep_min);
    if (s.after_min) pads.push('после ' + s.after_min);
    return '<button type="button" class="sv-row' + (s.is_active ? '' : ' off') + '" data-sv="' + esc(s.id) + '">' +
      '<span class="sv-main"><b>' + esc(s.title) + '</b><span class="sv-meta">' + icon('clock') + durText(s.duration_min) +
      (pads.length ? ' <i>· ' + esc(pads.join(', ')) + ' мин</i>' : '') + '</span>' +
      '<span class="sv-badges">' + (!s.is_active ? '<em class="off">Выключена</em>' : '') +
      (s.online_booking ? '' : '<em>' + icon('lock') + 'Только через админа</em>') +
      (s.prepay_text ? '<em class="pp">' + icon('wallet') + esc(s.prepay_text) + '</em>' : '') +
      (own ? '<em>' + icon('user-round') + 'Цены по мастерам</em>' : '') +
      roomNames(s).map(function (n) { return '<em>' + icon('door-open') + esc(n) + '</em>'; }).join('') + '</span></span>' +
      '<span class="sv-masters" title="' + esc(ms.map(function (m) { return m.name; }).join(', ') || 'Нет мастеров') + '">' +
      (ms.length ? ms.slice(0, 4).map(function (m) { return ava(m, 26); }).join('') + (ms.length > 4 ? '<span class="sv-more">+' + (ms.length - 4) + '</span>' : '')
        : '<span class="sv-nom">' + icon('circle-alert') + 'Нет мастера</span>') + '</span>' +
      '<span class="sv-price">' + esc(s.price_text || 'Цена не указана') + '</span></button>';
  }

  function bind() {
    var r = ctx.root;
    var back = S.$('[data-sv-back]', r); if (back) back.addEventListener('click', function () { ctx.back(); });
    S.$$('[data-sv-add]', r).forEach(function (b) { b.addEventListener('click', function () { editor(null); }); });
    var cats = S.$('[data-sv-cats]', r); if (cats) cats.addEventListener('click', categories);
    var q = S.$('#sv-q', r);
    if (q) q.addEventListener('input', function () { st.q = q.value; S.$('#sv-list', r).innerHTML = listHtml(); bindRows(); });
    bindRows();
  }
  function bindRows() {
    S.$$('[data-sv]', ctx.root).forEach(function (b) {
      b.addEventListener('click', function () {
        var s = (st.data.services || []).filter(function (x) { return x.id === b.getAttribute('data-sv'); })[0];
        if (s) editor(s);
      });
    });
  }

  // ── редактор услуги ──
  function editor(s0) {
    var d = st.data;
    var isNew = !s0;
    var s = JSON.parse(JSON.stringify(s0 || { title: '', category: (d.categories[0] || {}).name || '', subcategory: '', description: '',
      price: null, price_to: null, duration_min: 60, prep_min: 0, after_min: 0, online_booking: true, prepay: null, is_active: true, room_ids: [],
      masters: (d.masters || []).filter(function (m) { return m.is_active; }).map(function (m) { return { id: m.id, price: null, duration_min: null }; }) }));
    var canEdit = ctx.canEdit;
    var dirty = false;
    var cats = (d.categories || []).map(function (c) { return c.name; });
    var dlg = S.dialog({
      title: isNew ? 'Новая услуга' : s.title, size: 'lg',
      body: '<div class="sv-ed"></div>',
      foot: (!isNew && canEdit ? '<button class="btn btn-ghost" type="button" data-del style="color:var(--vf-danger);margin-right:auto">' + icon('trash-2') + '<span>Удалить</span></button>' : '') +
        '<button class="btn" type="button" data-c>' + (canEdit ? 'Отмена' : 'Закрыть') + '</button>' +
        (canEdit ? '<button class="btn btn-primary" type="button" data-save>' + (isNew ? 'Добавить' : 'Сохранить') + '</button>' : ''),
      beforeClose: function () {
        if (!dirty || !canEdit) return true;
        S.confirm({ title: 'Сохранить изменения?', message: 'Вы поменяли услугу.', confirmText: 'Сохранить', cancelText: 'Не сохранять' }).then(function (yes) {
          if (yes) save(); else { dirty = false; dlg.close(); }
        });
        return false;
      }
    });
    var el = dlg.el;
    el.querySelector('.vf-dialog').classList.add('sv-dlg');
    var box = S.$('.sv-ed', el);
    function $(q) { return S.$(q, el); }

    function subsOf(cat) { return ((d.categories || []).filter(function (c) { return c.name === cat; })[0] || {}).subs || []; }
    function catOptions() {
      var list = cats.slice();
      if (s.category && list.indexOf(s.category) < 0) list.push(s.category);
      return '<option value="">Без категории</option>' + list.map(function (c) { return '<option' + (c === s.category ? ' selected' : '') + '>' + esc(c) + '</option>'; }).join('') +
        '<option value="__new">+ Новая категория…</option>';
    }
    function subOptions() {
      var list = subsOf(s.category).slice();
      if (s.subcategory && list.indexOf(s.subcategory) < 0) list.push(s.subcategory);
      return '<option value="">Без подкатегории</option>' + list.map(function (c) { return '<option' + (c === s.subcategory ? ' selected' : '') + '>' + esc(c) + '</option>'; }).join('') +
        '<option value="__new">+ Новая подкатегория…</option>';
    }
    function chips(name, values, cur, unit) {
      return '<div class="sv-chips" data-chips="' + name + '">' + values.map(function (v) {
        return '<button type="button" class="' + (+(cur || 0) === v ? 'on' : '') + '" data-v="' + v + '">' + (v ? (name === 'duration_min' ? durText(v) : v + ' ' + unit) : 'нет') + '</button>';
      }).join('') + '</div>';
    }
    function bar() {
      var p = +s.prep_min || 0, dd = +s.duration_min || 0, a = +s.after_min || 0, t = p + dd + a || 1;
      return '<div class="sv-bar">' + (p ? '<i class="pad" style="flex:' + p + '">' + (p / t > .12 ? p + ' мин' : '') + '</i>' : '') +
        '<i class="main" style="flex:' + dd + '">' + esc(durText(dd)) + '</i>' + (a ? '<i class="pad" style="flex:' + a + '">' + (a / t > .12 ? a + ' мин' : '') + '</i>' : '') + '</div>' +
        '<div class="hint">' + (p || a ? 'Клиент у вас ' + durText(dd) + ', мастер занят ' + durText(p + dd + a) +
          (p ? ' (подготовка до прихода' : ' (') + (p && a ? ', ' : '') + (a ? 'уборка после' : '') + ').' : 'Клиент у вас ' + durText(dd) + '.') + '</div>';
    }
    function mastersHtml() {
      var ms = (d.masters || []).filter(function (m) { return m.is_active; });
      if (!ms.length) return '<div class="muted small">Мастеров пока нет — добавьте их на экране «Мастера».</div>';
      return '<div class="sv-mlist">' + ms.map(function (m) {
        var mine = (s.masters || []).filter(function (x) { return x.id === m.id; })[0];
        return '<div class="sv-m' + (mine ? '' : ' no') + '" data-m="' + esc(m.id) + '"><label class="sv-mn"><input type="checkbox"' + (mine ? ' checked' : '') + (canEdit ? '' : ' disabled') + '>' + ava(m, 32) +
          '<span><b>' + esc(m.name) + '</b><small>' + esc(m.role || 'Мастер') + '</small></span></label>' +
          '<input class="input" type="number" inputmode="numeric" min="0" step="10" data-mp placeholder="' + esc(s.price != null ? s.price : 'цена') + '" value="' + (mine && mine.price != null ? esc(mine.price) : '') + '"' + (canEdit ? '' : ' disabled') + '>' +
          '<input class="input" type="number" inputmode="numeric" min="5" step="5" data-md placeholder="' + esc(s.duration_min || 60) + '" value="' + (mine && mine.duration_min ? esc(mine.duration_min) : '') + '"' + (canEdit ? '' : ' disabled') + '></div>';
      }).join('') + '</div>';
    }
    // Кабинет / кресло / аппарат: запись встанет, только когда свободен один из отмеченных (шаг 1.4)
    function roomsHtml() {
      var rooms = d.rooms || [];
      if (!rooms.length) return '';
      var sel = s.room_ids || [];
      return '<h4 class="sv-h">Кабинет или кресло</h4><div class="sv-chips">' + rooms.map(function (r) {
        return '<button type="button" data-room="' + esc(r.id) + '" class="' + (sel.indexOf(r.id) >= 0 ? 'on' : '') + '" aria-pressed="' + (sel.indexOf(r.id) >= 0) + '">' + esc(r.name) + '</button>';
      }).join('') + '</div><div class="hint">' + (sel.length ? 'Запись встанет, только когда ' + (sel.length > 1 ? 'свободен хотя бы один из отмеченных' : 'он свободен') + ' — двух клиентов в один кабинет не запишут.' :
        'Не отмечено — услуга не занимает общий кабинет. Отметьте, если, например, массаж делают только в кабинете массажа.') + '</div>';
    }
    function render() {
      var pp = s.prepay || {};
      box.innerHTML =
        '<div class="field"><label class="label" for="sv-title">Название</label><input class="input" id="sv-title" maxlength="200" placeholder="Например: Маникюр с покрытием гель-лак" value="' + esc(s.title) + '"></div>' +
        '<div class="st-grid2"><div class="field"><label class="label" for="sv-cat">Категория</label><select class="select-native" id="sv-cat">' + catOptions() + '</select></div>' +
        '<div class="field"><label class="label" for="sv-sub">Подкатегория</label><select class="select-native" id="sv-sub"' + (s.category ? '' : ' disabled') + '>' + subOptions() + '</select></div></div>' +
        '<div class="field"><label class="label" for="sv-desc">Описание <span class="muted">— бот расскажет его клиенту</span></label><textarea class="textarea" id="sv-desc" rows="2" maxlength="4000" placeholder="Что входит, сколько держится, кому подходит">' + esc(s.description || '') + '</textarea></div>' +
        '<h4 class="sv-h">Цена</h4>' +
        '<div class="sv-price-row"><div class="field"><label class="label" for="sv-pf">от, сом</label><input class="input" id="sv-pf" type="number" inputmode="numeric" min="0" step="10" placeholder="1500" value="' + (s.price != null ? esc(s.price) : '') + '"></div>' +
        '<span class="sv-dash">—</span><div class="field"><label class="label" for="sv-pt">до, сом <span class="muted">(необязательно)</span></label><input class="input" id="sv-pt" type="number" inputmode="numeric" min="0" step="10" placeholder="2500" value="' + (s.price_to != null ? esc(s.price_to) : '') + '"></div></div>' +
        '<div class="hint">«До» — если цена зависит от длины волос или сложности. Бот и страница записи покажут «1 500–2 500 сом».</div>' +
        '<h4 class="sv-h">Время</h4>' +
        '<div class="field"><label class="label">Длительность</label>' + chips('duration_min', DUR, s.duration_min, 'мин') +
        '<div class="sv-own"><span class="muted small">или</span><input class="input" type="number" id="sv-dur" min="5" max="1440" step="5" value="' + esc(s.duration_min || '') + '"><span class="muted small">мин</span></div></div>' +
        '<div class="st-grid2"><div class="field"><label class="label">Подготовка до прихода</label>' + chips('prep_min', PADS, s.prep_min, 'мин') + '</div>' +
        '<div class="field"><label class="label">Перерыв после (уборка)</label>' + chips('after_min', PADS, s.after_min, 'мин') + '</div></div>' +
        '<div id="sv-bar">' + bar() + '</div>' +
        '<div class="field"><label class="label">Напомнить клиенту о повторе через</label>' + chips('repeat_days', REPEAT, s.repeat_days, 'дн.') +
        '<div class="hint">ИИ-управляющий сам напишет клиенту, когда подойдёт срок, и предложит время у его мастера (сценарий «Повтор услуги» в разделе «ИИ-управляющий»).</div></div>' +
        '<h4 class="sv-h">Кто делает и за сколько</h4>' +
        '<div class="sv-mhead"><span>Мастер</span><span>Цена</span><span>Мин</span></div>' + mastersHtml() +
        '<div class="hint">Пустые поля — цена и время услуги. Свои — например, у топ-мастера дороже.</div>' +
        roomsHtml() +
        '<h4 class="sv-h">Запись</h4>' +
        '<div class="st-switch-row"><div class="t"><b>Онлайн-запись на услугу</b><div>Клиенты по ссылке, бот и голосовой агент записывают сами. Выключите — например, сложное окрашивание только после консультации.</div></div>' +
        '<label class="switch"><input type="checkbox" id="sv-online"' + (s.online_booking ? ' checked' : '') + '><span class="track"></span></label></div>' +
        '<div class="field" style="margin-top:8px"><label class="label">Предоплата</label><div class="bk-seg sv-pp">' +
        [['', 'Нет'], ['fixed', 'Сумма'], ['percent', 'Процент']].map(function (o) { return '<button type="button" data-pp="' + o[0] + '" class="' + ((pp.type || '') === o[0] ? 'on' : '') + '">' + o[1] + '</button>'; }).join('') + '</div>' +
        (pp.type ? '<div class="sv-own" style="margin-top:8px"><input class="input" id="sv-ppv" type="number" min="1" step="' + (pp.type === 'percent' ? 1 : 50) + '" value="' + esc(pp.value || '') + '" placeholder="' + (pp.type === 'percent' ? '30' : '500') + '"><span class="muted small">' + (pp.type === 'percent' ? '% от цены' : 'сом') + '</span></div>' +
          '<div class="hint">Клиент увидит её при записи, бот скажет сумму. Оплату принимаете вы — реквизиты укажите в настройках записи («Как внести предоплату»), а в записи отметьте «Предоплата получена».</div>' : '') + '</div>' +
        (!isNew ? '<div class="st-switch-row" style="margin-top:6px"><div class="t"><b>Услуга в прайсе</b><div>Выключенную не видят клиенты и бот, записи остаются.</div></div>' +
          '<label class="switch"><input type="checkbox" id="sv-active"' + (s.is_active ? ' checked' : '') + '><span class="track"></span></label></div>' : '');
      if (!canEdit) S.$$('input,select,textarea,.sv-chips button,.sv-pp button', box).forEach(function (x) { x.disabled = true; });
      bindEd();
    }
    function collect() {
      if (!$('#sv-title')) return;
      s.title = $('#sv-title').value;
      s.description = $('#sv-desc').value;
      s.price = $('#sv-pf').value === '' ? null : +$('#sv-pf').value;
      s.price_to = $('#sv-pt').value === '' ? null : +$('#sv-pt').value;
      s.online_booking = $('#sv-online').checked;
      var act = $('#sv-active'); if (act) s.is_active = act.checked;
      var ppv = $('#sv-ppv'); if (ppv && s.prepay) s.prepay.value = ppv.value === '' ? null : +ppv.value;
      s.masters = S.$$('[data-m]', el).filter(function (row) { return S.$('input[type=checkbox]', row).checked; }).map(function (row) {
        var p = S.$('[data-mp]', row).value, m = S.$('[data-md]', row).value;
        return { id: row.getAttribute('data-m'), price: p === '' ? null : +p, duration_min: m === '' ? null : +m };
      });
    }
    function mark() { dirty = true; }
    function bindEd() {
      S.$$('input,textarea,select', box).forEach(function (x) { x.addEventListener('input', mark); x.addEventListener('change', mark); });
      $('#sv-cat').addEventListener('change', function (e) {
        collect();
        var v = e.target.value;
        if (v !== '__new') { s.category = v; s.subcategory = ''; render(); mark(); return; }
        askText('Новая категория', '', 'Например: Ногти', 'Категории: Ногти, Волосы, Брови и ресницы, Косметология…').then(function (n) {
          if (n) { s.category = n.slice(0, 60); s.subcategory = ''; if (cats.indexOf(s.category) < 0) cats.push(s.category); mark(); }
          render();
        });
      });
      $('#sv-sub').addEventListener('change', function (e) {
        collect();
        var v = e.target.value;
        if (v !== '__new') { s.subcategory = v; render(); mark(); return; }
        askText('Новая подкатегория', '', 'Например: Окрашивание', 'Внутри категории «' + s.category + '»').then(function (n) {
          if (n) {
            s.subcategory = n.slice(0, 56);
            var c = (d.categories || []).filter(function (x) { return x.name === s.category; })[0];
            if (c) { if (c.subs.indexOf(s.subcategory) < 0) c.subs.push(s.subcategory); }
            else d.categories.push({ name: s.category, subs: [s.subcategory], count: 0 });
            mark();
          }
          render();
        });
      });
      S.$$('[data-chips] button', box).forEach(function (b) {
        b.addEventListener('click', function () {
          collect();
          var key = b.closest('[data-chips]').getAttribute('data-chips');
          s[key] = +b.getAttribute('data-v');
          render(); mark();
        });
      });
      $('#sv-dur').addEventListener('change', function (e) { collect(); var v = +e.target.value; if (v >= 5) { s.duration_min = v; render(); mark(); } });
      S.$$('[data-pp]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          collect();
          var t = b.getAttribute('data-pp');
          s.prepay = t ? { type: t, value: s.prepay && s.prepay.type === t ? s.prepay.value : null } : null;
          render(); mark();
          var v = $('#sv-ppv'); if (v) v.focus();
        });
      });
      S.$$('[data-room]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          collect();
          var id = b.getAttribute('data-room'), cur = (s.room_ids || []).slice(), i = cur.indexOf(id);
          if (i >= 0) cur.splice(i, 1); else cur.push(id);
          s.room_ids = cur; render(); mark();
        });
      });
      S.$$('[data-m] input[type=checkbox]', el).forEach(function (c) {
        c.addEventListener('change', function () { c.closest('[data-m]').classList.toggle('no', !c.checked); });
      });
      ['#sv-pf'].forEach(function (q) {
        var x = $(q); if (x) x.addEventListener('input', function () {
          S.$$('[data-mp]', el).forEach(function (inp) { inp.placeholder = x.value || 'цена'; });
        });
      });
    }
    function save() {
      collect();
      if (!String(s.title || '').trim()) { S.toast('Укажите название услуги', 'error'); $('#sv-title').focus(); return; }
      if (s.price_to != null && s.price == null) { S.toast('Укажите цену «от»', 'error'); $('#sv-pf').focus(); return; }
      if (s.prepay && !(s.prepay.value > 0)) { S.toast('Укажите сумму или процент предоплаты', 'error'); return; }
      if (s.prepay && s.prepay.type === 'percent' && s.prepay.value > 100) { S.toast('Процент предоплаты — не больше 100', 'error'); return; }
      var body = { title: s.title, category: s.category || '', subcategory: s.subcategory || '', description: s.description || '',
        price: s.price, price_to: s.price_to, duration_min: s.duration_min, prep_min: s.prep_min || 0, after_min: s.after_min || 0,
        repeat_days: s.repeat_days || 0,
        online_booking: !!s.online_booking, prepay: s.prepay, room_ids: s.room_ids || [], masters: s.masters.map(function (m) { return Object.assign({ does: true }, m); }) };
      if (!isNew) body.is_active = !!s.is_active;
      var btn = $('[data-save]'); btn.disabled = true;
      S.api(isNew ? '/api/booking/services' : '/api/booking/services/' + s.id, { method: isNew ? 'POST' : 'PATCH', body: body }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        dirty = false;
        S.toast(isNew ? 'Услуга добавлена' : 'Услуга сохранена');
        dlg.close();
        changed();
      });
    }
    function remove() {
      S.confirm({ title: 'Удалить услугу «' + s.title + '»?', message: 'Если на неё есть будущие записи, услуга выключится, а записи останутся.', confirmText: 'Удалить', danger: true }).then(function (yes) {
        if (!yes) return;
        S.api('/api/booking/services/' + s.id, { method: 'DELETE' }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          S.toast(r.data.disabled ? 'Услуга выключена: на неё есть записи (' + r.data.upcoming + ')' : 'Услуга удалена');
          dirty = false; dlg.close(); changed();
        });
      });
    }
    $('[data-c]').addEventListener('click', function () { dlg.close(); });
    if ($('[data-save]')) $('[data-save]').addEventListener('click', save);
    if ($('[data-del]')) $('[data-del]').addEventListener('click', remove);
    render();
  }

  function changed() {
    load();
    if (ctx.onChange) ctx.onChange();
  }

  // ── категории: порядок и названия ──
  function categories() {
    var d = st.data;
    var list = (d.categories || []).map(function (c) { return { name: c.name, subs: c.subs.slice(), count: c.count }; });
    var dlg = S.dialog({ title: 'Категории услуг', size: 'lg', body: '<div class="sv-cats"></div>', foot: '<button class="btn btn-primary" type="button" data-c>Готово</button>',
      onClose: function () { changed(); } });
    var box = S.$('.sv-cats', dlg.el);
    function draw2() {
      box.innerHTML = '<p class="muted small" style="margin:0 0 10px">В таком порядке категории идут в прайсе, на странице записи и у бота.</p>' +
        list.map(function (c, i) {
          return '<div class="sv-ci"><div class="sv-cr"><b>' + esc(c.name) + '</b><span class="muted small">' + plural(c.count, 'услуга', 'услуги', 'услуг') + '</span>' +
            '<span class="bk-spacer"></span>' +
            '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-up="' + i + '"' + (i ? '' : ' disabled') + ' aria-label="Выше">' + icon('chevron-up') + '</button>' +
            '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-dn="' + i + '"' + (i < list.length - 1 ? '' : ' disabled') + ' aria-label="Ниже">' + icon('chevron-down') + '</button>' +
            '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-rn="' + i + '" aria-label="Переименовать">' + icon('pencil') + '</button></div>' +
            (c.subs.length ? '<div class="sv-cs">' + c.subs.map(function (sub, j) {
              return '<span class="sv-chip">' + esc(sub) + '<button type="button" data-rs="' + i + ':' + j + '" aria-label="Переименовать">' + icon('pencil') + '</button></span>';
            }).join('') + '</div>' : '') + '</div>';
        }).join('');
      S.$$('[data-up],[data-dn]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          var i = +(b.getAttribute('data-up') || b.getAttribute('data-dn')), j = b.hasAttribute('data-up') ? i - 1 : i + 1;
          var t = list[i]; list[i] = list[j]; list[j] = t;
          draw2();
          S.api('/api/booking/services/categories/order', { method: 'POST', body: { names: list.map(function (c) { return c.name; }) } })
            .then(function (r) { if (!r.ok) S.toast(S.apiError(r), 'error'); });
        });
      });
      S.$$('[data-rn]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          var c = list[+b.getAttribute('data-rn')];
          askText('Переименовать категорию', c.name, c.name).then(function (n) {
            if (!n || n === c.name) return;
            S.api('/api/booking/services/categories/rename', { method: 'POST', body: { old: c.name, new: n } }).then(function (r) {
              if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
              c.name = n; draw2(); S.toast('Категория переименована');
            });
          });
        });
      });
      S.$$('[data-rs]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          var p = b.getAttribute('data-rs').split(':'), c = list[+p[0]], sub = c.subs[+p[1]];
          askText('Переименовать подкатегорию', sub, sub, 'Пусто — убрать подкатегорию: услуги останутся в «' + c.name + '»').then(function (n) {
            if (n === null || n === sub) return;
            S.api('/api/booking/services/categories/rename', { method: 'POST', body: { old: c.name, new: n, sub_old: sub } }).then(function (r) {
              if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
              if (n) c.subs[+p[1]] = n; else c.subs.splice(+p[1], 1);
              draw2(); S.toast(n ? 'Подкатегория переименована' : 'Подкатегория убрана');
            });
          });
        });
      });
    }
    draw2();
    S.$('[data-c]', dlg.el).addEventListener('click', function () { dlg.close(); });
  }

  window.BookingServices = { open: open, reload: load, data: function () { return st.data; } };
})();
