# 05 · Лендинг VoksiAI (React + Vite)

Полная инвентаризация лендинга Voicyfy для пересборки 1:1 под бренд VoksiAI. Ниже — структура,
технологии, токены, все секции с текстами и стилями, модалка авторизации. Готовый CSS лендинга
лежит в `css/landing.css` (уже с палитрой VoksiAI); базовые токены и компоненты — в
`css/voksiai.css` (лендинг подключает его напрямую тегом `<link>`, вне бандла).

## Что менять для VoksiAI и рынка КР (сводка)

| Что | В Voicyfy | Для VoksiAI |
|---|---|---|
| Бренд в разметке | `Voicyfy` (Navbar, Footer, AuthModal, Frame в макетах, kicker в Hero, `<title>`, OG, JSON-LD) | `VoksiAI` |
| Логотип-картинка | `/static/images/IMG_2820.PNG` | свой файл, 30×30 в шапке, 20×20 в макете |
| Домен | `https://voicyfy.ru` (canonical, OG, JSON-LD, скрипт виджета в `Integration.jsx`) | домен VoksiAI |
| Телефон демо-агента | `+7 931 10-710-31` (`Hero.jsx` → `PHONE`, `PHONE_DISPLAY`) | номер КР (`+996 …`) |
| Валюта | `₽`, «подарочные рубли», `priceCurrency: "RUB"` | сом (`KGS`), «подарочные сомы»; цены телефонии и тарифов в JSX — пересчитать |
| Юр. информация в футере | ИП + ИНН РФ | реквизиты компании в КР |
| Фразы «по закону РФ» (ProductTour факт «Верификация», Start шаг 03, FAQ №2) | — | переписать под законодательство КР или убрать |
| Контакты | `info@voicyfy.ru`, `t.me/voicyfy`, `t.me/voicyfy_support` | свои |
| Языки | один (`lang="ru"`) | RU + KG: все строки лендинга вынести в словарь (`i18n/ru.json`, раздел `landing`), переключатель языка в навбаре |
| Демо-виджет на странице | `gemini-widget.js` с assistant-id Voicyfy | свой ассистент и свой домен |
| Шрифты | Inter + Unbounded | оставить; кыргызские буквы Ң Ө Ү проверить в Unbounded (в Inter есть) |
| Цвета `#2563eb / #1d4ed8 / #3b82f6 / #0f172a` в отчёте ниже | палитра Voicyfy | в `css/` уже заменены на `#2a5ce8 / #2149cf / #4a7cf3 / #0e1729`, см. `01-foundations.md` |

Пути вида `frontend/…` и `backend/static/…` ниже — расположение файлов в исходном репозитории
Voicyfy (для ориентира, где что лежало).

---

# Инвентаризация лендинга (исходник: Voicyfy)

Репозиторий: исходный репозиторий Voicyfy
Исходники лендинга: `frontend/`
Сборка: `backend/static/landing/`

---

## 0. Критично: два источника CSS

Лендинг НЕ самодостаточен. Токены и базовые компоненты (`.btn`, `.card`, `.chip`, `.input`, `.field`, `.label`, `.note`, `.ic`, `.logo`, `.table`, `.spin`, `.vf-logo`, `.vf-wave`, `.dot`) приходят из дизайн-системы личного кабинета:

- `backend/static/css/voicyfy.css` (443 строки) — подключается напрямую тегом `<link rel="stylesheet" href="/static/css/voicyfy.css">` в `frontend/index.html`, **не бандлится Vite**.
- `frontend/src/landing.css` (559 строк) — раскладка, типографика лендинга, макеты «экранов кабинета», модалка авторизации. Импортируется в `main.jsx`, попадает в бандл `assets/index-*.css`.

Для клона нужно перенести **оба** файла (или слить их).

---

## 1. Технологии

### 1.1 Зависимости (`frontend/package.json`)
```json
"dependencies": { "lenis": "^1.3.26", "motion": "^13.2.0", "react": "^18.2.0", "react-dom": "^18.2.0" }
"devDependencies": { "@vitejs/plugin-react": "^4.0.0", "vite": "^5.0.0" }
"scripts": { "dev": "vite", "build": "vite build && node scripts/prerender.mjs", "preview": "vite preview" }
```
- React **18.2** (`createRoot` / `hydrateRoot`), без роутера, без стейт-менеджера.
- **`motion` v13** — это пакет-преемник `framer-motion`, импорт из `motion/react`.
- **`lenis` v1.3** — плавная прокрутка.
- Vite 5, плагин `@vitejs/plugin-react`.

### 1.2 Vite (`frontend/vite.config.js`)
```js
build: { outDir: '../backend/static/landing', emptyOutDir: true }
base: '/static/landing/'
server.proxy: { '/api': 'http://localhost:8000', '/static': 'http://localhost:8000' }
```

### 1.3 Пререндер + гидрация
- `frontend/src/entry-server.jsx` — `renderToString(<App/>)` + генератор `FAQPage` JSON-LD из того же массива `QA`, что и секция FAQ.
- `frontend/scripts/prerender.mjs` — после `vite build` собирает SSR-бандл во временную папку `node_modules/.voicyfy-ssr`, рендерит `App` в строку, подменяет `<div id="root"></div>` на `<div id="root">{html}</div>` в `backend/static/landing/index.html` и вставляет `<script type="application/ld+json">{faq}</script>` перед `</head>`. Временную папку удаляет.
- `frontend/src/main.jsx`: если `#root` уже имеет детей → `hydrateRoot`, иначе `createRoot().render()` (dev-сервер). Обёрнуто в `React.StrictMode`.
- Цель пререндера прямо задокументирована в коде: «его читают роботы Яндекса, Google и ИИ-поиска, не выполняющие JS».

### 1.4 Lenis (`App.jsx`)
```js
if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;   // отключён
const lenis = new Lenis({ lerp: 0.1, smoothWheel: true });
requestAnimationFrame loop → lenis.raf(t)
```
- При открытой модалке: `lenis.stop()` / `lenis.start()`.
- Делегированный обработчик кликов по `a[href^="#"]`: `lenis.scrollTo(target, { offset: -72, duration: 1.1 })`, фолбэк `window.scrollTo({top, behavior:'smooth'})` с тем же `-72px` (высота шапки 64px + запас). После — `history.replaceState(null,'','#'+id)`.
- CSS-поддержка Lenis в `landing.css`:
  `html.lenis, html.lenis body { height: auto }`, `.lenis.lenis-smooth { scroll-behavior: auto !important }`, `.lenis.lenis-stopped { overflow: hidden }`.

### 1.5 Анимации (`frontend/src/components/Reveal.jsx`)
Единая обёртка над Motion. Ключевые константы:
```js
const EASE = [0.2, 0.7, 0.2, 1];
const VIEWPORT = { once: false, amount: 0.15, margin: '-32px 0px -32px 0px' };
```
Четыре экспорта:

**`<Reveal>`** — одиночное появление. Варианты:
```js
hidden: (side) => ({ opacity: 0, y: (side||1)*y, x })
show:   { opacity: 1, y: 0, x: 0, transition: { duration: 0.6, ease: EASE, delay } }
```
Дефолты: `y=24, x=0, delay=0, as='div'`. Всегда ставит атрибут `data-reveal=""`.

**`<Stagger>`** — контейнер каскада: `variants: { hidden:{}, show:{ transition:{ staggerChildren: stagger, delayChildren: delay } } }`, дефолт `stagger=0.08, delay=0, amount=0.15`.

**`<Item>`** — ребёнок каскада: `hidden: (s)=>({opacity:0, y:(s||1)*y, x, scale, rotate})`, `show:{opacity:1,y:0,x:0,scale:1,rotate:0,transition:{duration, ease}}`. Дефолты `y=26, x=0, scale=1, rotate=0, duration=0.6`.

**`<Parallax>`** — scroll-linked: `useScroll({ target: ref, offset: ['start end','end start'] })` + `useTransform(scrollYProgress,[0,1],[amount,-amount])` → `style={{ y }}`. Дефолт `amount=40`.

**`<CountUp>`** — счётчик от нуля через `IntersectionObserver` (threshold 0.6) + `requestAnimationFrame`, easing `1-(1-p)^3`, дефолт `duration=1.2`, форматирование `toLocaleString('ru-RU')`. **В текущем лендинге не используется** (экспортирован, но не импортирован ни одной секцией).

Важная особенность: анимация **повторяется при каждом входе в экран** (`once:false`). Через `SideContext` запоминается, с какой стороны блок ушёл (`entry.boundingClientRect.top < 0 ? -1 : 1`), и он возвращается с той же стороны.

`useReducedMotion()` из `motion/react` отключает всё: `initial={false}`, `animate='show'`.

Где используется параллакс: `ProductTour` (`amount={36}` на каждом макете), `AgentSection` (`amount={30}`), `Integration` (`amount={24}` на блоке кода).

Пререндер + скрытые блоки: HTML приходит с `opacity: 0` на элементах, поэтому в `landing.css` и в `<noscript>` есть страховка:
```css
@media (prefers-reduced-motion: reduce) { .lp [data-reveal] { opacity:1 !important; transform:none !important } }
```
```html
<noscript><style>.lp [data-reveal]{opacity:1 !important;transform:none !important}</style></noscript>
```

### 1.6 Шрифты
Ровно один запрос к Google Fonts (`frontend/index.html`, строки 88–90):
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Unbounded:wght@500;600;700&display=swap" rel="stylesheet">
```
- **Inter**: 400, 500, 600, 700 — основной текст.
- **Unbounded**: 500, 600, 700 — дисплейный (заголовки, цифры, логотип).
- Моноширинный — системный стек, без загрузки.

⚠️ Расхождение: `voicyfy.css` объявляет `--vf-font-display: 'Syne', var(--vf-font)`, но `.lp` переопределяет на `'Unbounded','Inter',sans-serif`. Syne на лендинге не загружается — за пределами `.lp` дисплейный шрифт деградирует в Inter.

### 1.7 Иконки — `frontend/src/components/Icon.jsx` (12 строк)
```jsx
<svg className={`ic ${className}`.trim()} aria-hidden="true">
  <use href={`/static/icons/ui.svg#i-${name}`} />
</svg>
```
- **Не lucide-react, не инлайн-SVG.** Внешний SVG-спрайт `/static/icons/ui.svg` (125 символов `<symbol id="i-…">`, иконки Lucide, лицензия `backend/static/icons/LICENSE-lucide.md`).
- Для клона спрайт обязателен; без него иконки пропадут (и при пререндере тоже — `<use>` не резолвится в SSR-строке).

Полный список используемых имён (36):
`arrow-right, audio-lines, bell, bot, brain, calendar, check, circle-alert, circle-check, clock, code, contact-round, copy, eye, eye-off, file-text, headset, house, info, lock, mail, menu, message-square, messages-square, mic, phone, phone-call, phone-incoming, phone-outgoing, play, plus, refresh-cw, send, square-check, triangle-alert, upload, wallet, x`

Размеры из `voicyfy.css`: `.ic{18×18}`, `.ic-sm{15×15}`, `.ic-lg{22×22}`; `.btn .ic{16×16}`; `.lp .btn .ic{17×17}`; `.chip .ic{13×13}`.

### 1.8 Логотипы моделей — `frontend/src/components/ModelLogo.jsx` (31 строка)
Обычные `<img>` на SVG-файлы из `/static/icons/models/`:
```js
export const MODELS = {
  openai:  { file: 'openai.svg',          name: 'OpenAI' },
  gemini:  { file: 'gemini-color.svg',    name: 'Gemini' },
  yandex:  { file: 'yandex-color.svg',    name: 'Яндекс' },
  fish:    { file: 'fishaudio-color.svg', name: 'Fish Audio' },
  cascade: { file: 'cascade-color.svg',   name: 'Каскад' },
};
export const MODEL_ORDER = ['openai','gemini','yandex','fish','cascade'];
```
Сигнатура: `<ModelLogo code size={22} wrap={true} />`.
- Класс `logo` + `logo-color` если имя файла оканчивается на `-color.svg` (т.е. у всех кроме `openai`).
- `wrap=true` → обёртка `<span class="logo-wrap" style="width:size+14; height:size+14">` (в ДС: `border-radius:10px; background:var(--vf-surface-2); border:1px solid var(--vf-border)`).
- Инлайн `style={{width:size,height:size}}` на самой картинке.
- Файлы реально лежат: `backend/static/icons/models/{openai.svg, gemini-color.svg, gemini.svg, yandex-color.svg, yandex.svg, fishaudio-color.svg, fishaudio.svg, cascade-color.svg, cascade.svg}` + `LICENSE.md`.
- В тёмной теме ДС: `[data-theme=dark] .logo-wrap img.logo { filter: invert(1) hue-rotate(180deg) }`, `.logo-color { filter:none }`. На лендинге тёмной темы нет.
- В модалке отдельная коррекция на тёмном фоне: `.lp-auth-model-openai .logo { filter: invert(1) }`, `.lp-auth-model-cascade .logo { filter: brightness(1.9) }`.

### 1.9 Данные с бэкенда — `frontend/src/hooks/useTariffs.js`
Один запрос `GET /api/wallet/tariffs` на страницу (кэш в модуле + дедупликация через `pending`).
- `useTariffs()` → массив `{code,name,price,rub}`. Фолбэк, пока нет ответа: `MODEL_ORDER` с `price: null`.
- `useWelcomeGrant()` → `data.welcome_grant_rub`, **фолбэк 65** (₽).
- `fmtPrice()`: `0` → `'бесплатно'`; иначе `«9 ₽/мин»`, дробные — запятая вместо точки, хвостовые нули срезаются.

Таким образом цены моделей на лендинге **динамические**; в разметку хардкодом попадают только цены телефонии и тарифов подписки.

---

## 2. Дизайн-токены

### 2.1 Токены ДС — `backend/static/css/voicyfy.css`, `:root`

**Поверхности**
| Токен | Значение | Назначение |
|---|---|---|
| `--vf-bg` | `#ffffff` | фон страницы |
| `--vf-surface` | `#ffffff` | панели, карточки |
| `--vf-surface-2` | `#f7f9fc` | вложенные блоки, подложки |
| `--vf-surface-3` | `#eef2f8` | hover строк |

**Границы**
| `--vf-border` | `#e3e8f0` |
| `--vf-border-strong` | `#cfd7e3` |

**Текст**
| `--vf-text` | `#0f172a` |
| `--vf-text-2` | `#475569` |
| `--vf-text-3` | `#64748b` |
| `--vf-text-4` | `#94a3b8` |

**Акцент и статусы**
| `--vf-accent` | `#2563eb` |
| `--vf-accent-hover` | `#1d4ed8` |
| `--vf-accent-soft` | `#eaf1ff` |
| `--vf-accent-ring` | `rgba(37,99,235,0.18)` |
| `--vf-success` | `#16a34a` |
| `--vf-success-soft` | `#e8f7ee` |
| `--vf-warning` | `#d97706` |
| `--vf-warning-soft` | `#fdf3e2` |
| `--vf-danger` | `#dc2626` |
| `--vf-danger-soft` | `#fdecec` |

**Тени**
```
--vf-shadow-1: 0 1px 2px rgba(15,23,42,.04), 0 1px 1px rgba(15,23,42,.03)
--vf-shadow-2: 0 1px 2px rgba(15,23,42,.05), 0 4px 12px -2px rgba(15,23,42,.08)
--vf-shadow-3: 0 2px 4px rgba(15,23,42,.06), 0 16px 40px -12px rgba(15,23,42,.18)
```

**Радиусы**: `--vf-r-sm: 8px`, `--vf-r-md: 12px`, `--vf-r-lg: 16px`, `--vf-r-full: 999px`.

**Шрифты**
```
--vf-font:         'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif
--vf-font-display: 'Syne', var(--vf-font)      ← переопределён в .lp на Unbounded
--vf-font-mono:    ui-monospace, SFMono-Regular, Menlo, Consolas, monospace
```

**Переходы**
```
--vf-fast:   140ms cubic-bezier(.2,.7,.2,1)
--vf-normal: 220ms cubic-bezier(.2,.7,.2,1)
```

**Каркас**: `--vf-sidebar-w: 256px`, `--vf-topbar-h: 64px` (для ЛК, на лендинге не используются).

Есть полный набор `[data-theme="dark"]` (тёмная тема ЛК), лендинг её не включает.

### 2.2 Токены лендинга — `.lp` в `landing.css`
```css
.lp {
  --lp-max: 1180px;                 /* ширина контейнера */
  --lp-violet: #7c3aed;             /* второй акцент: агент */
  --lp-violet-soft: #f1ebff;
  --vf-font-display: 'Unbounded','Inter',sans-serif;
  font-size: 15px;
  line-height: 1.6;
  color: var(--vf-text-2);
}
.lp-container { max-width: var(--lp-max); margin: 0 auto; padding: 0 24px; }  /* 16px ≤600px */
```

### 2.3 Типографика лендинга
| Селектор | Значение |
|---|---|
| `.vf .lp h1` | Unbounded, **42px**, lh 1.12, ls −0.02em, 700, `--vf-text`, margin `16px 0 22px`, `text-wrap: balance` |
| `.vf .lp h2` | Unbounded, **28px**, lh 1.18, ls −0.02em, 700, margin `0 0 14px`, balance |
| `.vf .lp h3` | Inter, **18px**, 700, ls −0.01em, margin `0 0 8px` |
| `.vf .lp h4` | **12px**, 600, UPPERCASE, ls .06em, `--vf-text-4`, margin `0 0 12px` |
| `.lp p` | margin 0 |
| `.lp-lead` | **16.5px**, lh 1.6, `--vf-text-2`, `max-width: 640px` |
| `.lp .muted` | `--vf-text-3` |
| `.lp .mono` | `--vf-font-mono`, `font-size: inherit` |
| `.lp-link` | inline-flex, gap 6px, 600, `--vf-accent`; hover — иконка `translateX(3px)` |
| `.lp .btn-lg` | высота **44px**, padding `0 20px`, 15px (переопределяет ДС-овские 42px/18px/14.5px) |
| `.lp .btn .ic` | 17×17 |
| `.chip-violet` | bg `--lp-violet-soft`, color `--lp-violet` |

### 2.4 Секционная сетка и фоны
```css
.sec       { padding: 96px 0; position: relative; }
.sec-alt   { background: var(--vf-surface-2); }          /* объявлен, не используется */
.sec-tint  { --tint: #f2f6ff; padding: 120px 0; }
.sec-tint::before  → linear-gradient(180deg, rgba(255,255,255,0) 0%, var(--tint) 14%, var(--tint) 86%, rgba(255,255,255,0) 100%)
.tint-lavender { --tint: #f4f0fc; }
.tint-blue     { --tint: #f1f5fd; }
.tint-sand     { --tint: #fbf6ee; }
.sec-tint + .sec, .sec + .sec-tint { padding-top: 72px; }
```

**`.sec-grid`** — фоновая сетка (только Hero и ProductTour):
```css
background-image:
  linear-gradient(var(--vf-border-strong) 1px, transparent 1px),
  linear-gradient(90deg, var(--vf-border-strong) 1px, transparent 1px),
  radial-gradient(circle, var(--vf-border-strong) 1.2px, transparent 1.6px);
background-size: 48px 48px (×3);
background-position: 0 0, 0 0, 24px 24px;
opacity: .6;   /* .hero.sec-grid::before → .42 */
mask-image: linear-gradient(180deg, transparent 0%, #000 12%, #000 82%, transparent 100%),
            radial-gradient(ellipse 90% 90% at 50% 45%, #000 40%, transparent 90%);
mask-composite: intersect;
```

**`.stack`** — «стопка карточек» под макетами экранов: два псевдоэлемента с `border-radius:14px`, `border:1px solid var(--vf-border)`, `background:var(--vf-surface)`:
- `::before` — `translate(14px,14px) scale(.985)`, opacity `.55`
- `::after` — `translate(28px,28px) scale(.97)`, opacity `.3`
- `.stack .frame { box-shadow: var(--vf-shadow-3) }`
- ≤900px: смещения `6px/6px scale(.99)` и `12px/12px scale(.98)`

**`.sh::before`** — гигантская «призрачная» цифра индекса раздела:
```css
content: attr(data-index); right:0; top:-40px;
font-family: Unbounded; font-size: 160px; 700; lh 1; ls -0.04em;
color: var(--vf-text); opacity: .04;   /* ≤900px: 96px, top:-24px */
```

### 2.5 Брейкпоинты и что меняется

| Брейкпоинт | Изменения |
|---|---|
| **≤1300px** | пустой блок (заготовка) |
| **≤1100px** | `.lp-nav-links` скрыт; `.lp-burger` показан; `.lp-nav-actions .btn-ghost` («Войти») скрыт; появляется `.lp-nav-mobile` (колонка, `padding 8px 16px 16px`, `max-height: calc(100vh - 64px)`, пункты 44px/15px); `h1 → 36px`; `.hero-grid` gap 36px; `.extra` → 1 колонка, gap 28px |
| **≤900px** | `h1 → 32px`, `h2 → 24px`; `.sec, .sec-tint → padding 64px 0`, стыки `56px`; `.sh` → 1 колонка, gap 6px, mb 36px; hero padding `36px 0 32px`, `.hero-grid` → 1 колонка, `.hero-visual` центрируется; `.models` mt 36px; `.tour-row` → 1 колонка, gap 24px, padding `40px 0`, `.tour-text` перестаёт быть sticky; `.tour-mock/.agent-mock` padding `0 12px 12px 0`; `.frame-side` (сайдбар макета) **скрыт**; `.agent-grid/.agent-split/.brains/.integ/.final-inner/.faq/.brains-flow/.steps` → 1 колонка; `.flow-step` теряет левую границу, получает верхнюю; `.call` → 1 колонка, padding 24px, `.call-num`/`.call-actions` выравниваются влево; `.cases li` → 1 колонка; `.lp-footer-inner` → 2 колонки; `.m-agent` → 1 колонка; `.m-kanban` → 2 колонки |
| **≤760px** (только модалка) | `.lp-auth` → 1 колонка, `max-width 460px`, `min-height 0`, `border-radius 20px`; `.lp-auth-brand` padding `16px 20px`, gap 0, верхний ряд в строку; скрываются `.lp-auth-brand-switch`, `.lp-auth-models`, `.lp-auth-brand::after`; появляется `.lp-auth-brand-greet` (15px/600, «С возвращением, <span>Имя</span>»); pane padding `22px 20px 20px`; `.lp-auth-title → 21px`; `.lp-auth-row` → 1 колонка |
| **≤600px** | `.lp-container` padding `0 16px`; `h1 → 26px`, `h2 → 21px`; `.hero-lead/.lp-lead → 15.5px`; `.lp-nav-actions .btn-primary` → 34px/13px; кнопки в `.hero-actions/.final-actions/.sec-actions` на всю ширину; `.lp-pop` на всю ширину; `.hero-facts` → 1 колонка; `.plans-card` padding `4px 12px 12px`; `.models-item` padding `12px 14px`, 13px; `.cc { height: 420px }`, `.cc-foot` в колонку; `.call-phone → 20px`; `.facts > div` → 1 колонка; `.lp-footer-inner` → 1 колонка; `.day li` → `56px 1fr`; `.m-kanban/.m-prices/.m-models/.m-test-body` → 1 колонка; в `.m-table` скрывается 2-й столбец |
| **prefers-reduced-motion** | `.cc-live`, `.vf-wave i` — без анимации; `.cc-msg` — без transition; `.lp [data-reveal]` — `opacity:1; transform:none`; Lenis не инициализируется; в ДС глобально `animation/transition-duration: .01ms !important` |

### 2.6 Базовые компоненты ДС (нужны для 1:1)

**Кнопки**
```css
.btn { inline-flex; center; gap:8px; height:36px; padding:0 14px; border-radius:8px;
       border:1px solid var(--vf-border-strong); background:var(--vf-surface); color:var(--vf-text);
       font-weight:500; font-size:13.5px; box-shadow:var(--vf-shadow-1);
       transition: background/border/color/box-shadow/transform var(--vf-fast) }
.btn:hover  { background: var(--vf-surface-2) }
.btn:active { transform: translateY(1px); box-shadow: none }
.btn-primary { background:#2563eb; border-color:#2563eb; color:#fff;
               box-shadow: 0 1px 2px rgba(37,99,235,.3), inset 0 1px 0 rgba(255,255,255,.12) }
.btn-primary:hover { background:#1d4ed8 }
.btn-ghost { border-color:transparent; background:transparent; box-shadow:none; color:var(--vf-text-2) }
.btn-ghost:hover { background: var(--vf-surface-3) }
.btn-sm { height:30px; padding:0 10px; 12.5px; radius 7px }
.btn-lg { height:42px; padding:0 18px; 14.5px; radius 10px }   /* на лендинге → 44px/0 20px/15px */
.btn-icon { width:34px; padding:0 }
.btn[disabled], .btn.is-loading { opacity:.55; pointer-events:none }
```

**Поля**
```css
.field { flex column; gap:6px; margin-bottom:16px }           /* .lp-form .field → mb 14px */
.label { 12.5px; 600; var(--vf-text-2) }
.input { width:100%; height:38px; padding:0 12px; radius 8px; border:1px solid var(--vf-border-strong);
         14px; box-shadow: var(--vf-shadow-1) }               /* .lp-form .input → 44px, radius 10px */
.input:hover { border-color:#b8c3d4 }
.input:focus { border-color: var(--vf-accent); box-shadow: 0 0 0 3px var(--vf-accent-ring) }
.input-wrap { position:relative }
.input-wrap > .ic { absolute; left:11px; top:50%; translateY(-50%); color:var(--vf-text-4) }
.input-wrap > .input { padding-left: 36px }
```

**Карточки / чипы / примечания**
```css
.card { background:var(--vf-surface); border:1px solid var(--vf-border); radius 16px; shadow-1 }
.card-raised { box-shadow: var(--vf-shadow-2) }
.chip { inline-flex; gap:6px; height:24px; padding:0 9px; radius 999px; 12px/500;
        background:var(--vf-surface-3); color:var(--vf-text-2); border:1px solid transparent }
.chip-accent{bg accent-soft; color accent}  .chip-success{bg success-soft; color success}
.chip-warning{…} .chip-danger{…} .chip-outline{bg transparent; border-color border-strong}
.dot { 8×8; radius 50%; bg var(--vf-text-4) }  .dot-success{bg success}
.note { flex; gap:10px; padding:10px 12px; radius 8px; 12.5px; color text-2;
        bg surface-2; border:1px solid var(--vf-border) }
.spin { 16×16; border:2px solid rgba(255,255,255,.35); border-top-color:currentColor; radius 50%;
        animation: vf-spin .8s linear infinite }
.btn:not(.btn-primary) .spin { border-color: var(--vf-border-strong); border-top-color: var(--vf-accent) }
```

**Логотип**
```css
.vf-logo { flex; gap:10px; color:var(--vf-text); font-family: display; 800; 19px; ls -0.03em }
.vf-logo img { 30×30; border-radius:8px; object-fit:contain }
.vf-logo .wordmark { background: linear-gradient(135deg, #2563eb, #3b82f6);
                     -webkit-background-clip:text; -webkit-text-fill-color: transparent }
/* лендинг переопределяет: */
.lp .vf-logo { font-size:18px; font-weight:700; letter-spacing:-0.02em }
```

**Голосовая волна (используется в аватаре CallCard)**
```css
.vf-wave { flex; align-items:center; gap:4px; height:28px }
.vf-wave i { 4px × 8px; radius 2px; background: var(--vf-accent);
             animation: vf-wave 1.1s ease-in-out infinite }
/* задержки детей: 0, 120, 240, 360, 480ms */
@keyframes vf-wave { 0%,100% { height:8px; opacity:.55 } 50% { height:26px; opacity:1 } }
/* в .cc-avatar: height 24px, gap 3px, i { width: 3px } */
```

Также: `html { scroll-behavior: smooth; scrollbar-gutter: stable }`, `body.vf { overscroll-behavior-y: none }`, тонкий скроллбар `scrollbar-color: var(--vf-border-strong) transparent`.

---

## 3. Структура страницы

`App.jsx` → `<div className="lp">`:
```
<Navbar onOpenModal/>
<main>
  <Hero/>           #top        .hero.sec-grid
  <ProductTour/>    #platform   .sec.sec-grid          SectionHead 01
  <AgentSection/>   #agent      .sec.sec-tint.tint-lavender   02
  <Start/>          #how        .sec                          03
  <Integration/>    #integration .sec.sec-tint.tint-blue      04
  <Scenarios/>      #cases      .sec.sec-tint.tint-sand       05
  <Pricing/>        #pricing    .sec.sec-tint.tint-blue       06
  <Faq/>            #faq        .sec                          07
  <FinalCta/>                   .sec.final
</main>
<Footer/>
<AuthModal isOpen activeTab setActiveTab onClose/>
```
Состояние в `App`: `activeTab` (`'register'` по умолчанию), `isModalOpen`, `lenisRef`. `openModal(tab)` передаётся во все секции с CTA. Вызывается `useAuth()` — если в `localStorage` есть `auth_token`, немедленный редирект на `/static/dashboard.html`.

**`SectionHead`** (`SectionHead.jsx`) — общая шапка раздела:
```jsx
<Reveal className="sh" y={16} data-index={index}>
  <span className="sh-index">{index}</span>
  <div className="sh-text"><h2>{title}</h2>{lead && <p className="lp-lead">{lead}</p>}{children}</div>
</Reveal>
```
```css
.sh { grid; grid-template-columns: 96px 1fr; gap:20px; align-items:start; margin-bottom:52px }
.sh-index { mono; 13px; 600; color: var(--vf-accent); padding-top:12px; ls .04em }
.sh-text { max-width: 760px }
.sh .lp-lead { margin-top: 6px }
.sec-actions { flex; gap:18px; wrap; margin-top:40px; 14px }
```

---

## 4. Секции подробно

### 4.1 Navbar — `components/Navbar.jsx` (93 строки)

**Структура**
```html
<header class="lp-nav [scrolled]">
  <div class="lp-container lp-nav-inner">
    <a href="#top" class="vf-logo lp-logo" aria-label="Voicyfy">
      <img src="/static/images/IMG_2820.PNG" alt=""><span class="wordmark">Voicyfy</span>
    </a>
    <nav class="lp-nav-links"> 8 × <a class="lp-nav-link [on]"> </nav>
    <div class="lp-nav-actions">
      <button class="btn btn-ghost">Войти</button>
      <button class="btn btn-primary">Начать бесплатно</button>
      <button class="btn btn-icon lp-burger" aria-expanded><Icon menu|x/></button>
    </div>
  </div>
  {open && <div class="lp-nav-mobile">{links}<div class="lp-nav-mobile-actions">2 кнопки btn-lg</div></div>}
</header>
```

**Поведение**
- `sticky top:0; z-index:50`; фон `rgba(255,255,255,.9)` + `backdrop-filter: blur(10px)` (+ `-webkit-`); `border-bottom: 1px solid transparent`.
- Класс `.scrolled` при `window.scrollY > 8` → `border-bottom-color: var(--vf-border)`, `box-shadow: 0 4px 12px -8px rgba(15,23,42,.12)`. Слушатель `{passive:true}`.
- Активный пункт через `IntersectionObserver` по якорным секциям: `rootMargin: '-40% 0px -50% 0px'`, `threshold: [0, 0.1, 0.5]`; выбирается запись с максимальным `intersectionRatio`.
- Мобильное меню: `document.body.style.overflow = 'hidden'` пока открыто.

**Стили**
```css
.lp-nav-inner { height: 64px; flex; gap: 24px }
.lp-nav-links { flex; gap: 2px; margin-left: 8px }
.lp-nav-link  { height:64px; padding:0 11px; 14px/500; color: var(--vf-text-3);
                transition: color var(--vf-fast) }
.lp-nav-link::after { absolute; left:11px; right:11px; bottom:18px; height:2px; radius:2px;
                      background: var(--vf-accent); transform: scaleX(0); transform-origin:left;
                      transition: transform 180ms cubic-bezier(.2,.7,.2,1) }
.lp-nav-link:hover, .lp-nav-link.on → color: var(--vf-text); ::after scaleX(1)
.lp-nav-actions { margin-left:auto; flex; gap:8px }
.lp-burger, .lp-nav-mobile { display:none }   /* включаются ≤1100px */
```

**Ссылки (порядок и тексты)**
| href | Подпись | Тип |
|---|---|---|
| `#platform` | Кабинет | якорь |
| `#agent` | Агент | якорь |
| `#how` | Как начать | якорь |
| `#integration` | Виджет | якорь |
| `#pricing` | Тарифы | якорь |
| `/static/prompts-wiki.html` | База знаний | внешняя страница |
| `/static/api-docs.html` | API | внешняя страница |
| `https://t.me/voicyfy_support` | Поддержка | `external: true` → `target=_blank rel=noopener` |

Кнопки: **«Войти»** (`btn btn-ghost` → `openModal('login')`), **«Начать бесплатно»** (`btn btn-primary` → `openModal('register')`). Бургер: `aria-label` = «Открыть меню» / «Закрыть меню».

### 4.2 Hero — `components/Hero.jsx` (93 строки)

Экспортирует константы, переиспользуемые в `Start.jsx`:
```js
export const PHONE = '+79311071031';
export const PHONE_DISPLAY = '+7 931 10-710-31';
```

**Структура**: `<section class="hero sec-grid" id="top"> > .lp-container > .hero-grid` + строка `.models`.

Сетка:
```css
.hero { padding: 56px 0 40px }
.hero-grid { grid-template-columns: minmax(0,1.05fr) minmax(0,.95fr); gap: 56px; align-items: center }
.hero-visual { display:flex; justify-content: flex-end }
```

**Левая колонка** — `<Stagger className="hero-copy" stagger={0.09} amount={0.1}>`:

1. `<Item as="p" className="hero-kicker" y={10}>` — **«Voicyfy · голосовой ИИ, который создаёте вы»**
   `Unbounded, 11.5px, 500, ls .06em, UPPERCASE, color --vf-text-4`
2. `<Item as="h1" y={22}>` — **«Платформа голосовых ИИ‑ассистентов и агентов для бизнеса»** (обратите внимание: в тексте узкие неразрывные дефисы `U+2011` в «ИИ‑ассистентов»). 42px Unbounded 700.
3. `<Item as="p" className="hero-lead">` — **«Ассистент отвечает на звонки и разговаривает на сайте. Агент сам обзванивает клиентов, пишет в мессенджеры и помнит каждого. Собираются за десять минут, без программирования.»**
   `17.5px, lh 1.6, --vf-text-2, max-width 560px`
4. `<Item className="hero-actions">` (`flex; gap:12px; margin-top:28px`):
   - `btn btn-primary btn-lg` → **«Создать ассистента»** + `arrow-right`, открывает модалку регистрации
   - `.lp-pop-wrap > a.btn.btn-lg href="tel:+79311071031"` → иконка `phone` + **«Позвонить ИИ»**
     На десктопе (`window.innerWidth >= 768` и не мобильный UA) клик перехватывается → открывается поповер.
5. `<Item as="dl" className="hero-facts" y={12}>` — 4 факта, сетка `repeat(2, minmax(0,1fr))`, `gap 16px 28px`, `margin-top 32px`, `max-width 560px`; каждый `> div` имеет `border-top: 1px solid var(--vf-border); padding-top: 10px`:

| dt (Unbounded 10.5px/500 UPPERCASE ls .06em, text-4) | dd (Unbounded 13px/600 lh1.4, text) |
|---|---|
| Пробный период | 3 дня, без карты |
| На кошельке | **{welcome}** ₽ в подарок для теста (по умолчанию 65) |
| Первый звонок | тестовый номер на 10 минут |
| Оплата | посекундно, по тарифу модели |

**Поповер с номером** (`.lp-pop.card.card-raised`, `role="dialog" aria-label="Тестовый номер"`):
```css
.lp-pop-backdrop { fixed; inset:0; z-index:40 }
.lp-pop { absolute; left:0; top: calc(100% + 10px); z-index:41; width:300px; padding:18px; text-align:center }
.lp-pop-num  { Unbounded 18px 700, --vf-text, nowrap }
.lp-pop-hint { 12.5px, --vf-text-3, margin 4px 0 14px }
.lp-pop .btn { width: 100% }
.lp-pop-or   { 12px, --vf-text-4, margin-top 10px }
```
Тексты: номер `+7 931 10-710-31`; подпись **«Наш агент на линии · работает на Каскаде · 24/7»**; кнопка **«Скопировать номер»** / после клика **«Скопировано»** (иконка `copy` → `check`, сброс через 2000 мс); внизу **«или откройте сайт на телефоне и нажмите «Позвонить ИИ»»**.

**Правая колонка** — `<Stagger className="hero-visual" delay={0.3} amount={0.1}>` c одним `<Item x={40} y={0} rotate={1.5} duration={0.8}><CallCard/></Item>` (въезжает справа с лёгким поворотом).

**Строка моделей** `<Stagger className="models" stagger={0.06} delay={0.45} amount={0.3}>`:
```css
.models { flex; wrap; gap:0; margin-top:48px;
          border-top:1px solid var(--vf-border); border-bottom:1px solid var(--vf-border) }
.models-label { flex column; gap:3px; Unbounded 10.5px/500 UPPERCASE ls .06em; text-4; padding:12px 20px 12px 0 }
.models-label em { font-style:normal; Inter 11.5px; текст как есть; text-4 }
.models-item  { inline-flex; gap:8px; padding:16px 14px; border-left:1px solid var(--vf-border);
                Unbounded 12px/500; color var(--vf-text); nowrap }
.models-item .logo { 18×18 }
.models-item b { 600; var(--vf-text-3); font-variant-numeric: tabular-nums }
.models-note  { margin-left:auto; 12px; text-4; padding:16px 0 16px 16px }
```
Подпись: **«Голосовые модели»** + `<em>` **«со своим ключом бесплатно»**. Дальше 5 элементов из `useTariffs()`: логотип (18px, без обёртки) + название + цена жирным (`«9 ₽/мин»` и т.п., или ничего, пока нет ответа API). Названия: OpenAI, Gemini, Яндекс, Fish Audio, Каскад. `.models-note` в разметке не используется.

### 4.3 CallCard — анимированный виджет звонка (`components/CallCard.jsx`, 171 строка)

Главный визуальный акцент Hero. Не скриншот, не видео — собранный из DOM «экран звонка».

**Четыре сценария (`SCENES`)** — каждый проигрывается один раз, затем следующий по кругу; порядок перемешивается **после монтирования** (чтобы SSR и гидрация совпали по первой сцене):

| key | label (чип-таб) | dir | name (заголовок карточки) | model |
|---|---|---|---|---|
| `salon` | Входящий · салон | in | Администратор салона | gemini |
| `clinic` | Входящий · клиника | in | Регистратура клиники | yandex |
| `showroom` | Исходящий · агент | out | Агент шоурума | openai |
| `service` | Напоминание · агент | out | Агент автосервиса | fish |

**Реплики (полный текст)**

*salon*
1. user: «Здравствуйте, хочу записаться на стрижку в субботу»
2. bot: «Конечно! В субботу свободно 11:00 и 15:30. Какое время удобнее?»
3. user: «Давайте 15:30, к Ольге»
4. bot: «Записала к Ольге на 15:30. Пришлю SMS с адресом.» — теги `crm, calendar, sms`

*clinic*
1. user: «Мне нужно перенести приём с четверга»
2. bot: «Вижу запись к терапевту на четверг, 10:00. Подойдёт пятница в то же время?»
3. user: «Да, пятница подходит»
4. bot: «Перенесла на пятницу, 10:00. Напомню за день.» — теги `calendar, bell, crm`

*showroom*
1. bot: «Марина, добрый день! Шоурум «Лея». Пришла новая коллекция, хотела рассказать.»
2. user: «Интересно. Платья 44 размера есть?»
3. bot: «Шесть моделей. Отправлю подборку в Telegram и запишу на примерку в субботу?» — тег `telegram`
4. user: «Да, давайте» — теги `crm, calendar`

*service*
1. bot: «Игорь, добрый день, автосервис «Мотор». Напоминаю: завтра в 9:00 замена масла.»
2. user: «А можно перенести на 11?»
3. bot: «В 11:00 свободно, перенёс. Пришлю SMS с подтверждением. Ждём вас!» — теги `calendar, sms, crm`

**Словарь тегов (`TAGS`)**
| key | icon | label |
|---|---|---|
| crm | `square-check` | Запись в CRM |
| calendar | `calendar` | Календарь |
| bell | `bell` | Уведомление |
| sms | `message-square` | SMS |
| telegram | `send` | Telegram |

**Тайминги**
- Реплика *i* проявляется через `700 + i * 1500` мс.
- Переход к следующей сцене: `700 + msgs.length * 1500 + 4500` мс.
- Таймер разговора: `setInterval` 1000 мс, обнуляется при смене сцены, формат `MM:SS` (`padStart(2,'0')`).
- Все реплики отрисованы в DOM сразу (`opacity:0`), только «проявляются» — раскладка не прыгает.
- При `useReducedMotion()` — сразу показываются все реплики, автопереход сцен отключён.
- Клик по табу: если та же сцена — инкремент `run` (перезапуск), иначе смена `sceneKey`.

**DOM и стили**
```html
<div class="cc-wrap">
  <div class="cc-scenes" role="tablist" aria-label="Сценарии звонка">
    4 × <button class="cc-scene [on]" role="tab" aria-selected>
          <Icon phone-incoming|phone-outgoing ic-sm/>{label}</button>
  </div>
  <div class="cc" aria-hidden="true">
    <div class="cc-head">
      <div class="cc-avatar"><span class="vf-wave"><i×5></span></div>
      <div class="cc-who"><div class="cc-name">{name}</div>
        <div class="cc-sub"><span class="cc-live"></span>Входящий|Исходящий звонок · MM:SS</div></div>
      <span class="chip chip-success"><Icon .../>На линии</span>
    </div>
    <div class="cc-msgs">4 × <div class="cc-msg cc-msg-user|bot [on]"><span>{text}</span></div></div>
    <div class="cc-foot">
      <span class="cc-model"><ModelLogo 14/>{MODELS[model].name}</span>
      <div class="cc-tags">N × <span class="cc-tag [on]"><Icon/>{label}</span></div>
    </div>
  </div>
</div>
```
```css
.cc-wrap { width:100%; max-width:460px }
.cc-scenes { flex; wrap; gap:6px; margin-bottom:12px }
.cc-scene { inline-flex; gap:6px; height:28px; padding:0 10px; radius 999px;
            border:1px solid var(--vf-border); background:var(--vf-surface);
            12.5px/500; color var(--vf-text-3); cursor:pointer; transition all var(--vf-fast) }
.cc-scene:hover { border-color: var(--vf-border-strong); color: var(--vf-text) }
.cc-scene.on { background: var(--vf-text); border-color: var(--vf-text); color:#fff }   /* тёмный чип */
.cc { background:var(--vf-surface); border:1px solid var(--vf-border); border-radius:18px;
      box-shadow: var(--vf-shadow-3); padding:18px; height:392px; flex column }   /* 420px ≤600px */
.cc-head { flex; gap:12px; padding-bottom:14px; border-bottom:1px solid var(--vf-border) }
.cc-avatar { 46×46; radius 13px; background: var(--vf-accent-soft); center }
.cc-name { 700; var(--vf-text); 14.5px; ellipsis }
.cc-sub  { flex; gap:6px; 12.5px; var(--vf-text-3); tabular-nums }
.cc-live { 7×7; radius 50%; background: var(--vf-success); box-shadow: 0 0 0 3px var(--vf-success-soft);
           animation: cc-pulse 1.6s ease-in-out infinite }
@keyframes cc-pulse { 0%,100%{ box-shadow: 0 0 0 3px var(--vf-success-soft) } 50%{ box-shadow: 0 0 0 6px transparent } }
.cc-msgs { flex column; gap:9px; padding:14px 0 8px; flex:1 1 auto; overflow:hidden; justify-content:flex-start }
.cc-msg  { flex; opacity:0; transform: translateY(6px);
           transition: opacity .35s ease, transform .35s cubic-bezier(.2,.7,.2,1) }
.cc-msg.on { opacity:1; transform:none }
.cc-msg span { max-width:86%; padding:9px 13px; border-radius:14px; 13.5px; lh 1.45 }
.cc-msg-user span { background: var(--vf-surface-3); color: var(--vf-text); border-bottom-left-radius: 5px }
.cc-msg-bot  { justify-content: flex-end }
.cc-msg-bot span { background: var(--vf-accent); color:#fff; border-bottom-right-radius: 5px }
.cc-foot { flex; space-between; gap:10px; padding-top:12px; border-top:1px solid var(--vf-border) }
.cc-model { inline-flex; gap:6px; 12px/600; var(--vf-text-3); .logo { 14×14 } }
.cc-tag { inline-flex; gap:5px; height:26px; padding:0 9px; radius 999px; 12px/500;
          color var(--vf-text-4); background var(--vf-surface-2); border:1px solid var(--vf-border);
          transition all var(--vf-normal) }
.cc-tag.on { color: var(--vf-success); background: var(--vf-success-soft); border-color: transparent }
```
⚠️ Баг в CSS: селектор `.cc-msg-user.cc-foot` (строка 144 `landing.css`) задаёт стили футера, хотя `.cc-foot` в JSX не имеет класса `cc-msg-user` — футер получает стили из более общих правил, а это правило мёртвое. При переносе лучше писать просто `.cc-foot`.

### 4.4 ProductTour — «Кабинет» — `components/ProductTour.jsx` (71 строка), `id="platform"`

**SectionHead 01**
- Заголовок: **«Кабинет, в котором всё это работает»**
- Лид: **«Пять экранов, которые вы увидите после регистрации. Ассистенты, телефония, диалоги, CRM и база знаний, без сторонних сервисов.»**

**Раскладка**: `.tour > 5 × .tour-row`
```css
.tour-row { grid-template-columns: minmax(0,5fr) minmax(0,7fr); gap:56px; align-items:start;
            padding:64px 0; border-top:1px solid var(--vf-border) }
.tour-row:first-child { border-top:none; padding-top:0 }
.tour-text { position: sticky; top: 96px }
.tour-mock { padding: 0 28px 28px 0 }       /* место для «стопки» */
.tour-n    { mono 12.5px/600; var(--vf-accent); ls .04em }
.tour-text h3 { Unbounded 21px/600; ls -0.01em; margin 10px 0 12px }
.tour-text p  { 15px; lh 1.6; var(--vf-text-2) }
```
Текстовая часть — `<Reveal className="tour-text" x={-16} y={0}>` (въезд слева), макет — `<Parallax amount={36}><Reveal className="stack" y={24} delay={0.1}>`.

Список «фактов» в каждой строке — `dl.facts`:
```css
.facts { margin:18px 0 0; grid; gap:0 }
.facts > div { grid-template-columns: 132px 1fr; gap:12px; padding:9px 0;
               border-top:1px solid var(--vf-border); 13.5px; lh 1.5 }
.facts > div:last-child { border-bottom: 1px solid var(--vf-border) }
.facts dt { color: var(--vf-text-4) }
.facts dd { margin:0; color: var(--vf-text) }
```

**Полный контент 5 строк**

**01. Конструктор ассистента**
Текст: «Имя, первая фраза, промпт и голосовая модель на одной странице. Пять моделей, цена за минуту у каждой. Функции и база знаний подключаются вкладками, там же тест в браузере и код виджета.»
Факты: Модели → «OpenAI, Gemini, Яндекс, Fish Audio, Каскад»; Каналы → «телефония, виджет на сайте»; Смена модели → «в один клик, номера перепривязываются сами».
Макет: `<MockAssistant/>`.

**02. Телефония**
Текст: «Номер покупается в кабинете и привязывается к ассистенту. До покупки есть тестовый номер: десять минут, чтобы позвонить ассистенту с телефона. Входящие, исходящие, SMS, запись и лог каждого звонка.»
Факты: Входящий → «1,7 ₽/мин»; Исходящий → «2,7 ₽/мин»; Верификация → «онлайн, по закону РФ, физлицо, ИП или юрлицо».
Макет: `<MockTelephony/>`.

**03. Диалоги**
Текст: «Каждый разговор сохраняется: расшифровка, запись, длительность, источник и итог. Фильтры по дате и каналу. Отсюда видно, что ассистент сказал и что сделал.»
Факты: Источники → «звонки, виджет, агент»; Итог → «запись, задача, контакт в CRM»; Экспорт → «для отчётов».
Макет: `<MockDialogs/>`.

**04. CRM**
Текст: «Карточки клиентов создаются из разговоров сами. Стадия воронки, контакты, факты, которые клиент говорил хотя бы раз, и вся история общения в одной хронологии.»
Факты: Воронка → «новый, в работе, успех, отказ, не звонить»; Импорт → «xlsx и csv»; Память → «сводка, факты, лучшее время для звонка».
Макет: `<MockCrm/>`.

**05. База знаний**
Текст: «Загрузите прайсы, инструкции и ответы на частые вопросы в любом формате. Ассистент находит нужный факт во время разговора и отвечает по нему, а не выдумывает.»
Факты: Форматы → «pdf, docx, txt, текст»; Индексация → «автоматически после загрузки»; Подключение → «к любому ассистенту».
Макет: `<MockKnowledge/>`.

**Подвал секции** `<Reveal className="tour-foot" y={12}>`:
```css
.tour-foot { flex; space-between; gap:24px; wrap; margin-top:40px; padding-top:24px;
             border-top:1px solid var(--vf-border); 14.5px; var(--vf-text-2) }
```
Текст: **«Ключи провайдеров уже подключены. Оплата посекундная, с кошелька, по тарифу выбранной модели. Со своим ключом минуты бесплатны.»**
Ссылка `.lp-link` → `#pricing`: **«Тарифы и цены за минуту»** + `arrow-right`.

### 4.5 Mockups — `components/Mockups.jsx` (191 строка)

**Это НЕ скриншоты.** Все «экраны кабинета» нарисованы вручную из div'ов с классами `.frame*` и `.m-*` (~100 строк CSS в `landing.css`, строки 164–252). Нечитаемый текст заменён серыми полосами `.m-bar`.

**Общая рамка `<Frame active title wide>`**
```html
<div class="frame">
  <div class="frame-side">
    <div class="frame-logo"><img src="/static/images/IMG_2820.PNG"><span>Voicyfy</span></div>
    <div class="frame-nav"> 6 × <span class="frame-nav-item [on]"><Icon/>{label}</span> </div>
    <div class="frame-wallet"><span>Кошелёк</span><b>1 240 ₽</b></div>
  </div>
  <div class="frame-main">
    <div class="frame-top"><b>{title}</b><span class="frame-ava">АП</span></div>
    <div class="frame-body">{children}</div>
  </div>
</div>
```
Пункты меню: `house` Дашборд · `headset` Агент · `audio-lines` Голосовые ассистенты · `messages-square` Диалоги · `phone` Телефония · `contact-round` CRM.

```css
.frame { flex; width:100%; min-height:380px; border:1px solid var(--vf-border); radius:14px;
         background:var(--vf-surface); box-shadow:var(--vf-shadow-2); overflow:hidden;
         12px; lh 1.45; color var(--vf-text-2) }
.frame-side { flex:0 0 156px; border-right:1px solid var(--vf-border); padding:14px 10px; gap:12px }
.frame-logo { Unbounded 700 12px; color var(--vf-accent); padding: 0 8px 6px; img 20×20 radius 6px }
.frame-nav-item { height:30px; padding:0 8px; radius:8px; 12px/500; color var(--vf-text-3) }
.frame-nav-item.on { background: var(--vf-accent-soft); color: var(--vf-text); 600; .ic → accent }
.frame-wallet { margin-top:auto; padding:10px; radius 10px; bg surface-2; border; 11px; text-4; b { 14px, text } }
.frame-top { height:44px; space-between; padding:0 16px; border-bottom:1px solid var(--vf-border) }
.frame-top b { Unbounded 12px/600 }
.frame-ava { 24×24; radius 50%; background var(--vf-accent); #fff; 10px/700 }
.frame-body { padding:16px; flex column; gap:12px }
.frame .chip { height:20px; padding:0 7px; 10.5px }
.frame .btn-sm { height:26px; 11.5px }
```
На ≤900px `.frame-side` скрывается целиком.

**`MockAssistant`** — экран «Голосовые ассистенты», `Stagger stagger={0.07} amount={0.3}`:
- Шапка: логотип Gemini (14px), **«Менеджер по записи»**, чип-успех с точкой **«активен»**
- Вкладки `.m-tabs`: **Настройки** (активна) · Функции · База знаний · Тестирование · Встраивание
  (`.m-tabs span.on { color: var(--vf-accent); border-bottom-color: var(--vf-accent); 600 }`)
- Поле «Первая фраза» → **«Здравствуйте! Салон «Лея», чем могу помочь?»**
- Поле «Системный промпт» → `.m-textarea` (min-height 64px) с 4 полосками `.m-bar` шириной 92%, 78%, 85%, 40%
- Поле «Голосовая модель» → `.m-models` (2 колонки, gap 8px), 4 карточки:
  Каскад «бесплатно» · **Gemini «6 ₽/мин»** (активная, `.on`) · OpenAI «9 ₽/мин» · Яндекс «4,5 ₽/мин»
  `.m-model.on { border-color: var(--vf-accent); background: var(--vf-accent-soft); box-shadow: 0 0 0 1px var(--vf-accent) }`, галочка `circle-check` справа.

**`MockTelephony`** — экран «Телефония»:
- Карточка **«Тестовый номер»** + чип-успех **«Включён»**; «Позвоните на номер» → **`+7 933 091-64-41`**; «Отвечает: Менеджер по записи · Gemini»; таймер `.m-timer` — **07:42** (зелёный, Unbounded 20px/600, tabular-nums), подпись «до отключения», прогресс-бар 120×5px заполнен на **77%**
- Карточка **«Мои номера»** + `btn btn-sm btn-primary` **«Купить номер»**; таблица:
  `+7 495 ••• 12-40` / Москва / чип-accent «Менеджер по записи»
  `+7 812 ••• 07-15` / Санкт-Петербург / чип «без привязки»
- `.m-prices` (3 колонки): Входящий **1,7 ₽/мин** · Исходящий **2,7 ₽/мин** · SMS **по тарифу оператора**

**`MockDialogs`** — экран «Диалоги»:
- Список: «Сегодня, 14:30 / Входящий · +7 921 ••• 44-10 / 02:14 / чип-success «запись»»; «Сегодня, 12:05 / Исходящий · агент шоурума / 01:48 / чип-accent «в работе»»; «Вчера, 19:40 / Виджет на сайте / 00:52 / чип «вопрос»»
- Карточка **«Расшифровка»** + чип с иконкой `play` **«Запись»**; 4 реплики:
  К: «Есть свободное время на пятницу?» / бот: «Да, в 11:00 и 15:30. Какое удобнее?» / К: «В 11 подойдёт» / бот: «Записала на пятницу, 11:00. Пришлю SMS с адресом.»
- Итог `.m-result` (зелёный, 11.5px/600): **«Итог: запись создана, контакт добавлен в CRM»**

**`MockCrm`** — канбан `.m-kanban` (4 колонки, ≤900px — 2, ≤600px — 1):
- **Новые** (2): Алексей П., Ирина К.
- **В работе** (3): Марина С. *(выделена: `border-color accent + ring`, с чипами-фактами `chip-outline` **«платья, 44»**, **«суббота»**)*, Игорь Д., Ольга В.
- **Успех** (2): Дмитрий Р., Анна Л.
- **Отказ** (1): Сергей М.
- У каждого контакта телефон-заглушка `+7 9•• ••• ••-••` (mono, muted)

**`MockKnowledge`** — экран «База знаний»:
- Файлы: «Прайс-лист 2026.pdf» / 48 фрагментов; «Ответы на частые вопросы.docx» / 21 фрагмент; «Адреса и часы работы.txt» / 3 фрагмента — все с чипом-успех **«проиндексирован»**
- Дроп-зона (`.m-row.m-drop`, пунктирная верхняя граница): иконка `upload` + **«Перетащите файлы или вставьте текст»**
- Карточка **«Проверка ответа»**: «?»: «Сколько стоит окрашивание в один тон?» → бот: «Окрашивание в один тон от 3 500 ₽, длинные волосы от 4 200 ₽. Источник: Прайс-лист 2026, стр. 2.»

**`MockAgent`** (используется в AgentSection, `wide`) — экран «Агент шоурума», `.m-agent` (`grid-template-columns: 1.2fr .8fr; gap 12px`):
- Левая карточка **«Чат с агентом»** + чип с иконкой `send` **«Telegram»**:
  - Вы: «Обзвони всех из «в работе», расскажи о новой коллекции. Кому интересно, отправь подборку и запиши на примерку»
  - Агент: «Поставил 87 звонков по стадии «в работе». Стартую в 10:00 по вашим рабочим часам. Подборку отправлю в Telegram тем, кто ответит «интересно».»
  - Вы: «Как прошёл обзвон?»
  - Агент: «Дозвонился до 61, заинтересовались 23, записал на примерку 9. Недозвонам перезвоню завтра, 4 просили не звонить.»
- Правая колонка:
  - **«Задачи агента»** + чип-accent «завтра»: 10:00 «Перезвонить: 26 недозвонов» · 12:00 «Напомнить Марине о примерке» · 15:30 «Написать Ирине: подборка» · 18:00 «Отчёт за день»
  - **«Карточка: Марина С.»** + чип-accent «в работе»: «**Сводка.** Интересуют платья 44 размера, удобно в субботу, подборка отправлена.» / «**Лучшее время.** После 14:00, утром не берёт трубку.»

Стили реплик:
```css
.m-line > span:last-child { padding:7px 10px; radius 11px; background: var(--vf-surface-3);
                            border-bottom-left-radius: 4px }
.m-line.bot > span:last-child { background: var(--vf-accent); color:#fff;
                                border-bottom-left-radius:11px; border-bottom-right-radius:4px }
.m-ava { 24×24; radius 50%; bg surface-3; 10px/700 }
.m-ava.bot { background: var(--vf-accent-soft); color: var(--vf-accent) }
```

### 4.6 AgentSection — `components/AgentSection.jsx` (80 строк), `id="agent"`, фон `tint-lavender` (#f4f0fc)

**SectionHead 02**
- Заголовок: **«Агент. Сотрудник, который звонит сам, всё помнит и доводит до результата»**
- Лид: **«Вы описываете бизнес пятью полями обычными словами и загружаете базу контактов. Дальше агент звонит, пишет, перезванивает и отчитывается вам в чате. Он не болеет, не уходит в отпуск и не забывает.»**

**Макет**: `<Parallax className="agent-mock" amount={30}><Reveal className="stack" y={28}><MockAgent/></Reveal></Parallax>`, `.agent-mock { padding: 0 28px 28px 0; margin-bottom: 56px }`.

**`.agent-grid`** (`1fr 1fr; gap 56px; align-items:start`):

*Левая часть* — `<Reveal className="agent-split" x={-16} y={0}>`, внутри `.agent-split { grid 1fr 1fr; gap 24px }`, два `.split-col` (`h3: Unbounded 15px/600, mb 10px`):

**«Вы, один раз»** (`ul.rule-list`):
- Заполняете пять полей: кто вы, кому звоните, как говорите, что предлагаете, что считать успехом
- Загружаете базу контактов файлом xlsx или csv
- Подключаете номер и, если нужно, Telegram или MAX
- Отвечаете на уведомления о горячих клиентах

**«Агент, каждый день»**:
- Звонит по базе и принимает входящие живым голосом
- Готовится к каждому звонку по карточке клиента
- После разговора записывает выводы и ставит следующий шаг
- Перезванивает недозвонам, пишет в Telegram, MAX и SMS
- Ведёт воронку: новый, в работе, успех, отказ, не звонить
- Отвечает на ваши вопросы по данным своей CRM

```css
.rule-list li { padding:10px 0; border-top:1px solid var(--vf-border); 14.5px; lh 1.5; var(--vf-text-2) }
.rule-list li:last-child { border-bottom: 1px solid var(--vf-border) }
```

*Правая часть* — `.agent-day`:
- `<Reveal y={12}>`: h3 **«Один день агента с одной клиенткой»** (Unbounded 16px/600), p.muted **«Шоурум одежды сообщает базе о новой коллекции.»**
- `<Stagger as="ol" className="day" stagger={0.1}>`, каждый `<Item as="li" x={16} y={0}>`:
```css
.day li { grid-template-columns: 64px 1fr; gap:12px; padding:12px 0;
          border-top:1px solid var(--vf-border); 14px; lh 1.5; var(--vf-text-2) }   /* 56px ≤600px */
.day-time { mono 12.5px/600; var(--vf-accent); padding-top:2px }
.day b { color: var(--vf-text) }
```

| Время | Заголовок | Текст |
|---|---|---|
| 10:05 | Недозвон. | Марина не ответила. Перезвон поставлен на завтра, в карточке пометка: утром не берёт трубку. |
| 14:30 | Перезвонила сама. | Агент узнал номер и начал с сути. Марине интересны платья 44 размера, ближе к выходным. |
| 14:33 | После разговора. | Карточка обновлена, стадия «в работе». Подборка ушла в Telegram, задача: в пятницу напомнить о примерке. |
| 15:10 | Переписка. | «А синее есть в наличии?» Агент отвечает по базе знаний и договаривается на субботу, 12:00. |
| Вечер | Отчёт. | Вы спрашиваете в чате, как прошёл обзвон. Дозвонился до 61, заинтересовались 23, записал 9. |

**Блок «Как устроен внутри»** — `<Reveal className="brains" y={16}>`:
```css
.brains { margin-top:64px; grid-template-columns: minmax(0,5fr) minmax(0,7fr); gap:48px;
          padding-top:40px; border-top:1px solid var(--vf-border) }
.brains-head h3 { Unbounded 18px/600 }   .brains-head p { 14.5px }
.brains-flow { grid-template-columns: repeat(3, minmax(0,1fr)) }
.flow-step { padding: 4px 18px 4px 16px; border-left: 1px solid var(--vf-border) }
.flow-step:first-child { border-left:none; padding-left:0 }
.flow-step b { display:block; 15px; var(--vf-text); margin: 8px 0 6px }
.flow-step p { 13px; lh 1.5; var(--vf-text-3) }
.flow-who { inline-flex; gap:6px; 11px/600; UPPERCASE; ls .05em }
.flow-who.orch  { color: var(--lp-violet) }   /* #7c3aed */
.flow-who.voice { color: var(--vf-accent) }   /* #2563eb */
```
Заголовок: **«Как устроен внутри»**
Лид: **«Две модели, у каждой своя инструкция. В живом звонке нельзя думать секундами: пауза, и клиент вешает трубку. Поэтому всё долгое мышление вынесено до и после звонка.»**

| Роль (иконка) | Этап | Текст |
|---|---|---|
| Оркестратор (`brain`, фиолетовый) | До звонка | Читает задачу, карточку и прошлые разговоры. Пишет первую фразу, тактику и факты. |
| Голосовой агент (`audio-lines`, синий) | Звонок | Говорит на выбранной модели по плану, ищет ответы в базе знаний, вызывает функции. |
| Оркестратор (`brain`, фиолетовый) | После звонка | Читает транскрипт, обновляет карточку, двигает по воронке, ставит перезвон или сообщение. |

**CTA секции** `.sec-actions`: `btn btn-primary btn-lg` **«Подключить агента»** + `arrow-right`; рядом `.muted`: **«Тариф Agent: 5 490 ₽ в месяц, до трёх агентов, 20 000 кредитов. Один звонок около 20 кредитов.»**

### 4.7 Start — «Как начать» — `components/Start.jsx` (64 строки), `id="how"`, белый фон

**SectionHead 03**
- Заголовок: **«Три шага до первого разговора»**
- Лид: **«Обычно на это уходит меньше десяти минут. Те же три шага встретят вас на дашборде после регистрации.»**

**`<Stagger as="ol" className="steps" stagger={0.12}>`**
```css
.steps { grid-template-columns: repeat(3, minmax(0,1fr)); gap:40px }   /* 1 колонка ≤900px, gap 24px */
.steps li { padding-top:16px; border-top: 2px solid var(--vf-text) }   /* жирная тёмная линия сверху */
.steps-n { block; mono 12.5px/600; var(--vf-accent); margin-bottom:12px }
.steps h3 { Unbounded 16px/600 }
.steps p  { 14.5px; lh 1.55; var(--vf-text-3) }
```
Номера генерируются как `String(i+1).padStart(2,'0')` → `01`, `02`, `03`.

| № | Заголовок | Текст |
|---|---|---|
| 01 | Создайте ассистента | Имя, модель, голос и промпт: роль, компания, как вести разговор. База знаний и функции, если нужно отвечать по фактам и записывать клиентов. |
| 02 | Позвоните ему | Тестовый номер на 10 минут, звонок с любого телефона. Пока идёт тест, меняйте модель: номер перепривяжется сам, и вы послушаете все голоса. |
| 03 | Подключите свой номер | Верификация онлайн, по закону РФ без неё нельзя держать номера. Купите номер, привяжите ассистента, он начнёт отвечать на входящие. |

**CTA** `.sec-actions`: `btn btn-primary btn-lg` **«Начать бесплатно»** + `arrow-right`; `.muted` **«Три дня со всеми функциями, карта не нужна.»**

**Блок тестового звонка `<Reveal className="call" y={20}>`**
```css
.call { margin-top:72px; grid-template-columns: 1.1fr .9fr; gap:32px; align-items:center;
        padding:32px 36px; border:1px solid var(--vf-border); border-radius:18px;
        background:var(--vf-surface); box-shadow: var(--vf-shadow-2) }
.call-kicker { block; Unbounded 19px/600; ls -0.01em; var(--vf-text); margin-bottom:8px }
.call-copy p { 14.5px; margin-bottom:14px }
.call-tags   { flex; wrap; gap:6px; .logo { 14×14 } }
.call-num    { text-align: right }
.call-phone  { block; Unbounded 28px/700; var(--vf-text); tabular-nums; nowrap; margin-bottom:14px }
.call-phone:hover { color: var(--vf-accent) }
.call-actions { flex; justify-content: flex-end; gap:8px; wrap }
```
Тексты:
- Кикер: **«Или позвоните нашему агенту прямо сейчас»**
- Абзац: **«Он ответит мгновенно, расскажет о платформе и запишет ваш вопрос. Бесплатно, круглосуточно.»**
- Три чипа: логотип Каскада + **«Работает на Каскаде»** · `mic` **«Живой голос»** · `clock` **«24/7»**
- Номер: **`+7 931 10-710-31`** (ссылка `tel:+79311071031`)
- Кнопки: `btn btn-primary` `phone` **«Позвонить»**; `btn` `copy` **«Скопировать»** → **«Скопировано»** (2 с)

### 4.8 Integration — «Виджет» — `components/Integration.jsx` (99 строк), `id="integration"`, фон `tint-blue`

**SectionHead 04**
- Заголовок: **«Виджет на сайт одной строкой»**
- Лид: **«Вставьте код перед закрывающим тегом body. Голосовой виджет появится в углу и будет разговаривать с посетителями. Такой же работает на этой странице, справа внизу.»**

**Раскладка** `.integ { grid-template-columns: minmax(0,7fr) minmax(0,5fr); gap:48px; align-items:start }`

*Левая колонка* — блок кода в `<Parallax amount={24}>`:
```css
.code { border-radius:14px; overflow:hidden; background:#0f172a; border:1px solid #1e293b;
        box-shadow: var(--vf-shadow-2) }
.code-head { flex; space-between; padding: 8px 10px 8px 16px; border-bottom:1px solid #1e293b }
.code-file { inline-flex; gap:7px; mono 12.5px; color:#94a3b8 }
.code .btn-ghost { color:#cbd5e1 }
.code .btn-ghost:hover { background:#1e293b; color:#fff }
.code pre { margin:0; padding:18px 20px; mono 12.5px; lh 1.6; color:#e2e8f0;
            white-space: pre; overflow-x: auto }
```
Шапка: иконка `code` + имя файла **`index.html`**; справа `btn btn-sm btn-ghost` **«Скопировать»** → **«Скопировано»**.

Содержимое `<pre>` (точный текст):
```html
<!-- Voicyfy Voice Assistant -->
<script>
  (function () {
    var script = document.createElement('script');
    script.src = 'https://voicyfy.ru/static/gemini-widget.js';
    script.dataset.assistantId = 'ВАШ_ASSISTANT_ID';
    script.dataset.server = 'https://voicyfy.ru';
    script.dataset.position = 'bottom-right';
    script.async = true;
    document.head.appendChild(script);
  })();
</script>
<!-- End Voicyfy Widget -->
```

*Правая колонка* — `<Reveal className="integ-text" x={16} y={0} delay={0.1}>`, `dl.facts` (`.integ-text .facts { margin-top:0; margin-bottom:20px }`):

| dt | dd |
|---|---|
| Модели для виджета | OpenAI и Gemini, с прерываниями и вызовом функций |
| Где взять код | вкладка «Встраивание» у ассистента |
| Исходящие по событию | вызов API из вашей системы, документация в кабинете |
| Интеграции | Google Sheets, вебхуки, Telegram, свои функции |

Ссылка `.lp-link` → `/static/api-docs.html`: **«Документация API»** + `arrow-right`.

**Живой демо-виджет (важное поведение)**
- Через 1500 мс после монтирования в `<head>` вставляется `<script src="https://voicyfy.ru/static/gemini-widget.js">` с `data-assistant-id="991b2b45-b52b-43be-9e59-81eaf7ea980a"`, `data-server="https://voicyfy.ru"`, `data-position="bottom-right"`, `data-lp-widget="1"`, `async`. Повторная вставка защищена проверкой `script[data-lp-widget]`.
- Виджет **виден только пока секция на экране** (`IntersectionObserver`, `threshold: 0.12`) — либо если он открыт (контейнер `#wellcomeai-widget-container` имеет класс `active`). Проверка каждые 800 мс (`setInterval`).
- Переключение через класс на `<html>`:
```css
.lp-widget-hidden #wellcomeai-widget-container { opacity:0 !important; pointer-events:none !important;
                                                 transform: translateY(16px) }
#wellcomeai-widget-container { transition: opacity .3s ease, transform .3s ease }
```
Для клона: `assistant-id` и домен — хардкод, их нужно заменить.

### 4.9 Scenarios — «Где это работает» — `components/Scenarios.jsx` (34 строки), `id="cases"`, фон `tint-sand` (#fbf6ee)

**SectionHead 05**
- Заголовок: **«Где это работает»**
- Лид: **«Четыре задачи, с которых обычно начинают. Каждая настраивается словами в промпте, без программирования.»**

**`<Stagger as="ul" className="cases" stagger={0.1}>`**
```css
.cases li { grid-template-columns: 180px 1fr auto; gap:24px; align-items:start; padding:24px 0;
            border-top: 1px solid var(--vf-border) }
.cases li:last-child { border-bottom: 1px solid var(--vf-border) }
.cases-who { Unbounded 14px/600; var(--vf-text); padding-top:3px }
.cases h3  { 16px; margin-bottom:6px }
.cases p   { 14.5px; lh 1.55; var(--vf-text-2); max-width:640px }
```
Третья колонка — чип: `chip chip-accent` если инструмент начинается со слова «Агент», иначе обычный `chip`.

| Кто | Задача (h3) | Текст | Чип |
|---|---|---|---|
| Салон красоты | Входящие без администратора | Отвечает в 21:40, когда администратор ушёл. Называет свободное время по базе знаний, записывает, подтверждает SMS, накануне напоминает. Постоянных клиентов узнаёт. | Ассистент |
| Шоурум | Уведомить базу о поступлении | Обзванивает 400 покупательниц о новой коллекции. Заинтересованным отправляет подборку в Telegram, ведёт переписку и записывает на примерку. | **Агент** (accent) |
| Автосервис | Подтверждение и перенос записи | Накануне звонит с напоминанием, переносит по просьбе клиента, отправляет SMS с новым временем и обновляет календарь. | **Агент** (accent) |
| Клиника | Регистратура и реактивация | Принимает записи и переносы круглосуточно. Пациентам, которые не были больше полугода, напоминает о профилактическом осмотре. | Ассистент и агент |

### 4.10 Pricing — «Тарифы» — `components/Pricing.jsx` (118 строк), `id="pricing"`, фон `tint-blue`

**Важно: это НЕ карточки планов, а сравнительная таблица** (5 колонок-тарифов × 12 строк-функций) в карточке `.plans-card`.

**SectionHead 06**
- Заголовок: **«Тарифы»**
- Лид: **«Подписка открывает функции и лимит ассистентов. Минуты голоса, связь и кредиты агента считаются отдельно на любом тарифе, ниже видно как.»**

**Тарифы (5 колонок), валюта — рубль (₽)**

| key | name | Бейдж-чип | Описание | Цена | Период (`<small>`) | Кнопка | Подсветка колонки |
|---|---|---|---|---|---|---|---|
| trial | **Trial** | `chip-success` «с него начинают» | Все функции, карта не нужна | **0 ₽** | `/ 3 дня` | **Начать бесплатно** (`btn-primary`) | — |
| voice | **AI Voice** | `chip-ghost` (пустой, невидимый) | Голосовой консультант на сайте | **1 490 ₽** | `/ мес` | Выбрать | — |
| start | **Start** | `chip-accent` «популярный» | Ассистенты на телефоне и на сайте | **2 990 ₽** | `/ мес` | **Выбрать** (`btn-primary`) | `.hot` — синий |
| profi | **Profi** | `chip-ghost` | Несколько направлений и номеров | **5 990 ₽** | `/ мес` | Выбрать | — |
| agent | **Agent** | `chip-violet` «автономный» | Сотрудник, который звонит сам | **5 490 ₽** | `/ мес` | Выбрать | `.agent` — фиолетовый |

Все кнопки открывают модалку регистрации (`onOpenModal('register')`).

**Строки сравнения (12)**

| Функция | Trial | AI Voice | Start | Profi | Agent |
|---|---|---|---|---|---|
| Голосовые ассистенты | 1 | до 3 | до 5 | до 10 | через агента |
| Автономные агенты | — | — | — | — | до 3 |
| Телефония и номера | ✓ | — | ✓ | ✓ | ✓ |
| Виджет на сайт | ✓ | ✓ | ✓ | ✓ | ✓ |
| Тестовый номер на 10 минут | ✓ | ✓ | ✓ | ✓ | ✓ |
| CRM и воронка | ✓ | — | ✓ | ✓ | ✓ |
| База знаний | ✓ | ✓ | ✓ | ✓ | ✓ |
| Функции, вебхуки, Google Sheets | ✓ | ✓ | ✓ | ✓ | ✓ |
| Telegram, MAX и SMS от агента | — | — | — | — | ✓ |
| Кредиты агента | — | — | — | — | 20 000 в месяц |
| Подарочные рубли на кошельке | **{welcome} ₽** (65 по умолчанию, из API) | — | — | — | — |
| Поддержка | чат | чат | приоритетная | VIP | приоритетная |

Рендер ячеек (`<Cell>`): `true` → `<span class="cell-yes"><Icon check ic-sm/></span>`; `false` → `<span class="cell-no">—</span>`; `'welcome'` → `{welcome} ₽`; иначе текст.

**Стили таблицы**
```css
.plans-card { border:1px solid var(--vf-border); border-radius:18px; background:var(--vf-surface);
              box-shadow: var(--vf-shadow-2); padding: 4px 24px 8px }   /* 4px 12px 12px ≤600px */
.plans-wrap { overflow-x: auto }                 /* + класс .table-wrap из ДС */
.plans { width:100%; border-collapse:separate; border-spacing:0; 14px; min-width: 900px }
.plans th, .plans td { padding:0 12px; height:40px; border-bottom:1px solid var(--vf-border);
                       text-align:center; vertical-align:middle; color var(--vf-text-2); 13.5px }
.plans th { height:auto; vertical-align:top; padding: 14px 10px 12px }
.plans th.plans-feature { vertical-align: bottom }
.plans tbody tr:last-child td { border-bottom: none }
.plan-head { flex column; align-items:center; gap:4px }
.plan-head .chip { margin-bottom:4px }
.chip-ghost { background: transparent; color: transparent }      /* распорка, держит высоту */
.plan-head b { Unbounded 17px/600; var(--vf-text) }
.plan-desc  { 11.5px; lh 1.35; var(--vf-text-3); min-height:31px; max-width:150px }
.plans-price { block; Unbounded 19px/700; var(--vf-text); margin-top:2px; ls -0.01em; nowrap }
.plans-price small { Inter 11px/500; var(--vf-text-4); margin-left:4px }
.plan-head .btn { margin-top:8px; width:100% }
.plans .plans-feature { text-align:left; color:var(--vf-text); 500; padding-left:0; nowrap }
/* подсветка колонок */
.plans td.hot,  .plans th.hot   { background: var(--vf-accent-soft) }   /* #eaf1ff */
.plans td.agent,.plans th.agent { background: var(--lp-violet-soft) }   /* #f1ebff */
.plans th.hot, .plans th.agent { border-radius: 12px 12px 0 0 }
.plans tbody tr:last-child td.hot/.agent { border-radius: 0 0 12px 12px }
.plans th.hot   { box-shadow: inset 0 2px 0 var(--vf-accent) }          /* верхняя полоса */
.plans th.agent { box-shadow: inset 0 2px 0 var(--lp-violet) }
.cell-yes { inline-flex; center; 22×22; radius 50%; background: var(--vf-success-soft);
            color: var(--vf-success) }
.cell-no  { color: var(--vf-text-4) }
```
Заголовок первой колонки пустой (`<th class="plans-feature"/>`); класс `.plans-what` (Unbounded 10.5px UPPERCASE) объявлен, но в разметке не встречается.

**Блок `.extra` — три колонки под таблицей**
```css
.extra { grid-template-columns: repeat(3, minmax(0,1fr)); gap:40px; margin-top:56px;
         padding-top:40px; border-top: 1px solid var(--vf-border) }   /* 1 колонка ≤1100px */
.extra-col h3 { Unbounded 15px/600 }
.extra-col p  { 13.5px; margin-bottom:12px }
.rate-list li { flex; gap:10px; padding:9px 0; border-top:1px solid var(--vf-border); 13.5px }
.rate-list li:last-child { border-bottom: 1px solid var(--vf-border) }
.rate-list li span { flex:1; var(--vf-text-2) }
.rate-list li b { var(--vf-text); 600; tabular-nums }
.rate-list .logo { 16×16 }   .rate-list .ic { color: var(--vf-text-4) }
```
Задержки появления: 0, 0.08, 0.16 с.

**1) «Минуты голоса»** — «Списываются с кошелька посекундно по тарифу модели, минимум 10 секунд за разговор. Со своим ключом провайдера бесплатно.»
Список — динамический из `useTariffs()`: логотип 16px + название + цена (`b`), фолбэк `—`.

**2) «Связь»** — «Отдельный баланс телефонии: аренда номеров, минуты оператора, SMS.»
- `phone-incoming` Входящий звонок — **1,7 ₽/мин**
- `phone-outgoing` Исходящий звонок — **2,7 ₽/мин**
- `message-square` SMS — **по тарифу оператора**
- `phone` Аренда номера — **от 190 ₽/мес**

**3) «Кредиты агента»** — «Оплачивают мышление агента: подготовку к звонку, разбор результата, ответы в чате и мессенджерах.»
- `brain` Один звонок — **около 20 кредитов**
- `refresh-cw` В тарифе Agent — **20 000 в месяц**
- `plus` Пакеты — **докупаются в кабинете**

### 4.11 Faq — `components/Faq.jsx` (29 строк), `id="faq"`, белый фон

**Это НЕ аккордеон.** Никакого раскрытия/сворачивания нет — все ответы всегда видны. Семантика — `<dl>` в две колонки.

**SectionHead 07**: заголовок **«Вопросы перед стартом»**, лид отсутствует.

```css
.faq { grid-template-columns: 1fr 1fr; gap: 0 48px; margin: 0 }   /* 1 колонка ≤900px */
.faq > div { padding: 20px 0; border-top: 1px solid var(--vf-border) }
.faq dt { 16px/700; var(--vf-text); margin-bottom: 8px; ls -0.01em }
.faq dd { margin:0; 14.5px; lh 1.6; var(--vf-text-2) }
```
Обёртка `<Stagger as="dl" className="faq" stagger={0.06}>`, каждая пара — `<Item as="div" y={14}>`.

Массив `QA` экспортируется и переиспользуется в `entry-server.jsx` для `FAQPage` JSON-LD.

**Полные вопросы и ответы (6)**

1. **«Сколько стоит минута разговора?»**
   «Зависит от модели: цены за минуту показаны в разделе тарифов и в конструкторе ассистентов. Списание посекундное, минимум 10 секунд за разговор. Связь оплачивается отдельно с баланса телефонии.»

2. **«Нужна ли верификация?»**
   «Для покупки номеров да: по закону РФ оператор обязан знать владельца номера. Верификация проходит онлайн для физлица, ИП или юрлица и занимает несколько минут. Виджет и тестовый номер работают без неё.»

3. **«Можно ли использовать свой ключ OpenAI или Gemini?»**
   «Да. Укажите ключ провайдера в настройках, и разговоры на этой модели не будут списываться с кошелька. Без своего ключа работают серверные ключи платформы.»

4. **«Что входит в пробный период?»**
   «Три дня со всеми функциями: один ассистент, виджет, CRM, телефония, база знаний и тестовый номер на 10 минут, чтобы позвонить ассистенту с телефона. На кошелёк сразу начисляются подарочные рубли на первые разговоры. Карта не нужна.»

5. **«Как агент помнит клиентов?»**
   «У каждого контакта есть карточка: сводка, ключевые факты, лучшее время для звонка и вся история общения. Агент обновляет её после каждого разговора и сообщения и читает перед следующим звонком.»

6. **«Где хранятся данные?»**
   «Записи, расшифровки и карточки клиентов хранятся в вашем кабинете и доступны только вам. Ключи провайдеров хранятся в зашифрованном виде и не передаются третьим лицам.»

### 4.12 FinalCta — `components/FinalCta.jsx` (24 строки), без `id`

```css
.final { padding: 72px 0 }
.final-inner { grid-template-columns: 1.3fr 1fr; gap:40px; align-items:center }   /* 1 колонка ≤900px */
.final-actions { flex; gap:12px; wrap; justify-content: flex-end }                /* влево ≤900px */
```
- h2: **«Соберите первого ассистента за десять минут»**
- p.lp-lead: **«Три дня бесплатно и без карты. Позвоните ассистенту на тестовый номер и решите, подходит ли он вам.»**
- Кнопки: `btn btn-primary btn-lg` **«Создать ассистента»** + `arrow-right` (модалка регистрации); `a.btn.btn-lg` → `https://t.me/voicyfy_support` (`target=_blank rel=noopener`), иконка `send` + **«Задать вопрос»**

### 4.13 Footer — `components/Footer.jsx` (40 строк)

```css
.lp-footer { border-top:1px solid var(--vf-border); background: var(--vf-surface-2);
             padding: 48px 0 28px }
.lp-footer-inner { grid-template-columns: 1.6fr 1fr 1fr 1fr; gap:32px }  /* 2 кол ≤900, 1 кол ≤600 */
.lp-footer-brand p { margin-top:12px; max-width:360px; 13.5px; var(--vf-text-3) }
.lp-footer-col   { flex column; gap:8px }
.lp-footer-col a { 14px; var(--vf-text-2) }
.lp-footer-col a:hover { color: var(--vf-accent) }
.lp-footer-bottom { flex; space-between; gap:12px; wrap; margin-top:36px; padding-top:18px;
                    border-top:1px solid var(--vf-border); 12.5px; var(--vf-text-4) }
```
Заголовки колонок — `<h4>` (12px/600 UPPERCASE ls .06em, `--vf-text-4`).

**Колонка 1 — бренд**: логотип (`a.vf-logo.lp-logo` → `#top`, картинка `/static/images/IMG_2820.PNG` + `<span class="wordmark">Voicyfy</span>`), под ним текст:
«Платформа голосовых ИИ-ассистентов и агента для бизнеса. OpenAI, Gemini, Яндекс, Fish Audio и Каскад в одном кабинете.»

**Колонка 2 — «Продукт»**
| Возможности | `#platform` |
| Агент | `#agent` |
| Тарифы | `#pricing` |
| API | `/static/api-docs.html` |
| База знаний | `/static/prompts-wiki.html` |

**Колонка 3 — «Документы»**
| Конфиденциальность | `/static/privacy-policy.html` |
| Соглашение | `/static/terms-of-service.html` |
| Оферта | `/static/public-offer.html` |

**Колонка 4 — «Контакты»**
| Telegram | `https://t.me/voicyfy` (`_blank`, `noopener`) |
| Поддержка | `https://t.me/voicyfy_support` (`_blank`, `noopener`) |
| info@voicyfy.ru | `mailto:info@voicyfy.ru` |

**Нижняя строка (два `<span>`)**
- **«© 2025–2026 Voicyfy. Все права защищены.»**
- **«ИП Шишкин Валерий Сергеевич · ИНН 385101159652»**

Примечание: в футере нет ссылки на `/static/payment-terms.html`, хотя файл существует.

---

## 5. AuthModal — модалка входа и регистрации

Файлы: `components/AuthModal.jsx` (166), `AuthSection/LoginForm.jsx` (78), `AuthSection/RegisterForm.jsx` (135), `AuthSection/PasswordField.jsx` (38), `AuthSection/EmailVerificationSection.jsx` (84), `components/InlineNotification.jsx` (22), `hooks/useEmailVerification.js`, `hooks/useReferralTracker.js`, `utils/api.js`, `utils/rememberedName.js`.

### 5.1 Каркас
```html
<div class="lp-auth-backdrop" onClick=закрыть>
  <div class="lp-auth lp-auth-{login|register}" role="dialog" aria-modal="true"
       aria-label="Вход|Регистрация" onClick=stopPropagation>
    <aside class="lp-auth-brand"> … левая брендовая панель … </aside>
    <section class="lp-auth-pane">
      <button class="btn btn-icon lp-auth-close" aria-label="Закрыть"><Icon x/></button>
      <div class="lp-auth-switch" role="tablist"> 2 × button.lp-auth-switch-btn </div>
      <div class="lp-auth-body" key={activeTab}> LoginForm | RegisterForm </div>
    </section>
  </div>
</div>
```
Поведение: закрытие по `Escape` (`window keydown`), по клику на подложку, по кнопке × ; `document.body.style.overflow = 'hidden'` пока открыта; `lenis.stop()` из `App`. Рендерится только при `isOpen` (иначе `return null`) — **без портала**, прямо в дереве `.lp`.

**Двухпанельный макет** — левая панель задаёт высоту, поэтому вход и регистрация одного размера и модалка не «прыгает» при переключении:
```css
.lp-auth-backdrop { fixed; inset:0; z-index:1100; background: rgba(15,23,42,.55);
                    backdrop-filter: blur(8px); flex center; padding:16px; overflow-y:auto;
                    animation: fade-in .2s ease both }
.lp-auth { position:relative; width:100%; max-width:900px; min-height:620px; margin:auto;
           grid-template-columns: 340px minmax(0,1fr); background: var(--vf-surface);
           border-radius: 22px; overflow:hidden;
           box-shadow: 0 30px 90px rgba(15,23,42,.35), 0 0 0 1px rgba(255,255,255,.08);
           animation: lp-auth-in .28s cubic-bezier(.2,.7,.2,1) both }
@keyframes lp-auth-in   { from { opacity:0; transform: translateY(18px) scale(.98) } to { … } }
@keyframes lp-auth-pane { from { opacity:0; transform: translateY(8px) } to { … } }
```
`.lp-auth-body` и `.lp-auth-brand-switch` анимируются `lp-auth-pane .25s ease both`; `key={activeTab}` заставляет их переигрывать анимацию при переключении вкладки.

### 5.2 Левая панель `.lp-auth-brand`
```css
.lp-auth-brand { flex column; gap:28px; padding: 30px 30px 26px; color:#fff;
                 background: linear-gradient(160deg, #0f172a 0%, #1e3a8a 60%, #2563eb 130%);
                 overflow:hidden }
.lp-auth-brand::before { радиальный точечный паттерн: radial-gradient(rgba(255,255,255,.14) 1px, transparent 1px);
                         background-size: 22px 22px; opacity:.5 }
.lp-auth-brand::after  { 420×420; right:-160px; bottom:-200px; radius 50%;
                         background: radial-gradient(circle, rgba(96,165,250,.55), transparent 65%);
                         filter: blur(8px) }
```
Содержимое:
1. `.lp-auth-brand-top`: логотип (`.vf-logo.lp-auth-logo`, белый, 18px) + бейдж `.lp-auth-badge` с зелёной точкой:
   - регистрация → **«3 дня бесплатно · без карты»**
   - вход → **«Все сервисы работают»**
   ```css
   .lp-auth-badge { height:26px; padding:0 11px; radius 999px; background: rgba(255,255,255,.1);
                    border:1px solid rgba(255,255,255,.18); 12px/500; color:#dbeafe }
   .lp-auth-dot   { 7×7; radius 50%; background:#22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,.22) }
   ```
2. `.lp-auth-brand-greet` — только ≤760px и только при входе с запомненным именем: **«С возвращением, <span>Имя</span>»** (Unbounded 15px/600, `span` — `#93c5fd`).
3. `.lp-auth-brand-switch` — переключаемый блок:

   **Регистрация (`BrandRegister`)**
   - Заголовок `.lp-auth-brand-title` (Unbounded 22px/700, lh 1.2, `#fff`; `<span>` → `#93c5fd`): **«Первый звонок <span>за 10 минут</span>»**
   - Нумерованный список `.lp-auth-steps` (номера в кружках `.lp-auth-step-n` 22×22, mono 11px/600, цвет `#bfdbfe`):
     1. **Зарегистрируйтесь**
     2. **Введите код, присланный на почту**
     3. **Создайте ассистента**
     4. **Подключите тестовый номер на 10 минут**
   - Плашка `.lp-auth-ready` (иконка `phone-call`, зелёная): **«Готово! Можно звонить»**
     ```css
     .lp-auth-ready { height:36px; padding:0 14px; radius 10px; background: rgba(34,197,94,.14);
                      border:1px solid rgba(34,197,94,.35); 13.5px/600; color:#bbf7d0;
                      .ic { color:#4ade80 } }
     ```

   **Вход (`BrandLogin`)**
   - Заголовок: **«С возвращением»** + `, <span>{Имя}</span>` если имя запомнено
   - Текст `.lp-auth-brand-text` (13.5px, `rgba(255,255,255,.72)`): **«Войдите в кабинет — всё, что вы настроили, на месте.»**
   - Список `.lp-auth-steps.lp-auth-points` — иконка вместо цифры (12×12 внутри кружка):
     - `bot` **«Ассистенты и их настройки»**
     - `phone-call` **«Звонки, номера и история диалогов»**
     - `wallet` **«Кошелёк и тарифы моделей»**
4. `.lp-auth-models` (`margin-top:auto; padding-top:20px; border-top:1px solid rgba(255,255,255,.12)`):
   - Подпись `.lp-auth-models-label`: **«Работает на голосовых моделях»** (Unbounded 10.5px/500 UPPERCASE ls .06em, `rgba(255,255,255,.5)`)
   - Ряд из 5 плашек `.lp-auth-model` (высота 28px, radius 8px, `rgba(255,255,255,.08)`, border `rgba(255,255,255,.14)`, 12px/500, `rgba(255,255,255,.9)`), логотип 16px: **OpenAI, Gemini, Яндекс, Fish Audio, Каскад**

### 5.3 Правая панель — табы
```css
.lp-auth-pane   { flex column; padding: 30px 40px 28px }
.lp-auth-close  { absolute; top:16px; right:16px; z-index:2 }
.lp-auth-switch { inline-flex; align-self:flex-start; gap:2px; padding:4px; radius 999px;
                  background: var(--vf-surface-3); margin-bottom: 22px }
.lp-auth-switch-btn { height:32px; padding:0 16px; border:0; radius 999px; background:transparent;
                      13.5px/600; color var(--vf-text-3) }
.lp-auth-switch-btn.active { background: var(--vf-surface); color: var(--vf-text);
                             box-shadow: 0 1px 3px rgba(15,23,42,.12), 0 0 0 1px rgba(15,23,42,.04) }
```
Две вкладки: **«Вход»** и **«Регистрация»** (`role="tablist"`/`role="tab"`/`aria-selected`).

### 5.4 Форма входа (`LoginForm`)
- Заголовок `.lp-auth-title` (Unbounded 22px, lh 1.2, ls −0.01em): **«Вход в кабинет»**
- Подзаголовок `.lp-auth-sub` (14px, `--vf-text-3`): **«Войдите, чтобы продолжить работу с ассистентами»**
- Поля:
  - `Email` — `input[type=email] required autoComplete=email`, placeholder **`you@company.com`**, иконка `mail` слева
  - `Пароль` — `PasswordField`, placeholder **`••••••••`**, `autoComplete=current-password`, иконка `lock` слева, кнопка-глаз справа
- Кнопка: `btn btn-primary btn-lg lp-form-submit` — **«Войти»** + `arrow-right`; в загрузке — `<span class="spin"/> Входим...`, `disabled`
- Подсказка `.lp-form-hint`: **«Нет аккаунта? Создать бесплатно»** (ссылка переключает вкладку)
- Логика: `api.login({email,password})` → `localStorage.setItem('auth_token', data.token)` → уведомление **«Успешный вход! Переходим...»** → через 500 мс `window.location.href = '/static/dashboard.html'`

**Состояния ошибок (точные тексты)**
| Условие (по `error.message`) | Тип | Сообщение |
|---|---|---|
| — (старт) | `loading` | «Выполняется вход...» |
| успех | `success` | «Успешный вход! Переходим...» |
| содержит `not verified` или `не подтвержден` | `warning` | «Email не подтверждён! Проверьте почту для кода верификации.» |
| содержит `Invalid` или `password` | `error` | «Неверный email или пароль» |
| иначе | `error` | `error.message` или «Ошибка входа» |

### 5.5 Форма регистрации (`RegisterForm`)
- Заголовок: **«Создайте аккаунт»**; подзаголовок: **«3 дня полного доступа ко всем функциям»**
- Поля (первые два в ряд `.lp-auth-row { grid 1fr 1fr; gap 12px }`, ≤760px — в колонку):
  1. `Имя` — `text`, placeholder **«Как к вам обращаться»**, `autoComplete=given-name`, **не обязательное**
  2. `Компания` + `<span class="muted">необязательно</span>` — placeholder **«Название компании»**, `autoComplete=organization`
  3. `Email` — `email required`, placeholder `you@company.com`, иконка `mail`
  4. `Пароль` + `<span class="muted">минимум 8 символов</span>` — `PasswordField`, `minLength=8`, `autoComplete=new-password`
- Кнопка: **«Создать аккаунт»** + `arrow-right`; в загрузке — «Регистрируем...»
- Юридический текст `.lp-form-legal` (12px, lh 1.5, `--vf-text-4`, подчёркнутые ссылки):
  **«Нажимая кнопку, вы принимаете [соглашение](/static/terms-of-service.html) и [политику конфиденциальности](/static/privacy-policy.html).»** (обе `_blank`, `noopener`)
- Подсказка внизу: **«Уже есть аккаунт? Войти»**
- **Социального логина НЕТ** — ни Google, ни VK, ни Яндекс ID, ни Telegram. Только email + пароль.
- Payload в `api.register`: `{ email, password, first_name (или null), last_name: null, company_name (или null), referral_code, utm_data }`. Реферальные данные — из `useReferralTracker` (UTM пишутся в `localStorage` только при `utm_source=partner` и непустом `utm_campaign`).
- Имя сохраняется локально: `rememberFirstName()` → `localStorage['voicyfy_first_name']` (обрезка до 40 символов), используется в приветствии при следующем входе.

**Состояния ошибок**
| Условие | Тип | Сообщение |
|---|---|---|
| старт | `loading` | «Отправляем код подтверждения на email...» |
| успех | `success` | «Код отправлен! Проверьте email.» |
| ответ содержит `exists but not verified` | — | экран верификации с сообщением «Аккаунт уже существует. Новый код подтверждения отправлен на email.» |
| `error.message` содержит `already registered` | `error` | «Email уже зарегистрирован и подтверждён. Войдите в аккаунт.» + через 2000 мс автопереключение на вкладку входа |
| иначе | `error` | `error.message` или «Ошибка регистрации» |

Если `data.verification_required && data.verification_sent` → показывается `EmailVerificationSection` и чистятся реферальные данные. Если сразу пришёл `data.token` → сохранение и редирект на дашборд.

### 5.6 `PasswordField`
```html
<div class="input-wrap lp-auth-input">
  <Icon lock ic-sm/>
  <input type="password|text" class="input lp-auth-input-action" required …>
  <button class="lp-auth-eye" tabIndex={-1} aria-label="Показать пароль|Скрыть пароль">
    <Icon eye|eye-off ic-sm/></button>
</div>
```
```css
.lp-auth-input > .ic { left:13px; 16×16; transition: color var(--vf-fast) }
.lp-auth-input:focus-within > .ic { color: var(--vf-accent) }
.lp-auth-input > .input { padding-left: 38px }
.lp-auth-input > .lp-auth-input-action { padding-right: 42px }
.lp-auth-eye { absolute; right:5px; top:50%; 34×34; border:0; radius 8px; background:transparent;
               color var(--vf-text-4) }
.lp-auth-eye:hover { background: var(--vf-surface-3); color: var(--vf-text-2) }
```

### 5.7 Экран подтверждения почты (`EmailVerificationSection`)
- Иконка `.lp-verify-icon` (52×52, radius 14px, `--vf-accent-soft`/`--vf-accent`; при `message` — модификатор `.warning` с `--vf-warning-soft`/`--vf-warning`), иконка `mail` или `info`
- Заголовок: **«Подтвердите почту»**
- Подзаголовок: **«Мы отправили 6-значный код на <b>{email}</b>»** (или переданный `message`)
- Поле «Код из письма» — `.input.lp-code-input`:
  ```css
  .lp-form .lp-code-input { text-align:center; mono; 24px/500; letter-spacing:.45em;
                            padding-left: calc(14px + .45em); height:58px }
  .lp-code-input::placeholder { color: var(--vf-border-strong) }
  ```
  placeholder **`000000`**, `maxLength=6`, `pattern=[0-9]{6}`, `inputMode=numeric`, `autoComplete=one-time-code`, `autoFocus`; на вводе всё нецифровое вырезается; `Enter` → подтверждение
- `.lp-verify-info`: чип **«Осталось попыток: {N}»** (цвет чипа: 3 → обычный, 2 → `chip-warning`, 1 → `chip-danger`) + при активном таймере «Повторная отправка через **{сек}** с»
- Кнопки: `btn-primary btn-lg` **«Подтвердить»** + `arrow-right` (в загрузке «Проверяем...»); после истечения таймера — вторая кнопка `btn btn-lg` с `refresh-cw` **«Отправить код повторно»** (в загрузке «Отправка...»). `.lp-form-submit + .lp-form-submit { margin-top: 10px }`
- Логика (`useEmailVerification`): 3 попытки, таймер 60 с, запускается при монтировании; `POST /api/email-verification/verify` → `localStorage['auth_token'] = response.data.token` → «Email подтвержден! Переходим в dashboard...» → 500 мс → `/static/dashboard.html`; `POST /api/email-verification/resend` сбрасывает попытки на 3 и перезапускает таймер.

Сообщения верификации:
- «Введите 6-значный код» (невалидная длина/формат)
- «Неверный код. Осталось попыток: {N}»
- «Исчерпаны попытки ввода кода. Запросите новый код.» (поле блокируется)
- «Новый код отправлен на email!»
- «Подождите перед повторной отправкой» (warning, если ответ содержит `wait`/`подождите`)
- «Ошибка отправки кода. Попробуйте позже.»

### 5.8 Кнопка submit и уведомления
```css
.lp .lp-form-submit { width:100%; height:46px; margin-top:4px; border-radius:11px; gap:8px }
.lp .lp-form-submit.btn-primary { background: linear-gradient(135deg, #2563eb, #3b82f6);
    box-shadow: 0 8px 24px rgba(37,99,235,.28), inset 0 1px 0 rgba(255,255,255,.2) }
.lp .lp-form-submit.btn-primary:hover { transform: translateY(-1px);
    box-shadow: 0 12px 30px rgba(37,99,235,.36), inset 0 1px 0 rgba(255,255,255,.2) }
.lp .lp-form-submit:hover .ic { transform: translateX(3px) }
.lp-form-hint  { margin-top:auto; padding-top:20px; text-align:center; 13.5px; var(--vf-text-3) }
.lp-form-hint a { font-weight: 600 }
.lp-form .field  { margin-bottom: 14px; min-width: 0 }
.lp-form .label  { flex; space-between; align-items: baseline; gap: 8px }
.lp-form .label .muted { font-weight:400; 11.5px; var(--vf-text-4); nowrap }
.lp-form .input  { height: 44px; border-radius: 10px; font-size: 14px }
```

**`InlineNotification`** — единый компонент под форму:
```jsx
<div className={`note lp-inote lp-inote-${type}`} role="status">
  {type === 'loading' ? <span className="spin"/> : <Icon name={ICONS[type]} className="ic-sm"/>}
  <span>{message}</span>
</div>
```
Карта иконок: `success → circle-check`, `error → circle-alert`, `warning → triangle-alert`, `info → info`; `loading` — спиннер.
```css
.lp-inote { margin-bottom: 14px }
.lp-inote-success { background: var(--vf-success-soft); border-color: transparent; color: #14532d;
                    .ic { color: var(--vf-success) } }
.lp-inote-error   { background: var(--vf-danger-soft);  border-color: transparent; color: #7f1d1d;
                    .ic { color: var(--vf-danger) } }
.lp-inote-warning { background: var(--vf-warning-soft); border-color: transparent; color: #7c4a03;
                    .ic { color: var(--vf-warning) } }
.lp-inote .spin { border-color: var(--vf-border-strong); border-top-color: var(--vf-accent) }
```
`utils/notifications.js` содержит единственную функцию `createNotification(type,message)` → `{type,message,id:Date.now()}` и **нигде не импортируется** (мёртвый код).

---

## 6. Брендовые ассеты (хардкод)

| Что | Путь / значение | Комментарий |
|---|---|---|
| Логотип (знак) | `/static/images/IMG_2820.PNG` → `backend/static/images/IMG_2820.PNG` (12 КБ) | Встречается **4 раза**: Navbar, Footer, AuthModal, `Frame` в Mockups. Непереименованный файл с iPhone — при клоне заменить. Рендерится 30×30 (в макете 20×20), `border-radius: 8px` (в макете 6px) |
| Логотип (текст) | `<span class="wordmark">Voicyfy</span>` | Градиентный текст `linear-gradient(135deg, #2563eb, #3b82f6)` через `background-clip:text` |
| В AuthModal/Frame | `<span>Voicyfy</span>` **без класса `wordmark`** | Там текст белый (модалка) / `--vf-accent` (макет) |
| Favicon | `/static/favicon.ico`, `favicon-16x16.png`, `favicon-32x32.png` (есть также 48/64/96/192/512) | |
| Apple touch icon | `/static/apple-touch-icon.png` — **объявлен в `<head>`, но файла НЕТ** | битая ссылка |
| Manifest | `/static/manifest.json` — name «Voicyfy», `background_color: #f8fafc`, `theme_color: #2563eb`, иконки `android-chrome-192x192.png` / `512x512.png` | Есть и `site.webmanifest` (не подключён) |
| OG-image | `https://voicyfy.ru/static/images/og-image.png` (293 КБ), 1200×630 | alt: «Voicyfy: голосовой ИИ для бизнеса за десять минут, без программирования» |
| Логотип для schema.org | `https://voicyfy.ru/static/android-chrome-512x512.png` | |
| `theme-color` | `#2563eb` | |
| Спрайт иконок | `/static/icons/ui.svg` (125 символов, Lucide) | |
| Логотипы моделей | `/static/icons/models/*.svg` | см. §1.8 |
| Виджет | `https://voicyfy.ru/static/gemini-widget.js`, assistant-id `991b2b45-b52b-43be-9e59-81eaf7ea980a` | абсолютный домен в коде |
| Телефон | `+79311071031` / отображение `+7 931 10-710-31` | `Hero.jsx`, экспортируется |
| Тестовый номер в макете | `+7 933 091-64-41` | только картинка |
| Почта | `info@voicyfy.ru` | |
| Telegram | `https://t.me/voicyfy` (канал), `https://t.me/voicyfy_support` (поддержка) | |
| Юр. лицо | ИП Шишкин Валерий Сергеевич, ИНН 385101159652 | футер и, вероятно, юр. документы |
| Домен | `https://voicyfy.ru/` | canonical, OG, JSON-LD, виджет |

**Фирменные цвета**: основной акцент `#2563eb` (синий), hover `#1d4ed8`, светлый `#3b82f6` (градиенты), второй акцент `#7c3aed` (фиолетовый — «Агент»), тёмный `#0f172a`. Акцентные тинты секций: лаванда `#f4f0fc`, голубой `#f1f5fd`, песок `#fbf6ee`.

**SEO-блок в `<head>`** (`frontend/index.html`, строки 6–81): `title`, `description`, `robots: index, follow, max-image-preview:large`, `canonical`, полный набор OG + Twitter `summary_large_image`, и JSON-LD `@graph` из трёх объектов — `Organization`, `WebSite`, `SoftwareApplication` (с `featureList` из 5 пунктов и `offers: { price: "0", priceCurrency: "RUB", description: "Пробный период 3 дня без карты, дальше посекундная оплата по тарифу модели" }`). `FAQPage` добавляется пререндером.

---

## 7. `backend/static/index.html` и `backend/static/index/` — что это

**Это предыдущая версия лендинга, vanilla HTML/CSS/JS.** Не React.

**Состав**
- `backend/static/index.html` (19 КБ) — страница; `<meta name="robots" content="noindex, nofollow">`; title **«Voicyfy - Говорит. Слушает. Понимает.»**; подключает Font Awesome 6.4.0 с CDN и `/static/index/css/styles.css`
- `backend/static/index/css/styles.css` (23 КБ) — собственный набор переменных (`--primary-blue: #2563eb`, `--gradient-blue: linear-gradient(135deg,#4a86e8,#2563eb)`, `--bg-light: #f8fafc`, `--radius-md: .5rem` и т.п.) — **другая система токенов**, не `--vf-*`
- `backend/static/index/js/` — `utils.js`, `api.js`, `email-verification.js`, `referral-tracker.js`, `app.js` (те же задачи, что у нынешних хуков, но на ванильном JS)
- Рядом: `backend/static/index_original.html` (61 КБ) — ещё более ранняя версия

**Содержимое старой страницы**: hero со сферой и волнами (`.sphere-container`, `.wave`), заголовок «Ваш голосовой ИИ.» / «Говорит. Слушает. Понимает.», встроенные формы входа/регистрации на странице (`.auth-title` «Вход в аккаунт» / «Создайте аккаунт»), секция «Для чего использовать Voicyfy?» (кейсы «Встроить на сайт», «Подключить к телефонии»), «Тарифы и стоимость» с карточками **Пробный / AI Voice / Старт / Profi** (обратите внимание — там «Старт», а сейчас «Start», и нет тарифа Agent).

**Обслуживается ли**
- Маршрут `/` (`app.py:2493–2499`, `serve_landing`) отдаёт **`backend/static/landing/index.html`** — React-лендинг. Старая страница на корне НЕ отдаётся.
- Но она по-прежнему **доступна** по прямому URL `/static/index.html`, потому что вся папка смонтирована: `app.py:300` → `app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")`.
- Больше того — на неё **всё ещё ссылаются** легаси-страницы кабинета в качестве цели выхода (logout): `gemini-agents.html`, `gemini-agents_old.html`, `cartesia-agents.html`, `fish-agents.html`, `knowledge-base.html`, `cascade.html`, `conversations/index.js` и др. — `window.location.href = '/static/index.html'` / `href="https://voicyfy.ru/static/index.html"`.

**Вывод**: старый лендинг — мёртвый для органики (noindex + не на корне), но живой как «страница после выхода» для части внутренних страниц. Для клона его воспроизводить не нужно; нужно лишь помнить, что logout в легаси-страницах ведёт не на новый лендинг.

---

## 8. Несоответствия и подводные камни для переноса

1. **`frontend/src/claude-frontend-src.md` устарел.** Описывает предыдущую структуру (`HeroSection.jsx`, `CodeSection.jsx`, `ShowcaseSection.jsx`, `PhoneCTASection.jsx`, `ProvidersSection.jsx`, `PricingSection.jsx`, `UseCasesSection.jsx`, `ScrollProgress.jsx`, `MeshBackground.jsx`, `SphereAnimation.jsx`, папку `styles/` из 15 файлов, `index.css`, `.rev → .on` через IntersectionObserver). **Ничего из этого в репозитории нет.** `frontend/claude-frontend.md` тоже неточен: пишет, что зависимости — «только React», хотя есть `motion` и `lenis`, и не упоминает пререндер.
2. **Два CSS-файла, один из них вне бандла.** `voicyfy.css` подключается абсолютным путём `/static/css/voicyfy.css` — при клоне на другой домен/структуру путь придётся править.
3. **Иконки — внешний спрайт по абсолютному пути.** Без `/static/icons/ui.svg` UI лишится всех иконок, и SSR-строка тоже будет без них.
4. **`--vf-font-display: 'Syne'` в ДС vs Unbounded на лендинге** — Syne не грузится, вне `.lp` деградация в Inter.
5. **`apple-touch-icon.png` отсутствует** при объявленном `<link>`.
6. **Мёртвый код**: `CountUp` и `Parallax`-экспорт `CountUp` не используется; `utils/notifications.js` не импортируется; классы `.sec-alt`, `.models-note`, `.plans-what`, `.frame-wide` (передаётся проп `wide`, но CSS-правила для `.frame-wide` в `landing.css` нет) не задействованы или без стилей; правило `.cc-msg-user.cc-foot` мёртвое; медиазапрос `@media (max-width: 1300px) {}` пустой.
7. **Хардкод домена** `https://voicyfy.ru` в `Integration.jsx` (скрипт виджета — и в примере кода, и в реально загружаемом демо), в `index.html` (canonical/OG/JSON-LD).
8. **Цены моделей динамические** (`/api/wallet/tariffs`), цены подписок и телефонии — статические в JSX. При клоне без бэкенда строка моделей покажет только названия без цен, а «подарочные рубли» останутся `65`.
9. **`useAuth()` редиректит** при наличии `auth_token` в `localStorage` — лендинг невозможно посмотреть залогиненным.
10. **Нет модалки-портала**: `AuthModal` рендерится внутри `.lp`, а не в `document.body`; `z-index: 1100` у подложки выше `.lp-nav` (50), но ниже слоёв ДС (`.vf-loader` 1200).
11. **Сборка**: `npm run build` = `vite build && node scripts/prerender.mjs`, результат — `backend/static/landing/{index.html, assets/index-*.js (~374 КБ), assets/index-*.css (~41 КБ)}`. Папку `backend/static/landing/` руками не редактировать.
