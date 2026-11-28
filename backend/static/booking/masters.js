/**
 * Мастера салона (Voksy Beauty, шаг 1.2): экран карточек и редактор мастера — /static/booking.html.
 *
 * Экран «Мастера»: карточки с фото, специализацией, опытом, графиком, услугами, отметками
 * «онлайн-запись» и «вход в кабинет», счётчик по тарифу, «Добавить мастера».
 * Редактор (одна кнопка «Сохранить» на все вкладки; фото загружаются сразу):
 *   Профиль — фото, имя, специализация, телефон, цвет в журнале, опыт, о себе, онлайн-запись;
 *   График — дни, часы, перерыв, выходные даты;
 *   Услуги и цены — какие услуги делает и свои цена/длительность (топ-мастер дороже);
 *   Работы — портфолио и сертификаты (показываются на странице салона);
 *   Вход и оплата — вход мастера в свой кабинет (создать / привязать) и % / оклад (владелец, бухгалтер).
 * API: /api/booking/resources*, /api/payroll/masters/{id}/scheme (оплата мастера). Общее — window.BookingMasters.
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var COLORS = ['#2a5ce8', '#0d9488', '#7c3aed', '#db2777', '#ea580c', '#0891b2', '#4d7c0f', '#b45309'];
  var ROLES = ['Парикмахер-стилист', 'Колорист', 'Барбер', 'Мастер маникюра', 'Мастер педикюра', 'Мастер ногтевого сервиса',
    'Бровист', 'Лашмейкер', 'Косметолог', 'Массажист', 'Визажист', 'Мастер эпиляции', 'Топ-мастер'];
  var DAYS = [['mon', 'Пн'], ['tue', 'Вт'], ['wed', 'Ср'], ['thu', 'Чт'], ['fri', 'Пт'], ['sat', 'Сб'], ['sun', 'Вс']];
  var LIMIT_PORTFOLIO = 30, LIMIT_CERTS = 12;

  // ── общие кусочки ──
  function ava(r, size, extra) {
    var s = size || 40;
    var inner = r && r.photo_url
      ? '<img src="' + esc(r.photo_url) + '" alt="" loading="lazy" decoding="async">'
      : '<b>' + esc((r && r.initials) || '?') + '</b>';
    return '<span class="bk-ava ' + (extra || '') + '" style="--c:' + esc((r && r.color) || '#2a5ce8') + ';--s:' + s + 'px">' + inner + '</span>';
  }
  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    return n + ' ' + (m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many);
  }
  function years(n) { return plural(n, 'год', 'года', 'лет'); }
  function money(v) { return v == null || v === '' ? '' : Number(v).toLocaleString('ru-RU') + ' сом'; }
  function toMin(t) { var p = String(t || '0:0').split(':'); return +p[0] * 60 + +(p[1] || 0); }

  // «Пн–Пт 9:00–19:00, Сб 10:00–16:00» — коротко о графике
  function schedSummary(r) {
    var groups = [], cur = null;
    DAYS.forEach(function (d, i) {
      var iv = (r.schedule || {})[d[0]] || [];
      var key = iv.length ? iv[0][0].replace(/^0/, '') + '–' + iv[iv.length - 1][1].replace(/^0/, '') : '';
      if (cur && cur.key === key && cur.end === i - 1) { cur.end = i; }
      else { cur = { key: key, start: i, end: i }; groups.push(cur); }
    });
    var parts = groups.filter(function (g) { return g.key; }).map(function (g) {
      return (g.start === g.end ? DAYS[g.start][1] : DAYS[g.start][1] + '–' + DAYS[g.end][1]) + ' ' + g.key;
    });
    return parts.length ? parts.join(', ') : 'График не задан';
  }
  function weekHours(r) {
    var m = 0;
    DAYS.forEach(function (d) { ((r.schedule || {})[d[0]] || []).forEach(function (iv) { m += toMin(iv[1]) - toMin(iv[0]); }); });
    return Math.round(m / 60);
  }

  // ── экран «Мастера» ──
  var ctx = null;   // {data, root, back, reload, canSetup, isOwner}

  function data() { return ctx.data(); }
  function list() { return (data() && data().resources) || []; }

  function open(c) {
    ctx = c;
    draw();
  }

  function draw() {
    var d = data() || {};
    var rs = list();
    var lim = d.plan_limit;
    var active = rs.filter(function (r) { return r.is_active; }).length;
    var head =
      '<div class="ms-head"><button class="btn btn-sm" type="button" data-ms-back>' + icon('arrow-left') + '<span>К журналу</span></button>' +
      '<div class="ms-title"><h2>Мастера</h2><div class="muted small">' +
      (lim && lim.limit ? 'В тарифе ' + esc(lim.plan_name) + ': ' + active + ' из ' + lim.limit : plural(active, 'мастер работает', 'мастера работают', 'мастеров работают')) +
      '</div></div><div class="bk-spacer"></div>' +
      (ctx.canSetup ? '<button class="btn btn-primary btn-sm" type="button" data-ms-add>' + icon('user-plus') + '<span>Добавить мастера</span></button>' : '') +
      '</div>';
    var cards = rs.map(function (r) {
      var svcTotal = ((d.services || []).filter(function (s) { return !r.service_ids || r.service_ids.indexOf(s.id) >= 0; })).length;
      var own = Object.keys(r.service_prices || {}).length;
      var login = r.team_member_id ? ((d.logins || []).filter(function (l) { return l.id === r.team_member_id; })[0] || { email: 'вход есть' }) : null;
      return '<article class="ms-card' + (r.is_active ? '' : ' off') + '" data-ms="' + esc(r.id) + '" tabindex="0">' +
        '<div class="ms-cover" style="--c:' + esc(r.color) + '">' + ava(r, 84, 'ring') +
        (r.is_active ? '' : '<span class="ms-flag">Выключен</span>') + '</div>' +
        '<div class="ms-body"><div class="ms-name">' + esc(r.name) + '</div>' +
        '<div class="ms-role">' + esc(r.role || 'Мастер') + (r.experience_years ? ' · опыт ' + years(r.experience_years) : '') + '</div>' +
        '<div class="ms-line">' + icon('clock') + '<span>' + esc(schedSummary(r)) + '</span></div>' +
        '<div class="ms-line">' + icon('sparkles') + '<span>' + plural(svcTotal, 'услуга', 'услуги', 'услуг') + (own ? ' · ' + own + ' со своей ценой' : '') + '</span></div>' +
        '<div class="ms-tags">' +
        '<span class="ms-tag ' + (r.online_booking ? 'ok' : '') + '">' + icon(r.online_booking ? 'globe' : 'lock') + (r.online_booking ? 'Онлайн-запись' : 'Только через админа') + '</span>' +
        (ctx.isOwner ? '<span class="ms-tag ' + (login ? 'ok' : '') + '">' + icon(login ? 'key-round' : 'user') + (login ? 'Свой кабинет' : 'Без входа') + '</span>' : '') +
        ((r.portfolio || []).length ? '<span class="ms-tag">' + icon('image') + (r.portfolio || []).length + ' фото работ</span>' : '') +
        '</div></div></article>';
    }).join('');
    var empty = !rs.length ? '<div class="bk-card"><div class="bk-empty">' + icon('users') + 'Мастеров пока нет.<br>Добавьте первого — с фото и графиком.</div></div>' : '';
    var tip = ctx.isOwner && rs.length && !rs.some(function (r) { return r.team_member_id; })
      ? '<div class="ms-tip">' + icon('smartphone') + '<div><b>Дайте мастерам свой кабинет на телефоне</b><span>Мастер видит только свои записи, клиентов и заработок. Откройте карточку мастера → «Вход и оплата».</span></div></div>' : '';
    ctx.root.innerHTML = '<div class="ms">' + head + tip + '<div class="ms-grid">' + cards + '</div>' + empty + '</div>';
    S.$('[data-ms-back]', ctx.root).addEventListener('click', function () { ctx.back(); });
    var add = S.$('[data-ms-add]', ctx.root);
    if (add) add.addEventListener('click', addMaster);
    S.$$('.ms-card', ctx.root).forEach(function (el) {
      function go() { var r = byId(el.getAttribute('data-ms')); if (r) editor(r); }
      el.addEventListener('click', go);
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
    });
  }

  function byId(id) { return list().filter(function (r) { return r.id === id; })[0]; }
  function replace(r) {
    var d = data();
    var found = false;
    d.resources = (d.resources || []).map(function (x) { if (x.id === r.id) { found = true; return r; } return x; });
    if (!found) d.resources.push(r);
  }

  function limitToast(res) {
    if (window.VF && VF.toast) {
      VF.toast(S.apiError(res), { type: 'error', duration: 10000, actionLabel: 'Сменить тариф',
        action: function () { location.href = '/static/dashboard.html#plans'; } });
    } else S.toast(S.apiError(res), 'error');
  }

  function addMaster() {
    var n = list().length + 1;
    S.api('/api/booking/resources', { method: 'POST', body: { name: 'Мастер ' + n } }).then(function (r) {
      if (!r.ok && r.status === 402) { limitToast(r); return; }
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      replace(r.data.resource);
      draw();
      editor(r.data.resource, { fresh: true });
    });
  }

  // ── редактор мастера ──
  function editor(r0, opt) {
    opt = opt || {};
    var d = data();
    var r = JSON.parse(JSON.stringify(r0));     // черновик
    var services = d.services || [];
    var canMoney = r.pay_percent !== undefined;
    var dirty = false;
    var tabs = [['p', 'Профиль', 'user-round'], ['s', 'График', 'clock'], ['v', 'Услуги и цены', 'sparkles'], ['w', 'Работы', 'image']];
    if (ctx.isOwner || canMoney) tabs.push(['l', ctx.isOwner ? 'Вход и оплата' : 'Оплата', 'key-round']);
    var dlg = S.dialog({
      title: opt.fresh ? 'Новый мастер' : r.name, size: 'xl',
      body: '<div class="me-tabs" role="tablist">' + tabs.map(function (t, i) {
        return '<button type="button" role="tab" data-tab="' + t[0] + '" class="' + (i ? '' : 'on') + '">' + icon(t[2]) + '<span>' + t[1] + '</span></button>';
      }).join('') + '</div><div class="me-panes"></div>',
      foot: '<button class="btn btn-ghost" type="button" data-del style="color:var(--vf-danger);margin-right:auto">' + icon('trash-2') + '<span>Удалить</span></button>' +
        (ctx.isOwner ? '<a class="btn" data-preview target="_blank" rel="noopener" href="/static/master.html?resource_id=' + esc(r.id) + '">' + icon('smartphone') + '<span>Как видит мастер</span></a>' : '') +
        '<button class="btn btn-primary" type="button" data-save>Сохранить</button>',
      onClose: function () { if (ctx.refresh) ctx.refresh(); else draw(); },
      // Закрыть с несохранёнными правками — спросить
      beforeClose: function () {
        try { collect(); } catch (e) { /* поля уже убраны */ }
        if (!dirty) return true;
        S.confirm({ title: 'Сохранить изменения?', message: 'Вы поменяли карточку мастера.', confirmText: 'Сохранить', cancelText: 'Не сохранять' }).then(function (yes) {
          if (yes) save(); else { dirty = false; dlg.close(); }
        });
        return false;
      }
    });
    var el = dlg.el;
    el.querySelector('.vf-dialog').classList.add('me-dlg');
    var panes = S.$('.me-panes', el);
    if (!ctx.canSetup) {
      S.$('[data-del]', el).remove();
    }
    var cur = 'p';
    function mark() { dirty = true; }

    function render(tab) {
      cur = tab;
      S.$$('.me-tabs [data-tab]', el).forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-tab') === tab); });
      panes.innerHTML = ({ p: paneProfile, s: paneSchedule, v: paneServices, w: paneWorks, l: paneLogin })[tab]();
      ({ p: bindProfile, s: bindSchedule, v: bindServices, w: bindWorks, l: bindLogin })[tab]();
      panes.scrollTop = 0;
    }
    function collect() {
      // читаем поля текущей вкладки в черновик
      if (cur === 'p') {
        r.name = S.$('#me-name', el).value;
        r.role = S.$('#me-role', el).value;
        r.phone = S.$('#me-phone', el).value;
        r.bio = S.$('#me-bio', el).value;
        var y = S.$('#me-since', el).value;
        r.experience_since = y ? +y : null;
        r.online_booking = S.$('#me-online', el).checked;
      } else if (cur === 's') {
        DAYS.forEach(function (dd) {
          if (!S.$('[data-wd="' + dd[0] + '"]', el).checked) { r.schedule[dd[0]] = []; return; }
          var from = S.$('[data-from="' + dd[0] + '"]', el).value || '09:00', to = S.$('[data-to="' + dd[0] + '"]', el).value || '19:00';
          var bf = S.$('[data-bf="' + dd[0] + '"]', el).value, bt = S.$('[data-bt="' + dd[0] + '"]', el).value;
          r.schedule[dd[0]] = bf && bt && bf < bt && from < bf && bt < to ? [[from, bf], [bt, to]] : [[from, to]];
        });
      } else if (cur === 'v') {
        var all = S.$('#me-all', el).checked;
        var ids = [];
        var prices = {};
        S.$$('[data-sv]', el).forEach(function (row) {
          var id = row.getAttribute('data-sv');
          if (S.$('input[type=checkbox]', row).checked) ids.push(id);
          var p = S.$('[data-pp]', row).value, m = S.$('[data-pm]', row).value;
          if (p !== '' || m !== '') prices[id] = { price: p === '' ? null : +p, duration_min: m === '' ? null : +m };
        });
        r.service_ids = all ? null : ids;
        r.service_prices = prices;
      } else if (cur === 'w') {
        S.$$('[data-cap]', el).forEach(function (inp) {
          var id = inp.getAttribute('data-cap');
          (r.portfolio || []).forEach(function (it) { if (it.id === id) it.caption = inp.value; });
        });
        S.$$('[data-ctitle]', el).forEach(function (inp) {
          var id = inp.getAttribute('data-ctitle');
          (r.certificates || []).forEach(function (it) { if (it.id === id) it.title = inp.value; });
        });
      } else if (cur === 'l' && canMoney) {
        var pp = S.$('#me-pct', el), pf = S.$('#me-fix', el), ps = S.$('#me-shift', el);
        if (pp) r.pay_percent = pp.value === '' ? 0 : +pp.value;
        if (pf) r.pay_fixed = pf.value === '' ? 0 : +pf.value;
        if (ps) r.pay_shift = ps.value === '' ? 0 : +ps.value;
      }
    }

    // — профиль —
    function paneProfile() {
      var now = new Date().getFullYear(), opts = '<option value="">—</option>';
      for (var y = now; y >= now - 45; y--) opts += '<option value="' + y + '"' + (+r.experience_since === y ? ' selected' : '') + '>' + y + '</option>';
      return '<div class="me-prof">' +
        '<div class="me-photo">' + ava(r, 112, 'ring') +
        '<div class="me-photo-act"><button class="btn btn-sm" type="button" data-up-ava>' + icon('upload') + '<span>' + (r.photo_url ? 'Заменить фото' : 'Загрузить фото') + '</span></button>' +
        (r.photo_url ? '<button class="btn btn-ghost btn-sm" type="button" data-rm-ava>Убрать</button>' : '') +
        '<div class="hint">Лицо крупно, светлый фон. Фото видят клиенты при записи.</div></div></div>' +
        '<div class="me-fields">' +
        '<div class="st-grid2"><div class="field"><label class="label" for="me-name">Имя</label><input class="input" id="me-name" maxlength="120" value="' + esc(r.name) + '"></div>' +
        '<div class="field"><label class="label" for="me-role">Специализация</label><input class="input" id="me-role" maxlength="120" list="me-roles" placeholder="Мастер маникюра" value="' + esc(r.role || '') + '">' +
        '<datalist id="me-roles">' + ROLES.map(function (x) { return '<option value="' + esc(x) + '">'; }).join('') + '</datalist></div>' +
        '<div class="field"><label class="label" for="me-phone">Телефон <span class="muted">(виден только вам)</span></label><input class="input" id="me-phone" type="tel" maxlength="32" placeholder="+996 700 123 456" value="' + esc(r.phone || '') + '"></div>' +
        '<div class="field"><label class="label" for="me-since">Работает с</label><select class="select-native" id="me-since">' + opts + '</select>' +
        '<div class="hint">' + (r.experience_years ? 'Опыт ' + years(r.experience_years) : 'Покажем «опыт N лет»') + '</div></div></div>' +
        '<div class="field"><label class="label">Цвет в журнале</label><div class="me-colors">' + COLORS.map(function (c) {
          return '<button type="button" class="me-color' + (c === r.color ? ' on' : '') + '" data-color="' + c + '" style="--c:' + c + '" aria-label="Цвет ' + c + '"></button>';
        }).join('') + '<label class="me-color custom" title="Свой цвет"><input type="color" id="me-color" value="' + esc(r.color || '#2a5ce8') + '">' + icon('plus') + '</label></div></div>' +
        '<div class="field"><label class="label" for="me-bio">О себе</label><textarea class="textarea" id="me-bio" rows="4" maxlength="1500" placeholder="Например: делаю аккуратный маникюр с укреплением, люблю нюдовые оттенки. Работаю на материалах Kodi.">' + esc(r.bio || '') + '</textarea>' +
        '<div class="hint"><span id="me-bio-n">' + (r.bio || '').length + '</span>/1500 · покажем на странице салона</div></div>' +
        '<div class="st-switch-row"><div class="t"><b>Онлайн-запись к мастеру</b><div>Клиенты по ссылке, бот и голосовой агент записывают к нему сами. Выключите — запишет только администратор.</div></div>' +
        '<label class="switch"><input type="checkbox" id="me-online"' + (r.online_booking ? ' checked' : '') + '><span class="track"></span></label></div>' +
        (r.is_active ? '' : '<div class="me-note warn">' + icon('circle-alert') + '<span>Мастер выключен: его нет в журнале и в онлайн-записи. <button class="btn btn-sm" type="button" data-on>Включить</button></span></div>') +
        '</div></div>';
    }
    function bindProfile() {
      S.$$('input,textarea,select', panes).forEach(function (x) { x.addEventListener('input', mark); x.addEventListener('change', mark); });
      S.$('#me-bio', el).addEventListener('input', function (e) { S.$('#me-bio-n', el).textContent = e.target.value.length; });
      S.$$('.me-color[data-color]', el).forEach(function (b) {
        b.addEventListener('click', function () { r.color = b.getAttribute('data-color'); mark(); S.$$('.me-color', el).forEach(function (x) { x.classList.toggle('on', x === b); }); refreshAva(); });
      });
      S.$('#me-color', el).addEventListener('input', function (e) { r.color = e.target.value; mark(); S.$$('.me-color', el).forEach(function (x) { x.classList.remove('on'); }); refreshAva(); });
      S.$('[data-up-ava]', el).addEventListener('click', function () { collect(); pick(false, function (files) { upload('avatar', files[0]); }); });
      var rm = S.$('[data-rm-ava]', el);
      if (rm) rm.addEventListener('click', function () { collect(); removeMedia(r.photo_id); });
      var on = S.$('[data-on]', el);
      if (on) on.addEventListener('click', function () {
        S.api('/api/booking/resources/' + r.id, { method: 'PATCH', body: { is_active: true } }).then(function (res) {
          if (!res.ok && res.status === 402) { limitToast(res); return; }
          if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
          collect(); r.is_active = true; replace(Object.assign({}, res.data.resource)); render('p'); S.toast('Мастер включён');
        });
      });
    }
    function refreshAva() { var a = S.$('.me-photo .bk-ava', el); if (a) a.outerHTML = ava(r, 112, 'ring'); }

    // — график —
    function paneSchedule() {
      var offs = (r.days_off || []).slice().sort();
      return '<div class="me-sched">' + DAYS.map(function (dd) {
        var iv = r.schedule[dd[0]] || [];
        var a = iv[0] || ['09:00', '19:00'], end = iv.length ? iv[iv.length - 1][1] : a[1];
        var br = iv.length > 1 ? [iv[0][1], iv[1][0]] : ['', ''];
        return '<div class="me-day' + (iv.length ? '' : ' off') + '" data-day="' + dd[0] + '">' +
          '<label class="me-dname"><input type="checkbox" data-wd="' + dd[0] + '"' + (iv.length ? ' checked' : '') + '><span>' + dd[1] + '</span></label>' +
          '<div class="me-times"><input type="time" data-from="' + dd[0] + '" value="' + esc(a[0]) + '"><span>–</span><input type="time" data-to="' + dd[0] + '" value="' + esc(end) + '">' +
          '<span class="me-br">перерыв</span><input type="time" data-bf="' + dd[0] + '" value="' + esc(br[0]) + '"><span>–</span><input type="time" data-bt="' + dd[0] + '" value="' + esc(br[1]) + '"></div>' +
          '<span class="me-dayoff">выходной</span></div>';
      }).join('') + '</div>' +
        '<div class="me-row"><button class="btn btn-sm" type="button" data-copy>' + icon('copy') + '<span>Как в понедельник — на все рабочие дни</span></button></div>' +
        '<div class="field" style="margin-top:16px"><label class="label">Выходные даты, отпуск</label>' +
        '<div class="me-offs">' + (offs.length ? offs.map(function (x) {
          var dt = new Date(x + 'T12:00:00');
          return '<span class="me-off">' + esc(dt.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', weekday: 'short' })) + '<button type="button" data-rmoff="' + esc(x) + '" aria-label="Убрать">' + icon('x') + '</button></span>';
        }).join('') : '<span class="muted small">Нет — мастер работает по графику</span>') + '</div>' +
        '<div class="me-row"><input class="input" type="date" id="me-off" style="max-width:180px"><span class="muted small">по</span><input class="input" type="date" id="me-off2" style="max-width:180px">' +
        '<button class="btn btn-sm" type="button" data-addoff>' + icon('plus') + '<span>Добавить</span></button></div>' +
        '<div class="hint">Одна дата — выходной день; две — отпуск целиком. В эти дни к мастеру нельзя записаться.</div></div>';
    }
    function bindSchedule() {
      S.$$('input', S.$('.me-sched', el)).forEach(function (x) { x.addEventListener('change', function () { mark(); if (x.hasAttribute('data-wd')) x.closest('.me-day').classList.toggle('off', !x.checked); }); });
      S.$('[data-copy]', el).addEventListener('click', function () {
        collect();
        var mon = r.schedule.mon || [];
        if (!mon.length) { S.toast('Сначала задайте понедельник', 'error'); return; }
        DAYS.forEach(function (dd) { if (dd[0] !== 'mon' && (r.schedule[dd[0]] || []).length) r.schedule[dd[0]] = JSON.parse(JSON.stringify(mon)); });
        mark(); render('s'); S.toast('Часы понедельника скопированы на рабочие дни');
      });
      S.$('[data-addoff]', el).addEventListener('click', function () {
        collect();
        var a = S.$('#me-off', el).value, b = S.$('#me-off2', el).value || a;
        if (!a) { S.toast('Выберите дату', 'error'); return; }
        if (b < a) { var t = a; a = b; b = t; }
        var set = {};
        (r.days_off || []).forEach(function (x) { set[x] = 1; });
        var dt = new Date(a + 'T12:00:00'), end = new Date(b + 'T12:00:00'), n = 0;
        while (dt <= end && n < 120) { set[dt.toISOString().slice(0, 10)] = 1; dt.setDate(dt.getDate() + 1); n++; }
        r.days_off = Object.keys(set).sort();
        mark(); render('s');
      });
      S.$$('[data-rmoff]', el).forEach(function (b) {
        b.addEventListener('click', function () {
          collect();
          var x = b.getAttribute('data-rmoff');
          r.days_off = (r.days_off || []).filter(function (y) { return y !== x; });
          mark(); render('s');
        });
      });
    }

    // — услуги и цены —
    function paneServices() {
      if (!services.length) {
        return '<div class="bk-empty">' + icon('sparkles') + 'В каталоге пока нет услуг.<br>Добавьте их — с ценой и длительностью — и отметьте здесь, что делает мастер.' +
          '<div style="margin-top:12px"><a class="btn btn-sm" href="/static/catalog.html">' + icon('package') + '<span>Открыть каталог услуг</span></a></div></div>';
      }
      var all = !r.service_ids;
      var groups = {};
      services.forEach(function (s) { (groups[s.collection || 'Услуги'] = groups[s.collection || 'Услуги'] || []).push(s); });
      return '<div class="st-switch-row"><div class="t"><b>Делает все услуги салона</b><div>Выключите, чтобы отметить только свои</div></div>' +
        '<label class="switch"><input type="checkbox" id="me-all"' + (all ? ' checked' : '') + '><span class="track"></span></label></div>' +
        (services.length > 8 ? '<input class="input me-search" id="me-sq" placeholder="Найти услугу" type="search">' : '') +
        '<div class="me-svc-head"><span>Услуга</span><span>Цена мастера</span><span>Мин</span></div>' +
        Object.keys(groups).map(function (g) {
          return '<div class="me-sgroup"><div class="me-sgt">' + esc(g) + '</div>' + groups[g].map(function (s) {
            var on = all || (r.service_ids || []).indexOf(s.id) >= 0;
            var own = (r.service_prices || {})[s.id] || {};
            return '<div class="me-svc' + (on ? '' : ' no') + '" data-sv="' + esc(s.id) + '" data-q="' + esc((s.title || '').toLowerCase()) + '">' +
              '<label class="me-sname"><input type="checkbox"' + (on ? ' checked' : '') + (all ? ' disabled' : '') + '><span><b>' + esc(s.title) + '</b>' +
              '<small>В каталоге: ' + (s.price != null ? esc(money(s.price)) + ' · ' : '') + s.duration_min + ' мин</small></span></label>' +
              '<input class="input" type="number" inputmode="numeric" min="0" step="10" data-pp placeholder="' + (s.price != null ? esc(s.price) : 'цена') + '" value="' + (own.price != null ? esc(own.price) : '') + '">' +
              '<input class="input" type="number" inputmode="numeric" min="5" max="1440" step="5" data-pm placeholder="' + esc(s.duration_min) + '" value="' + (own.duration_min ? esc(own.duration_min) : '') + '"></div>';
          }).join('') + '</div>';
        }).join('') +
        '<div class="hint" style="margin-top:10px">Пустое поле — цена и время из каталога. Свои — например, у топ-мастера дороже: клиент увидит их при записи к нему, а время в журнале займётся по его длительности.</div>';
    }
    function bindServices() {
      if (!services.length) return;
      S.$$('input', panes).forEach(function (x) { x.addEventListener('input', mark); x.addEventListener('change', mark); });
      S.$('#me-all', el).addEventListener('change', function (e) {
        S.$$('[data-sv] input[type=checkbox]', el).forEach(function (c) { c.disabled = e.target.checked; if (e.target.checked) c.checked = true; });
        S.$$('[data-sv]', el).forEach(function (row) { row.classList.toggle('no', !S.$('input[type=checkbox]', row).checked); });
      });
      S.$$('[data-sv] input[type=checkbox]', el).forEach(function (c) {
        c.addEventListener('change', function () { c.closest('[data-sv]').classList.toggle('no', !c.checked); });
      });
      var q = S.$('#me-sq', el);
      if (q) q.addEventListener('input', function () {
        var v = q.value.trim().toLowerCase();
        S.$$('[data-sv]', el).forEach(function (row) { row.hidden = v && row.getAttribute('data-q').indexOf(v) < 0; });
      });
    }

    // — работы: портфолио и сертификаты —
    function paneWorks() {
      var pf = r.portfolio || [], cs = r.certificates || [];
      return '<div class="me-sec"><div class="me-sec-h"><div><b>Портфолио</b><span class="muted small"> · ' + pf.length + ' из ' + LIMIT_PORTFOLIO + '</span>' +
        '<div class="hint">Лучшие работы мастера — клиенты увидят их на странице салона и при выборе мастера.</div></div>' +
        (pf.length < LIMIT_PORTFOLIO ? '<button class="btn btn-sm" type="button" data-up-pf>' + icon('upload') + '<span>Добавить фото</span></button>' : '') + '</div>' +
        (pf.length ? '<div class="me-gal">' + pf.map(function (it, i) {
          return '<figure class="me-shot"><img src="' + esc(it.url) + '" alt="" loading="lazy">' +
            '<div class="me-shot-act">' + (i ? '<button type="button" data-mv="' + i + '" data-dir="-1" aria-label="Левее">' + icon('chevron-left') + '</button>' : '') +
            (i < pf.length - 1 ? '<button type="button" data-mv="' + i + '" data-dir="1" aria-label="Правее">' + icon('chevron-right') + '</button>' : '') +
            '<button type="button" data-rm="' + esc(it.id) + '" aria-label="Удалить">' + icon('trash-2') + '</button></div>' +
            '<input class="input" data-cap="' + esc(it.id) + '" maxlength="200" placeholder="Подпись: маникюр, френч" value="' + esc(it.caption || '') + '"></figure>';
        }).join('') + '</div>' : '<div class="me-drop" data-up-pf>' + icon('image') + '<b>Загрузите фото работ</b><span>Можно сразу несколько. JPG, PNG — до 15 МБ каждое.</span></div>') +
        '</div>' +
        '<div class="me-sec"><div class="me-sec-h"><div><b>Сертификаты и дипломы</b><span class="muted small"> · ' + cs.length + ' из ' + LIMIT_CERTS + '</span>' +
        '<div class="hint">Повышают доверие — покажем в карточке мастера.</div></div>' +
        (cs.length < LIMIT_CERTS ? '<button class="btn btn-sm" type="button" data-up-ct>' + icon('upload') + '<span>Добавить</span></button>' : '') + '</div>' +
        (cs.length ? '<div class="me-certs">' + cs.map(function (it) {
          return '<div class="me-cert"><img src="' + esc(it.url) + '" alt="" loading="lazy"><input class="input" data-ctitle="' + esc(it.id) + '" maxlength="200" placeholder="Например: Kodi Professional, 2023" value="' + esc(it.title || '') + '">' +
            '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-rm="' + esc(it.id) + '" aria-label="Удалить">' + icon('trash-2') + '</button></div>';
        }).join('') + '</div>' : '<div class="muted small">Пока нет.</div>') + '</div>' +
        '<div class="me-progress" id="me-prog" hidden></div>';
    }
    function bindWorks() {
      S.$$('[data-cap],[data-ctitle]', el).forEach(function (x) { x.addEventListener('input', mark); });
      S.$$('[data-up-pf]', el).forEach(function (b) {
        b.addEventListener('click', function () {
          collect();
          pick(true, function (files) { uploadMany('portfolio', files.slice(0, LIMIT_PORTFOLIO - (r.portfolio || []).length)); });
        });
      });
      var ct = S.$('[data-up-ct]', el);
      if (ct) ct.addEventListener('click', function () { collect(); pick(true, function (files) { uploadMany('certificate', files.slice(0, LIMIT_CERTS - (r.certificates || []).length)); }); });
      S.$$('[data-rm]', el).forEach(function (b) {
        b.addEventListener('click', function () {
          collect();
          S.confirm({ title: 'Удалить фото?', message: 'Фото пропадёт и со страницы салона.', confirmText: 'Удалить', danger: true }).then(function (yes) {
            if (yes) removeMedia(b.getAttribute('data-rm'));
          });
        });
      });
      S.$$('[data-mv]', el).forEach(function (b) {
        b.addEventListener('click', function () {
          collect();
          var i = +b.getAttribute('data-mv'), j = i + +b.getAttribute('data-dir');
          var pf = r.portfolio;
          var t = pf[i]; pf[i] = pf[j]; pf[j] = t;
          mark(); render('w');
        });
      });
    }

    // — вход и оплата —
    function paneLogin() {
      var out = '';
      if (ctx.isOwner) {
        var logins = d.logins || [];
        var linked = r.team_member_id ? logins.filter(function (l) { return l.id === r.team_member_id; })[0] : null;
        var free = logins.filter(function (l) { return l.id !== r.team_member_id && !list().some(function (x) { return x.team_member_id === l.id; }); });
        out += '<div class="me-sec"><div class="me-sec-h"><div><b>Кабинет мастера на телефоне</b>' +
          '<div class="hint">Мастер входит на voksyai.online со своим email и паролем и видит только своё: записи, клиентов, заработок и статистику. Отменить запись или увидеть чужие — не может.</div></div></div>';
        if (linked) {
          out += '<div class="me-login on">' + icon('key-round') + '<div><b>' + esc(linked.email) + '</b><span>' + esc(linked.name) + (linked.is_active ? '' : ' · вход выключен') +
            ((linked.permissions || []).indexOf('booking_own') < 0 ? ' · видит весь журнал' : '') + '</span></div>' +
            '<button class="btn btn-sm" type="button" data-unlink>Отвязать</button></div>' +
            '<div class="me-row"><a class="btn btn-sm" href="/static/team.html">' + icon('users') + '<span>Пароль и доступ — в «Сотрудники»</span></a>' +
            '<a class="btn btn-sm" target="_blank" rel="noopener" href="/static/master.html?resource_id=' + esc(r.id) + '">' + icon('smartphone') + '<span>Открыть его кабинет</span></a></div>';
        } else {
          var lim = d.plan_limit;
          out += '<div class="me-login">' + icon('user') + '<div><b>Входа пока нет</b><span>Создайте — займёт минуту' + (lim && lim.limit ? '. Входы сотрудников входят в тариф: до ' + lim.limit : '') + '.</span></div></div>' +
            '<div class="st-grid2"><div class="field"><label class="label" for="me-em">Email мастера — логин</label><input class="input" id="me-em" type="email" maxlength="255" placeholder="aigul@gmail.com"></div>' +
            '<div class="field"><label class="label" for="me-pw">Пароль</label><input class="input" id="me-pw" maxlength="64" placeholder="Придумаем сами"></div></div>' +
            '<button class="btn btn-primary btn-sm" type="button" data-mklogin>' + icon('user-plus') + '<span>Создать вход мастеру</span></button>' +
            (free.length ? '<div class="field" style="margin-top:16px"><label class="label" for="me-link">или привязать сотрудника, у которого уже есть вход</label>' +
              '<div class="me-row"><select class="select-native" id="me-link" style="max-width:320px"><option value="">Выберите сотрудника</option>' +
              free.map(function (l) { return '<option value="' + esc(l.id) + '">' + esc(l.name + ' · ' + l.email) + '</option>'; }).join('') + '</select>' +
              '<button class="btn btn-sm" type="button" data-link>Привязать</button></div></div>' : '');
        }
        out += '</div>';
      }
      if (canMoney) {
        var own = Object.keys(r.pay_categories || {});
        out += '<div class="me-sec"><div class="me-sec-h"><div><b>Оплата мастера</b><div class="hint">% с оплаченных услуг, ставка за выход и оклад — можно сочетать. Ведомость, авансы, штрафы, премии и выплаты — в «Зарплате».</div></div>' +
          (r.id ? '<a class="btn btn-sm" href="/static/payroll.html?master=' + esc(r.id) + '">Зарплата</a>' : '') + '</div>' +
          '<div class="st-grid2"><div class="field"><label class="label" for="me-pct">% с услуг</label><input class="input" id="me-pct" type="number" inputmode="decimal" min="0" max="100" step="1" placeholder="40" value="' + (r.pay_percent ? esc(r.pay_percent) : '') + '"></div>' +
          '<div class="field"><label class="label" for="me-shift">Ставка за выход, сом</label><input class="input" id="me-shift" type="number" inputmode="numeric" min="0" step="100" placeholder="0" value="' + (r.pay_shift ? esc(r.pay_shift) : '') + '"></div>' +
          '<div class="field"><label class="label" for="me-fix">Оклад в месяц, сом</label><input class="input" id="me-fix" type="number" inputmode="numeric" min="0" step="500" placeholder="0" value="' + (r.pay_fixed ? esc(r.pay_fixed) : '') + '"></div></div>' +
          (own.length ? '<div class="hint">Свой процент: ' + esc(own.map(function (k) { return k + ' ' + r.pay_categories[k] + '%'; }).join(', ')) + ' — меняется в «Зарплате» → мастер → «Оплата мастера».</div>' :
            '<div class="hint">Свой процент на отдельные категории (например, окрашивание 50%) — в «Зарплате» → мастер → «Оплата мастера».</div>') + '</div>';
      }
      return out;
    }
    function bindLogin() {
      S.$$('#me-pct,#me-fix,#me-shift', el).forEach(function (x) { x.addEventListener('input', mark); });
      var un = S.$('[data-unlink]', el);
      if (un) un.addEventListener('click', function () {
        S.confirm({ title: 'Отвязать вход?', message: 'Сотрудник останется, но перестанет видеть журнал этого мастера.', confirmText: 'Отвязать' }).then(function (yes) {
          if (yes) linkLogin(null);
        });
      });
      var ln = S.$('[data-link]', el);
      if (ln) ln.addEventListener('click', function () {
        var v = S.$('#me-link', el).value;
        if (!v) { S.toast('Выберите сотрудника', 'error'); return; }
        linkLogin(v);
      });
      var mk = S.$('[data-mklogin]', el);
      if (mk) mk.addEventListener('click', function () {
        var em = S.$('#me-em', el).value.trim(), pw = S.$('#me-pw', el).value.trim();
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(em)) { S.toast('Проверьте email мастера', 'error'); S.$('#me-em', el).focus(); return; }
        mk.disabled = true;
        S.api('/api/booking/resources/' + r.id + '/login', { method: 'POST', body: { email: em, password: pw || null } }).then(function (res) {
          mk.disabled = false;
          if (!res.ok && res.status === 402) { limitToast(res); return; }
          if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
          d.logins = res.data.logins;
          r.team_member_id = res.data.resource.team_member_id;
          replace(Object.assign({}, byId(r.id) || {}, { team_member_id: r.team_member_id }));
          showPassword(em, res.data.password, r.name);
          render('l');
        });
      });
    }
    function linkLogin(memberId) {
      S.api('/api/booking/resources/' + r.id + '/login', { method: 'PUT', body: { team_member_id: memberId } }).then(function (res) {
        if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
        d.logins = res.data.logins;
        r.team_member_id = res.data.resource.team_member_id;
        // у других мастеров этот вход снят сервером — подтянем
        list().forEach(function (x) { if (x.id !== r.id && memberId && x.team_member_id === memberId) x.team_member_id = null; });
        replace(Object.assign({}, byId(r.id) || {}, { team_member_id: r.team_member_id }));
        S.toast(memberId ? 'Вход привязан' : 'Вход отвязан');
        render('l');
      });
    }

    // — фото: выбор, загрузка, удаление —
    function pick(multi, cb) {
      var inp = document.createElement('input');
      inp.type = 'file'; inp.accept = 'image/*'; if (multi) inp.multiple = true;
      inp.addEventListener('change', function () { var f = Array.prototype.slice.call(inp.files || []); if (f.length) cb(f); });
      inp.click();
    }
    function applyServer(res) {
      var fresh = res.data.resource;
      // медиа берём с сервера, несохранённые поля черновика не трогаем
      r.photo_id = fresh.photo_id; r.photo_url = fresh.photo_url;
      var caps = {}; (r.portfolio || []).forEach(function (it) { caps[it.id] = it.caption; });
      var tts = {}; (r.certificates || []).forEach(function (it) { tts[it.id] = it.title; });
      r.portfolio = (fresh.portfolio || []).map(function (it) { return Object.assign({}, it, caps[it.id] !== undefined ? { caption: caps[it.id] } : {}); });
      r.certificates = (fresh.certificates || []).map(function (it) { return Object.assign({}, it, tts[it.id] !== undefined ? { title: tts[it.id] } : {}); });
      var base = byId(r.id) || {};
      replace(Object.assign({}, base, { photo_id: fresh.photo_id, photo_url: fresh.photo_url, portfolio: fresh.portfolio, certificates: fresh.certificates }));
    }
    function upload(kind, file) {
      var prog = S.$('#me-prog', el);
      S.toast('Загружаем фото…');
      return S.upload('/api/booking/resources/' + r.id + '/media?kind=' + kind, file).then(function (res) {
        if (!res.ok) { S.toast(S.apiError(res), 'error'); return false; }
        applyServer(res);
        if (kind === 'avatar') { render('p'); S.toast('Фото мастера обновлено'); }
        if (prog) prog.hidden = true;
        return true;
      });
    }
    function uploadMany(kind, files) {
      if (!files.length) return;
      var done = 0, ok = 0;
      var prog = S.$('#me-prog', el);
      function next() {
        if (done >= files.length) {
          render('w');
          S.toast(ok === files.length ? (ok === 1 ? 'Фото добавлено' : 'Добавлено фото: ' + ok) : 'Добавлено ' + ok + ' из ' + files.length, ok === files.length ? '' : 'error');
          return;
        }
        if (prog) { prog.hidden = false; prog.innerHTML = '<span class="vf-spin"></span>Загружаем ' + (done + 1) + ' из ' + files.length + '…'; }
        S.upload('/api/booking/resources/' + r.id + '/media?kind=' + kind, files[done]).then(function (res) {
          done++;
          if (res.ok) { ok++; applyServer(res); } else if (done === 1 || ok === 0) S.toast(S.apiError(res), 'error');
          next();
        });
      }
      next();
    }
    function removeMedia(id) {
      if (!id) return;
      S.api('/api/booking/resources/' + r.id + '/media/' + id, { method: 'DELETE' }).then(function (res) {
        if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
        applyServer(res);
        render(cur);
      });
    }

    // — сохранить / удалить —
    function save() {
      collect();
      if (!String(r.name || '').trim()) { render('p'); S.toast('Укажите имя мастера', 'error'); S.$('#me-name', el).focus(); return; }
      var body = { name: r.name, role: r.role || '', phone: r.phone || '', color: r.color, bio: r.bio || '',
        experience_since: r.experience_since || null, online_booking: !!r.online_booking, schedule: r.schedule,
        days_off: r.days_off || [], service_ids: r.service_ids === null ? [] : r.service_ids, service_prices: r.service_prices || {},
        portfolio: (r.portfolio || []).map(function (it) { return { id: it.id, caption: it.caption || '' }; }),
        certificates: (r.certificates || []).map(function (it) { return { id: it.id, title: it.title || '' }; }) };
      var btn = S.$('[data-save]', el); btn.disabled = true;
      var reqs = [S.api('/api/booking/resources/' + r.id, { method: 'PATCH', body: body })];
      if (canMoney && (r.pay_percent !== r0.pay_percent || r.pay_fixed !== r0.pay_fixed || (r.pay_shift || 0) !== (r0.pay_shift || 0))) {
        reqs.push(S.api('/api/payroll/masters/' + r.id + '/scheme', { method: 'PUT', body: { percent: r.pay_percent || 0, shift: r.pay_shift || 0, fixed: r.pay_fixed || 0 } }));
      }
      Promise.all(reqs).then(function (rs) {
        btn.disabled = false;
        var bad = rs.filter(function (x) { return !x.ok; })[0];
        if (bad) { S.toast(S.apiError(bad), 'error'); return; }
        var fresh = rs[0].data.resource;
        if (canMoney) { fresh.pay_percent = r.pay_percent || 0; fresh.pay_fixed = r.pay_fixed || 0; fresh.pay_shift = r.pay_shift || 0; fresh.pay_categories = r.pay_categories || {}; }
        replace(fresh);
        r0 = fresh; dirty = false;
        S.toast('Мастер сохранён');
        dlg.close();
        if (ctx.reload) ctx.reload();
      });
    }
    S.$('[data-save]', el).addEventListener('click', save);
    var del = S.$('[data-del]', el);
    if (del) del.addEventListener('click', function () {
      S.confirm({ title: 'Удалить мастера «' + r.name + '»?', message: 'Если у мастера есть будущие записи, он будет выключен, а записи останутся в журнале.', confirmText: 'Удалить', danger: true }).then(function (yes) {
        if (!yes) return;
        S.api('/api/booking/resources/' + r.id, { method: 'DELETE' }).then(function (res) {
          if (!res.ok) { S.toast(S.apiError(res), 'error'); return; }
          var dd = data();
          if (res.data.disabled) {
            replace(Object.assign({}, byId(r.id), { is_active: false }));
            S.toast('Мастер выключен: у него есть будущие записи (' + res.data.upcoming + ')');
          } else {
            dd.resources = dd.resources.filter(function (x) { return x.id !== r.id; });
            S.toast('Мастер удалён');
          }
          dirty = false; dlg.close();
          if (ctx.reload) ctx.reload();
        });
      });
    });
    S.$$('.me-tabs [data-tab]', el).forEach(function (b) {
      b.addEventListener('click', function () { collect(); render(b.getAttribute('data-tab')); });
    });
    render(opt.tab || 'p');
  }

  window.BookingMasters = { open: open, ava: ava, editor: function (r, opt) { editor(r, opt); }, schedSummary: schedSummary, weekHours: weekHours, bind: function (c) { ctx = c; } };
})();
