/**
 * VoksiAI — страница «Голосовые ассистенты» (/static/voice-assistants.html).
 *
 * В кабинете остались только ассистенты ElevenLabs (диалог ведёт OpenAI на
 * серверном ключе, озвучивает ElevenLabs v3). Витрина карточек → редактор с
 * вкладками Настройки · Функции · База знаний · Тестирование · Встраивание.
 *
 * API:
 *   /api/eleven-assistants            список ({assistants}), создание, /{id} — чтение, правка, удаление (204)
 *   /api/eleven-assistants/options    модели синтеза, языки, стабильность, диалоговые модели
 *   /api/eleven-assistants/status     настроены ли серверные ключи
 *   /api/eleven-assistants/voices     голоса аккаунта (рекомендованные для языка первыми)
 *   /api/eleven-assistants/voices/library(+/add)  публичная библиотека голосов
 *   /api/knowledge-base/assistant/eleven/{id}     индивидуальная база знаний ассистента
 *   /api/functions/                   каталог функций
 *   /api/subscriptions/assistants-usage  лимит ассистентов по тарифу
 *
 * Строки — раздел va словарей /static/i18n/{ru,ky}.json (RU по умолчанию ниже).
 */
(function () {
  'use strict';

  var STR = {
    page_title: 'Голосовые ассистенты', create: 'Создать', my_assistants: 'Мои ассистенты',
    usage_unlimited: 'Без лимита', usage_tip: 'Лимит ассистентов по тарифу',
    back_tip: 'Все ассистенты', more_tip: 'Ещё', active: 'Активен', inactive_tip: 'Выключен',
    new_assistant: 'Новый ассистент', no_description: 'Без описания',
    empty_title: 'Пока нет ассистентов', empty_text: 'Создайте первого: задайте промпт, выберите голос и подключите функции.',
    empty_btn: 'Создать ассистента', load_failed: 'Не удалось загрузить ассистентов: {reason}',
    tab_settings: 'Настройки', tab_functions: 'Функции', tab_knowledge: 'База знаний', tab_test: 'Тестирование', tab_embed: 'Встраивание',
    block_main: 'Основное', f_name: 'Имя ассистента', f_name_ph: 'Менеджер по записи', f_desc: 'Описание', f_desc_ph: 'Кратко, зачем нужен ассистент',
    f_greeting: 'Первая фраза', f_greeting_ph: 'Здравствуйте! Чем могу помочь?', f_prompt: 'Системный промпт',
    f_prompt_ph: 'Кто ассистент, как он говорит, что делает и чего не делает', chars: '{n} символов',
    default_greeting: 'Здравствуйте! Чем я могу вам помочь?',
    block_voice: 'Голос',
    server_not_ready: 'На сервере не настроены ключи ElevenLabs: ассистент пока не сможет отвечать. Сообщите администратору.',
    llm_model: 'Диалоговая модель', language: 'Язык',
    eleven_tts: 'Модель синтеза', eleven_voice: 'Голос', eleven_voice_empty: 'Выберите голос', eleven_stability: 'Стабильность голоса',
    eleven_group_rec: 'Рекомендованы для языка', eleven_group_other: 'Остальные голоса', eleven_missing: '(нет в аккаунте)',
    eleven_voices_failed: 'Не удалось загрузить голоса ElevenLabs', eleven_reload: 'Обновить список', eleven_library: 'Найти в библиотеке',
    eleven_voice_required: 'Выберите голос — без него ассистент не сможет говорить',
    lib_search_ph: 'Имя или стиль голоса', lib_any_gender: 'Любой пол', lib_female: 'Женский', lib_male: 'Мужской', lib_search: 'Найти',
    lib_more: 'Показать ещё', lib_add: 'Добавить', lib_select: 'Выбрать', lib_added: 'Голос «{name}» добавлен и выбран', lib_empty: 'Ничего не найдено',
    funcs_unavailable: 'Каталог функций недоступен', funcs_unavailable_text: 'Включённые функции сохранятся без изменений.',
    kb_create: 'Создать базу знаний', kb_empty_title: 'У ассистента пока нет базы знаний',
    kb_empty_text: 'Добавьте текст: описание услуг, цены, адреса, ответы на частые вопросы.',
    kb_new_title: 'База знаний ассистента', kb_edit_title: 'Редактировать базу знаний',
    kb_edit: 'Редактировать', kb_delete: 'Удалить', kb_updated: 'Обновлена {date}', kb_chars: '{n} символов',
    kb_saved: 'База знаний сохранена, поиск по ней включён', kb_deleted: 'База знаний удалена', kb_need_content: 'Добавьте содержимое базы',
    kb_delete_title: 'Удалить базу знаний?', kb_delete_msg: 'База «{name}» будет удалена, ассистент перестанет находить в ней ответы.',
    kb_load_failed: 'Не удалось загрузить базу знаний', kb_saving: 'Строим базу… Это может занять до минуты.',
    kb_search_off: 'Функция «Поиск по базе знаний» выключена — ассистент не будет искать в базе.', kb_search_on: 'Включить поиск',
    save_first: 'Сначала сохраните ассистента', save_first_text: 'База знаний, тестирование и код для сайта появятся после сохранения.',
    widget_title: 'Виджет запущен в правом нижнем углу', widget_text: 'Нажмите на кнопку виджета и говорите.',
    widget_loading: 'Загружаем виджет…', widget_loaded: 'Виджет загружен', widget_failed: 'Не удалось загрузить виджет',
    widget_inactive: 'Ассистент выключен. Включите его, чтобы протестировать.',
    phone_test: 'Позвонить ассистенту можно, привязав его к номеру в Телефонии.', open_telephony: 'Открыть Телефонию',
    embed_text: 'Вставьте код перед закрывающим тегом </body> на вашем сайте.', embed_copy: 'Копировать', embed_copied: 'Код скопирован',
    embed_inactive: 'Ассистент выключен — включите его, чтобы получить код.',
    assistant_id: 'ID ассистента', id_copied: 'ID скопирован',
    saved: 'Сохранено', dirty: 'Есть несохранённые изменения', save: 'Сохранить', saving: 'Сохраняем…',
    created: 'Ассистент создан', updated: 'Сохранено', deleted: 'Ассистент удалён',
    need_name: 'Укажите имя ассистента',
    unsaved_title: 'Несохранённые изменения', unsaved_msg: 'Продолжить без сохранения? Изменения будут потеряны.', unsaved_ok: 'Продолжить',
    menu_telephony: 'Открыть телефонию', menu_copy_prompt: 'Скопировать промпт', menu_copy_id: 'Скопировать ID', menu_delete: 'Удалить ассистента',
    prompt_copied: 'Промпт скопирован', copy_failed: 'Не удалось скопировать',
    delete_title: 'Удалить ассистента?', delete_msg: '«{name}» будет удалён вместе с базой знаний. Номера, привязанные к нему, останутся без ассистента.', delete_ok: 'Удалить',
    limit_reached: 'Достигнут лимит ассистентов по тарифу ({used} из {max}). Удалите лишних или обновите тариф.',
    request_error: 'Ошибка запроса'
  };
  function format(s, p) { return p ? String(s).replace(/\{(\w+)\}/g, function (m, k) { return p[k] != null ? p[k] : m; }) : s; }
  function t(key, params) { return window.I18N ? window.I18N.t('va.' + key, params, STR[key]) : format(STR[key] || key, params); }
  var esc = VF.esc;
  var BASE = '/api/eleven-assistants';
  var KY_GREETING = 'Саламатсызбы! Мен сизге кантип жардам бере алам?';
  var SEARCH_FN = 'search_pinecone';

  // ------------------------------------------------------------------
  // API
  // ------------------------------------------------------------------
  function token() { try { return localStorage.getItem('auth_token'); } catch (e) { return null; } }
  function errText(data, status) {
    var d = data && data.detail;
    if (typeof d === 'string') return d;
    if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
    if (Array.isArray(d) && d.length && d[0].msg) return d[0].msg;
    return t('request_error') + (status ? ' (' + status + ')' : '');
  }
  function api(path, opts) {
    opts = opts || {};
    var headers = { 'Authorization': 'Bearer ' + token() };
    var body = opts.body;
    if (body !== undefined && typeof body !== 'string') { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
    return fetch(path, { method: opts.method || 'GET', headers: headers, body: body }).then(function (r) {
      if (r.status === 401) { location.href = '/static/login.html?reason=session_expired'; throw new Error('unauthorized'); }
      if (r.status === 204) return null;
      return r.text().then(function (txt) {
        var data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
        if (!r.ok) { var err = new Error(errText(data, r.status)); err.status = r.status; throw err; }
        return data;
      });
    });
  }
  if (!token()) { location.href = '/static/login.html'; return; }
  function itemUrl(id) { return BASE + '/' + encodeURIComponent(id); }
  function kbUrl(id) { return '/api/knowledge-base/assistant/eleven/' + encodeURIComponent(id); }

  // ------------------------------------------------------------------
  // Состояние
  // ------------------------------------------------------------------
  var $ = function (id) { return document.getElementById(id); };
  var state = {
    items: [], user: null, usage: null, catalog: null, options: {}, status: null,
    current: null,       // сохранённый ассистент (данные API) или null (новый)
    form: null, snapshot: '', tab: 'settings',
    voices: null,        // {language, voices}
    kb: null             // база текущего ассистента
  };
  var editorOpen = false;

  function isPrivileged() { var u = state.user; return !!(u && (u.is_admin || u.email === 'amanat.aichat@gmail.com')); }
  function canCreate() { return isPrivileged() || !state.usage || state.usage.unlimited || state.usage.can_create !== false; }
  function langTitle(code) {
    var l = (state.options.languages || []).filter(function (x) { return x.code === code; })[0];
    return l ? l.title : (code || '');
  }

  // ------------------------------------------------------------------
  // Загрузка
  // ------------------------------------------------------------------
  function loadList() {
    return api(BASE + '?include_agent_voices=false').then(function (d) {
      state.items = (d && d.assistants) || [];
      if (d && d.server_ready === false) state.status = { ready: false };
      state.items.sort(function (a, b) { return String(b.created_at || '').localeCompare(String(a.created_at || '')); });
      renderList();
    }).catch(function (e) {
      if (e.message !== 'unauthorized') VF.toast(t('load_failed', { reason: e.message }), { type: 'error' });
      renderList();
    });
  }
  function loadUsage() { return api('/api/subscriptions/assistants-usage').then(function (u) { state.usage = u; renderUsage(); }, function () { state.usage = null; renderUsage(); }); }
  function loadUser() { return api('/api/users/me').then(function (u) { state.user = u; }, function () {}); }
  function loadCatalog() { return api('/api/functions/').then(function (d) { state.catalog = Array.isArray(d) ? d : null; }, function () { state.catalog = null; }); }
  function loadOptions() {
    return Promise.all([
      api(BASE + '/options').then(function (o) { state.options = o || {}; }, function () { state.options = {}; }),
      api(BASE + '/status').then(function (s) { state.status = s || null; }, function () {})
    ]);
  }

  // ------------------------------------------------------------------
  // Список
  // ------------------------------------------------------------------
  function logo(size) {
    size = size || 20;
    return '<span class="logo-wrap" style="width:' + (size + 14) + 'px;height:' + (size + 14) + 'px">' + VF.icon('audio-lines') + '</span>';
  }
  function fmtDate(v) {
    if (!v) return '';
    var d = new Date(v);
    return isNaN(d) ? '' : d.toLocaleDateString('ru-RU', { timeZone: 'Asia/Bishkek' });
  }
  function renderList() {
    var body = $('list-body');
    $('list-count').textContent = state.items.length ? String(state.items.length) : '';
    if (!state.items.length) {
      body.innerHTML = '<div class="empty"><div class="empty-title">' + esc(t('empty_title')) + '</div><div class="empty-text">' + esc(t('empty_text')) +
        '</div><button class="btn btn-primary" id="btn-new-empty" type="button">' + VF.icon('plus') + esc(t('empty_btn')) + '</button></div>';
      $('btn-new-empty').addEventListener('click', requestNew);
      return;
    }
    body.innerHTML = state.items.map(function (a) {
      var cur = state.current && state.current.id === a.id;
      var desc = a.description || (a.system_prompt ? String(a.system_prompt).slice(0, 160) : t('no_description'));
      return '<div class="a-card' + (cur ? ' active' : '') + '" data-id="' + esc(a.id) + '" tabindex="0">' +
        '<div class="a-top">' + logo(20) +
        '<div class="a-main"><div class="a-name"><span class="truncate">' + esc(a.name || '—') + '</span>' +
        (a.is_active === false ? '<span class="dot" data-tip="' + esc(t('inactive_tip')) + '"></span>' : '') + '</div>' +
        '<div class="faint small truncate">' + esc(a.voice_name || 'ElevenLabs') + '</div></div></div>' +
        '<div class="a-desc">' + esc(desc) + '</div>' +
        '<div class="a-meta"><span class="chip chip-accent">' + esc(langTitle(a.language)) + '</span><span class="faint" style="margin-left:auto">' + esc(fmtDate(a.created_at)) + '</span></div></div>';
    }).join('');
    Array.prototype.forEach.call(body.querySelectorAll('.a-card'), function (el) {
      var open = function () { requestOpen(el.getAttribute('data-id')); };
      el.addEventListener('click', open);
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter') open(); });
    });
  }
  function renderUsage() {
    var pill = $('usage-pill'), u = state.usage;
    if (!u) { pill.classList.add('hidden'); return; }
    pill.textContent = u.unlimited ? t('usage_unlimited') : (u.used + ' / ' + u.max);
    pill.classList.remove('hidden');
  }

  // ------------------------------------------------------------------
  // Форма
  // ------------------------------------------------------------------
  function fnNames(raw) {
    if (!raw) return [];
    if (Array.isArray(raw)) return raw.map(function (f) { return typeof f === 'string' ? f : f && f.name; }).filter(Boolean);
    if (raw.enabled_functions && Array.isArray(raw.enabled_functions)) return raw.enabled_functions.slice();
    return [];
  }
  function formFrom(a) {
    var o = state.options;
    return {
      name: a ? a.name || '' : '',
      description: a ? a.description || '' : '',
      greeting: a ? a.greeting_message || '' : (o.default_greeting || KY_GREETING),
      prompt: a ? a.system_prompt || '' : '',
      functions: fnNames(a && a.functions),
      rawFunctions: a ? a.functions : null,
      sheet: a ? a.google_sheet_id || '' : '',
      active: a ? a.is_active !== false : true,
      language: (a && a.language) || o.default_language || 'ky',
      tts_model: (a && a.tts_model) || o.default_tts_model || 'eleven_v3_conversational',
      voice_id: (a && a.voice_id) || '',
      voice_name: (a && a.voice_name) || '',
      stability: a && a.stability != null ? a.stability : (o.default_stability != null ? o.default_stability : 0.5),
      llm_model: (a && a.llm_model) || o.default_llm_model || 'gpt-realtime-2'
    };
  }
  function snapshotOf() {
    var f = state.form;
    return JSON.stringify([f.name, f.description, f.greeting, f.prompt, f.functions.slice().sort(), f.sheet, f.active, f.language, f.tts_model, f.voice_id, f.stability, f.llm_model]);
  }
  function isDirty() { return editorOpen && state.form && snapshotOf() !== state.snapshot; }
  function markClean() { state.snapshot = snapshotOf(); renderDirty(); }
  function renderDirty() {
    var el = $('dirty-state'), d = isDirty() || (editorOpen && !state.current);
    el.className = 'state' + (d ? ' dirty' : '');
    el.innerHTML = d ? VF.icon('circle-alert') + esc(t('dirty')) : VF.icon('circle-check') + esc(t('saved'));
  }

  // ------------------------------------------------------------------
  // Редактор: открыть / закрыть
  // ------------------------------------------------------------------
  function confirmLeave() {
    if (!isDirty()) return Promise.resolve(true);
    return VF.confirm({ title: t('unsaved_title'), message: t('unsaved_msg'), confirmText: t('unsaved_ok'), warning: true, icon: 'circle-alert' });
  }
  function requestOpen(id) {
    if (editorOpen && state.current && state.current.id === id) return;
    confirmLeave().then(function (ok) { if (ok) openAssistant(id); });
  }
  function requestNew() {
    confirmLeave().then(function (ok) {
      if (!ok) return;
      if (!canCreate()) { VF.toast(t('limit_reached', { used: state.usage.used, max: state.usage.max }), { type: 'warning', duration: 7000 }); return; }
      openNew();
    });
  }
  function openAssistant(id, tab) {
    var it = state.items.filter(function (x) { return String(x.id) === String(id); })[0];
    return (it ? Promise.resolve(it) : api(itemUrl(id))).then(function (a) {
      state.current = a;
      state.form = formFrom(a);
      showEditor(tab || 'settings');
    }).catch(function (e) { VF.toast(e.message, { type: 'error' }); closeEditor(true); });
  }
  function openNew() {
    state.current = null;
    state.form = formFrom(null);
    showEditor('settings');
  }
  function showEditor(tab) {
    editorOpen = true;
    state.kb = null;
    teardownWidget();
    $('layout').classList.remove('grid-mode');
    fillForm();
    markClean();
    switchTab(tab);
    renderList();
    window.scrollTo({ top: 0 });
  }
  function closeEditor(force) {
    var go = function () {
      editorOpen = false; state.current = null; state.form = null;
      teardownWidget();
      $('layout').classList.add('grid-mode');
      renderList(); syncUrl();
    };
    if (force) return go();
    confirmLeave().then(function (ok) { if (ok) go(); });
  }
  function syncUrl() {
    var q = new URLSearchParams();
    if (editorOpen) {
      if (state.current) q.set('id', state.current.id); else q.set('new', '1');
      if (state.tab !== 'settings') q.set('tab', state.tab);
    }
    var lang = new URLSearchParams(location.search).get('lang');
    if (lang) q.set('lang', lang);
    var s = q.toString();
    history.replaceState(null, '', location.pathname + (s ? '?' + s : ''));
  }

  function fillForm() {
    var f = state.form, isNew = !state.current;
    $('editor-title').textContent = isNew ? t('new_assistant') : (f.name || '—');
    $('editor-logo').innerHTML = logo(18);
    $('active-wrap').classList.toggle('hidden', isNew);
    $('btn-more').classList.toggle('hidden', isNew);
    $('f-active').checked = f.active;
    $('f-name').value = f.name;
    $('f-desc').value = f.description;
    $('f-greeting').value = f.greeting;
    $('f-greeting').maxLength = 500;
    $('f-prompt').value = f.prompt;
    $('f-sheet').value = f.sheet;
    updatePromptCount();
    renderServerNote();
    renderVoiceSettings();
    renderFunctions();
    renderSaveButton(false);
  }
  function updatePromptCount() { $('prompt-count').textContent = t('chars', { n: $('f-prompt').value.length.toLocaleString('ru-RU') }); }
  function renderSaveButton(busy) {
    var b = $('btn-save');
    b.disabled = !!busy;
    b.innerHTML = busy ? '<span class="spin"></span> ' + esc(t('saving')) : VF.icon('check') + esc(t('save'));
  }
  function renderServerNote() {
    $('model-extra').innerHTML = state.status && state.status.ready === false
      ? '<div class="note note-warning" style="margin-bottom:14px">' + VF.icon('triangle-alert') + '<span>' + esc(t('server_not_ready')) + '</span></div>' : '';
  }

  // ------------------------------------------------------------------
  // Голос
  // ------------------------------------------------------------------
  function selectOptions(list, value) {
    return list.map(function (o) { return '<option value="' + esc(o.value) + '"' + (String(o.value) === String(value) ? ' selected' : '') + '>' + esc(o.label) + '</option>'; }).join('');
  }
  function bindVal(id, fn) {
    var el = $(id);
    var h = function () { fn(el.value); renderDirty(); };
    el.addEventListener('input', h); el.addEventListener('change', h);
  }
  function renderVoiceSettings() {
    var box = $('voice-settings'), d = state.form, o = state.options;
    var tts = (o.tts_models || []).map(function (x) { return { value: x.id, label: x.title + (x.price ? ' — ' + x.price : '') }; });
    if (tts.every(function (x) { return x.value !== d.tts_model; })) tts.unshift({ value: d.tts_model, label: d.tts_model });
    var langs = (o.languages || [{ code: 'ky', title: 'Кыргызский' }, { code: 'ru', title: 'Русский' }]).map(function (x) { return { value: x.code, label: x.title + ' (' + x.code + ')' }; });
    var stab = (o.stability_levels || [{ value: 0, title: 'Creative' }, { value: 0.5, title: 'Natural' }, { value: 1, title: 'Robust' }]).map(function (x) { return { value: x.value, label: x.title }; });
    if (stab.every(function (x) { return Number(x.value) !== Number(d.stability); })) stab.push({ value: d.stability, label: String(d.stability) });
    var llms = (o.llm_models || ['gpt-realtime-2', 'gpt-realtime-2.1-mini']).map(function (x) { return { value: x, label: x }; });
    if (llms.every(function (x) { return x.value !== d.llm_model; })) llms.unshift({ value: d.llm_model, label: d.llm_model });
    var ttsInfo = (o.tts_models || []).filter(function (x) { return x.id === d.tts_model; })[0];
    box.innerHTML =
      '<div class="form-row"><div class="field"><label class="label" for="ex-lang">' + esc(t('language')) + '</label><select class="form-control" id="ex-lang">' + selectOptions(langs, d.language) + '</select></div>' +
      '<div class="field"><label class="label" for="ex-tts">' + esc(t('eleven_tts')) + '</label><select class="form-control" id="ex-tts">' + selectOptions(tts, d.tts_model) + '</select>' +
      '<span class="hint" id="ex-tts-hint">' + esc(ttsInfo && ttsInfo.description || '') + '</span></div></div>' +
      '<div class="field"><div class="row-between" style="flex-wrap:wrap"><label class="label" for="ex-voice">' + esc(t('eleven_voice')) + '</label>' +
      '<div class="row"><button class="btn btn-ghost btn-sm" type="button" id="ex-reload">' + VF.icon('refresh-cw', 'ic-sm') + esc(t('eleven_reload')) + '</button>' +
      '<button class="btn btn-ghost btn-sm" type="button" id="ex-lib-toggle">' + VF.icon('search', 'ic-sm') + esc(t('eleven_library')) + '</button></div></div>' +
      '<div id="ex-voice-wrap"><div class="skeleton" style="height:38px"></div></div><div id="ex-preview"></div>' +
      '<div class="lib hidden" id="ex-lib"><div class="row" style="flex-wrap:wrap"><input class="input grow" id="lib-q" placeholder="' + esc(t('lib_search_ph')) + '">' +
      '<select class="form-control" id="lib-gender" style="max-width:170px"><option value="">' + esc(t('lib_any_gender')) + '</option><option value="female">' + esc(t('lib_female')) + '</option><option value="male">' + esc(t('lib_male')) + '</option></select>' +
      '<button class="btn" type="button" id="lib-go">' + esc(t('lib_search')) + '</button></div><div class="lib-list" id="lib-list"></div>' +
      '<button class="btn btn-sm hidden" type="button" id="lib-more" style="margin-top:8px">' + esc(t('lib_more')) + '</button></div></div>' +
      '<div class="form-row"><div class="field"><label class="label" for="ex-stab">' + esc(t('eleven_stability')) + '</label><select class="form-control" id="ex-stab">' + selectOptions(stab, d.stability) + '</select></div>' +
      '<div class="field"><label class="label" for="ex-llm">' + esc(t('llm_model')) + '</label><select class="form-control" id="ex-llm">' + selectOptions(llms, d.llm_model) + '</select></div></div>';
    bindVal('ex-lang', function (v) { d.language = v; loadVoices(true); });
    bindVal('ex-tts', function (v) {
      d.tts_model = v;
      var info = (o.tts_models || []).filter(function (x) { return x.id === v; })[0];
      $('ex-tts-hint').textContent = info && info.description || '';
    });
    bindVal('ex-stab', function (v) { d.stability = parseFloat(v); });
    bindVal('ex-llm', function (v) { d.llm_model = v; });
    $('ex-reload').addEventListener('click', function () { loadVoices(true); });
    $('ex-lib-toggle').addEventListener('click', function () {
      $('ex-lib').classList.toggle('hidden');
      if (!$('ex-lib').classList.contains('hidden') && !$('lib-list').children.length) searchLibrary(0);
    });
    $('lib-go').addEventListener('click', function () { searchLibrary(0); });
    $('lib-q').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); searchLibrary(0); } });
    $('lib-more').addEventListener('click', function () { searchLibrary(libPage + 1); });
    loadVoices(false);
  }
  function loadVoices(force) {
    var d = state.form; if (!d) return;
    var lang = d.language || 'ky';
    var cached = state.voices && state.voices.language === lang && !force;
    (cached ? Promise.resolve(state.voices) : api(BASE + '/voices?language=' + encodeURIComponent(lang)).then(function (r) {
      state.voices = { language: lang, voices: (r && r.voices) || [] };
      return state.voices;
    })).then(renderVoices).catch(function (e) {
      var w = $('ex-voice-wrap');
      if (w) w.innerHTML = '<div class="note note-warning">' + VF.icon('triangle-alert') + '<span>' + esc(t('eleven_voices_failed')) + ': ' + esc(e.message) + '</span></div>';
    });
  }
  function voiceLabel(v) {
    var extra = [v.gender, v.accent, v.category].filter(Boolean).join(', ');
    return v.name + (extra ? ' (' + extra + ')' : '');
  }
  function renderVoices(res) {
    var w = $('ex-voice-wrap'); if (!w || !state.form) return;
    var d = state.form, voices = res.voices;
    var rec = voices.filter(function (v) { return v.recommended; }), other = voices.filter(function (v) { return !v.recommended; });
    var html = '<select class="form-control" id="ex-voice"><option value="">' + esc(t('eleven_voice_empty')) + '</option>';
    if (d.voice_id && voices.every(function (v) { return v.voice_id !== d.voice_id; })) {
      html += '<option value="' + esc(d.voice_id) + '" selected>' + esc((d.voice_name || d.voice_id) + ' ' + t('eleven_missing')) + '</option>';
    }
    var opt = function (v) { return '<option value="' + esc(v.voice_id) + '"' + (v.voice_id === d.voice_id ? ' selected' : '') + '>' + esc(voiceLabel(v)) + '</option>'; };
    if (rec.length) html += '<optgroup label="' + esc(t('eleven_group_rec') + ' «' + res.language + '»') + '">' + rec.map(opt).join('') + '</optgroup>';
    if (other.length) html += '<optgroup label="' + esc(t('eleven_group_other')) + '">' + other.map(opt).join('') + '</optgroup>';
    w.innerHTML = html + '</select>';
    var sel = $('ex-voice');
    var update = function () {
      var v = voices.filter(function (x) { return x.voice_id === sel.value; })[0];
      d.voice_id = sel.value;
      if (v) d.voice_name = v.name;
      $('ex-preview').innerHTML = v && v.preview_url ? '<audio class="preview" controls preload="none" src="' + esc(v.preview_url) + '"></audio>' : '';
    };
    sel.addEventListener('change', function () { update(); renderDirty(); });
    update();
  }
  var libPage = 0;
  function searchLibrary(page) {
    var list = $('lib-list'); if (!list) return;
    var d = state.form;
    libPage = page;
    if (!page) list.innerHTML = '<div class="skeleton" style="height:46px"></div><div class="skeleton" style="height:46px"></div>';
    var q = new URLSearchParams({ language: d.language || 'ky', search: $('lib-q').value.trim(), gender: $('lib-gender').value, page: String(page), page_size: '20' });
    api(BASE + '/voices/library?' + q.toString()).then(function (r) {
      if (!page) list.innerHTML = '';
      var voices = (r && r.voices) || [];
      if (!page && !voices.length) list.innerHTML = '<div class="empty" style="padding:16px">' + esc(t('lib_empty')) + '</div>';
      voices.forEach(function (v) {
        var row = document.createElement('div');
        row.className = 'lib-item';
        row.innerHTML = '<div class="grow"><div><b>' + esc(v.name) + '</b></div><div class="faint">' + esc([v.gender, v.accent, v.age, v.use_case].filter(Boolean).join(' · ')) + '</div></div>' +
          (v.preview_url ? '<audio controls preload="none" src="' + esc(v.preview_url) + '"></audio>' : '') +
          '<button class="btn btn-sm" type="button">' + esc(v.is_added ? t('lib_select') : t('lib_add')) + '</button>';
        row.querySelector('button').addEventListener('click', function (e) {
          var b = e.currentTarget; b.disabled = true;
          api(BASE + '/voices/library/add', { method: 'POST', body: { public_owner_id: v.public_owner_id, voice_id: v.voice_id, name: v.name } }).then(function (res) {
            d.voice_id = res.voice_id; d.voice_name = res.name || v.name;
            VF.toast(t('lib_added', { name: d.voice_name }), { type: 'success' });
            renderDirty();
            loadVoices(true);
          }).catch(function (err) { VF.toast(err.message, { type: 'error' }); }).then(function () { b.disabled = false; });
        });
        list.appendChild(row);
      });
      $('lib-more').classList.toggle('hidden', !(r && r.has_more));
    }).catch(function (e) { list.innerHTML = '<div class="note note-warning">' + VF.icon('triangle-alert') + '<span>' + esc(e.message) + '</span></div>'; });
  }

  // ------------------------------------------------------------------
  // Функции
  // ------------------------------------------------------------------
  function renderFunctions() {
    var box = $('funcs'), f = state.form;
    if (!state.catalog || !state.catalog.length) {
      box.innerHTML = '<div class="empty" style="grid-column:1/-1"><div class="empty-title">' + esc(t('funcs_unavailable')) + '</div><div class="empty-text">' + esc(t('funcs_unavailable_text')) + '</div></div>';
      updateSheetBlock();
      return;
    }
    box.innerHTML = state.catalog.map(function (fn) {
      var on = f.functions.indexOf(fn.name) !== -1;
      return '<label class="func' + (on ? ' on' : '') + '" data-name="' + esc(fn.name) + '"><span class="switch"><input type="checkbox"' + (on ? ' checked' : '') + '><span class="track"></span></span>' +
        '<span><div class="f-name">' + esc(fn.display_name || fn.name) + '</div><div class="f-desc">' + esc(fn.description || '') + '</div></span></label>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('.func'), function (el) {
      var cb = el.querySelector('input');
      cb.addEventListener('change', function () {
        setFunction(el.getAttribute('data-name'), cb.checked);
        el.classList.toggle('on', cb.checked);
        updateSheetBlock(); renderDirty();
      });
    });
    updateSheetBlock();
  }
  function setFunction(name, on) {
    var list = state.form.functions, i = list.indexOf(name);
    if (on && i === -1) list.push(name);
    if (!on && i !== -1) list.splice(i, 1);
  }
  function updateSheetBlock() {
    var need = state.form.functions.some(function (n) { return /sheet/i.test(n); }) || !!state.form.sheet;
    $('sheet-block').classList.toggle('hidden', !need);
  }
  function functionsPayload() {
    if (!state.catalog || !state.catalog.length) return state.form.rawFunctions || null;
    var byName = {};
    state.catalog.forEach(function (fn) { byName[fn.name] = fn; });
    var list = state.form.functions.filter(function (n) { return byName[n]; }).map(function (n) { return { name: n, description: byName[n].description || '' }; });
    return list.length ? list : null;
  }

  // ------------------------------------------------------------------
  // Сохранение
  // ------------------------------------------------------------------
  function readForm() {
    var f = state.form; if (!f) return;
    f.name = $('f-name').value; f.description = $('f-desc').value; f.greeting = $('f-greeting').value;
    f.prompt = $('f-prompt').value; f.sheet = $('f-sheet').value.trim(); f.active = $('f-active').checked;
  }
  function payload(isUpdate) {
    var f = state.form;
    var body = {
      name: f.name.trim(),
      description: f.description.trim() || null,
      system_prompt: f.prompt || null,
      greeting_message: f.greeting.trim() || KY_GREETING,
      google_sheet_id: f.sheet || null,
      functions: functionsPayload(),
      voice_id: f.voice_id || null,
      voice_name: f.voice_name || null,
      tts_model: f.tts_model,
      stability: Number(f.stability),
      llm_model: f.llm_model,
      language: f.language || 'ky'
    };
    if (isUpdate) body.is_active = !!f.active;
    return body;
  }
  function validate() {
    var f = state.form;
    if (!f.name.trim()) { VF.toast(t('need_name'), { type: 'warning' }); switchTab('settings'); $('f-name').focus(); return false; }
    if (!f.voice_id) { VF.toast(t('eleven_voice_required'), { type: 'warning' }); switchTab('settings'); return false; }
    return true;
  }
  function save() {
    if (!editorOpen || $('btn-save').disabled) return Promise.resolve(false);
    readForm();
    if (!validate()) return Promise.resolve(false);
    renderSaveButton(true);
    var isNew = !state.current;
    var req = isNew
      ? api(BASE, { method: 'POST', body: payload(false) })
      : api(itemUrl(state.current.id), { method: 'PUT', body: payload(true) });
    return req.then(function (a) {
      VF.toast(isNew ? t('created') : t('updated'), { type: 'success' });
      afterSaved(a, isNew);
      return true;
    }).catch(function (e) { VF.toast(e.message, { type: 'error', duration: 7000 }); return false; })
      .then(function (ok) { renderSaveButton(false); return ok; });
  }
  function afterSaved(a, isNew) {
    var keepTab = state.tab;
    state.current = a;
    state.form = formFrom(a);
    var idx = -1;
    state.items.forEach(function (x, i) { if (x.id === a.id) idx = i; });
    if (idx === -1) state.items.unshift(a); else state.items[idx] = a;
    fillForm(); markClean(); switchTab(keepTab); renderList();
    if (isNew) loadUsage();
  }
  // Точечное обновление функций сохранённого ассистента (без остальных полей формы)
  function saveFunctionsOnly(names) {
    var byName = {};
    (state.catalog || []).forEach(function (fn) { byName[fn.name] = fn; });
    var list = names.map(function (n) { return { name: n, description: (byName[n] && byName[n].description) || '' }; });
    return api(itemUrl(state.current.id), { method: 'PUT', body: { functions: list.length ? list : null } }).then(function (a) {
      state.current.functions = a.functions;
      state.items.forEach(function (x) { if (x.id === a.id) x.functions = a.functions; });
      // В форме — то же, не трогая остальные несохранённые правки
      var saved = fnNames(a.functions);
      var wasClean = !isDirty();
      state.form.functions = saved.slice(); state.form.rawFunctions = a.functions;
      renderFunctions();
      if (wasClean) markClean(); else renderDirty();
    });
  }

  function deleteCurrent() {
    var cur = state.current; if (!cur) return;
    VF.confirm({ title: t('delete_title'), message: t('delete_msg', { name: cur.name }), confirmText: t('delete_ok'), danger: true }).then(function (ok) {
      if (!ok) return;
      api(itemUrl(cur.id), { method: 'DELETE' }).then(function () {
        state.items = state.items.filter(function (x) { return x.id !== cur.id; });
        VF.toast(t('deleted'), { type: 'success' });
        closeEditor(true); loadUsage();
      }).catch(function (e) { VF.toast(e.message, { type: 'error', duration: 8000 }); });
    });
  }
  function copyText(text, okMsg) {
    var done = function () { VF.toast(okMsg, { type: 'success' }); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { VF.toast(t('copy_failed'), { type: 'error' }); });
    } else {
      var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { VF.toast(t('copy_failed'), { type: 'error' }); }
      ta.remove();
    }
  }

  // ------------------------------------------------------------------
  // Вкладки
  // ------------------------------------------------------------------
  function switchTab(tab) {
    if (!document.querySelector('#tabs .tab[data-tab="' + tab + '"]')) tab = 'settings';
    state.tab = tab;
    Array.prototype.forEach.call(document.querySelectorAll('#tabs .tab'), function (el) { el.classList.toggle('active', el.getAttribute('data-tab') === tab); });
    Array.prototype.forEach.call(document.querySelectorAll('.tab-pane'), function (el) { el.classList.toggle('active', el.getAttribute('data-pane') === tab); });
    if (tab !== 'test') teardownWidget();
    if (tab === 'knowledge') renderKb();
    if (tab === 'test') renderTest();
    if (tab === 'embed') renderEmbed();
    syncUrl();
  }
  function emptyBox(icon, title, text, extra) {
    return '<div class="empty">' + (icon ? VF.icon(icon, 'ic-lg') : '') + '<div class="empty-title">' + esc(title) + '</div>' + (text ? '<div class="empty-text">' + esc(text) + '</div>' : '') + (extra || '') + '</div>';
  }

  // Тестирование: тот же widget.js, что на сайте клиента, с сокетом /ws/eleven/
  function teardownWidget() {
    ['#wellcomeai-widget-container', '.wellcomeai-widget-container', '#wellcomeai-widget-styles'].forEach(function (s) {
      Array.prototype.forEach.call(document.querySelectorAll(s), function (el) { el.remove(); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('script[data-va-widget]'), function (el) { el.remove(); });
  }
  function renderTest() {
    var box = $('test-pane'), cur = state.current;
    if (!cur) { box.innerHTML = emptyBox('save', t('save_first'), t('save_first_text')); return; }
    var phone = '<p class="hint" style="margin-top:12px">' + esc(t('phone_test')) + ' <a href="/static/telephony.html">' + esc(t('open_telephony')) + '</a></p>';
    if (cur.is_active === false) { box.innerHTML = emptyBox('circle-minus', t('widget_inactive'), '', phone); return; }
    var warn = state.status && state.status.ready === false
      ? '<div class="note note-warning" style="max-width:520px;margin:12px auto 0;text-align:left">' + VF.icon('triangle-alert') + '<span>' + esc(t('server_not_ready')) + '</span></div>' : '';
    box.innerHTML = emptyBox('headset', t('widget_title'), t('widget_text'), '<div class="small faint" id="widget-status">' + esc(t('widget_loading')) + '</div>' + warn + phone);
    // Виджет уже запускался для другого ассистента: его звук и сокет живут в
    // замыкании скрипта, надёжно сбросить их можно только перезагрузкой.
    if (window.__vaWidgetUsed && window.__vaWidgetUsed !== cur.id) { location.reload(); return; }
    teardownWidget();
    var s = document.createElement('script');
    s.src = '/static/widget.js?t=' + Date.now();
    s.setAttribute('data-assistantId', cur.id);
    s.setAttribute('data-server', location.origin);
    s.setAttribute('data-position', 'bottom-right');
    s.setAttribute('data-ws-path', '/ws/eleven/');
    s.setAttribute('data-va-widget', '1');
    s.async = true;
    s.onload = function () { var st = $('widget-status'); if (st) st.textContent = t('widget_loaded'); };
    s.onerror = function () { var st = $('widget-status'); if (st) st.textContent = t('widget_failed'); };
    document.head.appendChild(s);
    window.__vaWidgetUsed = cur.id;
  }

  // Встраивание: код для сайта клиента
  function renderEmbed() {
    var box = $('embed-pane'), cur = state.current;
    if (!cur) { box.innerHTML = emptyBox('save', t('save_first'), t('save_first_text')); return; }
    if (cur.is_active === false) { box.innerHTML = emptyBox('circle-minus', t('embed_inactive'), ''); return; }
    var origin = location.origin;
    var code = '<!-- VoksiAI Voice Assistant -->\n<script src="' + origin + '/static/widget.js"\n        data-assistantId="' + cur.id + '"\n        data-server="' + origin +
      '"\n        data-ws-path="/ws/eleven/"\n        data-position="bottom-right"\n        async></' + 'script>';
    box.innerHTML = '<p class="muted" style="margin-bottom:12px">' + esc(t('embed_text')) + '</p><div class="code-box"><pre id="embed-code"></pre>' +
      '<button class="btn btn-sm" type="button" id="copy-code">' + VF.icon('copy', 'ic-sm') + esc(t('embed_copy')) + '</button></div>' +
      '<div class="field" style="margin-top:20px"><label class="label">' + esc(t('assistant_id')) + '</label><div class="row"><code class="mono grow truncate">' + esc(cur.id) +
      '</code><button class="btn btn-sm" type="button" id="copy-id">' + VF.icon('copy', 'ic-sm') + esc(t('embed_copy')) + '</button></div></div>';
    $('embed-code').textContent = code;
    $('copy-code').addEventListener('click', function () { copyText(code, t('embed_copied')); });
    $('copy-id').addEventListener('click', function () { copyText(String(cur.id), t('id_copied')); });
  }

  // ------------------------------------------------------------------
  // База знаний ассистента (одна на ассистента)
  // ------------------------------------------------------------------
  var kbModal = null;
  function renderKb() {
    var box = $('kb-box'), cur = state.current;
    if (!cur) { box.innerHTML = emptyBox('save', t('save_first'), t('save_first_text')); return; }
    box.innerHTML = '<div class="skeleton" style="height:120px"></div>';
    var id = cur.id;
    api(kbUrl(id)).then(function (kb) {
      if (!state.current || state.current.id !== id) return;
      state.kb = kb && kb.has_knowledge_base ? kb : null;
      drawKb();
    }).catch(function (e) {
      box.innerHTML = '<div class="note note-warning">' + VF.icon('triangle-alert') + '<span>' + esc(t('kb_load_failed')) + ': ' + esc(e.message) + '</span></div>';
    });
  }
  function drawKb() {
    var box = $('kb-box'), kb = state.kb;
    if (!kb) {
      box.innerHTML = '<div class="table-wrap">' + emptyBox('book-open', t('kb_empty_title'), t('kb_empty_text'),
        '<button class="btn btn-primary" type="button" id="kb-create">' + VF.icon('plus') + esc(t('kb_create')) + '</button>') + '</div>';
      $('kb-create').addEventListener('click', function () { openKbModal(); });
      return;
    }
    var searchOff = state.form.functions.indexOf(SEARCH_FN) === -1;
    box.innerHTML = '<div class="kb-card"><div class="kb-head"><span class="logo-wrap">' + VF.icon('book-open') + '</span>' +
      '<div class="grow"><div style="font-weight:600">' + esc(kb.name) + '</div><div class="faint small">' +
      esc(t('kb_chars', { n: Number(kb.char_count || 0).toLocaleString('ru-RU') })) + ' · ' + esc(t('kb_updated', { date: fmtDate(kb.updated_at) })) + '</div></div>' +
      '<button class="btn btn-sm" type="button" id="kb-edit">' + VF.icon('pen', 'ic-sm') + esc(t('kb_edit')) + '</button>' +
      '<button class="btn btn-sm btn-danger" type="button" id="kb-del">' + VF.icon('trash-2', 'ic-sm') + esc(t('kb_delete')) + '</button></div>' +
      (kb.content_preview ? '<div class="kb-preview"></div>' : '') +
      (searchOff ? '<div class="note note-warning">' + VF.icon('triangle-alert') + '<span class="grow">' + esc(t('kb_search_off')) +
        '</span><button class="btn btn-sm" type="button" id="kb-search-on">' + esc(t('kb_search_on')) + '</button></div>' : '') + '</div>';
    if (kb.content_preview) box.querySelector('.kb-preview').textContent = kb.content_preview;
    $('kb-edit').addEventListener('click', function () { openKbModal(kb); });
    $('kb-del').addEventListener('click', deleteKb);
    var on = $('kb-search-on');
    if (on) on.addEventListener('click', function () {
      on.disabled = true;
      var names = fnNames(state.current.functions); if (names.indexOf(SEARCH_FN) === -1) names.push(SEARCH_FN);
      saveFunctionsOnly(names).then(drawKb, function (e) { on.disabled = false; VF.toast(e.message, { type: 'error' }); });
    });
  }
  function openKbModal(kb) {
    $('kb-modal-title').textContent = kb ? t('kb_edit_title') : t('kb_new_title');
    $('kb-save').innerHTML = VF.icon('check') + esc(t('save'));
    $('kb-save').disabled = false;
    $('kb-name').value = kb ? kb.name || '' : (state.current.name || '');
    $('kb-content').value = kb ? kb.full_content || '' : '';
    updateKbCount();
    kbModal.open();
  }
  function updateKbCount() { $('kb-count').textContent = t('chars', { n: $('kb-content').value.length.toLocaleString('ru-RU') }); }
  function saveKb() {
    var name = $('kb-name').value.trim(), content = $('kb-content').value;
    if (!content.trim()) { VF.toast(t('kb_need_content'), { type: 'warning' }); return; }
    var b = $('kb-save'), id = state.current.id;
    b.disabled = true; b.innerHTML = '<span class="spin"></span> ' + esc(t('saving'));
    var slow = setTimeout(function () { VF.toast(t('kb_saving'), { type: 'info' }); }, 2500);
    api(kbUrl(id), { method: 'PUT', body: { name: name, content: content } }).then(function () {
      kbModal.close();
      // Поиск по базе включаем сразу — иначе ассистент в неё не заглянет
      var names = fnNames(state.current.functions);
      var p = names.indexOf(SEARCH_FN) === -1 ? (names.push(SEARCH_FN), saveFunctionsOnly(names)) : Promise.resolve();
      return p.then(function () { VF.toast(t('kb_saved'), { type: 'success' }); renderKb(); });
    }).catch(function (e) { VF.toast(e.message, { type: 'error', duration: 8000 }); })
      .then(function () { clearTimeout(slow); b.disabled = false; b.innerHTML = VF.icon('check') + esc(t('save')); });
  }
  function deleteKb() {
    var kb = state.kb; if (!kb) return;
    VF.confirm({ title: t('kb_delete_title'), message: t('kb_delete_msg', { name: kb.name }), confirmText: t('delete_ok'), danger: true }).then(function (ok) {
      if (!ok) return;
      api(kbUrl(state.current.id), { method: 'DELETE' }).then(function () {
        var names = fnNames(state.current.functions).filter(function (n) { return n !== SEARCH_FN; });
        return saveFunctionsOnly(names).catch(function () {});
      }).then(function () { VF.toast(t('kb_deleted'), { type: 'success' }); state.kb = null; drawKb(); })
        .catch(function (e) { VF.toast(e.message, { type: 'error' }); });
    });
  }

  // ------------------------------------------------------------------
  // События
  // ------------------------------------------------------------------
  function bind() {
    $('btn-new').addEventListener('click', requestNew);
    $('btn-back').addEventListener('click', function () { closeEditor(false); });
    $('btn-cancel').addEventListener('click', function () {
      if (!state.current) { closeEditor(false); return; }
      confirmLeave().then(function (ok) { if (ok) { state.form = formFrom(state.current); fillForm(); markClean(); switchTab(state.tab); } });
    });
    $('btn-save').addEventListener('click', save);
    ['f-name', 'f-desc', 'f-greeting', 'f-prompt', 'f-sheet'].forEach(function (id) {
      $(id).addEventListener('input', function () {
        readForm();
        if (id === 'f-prompt') updatePromptCount();
        if (id === 'f-sheet') updateSheetBlock();
        renderDirty();
      });
    });
    $('f-active').addEventListener('change', function () { readForm(); renderDirty(); });
    Array.prototype.forEach.call(document.querySelectorAll('#tabs .tab'), function (el) {
      el.addEventListener('click', function () { switchTab(el.getAttribute('data-tab')); });
    });
    $('btn-more').addEventListener('click', function (e) {
      var cur = state.current; if (!cur) return;
      VF.menu(e.currentTarget, [
        { label: t('menu_telephony'), icon: 'phone', onClick: function () { location.href = '/static/telephony.html'; } },
        { label: t('menu_copy_id'), icon: 'copy', onClick: function () { copyText(String(cur.id), t('id_copied')); } },
        { label: t('menu_copy_prompt'), icon: 'copy', onClick: function () { copyText($('f-prompt').value, t('prompt_copied')); } },
        { sep: true },
        { label: t('menu_delete'), icon: 'trash-2', danger: true, onClick: deleteCurrent }
      ]);
    });
    kbModal = VF.modal('kb-modal');
    $('kb-save').addEventListener('click', saveKb);
    $('kb-content').addEventListener('input', updateKbCount);
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S' || e.key === 'ы' || e.key === 'Ы') && editorOpen) { e.preventDefault(); save(); }
    });
    window.addEventListener('beforeunload', function (e) { if (isDirty()) { e.preventDefault(); e.returnValue = ''; } });
  }

  // ------------------------------------------------------------------
  // Старт
  // ------------------------------------------------------------------
  bind();
  VF.skeleton($('list-body'), 6);
  Promise.all([loadUser(), loadUsage(), loadCatalog(), loadOptions()]).then(loadList).then(function () {
    var q = new URLSearchParams(location.search);
    var id = q.get('id'), tab = q.get('tab');
    if (id) {
      if (state.items.some(function (x) { return String(x.id) === id; })) return openAssistant(id, tab);
      history.replaceState(null, '', location.pathname);   // ассистент другого провайдера или удалён
      return;
    }
    if (q.get('new') === '1') {
      if (canCreate()) openNew();
      else VF.toast(t('limit_reached', { used: state.usage.used, max: state.usage.max }), { type: 'warning', duration: 7000 });
    }
  }).catch(function (e) { console.error(e); }).then(function () { renderDirty(); VF.ready(); });
})();
