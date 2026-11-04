/* ============================================================================
 * inbox/inbox.js — Инбокс: общий чат с клиентами (WhatsApp · Instagram · Telegram)
 *
 * Тарифы Старт и Profi. Старт открывает страницу из меню кабинета, Profi — кнопкой
 * «Инбокс» в шапке agent.html: там она живёт в панели поверх страницы
 * (?embed=1), закрывается Esc / ✕ мгновенно и при повторном открытии не грузится заново.
 *
 * Данные — /api/inbox/* (backend/api/inbox.py). Один диалог = один клиент:
 * все каналы клиента в одной ленте, плюс звонки с записью.
 * Общие утилиты — window.StartApp (start/common.js): esc, форматирование, диалоги.
 * ========================================================================== */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var API = '/api/inbox';
  var EMBED = document.documentElement.classList.contains('ib-embed');
  var TZ = 'Asia/Bishkek';
  var CH = {
    whatsapp: { label: 'WhatsApp', icon: 'message-circle', color: '#25D366' },
    instagram: { label: 'Instagram', icon: 'instagram', color: '#E1306C' },
    telegram: { label: 'Telegram', icon: 'send', color: '#229ED9' }
  };
  var CH_ORDER = ['whatsapp', 'instagram', 'telegram'];
  var FOLDERS = [
    { id: 'all', label: 'Все диалоги', icon: 'inbox' },
    { id: 'waiting', label: 'Ждут ответа', icon: 'hourglass', hot: true },
    { id: 'unread', label: 'Непрочитанные', icon: 'message-square' },
    { id: 'paused', label: 'Бот на паузе', icon: 'pause' },
    { id: 'no_card', label: 'Без карточки', icon: 'user-round' }
  ];
  var PAUSE_LABELS = { channel: 'как в настройках канала', 0: 'не останавливать', 30: '30 минут', 60: '1 час', 120: '2 часа', 240: '4 часа', 480: '8 часов', 1440: 'сутки', '-1': 'до включения вручную' };

  var st = {
    ov: null, mode: null, agents: [], settings: null, languages: [],
    folder: 'all', channel: null, stage: null, q: '',
    items: [], counts: {}, hasMore: false, loadingList: false, listSeq: 0,
    key: null, d: null,                 // открытый диалог: { contact, timeline, default_channel, stages }
    unlinked: null,                     // открытый чат без карточки
    replyChannel: null, pause: null,    // канал ответа, пауза после ответа (null = из настроек)
    attach: null,                       // { kind: 'file'|'library'|'voice', file, blob, url, name, size, mime, id }
    tr: null,                           // { original, language } — текст переведён помощником
    drafts: {},
    latestAt: null, unreadMsgs: null, pollT: null,
    rec: null,
    cardOpen: true,
    hidden: EMBED                       // встроенная панель скрыта, пока её не открыли
  };
  var root;

  // ── Запуск ──────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    root = document.getElementById('ib');
    if (!EMBED) S.initMobileMenu();
    try { st.cardOpen = localStorage.getItem('ib_card') !== '0'; } catch (e) { /* ignore */ }
    if (window.innerWidth <= 1180) st.cardOpen = false;
    if (EMBED) addEmbedHead();
    api('/overview').then(function (r) {
      if (r.status === 402) { root.innerHTML = noAccess(); S.ready(); return; }
      if (!r.ok) { root.innerHTML = '<div class="ib-loading"><div class="ib-empty">' + esc(S.apiError(r)) + '</div></div>'; S.ready(); return; }
      st.ov = r.data;
      st.mode = r.data.agent_mode;
      try { localStorage.setItem('vf_agent_mode', st.mode || ''); } catch (e) { /* ignore */ }
      st.agents = r.data.agents || [];
      st.settings = r.data.settings;
      st.languages = r.data.languages || [];
      if (!st.agents.length) { root.innerHTML = noBot(); S.ready(); return; }
      renderShell();
      S.ready();
      var p = new URLSearchParams(location.search);
      if (p.get('contact')) openDialog('contact:' + p.get('contact'));
      loadList(true);
      startPolling();
    });
    document.addEventListener('keydown', onKey);
    window.addEventListener('message', function (e) {
      if (e.origin !== location.origin || !e.data) return;
      if (e.data.type === 'inbox:hidden') st.hidden = true;
      if (e.data.type === 'inbox:shown') { st.hidden = false; poll(true); var ta = S.$('#ib-text'); if (ta && window.innerWidth > 860) ta.focus(); }
      if (e.data.type === 'inbox:open' && e.data.contact) openDialog('contact:' + e.data.contact);
    });
  });

  function onKey(e) {
    if (e.key !== 'Escape') return;
    if (S.$('.vf-backdrop') || S.$('.ib-lightbox')) return;
    if (closePop()) return;
    if (EMBED) closeEmbed();
  }
  function closeEmbed() { try { parent.postMessage({ type: 'inbox:close' }, location.origin); } catch (e) { /* ignore */ } }

  function addEmbedHead() {
    var h = document.createElement('div');
    h.className = 'ib-embed-head';
    h.innerHTML = icon('inbox') + '<b>Инбокс</b><span class="muted small">все чаты с клиентами</span><span class="sp"></span>' +
      '<kbd>Esc</kbd><button class="btn btn-ghost btn-icon btn-sm" type="button" id="ib-embed-close" aria-label="Закрыть">' + icon('x') + '</button>';
    root.parentNode.insertBefore(h, root);
    S.$('#ib-embed-close').addEventListener('click', closeEmbed);
  }

  // ── API ─────────────────────────────────────────────────────────────────
  function api(path, opts) { return S.api(path.indexOf('/api/') === 0 ? path : API + path, opts); }
  function postForm(path, fd) {
    var headers = {}, t = S.token();
    if (t) headers.Authorization = 'Bearer ' + t;
    return fetch(API + path, { method: 'POST', headers: headers, body: fd })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, status: r.status, data: data }; }); })
      .catch(function () { return { ok: false, status: 0, data: { detail: 'Нет связи с сервером' } }; });
  }
  function errText(r) {
    var d = r && r.data && r.data.detail;
    if (d && typeof d === 'object' && d.message) return d.message;
    return S.apiError(r);
  }

  // ── Пустые состояния ────────────────────────────────────────────────────
  function botHref() { return st.mode === 'reactive' ? '/static/start-bot.html' : '/static/agent.html'; }
  function channelsHref() { return st.mode === 'reactive' ? '/static/start-bot.html#channels' : '/static/agent.html'; }
  function noBot() {
    return '<div class="ib-loading"><div class="st-empty-big"><div class="st-empty-ic">' + icon('bot') + '</div>' +
      '<h2>Сначала создайте чат-бота</h2><p>Инбокс собирает переписку бота с клиентами из WhatsApp, Instagram и Telegram. ' +
      'Создайте бота и подключите каналы — диалоги появятся здесь.</p>' +
      '<a class="btn btn-primary" href="' + botHref() + '"' + (EMBED ? ' target="_top"' : '') + '>' + icon('plus') + '<span>Создать бота</span></a></div></div>';
  }
  function noAccess() {
    return '<div class="ib-loading"><div class="st-empty-big"><div class="st-empty-ic">' + icon('lock') + '</div>' +
      '<h2>Инбокс входит в тарифы Старт и Profi</h2><p>Все чаты с клиентами в одном окне: отвечайте сами, останавливайте бота, переводите сообщения.</p>' +
      '<a class="btn btn-primary" href="/static/dashboard.html#plans" target="_top">Выбрать тариф</a></div></div>';
  }

  // ── Каркас ──────────────────────────────────────────────────────────────
  function renderShell() {
    root.className = 'ib' + (st.cardOpen ? '' : ' no-card');
    root.innerHTML =
      '<aside class="ib-side" id="ib-side"></aside>' +
      '<section class="ib-list"><div class="ib-list-head">' +
      '<div class="ib-search">' + icon('search') + '<input class="input" id="ib-q" type="search" placeholder="Имя, телефон или текст сообщения"></div>' +
      '<div class="ib-chips" id="ib-chips"></div></div>' +
      '<div id="ib-banner"></div>' +
      '<div class="ib-items" id="ib-items"></div></section>' +
      '<section class="ib-chat" id="ib-chat"></section>' +
      '<aside class="ib-card" id="ib-card"></aside>';
    S.$('#ib-q').addEventListener('input', S.debounce(function (e) { st.q = e.target.value.trim(); loadList(true); }, 280));
    S.$('#ib-items').addEventListener('scroll', function (e) {
      var el = e.target;
      if (st.hasMore && !st.loadingList && el.scrollTop + el.clientHeight > el.scrollHeight - 200) loadList(false);
    });
    renderSide();
    renderBanner();
    renderChatEmpty();
  }

  function allStages() {
    var seen = {}, out = [];
    st.agents.forEach(function (a) { (a.stages || []).forEach(function (s) { if (!seen[s.key]) { seen[s.key] = 1; out.push(s); } }); });
    return out;
  }
  function stageOf(key, stages) {
    var list = stages || allStages();
    for (var i = 0; i < list.length; i++) if (list[i].key === key) return list[i];
    return { key: key, label: key || '—', color: '#6B7280' };
  }
  function channelState(ch) {
    var on = false, exists = false;
    st.agents.forEach(function (a) { var c = (a.channels || {})[ch] || {}; if (c.connected) on = true; if (c.exists) exists = true; });
    return { connected: on, exists: exists };
  }
  function visibleChannel(ch) { return (st.settings.channels || {})[ch] !== false; }

  function renderSide() {
    var c = st.counts || {};
    var folders = FOLDERS.map(function (f) {
      var n = c[f.id] || 0;
      return '<button class="ib-nav-item' + (st.folder === f.id ? ' active' : '') + '" data-folder="' + f.id + '">' + icon(f.icon) +
        '<span>' + f.label + '</span><span class="n' + (f.hot && n ? ' hot' : '') + '">' + (n || '') + '</span></button>';
    }).join('');
    var chans = CH_ORDER.map(function (k) {
      var s = channelState(k), vis = visibleChannel(k);
      return '<button class="ib-nav-item' + (st.channel === k ? ' active' : '') + (vis ? '' : ' off') + '" data-channel="' + k + '" style="--c:' + CH[k].color + '">' +
        '<span class="dot"></span><span>' + CH[k].label + '</span>' +
        '<span class="st' + (s.connected ? ' ok' : '') + '">' + (!vis ? 'скрыт' : s.connected ? 'подключён' : 'не подключён') + '</span></button>';
    }).join('');
    var stages = allStages().map(function (s) {
      return '<button class="ib-nav-item' + (st.stage === s.key ? ' active' : '') + '" data-stage="' + esc(s.key) + '" style="--c:' + esc(s.color) + '">' +
        '<span class="dot"></span><span>' + esc(s.label) + '</span></button>';
    }).join('');
    S.$('#ib-side').innerHTML =
      '<div><div class="ib-group-h">Папки</div><div class="ib-nav">' + folders + '</div></div>' +
      '<div><div class="ib-group-h">Каналы <button type="button" id="ib-ch-set" title="Настроить каналы">' + icon('settings') + '</button></div><div class="ib-nav">' + chans + '</div></div>' +
      '<div><div class="ib-group-h">Стадии CRM</div><div class="ib-nav">' + stages + '</div></div>' +
      '<div class="ib-side-foot"><button class="btn btn-sm" type="button" id="ib-settings">' + icon('sliders-horizontal') + '<span>Настройки инбокса</span></button>' +
      '<a class="btn btn-ghost btn-sm" href="' + crmHref() + '"' + (EMBED ? ' data-crm' : '') + '>' + icon('contact-round') + '<span>Открыть CRM</span></a></div>';
    S.$$('[data-folder]', S.$('#ib-side')).forEach(function (b) { b.addEventListener('click', function () { setFolder(b.dataset.folder); }); });
    S.$$('[data-channel]', S.$('#ib-side')).forEach(function (b) { b.addEventListener('click', function () { st.channel = st.channel === b.dataset.channel ? null : b.dataset.channel; renderSide(); loadList(true); }); });
    S.$$('[data-stage]', S.$('#ib-side')).forEach(function (b) { b.addEventListener('click', function () { st.stage = st.stage === b.dataset.stage ? null : b.dataset.stage; renderSide(); renderChips(); loadList(true); }); });
    S.$('#ib-settings').addEventListener('click', settingsDialog);
    S.$('#ib-ch-set').addEventListener('click', channelsDialog);
    var crm = S.$('[data-crm]', S.$('#ib-side'));
    if (crm) crm.addEventListener('click', function (e) { e.preventDefault(); openCrm(null); });
    renderChips();
  }

  function renderChips() {
    var c = st.counts || {};
    var el = S.$('#ib-chips');
    if (!el) return;
    el.innerHTML = FOLDERS.map(function (f) {
      return '<button class="ib-chip' + (st.folder === f.id ? ' active' : '') + '" data-f="' + f.id + '">' + f.label + (c[f.id] ? ' <span class="n">' + c[f.id] + '</span>' : '') + '</button>';
    }).join('') +
      '<button class="ib-chip' + (st.channel ? ' active' : '') + '" data-ch>' + icon('filter') + (st.channel ? CH[st.channel].label : 'Канал') + '</button>' +
      '<button class="ib-chip' + (st.stage ? ' active' : '') + '" data-st>' + (st.stage ? esc(stageOf(st.stage).label) : 'Стадия') + '</button>' +
      '<button class="ib-chip" data-set>' + icon('sliders-horizontal') + '</button>';
    S.$$('[data-f]', el).forEach(function (b) { b.addEventListener('click', function () { setFolder(b.dataset.f); }); });
    S.$('[data-ch]', el).addEventListener('click', function (e) {
      pop(e.currentTarget, [{ label: 'Все каналы', sel: !st.channel, fn: function () { st.channel = null; renderSide(); loadList(true); } }].concat(CH_ORDER.map(function (k) {
        return { label: CH[k].label, dot: CH[k].color, sel: st.channel === k, fn: function () { st.channel = k; renderSide(); loadList(true); } };
      })));
    });
    S.$('[data-st]', el).addEventListener('click', function (e) {
      pop(e.currentTarget, [{ label: 'Все стадии', sel: !st.stage, fn: function () { st.stage = null; renderSide(); loadList(true); } }].concat(allStages().map(function (s) {
        return { label: s.label, dot: s.color, sel: st.stage === s.key, fn: function () { st.stage = s.key; renderSide(); loadList(true); } };
      })));
    });
    S.$('[data-set]', el).addEventListener('click', settingsDialog);
  }

  function setFolder(f) { st.folder = f; renderSide(); loadList(true); }

  function renderBanner() {
    var el = S.$('#ib-banner');
    if (!el) return;
    var off = CH_ORDER.filter(function (k) { return visibleChannel(k) && !channelState(k).connected; });
    if (off.length === CH_ORDER.length) {
      el.innerHTML = '<div class="ib-banner">' + icon('plug') + '<div>Каналы не подключены. <a href="' + channelsHref() + '" target="_top">Подключите WhatsApp, Instagram или Telegram</a> — переписка появится здесь.</div></div>';
    } else el.innerHTML = '';
  }

  // ── Список диалогов ─────────────────────────────────────────────────────
  function listQuery(before) {
    var p = ['folder=' + st.folder, 'limit=40'];
    if (st.channel) p.push('channel=' + st.channel);
    if (st.stage) p.push('stage=' + encodeURIComponent(st.stage));
    if (st.q) p.push('q=' + encodeURIComponent(st.q));
    if (before) p.push('before=' + encodeURIComponent(before));
    return '/dialogs?' + p.join('&');
  }

  function loadList(reset, silent) {
    var seq = ++st.listSeq;
    var before = reset ? null : (st.items.length ? st.items[st.items.length - 1].last_at : null);
    if (reset && !silent) S.$('#ib-items').innerHTML = skeletonList();
    st.loadingList = true;
    return api(listQuery(before)).then(function (r) {
      if (seq !== st.listSeq) return;
      st.loadingList = false;
      if (!r.ok) { S.$('#ib-items').innerHTML = '<div class="ib-empty">' + esc(errText(r)) + '</div>'; return; }
      st.items = reset ? r.data.items : st.items.concat(r.data.items);
      st.hasMore = !!r.data.has_more;
      st.counts = r.data.counts || {};
      if (reset) st.latestAt = r.data.latest_at;
      noteUnread(st.counts.unread_messages);
      renderSide();
      renderItems();
    });
  }
  function skeletonList() {
    var h = '';
    for (var i = 0; i < 7; i++) h += '<div class="ib-item"><div class="skeleton" style="width:42px;height:42px;border-radius:50%"></div><div><div class="skeleton" style="height:14px;width:60%"></div><div class="skeleton" style="height:12px;width:85%;margin-top:8px"></div></div><div></div></div>';
    return h;
  }

  function renderItems() {
    var el = S.$('#ib-items');
    if (!st.items.length) {
      var txt = st.q ? 'Ничего не найдено.' : {
        waiting: 'Все клиенты получили ответ.', unread: 'Новых сообщений нет.', paused: 'Бот отвечает всем клиентам.',
        no_card: 'Все, кто пишет в WhatsApp, уже есть в CRM.'
      }[st.folder] || 'Диалогов пока нет. Они появятся, когда клиенты напишут в подключённые каналы.';
      el.innerHTML = '<div class="ib-empty">' + icon('inbox') + esc(txt) + '</div>';
      return;
    }
    el.innerHTML = st.items.map(itemHtml).join('') + (st.hasMore ? '<div class="ib-more"><span class="muted small">Загружаем ещё…</span></div>' : '');
    S.$$('.ib-item', el).forEach(function (row) { row.addEventListener('click', function () { openDialog(row.dataset.key); }); });
  }

  function itemHtml(x) {
    var ch = CH[x.last_channel] || CH.whatsapp;
    var who = x.last_by === 'owner' ? '<b>Вы: </b>' : x.last_by === 'bot' ? '<b>Бот: </b>' : '';
    var late = x.waiting && x.last_in_at && (Date.now() - new Date(x.last_in_at).getTime() > 15 * 60000);
    var meta = [];
    if (x.no_card) meta.push('<span class="ib-mini new">без карточки</span>');
    if (x.stage) { var s = stageOf(x.stage); meta.push('<span class="st-stage" style="--c:' + esc(s.color) + '"><i></i>' + esc(s.label) + '</span>'); }
    if (x.paused) meta.push('<span class="ib-mini warn" title="Бот молчит с этим клиентом">' + icon('pause') + (x.paused_forever ? 'бот выкл.' : 'до ' + esc(fmtTime(x.paused_until))) + '</span>');
    if (x.tasks_count) {
      var t = x.next_task || {};
      meta.push('<span class="ib-mini task" title="' + esc((t.created_by === 'owner' ? 'Вы поставили: ' : 'Бот поставил: ') + (t.title || '') + ' — ' + fmtWhen(t.scheduled_time)) + '">' +
        icon(t.created_by === 'owner' ? 'user' : 'bot') + icon('calendar') + (x.tasks_count > 1 ? x.tasks_count : fmtWhen(t.scheduled_time, true)) + '</span>');
    }
    return '<div class="ib-item' + (x.key === st.key ? ' active' : '') + (x.unread ? ' unread' : '') + '" data-key="' + esc(x.key) + '">' +
      '<div class="ib-ava">' + esc(S.initial(x.name)) + '<span class="chb" style="--c:' + ch.color + '">' + icon(ch.icon) + '</span></div>' +
      '<div style="min-width:0"><div class="top"><span class="nm">' + esc(x.name) + '</span></div>' +
      '<div class="pv">' + who + esc(x.preview || '…') + '</div>' +
      (meta.length ? '<div class="meta">' + meta.join('') + '</div>' : '') + '</div>' +
      '<div class="right"><span class="tm' + (late ? ' late' : '') + '" title="' + (late ? 'Клиент ждёт ответа' : '') + '">' + esc(fmtListTime(x.last_at)) + '</span>' +
      (x.unread ? '<span class="ib-badge">' + x.unread + '</span>' : '') + '</div></div>';
  }

  // ── Время ───────────────────────────────────────────────────────────────
  function bk(d) { return new Date(new Date(d).getTime() + 6 * 3600000); } // Бишкек = UTC+6 без летнего времени
  function fmtTime(iso) { if (!iso) return ''; return new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }).format(new Date(iso)); }
  function sameDay(a, b) { return S.dayKey(a) === S.dayKey(b); }
  function fmtListTime(iso) {
    if (!iso) return '';
    var d = new Date(iso), now = new Date();
    if (sameDay(iso, now.toISOString())) return fmtTime(iso);
    if (now - d < 6 * 86400000) return new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, weekday: 'short' }).format(d);
    return new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: '2-digit', month: '2-digit' }).format(d);
  }
  function fmtWhen(iso, short) {
    if (!iso) return '';
    var d = new Date(iso), now = new Date();
    var tm = fmtTime(iso);
    if (sameDay(iso, now.toISOString())) return (short ? '' : 'сегодня ') + tm;
    var tmr = new Date(now.getTime() + 86400000);
    if (sameDay(iso, tmr.toISOString())) return 'завтра ' + tm;
    return new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: 'numeric', month: 'short' }).format(d) + (short ? '' : ' ' + tm);
  }
  function minutesToMorning() {
    var b = bk(Date.now());
    var target = new Date(Date.UTC(b.getUTCFullYear(), b.getUTCMonth(), b.getUTCDate(), 9, 0, 0));
    if (b >= target) target = new Date(target.getTime() + 86400000);
    return Math.max(30, Math.round((target - b) / 60000));
  }

  // ── Открыть диалог ──────────────────────────────────────────────────────
  function renderChatEmpty() {
    S.$('#ib-chat').innerHTML = '<div class="ib-chat-empty">' + icon('messages-square') + '<h3>Выберите диалог</h3>' +
      '<p>Здесь вся переписка клиента из WhatsApp, Instagram и Telegram и его звонки. Отвечайте сами — бот на это время замолчит.</p></div>';
    S.$('#ib-card').innerHTML = '';
  }

  function openDialog(key) {
    if (!key) return;
    saveDraft();
    stopRecording(true);
    st.key = key; st.attach = null; st.tr = null; st.pause = null; st.unlinked = null; st.d = null;
    root.classList.add('show-chat');
    S.$$('.ib-item').forEach(function (r) { r.classList.toggle('active', r.dataset.key === key); });
    var it = st.items.filter(function (x) { return x.key === key; })[0];
    if (it && it.unread) { it.unread = 0; renderItems(); }
    S.$('#ib-chat').innerHTML = '<div class="ib-chat-head"><div class="skeleton" style="height:38px;width:240px"></div></div><div class="ib-feed"><div class="skeleton" style="height:60%;min-height:200px"></div></div>';
    if (key.indexOf('wa_chat:') === 0) return openUnlinked(key.slice(8));
    var id = key.slice(8);
    try { history.replaceState(null, '', location.pathname + (EMBED ? '?embed=1&' : '?') + 'contact=' + id); } catch (e) { /* ignore */ }
    api('/dialogs/' + id).then(function (r) {
      if (st.key !== key) return;
      if (!r.ok) { S.$('#ib-chat').innerHTML = '<div class="ib-chat-empty">' + icon('circle-alert') + '<h3>Не удалось открыть диалог</h3><p>' + esc(errText(r)) + '</p></div>'; return; }
      st.d = r.data;
      var sc = st.d.contact.send_channels || {};
      st.replyChannel = st.d.default_channel || CH_ORDER.filter(function (k) { return (sc[k] || {}).can_send; })[0] || null;
      renderChat();
      renderCard();
      autoTranslate();
    });
  }

  function reloadDialog(keepScroll) {
    if (!st.d || !st.key) return Promise.resolve();
    var key = st.key, feed = S.$('#ib-feed');
    var atBottom = !feed || feed.scrollTop + feed.clientHeight > feed.scrollHeight - 80;
    return api('/dialogs/' + st.d.contact.id).then(function (r) {
      if (st.key !== key || !r.ok) return;
      var prev = JSON.stringify(st.d.timeline.items);
      var prevCard = JSON.stringify(st.d.contact);
      st.d.contact = r.data.contact; st.d.stages = r.data.stages;
      if (JSON.stringify(r.data.timeline.items) !== prev) {
        st.d.timeline = r.data.timeline;
        renderFeed(keepScroll && !atBottom);
        autoTranslate();
      }
      renderHead();
      if (JSON.stringify(st.d.contact) !== prevCard) renderCard();
    });
  }

  function renderChat() {
    var chat = S.$('#ib-chat');
    chat.innerHTML = '<div class="ib-chat-head" id="ib-head"></div><div class="ib-feed" id="ib-feed"></div><div id="ib-compose"></div>';
    renderHead();
    renderFeed(false);
    renderCompose();
  }

  function renderHead() {
    var c = st.d.contact, head = S.$('#ib-head');
    if (!head) return;
    var s = stageOf(c.status, st.d.stages);
    var paused = !!c.ai_paused_until;
    var handles = [];
    if (c.real_phone) handles.push(S.fmtPhone(c.real_phone));
    if (c.handles && c.handles.telegram && c.handles.telegram.username) handles.push('@' + c.handles.telegram.username);
    if (c.handles && c.handles.instagram && c.handles.instagram.username) handles.push('ig: @' + c.handles.instagram.username);
    head.innerHTML =
      '<button class="btn btn-ghost btn-icon btn-sm ib-back" type="button" id="ib-back" aria-label="Назад">' + icon('arrow-left') + '</button>' +
      '<div class="who" id="ib-who"><div class="ib-ava">' + esc(S.initial(c.display_name)) + '</div><div style="min-width:0">' +
      '<div class="nm">' + esc(c.display_name) + '</div><div class="sub">' + esc(handles.join(' · ') || (c.company || '')) + '</div></div></div>' +
      '<div class="ib-head-actions">' +
      '<button class="ib-stage-btn" type="button" id="ib-stage" style="--c:' + esc(s.color) + '" title="Стадия в CRM"><i></i><span>' + esc(s.label) + '</span>' + icon('chevron-down') + '</button>' +
      '<button class="ib-bot-btn' + (paused ? ' paused' : '') + '" type="button" id="ib-bot" title="Бот в переписке с этим клиентом">' +
      (paused ? icon('pause') : '<span class="led"></span>') + '<span>' + (paused ? (c.ai_paused_forever ? 'Бот выключен' : 'Бот на паузе до ' + esc(fmtWhen(c.ai_paused_until, true))) : 'Бот отвечает') + '</span>' + icon('chevron-down') + '</button>' +
      '<button class="btn btn-sm" type="button" id="ib-call" title="Бот позвонит клиенту сейчас">' + icon('phone') + '<span class="lbl">Позвонить</span></button>' +
      '<button class="btn btn-ghost btn-icon btn-sm" type="button" id="ib-card-t" title="Карточка клиента">' + icon('panel-right') + '</button>' +
      '</div>';
    S.$('#ib-back').addEventListener('click', function () { root.classList.remove('show-chat'); });
    S.$('#ib-who').addEventListener('click', toggleCard);
    S.$('#ib-card-t').addEventListener('click', toggleCard);
    S.$('#ib-stage').addEventListener('click', function (e) { stageMenu(e.currentTarget); });
    S.$('#ib-bot').addEventListener('click', function (e) { botMenu(e.currentTarget); });
    S.$('#ib-call').addEventListener('click', callNow);
  }

  function toggleCard() {
    st.cardOpen = !st.cardOpen;
    root.classList.toggle('no-card', !st.cardOpen);
    if (window.innerWidth > 1180) { try { localStorage.setItem('ib_card', st.cardOpen ? '1' : '0'); } catch (e) { /* ignore */ } }
  }

  // ── Стадия и бот ────────────────────────────────────────────────────────
  function stageMenu(btn) {
    var c = st.d.contact;
    pop(btn, [{ header: 'Перенести на стадию' }].concat((st.d.stages || allStages()).map(function (s) {
      return { label: s.label, dot: s.color, sel: s.key === c.status, fn: function () { moveStage(s.key); } };
    })).concat([{ sep: true }, { label: 'Все клиенты этой стадии в CRM', icon: 'external-link', fn: function () { openCrm(null, c.status); } }]));
  }
  function moveStage(key) {
    var c = st.d.contact;
    if (key === c.status) return;
    var prev = c.status;
    c.status = key; renderHead(); renderCard();
    api('/dialogs/' + c.id + '/stage', { method: 'POST', body: { stage: key } }).then(function (r) {
      if (!r.ok) { c.status = prev; renderHead(); renderCard(); S.toast(errText(r), 'error'); return; }
      S.toast('Клиент перенесён: ' + stageOf(key, st.d.stages).label, 'success');
      var it = st.items.filter(function (x) { return x.contact_id === c.id; })[0];
      if (it) { it.stage = key; renderItems(); }
    });
  }

  function botMenu(btn) {
    var c = st.d.contact, paused = !!c.ai_paused_until;
    var items = [{ header: paused ? (c.ai_paused_forever ? 'Бот выключен для этого клиента' : 'Бот молчит до ' + fmtWhen(c.ai_paused_until)) : 'Бот отвечает этому клиенту' }];
    if (paused) items.push({ label: 'Включить бота', icon: 'play', fn: function () { resumeBot(); } });
    items.push({ header: paused ? 'Изменить срок' : 'Остановить бота' });
    [[60, 'На 1 час'], [120, 'На 2 часа'], [240, 'На 4 часа'], [minutesToMorning(), 'До завтра 9:00'], [1440 * 3, 'На 3 дня']].forEach(function (p) {
      items.push({ label: p[1], icon: 'clock', fn: function () { pauseBot(p[0]); } });
    });
    items.push({ label: 'Навсегда (пока не включу)', icon: 'pause', fn: function () { pauseBot(-1); } });
    items.push({ sep: true });
    items.push({ label: 'Пауза касается всех мессенджеров. Звонки бот принимает как обычно.', muted: true });
    pop(btn, items);
  }
  function pauseBot(minutes) {
    var c = st.d.contact;
    api('/dialogs/' + c.id + '/pause', { method: 'POST', body: minutes < 0 ? { forever: true } : { minutes: minutes } }).then(function (r) {
      if (!r.ok) { S.toast(errText(r), 'error'); return; }
      c.ai_paused_until = r.data.ai_paused_until; c.ai_paused_forever = r.data.ai_paused_forever;
      renderHead(); updateItemPause(c);
      S.toast(minutes < 0 ? 'Бот выключен для этого клиента' : 'Бот молчит до ' + fmtWhen(c.ai_paused_until), 'success');
    });
  }
  function resumeBot() {
    var c = st.d.contact;
    api('/dialogs/' + c.id + '/pause', { method: 'DELETE' }).then(function (r) {
      if (!r.ok) { S.toast(errText(r), 'error'); return; }
      c.ai_paused_until = null; c.ai_paused_forever = false;
      renderHead(); updateItemPause(c);
      S.toast('Бот снова отвечает клиенту', 'success');
    });
  }
  function updateItemPause(c) {
    var it = st.items.filter(function (x) { return x.contact_id === c.id; })[0];
    if (!it) return;
    it.paused = !!c.ai_paused_until; it.paused_until = c.ai_paused_until; it.paused_forever = !!c.ai_paused_forever;
    renderItems();
  }

  function callNow() {
    var c = st.d.contact;
    S.confirm({ title: 'Позвонить клиенту?', message: 'Бот позвонит ' + c.display_name + ' в течение минуты. Итог разговора и запись появятся в этой ленте.', confirmText: 'Позвонить' }).then(function (ok) {
      if (!ok) return;
      api('/dialogs/' + c.id + '/call', { method: 'POST' }).then(function (r) {
        if (!r.ok) { S.toast(errText(r), 'error'); return; }
        S.toast(r.data.already ? 'Звонок уже в очереди' : 'Звонок поставлен в очередь — бот позвонит в течение минуты', 'success');
        setTimeout(function () { reloadDialog(true); }, 1500);
      });
    });
  }

  // ── Лента ───────────────────────────────────────────────────────────────
  function renderFeed(keepScroll) {
    var feed = S.$('#ib-feed');
    if (!feed) return;
    var prevH = feed.scrollHeight, prevTop = feed.scrollTop;
    var tl = st.d.timeline, items = tl.items || [];
    var h = tl.has_more ? '<button class="btn btn-sm ib-earlier" type="button" id="ib-earlier">' + icon('history') + '<span>Показать раньше</span></button>' : '';
    if (!items.length) h += '<div class="ib-chat-empty">' + icon('message-circle') + '<p>Сообщений пока нет. Напишите клиенту первым — выберите канал внизу.</p></div>';
    var lastDay = null, prev = null;
    items.forEach(function (it) {
      var day = S.dayKey(it.ts);
      if (day !== lastDay) { h += '<div class="ib-day">' + esc(S.fmtDay(day)) + '</div>'; lastDay = day; prev = null; }
      h += it.type === 'call' ? callHtml(it) : msgHtml(it, prev);
      prev = it;
    });
    feed.innerHTML = h;
    var more = S.$('#ib-earlier');
    if (more) more.addEventListener('click', loadEarlier);
    S.$$('[data-tr]', feed).forEach(function (b) { b.addEventListener('click', function () { translateMsg(b.dataset.ch, b.dataset.tr); }); });
    S.$$('.ib-att-img', feed).forEach(function (img) { img.addEventListener('click', function () { lightbox(img.src); }); });
    S.$$('[data-reply]', feed).forEach(function (b) { b.addEventListener('click', function () { setReplyChannel(b.dataset.reply); }); });
    if (keepScroll === 'top') feed.scrollTop = feed.scrollHeight - prevH + prevTop;
    else if (keepScroll) feed.scrollTop = prevTop;
    else feed.scrollTop = feed.scrollHeight;
  }

  function loadEarlier() {
    var tl = st.d.timeline, key = st.key;
    api('/dialogs/' + st.d.contact.id + '/timeline?before=' + encodeURIComponent(tl.before)).then(function (r) {
      if (st.key !== key || !r.ok) return;
      var known = {};
      tl.items.forEach(function (x) { known[x.type + x.id] = 1; });
      var older = r.data.items.filter(function (x) { return !known[x.type + x.id]; });
      tl.items = older.concat(tl.items);
      tl.has_more = r.data.has_more; tl.before = r.data.before;
      renderFeed('top');
    });
  }

  function looksForeign(text) {
    if (!text) return false;
    var lang = st.settings.my_language || 'ru';
    if (lang !== 'ru' && lang !== 'ky') return false;
    var letters = text.replace(/[^\p{L}]/gu, '');
    if (letters.length < 3) return false;
    var cyr = (letters.match(/[Ѐ-ӿ]/g) || []).length;
    return cyr / letters.length < 0.3;
  }

  function msgHtml(m, prev) {
    var dir = m.who === 'client' ? 'in' : 'out';
    var same = prev && prev.type === 'message' && prev.who === m.who && prev.channel === m.channel;
    var ch = CH[m.channel] || CH.whatsapp;
    var body = '';
    var a = m.attachment;
    if (a) body += attHtml(a, m.voice);
    if (m.text) body += (m.voice ? '<div class="ib-voice-text">' + esc(m.text) + '</div>' : esc(m.text));
    else if (!a) body += m.voice ? '<span class="muted">🎤 Голосовое</span>' : '';
    if (m.translation) body += '<div class="ib-tr"><small>Перевод</small>' + esc(m.translation.text) + '</div>';
    if (m.original) body += '<div class="ib-tr"><small>Вы написали</small>' + esc(m.original.text) + '</div>';
    var by = m.who === 'owner' ? '<span class="by">Вы</span>' : m.who === 'bot' ? '<span class="by bot">Бот</span>' : '';
    var trBtn = (m.who === 'client' && m.text && !m.translation && looksForeign(m.text)) ? '<button class="lnk" type="button" data-tr="' + esc(m.id) + '" data-ch="' + esc(m.channel) + '">Перевести</button>' : '';
    return '<div class="ib-msg ' + dir + ' ' + m.who + (same ? ' same' : '') + '" data-id="' + esc(m.id) + '">' +
      '<div class="ib-bubble">' + (body || '<span class="muted">—</span>') + '</div>' +
      '<div class="ib-meta"><span title="' + ch.label + '" style="color:' + ch.color + ';display:inline-flex">' + icon(ch.icon) + '</span>' + by + '<span>' + esc(fmtTime(m.ts)) + '</span>' + trBtn + '</div></div>';
  }

  function attHtml(a, isVoice) {
    var url = a.url ? esc(a.url) : '';
    if (!url) return '<div class="ib-att-doc">' + icon('file') + '<div><div class="fn">' + esc(a.filename || 'Файл') + '</div><div class="fs">файл недоступен</div></div></div>';
    if (a.kind === 'image') return '<img class="ib-att-img" src="' + url + '" alt="' + esc(a.filename || '') + '" loading="lazy">';
    if (a.kind === 'video') return '<video class="ib-att-video" src="' + url + '" controls preload="metadata"></video>';
    if (a.kind === 'voice' || a.kind === 'audio' || isVoice) return '<audio class="ib-att-audio" src="' + url + '" controls preload="none"></audio>';
    return '<a class="ib-att-doc" href="' + url + '" target="_blank" rel="noopener">' + icon('file-text') + '<div style="min-width:0"><div class="fn">' + esc(a.filename || 'Документ') + '</div><div class="fs">' + esc(S.fmtSize(a.size)) + ' · открыть</div></div></a>';
  }

  function callHtml(c) {
    var o = c.outcome || {};
    var label = c.direction === 'inbound' ? 'Входящий звонок' : 'Исходящий звонок';
    var ic = c.direction === 'inbound' ? 'phone-incoming' : 'phone-outgoing';
    return '<div class="ib-call"><div class="h">' + icon(ic) + '<b>' + label + '</b>' + (o.label ? '<span class="st-badge ' + ({ ok: 'ok', warn: 'warn', error: 'err', progress: 'info' }[o.kind] || '') + '">' + esc(o.label) + '</span>' : '') +
      '<span class="tm">' + esc(fmtTime(c.ts)) + (c.duration ? ' · ' + esc(S.fmtDur(c.duration)) : '') + '</span></div>' +
      (o.detail ? '<div class="d">' + esc(o.detail) + '</div>' : '') +
      (c.record_url ? '<audio controls preload="none" src="' + esc(c.record_url) + '"></audio>' : '') +
      (c.transcript ? '<details><summary>Расшифровка разговора</summary><pre>' + esc(c.transcript) + '</pre></details>' : '') + '</div>';
  }

  function lightbox(src) {
    var lb = document.createElement('div');
    lb.className = 'ib-lightbox';
    lb.innerHTML = '<img src="' + esc(src) + '" alt="">';
    lb.addEventListener('click', function () { lb.remove(); });
    document.addEventListener('keydown', function k(e) { if (e.key === 'Escape') { lb.remove(); document.removeEventListener('keydown', k); } });
    document.body.appendChild(lb);
  }

  function findMsg(id) { return (st.d.timeline.items || []).filter(function (x) { return x.type === 'message' && x.id === id; })[0]; }
  function translateMsg(ch, id, silent) {
    var m = findMsg(id);
    if (!m || m.translation || m._tr) return;
    m._tr = true;
    api('/messages/' + ch + '/' + id + '/translate', { method: 'POST', body: {} }).then(function (r) {
      m._tr = false;
      if (!r.ok) { if (!silent) S.toast(errText(r), 'error'); m._trFailed = true; return; }
      m.translation = { text: r.data.text, language: r.data.language };
      renderFeed(true);
    });
  }
  function autoTranslate() {
    if (!st.settings.auto_translate_incoming || !st.d) return;
    var todo = (st.d.timeline.items || []).filter(function (m) {
      return m.type === 'message' && m.who === 'client' && !m.translation && !m._trFailed && looksForeign(m.text);
    }).slice(-5);
    todo.forEach(function (m) { translateMsg(m.channel, m.id, true); });
  }

  // ── Окно ввода ──────────────────────────────────────────────────────────
  function pauseDefault() { return st.settings.default_pause === undefined ? 'channel' : String(st.settings.default_pause); }
  function channelPauseLabel(ch) {
    var a = st.agents.filter(function (x) { return x.id === (st.d.contact.agent || {}).id; })[0] || st.agents[0];
    var m = a && a.channels && a.channels[ch] ? a.channels[ch].owner_pause_minutes : 30;
    return m < 0 ? 'до включения' : m >= 1440 ? 'сутки' : m >= 60 ? (m / 60) + ' ч' : m + ' мин';
  }

  function renderCompose() {
    var box = S.$('#ib-compose');
    if (!box || !st.d) return;
    var sc = st.d.contact.send_channels || {};
    var any = CH_ORDER.some(function (k) { return (sc[k] || {}).can_send; });
    if (!any) {
      box.innerHTML = '<div class="ib-disabled-compose">' + icon('plug') + ' Написать этому клиенту пока некуда: ' +
        esc(CH_ORDER.map(function (k) { return (sc[k] || {}).reason; }).filter(Boolean)[0] || 'нет подключённых каналов') +
        '. <a href="' + channelsHref() + '" target="_top">Подключить каналы</a></div>';
      return;
    }
    var chBtns = CH_ORDER.map(function (k) {
      var s = sc[k] || {};
      return '<button type="button" data-rc="' + k + '" class="' + (st.replyChannel === k ? 'active' : '') + '" style="--c:' + CH[k].color + '"' +
        (s.can_send ? '' : ' disabled title="' + esc(s.reason || '') + '"') + '>' + icon(CH[k].icon) + '<span class="chl">' + CH[k].label + '</span></button>';
    }).join('');
    var pv = st.pause !== null ? st.pause : pauseDefault();
    var pauseOpts = ['channel', '0', '30', '60', '120', '240', '480', '1440', '-1'].map(function (v) {
      var lbl = v === 'channel' ? 'как в канале (' + channelPauseLabel(st.replyChannel) + ')' : PAUSE_LABELS[v];
      return '<option value="' + v + '"' + (String(pv) === v ? ' selected' : '') + '>' + lbl + '</option>';
    }).join('');
    var note = (sc[st.replyChannel] || {}).note;
    var langs = (st.settings.languages || []).length;
    box.innerHTML = '<div class="ib-compose">' +
      '<div class="ib-compose-top"><span>Ответить в</span><div class="ib-chsel">' + chBtns + '</div>' +
      '<label class="ib-pausesel" title="Сколько бот молчит с клиентом после вашего сообщения">' + icon('bot') + '<span>Бот после ответа:</span>' +
      '<select class="select-native" id="ib-pause">' + pauseOpts + '</select></label></div>' +
      (note ? '<div class="ib-note">' + icon('triangle-alert') + '<span>' + esc(note) + '</span></div>' : '') +
      '<div id="ib-trbar"></div><div id="ib-attach"></div>' +
      '<div class="ib-box" id="ib-box"><textarea id="ib-text" rows="1" placeholder="Сообщение клиенту…"></textarea>' +
      '<button class="ib-send" type="button" id="ib-send" title="Отправить">' + icon('send') + '</button></div>' +
      '<div class="ib-tools">' +
      '<button class="ib-tool icon" type="button" id="ib-file" title="Фото, видео или документ с компьютера">' + icon('paperclip') + '</button>' +
      '<button class="ib-tool icon" type="button" id="ib-lib" title="Файл из библиотеки бота (прайс, КП, каталог)">' + icon('folder') + '</button>' +
      '<button class="ib-tool icon" type="button" id="ib-mic" title="Записать голосовое">' + icon('mic') + '</button>' +
      '<span class="sep"></span>' +
      '<button class="ib-tool" type="button" id="ib-fix" title="Исправить ошибки и тон">' + icon('wand-2') + '<span class="lbl">Улучшить</span></button>' +
      '<button class="ib-tool" type="button" id="ib-lang" title="Перевести на другой язык"' + (langs ? '' : ' disabled') + '>' + icon('languages') + '<span class="lbl">Перевести</span>' + icon('chevron-down') + '</button>' +
      '<button class="ib-tool" type="button" id="ib-draft" title="Бот предложит ответ по базе знаний">' + icon('sparkles') + '<span class="lbl">Ответ от бота</span></button>' +
      '<span class="hint">' + (st.settings.enter_to_send !== false ? 'Enter — отправить, Shift+Enter — новая строка' : 'Ctrl+Enter — отправить') + '</span>' +
      '</div><input type="file" id="ib-file-in" hidden></div>';
    var ta = S.$('#ib-text');
    ta.value = st.drafts[st.d.contact.id] || '';
    autosize(ta);
    ta.addEventListener('input', function () { autosize(ta); if (st.tr && !ta.value.trim()) { st.tr = null; renderTrBar(); } });
    ta.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' || e.isComposing) return;
      var enter = st.settings.enter_to_send !== false;
      if ((enter && !e.shiftKey && !e.ctrlKey) || (!enter && (e.ctrlKey || e.metaKey))) { e.preventDefault(); send(); }
    });
    ta.addEventListener('paste', function (e) {
      var f = e.clipboardData && e.clipboardData.files && e.clipboardData.files[0];
      if (f) { e.preventDefault(); setFile(f); }
    });
    var boxEl = S.$('#ib-box');
    ['dragover', 'drop'].forEach(function (ev) {
      boxEl.addEventListener(ev, function (e) {
        e.preventDefault();
        if (ev === 'drop' && e.dataTransfer.files && e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
      });
    });
    S.$$('[data-rc]', box).forEach(function (b) { b.addEventListener('click', function () { setReplyChannel(b.dataset.rc); }); });
    S.$('#ib-pause').addEventListener('change', function (e) { st.pause = e.target.value; });
    S.$('#ib-send').addEventListener('click', send);
    S.$('#ib-file').addEventListener('click', function () { S.$('#ib-file-in').click(); });
    S.$('#ib-file-in').addEventListener('change', function (e) { if (e.target.files[0]) setFile(e.target.files[0]); e.target.value = ''; });
    S.$('#ib-lib').addEventListener('click', libraryDialog);
    S.$('#ib-mic').addEventListener('click', startRecording);
    S.$('#ib-fix').addEventListener('click', function () { assist('fix'); });
    S.$('#ib-lang').addEventListener('click', function (e) { langMenu(e.currentTarget); });
    S.$('#ib-draft').addEventListener('click', function () { assist('draft'); });
    renderAttach();
    renderTrBar();
    if (window.innerWidth > 860) setTimeout(function () { ta.focus(); }, 30);
  }

  function setReplyChannel(ch) {
    var sc = st.d.contact.send_channels || {};
    if (!(sc[ch] || {}).can_send) { S.toast((sc[ch] || {}).reason || 'В этот канал написать нельзя', 'warning'); return; }
    saveDraft();
    st.replyChannel = ch;
    renderCompose();
  }
  function saveDraft() {
    var ta = S.$('#ib-text');
    if (ta && st.d) st.drafts[st.d.contact.id] = ta.value;
  }
  function autosize(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(180, ta.scrollHeight) + 'px'; }

  // Вложение
  function setFile(f) {
    var max = 20 * 1024 * 1024;
    if (f.size > max) { S.toast('Файл больше 20 МБ', 'error'); return; }
    if (st.attach && st.attach.url) URL.revokeObjectURL(st.attach.url);
    st.attach = { kind: 'file', file: f, name: f.name, size: f.size, mime: f.type, url: /^image\//.test(f.type) ? URL.createObjectURL(f) : null };
    if (st.replyChannel === 'instagram' && !/^image\//.test(f.type)) S.toast('В Instagram этот файл уйдёт ссылкой — Instagram принимает только фото', 'warning');
    renderAttach();
  }
  function renderAttach() {
    var el = S.$('#ib-attach');
    if (!el) return;
    var a = st.attach;
    if (!a || a.kind === 'voice') { if (!st.rec) el.innerHTML = ''; return; }
    var ic = /^video\//.test(a.mime) ? 'video' : /^audio\//.test(a.mime) ? 'volume-2' : a.kind === 'library' ? 'folder' : 'file-text';
    el.innerHTML = '<div class="ib-attach">' + (a.url ? '<img src="' + esc(a.url) + '" alt="">' : icon(ic)) +
      '<div style="min-width:0"><div class="fn">' + esc(a.name) + '</div><div class="fs">' + (a.kind === 'library' ? 'из библиотеки бота' : esc(S.fmtSize(a.size))) + ' · текст ниже станет подписью</div></div>' +
      '<button class="btn btn-ghost btn-icon btn-sm x" type="button" id="ib-att-x" aria-label="Убрать">' + icon('x') + '</button></div>';
    S.$('#ib-att-x').addEventListener('click', function () { if (a.url) URL.revokeObjectURL(a.url); st.attach = null; renderAttach(); });
  }

  // Голосовое
  function startRecording() {
    if (st.rec) return;
    if (!navigator.mediaDevices || !window.MediaRecorder) { S.toast('Браузер не умеет записывать звук', 'error'); return; }
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }).then(function (stream) {
      var mime = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].filter(function (m) { return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m); })[0];
      var rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      var chunks = [];
      st.rec = { rec: rec, stream: stream, started: Date.now(), timer: null, mime: rec.mimeType || mime || 'audio/webm' };
      rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = function () {
        stream.getTracks().forEach(function (t) { t.stop(); });
        var r = st.rec; st.rec = null;
        if (!r || r.cancel) { renderAttach(); return; }
        var blob = new Blob(chunks, { type: r.mime.split(';')[0] });
        st.attach = { kind: 'voice', blob: blob, url: URL.createObjectURL(blob), mime: blob.type, seconds: Math.round((Date.now() - r.started) / 1000) };
        renderVoice();
      };
      rec.start(250);
      renderRecording();
    }).catch(function () { S.toast('Нет доступа к микрофону. Разрешите его в настройках браузера', 'error'); });
  }
  function renderRecording() {
    var el = S.$('#ib-attach');
    if (!el || !st.rec) return;
    el.innerHTML = '<div class="ib-rec"><span class="pulse"></span><span id="ib-rec-t">0:00</span><span>Идёт запись…</span><span class="sp"></span>' +
      '<button class="btn btn-sm" type="button" id="ib-rec-x">Отмена</button><button class="btn btn-sm btn-primary" type="button" id="ib-rec-stop">' + icon('square') + '<span>Готово</span></button></div>';
    S.$('#ib-rec-x').addEventListener('click', function () { stopRecording(true); });
    S.$('#ib-rec-stop').addEventListener('click', function () { stopRecording(false); });
    st.rec.timer = setInterval(function () {
      if (!st.rec) return;
      var s = Math.floor((Date.now() - st.rec.started) / 1000);
      var t = S.$('#ib-rec-t'); if (t) t.textContent = Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
      if (s >= 300) stopRecording(false);   // 5 минут — достаточно для голосового
    }, 250);
  }
  function stopRecording(cancel) {
    if (!st.rec) return;
    clearInterval(st.rec.timer);
    st.rec.cancel = !!cancel;
    try { st.rec.rec.stop(); } catch (e) { st.rec = null; }
  }
  function renderVoice() {
    var el = S.$('#ib-attach'), a = st.attach;
    if (!el || !a) return;
    el.innerHTML = '<div class="ib-rec" style="background:var(--vf-accent-soft);color:var(--vf-accent)">' + icon('mic') + '<audio controls src="' + esc(a.url) + '"></audio>' +
      '<button class="btn btn-ghost btn-icon btn-sm" type="button" id="ib-v-x" title="Удалить запись">' + icon('trash-2') + '</button></div>';
    S.$('#ib-v-x').addEventListener('click', function () { URL.revokeObjectURL(a.url); st.attach = null; renderAttach(); });
  }

  // Перевод / помощник
  function renderTrBar() {
    var el = S.$('#ib-trbar');
    if (!el) return;
    if (!st.tr) { el.innerHTML = ''; return; }
    el.innerHTML = '<div class="ib-trbar">' + icon('languages') + '<span>Переведено: ' + esc(langLabel(st.tr.language)) + '. Клиенту уйдёт перевод, ваш текст сохранится рядом.</span><span class="sp"></span>' +
      '<button type="button" id="ib-tr-undo">Вернуть мой текст</button></div>';
    S.$('#ib-tr-undo').addEventListener('click', function () {
      var ta = S.$('#ib-text'); ta.value = st.tr.original; st.tr = null; autosize(ta); renderTrBar();
    });
  }
  function langLabel(code) { var l = st.languages.filter(function (x) { return x.code === code; })[0]; return l ? l.label : code; }
  function langMenu(btn) {
    var items = [{ header: 'Перевести моё сообщение на' }].concat((st.settings.languages || []).map(function (code) {
      return { label: langLabel(code), fn: function () { assist('translate', code); } };
    })).concat([{ sep: true }, { label: 'Выбрать языки…', icon: 'settings', fn: settingsDialog }]);
    pop(btn, items);
  }
  function assist(action, language) {
    var ta = S.$('#ib-text');
    var text = (st.tr && action !== 'translate') ? ta.value : (st.tr ? st.tr.original : ta.value);
    if (action !== 'draft' && !text.trim()) { S.toast('Сначала напишите текст', 'warning'); ta.focus(); return; }
    var btn = S.$(action === 'fix' ? '#ib-fix' : action === 'draft' ? '#ib-draft' : '#ib-lang');
    btn.classList.add('ib-busy');
    var key = st.key;
    api('/assist', { method: 'POST', body: { action: action, text: text, language: language || null, contact_id: st.d.contact.id } }).then(function (r) {
      btn.classList.remove('ib-busy');
      if (st.key !== key) return;
      if (!r.ok) { S.toast(errText(r), 'error'); return; }
      if (action === 'translate') st.tr = { original: text, language: language };
      else if (action === 'fix' && st.tr) st.tr = null;
      ta.value = r.data.text; autosize(ta); ta.focus();
      renderTrBar();
    });
  }

  // Отправка
  function send() {
    if (!st.d || st.sending) return;
    if (st.rec) { S.toast('Сначала остановите запись', 'warning'); return; }
    var ta = S.$('#ib-text'), text = ta.value.trim(), a = st.attach;
    if (!text && !a) { ta.focus(); return; }
    if (!st.replyChannel) { S.toast('Выберите канал', 'warning'); return; }
    var fd = new FormData();
    fd.append('channel', st.replyChannel);
    fd.append('text', a && a.kind === 'voice' ? '' : text);
    fd.append('pause', st.pause !== null ? st.pause : pauseDefault());
    if (st.tr && text) { fd.append('original_text', st.tr.original); fd.append('original_language', st.settings.my_language || 'ru'); }
    if (a && a.kind === 'file') fd.append('file', a.file, a.name);
    if (a && a.kind === 'library') fd.append('library_file_id', a.id);
    if (a && a.kind === 'voice') fd.append('voice', a.blob, 'voice.' + (/ogg/.test(a.mime) ? 'ogg' : /mp4/.test(a.mime) ? 'm4a' : 'webm'));
    var sendBtn = S.$('#ib-send');
    st.sending = true; sendBtn.disabled = true; sendBtn.classList.add('ib-busy');
    var contact = st.d.contact, key = st.key;
    postForm('/dialogs/' + contact.id + '/send', fd).then(function (r) {
      st.sending = false;
      var b = S.$('#ib-send'); if (b) { b.disabled = false; b.classList.remove('ib-busy'); }
      if (!r.ok) { S.toast(errText(r), 'error'); return; }
      if (st.key !== key) return;
      if (a && a.url) URL.revokeObjectURL(a.url);
      st.attach = null; st.tr = null; st.drafts[contact.id] = '';
      if (a && a.kind === 'voice' && text) { ta.value = text; } else ta.value = '';
      autosize(ta); renderAttach(); renderTrBar();
      contact.ai_paused_until = r.data.paused_until || contact.ai_paused_until;
      if (r.data.paused_forever) contact.ai_paused_forever = true;
      if (r.data.sent_as === 'link') S.toast('Instagram не принимает такие файлы — клиент получил ссылку', 'info');
      reloadDialog(false).then(function () { renderHead(); });
      loadList(true, true);
    });
  }

  // ── Карточка клиента ────────────────────────────────────────────────────
  function renderCard() {
    var el = S.$('#ib-card');
    if (!el || !st.d) return;
    var c = st.d.contact, mem = c.memory || {};
    var stages = st.d.stages || allStages();
    var stageOpts = stages.map(function (s) { return '<option value="' + esc(s.key) + '"' + (s.key === c.status ? ' selected' : '') + '>' + esc(s.label) + '</option>'; }).join('');
    var kv = [];
    if (c.real_phone) kv.push(['phone', '<a href="tel:' + esc(c.real_phone) + '">' + esc(S.fmtPhone(c.real_phone)) + '</a>' + (c.call_phone ? ' <small>· звоним на ' + esc(S.fmtPhone(c.call_phone)) + '</small>' : '')]);
    (c.alt_phones || []).forEach(function (p) { kv.push(['smartphone', esc(S.fmtPhone(p)) + ' <small>второй номер</small>']); });
    var hd = c.handles || {};
    if (hd.whatsapp) kv.push(['message-circle', 'WhatsApp ' + esc(hd.whatsapp.phone ? S.fmtPhone(hd.whatsapp.phone) : '') + (hd.whatsapp.name ? ' <small>· ' + esc(hd.whatsapp.name) + '</small>' : '')]);
    if (hd.telegram) kv.push(['send', 'Telegram ' + esc(hd.telegram.username ? '@' + hd.telegram.username : (hd.telegram.name || ''))]);
    if (hd.instagram) kv.push(['instagram', 'Instagram ' + esc(hd.instagram.username ? '@' + hd.instagram.username : '')]);
    if (c.company || c.position) kv.push(['building', esc([c.company, c.position].filter(Boolean).join(' · '))]);

    var facts = (mem.key_facts || []).slice(0, 10).map(function (f) { return '<li>' + esc(typeof f === 'string' ? f : JSON.stringify(f)) + '</li>'; }).join('');
    var rows = [];
    if (mem.niche || mem.industry) rows.push(['Сфера', mem.niche || mem.industry]);
    if (mem.direction_need || mem.needs) rows.push(['Что нужно', mem.direction_need || (Array.isArray(mem.needs) ? mem.needs.join(', ') : mem.needs)]);
    if (mem.volume || mem.call_volume_per_day) rows.push(['Объём', mem.volume || mem.call_volume_per_day]);
    if (mem.best_time) rows.push(['Удобное время', mem.best_time]);
    if (mem.funnel_stage) rows.push(['Где в решении', mem.funnel_stage]);
    if (mem.proposal_sent_at) rows.push(['КП отправлено', S.fmtDate(mem.proposal_sent_at) + (mem.proposal_channel ? ' · ' + mem.proposal_channel : '')]);
    var know = (mem.summary || facts || rows.length)
      ? (mem.summary ? '<p>' + esc(mem.summary) + '</p>' : '') +
        rows.map(function (r) { return '<div class="row"><b>' + esc(r[0]) + ':</b> ' + esc(String(r[1])) + '</div>'; }).join('') +
        (facts ? '<ul>' + facts + '</ul>' : '')
      : '<div class="muted small">Бот пока ничего не записал о клиенте. Он заполняет это по ходу переписки и звонков.</div>';
    var docs = (c.documents || []).filter(Boolean).map(function (d) {
      return '<a class="ib-doc" href="' + esc(d.url || '#') + '" target="_blank" rel="noopener">' + icon('file-text') + '<span>' + esc(d.filename) + '</span></a>';
    }).join('');

    var tasks = (c.tasks || []).map(function (t) {
      var mine = t.created_by === 'owner';
      var what = t.channel === 'owner' ? 'Напоминание вам' : t.channel === 'telegram' ? 'Сообщение в Telegram' : 'Звонок';
      return '<div class="ib-task"><span class="who ' + (mine ? 'owner' : 'agent') + '" title="' + (mine ? 'Поставили вы' : 'Поставил бот') + '">' + icon(mine ? 'user' : 'bot') + '</span>' +
        '<div style="min-width:0"><div class="t">' + esc(t.title || what) + '</div><div class="s">' + esc(what) + ' · ' + esc(fmtWhen(t.scheduled_time)) + ' · ' + (mine ? 'вы' : 'бот') + '</div></div>' +
        '<button class="btn btn-ghost btn-icon btn-sm del" type="button" data-del-task="' + esc(t.id) + '" title="Отменить">' + icon('x') + '</button></div>';
    }).join('');

    el.innerHTML =
      '<div class="ib-card-head"><button class="btn btn-ghost btn-icon btn-sm x" type="button" id="ib-card-x" aria-label="Скрыть">' + icon('x') + '</button>' +
      '<div class="ib-ava lg">' + esc(S.initial(c.display_name)) + '</div><div class="nm">' + esc(c.display_name) + '</div>' +
      '<div class="sub">' + esc(c.agent ? 'Бот: ' + c.agent.name : '') + '</div>' +
      '<div class="acts"><button class="btn btn-sm btn-primary" type="button" id="ib-crm">' + icon('external-link') + '<span>Открыть в CRM</span></button>' +
      '<button class="btn btn-sm" type="button" id="ib-call2">' + icon('phone') + '<span>Позвонить</span></button></div></div>' +
      '<div class="ib-sec"><h4>Контакты</h4><div class="ib-kv">' + (kv.map(function (x) { return icon(x[0]) + '<div class="v">' + x[1] + '</div>'; }).join('') || '<span></span><div class="muted small">Нет данных</div>') + '</div></div>' +
      '<div class="ib-sec ib-know"><h4>Что узнал бот</h4>' + know + (docs ? '<div style="margin-top:10px">' + docs + '</div>' : '') + '</div>' +
      '<div class="ib-sec"><h4>Задачи <button class="btn btn-ghost btn-sm" type="button" id="ib-rem">' + icon('plus') + 'Напоминание</button></h4>' +
      (tasks || '<div class="muted small">Задач нет.</div>') + '</div>' +
      '<div class="ib-sec"><h4>Сделка</h4>' +
      '<div class="ib-field"><label>Стадия</label><select class="select-native" id="cf-stage">' + stageOpts + '</select></div>' +
      '<div class="ib-2"><div class="ib-field"><label>Имя</label><input class="input" id="cf-name" value="' + esc(c.name || '') + '"></div>' +
      '<div class="ib-field"><label>Сумма, сом</label><input class="input" id="cf-deal" type="number" min="0" step="1" value="' + esc(c.deal_amount == null ? '' : c.deal_amount) + '"></div></div>' +
      '<div class="ib-field"><label>Компания</label><input class="input" id="cf-company" value="' + esc(c.company || '') + '"></div>' +
      '<div class="ib-field"><label>Теги (через запятую)</label><input class="input" id="cf-tags" value="' + esc((c.tags || []).join(', ')) + '"></div>' +
      '<div class="ib-field"><label>Следующий шаг</label><input class="input" id="cf-next" value="' + esc(c.next_step_text || '') + '" placeholder="Например: отправить счёт"></div>' +
      '<div class="ib-field"><label>Заметки</label><textarea class="textarea" id="cf-notes" rows="3">' + esc(c.notes || '') + '</textarea></div>' +
      '<button class="btn btn-sm" type="button" id="cf-save" style="width:100%">' + icon('save') + '<span>Сохранить</span></button></div>';
    S.$('#ib-card-x').addEventListener('click', toggleCard);
    S.$('#ib-crm').addEventListener('click', function () { openCrm(c.id); });
    S.$('#ib-call2').addEventListener('click', callNow);
    S.$('#ib-rem').addEventListener('click', reminderDialog);
    S.$('#cf-stage').addEventListener('change', function (e) { moveStage(e.target.value); });
    S.$('#cf-save').addEventListener('click', saveCard);
    S.$$('[data-del-task]', el).forEach(function (b) {
      b.addEventListener('click', function () {
        S.confirm({ title: 'Отменить задачу?', message: 'Задача будет отменена.', confirmText: 'Отменить задачу', danger: true }).then(function (ok) {
          if (!ok) return;
          api('/api/agent/tasks/' + b.dataset.delTask, { method: 'DELETE' }).then(function (r) {
            if (!r.ok) { S.toast(errText(r), 'error'); return; }
            c.tasks = c.tasks.filter(function (t) { return t.id !== b.dataset.delTask; }); renderCard(); loadList(true, true);
          });
        });
      });
    });
  }

  function saveCard() {
    var c = st.d.contact;
    var deal = S.$('#cf-deal').value;
    var body = {
      name: S.$('#cf-name').value.trim() || null,
      company: S.$('#cf-company').value.trim(),
      tags: S.$('#cf-tags').value.split(',').map(function (x) { return x.trim(); }).filter(Boolean),
      next_step_text: S.$('#cf-next').value.trim(),
      notes: S.$('#cf-notes').value
    };
    if (deal !== '') body.deal_amount = Number(deal);
    api('/api/agent/contacts/' + c.id, { method: 'PUT', body: body }).then(function (r) {
      if (!r.ok) { S.toast(errText(r), 'error'); return; }
      S.toast('Сохранено', 'success');
      reloadDialog(true);
      loadList(true, true);
    });
  }

  function crmHref(contactId, stage) {
    if (st.mode === 'reactive') return '/static/start-crm.html' + (contactId ? '?contact=' + contactId : stage ? '?stage=' + encodeURIComponent(stage) : '');
    if (contactId) return '/static/agent.html?tab=clients&client=' + contactId;
    return '/static/agent.html?tab=' + (stage ? 'pipeline' : 'clients');
  }
  function openCrm(contactId, stage) {
    if (EMBED) {
      try { parent.postMessage({ type: 'inbox:open-crm', contact: contactId || null, stage: stage || null }, location.origin); } catch (e) { /* ignore */ }
      return;
    }
    location.href = crmHref(contactId, stage);
  }

  // ── Чат без карточки (WhatsApp) ─────────────────────────────────────────
  function openUnlinked(chatId) {
    api('/unlinked/' + chatId).then(function (r) {
      if (st.key !== 'wa_chat:' + chatId) return;
      if (!r.ok) { S.$('#ib-chat').innerHTML = '<div class="ib-chat-empty"><p>' + esc(errText(r)) + '</p></div>'; return; }
      st.unlinked = r.data;
      var ch = r.data.chat;
      S.$('#ib-chat').innerHTML = '<div class="ib-chat-head"><button class="btn btn-ghost btn-icon btn-sm ib-back" type="button" id="ib-back">' + icon('arrow-left') + '</button>' +
        '<div class="who"><div class="ib-ava">' + esc(S.initial(ch.name || ch.phone)) + '</div><div><div class="nm">' + esc(ch.name || ch.phone || 'Новый клиент') + '</div><div class="sub">' + esc(S.fmtPhone(ch.phone)) + ' · WhatsApp</div></div></div></div>' +
        '<div class="ib-feed" id="ib-feed">' + r.data.items.map(function (m) { return msgHtml(m, null); }).join('') + '</div>' +
        '<div class="ib-compose"><div class="ib-note">' + icon('info') + '<span>Этого человека нет в CRM, поэтому бот ему не отвечает (в настройках WhatsApp бот отвечает только знакомым). Создайте карточку — после этого можно ответить, а бот начнёт вести диалог.</span></div>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap"><input class="input" id="ib-ln-name" placeholder="Имя клиента" value="' + esc(ch.name || '') + '" style="flex:1;min-width:180px">' +
        '<button class="btn btn-primary" type="button" id="ib-ln">' + icon('plus') + '<span>Создать карточку</span></button></div></div>';
      S.$('#ib-back').addEventListener('click', function () { root.classList.remove('show-chat'); });
      var feed = S.$('#ib-feed'); feed.scrollTop = feed.scrollHeight;
      S.$('#ib-card').innerHTML = '';
      S.$('#ib-ln').addEventListener('click', function () {
        api('/unlinked/' + chatId + '/link', { method: 'POST', body: { name: S.$('#ib-ln-name').value.trim() || null } }).then(function (r2) {
          if (!r2.ok) { S.toast(errText(r2), 'error'); return; }
          S.toast('Карточка создана', 'success');
          if (st.folder === 'no_card') st.folder = 'all';
          loadList(true, true);
          openDialog('contact:' + r2.data.contact_id);
        });
      });
    });
  }

  // ── Окна ────────────────────────────────────────────────────────────────
  function libraryDialog() {
    var agentId = (st.d.contact.agent || {}).id;
    var dlg = S.dialog({ title: 'Файл из библиотеки бота', body: '<div class="ib-lib" id="ib-lib-list"><div class="skeleton" style="height:120px"></div></div>' });
    api('/api/agent/files?source=library&limit=200' + (agentId ? '&agent_id=' + agentId : '')).then(function (r) {
      var list = S.$('#ib-lib-list', dlg.el);
      if (!list) return;
      var files = (r.ok && r.data.files) || [];
      if (!files.length) {
        list.innerHTML = '<div class="muted">В библиотеке пока нет файлов. Загрузите прайс, КП или каталог в разделе знаний бота.</div>';
        return;
      }
      list.innerHTML = files.map(function (f) {
        var ic = f.kind === 'image' ? 'image' : f.kind === 'video' ? 'video' : 'file-text';
        return '<button type="button" data-f="' + esc(f.id) + '">' + icon(ic) + '<div style="min-width:0"><div class="fn">' + esc(f.title || f.filename) + '</div><div class="fs">' + esc(f.filename) + ' · ' + esc(S.fmtSize(f.size_bytes)) + '</div></div></button>';
      }).join('');
      S.$$('[data-f]', list).forEach(function (b) {
        b.addEventListener('click', function () {
          var f = files.filter(function (x) { return x.id === b.dataset.f; })[0];
          st.attach = { kind: 'library', id: f.id, name: f.title || f.filename, size: f.size_bytes, mime: f.mime_type || '', url: f.kind === 'image' ? (f.preview_url || null) : null };
          renderAttach(); dlg.close();
          var ta = S.$('#ib-text'); if (ta) ta.focus();
        });
      });
    });
  }

  function reminderDialog() {
    var c = st.d.contact;
    var def = new Date(Date.now() + 3600000);
    var dlg = S.dialog({
      title: 'Напоминание вам', size: 'sm',
      body: '<div class="ib-field"><label>О чём напомнить</label><input class="input" id="rm-t" value="Связаться с ' + esc(c.display_name) + '"></div>' +
        '<div class="ib-field"><label>Когда (Бишкек)</label><input class="input" id="rm-at" type="datetime-local" value="' + esc(S.isoToLocalInput(def.toISOString())) + '"></div>' +
        '<div class="muted small">Напоминание появится в задачах клиента и в CRM. Бот его не выполняет — это задача для вас.</div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>Создать</button>'
    });
    S.$('[data-c]', dlg.el).addEventListener('click', dlg.close);
    S.$('[data-ok]', dlg.el).addEventListener('click', function () {
      var at = S.localInputToIso(S.$('#rm-at', dlg.el).value);
      if (!at) { S.toast('Укажите время', 'warning'); return; }
      api('/dialogs/' + c.id + '/reminder', { method: 'POST', body: { title: S.$('#rm-t', dlg.el).value, scheduled_time: at } }).then(function (r) {
        if (!r.ok) { S.toast(errText(r), 'error'); return; }
        dlg.close(); S.toast('Напоминание создано', 'success');
        c.tasks = (c.tasks || []).concat([r.data]).sort(function (a, b) { return a.scheduled_time < b.scheduled_time ? -1 : 1; });
        renderCard(); loadList(true, true);
      });
    });
  }

  function settingsDialog() {
    var s = st.settings;
    var langs = st.languages.map(function (l) {
      return '<label class="ib-check"><input type="checkbox" value="' + l.code + '"' + ((s.languages || []).indexOf(l.code) >= 0 ? ' checked' : '') + '>' + esc(l.label) + '</label>';
    }).join('');
    var myOpts = st.languages.map(function (l) { return '<option value="' + l.code + '"' + (s.my_language === l.code ? ' selected' : '') + '>' + esc(l.label) + '</option>'; }).join('');
    var pauseOpts = ['channel', '30', '60', '120', '240', '480', '1440', '-1'].map(function (v) {
      return '<option value="' + v + '"' + (String(s.default_pause) === v ? ' selected' : '') + '>' + PAUSE_LABELS[v] + '</option>';
    }).join('');
    function sw(id, on) { return '<label class="switch"><input type="checkbox" id="' + id + '"' + (on ? ' checked' : '') + '><span class="track"></span></label>'; }
    var chans = CH_ORDER.map(function (k) {
      return '<div class="ib-set-row"><div><div class="t">' + CH[k].label + '</div><div class="d">' + (channelState(k).connected ? 'Подключён' : 'Не подключён') + '</div></div>' + sw('set-ch-' + k, (s.channels || {})[k] !== false) + '</div>';
    }).join('');
    var dlg = S.dialog({
      title: 'Настройки инбокса', size: 'lg',
      body:
        '<div class="ib-set-h">Каналы в инбоксе</div>' + chans +
        '<div class="ib-set-h">Языки помощника</div><div class="muted small" style="margin-bottom:8px">Эти языки будут в меню «Перевести». Помощник переводит ваше сообщение перед отправкой и сообщения клиентов.</div>' +
        '<div class="ib-langs" id="set-langs">' + langs + '</div>' +
        '<div class="ib-set-row" style="margin-top:8px"><div><div class="t">Мой язык</div><div class="d">На него переводятся сообщения клиентов</div></div><select class="select-native" id="set-my">' + myOpts + '</select></div>' +
        '<div class="ib-set-row"><div><div class="t">Переводить входящие сразу</div><div class="d">Сообщения не на вашем языке переводятся при открытии диалога (тратит кредиты бота)</div></div>' + sw('set-auto', s.auto_translate_incoming) + '</div>' +
        '<div class="ib-set-row"><div><div class="t">Тон помощника</div><div class="d">Как помощник исправляет и пишет черновики</div></div><select class="select-native" id="set-tone">' +
        [['neutral', 'Деловой'], ['friendly', 'Дружелюбный'], ['formal', 'Официальный, на «Вы»']].map(function (t) { return '<option value="' + t[0] + '"' + (s.tone === t[0] ? ' selected' : '') + '>' + t[1] + '</option>'; }).join('') + '</select></div>' +
        '<div class="ib-set-h">Бот и ответы</div>' +
        '<div class="ib-set-row"><div><div class="t">Бот после вашего ответа молчит</div><div class="d">По умолчанию для нового сообщения. Можно поменять в окне ввода для конкретного ответа</div></div><select class="select-native" id="set-pause">' + pauseOpts + '</select></div>' +
        '<div class="ib-set-row"><div><div class="t">Enter отправляет</div><div class="d">Выключено — отправка по Ctrl+Enter, Enter переносит строку</div></div>' + sw('set-enter', s.enter_to_send !== false) + '</div>' +
        '<div class="ib-set-h">Уведомления</div>' +
        '<div class="ib-set-row"><div><div class="t">Звук нового сообщения</div></div>' + sw('set-sound', s.sound !== false) + '</div>' +
        '<div class="ib-set-row"><div><div class="t">Уведомления браузера</div><div class="d">Всплывающее окно, когда инбокс открыт в фоне</div></div>' + sw('set-notify', !!s.desktop_notify) + '</div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>Сохранить</button>'
    });
    S.$('[data-c]', dlg.el).addEventListener('click', dlg.close);
    S.$('[data-ok]', dlg.el).addEventListener('click', function () {
      var chosen = S.$$('#set-langs input:checked', dlg.el).map(function (i) { return i.value; });
      if (!chosen.length) { S.toast('Выберите хотя бы один язык', 'warning'); return; }
      var dp = S.$('#set-pause', dlg.el).value;
      var body = {
        channels: {},
        languages: chosen,
        my_language: S.$('#set-my', dlg.el).value,
        auto_translate_incoming: S.$('#set-auto', dlg.el).checked,
        tone: S.$('#set-tone', dlg.el).value,
        default_pause: dp === 'channel' ? 'channel' : Number(dp),
        enter_to_send: S.$('#set-enter', dlg.el).checked,
        sound: S.$('#set-sound', dlg.el).checked,
        desktop_notify: S.$('#set-notify', dlg.el).checked
      };
      CH_ORDER.forEach(function (k) { body.channels[k] = S.$('#set-ch-' + k, dlg.el).checked; });
      if (body.desktop_notify && window.Notification && Notification.permission === 'default') Notification.requestPermission();
      api('/settings', { method: 'PUT', body: body }).then(function (r) {
        if (!r.ok) { S.toast(errText(r), 'error'); return; }
        st.settings = r.data; dlg.close(); S.toast('Настройки сохранены', 'success');
        renderSide(); renderBanner(); loadList(true);
        if (st.d) { renderCompose(); autoTranslate(); }
      });
    });
  }

  function channelsDialog() {
    var agent = (st.d && st.d.contact.agent && st.agents.filter(function (a) { return a.id === st.d.contact.agent.id; })[0]) || st.agents[0];
    var chs = agent.channels || {};
    var PAUSES = [[10, '10 минут'], [30, '30 минут'], [60, '1 час'], [1440, 'сутки'], [-1, 'до включения вручную']];
    var DELAYS = [5, 10, 15, 20, 30, 60];
    var body = CH_ORDER.map(function (k) {
      var c = chs[k] || {};
      var head = '<div class="ib-set-h" style="display:flex;align-items:center;gap:8px"><span class="st-ch" style="--c:' + CH[k].color + '">' + icon(CH[k].icon) + '</span>' + CH[k].label +
        '<span class="muted small" style="font-weight:400">' + (c.connected ? 'подключён' + (c.account ? ' · ' + esc(c.account) : '') : 'не подключён') + '</span></div>';
      if (!c.exists) return head + '<div class="muted small">Подключите канал в настройках бота: <a href="' + channelsHref() + '" target="_top">перейти</a></div>';
      var rows = '';
      if (k !== 'instagram') {
        rows += '<div class="ib-set-row"><div><div class="t">Бот отвечает в ' + CH[k].label + '</div></div><label class="switch"><input type="checkbox" data-k="' + k + '" data-f="auto_reply_enabled"' + (c.auto_reply_enabled ? ' checked' : '') + '><span class="track"></span></label></div>';
        rows += '<div class="ib-set-row"><div><div class="t">Кому отвечает бот</div><div class="d">«Всем» — бот отвечает новым людям и сам заводит карточку</div></div><select class="select-native" data-k="' + k + '" data-f="reply_scope">' +
          '<option value="contacts"' + (c.reply_scope !== 'all' ? ' selected' : '') + '>Только клиентам из CRM</option><option value="all"' + (c.reply_scope === 'all' ? ' selected' : '') + '>Всем, кто пишет</option></select></div>';
      }
      rows += '<div class="ib-set-row"><div><div class="t">После вашего сообщения бот молчит</div></div><select class="select-native" data-k="' + k + '" data-f="owner_pause_minutes">' +
        PAUSES.map(function (p) { return '<option value="' + p[0] + '"' + (c.owner_pause_minutes === p[0] ? ' selected' : '') + '>' + p[1] + '</option>'; }).join('') + '</select></div>';
      rows += '<div class="ib-set-row"><div><div class="t">Бот отвечает через</div><div class="d">Ждёт, пока клиент допишет</div></div><select class="select-native" data-k="' + k + '" data-f="reply_delay_seconds">' +
        DELAYS.map(function (d) { return '<option value="' + d + '"' + (c.reply_delay_seconds === d ? ' selected' : '') + '>' + d + ' секунд</option>'; }).join('') + '</select></div>';
      return head + rows;
    }).join('');
    var dlg = S.dialog({ title: 'Каналы' + (st.agents.length > 1 ? ' · ' + agent.name : ''), size: 'lg', body: body + '<div class="muted small" style="margin-top:12px">Изменения сохраняются сразу.</div>',
      foot: '<button class="btn" type="button" data-c>Готово</button>' });
    S.$('[data-c]', dlg.el).addEventListener('click', dlg.close);
    var urls = { whatsapp: '/api/agent/whatsapp/settings', telegram: '/api/agent/telegram-account/settings', instagram: '/api/agent/connectors/instagram/settings' };
    S.$$('[data-k]', dlg.el).forEach(function (inp) {
      inp.addEventListener('change', function () {
        var k = inp.dataset.k, f = inp.dataset.f, b = {};
        b[f] = inp.type === 'checkbox' ? inp.checked : (f === 'reply_scope' ? inp.value : Number(inp.value));
        api(urls[k] + '?agent_id=' + agent.id, { method: 'PATCH', body: b }).then(function (r) {
          if (!r.ok) { S.toast(errText(r), 'error'); return; }
          (agent.channels[k] || {})[f] = b[f];
          S.toast('Сохранено', 'success');
          if (st.d) renderCompose();
        });
      });
    });
  }

  // ── Меню-поповер ────────────────────────────────────────────────────────
  var popEl = null;
  function closePop() { if (!popEl) return false; popEl.remove(); popEl = null; return true; }
  function pop(anchor, items) {
    closePop();
    var el = document.createElement('div');
    el.className = 'ib-pop';
    el.innerHTML = items.map(function (it, i) {
      if (it.sep) return '<div class="ib-pop-sep"></div>';
      if (it.header) return '<div class="ib-pop-h">' + esc(it.header) + '</div>';
      if (it.muted) return '<div class="muted small" style="padding:4px 10px 6px">' + esc(it.label) + '</div>';
      return '<button class="ib-pop-i' + (it.sel ? ' sel' : '') + '" type="button" data-i="' + i + '">' +
        (it.dot ? '<i class="dot" style="--c:' + esc(it.dot) + '"></i>' : it.icon ? icon(it.icon) : '') + '<span>' + esc(it.label) + '</span>' + (it.sel ? icon('check') : '') + '</button>';
    }).join('');
    document.body.appendChild(el);
    var r = anchor.getBoundingClientRect();
    var w = el.offsetWidth, h = el.offsetHeight;
    var left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    var top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    el.style.left = left + 'px'; el.style.top = top + 'px';
    S.$$('[data-i]', el).forEach(function (b) {
      b.addEventListener('click', function () { var it = items[+b.dataset.i]; closePop(); if (it.fn) it.fn(); });
    });
    popEl = el;
    setTimeout(function () {
      document.addEventListener('mousedown', function off(e) {
        if (popEl && !popEl.contains(e.target)) { closePop(); }
        document.removeEventListener('mousedown', off);
      });
    }, 0);
  }

  // ── Обновление в фоне и уведомления ─────────────────────────────────────
  function startPolling() {
    clearTimeout(st.pollT);
    st.pollT = setTimeout(function tick() {
      poll().then(function () { st.pollT = setTimeout(tick, (document.hidden || st.hidden) ? 30000 : 6000); });
    }, 6000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(true); });
  }
  function poll(force) {
    return api('/updates').then(function (r) {
      if (!r.ok) return;
      var changed = r.data.latest_at !== st.latestAt;
      noteUnread(r.data.counts && r.data.counts.unread_messages);
      if (!changed && !force) return;
      st.latestAt = r.data.latest_at;
      var listEl = S.$('#ib-items'), top = listEl ? listEl.scrollTop : 0;
      return Promise.all([
        loadList(true, true).then(function () { if (listEl) listEl.scrollTop = top; }),
        st.d ? reloadDialog(true).then(function () { if (st.d) api('/dialogs/' + st.d.contact.id + '/read', { method: 'POST' }); }) : null
      ]);
    });
  }

  function noteUnread(n) {
    if (n === undefined || n === null) return;
    var prev = st.unreadMsgs;
    st.unreadMsgs = n;
    document.title = (n ? '(' + n + ') ' : '') + 'Инбокс | Voksy AI';
    try { parent.postMessage({ type: 'inbox:unread', n: n }, location.origin); } catch (e) { /* ignore */ }
    if (prev !== null && n > prev) {
      if (st.settings.sound !== false) beep();
      if (st.settings.desktop_notify && (document.hidden || st.hidden) && window.Notification && Notification.permission === 'granted') {
        try { new Notification('Новое сообщение от клиента', { body: 'Откройте инбокс, чтобы ответить', icon: '/static/favicon.svg', tag: 'voksy-inbox' }); } catch (e) { /* ignore */ }
      }
    }
  }
  var audioCtx = null;
  function beep() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      var o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = 'sine'; o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, audioCtx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.12, audioCtx.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.35);
      o.connect(g); g.connect(audioCtx.destination);
      o.start(); o.stop(audioCtx.currentTime + 0.4);
    } catch (e) { /* звук недоступен — не страшно */ }
  }
})();
