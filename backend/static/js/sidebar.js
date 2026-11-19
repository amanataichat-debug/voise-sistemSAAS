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
    // Аналитика (ветка 1811-niche-admin-speed) — для всех тарифов: без бота показывает звонки и разговоры ассистентов
    { href: '/static/analytics.html', lucide: 'chart-column', label: 'sidebar.analytics', def: 'Аналитика', id: 'analytics-nav-item' },

    { section: 'sidebar.section_account', def: 'Аккаунт', account: true },
    // Сотрудники (ветка 1811-niche-admin-speed): свой вход, только выбранные разделы — на Старте и Profi
    { href: '/static/team.html', lucide: 'users', label: 'sidebar.team', def: 'Сотрудники', id: 'team-nav-item', modes: ['full', 'reactive'] },
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
    // CRM — и на телефоне (18.11.2026): список клиентов карточками со звонком и WhatsApp одним нажатием
    { href: '/static/start-crm.html', lucide: 'contact-round', label: 'sidebar.crm', def: 'CRM', id: 'crm-nav-item' },
    { href: '/static/analytics.html', lucide: 'chart-column', label: 'sidebar.analytics', def: 'Аналитика', id: 'analytics-nav-item',
      aliases: ['/static/start-analytics.html'] },
    { href: '/static/start-bot.html', lucide: 'bot', label: 'sidebar.chatbot', def: 'Чат-бот', id: 'chatbot-nav-item' },
    // Автономный агент — только Profi: на Старте и пробном периоде пункт с замком (ведёт к оплате)
    { href: '/static/agent.html', lucide: 'headset', label: 'sidebar.agent', def: 'Агент обзвона', id: 'agent-nav-item', profi: true },

    { section: 'sidebar.section_account', def: 'Аккаунт', account: true },
    { href: '/static/team.html', lucide: 'users', label: 'sidebar.team', def: 'Сотрудники', id: 'team-nav-item' },
    { href: '/static/settings.html', lucide: 'settings', label: 'sidebar.settings', def: 'Настройки', id: 'settings-nav-item' }
  ];

  // Режим агента кэшируем, чтобы меню не мигало «Профи → Старт» при каждой загрузке
  var MODE_KEY = 'vf_agent_mode';
  function cachedMode() {
    try { return localStorage.getItem(MODE_KEY) || ''; } catch (e) { return ''; }
  }
  // Профиль бизнеса (ниша → модули, вкладки телефона) из /api/users/me → business. Храним в
  // localStorage, чтобы меню и вкладки рисовались сразу и не прыгали (ветка 1811-niche-admin-speed).
  var BIZ_KEY = 'vf_business';
  function cachedBusiness() {
    try { return JSON.parse(localStorage.getItem(BIZ_KEY) || 'null'); } catch (e) { return null; }
  }
  function adaptMenu(menu) {
    var biz = cachedBusiness();
    var mods = (biz && biz.modules) || {};
    var out = [];
    var profi = cachedMode() === 'full';
    menu.forEach(function (item) {
      if (item.id === 'crm-nav-item' && profi) {
        // Profi: «CRM» — клиенты агента (общая страница со Стартом), а не старая база контактов ассистентов
        item = Object.assign({}, item, { href: '/static/start-crm.html', aliases: ['/static/crm.html', '/static/crm-contact.html'] });
      }
      if (item.id === 'crm-nav-item' && biz && biz.niche && mods.crm === false) {
        // CRM-воронка не нужна (салон, магазин): раздел остаётся как «Клиенты» — база с историей
        item = Object.assign({}, item, { label: 'sidebar.clients', def: 'Клиенты' });
      }
      out.push(item);
      if (item.id === 'inbox-nav-item' || (item.id === 'dashboard-nav-item' && !menu.some(function (m) { return m.id === 'inbox-nav-item'; }))) {
        // biz.pages — все включённые модули; старый кэш без pages — по вкладкам телефона
        var pages = Object.assign({}, (biz && biz.pages) || {});
        (biz && biz.tabs || []).forEach(function (t) {
          if ((t.id === 'booking' || t.id === 'orders') && !(t.id in pages)) pages[t.id] = t.href;
        });
        if (pages.booking) out.push({ href: pages.booking, lucide: 'calendar', label: 'sidebar.booking', def: 'Записи', id: 'booking-nav-item' });
        if (pages.orders) out.push({ href: pages.orders, lucide: 'shopping-cart', label: 'sidebar.orders', def: 'Заказы', id: 'orders-nav-item' });
      }
    });
    return out;
  }
  // Сотрудник кабинета (вход по своей почте): /api/users/me → staff = {name, role_title, permissions, menu, home}.
  // Кэш привязан к токену: другой вход на этом телефоне не увидит чужое меню.
  var STAFF_KEY = 'vf_staff';
  function tokenTail() { var t = token() || ''; return t.slice(-24); }
  function cachedStaff() {
    try {
      var c = JSON.parse(localStorage.getItem(STAFF_KEY) || 'null');
      return c && c.t === tokenTail() ? (c.staff || null) : null;
    } catch (e) { return null; }
  }
  function staffMenu(staff) {
    var out = [{ section: 'sidebar.section_work', def: 'Работа' }];
    (staff.menu || []).forEach(function (m) {
      var lucide = { inbox: 'inbox', booking: 'calendar', orders: 'shopping-cart', crm: 'contact-round', catalog: 'package', analytics: 'chart-column' }[m.id] || m.icon;
      var item = { href: m.href, lucide: lucide, label: 'staff.' + m.id, def: m.label, id: m.id + '-nav-item' };
      if (m.id === 'crm') item.aliases = ['/static/crm.html', '/static/crm-contact.html'];
      if (m.id === 'analytics') item.aliases = ['/static/start-analytics.html'];
      if (m.id === 'catalog') item.label = '';
      out.push(item);
    });
    out.push({ section: 'sidebar.section_account', def: 'Аккаунт', account: true });
    out.push({ href: '#password', lucide: 'key-round', label: 'staff.password', def: 'Сменить пароль', id: 'staff-password-item', action: 'password' });
    return out;
  }
  function currentMenu() {
    var staff = cachedStaff();
    if (staff) return staffMenu(staff);
    return adaptMenu(cachedMode() === 'reactive' ? START_MENU : MENU);
  }
  // Страницы, открытые сотруднику; остальные (дашборд с кошельком, настройки, бот…) → его первая страница
  function staffGuard() {
    var staff = cachedStaff();
    if (!staff || window.top !== window.self) return false;
    var p = currentPath();
    var ok = (staff.menu || []).some(function (m) {
      var hp = m.href.split('#')[0];
      return hp === p || (m.id === 'crm' && (p === '/static/crm.html' || p === '/static/crm-contact.html'))
        || (m.id === 'analytics' && p === '/static/start-analytics.html');
    });
    if (ok) return false;
    location.replace(staff.home || '/static/inbox.html');
    return true;
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
    a.innerHTML = icon(item.lucide) + '<span>' + (item.label ? T(item.label, item.def) : item.def) + '</span>' +
      (item.feature ? icon('lock', 'lock') : '');
    if (item.action === 'password') {
      a.classList.remove('active');
      a.setAttribute('data-no-prefetch', '1');
      a.addEventListener('click', function (e) { e.preventDefault(); openStaffPassword(); });
    }
    return a;
  }

  function renderNav() {
    var nav = document.getElementById('sidebar-nav') || document.querySelector('.sidebar-nav');
    if (!nav) return null;
    nav.innerHTML = '';
    var mode = cachedMode();
    currentMenu().forEach(function (item) {
      if (item.mode && item.mode !== mode) return;   // пункт только для режима агента (инбокс — у Profi)
      if (item.modes && item.modes.indexOf(mode) < 0) return;
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
    if (cachedStaff()) return;   // кошелёк и оплата — только владелец
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
    if (!token() || cachedStaff()) return Promise.resolve(null);
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
    // Админ в кабинете клиента: «Выйти» = вернуться в свою админку, а не выйти совсем
    if (window.VF && window.VF.impersonation && window.VF.impersonation()) { window.VF.returnToAdmin(); return; }
    try { localStorage.removeItem('auth_token'); localStorage.removeItem(STAFF_KEY); } catch (e) { /* ignore */ }
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
      renderVoiceFab();
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
      // Меню слева и нижняя панель телефона: на телефоне «moderate» срабатывает уже при касании
      // пальцем (pointerdown) — страница готовится, пока палец ещё на экране.
      var sel = { selector_matches: '#sidebar-nav a[href^="/static/"]:not([data-no-prefetch]), #vf-tabbar a[href^="/static/"]' };
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
  // Profi (решение владельца 18.11.2026): Чаты · Звонки · Агент · Клиенты · Ещё
  var PROFI_TABS = [
    { id: 'inbox', href: '/static/inbox.html', icon: 'inbox', label: 'Чаты', ky: 'Чаттар' },
    { id: 'calls', href: '/static/conversations.html', icon: 'phone', label: 'Звонки', ky: 'Чалуулар' },
    { id: 'agent', href: '/static/agent.html', icon: 'headset', label: 'Агент', ky: 'Агент' },
    { id: 'clients', href: '/static/start-crm.html', icon: 'users', label: 'Клиенты', ky: 'Кардарлар' },
    { id: 'more', icon: 'menu', label: 'Ещё', ky: 'Дагы' }
  ];
  function activeTab() {
    var p = currentPath(), h = (location.hash || '').replace('#', '');
    var tabs = currentTabs();
    for (var i = 0; i < tabs.length; i++) {
      var t = tabs[i];
      if (!t.href) continue;
      var tp = t.href.split('#')[0], th = t.href.split('#')[1] || '';
      if (tp !== p) continue;
      if (th) { if (h === th || (th === 'catalog' && h === 'products')) return t.id; continue; }
      if (p === '/static/start-bot.html' && (h === 'catalog' || h === 'products') && tabs.some(function (x) { return x.href === '/static/start-bot.html#catalog'; })) continue;
      return t.id;
    }
    if (p === '/static/crm-contact.html' || p === '/static/start-crm.html' || p === '/static/crm.html') return 'clients';
    if (p === '/static/agent-knowledge.html' || p === '/static/agent.html') return tabs.some(function (t) { return t.id === 'agent'; }) ? 'agent' : 'more';
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
  function currentTabs() {
    var staff = cachedStaff();
    if (staff) {
      return (staff.menu || []).slice(0, 4).map(function (m) {
        return { id: m.id, href: m.href, icon: m.icon, label: m.label, ky: m.ky || m.label };
      }).concat([{ id: 'more', icon: 'menu', label: 'Ещё', ky: 'Дагы' }]);
    }
    var biz = cachedBusiness();
    if (biz && biz.tabs && biz.tabs.length) return biz.tabs;
    return cachedMode() === 'full' ? PROFI_TABS : TABS;
  }
  function renderTabBar() {
    var mode = cachedMode();
    if (mode !== 'reactive' && mode !== 'full' && !cachedStaff()) return;
    if (window.top !== window.self) return;           // инбокс во фрейме у Profi — без своей панели
    if (document.getElementById('vf-tabbar')) { markTabs(); return; }
    if (!document.getElementById('vf-tabbar-style')) {
      var st = document.createElement('style'); st.id = 'vf-tabbar-style'; st.textContent = TABBAR_STYLE;
      document.head.appendChild(st);
    }
    var ky = lang() === 'ky';
    var bar = document.createElement('nav');
    bar.id = 'vf-tabbar'; bar.className = 'vf-tabbar'; bar.setAttribute('aria-label', 'Меню');
    bar.innerHTML = currentTabs().map(function (t) {
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
  // Раньше здесь была заглушка «CRM удобнее на компьютере» (13.11.2026). С 18.11.2026 у CRM есть вид для
  // телефона (start/crm.js → drawClientsPhone), заглушка не нужна. Имя функции оставлено для вызовов.
  function phoneCrmNote() { /* пусто */ }

  // ---------------------------------------------------------------------
  // Приложение на рабочем столе (13.11.2026): manifest.json + /static/sw.js.
  // Android (Chrome) — системное окно установки; iPhone — подсказка «Поделиться → На экран Домой».
  // Кнопка «Установить приложение» — в меню на телефоне; на дашборде — плашка раз в 14 дней.
  // ---------------------------------------------------------------------
  var installEvt = null;
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault(); installEvt = e;
    var b = document.getElementById('vf-install-btn'); if (b) b.hidden = false;
  });
  window.addEventListener('appinstalled', function () {
    installEvt = null;
    ['vf-install-btn', 'vf-install-bar'].forEach(function (id) { var el = document.getElementById(id); if (el) el.remove(); });
  });
  function standalone() {
    try { return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true; } catch (e) { return false; }
  }
  function isIOS() { return /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1); }
  function inAppBrowser() { return /Telegram|Instagram|FBAN|FBAV|WhatsApp/i.test(navigator.userAgent); }
  function registerSW() {
    if (!('serviceWorker' in navigator) || location.protocol !== 'https:' && location.hostname !== '127.0.0.1' && location.hostname !== 'localhost') return;
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/static/sw.js', { scope: '/static/' }).catch(function () { /* ignore */ });
    });
  }
  var INSTALL_STYLE = [
    '.vf-install-bar{display:flex;align-items:center;gap:12px;margin:0 0 14px;padding:12px 12px 12px 14px;border-radius:16px;',
    'background:linear-gradient(135deg,#2a5ce8,#4f46e5);color:#fff;box-shadow:0 8px 24px -12px rgba(42,92,232,.6)}',
    '.vf-install-bar img{width:40px;height:40px;border-radius:11px;background:#fff;flex:none}',
    '.vf-install-bar .t{flex:1;min-width:0;font-size:13px;line-height:1.35}',
    '.vf-install-bar .t b{display:block;font-size:14.5px;margin-bottom:1px}',
    '.vf-install-bar .go{flex:none;height:38px;padding:0 14px;border-radius:10px;border:0;background:#fff;color:#2a5ce8;font-weight:700;font-size:13.5px;cursor:pointer}',
    '.vf-install-bar .x{flex:none;width:30px;height:30px;border:0;background:none;color:rgba(255,255,255,.8);font-size:20px;line-height:1;cursor:pointer}',
    '.vf-install-tip{position:fixed;inset:0;z-index:1300;background:rgba(15,23,42,.55);display:flex;align-items:flex-end;justify-content:center}',
    '.vf-install-tip .sh{width:100%;max-width:480px;background:#fff;border-radius:20px 20px 0 0;padding:20px 20px calc(20px + env(safe-area-inset-bottom,0px));color:#0e1729}',
    '.vf-install-tip h3{margin:0 0 6px;font-size:18px}',
    '.vf-install-tip p{margin:0 0 12px;color:#4b5a70;font-size:14px;line-height:1.5}',
    '.vf-install-tip ol{margin:0 0 16px;padding-left:20px;font-size:15px;line-height:1.7}',
    '.vf-install-tip .ok{width:100%;height:46px;border:0;border-radius:12px;background:#2a5ce8;color:#fff;font-weight:700;font-size:15px}'
  ].join('');
  function installStyle() {
    if (document.getElementById('vf-install-style')) return;
    var st = document.createElement('style'); st.id = 'vf-install-style'; st.textContent = INSTALL_STYLE;
    document.head.appendChild(st);
  }
  function installTip() {
    installStyle();
    var ios = isIOS(), inapp = inAppBrowser();
    var steps = inapp
      ? '<p>Это окно открыто внутри мессенджера — отсюда приложение не установить.</p><ol><li>Нажмите «⋯» или «Поделиться» вверху</li><li>Выберите «Открыть в ' + (ios ? 'Safari' : 'браузере') + '»</li><li>Там нажмите «Установить приложение» ещё раз</li></ol>'
      : ios
        ? '<ol><li>Нажмите <b>«Поделиться»</b> ⬆︎ внизу Safari</li><li>Прокрутите и выберите <b>«На экран „Домой“»</b></li><li>Нажмите <b>«Добавить»</b></li></ol>'
        : '<ol><li>Нажмите меню браузера <b>⋮</b> вверху справа</li><li>Выберите <b>«Установить приложение»</b> или <b>«Добавить на главный экран»</b></li></ol>';
    var t = document.createElement('div');
    t.className = 'vf-install-tip';
    t.innerHTML = '<div class="sh"><h3>Voksy AI на рабочем столе</h3><p>Значок появится рядом с другими приложениями: Инбокс и Каталог открываются одним нажатием, без адресной строки.</p>' + steps + '<button type="button" class="ok">Понятно</button></div>';
    document.body.appendChild(t);
    t.addEventListener('click', function (e) { if (e.target === t || e.target.classList.contains('ok')) t.remove(); });
  }
  function installApp() {
    if (installEvt) {
      var ev = installEvt; installEvt = null;
      ev.prompt();
      if (ev.userChoice) ev.userChoice.then(function (r) { if (r && r.outcome === 'accepted') { var b = document.getElementById('vf-install-bar'); if (b) b.remove(); } });
      return;
    }
    installTip();
  }
  function renderInstallUI() {
    if (standalone() || !isPhone()) return;
    var foot = document.querySelector('.vf-sidebar .sidebar-footer');
    if (foot && !document.getElementById('vf-install-btn')) {
      var b = document.createElement('button');
      b.type = 'button'; b.id = 'vf-install-btn'; b.className = 'btn btn-ghost';
      b.style.cssText = 'width:100%;justify-content:flex-start';
      b.innerHTML = icon('download') + ' <span>Установить приложение</span>';
      b.addEventListener('click', installApp);
      foot.insertBefore(b, foot.firstChild);
    }
    if (currentPath() !== '/static/dashboard.html' || document.getElementById('vf-install-bar')) return;
    try { var d = +localStorage.getItem('vf_install_dismissed') || 0; if (Date.now() - d < 14 * 864e5) return; } catch (e) { /* ignore */ }
    var host = document.querySelector('.vf-content') || document.getElementById('content-container'); if (!host) return;
    installStyle();
    var bar = document.createElement('div');
    bar.id = 'vf-install-bar'; bar.className = 'vf-install-bar';
    bar.innerHTML = '<img src="/static/apple-touch-icon.png" alt=""><div class="t"><b>Установите приложение</b>Инбокс и каталог одним нажатием</div>' +
      '<button type="button" class="go">Установить</button><button type="button" class="x" aria-label="Закрыть">×</button>';
    host.insertBefore(bar, host.firstChild);
    bar.querySelector('.go').addEventListener('click', installApp);
    bar.querySelector('.x').addEventListener('click', function () {
      try { localStorage.setItem('vf_install_dismissed', String(Date.now())); } catch (e) { /* ignore */ }
      bar.remove();
    });
  }
  registerSW();
  window.VoksyInstall = { open: installApp, standalone: standalone };

  // ---------------------------------------------------------------------
  // «Спросить голосом» (ветка 1811-niche-admin-speed): голосовой помощник владельца на любой странице
  // кабинета для Старта и Profi — кнопка справа внизу (на телефоне — над нижней панелью). Скрипт и стили
  // окна (/static/agent/voice-chat.js + /static/css/voice-live.css) грузятся только по нажатию.
  // На странице агента Profi своя кнопка «Поговорить» в чате, в iframe и на самой голосовой странице — нет.
  // ---------------------------------------------------------------------
  var VOICE_FAB_STYLE = [
    '.vf-voice-fab{position:fixed;right:22px;bottom:22px;z-index:880;display:inline-flex;align-items:center;gap:9px;height:52px;',
    'padding:0 20px 0 16px;border:0;border-radius:999px;cursor:pointer;color:#fff;font:650 14.5px/1 Inter,system-ui,sans-serif;',
    'background:linear-gradient(135deg,#3b6dff 0%,#6d3ff0 100%);box-shadow:0 12px 30px -8px rgba(76,92,255,.6),0 2px 6px rgba(15,23,42,.15);',
    'transition:transform .15s,box-shadow .15s;-webkit-tap-highlight-color:transparent}',
    '.vf-voice-fab:hover{transform:translateY(-2px);box-shadow:0 16px 36px -8px rgba(76,92,255,.7),0 2px 6px rgba(15,23,42,.15)}',
    '.vf-voice-fab:active{transform:scale(.96)}',
    '.vf-voice-fab:focus-visible{outline:3px solid #a5b8ff;outline-offset:3px}',
    '.vf-voice-fab .ic{width:21px;height:21px}',
    '.vf-voice-fab .vf-vf-wave{display:inline-flex;align-items:center;gap:2px;height:18px}',
    '.vf-voice-fab .vf-vf-wave i{width:3px;border-radius:2px;background:#fff;animation:vf-vf-w 1.2s ease-in-out infinite}',
    '.vf-voice-fab .vf-vf-wave i:nth-child(1){height:8px}.vf-voice-fab .vf-vf-wave i:nth-child(2){height:16px;animation-delay:.15s}',
    '.vf-voice-fab .vf-vf-wave i:nth-child(3){height:11px;animation-delay:.3s}.vf-voice-fab .vf-vf-wave i:nth-child(4){height:6px;animation-delay:.45s}',
    '@keyframes vf-vf-w{0%,100%{transform:scaleY(.55)}50%{transform:scaleY(1)}}',
    '.vf-voice-fab.busy{opacity:.8;cursor:wait}',
    '@media (max-width:760px){.vf-voice-fab{right:14px;bottom:calc(76px + env(safe-area-inset-bottom,0px));height:54px;width:54px;padding:0;',
    'justify-content:center}.vf-voice-fab span.t{display:none}',
    'body:has(.bk-fab) .vf-voice-fab,body:has(.od-fab) .vf-voice-fab{bottom:calc(146px + env(safe-area-inset-bottom,0px))}}',
    'body:not(.vf-has-tabbar) .vf-voice-fab{bottom:calc(18px + env(safe-area-inset-bottom,0px))}',
    'body.vv-open .vf-voice-fab,body.modal-open .vf-voice-fab{display:none}',
    '@media (prefers-reduced-motion:reduce){.vf-voice-fab .vf-vf-wave i{animation:none}}'
  ].join('');
  var VOICE_SKIP = ['/static/agent.html', '/static/voice-live.html', '/static/admin.html', '/static/inbox.html'];
  function renderVoiceFab() {
    var mode = cachedMode();
    var old = document.getElementById('vf-voice-fab');
    var allowed = (mode === 'reactive' || mode === 'full') && window.top === window.self && VOICE_SKIP.indexOf(currentPath()) < 0
      && !!token() && !cachedStaff();   // голосом помощник делает действия от имени владельца — сотруднику нет
    if (!allowed) { if (old) old.remove(); return; }
    if (old || !document.body) return;
    if (!document.getElementById('vf-voice-fab-style')) {
      var st = document.createElement('style'); st.id = 'vf-voice-fab-style'; st.textContent = VOICE_FAB_STYLE;
      document.head.appendChild(st);
    }
    var ky = lang() === 'ky';
    var b = document.createElement('button');
    b.type = 'button'; b.id = 'vf-voice-fab'; b.className = 'vf-voice-fab';
    b.setAttribute('aria-label', ky ? 'Агент менен сүйлөшүү' : 'Поговорить с агентом голосом');
    b.title = ky ? 'Агенттен үн менен сураңыз: отчёт, ким жазды, ким жазылды' : 'Спросите агента голосом: отчёт, кто писал, кто записан';
    b.innerHTML = '<span class="vf-vf-wave" aria-hidden="true"><i></i><i></i><i></i><i></i></span><span class="t">' +
      (ky ? 'Үн менен сураңыз' : 'Спросить голосом') + '</span>';
    b.addEventListener('click', openOwnerVoice);
    document.body.appendChild(b);
  }
  function loadVoiceUi(cb) {
    if (window.VoksyVoice) { cb(); return; }
    if (!document.getElementById('vf-voice-css')) {
      var l = document.createElement('link'); l.id = 'vf-voice-css'; l.rel = 'stylesheet'; l.href = '/static/css/voice-live.css';
      document.head.appendChild(l);
    }
    var sc = document.createElement('script');
    sc.src = '/static/agent/voice-chat.js';
    sc.onload = cb;
    sc.onerror = function () { var b = document.getElementById('vf-voice-fab'); if (b) b.classList.remove('busy'); };
    document.head.appendChild(sc);
  }
  function openOwnerVoice() {
    var b = document.getElementById('vf-voice-fab');
    if (b) b.classList.add('busy');
    loadVoiceUi(function () {
      if (b) b.classList.remove('busy');
      if (!window.VoksyVoice || window.VoksyVoice.isOpen()) return;
      window.VoksyVoice.open({
        agentId: null, token: token, title: lang() === 'ky' ? 'Үн жардамчы' : 'Голосовой помощник',
        onCredits: function () { location.href = cachedMode() === 'full' ? '/static/agent.html' : '/static/start-bot.html#credits'; }
      });
    });
  }
  window.VoksyOwnerVoice = { open: openOwnerVoice };

  // ---------------------------------------------------------------------
  // Сотрудник: кто вошёл (над «Выйти») и смена своего пароля
  // ---------------------------------------------------------------------
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }
  var STAFF_STYLE = [
    '.vf-staff{display:flex;align-items:center;gap:10px;margin:0 12px 10px;padding:10px 12px;border:1px solid var(--vf-border,#e2e8f0);',
    'border-radius:12px;background:var(--vf-surface-2,#f8fafc)}',
    '.vf-staff-ava{width:34px;height:34px;border-radius:10px;flex:none;display:grid;place-items:center;background:var(--vf-accent,#2a5ce8);',
    'color:#fff;font:700 13px/1 Inter,sans-serif}',
    '.vf-staff-t{min-width:0;display:flex;flex-direction:column;gap:1px}',
    '.vf-staff-t b{font-size:13.5px;color:var(--vf-text,#0e1729);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.vf-staff-t span{font-size:12px;color:var(--vf-text-3,#63738b);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
  ].join('');
  function renderStaffBadge() {
    var staff = cachedStaff();
    var old = document.getElementById('vf-staff-badge');
    if (!staff) { if (old) old.remove(); return; }
    var aside = document.querySelector('.sidebar');
    if (old || !aside) return;
    if (!document.getElementById('vf-staff-style')) {
      var st = document.createElement('style'); st.id = 'vf-staff-style'; st.textContent = STAFF_STYLE; document.head.appendChild(st);
    }
    var parts = String(staff.name || '?').trim().split(/\s+/);
    var ini = ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
    var el = document.createElement('div');
    el.id = 'vf-staff-badge'; el.className = 'vf-staff';
    el.innerHTML = '<div class="vf-staff-ava">' + esc(ini) + '</div><div class="vf-staff-t"><b>' + esc(staff.name) + '</b><span>' +
      esc((staff.role_title || 'Сотрудник') + (staff.company ? ' · ' + staff.company : '')) + '</span></div>';
    var footer = aside.querySelector('.sidebar-footer');
    if (footer) aside.insertBefore(el, footer); else aside.appendChild(el);
  }
  function openStaffPassword() {
    if (!window.VF || document.getElementById('vf-staff-pw')) return;
    var bd = document.createElement('div');
    bd.className = 'vf-backdrop'; bd.id = 'vf-staff-pw';
    bd.innerHTML = '<div class="vf-dialog vf-dialog-sm" role="dialog" aria-modal="true">' +
      '<div class="vf-dialog-head"><h3>Сменить пароль</h3></div>' +
      '<form class="vf-dialog-body" id="vf-spw-form">' +
      '<div class="field"><label class="label" for="vf-spw-old">Текущий пароль</label><input class="input" id="vf-spw-old" type="password" autocomplete="current-password" required></div>' +
      '<div class="field"><label class="label" for="vf-spw-new">Новый пароль</label><input class="input" id="vf-spw-new" type="password" autocomplete="new-password" minlength="6" required>' +
      '<div style="font-size:12px;color:var(--vf-text-4,#93a2b8);margin-top:4px">Не короче 6 символов. На других устройствах нужно будет войти заново.</div></div>' +
      '<div id="vf-spw-err" style="color:var(--vf-danger,#dc2626);font-size:13px" hidden></div></form>' +
      '<div class="vf-dialog-foot"><button type="button" class="btn" data-x>Отмена</button><button type="button" class="btn btn-primary" id="vf-spw-save">Сохранить</button></div></div>';
    document.body.appendChild(bd);
    var m = window.VF.modal(bd, { onClose: function () { setTimeout(function () { bd.remove(); }, 260); } });
    bd.querySelector('[data-x]').addEventListener('click', function () { m.close('cancel'); });
    function err(t) { var e = bd.querySelector('#vf-spw-err'); e.textContent = t || ''; e.hidden = !t; }
    function save(ev) {
      if (ev) ev.preventDefault();
      var a = bd.querySelector('#vf-spw-old').value, b = bd.querySelector('#vf-spw-new').value;
      if (!a) { err('Введите текущий пароль'); return; }
      if ((b || '').trim().length < 6) { err('Новый пароль — не короче 6 символов'); return; }
      var btn = bd.querySelector('#vf-spw-save'); btn.disabled = true;
      apiFetch('/team/me/password', { method: 'POST', body: JSON.stringify({ current_password: a, new_password: b }) })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          btn.disabled = false;
          if (!res.ok || !res.d.token) {
            var x = res.d.detail || res.d.message;
            err((x && x.message) || x || 'Не удалось сменить пароль');
            return;
          }
          var staff = cachedStaff();
          try {
            localStorage.setItem('auth_token', res.d.token);
            localStorage.setItem(STAFF_KEY, JSON.stringify({ t: res.d.token.slice(-24), staff: staff }));
          } catch (e) { /* ignore */ }
          m.close('done');
          window.VF.toast('Пароль изменён', { type: 'success' });
        });
    }
    bd.querySelector('#vf-spw-form').addEventListener('submit', save);
    bd.querySelector('#vf-spw-save').addEventListener('click', save);
    m.open();
  }
  function applyStaff(staff) {
    var prev = JSON.stringify(cachedStaff());
    try { localStorage.setItem(STAFF_KEY, JSON.stringify({ t: tokenTail(), staff: staff || null })); } catch (e) { /* ignore */ }
    if (prev === JSON.stringify(staff || null)) return false;
    if (staffGuard()) return true;
    var nav = renderNav();
    var bar = document.getElementById('vf-tabbar');
    if (bar) { bar.remove(); document.body.classList.remove('vf-has-tabbar'); }
    renderTabBar(); renderVoiceFab(); renderStaffBadge();
    var w = document.getElementById('vf-wallet-card');
    if (staff && w) w.remove();
    else if (!staff && nav) { renderWalletCard(nav); refreshWallet(); }
    return false;
  }
  window.VoksyStaff = { current: cachedStaff, changePassword: openStaffPassword };

  function init() {
    if (staffGuard()) return;
    if (redirectStartPages()) return;
    var nav = renderNav();
    if (document.body) { renderTabBar(); phoneCrmNote(); renderInstallUI(); renderVoiceFab(); }
    else document.addEventListener('DOMContentLoaded', function () { renderTabBar(); phoneCrmNote(); renderInstallUI(); renderVoiceFab(); });
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
    renderStaffBadge();
    apiFetch('/users/me').then(function (r) { return r.ok ? r.json() : null; }).then(function (user) {
      if (!user) return;
      if (applyStaff(user.staff || null)) return;   // сотрудник на чужой странице — ушёл на свою
      var admin = !user.staff && (!!user.is_admin || ADMIN_EMAILS.indexOf(user.email) !== -1);
      try { sessionStorage.setItem('vf_is_admin', admin ? '1' : '0'); } catch (e) { /* ignore */ }
      applyAgentMode(user.agent_mode || '');
      applyBusiness(user.business || null);
      if (admin) injectAdmin(document.getElementById('sidebar-nav') || nav);
      document.dispatchEvent(new CustomEvent('vf:user', { detail: user }));
      maybeBusinessSetup(user, admin);
    }).catch(function () { /* ignore */ });
  }

  function applyBusiness(biz) {
    var prev = localStorage.getItem(BIZ_KEY) || 'null';
    var next = JSON.stringify(biz || null);
    if (prev === next) return;
    try { localStorage.setItem(BIZ_KEY, next); } catch (e) { /* ignore */ }
    var nav = renderNav();
    if (nav) { try { if (sessionStorage.getItem('vf_is_admin') === '1') injectAdmin(nav); } catch (e) { /* ignore */ } }
    var bar = document.getElementById('vf-tabbar');
    if (bar) { bar.remove(); document.body.classList.remove('vf-has-tabbar'); }
    renderTabBar();
  }
  document.addEventListener('vf:business', function (e) { applyBusiness(e.detail || null); });

  // «Чем занимается ваш бизнес?» — первым экраном после регистрации (и один раз — старым клиентам)
  function maybeBusinessSetup(user, admin) {
    var biz = user.business;
    if (!biz || !biz.needs_setup || admin || user.impersonated || user.staff) return;
    if (window.top !== window.self || currentPath() === '/static/admin.html') return;
    var go = function () { if (window.VoksyBusinessSetup) window.VoksyBusinessSetup.open({ me: user }); };
    if (window.VoksyBusinessSetup) { go(); return; }
    var sc = document.createElement('script');
    sc.src = '/static/js/business-setup.js';
    sc.onload = go;
    document.head.appendChild(sc);
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
