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
    { href: '/static/crm.html', lucide: 'contact-round', label: 'sidebar.crm', def: 'Клиенты', id: 'crm-nav-item', feature: 'crm',
      aliases: ['/static/crm-contact.html'] },
    // Аналитика (ветка 1811-niche-admin-speed) — для всех тарифов: без бота показывает звонки и разговоры ассистентов
    { href: '/static/analytics.html', lucide: 'chart-column', label: 'sidebar.analytics', def: 'Аналитика', id: 'analytics-nav-item' },
    // Финансы (19.11.2026): выручка, расходы (аренда, зарплаты мастеров), прибыль — у ниш с записями или заказами
    { href: '/static/finance.html', lucide: 'banknote', label: 'sidebar.finance', def: 'Финансы', id: 'finance-nav-item', modes: ['full', 'reactive'], money: true },
    // ИИ-управляющий (Voksy Beauty 1.6б): утренний отчёт, предложения на одобрение, сценарии — у ниш с онлайн-записью
    { href: '/static/manager.html', lucide: 'sparkles', label: 'sidebar.manager', def: 'ИИ-управляющий', id: 'manager-nav-item', modes: ['full', 'reactive'], booking: true },
    // Журнал действий (Voksy Beauty 1.7): кто перенёс, отменил, поменял цену, что сделал бот — у ниш с онлайн-записью
    { href: '/static/activity.html', lucide: 'history', label: 'sidebar.activity', def: 'Журнал действий', id: 'activity-nav-item', modes: ['full', 'reactive'], booking: true },
    // Автоматизации (Voksy Beauty 2.2): напоминания, подтверждение, «спасибо», ДР, повтор, «давно не были» — тексты и журнал отправок
    { href: '/static/automations.html', lucide: 'send', label: 'sidebar.automations', def: 'Автоматизации', id: 'automations-nav-item', modes: ['full', 'reactive'], booking: true },
    // Отзывы (Voksy Beauty 2.3): рейтинг салона и мастеров, проверка оценок 1–3, ответ салона
    { href: '/static/reviews.html', lucide: 'star', label: 'sidebar.reviews', def: 'Отзывы', id: 'reviews-nav-item', modes: ['full', 'reactive'], booking: true },

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
    { href: '/static/start-crm.html', lucide: 'contact-round', label: 'sidebar.crm', def: 'Клиенты', id: 'crm-nav-item' },
    { href: '/static/analytics.html', lucide: 'chart-column', label: 'sidebar.analytics', def: 'Аналитика', id: 'analytics-nav-item',
      aliases: ['/static/start-analytics.html'] },
    { href: '/static/finance.html', lucide: 'banknote', label: 'sidebar.finance', def: 'Финансы', id: 'finance-nav-item', money: true },
    { href: '/static/manager.html', lucide: 'sparkles', label: 'sidebar.manager', def: 'ИИ-управляющий', id: 'manager-nav-item', booking: true },
    { href: '/static/activity.html', lucide: 'history', label: 'sidebar.activity', def: 'Журнал действий', id: 'activity-nav-item', booking: true },
    { href: '/static/automations.html', lucide: 'send', label: 'sidebar.automations', def: 'Автоматизации', id: 'automations-nav-item', booking: true },
    { href: '/static/reviews.html', lucide: 'star', label: 'sidebar.reviews', def: 'Отзывы', id: 'reviews-nav-item', booking: true },
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
      // Финансы — где есть выручка: записи или заказы (у ниши без них раздел пустой)
      if (item.money && biz && biz.niche && !mods.booking && !mods.orders) return;
      // ИИ-управляющий — где есть журнал записи (отчёт по записям, сценарии «вернуть / окна / повтор»)
      if (item.booking && !(biz && mods.booking)) return;
      // Салону (Voksy Beauty) тариф Profi не продаётся — пункт с замком «Profi» не показываем
      if (item.profi && biz && biz.beauty && !profi) return;
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
  // Вход сотрудника виден по самому токену (claim staff) — ещё до ответа /api/users/me
  function tokenIsStaff() {
    try {
      var p = (token() || '').split('.')[1];
      if (!p) return false;
      return !!JSON.parse(atob(p.replace(/-/g, '+').replace(/_/g, '/'))).staff;
    } catch (e) { return false; }
  }
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
    '.vf-topup-actions button[disabled]{opacity:.6;cursor:default}',
    // Телефон: шторка снизу, суммы — крупные плитки, кнопка оплаты во всю ширину
    '@media (max-width:760px){.vf-modal-overlay{align-items:flex-end;padding:0}',
    '.vf-topup{max-width:none;border-radius:20px 20px 0 0;padding:22px 16px calc(14px + env(safe-area-inset-bottom,0px));max-height:92dvh;overflow-y:auto;animation:vf-sheet-up .22s ease-out;position:relative}',
    '.vf-topup::before{content:"";position:absolute;top:7px;left:50%;width:40px;height:4px;margin-left:-20px;border-radius:4px;background:var(--vf-border-strong,#cbd5e1)}',
    '.vf-topup-chips{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}',
    '.vf-topup-chip{height:48px;font-size:15px;font-weight:600;border-radius:12px}',
    '.vf-topup input{height:50px;font-size:18px;font-weight:600}',
    '.vf-topup-actions{flex-direction:column-reverse}.vf-topup-actions button{height:50px;font-size:16px;width:100%}}',
    '@keyframes vf-sheet-up{from{transform:translateY(40px);opacity:.4}to{transform:none;opacity:1}}'
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
    // Стили окна кладёт карточка кошелька в боковом меню; на странице без него (или пока её
    // нет) окно на телефоне открывалось без вида шторки — кладём сами
    if (!document.getElementById('vf-wallet-style')) {
      var st = document.createElement('style'); st.id = 'vf-wallet-style'; st.textContent = WALLET_STYLE; document.head.appendChild(st);
    }
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
  // админа и уводим на экран входа (/static/login.html): в приложении сайт не открывается.
  // ---------------------------------------------------------------------
  function logout() {
    // Админ в кабинете клиента: «Выйти» = вернуться в свою админку, а не выйти совсем
    if (window.VF && window.VF.impersonation && window.VF.impersonation()) { window.VF.returnToAdmin(); return; }
    try { localStorage.removeItem('auth_token'); localStorage.removeItem(STAFF_KEY); } catch (e) { /* ignore */ }
    try { sessionStorage.removeItem('vf_is_admin'); sessionStorage.removeItem('vf_wallet'); } catch (e) { /* ignore */ }
    window.location.href = '/static/login.html?reason=logout';
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
    // Полоса загрузки при переходе по вкладке (body.vf-nav-pending снимается новой страницей сама)
    'body.vf-nav-pending::before{content:"";position:fixed;top:0;left:0;height:3px;z-index:9999;width:100%;',
    'background:linear-gradient(90deg,transparent,var(--vf-accent,#2a5ce8) 30%,#6d3ff0 70%,transparent);',
    'background-size:50% 100%;background-repeat:no-repeat;animation:vf-navbar 1s linear infinite}',
    '@keyframes vf-navbar{from{background-position:-50% 0}to{background-position:150% 0}}',
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
  // «Ещё» (19.11.2026): раньше кнопка пыталась открыть боковое меню, которого на страницах телефона нет, —
  // нажатие ничего не делало. Теперь своя шторка снизу: тариф, кошелёк, кредиты бота (оплата с телефона),
  // крупная плитка «Отчёты» и все разделы, которых нет в нижней панели.
  var MORE_STYLE = [
    '.vf-more-ov{position:fixed;inset:0;z-index:9500;background:rgba(14,23,41,.5);display:flex;align-items:flex-end;opacity:0;transition:opacity .18s}',
    '.vf-more-ov.show{opacity:1}',
    '.vf-more{width:100%;max-height:90dvh;overflow-y:auto;overscroll-behavior:contain;background:var(--vf-bg,#f6f8fb);border-radius:22px 22px 0 0;',
    'padding:10px 14px calc(16px + env(safe-area-inset-bottom,0px));transform:translateY(30px);transition:transform .22s ease-out;font-family:Inter,system-ui,sans-serif;color:var(--vf-text,#0e1729)}',
    '.vf-more-ov.show .vf-more{transform:none}',
    '.vf-more-grip{width:40px;height:4px;border-radius:4px;background:var(--vf-border-strong,#cbd5e1);margin:0 auto 12px}',
    '.vf-more-head{display:flex;align-items:center;gap:12px;margin:0 2px 14px}',
    '.vf-more-ava{width:44px;height:44px;border-radius:14px;background:linear-gradient(135deg,#2a5ce8,#6d3ff0);color:#fff;display:flex;align-items:center;justify-content:center;font:700 18px/1 Inter,sans-serif;flex:0 0 auto}',
    '.vf-more-who{min-width:0;flex:1}.vf-more-who b{display:block;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.vf-more-who span{font-size:13px;color:var(--vf-text-3,#63738b)}',
    '.vf-more-x{width:36px;height:36px;border-radius:12px;border:0;background:var(--vf-surface,#fff);color:var(--vf-text-3,#63738b);display:flex;align-items:center;justify-content:center;flex:0 0 auto}',
    '.vf-more-x .ic{width:18px;height:18px}',
    '.vf-more-money{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:10px}',
    '.vf-more-m{background:var(--vf-surface,#fff);border:1px solid var(--vf-border,#e2e8f0);border-radius:16px;padding:11px 10px 10px;display:flex;flex-direction:column;gap:3px;min-width:0;text-align:left;color:inherit;font:inherit;cursor:pointer}',
    '.vf-more-m .l{font-size:11.5px;color:var(--vf-text-3,#63738b);display:flex;align-items:center;gap:4px}.vf-more-m .l .ic{width:13px;height:13px}',
    '.vf-more-m .v{font:700 16px/1.2 Inter,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.vf-more-m .v.neg{color:#dc2626}',
    '.vf-more-m .a{margin-top:4px;font-size:12.5px;font-weight:650;color:var(--vf-accent,#2a5ce8)}',
    '.vf-more-rep{display:flex;align-items:center;gap:12px;padding:14px;border-radius:18px;margin-bottom:10px;text-decoration:none;color:#fff;',
    'background:linear-gradient(135deg,#2a5ce8 0%,#4a46e0 60%,#6d3ff0 100%);box-shadow:0 10px 24px -12px rgba(42,92,232,.7)}',
    '.vf-more-rep .ic{width:26px;height:26px;flex:0 0 auto}.vf-more-rep b{display:block;font-size:16px}.vf-more-rep span{font-size:12.5px;opacity:.85}',
    '.vf-more-rep .go{margin-left:auto;width:20px;height:20px;opacity:.9}',
    '.vf-more-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:12px}',
    '.vf-more-it{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;min-height:84px;padding:10px 6px;border-radius:16px;',
    'background:var(--vf-surface,#fff);border:1px solid var(--vf-border,#e2e8f0);text-decoration:none;color:var(--vf-text,#0e1729);font-size:12.5px;font-weight:550;text-align:center;line-height:1.2;position:relative}',
    '.vf-more-it .ic{width:24px;height:24px;color:var(--vf-accent,#2a5ce8)}',
    '.vf-more-it.on{border-color:var(--vf-accent,#2a5ce8);background:var(--vf-accent-soft,#eaf0fe)}',
    '.vf-more-it .lk{position:absolute;top:6px;right:6px;font-size:9.5px;font-weight:700;color:#8a5a00;background:#ffe7a8;border-radius:999px;padding:1px 6px}',
    '.vf-more-foot{display:flex;align-items:center;gap:8px}',
    '.vf-more-foot .vf-more-lang{display:inline-flex;padding:3px;border-radius:999px;background:var(--vf-surface,#fff);border:1px solid var(--vf-border,#e2e8f0)}',
    '.vf-more-foot .vf-more-lang button{border:0;background:none;font:600 13px/1 Inter,sans-serif;padding:9px 14px;border-radius:999px;color:var(--vf-text-3,#63738b)}',
    '.vf-more-foot .vf-more-lang button.on{background:var(--vf-accent-soft,#eaf0fe);color:var(--vf-accent,#2a5ce8)}',
    '.vf-more-out{margin-left:auto;display:inline-flex;align-items:center;gap:6px;height:42px;padding:0 16px;border-radius:12px;border:1px solid var(--vf-border,#e2e8f0);background:var(--vf-surface,#fff);color:#dc2626;font:600 14px/1 Inter,sans-serif}',
    '.vf-more-out .ic{width:16px;height:16px}',
    '.vf-credit-pk{display:flex;align-items:center;gap:12px;width:100%;padding:14px;margin-bottom:8px;border-radius:14px;border:1px solid var(--vf-border,#e2e8f0);background:var(--vf-surface,#fff);color:inherit;font:inherit;text-align:left;cursor:pointer}',
    '.vf-credit-pk b{font-size:16px}.vf-credit-pk span{display:block;font-size:12.5px;color:var(--vf-text-3,#63738b)}',
    '.vf-credit-pk .p{margin-left:auto;font:700 16px/1 Inter,sans-serif;color:var(--vf-accent,#2a5ce8);white-space:nowrap}',
    '.vf-credit-pk[disabled]{opacity:.6}'
  ].join('');
  var moreData = null;   // {me, credits} — последний ответ, чтобы шторка открывалась сразу
  // Название тарифа: короткое для прежних, у Voksy Beauty и индивидуальных — имя из базы
  // (было «Тариф beauty_pro» — код вместо названия)
  function planTitle(code, name) {
    return { start: 'Старт', profi: 'Profi', ai_voice: 'AI Voice', agent: 'Агент', free: 'Пробный' }[code] || name || (code || '');
  }
  function closeMore() {
    var ov = document.getElementById('vf-more');
    if (!ov) return;
    ov.classList.remove('show');
    setTimeout(function () { if (ov.parentNode) ov.parentNode.removeChild(ov); }, 200);
    document.body.style.overflow = '';
    if (history.state && history.state.vfMore) history.back();
  }
  function moreHtml() {
    var ky = lang() === 'ky';
    var staff = cachedStaff();
    var me = (moreData && moreData.me) || {};
    var cr = (moreData && moreData.credits) || {};
    var name = me.company_name || [me.first_name, me.last_name].filter(Boolean).join(' ') || me.email || 'Voksy AI';
    if (staff) name = staff.name || name;
    var sub = (moreData && moreData.sub) || {};
    var code = (sub.subscription_plan && sub.subscription_plan.code) || '';
    var planName = (sub.subscription_plan && sub.subscription_plan.name) || '';
    var days = sub.days_left != null ? sub.days_left : (cr.days_remaining != null ? cr.days_remaining : null);
    var trial = sub.is_trial || me.is_trial || code === 'free';
    var line = staff ? (staff.role_title || (ky ? 'Кызматкер' : 'Сотрудник'))
      : ((trial ? (ky ? 'Сыноо мөөнөтү' : 'Пробный период') : (ky ? 'Тариф ' : 'Тариф ') + planTitle(code, planName)) +
        (days != null ? ' · ' + (ky ? 'калды ' + days + ' күн' : 'осталось ' + days + ' дн.') : ''));
    var tabHrefs = currentTabs().map(function (t) { return (t.href || '').split('#')[0]; });
    var items = [];
    currentMenu().forEach(function (it) {
      if (!it.href || it.section || it.action) return;
      var mode = cachedMode();
      if (it.mode && it.mode !== mode) return;
      if (it.modes && it.modes.indexOf(mode) < 0) return;
      var p = it.href.split('#')[0];
      if (tabHrefs.indexOf(p) >= 0 || p === '/static/analytics.html') return;
      items.push(it);
    });
    // Каталог живёт на странице бота (#catalog): если его нет внизу — отдельная плитка
    if (!staff && cachedMode() === 'reactive' && !currentTabs().some(function (t) { return t.id === 'catalog' || (t.href || '').indexOf('#catalog') >= 0; })) {
      var biz = cachedBusiness() || {};
      items.push({ href: '/static/start-bot.html#catalog', lucide: 'package', def: (biz.catalog && biz.catalog.title) || 'Каталог' });
    }
    // Сначала то, чем пользуются каждый день; настройки и редкое — в конце
    // Profi: каталог живёт в «Знаниях агента»; главной в нижней панели нет — она первой плиткой
    if (!staff && cachedMode() === 'full' && !currentTabs().some(function (t) { return t.id === 'catalog'; })) {
      var bz = cachedBusiness() || {};
      items.push({ href: '/static/agent-knowledge.html#catalog', lucide: 'package', def: (bz.catalog && bz.catalog.title) || 'Каталог' });
    }
    items = items.map(function (it) { return it.id === 'dashboard-nav-item' ? Object.assign({}, it, { label: '', def: ky ? 'Башкы бет' : 'Главная' }) : it; });
    // Салон: «Отзывы» и «Автоматизации» — рядом с клиентами, а не после «Настроек» (шаги 2.2–2.3 Beauty)
    var ORDER = ['dashboard', 'manager', 'finance', 'booking', 'orders', 'catalog', 'crm', 'reviews', 'automations', 'inbox', 'team', 'activity', 'chatbot', 'conversations', 'telephony', 'assistants', 'agent', 'settings', 'admin'];
    function rank(it) {
      var id = (it.id || '').replace('-nav-item', '');
      if ((it.href || '').indexOf('#catalog') >= 0) id = 'catalog';
      if ((it.href || '').indexOf('finance') >= 0) id = 'finance';
      if ((it.href || '').indexOf('admin') >= 0) id = 'admin';
      var i = ORDER.indexOf(id); return i < 0 ? 50 : i;
    }
    items.sort(function (x, y) { return rank(x) - rank(y); });
    var admin = false; try { admin = sessionStorage.getItem('vf_is_admin') === '1'; } catch (e) { /* ignore */ }
    if (admin) items.push({ href: '/static/admin.html', lucide: 'shield-check', label: 'sidebar.admin', def: 'Управление' });
    var showReports = !staff || (staff.menu || []).some(function (m) { return m.id === 'analytics'; });
    var bal = walletState.balance;
    var html = '<div class="vf-more" role="dialog" aria-modal="true" aria-label="' + (ky ? 'Дагы' : 'Ещё') + '">' +
      '<div class="vf-more-grip"></div>' +
      '<div class="vf-more-head"><div class="vf-more-ava">' + esc((name || 'V').trim().charAt(0).toUpperCase()) + '</div>' +
      '<div class="vf-more-who"><b>' + esc(name) + '</b><span>' + esc(line) + '</span></div>' +
      '<button type="button" class="vf-more-x" data-more="close" aria-label="Закрыть">' + icon('x') + '</button></div>';
    if (!staff) {
      html += '<div class="vf-more-money">' +
        '<button type="button" class="vf-more-m" data-more="topup"><span class="l">' + icon('wallet') + (ky ? 'Капчык' : 'Кошелёк') + '</span>' +
        '<span class="v' + (bal < 0 ? ' neg' : '') + '">' + esc(fmtSom(bal)) + '</span><span class="a">' + (ky ? 'Толуктоо' : 'Пополнить') + '</span></button>' +
        '<button type="button" class="vf-more-m" data-more="credits"><span class="l">' + icon('coins') + (ky ? 'Бот кредити' : 'Кредиты бота') + '</span>' +
        '<span class="v">' + (cr.credits_balance != null ? Number(cr.credits_balance).toLocaleString('ru-RU') : '…') + '</span><span class="a">' + (ky ? 'Сатып алуу' : 'Купить') + '</span></button>' +
        '<button type="button" class="vf-more-m" data-more="plans"><span class="l">' + icon('crown') + (ky ? 'Тариф' : 'Тариф') + '</span>' +
        '<span class="v">' + esc(trial ? (ky ? 'Сыноо' : 'Пробный') : planTitle(code, planName) || '—') + '</span><span class="a">' + (ky ? 'Өзгөртүү' : 'Сменить') + '</span></button>' +
        '</div>';
    }
    if (showReports) {
      html += '<a class="vf-more-rep" href="/static/analytics.html">' + icon('chart-column') +
        '<div><b>' + (ky ? 'Отчёттор жана аналитика' : 'Отчёты и аналитика') + '</b><span>' +
        (ky ? 'Киреше, кардарлар, кызматтар, каналдар' : 'Выручка, клиенты, услуги, каналы') + '</span></div>' + icon('chevron-right', 'go') + '</a>';
    }
    html += '<div class="vf-more-grid">' + items.map(function (it) {
      var locked = profiLocked(it);
      var href = locked ? PLANS_URL : it.href;
      var on = isActive(it) ? ' on' : '';
      return '<a class="vf-more-it' + on + '" href="' + esc(href) + '">' + icon(it.lucide || it.icon || 'circle') +
        '<span>' + esc(it.label ? T(it.label, it.def) : it.def) + '</span>' + (locked ? '<span class="lk">Profi</span>' : '') + '</a>';
    }).join('') + '</div>';
    html += '<div class="vf-more-foot"><div class="vf-more-lang">' +
      '<button type="button" data-lang="ru" class="' + (ky ? '' : 'on') + '">RU</button>' +
      '<button type="button" data-lang="ky" class="' + (ky ? 'on' : '') + '">KY</button></div>' +
      '<button type="button" class="vf-more-out" data-more="logout">' + icon('log-out') + (ky ? 'Чыгуу' : 'Выйти') + '</button></div>';
    return html + '</div>';
  }
  function bindMore(ov) {
    ov.addEventListener('click', function (e) {
      if (e.target === ov) { closeMore(); return; }
      var b = e.target.closest('[data-more],[data-lang],a');
      if (!b) return;
      var act = b.getAttribute('data-more');
      if (b.hasAttribute('data-lang')) { if (window.I18N) window.I18N.setLang(b.getAttribute('data-lang')); else { try { localStorage.setItem('vs_lang', b.getAttribute('data-lang')); } catch (x) { /* ignore */ } location.reload(); } return; }
      if (act === 'close') closeMore();
      else if (act === 'topup') { closeMore(); openTopupModal(); }
      else if (act === 'credits') { closeMore(); openCreditsModal(); }
      else if (act === 'plans') { closeMore(); openPlans(); }
      else if (act === 'logout') logout();
      else if (b.tagName === 'A') {
        // Ссылка на эту же страницу с другим якорем (каталог ↔ бот) — без перезагрузки
        var url = b.getAttribute('href'), path = url.split('#')[0];
        if (path !== currentPath()) { document.body.classList.add('vf-nav-pending'); return; }
        if (path === currentPath()) {
          e.preventDefault(); closeMore();
          var h = url.indexOf('#') >= 0 ? url.split('#')[1] : '';
          setTimeout(function () { if (h) location.hash = h; else location.hash = ''; }, 60);
        }
      }
    });
  }
  function openPlans() {
    if (currentPath() === '/static/dashboard.html') {
      if (location.hash === '#plans') history.replaceState(null, '', location.pathname);
      location.hash = 'plans';
    } else location.href = PLANS_URL;
  }
  function refreshMoreData() {
    if (!token()) return Promise.resolve();
    var reqs = [apiFetch('/users/me').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })];
    if (!cachedStaff()) {
      reqs.push(apiFetch('/credits/balance').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }));
      reqs.push(apiFetch('/subscriptions/my-subscription').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }));
      reqs.push(refreshWallet());
    }
    return Promise.all(reqs).then(function (res) {
      var old = moreData || {};
      moreData = { me: res[0] || old.me, credits: res[1] || old.credits, sub: res[2] || old.sub };
      try { sessionStorage.setItem('vf_more', JSON.stringify(moreData)); } catch (e) { /* ignore */ }
    });
  }
  function openMoreMenu() {
    if (!isPhone()) {
      var btn = document.getElementById('sidebar-toggle');
      if (btn) { btn.click(); return; }
      var sb = document.getElementById('sidebar');
      if (sb) { sb.classList.toggle('open'); return; }
    }
    if (document.getElementById('vf-more')) { closeMore(); return; }
    if (!document.getElementById('vf-more-style')) {
      var st = document.createElement('style'); st.id = 'vf-more-style'; st.textContent = MORE_STYLE;
      document.head.appendChild(st);
    }
    if (!moreData) { try { moreData = JSON.parse(sessionStorage.getItem('vf_more') || 'null'); } catch (e) { /* ignore */ } }
    cachedBalanceText();
    var ov = document.createElement('div');
    ov.className = 'vf-more-ov'; ov.id = 'vf-more';
    ov.innerHTML = moreHtml();
    document.body.appendChild(ov);
    document.body.style.overflow = 'hidden';
    bindMore(ov);
    requestAnimationFrame(function () { ov.classList.add('show'); });
    // Кнопка «Назад» телефона закрывает шторку, а не уходит со страницы
    try { history.pushState({ vfMore: 1 }, ''); } catch (e) { /* ignore */ }
    refreshMoreData().then(function () {
      var cur = document.getElementById('vf-more');
      if (cur) { cur.innerHTML = moreHtml(); }
    });
  }
  window.addEventListener('popstate', function () {
    var ov = document.getElementById('vf-more');
    if (ov) { ov.classList.remove('show'); setTimeout(function () { if (ov.parentNode) ov.parentNode.removeChild(ov); }, 200); document.body.style.overflow = ''; }
  });

  // Кредиты бота (переписка, разбор звонков, голосовые) — купить пакет с телефона одним нажатием
  function openCreditsModal() {
    if (document.getElementById('vf-credits-modal')) return;
    if (!document.getElementById('vf-wallet-style')) {
      var st0 = document.createElement('style'); st0.id = 'vf-wallet-style'; st0.textContent = WALLET_STYLE; document.head.appendChild(st0);
    }
    if (!document.getElementById('vf-more-style')) {
      var st = document.createElement('style'); st.id = 'vf-more-style'; st.textContent = MORE_STYLE; document.head.appendChild(st);
    }
    var ky = lang() === 'ky';
    var ov = document.createElement('div');
    ov.className = 'vf-modal-overlay'; ov.id = 'vf-credits-modal';
    var cr = (moreData && moreData.credits) || {};
    ov.innerHTML = '<div class="vf-topup" role="dialog" aria-modal="true"><h3>' + (ky ? 'Бот кредиттери' : 'Кредиты бота') + '</h3>' +
      '<p>' + (ky ? 'Кредиттер ботко жумшалат: жооптор, чалууларды талдоо, кардарлардын үн каттары жана сүрөттөрү.'
        : 'Кредиты тратит бот: ответы клиентам, разбор звонков, голосовые и фото клиентов.') +
      (cr.credits_balance != null ? ' ' + (ky ? 'Калдык: ' : 'Сейчас: ') + '<b>' + Number(cr.credits_balance).toLocaleString('ru-RU') + '</b>.' : '') + '</p>' +
      '<div id="vf-credit-list"><p>' + (ky ? 'Жүктөлүүдө…' : 'Загружаем пакеты…') + '</p></div>' +
      '<div class="vf-topup-actions"><button type="button" data-x="1">' + (ky ? 'Жабуу' : 'Закрыть') + '</button></div></div>';
    document.body.appendChild(ov);
    function close() { ov.remove(); }
    ov.addEventListener('click', function (e) { if (e.target === ov || e.target.getAttribute('data-x')) close(); });
    apiFetch('/credits/packages').then(function (r) { return r.ok ? r.json() : { packages: [] }; }).then(function (d) {
      var list = ov.querySelector('#vf-credit-list');
      var pk = (d && d.packages) || [];
      if (!pk.length) { list.innerHTML = '<p>' + (ky ? 'Пакеттер азыр жок.' : 'Пакетов пока нет — напишите в поддержку.') + '</p>'; return; }
      list.innerHTML = pk.map(function (p) {
        return '<button type="button" class="vf-credit-pk" data-pk="' + esc(p.code) + '"><div><b>' + esc(p.name || p.code) + '</b><span>' +
          Number(p.credits || 0).toLocaleString('ru-RU') + ' ' + (ky ? 'кредит' : 'кредитов') + '</span></div><span class="p">' +
          esc(fmtSom(p.price_rub)) + '</span></button>';
      }).join('');
      Array.prototype.forEach.call(list.querySelectorAll('[data-pk]'), function (b) {
        b.addEventListener('click', function () {
          b.disabled = true;
          apiFetch('/credits/purchase', { method: 'POST', body: JSON.stringify({ package_code: b.getAttribute('data-pk') }) })
            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, d: j }; }); })
            .then(function (res) {
              var url = res.d && (res.d.payment_url || res.d.url);
              if (res.ok && url) { location.href = url; return; }
              b.disabled = false;
              var why = res.d && res.d.detail === 'subscription_required'
                ? (ky ? 'Кредит сатып алуу үчүн тариф керек.' : 'Кредиты можно купить при действующем тарифе.')
                : (ky ? 'Төлөмдү ачуу мүмкүн болгон жок.' : 'Не удалось открыть оплату.');
              alert(why);
            }).catch(function () { b.disabled = false; });
        });
      });
    });
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
    if (document.body && document.body.hasAttribute('data-own-tabbar')) return;   // кабинет мастера: своя нижняя панель
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
        if (path !== currentPath()) {
          // Отклик сразу: вкладка подсвечена, сверху бежит полоса — пока телефон открывает страницу
          Array.prototype.forEach.call(bar.querySelectorAll('.vf-tab'), function (t) { t.classList.toggle('on', t === a); });
          document.body.classList.add('vf-nav-pending');
          return;
        }
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
    scheduleWarm();
  }
  // Вернулись «Назад» (страница из кэша браузера) — полосы загрузки быть не должно
  window.addEventListener('pageshow', function () { if (document.body) document.body.classList.remove('vf-nav-pending'); });
  // Прогрев вкладок (19.11.2026): через пару секунд после открытия кабинета телефон в фоне скачивает
  // страницы нижней панели и их скрипты/стили (через service worker они ложатся в кэш) — первое
  // нажатие на вкладку открывает страницу без ожидания сети. Раз за сессию, только при Wi-Fi/4G без «экономии».
  function warmTabs() {
    try {
      if (sessionStorage.getItem('vf_warm') === '1') return;
      if (!navigator.serviceWorker || !navigator.serviceWorker.controller) return;
      var c = navigator.connection;
      if (c && (c.saveData || /(^|-)2g$/.test(c.effectiveType || ''))) return;
      sessionStorage.setItem('vf_warm', '1');
    } catch (e) { return; }
    var pages = currentTabs().filter(function (t) { return t.href; }).map(function (t) { return t.href.split('#')[0]; })
      .concat(['/static/analytics.html', '/static/finance.html']);
    pages = pages.filter(function (p, i) { return pages.indexOf(p) === i && p !== currentPath(); });
    var i = 0;
    (function next() {
      if (i >= pages.length) return;
      var p = pages[i++];
      fetch(p, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.text() : ''; }).then(function (html) {
        // скрипты и стили страницы с меткой содержимого (?v=h…) — неизменяемые, кэшируются навсегда
        var re = /(?:src|href)="(\/static\/[^"]+\?v=h[0-9a-f]{10})"/g, m, seen = {};
        var jobs = [];
        while ((m = re.exec(html))) { if (!seen[m[1]]) { seen[m[1]] = 1; jobs.push(fetch(m[1]).catch(function () { /* ignore */ })); } }
        return Promise.all(jobs);
      }).catch(function () { /* ignore */ }).then(function () { setTimeout(next, 150); });
    })();
  }
  function scheduleWarm() {
    if (!isPhone()) return;
    var go = function () { setTimeout(warmTabs, 2500); };
    if (document.readyState === 'complete') go(); else window.addEventListener('load', go, { once: true });
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
  // Открыто как приложение: Android-приложение (TWA, ?source=twa, referrer android-app://),
  // установленное с сайта (display-mode standalone) или с экрана «Домой» iPhone. Метка живёт до закрытия вкладки.
  try { if (/[?&]source=(app|twa)\b/.test(location.search) || document.referrer.indexOf('android-app://') === 0) sessionStorage.setItem('vf_app', '1'); } catch (e) { /* ignore */ }
  function standalone() {
    try {
      return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true ||
        sessionStorage.getItem('vf_app') === '1';
    } catch (e) { return false; }
  }
  function isIOS() { return /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1); }
  function isAndroid() { return /android/i.test(navigator.userAgent); }
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
    // Android — настоящее приложение (файл .apk со страницы /app, решение владельца 26.11.2026)
    if (isAndroid() && !isIOS()) { location.href = '/app'; return; }
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
    var andr = isAndroid() && !isIOS();
    bar.innerHTML = '<img src="/static/brand/app-maskable-192.png" alt=""><div class="t"><b>' + (andr ? 'Приложение Voksy AI для Android' : 'Установите приложение') +
      '</b>' + (andr ? 'Чаты, записи и уведомления — без браузера' : 'Инбокс и каталог одним нажатием') + '</div>' +
      '<button type="button" class="go">' + (andr ? 'Скачать' : 'Установить') + '</button><button type="button" class="x" aria-label="Закрыть">×</button>';
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
    '.vf-voice-fab.vf-fab-away{opacity:0;transform:translateY(14px) scale(.9);pointer-events:none;transition:opacity .15s,transform .15s}',
    '@media (max-width:760px){.vf-voice-fab{right:14px;bottom:calc(76px + env(safe-area-inset-bottom,0px));height:54px;width:54px;padding:0;',
    'justify-content:center}.vf-voice-fab span.t{display:none}',
    'body:has(.bk-fab) .vf-voice-fab,body:has(.od-fab) .vf-voice-fab{bottom:calc(146px + env(safe-area-inset-bottom,0px))}}',
    'body:not(.vf-has-tabbar) .vf-voice-fab{bottom:calc(18px + env(safe-area-inset-bottom,0px))}',
    'body.vv-open .vf-voice-fab,body.modal-open .vf-voice-fab{display:none}',
    '@media (max-width:860px){body.ib-chat-open .vf-voice-fab{display:none}}',
    // Инбокс на компьютере: справа внизу поле ввода чата — кнопка становится кругом и уходит выше
    '@media (min-width:761px){body.ib-page .vf-voice-fab{width:48px;height:48px;padding:0;justify-content:center;bottom:96px;right:18px}body.ib-page .vf-voice-fab span.t{display:none}}',
    '@media (prefers-reduced-motion:reduce){.vf-voice-fab .vf-vf-wave i{animation:none}}'
  ].join('');
  // Инбокс (19.11.2026): кнопка есть и там — в списке диалогов; в открытом чате прячется (поле ввода)
  var VOICE_SKIP = ['/static/agent.html', '/static/voice-live.html', '/static/admin.html', '/static/master.html'];
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
    // Телефон: пока листаете, кнопка уходит и не закрывает кнопки списка (позвонить, WhatsApp); вернётся через 0,7 с
    if (!renderVoiceFab.scrollBound) {
      renderVoiceFab.scrollBound = true;
      var tm = null;
      window.addEventListener('scroll', function () {
        if (!isPhone()) return;
        var f = document.getElementById('vf-voice-fab'); if (!f) return;
        f.classList.add('vf-fab-away');
        clearTimeout(tm);
        tm = setTimeout(function () { f.classList.remove('vf-fab-away'); }, 700);
      }, { passive: true });
    }
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
    if (!cachedStaff() && !tokenIsStaff()) {   // кошелёк владельца сотруднику не нужен (и закрыт ему — 403)
      renderWalletCard(nav);
      refreshWallet();
    }
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
    openCredits: openCreditsModal,
    openMore: openMoreMenu,
    openStaffPassword: openStaffPassword,
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
