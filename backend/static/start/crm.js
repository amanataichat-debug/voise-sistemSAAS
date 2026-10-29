/* ============================================================================
 * start/crm.js — CRM тарифа Старт: клиенты · воронка · звонки · задачи
 * Страница /static/start-crm.html. Данные — /api/agent/* (бот пишет туда клиентов
 * из мессенджеров и звонков). Код и вид свои, не из страницы агента.
 * ========================================================================== */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;

  var PAGE = 50;
  var st = {
    clients: { items: [], total: 0, search: '', stage: '' },
    board: null,
    calls: { items: [], offset: 0, done: false },
    tasks: null
  };
  var loaded = {};
  var root;

  document.addEventListener('DOMContentLoaded', function () {
    root = document.getElementById('page');
    S.initMobileMenu();
    S.boot().then(function (ok) {
      if (!ok) return;
      if (!S.agent) { root.innerHTML = S.noBotHtml(); S.ready(); return; }
      render();
      S.ready();
    });
  });

  // ── Каркас ──────────────────────────────────────────────────────────────
  function render() {
    root.innerHTML =
      '<div class="st-head"><div><h2>Клиенты и сделки</h2>' +
      '<p>Бот записывает сюда каждого, кто пишет или звонит: имя, телефон, переписку и итог разговора.</p></div>' +
      '<div class="st-toolbar"><button class="btn" id="import-clients" type="button">' + icon('upload') + '<span>Импорт</span></button>' +
      '<button class="btn btn-primary" id="add-client" type="button">' + icon('plus') + '<span>Добавить клиента</span></button></div></div>' +
      '<div class="card st-tabs-card">' +
      '<div class="tabs" id="tabs">' +
      tab('clients', 'users', 'Клиенты') + tab('pipeline', 'columns-2', 'Воронка') +
      tab('calls', 'phone', 'Звонки') + tab('tasks', 'calendar', 'Задачи') + '</div>' +
      '<div class="tab-pane" data-pane="clients"><div class="st-pane" id="pane-clients"></div></div>' +
      '<div class="tab-pane" data-pane="pipeline"><div class="st-pane" id="pane-pipeline"></div></div>' +
      '<div class="tab-pane" data-pane="calls"><div class="st-pane" id="pane-calls"></div></div>' +
      '<div class="tab-pane" data-pane="tasks"><div class="st-pane" id="pane-tasks"></div></div>' +
      '</div>';
    document.getElementById('add-client').addEventListener('click', addClientDialog);
    document.getElementById('import-clients').addEventListener('click', importDialog);
    S.initTabs(document.getElementById('tabs'), openPane);
    refreshCounts();
  }
  function tab(id, ic, label) {
    return '<div class="tab" data-tab="' + id + '">' + icon(ic) + '<span>' + label + '</span><span class="st-count" data-count="' + id + '" hidden></span></div>';
  }
  function setCount(id, n) {
    var el = document.querySelector('[data-count="' + id + '"]');
    if (!el) return;
    el.textContent = n; el.hidden = !n;
  }
  function refreshCounts() {
    S.api('/stats').then(function (r) {
      if (!r.ok) return;
      setCount('clients', r.data.total_contacts || 0);
      setCount('calls', r.data.total_calls || 0);
      setCount('tasks', r.data.scheduled_tasks || 0);
    });
  }

  function openPane(id) {
    if (loaded[id]) return;
    loaded[id] = true;
    if (id === 'clients') initClients();
    if (id === 'pipeline') loadBoard();
    if (id === 'calls') initCalls();
    if (id === 'tasks') loadTasks();
  }

  // ══════════════════════════════ КЛИЕНТЫ ══════════════════════════════════
  function initClients() {
    var pane = document.getElementById('pane-clients');
    pane.innerHTML =
      '<div class="st-toolbar" style="margin-bottom:14px">' +
      '<div class="st-search">' + icon('search') + '<input class="input" id="c-search" type="search" placeholder="Имя, телефон или компания"></div>' +
      '<select class="select-native" id="c-stage" style="width:auto;min-width:160px"><option value="">Все стадии</option>' +
      S.stages.map(function (s) { return '<option value="' + esc(s.key) + '">' + esc(s.label) + '</option>'; }).join('') + '</select>' +
      '<span class="muted small" id="c-total"></span></div>' +
      '<div id="c-list"></div>';
    document.getElementById('c-search').addEventListener('input', S.debounce(function (e) {
      st.clients.search = e.target.value.trim(); loadClients(true);
    }, 250));
    document.getElementById('c-stage').addEventListener('change', function (e) {
      st.clients.stage = e.target.value; loadClients(true);
    });
    loadClients(true);
  }

  function loadClients(reset) {
    var c = st.clients, list = document.getElementById('c-list');
    var seq = c.seq = (c.seq || 0) + 1;
    var q = 'limit=' + PAGE + '&offset=' + (reset ? 0 : c.items.length);
    if (c.search) q += '&search=' + encodeURIComponent(c.search);
    if (c.stage) q += '&status=' + encodeURIComponent(c.stage);
    function show(r) {
      if (seq !== c.seq) return;            // пока шёл запрос, фильтр уже сменили
      if (!r.ok) { list.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      var got = r.data.contacts || [];
      c.items = reset ? got : c.items.concat(got);
      c.total = r.data.total || 0;
      drawClients();
    }
    if (reset) {
      c.items = [];
      // Первая страница без фильтров открывается мгновенно из кэша и обновляется в фоне
      if (!c.search && !c.stage) { S.apiSWR('/contacts?' + q, show); return; }
      list.innerHTML = '<div class="skeleton" style="height:200px"></div>';
    }
    S.api('/contacts?' + q).then(show);
  }

  function drawClients() {
    var c = st.clients, list = document.getElementById('c-list');
    document.getElementById('c-total').textContent = c.total ? 'Найдено: ' + c.total : '';
    if (!c.items.length) {
      list.innerHTML = '<div class="st-empty">' + (c.search || c.stage
        ? 'Ничего не найдено. Измените запрос или фильтр.'
        : 'Клиентов пока нет. Они появятся, когда кто-то напишет боту или позвонит. Можно добавить клиента вручную.') + '</div>';
      return;
    }
    list.innerHTML = '<div class="table-wrap"><table class="table st-table"><thead><tr>' +
      '<th>Клиент</th><th>Телефон</th><th>Стадия</th><th>Каналы</th><th class="num">Сделка</th><th>Обновлён</th></tr></thead><tbody>' +
      c.items.map(function (x) {
        return '<tr data-id="' + esc(x.id) + '">' +
          '<td><div class="st-person"><span class="st-avatar">' + esc(S.initial(x.name || x.phone)) + '</span><div>' +
          '<div class="nm">' + esc(x.name || 'Без имени') + (x.ai_paused_until ? ' <span class="st-pause" title="Бот молчит в переписке">пауза</span>' : '') + '</div>' +
          '<div class="sub">' + esc([x.company, x.position].filter(Boolean).join(' · ') || ' ') + '</div></div></div></td>' +
          '<td class="mono">' + esc(S.fmtPhone(x.phone)) + '</td>' +
          '<td>' + S.stageChip(x.status) + '</td>' +
          '<td><span class="st-chs">' + S.channelDots(x.channels) + '</span></td>' +
          '<td class="num">' + esc(S.fmtMoney(x.deal_amount, x.deal_currency)) + '</td>' +
          '<td class="muted">' + esc(S.fmtDate(x.updated_at || x.created_at)) + '</td></tr>';
      }).join('') + '</tbody></table></div>' +
      (c.items.length < c.total ? '<div class="st-more"><button class="btn" id="c-more" type="button">Показать ещё</button></div>' : '');
    S.$$('tr[data-id]', list).forEach(function (tr) {
      tr.addEventListener('click', function () { openClient(tr.dataset.id); });
    });
    var more = document.getElementById('c-more');
    if (more) more.addEventListener('click', function () { more.disabled = true; loadClients(false); });
  }

  // ── Добавить клиента ────────────────────────────────────────────────────
  function addClientDialog() {
    var d = S.dialog({
      title: 'Новый клиент', size: 'sm',
      body: '<div class="field"><label class="label">Телефон *</label><input class="input" id="n-phone" placeholder="+996 555 123 456"></div>' +
        '<div class="field"><label class="label">Имя</label><input class="input" id="n-name"></div>' +
        '<div class="field"><label class="label">Компания</label><input class="input" id="n-company"></div>' +
        '<div class="field"><label class="label">Заметка</label><textarea class="textarea" id="n-notes" rows="3"></textarea></div>',
      foot: '<button class="btn" type="button" data-x2>Отмена</button><button class="btn btn-primary" type="button" id="n-save">Добавить</button>'
    });
    S.$('[data-x2]', d.el).addEventListener('click', d.close);
    S.$('#n-save', d.el).addEventListener('click', function () {
      var phone = S.$('#n-phone', d.el).value.trim();
      if (!phone) { S.toast('Укажите телефон', 'error'); return; }
      var btn = this; btn.disabled = true;
      S.api('/contacts', {
        method: 'POST',
        body: {
          phone: phone, name: S.$('#n-name', d.el).value.trim() || null,
          company: S.$('#n-company', d.el).value.trim() || null, notes: S.$('#n-notes', d.el).value.trim() || null
        }
      }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        d.close();
        S.toast('Клиент добавлен', 'success');
        reloadAll();
      });
    });
    setTimeout(function () { var i = S.$('#n-phone', d.el); if (i) i.focus(); }, 60);
  }

  // ── Импорт из Excel / CSV ───────────────────────────────────────────────
  var IMPORT_ERRORS = {
    unsupported_format: 'Нужен файл .xlsx или .csv',
    parse_failed: 'Не удалось прочитать файл. Проверьте, что он не повреждён',
    exceed_limit: 'В файле больше 1000 строк. Разделите его на части',
    preview_expired: 'Загрузка устарела, выберите файл заново'
  };
  function importError(r) {
    var d = r && r.data && (r.data.detail || r.data.message);
    return (typeof d === 'string' && IMPORT_ERRORS[d]) || S.apiError(r);
  }

  function importDialog() {
    var preview = null;
    var d = S.dialog({
      title: 'Импорт клиентов', size: 'lg',
      body: '<div id="imp-body"></div>',
      foot: '<button class="btn" type="button" data-c>Закрыть</button><button class="btn btn-primary" type="button" id="imp-go" disabled>Импортировать</button>'
    });
    var body = S.$('#imp-body', d.el), go = S.$('#imp-go', d.el);
    S.$('[data-c]', d.el).addEventListener('click', d.close);

    function pick() {
      go.disabled = true; preview = null;
      body.innerHTML =
        '<p class="muted" style="margin-top:0">Загрузите таблицу со списком клиентов: имя, телефон, компания, должность, заметка. Клиенты с номером, который уже есть в базе, будут пропущены.</p>' +
        '<label class="st-drop" id="imp-drop" for="imp-file">' + icon('upload') +
        '<b>Выберите файл или перетащите сюда</b><span class="muted small">.xlsx или .csv, до 1000 строк</span>' +
        '<input id="imp-file" type="file" accept=".xlsx,.xls,.csv" hidden></label>' +
        '<div style="margin-top:12px"><button class="btn btn-ghost btn-sm" id="imp-tpl" type="button">' + icon('download') + '<span>Скачать шаблон</span></button></div>' +
        '<p class="st-note" style="margin-top:10px">Колонки про задачи и «Когда звонить» на тарифе Старт не используются: бот сам не звонит.</p>';
      var input = S.$('#imp-file', d.el), drop = S.$('#imp-drop', d.el);
      input.addEventListener('change', function () { if (input.files[0]) upload(input.files[0]); });
      ['dragover', 'dragenter'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('over'); }); });
      ['dragleave', 'drop'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('over'); }); });
      drop.addEventListener('drop', function (e) { var f = e.dataTransfer.files[0]; if (f) upload(f); });
      S.$('#imp-tpl', d.el).addEventListener('click', function () {
        S.download('/contacts/import/template', 'clients_template.xlsx').catch(function () { S.toast('Не удалось скачать шаблон', 'error'); });
      });
    }

    function upload(file) {
      body.innerHTML = '<div class="st-empty"><div class="skeleton" style="height:60px"></div><p>Читаю файл…</p></div>';
      S.upload('/contacts/import/preview', file).then(function (r) {
        if (!r.ok) { S.toast(importError(r), 'error'); pick(); return; }
        preview = r.data; showPreview(file.name);
      });
    }

    function showPreview(name) {
      var p = preview;
      go.disabled = !p.valid_rows;
      body.innerHTML =
        '<p class="muted" style="margin-top:0">Файл: <b>' + esc(name) + '</b></p>' +
        '<div class="st-kpis" style="grid-template-columns:repeat(3,1fr);margin-bottom:14px">' +
        '<div class="card st-kpi"><div class="lbl">Будет добавлено</div><div class="val" style="color:var(--vf-success)">' + p.valid_rows + '</div></div>' +
        '<div class="card st-kpi"><div class="lbl">Уже есть в базе</div><div class="val">' + (p.duplicates || []).length + '</div></div>' +
        '<div class="card st-kpi"><div class="lbl">С ошибками</div><div class="val"' + ((p.errors || []).length ? ' style="color:var(--vf-danger)"' : '') + '>' + (p.errors || []).length + '</div></div></div>' +
        ((p.errors || []).length
          ? '<div class="st-sub-h">Строки с ошибками</div><div class="st-actions" style="max-height:150px;overflow:auto">' +
            p.errors.slice(0, 30).map(function (e) { return '<div><span class="mono">строка ' + esc(e.row) + '</span><span>' + esc(e.error) + '</span></div>'; }).join('') + '</div>' +
            '<button class="btn btn-ghost btn-sm" id="imp-err" type="button" style="margin-top:8px">' + icon('download') + '<span>Скачать ошибки</span></button>'
          : '') +
        (!p.valid_rows ? '<div class="note note-warning" style="margin-top:12px">Нет ни одной подходящей строки для импорта.</div>' : '') +
        '<div style="margin-top:12px"><button class="btn btn-ghost btn-sm" id="imp-again" type="button">Выбрать другой файл</button></div>';
      S.$('#imp-again', d.el).addEventListener('click', pick);
      var er = S.$('#imp-err', d.el);
      if (er) er.addEventListener('click', function () {
        S.download('/contacts/import/errors/' + encodeURIComponent(p.preview_token), 'import_errors.xlsx').catch(function () { S.toast('Не удалось скачать', 'error'); });
      });
    }

    go.addEventListener('click', function () {
      if (!preview) return;
      go.disabled = true; go.textContent = 'Импортирую…';
      S.api('/contacts/import/execute', { method: 'POST', body: { preview_token: preview.preview_token, create_tasks: false } }).then(function (r) {
        if (!r.ok) { go.disabled = false; go.textContent = 'Импортировать'; S.toast(importError(r), 'error'); return; }
        var total = r.data.total || preview.valid_rows;
        d.close();
        S.toast('Импорт запущен: ' + total + ' клиентов. Список обновится через несколько секунд', 'success');
        // импорт идёт в фоне: обновляем список после паузы, пропорциональной размеру
        setTimeout(reloadAll, Math.min(15000, 2500 + total * 15));
      });
    });
    pick();
  }

  function reloadAll() {
    refreshCounts();
    if (loaded.clients) loadClients(true);
    if (loaded.pipeline) loadBoard();
  }

  // ══════════════════════════════ КАРТОЧКА КЛИЕНТА ═════════════════════════
  var cur = null; // открытый клиент

  function openClient(id) {
    var dr = S.drawer({ title: '<div class="skeleton" style="height:28px;width:220px"></div>', body: '<div class="st-sec"><div class="skeleton" style="height:260px"></div></div>' });
    S.api('/contacts/' + id).then(function (r) {
      if (!r.ok) { dr.body.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      cur = { data: r.data, drawer: dr, thread: null };
      drawClient();
    });
  }

  function drawClient() {
    var c = cur.data, dr = cur.drawer;
    var title = '<div class="st-person"><span class="st-avatar lg">' + esc(S.initial(c.name || c.phone)) + '</span><div>' +
      '<div class="nm" style="font-size:16px">' + esc(c.name || 'Без имени') + '</div>' +
      '<div class="sub mono">' + esc(S.fmtPhone(c.phone)) + (c.alt_phones && c.alt_phones.length ? ' · ' + esc(c.alt_phones.map(S.fmtPhone).join(', ')) : '') + '</div></div></div>';
    dr.setTitle(title);

    var stageOpts = S.stages.map(function (s) {
      return '<option value="' + esc(s.key) + '"' + (s.key === c.status ? ' selected' : '') + '>' + esc(s.label) + '</option>';
    }).join('');
    if (!S.stageMap[c.status]) stageOpts = '<option value="' + esc(c.status) + '" selected>' + esc(c.status) + '</option>' + stageOpts;

    var mem = c.memory || {};
    var facts = (mem.key_facts || []).map(function (f) { return '<li>' + esc(f) + '</li>'; }).join('');
    var memHtml = (mem.summary || facts || mem.niche || mem.direction_need)
      ? '<div class="st-sec"><h4>Что бот знает о клиенте</h4>' +
        (mem.summary ? '<p style="margin:0 0 8px">' + esc(mem.summary) + '</p>' : '') +
        (mem.niche ? '<div class="small muted">Сфера: ' + esc(mem.niche) + '</div>' : '') +
        (mem.direction_need ? '<div class="small muted">Что нужно: ' + esc(mem.direction_need) + '</div>' : '') +
        (facts ? '<ul style="margin:8px 0 0 18px;padding:0;font-size:13.5px">' + facts + '</ul>' : '') + '</div>'
      : '';

    var paused = !!c.ai_paused_until;
    var pauseHtml = paused
      ? '<span class="st-pause">' + icon('clock') + ' Бот молчит' + (c.ai_paused_forever ? ' до включения' : ' до ' + esc(S.fmtDate(c.ai_paused_until))) + '</span>' +
        '<button class="btn btn-sm" type="button" id="cl-resume">Вернуть бота</button>'
      : '<span class="muted small">Бот отвечает клиенту</span>' +
        '<select class="select-native" id="cl-pause" style="width:auto;height:30px;font-size:12.5px"><option value="">Остановить бота…</option>' +
        '<option value="60">На 1 час</option><option value="1440">На сутки</option><option value="forever">Насовсем</option></select>';

    dr.body.innerHTML =
      '<div class="st-sec"><div class="row-between" style="gap:12px;flex-wrap:wrap">' +
      '<div><div class="label" style="margin-bottom:6px">Стадия</div><select class="select-native" id="cl-stage" style="min-width:190px">' + stageOpts + '</select></div>' +
      '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' + pauseHtml + '</div></div></div>' +
      memHtml +
      '<div class="st-sec"><h4>Данные клиента</h4>' +
      '<div class="st-grid2">' +
      field('Имя', '<input class="input" id="f-name" value="' + esc(c.name || '') + '">') +
      field('Компания', '<input class="input" id="f-company" value="' + esc(c.company || '') + '">') +
      field('Должность', '<input class="input" id="f-position" value="' + esc(c.position || '') + '">') +
      field('Сумма сделки, сом', '<input class="input" id="f-deal" type="number" min="0" step="1" value="' + esc(c.deal_amount == null ? '' : c.deal_amount) + '">') +
      field('Теги (через запятую)', '<input class="input" id="f-tags" value="' + esc((c.tags || []).join(', ')) + '">') +
      field('Следующий шаг', '<input class="input" id="f-next" value="' + esc(c.next_step_text || '') + '" placeholder="Например: выставить счёт">') +
      field('Когда', '<input class="input" id="f-next-at" type="datetime-local" value="' + esc(S.isoToLocalInput(c.next_step_at)) + '">') +
      (S.stageOf(c.status).kind === 'lost' ? field('Причина отказа', '<input class="input" id="f-lost" value="' + esc(c.lost_reason || '') + '">') : '') +
      '</div>' +
      field('Заметки', '<textarea class="textarea" id="f-notes" rows="3">' + esc(c.notes || '') + '</textarea>') +
      '<div style="display:flex;justify-content:space-between;gap:10px;margin-top:4px">' +
      '<button class="btn btn-danger btn-sm" type="button" id="cl-del">' + icon('trash-2') + '<span>Удалить клиента</span></button>' +
      '<button class="btn btn-primary" type="button" id="cl-save">Сохранить</button></div></div>' +
      '<div class="st-sec" id="sec-thread"></div>' +
      '<div class="st-sec" id="sec-calls"></div>' +
      '<div class="st-sec" id="sec-tasks"></div>';

    S.$('#cl-stage', dr.body).addEventListener('change', function () { changeStage(this.value); });
    S.$('#cl-save', dr.body).addEventListener('click', saveClient);
    S.$('#cl-del', dr.body).addEventListener('click', deleteClient);
    var rs = S.$('#cl-resume', dr.body);
    if (rs) rs.addEventListener('click', function () { setPause(null); });
    var ps = S.$('#cl-pause', dr.body);
    if (ps) ps.addEventListener('change', function () { if (this.value) setPause(this.value); });
    drawThread(); drawClientCalls(); drawClientTasks();
  }
  function field(label, input) { return '<div class="field"><label class="label">' + label + '</label>' + input + '</div>'; }

  function patchLocal(d) {
    // обновить открытого клиента и строки в списках без полной перезагрузки
    ['items'].forEach(function () {
      st.clients.items.forEach(function (x) { if (x.id === d.id) Object.keys(d).forEach(function (k) { x[k] = d[k]; }); });
    });
    if (st.board) st.board.forEach(function (x) { if (x.id === d.id) Object.keys(d).forEach(function (k) { x[k] = d[k]; }); });
    if (loaded.clients && document.getElementById('c-list')) drawClients();
    if (loaded.pipeline && st.board) drawBoard();
  }

  function changeStage(key) {
    S.api('/contacts/' + cur.data.id + '/status', { method: 'PATCH', body: { status: key } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      Object.keys(r.data).forEach(function (k) { cur.data[k] = r.data[k]; });
      patchLocal(r.data);
      S.toast('Стадия изменена', 'success');
      drawClient();
    });
  }

  function setPause(v) {
    var id = cur.data.id;
    var req = v === null
      ? S.api('/contacts/' + id + '/ai-pause', { method: 'DELETE' })
      : S.api('/contacts/' + id + '/ai-pause', { method: 'POST', body: v === 'forever' ? { forever: true } : { minutes: parseInt(v, 10) } });
    req.then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      Object.keys(r.data).forEach(function (k) { cur.data[k] = r.data[k]; });
      patchLocal(r.data);
      S.toast(v === null ? 'Бот снова отвечает клиенту' : 'Бот остановлен для этого клиента', 'success');
      drawClient();
    });
  }

  function saveClient() {
    var b = cur.drawer.body, v = function (id) { var e = S.$('#' + id, b); return e ? e.value.trim() : null; };
    var deal = v('f-deal');
    var body = {
      name: v('f-name'), company: v('f-company'), position: v('f-position'), notes: S.$('#f-notes', b).value,
      tags: (v('f-tags') || '').split(',').map(function (t) { return t.trim(); }).filter(Boolean),
      deal_amount: deal === '' ? null : Number(deal),
      next_step_text: v('f-next'), next_step_at: S.localInputToIso(v('f-next-at')) || ''
    };
    if (deal === '') delete body.deal_amount;
    var lost = v('f-lost'); if (lost !== null) body.lost_reason = lost;
    var btn = S.$('#cl-save', b); btn.disabled = true;
    S.api('/contacts/' + cur.data.id, { method: 'PUT', body: body }).then(function (r) {
      btn.disabled = false;
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      Object.keys(r.data).forEach(function (k) { cur.data[k] = r.data[k]; });
      patchLocal(r.data);
      S.toast('Сохранено', 'success');
      drawClient();
    });
  }

  function deleteClient() {
    S.confirm({ title: 'Удалить клиента?', message: 'Вместе с клиентом удалятся его звонки и задачи. Переписка в мессенджерах останется у вас в приложениях.', confirmText: 'Удалить', danger: true })
      .then(function (ok) {
        if (!ok) return;
        S.api('/contacts/' + cur.data.id, { method: 'DELETE' }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          cur.drawer.close();
          S.toast('Клиент удалён', 'success');
          reloadAll();
        });
      });
  }

  // переписка по каналам
  function drawThread() {
    var c = cur.data, sec = S.$('#sec-thread', cur.drawer.body);
    var chans = ['whatsapp', 'telegram', 'instagram'].filter(function (k) { return (c[k] || []).length; });
    if (!chans.length) {
      sec.innerHTML = '<h4>Переписка</h4><div class="muted small">Переписки с ботом пока нет.</div>';
      return;
    }
    if (!cur.thread || chans.indexOf(cur.thread) === -1) cur.thread = chans[0];
    var names = { whatsapp: 'WhatsApp', telegram: 'Telegram', instagram: 'Instagram' };
    var msgs = (c[cur.thread] || []).slice().sort(function (a, b) { return new Date(a.ts) - new Date(b.ts); });
    sec.innerHTML = '<h4>Переписка</h4><div class="st-thread-tabs">' + chans.map(function (k) {
      return '<button class="btn btn-sm ' + (k === cur.thread ? 'btn-primary' : '') + '" type="button" data-ch="' + k + '">' + names[k] + ' · ' + (c[k] || []).length + '</button>';
    }).join('') + '</div><div class="st-thread" id="thread">' + msgs.map(function (m) {
      var inbound = (m.direction || 'inbound') === 'inbound';
      var cls = inbound ? 'client' : (m.sent_by === 'owner' ? 'owner' : 'bot');
      var who = inbound ? (c.name || 'Клиент') : (m.sent_by === 'owner' ? 'Вы' : 'Бот');
      return '<div class="st-msg ' + cls + '"><span class="who">' + esc(who) + '</span>' + esc(m.body || (m.attachment_id ? 'Вложение' : '')) +
        '<span class="at">' + esc(S.fmtDate(m.ts)) + '</span></div>';
    }).join('') + '</div>';
    S.$$('[data-ch]', sec).forEach(function (b) {
      b.addEventListener('click', function () { cur.thread = b.dataset.ch; drawThread(); });
    });
    var th = S.$('#thread', sec); th.scrollTop = th.scrollHeight;
  }

  function drawClientCalls() {
    var calls = (cur.data.calls || []).filter(function (x) { return x.channel === 'call'; });
    var sec = S.$('#sec-calls', cur.drawer.body);
    if (!calls.length) { sec.innerHTML = '<h4>Звонки</h4><div class="muted small">Звонков с этим клиентом не было.</div>'; return; }
    sec.innerHTML = '<h4>Звонки · ' + calls.length + '</h4>' + calls.map(function (x, i) {
      var has = x.transcript && x.transcript !== '(Транскрипт недоступен)';
      return '<div style="padding:10px 0;border-top:1px solid var(--vf-border)">' +
        '<div class="row-between" style="gap:10px;flex-wrap:wrap"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
        dirChip(x.direction) + '<b style="font-size:13px">' + esc(S.fmtDate(x.started_at || x.created_at)) + '</b>' +
        '<span class="muted small">' + esc(S.fmtDur(x.duration_seconds)) + '</span>' + decisionChip(x.post_call_decision) + '</div>' +
        (has ? '<button class="btn btn-ghost btn-sm" type="button" data-tr="' + i + '">Транскрипт</button>' : '') + '</div>' +
        (x.record_url ? '<audio controls preload="none" src="' + esc(x.record_url) + '" style="width:100%;height:34px;margin-top:8px"></audio>' : '') +
        '<div class="st-transcript" id="tr-' + i + '" style="display:none;margin-top:8px">' + esc(x.transcript || '') + '</div></div>';
    }).join('');
    S.$$('[data-tr]', sec).forEach(function (b) {
      b.addEventListener('click', function () {
        var t = S.$('#tr-' + b.dataset.tr, sec); t.style.display = t.style.display === 'none' ? 'block' : 'none';
      });
    });
  }

  function drawClientTasks() {
    var tasks = cur.data.tasks || [], sec = S.$('#sec-tasks', cur.drawer.body);
    if (!tasks.length) { sec.style.display = 'none'; return; }
    sec.innerHTML = '<h4>Напоминания</h4>' + tasks.map(function (t) {
      return '<div class="st-task"><div class="time">' + esc(S.fmtDate(t.scheduled_time)) + '</div><div class="info"><b>' + esc(t.title || 'Задача') + '</b></div></div>';
    }).join('');
  }

  // ══════════════════════════════ ВОРОНКА ══════════════════════════════════
  function loadBoard() {
    var pane = document.getElementById('pane-pipeline');
    var shown = false;
    S.apiSWR('/contacts?limit=200&offset=0', function (r) {
      if (!r.ok) { if (!shown) pane.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      shown = true;
      st.board = r.data.contacts || [];
      st.boardTotal = r.data.total || 0;
      drawBoard();
    });
    if (!S.cacheGet('/contacts?limit=200&offset=0')) pane.innerHTML = '<div class="skeleton" style="height:320px"></div>';
  }

  function drawBoard() {
    var pane = document.getElementById('pane-pipeline');
    var firstKey = S.stages[0].key;
    var by = {};
    S.stages.forEach(function (s) { by[s.key] = []; });
    st.board.forEach(function (x) { (by[x.status] || by[firstKey]).push(x); });
    pane.innerHTML =
      (st.boardTotal > st.board.length ? '<div class="note note-warning" style="margin-bottom:12px">Показаны первые ' + st.board.length + ' клиентов из ' + st.boardTotal + '. Остальных найдёте во вкладке «Клиенты».</div>' : '') +
      '<div class="st-board">' + S.stages.map(function (s) {
        var sum = by[s.key].reduce(function (a, x) { return a + (Number(x.deal_amount) || 0); }, 0);
        return '<div class="st-col" data-stage="' + esc(s.key) + '" style="--c:' + esc(s.color) + '">' +
          '<div class="st-col-head"><b>' + esc(s.label) + '</b><span class="n">' + by[s.key].length + '</span></div>' +
          '<div class="st-col-sum">' + (sum ? esc(S.fmtMoney(sum)) : '') + '</div>' +
          '<div class="st-col-body">' + (by[s.key].length ? by[s.key].map(cardHtml).join('') : '<div class="st-board-empty">Пусто</div>') + '</div></div>';
      }).join('') + '</div>';

    S.$$('.st-card', pane).forEach(function (card) {
      card.addEventListener('click', function () { openClient(card.dataset.id); });
      card.addEventListener('dragstart', function (e) {
        e.dataTransfer.setData('text/plain', card.dataset.id); e.dataTransfer.effectAllowed = 'move';
        card.classList.add('dragging');
      });
      card.addEventListener('dragend', function () { card.classList.remove('dragging'); });
    });
    S.$$('.st-col', pane).forEach(function (col) {
      col.addEventListener('dragover', function (e) { e.preventDefault(); col.classList.add('over'); });
      col.addEventListener('dragleave', function (e) { if (!col.contains(e.relatedTarget)) col.classList.remove('over'); });
      col.addEventListener('drop', function (e) {
        e.preventDefault(); col.classList.remove('over');
        moveCard(e.dataTransfer.getData('text/plain'), col.dataset.stage);
      });
    });
  }

  function cardHtml(x) {
    return '<div class="st-card" draggable="true" data-id="' + esc(x.id) + '">' +
      '<div class="t">' + esc(x.name || S.fmtPhone(x.phone)) + '</div>' +
      '<div class="p">' + esc(x.name ? S.fmtPhone(x.phone) : (x.company || '')) + '</div>' +
      '<div class="row2"><span class="st-chs">' + S.channelDots(x.channels) + '</span>' +
      (x.deal_amount ? '<span class="deal">' + esc(S.fmtMoney(x.deal_amount, x.deal_currency)) + '</span>' : (x.ai_paused_until ? '<span class="st-pause">пауза</span>' : '')) + '</div>' +
      (x.next_step_text ? '<div class="next">' + icon('arrow-right') + '<span>' + esc(x.next_step_text) + '</span></div>' : '') + '</div>';
  }

  function moveCard(id, stage) {
    var x = st.board.filter(function (c) { return c.id === id; })[0];
    if (!x || x.status === stage) return;
    var prev = x.status;
    x.status = stage; drawBoard(); // сразу, откатим при ошибке
    S.api('/contacts/' + id + '/status', { method: 'PATCH', body: { status: stage } }).then(function (r) {
      if (!r.ok) { x.status = prev; drawBoard(); S.toast(S.apiError(r), 'error'); return; }
      st.clients.items.forEach(function (c) { if (c.id === id) c.status = stage; });
      if (loaded.clients) drawClients();
    });
  }

  // ══════════════════════════════ ЗВОНКИ ═══════════════════════════════════
  function initCalls() {
    document.getElementById('pane-calls').innerHTML = '<div id="calls-list"><div class="skeleton" style="height:220px"></div></div>';
    loadCalls();
  }

  function loadCalls() {
    var c = st.calls;
    S.api('/calls?limit=100&offset=' + c.offset).then(function (r) {
      var box = document.getElementById('calls-list');
      if (!r.ok) { box.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      var got = r.data.calls || [];
      c.offset += got.length;
      c.done = c.offset >= (r.data.total || 0) || !got.length;
      c.items = c.items.concat(got.filter(function (x) { return x.channel === 'call'; }));
      drawCalls();
    });
  }

  function drawCalls() {
    var c = st.calls, box = document.getElementById('calls-list');
    if (!c.items.length) {
      box.innerHTML = '<div class="st-empty">Звонков пока не было. Входящие на номер бота появятся здесь с записью и итогом.</div>' +
        (c.done ? '' : '<div class="st-more"><button class="btn" id="calls-more" type="button">Загрузить ещё</button></div>');
      bindCallsMore(); return;
    }
    box.innerHTML = '<div class="table-wrap"><table class="table st-table"><thead><tr>' +
      '<th>Дата</th><th>Клиент</th><th>Направление</th><th>Длительность</th><th>Итог</th><th>Решение бота</th></tr></thead><tbody>' +
      c.items.map(function (x, i) {
        return '<tr data-i="' + i + '"><td>' + esc(S.fmtDate(x.started_at || x.created_at)) + '</td>' +
          '<td><div class="st-person"><span class="st-avatar">' + esc(S.initial(x.contact_name || x.contact_phone)) + '</span><div><div class="nm">' + esc(x.contact_name || 'Без имени') + '</div><div class="sub mono">' + esc(S.fmtPhone(x.contact_phone)) + '</div></div></div></td>' +
          '<td>' + dirChip(x.direction) + '</td><td>' + esc(S.fmtDur(x.duration_seconds)) + '</td>' +
          '<td>' + outcomeChip(x) + '</td><td>' + decisionChip(x.post_call_decision) + '</td></tr>' +
          '<tr class="st-call-detail" id="cd-' + i + '" hidden><td colspan="6">' + callDetail(x) + '</td></tr>';
      }).join('') + '</tbody></table></div>' +
      (c.done ? '' : '<div class="st-more"><button class="btn" id="calls-more" type="button">Показать ещё</button></div>');
    S.$$('tr[data-i]', box).forEach(function (tr) {
      tr.addEventListener('click', function () {
        var d = document.getElementById('cd-' + tr.dataset.i); d.hidden = !d.hidden;
      });
    });
    S.$$('.st-call-detail', box).forEach(function (tr) { tr.addEventListener('click', function (e) { e.stopPropagation(); }); });
    bindCallsMore();
  }
  function bindCallsMore() {
    var m = document.getElementById('calls-more');
    if (m) m.addEventListener('click', function () { m.disabled = true; loadCalls(); });
  }

  var TOOL_LABELS = {
    update_contact_memory: 'Запомнил детали о клиенте', move_contact_stage: 'Перевёл клиента на другую стадию',
    update_contact_info: 'Обновил данные клиента', notify_owner_handoff: 'Передал клиента вам',
    send_telegram_notification: 'Отправил вам уведомление в Telegram', append_contact_note: 'Добавил заметку',
    whatsapp_send_message: 'Написал клиенту в WhatsApp', telegram_send_message: 'Написал клиенту в Telegram',
    instagram_send_message: 'Написал клиенту в Instagram', search_knowledge_base: 'Искал ответ в базе знаний'
  };

  function callDetail(x) {
    var post = x.postcall_log || {};
    var tools = (post.tool_calls || []).map(function (t) {
      return '<div>' + icon('circle-check') + '<span>' + esc(TOOL_LABELS[t.tool] || t.tool) + '</span></div>';
    }).join('');
    var tr = x.transcript && x.transcript !== '(Транскрипт недоступен)' ? x.transcript : '';
    return (x.outcome && x.outcome.detail ? '<div class="note" style="margin-bottom:12px">' + esc(x.outcome.detail) + '</div>' : '') +
      (x.record_url ? '<div class="st-sub-h">Запись звонка</div><audio controls preload="none" src="' + esc(x.record_url) + '" style="width:100%;height:36px"></audio>' : '') +
      (tr ? '<div class="st-sub-h">Разговор</div><div class="st-transcript">' + esc(tr) + '</div>' : '') +
      (tools ? '<div class="st-sub-h">Что сделал бот после звонка</div><div class="st-actions">' + tools + '</div>' : '') +
      (!tr && !tools && !x.record_url && !(x.outcome && x.outcome.detail) ? '<div class="muted small">Подробностей нет.</div>' : '');
  }

  function dirChip(d) {
    return d === 'inbound'
      ? '<span class="st-dir in">' + icon('phone-incoming') + 'Входящий</span>'
      : '<span class="st-dir out">' + icon('phone-outgoing') + 'Исходящий</span>';
  }
  function decisionChip(d) {
    var m = { SUCCESS: ['ok', 'Успех'], FOLLOWUP: ['info', 'Нужен перезвон'], NO_ANSWER: ['warn', 'Не ответил'], REJECTED: ['err', 'Отказ'] }[d];
    return m ? '<span class="st-badge ' + m[0] + '">' + m[1] + '</span>' : '<span class="muted">—</span>';
  }
  function outcomeChip(x) {
    var o = x.outcome;
    if (o) return '<span class="st-badge ' + ({ ok: 'ok', warn: 'warn', error: 'err', progress: 'info' }[o.kind] || '') + '">' + esc(o.label) + '</span>';
    return x.status === 'answered' ? '<span class="st-badge ok">Ответил</span>' : '<span class="st-badge warn">Не ответил</span>';
  }

  // ══════════════════════════════ ЗАДАЧИ ═══════════════════════════════════
  function loadTasks() {
    var pane = document.getElementById('pane-tasks');
    pane.innerHTML = '<div class="skeleton" style="height:160px"></div>';
    S.api('/tasks?status=scheduled&limit=500').then(function (r) {
      if (!r.ok) { pane.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      st.tasks = r.data.tasks || [];
      drawTasks();
    });
  }

  function drawTasks() {
    var pane = document.getElementById('pane-tasks'), tasks = st.tasks;
    if (!tasks.length) {
      pane.innerHTML = '<div class="st-empty">Задач нет. Когда клиент будет готов к разговору, бот поставит вам напоминание здесь и напишет в Telegram.</div>';
      return;
    }
    var days = {}, order = [];
    tasks.forEach(function (t) {
      var k = S.dayKey(t.scheduled_time);
      if (!days[k]) { days[k] = []; order.push(k); }
      days[k].push(t);
    });
    order.sort();
    pane.innerHTML = '<p class="muted small" style="margin:0 0 14px">Напоминания вам от бота: кому перезвонить и что сделать. Сам бот на тарифе Старт не звонит и не пишет первым.</p>' +
      order.map(function (k) {
        return '<div class="st-day"><h4>' + esc(S.fmtDay(k)) + '</h4>' + days[k].map(function (t) {
          var time = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Asia/Bishkek', hour: '2-digit', minute: '2-digit' }).format(new Date(t.scheduled_time));
          return '<div class="st-task"><div class="time">' + time + '</div><div class="info"><b>' + esc(t.title || 'Задача') + '</b>' +
            '<div>' + esc(t.contact_name || '') + (t.description ? ' · ' + esc(t.description) : '') + '</div></div>' +
            '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-del="' + esc(t.id) + '" aria-label="Удалить">' + icon('trash-2') + '</button></div>';
        }).join('') + '</div>';
      }).join('');
    S.$$('[data-del]', pane).forEach(function (b) {
      b.addEventListener('click', function () {
        S.api('/tasks/' + b.dataset.del, { method: 'DELETE' }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          st.tasks = st.tasks.filter(function (t) { return t.id !== b.dataset.del; });
          drawTasks(); refreshCounts();
        });
      });
    });
  }
})();
