/* ============================================================================
 * start/common.js — общий код страниц тарифа Старт (CRM, Аналитика, Чат-бот)
 *
 * Страницы Старта — отдельный набор в оформлении кабинета (/static/start-*.html).
 * Это НЕ страница агента обзвона (agent.html, её видит только Профи) и не использует
 * её скрипты: свой код, свои стили (start.css), общий только бэкенд бота (/api/agent/*).
 * Классический скрипт, всё лежит в window.StartApp.
 * ========================================================================== */
(function () {
  'use strict';

  var S = (window.StartApp = {});
  var API = '/api/agent';

  // ── Утилиты ─────────────────────────────────────────────────────────────
  S.esc = function (v) {
    if (v === null || v === undefined) return '';
    return String(v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  S.$ = function (sel, root) { return (root || document).querySelector(sel); };
  S.$$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  S.token = function () { try { return localStorage.getItem('auth_token'); } catch (e) { return null; } };

  function icon(name, cls) { return window.VF ? window.VF.icon(name, cls) : ''; }
  S.icon = icon;

  S.toast = function (msg, type) {
    if (window.VF && window.VF.toast) window.VF.toast(msg, { type: type || 'info' });
  };

  // Ошибки API приходят в detail (строка/объект) или message
  var ERRORS = {
    not_found: 'Не найдено',
    reactive_mode: 'На тарифе Старт агент только отвечает клиентам. Звонки и рассылки по расписанию доступны на Profi.',
    subscription_required: 'Нужна активная подписка',
    subscription_expired: 'Подписка истекла',
    insufficient_credits: 'Не хватает кредитов агента',
    agent_limit_reached: 'На тарифе Старт доступен один бот',
    invalid_token: 'Неверный токен. Скопируйте его целиком у @BotFather',
    token_already_used: 'Этот бот уже подключён к другому агенту. Создайте нового у @BotFather',
    no_chat_ids_configured: 'Сначала привяжите чат: нажмите «Подключить мой Telegram»',
    telegram_bot_not_configured: 'Сначала подключите Telegram-бота',
    already_exists: 'Этот чат уже добавлен',
    invalid_chat_type: 'Неверный тип чата',
    chat_id_required: 'Введите chat_id',
    not_configured: 'Не настроено на сервере. Напишите в поддержку',
    already_connected: 'Уже подключено',
    phone_invalid: 'Неверный номер телефона',
    auth_not_started: 'Сначала запросите код',
    password_not_expected: 'Пароль сейчас не нужен'
  };
  S.errText = function (v) {
    if (!v) return 'Что-то пошло не так';
    if (typeof v === 'string') return ERRORS[v] || v;
    if (v.message) return S.errText(v.message);
    if (v.detail) return S.errText(v.detail);
    try { return JSON.stringify(v); } catch (e) { return 'Ошибка'; }
  };

  // api('/contacts?limit=50') → { ok, status, data }. Токен и выход при 401 — как везде в кабинете.
  S.api = function (path, opts) {
    opts = opts || {};
    var headers = { 'Content-Type': 'application/json' };
    var t = S.token();
    if (t) headers.Authorization = 'Bearer ' + t;
    var url = path.indexOf('/api/') === 0 ? path : API + path;
    return fetch(url, { method: opts.method || 'GET', headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined })
      .then(function (r) {
        if (r.status === 401) {
          try { localStorage.removeItem('auth_token'); } catch (e) { /* ignore */ }
          location.href = '/static/login.html';
          return { ok: false, status: 401, data: {} };
        }
        return r.json().catch(function () { return {}; }).then(function (data) {
          return { ok: r.ok, status: r.status, data: data };
        });
      })
      .catch(function () { return { ok: false, status: 0, data: { detail: 'Нет связи с сервером' } }; });
  };
  // Загрузка файла (multipart): upload('/contacts/import/preview', file) → { ok, status, data }
  S.upload = function (path, file, extra) {
    var fd = new FormData();
    fd.append('file', file);
    if (extra && typeof extra === 'object') Object.keys(extra).forEach(function (k) { if (extra[k] != null) fd.append(k, extra[k]); });
    var headers = {};
    var t = S.token();
    if (t) headers.Authorization = 'Bearer ' + t;
    var url = path.indexOf('/api/') === 0 ? path : API + path;
    return fetch(url, { method: 'POST', headers: headers, body: fd })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
      })
      .catch(function () { return { ok: false, status: 0, data: { detail: 'Нет связи с сервером' } }; });
  };

  // Скачивание файла с токеном (ссылкой нельзя: нужен заголовок Authorization)
  S.download = function (path, filename) {
    var headers = {};
    var t = S.token();
    if (t) headers.Authorization = 'Bearer ' + t;
    var url = path.indexOf('/api/') === 0 ? path : API + path;
    return fetch(url, { headers: headers }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.blob();
    }).then(function (blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      document.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    });
  };

  S.apiError = function (res) { return S.errText(res && res.data && (res.data.detail || res.data.message)); };

  // ── Форматирование ─────────────────────────────────────────────────────
  var TZ = 'Asia/Bishkek';
  S.fmtDate = function (iso, withTime) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    var o = { timeZone: TZ, day: '2-digit', month: 'short' };
    if (withTime !== false) { o.hour = '2-digit'; o.minute = '2-digit'; }
    return new Intl.DateTimeFormat('ru-RU', o).format(d).replace('.', '');
  };
  S.dayKey = function (iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(d); // YYYY-MM-DD
  };
  S.fmtDay = function (key) {
    var d = new Date(key + 'T12:00:00+06:00');
    return new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, weekday: 'long', day: 'numeric', month: 'long' }).format(d);
  };
  S.fmtPhone = function (p) {
    if (!p) return '';
    var d = String(p).replace(/\D/g, '');
    if (d.length === 12 && d.indexOf('996') === 0) return '+996 ' + d.slice(3, 6) + ' ' + d.slice(6, 9) + ' ' + d.slice(9);
    return String(p).charAt(0) === '+' ? p : '+' + d;
  };
  S.fmtMoney = function (v, cur) {
    if (v === null || v === undefined || v === '') return '';
    var n = Number(v);
    if (isNaN(n)) return '';
    return n.toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' ' + (!cur || cur === 'KGS' ? 'сом' : cur);
  };
  S.fmtDur = function (sec) {
    sec = Math.round(Number(sec) || 0);
    if (!sec) return '—';
    var m = Math.floor(sec / 60), s = sec % 60;
    return m ? m + ' мин ' + (s ? s + ' с' : '') : s + ' с';
  };
  S.fmtSize = function (b) {
    b = Number(b) || 0;
    if (b < 1024) return b + ' Б';
    if (b < 1048576) return Math.round(b / 1024) + ' КБ';
    return (b / 1048576).toFixed(1) + ' МБ';
  };
  S.initial = function (name) { return String(name || '?').trim().charAt(0).toUpperCase() || '?'; };
  S.debounce = function (fn, ms) {
    var t; return function () { var a = arguments, c = this; clearTimeout(t); t = setTimeout(function () { fn.apply(c, a); }, ms); };
  };
  // Время (ISO, UTC) ↔ поле datetime-local (Бишкек, UTC+6 без перехода на летнее время)
  S.isoToLocalInput = function (iso) {
    if (!iso) return '';
    var d = new Date(new Date(iso).getTime() + 6 * 3600 * 1000);
    return d.toISOString().slice(0, 16);
  };
  S.localInputToIso = function (v) {
    if (!v) return null;
    return new Date(v + ':00+06:00').toISOString();
  };

  // Стадии воронки: из /pipeline/stages (кастомные или дефолтные)
  var DEFAULT_STAGES = [
    { key: 'new', label: 'Новый', color: '#6B7280', kind: 'open' },
    { key: 'active', label: 'В работе', color: '#3B82F6', kind: 'open' },
    { key: 'success', label: 'Успех', color: '#10B981', kind: 'won' },
    { key: 'rejected', label: 'Отказ', color: '#EF4444', kind: 'lost' },
    { key: 'do_not_call', label: 'Не звонить', color: '#1F2937', kind: 'lost' }
  ];
  S.stages = DEFAULT_STAGES;
  S.stageMap = {};
  function indexStages() { S.stageMap = {}; S.stages.forEach(function (s) { S.stageMap[s.key] = s; }); }
  indexStages();
  S.loadStages = function () {
    return S.api('/pipeline/stages').then(function (r) {
      if (r.ok && r.data.stages && r.data.stages.length) { S.stages = r.data.stages; indexStages(); }
      return S.stages;
    });
  };
  S.stageOf = function (key) { return S.stageMap[key] || { key: key, label: key || '—', color: '#6B7280', kind: 'open' }; };
  S.stageChip = function (key) {
    var st = S.stageOf(key);
    return '<span class="st-stage" style="--c:' + S.esc(st.color) + '"><i></i>' + S.esc(st.label) + '</span>';
  };

  var CHANNELS = {
    whatsapp: { label: 'WhatsApp', icon: 'message-circle', color: '#25D366' },
    telegram: { label: 'Telegram', icon: 'send', color: '#229ED9' },
    instagram: { label: 'Instagram', icon: 'instagram', color: '#E1306C' },
    call: { label: 'Звонок', icon: 'phone', color: '#2a5ce8' },
    sms: { label: 'SMS', icon: 'message-square', color: '#16A34A' }
  };
  S.CHANNELS = CHANNELS;
  S.channelDots = function (ch) {
    ch = ch || {};
    return Object.keys(CHANNELS).filter(function (k) { return ch[k]; }).map(function (k) {
      return '<span class="st-ch" title="' + CHANNELS[k].label + '" style="--c:' + CHANNELS[k].color + '">' + icon(CHANNELS[k].icon) + '</span>';
    }).join('');
  };

  // ── Диалоги и боковая панель ───────────────────────────────────────────
  // S.dialog({title, body, foot, size}) → { el, close }
  S.dialog = function (o) {
    var back = document.createElement('div');
    back.className = 'vf-backdrop';
    back.innerHTML =
      '<div class="vf-dialog ' + (o.size ? 'vf-dialog-' + o.size : '') + '" role="dialog" aria-modal="true">' +
      '<div class="vf-dialog-head"><h3>' + S.esc(o.title || '') + '</h3>' +
      '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-x aria-label="Закрыть">' + icon('x') + '</button></div>' +
      '<div class="vf-dialog-body">' + (o.body || '') + '</div>' +
      (o.foot ? '<div class="vf-dialog-foot">' + o.foot + '</div>' : '') + '</div>';
    document.body.appendChild(back);
    requestAnimationFrame(function () { back.classList.add('open'); });
    function close() {
      back.classList.remove('open');
      document.removeEventListener('keydown', onKey);
      setTimeout(function () { back.remove(); }, 180);
      if (o.onClose) o.onClose();
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    back.addEventListener('mousedown', function (e) { if (e.target === back) close(); });
    S.$('[data-x]', back).addEventListener('click', close);
    return { el: back, close: close };
  };

  // S.drawer({title, subtitle, body}) → { el, body, close } — панель справа (карточка клиента)
  var drawerEl = null;
  S.drawer = function (o) {
    if (drawerEl) { drawerEl.remove(); drawerEl = null; }
    var wrap = document.createElement('div');
    wrap.className = 'st-drawer-wrap';
    wrap.innerHTML =
      '<div class="st-drawer-back"></div>' +
      '<aside class="st-drawer" role="dialog" aria-modal="true">' +
      '<header class="st-drawer-head"><div class="st-drawer-title"></div>' +
      '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-x aria-label="Закрыть">' + icon('x') + '</button></header>' +
      '<div class="st-drawer-body"></div></aside>';
    document.body.appendChild(wrap);
    drawerEl = wrap;
    requestAnimationFrame(function () { wrap.classList.add('open'); });
    var api = {
      el: wrap,
      body: S.$('.st-drawer-body', wrap),
      setTitle: function (html) { S.$('.st-drawer-title', wrap).innerHTML = html; },
      close: function () {
        wrap.classList.remove('open');
        document.removeEventListener('keydown', onKey);
        setTimeout(function () { wrap.remove(); if (drawerEl === wrap) drawerEl = null; }, 220);
        if (o.onClose) o.onClose();
      }
    };
    function onKey(e) { if (e.key === 'Escape' && !S.$('.vf-backdrop')) api.close(); }
    document.addEventListener('keydown', onKey);
    S.$('.st-drawer-back', wrap).addEventListener('click', api.close);
    S.$('[data-x]', wrap).addEventListener('click', api.close);
    api.setTitle(o.title || '');
    if (o.body) api.body.innerHTML = o.body;
    return api;
  };

  S.confirm = function (o) {
    if (window.VF && window.VF.confirm) return window.VF.confirm(o);
    return Promise.resolve(window.confirm(o.message || o.title));
  };

  // ── Допуск на страницу ─────────────────────────────────────────────────
  // Страницы Старта показываем только тарифу Старт. Профи — на страницу агента, остальные — к тарифам.
  // ?preview=1 — для проверки админом.
  S.guard = function () {
    if (/[?&]preview=1/.test(location.search)) return Promise.resolve(true);
    return S.api('/api/users/me').then(function (r) {
      if (!r.ok) return true;
      var mode = r.data.agent_mode || '';
      try { localStorage.setItem('vf_agent_mode', mode); } catch (e) { /* ignore */ }
      if (mode === 'reactive') return true;
      location.replace(mode === 'full' ? '/static/agent.html' : '/static/dashboard.html#plans');
      return false;
    });
  };

  // ── Бот (агент) ─────────────────────────────────────────────────────────
  // На Старте бот один. null — ещё не создан.
  S.agent = null;
  S.loadAgent = function () {
    return S.api('/list').then(function (r) {
      var list = (r.ok && r.data.agents) || [];
      S.agent = list.length ? list[0] : null;
      return S.agent;
    });
  };

  // Пустое состояние «бот не создан»
  S.noBotHtml = function () {
    return '<div class="st-empty-big">' +
      '<div class="st-empty-ic">' + icon('bot') + '</div>' +
      '<h2>Сначала создайте чат-бота</h2>' +
      '<p>Бот отвечает клиентам в WhatsApp, Instagram и Telegram и на входящие звонки, а клиентов записывает в CRM. ' +
      'Создание занимает пару минут.</p>' +
      '<a class="btn btn-primary" href="/static/start-bot.html">' + icon('plus') + '<span>Создать бота</span></a></div>';
  };

  // Первая отрисовка страницы: снять загрузчик дизайн-системы
  S.ready = function () { if (window.VF && window.VF.ready) window.VF.ready(); };

  // Мобильное меню (общий шаблон кабинета)
  S.initMobileMenu = function () {
    var sb = document.getElementById('sidebar');
    var open = document.getElementById('sidebar-toggle');
    var close = document.getElementById('sidebar-close');
    var scrim = document.getElementById('st-scrim');
    function set(v) {
      if (!sb) return;
      sb.classList.toggle('open', v);
      if (scrim) scrim.classList.toggle('show', v);
    }
    if (open) open.addEventListener('click', function () { set(!sb.classList.contains('open')); });
    if (close) close.addEventListener('click', function () { set(false); });
    if (scrim) scrim.addEventListener('click', function () { set(false); });
  };

  // Лёгкий «каркас» вкладок: делает .tab активной и показывает соответствующий .tab-pane
  S.initTabs = function (root, onChange) {
    var tabs = S.$$('.tab[data-tab]', root);
    function show(id, silent) {
      tabs.forEach(function (t) { t.classList.toggle('active', t.dataset.tab === id); });
      S.$$('.tab-pane[data-pane]', root.parentNode || document).forEach(function (p) {
        p.classList.toggle('active', p.dataset.pane === id);
      });
      try { history.replaceState(null, '', '#' + id); } catch (e) { /* ignore */ }
      if (!silent && onChange) onChange(id);
    }
    tabs.forEach(function (t) { t.addEventListener('click', function () { show(t.dataset.tab); }); });
    var start = (location.hash || '').replace('#', '');
    if (!tabs.some(function (t) { return t.dataset.tab === start; })) start = tabs.length ? tabs[0].dataset.tab : '';
    if (start) show(start);
    return show;
  };
})();
