# 03 · Каркасы страниц (layouts)

В кабинете три каркаса: **A** — новый каркас дизайн-системы (`voice-assistants`, `crm-contact`),
**B** — легаси-каркас через мост (`dashboard`, `conversations`, `telephony`, `crm`, `settings`,
`admin`), **C** — отдельное трёхколоночное окно страницы «Агент обзвона» без общего сайдбара.
Плюс **D** — страница входа без сайдбара и **E** — лендинг (см. `05-landing.md`).

Для новой реализации используйте каркас A везде: мост B нужен только для переноса старых
страниц без переписывания.

---

## A. Каркас дизайн-системы (`.vf-app`)

```html
<body class="vf">
<div class="vf-app">
  <aside class="sidebar vf-sidebar" id="sidebar">
    <div class="vf-sidebar-head">
      <a href="/static/dashboard.html" class="vf-logo">
        <img src="/static/images/logo.png" alt="VoksiAI" onerror="this.style.display='none'">
        <span class="wordmark">VoksiAI</span>
      </a>
      <button class="btn btn-ghost btn-icon btn-sm mobile-toggle" id="sidebar-close" aria-label="Закрыть меню">
        <svg class="ic"><use href="/static/icons/ui.svg#i-x"></use></svg>
      </button>
    </div>
    <nav class="sidebar-nav" id="sidebar-nav"></nav>          <!-- рисует sidebar.js -->
    <!-- sidebar.js вставит сюда .vf-wallet -->
    <div class="sidebar-footer">
      <a href="/" data-logout class="btn btn-ghost" style="width:100%;justify-content:flex-start" id="logout-button">
        <svg class="ic"><use href="/static/icons/ui.svg#i-log-out"></use></svg> Выйти
      </a>
    </div>
  </aside>
  <script src="/static/js/sidebar.js"></script>

  <main class="vf-main">
    <header class="vf-topbar">
      <div class="vf-topbar-left">
        <button class="btn btn-ghost btn-icon mobile-toggle" id="sidebar-toggle" aria-label="Меню">
          <svg class="ic"><use href="/static/icons/ui.svg#i-menu"></use></svg>
        </button>
        <h1>Голосовые ассистенты</h1>
        <span class="chip chip-outline" id="usage-pill" data-tip="Лимит ассистентов по тарифу">2 / 5</span>
      </div>
      <div class="vf-topbar-right">
        <button class="btn btn-primary" id="btn-new">
          <svg class="ic"><use href="/static/icons/ui.svg#i-plus"></use></svg><span class="btn-text">Создать</span>
        </button>
      </div>
    </header>
    <div class="vf-content">…контент страницы…</div>
  </main>
</div>
</body>
```

Геометрия:

```
┌──────────────┬───────────────────────────────────────────────┐
│ sidebar 256  │ topbar 64 (sticky, blur, граница при скролле) │
│ fixed, full  ├───────────────────────────────────────────────┤
│ height,      │ .vf-content  padding 24 28 40                 │
│ border-right │                                               │
│              │  контент без max-width (тянется на всю ширину)│
│ ┌──────────┐ │                                               │
│ │ кошелёк  │ │                                               │
│ └──────────┘ │                                               │
│ footer Выйти │                                               │
└──────────────┴───────────────────────────────────────────────┘
```

- Сайдбар: `position: fixed; inset: 0 auto 0 0; width: 256px; z 30`, фон surface, правый бордер,
  вертикальный скролл. Голова 64px с логотипом (`padding 0 20px`).
- Топбар: 64px, `position: sticky; top: 0; z 20`, `padding 0 28px`, слева бургер (только ≤960) +
  h1 22px Syne + опциональные чипы, справа действия страницы. Полупрозрачный фон + blur; класс
  `.scrolled` при прокрутке добавляет бордер и тень (вешает `ui.js`).
- `.vf-main { margin-left: 256px; display: flex; flex-direction: column }`.
- Мобайл ≤960: сайдбар `translateX(-100%)`, `.open` показывает его со скримом `.vf-scrim`
  (`rgba(14,23,41,.4)`, z 29); клик по ссылке в сайдбаре закрывает.

### Пункты меню сайдбара (константа `MENU` в `js/sidebar.js`)

| Раздел | Пункт | Иконка Lucide | URL | `id` | Особенности |
|---|---|---|---|---|---|
| **Основное** | Дашборд | `house` | `/static/dashboard.html` | `dashboard-nav-item` | активен также для `/static` и `/` |
| | Агент обзвона | `headset` | `/static/agent.html` | `agent-nav-item` | открывает отдельный каркас C |
| | Голосовые ассистенты | `audio-lines` | `/static/voice-assistants.html` | `assistants-nav-item` | алиасы старых URL по провайдерам и `knowledge-base.html` |
| | Диалоги | `messages-square` | `/static/conversations.html` | `conversations-nav-item` | |
| | Телефония | `phone` | `/static/telephony.html` | `telephony-nav-item` | `data-feature="telephony"`, замок по тарифу |
| | CRM | `contact-round` | `/static/crm.html` | `crm-nav-item` | `data-feature="crm"`, алиас `crm-contact.html` |
| **Аккаунт** | Настройки | `settings` | `/static/settings.html` | `settings-nav-item` | |
| **Администрирование** (только админ, вставляется перед «Аккаунт») | Управление | `shield-check` | `/static/admin.html` | `admin-nav-item` | |

Заголовок раздела `.sidebar-section` 11px uppercase text-4, `padding 14px 12px 6px`. Пункт
`.sidebar-nav-item`: `padding 9px 12px`, gap 10, радиус 8, 500; active — 600, подложка
accent-soft, полоска 3×20 акцента слева, иконка accent. `.plan-locked-feature` — opacity .55 и
иконка `lock` 14px справа.

### Типовые сетки контента

| Паттерн | CSS | Где |
|---|---|---|
| Сетка карточек | `.grid-auto { --min: 280px }` | ассистенты (grid-mode), интеграции |
| Список + редактор | `.layout { grid-template-columns: 300px minmax(0,1fr); gap: 20px }`, список `position: sticky; top: calc(64px + 20px); max-height: calc(100vh - 64px - 40px)`; ≤720 одна колонка | voice-assistants |
| Стат-ряд | `grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px` | дашборд, CRM, админка |
| Настройки-секции | одна колонка карточек `.card` шириной max 960 (страница `settings`) | settings |
| Две колонки 2:1 | `grid-template-columns: minmax(0,2fr) minmax(0,1fr); gap 20` | crm-contact (карточка + таймлайн) |

---

## B. Легаси-каркас (мост `voksiai-legacy.css`)

Разметка та же по смыслу, но с классами старых страниц. Мост переводит их на токены, поэтому
визуально A и B не различаются.

```html
<body class="vf">
  <aside class="sidebar vf-sidebar" id="sidebar">…как в A…</aside>
  <script src="/static/js/sidebar.js"></script>
  <main class="main-content" id="main-content">
    <div class="top-nav">
      <button class="mobile-toggle" id="sidebar-toggle"><i class="fas fa-bars"></i></button>
      <h1 class="page-title">Панель управления</h1>
      <div class="user-menu">
        <button class="user-button" id="user-menu-button">
          <div class="user-avatar" id="user-avatar">АП</div>
          <span class="user-name" id="user-name">Имя пользователя</span>
          <i class="fas fa-chevron-down"></i>
        </button>
        <div class="user-dropdown" id="user-dropdown">
          <a href="/static/settings.html" class="dropdown-item"><i class="fas fa-user"></i> Профиль</a>
          <div class="dropdown-divider"></div>
          <a href="/" data-logout class="dropdown-item" id="dropdown-logout"><i class="fas fa-sign-out-alt"></i> Выйти</a>
        </div>
      </div>
    </div>
    <div class="content-container" id="content-container">…</div>
  </main>
</body>
```

Соответствия: `.main-content` = `.vf-main`, `.top-nav` = `.vf-topbar`, `.page-title` = h1,
`.content-container` = `.vf-content`. Дополнительно в топбаре справа **меню пользователя**:
`.user-button` — пилюля 36px (бордер, surface, тень shadow-1) с аватаром `.user-avatar`
28px круг accent с инициалами 12px 600, именем 13.5px 500 и шевроном; `.user-dropdown` —
радиус 12, бордер, shadow-3, паддинг 6, пункты `.dropdown-item` 8 10 радиус 7 13.5px.

---

## C. Каркас страницы «Агент обзвона» (`agent.html` + `agent/agent.css`)

Отдельное окно: **без общего сайдбара**, логотип в топбаре ведёт на дашборд. `<body>` без
класса `vf` (чтобы `ui.js` не подменял нативные `<select>`), `overflow: hidden`, страница
занимает ровно 100vh, скроллятся только панели и чат. Фон `--vf-surface-2` с едва заметным
свечением акцента: `body::before` = `radial-gradient(70vw 55vh at 8% 0%, rgba(42,92,232,.06), transparent 62%) + radial-gradient(45vw 40vh at 100% 100%, rgba(42,92,232,.035), transparent 60%)`.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ .top-nav 48px: [лого VoksiAI] | [панель◧] … [переключатель агентов ▾] [+чат] … [Активен ⏻][подписка/кредиты][⚙][◨] │
├───────────┬─────────────────────────────────────────────┬──────────────────┤
│ .col-left │ .chat (карточка)                            │ .col-right       │
│ 268px     │  header · лента сообщений · композер        │ 288px            │
│ «Работа»  │                                             │ «Агент»          │
│ статистика│                                             │ карточка агента  │
│ задачи    │                                             │ документы        │
│ звонки    │                                             │ база данных      │
│ история   │                                             │ коннекторы       │
│           │                                             │ Telegram-бот     │
│           │                                             │ кредиты          │
└───────────┴─────────────────────────────────────────────┴──────────────────┘
gap 10px, padding 10px; панели сворачиваются (grid-template-columns → 0), клавиши [ и ]
```

Токены раскладки: `--topbar-h: 48px; --left-w: 268px; --right-w: 288px; --gap: 10px;
--chat-max: 1600px`. `.main-layout { display: grid; grid-template-columns: var(--left-w) minmax(0,1fr) var(--right-w); transition: grid-template-columns 260ms }`;
классы `.left-closed`, `.right-closed`. `.panel-title` 11px uppercase text-3 высотой 24px,
`.panel-scroll` — вертикальный скролл с gap 8 между карточками.

Топбар: `grid-template-columns: 1fr auto 1fr`, фон surface, нижний бордер. Центр —
`.agent-switch` (триггер 36px с аватаром-буквой 26px accent-soft, именем 13.5px 600 и
статусом 11.5px success «Активен» / text-4 «Выключен»; дропдаун 320–380px со списком агентов,
пилюлей лимита и пунктом «Создать агента»). Справа: `.toggle-wrap` тумблер «Активен»,
`.sub-badge` подписка + кредиты, иконки настроек и панелей.

Мобайл ≤1100: панели скрыты, открываются как drawer, появляется `.burger-btn`.

Подробное содержимое панелей, чата, мастера и модалок — в `04-pages-cabinet.md`, раздел «Агент обзвона».

---

## D. Страница входа (`login.html`)

Без сайдбара: полноэкранный фон, по центру карточка формы. Детали — `04-pages-cabinet.md`,
раздел «Вход».

---

## E. Лендинг

React-приложение, собственный контейнер `max-width 1180px`, липкий навбар, секции с
вертикальными отступами `clamp(64px, 9vw, 120px)`. См. `05-landing.md`.
