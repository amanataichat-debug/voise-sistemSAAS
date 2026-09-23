# 04 · Страницы кабинета, часть 1: Агент обзвона · Голосовые ассистенты · Дашборд

Полная инвентаризация трёх ключевых страниц кабинета Voicyfy для пересборки 1:1 под VoksiAI.
Токены, компоненты и каркасы описаны в `01`–`03`; здесь — что лежит на каждой странице, в
DOM-порядке, с размерами, классами, состояниями и всеми пользовательскими строками.
Часть 2 (Диалоги, Телефония, CRM, Настройки, Вход, Админка) — в `06-pages-cabinet-2.md`.

## Что менять для VoksiAI (сводка по этим страницам)

| Что | В Voicyfy | Для VoksiAI |
|---|---|---|
| Бренд в текстах | «Voicyfy», «Voicyfy Agent», «кошелёк Voicyfy», «сервисный аккаунт Voicyfy», «Я ваш агент Voicyfy» | «VoksiAI» |
| Логотип | `/static/images/IMG_2820.PNG` (сайдбар 30×30, топбар агента 26×26, приветствие чата 56×56, аватар сообщений 28×28) | свой файл |
| Валюта и цены | `₽/мин`, `N ₽`, «подарочные рубли», тарифы 1 490 / 2 990 / 5 990 / 5 490 ₽, «Продлить за 4 990 ₽/мес» | сом; цены пересчитать; форматирование чисел `toLocaleString('ru-RU')` оставить (пробел-разделитель тысяч) |
| Часовой пояс | всё в МСК (`Europe/Moscow`, суффикс « МСК», «Время указывается по МСК») | `Asia/Bishkek`, суффикс « Бишкек» / «по времени Бишкека» |
| Телефоны в плейсхолдерах | `+79001234567`, `+7 933 091-64-41` | `+996 …` |
| «По закону РФ …» (гид дашборда, шаг 3; статьи) | верификация телефонии по закону РФ | законодательство КР или убрать |
| Названия тарифов | Trial / AI Voice / Старт / Profi / Agent | оставить или локализовать; на KG-версии названия обычно не переводят |
| Платёжный шлюз | Robokassa (форма POST) | свой шлюз, механика «скрытая форма POST на `payment_url`» подходит для большинства |
| Голосовые модели | OpenAI, Gemini, Яндекс, Fish Audio, Каскад | тот же набор; в KG-версии подписи «жен./муж.» у голосов и описания моделей переводятся |
| Языки интерфейса | RU | RU + KG: все строки из разделов ниже вынесены в `i18n/ru.json`, KG-перевод делает исполнитель |
| Админ-почта в JS | список e-mail в `dashboard.html` и `sidebar.js` | опираться на `user.is_admin` (в копии `js/sidebar.js` список пуст) |
| Цвета в тексте отчёта | `#2563eb / #1d4ed8 / #3b82f6 / #0f172a` и rgba(37,99,235,…) | в `css/` уже заменены на палитру VoksiAI (см. `01-foundations.md`), в разметке страниц использовать токены `--vf-*` |

Пути `backend/static/…` — расположение исходников в репозитории Voicyfy.

---

# 1. Страница «Агент обзвона» — `/static/agent.html` (Voicyfy Agent)

## 1.1 Назначение и подключения

- **Назначение:** дашборд автономного агента обзвона: чат с оркестратором + панель «Работа» (статистика, задачи, звонки, история) + панель «Агент» (настройки, документы, база знаний, память, коннекторы, Telegram-бот, кредиты). Не путать с `agents.html`.
- **URL:** `/static/agent.html`, прод — `https://voicyfy.ru/static/agent.html`. Раздаётся статикой (`app.mount("/static", StaticFiles(...))`), отдельного backend-роута нет.
- **`<html lang="ru" class="vf">`** — класс `vf` на `<html>`, а **не** на `<body>` (осознанно: `ui.js` не подменяет нативные `<select>`).
- `<meta name="robots" content="noindex, nofollow">`, `<meta name="theme-color" content="#2563eb">`, favicon `/static/favicon.ico`, title **«Voicyfy Agent»**.

**Загружаемые ресурсы (`<head>`, в порядке):**
1. `https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css` — Font Awesome 6.4.0 (как фолбэк к Lucide-мосту).
2. `preconnect` fonts.googleapis.com / fonts.gstatic.com.
3. `https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Syne:wght@700;800&family=Unbounded:wght@400;500;600;700&display=swap` — **Inter 400/500/600/700, Syne 700/800, Unbounded 400/500/600/700**.
4. `https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js` — markdown в чате.
5. `https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js` — санитайзер.
6. `/static/css/voicyfy.css` → `/static/agent/agent.css`.
7. `/static/js/ui.js`.

**Скрипты в конце `<body>` (строгий порядок, классические скрипты, общее глобальное окружение):**
`/static/js/sidebar.js` (только для `[data-logout]`, самого сайдбара на странице нет) → `agent/core.js` → `credits.js` → `agent-switcher.js` → `dashboard.js` → `tasks-calendar.js` → `chat.js` → `instructions-voice.js` → `telegram.js` → `telegram-account.js` → `max-account.js` → `contacts.js` → `calls.js` → `history.js` → `import.js` → `pipeline.js` → `knowledge-base.js` → `memory.js` → `connectors.js` → `onboarding.js` → `wizard.js` → `presence.js` → `motion.js` → `panels.js` → `settings-layout.js` → `init.js`.

## 1.2 Локальные CSS-переменные `agent.css` (алиасы старых имён → токены)

`:root` в `agent.css` (подключается **после** voicyfy.css) — это мост для инлайн-стилей в JS:

```
--bg: var(--vf-surface-2)        --white: var(--vf-surface)
--bg-soft: var(--vf-surface-2)   --blue: var(--vf-accent)
--blue-dark: var(--vf-accent-hover)   --blue-light: var(--vf-accent-soft)
--blue-border: rgba(37,99,235,.25)
--text: var(--vf-text)   --muted: var(--vf-text-3)   --hint: var(--vf-text-4)
--green: var(--vf-success)   --green-dark: #15803d   --green-light: var(--vf-success-soft)
--red: var(--vf-danger)   --red-light: var(--vf-danger-soft)
--amber: var(--vf-warning)   --amber-light: var(--vf-warning-soft)
--border / --border-soft: var(--vf-border)
--radius: var(--vf-r-lg)   --radius-sm: var(--vf-r-sm)   --radius-pill: var(--vf-r-full)
--elev-1/2/3: var(--vf-shadow-1/2/3)   --shadow: var(--vf-shadow-1)
--glow-accent: 0 0 0 3px var(--vf-accent-ring)
--ease-spring: cubic-bezier(.34,1.56,.64,1)   --ease-out: cubic-bezier(.22,1,.36,1)
--dur-fast: 120ms   --dur: 200ms   --dur-slow: 340ms
--transition: var(--dur) var(--ease-out)
/* Раскладка */
--topbar-h: 48px    --left-w: 268px    --right-w: 288px    --gap: 10px
--chat-max: 1600px  --font-brand: 'Unbounded', var(--vf-font-display)
```

**Переопределения токенов:** размеры кнопок — `.btn{height:34px;padding:0 12px;font-size:13.5px}` (в voicyfy.css 36px/14px), `.btn-sm{height:30px;padding:0 10px;font-size:12.5px;border-radius:7px}`; `.btn-secondary` перекрашена в surface/`--vf-border-strong`; `.btn-danger` — прозрачная с красным текстом, на hover `--vf-danger-soft`.

**База:** `body{font-family:var(--vf-font);font-size:14px;line-height:1.5;background:var(--vf-surface-2);height:100vh;overflow:hidden;display:flex;flex-direction:column}`. Фоновое свечение: `body::before` — `radial-gradient(70vw 55vh at 8% 0%, rgba(37,99,235,.06), transparent 62%)` + `radial-gradient(45vw 40vh at 100% 100%, rgba(37,99,235,.035), transparent 60%)`, `z-index:-1`. `:focus-visible{outline:2px solid var(--vf-accent);outline-offset:2px;border-radius:4px}`. Гашение двойного глифа FA: `i.vf-fa::before{content:none!important}`.

## 1.3 Скелет раскладки

```
#loading-screen (z-300, fixed, фон --vf-bg)
.ob-overlay#onboarding-overlay (z-210)
.wizard-overlay#wizard-overlay (z-200)
nav.top-nav#top-nav (height 48px, grid 1fr auto 1fr, z-20, display:none до showDashboard)
#sub-expired-banner (display:none)
.main-layout#main-layout (display:none → 'grid')
  .col-left > .panel-inner (width 268px)
  .chat-col (+ .handle-l / .handle-r)
  .col-right > .panel-inner (width 288px)
.drawer-overlay#drawer-overlay (z-90) > aside.drawer (z-91)
.app-footer#app-footer (display:none!important)
~13 модалок .modal-overlay (z-100; #contact-details-modal-overlay z-200)
```

- `.main-layout{flex:1;display:grid;grid-template-columns:var(--left-w) minmax(0,1fr) var(--right-w);gap:10px;padding:10px;transition:grid-template-columns 260ms cubic-bezier(.2,.7,.2,1), padding 260ms …}`
- Свёрнутые состояния: `.left-closed → 0 minmax(0,1fr) 288px; padding-left:0`; `.right-closed → 268px minmax(0,1fr) 0; padding-right:0`; обе → `0 minmax(0,1fr) 0`. Колонки гаснут `opacity:0;pointer-events:none` (transition 200ms).
- `.panel-scroll{overflow-y:auto;display:flex;flex-direction:column;gap:8px;padding-right:2px}`.
- `.panel-title{height:24px;padding:0 4px;margin-bottom:6px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--vf-text-3)}`; у правой панели `flex-direction:row-reverse`.
- **Язычки** `.handle` (как в ChatGPT): `position:absolute;top:50%;width:24px;height:72px`; `.handle-l{left:-4px}`, `.handle-r{right:-4px}`; внутри `.bar` (4×28, r999, `--vf-border-strong`) и `.hic` (14×14 svg-шеврон, `opacity:0`); на hover бар гаснет, шеврон появляется; при закрытой панели шеврон `rotate(180deg)`; `:focus-visible .bar{background:var(--vf-accent)}`. Также переключают панели клавиши `[` и `]` и любые `[data-panel="left|right"]`; состояние — `localStorage.agent_panels_v1`.

**Брейкпоинты agent.css:**
- **`@media (max-width:1100px)`** — `.main-layout` в одну колонку `minmax(0,1fr)`, `padding:8px`, `gap:0`; `.col-left/.col-right` и `.handle` скрыты; показывается `.burger-btn`, прячутся `.panel-btn`; `.top-nav{grid-template-columns:auto 1fr auto;padding:0 8px;gap:6px}`; скрыты `.nav-logo-text`, `.nav-sep`, `.sub-badge`, `.toggle-wrap`, `#delete-agent-btn`; `.nav-agent-name{max-width:46vw}`; `.agent-dropdown{min-width:280px;max-width:92vw}`; `.chat-messages{padding:14px 12px 8px}`, `.chat-foot{padding:6px 10px 10px}`; пузырь юзера `max-width:92%`. Колонки физически переезжают в drawer (`dashboard.js:applyLayout` + `_collectMigrations`, якоря-комментарии).
- **`@media (max-width:860px)`** — модалка настроек: `height:94vh`, `.settings-body{flex-direction:column}`, `.settings-nav` горизонтальный скролл, `small` в пунктах скрыт, `.settings-content{padding:16px}`.
- **`@media (max-width:720px)`** — календарь задач: `.tcal-wrap{flex-direction:column}`, `.tcal-side{width:100%}`, `.tcal-list{max-height:240px}`.
- **`@media (max-width:560px)`** — онбординг: `.ob-card{padding:18px 18px 22px}`, `.ob-title{font-size:19px}`, `.ob-k{flex-basis:72px}`.
- **`@media (prefers-reduced-motion: reduce)`** — все анимации/переходы в `.001ms`, отключены трансформы hover.

## 1.4 Блоки в DOM-порядке

### (1) `#loading-screen`
fixed inset 0, z-300, фон `--vf-bg`, колонка, gap 16. Содержит `.brand` (Syne 800, 20px, letter-spacing -.03em) с `img.nav-logo-img` (`/static/images/IMG_2820.PNG`) + `.nav-logo-text` «Voicyfy»; `.vf-wave` из 5 `<i>` (4×8px, r2, accent, анимация `vf-wave` 1.1s, задержки 0/120/240/360/480 мс, высота 8→26px); `<p>` **«Загрузка агента…»** (12.5px, `--vf-text-4`).

### (2) Онбординг `.ob-overlay#onboarding-overlay` (5 слайдов, `onboarding.js`)
`.ob-card`: max-w **560px**, padding `22px 28px 26px`, r16, `--vf-shadow-3`.
- `.ob-top`: `.ob-brand` (Syne 800 16px; `.nav-logo-icon` 28×28 с `fas fa-microphone-lines`) + кнопка `.ob-skip` «**Пропустить**» + `fa-xmark` (12.5px, `--vf-text-4`, h30, r7).
- `#ob-content`: `.ob-stage-art` (min-height **172px**, фон `--vf-surface-2`, бордер, r12, padding 18) → `.ob-badge` (h22, r999, 11px/600, uppercase, letter-spacing .05em, accent на accent-soft) → `h2.ob-title` (Syne, **21px**, 700, ls -.02em) → `p.ob-text` (13.5px, `--vf-text-3`, lh 1.6).
- `.ob-dots`: `.ob-dot` 7×7 (`--vf-border-strong`), `.done` → `--vf-text-4`, `.on` → accent + `scale(1.35)`.
- `.ob-actions`: слева «← Назад» (`btn btn-secondary`, `fa-arrow-left`) или пустой span, справа «Далее →» (`fa-arrow-right`) / на последнем — «**Создать агента**» (`fa-rocket`).

**Тексты 5 слайдов** (badge / title / text):
1. «Автономный сотрудник» / «Это не виджет и не автоответчик» / «Агент Voicyfy сам звонит по вашей базе, ведёт живой разговор, квалифицирует контакт и решает что делать дальше. Он заменяет первую линию продаж — а вы только ставите цели.» Арт: `.ob-orb` (74×74, r50%, градиент `135deg, #2563eb, #3b82f6`, тень `0 8px 22px rgba(37,99,235,.35)`, иконка `fa-headset` 28px) + `.ob-pulse` (анимация 1.8s) + чипы `.ob-chip` (h28, r999): «Звонит сам» (`fa-phone-volume`), «Ведёт диалог» (`fa-comments`), «Квалифицирует» (`fa-filter`).
2. «Pre-Call» / «Думает перед каждым звонком» / «Перед звонком оркестратор готовит стратегию: первую фразу, тон и тактику — опираясь на память о контакте и прошлые разговоры. Каждый звонок персональный, а не скрипт по бумажке.» Арт `.ob-mock` с шапкой «Стратегия звонка · Pre-Call» (`fa-brain`) и строками `.ob-row` (`.ob-k` flex-basis 84px): Первая фраза → «Иван, добрый день! Это Алина из…»; Тон → «деловой, без давления»; Тактика → «напомнить о прошлом интересе к CRM»; Помнит → «2 звонка · просил перезвонить в среду».
3. «Живой разговор» / «Говорит как человек» / «Голосовой агент ведёт настоящий телефонный диалог в реальном времени, при необходимости находит ответы в вашей Базе знаний и сам завершает звонок.» Арт `.ob-talk` с пузырями `.ob-bubble.agent` («Иван, удобно сейчас пару минут?», «Подскажу по тарифам» + чип `.ob-kb` «ищу в Базе знаний») и `.ob-bubble.client` («Да, слушаю»). Пузыри: max-w 82%, 12.5px, padding `8px 11px`, r12, у agent скруглён `border-bottom-left-radius:4px` на белом с бордером, у client — accent/белый текст и `border-bottom-right-radius:4px`.
4. «Post-Call + Память» / «Сам учится после разговора» / «После звонка агент разбирает диалог: обновляет память о контакте, двигает его по воронке и планирует перезвон. Каждый контакт со временем известен агенту всё лучше — без вашего участия.» Мокап «После звонка · автоматически» (`fa-rotate`): Итог → «заинтересован, ждёт КП»; Память → «+ ЛПР, бюджет до 50к, звонить после 15:00»; Перезвон → «запланирован на пятницу 16:00»; `.ob-funnel`: `new → active(.on) → success` (`.ob-stage`, 11px/600, r6, padding `3px 9px`).
5. «Управление словами» / «Командуете на простом языке» / «Через чат вы управляете всей базой обычными словами: «обзвони всех новых завтра с 10», «кому не дозвонились». Контакты, задачи и воронка ведутся автоматически и принадлежат только этому агенту.» Диалог: «Обзвони всех новых завтра с 10:00» → «Готово — запланировал 12 звонков, интервал 15 мин ✅» → «Кому не дозвонились на этой неделе?» → «7 контактов в очереди на перезвон».

### (3) Мастер `.wizard-overlay#wizard-overlay` (`wizard.js`, 9 шагов 0..8)
`.wizard-card`: max-w **600px**, padding `32px 36px`, r16, `--vf-shadow-3`. Шапка: `.wizard-logo` (`.nav-logo-icon` 30×30 accent + `fa-microphone-lines`) + `.wizard-logo-text` «Voicyfy» (Syne 19px/800); `.wizard-sub` — «**Создание автономного агента для звонков**» (13px, `--vf-text-4`, margin-bottom 24).
`.wizard-progress` — 8 точек `.w-dot` (8×8, `--vf-border-strong`; `.done`→success; `.active`→accent + `scale(1.3)`) с линиями `.w-line` (h2).
`.wizard-card h2` — Syne 22px/700, ls -.02em; `p.hint` — 13.5px, `--vf-text-3`.

**Шаг 0 «Создание агента» / «Выберите тип голосового ассистента.»** — карточки `.type-card` (бордер `--vf-border-strong`, r12, padding `14px 16px`, margin-bottom 10, flex gap 14; `.selected` → бордер accent + фон accent-soft + `box-shadow:0 0 0 1px var(--vf-accent)`); внутри `.type-radio` (18×18, r50%, бордер 2px; выбранная — точка 8×8 accent), `.type-logo` (`VF.logo(type,{size:18})`, `.logo-wrap` тут переопределён на 32×32/r9, `.logo` 18×18), `.type-info` с `.type-name` (14.5px/600) и `.type-desc` (12.5px, `--vf-text-3`).
`TYPE_DEFS` (порядок и тексты):
| type | name | desc |
|---|---|---|
| gemini | Gemini Voice | Быстрая и экономичная модель — рекомендуем для старта. |
| cascade | Cascade | Лучшее русское звучание. Бесплатно — платите только за связь. |
| fish | Fish Audio | Премиальный русский синтез: OpenAI ведёт диалог, Fish озвучивает. |
| yandex | Yandex SpeechKit | Голоса Yandex SpeechKit, российская инфраструктура. |
| openai | OpenAI Realtime | gpt-realtime — премиум-качество голоса. |

У выбранной карточки — `.type-key-status` с `.key-pill.ok` (h22, r999, success-soft) и `fa-wallet`: «На вашем API-ключе, с кошелька не списывается» / «<N> ₽/мин с кошелька Voicyfy» / «Бесплатно» / «Минуты списываются с кошелька Voicyfy»; ниже `.form-hint` «Минуты связи при звонках оплачиваются с баланса телефонии.»
Баннер телефонии `.tele-banner` (padding `12px 14px`, r8, 13px): `.ok` (success-soft, текст `#166534`) «Телефония подключена и верифицирована» (`fa-circle-check`); `.bad` (danger-soft, текст `#991b1b`) «Телефония не верифицирована. Без верифицированной телефонии агент не сможет звонить.» + кнопка «Настроить телефонию» → `/static/telephony.html`.

**Шаги 1–5 (документы), `WIZ_DOCS`** — заголовок «Шаг N: <title>»:
1. «Кто мы» / hint «Опишите компанию: название, сфера, УТП, средний чек.» / placeholder «Мы — компания «Ромашка», продаём CRM для малого бизнеса...»
2. «Кому звоним» / «Портрет клиента: должность, боли, возражения.» / «Руководители отделов продаж в компаниях от 20 человек...»
3. «Как говорим» / «Стиль общения и имя агента.» / «Дружелюбно, кратко, без давления...» + **доп. поле «Имя агента *»** (placeholder «Алина»)
4. «Что предлагаем» / «Продукты, цены, акции.» / «Тариф «Старт» — 5 000 ₽/мес...»
5. «Правила и цели» / «KPI, лимиты, что считать успехом.» / «Цель: назначить встречу. Макс 3 попытки...»
Валидация-тосты: «Заполните поле», «Укажите имя агента».

**Шаг 6 «Инструкции для оркестратора»** — hint «Опционально. Правила планирования звонков и работы с контактами для текстового мозга-оркестратора. В живом телефонном разговоре НЕ используются — для этого поле инструкций голосового агента на следующем шаге.»; placeholder «Например: «перед обзвоном новых контактов проверяй дубли», «не планируй звонки в выходные».»

**Шаг 7 «Модель и голос»** — hint «Модель оркестратора управляет агентом: планирует звонки, анализирует результаты, отвечает в чате. Голос — то, чем агент говорит в звонке. Биллинг включён в подписку Voicyfy.»; `<select#w-model>` + `.form-hint#w-model-desc`; блок выбора голоса (`voiceControlHtml`); textarea «Инструкции для голосового агента» (placeholder «Например: «говори коротко, не дави», «если спросят про цену — назови диапазон».», hint «Правила поведения именно в живом разговоре по телефону. Опционально.»); кнопка «🚀 Создать агента» (`fa-rocket`).

**Шаг 8 (создание)** — `<ul class="creation-list">` из трёх `.creation-item.pending` (padding `12px 0`, 14px, `--vf-text-3`, нижний бордер; `.creation-icon` 26×26 r50%, при `.done` — success-фон + `fa-check`, при pending — бордер 2px): «Сохранение документов», «Создание голосового агента (<Gemini|OpenAI|Cartesia|Yandex|Cascade|Fish>)», «Активация». `.spinner` 18×18 (border 2px, top-color accent, `spin .8s linear infinite`), шаг «проигрывается» 650 мс. Успех → кнопка «→ Открыть агента» (`location.reload()`) и примечание либо «🎉 Вам доступен бесплатный **тестовый период на 3 дня** и **1 500 кредитов** для теста оркестратора. После теста агент доступен на тарифе **Profi** (включает кредиты).», либо «Тестовый период уже использован. Агент доступен на тарифе **Profi**.» + кнопка «Перейти к тарифам». Ошибка — красный блок `--red-light`/`--red`, r10, 13px + кнопка «Назад».

### (4) Топбар `nav.top-nav#top-nav`
`height:48px; display:grid; grid-template-columns:1fr auto 1fr; gap:12px; padding:0 14px; background:var(--vf-surface); border-bottom:1px solid var(--vf-border); z-index:20`.
- **`.nav-left`**: `a.nav-logo` (title «В личный кабинет» → `/static/dashboard.html`; `img.nav-logo-img` 26×26 r9; `.nav-logo-text` «Voicyfy» — Syne 17px/800, ls -.03em, градиент `135deg,#2563eb,#3b82f6` через `background-clip:text`); `.nav-sep` (1×22, `--vf-border`); `button#panel-btn-left.icon-btn.panel-btn` (инлайн-SVG rect+линия, title «Панель «Работа»»); `button#burger-btn.burger-btn` (`fa-bars`, `display:none` до 1100px).
- **`.nav-center`** — вложенный grid `1fr auto 1fr`: по центру `.agent-switch#agent-switch`, в 3-й колонке `#chat-new-btn` (`fa-plus`, title «Новый чат (история очистится)»).
  - `.agent-trigger` (h36, padding `0 10px 0 6px`, r10, max-w 420): `.agent-trigger-avatar#nav-agent-avatar` (26×26, r8, accent-soft/accent, 700/13px, буква имени), `.nav-agent-name` (13.5px/600, ellipsis, max-w 320), `.nav-agent-status` (11.5px, success; `.off` → `--vf-text-4`) с `fa-circle` 7px и текстом **«Агент активен» / «Агент неактивен»**, `.agent-trigger-chevron` (`fa-chevron-down`, 11px, при open `rotate(180deg)`). При `.no-dropdown` шеврон скрыт и курсор default.
  - `.agent-dropdown` (абсолютный, `top:calc(100% + 6px)`, `left:50%` + `translateX(-50%)`, min-w **320px**, max-w 380, r12, `--vf-shadow-3`, z-50, padding 6, анимация `ddIn .15s`): шапка — `.agent-dropdown-title` «**Ваши агенты**» (11px/600, uppercase, ls .05em) и `.agent-dropdown-pill` «N/3» (h20, r999, бордер; `.full` → warning-soft); список `.agent-dd-item` (padding `8px 10px`, r7; `.active` → accent-soft) c `.agent-dd-avatar` (28×28 r8), `.agent-dd-name`, `.type-badge` (h22, r999, `--vf-surface-3`, 11.5px) с логотипом провайдера `VF.logo(type,{bare:true,size:13})` + название (`Gemini|OpenAI|Cartesia|Yandex|Cascade|Fish`), галочка `fa-check`; футер — `.agent-dd-create` «**Создать нового агента**» (accent, 13.5px/600, иконка в квадрате 28×28 accent-soft) либо `.agent-dd-limit` (warning-soft) «**Лимит агентов исчерпан (N/3). Удалите одного из существующих агентов, чтобы создать нового.**»
- **`.nav-right`**: `.toggle-wrap` (h32, padding `0 6px 0 12px`, бордер, r999; `:has(input:checked)` → success-soft, бордер прозрачный) с `.toggle-label#toggle-label` **«Активен»/«Неактивен»** (12.5px/500; `.on` → success), `label.switch` + `.slider` (38×22, off `#cbd5e1`, on `--vf-success`, кружок 18×18 с `box-shadow:0 1px 2px rgba(0,0,0,.25)`), подсказка `.hint.hint-l` с текстом «Выключенный агент не звонит и не отвечает клиентам. Запланированные задачи ждут включения.»; `.sub-badge#sub-badge` (h32, r999, бордер `--vf-border-strong`) с `.sub-status` (состояния `.active/.trial/.expired/.none`, тексты из credits.js: «● Активен», «● Trial — N д.», «● Истёк», «○ Не активен»), `.sub-credits` (иконка `fa-coins` warning, tabular-nums, title «Баланс кредитов оркестратора») и кнопкой `.sub-action` «Пополнить»/«Продлить» (h26, r7, accent; `.danger` → danger); `.nav-sep`; `a#guide-btn.icon-btn` (`far fa-circle-question`, title «Как это работает» → `/static/agent-guide.html`, target _blank); `a#delete-agent-btn.icon-btn` (`far fa-trash-can`, title «Удалить агента», hover → danger-soft/danger); `button#logout-btn.icon-btn` (`fa-right-from-bracket`, title «Выйти с платформы»); `button#panel-btn-right.icon-btn.panel-btn`.
- `.icon-btn`: 32×32, r8, прозрачный, `--vf-text-3`, hover `--vf-surface-3` + `--vf-text`, `:active{transform:scale(.94)}`, `.is-on` → accent-soft/accent; внутри `.ic` 17×17.

### (5) Баннер `#sub-expired-banner`
Инлайн: `background:#fdecea;border-bottom:1px solid #f5c2c0;color:#b71c1c;padding:10px 20px;font-size:13.5px` (CSS переопределяет на `--vf-danger-soft`, бордер прозрачный, текст `#7f1d1d`). Текст: «⚠ **Подписка истекла. Все звонки агента и работа оркестратора заблокированы.**» + кнопка `.sub-action.danger` «**Продлить за 4 990 ₽/мес**».

### (6) Левая панель `.col-left` — «Работа»
Заголовок «Работа» + `.hint` («Что происходит прямо сейчас: цифры, ближайшие задачи, последние звонки и лента событий.») + кнопка сворачивания (`fa-chevron-left`).

**`.stats-row`** — `grid-template-columns:1fr 1fr; gap:8px`. `.stat-card`: `--vf-surface`, бордер, r12, padding `10px 12px`, `--vf-shadow-1`. Внутри `.stat-icon` (24×24, r7, 11px; `.blue` accent-soft/accent, `.green` success-soft/success), `.stat-num` (Syne **24px**/700, lh 1.1, ls -.02em, tabular-nums), `.stat-label` (11.5px, `--vf-text-2`), ссылки `.stat-go` (11.5px, accent, hover underline).
- Карточка 1: `fa-phone-volume`, `#stat-calls`, «**Всего звонков**» (hint «Все разговоры агента: исходящие по задачам и входящие на его номер.»), ссылка «**Все звонки →**» → `openCallsModal()`.
- Карточка 2: `fa-users`, `#stat-contacts`, «**Контакты**» (hint «База контактов этого агента. «Воронка» показывает стадии, «Импорт» загружает список из файла.»), `.stat-links`: «**Воронка**», «**Все**», «**Добавить**», «**Импорт**».

**Карточка «Ближайшие задачи»** (`.card`: surface, бордер, r12, `--vf-shadow-1`, padding `10px 12px`; `.card-head` flex-wrap с `row-gap:4px`; `.card-head-title` 12.5px/600 с иконкой в квадрате 20×20 r6 accent-soft/accent; `.card-link` 12px accent, `margin-left:auto`): иконка `far fa-calendar-check`, hint «Что агент собирается сделать: звонки и сообщения, поставленные вами в чате или им самим после разговоров.», ссылка «**Календарь →**». Пустое состояние `.empty` — «**Нет задач**» / из JS «**Нет запланированных задач**» (12.5px, `--vf-text-4`, padding `10px 0`, по центру).
Разметка задачи (`loadTasks`): `.task-item` (flex gap 9, padding `7px 0`, верхний бордер) → `.task-date` (w44, по центру: `.d` 17px/700, `.m` 10px uppercase `--vf-text-4`, `.t` 11px accent 600) + `.task-body` (`.task-title` 12.5px/500 + бейдж канала, `.task-desc` 12px clamp 2 строки, `.task-foot` c `.task-contact` и `.task-when` — пилюля 10.5px/600, r999, success-soft; `.soon` → warning-soft). Бейджи канала (`core.js:taskChannelBadge`): Telegram — `background:#E0F2FE;color:#0369A1`, иконка `fa-paper-plane`; MAX — `background:#F5F3FF;color:#6D28D9`, `fa-comment-dots`; для звонков бейджа нет.

**Карточка «Последние звонки»** — `fa-clock-rotate-left`, hint «Итоги последних разговоров: транскрипт, длительность и решение агента по каждому.», ссылка «**Все звонки →**», пусто — «**Нет звонков**». Строка `.call-item`: `.avatar` (28×28, r8, accent-soft/accent, первая буква), `.call-info` (`.call-name` 12.5px/500, `.call-meta` 11.5px `--vf-text-3` с `.call-dot` 6×6 `.answered`→success `.no_answer`→danger, стрелкой направления `fa-arrow-down` `#0891B2` (входящий) / `fa-arrow-up` `#7C3AED` (исходящий), решением и датой), `.call-phone-ic` (28×28, r8, success-soft/success). Решения (`decisionRu`): `FOLLOWUP`→«Перезвон», `SUCCESS`→«Успех», `NO_ANSWER`→«Не ответил».

**Карточка «История работы агента»** — `fa-list-check`, hint «Лента всех событий: звонки, SMS, сообщения в Telegram и MAX и что агент решил по каждому.», ссылка-обновление `fa-rotate` (title «Обновить»), контейнер `.agent-history-list` (`max-height:520px; overflow-y:auto; margin:0 -4px; padding:0 4px`), строки `.hist-item` (padding `10px 0`, верхний бордер; `.hist-item-name` 13px/500, `.hist-item-phone` 12px). Стартовый текст «**Загрузка...**».

### (7) Центр `.chat-col`
- `.chat-messages`: `flex:1; overflow-y:auto; padding:16px 18px 10px; display:flex; flex-direction:column; align-items:center; gap:18px`.
- **Приветствие** `.chat-welcome` (max-w **820px**, анимация `welcomeIn .35s`, padding `24px 18px 40px`): `img.chat-welcome-img` 56×56 (`/static/images/IMG_2820.PNG`), `.chat-welcome-title` (шрифт **Unbounded** через `--font-brand`, 22px/600, lh 1.3) — «**Привет!**<br>**Я ваш агент Voicyfy**»; `.chat-welcome-sub` (14px, `--vf-text-2`, max-w 460) — «Помогаю управлять обзвоном: контакты, задачи, звонки и аналитика.<br>Выберите подсказку или напишите запрос сами.»; `.chat-tiles` — `grid-template-columns:repeat(auto-fill, minmax(150px,1fr)); gap:8px; max-width:560px`.
- `.chat-tile` — кнопка: `padding:11px 12px`, surface, бордер, r12, `--vf-shadow-1`, hover → `--vf-shadow-2` + `translateY(-1px)`; `.chat-tile-ic` 26×26 r7 accent-soft/accent (на hover карточки `scale(1.1) rotate(-4deg)` с `--ease-spring`), `.chat-tile-label` 12.5px/500.
  **10 случайных плиток из пула 25** (`CHAT_SUGGESTIONS`, icon / label / prompt): `fa-chart-pie` «Статистика агента» → «Покажи мою статистику за всё время»; `fa-compass` «Воронка контактов»; `fa-person-walking` «Кто в работе»; `fa-circle-check` «Успешные»; `fa-ban` «Отказы»; `fa-user-plus` «Последние контакты»; `fa-calendar-day` «Расписание на сегодня»; `fa-calendar-week` «Расписание на неделю»; `fa-forward` «Ближайшие звонки»; `fa-calendar-plus` «Запланировать звонок»; `fa-phone-slash` «Недозвоны»; `fa-chart-line` «Отчёт за неделю»; `fa-calendar-check` «Отчёт за месяц»; `fa-clock-rotate-left` «Последние звонки»; `fa-bullseye` «Конверсия»; `fa-user` «Добавить контакт»; `fa-file-import` «Импорт списка»; `fa-rocket` «Обзвонить новых»; `fa-phone-volume` «Позвонить сейчас»; `fa-circle-pause` «Пауза по контакту»; `fa-lightbulb` «Что ты умеешь»; `fa-mug-hot` «Итоги дня»; `fa-clock` «Лучшее время звонка»; `fa-rotate` «Кого перезвонить»; `fa-receipt` «Сводка по агенту».
- **Сообщения**: `.msg` (max-w `--chat-max` = **1600px**, анимация `msgIn .2s`). Ассистент — без пузыря: `.msg-bubble.answer` прозрачный, 14px/lh 1.65, аватар `.msg-avatar` 28×28 с картинкой. Пользователь — `.msg-bubble.user-chip`: фон `--vf-surface-3`, **шрифт Unbounded 13px/400**, lh 1.6, padding `9px 15px`, **r18**, `width:fit-content`, блок max-w 72% (на мобиле 92%). `.msg-time` 11px `--vf-text-4`.
- **Markdown** (`.msg-bubble.md`): `p{margin:0 0 8px}`, h1 15.5 / h2 15 / h3 14.5 / h4 14px (600), списки `padding-left:18px`, `code` — фон `rgba(15,23,42,.06)`, r5, `.88em`, моно; `pre` — surface + бордер + r10 + padding `10px 12px`; blockquote — левый бордер 3px `--vf-border-strong`; таблицы обёрнуты в `.md-table-wrap` (горизонтальный скролл), `th` — `--vf-surface-2`, 11.5px uppercase.
- **Индикатор набора** `.typing-row` — фон `--vf-surface-2`, r14 (левый нижний 4px), три `.typing-dot` 6×6 accent, анимация `tb 1.2s` с задержками .2/.4s.
- **Процесс-трейс инструментов:** `.activity-working` («мерцающий» текст `.aw-text` — градиент по тексту + `aw-shimmer 1.6s`, точка `.aw-dot` 8×8 accent с `aw-pulse 1s`); `.step` (бордер, r9, padding `5px 10px`, 12.5px) с `.step-ic` 22×22 r6 (ok → success-soft, error → danger-soft), `.step-meta` (11px, фон `--vf-surface-2`, r6), `.step-spin` 13×13 (`ts-spin .7s`), `.step-state.ok/.err`; свёрнутый итог `.trace-summary` (h26, r7, иконка `fa-bolt` accent, шеврон `.trace-chev` поворачивается) + `.trace-steps` (max-w 560). Есть отладочный лог `.debug-log` с типами `.de-thinking` (`#eef2ff`/`#4338ca`), `.de-tool_call` (warning), `.de-tool_result` (success), `.de-tool_error` (danger), `.de-gpt_response` (accent).
  `TOOL_LABELS` (чат, лейблы шагов): «Изучаю историю звонков», «Загружаю контакты», «Считаю статистику», «Проверяю задачи», «Создаю контакт», «Планирую звонок», «Обновляю память контакта», «Отправляю уведомление», «Обновляю данные контакта», «Меняю стадию воронки», «Удаляю задачу», «Ищу контакты», «Открываю карточку контакта», «Считаю воронку», «Добавляю контакты», «Удаляю контакт», «Дописываю заметку», «Переношу звонок», «Смотрю расписание», «Планирую серию звонков», «Звоню прямо сейчас», «Ставлю контакт на паузу», «Открываю транскрипт», «Готовлю отчёт за период», «Собираю недозвоны», «Записываю в свой блокнот». Иконки — `TOOL_ICONS` (fa-clock-rotate-left, fa-users, fa-chart-pie, fa-calendar-check, fa-user-plus, fa-calendar-plus, fa-brain, fa-paper-plane, fa-user-pen, fa-shuffle, fa-trash-can, fa-magnifying-glass, fa-id-card, fa-filter, fa-users-rectangle, fa-user-minus, fa-note-sticky, fa-pen-to-square, fa-calendar-days, fa-phone-volume, fa-circle-pause, fa-file-lines, fa-chart-line, fa-phone-slash, fa-book).
- **Плашки памяти** `.memory-chips` / `.memory-chip` (h auto, padding `4px 9px`, r999, бордер, фон accent-soft, 12px) — подписи «Запомнил», «Уточнил», «Забыл» (`.forget` — иконка серая).
- **Поле ввода** `.chat-foot{padding:6px 18px 6px}` → `.chat-input-area` (max-w 1600, flex `align-items:flex-end`, gap 6, padding `6px 6px 6px 10px`, бордер, **r18**, surface, `--vf-shadow-2`; `:focus-within` → бордер accent + `0 0 0 3px var(--vf-accent-ring), var(--vf-shadow-2)`). Внутри: слот орба `.chat-header.chat-orb-slot`, `textarea.chat-input#chat-input` (placeholder «**Напишите оркестратору…**», title «Enter — отправить, Shift+Enter — перенос строки», min-h 36, max-h 120, 14px), `button.chat-mic#chat-mic` (34×34, r11, title «Записать голосом»; `.recording` → danger-soft + `micPulse 1.2s`; `.loading` → `cursor:wait`), `button.chat-send#chat-send` (34×34, r11, accent/белый, hover accent-hover, `:active scale(.92)`, disabled → `--vf-border-strong`).
- **Орб присутствия** (`presence.js` вставляет в `.chat-header`): `.agent-orb` 24×24, `.orb-core` 22×22 r50% `radial-gradient(circle at 32% 28%, #93c5fd 0%, var(--vf-accent) 52%, var(--vf-accent-hover) 100%)`, тень `0 2px 8px rgba(37,99,235,.35), inset 0 1px 2px rgba(255,255,255,.55)`, анимация `orb-breathe 3.4s`; состояния `data-state="thinking"` (`orb-think .9s` + вращающееся кольцо `.orb-ring`), `"speaking"` (`orb-speak 1.1s`); ping-эффект `orb-ping .6s`. Карточки дашборда «оживляются» классом `.card-ripple` (анимация `card-ripple-anim 1s`, вспышка `0 0 0 2px rgba(37,99,235,.32)`).
- **Скелетоны** (`motion.js`): `.sk-num` (::after 48×22 r7 с шиммером `sk-shimmer 1.3s`), `.sk-row/.sk-dot(28×28 r8)/.sk-line(h10 r6, .short 52%)` — градиент `100deg, var(--vf-surface-3) 28%, var(--vf-surface-2) 50%, var(--vf-surface-3) 72%`.

### (8) Правая панель `.col-right` — «Агент»
Заголовок «Агент» + hint «Как агент устроен: настройки, документы, база знаний, что он запомнил и подключённые каналы.» + `fa-chevron-right`.

1. **«Настройки агента»** (`fa-sliders`, hint «Имя, голос, модель «мозга», номер для звонков и правила поведения в разговоре.», ссылка «**Изменить**»): `.agent-card-body` → `.agent-avatar-lg` (40×40, **r12**, градиент `135deg,#2563eb,#3b82f6`, белый, Syne 18px/800, первая буква имени), `.agent-name-lg` (15px/600), `.agent-status-lg` («Активен»/«Неактивен», success / `--vf-text-4`, `fa-circle` 7px), `.type-badge` (+ логотип провайдера через `settings-layout.js:_syncTypeBadgeLogo`), `.agent-id` (11px, `--vf-text-4`, моно) — текст «**ID агента: …**» (последние 16 символов).
2. **«Документы агента»** (`far fa-folder-open`, hint «Постоянное описание бизнеса: кто вы, кому звоните, как говорите, что предлагаете. Основа всех решений агента. Меняете вручную.», ссылка «**Все →**»): `.docs-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}`, карточки `.doc-card` (бордер, r8, padding `8px 9px`; 5-я — `.wide` на всю ширину) с `.doc-card-ic` (22×22, r6, 10.5px) в фирменных цветах, `.doc-card-title` (12.5px/600), `.doc-card-prev` (11.5px, clamp 2 строки; если пусто — «**Нажмите чтобы заполнить**»).
   `DOC_DEFS`: Кто мы (`fa-building`, `#7C3AED` на `#F3E8FF`); Кому звоним (`fa-bullseye`, `#2563EB` на `#DBEAFE`); Как говорим (`fa-comments`, `#059669` на `#D1FAE5`); Что предлагаем (`fa-box-open`, `#EA580C` на `#FFEDD5`); Правила и цели (`fa-flag`, `#DC2626` на `#FEE2E2`).
3. **«База данных»** (`fa-database`, hint «Тексты о продукте, ценах и условиях. Агент ищет по ним точные ответы во время звонка и в чате с вами.», ссылка «**Управлять →**»). Состояния из `knowledge-base.js`: «**База не создана**», «**База создана · N символов**», «<name> · Nk символов», «обновлено <дата>».
4. **«Что агент запомнил»** (`fa-brain`, hint «Рабочий блокнот агента: ваши поручения из чата и его наблюдения по звонкам. Меняется сам по ходу работы, в отличие от документов.», ссылка «**Открыть →**») + `.card-sub` (11.5px, `--vf-text-4`, `margin:-4px 0 8px`) «Поручения из чата и наблюдения по звонкам». Пусто: «**Пока пусто. Скажите в чат, что агенту делать иначе, — он запомнит**».
5. **«Коннекторы»** (`fa-plug`, hint «Внешние сервисы, которыми агент может пользоваться: календарь, почта, личные Telegram и MAX для переписки с клиентами.», ссылка «**Управлять →**»). Строки статусов: «Подключено», «Не подключено», «Требует переподключения», «Недоступен», «Ничего не подключено».
6. **«Telegram-бот»** (`fab fa-telegram`, hint «Ваш бот для уведомлений о результатах звонков и общения с агентом прямо из Telegram.», ссылка «**Настроить**»). Статусы: «**Не подключён**», «<uname> · N чатов», «<uname> · отключён».
7. **«Кредиты оркестратора»** (`fa-coins`, hint «Оплата работы «мозга» агента: планирование, анализ звонков, чат с вами. Минуты самих звонков сюда не входят.», ссылка «**Журнал →**»): три `.exp-row` (flex space-between, padding `5px 0`, 13px; `.lbl` `--vf-text-3`, `.val` 500 tabular-nums) — «**Баланс**», «**Подписка**», «**Действует до**»; кнопка на всю ширину `btn btn-primary btn-sm` «**+ Пополнить кредиты**».

### (9) Мобильный drawer
`.drawer-overlay` (fixed, `rgba(15,23,42,.45)`, blur 2px, z-90), `.drawer` (`width:min(360px, 88vw)`, фон `--vf-bg`, z-91, анимация `drawerIn .25s` из `translateX(-100%)`). `.drawer-header` (h 48px = `--topbar-h`, padding `0 12px 0 18px`, surface) с `.drawer-title` «**Меню**» (14px/600) и `.drawer-close` (×, 32×32, r7, 20px). `.drawer-body{padding:12px;gap:12px}` + слоты `#drawer-toggle-slot`, `#drawer-badge-slot`, `#drawer-actions`.

### (10) Подсказки `.hint` (CSS-тултипы, без JS)
Разметка: `<span class="hint" tabindex="0" data-hint="текст"><i class="far fa-circle-question"></i></span>`. Иконка 15×15, `--vf-text-4`, `cursor:help`; hover/focus → accent. Тултип `::after`: `position:absolute; top:calc(100% + 6px); left:0; right:0; padding:8px 10px; border-radius:8px; background:#0f172a; color:#fff; font-size:12px; line-height:1.45; box-shadow:var(--vf-shadow-3); z-index:300`, появление — `opacity` + `translateY(-3px)→0` за `--vf-fast`. Позиционируется от ближайшего `position:relative` контейнера (`.card-head`, `.panel-title`, `.stat-label`, `.form-label`, `.toggle-wrap`). Модификатор `.hint-l` — прижать вправо (`left:auto;right:0;width:max-content;max-width:280px`). `.form-label .hint::after{max-width:360px}`.

## 1.5 Модалки страницы агента (13 штук, `.modal-overlay` z-100)

Общая механика: `.modal-overlay{position:fixed;inset:0;background:rgba(15,23,42,.45);backdrop-filter:blur(3px);z-index:100;display:flex;align-items:center;justify-content:center;padding:16px;animation:ov .15s}`; скрытие — классом `.hidden`. `.modal{background:var(--vf-surface);border:1px solid var(--vf-border);border-radius:16px;width:100%;max-width:560px;max-height:92vh;display:flex;flex-direction:column;box-shadow:var(--vf-shadow-3);animation:mi .2s}`. `.modal-header{padding:18px 22px 12px}`, `.modal-title{font-size:16px;font-weight:700;gap:9px}` (иконка `--vf-text-4` 14px), `.modal-close` 30×30 r7. `.modal-body{padding:4px 22px 18px;overflow-y:auto}`. `.modal-footer{padding:12px 22px 18px;border-top:1px solid var(--vf-border);justify-content:flex-end;gap:8px}`. Закрытие по Esc — в `init.js`.

Варианты ширин: `.modal-lg{max-width:720px}`, `.modal-settings{max-width:1040px;height:86vh}`, `.modal-cal{max-width:880px}`, `.modal-pipeline{max-width:96vw;width:96vw}`, кредиты/биллинг — инлайн `max-width:640px`, коннекторы — `520px`, личный Telegram/MAX — `480px`.

Формы внутри: `.form-group{margin-bottom:14px}`, `.form-label{font-size:12.5px;font-weight:600;color:var(--vf-text-2);margin-bottom:6px}`, `.form-input/.form-textarea/.form-select{height:36px;padding:0 12px;border:1px solid var(--vf-border-strong);border-radius:8px;font-size:13.5px}` (focus → accent + `0 0 0 3px var(--vf-accent-ring)`), textarea `min-height:96px;padding:10px 12px`, `.form-select` — свой SVG-шеврон в `background-image`, `.form-hint{font-size:12px;color:var(--vf-text-4)}`.

1. **`#edit-modal-overlay` — «Настройки агента» (документы), `.modal-lg`.** Табы `.doc-tabs` (flex, `margin:0 -22px 16px`, нижний бордер, горизонтальный скролл без полосы) / `.doc-tab` (padding `10px`, 13px/500, `--vf-text-3`, активный — accent + нижний бордер 2px accent): **Кто мы · Кому звоним · Как говорим · Что предлагаем · Правила и цели**. В панелях — textarea rows=8 с лейблами: «Кто мы — описание компании», «Кому звоним — портрет клиента», «Как говорим — стиль общения», «Что предлагаем — продукты и цены», «Правила и цели — KPI и политика». Футер: «Отмена» / «✓ Сохранить».
2. **`#instructions-modal-overlay` — «Настройки агента», `.modal-settings` (1040×86vh).** Двухколоночная: `.settings-nav` (**232px**, padding `12px 10px`, правый бордер, фон `--vf-surface-2`) + `.settings-content` (padding `22px 26px 26px`, секции `max-width:680px`). Пункты `.settings-nav-item` (padding `9px 10px`, r8, иконка 18px `--vf-text-4`, `<b>` 13px/600, `<small>` 11.5px; активный — белый фон + `--vf-shadow-1` + accent у иконки и заголовка):
   - **Основное** / «Имя, голос и голосовая модель» (`fa-microphone-lines`) — поля: «Имя агента» (hint «Так агент представляется клиентам и так называется в списке ваших агентов.»); «Голосовая модель» (hint «Кто ведёт живой телефонный разговор. Меняет голос, скорость и стоимость минуты.») → `.model-type-cards` из тех же `.type-card` (`padding:12px 14px;margin-bottom:8px`) с бейджем `.type-current` «текущая» (10.5px/600, uppercase, r999, accent-soft); hint при смене: «После сохранения голосовой ассистент будет пересоздан на новой модели: база знаний, коннекторы, задачи и номера перенесутся автоматически. Выберите голос ниже.» иначе «Минуты связи при звонках оплачиваются с баланса телефонии.»; затем контрол голоса.
   - **Оркестратор** / «Модель, инструкции, вебхук» (`fa-brain`) — заголовок секции «Оркестратор · текстовый чат-агент», подзаголовок «Мозг-планировщик: ведёт CRM, планирует звонки, готовит стратегию каждого звонка и анализирует результат. В живом телефонном разговоре эти инструкции не используются.»; «Модель оркестратора» (hint «Мозг» агента: планирует звонки, анализирует разговоры, отвечает в чате. Дороже модель — точнее решения.»); «Инструкции для оркестратора» (placeholder «Например: «перед обзвоном новых контактов всегда проверяй дубли», «не планируй звонки в выходные».», hint «Правила планирования и работы с контактами, которые не вписываются в документы компании.»); «URL вебхука» (placeholder `https://n8n.example.com/webhook/abc123`, hint «Оркестратор сможет отправлять события (заявки, лиды, результаты звонков) в n8n / Make / Zapier или любой HTTP endpoint через функцию send_webhook. Оставьте пустым, чтобы отключить.»).
   - **Звонки** / «Номер, первая фраза, поведение» (`fa-phone`) — «Звонки · живой разговор» + «Как агент ведёт себя в телефонном разговоре с клиентом: с какого номера звонит, с чего начинает и чего избегает.»; «Номер для исходящих (Caller ID)» (первый option «**Автоматически**», hint «С какого вашего номера агент звонит. «Автоматически» — первый подключённый номер.»); «Первая фраза для входящих звонков» (placeholder «Например: «Здравствуйте, {name}! Чем могу помочь?»», hint про `{name}`); «Инструкции для голосового агента».
   - **Публичный API** / «Приём заявок извне» (`fa-plug`) — «Публичный API · приём заявок извне» + «Шлите заявки с формы сайта или из вашей CRM по секретному ключу — агент сам решит, что с ними делать (создаст контакт, поставит звонок, ответит). Запросы — только со своего сервера (server-to-server).»; в `#i-public-access` — `.pub-row`, `.pub-copy-row` (инпут моно 12px + кнопка) и `.pub-code` (`pre`, фон `--vf-surface-2`, r9, моно 11.5px).
   Заголовки секций: `h3` 16px/700 + `p` 12.5px `--vf-text-3`, нижний бордер и `margin-bottom:18px`.
   **Контрол голоса** (`voiceControlHtml`): для gemini/openai/yandex/cascade — `<select>` со списком и превью `.voice-preview` (flex, padding `10px 12px`, фон `--vf-surface-2`, бордер, r8) c `.voice-ava` 36×36 r50% (`g-m` градиент `#e3f0ff→#cfe2ff`, текст `#1f6fb2`; `g-f` `#fce7f3→#fbd0e6`, `#c03a86`; `g-n` `#edeafe→#ded8fb`, `#6c57c6`) и подписью «<Имя> · <Мужской|Женский|Нейтральный> голос» + краткое описание. Для `cartesia` и `fish` — текстовое поле Voice ID + ползунок «Скорость голоса» (0.5–1.5, шаг 0.1) + (у fish) «Режим синтеза»: `low`→«Быстрый старт», `balanced`→«Сбалансированный», `normal`→«Качественный».
   Списки голосов: OpenAI 10 (alloy, echo, marin, cedar, shimmer, ash, ballad, coral, sage, verse); Gemini 30 (Zephyr…Sulafat); Yandex 15 (marina, dasha, alexander, julia, lera, masha, anton, kirill, filipp, ermil, jane, omazh, zahar, madi_ru, saule_ru); Cascade 2 (Anna, Sergey). Дефолты `VOICE_DEFAULTS = {gemini:'Kore', openai:'alloy', yandex:'marina', cascade:'Anna'}`.
3. **`#telegram-modal-overlay`** `.modal-lg` — «Telegram-бот агента» (`fab fa-telegram`).
4. **`#contact-modal-overlay`** (560) — «Добавить контакт»: «Телефон *» (ph `+79001234567`), «Имя» (ph «Иван Петров»), `.form-row` «Компания»/«Должность», «Заметки». Футер «Отмена» / «+ Добавить».
5. **`#calls-modal-overlay`** `.modal-lg` — «История звонков». Таблица `.calls-table` (12.5px; `th` 11px uppercase на `--vf-surface-2`), бейджи `.status-badge` (h22, r999, 11px/600): `.badge-no-answer` danger-soft, `.badge-answered/.badge-success` success-soft, `.badge-followup` accent-soft; раскрытая строка `.transcript-row td` — фон `--vf-surface-2`, padding `14px 18px`, `white-space:pre-wrap`.
6. **`#tasks-cal-modal-overlay`** `.modal-cal` (880) — «Календарь задач» (`far fa-calendar-alt`). `.tcal-wrap` (flex gap 18): слева `.tcal-side` (**300px**) с `#tcal-side-head` «**Выберите день**», `#tcal-side-sub` «Дни с задачами отмечены в календаре», пустое `.tcal-empty` «Нажмите на день с задачами, чтобы посмотреть список»; справа `.tcal-main` с `.tcal-month` (15px/600, capitalize), `.tcal-nav` (две кнопки 30×30 r7), `.tcal-dow` (`grid repeat(7,1fr)`, gap 5, «Пн Вт Ср Чт Пт Сб Вс», 10.5px/600 uppercase), `.tcal-grid` (`repeat(7,1fr)`, gap 5) с `.tcal-day` (`aspect-ratio:1`, r8, фон `--vf-surface-2`; `.has` — accent-soft + 700; `.today` — бордер accent; `.sel` — accent/белый) и счётчиком `.tcal-count` (мин 16×16, r8, accent/белый, 9.5px/700). `.tcal-warn` (warning), `.tcal-more`, `.tcal-del-day` (danger-soft, hover → сплошной danger). Футер: «🗑 Удалить все задачи» (слева, `margin-right:auto`) / «Закрыть».
7. **`#contacts-list-modal-overlay`** `.modal-lg` — «Контакты агента» + счётчик; панель: поиск (ph «🔍 Поиск по имени, телефону или компании...») + «🗑 Удалить все» + «Импорт» + «+ Добавить».
8. **`#contact-details-modal-overlay`** `.modal-lg`, **z-index 200** (поверх воронки). Футер: «Удалить контакт» / «Закрыть». Внутри — задачи `.cd-task`, форма новой задачи `.cd-task-new` (пунктирный бордер, r8, фон `--vf-surface-2`), SMS-переписка `.cd-sms-thread` (`.cd-sms` max-w 80%, padding `8px 11px`, r12; `.cd-sms-in` — surface-2 + бордер + левый нижний 4px; `.cd-sms-out` — accent/белый + правый нижний 4px).
9. **`#pipeline-modal-overlay`** `.modal-pipeline` (96vw) — «Воронка контактов · перетащите карточку, чтобы сменить стадию» (`fa-stream`). `.pl-board` (flex, горизонтальный скролл, min-h 300), колонки `.pl-col` (`flex:0 0 230px`, фон `--vf-surface-2`, r10, max-h 68vh; `.pl-over` — `outline:2px dashed var(--vf-accent)`), `.pl-col-head` (padding `10px 12px`, 13px) + `.pl-count` (r10, 11px), карточки `.pl-card` (surface, r8, padding `9px 10px`, `cursor:grab`; `.dragging` — `opacity:.45`), `.pl-empty`. Drag&drop нативный HTML5 (`plDragStart/Over/Leave/Drop`), без Sortable.js.
   Стадии (`STAGE_META` в `core.js`, зеркало `backend/core/pipeline_stages.py`): `new` «Новый» `#6B7280`; `active` «В работе» `#3B82F6`; `success` «Успех» `#10B981`; `rejected` «Отказ» `#EF4444`; `do_not_call` «Не звонить» `#1F2937`; (легаси) `calling` «Дозвон» `#F59E0B`. Бейдж рисуется как `background:<color>22; color:<color>`.
10. **`#import-modal-overlay`** `.modal-lg` — «Импорт контактов» (`fa-file-import`), 3 шага.
    Шаг 1: инфоблок (`background:var(--bg);border-radius:10px;padding:14px;font-size:12.5px`) «**Формат файла (.xlsx или .csv):**» + «Колонки: **Имя, Телефон\*, Компания, Должность, Информация о клиенте, Задача, Описание задачи, Когда звонить**. Обязателен только телефон. Если колонки «Когда звонить» нет — звонки распределятся автоматически (старт через час, параллельно по 5).», «🕐 **Время указывается по МСК (Москва, UTC+3)**» (цвет `--blue`), «Лимит: до **1000 контактов** за один импорт.», «Для импорта на балансе должны быть **кредиты оркестратора** — ими оплачивается работа AI-оркестратора по каждому контакту: подготовка стратегии перед звонком и анализ результата после (≈30 кредитов на контакт). Точная стоимость будет показана на шаге предпросмотра.»; кнопка «⬇ Скачать шаблон»; дропзона `#import-dropzone` — `border:2px dashed var(--border);border-radius:12px;padding:28px;text-align:center`, иконка `fa-cloud-arrow-up` 28px accent, текст «**Перетащите файл сюда или нажмите для выбора**».
    Шаг 2: сводка + тумблер «**Проставлять авто-задачи через оркестратора**» (`.switch`, фон `var(--bg)`, r10, padding `12px 14px`) + чекбокс «Я согласен с переносом задач на ближайший рабочий день».
    Шаг 3: `.spinner` + «**Импортируем контакты…**» → `fa-circle-check` 40px зелёный + результат.
    Футер: «Назад» / «⬇ Скачать список ошибок» / «Отмена» / «▶ Начать импорт» / «Закрыть».
11. **`#credits-modal-overlay`** (640) — «Пополнить кредиты» (`fa-coins`). `.credits-pkg-grid{grid-template-columns:repeat(auto-fill, minmax(150px,1fr));gap:10px}`; `.credits-pkg` (бордер, r12, padding 14, по центру): `.pkg-name` 14px/600, `.pkg-credits` (warning, 600, tabular-nums), `.pkg-price` (13px `--vf-text-3`), кнопка 100% h32 r8 accent. Пусто — «Нет доступных пакетов».
12. **`#billing-modal-overlay`** (640) — «История кредитов» (`fa-receipt`). Строки `.credits-tx-row` (flex space-between, padding `8px 0`, нижний бордер, 13px), сумма `.credits-tx-amt.pos/.neg/.zero`. Типы операций: «Покупка», «Подписка», «Списание», «Возврат», «Система». Пусто — «Нет транзакций».
13. **`#kb-modal-overlay`** `.modal-lg` — «База данных агента» (`fa-database`): «Название базы (опционально)» (maxlength 100, ph «Например: Описание продукта»), «Содержимое базы знаний» (`rows=14`, `min-height:220px`, ph «Вставьте сюда текст: описание продукта, услуги, цены, частые вопросы и ответы, регламенты…\n\nАгент будет искать по этому тексту во время звонка и в чате с вами.»), подпись «После сохранения текст преобразуется в векторную базу. Поиск по ней подключается к голосовому агенту автоматически. Лимит — **200 000 символов**.» + счётчик «0 / 200 000». Футер: «🗑 Удалить базу» / «Отмена» / «✓ Сохранить».
14. **`#memory-modal-overlay`** `.modal-lg` — «Что агент запомнил» (`fa-brain`). Пояснения: «Рабочий блокнот агента. Он перечитывает его перед каждым звонком, сообщением и ответом в чате и сам ведёт по ходу работы. Скажите в чат «по пятницам не звони» — агент запишет это в **«Вы поручили»** и будет соблюдать. Любую заметку можно исправить или удалить.» и (на фоне `--vf-surface-2`, r8) «**Где что хранить.** Постоянное о компании и продукте — в «Документах агента». Разовые поручения — просто говорите в чат, агент запишет их сюда сам.». Форма добавления: select секции (200px) + textarea (maxlength 400, ph «Например: не предлагай скидку в первом звонке») + кнопка «+ Добавить», заголовок «Добавить поручение или заметку вручную». Футер: «🗑 Очистить всё» / «Закрыть». Секции API: «Вы поручили», «Агент заметил», «Агент планирует».
15. **`#connectors-modal-overlay`** (520) — «Коннекторы агента» (`fa-plug`), пояснение «Подключите внешние сервисы — агент сможет создавать события, отправлять письма и писать клиентам в Telegram с вашего личного аккаунта.».
16. **`#tg-account-modal-overlay`** (480) — «Личный Telegram» (`fa-paper-plane` цвет **`#229ED9`**), 3 шага (телефон → код → пароль 2FA).
17. **`#max-account-modal-overlay`** (480) — «Личный MAX» (`fa-comment-dots` цвет **`#6D28D9`**), 3 шага с опросом статуса.

## 1.6 UI-хелперы страницы агента
- **Свой тост** `showToast(msg, type)` в `core.js` (НЕ `VF.toast`): `#toast`, `position:fixed;bottom:24px;left:50%;transform:translateX(-50%);padding:11px 22px;border-radius:11px;font-size:13px;z-index:9999;box-shadow:0 6px 20px rgba(0,0,0,.2)`, фон `#166534` (success) / `#991B1B` (error) / `#1a1a2e` (иначе), автоскрытие 3500 мс.
- Подтверждения — **нативные `window.confirm`/`alert`** (не `VF.confirm`), см. `handle402`, `deleteAgent`, `clearAgentMemory` и т.д.
- `VF.logo(...)` используется в: карточках выбора модели (визард и настройки), `.type-badge` в шапке агента и в дропдауне агентов.
- `renderMarkdown` (marked + DOMPurify, `gfm:true, breaks:true`, таблицы оборачиваются в `.md-table-wrap`, внешние ссылки `target=_blank rel=noopener noreferrer`).
- Даты: всё в МСК (`Intl.DateTimeFormat('ru-RU', {timeZone:'Europe/Moscow'})`), суффикс « МСК»; `relTime` даёт «сейчас», «Через N мин», «Через N ч M мин», «Через N день/дня/дней».
- Тексты ошибок `errText` (словарь): «Телефония не верифицирована. Настройте её перед созданием агента.», «Нужен Google Gemini API ключ.», «Нужен OpenAI API ключ.», «Нужен Cartesia API ключ.», «Нужны API-ключ и Folder ID Yandex Cloud.», «Нужен API-ключ Fish Audio.», «Неверный тип ассистента.», «Неверная модель оркестратора.», «Агент уже существует.», «Достигнут лимит агентов (максимум 3).», «Агент не найден.», «Неподдерживаемый формат. Используйте .xlsx или .csv.», «Не удалось разобрать файл. Проверьте формат.», «Слишком много строк. Максимум 1000 за один импорт.», «База знаний слишком большая. Максимум 200 000 символов.», «Время на подтверждение истекло. Загрузите файл заново.», «В файле нет валидных контактов.», «Нет доступа.», «Недостаточно кредитов: нужно N, доступно M.».
- Прочие тосты: «Агент активирован» / «Агент остановлен» / «Не удалось изменить статус» / «Ошибка сети»; «Настройки сохранены»; «Ключ сохранён» / «Введите ключ» / «Ошибка сохранения ключа»; «Trial активирован! Вам доступно 1 500 кредитов на 3 дня.»; память — «Заметка добавлена/обновлена/удалена», «Блокнот очищен», «Введите текст заметки», «Заметка слишком длинная (до 400 символов)», «Достигнут лимит заметок — удалите лишние», «Такая заметка уже есть», «Память заполнена — удалите или сократите заметки»; БЗ — «База знаний сохранена/удалена», «Добавьте текст для базы знаний», «Удалить базу знаний? Это действие необратимо.»; Telegram — «Бот подключён/отключён/включён/выключен», «Чат добавлен/удалён», «Введите токен бота», «Невалидный токен бота. Проверьте токен от @BotFather.», «Этот бот уже подключён к другому агенту. У каждого агента должен быть свой бот.», «Сначала добавьте хотя бы один чат.»; коннекторы — «<Сервис> подключён/отключён», «Composio недоступен, попробуйте позже», «Не удалось получить ссылку авторизации», «Коннекторы не настроены на сервере», «Отключить <Сервис>? Агент перестанет использовать этот сервис.».

---

# 2. Страница «Голосовые ассистенты» — `/static/voice-assistants.html` (947 строк)

## 2.1 Назначение и подключения
- **Назначение:** единая страница для ассистентов всех пяти провайдеров (заменяет `agents.html`, `gemini-agents.html`, `fish-agents.html`, `yandex-agents.html`, `cascade.html`): сетка карточек → редактор с 5 вкладками.
- **URL:** `/static/voice-assistants.html`; поддерживает query-параметры `?id=<id>&model=<type>` и `?tab=<settings|functions|knowledge|test|embed>`; `history.replaceState` синхронизирует URL при выборе ассистента.
- `<html lang="ru" data-vf-loader>` — включает загрузчик VF (скрывается вызовом `VF.ready()`), `<body class="vf">` (значит нативные select автоматически становятся `VF.select`).
- Title «**Голосовые ассистенты | Voicyfy**», `robots noindex,nofollow`, `theme-color #2563eb`, manifest, apple-touch-icon.

**Ресурсы:** только `https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Syne:wght@700;800&display=swap` (**Inter 400/500/600/700 + Syne 700/800**, без Unbounded), `/static/css/voicyfy.css`, `/static/js/ui.js`, плюс `/static/js/sidebar.js` инлайн после `<aside>`. **Font Awesome не подключается вовсе** — все иконки берутся из SVG-спрайта `/static/icons/ui.svg` через `<use>` или `VF.icon()`. Весь JS страницы — один инлайн `<script>` (IIFE) в конце документа. Виджеты тестирования грузятся динамически: `/static/widget.js` (OpenAI) или `/static/gemini-widget.js` (остальные, у кого есть виджет).

## 2.2 Скелет
```
.vf-app
 └ aside.sidebar.vf-sidebar#sidebar  (vf-sidebar-head: .vf-logo, кнопка закрытия; nav#sidebar-nav; .sidebar-footer «Выйти»)
 └ main.vf-main
    ├ header.vf-topbar  (mobile-toggle + h1 «Голосовые ассистенты» + .chip.chip-outline#usage-pill; справа hidden #list-search + .btn-primary#btn-new «Создать»)
    └ .vf-content
       └ .layout#layout  (стартует в .grid-mode)
          ├ section.card.list-panel  (.list-head h2 «Мои ассистенты» + #list-count; .list-body#list-body)
          └ section.card.editor#editor (.editor-head, .tabs#tabs, .editor-body с 5 .tab-pane, .save-bar)
.vf-backdrop#kb-modal > .vf-dialog.vf-dialog-lg   (модалка базы знаний)
```

**Сетки и режимы:**
- `.layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px;align-items:start}`
- `.layout.grid-mode{grid-template-columns:1fr}` + `.editor{display:none}` — это **режим витрины карточек**; `.list-panel` теряет фон/бордер/тень/sticky, `.list-head` → `padding:0 0 14px` без бордера, `h2` 15px.
- `.list-panel` в режиме сплита: `position:sticky; top:calc(var(--vf-topbar-h) + 20px); max-height:calc(100vh - var(--vf-topbar-h) - 40px)`; `.list-body{overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:2px}`.
- **`.layout.grid-mode .list-body{display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:14px;padding:0;overflow:visible}`** ← сетка карточек.

**Брейкпоинты страницы:**
- **`@media (max-width:1100px)`** — `.layout{grid-template-columns:260px minmax(0,1fr);gap:14px}`.
- **`@media (max-width:960px)`** — `.layout{grid-template-columns:1fr}`; `.list-panel` перестаёт быть sticky; в режиме сплита `.list-body` становится горизонтальной лентой (`flex-direction:row;overflow-x:auto;gap:8px`), карточки `min-width:220px`; `.editor-body{padding:16px 14px 8px}`; `.editor-head`/`.save-bar` → `padding:12px 14px`; `#usage-pill` скрыт. (Плюс глобальный 960px из voicyfy.css — сайдбар уезжает.)

## 2.3 АНАТОМИЯ КАРТОЧКИ АССИСТЕНТА (`.a-card`) — детально

**Как рендерится:** функция `renderList()` собирает HTML **template-литералом** и присваивает `body.innerHTML = rows.map(...).join('')`; затем вешает слушатели `click` и `keydown(Enter)` на каждую `.a-card`. `createElement` не используется.

**HTML одной карточки:**
```html
<div class="a-card[ active]" data-id="…" data-type="openai|gemini|fish|yandex|cascade" tabindex="0">
  <div class="a-top">
    {VF.logo(type, {size:20})}                 <!-- .logo-wrap 34×34 r10 + img.logo 20×20 -->
    <div class="a-main">
      <div class="a-name"><span class="truncate">Имя</span>
        [<span class="dot" data-tip="Выключен"></span>]   <!-- если is_active === false -->
      </div>
      <div class="faint small">Gemini|OpenAI|Fish Audio|Яндекс|Каскад</div>
    </div>
  </div>
  <div class="a-desc">описание / первые 160 символов промпта / «Без описания»</div>
  <div class="a-meta">
    [<span class="chip[ chip-success]">1.9 ₽/мин | Бесплатно | На вашем API-ключе</span>]
    <span class="faint" style="margin-left:auto">дд.мм.гггг</span>
  </div>
</div>
```

**Стили (базовые):** `display:flex;gap:12px;align-items:flex-start;padding:10px 12px;border-radius:var(--vf-r-md);border:1px solid transparent;cursor:pointer;transition: background/border-color/box-shadow/transform var(--vf-fast)`; hover → `background:var(--vf-surface-2)`; `.active` → `background:var(--vf-accent-soft); border-color:rgba(37,99,235,.25)`.
- `.a-top{display:flex;align-items:center;gap:12px;width:100%;min-width:0}`
- `.a-name{font-weight:600;font-size:13.5px;display:flex;align-items:center;gap:8px}`, `.a-name .truncate{flex:1}`
- `.a-meta{display:flex;align-items:center;gap:6px;margin-top:4px;font-size:12px;color:var(--vf-text-3);flex-wrap:wrap}`
- `.a-desc{display:none}` по умолчанию.

**В режиме витрины (`.layout.grid-mode .a-card`):** `background:var(--vf-surface); border-color:var(--vf-border); box-shadow:var(--vf-shadow-1); padding:16px; border-radius:var(--vf-r-lg) (16px); flex-direction:column; gap:12px; min-height:148px`. Hover → `box-shadow:var(--vf-shadow-2); transform:translateY(-1px); border-color:var(--vf-border-strong)`. Описание становится видимым: `display:-webkit-box; -webkit-line-clamp:2; font-size:12.5px; color:var(--vf-text-3); line-height:1.45; flex:1`. Мета прижимается вниз: `margin-top:auto; padding-top:10px; border-top:1px solid var(--vf-border); width:100%`. `.empty` растягивается `grid-column:1 / -1`.

**В режиме сплита (`.layout:not(.grid-mode) .a-card`):** `flex-direction:column; gap:4px`, мета с отступом `margin:0 0 0 48px` (выравнивание под логотип).

**Логотип провайдера:** `VF.logo(a.type, {size:20})` → `<span class="logo-wrap" style="width:34px;height:34px"><img class="logo logo-color" src="/static/icons/models/<gemini-color|openai|fishaudio-color|yandex-color|cascade-color>.svg" alt="…" style="width:20px;height:20px"></span>`. `.logo-wrap` по умолчанию: `border-radius:10px; background:var(--vf-surface-2); border:1px solid var(--vf-border)`.

**Чип цены (`fmtPrice(tariff)`):** `own_key` → «**На вашем API-ключе**»; `price_rub_per_min == 0/none` → «**Бесплатно**»; иначе «**N ₽/мин**». Класс `chip-success` (зелёный) даётся, когда тариф бесплатный или на своём ключе; иначе нейтральный `.chip`.

**Статус-пилюля:** отдельной пилюли нет — неактивный ассистент помечается точкой `.dot` (8×8, r50%, `--vf-text-4`) с тултипом `data-tip="Выключен"`. Активность правится в редакторе тумблером «Активен».

**Кнопок действий и «кебаб»-меню в карточке нет.** Все действия — в редакторе: кнопка `#btn-more` (иконка `sliders-horizontal`, `data-tip="Ещё"`) открывает `VF.menu` с пунктами: «**Открыть телефонию**» (icon `phone`), «**Скопировать промпт**» (`copy`), разделитель, «**Удалить ассистента**» (`trash-2`, danger).

**Пустые состояния списка** (`.empty` из voicyfy.css: padding 40/20, по центру):
- Нет ассистентов вообще: `.empty-title` «**Пока нет ассистентов**», `.empty-text` «Создайте первого: выберите модель, задайте промпт и подключите функции.», кнопка «**+ Создать ассистента**» (`#btn-new-empty`).
- Поиск ничего не нашёл: «**Ничего не найдено**» / «Измените запрос в поиске.» (поле поиска `#list-search` сейчас `type=hidden`, то есть UI поиска скрыт, но логика сохранена).

Скелетон при загрузке: `VF.skeleton($('list-body'), 6)`.

## 2.4 Топбар и счётчик лимита
`#usage-pill` (`.chip.chip-outline`, `data-tip="Лимит ассистентов по тарифу"`) — текст «**Без лимита**» либо «**N / M**» (из `/api/subscriptions/assistants-usage`). Кнопка «**+ Создать**» (`btn btn-primary`, иконка `i-plus`, текст в `<span class="btn-text">`, который на мобиле прячется правилом voicyfy.css).

## 2.5 Редактор — шапка, табы, save-bar
- **`.editor{display:flex;flex-direction:column;min-height:60vh}`** на базе `.card`.
- `.editor-head{padding:14px 20px;border-bottom:1px solid var(--vf-border);display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}`; `h2#editor-title` — **шрифт Syne, 17px, ls -0.01em**, `.truncate`; слева кнопка назад `#btn-back` (`arrow-left`, `data-tip="Все ассистенты"`), затем `#editor-logo` (`VF.logo(model,{size:18})`). Справа: лейбл-тумблер `#active-wrap` со словом «**Активен**» + `.switch#f-active`; кнопка `#btn-more`. Для нового ассистента `#active-wrap` и `#btn-more` скрыты, заголовок — «**Новый ассистент**».
- **`.tabs#tabs`** (компонент voicyfy.css: нижний бордер, `.tab` padding `12px 12px`, 13.5px/500, активный — accent + нижний бордер 2px + 600; `.disabled{opacity:.45}`). Пять вкладок с иконками спрайта:
  | data-tab | Иконка | Подпись |
  |---|---|---|
  | `settings` | `i-sliders-horizontal` | **Настройки** |
  | `functions` | `i-puzzle` | **Функции** |
  | `knowledge` | `i-book-open` | **База знаний** |
  | `test` | `i-play` | **Тестирование** |
  | `embed` | `i-code` | **Встраивание** |
  Вкладки «Тестирование» и «Встраивание» получают класс `.disabled`, если у модели нет виджета (`modelHasWidget`: только OpenAI и Gemini).
- **`.editor-body{padding:22px 20px 8px}`**; блоки `.block{margin-bottom:26px}` с заголовком `.block-title` (12px/600, uppercase, ls .05em, `--vf-text-4`) и опциональным `.model-name` (без uppercase, `--vf-text-3`, 500).
- **`.save-bar`** — `position:sticky;bottom:0; background:color-mix(in srgb, var(--vf-surface) 88%, transparent); backdrop-filter:blur(8px); border-top:1px solid var(--vf-border); padding:12px 20px; display:flex; justify-content:space-between; border-radius:0 0 var(--vf-r-lg) var(--vf-r-lg)`. Слева `.state#dirty-state` (12.5px, `--vf-text-4`): «✓ **Сохранено**» (иконка `circle-check`) / при `.dirty` (цвет `--vf-warning`) «⚠ **Есть несохранённые изменения**» (иконка `circle-alert`). Справа кнопки «**Отмена**» (`#btn-cancel`) и «**✓ Сохранить**» (`#btn-save`, primary; при сохранении — `<span class="spin"></span> Сохраняем`). Горячая клавиша **Ctrl/Cmd+S** сохраняет (только когда редактор открыт); `beforeunload` предупреждает о несохранённом.

## 2.6 Вкладка «Настройки»
- Блок «**Основное**»: `.form-row` (grid `1fr 1fr`, gap `0 16px`) с полями «**Имя ассистента**» (ph «Менеджер по записи») и «**Описание**» (ph «Кратко, зачем нужен ассистент»); поле «**Первая фраза**» (ph «Здравствуйте! Чем могу помочь?»; дефолт при создании — `DEFAULT_GREETING = 'Здравствуйте! Чем я могу вам помочь?'`); «**Системный промпт**» — `textarea#f-prompt` `min-height:220px`, ph «Кто ассистент, как он говорит, что делает и чего не делает», под ним `.char-count` «<N> **символов**».
- Блок «**Голосовая модель**» → **`.models{display:grid;grid-template-columns:repeat(auto-fill, minmax(140px,1fr));gap:10px}`**.
  Карточка `.model`: `position:relative;padding:12px;border:1px solid var(--vf-border);border-radius:12px;background:var(--vf-surface);display:flex;flex-direction:column;gap:8px`, `role="radio"`, `tabindex=0`; hover → бордер `--vf-border-strong` + `--vf-shadow-2`; `.selected` → бордер accent + фон accent-soft + `box-shadow:0 0 0 1px var(--vf-accent)`.
  Содержимое: `.m-check` (абс. 10/10, 20×20, r50%, accent/белый, показывается только у выбранной) · `.m-head` (лого `VF.logo(code,{size:18})` + `.m-name` 14px/600) · цена — либо `.m-own` («**На вашем API-ключе**», 12.5px/600 success + `<small>` «с кошелька не списывается»), либо `.m-price` (**Syne 18px/700**, `letter-spacing:-.02em`) «N ₽<small>/ мин</small>» или «0 ₽<small>бесплатно</small>», и при платной модели (кроме cascade) ссылка `.m-key-link` «🔑 **Подключить свой ключ**» → `/static/settings.html#provider-keys` (11.5px, hover accent) · `.m-desc` (12px, `min-height:2.8em`) · `.m-tags` — чипы: опциональный `chip-accent` с `t.badge`, далее «**Виджет**» + «**Телефония**» либо «**Только телефония**», и для fish дополнительный чип «**Клон голоса**» (title «Можно клонировать свой голос в кабинете Fish Audio»). Есть также `.m-badge` (абс. 10/10, 10.5px/600, r999, `--vf-surface-3`), скрывается у выбранной.
  Под сеткой — `.note` («Цена модели списывается с кошелька за минуту разговора. Минуты связи при звонках оплачиваются с баланса телефонии. Если в профиле указан ваш API-ключ провайдера, модель работает на нём и с кошелька не списывается.») и скрытый `.note.note-warning#switch-warn` («При смене модели ассистент будет пересоздан с новым ID, привязанные номера телефонии перепривяжутся автоматически.»).
  **Порядок моделей** — `MODEL_ORDER = ['cascade','gemini','fish','yandex','openai']`, реально сортируется по `sort_order` из `/api/wallet/tariffs/me`.
- Блок «**Голосовые настройки**» + `· <название модели>` в заголовке; содержимое зависит от провайдера:
  - **OpenAI / Gemini / Яндекс** — `.voices{display:grid;grid-template-columns:repeat(auto-fill, minmax(140px,1fr));gap:8px}`; `.voice` (flex space-between, padding `9px 12px`, бордер, r8, 13px; `.selected` → бордер accent + фон accent-soft + 600 + текст accent), `small` 11px `--vf-text-4`. Gemini-голоса дополнительно подписаны «жен.»/«муж.» (30 голосов с гендером), OpenAI — 10 голосов с капитализацией, Яндекс — список из `/api/yandex-assistants/options` c фолбэком в 15 имён. У Gemini под сеткой хинт «Подмодель и режим размышлений скрыты намеренно ради скорости ответа.»; у Яндекса — «Модель распознавания и синтеза: **speech-realtime-260528**.»
  - **Fish Audio** — `.form-row` с «**Voice ID (Fish Audio)**» (ph «reference_id голоса из fish.audio», hint «Пусто — голос по умолчанию. Можно клонировать свой голос: загрузите образец в кабинете Fish Audio и вставьте сюда reference_id.») и «**Модель синтеза**» (VF.select; hint «Единственная доступная модель Fish Audio — премиальная s2.1-pro.»); «**Задержка**» (VF.select: low/balanced/normal); «**Скорость речи**» (`range` 0.5–2, шаг 0.05) и «**Температура**» (0–1, шаг 0.05) в `.range-row` (flex gap 12) с `.range-val` (min-w 42, right, 600, tabular-nums).
  - **Каскад** — «**Голос**» (VF.select по голосам провайдера VoxTTS; подписи «жен.»/«муж.»); «**Пауза перед ответом**» — `.presets` (flex wrap gap 8) из `.preset` (padding `10px 14px`, бордер, r8, 13px/500; `small` — 11.5px `--vf-text-4`; `.selected` → accent-бордер/accent-soft/accent): **«Быстрая» 300 мс · «Сбалансированная» 650 мс · «Терпеливая» 1000 мс** (`SILENCE_PRESETS`); hint «Меньше пауза, быстрее ответ, но ассистент чаще перебивает. Распознавание и синтез в Каскаде оплачиваются с баланса телефонии.»
  - Фолбэк: «Для этой модели нет дополнительных голосовых настроек.»

## 2.7 Вкладка «Функции»
Заголовок «**Функции ассистента**», подзаголовок «Набор функций одинаков для всех моделей. Ассистент вызывает включённые функции сам по ходу разговора, опишите в промпте, когда их использовать.». `.funcs{display:grid;grid-template-columns:repeat(auto-fill, minmax(290px,1fr));gap:10px}`; `.func` — `<label>`: flex gap 12, padding `12px 14px`, бордер, r12; hover → `--vf-border-strong`; `.on` → `border-color:rgba(37,99,235,.35); background:var(--vf-accent-soft)`. Внутри `.switch` + `.f-name` (13.5px/600) + `.f-desc` (12.5px, `--vf-text-3`). Каталог грузится из `/api/functions/`; если пусто — `.empty` «**Каталог функций недоступен**».
Условный блок `#sheet-block` (показывается, если включена функция с `sheet` в имени): «**ID Google-таблицы**» (ph «1AbC…xyz из ссылки на таблицу», hint «Для функции «Google Таблицы». Таблица должна быть доступна сервисному аккаунту Voicyfy.»).

## 2.8 Вкладка «База знаний»
Заголовок «**Базы знаний**» + кнопка `.btn.btn-sm#kb-new` «**+ Новая база**». `.note`: «Чтобы подключить базу, включите функцию «Поиск по базе знаний» и добавьте в промпт строку `Pinecone namespace: <namespace>`. Кнопка «В промпт» сделает это за вас.» Таблица `.table` в `.table-wrap` с колонками: **Название · Символов · Namespace · Обновлена · (действия)**. В строке: жирное имя, число с `toLocaleString('ru-RU')`, `.mono` namespace + кнопка копирования (`copy`, `data-tip="Копировать"`), дата, и справа: «**→ В промпт**» (`arrow-right`), иконка-кнопка редактирования (`pen`, `data-tip="Редактировать"`), иконка-кнопка удаления (`trash-2`, `.btn-danger`, `data-tip="Удалить"`). Пусто: «**Баз знаний пока нет**» / «Создайте первую и вставьте её namespace в промпт ассистента.».
Тосты: «Namespace скопирован» / «Не удалось скопировать»; при вставке — `VF.toast('Строка добавлена в промпт. Не забудьте сохранить ассистента.', {type:'success', action, actionLabel:'К промпту'})`.

## 2.9 Вкладки «Тестирование» и «Встраивание»
- **Тестирование:** если ассистент не сохранён — `.empty` «**Сначала сохраните ассистента**»; если модель без виджета — `.empty` с иконкой `phone` `ic-lg`: «**Модель «X» работает только в телефонии**» + «Привяжите ассистента к номеру и позвоните на него, чтобы протестировать разговор.» + кнопка «**Перейти в Телефонию**»; если сменили модель и не сохранили — «Сохраните смену модели, затем протестируйте». Иначе — `.empty` с иконкой `headset`: «**Виджет запущен в правом нижнем углу**» / «Нажмите на кнопку виджета и говорите. <Модель работает на вашем API-ключе, с кошелька не списывается. | Тестирование тарифицируется как обычный разговор: N ₽/мин с кошелька. | Тестирование бесплатно.>» + `#widget-status` «**Загружаем виджет**» → «Виджет загружен» / «Не удалось загрузить виджет». Скрипт виджета создаётся динамически с `data-assistant-id` / `data-assistantId`, `data-server`, `data-position="bottom-right"`; `teardownWidgets()` чистит `#wellcomeai-widget-container`, `#gemini-voice-widget`, `#fsw-*` и т.п.
- **Встраивание:** скелетон `height:120px`, затем текст «Вставьте код перед закрывающим тегом `</body>` на вашем сайте. Разговоры посетителей списываются с вашего кошелька по цене модели.» и `.code-box` — `pre` с `background:#0f172a; color:#e2e8f0; padding:16px; border-radius:12px; font-size:12.5px; white-space:pre-wrap; font-family:var(--vf-font-mono); line-height:1.5` и кнопкой «**Копировать**» абсолютом `top:10px;right:10px`. Тост «Код скопирован».

## 2.10 Модалка базы знаний и диалоги
- **`#kb-modal`** — `.vf-backdrop` + `.vf-dialog.vf-dialog-lg` (**max-width 760px**), открывается `VF.modal('kb-modal')`. Заголовок «**Новая база знаний**» / «**Редактировать базу знаний**». Поля: «Название» (ph «Прайс и условия», `autofocus`), «Содержимое» (`textarea` `min-height:300px`, ph «Описание услуг, цены, ответы на частые вопросы») + `.char-count` «N символов». Футер: «Отмена» (`data-close`) / «✓ Сохранить».
- **`VF.confirm` диалоги:**
  - «**Несохранённые изменения**» / «Продолжить без сохранения? Изменения будут потеряны.» / кнопка «Продолжить» (`warning`, icon `circle-alert`).
  - «**Сменить голосовую модель?**» / «Ассистент будет пересоздан на модели **X** с новым ID. Привязанные номера телефонии и тестовый номер перепривяжутся автоматически.» / «Сменить модель».
  - «**Достигнут лимит ассистентов**» / «Удалить текущего ассистента перед созданием на новой модели? Настройки перенесутся, номера перепривяжутся.» / «Удалить и создать» (danger).
  - «**Удалить ассистента?**» / ««<имя>» будет удалён без возможности восстановления. Номера, привязанные к нему, останутся без ассистента.» / «Удалить» (danger).
  - «**Удалить базу знаний?**» / ««<имя>» будет удалена из Pinecone. Ассистенты, у которых её namespace в промпте, перестанут находить ответы.» / «Удалить» (danger).
- **Тосты:** «Укажите имя ассистента» (warning), «Заполните системный промпт» (warning), «Ассистент создан», «Сохранено», «Ассистент удалён», «База знаний сохранена», «База знаний удалена», «Добавьте содержимое базы» (warning), «Промпт скопирован», «Не удалось скопировать», «Модель изменена», «Модель изменена, перепривязано номеров: N», «Новый ассистент создан, но N номер(ов) не удалось перепривязать…, старый ассистент сохранён. Проверьте Телефонию.», «Новый ассистент создан, старый удалить не удалось. Удалите вручную.», «Достигнут лимит ассистентов по тарифу (N из M). Удалите лишних или обновите тариф.», «Не удалось получить список номеров телефонии, смена модели отменена. Повторите попытку.», фолбэк ошибок «Ошибка запроса».

**Провайдеры (`PROVIDERS`), эндпоинты и наличие виджета:**
| code | name | list/create | item | listShape | widget | options |
|---|---|---|---|---|---|---|
| openai | OpenAI | `/api/assistants/` | `/api/assistants/{id}` | array | да | — (embed: `/embed-code`) |
| gemini | Gemini | `/api/gemini-assistants` | `…/{id}` | array | да | — (embed) |
| fish | Fish Audio | `/api/fish-assistants` | `…/{id}` | assistants | нет | `/api/fish-assistants/options` |
| yandex | Яндекс | `/api/yandex-assistants` | `…/{id}` | assistants | нет | `/api/yandex-assistants/options` |
| cascade | Каскад | `/api/grok-assistants/cascade` | `…/{id}` | array | нет | `…/tts-providers` |

## 2.11 Что на самом деле в `backend/static/agents/*.js` (легаси-страница `agents.html`)
Подключаются только из `agents.html` (`<script type="module">`). Эта страница со **своими** переменными (`--primary-blue:#2563eb`, `--text-dark:#0f172a`, `--bg-light:#f8fafc`, `--border-color:#e2e8f0`, `--radius-md:0.5rem`, `--radius-lg:1rem`, `--orange-500:#f97316`, `--shadow-sm/md/lg`), Font Awesome 6.4.0 CDN и Google Fonts `Syne:wght@600;700;800`; сайдбар 260px, фиксированный.
- **`agents/ui.js`** — объект `ui` (экспорт `export {ui}; export default ui`): `showNotification(message,type,duration=5000)` работает с DOM `#notification`/`#notification-message`, классы `notification-success|error|info|warning`, иконки `fas fa-check-circle | fa-exclamation-circle | fa-info-circle | fa-exclamation-triangle`; `hideNotification`; `switchTab(tabId)` (переключает `.tab[data-tab]` ↔ `#<tabId>-tab.tab-content`); `fillAgentForm`, `selectVoice` (`.voice-option.selected`), `loadFunctions`, `getFormData`, `validateForm`, `showValidationErrors`, `setLoading`, `showAgentsListSkeleton`, `showConfirmDialog(title,message,confirmText='Подтвердить',cancelText='Отмена')`, `showInfoDialog`, `copyToClipboard(text, 'Скопировано!')` + fallback, `formatDate`, `formatNumber`, `truncateText`, `scrollToElement`, `pulseElement`, `debounce`, `throttle`, `clearForm`, `createElementFromHTML`, `escapeHtml`.
- **`agents/index.js`** — карточки рисуются **через `createElement` + `innerHTML`** в `renderAgentsList(agents)`: контейнер `#agents-list`, заголовок `.agent-list-header > h4.agents-count` «OpenAI агентов: N (всего N/M)», список `.agent-items`, элемент `.agent-item` с `.agent-icon` (`fas fa-robot`), `.agent-info` (`h3.agent-name`, `p.agent-description` / «Нет описания», `.agent-meta` — `.agent-voice` `fas fa-volume-up` + голос, `.agent-date` `fas fa-calendar` + «Создан: дата»), `.agent-actions` — кнопка `.btn-outline.get-embed-code` (`fas fa-code`, title «Получить код для встраивания») и `.btn-primary.edit-agent` («✎ Редактировать»). Пустое состояние: `.empty-state` с `.empty-icon` (`fas fa-robot`), `h3.empty-title` «**У вас еще нет OpenAI агентов**», `.empty-description` «Создайте вашего первого голосового ассистента, чтобы встроить его на сайт или приложение. (N/M использовано)», кнопка «**+ Создать нового агента**».
- Табы `agents.html`: `.tabs > .tab[data-tab]` — «**Настройки**» (`settings`), «**Тестирование**» (`testing`), «**Встраивание**» (`embed`), «**Функции**» (`functions`); панели `#settings-tab`, `#testing-tab`, `#embed-tab`, `#functions-tab` (класс `.tab-content.active`).

---

# 3. Страница «Панель управления» — `/static/dashboard.html` (4255 строк)

## 3.1 Назначение и подключения
- **Назначение:** главная ЛК: онбординг-герой, три метрики, гид из трёх шагов, 9 статей-карточек с читалкой, блок подписки, модалки выбора тарифа / блокировки функции / истёкшей подписки.
- **URL:** `/static/dashboard.html`; поддерживает хэш `#article=<ключ>` (открывает статью).
- `<html lang="ru" data-vf-loader="auto">` (VF-загрузчик сам прячется после DOM+первых fetch), `<body class="vf">`.
- Title «**Панель управления | Voicyfy**», `robots noindex,nofollow`, `theme-color #2563eb`, полный набор favicon + manifest.

**Ресурсы (в порядке):**
1. `https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Syne:wght@700;800&display=swap` — **Inter 400/500/600/700 + Syne 700/800**.
2. Огромный инлайн `<style>` (строки 20–1980).
3. `/static/css/voicyfy.css`
4. `/static/css/voicyfy-legacy.css` ← мост старых классов на токены.
5. `/static/js/ui.js`
6. `/static/js/sidebar.js` (после `<aside>`).
7. Два инлайн `<script>`: основной (стр. 2948–4126: тарифы, api, ui, модалки, оплата) и «онбординговый» (стр. 4127–4253: иконки, гид, метрики, читалка статей).

**Font Awesome CDN НЕ подключён**, хотя разметка изобилует `fas fa-*` — иконки подставляет мост Lucide в `ui.js`. (Заметьте: `voicyfy-legacy.css` содержит правило `body.vf i.fas{font-family:"Font Awesome 6 Free"!important}` — это на случай страниц, где FA всё же есть.) Никаких chart-библиотек, Sortable, Lucide-скрипта и пр. на странице нет.

## 3.2 Локальные CSS-переменные (`:root` в инлайн-стиле) и их переопределение
```
--primary-blue:#2563eb        --primary-blue-light:#3b82f6   --primary-blue-dark:#1d4ed8
--accent-blue:#4a86e8         --gradient-blue: linear-gradient(135deg,#4a86e8,#2563eb)
--text-dark:#0f172a           --text-gray:#64748b            --text-light:#94a3b8
--bg-light:#f8fafc            --bg-blue-light:#eff6ff        --white:#ffffff
--border-color:#e2e8f0
--shadow-sm: 0 1px 2px 0 rgba(0,0,0,.05)
--shadow-md: 0 4px 6px -1px rgba(0,0,0,.1), 0 2px 4px -1px rgba(0,0,0,.06)
--shadow-lg: 0 10px 15px -3px rgba(0,0,0,.1), 0 4px 6px -2px rgba(0,0,0,.05)
--radius-md:.5rem   --radius-lg:1rem   --radius-full:9999px
--transition-sm: all .2s ease    --transition-md: all .3s ease
--green-500:#10b981 / --green-100: rgba(16,185,129,.1)
--orange-500:#f97316 / --orange-100: rgba(249,115,22,.1)
--red-500:#ef4444   / --red-100:  rgba(239,68,68,.1)
--purple-500:#8b5cf6 / --purple-100: rgba(139,92,246,.1)
```
**`voicyfy-legacy.css` переопределяет их под `body.vf`:** `--primary-blue→var(--vf-accent)`, `--text-dark→var(--vf-text)`, `--text-gray→var(--vf-text-3)`, `--text-light→var(--vf-text-4)`, `--bg-light→var(--vf-bg)` (т.е. фон страницы становится **белым**, а не `#f8fafc`), `--bg-blue-light→var(--vf-accent-soft)`, `--border-color→var(--vf-border)` (`#e3e8f0` вместо `#e2e8f0`), тени → `--vf-shadow-1/2/3`, `--radius-md→var(--vf-r-sm)` (8px вместо .5rem), `--radius-lg→var(--vf-r-lg)` (16px), `--green-500→var(--vf-success)` (`#16a34a`), `--red-500→var(--vf-danger)`, `--orange-500→var(--vf-warning)`.
Также legacy-мост перекрывает: `.sidebar{width:256px}`, `.main-content{margin-left:256px}`, `.top-nav{height:64px;padding:0 28px;position:sticky;backdrop-filter:saturate(160%) blur(10px);border-bottom:transparent}` (+ `.scrolled` даёт бордер и тень), `.page-title{font-family:Syne;font-size:22px;font-weight:700;ls:-0.02em}`, `.content-container{padding:24px 28px 40px}`, `.user-button{height:36px;border-radius:999px;border:1px solid var(--vf-border)}`, `.user-avatar{28×28;border-radius:50%;background:var(--vf-accent);color:#fff;12px/600}`, `.btn*` (h36, r8 и т.д.), `.card/.stat-card/.subscription-section{border-radius:16px;box-shadow:var(--vf-shadow-1)}`, `.stat-value{font-family:Syne;26px/700}`, `.notification{background:#0f172a;color:#e7ecf5}`, `.plan-locked-feature::after{display:none}` (вместо эмодзи-замка — svg-иконка `lock` из sidebar.js).

**Особые модификаторы страницы:** `body.subscription-expired{overflow:hidden}` и `.content-blocked{pointer-events:none;opacity:.5;filter:blur(2px)}` — при истёкшей подписке контент блюрится.

## 3.3 Скелет
```
aside.sidebar.vf-sidebar#sidebar   (vf-sidebar-head, nav#sidebar-nav, .sidebar-footer)
main.main-content#main-content
 ├ .top-nav  (button.mobile-toggle#sidebar-toggle «fa-bars» | h1.page-title «Панель управления» | .user-menu)
 └ .content-container#content-container
    ├ section.ob-hero#ob-hero
    ├ .ob-metrics (3 плитки)
    ├ section.ob-guide#ob-guide   (скрывается классом .ob-guide-hidden на контейнере)
    ├ .vf-backdrop#ob-article-backdrop > .vf-dialog.ob-article  (читалка)
    ├ 9 × <template data-art=… data-cat=… data-icon=…>
    └ .subscription-section
.notification#notification
.payment-modal#payment-modal              (z 9999)
.feature-blocked-modal#feature-blocked-modal (z 9999)
.subscription-expired-modal#subscription-expired-modal (z 10000)
```
`body{display:flex;min-height:100vh;line-height:1.5}`; `*{font-family:'Inter',…}` жёстко (поэтому legacy-мост отдельно возвращает Syne для `.vf-logo`, `.vf-wallet-balance`).

## 3.4 Топбар и меню пользователя
`.top-nav` — `background:var(--white); border-bottom:1px solid var(--border-color); padding:1rem 2rem; display:flex; justify-content:space-between; box-shadow:var(--shadow-sm)` (legacy → h64, sticky, blur). `h1.page-title` «**Панель управления**» (1.25rem/600 → legacy 22px Syne).
`.user-menu`: `.user-button` (flex gap .75rem, padding `.5rem 1rem`, r8) с `.user-avatar#user-avatar` (36×36 r50%, фон `--bg-blue-light`, текст accent, 600 → legacy 28×28 accent/белый), `.user-name#user-name` (заглушка «Александр Петров», подменяется именем) и `fa-chevron-down`. `.user-dropdown#user-dropdown` — `position:absolute; top:calc(100% + .5rem); right:0; min-width:180px; box-shadow:var(--shadow-md); display:none`, `.show` → `display:block` + анимация `dropdown-fade .2s` (`translateY(-10px)→0`). Пункты `.dropdown-item` (padding `.75rem 1rem`, .875rem): «**Профиль**» (`fa-user` → `/static/settings.html`), `.dropdown-divider`, «**Выйти**» (`fa-sign-out-alt`, `data-logout`).

## 3.5 Секция героя `.ob-hero`
`display:grid; grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr); gap:40px; padding:40px 44px; margin-bottom:28px; border-radius:20px; border:1px solid var(--vf-border)`; фон — два радиальных градиента (`900px 420px at 0% 0%, rgba(37,99,235,.08)` и `700px 380px at 100% 100%, rgba(37,99,235,.06)`) поверх `--vf-surface`; `::before` — сетка 28×28px из линий `rgba(15,23,42,.035)` с маской `radial-gradient(60% 80% at 70% 50%, #000 20%, transparent 100%)`.

**Левая часть:**
- `.ob-eyebrow` (12px/600, uppercase, ls .04em) + `.ob-eyebrow-dot` (8×8, accent, `box-shadow:0 0 0 4px rgba(37,99,235,.15)`): «**Voicyfy · платформа голосовых ИИ-ассистентов**».
- `h1.ob-title#welcome-message` — **Syne 800**, `font-size:clamp(28px, 3.2vw, 40px)`, `line-height:1.1`, `ls:-0.025em`: «**Добро пожаловать!**» (может подменяться именем в `loadUserData`).
- `p.ob-lead` (15.5px, lh 1.6, `--vf-text-2`, max-w 520): «Здесь вы собираете голосовых ассистентов, которые отвечают на звонки, сами обзванивают клиентов и разговаривают с посетителями сайта. Ключи провайдеров уже подключены — вы платите только за минуты разговора с кошелька.»
- `.ob-actions`: `.ob-btn.ob-btn-primary` «**Создать ассистента**» (иконка `plus`, h42, r10, accent, `box-shadow:0 6px 16px rgba(37,99,235,.25)`, hover `translateY(-1px)`) → `/static/voice-assistants.html`; `.ob-btn.ob-btn-ghost` «**Подключить номер**» (иконка `phone`, белый + бордер) → `/static/telephony.html`; `button.ob-link#ob-toggle-guide` «**Как это работает**» / после раскрытия «**Скрыть подсказки**» (h42, 14px/500, `--vf-text-3`, иконка `chevron-down`, которая в свёрнутом состоянии `rotate(-90deg)`).
- `.ob-models`: лейбл «**Работает на**» (12px, `--vf-text-4`) + чипы `.ob-model` (h30, r999, бордер, 12.5px/500) — рендерятся из `/api/wallet/tariffs/me`: `VF.logo(code,{size:14})` + название + `<b>` с ценой («ваш ключ» / «N ₽/мин» / «бесплатно»). Если тарифов нет — блок скрывается.

**Правая часть — имитация звонка** (`.ob-hero-visual`, скрывается на ≤1100px):
`.ob-call` — max-w 380, r18, бордер, `box-shadow:0 24px 60px -24px rgba(15,23,42,.28), 0 1px 2px rgba(15,23,42,.06)`, padding 16, **`transform:rotate(-1.2deg)`**.
- `.ob-call-head`: `.ob-call-avatar` (38×38, r12, accent-soft/accent, иконка `bot`), `.ob-call-name` «**Ассистент «Менеджер»**» (13.5px/600), `.ob-call-sub` «Входящий звонок · 00:42» с `.ob-live` (7×7 зелёная точка, `ob-pulse 1.8s`), справа `.ob-wave` из 5 полосок (3px, accent, анимация `ob-wave 1.1s`, задержки .12/.24/.36/.48s).
- `.ob-msgs` — 4 пузыря `.ob-msg` с каскадной анимацией `ob-msg-in .45s` (задержки .2 / .9 / 1.7 / 2.4 с): user «Здравствуйте, хочу записаться на завтра» → bot «Конечно! На завтра свободно в 12:00 и 16:30. Какое время удобнее?» → user «Давайте в 16:30» → bot «Записал вас на 16:30. Напомню за час до визита.». Пузыри max-w 84%, padding `8px 12px`, r14; user — `--vf-surface-3`, скруглён правый нижний 4px; bot — accent/белый, левый нижний 4px.
- `.ob-call-foot`: три `.ob-tag` (h24, r999, `--vf-surface-2`, 11.5px) с зелёными иконками: «**Запись в CRM**» (`square-check`), «**Календарь**» (`calendar`), «**Уведомление**» (`bell`).

## 3.6 Метрики `.ob-metrics`
`display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:0; border:1px solid var(--vf-border); border-radius:16px; overflow:hidden; margin-bottom:28px`; `.ob-metric{display:flex;gap:14px;padding:20px 22px}` с разделителем `border-left` между плитками. `.ob-metric-icon` 40×40 r12 accent-soft/accent; `.ob-metric-value` — **Syne 26px/700, ls -.02em**; `.ob-metric-title` 13px `--vf-text-3`; `.ob-metric-sub` 12px `--vf-text-4`, `min-height:1em`.
1. Иконка `bot` · `#bots-count` · «**Активных ассистентов**» · подпись `#bots-breakdown` — разбивка по типам из `ASSISTANT_TYPE_LABELS = {openai:'OpenAI', gemini:'Gemini', grok:'Grok', cascade:'Каскад', cartesia:'Cartesia', yandex:'Яндекс', translate:'Переводчики'}`, формат «OpenAI: 2, Gemini: 1».
2. Иконка `phone` · `#ob-numbers-count` · «**Телефонных номеров**» · подпись: «с ассистентом: N» / «без привязки» / «**купите номер в разделе «Телефония»**».
3. Иконка `wallet` · `#ob-wallet-value` (формат `N ₽` с `toLocaleString('ru-RU')`) · «**На кошельке**» · подпись: «списание посекундно по тарифу модели» / «подарочные рубли уже зачислены» / «**пополните, чтобы начать**».

## 3.7 Гид `.ob-guide` (скрыт по умолчанию)
Скрытие — классом `.ob-guide-hidden` на `#content-container`; состояние в `localStorage.vf_dash_guide_hidden` (`'1'` по умолчанию, `'0'` — раскрыт). При раскрытии — `scrollIntoView({behavior:'smooth'})`.
- `.ob-section-head` (flex, align-items:flex-end): `h2.ob-h2` **Syne 20px/700, ls -.02em** «**Три шага до первого разговора**» + `p.ob-muted` (13.5px) «Обычно на это уходит меньше десяти минут.». (В CSS есть и `.ob-progress`/`.ob-progress-bar` — 120×6, r999, заливка accent, transition .6s — но в текущей разметке прогресс-бар не используется.)
- `ol.ob-steps{display:grid;grid-template-columns:repeat(3, minmax(0,1fr));gap:14px;list-style:none}`; `.ob-step{display:flex;gap:14px;padding:20px;border-radius:16px;border:1px solid var(--vf-border);background:var(--vf-surface)}`, hover → бордер `#c9d3e2` + `box-shadow:0 8px 24px -16px rgba(15,23,42,.25)`. `.ob-step-num` — 34×34, r10, **Syne 700 14px**, accent-soft/accent; при `.done` — фон `#16a34a`, белая галочка вместо цифры, а к `h3` добавляется `::after` с текстом «**готово**» (11px/600, uppercase, ls .04em, `#16a34a`). `h3` 15px/600; `p` 13.5px/lh1.55 `--vf-text-3`; `.ob-step-link` (13.5px/600, accent, иконка `arrow-right`, на hover `translateX(3px)`), `margin-top:auto`. Опциональный `.ob-step-status` — пилюля `background:#dcfce7;color:#15803d`, r999, 12px/600.
  1. «**Создайте ассистента**» — «Дайте имя, выберите модель и голос. В системном промпте опишите роль, компанию и как вести разговор: это всё, что ассистент знает о вас. Первая фраза задаёт, с чего он начинает. База знаний и функции нужны, чтобы он отвечал по фактам и записывал клиентов.» Ссылка «**Открыть конструктор**» → `/static/voice-assistants.html`. Готово, если `#bots-count > 0` (через MutationObserver).
  2. «**Позвоните ассистенту**» — «Арендуйте тестовый номер на 10 минут в разделе «Телефония» и позвоните на него с любого телефона. Пока идёт тест, меняйте модель в конструкторе: номер перепривяжется сам, и за 10 минут вы послушаете все модели. Попытка одна, таймер идёт с момента включения: подготовьте ассистента заранее.» Ссылка «**Включить тестовый номер**» → `/static/telephony.html#test-number`. Готово, если `state==='active'` или были попытки.
  3. «**Пройдите верификацию и купите номер**» — «Если всё понравилось, подключите телефонию: по закону РФ для работы с номерами нужна верификация. В разделе «Телефония» нажмите «Подключить телефонию», выберите тип (физлицо, ИП или юрлицо) и следуйте инструкции. После проверки купите номер и привяжите к нему ассистента.» Ссылка «**Пройти верификацию**» → при подключённой телефонии подпись меняется на «**Открыть телефонию**», а статус-пилюля показывает «✓ **Телефония подключена**».
- Второй заголовок: «**Как это устроено**» + «Короткие статьи: что выбрать, как работает агент и за что списываются деньги.»
- `.ob-features{display:grid;grid-template-columns:repeat(3, minmax(0,1fr));gap:14px}`; `.ob-feature` — кнопка/ссылка: `flex-direction:column;gap:8px;padding:18px 20px;border-radius:16px;border:1px solid var(--vf-border)`, hover → `translateY(-2px)` + бордер `#c9d3e2` + `box-shadow:0 10px 28px -18px rgba(15,23,42,.3)`, иконка `.ob-feature-icon` 36×36 r10 (`--vf-surface-2`, на hover accent-soft/accent), рубрика `.ob-feature-cat` (11px/600, uppercase, ls .05em, `--vf-text-4`), `.ob-feature-title` (14.5px/600), `.ob-feature-text` (13px, lh 1.5, `--vf-text-3`), `.ob-feature-more` «**Читать →**» (12.5px/600, accent, иконка едет вправо на hover).

**9 карточек-статей (`data-article` / рубрика / иконка / заголовок / подзаголовок):**
| ключ | рубрика | иконка | заголовок карточки | текст карточки |
|---|---|---|---|---|
| `choose` | Начало | `route` | Ассистент или агент? | Два разных инструмента: один отвечает на звонки, второй сам ведёт клиентов. Как выбрать. |
| `telephony` | Телефония | `phone-incoming` | Телефония: номера, звонки, баланс | Как купить номер, привязать ассистента, что оплачивает баланс телефонии и как запускать исходящие. |
| `widget` | Голосовые ассистенты | `code` | Голосовой виджет для сайта | Одна строка кода на странице. Работает на OpenAI и Gemini. |
| `agent` | Агент | `headset` | Агент: сотрудник, который сам звонит и всё помнит | Что он делает за вас, один день агента, почему помнит клиентов, сколько стоит и как устроен внутри. |
| `claude` | Агент | `sparkles` | Настройка через Claude Code | Скачайте описание API и попросите Claude Code собрать агента за вас. |
| `billing` | Оплата | `wallet` | Кредиты и минуты | За что списываются рубли с кошелька, а за что кредиты, и где это видно. |
| `knowledge` | Настройка | `book-open` | База знаний | Загрузите документы, и ассистент отвечает по ним, а не выдумывает. |
| `crm` | Данные | `contact-round` | CRM и диалоги | Контакты, история разговоров, записи звонков и уведомления в одном месте. |
| `integrations` | Настройка | `puzzle` | Интеграции и функции | Google Sheets, Telegram, вебхуки, календарь и произвольные API-запросы. |

## 3.8 Читалка статей
`#ob-article-backdrop` — стандартный `.vf-backdrop` (z-1100, но правило страницы поднимает `body.vf .vf-backdrop{z-index:10010}`, чтобы быть выше модалки оплаты с z-9999), открывается через `VF.modal(backdrop)`. Диалог `.vf-dialog.ob-article`: **max-width 780px, max-height 92vh, padding 0, flex-column**.
- `.art-head` — sticky, `padding:14px 18px 12px 36px`, фон `color-mix(in srgb, var(--vf-surface) 92%, transparent)` + `backdrop-filter:blur(8px)`, нижний бордер. Слева `.art-cat` (12px/600, uppercase, ls .05em, accent) — иконка + рубрика; справа `.art-close` (34×34, r9, бордер).
- `.art-body{padding:26px 36px 36px;overflow-y:auto}`.

**Типографика статей:** `.art-title` — Syne 800, `clamp(24px,3vw,30px)`, lh 1.15, ls -.025em; `.art-lead` — 16.5px, lh 1.55, `--vf-text-2`; `h2` — Syne 19px/700, `margin:30px 0 10px`; `h3` — 15px/600; `p` — 14.5px, lh 1.65; `ul` — маркер-точка `::before` 7×7 accent (opacity .7), `li` padding-left 20px; `ol` — счётчик в квадрате 22×22 r7 accent-soft/accent (Syne 12px/700), li padding-left 34px; `code`/`.art-kbd` — моно 12.5px, фон `--vf-surface-2`, бордер, r5; `pre` — фон `#0f172a`, текст `#e2e8f0`, r12, padding `14px 16px`, `white-space:pre-wrap`.
**Спец-блоки статей:**
- `.art-note` — padding `14px 16px`, r12, фон accent-soft, бордер `color-mix(in srgb, var(--vf-accent) 20%, transparent)`, иконка 18×18; `.art-note.warn` — warning-soft + иконка `triangle-alert`.
- `.art-cols` — `grid-template-columns:1fr 1fr; gap:12px`; `.art-col` — padding `16px 18px`, r14, бордер, фон `--vf-surface-2`; `.art-col-head` — Syne 15px/700 + иконка accent.
- `.art-table` — 13.5px, `th` 11.5px uppercase `--vf-text-4`, `td:first-child` жирный `nowrap`.
- `.art-flow` — три шага в ряд (`repeat(3,1fr)`, слитно, скругления только по краям, между шагами — «стрелка» из повёрнутого на 45° квадрата 16×16 слева); `.art-flow-who` (11px/600 uppercase; `.orch` → `#7c3aed`, `.voice` → accent); `.art-flow-title` Syne 15px/700; `.art-flow-text` 12.5px. Плюс `.art-flow-loop`.
- `.art-brains` — `grid-template-columns:1fr auto 1fr`; `.art-brain.orch` — бордер/фон на базе `#7c3aed` (`color-mix … 35% / 5%`), `.art-brain.voice` — accent-бордер + accent-soft; между ними `.art-brains-link` (иконка 22×22, 11px, max-w 96).
- `.art-day` — таблица «дня агента»: `.art-day-row{grid-template-columns:76px 1fr;gap:12px;padding:11px 16px}` с `.art-day-time` (моно 12px/600 accent).
- `.art-cta` (h40, r10, accent/белый; `.ghost` — белая с бордером) и `.art-next` (верхний бордер, лейбл «Дальше» + кнопки-пилюли h32 r999 с переходом к следующей статье через `data-article`).
- `@media (max-width:720px)`: `.art-body{padding:20px 18px 28px}`, `.art-cols`/`.art-brains`/`.art-flow` в одну колонку, стрелки между шагами скрыты.

Содержательно статьи очень длинные (полные тексты в `dashboard.html`, строки 2244–2596). Ключевые заголовки внутри: «Ассистент или агент: что выбрать» (2 колонки + таблица «Когда что подходит»), «Телефония: номера, звонки и баланс» (разделы «Как всё подключить», «Входящие звонки», «Исходящие звонки», «Что оплачивает баланс телефонии» c колонками «Баланс телефонии» / «Кошелёк Voicyfy», предупреждение «Телефония входит в тарифы Старт, Profi и Agent. В тарифе AI Voice её нет…»), «Голосовой виджет для сайта», «Агент: сотрудник, который сам звонит, всё помнит и доводит до результата» (разделы «Что делаете вы и что делает агент», «Один день агента: магазин сообщает о поступлении» с таймлайном 10:05 / 10:06 / 14:30 / 14:33 / 14:34 / 15:10 / Пятница / Вечер, «Почему агент помнит всех клиентов», «На связи не только по телефону», «Вы управляете обычными словами», «Где агент приносит результат»), «Настройка через Claude Code», «Кредиты и минуты», «База знаний», «CRM и диалоги», «Интеграции и функции».

## 3.9 Секция подписки `.subscription-section`
`background:var(--white); border-radius:var(--radius-lg); padding:1.5rem; margin-bottom:2.5rem; box-shadow:var(--shadow-sm); border:1px solid var(--border-color)` (legacy → r16 + `--vf-shadow-1`).
- Шапка: `h2.section-title` «👑 **Ваша подписка**» (`fa-crown`).
- Состояние загрузки: `.subscription-loading` (колонка, padding `2rem 0`) с `.subscription-loader` (40×40, border 3px `rgba(74,134,232,.3)`, top-color accent, `spin 1s linear infinite`) и текстом «**Загрузка информации о подписке...**».
- Рендер (`renderSubscriptionDetails`): `.subscription-plan` (flex space-between) — `h3` с названием тарифа + `.subscription-status` (пилюля, `padding:.35rem .75rem`, r999, .75rem/600, uppercase, ls .5px): `.active` (фон `rgba(16,185,129,.1)`, текст `#10b981`) «**Активна**» или `.trial` (фон `rgba(249,115,22,.1)`, `#f97316`) «**Пробный период**». При ограничениях тарифа — `.subscription-warning` (фон `rgba(249,115,22,.1)`, текст `#f97316`, r8, padding `.75rem 1rem`) «ℹ **Недоступно на вашем тарифе: CRM, Телефония, Исходящие звонки**». Далее `.subscription-details-grid{grid-template-columns:repeat(auto-fill, minmax(200px,1fr));gap:1rem}` из трёх `.subscription-detail` (фон `--bg-light`, padding 1rem, r8) с `.detail-label` (.75rem, `--text-gray`) и `.detail-value` (600, 1.125rem): «**Дата окончания:**», «**Осталось дней:**», «**Макс. ассистентов:**».
- Пусто: `.subscription-empty` — «В настоящий момент у вас активирован базовый план. Приобретите тариф для доступа ко всем функциям.»
- Ошибка: `.subscription-error` — «Не удалось загрузить информацию о подписке.» + кнопка «🔄 **Повторить**».
- `.subscription-upgrade` (flex, justify-end) → кнопка `btn btn-primary#upgrade-subscription-btn` «**↑ Улучшить тариф**».
- Также в CSS есть `.subscription-progress` / `.progress-bar` (h8, r999) / `.progress-fill` (`linear-gradient(90deg,#10b981,#3b82f6)`) — заготовка, в текущей разметке не выводится.

## 3.10 Старый тост `.notification`
`position:fixed; bottom:20px; right:20px; border-radius:.5rem; box-shadow:var(--shadow-lg); padding:1rem 1.25rem; max-width:350px; z-index:1000; transform:translateY(100px); opacity:0; transition:all .3s` → `.show{transform:translateY(0);opacity:1}`. Левая полоса 4px по типу: success `#10b981`, error `#ef4444`, info accent. Legacy-мост перекрашивает фон в `#0f172a` с текстом `#e7ecf5`. **Фактически используется редко:** `ui.showNotification` сначала пробует `VF.toast`, и только без VF падает на этот DOM.

## 3.11 Модалка выбора тарифа `#payment-modal` (z-9999)
`display:none`, `.show` → `display:flex` + `modal-fade-in .3s`. `.payment-modal-overlay` — `rgba(0,0,0,.5)` + `blur(4px)`. `.payment-modal-content` — базово `max-width:650px`, но **`body.vf .payment-modal-content{max-width:920px;width:calc(100% - 32px);border-radius:18px}`**; `max-height:90vh; overflow-y:auto; animation:modal-slide-up .3s` (`translateY(30px) scale(.95)` → norm).
- Шапка `.payment-modal-header` (`1.5rem 2rem 1rem`, нижний бордер): `h2.payment-modal-title` «👑 **Выбор тарифа**» (1.5rem/600, иконка accent) + `.payment-modal-close` (`fa-times`).
- **`.plan-tabs` — в vf-режиме это НЕ табы, а 4 карточки:** `display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px`. `.plan-tab` — `flex-direction:column; align-items:flex-start; gap:4px; text-align:left; padding:18px 16px 14px; border-radius:14px; border:1px solid var(--vf-border); background:var(--vf-surface)`; hover → бордер `--vf-border-strong` + `translateY(-1px)`; `.active` → бордер accent + `box-shadow:0 0 0 3px var(--vf-accent-ring), var(--vf-shadow-2)`.
  Внутренние элементы: `.recommended-badge` (абс. `top:-10px;left:14px`, r999, accent/белый, 11px/600), `.tab-name` (**Syne 18px/700**), `.tab-tagline` (12.5px, `--vf-text-3`, `min-height:35px`), `.tab-price` (**Syne 22px/700, ls -.02em**) + `small` (12px, `--vf-text-4`), `.tab-key` (12.5px/600, `--vf-text-2`, нижний бордер + отступ), `.tab-mini` (колонка, 12px; `.ok i` → `--vf-success`, `.no` → `--vf-text-4` с иконкой `--vf-danger`).
  **Тёмная карточка Agent:** `.plan-tab-agent{background:#0f172a;border-color:#0f172a;color:#e2e8f0}`; hover `#111c33`; `.active` — бордер `#7c3aed` + `box-shadow:0 0 0 3px rgba(124,58,237,.3)`; `.tab-name/.tab-price` белые, `.tab-tagline` `#a5b4cf`, `.tab-key` `#cbd5e1` с бордером `rgba(148,163,184,.25)`, галочки `#a78bfa`, бейдж `.agent-badge` фон `#7c3aed`.

  **Содержимое четырёх карточек:**
  | plan | badge | name | tagline | price | key | мини-список |
  |---|---|---|---|---|---|---|
  | `ai_voice` | — | AI Voice | Голосовой виджет для сайта | 1 490 ₽/мес | до 3 ассистентов | ✓ Виджет на сайт · ✗ Телефония · ✗ CRM |
  | `start` | Популярный | Старт | Звонки, сайт и CRM для бизнеса | 2 990 ₽/мес | до 5 ассистентов | ✓ Телефония и исходящие · ✓ CRM и диалоги · ✓ Виджет на сайт |
  | `profi` | — | Profi | Больше ассистентов и VIP-поддержка | 5 990 ₽/мес | до 10 ассистентов | ✓ Всё из Старта · ✓ VIP-поддержка |
  | `agent` | Автономный сотрудник | Agent | Сам звонит, пишет и ведёт клиентов до результата | 5 490 ₽/мес | до 3 агентов | ✓ Обзвон и перезвоны сам · ✓ Telegram, MAX, SMS · ✓ 20 000 кредитов в месяц |
- `.plan-warning` (`#modal-plan-warning`, скрыт классом `.hidden`): flex gap 10, padding `12px 14px`, r12, фон `--vf-warning-soft`, бордер `color-mix(in srgb, var(--vf-warning) 30%, transparent)`, 13px. Текст для AI Voice: «В тариф AI Voice не входят телефония, исходящие звонки и CRM. Он подходит, если ассистент нужен только на сайте. Для звонков выберите Старт, Profi или Agent.»
- **`.period-tabs`** (`grid repeat(3,1fr); gap:4px; padding:4px; border-radius:12px; background:var(--vf-surface-2); border:1px solid var(--vf-border)`); `.period-tab` — h≥48, r9, 13.5px/500, активный — белый фон + `--vf-shadow-1` + 600. Пункты: «**1 месяц**»; «**6 месяцев**» + `.tab-discount` «**-20%**» (11px/600 success); «**1 год**» + «**-30%**» + `.best-value-badge` «**Выгодно**» (абс. `top:-9px;right:8px`, r999, градиент `135deg,#f97316,#ea580c`, белый, 10px). **Для тарифа Agent блок периодов скрывается целиком** (`singlePeriod:true`).
- `.payment-plan-info` в vf-режиме — `grid-template-columns:1fr auto; padding:14px 18px; border-radius:14px; background:var(--vf-surface-2)`: `.payment-plan-name` (Syne 18px/700), `.payment-plan-assistants` (13px `--vf-text-3`, «До N ассистентов» / для Agent «До 3 автономных агентов»), `.payment-plan-price` (Syne; `.price-amount` 30px/700, `.price-currency` 16px `--vf-text-3`), `.payment-plan-duration` (12.5px, иконка `fa-calendar-alt`, «30 дней» / «180 дней» / «365 дней»), `.savings-badge` («🐖 Вы экономите N ₽», `background:var(--green-100)`, `color:var(--green-500)`, r999, padding `.5rem 1rem`, 600/.875rem; `.hidden` скрывает), `.monthly-price-info` («1 490 ₽/мес» зачёркнутым `.old-price` + «1 192 ₽/мес» `.new-price` зелёным).
- `.payment-plan-features`: `h3` «**Что входит в тариф**» (в vf — 13px/600 uppercase ls .04em `--vf-text-4`); `ul.features-list` в vf-режиме — **две колонки** (`grid-template-columns:1fr 1fr; gap:6px 18px`), `li` 13.5px без бордеров; недоступные пункты `li.restricted` — текст `--vf-text-4` с `text-decoration:line-through`, иконка `fa-times` красная.
  Списки фич по тарифам (`SUBSCRIPTION_PLANS[*].features`, иконки FA):
  - **AI Voice**: `fa-robot` До 3 голосовых ассистентов ✓ · `fa-infinity` Безлимитные диалоги ✓ · `fa-book` База знаний ✓ · `fa-code` API доступ ✓ · `fa-headset` Поддержка ✓ · `fa-phone` Телефония ✗ · `fa-phone-alt` Исходящие звонки ✗ · `fa-address-book` CRM система ✗
  - **Старт**: те же + «До 5 голосовых ассистентов», «Приоритетная поддержка», всё ✓
  - **Profi**: «До 10 голосовых ассистентов», «База знаний без ограничений», «Полный API доступ», «VIP поддержка», всё ✓
  - **Agent**: `fa-brain` Оркестратор планирует звонки, перезванивает и ведёт воронку · `fa-user-astronaut` До 3 агентов, у каждого свой голос · `fa-paper-plane` Общение с клиентами в Telegram, MAX и SMS · `fa-comments` Чат с агентом: спросите, как дела у клиентов · `fa-coins` 20 000 кредитов каждый месяц · `fa-phone` Телефония и исходящие звонки · `fa-address-book` CRM и воронка продаж · `fa-book` База знаний · `fa-headset` Приоритетная поддержка
- `.payment-extra` (vf: `margin:6px 24px 0; padding:12px 14px; r12; фон --vf-surface-2; бордер`): заголовок «**Оплачивается отдельно, на любом тарифе**» (11.5px/600 uppercase `--vf-text-4`) и три пункта (12.5px, иконки accent): «**Минуты голосовых моделей с кошелька Voicyfy**» (`fa-wallet`), «**Связь: номера и минуты оператора на балансе телефонии**» (`fa-phone`), «**Кредиты оркестратора для агента**» (`fa-coins`).
- Футер `.payment-modal-footer` (vf: `padding:16px 24px`): «**Отмена**» и `.payment-pay-btn` «💳 **Оплатить 1 490 ₽**» (`flex:1`, 600/1rem, padding `.875rem 1.5rem`).
- Оверлей загрузки `.payment-loading` — белая полупрозрачная плашка (`rgba(255,255,255,.95)`) со спиннером 40×40 и текстом «**Создание платежа...**».
- **Логика цен:** `PERIOD_DISCOUNTS = {1:0, 6:20, 12:30}`, `PERIOD_DAYS = {1:30, 6:180, 12:365}`, `PERIOD_LABELS = {1:'1 месяц', 6:'6 месяцев', 12:'1 год'}`; итог = `round(base * months * (1 - d/100) / 10) * 10`; формат числа — пробелы как разделители тысяч. Дефолтный выбранный тариф при открытии — **`start`**.
- **Защитный VF.confirm перед оплатой AI Voice:** «**В этом тарифе нет телефонии**» / «AI Voice работает только как голосовой виджет на сайте: без номеров, звонков и CRM. Если вам нужны звонки, выберите Старт, Profi или Agent.» / кнопки «Мне нужен только виджет» и «Показать тарифы со звонками» (при отказе тариф переключается на `start`).
- Оплата: `agent` → `POST /api/credits/subscribe` (30 дней + 20 000 кредитов), остальные → `POST /api/payments/create-payment` → динамическая скрытая форма POST на Robokassa. Тосты: «Тестовый период Voicyfy Agent активирован! Вам доступно 1 500 кредитов на 3 дня.», «Перенаправляем на страницу оплаты...», «Ошибка при создании платежа. Попробуйте позже.», «Оплата прошла успешно! Доступ к платформе восстановлен.», «Ошибка при проверке статуса оплаты», «Не удалось загрузить данные пользователя».

## 3.12 Модалка блокировки функции `#feature-blocked-modal` (z-9999)
`.modal-content` — max-w **450px**, r16, `overflow:hidden`. Шапка `.modal-header` — **градиент `135deg, var(--orange-500), #ea580c`**, белый текст, по центру, padding 1.5rem: иконка `fa-lock` 2.5rem + `h3` «**Функция недоступна**» (1.25rem/600). Тело (padding 1.5rem, по центру): `#feature-blocked-message` «Для доступа к разделу "<Название>" необходим тариф Старт, Profi или Agent.», `.current-plan-info` (фон `--bg-light`, r8, padding `.75rem 1rem`) «Ваш текущий тариф: **<название>**», `.required-plans` (.875rem) «Необходим тариф: **Старт, Profi или Agent**». Футер — две кнопки `flex:1`: «**Закрыть**» и «**↑ Выбрать тариф**».
`FEATURE_NAMES = {crm:'CRM', telephony:'Телефония', outbound_calls:'Исходящие звонки'}`; `FEATURE_ACCESS` — все, кроме `ai_voice`, имеют доступ (`['free','referral_trial','start','profi','agent']`). Заблокированные пункты сайдбара получают класс `.plan-locked-feature` (`opacity:.6`, в базовом CSS — эмодзи 🔒 через `::after`; в vf-режиме `::after` отключён и показывается svg-иконка `lock`).
`getPlanDisplayName` дополнительно знает `free → 'Пробный период'`, `referral_trial → 'Реферальный триал'`.

## 3.13 Модалка истёкшей подписки `#subscription-expired-modal` (z-10000)
Оверлей: `rgba(0,0,0,.8)` + `blur(8px)` (жёстче, чем у остальных). `.subscription-expired-content` — base max-w 650, **в vf-режиме 920px, r18**; `max-height:90vh`.
- Шапка — **градиент `135deg, #ef4444, #dc2626`**, белый текст, по центру: эмодзи-иконка `⏰` (3rem), `h2.subscription-expired-title` «**Подписка истекла**» (1.75rem/700), `p.subscription-expired-subtitle` «Продлите подписку, чтобы продолжить использовать платформу».
- `.expired-notice` — фон `rgba(239,68,68,.1)`, левый бордер 4px `#ef4444`, r только справа: заголовок «⚠ **Ваша подписка завершилась**» (`#ef4444`, 600) и текст «К сожалению, **<тестовый период|ваша подписка>** завершился и ваши голосовые ассистенты временно отключены. Для восстановления доступа ко всем функциям платформы необходимо оформить подписку.»
- Далее — **те же 4 карточки тарифов** (`.expired-plan-tab`, идентичное наполнение и стили, включая тёмную Agent) и **те же 3 периода** (`.expired-period-tab`).
- `.expired-selected-plan` (в vf — `grid 1fr auto`, padding `14px 18px`, r14, `--vf-surface-2`): `.expired-plan-name` (Syne 18px/700), `.expired-plan-assistants` (13px), `.expired-plan-price` (Syne 28px/700), `.expired-plan-period` («30 дней доступа»), `.expired-savings-badge` («🐖 Экономия N ₽»).
- Футер (колонка, gap 1rem): `.renew-subscription-btn` — `background:linear-gradient(135deg, var(--primary-blue), var(--primary-blue-dark)); color:#fff; padding:1rem 2rem; r8; 600/1rem`, hover `translateY(-2px)` + `--shadow-md`; текст «💳 **Продлить подписку за 1 490 ₽**». Ниже «**Выйти**» (btn-secondary, 100%). `.contact-support` — «Нужна помощь? **Свяжитесь с поддержкой**» (ссылка `mailto:support@voicyfy.ru`).
- При показе: `document.body.classList.add('subscription-expired')` + `#content-container.content-blocked`. Дефолтный тариф в этой модалке — `agent`, если текущий план `agent`, иначе **`ai_voice`**.

## 3.14 Адаптив dashboard.html (инлайн-стиль)
- **`@media (max-width:1100px)`** (онбординг): `.ob-hero{grid-template-columns:1fr;padding:32px}`, `.ob-hero-visual{display:none}`, `.ob-steps`/`.ob-features` → 2 колонки.
- **`@media (max-width:1024px)`**: `.stats-grid{repeat(auto-fill, minmax(250px,1fr))}`, `.subscription-details-grid{minmax(180px,1fr)}`, `.plan-tabs/.expired-plan-tabs{flex-wrap:wrap}`, `.plan-tab{flex:1 1 calc(33.333% - .5rem);min-width:80px}`.
- **`@media (max-width:900px)`** (vf-режим): `.plan-tabs/.expired-plan-tabs{grid-template-columns:1fr 1fr}`.
- **`@media (max-width:768px)`**: сайдбар `translateX(-100%)` + `.open` возвращает; `.main-content{margin-left:0}`; `.mobile-toggle{display:block}`; `.top-nav{padding:1rem;flex-wrap:wrap;gap:1rem}` и `.page-title{order:2;width:100%;font-size:1.125rem}`, `.user-menu{order:1}`; `.content-container{padding:1.5rem 1rem}`; `.stats-grid{1fr}`; `.period-tabs/.plan-tabs{flex-direction:column;gap:.375rem}`, бейджи «Выгодно»/«Популярный» перемещаются на `top:50%;right:8px`; модалки `margin:10px`.
- **`@media (max-width:720px)`** (онбординг): `.ob-hero{padding:24px 20px;border-radius:16px}`, `.ob-title{font-size:26px}`, `.ob-metrics{1fr}` (разделитель становится верхним бордером), `.ob-steps/.ob-features{1fr}`, `.ob-section-head{flex-direction:column;align-items:flex-start}`; отдельно — адаптив статей (см. 3.8).
- **`@media (max-width:560px)`** (vf): `.plan-tabs{1fr}`, `.features-list{1fr}`.
- **`@media (max-width:480px)`**: `.content-container{padding:1rem .75rem}`, уменьшенные паддинги модалок, `.price-amount{font-size:2.25rem}`.
- Плюс глобальный **960px** из voicyfy.css / voicyfy-legacy.css (сайдбар, `.mobile-toggle`, `.vf-content`).

## 3.15 JS-хелперы dashboard.html
Свой объект `api` (обёртка fetch c `Authorization: Bearer localStorage.auth_token`) и объект `ui` (`showNotification` → делегирует в `VF.toast`, `hideNotification`, `showSubscriptionExpiredModal(isTrialExpired)`, `hideSubscriptionExpiredModal`). Диалоги — `VF.confirm` (с фолбэком на `window.confirm`). Иконки в онбординге проставляются скриптом через `[data-ob-icon]` → `VF.icon(name)`; в статьях — при открытии (`renderIcons(body)`). Читалка — `VF.modal`. Прогресс-бар VF не используется. Админ-проверка в самом dashboard — только `well96well@gmail.com` (в sidebar.js список шире).

---

# 4. Сводка по иконкам

| Страница | Источник иконок | Как используются |
|---|---|---|
| `agent.html` | **Font Awesome 6.4.0 CDN** + автоподмена на Lucide-спрайт через `ui.js` | Вся разметка на `<i class="fas|far|fab fa-…">`. Специфичные: `fa-microphone-lines`, `fa-headset`, `fa-phone-volume`, `fa-users`, `far fa-calendar-check`, `fa-clock-rotate-left`, `fa-list-check`, `fa-sliders`, `far fa-folder-open`, `fa-database`, `fa-brain`, `fa-plug`, `fab fa-telegram`, `fa-coins`, `far fa-circle-question` (все подсказки), `far fa-trash-can`, `fa-right-from-bracket`, `fa-bars`, `fa-plus`, `fa-paper-plane`, `fa-microphone`, `fa-chevron-left/right/down`, `fa-times`, `fa-check`, `fa-rocket`, `fa-stream`, `fa-file-import`, `fa-cloud-arrow-up`, `fa-circle-check`, `fa-triangle-exclamation`, `fa-wallet`, `fa-comment-dots`, `fa-mars/fa-venus/fa-circle-half-stroke` (пол голоса), `fa-building/fa-bullseye/fa-comments/fa-box-open/fa-flag` (документы), `fa-bolt` (трейс). Две инлайн-SVG для кнопок панелей и язычков. |
| `voice-assistants.html` | **Только Lucide-спрайт** `/static/icons/ui.svg` (`<use href="…#i-name">` и `VF.icon`) | `i-x`, `i-log-out`, `i-menu`, `i-plus`, `i-arrow-left`, `i-arrow-right`, `i-sliders-horizontal`, `i-puzzle`, `i-book-open`, `i-play`, `i-code`, `i-info`, `i-triangle-alert`, `i-check`, `i-circle-check`, `i-circle-alert`, `i-key`, `i-copy`, `i-pen`, `i-trash-2`, `i-phone`, `i-headset`. Логотипы провайдеров — `VF.logo`. |
| `dashboard.html` | **Lucide-спрайт** (через `data-ob-icon` и `VF.icon`) + `fas fa-*` в старых блоках, которые мост превращает в Lucide (FA CDN не подключён) | `data-ob-icon`: `plus`, `phone`, `chevron-down`, `bot`, `square-check`, `calendar`, `bell`, `wallet`, `check`, `arrow-right`, `route`, `phone-incoming`, `code`, `headset`, `sparkles`, `book-open`, `contact-round`, `puzzle`, `x`, `info`, `triangle-alert`, `audio-lines`, `user-round`. FA-классы: `fa-crown`, `fa-bars`, `fa-user`, `fa-sign-out-alt`, `fa-chevron-down`, `fa-times`, `fa-check`, `fa-credit-card`, `fa-lock`, `fa-arrow-up`, `fa-calendar-alt`, `fa-piggy-bank`, `fa-exclamation-triangle`, `fa-info-circle`, `fa-sync-alt`, `fa-robot`, `fa-infinity`, `fa-book`, `fa-plug`, `fa-headset`, `fa-code`, `fa-phone`, `fa-phone-alt`, `fa-address-book`, `fa-brain`, `fa-user-astronaut`, `fa-paper-plane`, `fa-comments`, `fa-coins`, `fa-wallet`, `fa-check-circle`, `fa-exclamation-circle`. |

**Файлы логотипов:** `backend/static/icons/models/{openai.svg, gemini.svg, gemini-color.svg, fishaudio.svg, fishaudio-color.svg, yandex.svg, yandex-color.svg, cascade.svg, cascade-color.svg}` + `LICENSE.md`. Для `cartesia` файла нет — фолбэк на иконку `bot` в `.logo-wrap`.

---

# 5. Важные для клонирования нюансы

1. **Три разных поколения вёрстки в одном ЛК.** `voice-assistants.html` — чистая дизайн-система (только токены `--vf-*`). `agent.html` — дизайн-система + собственный слой алиасов старых переменных + собственные значения раскладки. `dashboard.html` — легаси-вёрстка на `rem`-значениях и собственном `:root`, которую `voicyfy-legacy.css` «перекрашивает» в токены. Если клон делают с нуля, третий слой не нужен — но тогда реальные визуальные значения (радиусы, тени, отступы, размеры шрифтов) надо брать из **vf-варианта**, а не из инлайн-CSS dashboard.
2. **`class="vf"` меняет поведение.** На `voice-assistants.html` и `dashboard.html` он на `<body>` → нативные `<select>` подменяются на `VF.select`. На `agent.html` он намеренно на `<html>` → селекты остаются нативными, потому что скрипты читают/пишут их напрямую.
3. **Font Awesome нужен только `agent.html`** (и легаси `agents.html`); остальные две страницы живут на Lucide-спрайте из 125 иконок, а `fas fa-*` в dashboard превращаются в Lucide мостом `ui.js`.
4. **Google Fonts различаются:** agent — Inter 400/500/600/700 + Syne 700/800 + **Unbounded 400/500/600/700** (Unbounded используется для заголовка приветствия в чате и для пузыря сообщений пользователя); две другие — только Inter + Syne 700/800.
5. **Никаких Chart-библиотек, Sortable.js, Lucide-JS, Alpine и т.п. ни на одной из трёх страниц.** Drag&drop воронки на agent.html — нативный HTML5 DnD. Внешний JS только: marked 12.0.2 и DOMPurify 3.1.6 (agent.html) + Font Awesome CSS (agent.html).
6. **Два разных тостовых механизма:** `VF.toast` (voice-assistants, dashboard) и собственный `showToast` из `agent/core.js` (agent.html). И два разных механизма подтверждений: `VF.confirm` (voice-assistants, dashboard) и нативный `window.confirm` (agent.html).
7. **Z-index-лестница:** страничные `.modal-overlay` на agent.html — 100 (детали контакта — 200), wizard — 200, онбординг — 210, loading — 300; VF-слои — backdrop 1100, тосты 1120, loader 1200, progress 1210, портал VF.select 1300; dashboard поднимает `.vf-backdrop` до 10010, чтобы читалка/подтверждения были выше payment-modal (9999) и expired-modal (10000).
