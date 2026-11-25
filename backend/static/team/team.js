/**
 * Сотрудники кабинета (ветка 1811-niche-admin-speed): /static/team.html.
 *
 * Владелец добавляет до 10 сотрудников: имя, должность, email (логин), телефон, пароль и разделы
 * (чаты, клиенты, запись, мастера и график, каталог, аналитика). Место — 150 сом в месяц с кошелька,
 * списывается сразу. После добавления и смены пароля — окно «Доступ сотрудника»: email, пароль,
 * «Скопировать» и «Отправить в WhatsApp». API — /api/team (backend/api/team.py).
 */
(function () {
  'use strict';

  var root = null;
  var st = { data: null, busy: false };
  var PERM_ICONS = { inbox: 'inbox', crm: 'users', booking: 'calendar', booking_setup: 'clock', booking_own: 'user-round', orders: 'shopping-cart', catalog: 'package', analytics: 'chart-column', finance: 'banknote' };
  // Готовые роли. Мастер — только свой журнал (кабинет мастера на телефоне), бухгалтер — финансы.
  var PRESETS = [
    { id: 'manager', title: 'Управляющий', perms: ['inbox', 'crm', 'booking', 'booking_setup', 'orders', 'catalog', 'analytics', 'finance'] },
    { id: 'admin', title: 'Администратор', perms: ['inbox', 'crm', 'booking', 'booking_setup', 'orders', 'catalog', 'analytics'] },
    { id: 'master', title: 'Мастер', perms: ['booking_own'] },
    { id: 'accountant', title: 'Бухгалтер', perms: ['finance', 'analytics'] },
    { id: 'sales', title: 'Менеджер', perms: ['inbox', 'crm', 'orders', 'catalog'] },
    { id: 'seller', title: 'Продавец', perms: ['inbox', 'orders', 'catalog'] },
    { id: 'courier', title: 'Курьер', perms: ['orders'] },
    { id: 'operator', title: 'Оператор чатов', perms: ['inbox'] }
  ];
  var COLORS = ['#2a5ce8', '#0d9488', '#7c3aed', '#db2777', '#ea580c', '#0891b2', '#4f46e5', '#16a34a'];

  function icon(n, cls) { return window.VF ? VF.icon(n, cls) : ''; }
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function tok() { try { return localStorage.getItem('auth_token'); } catch (e) { return null; } }
  function toast(msg, type, extra) { if (window.VF) VF.toast(msg, Object.assign({ type: type || 'info' }, extra || {})); else alert(msg); }

  function call(method, path, body) {
    var h = { Authorization: 'Bearer ' + tok() };
    if (body) h['Content-Type'] = 'application/json';
    return fetch('/api/team' + path, { method: method, headers: h, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
      })
      .catch(function () { return { ok: false, status: 0, data: { message: 'Нет связи с сервером' } }; });
  }
  function errOf(res) {
    var d = res && res.data || {};
    var x = d.detail || d.message;
    if (x && typeof x === 'object') return { code: x.error, text: x.message || x.error || 'Ошибка' };
    return { code: null, text: x || 'Не удалось выполнить' };
  }
  function som(v) { return (Number(v) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' сом'; }
  function initials(name) {
    var p = String(name || '?').trim().split(/\s+/);
    return ((p[0] || '?')[0] + (p[1] ? p[1][0] : '')).toUpperCase();
  }
  function fmtPhone(p) {
    var d = String(p || '').replace(/\D/g, '');
    if (d.length === 12 && d.indexOf('996') === 0) return '+996 ' + d.slice(3, 6) + ' ' + d.slice(6, 9) + ' ' + d.slice(9);
    return p || '';
  }
  function colorOf(s) { var h = 0; s = String(s || ''); for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return COLORS[h % COLORS.length]; }
  function fmtDay(iso) {
    if (!iso) return '';
    return new Intl.DateTimeFormat('ru-RU', { timeZone: 'Asia/Bishkek', day: 'numeric', month: 'short' }).format(new Date(iso)).replace('.', '');
  }
  function ago(iso) {
    if (!iso) return '';
    var s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return 'только что';
    if (s < 3600) return Math.round(s / 60) + ' мин назад';
    if (s < 86400) return Math.round(s / 3600) + ' ч назад';
    if (s < 86400 * 2) return 'вчера';
    return fmtDay(iso);
  }
  function permTitle(key) {
    var p = (st.data && st.data.permissions || []).filter(function (x) { return x.key === key; })[0];
    return p ? p.title : key;
  }

  // ── загрузка и отрисовка ──────────────────────────────────────────────
  function load() {
    return call('GET', '').then(function (res) {
      if (window.VF && VF.ready) VF.ready();
      if (!res.ok) {
        var e = errOf(res);
        root.innerHTML = '<div class="tm-empty"><div class="ic-big">' + icon('lock') + '</div><h3>Раздел недоступен</h3><p>' + esc(e.text) + '</p></div>';
        return;
      }
      st.data = res.data;
      draw();
    });
  }

  function draw() {
    var d = st.data;
    if (!d.plan_ok) {
      root.innerHTML = '<div class="tm-empty"><div class="ic-big">' + icon('users') + '</div>' +
        '<h3>Сотрудники — на тарифах Старт, Profi и Voksy Beauty</h3>' +
        '<p>Администратор, менеджер или мастер получают свой вход и работают с чатами, клиентами и записями. Вы решаете, какие разделы им открыть.</p>' +
        '<a class="btn btn-primary" href="/static/dashboard.html#plans">Выбрать тариф</a></div>';
      return;
    }
    var members = d.members || [];
    var active = members.filter(function (m) { return m.is_active; }).length;
    var inc = !!d.included;                      // Voksy Beauty: места входят в тариф
    var full = inc && d.limit && active >= d.limit;
    var monthly = d.free ? 'бесплатно' : inc ? 'в тарифе' : som(d.monthly_som);
    var low = !d.free && !inc && d.wallet_som < (d.monthly_som || 0);
    var addTitle = inc && d.limit ? 'В тарифе ' + d.plan_name + ' — до ' + d.limit + ' ' + staffWord(d.limit) : 'Можно добавить до ' + d.max_members + ' сотрудников';
    var third = inc
      ? '<div class="tm-stat"><div class="k">Тариф</div><div class="v tm-plan">' + esc(d.plan_name || '') + (d.plan_until ? ' <small>до ' + esc(fmtDay(d.plan_until)) + '</small>' : '') + '</div></div>'
      : '<div class="tm-stat' + (low ? ' warn' : '') + '"><div class="k">На кошельке</div><div class="v">' + esc(d.free ? '—' : som(d.wallet_som)) + '</div></div>';
    var html = '<section class="tm-hero">' +
      '<div class="tm-hero-ic">' + icon('users') + '</div>' +
      '<div class="tm-hero-main"><h2>Ваша команда</h2><p>У каждого сотрудника свой вход по email и паролю и только те разделы, которые вы открыли. ' +
      'Деньги, тариф, настройки бота и каналы видите только вы.</p></div>' +
      '<button type="button" class="btn btn-primary" id="tm-add"' + (d.can_add ? '' : ' disabled title="' + esc(addTitle) + '"') + '>' +
      icon('user-plus') + '<span>Добавить сотрудника</span></button>' +
      '<div class="tm-stats">' +
      '<div class="tm-stat' + (full ? ' warn' : '') + '"><div class="k">Сотрудники</div><div class="v">' + active +
        (inc && !d.limit ? ' <small>без лимита</small>' : ' <small>из ' + (inc ? d.limit : d.max_members) + '</small>') + '</div></div>' +
      '<div class="tm-stat"><div class="k">В месяц</div><div class="v">' + esc(monthly) + '</div></div>' +
      third +
      '</div></section>';
    if (full) {
      html += '<div class="tm-upsell">' + icon('sparkles') + '<div><b>Все места тарифа заняты: ' + active + ' из ' + d.limit + '.</b> ' +
        'Нужен ещё сотрудник — перейдите на тариф больше, места добавятся сразу после оплаты.</div>' +
        '<a class="btn btn-sm btn-primary" href="/static/dashboard.html#plans">Сменить тариф</a></div>';
    }
    if (!members.length) {
      html += '<div class="tm-empty"><div class="ic-big">' + icon('user-plus') + '</div><h3>Пока вы работаете один</h3>' +
        '<p>Добавьте сотрудника — он войдёт на сайте со своей почтой и паролем и увидит только свои разделы. ' +
        (d.free ? 'Для вашего аккаунта места бесплатные.'
          : inc ? 'Сотрудники входят в тариф ' + esc(d.plan_name) + (d.limit ? ': до ' + d.limit + ' ' + staffWord(d.limit) + ', без доплаты.' : ' без доплаты и лимита.')
          : 'Место стоит ' + d.seat_som + ' сом в месяц, списывается с кошелька.') + '</p>' +
        '<div class="tm-roles-demo"><span>Администратор: всё, кроме денег</span><span>Мастер: свои записи</span><span>Продавец: чаты и каталог</span></div>' +
        '<button type="button" class="btn btn-primary" id="tm-add2">' + icon('user-plus') + '<span>Добавить сотрудника</span></button></div>';
    } else {
      html += '<div class="tm-list">' + members.map(memberCard).join('') + '</div>';
      html += '<div class="tm-note"><b>Как сотрудник войдёт:</b> откройте ему ' + esc(location.host) + ' → «Войти», email и пароль из окна доступа. ' +
        'Ответы сотрудника клиентам подписаны его именем в чатах. ' +
        (d.free ? '' : inc ? 'Места входят в тариф: пока тариф оплачен, сотрудники работают без доплаты. Закончится тариф — доступ сотрудников приостановится до продления, вам придёт сообщение.'
          : 'Место продлевается само раз в ' + d.seat_days + ' дней (' + d.seat_som + ' сом); не хватит денег на кошельке — доступ сотрудника приостановится, вам придёт сообщение.') + '</div>';
    }
    root.innerHTML = html;
    var add = document.getElementById('tm-add'), add2 = document.getElementById('tm-add2');
    if (add) add.addEventListener('click', function () { editor(null); });
    if (add2) add2.addEventListener('click', function () { editor(null); });
    Array.prototype.forEach.call(root.querySelectorAll('[data-more]'), function (b) {
      b.addEventListener('click', function () { moreMenu(b, find(b.getAttribute('data-more'))); });
    });
    Array.prototype.forEach.call(root.querySelectorAll('[data-on]'), function (b) {
      b.addEventListener('click', function () { activate(find(b.getAttribute('data-on')), b); });
    });
    Array.prototype.forEach.call(root.querySelectorAll('[data-edit]'), function (b) {
      b.addEventListener('click', function () { editor(find(b.getAttribute('data-edit'))); });
    });
  }

  function find(id) { return (st.data.members || []).filter(function (m) { return m.id === id; })[0]; }

  function memberCard(m) {
    var reason = { unpaid: 'не оплачено', plan: 'закончился тариф', limit: 'сверх лимита тарифа', owner: 'вручную' }[m.status_reason] || 'вручную';
    var status = !m.is_active ? '<span class="tm-pill off">Выключен · ' + esc(reason) + '</span>'
      : m.online ? '<span class="tm-pill on">В сети</span>'
      : '<span class="tm-pill idle">' + (m.last_login_at ? 'Последний вход ' + esc(ago(m.last_seen_at || m.last_login_at)) : 'Ещё не входили') + '</span>';
    var meta = [];
    if (st.data.included) { if (m.is_active) meta.push('Входит в тариф'); }
    else if (m.paid_until) meta.push((m.is_active ? 'Оплачено до ' : 'Было оплачено до ') + esc(fmtDay(m.paid_until)));
    else if (st.data.free) meta.push('Место бесплатное');
    if (m.created_at) meta.push('Добавлен ' + esc(fmtDay(m.created_at)));
    return '<div class="tm-card' + (m.is_active ? '' : ' off') + '">' +
      '<div class="tm-ava" style="background:' + colorOf(m.email) + '">' + esc(initials(m.name)) + (m.online ? '<span class="dot"></span>' : '') + '</div>' +
      '<div class="tm-main">' +
      '<div class="tm-name">' + esc(m.name) + (m.role_title ? '<span class="tm-role">· ' + esc(m.role_title) + '</span>' : '') + status + '</div>' +
      '<div class="tm-sub"><span>' + icon('mail') + esc(m.email) + '</span>' + (m.phone ? '<span>' + icon('phone') + esc(fmtPhone(m.phone)) + '</span>' : '') + '</div>' +
      '<div class="tm-perms">' + (m.permissions || []).map(function (k) {
        return '<span class="tm-perm">' + icon(PERM_ICONS[k] || 'check') + esc(permTitle(k)) + '</span>';
      }).join('') + '</div>' +
      (meta.length ? '<div class="tm-meta">' + meta.map(function (x) { return '<span>' + x + '</span>'; }).join('') + '</div>' : '') +
      '</div>' +
      '<div class="tm-actions">' +
      (m.is_active ? '<button type="button" class="btn btn-sm" data-edit="' + esc(m.id) + '">' + icon('pencil') + '<span class="vf-desktop-only">Изменить</span></button>'
        : '<button type="button" class="btn btn-primary btn-sm" data-on="' + esc(m.id) + '">' + icon('power') + '<span>Включить</span></button>') +
      '<button type="button" class="btn btn-ghost btn-icon" data-more="' + esc(m.id) + '" aria-label="Ещё">' + icon('ellipsis') + '</button>' +
      '</div></div>';
  }

  function moreMenu(btn, m) {
    if (!m || !window.VF) return;
    var items = [
      { label: 'Изменить разделы и данные', icon: 'pencil', onClick: function () { editor(m); } },
      { label: 'Новый пароль', icon: 'key-round', onClick: function () { newPassword(m); } }
    ];
    if (m.is_active) items.push({ label: 'Выключить доступ', icon: 'power', onClick: function () { deactivate(m); } });
    else items.push({ label: 'Включить доступ', icon: 'power', onClick: function () { activate(m); } });
    items.push({ sep: true });
    items.push({ label: 'Удалить сотрудника', icon: 'trash-2', danger: true, onClick: function () { remove(m); } });
    VF.menu(btn, items);
  }

  // ── окно добавления / правки ──────────────────────────────────────────
  function dialog(html, size) {
    var bd = document.createElement('div');
    bd.className = 'vf-backdrop';
    bd.innerHTML = '<div class="vf-dialog ' + (size || '') + '" role="dialog" aria-modal="true">' + html + '</div>';
    document.body.appendChild(bd);
    var m = VF.modal(bd, { onClose: function () { setTimeout(function () { bd.remove(); }, 260); } });
    bd.addEventListener('click', function (e) { if (e.target === bd) m.close('backdrop'); });
    Array.prototype.forEach.call(bd.querySelectorAll('[data-close]'), function (b) { b.addEventListener('click', function () { m.close('cancel'); }); });
    m.open();
    return { el: bd, close: function () { m.close('done'); } };
  }

  function editor(m) {
    var d = st.data;
    var isNew = !m;
    var perms = (d.permissions || []).filter(function (p) { return p.available || (m && (m.permissions || []).indexOf(p.key) >= 0); });
    var chosen = {};
    (m ? m.permissions : ['inbox', 'crm']).forEach(function (k) { chosen[k] = true; });
    var seenSets = {};
    var presets = PRESETS.map(function (p) {
      var ok = p.perms.filter(function (k) { return perms.some(function (x) { return x.key === k; }); });
      var sig = ok.slice().sort().join();
      // без нужных разделов роль не показываем; одинаковые наборы (Управляющий без «Финансов») — один раз
      if (!ok.length || seenSets[sig] || (p.id === 'accountant' && ok.indexOf('finance') < 0)) return null;
      seenSets[sig] = true;
      return Object.assign({}, p, { perms: ok });
    }).filter(Boolean);
    var activeNow = (d.members || []).filter(function (x) { return x.is_active; }).length;
    var cost = !isNew || d.free ? ''
      : d.included
        ? '<div class="tm-cost ok">' + icon('check') + '<span>Входит в тариф <b>' + esc(d.plan_name) + '</b>' +
          (d.limit ? ': будет ' + (activeNow + 1) + ' из ' + d.limit + ' ' + staffWord(d.limit) : '') + '. Без доплаты.</span></div>'
        : '<div class="tm-cost">' + icon('wallet') + '<span>Сейчас с кошелька спишется <b>' + d.seat_som + ' сом</b>, дальше — раз в ' + d.seat_days +
          ' дней. На кошельке ' + esc(som(d.wallet_som)) + '. Выключить сотрудника можно в любой момент.</span></div>';
    var dlg = dialog(
      '<div class="vf-dialog-head"><div><h3>' + (isNew ? 'Новый сотрудник' : 'Сотрудник: ' + esc(m.name)) + '</h3>' +
      '<div class="muted" style="font-size:13px;margin-top:4px">' + (isNew ? 'Свой вход по email и паролю, только выбранные разделы' : 'Изменения доступа действуют сразу') + '</div></div>' +
      '<button type="button" class="btn btn-ghost btn-icon btn-sm" data-close aria-label="Закрыть">' + icon('x') + '</button></div>' +
      '<form class="vf-dialog-body tm-form" id="tm-form" autocomplete="off">' +
      '<div class="grid2">' +
      '<div class="field"><label class="label" for="tm-name">Имя</label><input class="input" id="tm-name" maxlength="120" required placeholder="Асель" value="' + esc(m ? m.name : '') + '"></div>' +
      '<div class="field"><label class="label" for="tm-role">Должность</label><input class="input" id="tm-role" maxlength="80" list="tm-roles" placeholder="Администратор" value="' + esc(m ? m.role_title || '' : '') + '">' +
      '<datalist id="tm-roles">' + PRESETS.map(function (p) { return '<option value="' + esc(p.title) + '">'; }).join('') + '</datalist></div>' +
      '<div class="field"><label class="label" for="tm-email">Email — логин для входа</label><input class="input" id="tm-email" type="email" maxlength="255" required placeholder="asel@gmail.com" value="' + esc(m ? m.email : '') + '"></div>' +
      '<div class="field"><label class="label" for="tm-phone">Телефон <span class="muted">(необязательно)</span></label><input class="input" id="tm-phone" type="tel" maxlength="40" placeholder="+996 700 123 456" value="' + esc(m ? m.phone || '' : '') + '">' +
      '<div class="hint">Чтобы отправить доступ в WhatsApp одним нажатием</div></div>' +
      '</div>' +
      (isNew ? '<div class="field"><label class="label" for="tm-pw">Пароль</label><div class="tm-pw"><input class="input" id="tm-pw" maxlength="64" placeholder="Придумаем сами, если оставить пустым">' +
        '<button type="button" class="btn" id="tm-gen">' + icon('refresh-cw') + '<span>Придумать</span></button></div>' +
        '<div class="hint">Не короче 6 символов. Пароль покажем один раз после добавления.</div></div>' : '') +
      '<div class="label" style="margin:2px 0 8px">Что откроем сотруднику</div>' +
      (presets.length ? '<div class="tm-presets">' + presets.map(function (p) {
        return '<button type="button" class="tm-preset" data-preset="' + p.id + '">' + esc(p.title) + '</button>';
      }).join('') + '</div>' : '') +
      '<div class="tm-pgrid">' + perms.map(function (p) {
        return '<label class="tm-pcard' + (chosen[p.key] ? ' on' : '') + '" data-perm="' + p.key + '">' +
          '<input type="checkbox"' + (chosen[p.key] ? ' checked' : '') + '><span class="box">' + icon('check') + '</span>' +
          '<span><span class="t">' + icon(PERM_ICONS[p.key] || 'check') + esc(p.title) + '</span><span class="d">' + esc(p.hint) + '</span></span></label>';
      }).join('') + '</div>' +
      '<div class="hint" style="margin:10px 0 14px">Всегда закрыто: кошелёк и оплата, тариф, настройки бота и каналов, телефония, другие сотрудники.</div>' +
      cost +
      '<div id="tm-err" class="hint" style="color:var(--vf-danger);font-size:13px;margin-top:10px" hidden></div>' +
      '</form>' +
      '<div class="vf-dialog-foot"><button type="button" class="btn" data-close>Отмена</button>' +
      '<button type="button" class="btn btn-primary" id="tm-save">' + (isNew ? (d.free || d.included ? 'Добавить' : 'Добавить за ' + d.seat_som + ' сом') : 'Сохранить') + '</button></div>',
      'vf-dialog-lg tm-dlg');
    var el = dlg.el;
    function sync() {
      Array.prototype.forEach.call(el.querySelectorAll('.tm-pcard'), function (c) {
        var on = !!chosen[c.getAttribute('data-perm')];
        c.classList.toggle('on', on);
        c.querySelector('input').checked = on;
      });
      Array.prototype.forEach.call(el.querySelectorAll('.tm-preset'), function (b) {
        var p = presets.filter(function (x) { return x.id === b.getAttribute('data-preset'); })[0];
        var same = p && Object.keys(chosen).filter(function (k) { return chosen[k]; }).sort().join() === p.perms.slice().sort().join();
        b.classList.toggle('on', !!same);
      });
    }
    Array.prototype.forEach.call(el.querySelectorAll('.tm-pcard'), function (c) {
      c.addEventListener('click', function (e) {
        e.preventDefault();
        var k = c.getAttribute('data-perm');
        chosen[k] = !chosen[k];
        sync();
      });
    });
    Array.prototype.forEach.call(el.querySelectorAll('.tm-preset'), function (b) {
      b.addEventListener('click', function () {
        var p = presets.filter(function (x) { return x.id === b.getAttribute('data-preset'); })[0];
        if (!p) return;
        chosen = {};
        p.perms.forEach(function (k) { chosen[k] = true; });
        var role = el.querySelector('#tm-role');
        if (role && !role.value.trim()) role.value = p.title;
        sync();
      });
    });
    sync();
    var gen = el.querySelector('#tm-gen');
    if (gen) gen.addEventListener('click', function () {
      var a = 'abcdefghjkmnpqrstuvwxyz', n = '23456789', s = '';
      var rnd = function (max) { var u = new Uint32Array(1); (window.crypto || window.msCrypto).getRandomValues(u); return u[0] % max; };
      for (var i = 0; i < 4; i++) s += a[rnd(a.length)];
      s += '-';
      for (var j = 0; j < 4; j++) s += n[rnd(n.length)];
      el.querySelector('#tm-pw').value = s;
    });
    var form = el.querySelector('#tm-form');
    form.addEventListener('submit', function (e) { e.preventDefault(); save(); });
    el.querySelector('#tm-save').addEventListener('click', save);

    function showErr(t) { var x = el.querySelector('#tm-err'); x.textContent = t || ''; x.hidden = !t; }
    function save() {
      var permsOut = Object.keys(chosen).filter(function (k) { return chosen[k]; });
      var body = {
        name: el.querySelector('#tm-name').value.trim(),
        role_title: el.querySelector('#tm-role').value.trim(),
        email: el.querySelector('#tm-email').value.trim(),
        phone: el.querySelector('#tm-phone').value.trim(),
        permissions: permsOut
      };
      if (!body.name) { showErr('Укажите имя сотрудника'); el.querySelector('#tm-name').focus(); return; }
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(body.email)) { showErr('Проверьте email — по нему сотрудник будет входить'); el.querySelector('#tm-email').focus(); return; }
      if (!permsOut.length) { showErr('Отметьте хотя бы один раздел'); return; }
      if (isNew) {
        var pw = el.querySelector('#tm-pw').value.trim();
        if (pw && pw.length < 6) { showErr('Пароль — не короче 6 символов'); el.querySelector('#tm-pw').focus(); return; }
        if (pw) body.password = pw;
      }
      var btn = el.querySelector('#tm-save');
      btn.disabled = true;
      showErr('');
      call(isNew ? 'POST' : 'PATCH', isNew ? '' : '/' + m.id, body).then(function (res) {
        btn.disabled = false;
        if (!res.ok) {
          var er = errOf(res);
          showErr(er.text);
          if (er.code === 'wallet_low') walletLow(er.text);
          if (er.code === 'plan_limit') planLimit(er.text);
          return;
        }
        dlg.close();
        if (res.data.team) st.data = res.data.team; else replaceMember(res.data.member);
        if (window.VoksiAISidebar && window.VoksiAISidebar.refreshWallet) window.VoksiAISidebar.refreshWallet();
        draw();
        if (isNew) credentials(res.data.member, res.data.password, true);
        else toast('Сохранено: доступ сотрудника обновлён', 'success');
      });
    }
  }

  function replaceMember(m) {
    if (!m) return;
    var list = st.data.members || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === m.id) list[i] = m;
  }

  // после «до» и «из»: до 21 сотрудника, до 3 сотрудников (родительный падеж)
  function staffWord(n) {
    n = Math.abs(n);
    return (n % 10 === 1 && n % 100 !== 11) ? 'сотрудника' : 'сотрудников';
  }

  function planLimit(text) {
    toast(text, 'error', {
      action: function () { location.href = '/static/dashboard.html#plans'; },
      actionLabel: 'Сменить тариф', duration: 10000
    });
  }

  function walletLow(text) {
    toast(text, 'error', {
      action: function () { if (window.VoksiAISidebar && window.VoksiAISidebar.openTopup) window.VoksiAISidebar.openTopup(); },
      actionLabel: 'Пополнить', duration: 9000
    });
  }

  // ── доступ: показать пароль один раз, скопировать, отправить в WhatsApp ──
  function credentials(m, password, created) {
    var site = location.origin;
    var text = 'Здравствуйте, ' + m.name + '! Ваш вход в кабинет Voksy AI:\n' +
      site + ' → «Войти»\nEmail: ' + m.email + '\nПароль: ' + password + '\nПароль можно сменить после входа (меню → «Сменить пароль»).';
    var wa = '';
    var digits = String(m.phone || '').replace(/\D/g, '');
    if (digits.length === 10 && digits[0] === '0') digits = '996' + digits.slice(1);
    if (digits.length >= 11) wa = 'https://wa.me/' + digits + '?text=' + encodeURIComponent(text);
    var dlg = dialog(
      '<div class="vf-dialog-head"><div class="row" style="gap:14px;align-items:center"><span class="tm-done-ic">' + icon('circle-check') + '</span>' +
      '<div><h3>' + (created ? esc(m.name) + ' — в команде' : 'Новый пароль для ' + esc(m.name)) + '</h3>' +
      '<div class="muted" style="font-size:13px;margin-top:3px">Пароль показываем один раз — отправьте его сотруднику</div></div></div></div>' +
      '<div class="vf-dialog-body">' +
      '<div class="tm-creds">' +
      '<div class="tm-cred"><span class="k">Сайт</span><span class="v">' + esc(location.host) + ' → «Войти»</span></div>' +
      '<div class="tm-cred"><span class="k">Email</span><span class="v">' + esc(m.email) + '</span><button type="button" class="btn btn-ghost btn-icon btn-sm" data-copy="' + esc(m.email) + '" aria-label="Скопировать email">' + icon('copy') + '</button></div>' +
      '<div class="tm-cred"><span class="k">Пароль</span><span class="v mono">' + esc(password) + '</span><button type="button" class="btn btn-ghost btn-icon btn-sm" data-copy="' + esc(password) + '" aria-label="Скопировать пароль">' + icon('copy') + '</button></div>' +
      '</div>' +
      (created ? '<div class="tm-note">Сотрудник увидит только: ' + esc((m.permissions || []).map(permTitle).join(', ')) + '. Его ответы клиентам будут подписаны именем «' + esc(m.name) + '».</div>' : '') +
      '</div>' +
      '<div class="vf-dialog-foot" style="flex-wrap:wrap">' +
      '<button type="button" class="btn" id="tm-copy-all">' + icon('copy') + '<span>Скопировать всё</span></button>' +
      (wa ? '<a class="btn tm-wa" href="' + esc(wa) + '" target="_blank" rel="noopener">' + icon('message-circle') + '<span>Отправить в WhatsApp</span></a>' : '') +
      '<button type="button" class="btn btn-primary" data-close>Готово</button></div>', 'vf-dialog-sm');
    function copy(t) {
      var done = function () { toast('Скопировано', 'success'); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, function () { fallbackCopy(t); done(); });
      else { fallbackCopy(t); done(); }
    }
    Array.prototype.forEach.call(dlg.el.querySelectorAll('[data-copy]'), function (b) { b.addEventListener('click', function () { copy(b.getAttribute('data-copy')); }); });
    dlg.el.querySelector('#tm-copy-all').addEventListener('click', function () { copy(text); });
  }
  function fallbackCopy(t) {
    var ta = document.createElement('textarea'); ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    ta.remove();
  }

  // ── действия ──────────────────────────────────────────────────────────
  function newPassword(m) {
    VF.confirm({ title: 'Новый пароль для ' + m.name + '?', message: 'Придумаем новый пароль. Старый перестанет подходить: сотрудник выйдет из кабинета на всех устройствах.',
      confirmText: 'Сменить пароль', icon: 'key-round' }).then(function (ok) {
      if (!ok) return;
      call('POST', '/' + m.id + '/password', {}).then(function (res) {
        if (!res.ok) { toast(errOf(res).text, 'error'); return; }
        replaceMember(res.data.member);
        draw();
        credentials(res.data.member, res.data.password, false);
      });
    });
  }

  function deactivate(m) {
    var offMsg = st.data.included
      ? 'Сотрудник сразу выйдет из кабинета, а его место в тарифе освободится — можно отдать другому. Включить снова можно в любой момент, если есть свободное место.'
      : 'Сотрудник сразу выйдет из кабинета. Оплаченный срок сохранится — включить снова можно без доплаты до ' + (m.paid_until ? fmtDay(m.paid_until) : 'конца срока') + '.';
    VF.confirm({ title: 'Выключить доступ для ' + m.name + '?', message: offMsg,
      confirmText: 'Выключить', danger: true, icon: 'power' }).then(function (ok) {
      if (!ok) return;
      call('POST', '/' + m.id + '/deactivate').then(function (res) {
        if (!res.ok) { toast(errOf(res).text, 'error'); return; }
        st.data = res.data.team || st.data;
        draw();
        toast(m.name + ': доступ выключен', 'success');
      });
    });
  }

  function activate(m, btn) {
    if (!m) return;
    var paid = m.paid_until && new Date(m.paid_until).getTime() > Date.now();
    var go = function () {
      if (btn) btn.disabled = true;
      call('POST', '/' + m.id + '/activate').then(function (res) {
        if (btn) btn.disabled = false;
        if (!res.ok) {
          var e = errOf(res);
          if (e.code === 'wallet_low') walletLow(e.text); else if (e.code === 'plan_limit') planLimit(e.text); else toast(e.text, 'error');
          return;
        }
        st.data = res.data.team || st.data;
        if (window.VoksiAISidebar && window.VoksiAISidebar.refreshWallet) window.VoksiAISidebar.refreshWallet();
        draw();
        toast(m.name + ': доступ включён', 'success');
      });
    };
    if (paid || st.data.free || st.data.included) { go(); return; }
    VF.confirm({ title: 'Включить ' + m.name + '?', message: 'С кошелька спишется ' + st.data.seat_som + ' сом за следующие ' + st.data.seat_days + ' дней.',
      confirmText: 'Включить за ' + st.data.seat_som + ' сом', icon: 'wallet' }).then(function (ok) { if (ok) go(); });
  }

  function remove(m) {
    VF.confirm({ title: 'Удалить ' + m.name + '?', message: 'Сотрудник больше не сможет войти.' + (st.data.included || st.data.free ? '' : ' Оплаченные дни не возвращаются.') + ' Переписка и клиенты, с которыми он работал, остаются у вас.',
      confirmText: 'Удалить', danger: true }).then(function (ok) {
      if (!ok) return;
      call('DELETE', '/' + m.id).then(function (res) {
        if (!res.ok) { toast(errOf(res).text, 'error'); return; }
        st.data = res.data.team || st.data;
        draw();
        toast('Удалено из команды: ' + m.name, 'success');
      });
    });
  }

  function start() {
    root = document.getElementById('tm-root');
    if (!root) return;
    if (!tok()) { location.href = '/'; return; }
    load();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
