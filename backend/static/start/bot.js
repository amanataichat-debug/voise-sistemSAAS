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
    ['s-channels', 'messages-square', 'Каналы'], ['s-calls', 'phone', 'Входящие звонки'], ['s-notify', 'bell', 'Оповещения вам'],
    ['s-sheet', 'layers', 'Google Таблица'], ['s-credits', 'coins', 'Кредиты']
  ];

  document.addEventListener('DOMContentLoaded', function () {
    root = document.getElementById('page');
    S.initMobileMenu();
    S.guard().then(function (ok) {
      if (!ok) return;
      return S.loadAgent().then(function (a) {
        if (!a) { createView(); S.ready(); return; }
        return S.api('/').then(function (r) {
          A = r.ok ? r.data : a;
          renderPage();
          S.ready();
        });
      });
    });
  });

  function setupScroll() {
    var links = S.$$('.st-nav a');
    var map = {};
    links.forEach(function (a) { map[a.getAttribute('href').slice(1)] = a; });
    function spy() {
      var best = null;
      SECTIONS.forEach(function (s) {
        var el = document.getElementById(s[0]);
        if (el && el.getBoundingClientRect().top < 140) best = s[0];
      });
      links.forEach(function (a) { a.classList.remove('active'); });
      if (best && map[best]) map[best].classList.add('active');
    }
    window.addEventListener('scroll', S.debounce(spy, 60), { passive: true });
    links.forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        var el = document.getElementById(a.getAttribute('href').slice(1));
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
    spy();
    var h = (location.hash || '').replace('#', '');
    if (h === 'knowledge') h = 's-kb';
    if (h && document.getElementById(h)) setTimeout(function () { document.getElementById(h).scrollIntoView(); }, 80);
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
      sec('s-calls', 'Входящие звонки', 'Номер, на который клиенты звонят боту, и первая фраза.', '', 'b-calls') +
      sec('s-notify', 'Оповещения вам', 'Бот напишет вам в Telegram, когда придёт новый клиент или закончится звонок.', '', 'b-notify') +
      sec('s-sheet', 'Google Таблица', 'Заявки бот записывает на лист «Заявки», а прайс можно загрузить из таблицы в каталог.', '', 'b-sheet') +
      sec('s-credits', 'Кредиты бота', 'Расход на работу бота: ответы, разбор звонков, фото и голосовые клиентов.', '', 'b-credits') +
      '</div></div>';
    setupScroll();
    fillMain(); fillInstr(); loadKb(); loadChannels(); loadCalls(); loadNotify(); loadSheet(); loadCredits();
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
      '<div class="st-note">Голос по телефону: <b>' + esc(voice) + '</b></div>';
    S.$('#m-save').addEventListener('click', function () {
      var name = S.$('#m-name').value.trim();
      if (!name) { S.toast('Укажите имя бота', 'error'); return; }
      put({ name: name }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        A.name = name; S.toast('Сохранено', 'success');
      });
    });
    S.$('#m-active').addEventListener('change', function () {
      var on = this.checked, el = this;
      put({ is_active: on }).then(function (r) {
        if (!r.ok) { el.checked = !on; S.toast(S.apiError(r), 'error'); return; }
        A.is_active = on; S.toast(on ? 'Бот включён' : 'Бот выключен', 'success');
      });
    });
  }

  // ══════════════════════════════ ИНСТРУКЦИИ ═══════════════════════════════
  function fillInstr() {
    var b = S.$('#b-instr');
    b.innerHTML = DOCS.map(function (d) {
      return '<div class="field"><label class="label" for="i-' + d[0] + '">' + esc(d[1]) + '</label><textarea class="textarea" id="i-' + d[0] + '" rows="3" placeholder="' + esc(d[3]) + '">' + esc(A[d[0]] || '') + '</textarea><div class="hint">' + esc(d[2]) + '</div></div>';
    }).join('') +
      '<div class="field"><label class="label" for="i-add">Дополнительные правила для переписки</label><textarea class="textarea" id="i-add" rows="3" placeholder="Например: не называть скидки, сложные вопросы передавать владельцу">' + esc(A.additional_instructions || '') + '</textarea></div>' +
      '<div class="field"><label class="label" for="i-vadd">Дополнительные правила для звонков</label><textarea class="textarea" id="i-vadd" rows="3" placeholder="Например: говорить медленно, всегда уточнять имя">' + esc(A.voice_additional_instructions || '') + '</textarea></div>' +
      '<button class="btn btn-primary" id="i-save" type="button">Сохранить инструкции</button>';
    S.$('#i-save').addEventListener('click', function () {
      var body = { additional_instructions: S.$('#i-add').value, voice_additional_instructions: S.$('#i-vadd').value };
      for (var i = 0; i < DOCS.length; i++) {
        var v = S.$('#i-' + DOCS[i][0]).value.trim();
        if (!v) { S.toast('Поле «' + DOCS[i][1] + '» не должно быть пустым', 'error'); return; }
        body[DOCS[i][0]] = v;
      }
      var btn = this; btn.disabled = true;
      put(body).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        Object.keys(body).forEach(function (k) { A[k] = body[k]; });
        S.toast('Инструкции сохранены', 'success');
      });
    });
  }

  // ══════════════════════════════ БАЗА ЗНАНИЙ ══════════════════════════════
  var kb = { entries: [], counts: {}, voice: null, gaps: [], tab: 'qa' };
  var KB_TABS = [['qa', 'Вопросы и ответы'], ['section', 'О компании'], ['product', 'Каталог'], ['gaps', 'Без ответа']];

  function loadKb() {
    Promise.all([S.api('/knowledge'), S.api('/knowledge/gaps')]).then(function (res) {
      var b = S.$('#b-kb');
      if (!res[0].ok) { b.innerHTML = '<div class="st-empty">' + esc(S.apiError(res[0])) + '</div>'; return; }
      kb.entries = res[0].data.entries || []; kb.counts = res[0].data.counts || {}; kb.voice = res[0].data.voice || null;
      kb.gaps = res[1].ok ? (res[1].data.gaps || []) : [];
      drawKb();
    });
  }

  function drawKb() {
    var b = S.$('#b-kb');
    var counts = { qa: kb.counts.qa || 0, section: kb.counts.section || 0, product: kb.counts.product || 0, gaps: kb.gaps.length };
    b.innerHTML =
      '<div class="tabs" style="margin:-6px -8px 16px">' + KB_TABS.map(function (t) {
        return '<div class="tab' + (kb.tab === t[0] ? ' active' : '') + '" data-kt="' + t[0] + '"><span>' + t[1] + '</span><span class="st-count" style="margin-left:2px">' + counts[t[0]] + '</span></div>';
      }).join('') + '</div><div id="kb-body"></div>' +
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
    var items = kb.entries.filter(function (e) { return e.kind === kb.tab; });
    var addLabel = { qa: 'Добавить вопрос и ответ', section: 'Добавить раздел', product: 'Добавить позицию' }[kb.tab];
    var hint = {
      qa: 'Частые вопросы клиентов и ваши ответы. Бот отвечает на них сразу, без поиска.',
      section: 'Короткие блоки о компании: адрес, режим работы, условия доставки и оплаты.',
      product: 'Услуги и товары с ценой. Бот называет цену и наличие.'
    }[kb.tab];
    box.innerHTML = '<div class="row-between" style="margin-bottom:12px;gap:12px;flex-wrap:wrap"><span class="muted small" style="max-width:520px">' + hint + '</span>' +
      '<button class="btn btn-primary btn-sm" id="kb-add" type="button">' + icon('plus') + '<span>' + addLabel + '</span></button></div>' +
      (items.length ? items.map(function (e) {
        return '<div class="st-qa" data-id="' + esc(e.id) + '"><div class="q">' + esc(e.title || '—') +
          (e.kind === 'product' ? ' <span class="muted" style="font-weight:500">· ' + esc(e.price != null ? S.fmtMoney(e.price, e.currency) : 'цена не указана') + (e.in_stock === false ? ' · нет в наличии' : '') + '</span>' : '') + '</div>' +
          (e.body ? '<div class="a">' + esc(e.body) + '</div>' : '') +
          '<div class="acts"><button class="btn btn-ghost btn-sm" data-edit="' + esc(e.id) + '" type="button">' + icon('pen') + '<span>Изменить</span></button>' +
          '<button class="btn btn-ghost btn-sm" data-del="' + esc(e.id) + '" type="button">' + icon('trash-2') + '<span>Удалить</span></button></div></div>';
      }).join('') : '<div class="st-empty">Пока пусто.</div>');
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

  function entryDialog(kind, e, prefill) {
    var src = e || prefill || null;
    var titles = { qa: ['Вопрос клиента', 'Ответ бота'], section: ['Название раздела', 'Текст'], product: ['Название', 'Описание'] }[kind];
    var d = S.dialog({
      title: (e ? 'Изменить' : 'Добавить') + ': ' + { qa: 'вопрос и ответ', section: 'раздел', product: 'позицию' }[kind], size: 'lg',
      body: '<div class="field"><label class="label">' + titles[0] + ' *</label><input class="input" id="e-title" value="' + esc(src ? src.title : '') + '"></div>' +
        (kind === 'product'
          ? '<div class="st-grid2"><div class="field"><label class="label">Цена, сом</label><input class="input" id="e-price" type="number" min="0" value="' + esc(e && e.price != null ? e.price : '') + '"></div>' +
            '<div class="field"><label class="label">Наличие</label><select class="select-native" id="e-stock"><option value="1">Есть в наличии</option><option value="0"' + (e && e.in_stock === false ? ' selected' : '') + '>Нет в наличии</option></select></div></div>'
          : '') +
        '<div class="field"><label class="label">' + titles[1] + '</label><textarea class="textarea" id="e-body" rows="5">' + esc(src ? src.body : '') + '</textarea></div>' +
        '<div class="field"><label class="label">Ключевые слова</label><input class="input" id="e-kw" value="' + esc(src ? src.keywords || '' : '') + '" placeholder="через запятую: доставка, курьер">' +
        '<div class="hint">По ним бот находит запись, если клиент спросил другими словами.</div></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="e-save">Сохранить</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    S.$('#e-save', d.el).addEventListener('click', function () {
      var body = { kind: kind, title: S.$('#e-title', d.el).value.trim(), body: S.$('#e-body', d.el).value.trim(), keywords: S.$('#e-kw', d.el).value.trim() };
      if (!body.title && !body.body) { S.toast('Заполните запись', 'error'); return; }
      if (kind === 'product') {
        var p = S.$('#e-price', d.el).value;
        body.price = p === '' ? null : Number(p);
        body.in_stock = S.$('#e-stock', d.el).value === '1';
      }
      var btn = this; btn.disabled = true;
      S.api(e ? '/knowledge/' + e.id : '/knowledge', { method: e ? 'PUT' : 'POST', body: body }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        d.close(); S.toast('Сохранено', 'success'); loadKb();
      });
    });
    setTimeout(function () { var i = S.$('#e-title', d.el); if (i) i.focus(); }, 60);
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
        entryDialog('qa', null, { title: g.query, body: '', keywords: '' });
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
      ch.ig = cons.filter(function (c) { return c.toolkit === 'instagram'; })[0] || null;
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
    else if (wa.status === 'connected') html += chRow('#25D366', 'message-circle', 'WhatsApp', 'Подключён: ' + esc(wa.phone_masked || '') + (wa.wa_name ? ' · ' + esc(wa.wa_name) : '') + (wa.auto_reply_enabled ? '' : ' · автоответ выключен'), btn('wa-set', 'Настройки') + btn('wa-off', 'Отключить', 'btn-ghost'));
    else if (wa.status === 'pending_qr') html += chRow('#25D366', 'message-circle', 'WhatsApp', 'Ждём сканирования QR-кода', btn('wa-on', 'Показать QR', 'btn-primary'));
    else html += chRow('#25D366', 'message-circle', 'WhatsApp', 'Бот отвечает клиентам на ваш номер WhatsApp', btn('wa-on', 'Подключить', 'btn-primary'));
    // Telegram личный
    if (!tg || !tg.configured) html += chRow('#229ED9', 'send', 'Telegram', 'Недоступно на сервере, напишите в поддержку', '');
    else if (tg.status === 'connected') html += chRow('#229ED9', 'send', 'Telegram', 'Подключён: ' + esc(tg.tg_username ? '@' + tg.tg_username : (tg.tg_first_name || tg.phone_masked || '')) + (tg.auto_reply_enabled ? '' : ' · автоответ выключен'), btn('tg-set', 'Настройки') + btn('tg-off', 'Отключить', 'btn-ghost'));
    else if (tg.status === 'pending_code' || tg.status === 'pending_password') html += chRow('#229ED9', 'send', 'Telegram', 'Подключение не завершено', btn('tg-on', 'Продолжить', 'btn-primary'));
    else html += chRow('#229ED9', 'send', 'Telegram', 'Бот отвечает от имени вашего личного аккаунта Telegram', btn('tg-on', 'Подключить', 'btn-primary'));
    // Instagram
    if (!ig || !ch.igCfg || !ig.available) html += chRow('#E1306C', 'instagram', 'Instagram', 'Недоступно на сервере, напишите в поддержку', '');
    else if (ig.connected) html += chRow('#E1306C', 'instagram', 'Instagram', 'Подключён' + (ig.connected_email ? ': ' + esc(ig.connected_email) : ''), btn('ig-set', 'Настройки') + btn('ig-off', 'Отключить', 'btn-ghost'));
    else html += chRow('#E1306C', 'instagram', 'Instagram', 'Бот отвечает на сообщения в Direct', btn('ig-on', 'Подключить', 'btn-primary'));
    b.innerHTML = html + '<div class="st-note" style="margin-top:12px">Бот отвечает только на сообщения клиентов. Писать первым и звонить по расписанию он на тарифе Старт не будет.</div>';

    on('wa-on', openWa); on('wa-set', function () { settingsDialog('WhatsApp', '/whatsapp/settings', ch.wa, true); }); on('wa-off', function () { disconnect('WhatsApp', '/whatsapp'); });
    on('tg-on', openTg); on('tg-set', function () { settingsDialog('Telegram', '/telegram-account/settings', ch.tg, false); }); on('tg-off', function () { disconnect('Telegram', '/telegram-account'); });
    on('ig-on', connectIg); on('ig-set', function () { settingsDialog('Instagram', '/connectors/instagram/settings', ch.ig, false, true); }); on('ig-off', function () { disconnect('Instagram', '/connectors/instagram'); });
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
        box.innerHTML = '<div class="st-empty-ic">' + icon('circle-check') + '</div><b>WhatsApp подключён</b><div class="muted small">' + esc(s.phone_masked || '') + '</div>' +
          '<button class="btn btn-primary" type="button" id="wa-done">Готово</button>';
        S.$('#wa-done', d.el).addEventListener('click', d.close);
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
        box.innerHTML = '<div class="st-qr"><div class="st-empty-ic">' + icon('circle-check') + '</div><b>Telegram подключён</b><button class="btn btn-primary" type="button" id="tg-done">Готово</button></div>';
        S.$('#tg-done', d.el).addEventListener('click', d.close);
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

  // Instagram: OAuth через Composio
  function connectIg() {
    S.api('/connectors/instagram/connect', { method: 'POST' }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      if (r.data.connected) { S.toast('Instagram подключён', 'success'); loadChannels(); return; }
      if (r.data.redirect_url) {
        var w = window.open(r.data.redirect_url, 'ig_connect', 'width=520,height=720');
        var onMsg = function (e) {
          if (e.data && e.data.type === 'connector_result') { window.removeEventListener('message', onMsg); loadChannels(); }
        };
        window.addEventListener('message', onMsg);
        if (!w) location.href = r.data.redirect_url;
      }
    });
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
  var tgBot = null;
  function loadNotify() {
    S.api('/telegram').then(function (r) { tgBot = r.ok ? r.data : null; drawNotify(); });
  }

  function drawNotify() {
    var b = S.$('#b-notify'), t = tgBot;
    if (!t || !t.is_configured) {
      b.innerHTML = '<p class="muted" style="margin-top:0">Создайте бота-уведомлялку в Telegram: напишите <b>@BotFather</b>, команда <code>/newbot</code>, и вставьте токен сюда.</p>' +
        '<div class="field"><label class="label">Токен бота от @BotFather</label><input class="input" id="n-token" placeholder="123456:ABC…" autocomplete="off"></div>' +
        '<button class="btn btn-primary" id="n-connect" type="button">Подключить</button>';
      on('n-connect', function () {
        var tok = S.$('#n-token').value.trim(); if (!tok) return;
        this.disabled = true;
        S.api('/telegram', { method: 'PUT', body: { bot_token: tok } }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); loadNotify(); return; }
          tgBot = r.data; drawNotify(); S.toast('Бот подключён', 'success');
        });
      });
      return;
    }
    var n = t.notify || {};
    var chats = (t.chat_ids || []);
    b.innerHTML =
      '<div class="st-switch-row" style="padding-top:0"><div class="t"><b>Оповещения включены</b><div>Бот: @' + esc(t.bot_username || '') + '. Откройте его в Telegram и нажмите Start, чтобы получать сообщения.</div></div>' +
      '<label class="switch"><input type="checkbox" id="n-on"' + (t.enabled ? ' checked' : '') + '><span class="track"></span></label></div>' +
      notifyRow('n-lead', 'Новый клиент', 'Когда кто-то впервые написал или позвонил.', n.new_lead) +
      notifyRow('n-call', 'Итог звонка', 'Коротко, о чём поговорили и что решил бот.', n.call_summary) +
      notifyRow('n-day', 'Итоги дня', 'Каждый вечер в 20:00: сколько обращений и звонков.', n.daily_digest) +
      '<div class="st-sub-h" style="margin-top:18px">Чаты для оповещений</div>' +
      (chats.length ? chats.map(function (c) {
        return '<div class="row-between" style="padding:6px 0"><span>' + esc(c.title || c.chat_id) + ' <span class="muted small">· ' + esc(c.chat_id) + '</span></span><button class="btn btn-ghost btn-sm" data-rm="' + esc(c.chat_id) + '" type="button">Убрать</button></div>';
      }).join('') : '<div class="muted small">Пока ни одного. Нажмите Start у бота, и чат добавится сам.</div>') +
      '<div class="row" style="margin-top:14px;gap:8px"><button class="btn btn-sm" id="n-test" type="button">Отправить пробное сообщение</button>' +
      '<button class="btn btn-ghost btn-sm" id="n-off" type="button">Отключить бота</button></div>';
    on('n-on', function () { patchTg('/telegram/enabled', { enabled: this.checked }); });
    on('n-lead', function () { patchTg('/telegram/notify', { new_lead: this.checked }); });
    on('n-call', function () { patchTg('/telegram/notify', { call_summary: this.checked }); });
    on('n-day', function () { patchTg('/telegram/notify', { daily_digest: this.checked }); });
    on('n-test', function () {
      S.api('/telegram/test', { method: 'POST' }).then(function (r) { S.toast(r.ok ? 'Сообщение отправлено' : S.apiError(r), r.ok ? 'success' : 'error'); });
    });
    on('n-off', function () {
      S.confirm({ title: 'Отключить Telegram-бота?', message: 'Оповещения перестанут приходить.', confirmText: 'Отключить', danger: true }).then(function (ok) {
        if (!ok) return;
        S.api('/telegram', { method: 'DELETE' }).then(function (r) { if (r.ok) { tgBot = r.data; drawNotify(); } });
      });
    });
    S.$$('[data-rm]', b).forEach(function (x) {
      x.addEventListener('click', function () {
        S.api('/telegram/chats/' + encodeURIComponent(x.dataset.rm), { method: 'DELETE' }).then(function (r) { if (r.ok) { tgBot = r.data; drawNotify(); } });
      });
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
