/**
 * Voksy AI — страница «Знания агента» (/static/agent-knowledge.html).
 *
 * Объединяет то, что раньше жило в двух маленьких модалках страницы агента:
 * базу знаний и библиотеку файлов (ветка 1610-kb-calls). Это одно и то же
 * хозяйство — один и тот же файл агент и отправляет клиенту, и использует как
 * источник ответов, — поэтому роли файла здесь переключателями в одном списке.
 *
 * Пять разделов:
 *   Вопрос — ответ   агент отвечает мгновенно, без поиска (уходит в промпт)
 *   О компании       секции: условия, доставка, гарантия
 *   Каталог          плитки с фотографиями, цена, характеристики, наличие
 *   Файлы            библиотека: «отправляет клиентам» / «ищет ответы внутри»
 *   Чего не хватает  вопросы клиентов, на которые база не ответила
 *
 * Сверху — полоса «что агент знает наизусть на звонке» (тот же расчёт, что
 * уходит в промпт звонка, поле voice в ответе /knowledge) и «спросить как
 * клиент»: видно, ответит агент сразу или пойдёт в поиск.
 *
 * API: /api/agent/list, /api/agent/knowledge (CRUD, /test, /document, /gaps),
 *      /api/agent/files (+ роли и превью).
 * Строки — раздел akb словарей /static/i18n/{ru,ky}.json (RU по умолчанию ниже).
 */
(function () {
  'use strict';

  var STR = {
    page_title: 'Знания агента', add: 'Добавить', to_agent: 'К агенту',
    ask_title: 'Спросить как клиент',
    ask_sub: 'Введите вопрос так, как спросит клиент — покажу, ответит агент мгновенно или пойдёт в поиск.',
    ask_ph: 'Например: а рассрочка есть?',
    ask_fast: 'Агент ответит мгновенно — это знание у него в инструкции.',
    ask_search: 'Агент найдёт это поиском — на звонке будет небольшая пауза.',
    ask_none: 'Ответа в базе нет. Агент скажет, что уточнит у специалиста.',
    ask_empty: 'Введите вопрос',

    brain_title: 'Что агент знает наизусть на звонке',
    brain_sub: 'Эти знания уходят в инструкцию агента: на звонке он отвечает ими сразу, не вызывая поиск. Поиск — это пауза в несколько секунд, поэтому самое частое держите здесь.',
    brain_empty: 'База пустая: на звонке агент не знает о компании ничего и будет отправлять клиентов к специалисту.',
    brain_company: 'О компании', brain_knowledge: 'Вопрос-ответ и секции',
    brain_price: 'Прайс', brain_files: 'Материалы',
    brain_total: '{n} из {max} символов',
    brain_full: 'Бюджет мгновенных знаний почти исчерпан — остальное агент будет искать.',

    tab_qa: 'Вопрос — ответ', tab_section: 'О компании', tab_product: 'Каталог',
    tab_files: 'Файлы', tab_gaps: 'Чего не хватает',

    hint_qa: 'Пары «вопрос клиента — ответ». Самый быстрый раздел: эти ответы агент знает наизусть и называет без паузы. Держите здесь то, что спрашивают чаще всего: цены, адрес, часы работы, условия.',
    hint_section: 'Блоки о компании: условия, доставка, гарантия, акции. Короткие агент тоже помнит наизусть, длинные достаёт поиском.',
    hint_product: 'Товары и услуги с ценой, характеристиками и фото. Названия и цены агент знает наизусть и называет сразу; по фото и характеристикам ходит в каталог. Фото уходят клиенту в переписку.',
    hint_files: 'Прайсы, презентации, договоры. У каждого файла две роли: агент отправляет его клиентам и/или ищет ответы внутри. Включайте обе — это один и тот же файл.',
    hint_gaps: 'О чём спрашивали клиенты, а база промолчала. Добавьте ответы — и агент перестанет отправлять людей к менеджеру.',

    add_qa: 'Добавить вопрос', add_section: 'Добавить раздел', add_product: 'Добавить позицию',
    upload: 'Загрузить файл',

    empty_qa_t: 'Пока нет готовых ответов', empty_qa_p: 'Это самый быстрый способ научить агента: вопрос клиента — и что отвечать.',
    empty_section_t: 'Нет разделов о компании', empty_section_p: 'Добавьте условия, доставку, гарантию — агент будет говорить о них точно, а не приблизительно.',
    empty_product_t: 'Каталог пуст', empty_product_p: 'Добавьте товары с ценой и фото — агент будет называть цены на звонке и присылать фото в переписку.',
    empty_files_t: 'Библиотека пуста', empty_files_p: 'Загрузите прайс, презентацию или договор: агент отправит их клиенту и сможет искать ответы внутри.',
    empty_gaps_t: 'Пробелов нет', empty_gaps_p: 'Здесь появятся вопросы клиентов, на которые агент не нашёл ответа.',

    tag_fast: 'мгновенно', tag_fast_tip: 'Агент отвечает сразу, без поиска',
    tag_search: 'поиск', tag_search_tip: 'Агент найдёт это поиском — на звонке пауза',
    tag_off: 'выключено', tag_hits_tip: 'Сколько раз знание пригодилось',
    no_price: 'цена не указана', out_of_stock: 'нет в наличии',
    photos_more: '+{n}', no_photo: 'без фото',

    role_send: 'Агент отправляет клиентам', role_search: 'Агент ищет ответы внутри',
    role_search_no_text: 'Из этого файла не удалось извлечь текст — искать внутри нечего',
    role_in_catalog: 'в каталоге', vector_pending: 'обрабатывается', vector_failed: 'ошибка',
    file_size: '{n}', file_del_t: 'Удалить файл?',
    file_del_m: 'Файл «{name}» будет удалён. Если он был источником ответов, агент перестанет находить в нём информацию.',

    ed_qa: 'Вопрос и ответ', ed_section: 'Раздел о компании', ed_product: 'Позиция каталога',
    f_question: 'Вопрос клиента', f_question_ph: 'Есть ли рассрочка?',
    f_answer: 'Что отвечать', f_answer_ph: 'Да, рассрочка 0% на 6 месяцев, нужен только паспорт.',
    f_sec_title: 'Заголовок', f_sec_title_ph: 'Доставка',
    f_sec_body: 'Текст', f_sec_body_ph: 'По Бишкеку бесплатно от 10 000 сом, иначе 300 сом.',
    f_prod_title: 'Название', f_prod_title_ph: 'Холодильник Samsung RB37',
    f_prod_body: 'Описание', f_prod_body_ph: 'Двухкамерный, No Frost, 367 литров.',
    f_price: 'Цена', f_currency: 'Валюта', f_in_stock: 'В наличии',
    f_keywords: 'Как ещё об этом спрашивают',
    f_keywords_ph: 'почём, сколько стоит, рассрочка',
    f_keywords_hint: 'Слова клиента, которых нет в заголовке — через запятую. Поиск смотрит и сюда.',
    f_attrs: 'Характеристики', f_attr_name_ph: 'Цвет', f_attr_val_ph: 'чёрный', f_attr_add: 'Добавить характеристику',
    f_photos: 'Фото и видео', f_photos_hint: 'Агент отправит их клиенту в переписку вместе с ответом. Выберите из библиотеки или загрузите новые.',
    f_photos_empty: 'В библиотеке нет фото. Загрузите их кнопкой ниже.',
    f_photo_upload: 'Загрузить фото',
    f_in_prompt: 'Агент помнит наизусть',
    f_in_prompt_hint: 'Знание уходит в инструкцию агента — он отвечает без поиска. Для длинных текстов лучше выключить.',
    f_active: 'Используется',
    need_title: 'Заполните заголовок или текст',

    del_t: 'Удалить запись?', del_m: 'Запись «{name}» будет удалена из базы знаний.',
    gap_answer: 'Ответить', gap_hide: 'Скрыть', gap_asked: 'спрашивали {n} раз',
    gap_channel_call: 'на звонке', gap_channel_chat: 'в переписке',

    saved: 'Сохранено', deleted: 'Удалено', uploaded: 'Файл загружен',
    kb_on: 'Агент теперь ищет ответы в этом файле', kb_off: 'Файл больше не источник ответов',
    no_agent_t: 'Агент не создан', no_agent_p: 'Сначала создайте агента обзвона — знания привязываются к нему.',
    no_agent_btn: 'Создать агента',
    storage_off: 'Хранилище файлов не настроено — загрузка недоступна. Сообщите администратору.',
    load_failed: 'Не удалось загрузить: {reason}', request_error: 'Ошибка запроса',
  };

  function t(key, vars) {
    // I18N.t(key, params, def) — текст по умолчанию ТРЕТИЙ аргумент. Передав
    // его вторым, страница при промахе показывала сам ключ («akb.tab_qa»)
    // вместо русского текста. Плюс свой запасной путь: если словарь в браузере
    // старый (он кэшируется в sessionStorage по DICT_VERSION) и раздела akb в
    // нём нет — берём строку отсюда, а не показываем ключ.
    var fallback = STR[key];
    var s = fallback;
    if (window.I18N && window.I18N.t) {
      var v = window.I18N.t('akb.' + key, null, fallback);
      if (v != null && v !== 'akb.' + key) s = v;
    }
    s = s == null ? key : String(s);
    if (vars) {
      Object.keys(vars).forEach(function (k) { s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), vars[k]); });
    }
    return s;
  }

  var esc = VF.esc;
  var API = '/api/agent';
  var $ = function (id) { return document.getElementById(id); };

  // ------------------------------------------------------------------ API
  function token() { try { return localStorage.getItem('auth_token'); } catch (e) { return null; } }
  if (!token()) { location.href = '/static/login.html'; return; }

  function errText(data, status) {
    var d = data && (data.detail !== undefined ? data.detail : data.message);
    if (typeof d === 'string') return d;
    if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
    if (Array.isArray(d) && d.length && d[0].msg) return d[0].msg;
    return t('request_error') + (status ? ' (' + status + ')' : '');
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = { 'Authorization': 'Bearer ' + token() };
    var body = opts.body;
    // FormData отдаём как есть: свой Content-Type с boundary ставит браузер.
    if (body !== undefined && !(body instanceof FormData) && typeof body !== 'string') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    var url = path + (path.indexOf('?') >= 0 ? '&' : '?') + 'agent_id=' + encodeURIComponent(state.agentId || '');
    return fetch(url, { method: opts.method || 'GET', headers: headers, body: body }).then(function (r) {
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

  // ------------------------------------------------------------------ состояние
  var state = {
    agentId: null, agents: [], tab: 'qa',
    entries: [], counts: {}, voice: {}, files: [], gaps: [],
    storage: true, editing: null, pickedMedia: [], attrs: [],
  };
  var KINDS = ['qa', 'section', 'product'];
  var edModal = null;

  function entriesOf(kind) {
    return state.entries.filter(function (e) { return e.kind === kind; });
  }

  // ------------------------------------------------------------------ загрузка
  function boot() {
    var qp = new URLSearchParams(location.search);
    return fetch(API + '/list', { headers: { 'Authorization': 'Bearer ' + token() } })
      .then(function (r) { return r.ok ? r.json() : { agents: [] }; })
      .then(function (d) {
        state.agents = d.agents || [];
        var wanted = qp.get('agent_id');
        var found = state.agents.filter(function (a) { return a.id === wanted; })[0];
        state.agentId = (found || state.agents[0] || {}).id || null;
        if (!state.agentId) { renderNoAgent(); return null; }   // VF.ready() ниже в .then
        renderAgentPick();
        return loadAll();
      })
      .catch(function (e) {
        if (e.message !== 'unauthorized') VF.toast(t('load_failed', { reason: e.message }), { type: 'error' });
      })
      // Заставка кабинета снимается ТОЛЬКО по VF.ready() (атрибут
      // data-vf-loader без "auto"). Без этого вызова она висела до аварийного
      // таймаута в 6 с — страница была готова за доли секунды, а выглядело
      // это как «база знаний грузится 5–10 секунд».
      .then(function () { VF.ready(); });
  }

  function loadAll() {
    return Promise.all([
      api(API + '/knowledge').then(function (d) {
        state.entries = (d && d.entries) || [];
        state.counts = (d && d.counts) || {};
        state.voice = (d && d.voice) || {};
      }),
      api(API + '/files?source=library').then(function (d) {
        state.files = (d && d.files) || [];
        state.storage = !d || d.storage_configured !== false;
      }),
      api(API + '/knowledge/gaps').then(function (d) { state.gaps = (d && d.gaps) || []; }),
    ]).then(render).catch(function (e) {
      if (e.message !== 'unauthorized') VF.toast(t('load_failed', { reason: e.message }), { type: 'error' });
    });
  }

  // ------------------------------------------------------------------ каркас
  function renderNoAgent() {
    $('brain').style.display = 'none';
    $('tabs').style.display = 'none';
    $('btn-add').style.display = 'none';
    $('sec').innerHTML = '<div class="card"><div class="empty-box">' + VF.icon('headset') +
      '<h3>' + esc(t('no_agent_t')) + '</h3><p>' + esc(t('no_agent_p')) + '</p>' +
      '<a class="btn btn-primary" href="/static/agent.html">' + VF.icon('plus') + esc(t('no_agent_btn')) + '</a>' +
      '</div></div>';
  }

  function renderAgentPick() {
    var el = $('agent-pick');
    if (state.agents.length < 2) { el.innerHTML = ''; return; }
    el.innerHTML = '<select class="select-native" id="agent-select" style="max-width:220px">' +
      state.agents.map(function (a) {
        return '<option value="' + esc(a.id) + '"' + (a.id === state.agentId ? ' selected' : '') + '>' +
          esc(a.name || 'Агент') + '</option>';
      }).join('') + '</select>';
    var sel = $('agent-select');
    if (VF.enhanceSelect) VF.enhanceSelect(sel);
    sel.addEventListener('change', function () {
      state.agentId = sel.value;
      history.replaceState(null, '', location.pathname + '?agent_id=' + encodeURIComponent(state.agentId));
      loadAll();
    });
    // Ссылка «К агенту» ведёт к выбранному агенту
    $('btn-to-agent').href = '/static/agent.html?agent_id=' + encodeURIComponent(state.agentId);
  }

  function render() {
    renderBrain();
    renderTabs();
    renderSection();
  }

  // ------------------------------------------------------------------ «знает наизусть»
  function renderBrain() {
    var v = state.voice || {};
    var total = v.chars || 0;
    var max = v.budget || 4000;
    var el = $('brain-stats');
    var parts = [
      { k: 'company', cls: 'company', color: '#2a5ce8', n: v.company || 0 },
      { k: 'knowledge', cls: 'knowledge', color: '#10b981', n: v.knowledge || 0 },
      { k: 'price', cls: 'price', color: '#f59e0b', n: v.price || 0 },
      { k: 'files', cls: 'files', color: '#8b5cf6', n: v.files || 0 },
    ];
    var html = '<div class="brain-title">' + VF.icon('brain') + '<span>' + esc(t('brain_title')) + '</span>' +
      (total ? '<span class="chip chip-accent" style="margin-left:auto">' +
        esc(t('brain_total', { n: total, max: max })) + '</span>' : '') + '</div>';
    if (!total) {
      html += '<div class="brain-sub" style="margin-top:6px">' + VF.icon('triangle-alert') + ' ' + esc(t('brain_empty')) + '</div>';
      el.innerHTML = html;
      return;
    }
    html += '<div class="brain-sub">' + esc(t('brain_sub')) + '</div>';
    html += '<div class="brain-bar">' + parts.map(function (p) {
      var pct = Math.min(100, (p.n / max) * 100);
      return pct > 0 ? '<span class="brain-seg ' + p.cls + '" style="width:' + pct.toFixed(1) + '%"></span>' : '';
    }).join('') + '</div>';
    html += '<div class="brain-legend">' + parts.filter(function (p) { return p.n > 0; }).map(function (p) {
      return '<span><span class="dot" style="background:' + p.color + '"></span>' +
        esc(t('brain_' + p.k)) + ' <b>' + p.n + '</b></span>';
    }).join('') + '</div>';
    if (total > max * 0.9) {
      html += '<div class="note note-warning" style="margin-top:12px">' + VF.icon('triangle-alert') +
        '<span>' + esc(t('brain_full')) + '</span></div>';
    }
    el.innerHTML = html;
  }

  // ------------------------------------------------------------------ разделы
  var TABS = [
    { id: 'qa', icon: 'zap' },
    { id: 'section', icon: 'building-2' },
    { id: 'product', icon: 'shopping-cart' },
    { id: 'files', icon: 'folder' },
    { id: 'gaps', icon: 'circle-help' },
  ];

  function tabCount(id) {
    if (id === 'files') return state.files.length;
    if (id === 'gaps') return state.gaps.length;
    return (state.counts || {})[id] || 0;
  }

  function renderTabs() {
    $('tabs').innerHTML = TABS.map(function (tab) {
      var n = tabCount(tab.id);
      return '<button class="tab' + (state.tab === tab.id ? ' on' : '') + '" type="button" data-tab="' + tab.id + '">' +
        VF.icon(tab.icon) + '<span>' + esc(t('tab_' + tab.id)) + '</span>' +
        (n ? '<span class="n">' + n + '</span>' : '') + '</button>';
    }).join('');
    Array.prototype.forEach.call($('tabs').querySelectorAll('.tab'), function (b) {
      b.addEventListener('click', function () {
        state.tab = b.getAttribute('data-tab');
        renderTabs(); renderSection();
      });
    });
  }

  function renderSection() {
    var tab = state.tab;
    var add = $('btn-add');
    add.style.display = tab === 'gaps' ? 'none' : '';
    var addLabel = add.querySelector('.btn-text');
    if (addLabel) {
      addLabel.textContent = tab === 'files' ? t('upload')
        : tab === 'product' ? t('add_product')
        : tab === 'section' ? t('add_section') : t('add_qa');
    }

    var head = '<div class="sec-head"><div class="sec-hint">' + esc(t('hint_' + tab)) + '</div></div>';
    var body =
      tab === 'files' ? renderFiles() :
      tab === 'gaps' ? renderGaps() :
      renderEntries(tab);
    $('sec').innerHTML = '<section class="card" style="padding:18px">' + head + body + '</section>';
    bindSection();
  }

  function emptyBox(kind, icon) {
    return '<div class="empty-box">' + VF.icon(icon) +
      '<h3>' + esc(t('empty_' + kind + '_t')) + '</h3><p>' + esc(t('empty_' + kind + '_p')) + '</p></div>';
  }

  // ---------- вопрос-ответ и секции ----------
  function tags(e) {
    var out = e.in_prompt
      ? '<span class="tag tag-fast" title="' + esc(t('tag_fast_tip')) + '">' + esc(t('tag_fast')) + '</span>'
      : '<span class="tag tag-search" title="' + esc(t('tag_search_tip')) + '">' + esc(t('tag_search')) + '</span>';
    if (!e.is_active) out += ' <span class="tag tag-off">' + esc(t('tag_off')) + '</span>';
    if (e.hit_count) out += ' <span class="tag tag-hits" title="' + esc(t('tag_hits_tip')) + '">' + e.hit_count + '×</span>';
    return out;
  }

  function renderEntries(kind) {
    var rows = entriesOf(kind);
    if (kind === 'product') return rows.length ? renderCatalog(rows) : emptyBox('product', 'shopping-cart');
    if (!rows.length) return emptyBox(kind, kind === 'qa' ? 'zap' : 'building-2');
    return '<div class="rows">' + rows.map(function (e) {
      return '<div class="row-card' + (e.is_active ? '' : ' off') + '">' +
        '<div class="row-main" data-edit="' + esc(e.id) + '">' +
        '<div class="row-title">' + esc(e.title || '(без заголовка)') + ' ' + tags(e) + '</div>' +
        '<div class="row-sub">' + esc(e.body || '') + '</div></div>' +
        '<div class="row-acts">' +
        '<button class="btn btn-ghost btn-icon btn-sm" data-edit="' + esc(e.id) + '" title="Изменить">' + VF.icon('pen') + '</button>' +
        '<button class="btn btn-ghost btn-icon btn-sm" data-del="' + esc(e.id) + '" title="Удалить">' + VF.icon('trash-2') + '</button>' +
        '</div></div>';
    }).join('') + '</div>';
  }

  // ---------- каталог плитками ----------
  function renderCatalog(rows) {
    return '<div class="grid">' + rows.map(function (e) {
      var media = e.media || [];
      var first = media[0];
      var photo = first && first.url && !first.is_video
        ? '<img src="' + esc(first.url) + '" alt="" loading="lazy">'
        : (first && first.is_video ? VF.icon('play') : VF.icon('image'));
      var price = (e.price !== null && e.price !== undefined)
        ? '<div class="p-price">' + fmtNum(e.price) + ' <small>' + esc(curWord(e.currency)) + '</small></div>'
        : '<div class="p-price"><small>' + esc(t('no_price')) + '</small></div>';
      var attrs = Object.keys(e.attributes || {}).slice(0, 4).map(function (k) {
        return '<span class="p-attr">' + esc(k) + ': ' + esc(String(e.attributes[k])) + '</span>';
      }).join('');
      return '<div class="p-card' + (e.is_active ? '' : ' off') + '" data-edit="' + esc(e.id) + '">' +
        '<div class="p-photo">' + photo +
        (media.length > 1 ? '<span class="p-more">' + esc(t('photos_more', { n: media.length - 1 })) + '</span>' : '') +
        (e.in_stock === false ? '<span class="p-stock">' + esc(t('out_of_stock')) + '</span>' : '') +
        '</div>' +
        '<div class="p-body"><div class="p-name">' + esc(e.title || '') + '</div>' + price +
        (attrs ? '<div class="p-attrs">' + attrs + '</div>' : '') +
        (e.hit_count ? '<div class="p-attrs"><span class="tag tag-hits">' + e.hit_count + '×</span></div>' : '') +
        '</div></div>';
    }).join('') + '</div>';
  }

  function fmtNum(n) {
    try { return Number(n).toLocaleString('ru-RU'); } catch (e) { return String(n); }
  }

  // Код валюты владельцу ничего не говорит, а «сом» говорит. Тот же список,
  // что у бэкенда для озвучки цены (agent_knowledge_service.format_price).
  var CURRENCY_WORDS = { KGS: 'сом', RUB: 'руб.', USD: 'долл.', EUR: 'евро', KZT: 'тенге' };
  function curWord(code) {
    code = (code || 'KGS').toUpperCase();
    return CURRENCY_WORDS[code] || code;
  }

  // ---------- файлы ----------
  function fileIcon(f) {
    var m = f.mime_type || '';
    if (m.indexOf('image/') === 0) return 'image';
    if (m.indexOf('video/') === 0) return 'play';
    if (m.indexOf('audio/') === 0) return 'volume-2';
    return 'file-text';
  }

  function renderFiles() {
    var note = state.storage ? '' :
      '<div class="note note-warning" style="margin-bottom:14px">' + VF.icon('triangle-alert') +
      '<span>' + esc(t('storage_off')) + '</span></div>';
    if (!state.files.length) return note + emptyBox('files', 'folder');
    return note + '<div class="rows">' + state.files.map(function (f) {
      var thumb = f.preview_url
        ? '<img src="' + esc(f.preview_url) + '" alt="" loading="lazy">'
        : VF.icon(fileIcon(f));
      var meta = [];
      if (f.size_bytes) meta.push(fmtSize(f.size_bytes));
      if (f.used_in_catalog) meta.push(t('role_in_catalog'));
      if (f.knowledge_status === 'pending') meta.push(t('vector_pending'));
      if (f.knowledge_status === 'failed') meta.push(t('vector_failed'));
      var searchable = f.can_be_knowledge;
      return '<div class="f-row">' +
        '<div class="f-thumb">' + thumb + '</div>' +
        '<div><div class="f-name">' + esc(f.title || f.filename || '') +
        (f.description ? '' : '') + '</div>' +
        '<div class="f-meta">' + esc(meta.join(' · ')) + (f.description ? ' — ' + esc(f.description) : '') + '</div></div>' +
        '<div class="f-roles">' +
        '<label class="role"><span class="switch"><input type="checkbox" checked disabled><span class="track"></span></span>' +
        '<span>' + esc(t('role_send')) + '</span></label>' +
        '<label class="role' + (searchable ? '' : ' disabled') + '"' +
        (searchable ? '' : ' title="' + esc(t('role_search_no_text')) + '"') + '>' +
        '<span class="switch"><input type="checkbox" data-kb="' + esc(f.id) + '"' +
        (f.in_knowledge ? ' checked' : '') + (searchable ? '' : ' disabled') +
        (f.knowledge_entry_id ? ' data-entry="' + esc(f.knowledge_entry_id) + '"' : '') +
        '><span class="track"></span></span><span>' + esc(t('role_search')) + '</span></label>' +
        '<div class="f-acts">' +
        '<button class="btn btn-ghost btn-icon btn-sm" data-dl="' + esc(f.id) + '" title="Скачать">' + VF.icon('download') + '</button>' +
        '<button class="btn btn-ghost btn-icon btn-sm" data-fdel="' + esc(f.id) + '" title="Удалить">' + VF.icon('trash-2') + '</button>' +
        '</div></div></div>';
    }).join('') + '</div>';
  }

  function fmtSize(b) {
    if (b < 1024) return b + ' Б';
    if (b < 1024 * 1024) return Math.round(b / 1024) + ' КБ';
    return (b / 1024 / 1024).toFixed(1) + ' МБ';
  }

  // ---------- пробелы ----------
  function renderGaps() {
    if (!state.gaps.length) return emptyBox('gaps', 'circle-help');
    return '<div class="rows">' + state.gaps.map(function (g) {
      var meta = [t('gap_asked', { n: g.ask_count || 1 })];
      if (g.channel === 'call') meta.push(t('gap_channel_call'));
      else if (g.channel) meta.push(t('gap_channel_chat'));
      return '<div class="gap-row"><div><div class="gap-q">' + esc(g.query) + '</div>' +
        '<div class="gap-meta">' + esc(meta.join(' · ')) + '</div></div>' +
        '<div class="row" style="gap:6px">' +
        '<button class="btn btn-secondary btn-sm" data-gap-answer="' + esc(g.query) + '" data-gap-id="' + esc(g.id) + '">' +
        VF.icon('plus', 'ic-sm') + esc(t('gap_answer')) + '</button>' +
        '<button class="btn btn-ghost btn-icon btn-sm" data-gap-hide="' + esc(g.id) + '" title="' + esc(t('gap_hide')) + '">' +
        VF.icon('eye-off') + '</button></div></div>';
    }).join('') + '</div>';
  }

  // ------------------------------------------------------------------ события раздела
  function bindSection() {
    var sec = $('sec');
    sec.querySelectorAll('[data-edit]').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        ev.stopPropagation();
        openEditor(el.getAttribute('data-edit'));
      });
    });
    sec.querySelectorAll('[data-del]').forEach(function (el) {
      el.addEventListener('click', function (ev) { ev.stopPropagation(); delEntry(el.getAttribute('data-del')); });
    });
    sec.querySelectorAll('[data-kb]').forEach(function (el) {
      el.addEventListener('change', function () { toggleKbRole(el); });
    });
    sec.querySelectorAll('[data-dl]').forEach(function (el) {
      el.addEventListener('click', function () { downloadFile(el.getAttribute('data-dl')); });
    });
    sec.querySelectorAll('[data-fdel]').forEach(function (el) {
      el.addEventListener('click', function () { delFile(el.getAttribute('data-fdel')); });
    });
    sec.querySelectorAll('[data-gap-answer]').forEach(function (el) {
      el.addEventListener('click', function () {
        state.tab = 'qa'; renderTabs(); renderSection();
        openEditor(null, 'qa', el.getAttribute('data-gap-answer'), el.getAttribute('data-gap-id'));
      });
    });
    sec.querySelectorAll('[data-gap-hide]').forEach(function (el) {
      el.addEventListener('click', function () { hideGap(el.getAttribute('data-gap-hide')); });
    });
  }

  // ------------------------------------------------------------------ редактор записи
  function openEditor(entryId, forceKind, prefillTitle, gapId) {
    var e = entryId ? state.entries.filter(function (x) { return x.id === entryId; })[0] : null;
    var kind = e ? e.kind : (forceKind || (KINDS.indexOf(state.tab) >= 0 ? state.tab : 'qa'));
    state.editing = { id: entryId || null, kind: kind, gapId: gapId || null };
    state.pickedMedia = (e && e.media_file_ids) ? e.media_file_ids.slice() : [];
    state.attrs = Object.keys((e && e.attributes) || {}).map(function (k) {
      return { k: k, v: String(e.attributes[k]) };
    });

    $('ed-title').textContent = t('ed_' + kind);
    var L = {
      qa: { t: 'f_question', b: 'f_answer' },
      section: { t: 'f_sec_title', b: 'f_sec_body' },
      product: { t: 'f_prod_title', b: 'f_prod_body' },
    }[kind];

    var html =
      '<div class="field"><label class="label" for="ed-t">' + esc(t(L.t)) + '</label>' +
      '<input class="input" id="ed-t" type="text" maxlength="500" placeholder="' + esc(t(L.t + '_ph')) + '" ' +
      'value="' + esc((e && e.title) || prefillTitle || '') + '" autofocus></div>' +
      '<div class="field"><label class="label" for="ed-b">' + esc(t(L.b)) + '</label>' +
      '<textarea class="textarea" id="ed-b" placeholder="' + esc(t(L.b + '_ph')) + '" ' +
      (kind === 'qa' ? 'style="min-height:100px"' : '') + '>' + esc((e && e.body) || '') + '</textarea></div>';

    if (kind === 'product') {
      html += '<div class="ed-grid">' +
        '<div class="field"><label class="label" for="ed-price">' + esc(t('f_price')) + '</label>' +
        '<input class="input" id="ed-price" type="number" step="0.01" min="0" value="' +
        (e && e.price !== null && e.price !== undefined ? esc(String(e.price)) : '') + '"></div>' +
        '<div class="field"><label class="label" for="ed-cur">' + esc(t('f_currency')) + '</label>' +
        '<select class="select-native" id="ed-cur">' +
        ['KGS', 'USD', 'RUB', 'KZT', 'EUR'].map(function (c) {
          return '<option' + ((e && e.currency === c) || (!e && c === 'KGS') ? ' selected' : '') + '>' + c + '</option>';
        }).join('') + '</select></div>' +
        '<div class="field"><label class="label">' + esc(t('f_in_stock')) + '</label>' +
        '<label class="role" style="height:38px"><span class="switch"><input type="checkbox" id="ed-stock"' +
        (!e || e.in_stock !== false ? ' checked' : '') + '><span class="track"></span></span></label></div>' +
        '</div>' +
        '<div class="field"><label class="label">' + esc(t('f_attrs')) + '</label>' +
        '<div id="ed-attrs"></div>' +
        '<button class="btn btn-ghost btn-sm" type="button" id="ed-attr-add">' + VF.icon('plus', 'ic-sm') +
        esc(t('f_attr_add')) + '</button></div>' +
        '<div class="field"><label class="label">' + esc(t('f_photos')) + '</label>' +
        '<div class="hint" style="margin-bottom:8px">' + esc(t('f_photos_hint')) + '</div>' +
        '<div class="media-pick" id="ed-media"></div>' +
        '<div style="margin-top:8px"><button class="btn btn-ghost btn-sm" type="button" id="ed-photo-up">' +
        VF.icon('upload', 'ic-sm') + esc(t('f_photo_upload')) + '</button></div></div>';
    }

    html += '<div class="field"><label class="label" for="ed-kw">' + esc(t('f_keywords')) + '</label>' +
      '<input class="input" id="ed-kw" type="text" placeholder="' + esc(t('f_keywords_ph')) + '" ' +
      'value="' + esc((e && e.keywords) || '') + '">' +
      '<div class="hint">' + esc(t('f_keywords_hint')) + '</div></div>';

    if (kind !== 'product') {
      html += '<label class="role" style="margin-bottom:10px"><span class="switch"><input type="checkbox" id="ed-prompt"' +
        (!e || e.in_prompt !== false ? ' checked' : '') + '><span class="track"></span></span>' +
        '<span>' + esc(t('f_in_prompt')) + '</span></label>' +
        '<div class="hint" style="margin:-4px 0 14px 47px">' + esc(t('f_in_prompt_hint')) + '</div>';
    }
    html += '<label class="role"><span class="switch"><input type="checkbox" id="ed-active"' +
      (!e || e.is_active !== false ? ' checked' : '') + '><span class="track"></span></span>' +
      '<span>' + esc(t('f_active')) + '</span></label>';

    $('ed-body').innerHTML = html;
    if (kind === 'product') {
      renderAttrs();
      renderMediaPick();
      $('ed-attr-add').addEventListener('click', function () { state.attrs.push({ k: '', v: '' }); renderAttrs(); });
      $('ed-photo-up').addEventListener('click', function () { pickUpload('image/*,video/*', true); });
      if (VF.enhanceSelect) VF.enhanceSelect($('ed-cur'));
    }
    edModal.open();
  }

  function renderAttrs() {
    var box = $('ed-attrs');
    if (!box) return;
    box.innerHTML = state.attrs.map(function (a, i) {
      return '<div class="attr-row">' +
        '<input class="input" data-ak="' + i + '" placeholder="' + esc(t('f_attr_name_ph')) + '" value="' + esc(a.k) + '">' +
        '<input class="input" data-av="' + i + '" placeholder="' + esc(t('f_attr_val_ph')) + '" value="' + esc(a.v) + '">' +
        '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-arm="' + i + '">' + VF.icon('x') + '</button></div>';
    }).join('');
    box.querySelectorAll('[data-ak]').forEach(function (el) {
      el.addEventListener('input', function () { state.attrs[+el.getAttribute('data-ak')].k = el.value; });
    });
    box.querySelectorAll('[data-av]').forEach(function (el) {
      el.addEventListener('input', function () { state.attrs[+el.getAttribute('data-av')].v = el.value; });
    });
    box.querySelectorAll('[data-arm]').forEach(function (el) {
      el.addEventListener('click', function () { state.attrs.splice(+el.getAttribute('data-arm'), 1); renderAttrs(); });
    });
  }

  function renderMediaPick() {
    var box = $('ed-media');
    if (!box) return;
    var pics = state.files.filter(function (f) {
      return /^(image|video)\//.test(f.mime_type || '');
    });
    if (!pics.length) { box.innerHTML = '<div class="hint">' + esc(t('f_photos_empty')) + '</div>'; return; }
    box.innerHTML = pics.map(function (f) {
      var on = state.pickedMedia.indexOf(f.id) >= 0;
      var inner = f.preview_url ? '<img src="' + esc(f.preview_url) + '" alt="" loading="lazy">' : VF.icon(fileIcon(f));
      return '<div class="media-item' + (on ? ' on' : '') + '" data-mid="' + esc(f.id) + '">' + inner +
        '<span class="chk">' + VF.icon('check') + '</span>' +
        '<span class="cap">' + esc(f.title || f.filename || '') + '</span></div>';
    }).join('');
    box.querySelectorAll('[data-mid]').forEach(function (el) {
      el.addEventListener('click', function () {
        var id = el.getAttribute('data-mid');
        var i = state.pickedMedia.indexOf(id);
        if (i >= 0) { state.pickedMedia.splice(i, 1); el.classList.remove('on'); }
        else { state.pickedMedia.push(id); el.classList.add('on'); }
      });
    });
  }

  function saveEntry() {
    var ed = state.editing;
    if (!ed) return;
    var title = ($('ed-t').value || '').trim();
    var bodyText = ($('ed-b').value || '').trim();
    if (!title && !bodyText) { VF.toast(t('need_title'), { type: 'warning' }); return; }

    var payload = {
      kind: ed.kind, title: title, body: bodyText,
      keywords: ($('ed-kw').value || '').trim(),
      is_active: $('ed-active').checked,
    };
    if (ed.kind === 'product') {
      var price = ($('ed-price').value || '').trim();
      payload.price = price === '' ? null : Number(price);
      payload.currency = $('ed-cur').value;
      payload.in_stock = $('ed-stock').checked;
      payload.media_file_ids = state.pickedMedia.slice();
      var attrs = {};
      state.attrs.forEach(function (a) {
        var k = (a.k || '').trim();
        if (k) attrs[k] = (a.v || '').trim();
      });
      payload.attributes = attrs;
      payload.in_prompt = false;  // каталог достаётся поиском, в промпт идёт только прайс
    } else {
      payload.in_prompt = $('ed-prompt').checked;
    }

    var btn = $('ed-save');
    btn.disabled = true;
    var req = ed.id
      ? api(API + '/knowledge/' + encodeURIComponent(ed.id), { method: 'PUT', body: payload })
      : api(API + '/knowledge', { method: 'POST', body: payload });
    req.then(function () {
      // Вопрос закрыт — убираем его из пробелов базы
      if (ed.gapId) return api(API + '/knowledge/gaps/' + encodeURIComponent(ed.gapId), { method: 'DELETE' }).catch(function () {});
    }).then(function () {
      edModal.close();
      VF.toast(t('saved'), { type: 'success' });
      return loadAll();
    }).catch(function (e) {
      VF.toast(e.message, { type: 'error' });
    }).then(function () { btn.disabled = false; });
  }

  function delEntry(id) {
    var e = state.entries.filter(function (x) { return x.id === id; })[0];
    VF.confirm({
      title: t('del_t'), message: t('del_m', { name: (e && e.title) || '' }),
      danger: true, confirmText: 'Удалить',
    }).then(function (ok) {
      if (!ok) return;
      // Документ удаляется своей ручкой: она чистит и векторы в Pinecone
      var url = (e && e.kind === 'document')
        ? API + '/knowledge/document/' + encodeURIComponent(id)
        : API + '/knowledge/' + encodeURIComponent(id);
      api(url, { method: 'DELETE' }).then(function () {
        VF.toast(t('deleted'), { type: 'success' });
        return loadAll();
      }).catch(function (err) { VF.toast(err.message, { type: 'error' }); });
    });
  }

  // ------------------------------------------------------------------ файлы: роли и операции
  function toggleKbRole(input) {
    var fileId = input.getAttribute('data-kb');
    var entryId = input.getAttribute('data-entry');
    input.disabled = true;
    var done = function (msg) {
      VF.toast(msg, { type: 'success' });
      return loadAll();
    };
    var req = input.checked
      ? api(API + '/knowledge/document', { method: 'POST', body: { file_id: fileId } }).then(function () { return done(t('kb_on')); })
      : api(API + '/knowledge/document/' + encodeURIComponent(entryId || ''), { method: 'DELETE' }).then(function () { return done(t('kb_off')); });
    req.catch(function (e) {
      VF.toast(e.message, { type: 'error', duration: 7000 });
      input.checked = !input.checked;
    }).then(function () { input.disabled = false; });
  }

  function downloadFile(id) {
    api(API + '/files/' + encodeURIComponent(id) + '/link').then(function (d) {
      if (d && d.url) window.open(d.url, '_blank', 'noopener');
    }).catch(function (e) { VF.toast(e.message, { type: 'error' }); });
  }

  function delFile(id) {
    var f = state.files.filter(function (x) { return x.id === id; })[0];
    VF.confirm({
      title: t('file_del_t'), message: t('file_del_m', { name: (f && (f.title || f.filename)) || '' }),
      danger: true, confirmText: 'Удалить',
    }).then(function (ok) {
      if (!ok) return;
      api(API + '/files/' + encodeURIComponent(id), { method: 'DELETE' }).then(function () {
        VF.toast(t('deleted'), { type: 'success' });
        return loadAll();
      }).catch(function (e) { VF.toast(e.message, { type: 'error' }); });
    });
  }

  /** Диалог выбора файла. forMedia=true — загруженное сразу отмечается фото позиции. */
  function pickUpload(accept, forMedia) {
    if (!state.storage) { VF.toast(t('storage_off'), { type: 'warning' }); return; }
    var inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = accept || '';
    inp.multiple = !!forMedia;
    inp.addEventListener('change', function () {
      var files = Array.prototype.slice.call(inp.files || []);
      if (!files.length) return;
      uploadNext(files, forMedia);
    });
    inp.click();
  }

  function uploadNext(queue, forMedia) {
    if (!queue.length) {
      return loadAll().then(function () { if (forMedia) renderMediaPick(); });
    }
    var file = queue.shift();
    var fd = new FormData();
    fd.append('file', file);
    return api(API + '/files', { method: 'POST', body: fd }).then(function (d) {
      VF.toast(t('uploaded') + ': ' + (file.name || ''), { type: 'success' });
      if (forMedia && d && d.file && d.file.id) state.pickedMedia.push(d.file.id);
      // Список файлов нужен сразу: из него рисуется выбор фото позиции
      return api(API + '/files?source=library').then(function (r) { state.files = (r && r.files) || []; });
    }).catch(function (e) {
      VF.toast(e.message, { type: 'error', duration: 7000 });
    }).then(function () { return uploadNext(queue, forMedia); });
  }

  // ------------------------------------------------------------------ пробелы
  function hideGap(id) {
    api(API + '/knowledge/gaps/' + encodeURIComponent(id), { method: 'DELETE' }).then(function () {
      state.gaps = state.gaps.filter(function (g) { return g.id !== id; });
      renderTabs(); renderSection();
    }).catch(function (e) { VF.toast(e.message, { type: 'error' }); });
  }

  // ------------------------------------------------------------------ «спросить как клиент»
  function runAsk() {
    var q = ($('ask-input').value || '').trim();
    var out = $('ask-out');
    if (!q) { out.innerHTML = '<div class="hint">' + esc(t('ask_empty')) + '</div>'; return; }
    out.innerHTML = '<div class="hint"><span class="spin" style="border-color:rgba(0,0,0,.15);border-top-color:currentColor"></span></div>';
    api(API + '/knowledge/test', { method: 'POST', body: { query: q } }).then(function (d) {
      var hits = (d && d.results) || [];
      var cls = d && d.answered_from_prompt ? 'fast' : (hits.length ? 'search' : 'none');
      var icon = cls === 'fast' ? 'zap' : (cls === 'search' ? 'search' : 'circle-alert');
      var html = '<div class="ask-verdict ' + cls + '">' + VF.icon(icon) +
        '<span>' + esc(t('ask_' + (cls === 'fast' ? 'fast' : cls === 'search' ? 'search' : 'none'))) + '</span></div>';
      html += hits.slice(0, 3).map(function (h) {
        return '<div class="ask-hit"><b>' + esc(h.title || '') + '</b>' +
          (h.body ? '<div class="faint" style="margin-top:2px">' + esc(String(h.body).slice(0, 160)) + '</div>' : '') +
          '</div>';
      }).join('');
      out.innerHTML = html;
    }).catch(function (e) {
      out.innerHTML = '<div class="note note-warning">' + VF.icon('triangle-alert') + '<span>' + esc(e.message) + '</span></div>';
    });
  }

  // ------------------------------------------------------------------ старт
  document.addEventListener('DOMContentLoaded', function () {
    edModal = VF.modal('ed-modal');
    $('ed-save').addEventListener('click', saveEntry);
    $('btn-add').addEventListener('click', function () {
      if (state.tab === 'files') pickUpload('');
      else openEditor(null);
    });
    $('ask-btn').addEventListener('click', runAsk);
    $('ask-input').addEventListener('keydown', function (ev) { if (ev.key === 'Enter') runAsk(); });
    boot();
  });
})();
