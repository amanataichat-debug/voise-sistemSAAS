/**
 * VoksiAI — единый сайдбар личного кабинета (дизайн-система, этап 1).
 *
 * Раньше меню было скопировано руками в каждую страницу и везде разъехалось.
 * Теперь страница держит пустой <nav class="sidebar-nav" id="sidebar-nav"></nav>
 * внутри <aside class="sidebar">, а этот скрипт:
 *   1. синхронно рисует единое меню (скрипты страниц, которые ищут
 *      [data-feature], #telephony-nav-item и т.п., находят элементы);
 *   2. подсвечивает активный пункт по URL;
 *   3. добавляет раздел «Администрирование» админу (user.is_admin);
 *   4. даёт единый выход (#logout-button, #dropdown-logout, [data-logout]);
 *   5. ставит переключатель языка RU | KY в топбар.
 *
 * Кошелёк и обязательный онбординг из пакета дизайн-системы сюда не перенесены:
 * на бэкенде нет /api/wallet/* и onboarding_completed. Исходник с ними —
 * design-system/js/sidebar.js, переносить вместе с бэкендом.
 *
 * Блокировку пунктов по тарифу (класс plan-locked-feature) применяют сами
 * страницы (дашборд, настройки) — у них актуальная матрица тарифов.
 *
 * Тексты берутся из словаря /static/i18n/<lang>.json через window.I18N
 * (js/i18n.js); без него — русские строки по умолчанию.
 */
(function () {
  'use strict';

  var API = '/api';
  // Админ: флаг is_admin или e-mail (так же решают страницы дашборда и настроек)
  var ADMIN_EMAILS = ['amanat.aichat@gmail.com'];

  function T(key, def, params) {
    return window.I18N ? window.I18N.t(key, params, def) : def;
  }

  var MENU = [
    { section: 'sidebar.section_main', def: 'Основное' },
    { href: '/static/dashboard.html', lucide: 'house', label: 'sidebar.dashboard', def: 'Дашборд', id: 'dashboard-nav-item' },
    { href: '/static/agent.html', lucide: 'headset', label: 'sidebar.agent', def: 'Агент обзвона', id: 'agent-nav-item' },
    { href: '/static/voice-assistants.html', lucide: 'audio-lines', label: 'sidebar.assistants', def: 'Голосовые ассистенты', id: 'assistants-nav-item',
      aliases: ['/static/eleven-test.html'] },
    { href: '/static/conversations.html', lucide: 'messages-square', label: 'sidebar.conversations', def: 'Диалоги', id: 'conversations-nav-item' },
    { href: '/static/telephony.html', lucide: 'phone', label: 'sidebar.telephony', def: 'Телефония', id: 'telephony-nav-item', feature: 'telephony' },
    { href: '/static/crm.html', lucide: 'contact-round', label: 'sidebar.crm', def: 'CRM', id: 'crm-nav-item', feature: 'crm',
      aliases: ['/static/crm-contact.html'] },

    { section: 'sidebar.section_account', def: 'Аккаунт', account: true },
    { href: '/static/settings.html', lucide: 'settings', label: 'sidebar.settings', def: 'Настройки', id: 'settings-nav-item' }
  ];

  var ADMIN_ITEMS = [
    { section: 'sidebar.section_admin', def: 'Администрирование', admin: true },
    { href: '/static/admin.html', lucide: 'shield-check', label: 'sidebar.admin', def: 'Управление', id: 'admin-nav-item', admin: true }
  ];

  function token() {
    try { return localStorage.getItem('auth_token'); } catch (e) { return null; }
  }

  function currentPath() {
    var p = location.pathname.replace(/\/+$/, '');
    if (p === '/static' || p === '') p = '/static/dashboard.html';
    return p;
  }

  function isActive(item) {
    var p = currentPath();
    if (item.href === p) return true;
    return (item.aliases || []).indexOf(p) !== -1;
  }

  function icon(name, cls) {
    if (window.VF) return window.VF.icon(name, cls);
    return '<svg class="ic' + (cls ? ' ' + cls : '') + '" aria-hidden="true"><use href="/static/icons/ui.svg#i-' + name + '"></use></svg>';
  }

  function renderItem(item) {
    if (item.section) {
      var s = document.createElement('div');
      s.className = 'sidebar-section';
      s.textContent = T(item.section, item.def);
      if (item.admin) s.setAttribute('data-admin', '1');
      if (item.account) s.setAttribute('data-account', '1');
      return s;
    }
    var a = document.createElement('a');
    a.href = item.href;
    a.className = 'sidebar-nav-item' + (isActive(item) ? ' active' : '');
    if (item.id) a.id = item.id;
    if (item.feature) a.setAttribute('data-feature', item.feature);
    if (item.admin) a.setAttribute('data-admin', '1');
    a.innerHTML = icon(item.lucide) + '<span>' + T(item.label, item.def) + '</span>' +
      (item.feature ? icon('lock', 'lock') : '');
    return a;
  }

  function renderNav() {
    var nav = document.getElementById('sidebar-nav') || document.querySelector('.sidebar-nav');
    if (!nav) return null;
    nav.innerHTML = '';
    MENU.forEach(function (item) { nav.appendChild(renderItem(item)); });
    return nav;
  }

  // Некоторые страницы сами дописывают «Администрирование» (старый код,
  // сравнивает русский текст). Убираем их копию и ставим свою — с data-admin.
  function injectAdmin(nav) {
    if (!nav || nav.querySelector('[data-admin]')) return;
    Array.prototype.forEach.call(nav.querySelectorAll('a[href="/static/admin.html"]'), function (a) {
      var prev = a.previousElementSibling;
      if (prev && prev.classList.contains('sidebar-section')) prev.remove();
      a.remove();
    });
    var account = nav.querySelector('.sidebar-section[data-account]');
    ADMIN_ITEMS.forEach(function (item) {
      var el = renderItem(item);
      if (account) nav.insertBefore(el, account); else nav.appendChild(el);
    });
  }

  function apiFetch(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    var t = token();
    if (t) opts.headers['Authorization'] = 'Bearer ' + t;
    return fetch(API + path, opts);
  }

  // ---------------------------------------------------------------------
  // Переключатель языка RU | KY: в топбаре перед меню пользователя
  // ---------------------------------------------------------------------
  function renderLangSwitch() {
    if (!window.I18N || document.querySelector('.vf-lang-switch')) return;
    var bar = document.querySelector('.vf-topbar-right') || document.querySelector('.top-nav');
    if (!bar) return;
    var cur = window.I18N.lang;
    var wrap = document.createElement('div');
    wrap.className = 'vf-lang-switch';
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', T('common.language', 'Язык'));
    window.I18N.LANGS.forEach(function (l) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = l.toUpperCase();
      b.className = l === cur ? 'active' : '';
      b.setAttribute('aria-pressed', l === cur ? 'true' : 'false');
      b.addEventListener('click', function () { window.I18N.setLang(l); });
      wrap.appendChild(b);
    });
    // Меню пользователя бывает вложено (.page-actions, .top-nav-right) — ставим рядом с ним
    var userMenu = bar.querySelector('.user-menu');
    if (userMenu) userMenu.parentNode.insertBefore(wrap, userMenu);
    else bar.appendChild(wrap);
  }

  // ---------------------------------------------------------------------
  // Выход. Единый для всех страниц: снимаем токен, чистим кэш признака
  // админа и уводим на лендинг текущего домена.
  // ---------------------------------------------------------------------
  function logout() {
    try { localStorage.removeItem('auth_token'); } catch (e) { /* ignore */ }
    try { sessionStorage.removeItem('vf_is_admin'); } catch (e) { /* ignore */ }
    window.location.href = '/';
  }
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('#logout-button, #dropdown-logout, [data-logout]') : null;
    if (!el) return;
    e.preventDefault();
    logout();
  });

  function init() {
    var nav = renderNav();
    renderLangSwitch();
    if (!nav) return;

    // Быстрый путь: админ из кэша сессии (чтобы меню не мигало)
    try {
      if (sessionStorage.getItem('vf_is_admin') === '1') injectAdmin(nav);
    } catch (e) { /* ignore */ }

    if (!token()) return;
    apiFetch('/users/me').then(function (r) { return r.ok ? r.json() : null; }).then(function (user) {
      if (!user) return;
      var admin = !!user.is_admin || ADMIN_EMAILS.indexOf(user.email) !== -1;
      try { sessionStorage.setItem('vf_is_admin', admin ? '1' : '0'); } catch (e) { /* ignore */ }
      if (admin) injectAdmin(nav);
      document.dispatchEvent(new CustomEvent('vf:user', { detail: user }));
    }).catch(function () { /* ignore */ });
  }

  window.VoksiAISidebar = {
    logout: logout,
    MENU: MENU
  };

  if (document.readyState === 'loading' && !document.getElementById('sidebar-nav')) {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  // Топбар стоит в разметке после сайдбара — переключатель ставим, когда DOM готов
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderLangSwitch);
})();
