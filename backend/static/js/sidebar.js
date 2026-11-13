/**
 * Voksy AI — единый сайдбар личного кабинета (дизайн-система, этап 1).
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
 * 6. карточка «Кошелёк Voksy AI» (баланс в сомах, /api/wallet/balance) над подвалом сайдбара
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
    // Знания агента (/static/agent-knowledge.html) — ПОДстраница агента, а не
    // пункт кабинета: знания принадлежат конкретному агенту обзвона, а сам
    // агент доступен только по тарифу (profi/триал, User.has_agent_access).
    // Своего пункта в меню у неё поэтому нет — заходят из карточки «Знания и
    // файлы» в шторке агента, а меню подсвечивает «Агент обзвона» (alias).
    // profi: true — пункт виден всегда, но без оплаченного Profi (agent_mode !== 'full') стоит
    // с замком и ведёт к выбору тарифа (06.11.2026: пробного периода у Profi нет).
    { href: '/static/agent.html', lucide: 'headset', label: 'sidebar.agent', def: 'Агент обзвона', id: 'agent-nav-item',
      aliases: ['/static/agent-knowledge.html'], profi: true },
    // Инбокс (ветка 0411-inbox): общий чат с клиентами — только тем, у кого есть агент (mode: 'full')
    { href: '/static/inbox.html', lucide: 'inbox', label: 'sidebar.inbox', def: 'Инбокс', id: 'inbox-nav-item', mode: 'full' },
    { href: '/static/voice-assistants.html', lucide: 'audio-lines', label: 'sidebar.assistants', def: 'Голосовые ассистенты', id: 'assistants-nav-item',
      aliases: ['/static/eleven-test.html'] },
    { href: '/static/conversations.html', lucide: 'messages-square', label: 'sidebar.conversations', def: 'Диалоги', id: 'conversations-nav-item' },
    { href: '/static/telephony.html', lucide: 'phone', label: 'sidebar.telephony', def: 'Телефония', id: 'telephony-nav-item', feature: 'telephony' },
    { href: '/static/crm.html', lucide: 'contact-round', label: 'sidebar.crm', def: 'CRM', id: 'crm-nav-item', feature: 'crm',
      aliases: ['/static/crm-contact.html'] },

    { section: 'sidebar.section_account', def: 'Аккаунт', account: true },
    { href: '/static/settings.html', lucide: 'settings', label: 'sidebar.settings', def: 'Настройки', id: 'settings-nav-item' }
  ];

  // Тариф Старт (agent_mode = 'reactive') — отдельный набор страниц кабинета. Это НЕ страница
  // агента обзвона (agent.html, её видит только Профи): CRM, Аналитика и Чат-бот Старта —
  // собственные страницы в оформлении кабинета, свой код в /static/start/.
  var START_MENU = [
    { section: 'sidebar.section_main', def: 'Основное' },
    { href: '/static/dashboard.html', lucide: 'house', label: 'sidebar.dashboard', def: 'Дашборд', id: 'dashboard-nav-item' },
    // Инбокс — сразу после дашборда: на Старте с переписки начинается рабочий день
    { href: '/static/inbox.html', lucide: 'inbox', label: 'sidebar.inbox', def: 'Инбокс', id: 'inbox-nav-item' },
    { href: '/static/voice-assistants.html', lucide: 'audio-lines', label: 'sidebar.assistants', def: 'Голосовые ассистенты', id: 'assistants-nav-item',
      aliases: ['/static/eleven-test.html'] },
    { href: '/static/conversations.html', lucide: 'messages-square', label: 'sidebar.conversations', def: 'Диалоги', id: 'conversations-nav-item' },
    { href: '/static/telephony.html', lucide: 'phone', label: 'sidebar.telephony', def: 'Телефония', id: 'telephony-nav-item' },
    // CRM — таблицы и канбан: на телефоне скрыта (13.11.2026), там работают Инбокс и Каталог
    { href: '/static/start-crm.html', lucide: 'contact-round', label: 'sidebar.crm', def: 'CRM', id: 'crm-nav-item', desktop: true },
    { href: '/static/start-analytics.html', lucide: 'chart-column', label: 'sidebar.analytics', def: 'Аналитика', id: 'analytics-nav-item' },
    { href: '/static/start-bot.html', lucide: 'bot', label: 'sidebar.chatbot', def: 'Чат-бот', id: 'chatbot-nav-item' },
    // Автономный агент — только Profi: на Старте и пробном периоде пункт с замком (ведёт к оплате)
    { href: '/static/agent.html', lucide: 'headset', label: 'sidebar.agent', def: 'Агент обзвона', id: 'agent-nav-item', profi: true },

    { section: 'sidebar.section_account', def: 'Аккаунт', account: true },
    { href: '/static/settings.html', lucide: 'settings', label: 'sidebar.settings', def: 'Настройки', id: 'settings-nav-item' }
  ];

  // Режим агента кэшируем, чтобы меню не мигало «Профи → Старт» при каждой загрузке
  var MODE_KEY = 'vf_agent_mode';
  function cachedMode() {
    try { return localStorage.getItem(MODE_KEY) || ''; } catch (e) { return ''; }
  }
  function currentMenu() {
    return cachedMode() === 'reactive' ? START_MENU : MENU;
  }

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

  // Ссылка на выбор тарифа с выделенным Profi (дашборд открывает модалку по #plans)
  var PLANS_URL = '/static/dashboard.html#plans';
  var LOCK_STYLE = [
    '.sidebar-nav-item.vf-profi-locked{position:relative;color:var(--vf-text-3,#63738b)}',
    '.sidebar-nav-item.vf-profi-locked>.ic:first-child{opacity:.7}',
    '.vf-profi-pill{margin-left:auto;display:inline-flex;align-items:center;gap:4px;padding:2px 7px 2px 6px;border-radius:999px;',
    'font-size:10.5px;font-weight:700;letter-spacing:.03em;color:#8a5a00;background:linear-gradient(135deg,#fff4d6,#ffe7a8);border:1px solid #f3d58a}',
    '.vf-profi-pill .ic{width:11px;height:11px}',
    '.sidebar-nav-item.vf-profi-locked:hover .vf-profi-pill{border-color:#e0b84f}'
  ].join('');

  function profiLocked(item) {
    return !!item.profi && cachedMode() !== 'full';
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
    if (item.desktop) a.classList.add('vf-desktop-only');
    if (item.admin) a.setAttribute('data-admin', '1');
    if (profiLocked(item)) {
      if (!document.getElementById('vf-profi-lock-style')) {
        var st = document.createElement('style');
        st.id = 'vf-profi-lock-style';
        st.textContent = LOCK_STYLE;
        document.head.appendChild(st);
      }
      a.href = PLANS_URL;
      a.className = 'sidebar-nav-item vf-profi-locked';
      a.setAttribute('data-no-prefetch', '1');
      a.title = T('sidebar.profi_only', 'Автономный агент: сам звонит и пишет клиентам. Доступен после оплаты тарифа Profi');
      a.innerHTML = icon(item.lucide) + '<span>' + T(item.label, item.def) + '</span>' +
        '<span class="vf-profi-pill">' + icon('lock') + 'Profi</span>';
      a.addEventListener('click', function (e) {
        // Уже на дашборде: смена хэша не перезагружает страницу — дашборд слушает hashchange
        if (currentPath() === '/static/dashboard.html') {
          e.preventDefault();
          if (location.hash === '#plans') history.replaceState(null, '', location.pathname);
          location.hash = 'plans';
        }
      });
      return a;
    }
    a.innerHTML = icon(item.lucide) + '<span>' + T(item.label, item.def) + '</span>' +
      (item.feature ? icon('lock', 'lock') : '');
    return a;
  }

  function renderNav() {
    var nav = document.getElementById('sidebar-nav') || document.querySelector('.sidebar-nav');
    if (!nav) return null;
    nav.innerHTML = '';
    var mode = cachedMode();
    currentMenu().forEach(function (item) {
      if (item.mode && item.mode !== mode) return;   // пункт только для режима агента (инбокс — у Profi)
      nav.appendChild(renderItem(item));
    });
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

  // Баланс прошлой страницы показываем сразу, пока запрос не вернулся: иначе при каждом переходе
  // в карточке кошелька мигает «…» вместо суммы
  function cachedBalanceText() {
    try {
      var c = JSON.parse(sessionStorage.getItem('vf_wallet') || 'null');
      if (c && typeof c.balance === 'number') { walletState.balance = c.balance; return fmtSom(c.balance); }
    } catch (e) { /* ignore */ }
    return '…';
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
      '<div class="vf-wallet-label">' + icon('wallet') + ' ' + T('wallet.card_title', 'Кошелёк Voksy AI') + '</div>' +
      '<div class="vf-wallet-balance" id="vf-wallet-balance">' + cachedBalanceText() + '</div>' +
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
      try { sessionStorage.setItem('vf_wallet', JSON.stringify({ balance: walletState.balance })); } catch (e) { /* ignore */ }
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
    try { sessionStorage.removeItem('vf_is_admin'); sessionStorage.removeItem('vf_wallet'); } catch (e) { /* ignore */ }
    window.location.href = '/';
  }
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('#logout-button, #dropdown-logout, [data-logout]') : null;
    if (!el) return;
    e.preventDefault();
    logout();
  });

  // Страницы, которых у Старта нет: агент обзвона, «Знания агента» и старая CRM кабинета
  // (бот Старта пишет в CRM агента, поэтому «CRM» у Старта — start-crm.html)
  var START_REDIRECTS = {
    '/static/agent.html': '/static/start-bot.html',
    '/static/agent-knowledge.html': '/static/start-bot.html#knowledge',
    '/static/crm.html': '/static/start-crm.html',
    '/static/crm-contact.html': '/static/start-crm.html'
  };
  function redirectStartPages() {
    if (cachedMode() !== 'reactive') return false;
    var target = START_REDIRECTS[currentPath()];
    if (!target) return false;
    location.replace(target);
    return true;
  }

  function applyAgentMode(mode) {
    var prev = cachedMode();
    try { localStorage.setItem(MODE_KEY, mode); } catch (e) { /* ignore */ }
    if (mode !== prev) {
      // режим сменился (купили/потеряли Старт): перерисовываем меню и, если надо, уводим со страницы
      if (redirectStartPages()) return;
      var nav = renderNav();
      if (nav) {
        try { if (sessionStorage.getItem('vf_is_admin') === '1') injectAdmin(nav); } catch (e) { /* ignore */ }
        document.dispatchEvent(new CustomEvent('vf:menu', { detail: { mode: mode } }));
        renderTabBar(); phoneCrmNote();
      }
    }
  }

  // Быстрые переходы: страницу из меню браузер готовит заранее. Prerender — когда пользователь навёл
  // мышь на пункт (~200 мс): к моменту клика страница уже загружена, отрисована и открывается
  // мгновенно. Prefetch — запасной вариант (нажатие), если prerender не разрешён. Работает в Chrome/Edge,
  // в остальных браузерах правило игнорируется.
  function addSpeculation() {
    try {
      if (!(window.HTMLScriptElement && HTMLScriptElement.supports && HTMLScriptElement.supports('speculationrules'))) return;
      if (document.querySelector('script[type="speculationrules"]')) return;
      var sel = { selector_matches: '#sidebar-nav a[href^="/static/"]:not([data-no-prefetch])' };
      var sc = document.createElement('script');
      sc.type = 'speculationrules';
      sc.textContent = JSON.stringify({
        prefetch: [{ where: sel, eagerness: 'conservative' }],
        prerender: [{ where: sel, eagerness: 'moderate' }]
      });
      document.head.appendChild(sc);
    } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------------
  // Телефон (13.11.2026): нижняя панель как в приложении — Главная · Инбокс · Каталог · Бот · Ещё.
  // Продавцы на базаре работают только с телефона: всё главное — одним нажатием большим пальцем.
  // Только для Старта и пробного периода (у Profi своя страница агента). CRM на телефоне скрыта.
  // ---------------------------------------------------------------------
  var PHONE = '(max-width: 760px)';
  var TABBAR_STYLE = [
    '@media ' + PHONE + '{',
    '.vf-desktop-only{display:none!important}',
    'body.vf-has-tabbar{padding-bottom:calc(64px + env(safe-area-inset-bottom,0px))}',
    'body.vf-has-tabbar .st-bulk{bottom:calc(72px + env(safe-area-inset-bottom,0px))}',
    '.vf-tabbar{position:fixed;left:0;right:0;bottom:0;z-index:900;display:flex;background:rgba(255,255,255,.97);',
    'border-top:1px solid var(--vf-border,#e2e8f0);padding:4px 4px calc(4px + env(safe-area-inset-bottom,0px));',
    'box-shadow:0 -6px 20px rgba(15,23,42,.06)}',
    '.vf-tab{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-height:52px;',
    'border:0;background:none;color:var(--vf-text-3,#63738b);font:500 11px/1.1 Inter,system-ui,sans-serif;text-decoration:none;',
    'border-radius:12px;position:relative;-webkit-tap-highlight-color:transparent;touch-action:manipulation;cursor:pointer}',
    '.vf-tab .ic{width:23px;height:23px;transition:transform .15s}',
    '.vf-tab:active .ic{transform:scale(.88)}',
    '.vf-tab.on{color:var(--vf-accent,#2a5ce8);font-weight:650}',
    '.vf-tab.on::before{content:"";position:absolute;top:2px;width:36px;height:30px;border-radius:10px;background:var(--vf-accent-soft,#eaf0fe);z-index:-1}',
    '.vf-tab .vf-badge{position:absolute;top:4px;left:calc(50% + 6px);min-width:17px;height:17px;padding:0 4px;border-radius:999px;',
    'background:#ef4444;color:#fff;font:700 10px/17px Inter,sans-serif;text-align:center}',
    '}',
    '@media (min-width: 761px){.vf-tabbar{display:none}}',
    '.vf-phone-note{position:fixed;inset:0;z-index:850;background:var(--vf-bg,#f6f8fc);display:flex;align-items:center;justify-content:center;padding:24px}',
    '.vf-phone-note .box{max-width:340px;text-align:center;display:flex;flex-direction:column;gap:12px;align-items:center}',
    '.vf-phone-note .ico{width:64px;height:64px;border-radius:20px;background:var(--vf-accent-soft,#eaf0fe);color:var(--vf-accent,#2a5ce8);display:flex;align-items:center;justify-content:center}',
    '.vf-phone-note .ico .ic{width:30px;height:30px}',
    '.vf-phone-note h2{margin:0;font-size:19px}',
    '.vf-phone-note p{margin:0;color:var(--vf-text-3,#63738b);font-size:14px;line-height:1.5}',
    '.vf-phone-note .row{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:4px}',
    '.vf-phone-note a.b{display:inline-flex;align-items:center;gap:6px;height:44px;padding:0 18px;border-radius:12px;background:var(--vf-accent,#2a5ce8);color:#fff;font-weight:600;text-decoration:none}',
    '.vf-phone-note a.b.ghost{background:#fff;color:var(--vf-text,#0e1729);border:1px solid var(--vf-border,#e2e8f0)}',
    '.vf-phone-note button.l{border:0;background:none;color:var(--vf-text-4,#93a2b8);font-size:13px;text-decoration:underline;cursor:pointer;margin-top:6px}'
  ].join('');
  var TABS = [
    { id: 'home', href: '/static/dashboard.html', icon: 'house', label: 'Главная', ky: 'Башкы' },
    { id: 'inbox', href: '/static/inbox.html', icon: 'inbox', label: 'Инбокс', ky: 'Инбокс' },
    { id: 'catalog', href: '/static/start-bot.html#catalog', icon: 'package', label: 'Каталог', ky: 'Каталог' },
    { id: 'bot', href: '/static/start-bot.html', icon: 'bot', label: 'Бот', ky: 'Бот' },
    { id: 'more', icon: 'menu', label: 'Ещё', ky: 'Дагы' }
  ];
  function isPhone() { try { return window.matchMedia(PHONE).matches; } catch (e) { return false; } }
  function lang() { try { return localStorage.getItem('vs_lang') || 'ru'; } catch (e) { return 'ru'; } }
  function activeTab() {
    var p = currentPath(), h = (location.hash || '').replace('#', '');
    if (p === '/static/dashboard.html') return 'home';
    if (p === '/static/inbox.html') return 'inbox';
    if (p === '/static/start-bot.html') return (h === 'catalog' || h === 'products') ? 'catalog' : 'bot';
    return 'more';
  }
  function markTabs() {
    var bar = document.getElementById('vf-tabbar');
    if (!bar) return;
    var on = activeTab();
    Array.prototype.forEach.call(bar.querySelectorAll('.vf-tab'), function (t) { t.classList.toggle('on', t.getAttribute('data-tab') === on); });
  }
  function openMoreMenu() {
    var btn = document.getElementById('sidebar-toggle');
    if (btn) { btn.click(); return; }
    var sb = document.getElementById('sidebar');
    if (sb) sb.classList.toggle('open');
  }
  function renderTabBar() {
    if (cachedMode() !== 'reactive') return;
    if (window.top !== window.self) return;           // инбокс во фрейме у Profi — без своей панели
    if (document.getElementById('vf-tabbar')) { markTabs(); return; }
    if (!document.getElementById('vf-tabbar-style')) {
      var st = document.createElement('style'); st.id = 'vf-tabbar-style'; st.textContent = TABBAR_STYLE;
      document.head.appendChild(st);
    }
    var ky = lang() === 'ky';
    var bar = document.createElement('nav');
    bar.id = 'vf-tabbar'; bar.className = 'vf-tabbar'; bar.setAttribute('aria-label', 'Меню');
    bar.innerHTML = TABS.map(function (t) {
      var inner = icon(t.icon) + '<span>' + (ky ? t.ky : t.label) + '</span>' + (t.id === 'inbox' ? '<span class="vf-badge" id="vf-tab-unread" hidden></span>' : '');
      return t.href ? '<a class="vf-tab" data-tab="' + t.id + '" href="' + t.href + '">' + inner + '</a>'
        : '<button type="button" class="vf-tab" data-tab="' + t.id + '">' + inner + '</button>';
    }).join('');
    document.body.appendChild(bar);
    document.body.classList.add('vf-has-tabbar');
    bar.querySelector('[data-tab="more"]').addEventListener('click', openMoreMenu);
    // Каталог и Бот живут на одной странице: переход между ними — без перезагрузки
    Array.prototype.forEach.call(bar.querySelectorAll('a.vf-tab'), function (a) {
      a.addEventListener('click', function (e) {
        var url = a.getAttribute('href'), path = url.split('#')[0];
        if (path === currentPath()) {
          e.preventDefault();
          var h = url.indexOf('#') >= 0 ? url.split('#')[1] : '';
          if (h) { if (location.hash !== '#' + h) location.hash = h; else window.dispatchEvent(new HashChangeEvent('hashchange')); }
          else { history.replaceState(null, '', path); window.dispatchEvent(new HashChangeEvent('hashchange')); }
          markTabs();
          window.scrollTo(0, 0);
        }
      });
    });
    window.addEventListener('hashchange', markTabs);
    markTabs();
    refreshUnread();
  }
  // Непрочитанные в инбоксе — точка на вкладке (раз в минуту, только пока страница открыта)
  function refreshUnread() {
    if (!token() || !document.getElementById('vf-tab-unread')) return;
    apiFetch('/inbox/dialogs?folder=unread&limit=1').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      var el = document.getElementById('vf-tab-unread'); if (!el || !d) return;
      var n = (d.counts && d.counts.unread) || 0;
      el.hidden = !n; el.textContent = n > 99 ? '99+' : String(n);
    }).catch(function () { /* ignore */ });
    if (!refreshUnread.timer) refreshUnread.timer = setInterval(function () { if (!document.hidden) refreshUnread(); }, 60000);
  }
  // CRM на телефоне: таблицы и канбан не помещаются — понятный экран вместо мелкой каши
  function phoneCrmNote() {
    if (cachedMode() !== 'reactive' || !isPhone()) return;
    if (currentPath() !== '/static/start-crm.html') return;
    try { if (sessionStorage.getItem('vf_crm_phone_ok') === '1') return; } catch (e) { /* ignore */ }
    var n = document.createElement('div');
    n.className = 'vf-phone-note';
    n.innerHTML = '<div class="box"><div class="ico">' + icon('monitor') + '</div><h2>CRM удобнее на компьютере</h2>' +
      '<p>Таблица клиентов и воронка открываются на ноутбуке или планшете. С телефона клиентам отвечают в Инбоксе, а товары и остатки правят в Каталоге.</p>' +
      '<div class="row"><a class="b" href="/static/inbox.html">' + icon('inbox') + 'Инбокс</a><a class="b ghost" href="/static/start-bot.html#catalog">' + icon('package') + 'Каталог</a></div>' +
      '<button type="button" class="l">Всё равно открыть CRM</button></div>';
    document.body.appendChild(n);
    n.querySelector('button.l').addEventListener('click', function () {
      try { sessionStorage.setItem('vf_crm_phone_ok', '1'); } catch (e) { /* ignore */ }
      n.remove();
    });
  }

  function init() {
    if (redirectStartPages()) return;
    var nav = renderNav();
    if (document.body) { renderTabBar(); phoneCrmNote(); }
    else document.addEventListener('DOMContentLoaded', function () { renderTabBar(); phoneCrmNote(); });
    addSpeculation();
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
      applyAgentMode(user.agent_mode || '');
      if (admin) injectAdmin(document.getElementById('sidebar-nav') || nav);
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
