/**
 * Журнал действий (Voksy Beauty, шаг 1.7): /static/activity.html.
 *
 * Кто что сделал в кабинете: записи (создана, перенесена, статус, отмена, предоплата), блоки времени, услуги и
 * цены, мастера и график, страница салона и чат, сотрудники и права, расходы и зарплаты, решения по
 * ИИ-управляющему, входы поддержки Voksy. Лента новых сверху по дням, фильтры: когда (сегодня / неделя / месяц /
 * всё), кто (люди из журнала), что (вид действия); «Показать ещё» — по курсору next.
 * Нажатие на строку — подробности: было → стало, IP (только владельцу), «Открыть запись».
 * API: GET /api/beauty/activity (api/beauty.py → services/activity.py). Видят владелец и сотрудник с разделом
 * «Журнал действий» (роль «Управляющий»); остальным сервер отвечает 403.
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var root, esc, icon;
  var PERIODS = [['today', 'Сегодня'], ['week', 'Неделя'], ['month', 'Месяц'], ['all', 'Всё']];
  var ACT_ICON = { owner: 'user', staff: 'user-round', admin: 'shield-check', ai: 'sparkles', call: 'phone', client: 'user-check', system: 'settings' };
  var OBJ_ICON = { booking: 'calendar', visit: 'calendar', block: 'ban', service: 'sparkles', category: 'layers', master: 'users',
                   room: 'door-open', settings: 'sliders-horizontal', page: 'store', team: 'user-plus', expense: 'banknote',
                   payroll: 'wallet', manager: 'bot', admin: 'shield-check' };
  var WD = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
  var MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var st = { period: 'week', actor: '', group: '', items: [], next: null, people: [], groups: [], keep: 180, busy: false, open: {} };

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* приватный режим */ } }

  function bkDay(iso) { return new Date(iso).toLocaleDateString('en-CA', { timeZone: 'Asia/Bishkek' }); }
  function dayTitle(key) {
    var today = bkDay(new Date().toISOString());
    var y = new Date(Date.now() - 864e5).toLocaleDateString('en-CA', { timeZone: 'Asia/Bishkek' });
    if (key === today) return 'Сегодня';
    if (key === y) return 'Вчера';
    var p = key.split('-'), d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
    return WD[d.getUTCDay()] + ', ' + (+p[2]) + ' ' + MON[+p[1] - 1] + (p[0] !== String(new Date().getFullYear()) ? ' ' + p[0] : '');
  }

  function query(more) {
    var q = new URLSearchParams({ period: st.period, limit: '50' });
    if (st.actor) q.set('actor', st.actor);
    if (st.group) q.set('group', st.group);
    if (more && st.next) q.set('before', st.next);
    return '/api/beauty/activity?' + q.toString();
  }
  function load(more) {
    if (st.busy) return;
    st.busy = true;
    if (!more) { st.items = []; st.next = null; st.open = {}; renderList(true); }
    S.api(query(more)).then(function (r) {
      st.busy = false;
      if (!r.ok) {
        root.innerHTML = '<div class="st-empty">' + esc(r.status === 403 ? 'Журнал действий открыт владельцу и управляющему. Доступ выдаёт владелец в разделе «Сотрудники».' : S.apiError(r)) + '</div>';
        S.ready();
        return;
      }
      var d = r.data;
      if (d.people) st.people = d.people;
      if (d.groups) st.groups = d.groups;
      if (d.keep_days) st.keep = d.keep_days;
      st.items = st.items.concat(d.items || []);
      st.next = d.next;
      if (!more) renderFilters();
      renderList(false);
      S.ready();
    });
  }
  function writeUrl() {
    var q = new URLSearchParams();
    if (st.period !== 'week') q.set('period', st.period);
    if (st.actor) q.set('actor', st.actor);
    if (st.group) q.set('group', st.group);
    try { history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q.toString() : '')); } catch (e) { /* ignore */ }
  }

  function renderShell() {
    root.innerHTML = '<div class="ac-filters" id="ac-filters"></div><div class="ac-list" id="ac-list"></div>';
    renderFilters();
  }
  function renderFilters() {
    var box = document.getElementById('ac-filters');
    if (!box) return;
    var people = [{ key: '', name: 'Все' }].concat(st.people);
    if (st.actor && !people.some(function (p) { return p.key === st.actor; })) people.push({ key: st.actor, name: 'Выбранный' });
    var groups = [{ key: '', title: 'Все виды' }].concat(st.groups);
    box.innerHTML = '<div class="ac-periods" role="tablist">' + PERIODS.map(function (p) {
      return '<button type="button" role="tab" class="ac-chip' + (p[0] === st.period ? ' on' : '') + '" data-period="' + p[0] + '">' + p[1] + '</button>';
    }).join('') + '</div>' +
      '<div class="ac-selects">' +
      '<label class="ac-sel">' + icon('users') + '<select id="ac-actor" aria-label="Кто">' + people.map(function (p) {
        return '<option value="' + esc(p.key) + '"' + (p.key === st.actor ? ' selected' : '') + '>' + esc(p.key ? p.name : 'Все люди') + '</option>';
      }).join('') + '</select></label>' +
      '<label class="ac-sel">' + icon('filter') + '<select id="ac-group" aria-label="Что">' + groups.map(function (g) {
        return '<option value="' + esc(g.key) + '"' + (g.key === st.group ? ' selected' : '') + '>' + esc(g.title) + '</option>';
      }).join('') + '</select></label></div>';
  }

  function changesHtml(x) {
    var ch = x.changes;
    if (!ch || typeof ch !== 'object') return '';
    var keys = Object.keys(ch).filter(function (k) { return Array.isArray(ch[k]); });
    if (!keys.length) return '';
    function v(val) {
      if (val === null || val === undefined || val === '') return '—';
      if (val === true) return 'да';
      if (val === false) return 'нет';
      if (typeof val === 'string' && /^\d{4}-\d\d-\d\dT/.test(val)) {
        var d = new Date(val);
        return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', timeZone: 'Asia/Bishkek' }) + ' ' +
          d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bishkek' });
      }
      if (typeof val === 'object') return Object.keys(val).map(function (k) { return k + (val[k] != null ? ' ' + val[k] : ''); }).join(', ') || '—';
      return String(val);
    }
    var LBL = { start: 'Время', resource_id: 'Мастер', duration_min: 'Длительность, мин', status: 'Статус', note: 'Заметка', price: 'Цена',
                prepaid: 'Предоплата получена', service: 'Услуга' };
    return '<table class="ac-ch"><tbody>' + keys.filter(function (k) { return k !== 'resource_id'; }).map(function (k) {
      return '<tr><th>' + esc(LBL[k] || k) + '</th><td><s>' + esc(v(ch[k][0])) + '</s></td><td>' + icon('arrow-right') + '</td><td><b>' + esc(v(ch[k][1])) + '</b></td></tr>';
    }).join('') + '</tbody></table>';
  }
  function rowHtml(x) {
    var a = x.actor || {};
    var open = !!st.open[x.id];
    var day = (x.meta && x.meta.date) || bkDay(x.at);     // день самой записи, а не день действия
    var vb = x.object_type === 'visit' && x.meta && (x.meta.booking_ids || [])[0];
    var link = (x.object_type === 'booking' && x.object_id) ? '/static/booking.html?open=' + encodeURIComponent(x.object_id) + '&date=' + day
      : vb ? '/static/booking.html?open=' + encodeURIComponent(vb) + '&date=' + day
      : x.object_type === 'block' ? '/static/booking.html?date=' + day
      : x.object_type === 'master' || x.object_type === 'service' ? '/static/booking.html'
        : x.object_type === 'team' ? '/static/team.html'
          : x.object_type === 'expense' || x.object_type === 'payroll' ? '/static/finance.html'
            : x.object_type === 'manager' ? '/static/manager.html#journal'
              : x.object_type === 'page' ? '/static/booking.html?view=page' : '';
    var full = new Date(x.at);
    return '<div class="ac-row' + (open ? ' open' : '') + '" data-id="' + esc(x.id) + '" role="button" tabindex="0" aria-expanded="' + open + '">' +
      '<span class="ac-ava k-' + esc(a.kind) + '" title="' + esc(a.title) + '">' + icon(ACT_ICON[a.kind] || 'user') + '</span>' +
      '<div class="ac-main"><div class="ac-head"><b>' + esc(a.name) + '</b><span class="ac-what">' + icon(OBJ_ICON[x.object_type] || 'activity') +
      esc(x.action_title) + '</span></div><div class="ac-sum">' + esc(x.summary) + '</div>' +
      (open ? '<div class="ac-more">' + changesHtml(x) +
        '<div class="ac-meta">' + esc(full.toLocaleString('ru-RU', { timeZone: 'Asia/Bishkek', day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })) +
        (a.kind !== 'owner' && a.kind !== 'staff' ? ' · ' + esc(a.title) : '') + (x.ip ? ' · IP ' + esc(x.ip) : '') + '</div>' +
        (link ? '<a class="ac-open" href="' + esc(link) + '">' + (x.object_type === 'booking' || vb ? 'Открыть запись' : 'Открыть раздел') + icon('chevron-right') + '</a>' : '') +
        '</div>' : '') + '</div>' +
      '<span class="ac-time">' + esc(x.time) + '</span></div>';
  }
  function renderList(loading) {
    var box = document.getElementById('ac-list');
    if (!box) return;
    if (loading && !st.items.length) {
      box.innerHTML = '<div class="ac-card"><div class="skeleton" style="height:56px;border-radius:12px;margin-bottom:8px"></div>' +
        '<div class="skeleton" style="height:56px;border-radius:12px;margin-bottom:8px"></div><div class="skeleton" style="height:56px;border-radius:12px"></div></div>';
      return;
    }
    if (!st.items.length) {
      box.innerHTML = '<div class="ac-card ac-empty">' + icon('history') + '<b>' + (st.actor || st.group || st.period !== 'all' ? 'За этот период и с этими фильтрами — ничего' : 'Пока пусто') + '</b>' +
        '<p>Здесь видно, кто и когда записал клиента, перенёс или отменил запись, отметил «пришёл», поменял цену, график или страницу салона, ' +
        'что сделал бот и ИИ-управляющий. Журнал хранится ' + st.keep + ' дней.</p>' +
        (st.period !== 'all' ? '<button type="button" class="btn btn-sm" data-period="all">Показать всё</button>' : '') + '</div>';
      return;
    }
    var html = '', day = null;
    st.items.forEach(function (x) {
      var k = bkDay(x.at);
      if (k !== day) {
        if (day !== null) html += '</div>';
        day = k;
        html += '<div class="ac-day">' + esc(dayTitle(k)) + '</div><div class="ac-card">';
      }
      html += rowHtml(x);
    });
    html += '</div>';
    if (st.next) html += '<button type="button" class="btn ac-moreb" data-more>' + (st.busy ? 'Загружаем…' : 'Показать ещё') + '</button>';
    else html += '<p class="ac-foot">Журнал хранится ' + st.keep + ' дней — старые действия удаляются сами.</p>';
    box.innerHTML = html;
  }

  function bind() {
    root.addEventListener('click', function (e) {
      var p = e.target.closest('[data-period]');
      if (p) { st.period = p.getAttribute('data-period'); lsSet('vf_activity_period', st.period); writeUrl(); renderFilters(); load(false); return; }
      if (e.target.closest('[data-more]')) { load(true); return; }
      if (e.target.closest('a')) return;
      var row = e.target.closest('.ac-row');
      if (row) {
        var id = row.getAttribute('data-id');
        st.open[id] = !st.open[id];
        var x = st.items.filter(function (i) { return i.id === id; })[0];
        if (x) row.outerHTML = rowHtml(x);
      }
    });
    root.addEventListener('keydown', function (e) {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.classList && e.target.classList.contains('ac-row')) { e.preventDefault(); e.target.click(); }
    });
    root.addEventListener('change', function (e) {
      if (e.target.id === 'ac-actor') { st.actor = e.target.value; writeUrl(); load(false); }
      if (e.target.id === 'ac-group') { st.group = e.target.value; writeUrl(); load(false); }
    });
  }

  function start() {
    root = document.getElementById('ac');
    esc = S.esc; icon = S.icon;
    if (S.initMobileMenu) S.initMobileMenu();
    var q = new URLSearchParams(location.search);
    var saved = lsGet('vf_activity_period');
    st.period = q.get('period') || saved || 'week';
    if (!PERIODS.some(function (p) { return p[0] === st.period; })) st.period = 'week';
    st.actor = q.get('actor') || '';
    st.group = q.get('group') || '';
    renderShell();
    bind();
    load(false);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
