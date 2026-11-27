/**
 * Чат на странице салона (Voksy Beauty, шаг 1.6в) — window.SalonChat.
 *
 * Страница салона (/beauty/<slug>, /p/<slug>): круглая кнопка «Спросить» справа внизу → окно чата
 * (на телефоне — на весь экран, на компьютере — панель в углу). Отвечает бот салона (тот же агент,
 * что в WhatsApp/Telegram/Instagram), владелец видит разговор в инбоксе (канал «Сайт») и может
 * ответить сам. /b/<slug>?embed=1&chat=1 — только чат (виджет booking-widget.js с data-chat на сайте
 * владельца); «Записаться онлайн» внутри переключает на запись.
 *
 * API: /api/public/chat/<slug> (start / messages / identity), токен посетителя — localStorage
 * vxs_chat_<slug>, передаётся заголовком X-Chat-Token. Новые сообщения — опросом: часто, пока бот
 * «печатает» или посетитель только что писал, редко — пока окно открыто, раз в 30 с — пока закрыто
 * (значок непрочитанного). Без зависимостей; значки и стили — свои (salon/chat.css).
 */
(function () {
  'use strict';
  var IC = {
    chat: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    phone: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    cal: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
    check: '<path d="M20 6 9 17l-5-5"/>'
  };
  function ic(n) { return '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">' + (IC[n] || '') + '</svg>'; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function linkify(s) {
    return esc(s).replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)»"'])/g, '<a href="$1" target="_blank" rel="noopener nofollow">$1</a>').replace(/\n/g, '<br>');
  }
  function hm(iso) { try { return new Date(iso).toLocaleTimeString('ru-RU', { timeZone: 'Asia/Bishkek', hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; } }
  function ls(k, v) {
    try {
      if (v === undefined) return localStorage.getItem(k);
      if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v);
    } catch (e) { return null; }
    return null;
  }

  var C = {
    slug: '', base: '', sal: null, mode: 'page', embed: false, onBook: null,
    token: null, items: [], cursor: null, typing: false, open: false, started: false,
    unread: 0, pollT: null, lastActive: 0, sending: false, name: null, phone: null, idForm: false, seen: {}
  };
  var fab, panel, listEl, inputEl;

  function req(method, path, body) {
    var h = { 'Content-Type': 'application/json' };
    if (C.token) h['X-Chat-Token'] = C.token;
    return fetch(C.base + path, { method: method, headers: h, cache: 'no-store', body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) { var e = new Error((typeof d.message === 'string' && d.message) || (typeof d.detail === 'string' && d.detail) || 'Не получилось — попробуйте ещё раз'); e.status = r.status; throw e; }
          return d;
        });
      });
  }

  // ── разметка ──
  function buildFab() {
    fab = document.createElement('button');
    fab.type = 'button';
    fab.className = 'sc-fab';
    fab.setAttribute('aria-label', 'Написать в чат');
    fab.innerHTML = ic('chat') + '<span class="sc-fab-t">Спросить</span><b class="sc-badge" hidden></b>';
    fab.addEventListener('click', function () { openChat(); });
    document.body.appendChild(fab);
    var cta = document.getElementById('cta');
    if (cta && window.MutationObserver) {
      new MutationObserver(function () { fab.classList.toggle('up', cta.classList.contains('on')); })
        .observe(cta, { attributes: true, attributeFilter: ['class'] });
    }
  }
  function avatar() {
    var s = C.sal || {};
    return s.logo_url ? '<img src="' + esc(s.logo_url) + '" alt="">' : esc((s.name || '?').trim().charAt(0).toUpperCase());
  }
  function buildPanel() {
    panel = document.createElement('div');
    panel.className = 'sc' + (C.mode === 'chat' ? ' full' : '');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Чат с салоном');
    var ch = (C.sal && C.sal.chat) || {};
    panel.innerHTML =
      '<div class="sc-pane">' +
      '<div class="sc-h"><div class="sc-ava">' + avatar() + '<i></i></div>' +
      '<div class="sc-ttl"><b>' + esc(ch.title || 'Администратор') + '</b><span>' + esc((C.sal && C.sal.name) || '') + ' · онлайн</span></div>' +
      '<button class="sc-ico" type="button" data-id-open title="Оставить номер" aria-label="Оставить номер">' + ic('phone') + '</button>' +
      ((C.mode === 'chat' && !C.embed) ? '' : '<button class="sc-ico" type="button" data-x aria-label="Закрыть">' + ic('x') + '</button>') + '</div>' +
      '<div class="sc-list" id="sc-list" aria-live="polite"></div>' +
      '<div class="sc-id" id="sc-id" hidden></div>' +
      '<div class="sc-f"><div class="sc-quick" id="sc-quick"></div>' +
      '<form class="sc-in" id="sc-form"><textarea id="sc-text" rows="1" maxlength="2000" placeholder="Напишите вопрос…" aria-label="Сообщение"></textarea>' +
      '<button type="submit" class="sc-send" aria-label="Отправить">' + ic('send') + '</button></form>' +
      '<div class="sc-pw">Отвечает ИИ-администратор · <a href="https://voksyai.online" target="_blank" rel="noopener">Voksy AI</a></div></div>' +
      '</div>';
    var host = C.mode === 'chat' ? document.getElementById('app') : null;
    if (host) host.innerHTML = '';
    (host || document.body).appendChild(panel);
    listEl = panel.querySelector('#sc-list');
    inputEl = panel.querySelector('#sc-text');
    panel.addEventListener('click', onClick);
    panel.querySelector('#sc-form').addEventListener('submit', function (e) { e.preventDefault(); send(); });
    inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    });
    inputEl.addEventListener('input', grow);
    if (C.mode !== 'chat') panel.addEventListener('mousedown', function (e) { if (e.target === panel) closeChat(); });
  }
  function grow() { inputEl.style.height = 'auto'; inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px'; }

  function quickChips() {
    var q = panel.querySelector('#sc-quick');
    if (!q) return;
    var hasClient = C.items.some(function (m) { return m.who === 'client'; });
    if (hasClient) { q.innerHTML = ''; return; }
    var chips = ['Сколько стоит?', 'Есть время сегодня?', 'Где вы находитесь?'];
    q.innerHTML = chips.map(function (t) { return '<button type="button" data-q="' + esc(t) + '">' + esc(t) + '</button>'; }).join('') +
      (C.onBook ? '<button type="button" class="sc-bkq" data-book>' + ic('cal') + 'Записаться самому</button>' : '');
  }

  function bubble(m) {
    var mine = m.who === 'client';
    var att = '';
    if (m.attachment && m.attachment.url) {
      att = (m.attachment.kind === 'image')
        ? '<a class="sc-img" href="' + esc(m.attachment.url) + '" target="_blank" rel="noopener"><img src="' + esc(m.attachment.url) + '" alt="' + esc(m.attachment.name || '') + '" loading="lazy"></a>'
        : '<a class="sc-file" href="' + esc(m.attachment.url) + '" target="_blank" rel="noopener">' + ic('file') + '<span>' + esc(m.attachment.name || 'Файл') + '</span></a>';
    }
    return '<div class="sc-m ' + (mine ? 'me' : 'them') + (m.pending ? ' pending' : '') + '">' + att +
      (m.text ? '<div class="sc-b">' + linkify(m.text) + '</div>' : '') +
      '<span class="sc-t">' + esc(m.pending ? 'отправляем…' : hm(m.ts)) + '</span></div>';
  }
  function render(stick) {
    if (!listEl) return;
    var near = listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight < 80;
    var ch = (C.sal && C.sal.chat) || {};
    var html = '<div class="sc-m them greet"><div class="sc-b">' + linkify(ch.greeting || 'Здравствуйте! Чем помочь?') + '</div></div>';
    html += C.items.map(bubble).join('');
    if (C.typing) html += '<div class="sc-m them"><div class="sc-b sc-dots" aria-label="печатает"><i></i><i></i><i></i></div></div>';
    listEl.innerHTML = html;
    if (stick || near) listEl.scrollTop = listEl.scrollHeight;
    quickChips();
  }

  function merge(items) {
    var added = 0;
    (items || []).forEach(function (m) {
      if (C.seen[m.id]) return;
      C.seen[m.id] = 1;
      // своё сообщение, отправленное только что, уже на экране — заменяем «отправляем…»
      if (m.who === 'client') {
        for (var i = 0; i < C.items.length; i++) {
          if (C.items[i].pending && C.items[i].text === m.text) { C.items[i] = m; return; }
        }
      }
      C.items.push(m);
      if (m.who !== 'client') added++;
    });
    C.items.sort(function (a, b) { return (a.ts || '') < (b.ts || '') ? -1 : 1; });
    return added;
  }

  function setBadge() {
    if (!fab) return;
    var b = fab.querySelector('.sc-badge');
    b.hidden = !C.unread;
    b.textContent = C.unread > 9 ? '9+' : String(C.unread || '');
  }

  // ── сеть ──
  function start() {
    if (C.started) return Promise.resolve();
    C.token = ls('vxs_chat_' + C.slug);
    return req('POST', '/start', { origin: C.embed ? 'site' : 'salon' }).then(function (d) {
      C.started = true;
      if (d.token) { C.token = d.token; ls('vxs_chat_' + C.slug, d.token); }
      C.name = d.name; C.phone = d.phone;
      merge(d.items);
      C.cursor = d.cursor || C.cursor;
      C.typing = !!d.typing;
      render(true);
    }).catch(function (e) {
      if (e.status === 401 || e.status === 404) { ls('vxs_chat_' + C.slug, null); C.token = null; }
      throw e;
    });
  }
  function poll(silent) {
    if (!C.token) return Promise.resolve();
    return req('GET', '/messages' + (C.cursor ? '?after=' + encodeURIComponent(C.cursor) : '')).then(function (d) {
      var added = merge(d.items);
      if (d.cursor) C.cursor = d.cursor;
      var changed = added || C.typing !== !!d.typing;
      C.typing = !!d.typing;
      if (added) C.lastActive = Date.now();
      if (C.open) { if (changed) render(); }
      else if (added && !silent) { C.unread += added; setBadge(); }
    }).catch(function (e) {
      if (e.status === 401) { ls('vxs_chat_' + C.slug, null); C.token = null; C.started = false; }
    });
  }
  function schedule() {
    clearTimeout(C.pollT);
    if (!C.token || document.hidden) return;
    var fresh = Date.now() - C.lastActive < 90000;
    var ms = C.open ? (C.typing ? 1500 : fresh ? 2500 : 8000) : (fresh ? 6000 : 30000);
    C.pollT = setTimeout(function () { poll().then(schedule, schedule); }, ms);
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden && C.token) poll().then(schedule, schedule); });

  function send(textArg) {
    var text = (textArg != null ? textArg : inputEl.value).trim();
    if (!text || C.sending) return;
    if (text.length > 2000) { note('Сообщение длинное — разделите его на части'); return; }
    C.sending = true;
    var tmp = { id: 'tmp' + Date.now(), who: 'client', text: text, ts: new Date().toISOString(), pending: true };
    C.items.push(tmp);
    if (textArg == null) { inputEl.value = ''; grow(); }
    C.typing = true;
    render(true);
    (C.started ? Promise.resolve() : start()).then(function () {
      return req('POST', '/messages', { text: text });
    }).then(function (d) {
      C.lastActive = Date.now();
      if (d.message) { C.seen[d.message.id] = 1; for (var i = 0; i < C.items.length; i++) if (C.items[i] === tmp) C.items[i] = d.message; }
      render(true);
    }).catch(function (e) {
      C.items = C.items.filter(function (m) { return m !== tmp; });
      C.typing = false;
      if (textArg == null) { inputEl.value = text; grow(); }
      render(true);
      note(e.message);
    }).then(function () { C.sending = false; schedule(); });
  }

  function note(msg) {
    var n = document.createElement('div');
    n.className = 'sc-note';
    n.textContent = msg;
    panel.querySelector('.sc-f').prepend(n);
    setTimeout(function () { n.remove(); }, 4000);
  }

  // «Оставить номер»
  function idForm(show) {
    var box = panel.querySelector('#sc-id');
    if (!show) { box.hidden = true; box.innerHTML = ''; return; }
    var me = {};
    try { me = JSON.parse(ls('vxs_me') || '{}') || {}; } catch (e) { me = {}; }
    box.hidden = false;
    box.innerHTML = '<div class="sc-id-in"><b>Оставьте номер — перезвоним или напишем в WhatsApp</b>' +
      '<input id="sc-nm" autocomplete="name" placeholder="Ваше имя" maxlength="120" value="' + esc(C.name || me.name || '') + '">' +
      '<input id="sc-ph" type="tel" inputmode="tel" autocomplete="tel" placeholder="+996 700 123 456" maxlength="40" value="' + esc(C.phone || me.phone || '+996 ') + '">' +
      '<div class="sc-id-a"><button type="button" class="sc-btn ghost" data-id-x>Позже</button><button type="button" class="sc-btn" data-id-save>Отправить</button></div></div>';
    box.querySelector('#sc-ph').focus();
  }
  function saveId() {
    var nm = panel.querySelector('#sc-nm').value.trim(), ph = panel.querySelector('#sc-ph').value.trim();
    if (ph.replace(/\D/g, '').length < 9) { note('Проверьте номер телефона'); return; }
    (C.started ? Promise.resolve() : start()).then(function () {
      return req('POST', '/identity', { name: nm || null, phone: ph });
    }).then(function (d) {
      C.name = d.name; C.phone = d.phone;
      try { ls('vxs_me', JSON.stringify({ name: nm, phone: ph })); } catch (e) { /* ignore */ }
      idForm(false);
      note('Спасибо! Номер передали салону');
      var b = panel.querySelector('[data-id-open]');
      if (b) b.classList.add('ok');
    }).catch(function (e) { note(e.message); });
  }

  function onClick(e) {
    var t = e.target;
    if (t.closest('[data-x]')) return closeChat();
    var q = t.closest('[data-q]');
    if (q) return send(q.getAttribute('data-q'));
    if (t.closest('[data-book]')) {
      if (C.mode === 'chat') { if (C.onBook) C.onBook(); return; }
      closeChat();
      setTimeout(function () { if (C.onBook) C.onBook(); }, 60);
      return;
    }
    if (t.closest('[data-id-open]')) return idForm(panel.querySelector('#sc-id').hidden);
    if (t.closest('[data-id-x]')) return idForm(false);
    if (t.closest('[data-id-save]')) return saveId();
  }

  // ── открыть / закрыть ──
  var pushed = false;
  function openChat() {
    if (!panel) buildPanel();
    C.open = true;
    C.unread = 0; setBadge();
    panel.style.display = '';
    render(true);
    if (C.mode !== 'chat') {
      document.documentElement.classList.add('sc-lock');
      if (fab) fab.classList.add('hide');
      try { history.pushState({ l: 'chat' }, ''); pushed = true; } catch (e) { pushed = false; }
    }
    requestAnimationFrame(function () { panel.classList.add('on'); if (window.innerWidth > 720 || C.mode === 'chat') inputEl.focus(); });
    start().then(function () { C.lastActive = Date.now(); schedule(); }).catch(function (e) { note(e.message); });
  }
  function doClose() {
    if (!panel || !C.open) return;
    C.open = false;
    panel.classList.remove('on');
    document.documentElement.classList.remove('sc-lock');
    if (fab) fab.classList.remove('hide');
    setTimeout(function () { if (!C.open) panel.style.display = 'none'; }, 250);
    schedule();
  }
  function closeChat() {
    if (C.mode === 'chat') {
      if (C.embed) { try { window.parent.postMessage({ source: 'voksy-booking', type: 'close' }, '*'); } catch (e) { /* ignore */ } }
      return;
    }
    if (pushed) { pushed = false; try { history.back(); return; } catch (e) { /* ignore */ } }
    doClose();
  }
  window.addEventListener('popstate', function () { if (C.open && C.mode !== 'chat') { pushed = false; doClose(); } });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && C.open && C.mode !== 'chat') closeChat(); });

  window.SalonChat = {
    /** opts: {slug, salon, mode: 'page' | 'chat', embed, onBook} */
    init: function (opts) {
      C.slug = opts.slug; C.sal = opts.salon; C.mode = opts.mode || 'page'; C.embed = !!opts.embed; C.onBook = opts.onBook || null;
      C.base = '/api/public/chat/' + encodeURIComponent(C.slug);
      if (C.mode === 'chat') { openChat(); return; }
      buildFab();
      // уже переписывались с этого устройства — подтягиваем ответы, пока окно закрыто (значок)
      if (ls('vxs_chat_' + C.slug)) { C.token = ls('vxs_chat_' + C.slug); C.lastActive = 0; poll(true).then(schedule, schedule); }
      if (/(^|[?&])chat=1/.test(location.search) || location.hash === '#chat') openChat();
    },
    open: function () { openChat(); },
    close: function () { closeChat(); }
  };
})();
