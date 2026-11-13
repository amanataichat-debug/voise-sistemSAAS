/* ============================================================================
 * start/bot.js — Чат-бот тарифа Старт: настройки, база знаний, каналы
 * Страница /static/start-bot.html. Всё, что владелец настраивает у бота, на одной
 * странице кабинета: инструкции, знания, WhatsApp / Telegram / Instagram, входящие
 * звонки, оповещения владельцу, Google Таблица, кредиты. Данные — /api/agent/*.
 * ========================================================================== */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var root, A = null;

  var DOCS = [
    ['doc_who_am_i', 'Кто мы', 'Название, чем занимаетесь, чем вы лучше других.', 'Мы — кофейня «Алатоо» в Бишкеке. Свежая обжарка, завтраки и кофе с собой.'],
    ['doc_who_we_call', 'Для кого работаем', 'Кто ваши клиенты и что им обычно нужно.', 'Жители района и офисные сотрудники. Спрашивают меню, цены, время работы и доставку.'],
    ['doc_how_we_talk', 'Как говорит бот', 'Тон общения, имя бота, обращение на «вы».', 'Дружелюбно и коротко, на «вы». Зовут Айгерим.'],
    ['doc_what_we_offer', 'Что предлагаем', 'Услуги и товары, цены, акции.', 'Кофе от 150 сом, завтраки от 350 сом, доставка по центру бесплатно от 1000 сом.'],
    ['doc_rules_and_goals', 'Правила и цели', 'Чего бот добивается и чего не обещает.', 'Цель — принять заказ или записать на столик. Скидки не обещать, сложные вопросы передавать владельцу.']
  ];

  var SECTIONS = [
    ['s-main', 'bot', 'Основное'], ['s-instr', 'file-text', 'Инструкции'], ['s-kb', 'book-open', 'База знаний'],
    ['s-channels', 'messages-square', 'Каналы'], ['s-voicemsg', 'mic', 'Голосовые сообщения'], ['s-docs', 'receipt', 'Документы бота'], ['s-calls', 'phone', 'Входящие звонки'], ['s-notify', 'bell', 'Оповещения вам'],
    ['s-sheet', 'layers', 'Google Таблица'], ['s-credits', 'coins', 'Кредиты']
  ];

  document.addEventListener('DOMContentLoaded', function () {
    root = document.getElementById('page');
    S.initMobileMenu();
    // Если владелец уже начал править форму, свежие данные из фона её не перерисовывают
    var touched = false;
    root.addEventListener('input', function () { touched = true; }, true);
    root.addEventListener('change', function () { touched = true; }, true);
    S.boot({
      onUpdate: function (a) { if (touched || !A || !a) return; A = a; renderPage(); }
    }).then(function (ok) {
      if (!ok) return;
      if (!S.agent) { createView(); S.ready(); return; }
      A = S.agent;
      renderPage();
      S.ready();
    });
  });

  // Каждый раздел — отдельная вкладка: слева меню, справа один раздел. Адрес #s-kb открывает «База знаний»
  var TAB_ALIAS = { knowledge: 's-kb', channels: 's-channels', instructions: 's-instr', notify: 's-notify' };
  function showSection(id, push) {
    if (!document.getElementById(id)) id = 's-main';
    startSection(id);
    S.$$('.st-bot-main > section').forEach(function (el) { el.classList.toggle('active', el.id === id); });
    S.$$('.st-nav a').forEach(function (a) { a.classList.toggle('active', a.getAttribute('href') === '#' + id); });
    try { if (push !== false) history.replaceState(null, '', '#' + id); } catch (e) { /* ignore */ }
    var top = S.$('.st-bot-layout'); if (top && window.scrollY > top.offsetTop) window.scrollTo(0, 0);
  }
  // #catalog (вкладка «Каталог» нижней панели на телефоне) — База знаний сразу на каталоге,
  // без меню разделов: продавец с телефона пришёл править товары и остатки
  function routeHash(h) {
    var cat = h === 'catalog' || h === 'products';
    document.body.classList.toggle('st-catmode', cat);
    if (cat) {
      if (kb.tab !== 'product') { kb.tab = 'product'; if (S.$('#kb-body')) drawKb(); }
      showSection('s-kb', false);
      return;
    }
    showSection(TAB_ALIAS[h] || h || 's-main', false);
  }
  function setupScroll() {
    S.$$('.st-nav a').forEach(function (a) {
      a.addEventListener('click', function (e) { e.preventDefault(); document.body.classList.remove('st-catmode'); showSection(a.getAttribute('href').slice(1)); });
    });
    routeHash((location.hash || '').replace('#', ''));
    // Возврат после подключения Instagram/Gmail/Календаря во вкладке (телефон):
    // показать итог и убрать параметры из адреса.
    try {
      var qp = new URLSearchParams(location.search);
      if (qp.get('connector')) {
        var okc = qp.get('status') === 'success';
        setTimeout(function () { S.toast(okc ? 'Подключено' : 'Подключение не подтвердилось. Откройте «Каналы»: если аккаунт подключился, статус обновится сам', okc ? 'success' : 'error'); }, 300);
        history.replaceState(null, '', location.pathname + location.hash);
      }
    } catch (e) {}
    window.addEventListener('hashchange', function () {
      routeHash((location.hash || '').replace('#', ''));
    });
  }

  // ══════════════════════════════ СОЗДАНИЕ БОТА ════════════════════════════
  function createView() {
    root.innerHTML =
      '<div class="st-head"><div><h2>Создайте чат-бота</h2><p>Бот будет отвечать клиентам в WhatsApp, Instagram и Telegram и принимать входящие звонки. ' +
      'Расскажите о бизнесе: это можно будет поправить в любой момент.</p></div></div>' +
      '<div class="card"><div class="card-body" style="max-width:760px">' +
      '<div class="st-grid2"><div class="field"><label class="label">Имя бота *</label><input class="input" id="c-name" placeholder="Айгерим" maxlength="80"></div>' +
      '<div class="field"><label class="label">Язык разговора по телефону</label><select class="select-native" id="c-lang"><option value="ru">Русский</option><option value="ky">Кыргызский</option></select></div></div>' +
      '<div class="field"><label class="label">Голос по телефону</label><select class="select-native" id="c-voice"><option value="">Загрузка голосов…</option></select>' +
      '<div class="hint">Переписка не зависит от голоса: он нужен только для входящих звонков.</div></div>' +
      DOCS.map(function (d) {
        return '<div class="field"><label class="label">' + esc(d[1]) + ' *</label><textarea class="textarea" id="c-' + d[0] + '" rows="3" placeholder="' + esc(d[3]) + '"></textarea><div class="hint">' + esc(d[2]) + '</div></div>';
      }).join('') +
      '<button class="btn btn-primary btn-lg" id="c-go" type="button">Создать бота</button></div></div>';

    function loadVoices() {
      var lang = S.$('#c-lang').value, sel = S.$('#c-voice');
      sel.innerHTML = '<option value="">Загрузка голосов…</option>';
      S.api('/api/eleven-assistants/voices?language=' + lang).then(function (r) {
        var v = (r.ok && r.data.voices) || [];
        if (!v.length) { sel.innerHTML = '<option value="">Голос по умолчанию</option>'; return; }
        sel.innerHTML = v.slice(0, 60).map(function (x) {
          return '<option value="' + esc(x.voice_id) + '" data-name="' + esc(x.name) + '">' + esc(x.name) + (x.recommended ? ' — подходит для языка' : '') + '</option>';
        }).join('');
      });
    }
    S.$('#c-lang').addEventListener('change', loadVoices);
    loadVoices();

    S.$('#c-go').addEventListener('click', function () {
      var body = { name: S.$('#c-name').value.trim(), assistant_type: 'eleven', eleven_language: S.$('#c-lang').value };
      if (!body.name) { S.toast('Укажите имя бота', 'error'); return; }
      for (var i = 0; i < DOCS.length; i++) {
        var v = S.$('#c-' + DOCS[i][0]).value.trim();
        if (!v) { S.toast('Заполните поле «' + DOCS[i][1] + '»', 'error'); S.$('#c-' + DOCS[i][0]).focus(); return; }
        body[DOCS[i][0]] = v;
      }
      var opt = S.$('#c-voice').selectedOptions[0];
      if (opt && opt.value) { body.eleven_voice_id = opt.value; body.eleven_voice_name = opt.dataset.name || null; }
      var btn = this; btn.disabled = true; btn.textContent = 'Создаю…';
      S.api('/create', { method: 'POST', body: body }).then(function (r) {
        if (!r.ok) { btn.disabled = false; btn.textContent = 'Создать бота'; S.toast(S.apiError(r), 'error'); return; }
        location.reload();
      });
    });
  }

  // ══════════════════════════════ КАРКАС СТРАНИЦЫ ══════════════════════════
  function sec(id, title, sub, right, bodyId) {
    return '<section class="card" id="' + id + '"><div class="card-head"><div><h2>' + esc(title) + '</h2>' +
      (sub ? '<p class="muted">' + sub + '</p>' : '') + '</div>' + (right ? '<div>' + right + '</div>' : '') + '</div>' +
      '<div class="card-body" id="' + bodyId + '"><div class="skeleton" style="height:90px"></div></div></section>';
  }

  function renderPage() {
    root.innerHTML =
      '<div class="st-head"><div><h2>Чат-бот</h2><p>Всё, что нужно боту, чтобы отвечать вашим клиентам.</p></div></div>' +
      '<div class="st-bot-layout"><nav class="st-nav" aria-label="Разделы">' + SECTIONS.map(function (s) {
        return '<a href="#' + s[0] + '">' + icon(s[1]) + '<span>' + s[2] + '</span></a>';
      }).join('') + '</nav><div class="st-bot-main">' +
      sec('s-main', 'Основное', '', '', 'b-main') +
      sec('s-instr', 'Инструкции для бота', 'Расскажите о бизнесе. На этом бот строит каждый ответ в переписке и по телефону.', '', 'b-instr') +
      sec('s-kb', 'База знаний', 'Точные ответы, цены и каталог. Бот берёт факты отсюда и не выдумывает.', '', 'b-kb') +
      sec('s-channels', 'Каналы', 'Где бот общается с клиентами. Подключите нужные.', '', 'b-channels') +
      sec('s-voicemsg', 'Голосовые сообщения в переписке', 'Бот может отвечать голосом в WhatsApp и Telegram. Выберите, чем озвучивать и как часто.', '', 'b-voicemsg') +
      sec('s-docs', 'Документы бота', 'Бот сам собирает коммерческие предложения, счета и памятки в PDF и отправляет клиенту. Здесь задаётся, как они выглядят.', '', 'b-docs') +
      sec('s-calls', 'Входящие звонки', 'Номер, на который клиенты звонят боту, и первая фраза.', '', 'b-calls') +
      sec('s-notify', 'Оповещения вам', 'Бот напишет вам в Telegram, когда придёт новый клиент или закончится звонок.', '', 'b-notify') +
      sec('s-sheet', 'Google Таблица', 'Заявки бот записывает на лист «Заявки», а прайс можно загрузить из таблицы в каталог.', '', 'b-sheet') +
      sec('s-credits', 'Кредиты бота', 'Расход на работу бота: ответы, разбор звонков, фото и голосовые клиентов.', '', 'b-credits') +
      '</div></div>';
    fillMain(); fillInstr();
    started = {};
    setupScroll();     // откроет нужную вкладку и запустит её загрузку
  }

  // Данные раздела грузятся, когда его впервые открыли. Раньше страница сразу запрашивала всё
  // (около 20 запросов), хотя виден один раздел.
  var started = {};
  function startSection(id) {
    if (started[id]) return;
    started[id] = true;
    var run = {
      's-kb': loadKb, 's-channels': loadChannels, 's-voicemsg': loadVoiceMsg, 's-docs': loadDocs,
      's-calls': loadCalls, 's-notify': loadNotify, 's-sheet': loadSheet, 's-credits': loadCredits
    }[id];
    if (run) run();
  }

  function put(body) { return S.api('/', { method: 'PUT', body: body }); }

  // ══════════════════════════════ ОСНОВНОЕ ═════════════════════════════════
  function fillMain() {
    var b = S.$('#b-main');
    var voice = A.eleven_voice_engine === 'gpt_live' ? 'Voksy Live · ' + (A.eleven_live_voice || 'marin')
      : A.eleven_voice_engine === 'gemini_live' ? 'Voksy Live Go · ' + (A.eleven_live_voice || 'aoede') : (A.eleven_voice_name || 'Voksy Voice');
    b.innerHTML =
      '<div class="st-bot-hero"><span class="st-avatar lg">' + esc(S.initial(A.name)) + '</span>' +
      '<div class="grow"><label class="label" for="m-name">Имя бота</label><input class="input" id="m-name" value="' + esc(A.name || '') + '" maxlength="80"></div>' +
      '<button class="btn btn-primary" id="m-save" type="button">Сохранить</button></div>' +
      '<div class="st-switch-row" style="margin-top:14px"><div class="t"><b>Бот отвечает клиентам</b><div>Если выключить, бот перестанет отвечать в мессенджерах и по телефону. Данные и клиенты сохранятся.</div></div>' +
      '<label class="switch"><input type="checkbox" id="m-active"' + (A.is_active ? ' checked' : '') + '><span class="track"></span></label></div>' +
      '<div class="st-sub-h" style="margin-top:20px">Голос по телефону</div>' +
      '<div class="st-note" style="margin:0 0 10px">Сейчас: <b>' + esc(voice) + '</b>. Голос нужен только для входящих звонков, на переписку он не влияет.</div>' +
      '<div id="voice-box"><div class="skeleton" style="height:60px"></div></div>' +
      '<div class="st-sub-h" style="margin-top:20px">Модель бота</div>' +
      '<div class="st-note" style="margin:0 0 10px">Языковая модель, на которой бот думает и пишет ответы. Чем сильнее модель, тем больше кредитов уходит на ответ.</div>' +
      '<div id="model-box"><div class="skeleton" style="height:50px"></div></div>';
    S.$('#m-save').addEventListener('click', function () {
      var name = S.$('#m-name').value.trim();
      if (!name) { S.toast('Укажите имя бота', 'error'); return; }
      put({ name: name }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        A.name = name; S.toast('Сохранено', 'success');
      });
    });
    loadVoiceBox(); loadModelBox();
    S.$('#m-active').addEventListener('change', function () {
      var on = this.checked, el = this;
      put({ is_active: on }).then(function (r) {
        if (!r.ok) { el.checked = !on; S.toast(S.apiError(r), 'error'); return; }
        A.is_active = on; S.toast(on ? 'Бот включён' : 'Бот выключен', 'success');
      });
    });
  }

  // ── Голос по телефону: движок, язык, голос ──────────────────────────────
  var voiceOpts = null;
  function loadVoiceBox() {
    S.api('/api/eleven-assistants/options').then(function (r) {
      voiceOpts = r.ok ? r.data : null;
      drawVoiceBox();
    });
  }

  function isLiveEngine(id) { return id === 'gpt_live' || id === 'gemini_live'; }
  function drawVoiceBox() {
    var box = S.$('#voice-box');
    if (!box) return;
    if (!voiceOpts) { box.innerHTML = '<div class="muted small">Не удалось загрузить список голосов.</div>'; return; }
    var engine = isLiveEngine(A.eleven_voice_engine) ? A.eleven_voice_engine : 'eleven';
    var engines = voiceOpts.voice_engines || [];
    box.innerHTML =
      '<div class="st-grid2"><div class="field"><label class="label">Модель голоса</label><select class="select-native" id="v-engine">' +
      engines.map(function (e) {
        return '<option value="' + esc(e.id) + '"' + (e.id === engine ? ' selected' : '') + '>' + esc(e.title) + (e.som_per_minute ? ' · ' + e.som_per_minute + ' сом/мин' : '') + '</option>';
      }).join('') + '</select></div>' +
      '<div class="field" id="v-lang-wrap"><label class="label">Язык</label><select class="select-native" id="v-lang">' +
      (voiceOpts.languages || []).map(function (l) {
        return '<option value="' + esc(l.code) + '"' + (l.code === (A.eleven_language || 'ky') ? ' selected' : '') + '>' + esc(l.title) + '</option>';
      }).join('') + '</select></div></div>' +
      '<div class="field" id="v-eleven-wrap"><label class="label">Голос</label><div class="row" style="gap:8px;flex-wrap:nowrap"><select class="select-native" id="v-voice"><option>Загрузка…</option></select>' +
      '<button class="btn" id="v-play" type="button" aria-label="Прослушать">' + icon('play') + '</button></div><div class="hint">Голоса, подходящие для выбранного языка, показаны первыми.</div></div>' +
      '<div class="field" id="v-live-wrap"><label class="label" id="v-live-label">Голос Voksy Live</label><select class="select-native" id="v-live"></select></div>' +
      '<button class="btn btn-primary" id="v-save" type="button">Сохранить голос</button>';
    var voices = [];
    // Голос «речь в речь» лежит в одной колонке на оба движка: список и подпись зависят от движка
    function fillLive() {
      var eng = S.$('#v-engine').value, gem = eng === 'gemini_live';
      var list = gem ? (voiceOpts.gemini_voices || ['Aoede']) : (voiceOpts.live_voices || []);
      var prev = String(S.$('#v-live').value || A.eleven_live_voice || '').toLowerCase();
      var def = gem ? String(voiceOpts.default_gemini_voice || list[0]).toLowerCase() : voiceOpts.default_live_voice;
      var values = list.map(function (v) { return gem ? String(v).toLowerCase() : v; });
      var cur = values.indexOf(prev) !== -1 ? prev : def;
      S.$('#v-live').innerHTML = list.map(function (v, i) {
        return '<option value="' + esc(values[i]) + '"' + (values[i] === cur ? ' selected' : '') + '>' + esc(v) + '</option>';
      }).join('');
      S.$('#v-live-label').textContent = gem ? 'Голос Voksy Live Go' : 'Голос Voksy Live';
    }
    function toggle() {
      var live = isLiveEngine(S.$('#v-engine').value);
      S.$('#v-eleven-wrap').style.display = live ? 'none' : '';
      S.$('#v-lang-wrap').style.display = live ? 'none' : '';
      S.$('#v-live-wrap').style.display = live ? '' : 'none';
      if (live) fillLive();
    }
    function loadVoices() {
      var sel = S.$('#v-voice'); sel.innerHTML = '<option>Загрузка…</option>';
      S.api('/api/eleven-assistants/voices?language=' + S.$('#v-lang').value).then(function (r) {
        voices = (r.ok && r.data.voices) || [];
        if (!voices.length) { sel.innerHTML = '<option value="">Голос по умолчанию</option>'; return; }
        sel.innerHTML = voices.slice(0, 100).map(function (x) {
          return '<option value="' + esc(x.voice_id) + '"' + (x.voice_id === A.eleven_voice_id ? ' selected' : '') + '>' + esc(x.name) + (x.recommended ? ' — подходит для языка' : '') + '</option>';
        }).join('');
      });
    }
    S.$('#v-engine').addEventListener('change', toggle);
    S.$('#v-lang').addEventListener('change', loadVoices);
    var audio = null;
    S.$('#v-play').addEventListener('click', function () {
      var v = voices.filter(function (x) { return x.voice_id === S.$('#v-voice').value; })[0];
      if (!v || !v.preview_url) { S.toast('У этого голоса нет образца', 'info'); return; }
      if (audio) audio.pause();
      audio = new Audio(v.preview_url); audio.play().catch(function () { S.toast('Не удалось воспроизвести', 'error'); });
    });
    S.$('#v-save').addEventListener('click', function () {
      var eng = S.$('#v-engine').value, live = isLiveEngine(eng);
      var body = { eleven_voice_engine: live ? eng : 'eleven' };
      if (live) body.eleven_live_voice = S.$('#v-live').value;
      else {
        var opt = S.$('#v-voice').selectedOptions[0];
        body.eleven_language = S.$('#v-lang').value;
        if (opt && opt.value) { body.eleven_voice_id = opt.value; body.eleven_voice_name = (opt.textContent || '').replace(' — подходит для языка', ''); }
      }
      var btn = this; btn.disabled = true;
      put(body).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        Object.keys(body).forEach(function (k) { A[k] = body[k]; });
        S.toast('Голос сохранён', 'success'); fillMain();
      });
    });
    toggle(); loadVoices();
  }

  // ── Модель бота (оркестратор) ───────────────────────────────────────────
  function loadModelBox() {
    S.api('/orchestrator-models').then(function (r) {
      var box = S.$('#model-box'); if (!box) return;
      var list = (r.ok && r.data.models) || [];
      if (!list.length) { box.innerHTML = '<div class="muted small">Не удалось загрузить список моделей.</div>'; return; }
      var cur = A.orchestrator_model || r.data.default;
      var curM = list.filter(function (m) { return m.slug === cur; })[0];
      box.innerHTML = '<div class="field" style="margin-bottom:6px"><select class="select-native" id="mdl">' + list.map(function (m) {
        return '<option value="' + esc(m.slug) + '"' + (m.slug === cur ? ' selected' : '') + '>' + esc(m.name) + (m.credits_per_call ? ' · ≈' + m.credits_per_call + ' кредитов за ответ' : '') + '</option>';
      }).join('') + '</select></div><div class="hint" id="mdl-d">' + esc(curM ? curM.description || '' : '') + '</div>';
      S.$('#mdl').addEventListener('change', function () {
        var sel = this, slug = sel.value;
        var m = list.filter(function (x) { return x.slug === slug; })[0];
        S.$('#mdl-d').textContent = m ? m.description || '' : '';
        put({ orchestrator_model: slug }).then(function (x) {
          if (!x.ok) { sel.value = A.orchestrator_model || r.data.default; S.toast(S.apiError(x), 'error'); return; }
          A.orchestrator_model = slug; S.toast('Модель бота изменена', 'success');
        });
      });
    });
  }

  // ══════════════════════════════ ИНСТРУКЦИИ ═══════════════════════════════
  var TPL_CHAT = 'Роль: ты менеджер компании. Отвечаешь клиентам в WhatsApp, Instagram и Telegram.\n\n' +
    'Как общаться: коротко, дружелюбно, на «вы». Одно-два предложения, без длинных списков. Отвечай на языке клиента.\n\n' +
    'Что делать: сначала выясни, что нужно клиенту. Предложи подходящий товар или услугу и назови цену из базы знаний. Когда клиент готов, уточни имя и телефон и запиши заявку.\n\n' +
    'Файлы и фото: когда клиент просит прайс, каталог или фото товара, отправь нужный файл из библиотеки и добавь одну короткую фразу. Договор и счёт отправляй только по просьбе клиента.\n\n' +
    'Если не знаешь ответ: не выдумывай. Скажи, что уточнишь, и передай клиента владельцу.\n\n' +
    'Когда передавать владельцу: клиент недоволен, просит скидку, хочет встретиться или говорит о крупном заказе.';
  var TPL_VOICE = 'Говори спокойно и дружелюбно, не торопись. Сначала поздоровайся и назови компанию. Задавай по одному вопросу за раз. ' +
    'Цены и адреса называй медленно. Если клиент просит прислать подробности, скажи, что отправишь в WhatsApp. ' +
    'Если не уверен в ответе, не выдумывай, скажи, что уточнишь, и предложи перезвонить.';

  function fillInstr() {
    var b = S.$('#b-instr');
    b.innerHTML =
      '<div class="note" style="margin-bottom:18px"><div>Здесь вы объясняете боту, как работать. <b>Главная инструкция</b> действует в переписке (WhatsApp, Telegram, Instagram, почта) и при разборе звонков. ' +
      '<b>Инструкция для звонков</b> действует, когда клиент звонит боту. Ниже ещё блок «О бизнесе» с фактами о компании.</div></div>' +

      '<div class="st-sub-h">Главная инструкция бота (переписка)</div>' +
      '<div class="field"><textarea class="textarea st-textarea-lg" id="i-add" rows="10" placeholder="Как общаться, когда отправлять файлы, что делать, если не знает ответ, когда передавать вам клиента">' + esc(A.additional_instructions || '') + '</textarea>' +
      '<div class="hint">Пишите так, как объясняли бы новому сотруднику: как вежливо общаться, в какой момент отправлять файлы и фото, что узнавать у клиента, когда звать вас. Чем конкретнее, тем лучше ответы.</div></div>' +
      '<div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn btn-primary" id="i-save-chat" type="button">Сохранить</button><button class="btn" id="i-gen-chat" type="button">' + icon('wand-2') + '<span>Сгенерировать по описанию</span></button><button class="btn" id="i-fix-chat" type="button"><span>Добавить правку</span></button><button class="btn" id="i-tpl-chat" type="button"><span>Вставить шаблон</span></button></div>' +

      '<div class="st-sub-h" style="margin-top:26px">Инструкция для звонков</div>' +
      '<div class="field"><textarea class="textarea" id="i-vadd" rows="6" placeholder="Стиль общения по телефону, манера, что говорить и чего не говорить">' + esc(A.voice_additional_instructions || '') + '</textarea>' +
      '<div class="hint">Как бот звучит и ведёт себя в телефонном разговоре: темп, тон, что уточнять, как заканчивать звонок. Знания из базы бот берёт сам.</div></div>' +
      '<div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn btn-primary" id="i-save-voice" type="button">Сохранить</button><button class="btn" id="i-gen-voice" type="button">' + icon('wand-2') + '<span>Сгенерировать по описанию</span></button><button class="btn" id="i-fix-voice" type="button"><span>Добавить правку</span></button><button class="btn" id="i-tpl-voice" type="button"><span>Вставить шаблон</span></button></div>' +

      '<div class="st-sub-h" style="margin-top:26px">О бизнесе</div>' +
      DOCS.map(function (d) {
        return '<div class="field"><label class="label" for="i-' + d[0] + '">' + esc(d[1]) + '</label><textarea class="textarea" id="i-' + d[0] + '" rows="3" placeholder="' + esc(d[3]) + '">' + esc(A[d[0]] || '') + '</textarea><div class="hint">' + esc(d[2]) + '</div></div>';
      }).join('') +
      '<button class="btn btn-primary" id="i-save-docs" type="button">Сохранить «О бизнесе»</button>';

    function saveBody(body, btn, msg) {
      btn.disabled = true;
      put(body).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        Object.keys(body).forEach(function (k) { A[k] = body[k]; });
        S.toast(msg, 'success');
      });
    }
    function tpl(id, text, kind) {
      var el = S.$(id);
      // Общие шаблоны кабинетов Старта и Profi (js/instruction-templates.js); нет файла — прежние короткие
      if (window.VoksyInstructionTemplates) { window.VoksyInstructionTemplates.insert(el, kind); return; }
      if (el.value.trim() && !window.confirm('Заменить текст шаблоном?')) return;
      el.value = text; el.focus();
    }
    // «Сгенерировать по описанию» / «Добавить правку» (js/instruction-builder.js): сервер пишет текст, сохраняем здесь
    function builder(mode, kind, sel, field) {
      if (!window.VoksyInstructionBuilder) { S.toast('Обновите страницу', 'error'); return; }
      window.VoksyInstructionBuilder.open({
        mode: mode, kind: kind,
        request: function (path, body) { return S.api(path, { method: 'POST', body: body }); },
        getCurrent: function () { return S.$(sel).value; },
        onApply: function (text) {
          var body = {}; body[field] = text;
          return put(body).then(function (r) {
            if (!r.ok) { S.toast(S.apiError(r), 'error'); return false; }
            A[field] = text; S.$(sel).value = text;
            S.toast(kind === 'voice' ? 'Инструкция для звонков сохранена' : 'Инструкция для переписки сохранена', 'success');
            return true;
          });
        }
      });
    }
    S.$('#i-gen-chat').addEventListener('click', function () { builder('generate', 'chat', '#i-add', 'additional_instructions'); });
    S.$('#i-fix-chat').addEventListener('click', function () { builder('refine', 'chat', '#i-add', 'additional_instructions'); });
    S.$('#i-gen-voice').addEventListener('click', function () { builder('generate', 'voice', '#i-vadd', 'voice_additional_instructions'); });
    S.$('#i-fix-voice').addEventListener('click', function () { builder('refine', 'voice', '#i-vadd', 'voice_additional_instructions'); });
    S.$('#i-tpl-chat').addEventListener('click', function () { tpl('#i-add', TPL_CHAT, 'chat'); });
    S.$('#i-tpl-voice').addEventListener('click', function () { tpl('#i-vadd', TPL_VOICE, 'voice'); });
    function filled(text) {
      var T = window.VoksyInstructionTemplates;
      return !T || T.confirmFilled([text]);
    }
    S.$('#i-save-chat').addEventListener('click', function () {
      if (!filled(S.$('#i-add').value)) return;
      saveBody({ additional_instructions: S.$('#i-add').value }, this, 'Инструкция для переписки сохранена');
    });
    S.$('#i-save-voice').addEventListener('click', function () {
      if (!filled(S.$('#i-vadd').value)) return;
      saveBody({ voice_additional_instructions: S.$('#i-vadd').value }, this, 'Инструкция для звонков сохранена');
    });
    S.$('#i-save-docs').addEventListener('click', function () {
      var body = {};
      for (var i = 0; i < DOCS.length; i++) {
        var v = S.$('#i-' + DOCS[i][0]).value.trim();
        if (!v) { S.toast('Поле «' + DOCS[i][1] + '» не должно быть пустым', 'error'); return; }
        body[DOCS[i][0]] = v;
      }
      saveBody(body, this, 'Сохранено');
    });
  }

  // ══════════════════════════════ БАЗА ЗНАНИЙ ══════════════════════════════
  // Всё, что у агента Profi в «Знаниях»: вопрос-ответ, о компании, каталог с фото и характеристиками,
  // файлы с двумя ролями («бот отправляет клиентам» и «бот ищет ответы внутри»), пробелы.
  var kb = { entries: [], counts: {}, voice: null, gaps: [], files: [], generated: [], storage: true, tab: 'qa' };
  var KB_TABS = [['qa', 'Вопросы и ответы'], ['section', 'О компании'], ['product', 'Каталог'], ['files', 'Файлы'], ['gaps', 'Без ответа']];
  var CURRENCIES = [['KGS', 'сом'], ['USD', 'долл.'], ['RUB', 'руб.'], ['KZT', 'тенге'], ['EUR', 'евро']];

  function loadKb() {
    return Promise.all([S.api('/knowledge'), S.api('/knowledge/gaps'), S.api('/files?source=library'), S.api('/files?source=generated')]).then(function (res) {
      var b = S.$('#b-kb');
      if (!res[0].ok) { b.innerHTML = '<div class="st-empty">' + esc(S.apiError(res[0])) + '</div>'; return; }
      kb.entries = res[0].data.entries || []; kb.counts = res[0].data.counts || {}; kb.voice = res[0].data.voice || null;
      kb.collections = res[0].data.collections || [];
      kb.gaps = res[1].ok ? (res[1].data.gaps || []) : [];
      kb.files = res[2].ok ? (res[2].data.files || []) : [];
      kb.storage = res[2].ok ? res[2].data.storage_configured !== false : true;
      kb.generated = res[3].ok ? (res[3].data.files || []) : [];
      drawKb();
    });
  }

  function curWord(c) {
    var f = CURRENCIES.filter(function (x) { return x[0] === (c || 'KGS'); })[0];
    return f ? f[1] : c;
  }
  function priceText(e) { return e.price != null && e.price !== '' ? Number(e.price).toLocaleString('ru-RU') + ' ' + curWord(e.currency) : 'цена не указана'; }

  function drawKb() {
    var b = S.$('#b-kb');
    var counts = { qa: kb.counts.qa || 0, section: kb.counts.section || 0, product: kb.counts.product || 0, files: kb.files.length, gaps: kb.gaps.length };
    var v = kb.voice && kb.voice.chars ? '<div class="st-note st-kbvoice" style="margin:0 0 12px">Бот знает наизусть на звонке: ' + Number(kb.voice.chars).toLocaleString('ru-RU') + ' из ' + Number(kb.voice.budget || 0).toLocaleString('ru-RU') + ' символов. Остальное он находит поиском, это чуть дольше.</div>' : '';
    b.innerHTML =
      '<div class="tabs st-kbtabs" style="margin:-6px -8px 16px">' + KB_TABS.map(function (t) {
        return '<div class="tab' + (kb.tab === t[0] ? ' active' : '') + '" data-kt="' + t[0] + '"><span>' + t[1] + '</span><span class="st-count" style="margin-left:2px">' + counts[t[0]] + '</span></div>';
      }).join('') + '</div>' + v + '<div id="kb-body"></div>' +
      '<div class="st-kbask"><div class="st-sub-h" style="margin-top:22px;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">' +
      '<span>Проверить как клиент</span><button class="btn" id="kb-health" type="button">' + icon('circle-check') + '<span>Проверить базу знаний</span></button></div>' +
      '<div class="st-toolbar"><div class="st-search" style="max-width:none">' + icon('search') + '<input class="input" id="kb-q" placeholder="Задайте вопрос, как клиент: «сколько стоит доставка?»"></div>' +
      '<button class="btn" id="kb-ask" type="button">Спросить</button></div><div id="kb-ans"></div></div>';
    S.$$('[data-kt]', b).forEach(function (t) {
      t.addEventListener('click', function () { kb.tab = t.dataset.kt; drawKb(); });
    });
    drawKbBody();
    // «Проверить базу знаний» (js/knowledge-health.js): находит ли бот каждую запись так же, как на звонке
    S.$('#kb-health').addEventListener('click', function () {
      if (!window.VoksyKnowledgeHealth) { S.toast('Обновите страницу', 'error'); return; }
      window.VoksyKnowledgeHealth.open({ request: function () { return S.api('/knowledge/health'); } });
    });
    var ask = function () {
      var q = S.$('#kb-q').value.trim(); if (!q) return;
      S.$('#kb-ans').innerHTML = '<div class="skeleton" style="height:60px;margin-top:10px"></div>';
      S.api('/knowledge/test', { method: 'POST', body: { query: q } }).then(function (r) {
        var box = S.$('#kb-ans');
        if (!r.ok) { box.innerHTML = '<div class="note note-danger" style="margin-top:10px">' + esc(S.apiError(r)) + '</div>'; return; }
        box.innerHTML = '<div class="note" style="margin-top:10px">' + esc(r.data.note || '') + '</div>' +
          (r.data.results || []).map(function (x) {
            return '<div class="st-qa"><div class="q">' + esc(x.title || '') + '</div><div class="a">' + esc(x.body || '') + '</div></div>';
          }).join('');
      });
    };
    S.$('#kb-ask').addEventListener('click', ask);
    S.$('#kb-q').addEventListener('keydown', function (e) { if (e.key === 'Enter') ask(); });
  }

  function drawKbBody() {
    var box = S.$('#kb-body');
    if (kb.tab === 'gaps') { drawGaps(box); return; }
    if (kb.tab === 'files') { drawFiles(box); return; }
    var items = kb.entries.filter(function (e) { return e.kind === kb.tab; });
    var addLabel = { qa: 'Добавить вопрос и ответ', section: 'Добавить раздел', product: 'Добавить позицию' }[kb.tab];
    var hint = {
      qa: 'Частые вопросы клиентов и ваши ответы. Бот отвечает на них сразу, без поиска.',
      section: 'Короткие блоки о компании: адрес, режим работы, условия доставки и оплаты.',
      product: 'Услуги и товары с ценой, характеристиками и фото. Бот называет цену и наличие и может отправить фото клиенту.'
    }[kb.tab];
    var head = '<div class="row-between" style="margin-bottom:12px;gap:12px;flex-wrap:wrap"><span class="muted small" style="max-width:560px">' + hint + '</span>' +
      '<button class="btn btn-primary btn-sm" id="kb-add" type="button">' + icon('plus') + '<span>' + addLabel + '</span></button></div>';
    if (kb.tab === 'product') { drawCatalog(box); return; }
    {
      box.innerHTML = head + (items.length ? items.map(function (e) {
        return '<div class="st-qa' + (e.is_active === false ? ' off' : '') + '" data-id="' + esc(e.id) + '"><div class="q">' + esc(e.title || '—') + '</div>' +
          (e.body ? '<div class="a">' + esc(e.body) + '</div>' : '') +
          '<div class="acts"><button class="btn btn-ghost btn-sm" data-edit="' + esc(e.id) + '" type="button">' + icon('pen') + '<span>Изменить</span></button>' +
          '<button class="btn btn-ghost btn-sm" data-del="' + esc(e.id) + '" type="button">' + icon('trash-2') + '<span>Удалить</span></button></div></div>';
      }).join('') : '<div class="st-empty">Пока пусто.</div>');
    }
    S.$('#kb-add').addEventListener('click', function () { entryDialog(kb.tab, null); });
    S.$$('[data-view]', box).forEach(function (el) {
      el.addEventListener('click', function () { mediaViewer(kb.entries.filter(function (e) { return e.id === el.dataset.view; })[0]); });
    });
    S.$$('[data-edit]', box).forEach(function (b) {
      b.addEventListener('click', function () { entryDialog(kb.tab, kb.entries.filter(function (e) { return e.id === b.dataset.edit; })[0]); });
    });
    S.$$('[data-del]', box).forEach(function (b) {
      b.addEventListener('click', function () {
        S.confirm({ title: 'Удалить запись?', message: 'Бот больше не будет использовать её в ответах.', confirmText: 'Удалить', danger: true }).then(function (ok) {
          if (!ok) return;
          S.api('/knowledge/' + b.dataset.del, { method: 'DELETE' }).then(function (r) {
            if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
            loadKb();
          });
        });
      });
    });
  }

  // ── Запись базы знаний: вопрос-ответ / раздел / позиция каталога ──
  function entryDialog(kind, e, prefill, gapId) {
    if (kind === 'product') { productDialog(e, prefill, gapId); return; }
    var src = e || prefill || null;
    var titles = { qa: ['Вопрос клиента', 'Ответ бота'], section: ['Название раздела', 'Текст'], product: ['Название', 'Описание'] }[kind];
    var picked = (e && e.media_file_ids) ? e.media_file_ids.slice() : [];
    var attrs = Object.keys((e && e.attributes) || {}).map(function (k) { return { k: k, v: String(e.attributes[k]) }; });
    var isProd = kind === 'product';
    var d = S.dialog({
      title: (e ? 'Изменить' : 'Добавить') + ': ' + { qa: 'вопрос и ответ', section: 'раздел', product: 'позицию' }[kind], size: 'lg',
      body: '<div class="field"><label class="label">' + titles[0] + ' *</label><input class="input" id="e-title" value="' + esc(src ? src.title : '') + '"></div>' +
        (isProd
          ? '<div class="st-grid3"><div class="field"><label class="label">Цена</label><input class="input" id="e-price" type="number" min="0" step="0.01" value="' + esc(e && e.price != null ? e.price : '') + '"></div>' +
            '<div class="field"><label class="label">Валюта</label><select class="select-native" id="e-cur">' + CURRENCIES.map(function (c) { return '<option value="' + c[0] + '"' + ((e && e.currency === c[0]) ? ' selected' : '') + '>' + c[1] + '</option>'; }).join('') + '</select></div>' +
            '<div class="field"><label class="label">Наличие</label><select class="select-native" id="e-stock"><option value="1">Есть в наличии</option><option value="0"' + (e && e.in_stock === false ? ' selected' : '') + '>Нет в наличии</option></select></div></div>'
          : '') +
        '<div class="field"><label class="label">' + titles[1] + '</label><textarea class="textarea" id="e-body" rows="4">' + esc(src ? src.body : '') + '</textarea></div>' +
        (isProd ? '<div class="field"><label class="label">Характеристики</label><div id="e-attrs"></div><button class="btn btn-ghost btn-sm" type="button" id="e-attr-add">' + icon('plus') + '<span>Добавить характеристику</span></button></div>' +
          '<div class="field"><label class="label">Фото и видео</label><div class="hint" style="margin-bottom:8px">Отмеченные фото и видео бот отправит клиенту, когда спросят про эту позицию. Можно загрузить сразу несколько файлов (каждый до 20 МБ).</div><div id="e-media"></div>' +
          '<div style="margin-top:8px"><button class="btn btn-ghost btn-sm" type="button" id="e-up">' + icon('upload') + '<span>Загрузить фото или видео</span></button></div></div>' : '') +
        '<div class="field"><label class="label">Ключевые слова</label><input class="input" id="e-kw" value="' + esc(src ? src.keywords || '' : '') + '" placeholder="через запятую: доставка, курьер">' +
        '<div class="hint">По ним бот находит запись, если клиент спросил другими словами.</div></div>' +
        (!isProd ? '<div class="st-switch-row"><div class="t"><b>Знать наизусть</b><div>Бот отвечает сразу, без поиска. Подходит для частых вопросов.</div></div><label class="switch"><input type="checkbox" id="e-prompt"' + (!e || e.in_prompt !== false ? ' checked' : '') + '><span class="track"></span></label></div>' : '') +
        '<div class="st-switch-row"><div class="t"><b>Запись активна</b><div>Выключенную запись бот не использует.</div></div><label class="switch"><input type="checkbox" id="e-active"' + (!e || e.is_active !== false ? ' checked' : '') + '><span class="track"></span></label></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="e-save">Сохранить</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);

    function drawAttrs() {
      var box = S.$('#e-attrs', d.el); if (!box) return;
      box.innerHTML = attrs.map(function (a, i) {
        return '<div class="st-attr"><input class="input" data-ak="' + i + '" placeholder="Например: Размер" value="' + esc(a.k) + '"><input class="input" data-av="' + i + '" placeholder="Например: 250 мл" value="' + esc(a.v) + '">' +
          '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-arm="' + i + '" aria-label="Убрать">' + icon('x') + '</button></div>';
      }).join('');
      S.$$('[data-ak]', box).forEach(function (el) { el.addEventListener('input', function () { attrs[+el.dataset.ak].k = el.value; }); });
      S.$$('[data-av]', box).forEach(function (el) { el.addEventListener('input', function () { attrs[+el.dataset.av].v = el.value; }); });
      S.$$('[data-arm]', box).forEach(function (el) { el.addEventListener('click', function () { attrs.splice(+el.dataset.arm, 1); drawAttrs(); }); });
    }
    // В позиции видны только ЕЁ фото и видео. Остальная медиатека бота (фото и видео
    // других позиций) спрятана под кнопкой «Выбрать из ранее загруженных»: раньше вся
    // медиатека показывалась сразу, и казалось, что видео другой позиции прикрепилось сюда.
    var libOpen = false;
    function drawMedia() {
      var box = S.$('#e-media', d.el); if (!box) return;
      var pics = kb.files.filter(function (f) { return /^(image|video)\//.test(f.mime_type || ''); });
      var byId = {}; pics.forEach(function (f) { byId[f.id] = f; });
      var mine = picked.filter(function (id) { return byId[id]; });
      var rest = pics.filter(function (f) { return picked.indexOf(f.id) < 0; });
      var tile = function (f, on) {
        return '<div class="st-media-item' + (on ? ' on' : '') + '" data-mid="' + esc(f.id) + '" title="' + (on ? 'Убрать из позиции' : 'Добавить в позицию') + '">' +
          (f.preview_url ? mediaThumb(f.preview_url, /^video\//.test(f.mime_type || '')) : '<span class="st-ph">' + icon('play') + '</span>') +
          '<span class="chk">' + icon(on ? 'x' : 'plus') + '</span></div>';
      };
      var html = mine.length
        ? '<div class="st-media">' + mine.map(function (id) { return tile(byId[id], true); }).join('') + '</div>'
        : '<div class="hint">У этой позиции пока нет фото и видео. Загрузите — можно сразу несколько.</div>';
      if (rest.length) {
        html += '<button class="btn btn-ghost btn-sm" type="button" id="e-lib" style="margin-top:8px">' + icon(libOpen ? 'chevron-up' : 'chevron-down') +
          '<span>' + (libOpen ? 'Скрыть ранее загруженные' : 'Выбрать из ранее загруженных (' + rest.length + ')') + '</span></button>';
        if (libOpen) html += '<div class="hint" style="margin:6px 0">Нажмите на фото или видео, чтобы добавить его к этой позиции.</div><div class="st-media st-media-lib">' + rest.map(function (f) { return tile(f, false); }).join('') + '</div>';
      }
      box.innerHTML = html;
      S.$$('[data-mid]', box).forEach(function (el) {
        el.addEventListener('click', function () {
          var i = picked.indexOf(el.dataset.mid);
          if (i >= 0) picked.splice(i, 1); else picked.push(el.dataset.mid);
          drawMedia();
        });
      });
      var lb = S.$('#e-lib', box);
      if (lb) lb.addEventListener('click', function () { libOpen = !libOpen; drawMedia(); });
    }
    if (isProd) {
      drawAttrs(); drawMedia();
      S.$('#e-attr-add', d.el).addEventListener('click', function () { attrs.push({ k: '', v: '' }); drawAttrs(); });
      S.$('#e-up', d.el).addEventListener('click', function () {
        pickFiles('image/*,video/*', true, function (rows) {
          rows.forEach(function (f) { picked.push(f.id); });
          S.api('/files?source=library').then(function (r) { if (r.ok) kb.files = r.data.files || []; drawMedia(); });
        });
      });
    }
    S.$('#e-save', d.el).addEventListener('click', function () {
      var body = { kind: kind, title: S.$('#e-title', d.el).value.trim(), body: S.$('#e-body', d.el).value.trim(), keywords: S.$('#e-kw', d.el).value.trim(), is_active: S.$('#e-active', d.el).checked };
      if (!body.title && !body.body) { S.toast('Заполните запись', 'error'); return; }
      if (isProd) {
        var p = S.$('#e-price', d.el).value;
        body.price = p === '' ? null : Number(p);
        body.currency = S.$('#e-cur', d.el).value;
        body.in_stock = S.$('#e-stock', d.el).value === '1';
        body.media_file_ids = picked.slice();
        var obj = {}; attrs.forEach(function (a) { var k = (a.k || '').trim(); if (k) obj[k] = (a.v || '').trim(); });
        body.attributes = obj;
        body.in_prompt = false; // каталог находится поиском, в инструкцию идёт только прайс
      } else {
        body.in_prompt = S.$('#e-prompt', d.el).checked;
      }
      var btn = this; btn.disabled = true;
      S.api(e ? '/knowledge/' + e.id : '/knowledge', { method: e ? 'PUT' : 'POST', body: body }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        if (gapId) S.api('/knowledge/gaps/' + gapId, { method: 'DELETE' });
        d.close(); S.toast('Сохранено', 'success'); loadKb();
      });
    });
    setTimeout(function () { var i = S.$('#e-title', d.el); if (i) i.focus(); }, 60);
  }

  // Превью фото или видео. Видео — первым кадром (preload=metadata) со значком ▶:
  // раньше для видео рисовался пустой значок картинки, и казалось, что файл не загрузился.
  function mediaThumb(url, isVideo) {
    if (!isVideo) return '<img src="' + esc(url) + '" alt="" loading="lazy">';
    return '<video class="st-vthumb" src="' + esc(url) + '#t=0.1" preload="metadata" muted playsinline onerror="this.style.visibility=\'hidden\'"></video>' +
      '<span class="st-play">' + icon('play') + '</span>';
  }

  // Просмотр всех фото и видео позиции каталога.
  function mediaViewer(e) {
    var list = (e && e.media) || [];
    if (!list.length) return;
    var d = S.dialog({
      title: e.title || 'Фото и видео', size: 'lg',
      body: '<div class="st-viewer">' + list.map(function (m) {
        return m.is_video
          ? '<video src="' + esc(m.url) + '" controls playsinline preload="metadata"></video>'
          : '<img src="' + esc(m.url) + '" alt="">';
      }).join('') + '</div><div class="hint" style="margin-top:8px">Если видео не играет в браузере (например, .mov с iPhone), клиенту оно всё равно уйдёт файлом. Для просмотра на сайте лучше MP4.</div>',
      foot: '<button class="btn" type="button" data-c>Закрыть</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
  }

  // ══════════════════════════════ КАТАЛОГ КАК У МАГАЗИНА (13.11.2026) ══════════════════════
  // Коллекции, поиск, фильтры, плитки или таблица, остаток по размерам и цветам, массовые действия,
  // правка цены и остатка прямо в таблице, позиции из пачки фото, вставка списком, подсказки и превью
  // карточки, которую бот пришлёт клиенту. Владельцы (норка, детская одежда) путались в куче крупных
  // плиток и не понимали, что уже заполнено.
  var NO_COL = '__none';
  var CAT_PAGE = 60;
  var LOW = 3;
  var SIZE_PRESETS = [
    ['Одежда XS–XXL', ['XS', 'S', 'M', 'L', 'XL', 'XXL']],
    ['Одежда 42–56', ['42', '44', '46', '48', '50', '52', '54', '56']],
    ['Головные уборы 54–62', ['54', '55', '56', '57', '58', '59', '60', '61', '62']],
    ['Детская по росту', ['80', '86', '92', '98', '104', '110', '116', '122', '128', '134', '140', '146', '152']],
    ['Обувь 35–45', ['35', '36', '37', '38', '39', '40', '41', '42', '43', '44', '45']]
  ];
  var COLOR_QUICK = ['чёрный', 'белый', 'серый', 'коричневый', 'бежевый', 'синий', 'красный', 'розовый', 'зелёный'];
  var CAT_FILTERS = [['all', 'Все позиции'], ['in', 'В наличии'], ['out', 'Нет в наличии'], ['low', 'Мало осталось (до 3 шт.)'],
    ['sale', 'Со скидкой'], ['nophoto', 'Без фото'], ['noprice', 'Без цены'], ['off', 'Выключенные']];
  var CAT_SORTS = [['new', 'Сначала новые'], ['name', 'По названию'], ['price_up', 'Дешевле'], ['price_down', 'Дороже'], ['stock', 'Меньше остаток']];
  var cat = (function () {
    var d = { col: '', q: '', filter: 'all', sort: 'new', view: 'grid', shown: CAT_PAGE, sel: {} };
    try { var x = JSON.parse(localStorage.getItem('vf_catalog_view') || '{}'); d.view = x.view === 'list' ? 'list' : 'grid'; d.sort = x.sort || 'new'; } catch (e) {}
    return d;
  })();
  function catSave() { try { localStorage.setItem('vf_catalog_view', JSON.stringify({ view: cat.view, sort: cat.sort })); } catch (e) {} }
  function catProducts() { return kb.entries.filter(function (e) { return e.kind === 'product'; }); }
  function catById(id) { return kb.entries.filter(function (x) { return x.id === id; })[0]; }
  function catCollections() {
    var map = {};
    catProducts().forEach(function (e) { var c = (e.collection || '').trim(); if (c) map[c] = (map[c] || 0) + 1; });
    // Порядок — справочник владельца (пустые коллекции тоже показываем), потом найденные у товаров
    var order = (kb.collections || []).slice();
    Object.keys(map).sort(function (a, b) { return a.localeCompare(b, 'ru'); }).forEach(function (k) { if (order.indexOf(k) < 0) order.push(k); });
    return order.map(function (k) { return [k, map[k] || 0]; });
  }
  function articleOf(e) {
    var a = e.attributes || {};
    for (var k in a) if (/^(артикул|код|sku)$/i.test(k)) return String(a[k] || '');
    return '';
  }
  function money(v, c) { return Number(v).toLocaleString('ru-RU') + ' ' + curWord(c); }
  function hasPrice(e) { return e.price != null && e.price !== ''; }
  function hasPhoto(e) { return (e.media || []).length > 0 || !!(e.attributes || {})._photo_url; }
  function discount(e) {
    if (!e.old_price || !hasPrice(e) || Number(e.old_price) <= Number(e.price)) return 0;
    return Math.round((1 - Number(e.price) / Number(e.old_price)) * 100);
  }
  function sizesInStock(e) {
    var v = e.variants || [];
    if (v.length) {
      var out = [];
      v.forEach(function (x) { if (Number(x.qty) > 0 && x.size && out.indexOf(x.size) < 0) out.push(x.size); });
      return out;
    }
    return e.in_stock === false ? [] : (e.sizes || []);
  }
  function stockBadge(e) {
    if (e.in_stock === false) return '<span class="st-cbadge out">нет в наличии</span>';
    if (e.stock_qty != null && e.stock_qty <= LOW) return '<span class="st-cbadge low">осталось ' + e.stock_qty + ' шт.</span>';
    if (e.stock_qty != null) return '<span class="st-cbadge ok">' + e.stock_qty + ' шт.</span>';
    return '';
  }
  function catVisible() {
    var q = cat.q.trim().toLowerCase();
    var list = catProducts().filter(function (e) {
      if (cat.col === NO_COL) { if ((e.collection || '').trim()) return false; }
      else if (cat.col && (e.collection || '').trim() !== cat.col) return false;
      switch (cat.filter) {
        case 'in': if (e.in_stock === false) return false; break;
        case 'out': if (e.in_stock !== false) return false; break;
        case 'low': if (!(e.stock_qty != null && e.stock_qty > 0 && e.stock_qty <= LOW)) return false; break;
        case 'sale': if (!discount(e)) return false; break;
        case 'nophoto': if (hasPhoto(e)) return false; break;
        case 'noprice': if (hasPrice(e)) return false; break;
        case 'off': if (e.is_active !== false) return false; break;
      }
      if (!q) return true;
      var hay = [e.title, e.body, e.keywords, e.collection, (e.sizes || []).join(' '), articleOf(e),
        (e.variants || []).map(function (v) { return v.color || ''; }).join(' '),
        Object.keys(e.attributes || {}).filter(function (k) { return k.charAt(0) !== '_'; }).map(function (k) { return e.attributes[k]; }).join(' ')
      ].join(' ').toLowerCase();
      return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
    });
    var num = function (v) { return v == null || v === '' ? null : Number(v); };
    list.sort(function (a, b) {
      switch (cat.sort) {
        case 'name': return (a.title || '').localeCompare(b.title || '', 'ru');
        case 'price_up': return (num(a.price) == null) - (num(b.price) == null) || (num(a.price) || 0) - (num(b.price) || 0);
        case 'price_down': return (num(a.price) == null) - (num(b.price) == null) || (num(b.price) || 0) - (num(a.price) || 0);
        case 'stock': return (a.stock_qty == null) - (b.stock_qty == null) || (a.stock_qty || 0) - (b.stock_qty || 0);
        default: return String(b.created_at || b.updated_at || '').localeCompare(String(a.created_at || a.updated_at || ''));
      }
    });
    return list;
  }
  function selIds() { return Object.keys(cat.sel).filter(function (id) { return cat.sel[id] && catById(id); }); }

  function drawCatalog(box) {
    var all = catProducts();
    var cols = catCollections();
    var noCol = all.filter(function (e) { return !(e.collection || '').trim(); }).length;
    if (cat.col && cat.col !== NO_COL && !cols.some(function (c) { return c[0] === cat.col; })) cat.col = '';
    var chip = function (key, label, n) {
      return '<button class="st-colchip' + (cat.col === key ? ' on' : '') + '" type="button" data-col="' + esc(key) + '">' + esc(label) +
        '<span class="n">' + n + '</span></button>';
    };
    var curCol = cat.col && cat.col !== NO_COL ? cat.col : '';
    box.innerHTML =
      '<div class="st-cathead"><div><b>Каталог</b><span class="muted small">Товары с ценой, размерами, остатком, фото и видео. Бот показывает их клиентам карточкой: фото, цена, скидка, размеры в наличии.</span></div>' +
      '<div class="st-catadd"><button class="btn btn-sm" id="cat-photos" type="button" title="Каждое фото станет позицией: потом допишете цены прямо в таблице">' + icon('image') + '<span>Позиции из фото</span></button>' +
      '<button class="btn btn-sm" id="cat-paste" type="button" title="Название; цена; размеры; количество — по строке на товар">' + icon('list') + '<span>Вставить списком</span></button>' +
      '<a class="btn btn-sm" href="#s-sheet" title="Импорт из Google Таблицы">' + icon('layers') + '<span>Из таблицы</span></a>' +
      '<button class="btn btn-primary btn-sm" id="kb-add" type="button">' + icon('plus') + '<span>Добавить' + (curCol ? ' в «' + esc(curCol) + '»' : ' позицию') + '</span></button></div></div>' +
      '<div class="st-colbar">' + chip('', 'Все', all.length) + cols.map(function (c) { return chip(c[0], c[0], c[1]); }).join('') +
      (cols.length && noCol ? chip(NO_COL, 'Без коллекции', noCol) : '') +
      '<button class="st-colchip add" type="button" id="cat-newcol">' + icon('plus') + 'Коллекция</button>' +
      '<button class="btn btn-ghost btn-sm" type="button" id="cat-colmenu" title="Создать, переименовать, убрать коллекции и поменять порядок">' + icon('settings') + '<span>Коллекции</span></button>' +
      '</div>' +
      '<div id="cat-health"></div>' +
      '<div class="st-cattools"><div class="st-search">' + icon('search') + '<input class="input" id="cat-q" placeholder="Найти: название, размер, цвет, артикул" value="' + esc(cat.q) + '"></div>' +
      '<select class="select-native" id="cat-filter">' + CAT_FILTERS.map(function (f) { return '<option value="' + f[0] + '"' + (cat.filter === f[0] ? ' selected' : '') + '>' + f[1] + '</option>'; }).join('') + '</select>' +
      '<select class="select-native" id="cat-sort">' + CAT_SORTS.map(function (f) { return '<option value="' + f[0] + '"' + (cat.sort === f[0] ? ' selected' : '') + '>' + f[1] + '</option>'; }).join('') + '</select>' +
      '<div class="st-seg"><button type="button" data-cview="grid" class="' + (cat.view === 'grid' ? 'on' : '') + '" title="Плитки">' + icon('layout-grid') + '</button>' +
      '<button type="button" data-cview="list" class="' + (cat.view === 'list' ? 'on' : '') + '" title="Таблица: цену и остаток можно править прямо в ней">' + icon('list') + '</button></div></div>' +
      '<div class="st-catsel"><label class="st-chk"><input type="checkbox" id="cat-selall"><span>Выбрать все на экране</span></label><span id="cat-stats" class="muted small"></span></div>' +
      '<div id="cat-items"></div><div id="cat-bulk"></div>';

    S.$('#kb-add').addEventListener('click', function () { productDialog(null, curCol ? { collection: curCol } : null); });
    S.$('#cat-photos').addEventListener('click', function () { productsFromPhotos(curCol); });
    S.$('#cat-paste').addEventListener('click', function () { pasteDialog(curCol); });
    S.$$('[data-col]', box).forEach(function (b) { b.addEventListener('click', function () { cat.col = b.dataset.col; cat.shown = CAT_PAGE; drawCatalog(box); }); });
    S.$('#cat-newcol').addEventListener('click', function () { newCollectionDialog(box); });
    var cm = S.$('#cat-colmenu'); if (cm) cm.addEventListener('click', function () { collectionsManager(); });
    var qi = S.$('#cat-q');
    qi.addEventListener('input', S.debounce(function () { cat.q = qi.value; cat.shown = CAT_PAGE; drawCatItems(); }, 150));
    S.$('#cat-filter').addEventListener('change', function (e) { cat.filter = e.target.value; cat.shown = CAT_PAGE; drawCatItems(); });
    S.$('#cat-sort').addEventListener('change', function (e) { cat.sort = e.target.value; catSave(); drawCatItems(); });
    S.$$('[data-cview]', box).forEach(function (b) {
      b.addEventListener('click', function () { cat.view = b.dataset.cview; catSave(); S.$$('[data-cview]', box).forEach(function (x) { x.classList.toggle('on', x === b); }); drawCatItems(); });
    });
    S.$('#cat-selall').addEventListener('change', function () {
      var on = this.checked;
      catVisible().slice(0, cat.shown).forEach(function (e) { if (on) cat.sel[e.id] = true; else delete cat.sel[e.id]; });
      drawCatItems();
    });
    drawHealth();
    drawCatItems();
  }

  // Что ещё заполнить — с кнопкой, которая сразу показывает эти позиции
  function drawHealth() {
    var box = S.$('#cat-health'); if (!box) return;
    var all = catProducts().filter(function (e) { return e.is_active !== false; });
    if (!all.length) { box.innerHTML = ''; return; }
    var tips = [];
    var n = all.filter(function (e) { return !hasPhoto(e); }).length;
    if (n) tips.push(['nophoto', n + ' без фото', 'бот не сможет показать их клиенту']);
    n = all.filter(function (e) { return !hasPrice(e); }).length;
    if (n) tips.push(['noprice', n + ' без цены', 'бот не назовёт цену']);
    n = all.filter(function (e) { return e.in_stock === false; }).length;
    if (n) tips.push(['out', n + ' нет в наличии', 'бот скажет, что их нет']);
    n = all.filter(function (e) { return e.stock_qty != null && e.stock_qty > 0 && e.stock_qty <= LOW; }).length;
    if (n) tips.push(['low', n + ' заканчиваются', 'осталось до 3 шт.']);
    var ready = all.filter(function (e) { return hasPhoto(e) && hasPrice(e); }).length;
    var pct = Math.round(ready * 100 / all.length);
    box.innerHTML = '<div class="st-health"><div class="st-health-ring" style="--p:' + pct + '"><span>' + pct + '%</span></div>' +
      '<div class="st-health-t"><b>' + (pct === 100 ? 'Каталог готов: у всех позиций есть фото и цена' : 'Готово к показу клиентам: ' + ready + ' из ' + all.length) + '</b>' +
      (tips.length ? '<div class="st-health-tips">' + tips.map(function (t) {
        return '<button type="button" data-hf="' + t[0] + '" class="' + (cat.filter === t[0] ? 'on' : '') + '"><b>' + esc(t[1]) + '</b> · ' + esc(t[2]) + '</button>';
      }).join('') + '</div>' : '<span class="muted small">Бот покажет любую позицию карточкой с фото и ценой.</span>') + '</div></div>';
    S.$$('[data-hf]', box).forEach(function (b) {
      b.addEventListener('click', function () {
        cat.filter = cat.filter === b.dataset.hf ? 'all' : b.dataset.hf;
        var f = S.$('#cat-filter'); if (f) f.value = cat.filter;
        cat.shown = CAT_PAGE; drawHealth(); drawCatItems();
      });
    });
  }

  function drawCatItems() {
    var wrap = S.$('#cat-items'); if (!wrap) return;
    var all = catProducts();
    var list = catVisible();
    S.$('#cat-stats').textContent = all.length ? 'Показано ' + Math.min(list.length, cat.shown) + ' из ' + list.length : '';
    drawBulkBar();
    var sa = S.$('#cat-selall');
    if (sa) {
      var page0 = list.slice(0, cat.shown);
      sa.checked = page0.length > 0 && page0.every(function (e) { return cat.sel[e.id]; });
      sa.parentNode.style.visibility = list.length ? '' : 'hidden';
    }
    if (!all.length) {
      wrap.innerHTML = '<div class="st-catempty">' + icon('package') + '<b>Каталог пуст</b><span>Самый быстрый способ — «Позиции из фото»: выберите сразу все фото товаров, каждое станет позицией, а цены и размеры допишете в таблице. Или «Вставить списком» из Excel и WhatsApp.</span></div>';
      return;
    }
    if (!list.length) {
      wrap.innerHTML = '<div class="st-empty">' + (cat.col && cat.col !== NO_COL && !cat.q && cat.filter === 'all'
        ? 'В коллекции «' + esc(cat.col) + '» пока нет позиций. Нажмите «Добавить».' : 'Ничего не нашлось. Измените поиск или фильтр.') + '</div>';
      return;
    }
    var page = list.slice(0, cat.shown);
    var showCol = !cat.col;
    var selBox = function (e) { return '<label class="st-sel" title="Выбрать"><input type="checkbox" data-sel="' + esc(e.id) + '"' + (cat.sel[e.id] ? ' checked' : '') + '><span></span></label>'; };
    var html;
    if (cat.view === 'list') {
      html = '<div class="st-ctable"><div class="st-crow head"><span></span><span></span><span>Позиция</span><span>Цена</span><span>Размеры в наличии</span><span>Остаток</span><span></span></div>' +
        page.map(function (e) {
          var ph = (e.media || [])[0];
          var d = discount(e);
          var stockCell = (e.variants || []).length
            ? '<button class="st-cell" type="button" data-cedit="' + esc(e.id) + '" title="Остаток по размерам">' + (stockBadge(e) || '—') + '</button>'
            : '<button class="st-cell" type="button" data-istock="' + esc(e.id) + '" title="Изменить остаток">' + (stockBadge(e) || '<span class="st-cbadge ok">есть</span>') + '</button>';
          return '<div class="st-crow' + (e.is_active === false ? ' off' : '') + (cat.sel[e.id] ? ' picked' : '') + '" data-open="' + esc(e.id) + '">' + selBox(e) +
            '<span class="th">' + (ph && ph.url ? mediaThumb(ph.url, ph.is_video) : icon('image')) + '</span>' +
            '<span class="nm"><b>' + esc(e.title || '—') + '</b><small>' + esc([showCol ? e.collection : '', articleOf(e) ? 'арт. ' + articleOf(e) : ''].filter(Boolean).join(' · ')) + '</small></span>' +
            '<span class="pr"><button class="st-cell" type="button" data-iprice="' + esc(e.id) + '" title="Изменить цену">' + (hasPrice(e) ? esc(money(e.price, e.currency)) : '<span class="st-need">указать цену</span>') + '</button>' +
            (d ? '<small><s>' + esc(money(e.old_price, e.currency)) + '</s> −' + d + '%</small>' : '') + '</span>' +
            '<span class="sz">' + esc(sizesInStock(e).join(', ')) + '</span>' +
            '<span>' + stockCell + '</span>' +
            '<span class="acts">' + catActions(e) + '</span></div>';
        }).join('') + '</div>';
    } else {
      html = '<div class="st-cgrid">' + page.map(function (e) {
        var ph = (e.media || [])[0];
        var d = discount(e);
        var sizes = sizesInStock(e);
        return '<div class="st-ccard' + (e.is_active === false ? ' off' : '') + (cat.sel[e.id] ? ' picked' : '') + '" data-open="' + esc(e.id) + '">' +
          '<div class="pic">' + (ph && ph.url ? mediaThumb(ph.url, ph.is_video) : '<span class="st-ph">' + icon('image') + '<small>нет фото</small></span>') + selBox(e) +
          (d ? '<span class="sale">−' + d + '%</span>' : '') +
          ((e.media || []).length > 1 ? '<span class="cnt">' + icon('image') + (e.media || []).length + '</span>' : '') +
          (ph && ph.url ? '<button class="zoom" type="button" data-view="' + esc(e.id) + '" title="Посмотреть фото и видео">' + icon('search') + '</button>' : '') + '</div>' +
          '<div class="bd">' + (showCol ? '<div class="col">' + esc(e.collection || '') + '</div>' : '') +
          '<div class="t">' + esc(e.title || '—') + '</div>' +
          '<div class="pr">' + (hasPrice(e) ? esc(money(e.price, e.currency)) : '<span class="st-need">цена не указана</span>') +
          (d ? ' <s>' + esc(money(e.old_price, e.currency)) + '</s>' : '') + '</div>' +
          (sizes.length ? '<div class="sz">' + sizes.slice(0, 6).map(function (x) { return '<span>' + esc(x) + '</span>'; }).join('') + (sizes.length > 6 ? '<span>+' + (sizes.length - 6) + '</span>' : '') + '</div>' : '') +
          '<div class="ft">' + stockBadge(e) + '<span class="acts">' + catActions(e) + '</span></div></div></div>';
      }).join('') + '</div>';
    }
    if (list.length > cat.shown) html += '<div style="text-align:center;margin-top:14px"><button class="btn" type="button" id="cat-more">Показать ещё ' + Math.min(CAT_PAGE, list.length - cat.shown) + '</button></div>';
    wrap.innerHTML = html;
    S.$$('[data-open]', wrap).forEach(function (el) {
      el.addEventListener('click', function (ev) { if (ev.target.closest('button,label,input')) return; productDialog(catById(el.dataset.open)); });
    });
    S.$$('[data-sel]', wrap).forEach(function (cb) {
      cb.addEventListener('change', function () {
        if (cb.checked) cat.sel[cb.dataset.sel] = true; else delete cat.sel[cb.dataset.sel];
        var card = cb.closest('[data-open]'); if (card) card.classList.toggle('picked', cb.checked);
        drawBulkBar();
        var sa2 = S.$('#cat-selall'); if (sa2) { var p = catVisible().slice(0, cat.shown); sa2.checked = p.every(function (e) { return cat.sel[e.id]; }); }
      });
    });
    S.$$('[data-view]', wrap).forEach(function (b) { b.addEventListener('click', function () { mediaViewer(catById(b.dataset.view)); }); });
    S.$$('[data-cedit]', wrap).forEach(function (b) { b.addEventListener('click', function () { productDialog(catById(b.dataset.cedit)); }); });
    S.$$('[data-iprice]', wrap).forEach(function (b) { b.addEventListener('click', function () { inlineEdit(b, catById(b.dataset.iprice), 'price'); }); });
    S.$$('[data-istock]', wrap).forEach(function (b) { b.addEventListener('click', function () { inlineEdit(b, catById(b.dataset.istock), 'stock_qty'); }); });
    S.$$('[data-ccopy]', wrap).forEach(function (b) {
      b.addEventListener('click', function () {
        var src = catById(b.dataset.ccopy); if (!src) return;
        var copy = JSON.parse(JSON.stringify(src)); delete copy.id; copy.title = (src.title || '') + ' (копия)';
        productDialog(null, copy);
      });
    });
    S.$$('[data-cdel]', wrap).forEach(function (b) {
      b.addEventListener('click', function () {
        S.confirm({ title: 'Удалить позицию?', message: 'Бот больше не будет её предлагать. Фото и видео останутся в файлах.', confirmText: 'Удалить', danger: true }).then(function (ok) {
          if (!ok) return;
          S.api('/knowledge/' + b.dataset.cdel, { method: 'DELETE' }).then(function (r) {
            if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
            delete cat.sel[b.dataset.cdel];
            loadKb();
          });
        });
      });
    });
    var more = S.$('#cat-more'); if (more) more.addEventListener('click', function () { cat.shown += CAT_PAGE; drawCatItems(); });
  }
  function catActions(e) {
    return '<button class="btn btn-ghost btn-icon btn-sm" data-cedit="' + esc(e.id) + '" type="button" title="Изменить">' + icon('pen') + '</button>' +
      '<button class="btn btn-ghost btn-icon btn-sm" data-ccopy="' + esc(e.id) + '" type="button" title="Копировать: похожая позиция">' + icon('copy') + '</button>' +
      '<button class="btn btn-ghost btn-icon btn-sm" data-cdel="' + esc(e.id) + '" type="button" title="Удалить">' + icon('trash-2') + '</button>';
  }

  // Цена или остаток прямо в таблице: Enter — сохранить, Esc — отменить
  function inlineEdit(btn, e, field) {
    if (!e || btn.dataset.busy) return;
    var cur = field === 'price' ? (hasPrice(e) ? e.price : '') : (e.stock_qty == null ? '' : e.stock_qty);
    var holder = document.createElement('span');
    holder.className = 'st-inline';
    holder.innerHTML = '<input class="input" type="number" min="0" step="' + (field === 'price' ? '0.01' : '1') + '" value="' + esc(cur) + '" placeholder="' + (field === 'price' ? 'Цена' : 'шт.') + '">';
    btn.replaceWith(holder);
    var inp = holder.querySelector('input');
    inp.focus(); inp.select();
    var done = false;
    var finish = function (save) {
      if (done) return; done = true;
      var v = inp.value.trim();
      if (!save || String(v) === String(cur)) { drawCatItems(); return; }
      var body = {};
      if (field === 'price') body.price = v === '' ? null : Number(v);
      else { body.stock_qty = v === '' ? null : Math.max(0, parseInt(v, 10) || 0); if (v === '') body.in_stock = true; }
      inp.disabled = true;
      S.api('/knowledge/' + e.id, { method: 'PUT', body: body }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); drawCatItems(); return; }
        var fresh = r.data.entry;
        var i = kb.entries.indexOf(e);
        if (i >= 0 && fresh) { fresh.media = e.media; kb.entries[i] = fresh; }
        drawHealth(); drawCatItems();
      });
    };
    inp.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
      else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); finish(false); }
    });
    inp.addEventListener('blur', function () { finish(true); });
  }

  // ── Массовые действия ──
  function drawBulkBar() {
    var bar = S.$('#cat-bulk'); if (!bar) return;
    var ids = selIds();
    if (!ids.length) { bar.innerHTML = ''; bar.className = ''; return; }
    bar.className = 'st-bulk';
    bar.innerHTML = '<b>Выбрано: ' + ids.length + '</b>' +
      '<button class="btn btn-sm" type="button" data-bk="collection">' + icon('layers') + '<span>В коллекцию</span></button>' +
      '<button class="btn btn-sm" type="button" data-bk="price">' + icon('coins') + '<span>Цена</span></button>' +
      '<button class="btn btn-sm" type="button" data-bk="discount">%<span>Скидка</span></button>' +
      '<button class="btn btn-sm" type="button" data-bk="stock">' + icon('package') + '<span>Наличие</span></button>' +
      '<button class="btn btn-sm" type="button" data-bk="active">' + icon('eye') + '<span>Показ</span></button>' +
      '<button class="btn btn-sm btn-danger-ghost" type="button" data-bk="delete">' + icon('trash-2') + '<span>Удалить</span></button>' +
      '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-bk="clear" title="Снять выбор">' + icon('x') + '</button>';
    S.$$('[data-bk]', bar).forEach(function (b) { b.addEventListener('click', function () { bulkAction(b.dataset.bk, ids); }); });
  }
  function bulkSend(ids, action, value, label) {
    return S.api('/knowledge/bulk', { method: 'POST', body: { ids: ids, action: action, value: value } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return false; }
      if (action === 'delete') ids.forEach(function (id) { delete cat.sel[id]; });
      S.toast((label || 'Готово') + ': ' + r.data.changed, 'success');
      loadKb();
      return true;
    });
  }
  function bulkAction(kind, ids) {
    if (kind === 'clear') { cat.sel = {}; drawCatItems(); return; }
    if (kind === 'delete') {
      S.confirm({ title: 'Удалить ' + ids.length + ' позиций?', message: 'Бот больше не будет их предлагать. Фото и видео останутся в файлах.', confirmText: 'Удалить', danger: true })
        .then(function (ok) { if (ok) bulkSend(ids, 'delete', null, 'Удалено'); });
      return;
    }
    var opts = {
      collection: { title: 'Перенести в коллекцию', body: '<div class="field"><label class="label">Коллекция</label>' + collectionSelect('bk-v', cat.col && cat.col !== NO_COL ? cat.col : '') + '<div class="hint">«Без коллекции» — убрать из коллекции.</div></div>',
        bind: function (d) { bindCollectionSelect(S.$('#bk-v', d.el)); },
        go: function (d) { var v = S.$('#bk-v', d.el).value; return bulkSend(ids, 'collection', v === '__new' ? '' : v, 'Перенесено'); } },
      price: { title: 'Изменить цену', body: '<div class="st-grid2"><div class="field"><label class="label">На сколько процентов</label><input class="input" id="bk-pct" type="number" placeholder="10 — дороже, −10 — дешевле"></div>' +
        '<div class="field"><label class="label">Или одна цена всем</label><input class="input" id="bk-set" type="number" min="0" placeholder="Например: 9500"></div></div><div class="hint">Новые цены округляются до 10 сом.</div>',
        go: function (d) {
          var pct = S.$('#bk-pct', d.el).value, set = S.$('#bk-set', d.el).value;
          if (set !== '') return bulkSend(ids, 'price_set', Number(set), 'Цена изменена');
          if (pct !== '' && Number(pct) !== 0) return bulkSend(ids, 'price_percent', Number(pct), 'Цена изменена');
          S.toast('Укажите процент или цену', 'error'); return Promise.resolve(false);
        } },
      discount: { title: 'Скидка', body: '<div class="field"><label class="label">Скидка, %</label><input class="input" id="bk-v" type="number" min="1" max="99" placeholder="Например: 20"></div>' +
        '<div class="hint">Старая цена сохранится и будет зачёркнута, бот назовёт обе. Чтобы вернуть цену — «Убрать скидку».</div>',
        extra: '<button class="btn" type="button" id="bk-off">Убрать скидку</button>',
        go: function (d) { var v = Number(S.$('#bk-v', d.el).value); if (!(v > 0 && v < 100)) { S.toast('Скидка от 1 до 99%', 'error'); return Promise.resolve(false); } return bulkSend(ids, 'discount', v, 'Скидка поставлена'); },
        bind: function (d) { S.$('#bk-off', d.el).addEventListener('click', function () { bulkSend(ids, 'discount_off', null, 'Скидка убрана').then(function (ok) { if (ok) d.close(); }); }); } },
      stock: { title: 'Наличие', body: '<div class="st-bkpick"><button class="btn" type="button" data-v="in_stock">' + icon('circle-check') + '<span>Есть в наличии</span></button>' +
        '<button class="btn" type="button" data-v="out_of_stock">' + icon('circle-x') + '<span>Нет в наличии</span></button></div><div class="hint">«Нет в наличии» обнуляет остатки. Владельцу придёт сообщение в Telegram, если включены оповещения о складе.</div>',
        pick: { in_stock: 'Есть в наличии', out_of_stock: 'Нет в наличии' } },
      active: { title: 'Показ клиентам', body: '<div class="st-bkpick"><button class="btn" type="button" data-v="activate">' + icon('eye') + '<span>Показывать</span></button>' +
        '<button class="btn" type="button" data-v="deactivate">' + icon('eye-off') + '<span>Скрыть от бота</span></button></div><div class="hint">Скрытые позиции бот не предлагает, но они остаются в каталоге.</div>',
        pick: { activate: 'Включено', deactivate: 'Скрыто' } }
    }[kind];
    if (!opts) return;
    var d = S.dialog({
      title: opts.title + ' · ' + ids.length + ' поз.', body: opts.body,
      foot: '<button class="btn" type="button" data-c>Отмена</button>' + (opts.extra || '') + (opts.go ? '<button class="btn btn-primary" type="button" id="bk-ok">Применить</button>' : '')
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    if (opts.go) S.$('#bk-ok', d.el).addEventListener('click', function () { var b = this; b.disabled = true; opts.go(d).then(function (ok) { b.disabled = false; if (ok) d.close(); }); });
    if (opts.bind) opts.bind(d);
    if (opts.pick) S.$$('[data-v]', d.el).forEach(function (b) { b.addEventListener('click', function () { bulkSend(ids, b.dataset.v, null, opts.pick[b.dataset.v]).then(function (ok) { if (ok) d.close(); }); }); });
    setTimeout(function () { var i = S.$('input', d.el); if (i) i.focus(); }, 60);
  }

  // ── Быстрая загрузка: позиции из фото и вставка списком ──
  function titleFromFile(name) {
    var t = String(name || '').replace(/\.[a-z0-9]{2,5}$/i, '').replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!t || /^(img|image|photo|dsc|pxl|whatsapp image|фото)[\s\-_0-9]*/i.test(t) && /\d{3,}/.test(t)) return '';
    return t.charAt(0).toUpperCase() + t.slice(1);
  }
  function productsFromPhotos(col) {
    pickFiles('image/*,video/*', true, function (rows) {
      if (!rows.length) return;
      var n = 0;
      var items = rows.map(function (f) {
        n += 1;
        return { title: titleFromFile(f.filename || f.title) || ('Новый товар ' + n), collection: col || null, media_file_ids: [f.id] };
      });
      S.api('/knowledge/products/batch', { method: 'POST', body: { items: items } }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast('Создано позиций: ' + r.data.created + '. Допишите названия и цены прямо в таблице', 'success');
        cat.view = 'list'; cat.filter = 'noprice'; cat.q = ''; catSave();
        loadKb();
      });
    });
  }
  function parsePasted(text, col) {
    var out = [];
    String(text || '').split(/\r?\n/).forEach(function (line) {
      line = line.trim(); if (!line) return;
      var parts = line.indexOf('\t') >= 0 ? line.split('\t') : line.split(/\s*[;|]\s*/);
      if (parts.length === 1) {
        // «Шапка норка 9500» — цена последним числом в строке
        var m = line.match(/^(.*?)[\s:,-]+(\d[\d\s]{1,9})\s*(сом|с|kgs)?\.?$/i);
        parts = m ? [m[1], m[2]] : [line];
      }
      var title = (parts[0] || '').trim(); if (!title) return;
      var price = parts[1] ? Number(String(parts[1]).replace(/[^\d.,]/g, '').replace(',', '.')) : null;
      var it = { title: title, collection: col || null, price: price && !isNaN(price) ? price : null };
      if (parts[2]) it.sizes = String(parts[2]).split(/[,/ ]+/).filter(Boolean);
      if (parts[3] && /\d/.test(parts[3])) it.stock_qty = parseInt(String(parts[3]).replace(/\D/g, ''), 10);
      out.push(it);
    });
    return out;
  }
  function pasteDialog(col) {
    var d = S.dialog({
      title: 'Вставить списком' + (col ? ' в «' + col + '»' : ''), size: 'lg',
      body: '<div class="hint" style="margin-bottom:8px">По товару на строку. Можно скопировать прямо из Excel или переписать из WhatsApp:<br>' +
        '<code>Название; цена; размеры; количество</code> — всё, кроме названия, необязательно.</div>' +
        '<textarea class="textarea" id="pl-text" rows="9" placeholder="Шапка-ушанка норка; 9500; 56, 58; 5&#10;Берет норка; 8000&#10;Палантин норка 15500"></textarea>' +
        '<div id="pl-prev" class="st-plprev"></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="pl-ok" disabled>Добавить</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    var ta = S.$('#pl-text', d.el), prev = S.$('#pl-prev', d.el), ok = S.$('#pl-ok', d.el);
    var items = [];
    var redraw = function () {
      items = parsePasted(ta.value, col);
      ok.disabled = !items.length;
      ok.textContent = items.length ? 'Добавить ' + items.length : 'Добавить';
      prev.innerHTML = items.slice(0, 8).map(function (it) {
        return '<div><b>' + esc(it.title) + '</b><span>' + esc([it.price != null ? money(it.price, 'KGS') : 'без цены', (it.sizes || []).join(', '), it.stock_qty != null ? it.stock_qty + ' шт.' : ''].filter(Boolean).join(' · ')) + '</span></div>';
      }).join('') + (items.length > 8 ? '<div class="muted small">…и ещё ' + (items.length - 8) + '</div>' : '');
    };
    ta.addEventListener('input', S.debounce(redraw, 120));
    ok.addEventListener('click', function () {
      if (!items.length) return;
      ok.disabled = true;
      S.api('/knowledge/products/batch', { method: 'POST', body: { items: items.slice(0, 500) } }).then(function (r) {
        ok.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        d.close(); S.toast('Добавлено позиций: ' + r.data.created + '. Фото можно добавить в каждой позиции', 'success'); loadKb();
      });
    });
    setTimeout(function () { ta.focus(); }, 60);
  }

  // Справочник коллекций хранится на сервере (agent_configs.catalog_collections): коллекции есть и пустыми,
  // а в товаре выбираются из выпадающего списка.
  function saveCollections(list) {
    return S.api('/knowledge/collections/set', { method: 'POST', body: { collections: list } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return null; }
      kb.collections = r.data.collections || list;
      return kb.collections;
    });
  }
  function addCollection(name) {
    var v = String(name || '').replace(/\s+/g, ' ').trim();
    if (!v) return Promise.resolve(null);
    var list = catCollections().map(function (c) { return c[0]; });
    var same = list.filter(function (c) { return c.toLowerCase() === v.toLowerCase(); })[0];
    if (same) return Promise.resolve(same);
    list.push(v);
    return saveCollections(list).then(function (r) { return r ? v : null; });
  }
  function newCollectionDialog(box) {
    var d = S.dialog({
      title: 'Новая коллекция',
      body: '<div class="field"><label class="label">Название</label><input class="input" id="nc-name" placeholder="Например: Женская, Мужская, Детская, Осень, Новинки"></div>' +
        '<div class="hint">Коллекция появится сверху каталога и в выпадающем списке у каждого товара.</div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="nc-ok">Создать</button>'
    });
    var go = function () {
      var v = S.$('#nc-name', d.el).value;
      if (!v.trim()) { S.toast('Введите название', 'error'); return; }
      addCollection(v).then(function (name) {
        if (!name) return;
        cat.col = name; cat.q = ''; cat.filter = 'all'; d.close(); drawCatalog(box);
      });
    };
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    S.$('#nc-ok', d.el).addEventListener('click', go);
    S.$('#nc-name', d.el).addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
    setTimeout(function () { S.$('#nc-name', d.el).focus(); }, 60);
  }

  // Управление коллекциями: список с количеством товаров, порядок, переименование, удаление, новая
  function collectionsManager() {
    var d = S.dialog({ title: 'Коллекции каталога', size: 'lg', body: '<div id="cm-list"></div>' +
      '<div class="st-sizeadd" style="max-width:none;margin-top:12px"><input class="input" id="cm-new" placeholder="Новая коллекция: Женская, Мужская, Детская, Осень…"><button class="btn btn-primary btn-sm" type="button" id="cm-add">' + icon('plus') + '<span>Добавить</span></button></div>' +
      '<div class="hint" style="margin-top:8px">Порядок здесь = порядок вкладок над каталогом. Удалить коллекцию — товары не удаляются, они попадут в «Без коллекции».</div>',
      foot: '<button class="btn btn-primary" type="button" data-c>Готово</button>', onClose: function () { loadKb(); } });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    function draw() {
      var cols = catCollections();
      var box = S.$('#cm-list', d.el);
      box.innerHTML = cols.length ? '<div class="st-cmlist">' + cols.map(function (c, i) {
        return '<div class="st-cmrow" data-i="' + i + '"><span class="nm">' + esc(c[0]) + '</span><span class="muted small">' + c[1] + ' тов.</span>' +
          '<span class="acts"><button class="btn btn-ghost btn-icon btn-sm" type="button" data-up="' + i + '" title="Выше"' + (i ? '' : ' disabled') + '>' + icon('chevron-up') + '</button>' +
          '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-down="' + i + '" title="Ниже"' + (i < cols.length - 1 ? '' : ' disabled') + '>' + icon('chevron-down') + '</button>' +
          '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-ren="' + i + '" title="Переименовать">' + icon('pen') + '</button>' +
          '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-rm="' + i + '" title="Убрать">' + icon('trash-2') + '</button></span></div>';
      }).join('') + '</div>' : '<div class="st-empty" style="padding:16px">Коллекций пока нет. Добавьте первую ниже.</div>';
      var names = cols.map(function (c) { return c[0]; });
      var move = function (i, j) { var t = names[i]; names[i] = names[j]; names[j] = t; saveCollections(names).then(draw); };
      S.$$('[data-up]', box).forEach(function (b) { b.addEventListener('click', function () { var i = +b.dataset.up; move(i, i - 1); }); });
      S.$$('[data-down]', box).forEach(function (b) { b.addEventListener('click', function () { var i = +b.dataset.down; move(i, i + 1); }); });
      S.$$('[data-ren]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          var row = b.closest('.st-cmrow'), old = names[+b.dataset.ren];
          row.querySelector('.nm').innerHTML = '<input class="input" value="' + esc(old) + '">';
          var inp = row.querySelector('input'); inp.focus(); inp.select();
          var done = false;
          var fin = function (ok) {
            if (done) return; done = true;
            var v = inp.value.replace(/\s+/g, ' ').trim();
            if (!ok || !v || v === old) { draw(); return; }
            S.api('/knowledge/collections/rename', { method: 'POST', body: { old: old, new: v } }).then(function (r) {
              if (!r.ok) { S.toast(S.apiError(r), 'error'); draw(); return; }
              kb.collections = r.data.collections || kb.collections;
              kb.entries.forEach(function (e) { if (e.kind === 'product' && (e.collection || '') === old) e.collection = v; });
              if (cat.col === old) cat.col = v;
              draw();
            });
          };
          inp.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') fin(true); if (ev.key === 'Escape') { ev.stopPropagation(); fin(false); } });
          inp.addEventListener('blur', function () { fin(true); });
        });
      });
      S.$$('[data-rm]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          var c = cols[+b.dataset.rm];
          var go = function () {
            S.api('/knowledge/collections/rename', { method: 'POST', body: { old: c[0], new: '' } }).then(function (r) {
              if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
              kb.collections = r.data.collections || kb.collections.filter(function (x) { return x !== c[0]; });
              kb.entries.forEach(function (e) { if (e.kind === 'product' && (e.collection || '') === c[0]) e.collection = ''; });
              if (cat.col === c[0]) cat.col = '';
              draw();
            });
          };
          if (!c[1]) { go(); return; }
          S.confirm({ title: 'Убрать коллекцию «' + c[0] + '»?', message: c[1] + ' товаров не удалятся, они попадут в «Без коллекции».', confirmText: 'Убрать' }).then(function (ok) { if (ok) go(); });
        });
      });
    }
    var add = function () {
      var inp = S.$('#cm-new', d.el);
      addCollection(inp.value).then(function (name) { if (name) { inp.value = ''; draw(); inp.focus(); } });
    };
    S.$('#cm-add', d.el).addEventListener('click', add);
    S.$('#cm-new', d.el).addEventListener('keydown', function (e) { if (e.key === 'Enter') add(); });
    draw();
    setTimeout(function () { S.$('#cm-new', d.el).focus(); }, 60);
  }

  // Выпадающий список коллекций (товар, массовый перенос): «Без коллекции», справочник, «+ Новая…»
  function collectionSelect(id, current) {
    var cols = catCollections().map(function (c) { return c[0]; });
    if (current && cols.indexOf(current) < 0) cols.push(current);
    return '<select class="select-native" id="' + id + '"><option value="">Без коллекции</option>' +
      cols.map(function (c) { return '<option value="' + esc(c) + '"' + (c === current ? ' selected' : '') + '>' + esc(c) + '</option>'; }).join('') +
      '<option value="__new">+ Новая коллекция…</option></select>';
  }
  function bindCollectionSelect(sel, onChange) {
    var prev = sel.value;
    sel.addEventListener('change', function () {
      if (sel.value !== '__new') { prev = sel.value; if (onChange) onChange(); return; }
      var name = window.prompt('Название новой коллекции: Женская, Мужская, Детская, Осень…', '');
      if (!name || !name.trim()) { sel.value = prev; return; }
      addCollection(name).then(function (v) {
        if (!v) { sel.value = prev; return; }
        if (![].some.call(sel.options, function (o) { return o.value === v; })) {
          var o = document.createElement('option'); o.value = v; o.textContent = v;
          sel.insertBefore(o, sel.querySelector('option[value="__new"]'));
        }
        sel.value = v; prev = v; if (onChange) onChange();
      });
    });
  }

  // ── Окно позиции каталога ──
  function productDialog(e, prefill, gapId) {
    var src = e || prefill || {};
    var picked = (src.media_file_ids || []).slice();
    var sizes = (src.sizes || []).slice();
    var colors = [];
    (src.variants || []).forEach(function (v) { if (v.color && colors.indexOf(v.color) < 0) colors.push(v.color); if (v.size && sizes.indexOf(v.size) < 0) sizes.push(v.size); });
    var qtyMap = {};   // "size|color" → остаток
    (src.variants || []).forEach(function (v) { qtyMap[(v.size || '') + '|' + (v.color || '')] = v.qty; });
    var stockMode = (src.variants || []).length ? 'variants' : 'total';
    var previewReady = false;
    var sku = articleOf(src);
    var attrs = Object.keys(src.attributes || {}).filter(function (k) { return k.charAt(0) !== '_' && !/^(артикул|код|sku)$/i.test(k); })
      .map(function (k) { return { k: k, v: String(src.attributes[k]) }; });
    var hidden = {};
    Object.keys(src.attributes || {}).forEach(function (k) { if (k.charAt(0) === '_') hidden[k] = src.attributes[k]; });
    var cols = catCollections().map(function (c) { return c[0]; });
    var val = function (v) { return v == null ? '' : esc(v); };
    var d = S.dialog({
      title: e ? 'Изменить позицию' : 'Новая позиция', size: 'xl',
      body: '<div class="st-pwrap"><div class="st-pform">' +
        '<div class="st-psec"><div class="field"><label class="label">Название *</label><input class="input" id="p-title" value="' + val(src.title) + '" placeholder="Например: Шапка-ушанка из норки"></div>' +
        '<div class="st-grid2"><div class="field"><label class="label">Коллекция</label>' + collectionSelect('p-col', (src.collection || '').trim()) + '</div>' +
        '<div class="field"><label class="label">Артикул / код</label><input class="input" id="p-sku" value="' + val(sku) + '" placeholder="Необязательно"></div></div></div>' +

        '<div class="st-psec"><div class="st-psec-h">Фото и видео</div><div class="hint" style="margin-bottom:8px">Первое фото уйдёт клиенту в карточке товара. Можно выбрать сразу несколько файлов (каждый до 20 МБ).</div>' +
        '<div id="e-media"></div><div style="margin-top:8px"><button class="btn btn-ghost btn-sm" type="button" id="e-up">' + icon('upload') + '<span>Загрузить фото или видео</span></button></div></div>' +

        '<div class="st-psec"><div class="st-psec-h">Цена</div><div class="st-grid4">' +
        '<div class="field"><label class="label">Цена сейчас</label><input class="input" id="p-price" type="number" min="0" step="0.01" value="' + val(src.price) + '"></div>' +
        '<div class="field"><label class="label">Скидка, %</label><input class="input" id="p-pct" type="number" min="0" max="95" step="1" placeholder="0"></div>' +
        '<div class="field"><label class="label">Цена до скидки</label><input class="input" id="p-old" type="number" min="0" step="0.01" value="' + val(src.old_price) + '" placeholder="Считается сама"></div>' +
        '<div class="field"><label class="label">Валюта</label><select class="select-native" id="p-cur">' + CURRENCIES.map(function (c) { return '<option value="' + c[0] + '"' + ((src.currency || 'KGS') === c[0] ? ' selected' : '') + '>' + c[1] + '</option>'; }).join('') + '</select></div>' +
        '</div><div class="hint" id="p-disc"></div></div>' +

        '<div class="st-psec"><div class="st-psec-h">Размеры</div><div class="st-sizes" id="p-sizes"></div>' +
        '<div class="st-sizeadd"><input class="input" id="p-size" placeholder="Добавить размер и Enter: 46, M, 110"><button class="btn btn-sm" type="button" id="p-size-add">' + icon('plus') + '</button></div>' +
        '<div class="st-quick" style="margin-top:8px">' + SIZE_PRESETS.map(function (p, i) { return '<button type="button" data-ps="' + i + '">' + esc(p[0]) + '</button>'; }).join('') +
        '<button type="button" id="p-size-clear">Очистить</button></div></div>' +

        '<div class="st-psec"><div class="st-psec-h st-psec-hrow"><span>Остаток</span><div class="st-seg st-seg-text"><button type="button" data-sm="total">Одним числом</button><button type="button" data-sm="variants">По размерам и цветам</button></div></div>' +
        '<div id="p-stock-total"><div class="st-grid2">' +
        '<div class="field"><label class="label">Остаток, шт.</label><input class="input" id="p-stock" type="number" min="0" step="1" value="' + val(stockMode === 'total' ? src.stock_qty : '') + '" placeholder="Пусто — не считаем"></div>' +
        '<div class="field"><label class="label">Наличие</label><select class="select-native" id="p-instock"><option value="1">Есть в наличии</option><option value="0"' + (src.in_stock === false ? ' selected' : '') + '>Нет в наличии</option></select></div>' +
        '</div></div>' +
        '<div id="p-stock-var"><div class="field"><label class="label">Цвета (необязательно)</label><div class="st-sizes" id="p-colors"></div>' +
        '<div class="st-sizeadd"><input class="input" id="p-color" placeholder="Добавить цвет и Enter"><button class="btn btn-sm" type="button" id="p-color-add">' + icon('plus') + '</button></div>' +
        '<div class="st-quick" style="margin-top:6px">' + COLOR_QUICK.map(function (c) { return '<button type="button" data-qc="' + c + '">' + c + '</button>'; }).join('') + '</div></div>' +
        '<div id="p-matrix"></div></div>' +
        '<div class="hint" id="p-stock-hint"></div></div>' +

        '<div class="st-psec"><div class="st-psec-h">Описание и характеристики</div>' +
        '<div class="field"><label class="label">Описание</label><textarea class="textarea" id="p-body" rows="3" placeholder="Материал, фасон, для кого, уход. Бот возьмёт отсюда 1–2 фразы в карточку">' + val(src.body) + '</textarea></div>' +
        '<div class="field"><label class="label">Характеристики</label><div id="e-attrs"></div><button class="btn btn-ghost btn-sm" type="button" id="e-attr-add">' + icon('plus') + '<span>Добавить характеристику</span></button>' +
        '<div class="st-quick" style="margin-top:6px">' + ['Цвет', 'Материал', 'Сезон', 'Пол', 'Возраст', 'Бренд', 'Страна'].map(function (k) { return '<button type="button" data-ak2="' + k + '">' + k + '</button>'; }).join('') + '</div></div>' +
        '<div class="field"><label class="label">Ключевые слова</label><input class="input" id="p-kw" value="' + val(src.keywords) + '" placeholder="через запятую: ушанка, зимняя шапка, мех">' +
        '<div class="hint">По ним бот находит позицию, если клиент назвал её по-другому.</div></div></div>' +

        '<div class="st-switch-row"><div class="t"><b>Позиция активна</b><div>Выключенную позицию бот не предлагает.</div></div><label class="switch"><input type="checkbox" id="p-active"' + (src.is_active !== false ? ' checked' : '') + '><span class="track"></span></label></div>' +
        '</div>' +
        '<aside class="st-pside"><div class="st-psec-h">Так клиент увидит товар</div><div class="st-phone"><div class="st-bubble"><div class="img" id="pv-img"></div><div class="cap" id="pv-cap"></div><div class="tm">12:30 ✓✓</div></div></div>' +
        '<div class="st-ready" id="pv-ready"></div></aside></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button>' +
        (!e ? '<button class="btn" type="button" id="p-save-more">Сохранить и добавить ещё</button>' : '') +
        '<button class="btn btn-primary" type="button" id="p-save">Сохранить</button>'
    });
    var $ = function (sel) { return S.$(sel, d.el); };
    $('[data-c]').addEventListener('click', d.close);

    function chips(boxSel, list, onRemove) {
      $(boxSel).innerHTML = list.length ? list.map(function (x, i) { return '<span class="st-sz">' + esc(x) + '<button type="button" data-rm="' + i + '" aria-label="Убрать">' + icon('x') + '</button></span>'; }).join('')
        : '<span class="hint">' + (boxSel === '#p-sizes' ? 'Размеров нет. Для товаров без размера оставьте пусто.' : 'Без цветов: остаток только по размерам.') + '</span>';
      S.$$(boxSel + ' [data-rm]', d.el).forEach(function (b) { b.addEventListener('click', function () { onRemove(+b.dataset.rm); }); });
    }
    function drawSizes() { chips('#p-sizes', sizes, function (i) { sizes.splice(i, 1); drawSizes(); }); drawMatrix(); }
    function drawColors() {
      // Первый цвет: остатки, введённые только по размерам, переходят к нему, а не обнуляются
      if (colors.length === 1) {
        (sizes.length ? sizes : ['']).forEach(function (s) {
          var from = s + '|', to = s + '|' + colors[0];
          if (qtyMap[from] != null && qtyMap[to] == null) qtyMap[to] = qtyMap[from];
        });
      }
      chips('#p-colors', colors, function (i) { colors.splice(i, 1); drawColors(); });
      drawMatrix();
    }
    function addTo(list, vals, redraw) {
      vals.forEach(function (x) {
        x = String(x || '').replace(/\s+/g, ' ').trim().slice(0, list === sizes ? 20 : 40);
        if (x && !list.some(function (s) { return s.toLowerCase() === x.toLowerCase(); })) list.push(x);
      });
      redraw();
    }
    var sizeIn = $('#p-size'), colorIn = $('#p-color');
    var addSize = function () { addTo(sizes, sizeIn.value.split(/[,;/]+/), drawSizes); sizeIn.value = ''; sizeIn.focus(); };
    var addColor = function () { addTo(colors, colorIn.value.split(/[,;/]+/), drawColors); colorIn.value = ''; colorIn.focus(); };
    sizeIn.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); addSize(); } });
    colorIn.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); addColor(); } });
    $('#p-size-add').addEventListener('click', addSize);
    $('#p-color-add').addEventListener('click', addColor);
    S.$$('[data-ps]', d.el).forEach(function (b) { b.addEventListener('click', function () { addTo(sizes, SIZE_PRESETS[+b.dataset.ps][1], drawSizes); }); });
    S.$$('[data-qc]', d.el).forEach(function (b) { b.addEventListener('click', function () { addTo(colors, [b.dataset.qc], drawColors); }); });
    $('#p-size-clear').addEventListener('click', function () { sizes = []; drawSizes(); });
    bindCollectionSelect($('#p-col'), function () { if (previewReady) preview(); });

    // Таблица остатков: строки — размеры, столбцы — цвета (или один «Остаток»)
    function drawMatrix() {
      var box = $('#p-matrix'); if (!box) return;
      if (!sizes.length && !colors.length) { box.innerHTML = '<div class="hint">Добавьте размеры выше (и цвета, если нужно) — здесь появится таблица остатков.</div>'; updateStockHint(); return; }
      var rows = sizes.length ? sizes : [''];
      var cs = colors.length ? colors : [''];
      var cell = function (s, c) {
        var k = s + '|' + c, q = qtyMap[k];
        return '<input class="input' + (q === 0 ? ' zero' : '') + '" type="number" min="0" step="1" data-q="' + esc(k) + '" value="' + (q == null ? '' : esc(q)) + '" placeholder="0">';
      };
      var grid = cs.length <= 1 || rows.length <= 1;
      if (grid) {
        // Один цвет (или один размер) — компактная сетка «размер: штук», а не длинная таблица
        var pairs = [];
        rows.forEach(function (s) { cs.forEach(function (c) { if (s || c) pairs.push([s, c]); }); });
        box.innerHTML = (cs.length === 1 && cs[0] && rows.length > 1 ? '<div class="hint" style="margin:4px 0">Цвет: ' + esc(cs[0]) + '</div>' : '') +
          '<div class="st-qgrid">' + pairs.map(function (pc) {
            return '<label><span>' + esc(rows.length > 1 || !pc[1] ? (pc[0] || 'Все') : pc[1]) + '</span>' + cell(pc[0], pc[1]) + '</label>';
          }).join('') + '</div>' +
          '<div class="st-quick"><button type="button" id="p-fill1">Всем по 1</button><button type="button" id="p-fill0">Обнулить</button></div>';
      } else
      box.innerHTML = '<div class="st-matrix"><table><thead><tr><th>' + (sizes.length ? 'Размер' : '') + '</th>' + cs.map(function (c) { return '<th>' + esc(c || 'Остаток, шт.') + '</th>'; }).join('') + '</tr></thead><tbody>' +
        rows.map(function (s) {
          return '<tr><td><b>' + esc(s || 'Все') + '</b></td>' + cs.map(function (c) { return '<td>' + cell(s, c) + '</td>'; }).join('') + '</tr>';
        }).join('') + '</tbody></table></div>' +
        '<div class="st-quick"><button type="button" id="p-fill1">Всем по 1</button><button type="button" id="p-fill0">Обнулить</button></div>';
      S.$$('[data-q]', box).forEach(function (inp) {
        inp.addEventListener('input', function () { var v = inp.value; qtyMap[inp.dataset.q] = v === '' ? null : Math.max(0, parseInt(v, 10) || 0); inp.classList.toggle('zero', qtyMap[inp.dataset.q] === 0); updateStockHint(); preview(); });
      });
      var fill = function (n) { S.$$('[data-q]', box).forEach(function (inp) { qtyMap[inp.dataset.q] = n; inp.value = n; inp.classList.toggle('zero', n === 0); }); updateStockHint(); preview(); };
      $('#p-fill1').addEventListener('click', function () { fill(1); });
      $('#p-fill0').addEventListener('click', function () { fill(0); });
      updateStockHint();
      if (previewReady) preview();
    }
    function buildVariants() {
      var out = [];
      var rows = sizes.length ? sizes : [''];
      var cs = colors.length ? colors : [''];
      rows.forEach(function (s) { cs.forEach(function (c) { if (!s && !c) return; out.push({ size: s, color: c, qty: Number(qtyMap[s + '|' + c] || 0) }); }); });
      return out;
    }
    function setStockMode(m) {
      stockMode = m;
      S.$$('[data-sm]', d.el).forEach(function (b) { b.classList.toggle('on', b.dataset.sm === m); });
      $('#p-stock-total').style.display = m === 'total' ? '' : 'none';
      $('#p-stock-var').style.display = m === 'variants' ? '' : 'none';
      drawMatrix(); updateStockHint(); preview();
    }
    function updateStockHint() {
      var h = $('#p-stock-hint'); if (!h) return;
      if (stockMode === 'variants') {
        var v = buildVariants(), total = v.reduce(function (a, x) { return a + x.qty; }, 0);
        var have = v.filter(function (x) { return x.qty > 0; }).map(function (x) { return [x.size, x.color].filter(Boolean).join(' '); });
        h.textContent = v.length ? ('Всего ' + total + ' шт. ' + (total ? 'Бот предложит только то, что есть: ' + have.slice(0, 8).join(', ') + (have.length > 8 ? ' и ещё ' + (have.length - 8) + '.' : '.') : 'Ничего нет в наличии — бот так и скажет.')) : '';
      } else {
        h.textContent = 'Укажете остаток — наличие считается само: 0 шт. значит «нет в наличии», до 3 шт. бот скажет, что осталось мало.';
      }
    }
    S.$$('[data-sm]', d.el).forEach(function (b) { b.addEventListener('click', function () { setStockMode(b.dataset.sm); }); });
    var syncStock = function () { var v = $('#p-stock').value; var sel = $('#p-instock'); sel.disabled = v !== ''; if (v !== '') sel.value = Number(v) > 0 ? '1' : '0'; };
    $('#p-stock').addEventListener('input', function () { syncStock(); preview(); });
    $('#p-instock').addEventListener('change', preview);
    syncStock();

    // Цена, скидка в процентах и цена до скидки связаны: владелец вписывает то, что ему удобнее.
    // На живом кабинете в «старую цену» вписали 15 — имели в виду скидку 15%.
    function round10(v) { return v >= 1000 ? Math.round(v / 10) * 10 : Math.round(v); }
    function drawDisc() {
      var p = parseFloat($('#p-price').value), o = parseFloat($('#p-old').value), el = $('#p-disc');
      var warn = [];
      if (p > 5000000) warn.push('Цена ' + Number(p).toLocaleString('ru-RU') + ' — проверьте, нет ли лишних цифр.');
      if (o && p && o <= p) warn.push(o <= 95 ? 'В «Цена до скидки» вписано ' + o + ' — если это скидка в процентах, впишите её в поле «Скидка, %».' : 'Цена до скидки должна быть больше текущей — иначе скидки нет.');
      el.classList.toggle('st-warn', warn.length > 0);
      el.textContent = warn.length ? warn.join(' ') : (p > 0 && o > p ? 'Скидка ' + Math.round((1 - p / o) * 100) + '%: клиент увидит обе цены.' : '');
    }
    function syncPct() {
      var p = parseFloat($('#p-price').value), o = parseFloat($('#p-old').value);
      $('#p-pct').value = p > 0 && o > p ? Math.round((1 - p / o) * 100) : '';
    }
    $('#p-price').addEventListener('input', function () {
      var pct = parseFloat($('#p-pct').value), p = parseFloat($('#p-price').value);
      if (pct > 0 && pct < 96 && p > 0) $('#p-old').value = round10(p / (1 - pct / 100));
      drawDisc(); preview();
    });
    $('#p-pct').addEventListener('input', function () {
      var pct = parseFloat($('#p-pct').value), p = parseFloat($('#p-price').value);
      $('#p-old').value = pct > 0 && pct < 96 && p > 0 ? round10(p / (1 - pct / 100)) : '';
      drawDisc(); preview();
    });
    $('#p-old').addEventListener('input', function () { syncPct(); drawDisc(); preview(); });
    syncPct();
    ['#p-title', '#p-body'].forEach(function (s) { $(s).addEventListener('input', S.debounce(preview, 120)); });
    drawDisc();

    function drawAttrs() {
      var box = $('#e-attrs');
      box.innerHTML = attrs.map(function (a, i) {
        return '<div class="st-attr"><input class="input" data-ak="' + i + '" placeholder="Например: Цвет" value="' + esc(a.k) + '"><input class="input" data-av="' + i + '" placeholder="Например: чёрный" value="' + esc(a.v) + '">' +
          '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-arm="' + i + '" aria-label="Убрать">' + icon('x') + '</button></div>';
      }).join('');
      S.$$('[data-ak]', box).forEach(function (el) { el.addEventListener('input', function () { attrs[+el.dataset.ak].k = el.value; preview(); }); });
      S.$$('[data-av]', box).forEach(function (el) { el.addEventListener('input', function () { attrs[+el.dataset.av].v = el.value; preview(); }); });
      S.$$('[data-arm]', box).forEach(function (el) { el.addEventListener('click', function () { attrs.splice(+el.dataset.arm, 1); drawAttrs(); preview(); }); });
    }
    $('#e-attr-add').addEventListener('click', function () { attrs.push({ k: '', v: '' }); drawAttrs(); });
    S.$$('[data-ak2]', d.el).forEach(function (b) {
      b.addEventListener('click', function () {
        if (!attrs.some(function (a) { return a.k === b.dataset.ak2; })) attrs.push({ k: b.dataset.ak2, v: '' });
        drawAttrs();
        var inputs = S.$$('[data-av]', d.el); if (inputs.length) inputs[inputs.length - 1].focus();
      });
    });

    var libOpen = false;
    function mediaFiles() {
      var byId = {}; kb.files.forEach(function (f) { byId[f.id] = f; });
      return picked.map(function (id) { return byId[id]; }).filter(Boolean);
    }
    function drawMedia() {
      var box = $('#e-media'); if (!box) return;
      var pics = kb.files.filter(function (f) { return /^(image|video)\//.test(f.mime_type || ''); });
      var byId = {}; pics.forEach(function (f) { byId[f.id] = f; });
      var mine = picked.filter(function (id) { return byId[id]; });
      var rest = pics.filter(function (f) { return picked.indexOf(f.id) < 0; });
      var tile = function (f, on, i) {
        return '<div class="st-media-item' + (on ? ' on' : '') + '" data-mid="' + esc(f.id) + '" title="' + (on ? 'Убрать из позиции' : 'Добавить в позицию') + '">' +
          (f.preview_url ? mediaThumb(f.preview_url, /^video\//.test(f.mime_type || '')) : '<span class="st-ph">' + icon('play') + '</span>') +
          (on && i === 0 ? '<span class="main">в карточке</span>' : '') +
          (on && i > 0 ? '<button type="button" class="mk" data-main="' + esc(f.id) + '" title="Сделать главным">' + icon('star') + '</button>' : '') +
          '<span class="chk">' + icon(on ? 'x' : 'plus') + '</span></div>';
      };
      var html = mine.length ? '<div class="st-media">' + mine.map(function (id, i) { return tile(byId[id], true, i); }).join('') + '</div>'
        : '<div class="hint">У позиции пока нет фото и видео — бот пришлёт карточку текстом.</div>';
      if (rest.length) {
        html += '<button class="btn btn-ghost btn-sm" type="button" id="e-lib" style="margin-top:8px">' + icon(libOpen ? 'chevron-up' : 'chevron-down') +
          '<span>' + (libOpen ? 'Скрыть ранее загруженные' : 'Выбрать из ранее загруженных (' + rest.length + ')') + '</span></button>';
        if (libOpen) html += '<div class="st-media st-media-lib" style="margin-top:6px">' + rest.map(function (f) { return tile(f, false, 1); }).join('') + '</div>';
      }
      box.innerHTML = html;
      S.$$('[data-main]', box).forEach(function (b) {
        b.addEventListener('click', function (ev) { ev.stopPropagation(); var id = b.dataset.main; picked.splice(picked.indexOf(id), 1); picked.unshift(id); drawMedia(); preview(); });
      });
      S.$$('[data-mid]', box).forEach(function (el) {
        el.addEventListener('click', function (ev) {
          if (ev.target.closest('[data-main]')) return;
          var i = picked.indexOf(el.dataset.mid);
          if (i >= 0) picked.splice(i, 1); else picked.push(el.dataset.mid);
          drawMedia(); preview();
        });
      });
      var lb = S.$('#e-lib', box); if (lb) lb.addEventListener('click', function () { libOpen = !libOpen; drawMedia(); });
    }
    $('#e-up').addEventListener('click', function () {
      pickFiles('image/*,video/*', true, function (rows) {
        rows.forEach(function (f) { picked.push(f.id); });
        S.api('/files?source=library').then(function (r) { if (r.ok) kb.files = r.data.files || []; drawMedia(); preview(); });
      });
    });

    // Превью карточки — та же логика, что catalog_service.product_card_text на сервере
    function preview() {
      var title = $('#p-title').value.trim() || 'Название товара';
      var p = parseFloat($('#p-price').value), o = parseFloat($('#p-old').value), cur = $('#p-cur').value;
      var lines = ['<b>' + esc(title) + '</b>'];
      if (p >= 0 && $('#p-price').value !== '') {
        lines.push(o > p ? 'Цена: ' + esc(money(p, cur)) + ' вместо ' + esc(money(o, cur)) + ', скидка ' + Math.round((1 - p / o) * 100) + '%' : 'Цена: ' + esc(money(p, cur)));
      }
      var inStock, haveSizes = [], haveColors = [], left = null;
      if (stockMode === 'variants' && buildVariants().length) {
        var v = buildVariants();
        left = v.reduce(function (a, x) { return a + x.qty; }, 0);
        inStock = left > 0;
        v.forEach(function (x) { if (x.qty > 0) { if (x.size && haveSizes.indexOf(x.size) < 0) haveSizes.push(x.size); if (x.color && haveColors.indexOf(x.color) < 0) haveColors.push(x.color); } });
      } else {
        var st = $('#p-stock').value;
        left = st === '' ? null : Number(st);
        inStock = st === '' ? $('#p-instock').value === '1' : Number(st) > 0;
        if (inStock) haveSizes = sizes.slice();
      }
      if (!inStock) lines.push('Сейчас нет в наличии');
      else {
        if (haveSizes.length) lines.push('Размеры в наличии: ' + esc(haveSizes.join(', ')));
        if (haveColors.length) lines.push('Цвета: ' + esc(haveColors.join(', ')));
        if (left != null && left > 0 && left <= LOW) lines.push('Осталось ' + left + ' шт.');
      }
      attrs.filter(function (a) { return a.k.trim() && a.v.trim(); }).slice(0, 3).forEach(function (a) { lines.push(esc(a.k.trim()) + ': ' + esc(a.v.trim())); });
      var desc = $('#p-body').value.replace(/\s+/g, ' ').trim();
      if (desc) lines.push('<span class="d">' + esc(desc.length > 350 ? desc.slice(0, 350).replace(/\s+\S*$/, '') + '…' : desc) + '</span>');
      $('#pv-cap').innerHTML = lines.join('<br>');
      var f = mediaFiles()[0];
      $('#pv-img').innerHTML = f && f.preview_url ? mediaThumb(f.preview_url, /^video\//.test(f.mime_type || '')) : '<span>' + icon('image') + 'без фото — карточка уйдёт текстом</span>';
      $('#pv-img').classList.toggle('none', !(f && f.preview_url));
      // Готовность
      var checks = [
        [!!$('#p-title').value.trim(), 'Название'],
        [!!f, 'Фото — клиент видит товар'],
        [$('#p-price').value !== '', 'Цена — бот её назовёт'],
        [!!desc, 'Описание — 1–2 фразы в карточку'],
        [stockMode === 'variants' ? buildVariants().length > 0 : ($('#p-stock').value !== '' || true), stockMode === 'variants' ? 'Остаток по размерам' : 'Наличие'],
        [($('#p-col').value && $('#p-col').value !== '__new') || !catCollections().length, 'Коллекция']
      ];
      var okN = checks.filter(function (c) { return c[0]; }).length;
      $('#pv-ready').innerHTML = '<div class="st-ready-h"><b>Готовность ' + okN + ' из ' + checks.length + '</b><span class="bar"><i style="width:' + Math.round(okN * 100 / checks.length) + '%"></i></span></div>' +
        checks.map(function (c) { return '<div class="' + (c[0] ? 'ok' : 'no') + '">' + icon(c[0] ? 'circle-check' : 'circle') + '<span>' + esc(c[1]) + '</span></div>'; }).join('');
    }

    drawAttrs(); drawSizes(); drawColors(); drawMedia();
    previewReady = true;
    setStockMode(stockMode);

    function collect() {
      var title = $('#p-title').value.trim();
      if (!title) { S.toast('Укажите название', 'error'); $('#p-title').focus(); return null; }
      var obj = {};
      Object.keys(hidden).forEach(function (k) { obj[k] = hidden[k]; });
      var skuV = $('#p-sku').value.trim(); if (skuV) obj['Артикул'] = skuV;
      attrs.forEach(function (a) { var k = (a.k || '').trim(); if (k && (a.v || '').trim()) obj[k] = a.v.trim(); });
      var p = $('#p-price').value, o = $('#p-old').value, st = $('#p-stock').value;
      var body = {
        kind: 'product', title: title, body: $('#p-body').value.trim(), keywords: $('#p-kw').value.trim(),
        collection: $('#p-col').value === '__new' ? '' : $('#p-col').value,
        price: p === '' ? null : Number(p), old_price: o === '' ? null : Number(o), currency: $('#p-cur').value,
        sizes: sizes.slice(), media_file_ids: picked.slice(), attributes: obj, is_active: $('#p-active').checked, in_prompt: false
      };
      if (stockMode === 'variants' && buildVariants().length) {
        body.variants = buildVariants();
      } else {
        body.variants = [];
        body.stock_qty = st === '' ? null : Math.max(0, parseInt(st, 10) || 0);
        body.in_stock = st === '' ? $('#p-instock').value === '1' : Number(st) > 0;
      }
      return body;
    }
    function save(more, btn) {
      var body = collect(); if (!body) return;
      btn.disabled = true;
      S.api(e ? '/knowledge/' + e.id : '/knowledge', { method: e ? 'PUT' : 'POST', body: body }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        if (gapId) S.api('/knowledge/gaps/' + gapId, { method: 'DELETE' });
        d.close(); S.toast('Сохранено', 'success');
        loadKb().then(function () {
          // Следующая позиция той же коллекции: коллекция, валюта и размеры остаются
          if (more) productDialog(null, { collection: body.collection, currency: body.currency, sizes: body.sizes });
        });
      });
    }
    $('#p-save').addEventListener('click', function () { save(false, this); });
    var sm = $('#p-save-more'); if (sm) sm.addEventListener('click', function () { save(true, this); });
    setTimeout(function () { var i = $('#p-title'); if (i && !e) i.focus(); }, 60);
  }

  // Выбор и загрузка файлов в библиотеку бота → cb(список созданных файлов)
  function pickFiles(accept, multiple, cb) {
    if (!kb.storage) { S.toast('Хранилище файлов не настроено на сервере', 'error'); return; }
    var inp = document.createElement('input');
    inp.type = 'file'; inp.accept = accept || ''; inp.multiple = !!multiple;
    inp.addEventListener('change', function () {
      var list = Array.prototype.slice.call(inp.files || []);
      if (!list.length) return;
      var done = [];
      (function next() {
        if (!list.length) { cb(done); return; }
        var f = list.shift();
        S.toast('Загружаю: ' + f.name);
        S.upload('/files', f).then(function (r) {
          if (!r.ok) S.toast(S.errText(r.data.detail === 'file_too_large' ? 'Файл слишком большой' : r.data.detail === 'library_limit_reached' ? 'В библиотеке больше нельзя хранить файлов' : r.data.detail || r.data), 'error');
          else done.push(r.data.file);
          next();
        });
      })();
    });
    inp.click();
  }

  // ── Файлы бота ──
  function fileIc(f) {
    var m = f.mime_type || '';
    return m.indexOf('image/') === 0 ? 'image' : m.indexOf('video/') === 0 ? 'play' : m.indexOf('audio/') === 0 ? 'volume-2' : 'file-text';
  }
  function openFile(id) {
    S.api('/files/' + id + '/link').then(function (r) {
      if (r.ok && r.data.url) window.open(r.data.url, '_blank', 'noopener'); else S.toast(S.apiError(r), 'error');
    });
  }

  function drawFiles(box) {
    var rows = function (arr, lib) {
      return arr.map(function (f) {
        var thumb = f.preview_url ? mediaThumb(f.preview_url, /^video\//.test(f.mime_type || '')) : icon(fileIc(f));
        var meta = [S.fmtSize(f.size_bytes)];
        if (f.used_in_catalog) meta.push('в каталоге');
        if (f.knowledge_status === 'pending') meta.push('индексируется');
        if (f.knowledge_status === 'failed') meta.push('ошибка индексации');
        if (!lib) meta.push(S.fmtDate(f.created_at));
        return '<div class="st-file" data-id="' + esc(f.id) + '"><div class="th">' + thumb + '</div>' +
          '<div class="nm"><b>' + esc(f.title || f.filename || '') + '</b><div class="muted small">' + esc(meta.join(' · ')) + (f.description ? ' · ' + esc(f.description) : '') + '</div></div>' +
          '<div class="rl">' + (lib ? '<label class="st-role' + (f.can_be_knowledge ? '' : ' dis') + '" title="' + (f.can_be_knowledge ? '' : 'В файле нет текста') + '"><span class="switch"><input type="checkbox" data-kbf="' + esc(f.id) + '"' + (f.in_knowledge ? ' checked' : '') + (f.can_be_knowledge ? '' : ' disabled') + (f.knowledge_entry_id ? ' data-entry="' + esc(f.knowledge_entry_id) + '"' : '') + '><span class="track"></span></span><span>Бот ищет ответы внутри</span></label>' : '') +
          '<span class="acts">' + (lib ? '<button class="btn btn-ghost btn-icon btn-sm" data-fedit="' + esc(f.id) + '" type="button" aria-label="Изменить">' + icon('pen') + '</button>' : '') +
          '<button class="btn btn-ghost btn-icon btn-sm" data-fdl="' + esc(f.id) + '" type="button" aria-label="Открыть">' + icon('download') + '</button>' +
          '<button class="btn btn-ghost btn-icon btn-sm" data-fdel="' + esc(f.id) + '" type="button" aria-label="Удалить">' + icon('trash-2') + '</button></span></div></div>';
      }).join('');
    };
    box.innerHTML = (kb.storage ? '' : '<div class="note note-warning" style="margin-bottom:12px">Хранилище файлов не настроено на сервере, загрузка недоступна.</div>') +
      '<div class="row-between" style="margin-bottom:12px;gap:12px;flex-wrap:wrap"><span class="muted small" style="max-width:560px">Прайсы, презентации, договоры, фото. Бот отправляет их клиенту, когда тот просит. Включите «ищет ответы внутри», и бот будет брать факты из текста файла.</span>' +
      '<button class="btn btn-primary btn-sm" id="f-up" type="button">' + icon('upload') + '<span>Загрузить файл</span></button></div>' +
      (kb.files.length ? '<div class="st-files">' + rows(kb.files, true) + '</div>' : '<div class="st-empty">Файлов пока нет.</div>') +
      '<div class="st-sub-h" style="margin-top:22px">Документы, которые бот собрал сам</div>' +
      '<p class="muted small" style="margin:0 0 10px">Коммерческие предложения, счета и памятки: бот составляет их в PDF и отправляет клиенту в переписке. Оформление настраивается в разделе «Документы бота».</p>' +
      (kb.generated.length ? '<div class="st-files">' + rows(kb.generated, false) + '</div>' : '<div class="st-empty" style="padding:16px">Пока бот ничего не собирал.</div>');
    on('f-up', function () { pickFiles('', true, function () { loadKb(); }); });
    S.$$('[data-fdl]', box).forEach(function (b) { b.addEventListener('click', function () { openFile(b.dataset.fdl); }); });
    S.$$('[data-fdel]', box).forEach(function (b) {
      b.addEventListener('click', function () {
        S.confirm({ title: 'Удалить файл?', message: 'Файл исчезнет из библиотеки бота.', confirmText: 'Удалить', danger: true }).then(function (ok) {
          if (!ok) return;
          S.api('/files/' + b.dataset.fdel, { method: 'DELETE' }).then(function (r) { if (!r.ok) S.toast(S.apiError(r), 'error'); loadKb(); });
        });
      });
    });
    S.$$('[data-fedit]', box).forEach(function (b) {
      b.addEventListener('click', function () {
        var f = kb.files.filter(function (x) { return x.id === b.dataset.fedit; })[0];
        var d = S.dialog({
          title: 'Файл', size: 'sm',
          body: '<div class="field"><label class="label">Название</label><input class="input" id="fe-t" value="' + esc(f.title || f.filename || '') + '"></div>' +
            '<div class="field"><label class="label">Когда отправлять</label><textarea class="textarea" id="fe-d" rows="3" placeholder="Например: когда клиент просит прайс">' + esc(f.description || '') + '</textarea><div class="hint">Бот читает это описание и понимает, когда файл уместен.</div></div>',
          foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="fe-s">Сохранить</button>'
        });
        S.$('[data-c]', d.el).addEventListener('click', d.close);
        S.$('#fe-s', d.el).addEventListener('click', function () {
          S.api('/files/' + f.id, { method: 'PATCH', body: { title: S.$('#fe-t', d.el).value, description: S.$('#fe-d', d.el).value } }).then(function (r) {
            if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
            d.close(); loadKb();
          });
        });
      });
    });
    S.$$('[data-kbf]', box).forEach(function (inp) {
      inp.addEventListener('change', function () {
        inp.disabled = true;
        var req = inp.checked
          ? S.api('/knowledge/document', { method: 'POST', body: { file_id: inp.dataset.kbf } })
          : S.api('/knowledge/document/' + encodeURIComponent(inp.dataset.entry || ''), { method: 'DELETE' });
        req.then(function (r) {
          if (!r.ok) { inp.checked = !inp.checked; inp.disabled = false; S.toast(S.apiError(r), 'error'); return; }
          S.toast(inp.checked ? 'Бот теперь ищет ответы в этом файле' : 'Файл больше не источник ответов', 'success'); loadKb();
        });
      });
    });
  }

  function drawGaps(box) {
    if (!kb.gaps.length) {
      box.innerHTML = '<div class="st-empty">Вопросов без ответа нет. Когда бот не найдёт ответ в базе, вопрос появится здесь, и вы сможете его добавить.</div>';
      return;
    }
    box.innerHTML = '<p class="muted small" style="margin:0 0 12px">Клиенты спрашивали, а ответа в базе не нашлось. Добавьте ответ, и бот будет отвечать сам.</p>' +
      kb.gaps.map(function (g) {
        return '<div class="st-qa"><div class="q">' + esc(g.query) + '</div><div class="a">Спрашивали ' + g.ask_count + ' раз · ' + esc(S.fmtDate(g.last_asked_at)) + '</div>' +
          '<div class="acts"><button class="btn btn-sm btn-primary" data-ans="' + esc(g.id) + '" type="button">Добавить ответ</button>' +
          '<button class="btn btn-ghost btn-sm" data-hide="' + esc(g.id) + '" type="button">Скрыть</button></div></div>';
      }).join('');
    S.$$('[data-ans]', box).forEach(function (b) {
      b.addEventListener('click', function () {
        var g = kb.gaps.filter(function (x) { return x.id === b.dataset.ans; })[0];
        entryDialog('qa', null, { title: g.query, body: '', keywords: '' }, g.id);
      });
    });
    S.$$('[data-hide]', box).forEach(function (b) {
      b.addEventListener('click', function () {
        S.api('/knowledge/gaps/' + b.dataset.hide, { method: 'DELETE' }).then(function () { loadKb(); });
      });
    });
  }

  // ══════════════════════════════ КАНАЛЫ ═══════════════════════════════════
  var ch = { wa: null, tg: null, ig: null };

  function loadChannels() {
    Promise.all([S.api('/whatsapp'), S.api('/telegram-account'), S.api('/connectors')]).then(function (res) {
      ch.wa = res[0].ok ? res[0].data : null;
      ch.tg = res[1].ok ? res[1].data : null;
      var cons = res[2].ok ? (res[2].data.connectors || []) : [];
      var byKit = function (k) { return cons.filter(function (c) { return c.toolkit === k; })[0] || null; };
      ch.ig = byKit('instagram'); ch.gmail = byKit('gmail'); ch.gcal = byKit('google_calendar');
      ch.igCfg = res[2].ok ? !!res[2].data.configured : false;
      drawChannels();
    });
  }

  function chRow(color, ic, name, status, actions) {
    return '<div class="st-row-card"><span class="logo" style="--c:' + color + '">' + icon(ic) + '</span>' +
      '<div class="info"><b>' + name + '</b><div>' + status + '</div></div><div style="display:flex;gap:8px;flex-wrap:wrap">' + actions + '</div></div>';
  }
  function btn(id, label, cls) { return '<button class="btn btn-sm ' + (cls || '') + '" type="button" id="' + id + '">' + label + '</button>'; }

  function drawChannels() {
    var b = S.$('#b-channels');
    var wa = ch.wa, tg = ch.tg, ig = ch.ig;
    var html = '';
    // WhatsApp
    if (!wa || !wa.configured) html += chRow('#25D366', 'message-circle', 'WhatsApp', 'Недоступно на сервере, напишите в поддержку', '');
    else if (wa.status === 'connected') html += chRow('#25D366', 'message-circle', 'WhatsApp', 'Подключён: ' + esc(wa.phone_masked || '') + (wa.wa_name ? ' · ' + esc(wa.wa_name) : '') + scopeText(wa), btn('wa-set', 'Настройки') + btn('wa-off', 'Отключить', 'btn-ghost'));
    else if (wa.status === 'pending_qr') html += chRow('#25D366', 'message-circle', 'WhatsApp', 'Ждём сканирования QR-кода', btn('wa-on', 'Показать QR', 'btn-primary'));
    else if (wa.status === 'disconnected') html += chRow('#25D366', 'message-circle', 'WhatsApp', '<span style="color:var(--vf-warning)">Номер вышел из WhatsApp Web (отключили на телефоне или WhatsApp завершил сессию). Нужен новый QR-код</span>', btn('wa-on', 'Переподключить', 'btn-primary'));
    else html += chRow('#25D366', 'message-circle', 'WhatsApp', 'Бот отвечает клиентам на ваш номер WhatsApp', btn('wa-on', 'Подключить', 'btn-primary'));
    // Telegram личный
    if (!tg || !tg.configured) html += chRow('#229ED9', 'send', 'Telegram', 'Недоступно на сервере, напишите в поддержку', '');
    else if (tg.status === 'connected') html += chRow('#229ED9', 'send', 'Telegram', 'Подключён: ' + esc(tg.tg_username ? '@' + tg.tg_username : (tg.tg_first_name || tg.phone_masked || '')) + scopeText(tg), btn('tg-set', 'Настройки') + btn('tg-off', 'Отключить', 'btn-ghost'));
    else if (tg.status === 'pending_code' || tg.status === 'pending_password' || tg.status === 'pending_qr') html += chRow('#229ED9', 'send', 'Telegram', 'Подключение не завершено', btn('tg-on', 'Продолжить', 'btn-primary'));
    else if (tg.status === 'qr_expired') html += chRow('#229ED9', 'send', 'Telegram', 'QR-код устарел, покажем новый', btn('tg-on', 'Подключить', 'btn-primary'));
    else if (tg.status === 'error') html += chRow('#229ED9', 'send', 'Telegram', '<span style="color:var(--vf-warning)">Сессия недействительна' + (tg.last_error ? ' (' + esc(tg.last_error) + ')' : '') + '. Подключите аккаунт заново</span>', btn('tg-on', 'Переподключить', 'btn-primary'));
    else html += chRow('#229ED9', 'send', 'Telegram', 'Бот отвечает от имени вашего личного аккаунта Telegram', btn('tg-on', 'Подключить', 'btn-primary'));
    // Instagram
    if (!ig || !ch.igCfg || !ig.available) html += chRow('#E1306C', 'instagram', 'Instagram', 'Недоступно на сервере, напишите в поддержку', '');
    else if (ig.connected) html += chRow('#E1306C', 'instagram', 'Instagram', 'Подключён' + (ig.connected_email ? ': ' + esc(ig.connected_email) : ''), btn('ig-set', 'Настройки') + btn('ig-off', 'Отключить', 'btn-ghost'));
    else html += chRow('#E1306C', 'instagram', 'Instagram', 'Бот отвечает на сообщения в Direct', btn('ig-on', 'Подключить', 'btn-primary'));
    // Gmail и Google Календарь (через Composio, как у агента Profi)
    html += connRow('gmail', 'mail', '#EA4335', 'Gmail', 'Бот читает письма и отвечает клиентам с вашей почты', 'Подключить', ch.gmail);
    html += connRow('gcal', 'calendar', '#4285F4', 'Google Календарь', 'Бот смотрит свободное время и записывает клиентов в ваш календарь', 'Подключить', ch.gcal);
    b.innerHTML = html + '<div class="st-note" style="margin-top:12px">Бот отвечает клиентам и может писать первым в WhatsApp и Telegram: по вашей команде в чате и в рамках суточного лимита новых чатов. В Instagram первым написать нельзя, только в диалог, который уже есть. Звонить клиентам и вести рассылки по расписанию бот на тарифе Старт не будет.</div>';

    on('wa-on', openWa); on('wa-set', function () { settingsDialog('WhatsApp', '/whatsapp/settings', ch.wa, true, false, 'wa'); }); on('wa-off', function () { disconnect('WhatsApp', '/whatsapp'); });
    on('tg-on', openTg); on('tg-set', function () { settingsDialog('Telegram', '/telegram-account/settings', ch.tg, false, false, 'tg'); }); on('tg-off', function () { disconnect('Telegram', '/telegram-account'); });
    on('gmail-on', function () { connectToolkit('gmail'); }); on('gmail-off', function () { disconnect('Gmail', '/connectors/gmail'); });
    on('gcal-on', function () { connectToolkit('google_calendar'); }); on('gcal-off', function () { disconnect('Google Календарь', '/connectors/google_calendar'); });
    on('ig-on', function () { connectToolkit('instagram'); }); on('ig-set', function () { settingsDialog('Instagram', '/connectors/instagram/settings', ch.ig, false, true, 'ig'); }); on('ig-off', function () { disconnect('Instagram', '/connectors/instagram'); });
  }
  // Кому отвечает бот. 'contacts' — только клиентам, уже заведённым в CRM: новые люди без ответа, поэтому подсвечиваем
  function scopeText(c) {
    if (!c.auto_reply_enabled) return ' · <span style="color:var(--vf-warning)">автоответ выключен</span>';
    return c.reply_scope === 'all' ? ' · отвечает всем, кто пишет'
      : ' · <span style="color:var(--vf-warning)">отвечает только клиентам из CRM, новым не ответит</span>';
  }

  // Шаг после подключения: кому отвечать. Без явного выбора новые клиенты остались бы без ответа
  function scopeStep(box, path, onDone) {
    box.innerHTML = '<div style="text-align:left;width:100%"><b>Кому отвечать боту?</b>' +
      '<label class="st-opt"><input type="radio" name="sc" value="all"><span><b>Всем, кто пишет</b><small>Каждый новый человек сразу попадёт в CRM, и бот ему ответит. Подходит для рабочего номера.</small></span></label>' +
      '<label class="st-opt"><input type="radio" name="sc" value="contacts"><span><b>Только клиентам из CRM</b><small>Знакомым и друзьям бот не ответит. Новых клиентов нужно добавлять вручную.</small></span></label>' +
      '<button class="btn btn-primary" id="sc-go" type="button" disabled style="margin-top:10px">Сохранить</button></div>';
    var go = S.$('#sc-go', box);
    S.$$('input[name="sc"]', box).forEach(function (r) { r.addEventListener('change', function () { go.disabled = false; }); });
    go.addEventListener('click', function () {
      var v = S.$('input[name="sc"]:checked', box).value;
      go.disabled = true;
      S.api(path, { method: 'PATCH', body: { reply_scope: v, auto_reply_enabled: true } }).then(function (r) {
        if (!r.ok) { go.disabled = false; S.toast(S.apiError(r), 'error'); return; }
        S.toast('Готово', 'success'); onDone();
      });
    });
  }

  function connRow(id, ic, color, name, sub, label, c) {
    if (!ch.igCfg || !c || !c.available) return chRow(color, ic, name, 'Недоступно на сервере, напишите в поддержку', '');
    if (c.connected) return chRow(color, ic, name, 'Подключён' + (c.connected_email ? ': ' + esc(c.connected_email) : ''), btn(id + '-off', 'Отключить', 'btn-ghost'));
    if (c.status === 'error') return chRow(color, ic, name, 'Подключение не удалось, попробуйте ещё раз', btn(id + '-on', label, 'btn-primary'));
    return chRow(color, ic, name, sub, btn(id + '-on', label, 'btn-primary'));
  }
  function on(id, fn) { var el = document.getElementById(id); if (el) el.addEventListener('click', fn); }

  function disconnect(name, path) {
    S.confirm({ title: 'Отключить ' + name + '?', message: 'Бот перестанет отвечать клиентам в этом канале. Переписка в карточках клиентов сохранится.', confirmText: 'Отключить', danger: true }).then(function (ok) {
      if (!ok) return;
      S.api(path, { method: 'DELETE' }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast(name + ' отключён', 'success'); loadChannels();
      });
    });
  }

  var PAUSES = [[10, '10 минут'], [30, '30 минут'], [60, '1 час'], [1440, '1 день'], [-1, 'пока я сам не включу']];
  var DELAYS = [5, 10, 15, 20, 30, 60];

  var CH_WARN = {
    wa: '<b>Важно:</b> подключение неофициальное (как WhatsApp Web). WhatsApp может заблокировать номер за рассылки незнакомым людям. Не используйте холодную базу, лучше отдельный рабочий номер. Бот может писать клиентам первым, но только в пределах суточного лимита новых чатов (поле ниже).',
    tg: '<b>Важно:</b> бот отвечает с вашего личного аккаунта. Telegram банит за сообщения незнакомым людям, поэтому для рабочего аккаунта выбирайте «Только клиентам из CRM». Вариант «Всем, кто написал» ответит и друзьям, и родным, если они напишут. Первым бот пишет редко и в рамках часовых лимитов отправки.',
    ig: 'Instagram не даёт писать первым: бот отвечает только в диалогах, которые клиент уже начал.'
  };
  var CH_POLL = {
    wa: '',
    tg: 'Личный Telegram проверяется раз в минуту, поэтому ответ придёт через указанное время + до 60 секунд.',
    ig: 'Instagram проверяется раз в 90 секунд, поэтому ответ придёт через указанное время + до 90 секунд.'
  };
  function settingsDialog(name, path, cur, hasScope, isIg, kind) {
    cur = cur || {};
    var pause = cur.owner_pause_minutes != null ? Number(cur.owner_pause_minutes) : 30;
    var delay = cur.reply_delay_seconds != null ? Number(cur.reply_delay_seconds) : 15;
    var d = S.dialog({
      title: name + ': настройки', size: 'sm',
      body: (CH_WARN[kind] ? '<div class="note note-warning" style="margin-bottom:14px">' + CH_WARN[kind] + '</div>' : '') +
        (isIg ? '' : '<div class="st-switch-row" style="padding-top:0"><div class="t"><b>Автоответ</b><div>Бот отвечает на входящие сообщения.</div></div><label class="switch"><input type="checkbox" id="s-auto"' + (cur.auto_reply_enabled ? ' checked' : '') + '><span class="track"></span></label></div>') +
        (isIg ? '' : '<div class="field"><label class="label">Кому отвечать</label><select class="select-native" id="s-scope"><option value="contacts"' + (cur.reply_scope !== 'all' ? ' selected' : '') + '>Только клиентам из CRM</option><option value="all"' + (cur.reply_scope === 'all' ? ' selected' : '') + '>Всем, кто написал</option></select></div>') +
        (kind === 'wa' ? '<div class="field"><label class="label">Новых чатов в сутки (бот пишет первым)</label><input class="input" type="number" min="0" max="100" id="s-limit" style="max-width:120px" value="' + Number(cur.daily_new_chats_limit != null ? cur.daily_new_chats_limit : 20) + '">' +
          '<div class="hint">Для нового номера начинайте с 10–20 в сутки: так WhatsApp реже блокирует номер. 0 — бот только отвечает и сам первым не пишет.</div></div>' : '') +
        '<div class="field"><label class="label">Если вы написали клиенту сами, бот молчит</label><select class="select-native" id="s-pause">' + PAUSES.map(function (p) { return '<option value="' + p[0] + '"' + (p[0] === pause ? ' selected' : '') + '>' + p[1] + '</option>'; }).join('') + '</select>' +
        '<div class="hint">Вы сможете спокойно договориться с клиентом без бота.</div></div>' +
        '<div class="field"><label class="label">Отвечать через</label><select class="select-native" id="s-delay">' + DELAYS.map(function (v) { return '<option value="' + v + '"' + (v === delay ? ' selected' : '') + '>' + v + ' секунд</option>'; }).join('') + '</select>' +
        '<div class="hint">Столько бот ждёт после последнего сообщения, чтобы ответить на всю серию сразу.' + (CH_POLL[kind] ? ' ' + CH_POLL[kind] : '') + '</div></div>' +
        (cur.voice_reply_mode !== undefined ? '<div class="field"><label class="label">Голосовые ответы</label><select class="select-native" id="s-voice">' +
          [['on_voice', 'В ответ на голосовое клиента'], ['auto', 'Бот решает сам'], ['never', 'Никогда, только текст']].map(function (p) { return '<option value="' + p[0] + '"' + (p[0] === cur.voice_reply_mode ? ' selected' : '') + '>' + p[1] + '</option>'; }).join('') + '</select></div>' : ''),
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="s-save">Сохранить</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    S.$('#s-save', d.el).addEventListener('click', function () {
      var body = { owner_pause_minutes: parseInt(S.$('#s-pause', d.el).value, 10), reply_delay_seconds: parseInt(S.$('#s-delay', d.el).value, 10) };
      var lim = S.$('#s-limit', d.el); if (lim) body.daily_new_chats_limit = Math.max(0, Math.min(100, parseInt(lim.value || '0', 10) || 0));
      if (!isIg) { body.auto_reply_enabled = S.$('#s-auto', d.el).checked; body.reply_scope = S.$('#s-scope', d.el).value; }
      var v = S.$('#s-voice', d.el); if (v) body.voice_reply_mode = v.value;
      S.api(path, { method: 'PATCH', body: body }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        d.close(); S.toast('Сохранено', 'success'); loadChannels();
      });
    });
  }

  // WhatsApp: QR
  var waTimer = null;
  function openWa() {
    var d = S.dialog({ title: 'Подключить WhatsApp', size: 'sm', body: '<div class="st-qr" id="wa-box"><div class="skeleton" style="width:240px;height:240px"></div></div>', onClose: function () { clearInterval(waTimer); loadChannels(); } });
    function paint(s) {
      var box = S.$('#wa-box', d.el); if (!box) return;
      if (s.status === 'connected') {
        clearInterval(waTimer);
        box.innerHTML = '<div class="st-empty-ic">' + icon('circle-check') + '</div><b>WhatsApp подключён</b><div class="muted small">' + esc(s.phone_masked || '') + '</div><div id="wa-scope" style="width:100%"></div>';
        scopeStep(S.$('#wa-scope', d.el), '/whatsapp/settings', d.close);
        return;
      }
      box.innerHTML = (s.qr ? '<img src="' + esc(s.qr) + '" alt="QR-код">' : '<div class="skeleton" style="width:240px;height:240px"></div>') +
        '<div class="muted small" style="text-align:center;max-width:300px">Откройте WhatsApp на телефоне, зайдите в «Связанные устройства» и отсканируйте код.</div>';
    }
    S.api('/whatsapp/connect', { method: 'POST' }).then(function (r) {
      if (!r.ok) { S.$('#wa-box', d.el).innerHTML = '<div class="note note-danger">' + esc(S.errText(r.data.detail === 'not_configured' ? 'WhatsApp-шлюз не настроен' : r.data.detail || r.data)) + '</div>'; return; }
      paint(r.data);
      if (r.data.status !== 'connected') {
        waTimer = setInterval(function () {
          S.api('/whatsapp/qr').then(function (q) { if (q.ok) paint(q.data); });
        }, 3000);
      }
    });
  }

  // Telegram: по QR-коду (основной путь) или телефон → код → пароль.
  // 13.11.2026: код из приложения у части владельцев «не проходит» — вход по QR без кода.
  var tgTimer = null;
  function openTg() {
    var d = S.dialog({ title: 'Подключить Telegram', size: 'sm', body: '<div id="tg-box"></div>', onClose: function () { clearInterval(tgTimer); tgTimer = null; loadChannels(); } });
    var box = S.$('#tg-box', d.el);
    var phone = window.matchMedia && window.matchMedia('(max-width: 760px)').matches;
    var qrSince = 0;
    function stopPoll() { clearInterval(tgTimer); tgTimer = null; }
    function poll() {
      if (tgTimer) return;
      tgTimer = setInterval(function () {
        S.api('/telegram-account').then(function (r) { if (r.ok && box.isConnected) step(r.data); });
      }, 2000);
    }
    function qrStart() {
      box.innerHTML = '<div class="st-qr"><div class="skeleton" style="width:240px;height:240px;border-radius:12px"></div><div class="muted small">Готовим QR-код…</div></div>';
      qrSince = Date.now();
      S.api('/telegram-account/qr/start', { method: 'POST' }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); step({ status: '' }); return; }
        step(r.data);
      });
    }
    function phoneLink() { return '<button class="btn btn-ghost btn-sm" type="button" id="tg-byphone">Войти по номеру телефона и коду</button>'; }
    function bindPhoneLink() { var b = S.$('#tg-byphone', d.el); if (b) b.addEventListener('click', function () { stopPoll(); step({ status: 'phone' }); }); }
    function step(s) {
      var status = s.status || '';
      if (status === 'pending_qr') {
        poll();
        var stale = !s.qr && Date.now() - qrSince > 15000;
        if (s.qr_error === 'qr_render_failed') stale = true;
        box.innerHTML = '<div class="st-qr">' +
          (s.qr ? '<img src="' + esc(s.qr) + '" alt="QR-код для входа в Telegram">'
            : (stale ? '<div class="note" style="max-width:300px">QR-код не обновился. Нажмите «Новый QR-код».</div>' : '<div class="skeleton" style="width:240px;height:240px;border-radius:12px"></div>')) +
          '<ol class="st-tgsteps"><li>Откройте Telegram на телефоне</li><li><b>Настройки → Устройства → Подключить устройство</b></li><li>Наведите камеру на этот код</li></ol>' +
          (phone && s.qr_link ? '<a class="btn btn-primary" href="' + esc(s.qr_link) + '">' + icon('send') + '<span>Открыть в Telegram на этом телефоне</span></a>' : '') +
          '<div class="muted small" style="text-align:center;max-width:300px">Код обновляется сам каждые полминуты — окно можно не закрывать.</div>' +
          '<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:center">' + (stale ? '<button class="btn btn-sm" type="button" id="tg-qrnew">Новый QR-код</button>' : '') + phoneLink() + '</div></div>';
        var nb = S.$('#tg-qrnew', d.el); if (nb) nb.addEventListener('click', qrStart);
        bindPhoneLink();
        return;
      }
      stopPoll();
      if (status === 'pending_code') {
        box.innerHTML = '<p class="muted" style="margin-top:0">Код отправлен в приложение Telegram на номер ' + esc(s.phone_masked || '') + '. Пересылать его никому нельзя: Telegram тогда отменяет вход.</p>' +
          '<div class="field"><label class="label">Код из Telegram</label><input class="input" id="tg-code" inputmode="numeric" autocomplete="one-time-code"></div>' +
          '<button class="btn btn-primary" id="tg-go" type="button">Подтвердить</button>' +
          '<div style="margin-top:12px"><button class="btn btn-ghost btn-sm" type="button" id="tg-toqr">' + icon('layout-grid') + '<span>Код не подходит? Войти по QR-коду</span></button></div>';
        S.$('#tg-go', d.el).addEventListener('click', function () {
          var code = S.$('#tg-code', d.el).value.trim(); if (!code) return;
          call('/telegram-account/verify-code', { code: code });
        });
        S.$('#tg-toqr', d.el).addEventListener('click', qrStart);
      } else if (status === 'pending_password') {
        box.innerHTML = '<p class="muted" style="margin-top:0">У аккаунта включена двухэтапная проверка. Введите облачный пароль Telegram.</p>' +
          '<div class="field"><label class="label">Пароль</label><input class="input" id="tg-pw" type="password" autocomplete="off"></div>' +
          '<button class="btn btn-primary" id="tg-go" type="button">Подтвердить</button>';
        S.$('#tg-go', d.el).addEventListener('click', function () {
          var pw = S.$('#tg-pw', d.el).value; if (!pw) return;
          call('/telegram-account/verify-password', { password: pw });
        });
      } else if (status === 'connected') {
        box.innerHTML = '<div class="st-qr"><div class="st-empty-ic">' + icon('circle-check') + '</div><b>Telegram подключён</b><div id="tg-scope" style="width:100%"></div></div>';
        scopeStep(S.$('#tg-scope', d.el), '/telegram-account/settings', d.close);
      } else if (status === 'phone') {
        box.innerHTML = '<p class="muted" style="margin-top:0">Введите номер телефона, к которому привязан Telegram. Код придёт в приложение Telegram.</p>' +
          '<div class="field"><label class="label">Номер телефона</label><input class="input" id="tg-phone" placeholder="+996 700 123 456" inputmode="tel"></div>' +
          '<button class="btn btn-primary" id="tg-go" type="button">Получить код</button>' +
          '<div style="margin-top:12px"><button class="btn btn-ghost btn-sm" type="button" id="tg-toqr">' + icon('layout-grid') + '<span>Войти по QR-коду</span></button></div>';
        S.$('#tg-go', d.el).addEventListener('click', function () {
          var p = S.$('#tg-phone', d.el).value.trim(); if (!p) return;
          call('/telegram-account/start', { phone: p });
        });
        S.$('#tg-toqr', d.el).addEventListener('click', qrStart);
      } else {
        var why = status === 'qr_expired' ? '<div class="note" style="margin-bottom:12px">QR-код устарел: его не отсканировали за 3 минуты. Покажем новый.</div>'
          : (status === 'error' && s.last_error ? '<div class="note note-danger" style="margin-bottom:12px">Не получилось: ' + esc(s.last_error) + '. Попробуйте ещё раз.</div>' : '');
        box.innerHTML = why + '<p class="muted" style="margin-top:0">Бот будет отвечать клиентам от имени вашего аккаунта Telegram. Быстрее всего — по QR-коду, без кода подтверждения.</p>' +
          '<button class="btn btn-primary" id="tg-qr" type="button" style="width:100%;justify-content:center">' + icon('layout-grid') + '<span>Показать QR-код</span></button>' +
          '<div style="margin-top:10px;text-align:center">' + phoneLink() + '</div>';
        S.$('#tg-qr', d.el).addEventListener('click', qrStart);
        bindPhoneLink();
      }
    }
    function call(path, body) {
      var go = S.$('#tg-go', d.el); if (go) go.disabled = true;
      S.api(path, { method: 'POST', body: body }).then(function (r) {
        if (!r.ok) { if (go) go.disabled = false; S.toast(S.apiError(r), 'error'); return; }
        step(r.data);
      });
    }
    var cur = ch.tg || {};
    if (cur.status === 'pending_qr') qrSince = Date.now();
    step(cur.status === 'pending_qr' ? { status: '' } : cur);
  }

  // Instagram, Gmail, Google Календарь: OAuth через Composio (окно входа, результат приходит сообщением)
  function connectToolkit(toolkit) {
    S.api('/connectors/' + toolkit + '/connect', { method: 'POST' }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      if (r.data.connected) { S.toast('Подключено', 'success'); loadChannels(); return; }
      if (!r.data.redirect_url) { S.toast('Не удалось начать подключение', 'error'); return; }
      var w = window.open(r.data.redirect_url, 'connect_' + toolkit, 'width=520,height=720');
      var onMsg = function (e) {
        if (e.data && e.data.type === 'connector_result') {
          window.removeEventListener('message', onMsg);
          S.toast(e.data.status === 'success' ? 'Подключено' : 'Подключение не удалось', e.data.status === 'success' ? 'success' : 'error');
          loadChannels();
        }
      };
      window.addEventListener('message', onMsg);
      // Окно могло не прислать итог (вход в отдельной вкладке): вернулись на страницу —
      // перечитываем каналы, сервер сам сверит подключение с Instagram/Google.
      var onFocus = function () { window.removeEventListener('focus', onFocus); setTimeout(loadChannels, 800); };
      window.addEventListener('focus', onFocus);
      if (!w) location.href = r.data.redirect_url;
    });
  }

  // ══════════════════════════════ ГОЛОСОВЫЕ СООБЩЕНИЯ ══════════════════════
  var vm = null, vmOpts = null, vmAudio = null;
  var STAB = [[0, 'Creative: эмоциональнее'], [0.5, 'Natural: ровная речь'], [1, 'Robust: максимально стабильно']];
  var MODES = [['on_voice', 'В ответ на голосовое клиента'], ['auto', 'Бот решает сам'], ['never', 'Никогда, только текст']];

  function loadVoiceMsg() {
    Promise.all([S.api('/voice-messages'), S.api('/api/eleven-assistants/options')]).then(function (res) {
      var b = S.$('#b-voicemsg');
      if (!res[0].ok) { b.innerHTML = '<div class="st-empty">' + esc(S.apiError(res[0])) + '</div>'; return; }
      vm = res[0].data; vmOpts = res[1].ok ? res[1].data : {};
      drawVoiceMsg();
    });
  }

  function saveVm(body, quiet) {
    return S.api('/voice-messages', { method: 'PUT', body: body }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return r; }
      vm = r.data; if (!quiet) S.toast('Сохранено', 'success');
      return r;
    });
  }

  function drawVoiceMsg() {
    var b = S.$('#b-voicemsg');
    if (!vm.configured) { b.innerHTML = '<div class="note note-warning">Озвучка не настроена на сервере. Напишите в поддержку.</div>'; return; }
    var engine = vm.engine;
    var chans = vm.channels || {};
    // Все три канала видны всегда (как у Profi): подключённый — с выбором режима, неподключённый — с подсказкой
    var chRows = [['telegram', 'Telegram', 'send', '#229ED9'], ['whatsapp', 'WhatsApp', 'message-circle', '#25D366']].map(function (c) {
      var k = c[0];
      if (!(k in chans)) {
        return '<div class="st-row-card" style="margin-bottom:10px"><span class="logo" style="--c:' + c[3] + '">' + icon(c[2]) + '</span>' +
          '<div class="info"><b>' + c[1] + '</b><div>Не подключён. Подключите в разделе «Каналы», и здесь появится выбор.</div></div>' +
          '<a class="btn btn-sm" href="#channels" data-goto="s-channels">Каналы</a></div>';
      }
      return '<div class="field" style="margin-bottom:10px"><label class="label">' + c[1] + ': когда отвечать голосом</label><select class="select-native" data-vch="' + k + '">' +
        MODES.map(function (m) { return '<option value="' + m[0] + '"' + (m[0] === chans[k] ? ' selected' : '') + '>' + m[1] + '</option>'; }).join('') + '</select></div>';
    }).join('') +
      '<div class="st-row-card" style="margin-bottom:10px"><span class="logo" style="--c:#E1306C">' + icon('instagram') + '</span>' +
      '<div class="info"><b>Instagram</b><div>Голосовые недоступны: Instagram через наш коннектор принимает только текст и фото.</div></div></div>';
    b.innerHTML =
      '<div class="st-engine">' + [['eleven', 'Voksy Voice', 'Живые голоса, кыргызский и русский'], ['openai', 'Voksy Live', 'Самый живой голос, естественная интонация']].map(function (e) {
        var avail = vm.engines ? vm.engines[e[0]] !== false : true;
        return '<label class="st-engine-item' + (engine === e[0] ? ' on' : '') + (avail ? '' : ' dis') + '"><input type="radio" name="vm-engine" value="' + e[0] + '"' + (engine === e[0] ? ' checked' : '') + (avail ? '' : ' disabled') + '><b>' + e[1] + '</b><span>' + e[2] + '</span></label>';
      }).join('') + '</div>' +
      '<div id="vm-eleven" style="margin-top:14px"' + (engine === 'eleven' ? '' : ' hidden') + '>' +
      '<div class="st-grid2"><div class="field"><label class="label">Язык</label><select class="select-native" id="vm-lang"><option value="">Как в звонках</option>' +
      (vmOpts.languages || []).map(function (l) { return '<option value="' + esc(l.code) + '"' + (l.code === vm.language ? ' selected' : '') + '>' + esc(l.title) + '</option>'; }).join('') + '</select></div>' +
      '<div class="field"><label class="label">Подача</label><select class="select-native" id="vm-stab">' + STAB.map(function (x) {
        var cur = vm.stability != null ? vm.stability : vm.default_stability;
        return '<option value="' + x[0] + '"' + (Number(cur) === x[0] ? ' selected' : '') + '>' + x[1] + '</option>';
      }).join('') + '</select></div></div>' +
      '<div class="field"><label class="label">Голос</label><select class="select-native" id="vm-voice"><option value="">Как в звонках</option></select></div></div>' +
      '<div id="vm-openai" style="margin-top:14px"' + (engine === 'openai' ? '' : ' hidden') + '><div class="field"><label class="label">Голос Voksy Live</label><select class="select-native" id="vm-ovoice">' +
      (vm.openai_voices || []).map(function (v) { return '<option value="' + esc(v) + '"' + (v === vm.openai_voice ? ' selected' : '') + '>' + esc(v) + '</option>'; }).join('') + '</select></div></div>' +
      '<div class="st-sub-h">Когда бот отвечает голосом</div>' + chRows +
      '<div class="field" id="vm-share-wrap"><label class="label">Доля голосовых, когда бот решает сам</label><select class="select-native" id="vm-share">' +
      (vm.auto_share_choices || [30, 40, 50]).map(function (v) { return '<option value="' + v + '"' + (v === vm.auto_share ? ' selected' : '') + '>около ' + v + '% ответов</option>'; }).join('') + '</select>' +
      '<div class="hint">В режиме «Бот решает сам» он чередует текст и голосовые примерно в этой пропорции. На голосовое клиента всегда отвечает голосом, короткие «да» и «минуту» текстом. Цены, адреса и списки вопросов присылает отдельным текстом.</div></div>' +
      '<div class="field"><label class="label">Прослушать</label><textarea class="input" id="vm-text" rows="2" maxlength="400" placeholder="Свой текст для примера (необязательно). Можно метки: [softly] Так, смотрите… [short pause] доставка завтра."></textarea></div>' +
      '<div class="row" style="gap:10px;margin-top:6px"><button class="btn" id="vm-play" type="button">' + icon('play') + '<span>Прослушать</span></button>' +
      '<span class="muted small">Прослушивание и каждое голосовое списывают кредиты бота (≈' + (vm.credits_per_400_chars || 0) + ' за 400 знаков).</span></div>';

    function loadVoices() {
      var sel = S.$('#vm-voice'); if (!sel) return;
      var lang = S.$('#vm-lang').value || (vm.call_voice && vm.call_voice.language) || 'ky';
      S.api('/api/eleven-assistants/voices?language=' + lang).then(function (r) {
        var v = (r.ok && r.data.voices) || [];
        vmVoices = v;
        sel.innerHTML = '<option value="">Как в звонках</option>' + v.slice(0, 100).map(function (x) {
          return '<option value="' + esc(x.voice_id) + '" data-name="' + esc(x.name) + '"' + (x.voice_id === vm.voice_id ? ' selected' : '') + '>' + esc(x.name) + (x.recommended ? ' — подходит для языка' : '') + '</option>';
        }).join('');
      });
    }
    var vmVoices = [];
    S.$$('input[name="vm-engine"]', b).forEach(function (r) {
      r.addEventListener('change', function () {
        saveVm({ engine: r.value }, true).then(function () { drawVoiceMsg(); S.toast('Озвучка переключена', 'success'); });
      });
    });
    S.$('#vm-lang').addEventListener('change', function () { saveVm({ language: this.value || null }); loadVoices(); });
    S.$('#vm-stab').addEventListener('change', function () { saveVm({ stability: Number(this.value) }); });
    S.$('#vm-voice').addEventListener('change', function () {
      var o = this.selectedOptions[0];
      saveVm({ voice_id: this.value || null, voice_name: this.value ? (o.dataset.name || '') : null });
    });
    S.$('#vm-ovoice').addEventListener('change', function () { saveVm({ openai_voice: this.value }); });
    var sh = S.$('#vm-share'); if (sh) sh.addEventListener('change', function () { saveVm({ auto_share: Number(this.value) }); });
    S.$$('[data-goto]', b).forEach(function (a) {
      a.addEventListener('click', function (e) { e.preventDefault(); showSection(a.dataset.goto); });
    });
    S.$$('[data-vch]', b).forEach(function (sel) {
      sel.addEventListener('change', function () {
        var path = sel.dataset.vch === 'whatsapp' ? '/whatsapp/settings' : '/telegram-account/settings';
        S.api(path, { method: 'PATCH', body: { voice_reply_mode: sel.value } }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          vm.channels[sel.dataset.vch] = sel.value;
          S.toast('Сохранено', 'success');
        });
      });
    });
    S.$('#vm-play').addEventListener('click', function () {
      var btn = this; btn.disabled = true;
      var body = { engine: S.$('input[name="vm-engine"]:checked', b).value };
      var txt = (S.$('#vm-text', b).value || '').trim(); if (txt) body.text = txt;
      if (body.engine === 'openai') body.openai_voice = S.$('#vm-ovoice').value;
      else { body.voice_id = S.$('#vm-voice').value || null; body.language = S.$('#vm-lang').value || null; body.stability = Number(S.$('#vm-stab').value); }
      var headers = { 'Content-Type': 'application/json' }; var t = S.token(); if (t) headers.Authorization = 'Bearer ' + t;
      fetch('/api/agent/voice-messages/preview', { method: 'POST', headers: headers, body: JSON.stringify(body) }).then(function (r) {
        if (!r.ok) return r.json().then(function (d) {
          var det = d.detail || d.message; throw new Error(det && det.provider_message ? det.provider_message : S.errText(det));
        });
        return r.blob();
      }).then(function (blob) {
        if (vmAudio) vmAudio.pause();
        vmAudio = new Audio(URL.createObjectURL(blob)); vmAudio.play();
      }).catch(function (e) { S.toast(e.message || 'Не удалось озвучить', 'error'); })
        .then(function () { btn.disabled = false; });
    });
    loadVoices();
  }

  // ══════════════════════════════ ДОКУМЕНТЫ БОТА (фирменный стиль) ══════════
  var BRAND_COLORS = [['#2a5ce8', 'Синий Voksy AI'], ['#0f766e', 'Изумрудный'], ['#b91c1c', 'Красный'], ['#7c3aed', 'Фиолетовый'], ['#c2410c', 'Оранжевый'], ['#1f2937', 'Графитовый']];

  function loadDocs() {
    var b = S.$('#b-docs'); if (!b) return;
    var brand = { color: A.brand_color || '', company: A.brand_company_name || '', contacts: A.brand_contacts || '', logoId: A.brand_logo_file_id || null, logoUrl: null };
    function draw() {
      b.innerHTML =
        '<div class="field"><label class="label">Логотип</label><div class="row" style="gap:14px">' +
        '<div class="st-logo">' + (brand.logoUrl ? '<img src="' + esc(brand.logoUrl) + '" alt="Логотип">' : brand.logoId ? '<span class="muted small">Загружен</span>' : '<span class="muted small">Нет</span>') + '</div>' +
        '<div><button class="btn btn-sm" id="br-up" type="button">' + icon('upload') + '<span>' + (brand.logoId ? 'Заменить' : 'Загрузить логотип') + '</span></button>' +
        (brand.logoId ? ' <button class="btn btn-ghost btn-sm" id="br-rm" type="button">Убрать</button>' : '') +
        '<div class="hint" style="margin-top:6px">PNG с прозрачным фоном выглядит лучше всего. Логотип встанет в шапку документа слева.</div></div></div></div>' +
        '<div class="field"><label class="label">Цвет шапки</label><div class="st-swatches">' + BRAND_COLORS.map(function (c) {
          return '<button type="button" class="st-swatch' + (brand.color.toLowerCase() === c[0] ? ' on' : '') + '" data-col="' + c[0] + '" style="background:' + c[0] + '" title="' + esc(c[1]) + '" aria-label="' + esc(c[1]) + '">' + icon('check') + '</button>';
        }).join('') + '<input class="input" id="br-col" maxlength="7" placeholder="#2a5ce8" value="' + esc(brand.color) + '" style="width:120px"></div><div class="hint">Свой цвет в формате #RRGGBB. Пусто: синий Voksy AI.</div></div>' +
        '<div class="st-grid2"><div class="field"><label class="label">Название компании</label><input class="input" id="br-name" maxlength="255" value="' + esc(brand.company) + '" placeholder="ОсОО «Ваша компания»"><div class="hint">В шапке рядом с логотипом и в подвале.</div></div>' +
        '<div class="field"><label class="label">Контакты в подвале</label><input class="input" id="br-contacts" maxlength="500" value="' + esc(brand.contacts) + '" placeholder="сайт · телефон · почта"><div class="hint">Одной строкой, внизу каждой страницы.</div></div></div>' +
        '<button class="btn btn-primary" id="br-save" type="button">Сохранить оформление</button>';
      S.$$('[data-col]', b).forEach(function (x) { x.addEventListener('click', function () { brand.color = x.dataset.col; S.$('#br-col').value = brand.color; S.$$('.st-swatch', b).forEach(function (y) { y.classList.toggle('on', y === x); }); }); });
      on('br-save', function () {
        brand.color = S.$('#br-col').value.trim(); brand.company = S.$('#br-name').value.trim(); brand.contacts = S.$('#br-contacts').value.trim();
        if (brand.color && !/^#[0-9a-fA-F]{6}$/.test(brand.color)) { S.toast('Цвет нужен в формате #RRGGBB', 'error'); return; }
        var body = { brand_company_name: brand.company || null, brand_color: brand.color || null, brand_contacts: brand.contacts || null, brand_logo_file_id: brand.logoId || null };
        put(body).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          Object.keys(body).forEach(function (k) { A[k] = body[k]; }); S.toast('Оформление сохранено', 'success');
        });
      });
      on('br-rm', function () { brand.logoId = null; brand.logoUrl = null; draw(); });
      on('br-up', function () {
        var inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/png,image/jpeg,image/webp';
        inp.addEventListener('change', function () {
          var f = inp.files[0]; if (!f) return;
          S.upload('/files', f, { title: 'Логотип компании', description: 'Логотип для шапки документов бота' }).then(function (r) {
            if (!r.ok) { S.toast(S.errText(r.data.detail === 'storage_not_configured' ? 'Хранилище файлов не настроено' : r.data.detail || r.data), 'error'); return; }
            brand.logoId = r.data.file.id; fetchLogo(); S.toast('Логотип загружен. Нажмите «Сохранить оформление»', 'success');
          });
        });
        inp.click();
      });
    }
    function fetchLogo() {
      if (!brand.logoId) { draw(); return; }
      S.api('/files/' + brand.logoId + '/link').then(function (r) { brand.logoUrl = r.ok ? r.data.url : null; draw(); });
    }
    fetchLogo();
  }

  // ══════════════════════════════ ВХОДЯЩИЕ ЗВОНКИ ══════════════════════════
  function loadCalls() {
    S.api('/phone-numbers').then(function (r) {
      var b = S.$('#b-calls');
      var nums = (r.ok && r.data.phone_numbers) || [];
      if (!nums.length) {
        b.innerHTML = '<div class="note">Номера пока нет. Подключите номер на странице <a href="/static/telephony.html">«Телефония»</a>, и клиенты смогут звонить боту.</div>' + greetingField();
        bindGreeting(); return;
      }
      var sel = r.data.selected_number_id || '';
      b.innerHTML = '<div class="field"><label class="label">Номер, на который отвечает бот</label><select class="select-native" id="ph-sel"><option value="">Не выбран</option>' +
        nums.map(function (n) { return '<option value="' + esc(n.id) + '"' + (n.id === sel ? ' selected' : '') + '>' + esc(S.fmtPhone(n.phone_number)) + (n.label ? ' · ' + esc(n.label) : '') + '</option>'; }).join('') + '</select>' +
        '<div class="hint">Входящие звонки на этот номер принимает бот. Исходящие звонки на тарифе Старт — вручную со страницы «Телефония».</div></div>' + greetingField();
      S.$('#ph-sel').addEventListener('change', function () {
        S.api('/phone-number', { method: 'PUT', body: { number_id: this.value || null } }).then(function (x) {
          if (!x.ok) { S.toast(S.apiError(x), 'error'); return; }
          S.toast('Номер сохранён', 'success');
        });
      });
      bindGreeting();
    });
  }
  function greetingField() {
    return '<div class="field"><label class="label" for="g-phrase">Первая фраза бота при входящем звонке</label>' +
      '<input class="input" id="g-phrase" maxlength="500" value="' + esc(A.inbound_first_phrase || '') + '" placeholder="Здравствуйте! Кофейня «Алатоо», чем могу помочь?">' +
      '<div class="hint">Если оставить пустой, бот поздоровается сам.</div></div><button class="btn btn-primary btn-sm" id="g-save" type="button">Сохранить фразу</button>';
  }
  function bindGreeting() {
    on('g-save', function () {
      var v = S.$('#g-phrase').value.trim();
      put({ inbound_first_phrase: v }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        A.inbound_first_phrase = v; S.toast('Сохранено', 'success');
      });
    });
  }

  // ══════════════════════════════ ОПОВЕЩЕНИЯ ═══════════════════════════════
  // Как это работает: вы создаёте своего Telegram-бота у @BotFather и вставляете токен. Бот пишет ВАМ
  // (в личку или в группу) о новых клиентах, итогах звонков и итогах дня. Куда именно писать, определяют
  // привязанные чаты: привяжите свой Telegram кнопкой (нажать Start в боте) или добавьте chat_id вручную.
  var tgBot = null, tgPoll = null;
  function loadNotify() {
    S.api('/telegram').then(function (r) { tgBot = r.ok ? r.data : null; drawNotify(); });
  }

  function drawNotify() {
    clearInterval(tgPoll);
    var b = S.$('#b-notify'), t = tgBot;
    if (!t || !t.is_configured) {
      b.innerHTML =
        '<div class="note" style="margin-bottom:14px"><div>Оповещения приходят в Telegram от <b>вашего собственного бота</b>. Он нужен только для того, чтобы бот писал вам о клиентах. Создаётся за минуту.</div></div>' +
        '<ol class="st-steps"><li>Откройте в Telegram <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a>.</li>' +
        '<li>Отправьте команду <code>/newbot</code>, придумайте имя и логин бота (логин заканчивается на <code>bot</code>).</li>' +
        '<li>BotFather пришлёт токен вида <code>123456:ABC-DEF…</code>. Скопируйте его целиком и вставьте ниже.</li>' +
        '<li>Нажмите «Подключить», затем «Подключить мой Telegram» и нажмите Start в боте.</li></ol>' +
        '<div class="field"><label class="label">Токен бота</label><input class="input" id="n-token" placeholder="123456:ABC-DEF…" autocomplete="off"></div>' +
        '<button class="btn btn-primary" id="n-connect" type="button">Подключить</button>';
      on('n-connect', function () {
        var tok = S.$('#n-token').value.trim(); if (!tok) { S.toast('Вставьте токен бота', 'error'); return; }
        this.disabled = true;
        S.api('/telegram', { method: 'PUT', body: { bot_token: tok } }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); loadNotify(); return; }
          tgBot = r.data; drawNotify(); S.toast('Бот подключён. Теперь привяжите свой Telegram', 'success');
        });
      });
      return;
    }
    var n = t.notify || {};
    var chats = t.chat_ids || [];
    var noChats = !chats.length;
    b.innerHTML =
      (noChats
        ? '<div class="note note-warning" style="margin-bottom:14px"><div><b>Оповещения пока некуда отправлять.</b> Привяжите свой Telegram: нажмите кнопку ниже, откроется бот, нажмите там Start.</div></div>'
        : '') +
      '<div class="st-row-card"><span class="logo" style="--c:#229ED9">' + icon('send') + '</span><div class="info"><b>@' + esc(t.bot_username || '') + '</b>' +
      '<div>' + (noChats ? 'Чат не привязан' : 'Привязано чатов: ' + chats.length) + (t.enabled ? '' : ' · оповещения выключены') + '</div></div>' +
      (t.link_url ? '<a class="btn btn-primary btn-sm" id="n-link" href="' + esc(t.link_url) + '" target="_blank" rel="noopener">' + icon('send') + '<span>Подключить мой Telegram</span></a>' : '') + '</div>' +

      '<div class="st-note" style="margin-top:14px">В подключённом чате бот отвечает и на ваши вопросы: «какая статистика за неделю», «найди клиента Айбек», «какие задачи». На тарифе Старт он только читает данные и отвечает; команды «позвони», «напиши», «поставь задачу» доступны на тарифе Profi.</div>' +
      '<div class="st-switch-row" style="margin-top:14px"><div class="t"><b>Оповещения включены</b><div>Общий выключатель. Выключите, если пока не хотите получать сообщения.</div></div>' +
      '<label class="switch"><input type="checkbox" id="n-on"' + (t.enabled ? ' checked' : '') + '><span class="track"></span></label></div>' +
      '<div class="st-sub-h" style="margin-top:14px">Что присылать</div>' +
      notifyRow('n-lead', 'Новый клиент', 'Когда кто-то впервые написал или позвонил: имя, телефон, первое сообщение.', n.new_lead) +
      notifyRow('n-call', 'Итог звонка', 'После разговора: коротко, о чём поговорили и что решил бот.', n.call_summary) +
      notifyRow('n-day', 'Итоги дня', 'Каждый вечер в 20:00 по Бишкеку: сколько обращений, звонков и сделок, а также что закончилось на складе.', n.daily_digest) +
      notifyRow('n-stock', 'Склад', 'Сразу, когда товар закончился, осталось мало (до 3 шт.) или снова появился в наличии.', n.stock !== false) +

      '<div class="st-sub-h" style="margin-top:18px">Куда присылать</div>' +
      (chats.length ? chats.map(function (c) {
        return '<div class="row-between" style="padding:7px 0;border-bottom:1px solid var(--vf-border)"><span>' + esc(c.title || 'Чат') + ' <span class="muted small mono">· ' + esc(c.chat_id) + '</span></span><button class="btn btn-ghost btn-sm" data-rm="' + esc(c.chat_id) + '" type="button">Убрать</button></div>';
      }).join('') : '<div class="muted small">Пока ни одного чата.</div>') +
      '<details class="st-details"><summary>Добавить чат вручную (группа, канал или другой человек)</summary>' +
      '<p class="muted small" style="margin:8px 0">Добавьте бота в группу или напишите ему, затем отправьте ему <code>/start</code>: он ответит номером чата (chat_id). Либо узнайте номер у <a href="https://t.me/userinfobot" target="_blank" rel="noopener">@userinfobot</a>. Для групп номер начинается с минуса.</p>' +
      '<div class="st-grid2"><div class="field"><label class="label">chat_id</label><input class="input" id="n-cid" placeholder="например 123456789 или -1001234567890"></div>' +
      '<div class="field"><label class="label">Название</label><input class="input" id="n-ctitle" placeholder="Например: Отдел продаж"></div></div>' +
      '<button class="btn btn-sm" id="n-cadd" type="button">' + icon('plus') + '<span>Добавить чат</span></button></details>' +

      '<div class="row" style="margin-top:18px;gap:8px;flex-wrap:wrap"><button class="btn btn-sm" id="n-test" type="button">Отправить пробное сообщение</button>' +
      '<button class="btn btn-ghost btn-sm" id="n-off" type="button">Отключить бота</button></div>';

    on('n-on', function () { patchTg('/telegram/enabled', { enabled: this.checked }); });
    on('n-lead', function () { patchTg('/telegram/notify', { new_lead: this.checked }); });
    on('n-call', function () { patchTg('/telegram/notify', { call_summary: this.checked }); });
    on('n-day', function () { patchTg('/telegram/notify', { daily_digest: this.checked }); });
    on('n-stock', function () { patchTg('/telegram/notify', { stock: this.checked }); });
    on('n-test', function () {
      S.api('/telegram/test', { method: 'POST' }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast(r.data.sent ? 'Сообщение отправлено в Telegram (' + r.data.sent + ' из ' + r.data.total + ')' : 'Не удалось доставить. Откройте бота в Telegram и нажмите Start', r.data.sent ? 'success' : 'error');
      });
    });
    on('n-off', function () {
      S.confirm({ title: 'Отключить Telegram-бота?', message: 'Оповещения перестанут приходить.', confirmText: 'Отключить', danger: true }).then(function (ok) {
        if (!ok) return;
        S.api('/telegram', { method: 'DELETE' }).then(function (r) { if (r.ok) { tgBot = r.data; drawNotify(); } });
      });
    });
    on('n-cadd', function () {
      var cid = S.$('#n-cid').value.trim(); if (!cid) { S.toast('Введите chat_id', 'error'); return; }
      S.api('/telegram/chats', { method: 'POST', body: { chat_id: cid, title: S.$('#n-ctitle').value.trim() || null } }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        tgBot = r.data; drawNotify(); S.toast('Чат добавлен', 'success');
      });
    });
    S.$$('[data-rm]', b).forEach(function (x) {
      x.addEventListener('click', function () {
        S.api('/telegram/chats/' + encodeURIComponent(x.dataset.rm), { method: 'DELETE' }).then(function (r) { if (r.ok) { tgBot = r.data; drawNotify(); } });
      });
    });
    // После нажатия «Подключить мой Telegram» ждём, пока в боте нажмут Start: чат появится сам
    var link = S.$('#n-link');
    if (link) link.addEventListener('click', function () {
      var before = chats.length, tries = 0;
      clearInterval(tgPoll);
      tgPoll = setInterval(function () {
        if (++tries > 40) { clearInterval(tgPoll); return; }
        S.api('/telegram').then(function (r) {
          if (r.ok && (r.data.chat_ids || []).length > before) {
            clearInterval(tgPoll); tgBot = r.data; drawNotify(); S.toast('Telegram привязан. Оповещения включены', 'success');
          }
        });
      }, 3000);
    });
  }
  function notifyRow(id, title, sub, on_) {
    return '<div class="st-switch-row"><div class="t"><b>' + title + '</b><div>' + sub + '</div></div><label class="switch"><input type="checkbox" id="' + id + '"' + (on_ ? ' checked' : '') + '><span class="track"></span></label></div>';
  }
  function patchTg(path, body) {
    S.api(path, { method: 'PATCH', body: body }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); loadNotify(); return; }
      tgBot = r.data; S.toast('Сохранено', 'success');
    });
  }

  // ══════════════════════════════ GOOGLE ТАБЛИЦА ═══════════════════════════
  function loadSheet() {
    S.api('/sheet').then(function (r) {
      var b = S.$('#b-sheet'); if (!r.ok) { b.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      var s = r.data;
      if (!s.connected) {
        b.innerHTML = '<ol class="muted" style="margin:0 0 14px 18px;padding:0;line-height:1.7">' +
          '<li>Создайте Google Таблицу.</li><li>Нажмите «Настройки доступа» и добавьте как редактора адрес: <b id="sh-mail">' + esc(s.service_email || 'не настроен') + '</b></li>' +
          '<li>Вставьте ссылку на таблицу ниже.</li></ol>' +
          '<div class="field"><label class="label">Ссылка на таблицу</label><input class="input" id="sh-url" placeholder="https://docs.google.com/spreadsheets/d/…"></div>' +
          '<button class="btn btn-primary" id="sh-go" type="button">Подключить</button>';
        on('sh-go', function () {
          var url = S.$('#sh-url').value.trim(); if (!url) return;
          this.disabled = true;
          S.api('/sheet', { method: 'POST', body: { url: url } }).then(function (x) {
            if (!x.ok) { S.toast(S.apiError(x), 'error'); loadSheet(); return; }
            S.toast('Таблица подключена', 'success'); loadSheet();
          });
        });
        return;
      }
      b.innerHTML = '<div class="st-row-card"><span class="logo" style="--c:#16A34A">' + icon('layers') + '</span><div class="info"><b>' + esc(s.title || 'Таблица') + '</b>' +
        '<div>Новые клиенты записываются на лист «' + esc(s.leads_sheet || 'Заявки') + '»</div></div>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn btn-sm" href="' + esc(s.url) + '" target="_blank" rel="noopener">Открыть</a>' +
        '<button class="btn btn-sm" id="sh-imp" type="button">Загрузить прайс в каталог</button><button class="btn btn-ghost btn-sm" id="sh-off" type="button">Отключить</button></div></div>' +
        '<div class="st-note">Прайс берётся с первого листа: колонки находятся по названиям («Название», «Цена», «Описание»). Повторная загрузка обновляет только позиции из таблицы.</div>';
      on('sh-imp', function () {
        this.disabled = true;
        S.api('/sheet/import-catalog', { method: 'POST' }).then(function (x) {
          loadSheet();
          if (!x.ok) { S.toast(S.apiError(x), 'error'); return; }
          S.toast('Загружено позиций: ' + (x.data.total || 0), 'success'); loadKb();
        });
      });
      on('sh-off', function () { S.api('/sheet', { method: 'DELETE' }).then(function () { loadSheet(); }); });
    });
  }

  // ══════════════════════════════ КРЕДИТЫ ══════════════════════════════════
  function loadCredits() {
    Promise.all([S.api('/api/credits/balance'), S.api('/api/credits/packages')]).then(function (res) {
      var b = S.$('#b-credits');
      if (!res[0].ok) { b.innerHTML = '<div class="st-empty">' + esc(S.apiError(res[0])) + '</div>'; return; }
      var c = res[0].data, pk = res[1].ok ? (res[1].data.packages || []) : [];
      b.innerHTML = '<div class="st-kpis" style="margin-bottom:16px">' +
        '<div class="card st-kpi"><div class="lbl">' + icon('coins') + 'Баланс</div><div class="val">' + Number(c.credits_balance || 0).toLocaleString('ru-RU') + '</div><div class="sub">кредитов бота</div></div>' +
        '<div class="card st-kpi"><div class="lbl">' + icon('calendar') + 'Тариф действует до</div><div class="val" style="font-size:20px">' + esc(c.subscription_end_date ? S.fmtDate(c.subscription_end_date, false) : '—') + '</div><div class="sub">' + (c.days_remaining != null ? 'осталось дней: ' + c.days_remaining : '') + '</div></div></div>' +
        '<p class="muted small" style="margin:0 0 12px">Тариф Старт включает 10 000 кредитов в месяц. Если не хватит, докупите пакет.</p>' +
        (pk.length ? '<div style="display:flex;gap:10px;flex-wrap:wrap">' + pk.map(function (p) {
          return '<button class="btn" type="button" data-pk="' + esc(p.code) + '">' + esc(p.name || p.code) + ' · ' + Number(p.credits || 0).toLocaleString('ru-RU') + ' кредитов · ' + esc(S.fmtMoney(p.price_rub)) + '</button>';
        }).join('') + '</div>' : '');
      S.$$('[data-pk]', b).forEach(function (x) {
        x.addEventListener('click', function () {
          x.disabled = true;
          S.api('/api/credits/purchase', { method: 'POST', body: { package_code: x.dataset.pk } }).then(function (r) {
            x.disabled = false;
            if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
            var url = r.data.payment_url || r.data.url;
            if (url) location.href = url; else S.toast('Не удалось открыть оплату', 'error');
          });
        });
      });
    });
  }
})();
