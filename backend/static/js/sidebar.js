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
 * 6. карточка «Кошелёк VoksiAI» (баланс в сомах, /api/wallet/balance) над подвалом сайдбара
 *    и модалка пополнения (POST /api/wallet/topup → переход на страницу оплаты Finik).
 *    Другие страницы обновляют баланс через window.VoksiAISidebar.refreshWallet()
 *    и получают событие vf:wallet с ответом /balance.
 *
 * Обязательный онбординг из пакета дизайн-системы не перенесён (нет onboarding_completed);
 * исходник — design-system/js/sidebar.js.
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
  // Кошелёк (сом)
  // ---------------------------------------------------------------------
  var WALLET_STYLE = [
    '.vf-wallet{margin:0 12px 10px;padding:12px 14px;border:1px solid var(--vf-border,#e2e8f0);border-radius:12px;background:var(--vf-surface-2,#f8fafc)}',
    '.vf-wallet-label{display:flex;align-items:center;gap:6px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--vf-text-4,#93a2b8);font-weight:600}',
    '.vf-wallet-label .ic{width:14px;height:14px}',
    '.vf-wallet-balance{font-size:20px;font-weight:700;color:var(--vf-text,#0e1729);margin:4px 0 10px;font-family:"Syne",sans-serif;letter-spacing:-.02em}',
    '.vf-wallet-balance.neg{color:var(--vf-danger,#dc2626)}',
    '.vf-wallet-btn{display:flex;align-items:center;justify-content:center;gap:6px;width:100%;height:32px;border:none;border-radius:8px;background:var(--vf-accent,#2a5ce8);color:#fff;font-weight:600;cursor:pointer;font-size:13px}',
    '.vf-wallet-btn:hover{background:var(--vf-accent-hover,#2149cf)}',
    '.vf-wallet-btn .ic{width:14px;height:14px}',
    '.vf-wallet-link{display:block;margin-top:8px;font-size:12px;color:var(--vf-text-3,#63738b);text-decoration:none;text-align:center}',
    '.vf-wallet-link:hover{color:var(--vf-accent,#2a5ce8)}',
    '.vf-modal-overlay{position:fixed;inset:0;background:rgba(14,23,41,.55);display:flex;align-items:center;justify-content:center;z-index:10000;padding:16px}',
    '.vf-topup{background:var(--vf-surface,#fff);color:var(--vf-text,#0e1729);border-radius:16px;width:100%;max-width:420px;padding:24px;box-shadow:0 20px 40px rgba(0,0,0,.2)}',
    '.vf-topup h3{margin:0 0 4px;font-size:18px}',
    '.vf-topup p{margin:0 0 14px;color:var(--vf-text-3,#63738b);font-size:14px;line-height:1.45}',
    '.vf-topup-chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}',
    '.vf-topup-chip{padding:7px 13px;border:1px solid var(--vf-border,#e2e8f0);border-radius:999px;background:transparent;color:inherit;cursor:pointer;font-size:13px}',
    '.vf-topup-chip.active,.vf-topup-chip:hover{border-color:var(--vf-accent,#2a5ce8);color:var(--vf-accent,#2a5ce8)}',
    '.vf-topup input{width:100%;height:40px;padding:0 12px;border:1px solid var(--vf-border,#e2e8f0);border-radius:8px;font-size:16px;box-sizing:border-box;background:transparent;color:inherit}',
    '.vf-topup input.err{border-color:#ef4444}',
    '.vf-topup-hint{font-size:12px;color:var(--vf-text-4,#93a2b8);margin-top:8px}',
    '.vf-topup-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:18px}',
    '.vf-topup-actions button{height:38px;padding:0 16px;border-radius:8px;border:1px solid var(--vf-border,#e2e8f0);background:transparent;color:inherit;cursor:pointer;font-weight:600}',
    '.vf-topup-actions .primary{background:var(--vf-accent,#2a5ce8);border-color:var(--vf-accent,#2a5ce8);color:#fff}',
    '.vf-topup-actions button[disabled]{opacity:.6;cursor:default}'
  ].join('');
  var walletState = { balance: 0, min: 10, max: 100000, free: false };

  function fmtSom(v) {
    return (Number(v) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' ' + T('wallet.currency', 'сом');
  }

  function renderWalletCard(nav) {
    var aside = nav ? nav.closest('.sidebar') : document.querySelector('.sidebar');
    if (!aside || aside.querySelector('.vf-wallet')) return;
    if (!document.getElementById('vf-wallet-style')) {
      var st = document.createElement('style');
      st.id = 'vf-wallet-style';
      st.textContent = WALLET_STYLE;
      document.head.appendChild(st);
    }
    var card = document.createElement('div');
    card.className = 'vf-wallet';
    card.id = 'vf-wallet-card';
    card.innerHTML =
      '<div class="vf-wallet-label">' + icon('wallet') + ' ' + T('wallet.card_title', 'Кошелёк VoksiAI') + '</div>' +
      '<div class="vf-wallet-balance" id="vf-wallet-balance">…</div>' +
      '<button class="vf-wallet-btn" type="button" id="vf-wallet-topup">' + icon('plus') + ' ' + T('wallet.topup', 'Пополнить') + '</button>' +
      '<a class="vf-wallet-link" href="/static/settings.html#wallet">' + T('wallet.history', 'История операций') + '</a>';
    var footer = aside.querySelector('.sidebar-footer');
    if (footer) aside.insertBefore(card, footer); else aside.appendChild(card);
    card.querySelector('#vf-wallet-topup').addEventListener('click', openTopupModal);
  }

  function refreshWallet() {
    var el = document.getElementById('vf-wallet-balance');
    if (!token()) return Promise.resolve(null);
    return apiFetch('/wallet/balance').then(function (r) {
      if (!r.ok) throw new Error('balance ' + r.status);
      return r.json();
    }).then(function (d) {
      walletState.balance = d.balance || 0;
      walletState.min = d.min_topup || 10;
      walletState.max = d.max_topup || 100000;
      walletState.free = !!d.free;
      if (el) {
        el.textContent = fmtSom(walletState.balance);
        el.classList.toggle('neg', walletState.balance < 0);
      }
      document.dispatchEvent(new CustomEvent('vf:wallet', { detail: d }));
      return d;
    }).catch(function () { if (el) el.textContent = '—'; return null; });
  }

  function openTopupModal() {
    if (document.getElementById('vf-topup-modal')) return;
    var presets = [100, 500, 1000, 3000];
    var overlay = document.createElement('div');
    overlay.className = 'vf-modal-overlay';
    overlay.id = 'vf-topup-modal';
    overlay.innerHTML =
      '<div class="vf-topup" role="dialog" aria-modal="true">' +
      '<h3>' + T('wallet.topup_modal_title', 'Пополнить кошелёк') + '</h3>' +
      '<p>' + T('wallet.topup_text', 'Баланс: {balance}. С кошелька списываются минуты разговора голосовых ассистентов и агентов.',
        { balance: '<b>' + fmtSom(walletState.balance) + '</b>' }) + '</p>' +
      '<div class="vf-topup-chips">' + presets.map(function (v) {
        return '<button type="button" class="vf-topup-chip' + (v === 500 ? ' active' : '') + '" data-v="' + v + '">' + fmtSom(v) + '</button>';
      }).join('') + '</div>' +
      '<input id="vf-topup-amount" type="number" min="' + walletState.min + '" max="' + walletState.max + '" step="1" value="500">' +
      '<div class="vf-topup-hint">' + T('wallet.topup_hint', 'От {min} до {max}. Оплата через платёжный шлюз.',
        { min: fmtSom(walletState.min), max: fmtSom(walletState.max) }) + '</div>' +
      '<div class="vf-topup-actions">' +
      '<button type="button" id="vf-topup-cancel">' + T('wallet.topup_cancel', 'Отмена') + '</button>' +
      '<button type="button" class="primary" id="vf-topup-pay">' + T('wallet.topup_submit', 'Перейти к оплате') + '</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    var input = overlay.querySelector('#vf-topup-amount');
    Array.prototype.forEach.call(overlay.querySelectorAll('.vf-topup-chip'), function (c) {
      c.addEventListener('click', function () {
        Array.prototype.forEach.call(overlay.querySelectorAll('.vf-topup-chip'), function (x) { x.classList.remove('active'); });
        c.classList.add('active');
        input.value = c.getAttribute('data-v');
      });
    });
    function close() { overlay.remove(); }
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    overlay.querySelector('#vf-topup-cancel').addEventListener('click', close);
    overlay.querySelector('#vf-topup-pay').addEventListener('click', function () {
      var amount = parseFloat(input.value);
      if (!amount || amount < walletState.min || amount > walletState.max) { input.classList.add('err'); input.focus(); return; }
      var btn = this; btn.disabled = true;
      apiFetch('/wallet/topup', { method: 'POST', body: JSON.stringify({ amount: amount }) })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok || !res.d.payment_url) {
            alert(T('wallet.topup_failed', 'Не удалось создать платёж') + ': ' + (res.d.detail || res.d.message || ''));
            btn.disabled = false;
            return;
          }
          window.location.href = res.d.payment_url;
        })
        .catch(function (e) { alert(e.message); btn.disabled = false; });
    });
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
    renderWalletCard(nav);
    refreshWallet();
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
    refreshWallet: refreshWallet,
    openTopup: openTopupModal,
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
