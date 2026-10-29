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
  function setupScroll() {
    S.$$('.st-nav a').forEach(function (a) {
      a.addEventListener('click', function (e) { e.preventDefault(); showSection(a.getAttribute('href').slice(1)); });
    });
    var h = (location.hash || '').replace('#', '');
    showSection(TAB_ALIAS[h] || h || 's-main', false);
    window.addEventListener('hashchange', function () {
      var x = (location.hash || '').replace('#', ''); showSection(TAB_ALIAS[x] || x || 's-main', false);
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
    var voice = A.eleven_voice_engine === 'gpt_live' ? 'GPT-Live · ' + (A.eleven_live_voice || 'marin') : (A.eleven_voice_name || 'ElevenLabs');
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

  function drawVoiceBox() {
    var box = S.$('#voice-box');
    if (!box) return;
    if (!voiceOpts) { box.innerHTML = '<div class="muted small">Не удалось загрузить список голосов.</div>'; return; }
    var engine = A.eleven_voice_engine === 'gpt_live' ? 'gpt_live' : 'eleven';
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
      '<div class="field" id="v-live-wrap"><label class="label">Голос GPT-Live</label><select class="select-native" id="v-live">' +
      (voiceOpts.live_voices || []).map(function (v) { return '<option value="' + esc(v) + '"' + (v === (A.eleven_live_voice || voiceOpts.default_live_voice) ? ' selected' : '') + '>' + esc(v) + '</option>'; }).join('') + '</select></div>' +
      '<button class="btn btn-primary" id="v-save" type="button">Сохранить голос</button>';
    var voices = [];
    function toggle() {
      var live = S.$('#v-engine').value === 'gpt_live';
      S.$('#v-eleven-wrap').style.display = live ? 'none' : '';
      S.$('#v-lang-wrap').style.display = live ? 'none' : '';
      S.$('#v-live-wrap').style.display = live ? '' : 'none';
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
      var live = S.$('#v-engine').value === 'gpt_live';
      var body = { eleven_voice_engine: live ? 'gpt_live' : 'eleven' };
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
      '<div class="row" style="gap:8px"><button class="btn btn-primary" id="i-save-chat" type="button">Сохранить</button><button class="btn" id="i-tpl-chat" type="button">' + icon('wand-2') + '<span>Вставить шаблон</span></button></div>' +

      '<div class="st-sub-h" style="margin-top:26px">Инструкция для звонков</div>' +
      '<div class="field"><textarea class="textarea" id="i-vadd" rows="6" placeholder="Стиль общения по телефону, манера, что говорить и чего не говорить">' + esc(A.voice_additional_instructions || '') + '</textarea>' +
      '<div class="hint">Как бот звучит и ведёт себя в телефонном разговоре: темп, тон, что уточнять, как заканчивать звонок. Знания из базы бот берёт сам.</div></div>' +
      '<div class="row" style="gap:8px"><button class="btn btn-primary" id="i-save-voice" type="button">Сохранить</button><button class="btn" id="i-tpl-voice" type="button">' + icon('wand-2') + '<span>Вставить шаблон</span></button></div>' +

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
    function tpl(id, text) {
      var el = S.$(id);
      if (el.value.trim() && !window.confirm('Заменить текст шаблоном?')) return;
      el.value = text; el.focus();
    }
    S.$('#i-tpl-chat').addEventListener('click', function () { tpl('#i-add', TPL_CHAT); });
    S.$('#i-tpl-voice').addEventListener('click', function () { tpl('#i-vadd', TPL_VOICE); });
    S.$('#i-save-chat').addEventListener('click', function () { saveBody({ additional_instructions: S.$('#i-add').value }, this, 'Инструкция для переписки сохранена'); });
    S.$('#i-save-voice').addEventListener('click', function () { saveBody({ voice_additional_instructions: S.$('#i-vadd').value }, this, 'Инструкция для звонков сохранена'); });
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
    var v = kb.voice && kb.voice.chars ? '<div class="st-note" style="margin:0 0 12px">Бот знает наизусть на звонке: ' + Number(kb.voice.chars).toLocaleString('ru-RU') + ' из ' + Number(kb.voice.budget || 0).toLocaleString('ru-RU') + ' символов. Остальное он находит поиском, это чуть дольше.</div>' : '';
    b.innerHTML =
      '<div class="tabs" style="margin:-6px -8px 16px">' + KB_TABS.map(function (t) {
        return '<div class="tab' + (kb.tab === t[0] ? ' active' : '') + '" data-kt="' + t[0] + '"><span>' + t[1] + '</span><span class="st-count" style="margin-left:2px">' + counts[t[0]] + '</span></div>';
      }).join('') + '</div>' + v + '<div id="kb-body"></div>' +
      '<div class="st-sub-h" style="margin-top:22px">Проверить как клиент</div>' +
      '<div class="st-toolbar"><div class="st-search" style="max-width:none">' + icon('search') + '<input class="input" id="kb-q" placeholder="Задайте вопрос, как клиент: «сколько стоит доставка?»"></div>' +
      '<button class="btn" id="kb-ask" type="button">Спросить</button></div><div id="kb-ans"></div>';
    S.$$('[data-kt]', b).forEach(function (t) {
      t.addEventListener('click', function () { kb.tab = t.dataset.kt; drawKb(); });
    });
    drawKbBody();
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
    if (kb.tab === 'product') {
      box.innerHTML = head + (items.length ? '<div class="st-catalog">' + items.map(function (e) {
        var ph = (e.media || [])[0];
        var thumb = ph && ph.url && !ph.is_video ? '<img src="' + esc(ph.url) + '" alt="" loading="lazy">' : '<span class="st-ph">' + icon('image') + '</span>';
        var attrs = Object.keys(e.attributes || {}).slice(0, 3).map(function (k) { return '<span class="st-tag">' + esc(k) + ': ' + esc(e.attributes[k]) + '</span>'; }).join(' ');
        return '<div class="st-prod' + (e.is_active === false ? ' off' : '') + '" data-id="' + esc(e.id) + '"><div class="pic">' + thumb +
          ((e.media || []).length > 1 ? '<span class="cnt">' + (e.media || []).length + '</span>' : '') + '</div>' +
          '<div class="bd"><div class="t">' + esc(e.title || '—') + '</div><div class="pr">' + esc(priceText(e)) + (e.in_stock === false ? ' <span class="st-badge warn">нет в наличии</span>' : '') + '</div>' +
          (attrs ? '<div class="at">' + attrs + '</div>' : '') +
          '<div class="acts"><button class="btn btn-ghost btn-sm" data-edit="' + esc(e.id) + '" type="button">' + icon('pen') + '<span>Изменить</span></button>' +
          '<button class="btn btn-ghost btn-icon btn-sm" data-del="' + esc(e.id) + '" type="button" aria-label="Удалить">' + icon('trash-2') + '</button></div></div></div>';
      }).join('') + '</div>' : '<div class="st-empty">Каталог пуст. Добавьте позиции вручную или загрузите прайс из Google Таблицы (раздел ниже).</div>');
    } else {
      box.innerHTML = head + (items.length ? items.map(function (e) {
        return '<div class="st-qa' + (e.is_active === false ? ' off' : '') + '" data-id="' + esc(e.id) + '"><div class="q">' + esc(e.title || '—') + '</div>' +
          (e.body ? '<div class="a">' + esc(e.body) + '</div>' : '') +
          '<div class="acts"><button class="btn btn-ghost btn-sm" data-edit="' + esc(e.id) + '" type="button">' + icon('pen') + '<span>Изменить</span></button>' +
          '<button class="btn btn-ghost btn-sm" data-del="' + esc(e.id) + '" type="button">' + icon('trash-2') + '<span>Удалить</span></button></div></div>';
      }).join('') : '<div class="st-empty">Пока пусто.</div>');
    }
    S.$('#kb-add').addEventListener('click', function () { entryDialog(kb.tab, null); });
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
          '<div class="field"><label class="label">Фото и видео</label><div class="hint" style="margin-bottom:8px">Бот покажет их клиенту, когда спросят про эту позицию.</div><div class="st-media" id="e-media"></div>' +
          '<div style="margin-top:8px"><button class="btn btn-ghost btn-sm" type="button" id="e-up">' + icon('upload') + '<span>Загрузить фото</span></button></div></div>' : '') +
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
    function drawMedia() {
      var box = S.$('#e-media', d.el); if (!box) return;
      var pics = kb.files.filter(function (f) { return /^(image|video)\//.test(f.mime_type || ''); });
      if (!pics.length) { box.innerHTML = '<div class="hint">Фото пока нет. Загрузите первое.</div>'; return; }
      box.innerHTML = pics.map(function (f) {
        var on = picked.indexOf(f.id) >= 0;
        return '<div class="st-media-item' + (on ? ' on' : '') + '" data-mid="' + esc(f.id) + '">' + (f.preview_url && !/^video\//.test(f.mime_type) ? '<img src="' + esc(f.preview_url) + '" alt="" loading="lazy">' : '<span class="st-ph">' + icon('play') + '</span>') +
          '<span class="chk">' + icon('check') + '</span></div>';
      }).join('');
      S.$$('[data-mid]', box).forEach(function (el) {
        el.addEventListener('click', function () {
          var i = picked.indexOf(el.dataset.mid);
          if (i >= 0) { picked.splice(i, 1); el.classList.remove('on'); } else { picked.push(el.dataset.mid); el.classList.add('on'); }
        });
      });
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
        var thumb = f.preview_url ? '<img src="' + esc(f.preview_url) + '" alt="" loading="lazy">' : icon(fileIc(f));
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
    else html += chRow('#25D366', 'message-circle', 'WhatsApp', 'Бот отвечает клиентам на ваш номер WhatsApp', btn('wa-on', 'Подключить', 'btn-primary'));
    // Telegram личный
    if (!tg || !tg.configured) html += chRow('#229ED9', 'send', 'Telegram', 'Недоступно на сервере, напишите в поддержку', '');
    else if (tg.status === 'connected') html += chRow('#229ED9', 'send', 'Telegram', 'Подключён: ' + esc(tg.tg_username ? '@' + tg.tg_username : (tg.tg_first_name || tg.phone_masked || '')) + scopeText(tg), btn('tg-set', 'Настройки') + btn('tg-off', 'Отключить', 'btn-ghost'));
    else if (tg.status === 'pending_code' || tg.status === 'pending_password') html += chRow('#229ED9', 'send', 'Telegram', 'Подключение не завершено', btn('tg-on', 'Продолжить', 'btn-primary'));
    else html += chRow('#229ED9', 'send', 'Telegram', 'Бот отвечает от имени вашего личного аккаунта Telegram', btn('tg-on', 'Подключить', 'btn-primary'));
    // Instagram
    if (!ig || !ch.igCfg || !ig.available) html += chRow('#E1306C', 'instagram', 'Instagram', 'Недоступно на сервере, напишите в поддержку', '');
    else if (ig.connected) html += chRow('#E1306C', 'instagram', 'Instagram', 'Подключён' + (ig.connected_email ? ': ' + esc(ig.connected_email) : ''), btn('ig-set', 'Настройки') + btn('ig-off', 'Отключить', 'btn-ghost'));
    else html += chRow('#E1306C', 'instagram', 'Instagram', 'Бот отвечает на сообщения в Direct', btn('ig-on', 'Подключить', 'btn-primary'));
    // Gmail и Google Календарь (через Composio, как у агента Profi)
    html += connRow('gmail', 'mail', '#EA4335', 'Gmail', 'Бот читает письма и отвечает клиентам с вашей почты', 'Подключить', ch.gmail);
    html += connRow('gcal', 'calendar', '#4285F4', 'Google Календарь', 'Бот смотрит свободное время и записывает клиентов в ваш календарь', 'Подключить', ch.gcal);
    b.innerHTML = html + '<div class="st-note" style="margin-top:12px">Бот отвечает только на сообщения клиентов. Писать первым и звонить по расписанию он на тарифе Старт не будет.</div>';

    on('wa-on', openWa); on('wa-set', function () { settingsDialog('WhatsApp', '/whatsapp/settings', ch.wa, true); }); on('wa-off', function () { disconnect('WhatsApp', '/whatsapp'); });
    on('tg-on', openTg); on('tg-set', function () { settingsDialog('Telegram', '/telegram-account/settings', ch.tg, false); }); on('tg-off', function () { disconnect('Telegram', '/telegram-account'); });
    on('gmail-on', function () { connectToolkit('gmail'); }); on('gmail-off', function () { disconnect('Gmail', '/connectors/gmail'); });
    on('gcal-on', function () { connectToolkit('google_calendar'); }); on('gcal-off', function () { disconnect('Google Календарь', '/connectors/google_calendar'); });
    on('ig-on', function () { connectToolkit('instagram'); }); on('ig-set', function () { settingsDialog('Instagram', '/connectors/instagram/settings', ch.ig, false, true); }); on('ig-off', function () { disconnect('Instagram', '/connectors/instagram'); });
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

  function settingsDialog(name, path, cur, hasScope, isIg) {
    cur = cur || {};
    var pause = cur.owner_pause_minutes != null ? Number(cur.owner_pause_minutes) : 30;
    var delay = cur.reply_delay_seconds != null ? Number(cur.reply_delay_seconds) : 15;
    var d = S.dialog({
      title: name + ': настройки', size: 'sm',
      body: (isIg ? '' : '<div class="st-switch-row" style="padding-top:0"><div class="t"><b>Автоответ</b><div>Бот отвечает на входящие сообщения.</div></div><label class="switch"><input type="checkbox" id="s-auto"' + (cur.auto_reply_enabled ? ' checked' : '') + '><span class="track"></span></label></div>') +
        (isIg ? '' : '<div class="field"><label class="label">Кому отвечать</label><select class="select-native" id="s-scope"><option value="contacts"' + (cur.reply_scope !== 'all' ? ' selected' : '') + '>Только клиентам из CRM</option><option value="all"' + (cur.reply_scope === 'all' ? ' selected' : '') + '>Всем, кто написал</option></select></div>') +
        '<div class="field"><label class="label">Если вы написали клиенту сами, бот молчит</label><select class="select-native" id="s-pause">' + PAUSES.map(function (p) { return '<option value="' + p[0] + '"' + (p[0] === pause ? ' selected' : '') + '>' + p[1] + '</option>'; }).join('') + '</select>' +
        '<div class="hint">Вы сможете спокойно договориться с клиентом без бота.</div></div>' +
        '<div class="field"><label class="label">Отвечать через</label><select class="select-native" id="s-delay">' + DELAYS.map(function (v) { return '<option value="' + v + '"' + (v === delay ? ' selected' : '') + '>' + v + ' секунд</option>'; }).join('') + '</select>' +
        '<div class="hint">Столько бот ждёт после последнего сообщения, чтобы ответить на всю серию сразу.</div></div>' +
        (cur.voice_reply_mode !== undefined ? '<div class="field"><label class="label">Голосовые ответы</label><select class="select-native" id="s-voice">' +
          [['on_voice', 'В ответ на голосовое клиента'], ['auto', 'Бот решает сам'], ['never', 'Никогда, только текст']].map(function (p) { return '<option value="' + p[0] + '"' + (p[0] === cur.voice_reply_mode ? ' selected' : '') + '>' + p[1] + '</option>'; }).join('') + '</select></div>' : ''),
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="s-save">Сохранить</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    S.$('#s-save', d.el).addEventListener('click', function () {
      var body = { owner_pause_minutes: parseInt(S.$('#s-pause', d.el).value, 10), reply_delay_seconds: parseInt(S.$('#s-delay', d.el).value, 10) };
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

  // Telegram: телефон → код → пароль
  function openTg() {
    var d = S.dialog({ title: 'Подключить Telegram', size: 'sm', body: '<div id="tg-box"></div>', onClose: loadChannels });
    var box = S.$('#tg-box', d.el);
    function step(status, phone) {
      if (status === 'pending_code') {
        box.innerHTML = '<p class="muted" style="margin-top:0">Код отправлен в приложение Telegram на номер ' + esc(phone || '') + '.</p>' +
          '<div class="field"><label class="label">Код из Telegram</label><input class="input" id="tg-code" inputmode="numeric" autocomplete="one-time-code"></div>' +
          '<button class="btn btn-primary" id="tg-go" type="button">Подтвердить</button>';
        S.$('#tg-go', d.el).addEventListener('click', function () {
          var code = S.$('#tg-code', d.el).value.trim(); if (!code) return;
          call('/telegram-account/verify-code', { code: code });
        });
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
      } else {
        box.innerHTML = '<p class="muted" style="margin-top:0">Бот будет отвечать клиентам от имени вашего аккаунта. Введите номер телефона, к которому привязан Telegram.</p>' +
          '<div class="field"><label class="label">Номер телефона</label><input class="input" id="tg-phone" placeholder="+996 700 123 456" inputmode="tel"></div>' +
          '<button class="btn btn-primary" id="tg-go" type="button">Получить код</button>';
        S.$('#tg-go', d.el).addEventListener('click', function () {
          var p = S.$('#tg-phone', d.el).value.trim(); if (!p) return;
          call('/telegram-account/start', { phone: p });
        });
      }
    }
    function call(path, body) {
      var go = S.$('#tg-go', d.el); if (go) go.disabled = true;
      S.api(path, { method: 'POST', body: body }).then(function (r) {
        if (!r.ok) { if (go) go.disabled = false; S.toast(S.apiError(r), 'error'); return; }
        step(r.data.status, r.data.phone_masked);
      });
    }
    step(ch.tg ? ch.tg.status : '', ch.tg ? ch.tg.phone_masked : '');
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
    var chRows = Object.keys(chans).map(function (k) {
      return '<div class="field" style="margin-bottom:10px"><label class="label">' + (k === 'whatsapp' ? 'WhatsApp' : 'Telegram') + ': когда отвечать голосом</label><select class="select-native" data-vch="' + k + '">' +
        MODES.map(function (m) { return '<option value="' + m[0] + '"' + (m[0] === chans[k] ? ' selected' : '') + '>' + m[1] + '</option>'; }).join('') + '</select></div>';
    }).join('');
    b.innerHTML =
      '<div class="st-engine">' + [['eleven', 'ElevenLabs', 'Живые голоса, кыргызский и русский'], ['openai', 'OpenAI GPT-Live', 'Голос ChatGPT, естественная интонация']].map(function (e) {
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
      '<div id="vm-openai" style="margin-top:14px"' + (engine === 'openai' ? '' : ' hidden') + '><div class="field"><label class="label">Голос GPT-Live</label><select class="select-native" id="vm-ovoice">' +
      (vm.openai_voices || []).map(function (v) { return '<option value="' + esc(v) + '"' + (v === vm.openai_voice ? ' selected' : '') + '>' + esc(v) + '</option>'; }).join('') + '</select></div></div>' +
      (chRows ? '<div class="st-sub-h">Когда бот отвечает голосом</div>' + chRows : '<div class="note" style="margin-top:12px">Подключите WhatsApp или Telegram в разделе «Каналы», и здесь появится выбор, когда отвечать голосом. Instagram голосовые не поддерживает.</div>') +
      '<div class="field" id="vm-share-wrap"' + (Object.keys(chans).some(function (k) { return chans[k] === 'auto'; }) ? '' : ' hidden') + '><label class="label">Доля голосовых, когда бот решает сам</label><select class="select-native" id="vm-share">' +
      (vm.auto_share_choices || [30, 40, 50]).map(function (v) { return '<option value="' + v + '"' + (v === vm.auto_share ? ' selected' : '') + '>около ' + v + '% ответов</option>'; }).join('') + '</select></div>' +
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
    S.$$('[data-vch]', b).forEach(function (sel) {
      sel.addEventListener('change', function () {
        var path = sel.dataset.vch === 'whatsapp' ? '/whatsapp/settings' : '/telegram-account/settings';
        S.api(path, { method: 'PATCH', body: { voice_reply_mode: sel.value } }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          vm.channels[sel.dataset.vch] = sel.value;
          S.toast('Сохранено', 'success');
          S.$('#vm-share-wrap').hidden = !Object.keys(vm.channels).some(function (k) { return vm.channels[k] === 'auto'; });
        });
      });
    });
    S.$('#vm-play').addEventListener('click', function () {
      var btn = this; btn.disabled = true;
      var body = { engine: S.$('input[name="vm-engine"]:checked', b).value };
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

      '<div class="st-switch-row" style="margin-top:14px"><div class="t"><b>Оповещения включены</b><div>Общий выключатель. Выключите, если пока не хотите получать сообщения.</div></div>' +
      '<label class="switch"><input type="checkbox" id="n-on"' + (t.enabled ? ' checked' : '') + '><span class="track"></span></label></div>' +
      '<div class="st-sub-h" style="margin-top:14px">Что присылать</div>' +
      notifyRow('n-lead', 'Новый клиент', 'Когда кто-то впервые написал или позвонил: имя, телефон, первое сообщение.', n.new_lead) +
      notifyRow('n-call', 'Итог звонка', 'После разговора: коротко, о чём поговорили и что решил бот.', n.call_summary) +
      notifyRow('n-day', 'Итоги дня', 'Каждый вечер в 20:00 по Бишкеку: сколько обращений, звонков и сделок.', n.daily_digest) +

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
