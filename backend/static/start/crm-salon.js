/* ============================================================================
 * start/crm-salon.js — клиенты салона (Voksy Beauty, шаг 2.1) внутри CRM (start-crm.html)
 * Включается у ниш с онлайн-записью (localStorage.vf_business → modules.booking).
 *   • вкладка «Клиенты»: сегменты со счётчиками (постоянные, новые, VIP, давно не были, ушли, неявки,
 *     ДР скоро, записаны, не записывались), поиск, мастер, источник, пол, сортировка, выгрузка в Excel;
 *     столбцы считаются из журнала записи: визиты, сумма, последний визит, ближайшая запись, любимый мастер;
 *   • карточка клиента: блок «В салоне» (цифры, любимые мастер и услуга, ДР, сегменты, будущие записи,
 *     история визитов, «Записать») + поля ДР, пол, источник в «Данных клиента».
 * Данные — /api/beauty/clients (services/salon_clients.py). Обязательны только имя и телефон.
 * ========================================================================== */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var PAGE = 50;
  var SORTS = [
    ['last_visit', 'Недавно были'], ['spent', 'Больше потратили'], ['visits', 'Чаще ходят'],
    ['next', 'Ближайшая запись'], ['birthday', 'Ближайший ДР'], ['name', 'По имени'], ['created', 'Новые карточки']
  ];
  var GENDERS = [['', 'Пол не указан'], ['f', 'Женщина'], ['m', 'Мужчина']];

  function biz() { try { return JSON.parse(localStorage.getItem('vf_business') || 'null'); } catch (e) { return null; } }
  function enabled() { var b = biz(); return !!(b && b.modules && b.modules.booking); }
  function money(v) { return v == null ? '—' : Math.round(Number(v)).toLocaleString('ru-RU') + ' сом'; }
  function daysAgo(n) {
    if (n == null) return '';
    if (n === 0) return 'сегодня';
    if (n === 1) return 'вчера';
    return n + ' ' + plural(n, 'день', 'дня', 'дней') + ' назад';
  }
  function plural(n, a, b, c) {
    n = Math.abs(n) % 100; var n1 = n % 10;
    if (n > 10 && n < 20) return c;
    if (n1 > 1 && n1 < 5) return b;
    if (n1 === 1) return a;
    return c;
  }
  function visitsWord(n) { return n + ' ' + plural(n, 'визит', 'визита', 'визитов'); }
  function bdayBadge(b) {
    if (!b || b.in_days == null || b.in_days > 14) return '';
    var t = b.in_days === 0 ? 'ДР сегодня' : b.in_days === 1 ? 'ДР завтра' : 'ДР через ' + b.in_days + ' дн.';
    return ' <span class="st-badge info cs-bd" title="' + esc(b.text) + '">' + icon('gift') + esc(t) + '</span>';
  }

  // ══════════════════════════════ СПИСОК ══════════════════════════════════
  var st = { segment: 'all', q: '', master: '', source: '', gender: '', sort: 'last_visit', items: [], total: 0, meta: null, seq: 0 };
  var host = null;   // { pane, openClient, isPhone, waLink, telLink }

  function initList(opts) {
    host = opts;
    var qp = new URLSearchParams(location.search);
    if (qp.get('segment')) st.segment = qp.get('segment');
    host.pane.innerHTML =
      '<div class="cs-segs st-colbar" id="cs-segs"><div class="skeleton" style="height:32px;width:100%"></div></div>' +
      '<div class="cs-hint muted small" id="cs-hint"></div>' +
      '<div class="st-toolbar cs-tools">' +
      '<div class="st-search">' + icon('search') + '<input class="input" id="cs-q" type="search" placeholder="Имя или телефон"></div>' +
      '<div class="cs-filters">' +
      '<select class="select-native" id="cs-sort" aria-label="Сортировка">' + SORTS.map(function (s) { return '<option value="' + s[0] + '">' + s[1] + '</option>'; }).join('') + '</select>' +
      '<select class="select-native" id="cs-master" aria-label="Мастер"><option value="">Все мастера</option></select>' +
      '<select class="select-native" id="cs-source" aria-label="Источник"><option value="">Все источники</option></select>' +
      '<select class="select-native" id="cs-gender" aria-label="Пол"><option value="">Мужчины и женщины</option><option value="f">Женщины</option><option value="m">Мужчины</option></select>' +
      '</div>' +
      '<button class="btn st-hide-phone" id="cs-export" type="button" hidden>' + icon('download') + '<span>Excel</span></button>' +
      '<span class="muted small" id="cs-total"></span></div>' +
      '<div id="cs-list"></div>';
    var $ = function (s) { return host.pane.querySelector(s); };
    $('#cs-sort').value = st.sort;
    $('#cs-q').addEventListener('input', S.debounce(function (e) { st.q = e.target.value.trim(); load(true); }, 250));
    ['master', 'source', 'gender', 'sort'].forEach(function (k) {
      $('#cs-' + k).addEventListener('change', function (e) { st[k] = e.target.value; load(true); });
    });
    $('#cs-export').addEventListener('click', exportCsv);
    load(true);
  }

  function query(offset, counts) {
    var p = new URLSearchParams({ segment: st.segment, sort: st.sort, limit: String(PAGE), offset: String(offset), counts: counts ? '1' : '0' });
    ['q', 'master', 'source', 'gender'].forEach(function (k) { if (st[k]) p.set(k, st[k]); });
    return p;
  }

  function load(reset) {
    var seq = ++st.seq;
    var list = host.pane.querySelector('#cs-list');
    if (reset) { st.items = []; list.innerHTML = '<div class="skeleton" style="height:220px"></div>'; }
    S.api('/api/beauty/clients?' + query(reset ? 0 : st.items.length, reset)).then(function (r) {
      if (seq !== st.seq) return;
      if (!r.ok) { list.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      st.items = reset ? r.data.items : st.items.concat(r.data.items);
      st.total = r.data.total || 0;
      if (r.data.segments) { st.meta = r.data; drawMeta(); }
      draw();
    });
  }

  function drawMeta() {
    var m = st.meta, $ = function (s) { return host.pane.querySelector(s); };
    var segs = $('#cs-segs');
    segs.innerHTML = m.segments.map(function (s) {
      if (s.key !== 'all' && !s.count && s.key !== st.segment) return '';
      return '<button type="button" class="st-colchip' + (s.key === st.segment ? ' on' : '') + '" data-seg="' + esc(s.key) + '" title="' + esc(s.hint) + '">' +
        esc(s.title) + '<span class="n">' + s.count + '</span></button>';
    }).join('');
    S.$$('[data-seg]', segs).forEach(function (b) {
      b.addEventListener('click', function () {
        st.segment = b.dataset.seg;
        if (st.segment === 'birthday') st.sort = 'birthday';
        else if (st.sort === 'birthday') st.sort = 'last_visit';
        $('#cs-sort').value = st.sort;
        S.$$('[data-seg]', segs).forEach(function (x) { x.classList.toggle('on', x === b); });
        hint(); load(true);
      });
    });
    hint();
    var ms = $('#cs-master');
    if (ms.options.length <= 1) {
      ms.innerHTML = '<option value="">Все мастера</option>' + (m.masters || []).map(function (x) { return '<option value="' + esc(x.id) + '">' + esc(x.name) + '</option>'; }).join('');
      ms.hidden = !(m.masters || []).length;
    }
    var ss = $('#cs-source');
    if (ss.options.length <= 1) {
      ss.innerHTML = '<option value="">Все источники</option>' + (m.sources || []).map(function (x) { return '<option value="' + esc(x.key) + '">' + esc(x.title) + '</option>'; }).join('') +
        '<option value="none">Не указан</option>';
    }
    $('#cs-export').hidden = !m.can_export;
  }

  function hint() {
    var m = st.meta, el = host.pane.querySelector('#cs-hint');
    var s = m && m.segments.filter(function (x) { return x.key === st.segment; })[0];
    el.textContent = s && s.key !== 'all' ? s.hint : '';
    el.hidden = !el.textContent;
  }

  function draw() {
    var list = host.pane.querySelector('#cs-list');
    host.pane.querySelector('#cs-total').textContent = st.total ? 'Найдено: ' + st.total : '';
    if (!st.items.length) {
      var filtered = st.q || st.master || st.source || st.gender || st.segment !== 'all';
      list.innerHTML = '<div class="st-empty">' + (filtered ? 'Никого не нашли. Измените сегмент или фильтры.'
        : 'Клиентов пока нет. Они появятся, когда кто-то запишется, напишет боту или позвонит.') + '</div>';
      return;
    }
    if (host.isPhone()) drawPhone(list); else drawTable(list);
    var more = list.querySelector('#cs-more');
    if (more) more.addEventListener('click', function () { more.disabled = true; load(false); });
  }

  function nextText(iso) {
    if (!iso) return '';
    return S.fmtDate(iso, true);
  }

  function drawTable(list) {
    list.innerHTML = '<div class="table-wrap"><table class="table st-table cs-table"><thead><tr>' +
      '<th>Клиент</th><th>Телефон</th><th class="num">Визиты</th><th class="num">Сумма</th><th>Последний визит</th>' +
      '<th>Запись</th><th>Любимый мастер</th><th>Источник</th></tr></thead><tbody>' +
      st.items.map(function (x) {
        var s = x.stats || {};
        return '<tr data-id="' + esc(x.id) + '">' +
          '<td><div class="st-person"><span class="st-avatar">' + esc(S.initial(x.name || x.phone)) + '</span><div>' +
          '<div class="nm">' + esc(x.name || 'Без имени') + bdayBadge(x.birthday_info) +
          (s.no_shows ? ' <span class="st-badge err" title="Неявки">' + s.no_shows + ' неявк.</span>' : '') + '</div>' +
          '<div class="sub">' + esc(s.favorite_service || ' ') + '</div></div></div></td>' +
          '<td class="mono">' + esc(x.phone ? S.fmtPhone(x.phone) : '—') + '</td>' +
          '<td class="num">' + (s.visits || 0) + '</td>' +
          '<td class="num">' + esc(s.spent ? money(s.spent) : '—') + '</td>' +
          '<td>' + (s.last_visit ? esc(S.fmtDate(s.last_visit, false)) + '<div class="sub muted small">' + esc(daysAgo(s.days_since_last)) + '</div>' : '<span class="muted">—</span>') + '</td>' +
          '<td>' + (s.next_at ? '<span class="st-badge ok">' + icon('calendar') + esc(nextText(s.next_at)) + '</span>' : '<span class="muted">—</span>') + '</td>' +
          '<td>' + esc(s.favorite_master || '—') + '</td>' +
          '<td class="muted">' + esc(x.source_title || '—') + '</td></tr>';
      }).join('') + '</tbody></table></div>' +
      (st.items.length < st.total ? '<div class="st-more"><button class="btn" id="cs-more" type="button">Показать ещё</button></div>' : '');
    S.$$('tr[data-id]', list).forEach(function (tr) { tr.addEventListener('click', function () { host.openClient(tr.dataset.id); }); });
  }

  function drawPhone(list) {
    list.innerHTML = '<div class="st-mlist">' + st.items.map(function (x) {
      var s = x.stats || {};
      var wa = x.phone ? host.waLink(x.phone) : '';
      var line = s.visits ? visitsWord(s.visits) + (s.spent ? ' · ' + money(s.spent) : '') : 'ещё не был';
      var when = s.next_at ? 'запись ' + nextText(s.next_at) : (s.last_visit ? 'был ' + daysAgo(s.days_since_last) : '');
      return '<div class="st-mrow" data-id="' + esc(x.id) + '" role="button" tabindex="0">' +
        '<span class="st-avatar">' + esc(S.initial(x.name || x.phone)) + '</span>' +
        '<div class="st-mmain"><div class="cs-nmrow"><span class="nm">' + esc(x.name || S.fmtPhone(x.phone) || 'Без имени') + '</span>' + bdayBadge(x.birthday_info) + '</div>' +
        '<div class="cs-line">' + esc(line) + '</div>' + (when ? '<div class="cs-when' + (s.next_at ? ' ok' : '') + '">' + esc(when) + '</div>' : '') + '</div>' +
        '<div class="st-mact">' +
        (x.phone ? '<a class="st-mbtn" href="' + esc(host.telLink(x.phone)) + '" aria-label="Позвонить">' + icon('phone') + '</a>' : '') +
        (wa ? '<a class="st-mbtn wa" href="' + esc(wa) + '" target="_blank" rel="noopener" aria-label="Написать в WhatsApp">' + icon('message-circle') + '</a>' : '') +
        '</div></div>';
    }).join('') + '</div>' +
      (st.items.length < st.total ? '<div class="st-more"><button class="btn" id="cs-more" type="button">Показать ещё</button></div>' : '');
    S.$$('.st-mrow', list).forEach(function (row) {
      row.addEventListener('click', function (e) { if (!e.target.closest('.st-mact')) host.openClient(row.dataset.id); });
      row.addEventListener('keydown', function (e) { if (e.key === 'Enter') host.openClient(row.dataset.id); });
    });
  }

  function exportCsv() {
    var btn = host.pane.querySelector('#cs-export');
    btn.disabled = true;
    var p = query(0, false); p.delete('limit'); p.delete('offset'); p.delete('counts');
    var d = new Date();
    var name = 'clients_' + d.toISOString().slice(0, 10) + '.csv';
    S.download('/api/beauty/clients/export.csv?' + p, name)
      .then(function () { S.toast('Файл скачан — откройте его в Excel', 'success'); })
      .catch(function () { S.toast('Не удалось выгрузить', 'error'); })
      .then(function () { btn.disabled = false; });
  }

  function refresh() { if (host) load(true); }

  // ══════════════════════════════ КАРТОЧКА ══════════════════════════════════
  // Блок «В салоне» — над «Данными клиента»; грузится отдельно, карточка не ждёт его
  function cardSection(box, contact) {
    box.innerHTML = '<div class="skeleton" style="height:140px"></div>';
    S.api('/api/beauty/clients/' + contact.id).then(function (r) {
      if (!r.ok) { box.innerHTML = ''; box.hidden = true; return; }
      drawCard(box, contact, r.data);
    });
  }

  function drawCard(box, contact, c) {
    var s = c.stats || {};
    var bd = c.birthday;
    var tiles = [
      ['Визиты', s.visits || 0, s.first_visit ? 'с ' + S.fmtDate(s.first_visit, false) : 'ещё не был'],
      ['Сумма визитов', s.spent ? money(s.spent) : '0 сом', s.avg_check ? 'средний чек ' + money(s.avg_check) : ''],
      ['Неявки', s.no_shows || 0, s.cancelled ? 'отмен: ' + s.cancelled : 'отмен нет'],
      ['Последний визит', s.last_visit ? S.fmtDate(s.last_visit, false) : '—', s.last_visit ? daysAgo(s.days_since_last) : '']
    ];
    var kv = [];
    if (s.favorite_master) kv.push(['Любимый мастер', esc(s.favorite_master)]);
    if (s.favorite_service) kv.push(['Любимая услуга', esc(s.favorite_service)]);
    if (s.usual_interval_days) kv.push(['Обычно ходит', 'раз в ' + s.usual_interval_days + ' ' + plural(s.usual_interval_days, 'день', 'дня', 'дней')]);
    if (bd) kv.push(['День рождения', esc(bd.text) + (bd.age != null ? ' · ' + bd.age + ' ' + plural(bd.age, 'год', 'года', 'лет') : '') +
      (bd.in_days <= 14 ? ' <span class="st-badge info">' + (bd.in_days === 0 ? 'сегодня' : 'через ' + bd.in_days + ' дн.') + '</span>' : '')]);
    if (c.source_title) kv.push(['Откуда пришёл', esc(c.source_title)]);
    if (s.avg_review) kv.push(['Оценки визитов', '★ ' + String(s.avg_review).replace('.', ',')]);
    var seg = (c.segments || []).map(function (x) { return '<span class="st-badge ' + (x.key === 'vip' || x.key === 'regular' ? 'ok' : x.key === 'no_show' || x.key === 'lost' ? 'err' : x.key === 'sleeping' ? 'warn' : 'info') + '">' + esc(x.title) + '</span>'; }).join(' ');
    var bookHref = '/static/booking.html?new=1&contact=' + encodeURIComponent(contact.id) +
      '&name=' + encodeURIComponent(contact.name || '') + (contact.phone && !/^(web|tg|ig|wa):/.test(contact.phone) ? '&phone=' + encodeURIComponent(contact.phone) : '');
    var lastSvc = (c.history || []).filter(function (h) { return h.status === 'arrived' || h.status === 'completed'; })[0];
    var againHref = lastSvc ? bookHref + (lastSvc.master_id ? '&master=' + encodeURIComponent(lastSvc.master_id) : '') : '';
    box.innerHTML =
      '<div class="row-between" style="gap:10px;flex-wrap:wrap;margin-bottom:10px"><h4 style="margin:0">В салоне</h4>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap">' + (againHref && lastSvc.master ? '<a class="btn btn-sm" href="' + esc(againHref) + '">' + icon('rotate-ccw') + '<span>Снова к ' + esc(lastSvc.master) + '</span></a>' : '') +
      '<a class="btn btn-primary btn-sm" href="' + esc(bookHref) + '">' + icon('calendar-plus') + '<span>Записать</span></a></div></div>' +
      (seg ? '<div class="cs-segs-card">' + seg + '</div>' : '') +
      '<div class="cs-tiles">' + tiles.map(function (t) {
        return '<div class="cs-tile"><div class="lbl">' + esc(t[0]) + '</div><div class="val">' + esc(String(t[1])) + '</div><div class="sub">' + esc(t[2] || '') + '</div></div>';
      }).join('') + '</div>' +
      (kv.length ? '<dl class="st-kv cs-kv">' + kv.map(function (k) { return '<dt>' + k[0] + '</dt><dd>' + k[1] + '</dd>'; }).join('') + '</dl>' : '') +
      (c.upcoming && c.upcoming.length ? '<div class="cs-sub">Будущие записи</div>' + c.upcoming.map(visitRow).join('') : '') +
      (c.history && c.history.length ? '<div class="cs-sub">История · ' + c.history.length + '</div><div class="cs-hist">' + c.history.map(visitRow).join('') + '</div>'
        : '<p class="muted small" style="margin:10px 0 0">Записей ещё не было.</p>');
  }

  function localDay(iso) {
    try { return new Date(iso).toLocaleDateString('sv-SE', { timeZone: 'Asia/Bishkek' }); } catch (e) { return String(iso).slice(0, 10); }
  }
  function visitRow(h) {
    var cls = h.status === 'no_show' ? 'err' : h.status === 'cancelled' ? '' : h.status === 'arrived' || h.status === 'completed' ? 'ok' : 'info';
    return '<a class="cs-visit" href="/static/booking.html?open=' + encodeURIComponent(h.id) + '&date=' + encodeURIComponent(localDay(h.start)) + '">' +
      '<span class="d">' + esc(S.fmtDate(h.start, true)) + '</span>' +
      '<span class="t"><b>' + esc(h.service || 'Запись') + '</b>' + (h.master ? ' · ' + esc(h.master) : '') +
      (h.review_score ? ' <span class="cs-star">★' + h.review_score + '</span>' : '') + '</span>' +
      '<span class="p">' + (h.price != null ? esc(money(h.price)) : '') + '</span>' +
      '<span class="st-badge ' + cls + '">' + esc(h.status_title) + '</span></a>';
  }

  // Поля ДР / пол / источник в «Данных клиента»
  function profileFields(c, sources) {
    var bdv = c.birthday ? (c.birthday.indexOf('--') === 0 ? c.birthday.slice(5).split('-').reverse().join('.') : c.birthday.split('-').reverse().join('.')) : '';
    var src = '<option value="">Не указан</option>' + (sources || []).map(function (s) {
      return '<option value="' + esc(s.key) + '"' + (c.source === s.key ? ' selected' : '') + '>' + esc(s.title) + '</option>';
    }).join('');
    if (c.source && !(sources || []).some(function (s) { return s.key === c.source; })) src += '<option value="' + esc(c.source) + '" selected>' + esc(c.source) + '</option>';
    return field('День рождения', '<input class="input" id="f-bday" value="' + esc(bdv) + '" placeholder="12.03.1990 или 12.03" inputmode="numeric">') +
      field('Пол', '<select class="select-native" id="f-gender">' + GENDERS.map(function (g) { return '<option value="' + g[0] + '"' + ((c.gender || '') === g[0] ? ' selected' : '') + '>' + g[1] + '</option>'; }).join('') + '</select>') +
      field('Откуда пришёл', '<select class="select-native" id="f-source">' + src + '</select>');
  }
  function field(label, input) { return '<div class="field"><label class="label">' + label + '</label>' + input + '</div>'; }

  function readProfile(body) {
    var g = function (id) { var e = body.querySelector('#' + id); return e ? e.value.trim() : null; };
    var out = {};
    if (body.querySelector('#f-bday')) out.birthday = g('f-bday') || '';
    if (body.querySelector('#f-gender')) out.gender = g('f-gender') || '';
    if (body.querySelector('#f-source')) out.source = g('f-source') || '';
    return out;
  }

  var SOURCES_DEFAULT = [
    ['whatsapp', 'WhatsApp'], ['instagram', 'Instagram'], ['telegram', 'Telegram'], ['webchat', 'Чат на сайте'],
    ['booking', 'Запись по ссылке'], ['call', 'Звонок'], ['recommendation', 'Рекомендация'], ['2gis', '2ГИС'],
    ['google', 'Google Карты'], ['ads', 'Реклама'], ['walk_in', 'Зашёл с улицы'], ['import', 'Импорт'],
    ['manual', 'Добавлен вручную'], ['other', 'Другое']
  ].map(function (x) { return { key: x[0], title: x[1] }; });
  function sources() { return (st.meta && st.meta.sources) || SOURCES_DEFAULT; }

  window.CrmSalon = {
    enabled: enabled, initList: initList, refresh: refresh, cardSection: cardSection,
    profileFields: function (c) { return profileFields(c, sources()); }, readProfile: readProfile
  };
})();
