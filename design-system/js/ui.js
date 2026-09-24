/**
 * VoksiAI UI — поведение компонентов дизайн-системы (v1), без зависимостей.
 *
 *   VF.icon(name, cls)              — SVG-иконка Lucide из спрайта /static/icons/ui.svg
 *   VF.logo(code, opts)             — логотип голосовой модели (/static/icons/models/*.svg)
 *   VF.loader.show()/hide()         — загрузчик страницы (волна VoksiAI); показывается сам
 *                                     при подключении скрипта, прячется по VF.ready()
 *   VF.progress.start()/done()      — тонкая полоса прогресса сверху
 *   VF.toast(msg, {type, action, actionLabel, duration})
 *   VF.confirm({title, message, confirmText, cancelText, danger}) → Promise<boolean>
 *   VF.alert({title, message, okText}) → Promise<void>
 *   VF.modal(el) → {open(), close()} — модалка с фокус-ловушкой и Esc
 *   VF.select(el, {options|groups, value, placeholder, search, onChange}) → api
 *   VF.menu(trigger, items)         — контекстное меню
 *   VF.skeleton(container, n)       — временные скелетоны
 *
 * Подключать после voksiai.css. Скрипт сразу вставляет загрузчик, поэтому его
 * место — в <head> или в самом начале <body>.
 */
(function (global) {
  'use strict';

  var SPRITE = '/static/icons/ui.svg';

  // ------------------------------------------------------------------
  // Общий кэш коротких GET-запросов профиля и кошелька.
  // Сайдбар и каждая страница при загрузке запрашивают одно и то же
  // (/users/me, /wallet/balance, ...), settings — трижды. Одинаковые GET
  // в течение TTL получают клон одного ответа; параллельные — один запрос.
  // Любой не-GET запрос к /api/ сбрасывает кэш, чтобы после оплаты или
  // правки профиля страница видела свежие данные. Логику страниц не меняет.
  // ------------------------------------------------------------------
  (function () {
    if (typeof global.fetch !== 'function') return;
    var SHARED = ['/api/users/me', '/api/wallet/balance', '/api/wallet/tariffs/me',
                  '/api/subscriptions/my-subscription', '/api/subscriptions/assistants-usage', '/api/users/api-key'];
    var TTL = 5000, cache = {};
    var origFetch = global.fetch;
    function keyOf(input) {
      var u = typeof input === 'string' ? input : (input && input.url) || '';
      try { var url = new URL(u, global.location.href); return url.pathname + url.search; } catch (e) { return u; }
    }
    global.fetch = function (input, init) {
      var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      var k = keyOf(input);
      if (method !== 'GET' && method !== 'HEAD') {
        if (k.indexOf('/api/') === 0) cache = {};
        return origFetch.apply(this, arguments);
      }
      if (SHARED.indexOf(k) === -1) return origFetch.apply(this, arguments);
      var hit = cache[k];
      if (hit && Date.now() - hit.t < TTL) return hit.p.then(function (r) { return r.clone(); });
      var p = origFetch.apply(this, arguments).then(function (r) {
        if (!r.ok) delete cache[k];  // ошибки не кэшируем
        return r;
      }, function (e) { delete cache[k]; throw e; });
      cache[k] = { t: Date.now(), p: p };
      return p.then(function (r) { return r.clone(); });
    };
    global.VF_INVALIDATE_SHARED = function () { cache = {}; };
  })();
  var MODEL_LOGOS = {
    openai: { file: 'openai.svg', name: 'OpenAI' },
    gemini: { file: 'gemini-color.svg', mono: 'gemini.svg', name: 'Gemini' },
    fish: { file: 'fishaudio-color.svg', mono: 'fishaudio.svg', name: 'Fish Audio' },
    yandex: { file: 'yandex-color.svg', mono: 'yandex.svg', name: 'Яндекс' },
    cascade: { file: 'cascade-color.svg', mono: 'cascade.svg', name: 'Каскад' },
  };

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function icon(name, cls) {
    return '<svg class="ic' + (cls ? ' ' + cls : '') + '" aria-hidden="true"><use href="' + SPRITE + '#i-' + name + '"></use></svg>';
  }

  function logo(code, opts) {
    opts = opts || {};
    var m = MODEL_LOGOS[code];
    if (!m) return '<span class="logo-wrap">' + icon('bot') + '</span>';
    var file = (opts.mono && m.mono) ? m.mono : m.file;
    var color = /-color\.svg$/.test(file);
    var img = '<img class="logo' + (color ? ' logo-color' : '') + '" src="/static/icons/models/' + file + '" alt="' + esc(m.name) + '"' + (opts.size ? ' style="width:' + opts.size + 'px;height:' + opts.size + 'px"' : '') + '>';
    return opts.bare ? img : '<span class="logo-wrap"' + (opts.size ? ' style="width:' + (opts.size + 14) + 'px;height:' + (opts.size + 14) + 'px"' : '') + '>' + img + '</span>';
  }

  // ------------------------------------------------------------------
  // Загрузчик страницы
  // ------------------------------------------------------------------
  var loader = (function () {
    var el = null, shownAt = 0, MIN_MS = 250, hidden = false;
    function ensure() {
      if (el || hidden) return el;
      el = document.createElement('div');
      el.className = 'vf-loader';
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      el.innerHTML =
        '<div class="brand"><span class="vf-wave"><i></i><i></i><i></i><i></i><i></i></span> VoksiAI</div>' +
        '<div class="sub">Загружаем кабинет…</div>';
      (document.body || document.documentElement).appendChild(el);
      shownAt = Date.now();
      return el;
    }
    function show(text) {
      hidden = false;
      ensure();
      if (text) el.querySelector('.sub').textContent = text;
      el.classList.remove('hide');
      shownAt = Date.now();
    }
    function hide() {
      hidden = true;
      if (!el) return;
      var wait = Math.max(0, MIN_MS - (Date.now() - shownAt));
      setTimeout(function () {
        if (!el) return;
        el.classList.add('hide');
        setTimeout(function () { if (el) { el.remove(); el = null; } }, 320);
      }, wait);
    }
    return { show: show, hide: hide, ensure: ensure };
  })();

  // Полоса прогресса
  var progress = (function () {
    var bar = null, timer = null, pct = 0;
    function ensure() {
      if (bar) return bar;
      bar = document.createElement('div'); bar.className = 'vf-progress';
      document.body.appendChild(bar); return bar;
    }
    function start() {
      ensure(); bar.classList.remove('done'); pct = 12; bar.style.width = pct + '%';
      clearInterval(timer);
      timer = setInterval(function () { pct = Math.min(90, pct + (90 - pct) * 0.12); bar.style.width = pct + '%'; }, 250);
    }
    function done() {
      if (!bar) return; clearInterval(timer); bar.style.width = '100%';
      setTimeout(function () { bar.classList.add('done'); }, 120);
      setTimeout(function () { if (bar) { bar.style.width = '0'; bar.classList.remove('done'); } }, 600);
    }
    return { start: start, done: done };
  })();

  // ------------------------------------------------------------------
  // Тосты
  // ------------------------------------------------------------------
  function toastRoot() {
    var r = document.querySelector('.vf-toasts');
    if (!r) { r = document.createElement('div'); r.className = 'vf-toasts'; document.body.appendChild(r); }
    return r;
  }
  function toast(msg, opts) {
    opts = opts || {};
    var type = opts.type || 'info';
    var icons = { success: 'circle-check', error: 'circle-x', warning: 'triangle-alert', info: 'info' };
    var t = document.createElement('div');
    t.className = 'vf-toast ' + type;
    t.innerHTML = icon(icons[type] || 'info') + '<span class="msg">' + esc(msg) + '</span>' +
      (opts.action ? '<button type="button" class="act">' + esc(opts.actionLabel || 'Отменить') + '</button>' : '') +
      '<button type="button" class="close" aria-label="Закрыть">' + icon('x', 'ic-sm') + '</button>';
    toastRoot().appendChild(t);
    requestAnimationFrame(function () { t.classList.add('show'); });
    var closed = false;
    function close() { if (closed) return; closed = true; t.classList.remove('show'); setTimeout(function () { t.remove(); }, 260); }
    t.querySelector('.close').addEventListener('click', close);
    if (opts.action) t.querySelector('.act').addEventListener('click', function () { close(); try { opts.action(); } catch (e) { console.error(e); } });
    var dur = opts.duration != null ? opts.duration : (type === 'error' ? 6000 : 3800);
    if (dur > 0) setTimeout(close, dur);
    return { close: close };
  }

  // ------------------------------------------------------------------
  // Модалки и диалог подтверждения
  // ------------------------------------------------------------------
  var FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  var openStack = [];

  function trapFocus(dialog, e) {
    var f = Array.prototype.filter.call(dialog.querySelectorAll(FOCUSABLE), function (x) { return x.offsetParent !== null; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  document.addEventListener('keydown', function (e) {
    if (!openStack.length) return;
    var top = openStack[openStack.length - 1];
    if (e.key === 'Escape') { e.preventDefault(); top.close('esc'); }
    else if (e.key === 'Tab') trapFocus(top.dialog, e);
  });

  /** Модалка из существующего элемента .vf-backdrop (внутри .vf-dialog) */
  function modal(el, opts) {
    opts = opts || {};
    var backdrop = typeof el === 'string' ? document.getElementById(el) : el;
    var dialog = backdrop.querySelector('.vf-dialog');
    var lastFocus = null;
    var api = {
      dialog: dialog,
      isOpen: false,
      open: function () {
        if (api.isOpen) return;
        lastFocus = document.activeElement;
        backdrop.style.display = 'flex';
        requestAnimationFrame(function () { backdrop.classList.add('open'); });
        api.isOpen = true; openStack.push(api);
        setTimeout(function () {
          var f = dialog.querySelector('[autofocus]') || dialog.querySelector(FOCUSABLE);
          if (f) f.focus();
        }, 30);
        if (opts.onOpen) opts.onOpen();
      },
      close: function (reason) {
        if (!api.isOpen) return;
        backdrop.classList.remove('open');
        api.isOpen = false;
        openStack = openStack.filter(function (x) { return x !== api; });
        setTimeout(function () { backdrop.style.display = 'none'; }, 240);
        if (lastFocus && lastFocus.focus) lastFocus.focus();
        if (opts.onClose) opts.onClose(reason);
      },
    };
    backdrop.style.display = 'none';
    backdrop.addEventListener('mousedown', function (e) { if (e.target === backdrop && opts.closeOnBackdrop !== false) api.close('backdrop'); });
    backdrop.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', function () { api.close('button'); }); });
    return api;
  }

  /** Диалог подтверждения. Возвращает Promise<boolean>. */
  function confirm(opts) {
    opts = typeof opts === 'string' ? { message: opts } : (opts || {});
    return new Promise(function (resolve) {
      var backdrop = document.createElement('div');
      backdrop.className = 'vf-backdrop';
      var iconName = opts.icon || (opts.danger ? 'trash-2' : 'circle-alert');
      backdrop.innerHTML =
        '<div class="vf-dialog vf-dialog-sm" role="dialog" aria-modal="true">' +
        '<div class="vf-dialog-head"><div class="row" style="gap:14px;align-items:flex-start">' +
        '<span class="vf-dialog-icon' + (opts.danger ? ' danger' : (opts.warning ? ' warning' : '')) + '">' + icon(iconName) + '</span>' +
        '<div><h3>' + esc(opts.title || 'Подтвердите действие') + '</h3></div></div></div>' +
        '<div class="vf-dialog-body" style="padding-left:78px">' + (opts.html || esc(opts.message || '')) + '</div>' +
        '<div class="vf-dialog-foot">' +
        '<button type="button" class="btn" data-act="cancel">' + esc(opts.cancelText || 'Отмена') + '</button>' +
        '<button type="button" class="btn ' + (opts.danger ? 'btn-danger btn-solid' : 'btn-primary') + '" data-act="ok" autofocus>' + esc(opts.confirmText || 'Подтвердить') + '</button>' +
        '</div></div>';
      document.body.appendChild(backdrop);
      var m = modal(backdrop, {
        onClose: function (reason) { setTimeout(function () { backdrop.remove(); }, 260); resolve(reason === 'ok'); },
      });
      backdrop.querySelector('[data-act="cancel"]').addEventListener('click', function () { m.close('cancel'); });
      backdrop.querySelector('[data-act="ok"]').addEventListener('click', function () { m.close('ok'); });
      m.open();
    });
  }

  /** VF.alert({title, message|html, okText, icon}) → Promise<void> — замена window.alert */
  function alertDialog(opts) {
    opts = typeof opts === 'string' ? { message: opts } : (opts || {});
    return new Promise(function (resolve) {
      var backdrop = document.createElement('div');
      backdrop.className = 'vf-backdrop';
      backdrop.innerHTML =
        '<div class="vf-dialog vf-dialog-sm" role="dialog" aria-modal="true">' +
        '<div class="vf-dialog-head"><div class="row" style="gap:14px;align-items:flex-start">' +
        '<span class="vf-dialog-icon' + (opts.danger ? ' danger' : '') + '">' + icon(opts.icon || (opts.danger ? 'circle-x' : 'info')) + '</span>' +
        '<div><h3>' + esc(opts.title || 'Сообщение') + '</h3></div></div></div>' +
        '<div class="vf-dialog-body" style="padding-left:78px;white-space:pre-wrap;word-break:break-word">' + (opts.html || esc(opts.message || '')) + '</div>' +
        '<div class="vf-dialog-foot"><button type="button" class="btn btn-primary" data-act="ok" autofocus>' + esc(opts.okText || 'Понятно') + '</button></div></div>';
      document.body.appendChild(backdrop);
      var m = modal(backdrop, { onClose: function () { setTimeout(function () { backdrop.remove(); }, 260); resolve(); } });
      backdrop.querySelector('[data-act="ok"]').addEventListener('click', function () { m.close('ok'); });
      m.open();
    });
  }

  // ------------------------------------------------------------------
  // Кастомный селект и меню
  // ------------------------------------------------------------------
  var openMenu = null;
  document.addEventListener('mousedown', function (e) {
    if (openMenu && !openMenu.root.contains(e.target) && !(openMenu.menu && openMenu.menu.contains(e.target))) openMenu.close();
  });
  window.addEventListener('resize', function () { if (openMenu) openMenu.close(); });
  // Список рисуется порталом в body (position: fixed), чтобы его не обрезали
  // модалки и контейнеры с overflow. При прокрутке любого контейнера — закрываем.
  document.addEventListener('scroll', function (e) {
    if (!openMenu || !openMenu.menu || openMenu.menu.contains(e.target)) return;
    // Прокрутка, «догнавшая» клик (браузер подводит кнопку в видимую область), не закрывает список
    if (Date.now() - (openMenu.openedAt || 0) < 350) { openMenu.reposition && openMenu.reposition(); return; }
    openMenu.close();
  }, true);

  /**
   * VF.select(container, opts)
   *   opts.options: [{value, label, sub, icon(html), disabled}]  или
   *   opts.groups:  [{label, options:[...]}]
   *   opts.value, opts.placeholder, opts.search (bool), opts.onChange(value, option)
   *   opts.renderValue(option) → html (необязательно)
   */
  function select(container, opts) {
    opts = opts || {};
    var root = typeof container === 'string' ? document.getElementById(container) : container;
    root.classList.add('vf-select');
    root.innerHTML = '';
    var btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'vf-select-btn'; btn.setAttribute('aria-haspopup', 'listbox'); btn.setAttribute('aria-expanded', 'false');
    var menu = document.createElement('div'); menu.className = 'vf-menu'; menu.setAttribute('role', 'listbox');
    root.appendChild(btn); root.appendChild(menu);

    var state = { value: opts.value != null ? String(opts.value) : null, groups: [], flat: [], hl: -1, q: '' };

    function setData(o) {
      state.groups = o.groups ? o.groups : [{ label: null, options: o.options || [] }];
      state.flat = [];
      state.groups.forEach(function (g) { (g.options || []).forEach(function (x) { state.flat.push(x); }); });
    }
    setData(opts);

    function current() {
      for (var i = 0; i < state.flat.length; i++) if (String(state.flat[i].value) === state.value) return state.flat[i];
      return null;
    }
    function renderBtn() {
      var c = current();
      var html = c ? (opts.renderValue ? opts.renderValue(c) : ((c.icon || '') + '<span class="truncate">' + esc(c.label) + '</span>' + (c.sub ? '<span class="faint small" style="margin-left:auto;padding-right:6px">' + esc(c.sub) + '</span>' : '')))
                   : '<span class="placeholder">' + esc(opts.placeholder || 'Выберите…') + '</span>';
      btn.innerHTML = html + '<span class="caret">' + icon('chevron-down', 'ic-sm') + '</span>';
    }
    function renderMenu() {
      var q = state.q.trim().toLowerCase();
      var html = '';
      if (opts.search) html += '<div class="vf-menu-search" style="position:sticky">' + icon('search') + '<input type="text" placeholder="Поиск…" value="' + esc(state.q) + '"></div>';
      var idx = 0, any = false;
      state.groups.forEach(function (g) {
        var items = (g.options || []).filter(function (x) { return !q || (String(x.label) + ' ' + (x.sub || '')).toLowerCase().indexOf(q) !== -1; });
        if (!items.length) return;
        any = true;
        if (g.label) html += '<div class="vf-menu-group">' + esc(g.label) + '</div>';
        items.forEach(function (x) {
          var sel = String(x.value) === state.value;
          html += '<div class="vf-menu-item' + (sel ? ' selected' : '') + (x.disabled ? ' disabled' : '') + (idx === state.hl ? ' hl' : '') + '" role="option" data-i="' + state.flat.indexOf(x) + '" aria-selected="' + sel + '">' +
            (x.icon || '') + '<span class="truncate">' + esc(x.label) + '</span>' + (x.sub ? '<span class="sub">' + esc(x.sub) + '</span>' : '') + (sel ? icon('check', 'ic-sm') : '') + '</div>';
          idx++;
        });
      });
      if (!any) html += '<div class="vf-menu-empty">' + esc(opts.emptyText || 'Ничего не найдено') + '</div>';
      menu.innerHTML = html;
      var inp = menu.querySelector('input');
      if (inp) {
        inp.addEventListener('input', function () { state.q = inp.value; state.hl = 0; renderMenu(); menu.querySelector('input').focus(); var i2 = menu.querySelector('input'); i2.setSelectionRange(i2.value.length, i2.value.length); });
        inp.addEventListener('keydown', onKey);
      }
      menu.querySelectorAll('.vf-menu-item').forEach(function (it) {
        it.addEventListener('click', function () { choose(parseInt(it.getAttribute('data-i'), 10)); });
      });
    }
    // Прокручиваем только сам список: scrollIntoView мог бы прокрутить страницу,
    // а прокрутка страницы закрывает открытый список.
    function scrollItemIntoMenu(item) {
      var top = item.offsetTop, bottom = top + item.offsetHeight;
      if (top < menu.scrollTop) menu.scrollTop = top - 6;
      else if (bottom > menu.scrollTop + menu.clientHeight) menu.scrollTop = bottom - menu.clientHeight + 6;
    }
    function positionMenu() {
      var r = btn.getBoundingClientRect();
      var spaceBelow = window.innerHeight - r.bottom;
      var below = !(spaceBelow < 200 && r.top > spaceBelow);
      var maxH = below ? Math.max(160, Math.min(320, spaceBelow - 16)) : Math.min(320, r.top - 16);
      menu.style.maxHeight = maxH + 'px';
      var w = Math.max(r.width, Math.min(260, window.innerWidth - r.left - 16));
      menu.style.left = r.left + 'px';
      menu.style.width = w + 'px';
      menu.style.minWidth = '0';
      menu.style.marginTop = '0';
      if (below) { menu.style.top = (r.bottom + 6) + 'px'; menu.style.bottom = 'auto'; menu.style.transformOrigin = 'top'; }
      else { menu.style.top = 'auto'; menu.style.bottom = (window.innerHeight - r.top + 6) + 'px'; menu.style.transformOrigin = 'bottom'; }
    }
    function open() {
      if (openMenu && openMenu !== api) openMenu.close();
      state.q = ''; state.hl = Math.max(0, state.flat.indexOf(current()));
      renderMenu();
      // Портал: список живёт в body поверх любых модалок и overflow-контейнеров
      menu.classList.add('fixed'); menu.style.zIndex = '1300';
      document.body.appendChild(menu);
      positionMenu();
      menu.classList.add('open'); btn.setAttribute('aria-expanded', 'true'); openMenu = api; api.openedAt = Date.now();
      var inp = menu.querySelector('input'); if (inp) setTimeout(function () { inp.focus({ preventScroll: true }); }, 10);
      var sel = menu.querySelector('.vf-menu-item.selected'); if (sel) scrollItemIntoMenu(sel);
    }
    function close() {
      menu.classList.remove('open'); btn.setAttribute('aria-expanded', 'false');
      if (menu.parentNode === document.body) root.appendChild(menu);
      if (openMenu === api) openMenu = null;
    }
    function choose(i) {
      var o = state.flat[i]; if (!o || o.disabled) return;
      var changed = state.value !== String(o.value);
      state.value = String(o.value); renderBtn(); close(); btn.focus();
      if (changed && opts.onChange) opts.onChange(o.value, o);
    }
    function onKey(e) {
      var items = Array.prototype.slice.call(menu.querySelectorAll('.vf-menu-item:not(.disabled)'));
      if (!menu.classList.contains('open')) { if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } return; }
      var cur = items.findIndex(function (x) { return x.classList.contains('hl'); });
      if (e.key === 'ArrowDown') { e.preventDefault(); cur = Math.min(items.length - 1, cur + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); cur = Math.max(0, cur - 1); }
      else if (e.key === 'Enter') { e.preventDefault(); if (items[cur]) choose(parseInt(items[cur].getAttribute('data-i'), 10)); return; }
      else if (e.key === 'Escape') { e.preventDefault(); close(); btn.focus(); return; }
      else return;
      items.forEach(function (x, i) { x.classList.toggle('hl', i === cur); });
      if (items[cur]) scrollItemIntoMenu(items[cur]);
    }
    btn.addEventListener('click', function () { menu.classList.contains('open') ? close() : open(); });
    btn.addEventListener('keydown', onKey);
    renderBtn();

    var api = {
      root: root, menu: menu, open: open, close: close, reposition: positionMenu, openedAt: 0,
      get value() { return state.value; },
      set: function (v, silent) { state.value = v == null ? null : String(v); renderBtn(); if (!silent && opts.onChange) opts.onChange(v, current()); },
      setOptions: function (o) { setData(o); if (!current()) state.value = null; renderBtn(); },
      setPlaceholder: function (p) { opts.placeholder = p; renderBtn(); },
      current: current,
    };
    return api;
  }

  /** Контекстное меню у триггера: items [{label, icon, danger, onClick, sep}] */
  function menu(trigger, items) {
    var m = document.createElement('div'); m.className = 'vf-menu fixed';
    m.innerHTML = items.map(function (it, i) {
      if (it.sep) return '<div class="vf-menu-sep"></div>';
      return '<div class="vf-menu-item' + (it.danger ? ' danger' : '') + '" data-i="' + i + '">' + (it.icon ? icon(it.icon, 'ic-sm') : '') + esc(it.label) + '</div>';
    }).join('');
    document.body.appendChild(m);
    var api = { root: m, close: function () { m.classList.remove('open'); setTimeout(function () { m.remove(); }, 160); if (openMenu === api) openMenu = null; } };
    m.querySelectorAll('.vf-menu-item').forEach(function (el) {
      el.addEventListener('click', function () { var it = items[parseInt(el.getAttribute('data-i'), 10)]; api.close(); if (it.onClick) it.onClick(); });
    });
    var r = trigger.getBoundingClientRect();
    m.style.top = (r.bottom + 4) + 'px';
    m.style.left = Math.min(r.left, window.innerWidth - 240) + 'px';
    m.style.minWidth = '200px';
    if (openMenu) openMenu.close();
    openMenu = api;
    requestAnimationFrame(function () { m.classList.add('open'); });
    return api;
  }

  // ------------------------------------------------------------------
  // Скелетоны и мобильное меню
  // ------------------------------------------------------------------
  function skeleton(container, n, cls) {
    var root = typeof container === 'string' ? document.getElementById(container) : container;
    var html = '';
    for (var i = 0; i < (n || 3); i++) html += '<div class="skeleton ' + (cls || 'skeleton-card') + '"></div>';
    root.innerHTML = html;
  }

  function initShell() {
    var sb = document.querySelector('.vf-sidebar');
    if (!sb) {
      var tb0 = document.querySelector('.top-nav');
      if (tb0) { var f0 = function () { tb0.classList.toggle('scrolled', (window.scrollY || document.documentElement.scrollTop) > 4); }; window.addEventListener('scroll', f0, { passive: true }); f0(); }
      return;
    }
    var t = document.getElementById('sidebar-toggle'), c = document.getElementById('sidebar-close');
    var scrim = document.createElement('div'); scrim.className = 'vf-scrim'; document.body.appendChild(scrim);
    function openSb() { sb.classList.add('open'); scrim.classList.add('show'); }
    function closeSb() { sb.classList.remove('open'); scrim.classList.remove('show'); }
    if (t) t.addEventListener('click', function (e) { e.stopPropagation(); openSb(); });
    if (c) c.addEventListener('click', closeSb);
    scrim.addEventListener('click', closeSb);
    sb.addEventListener('click', function (e) { if (e.target.closest('a')) closeSb(); });
    var tb = document.querySelector('.vf-topbar') || document.querySelector('.top-nav');
    if (tb) {
      var onScroll = function () { tb.classList.toggle('scrolled', (window.scrollY || document.documentElement.scrollTop) > 4); };
      window.addEventListener('scroll', onScroll, { passive: true }); onScroll();
    }
  }

  function ready() { loader.hide(); }

  // ------------------------------------------------------------------
  // Нативный <select> → VF.select без правки логики страниц.
  // Нативный элемент остаётся в DOM (скрыт), страница читает/пишет его
  // .value, меняет innerHTML и disabled, слушает change — всё это
  // синхронизируется с красивым списком в обе стороны.
  // ------------------------------------------------------------------
  var nativeValueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  var nativeIndexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex');
  function enhanceSelect(sel) {
    if (!sel || sel.tagName !== 'SELECT' || sel.multiple || sel.hasAttribute('data-vf-native') || sel.hasAttribute('data-vf-skip')) return null;
    sel.setAttribute('data-vf-native', '1');
    var wrap = document.createElement('div');
    wrap.className = 'vf-select vf-select-native';
    if (sel.id) wrap.setAttribute('data-for', sel.id);
    sel.classList.add('vf-native-hidden');
    sel.parentNode.insertBefore(wrap, sel.nextSibling);

    var syncing = false;
    function readOptions() {
      var groups = [], loose = [];
      Array.prototype.forEach.call(sel.children, function (ch) {
        if (ch.tagName === 'OPTGROUP') {
          groups.push({ label: ch.getAttribute('label') || '', options: Array.prototype.map.call(ch.querySelectorAll('option'), optToItem) });
        } else if (ch.tagName === 'OPTION') loose.push(optToItem(ch));
      });
      if (loose.length) groups.unshift({ label: null, options: loose });
      return groups;
    }
    function optToItem(o) {
      return { value: o.value, label: (o.textContent || '').trim(), disabled: o.disabled, _placeholder: o.value === '' };
    }
    var api = select(wrap, {
      groups: readOptions(),
      value: sel.value,
      placeholder: placeholderOf(),
      search: sel.options.length > 12,
      onChange: function (v) {
        syncing = true;
        try { nativeValueDesc.set.call(sel, v); } finally { syncing = false; }
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        sel.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    function placeholderOf() {
      var first = sel.options[0];
      return (first && first.value === '') ? (first.textContent || '').trim() : 'Выберите…';
    }
    function refresh() {
      var groups = readOptions();
      // Пустой option — это placeholder кнопки. В списке оставляем его только
      // когда это осмысленный выбор («Все регионы», «Не выбрано»), а не
      // служебный текст вроде «Выберите…» / «Загрузка…».
      var total = groups.reduce(function (n, g) { return n + g.options.length; }, 0);
      groups.forEach(function (g) {
        g.options = g.options.filter(function (o) {
          if (!o._placeholder) return true;
          return total > 1 && !/^(выберите|загрузка|ошибка|select|loading|—|-|\.\.\.)/i.test(o.label);
        });
      });
      groups = groups.filter(function (g) { return g.options.length; });
      api.setOptions({ groups: groups });
      var emptyListed = groups.some(function (g) { return g.options.some(function (o) { return o.value === ''; }); });
      api.set(sel.value === '' && !emptyListed ? null : sel.value, true);
      wrap.querySelector('.vf-select-btn').disabled = sel.disabled;
      wrap.classList.toggle('disabled', sel.disabled);
      api.setPlaceholder(placeholderOf());
    }
    // Перехват программной записи value/selectedIndex
    Object.defineProperty(sel, 'value', {
      configurable: true,
      get: function () { return nativeValueDesc.get.call(this); },
      set: function (v) { nativeValueDesc.set.call(this, v); if (!syncing) refresh(); }
    });
    Object.defineProperty(sel, 'selectedIndex', {
      configurable: true,
      get: function () { return nativeIndexDesc.get.call(this); },
      set: function (v) { nativeIndexDesc.set.call(this, v); refresh(); }
    });
    var mo = new MutationObserver(function () { refresh(); });
    mo.observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'selected', 'label'] });
    // Сброс формы возвращает нативному select первое значение
    if (sel.form) sel.form.addEventListener('reset', function () { setTimeout(refresh, 0); });
    refresh();
    sel._vf = api;
    return api;
  }
  function enhanceSelects(root) {
    root = root || document;
    if (!root.querySelectorAll) return;
    var list = root.querySelectorAll('select.form-control, select.filter-select, select.form-select, select.status-dropdown, select[data-vf-select]');
    for (var i = 0; i < list.length; i++) enhanceSelect(list[i]);
  }
  function selectShim() {
    if (!document.body || !document.body.classList.contains('vf')) return;
    enhanceSelects(document);
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) for (var j = 0; j < muts[i].addedNodes.length; j++) {
        var n = muts[i].addedNodes[j];
        if (n.nodeType !== 1) continue;
        if (n.tagName === 'SELECT') enhanceSelect(n); else enhanceSelects(n);
      }
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', selectShim);
  else selectShim();

  // ------------------------------------------------------------------
  // Мост Font Awesome → Lucide.
  // Страницы ЛК исторически размечены <i class="fas fa-…">. Вместо правки
  // сотен мест шим вставляет внутрь <i> svg из спрайта и следит за
  // динамической разметкой и сменой className (переключатели eye/eye-slash,
  // check/copy и т.п.). Элемент <i> остаётся — логика страниц не меняется.
  // ------------------------------------------------------------------
  var FA_MAP = {
    'check-circle': 'circle-check', 'robot': 'bot', 'user': 'user', 'info-circle': 'info', 'phone': 'phone', 'phone-alt': 'phone',
    'exclamation-circle': 'circle-alert', 'eye': 'eye', 'eye-slash': 'eye-off', 'clock': 'clock', 'trash': 'trash-2', 'trash-alt': 'trash-2',
    'sync-alt': 'refresh-cw', 'sync': 'refresh-cw', 'rotate': 'rotate-ccw', 'sign-out-alt': 'log-out', 'ruble-sign': 'russian-ruble',
    'exclamation-triangle': 'triangle-alert', 'chevron-down': 'chevron-down', 'chevron-up': 'chevron-down', 'chevron-right': 'chevron-right', 'chevron-left': 'chevron-left',
    'times': 'x', 'times-circle': 'circle-x', 'plus': 'plus', 'minus-circle': 'circle-minus', 'coins': 'coins', 'address-book': 'contact-round',
    'save': 'save', 'plug': 'plug', 'key': 'key', 'headset': 'headset', 'headphones': 'headphones', 'file-alt': 'file-text', 'copy': 'copy',
    'code': 'code', 'check': 'check', 'calendar-alt': 'calendar', 'calendar': 'calendar', 'calendar-plus': 'calendar-plus', 'brain': 'brain',
    'book': 'book', 'bars': 'menu', 'lock': 'lock', 'link': 'link', 'unlink': 'unlink', 'infinity': 'infinity', 'edit': 'pen', 'pen': 'pen',
    'crown': 'crown', 'comments': 'messages-square', 'comment': 'message-square', 'circle': 'circle', 'arrow-up': 'arrow-up', 'arrow-down': 'arrow-down',
    'arrow-left': 'arrow-left', 'arrow-right': 'arrow-right', 'users': 'users', 'user-friends': 'users', 'user-check': 'user-check',
    'user-astronaut': 'user-round', 'user-shield': 'shield-check', 'user-clock': 'clock', 'shield-alt': 'shield-check', 'telegram': 'send',
    'paper-plane': 'send', 'spinner': 'loader-circle', 'circle-notch': 'loader-circle', 'search': 'search', 'history': 'history',
    'credit-card': 'credit-card', 'wallet': 'wallet', 'star': 'star', 'rocket': 'rocket', 'receipt': 'receipt', 'piggy-bank': 'piggy-bank',
    'phone-volume': 'phone-call', 'phone-slash': 'phone-off', 'phone-arrow-up-right': 'phone-outgoing', 'microchip': 'cpu', 'globe': 'globe',
    'gem': 'gem', 'cog': 'settings', 'cogs': 'settings', 'building': 'building', 'bolt': 'zap', 'th': 'layout-grid', 'sticky-note': 'sticky-note',
    'sms': 'message-square', 'shopping-cart': 'shopping-cart', 'route': 'route', 'mobile-alt': 'smartphone', 'microphone-alt': 'mic', 'microphone': 'mic',
    'list': 'list', 'inbox': 'inbox', 'hourglass-half': 'hourglass', 'handshake': 'handshake', 'funnel-dollar': 'filter', 'filter': 'filter',
    'fish': 'fish', 'fire': 'flame', 'columns': 'columns-2', 'chart-column': 'chart-column', 'chart-bar': 'chart-bar', 'briefcase': 'briefcase',
    'download': 'download', 'upload': 'upload', 'external-link-alt': 'external-link', 'bell': 'bell', 'envelope': 'mail', 'question-circle': 'circle-help',
    'play': 'play', 'sliders-h': 'sliders-horizontal', 'layer-group': 'layers', 'tag': 'tag', 'map-marker-alt': 'map-pin', 'chart-line': 'trending-up',
    'volume-up': 'volume-2', 'image': 'image', 'bookmark': 'bookmark', 'folder': 'folder', 'file': 'file', 'puzzle-piece': 'puzzle', 'magic': 'sparkles',
    'pencil-alt': 'pencil', 'money-bill': 'banknote', 'comment-dots': 'message-circle', 'phone-incoming': 'phone-incoming',
    // Имена Font Awesome 6 (страница агента): маппинг на те же символы спрайта
    'xmark': 'x', 'circle-xmark': 'circle-x', 'circle-check': 'circle-check', 'circle-info': 'info', 'circle-exclamation': 'circle-alert',
    'circle-question': 'circle-help', 'triangle-exclamation': 'triangle-alert', 'trash-can': 'trash-2', 'right-from-bracket': 'log-out',
    'arrow-right-long': 'arrow-right', 'clock-rotate-left': 'history', 'rotate-right': 'refresh-cw', 'list-check': 'square-check',
    'list-ol': 'list', 'calendar-check': 'calendar', 'calendar-days': 'calendar', 'calendar-day': 'calendar', 'calendar-week': 'calendar',
    'cloud-arrow-up': 'upload', 'file-import': 'upload', 'file-lines': 'file-text', 'magnifying-glass': 'search', 'microphone-lines': 'mic',
    'note-sticky': 'sticky-note', 'pen-to-square': 'square-pen', 'user-pen': 'square-pen', 'shield-halved': 'shield-check', 'sliders': 'sliders-horizontal',
    'gear': 'settings', 'link-slash': 'unlink', 'stream': 'columns-2', 'folder-open': 'folder', 'chart-pie': 'chart-column', 'chart-line': 'trending-up',
    'magnifying-glass-chart': 'search', 'id-card': 'contact-round', 'user-plus': 'user-check', 'users-rectangle': 'users', 'lightbulb': 'sparkles',
    'bullseye': 'circle', 'flag': 'bookmark', 'building': 'building', 'box-open': 'inbox', 'comment-sms': 'message-square', 'compass': 'globe',
    'forward': 'arrow-right', 'circle-pause': 'circle-minus', 'pause-circle': 'circle-minus', 'mug-hot': 'hourglass', 'arrows-turn-right': 'route',
    'quote-left': 'message-square', 'user-minus': 'circle-minus', 'ban': 'circle-x', 'clone': 'copy', 'person-walking': 'activity', 'shuffle': 'route',
    'venus': 'user', 'mars': 'user', 'circle-half-stroke': 'circle', 'wand-magic-sparkles': 'wand-2',
    'database': 'database', 'instagram': 'instagram'
  };
  function faName(el) {
    var cl = el.classList;
    for (var i = 0; i < cl.length; i++) {
      if (cl[i].indexOf('fa-') === 0) {
        var n = cl[i].slice(3);
        if (n === 'spin' || n === 'fw' || n === 'lg' || n === 'sm' || n === 'xs' || /^\d?x$/.test(n) || n === 'pulse') continue;
        return n;
      }
    }
    return null;
  }
  function faRender(el) {
    if (!(el.tagName === 'I' || el.tagName === 'SPAN')) return;
    var n = faName(el);
    if (!n) return;
    var lucide = FA_MAP[n] || (document.getElementById('i-' + n) ? n : null);
    if (!lucide && el.getAttribute('data-vf-fa') !== n) {
      // Нет аналога — оставляем как есть, но помечаем, чтобы не проверять снова
      el.setAttribute('data-vf-fa', n);
      return;
    }
    if (!lucide) return;
    // Важно: не трогаем DOM без необходимости — любая запись класса рождает
    // новую мутацию, и наблюдатель зациклится.
    if (el.getAttribute('data-vf-fa') === n && el.firstElementChild) {
      if (!el.classList.contains('vf-fa')) el.classList.add('vf-fa');
      return;
    }
    el.setAttribute('data-vf-fa', n);
    if (!el.classList.contains('vf-fa')) el.classList.add('vf-fa');
    if (n === 'chevron-up' && !el.classList.contains('vf-fa-flip')) el.classList.add('vf-fa-flip');
    el.innerHTML = icon(lucide);
  }
  function faSweep(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.matches && (root.tagName === 'I' || root.tagName === 'SPAN') && /(^|\s)fa[srlb]?(\s|$)|(^|\s)fa-/.test(root.className || '')) faRender(root);
    var list = root.querySelectorAll('i[class*="fa-"], span.fas[class*="fa-"], span.far[class*="fa-"], span.fab[class*="fa-"]');
    for (var i = 0; i < list.length; i++) faRender(list[i]);
  }
  function faShim() {
    faSweep(document.body);
    var mo = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var m = muts[i];
        if (m.type === 'attributes') faRender(m.target);
        else for (var j = 0; j < m.addedNodes.length; j++) if (m.addedNodes[j].nodeType === 1) faSweep(m.addedNodes[j]);
      }
    });
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', faShim);
  else faShim();

  var VF = { icon: icon, logo: logo, faSweep: faSweep, FA_MAP: FA_MAP, enhanceSelect: enhanceSelect, esc: esc, loader: loader, progress: progress, toast: toast, confirm: confirm, alert: alertDialog, modal: modal, select: select, menu: menu, skeleton: skeleton, ready: ready, MODEL_LOGOS: MODEL_LOGOS };
  global.VF = VF;

  // Автозапуск: загрузчик сразу, каркас после DOM. Страховка: спрятать через 6 с.
  if (document.documentElement.hasAttribute('data-vf-loader')) {
    if (document.body) loader.ensure(); else document.addEventListener('DOMContentLoaded', function () { loader.ensure(); });
    setTimeout(function () { loader.hide(); }, 6000);
    // Страницы без VF.ready() (data-vf-loader="auto"): прячем загрузчик, когда DOM готов
    // и завершились первые запросы к API. Раньше ждали window.load, то есть шрифты,
    // картинки и favicon, и держали заставку на 1-2 с дольше, чем грузились данные.
    if (document.documentElement.getAttribute('data-vf-loader') === 'auto') {
      var inflight = 0, seen = 0, domReady = false, done = false;
      var finish = function () { if (!done) { done = true; loader.hide(); } };
      var settle = function () {
        // Ждём, пока страница запустит свои первые запросы и они вернутся
        if (done || !domReady || inflight > 0) return;
        if (seen === 0) { setTimeout(function () { if (seen === 0) finish(); else settle(); }, 300); return; }
        setTimeout(function () { if (inflight === 0) finish(); }, 80);
      };
      if (typeof window.fetch === 'function') {
        var origFetch = window.fetch;
        window.fetch = function () {
          inflight++; seen++;
          var p = origFetch.apply(this, arguments);
          var onDone = function () { inflight--; settle(); };
          p.then(onDone, onDone);
          return p;  // вызывающий код получает оригинальный промис и сам обрабатывает ошибки
        };
      }
      document.addEventListener('DOMContentLoaded', function () { domReady = true; setTimeout(settle, 0); });
      // Страховка на случай страниц без fetch: по load прячем, только если запросов в полёте нет
      window.addEventListener('load', function () { setTimeout(function () { if (inflight === 0) finish(); }, 250); });
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initShell); else initShell();
})(window);
