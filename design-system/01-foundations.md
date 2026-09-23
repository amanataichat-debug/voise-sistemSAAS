# 01 · Основы дизайн-системы VoksiAI

Дизайн-система перенесена 1:1 с Voicyfy (направление «Studio»: светлая тема, тонкие бордеры,
мягкие многослойные тени, синий акцент). Всё, что ниже, реализовано в `css/voksiai.css`
(токены + компоненты) и `js/ui.js` (поведение). Подключать файлы как есть, ничего не
переписывать: страницы кабинета собираются из этих классов.

Пространство имён сохранено: CSS-переменные `--vf-*`, классы `.vf-*`, глобальный объект `VF`.
Это намеренно, чтобы JS-модули и разметка переносились без правок. `vf` читать как
«voice framework».

---

## 1. Палитра

### 1.1 Светлая тема (по умолчанию) — VoksiAI

Цвета сдвинуты относительно Voicyfy едва заметно (акцент чуть более индиго, нейтральные
чуть холоднее). В таблице обе версии, чтобы было видно, что менялось.

| Токен | VoksiAI | Voicyfy (было) | Назначение |
|---|---|---|---|
| `--vf-bg` | `#ffffff` | `#ffffff` | фон страницы (единый белый, без серой подложки) |
| `--vf-surface` | `#ffffff` | `#ffffff` | панели, карточки |
| `--vf-surface-2` | `#f6f8fc` | `#f7f9fc` | вложенные блоки, подложки, hover кнопок |
| `--vf-surface-3` | `#edf1f8` | `#eef2f8` | hover строк и пунктов меню, чипы |
| `--vf-border` | `#e2e7f0` | `#e3e8f0` | обычная граница |
| `--vf-border-strong` | `#cdd5e2` | `#cfd7e3` | граница кнопок и полей |
| `--vf-text` | `#0e1729` | `#0f172a` | основной текст |
| `--vf-text-2` | `#46556b` | `#475569` | вторичный текст, подписи полей |
| `--vf-text-3` | `#63738b` | `#64748b` | приглушённый текст, неактивные табы |
| `--vf-text-4` | `#93a2b8` | `#94a3b8` | плейсхолдеры, подсказки, иконки в покое |
| `--vf-accent` | `#2a5ce8` | `#2563eb` | акцент: кнопки, ссылки, активные табы |
| `--vf-accent-hover` | `#2149cf` | `#1d4ed8` | hover акцента |
| `--vf-accent-soft` | `#ebf0ff` | `#eaf1ff` | подложка активного пункта, чип-accent |
| `--vf-accent-ring` | `rgba(42,92,232,.18)` | `rgba(37,99,235,.18)` | focus-кольцо 3px |
| `--vf-success` | `#159a4a` | `#16a34a` | успех, «Активен» |
| `--vf-success-soft` | `#e7f7ee` | `#e8f7ee` | подложка успеха |
| `--vf-warning` | `#db7508` | `#d97706` | предупреждение, trial |
| `--vf-warning-soft` | `#fdf3e2` | `#fdf3e2` | подложка предупреждения |
| `--vf-danger` | `#dd2a2a` | `#dc2626` | ошибка, удаление |
| `--vf-danger-soft` | `#fdecec` | `#fdecec` | подложка ошибки |

Градиент логотипа/wordmark: `linear-gradient(135deg, #2a5ce8, #4a7cf3)` (было `#2563eb → #3b82f6`).
Тёмная «чернильная» подложка тостов и тултипов: `#0e1729` (значение `--vf-text`).
Цвет `theme-color` в `<meta>`: `#2a5ce8`.

### 1.2 Тёмная тема `[data-theme="dark"]`

Поддерживается токенами (переключение атрибутом на `<html>`), в кабинете Voicyfy по умолчанию
не включена. Значения VoksiAI:

| Токен | Значение |
|---|---|
| `--vf-bg` | `#0b1220` |
| `--vf-surface` | `#111a2e` |
| `--vf-surface-2` | `#16203a` |
| `--vf-surface-3` | `#1c2744` |
| `--vf-border` | `rgba(148,163,184,.14)` |
| `--vf-border-strong` | `rgba(148,163,184,.28)` |
| `--vf-text` / `-2` / `-3` / `-4` | `#e7ecf5` / `#b6c2d6` / `#8a98b2` / `#61708c` |
| `--vf-accent` / `-hover` | `#4a7cf3` / `#6ea2fa` |
| `--vf-accent-soft` / `-ring` | `rgba(74,124,243,.14)` / `rgba(74,124,243,.3)` |
| `--vf-success-soft` / `-warning-soft` / `-danger-soft` | `rgba(21,154,74,.16)` / `rgba(219,117,8,.16)` / `rgba(221,42,42,.16)` |
| `--vf-shadow-1/2/3` | `0 1px 2px rgba(0,0,0,.3)` / `0 4px 14px -2px rgba(0,0,0,.45)` / `0 18px 48px -12px rgba(0,0,0,.6)` |

В тёмной теме монохромные логотипы моделей инвертируются: `[data-theme="dark"] .logo-wrap img.logo { filter: invert(1) hue-rotate(180deg) }`, цветные (`.logo-color`) не трогаются.

### 1.3 Легаси-алиасы (мост для старых страниц)

Страницы `dashboard`, `telephony`, `conversations`, `settings`, `crm`, `admin` написаны на
собственных переменных. Файл `css/voksiai-legacy.css` переопределяет их токенами:

```
--primary-blue → --vf-accent        --text-dark → --vf-text      --bg-light → --vf-bg
--primary-blue-dark → accent-hover  --text-gray → --vf-text-3    --bg-blue-light → accent-soft
--primary-blue-light → #4a7cf3      --text-light → --vf-text-4   --white → --vf-surface
--gradient-blue → 135deg accent     --border-color → --vf-border --shadow-sm/md/lg → shadow-1/2/3
--radius-md → --vf-r-sm (8px)       --radius-lg → --vf-r-lg      --radius-full → 999px
--green-500 → success  --red-500 → danger  --orange-500 → warning
--transition-sm → all var(--vf-fast)  --transition-md → all var(--vf-normal)
```

Страница агента (`agent.css`) использует ещё один набор алиасов: `--bg`, `--white`, `--blue`,
`--blue-dark`, `--blue-light`, `--blue-border: rgba(42,92,232,.25)`, `--text`, `--muted`, `--hint`,
`--green`, `--green-dark: #15803d`, `--green-light`, `--red`, `--red-light`, `--amber`,
`--amber-light`, `--border`, `--radius` (=r-lg), `--radius-sm`, `--radius-pill`, `--elev-1/2/3`,
`--glow-accent: 0 0 0 3px var(--vf-accent-ring)`.

---

## 2. Типографика

### 2.1 Шрифты

| Роль | Токен | Стек | Google Fonts |
|---|---|---|---|
| Основной | `--vf-font` | `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif` | `Inter:wght@400;500;600;700` (лендинг — `Inter:opsz,wght@14..32,400..700`) |
| Заголовочный/дисплейный (кабинет) | `--vf-font-display` | `'Syne', var(--vf-font)` | `Syne:wght@700;800` |
| Моно | `--vf-font-mono` | `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` | — |
| Дисплейный на лендинге (`.lp` переопределяет `--vf-font-display`) и бренд-шрифт страницы агента (`--font-brand`) | — | `'Unbounded', 'Inter', sans-serif` | лендинг `Unbounded:wght@500;600;700`, агент `Unbounded:wght@400;500;600;700` |

Единая строка подключения для страниц кабинета:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Syne:wght@700;800&display=swap">
```

**Важно для кыргызской версии.** Syne не содержит кириллицы: в Voicyfy им набраны только
латинский wordmark, цифры (`.stat-value`, баланс кошелька) и заголовки h1, где кириллица
фактически падает в Inter через фолбэк. Inter содержит полную кириллицу, включая кыргызские
буквы **Ң ң, Ө ө, Ү ү**. Unbounded тоже поддерживает кириллицу, но кыргызские буквы в нём
нужно проверить перед использованием в заголовках; безопасный вариант для KG-текстов —
Inter 700/800.

### 2.2 Шкала кабинета (`body.vf`)

| Элемент | Размер | Вес | Прочее |
|---|---|---|---|
| `body` | 14px | 400 | line-height 1.5, antialiased |
| `h1` (заголовок страницы, `.page-title`) | 22px | 700 | Syne, letter-spacing −0.02em |
| `h2` | 17px | 700 | letter-spacing −0.01em |
| `h3` | 15px | 700 | |
| Заголовок карточки `.card-title`, модалки `.modal-title`, `.vf-dialog-head h3` | 16px | 700 | |
| `.section-title` (подзаголовок раздела) | 13px | 600 | uppercase, letter-spacing .04em, цвет text-2 |
| `.sidebar-section`, `.panel-title`, `.vf-menu-group` | 11px | 600 | uppercase, letter-spacing .05–.06em, text-4 |
| Пункт сайдбара | 14px | 500 (active 600) | |
| Кнопка `.btn` | 13.5px | 500 | `.btn-sm` 12.5px, `.btn-lg` 14.5px |
| Поле `.input` | 14px | 400 | плейсхолдер text-4 |
| Подпись поля `.label` | 12.5px | 600 | text-2 |
| Подсказка `.hint`, `.form-helper` | 12px | 400 | text-4 |
| Таб `.tab` | 13.5px | 500 (active 600) | |
| Таблица `.table` | 13.5px | 400 | th 11.5px 600 uppercase .04em text-4 |
| Чип `.chip`, статус-бейдж | 12px | 500 | |
| Число в стат-карточке `.stat-value` | 26px | 700 | Syne, −0.02em |
| Подпись стат-карточки `.stat-label` | 12.5px | 500 | text-3 |
| Тост | 13.5px | 400 | |
| Тултип `[data-tip]` | 12px | 400 | |
| `.mono` | 12.5px | 400 | моно |
| `.kbd` | 11px | 400 | моно, рамка с утолщённым низом |
| `.small` | 12.5px | | |

Логотип в сайдбаре `.vf-logo`: Syne 800, 19px, letter-spacing −0.03em, wordmark залит
градиентом акцента (`background-clip: text`). Марка `.mark` 30×30, радиус 9px, фон акцент,
белая иконка 16px.

### 2.3 Шкала лендинга

Лендинг задаёт собственную шкалу в `css/landing.css` (см. `05-landing.md`): дисплейный шрифт
там Unbounded (не Syne): h1 42px/700 (36 ≤1100, 32 ≤900, 26 ≤600), h2 28px/700 (24 ≤900,
21 ≤600), h3 Inter 18px/700, h4 12px uppercase, лид 16.5px, базовый текст 15px, line-height 1.6.

---

## 3. Отступы, радиусы, тени, движение

### 3.1 Радиусы

| Токен | Значение | Где |
|---|---|---|
| `--vf-r-sm` | 8px | кнопки, поля, пункты сайдбара, заметки `.note` |
| `--vf-r-md` | 12px | меню, тосты, обёртка таблицы, карточка ассистента в списке |
| `--vf-r-lg` | 16px | карточки `.card`, диалоги, стат-карточки |
| `--vf-r-full` | 999px | чипы, переключатели, кнопка пользователя |
| без токена | 7px | `.btn-sm`, пункты меню, кнопка закрытия модалки |
| без токена | 10px | `.btn-lg`, `.logo-wrap`, `.stat-icon`, `.nav-logo` |
| без токена | 6px | скелетоны, кнопки в тостах, тултип |

### 3.2 Тени

| Токен | Значение |
|---|---|
| `--vf-shadow-1` | `0 1px 2px rgba(14,23,41,.04), 0 1px 1px rgba(14,23,41,.03)` — кнопки, поля, карточки в покое |
| `--vf-shadow-2` | `0 1px 2px rgba(14,23,41,.05), 0 4px 12px -2px rgba(14,23,41,.08)` — `.card-raised`, hover карточек |
| `--vf-shadow-3` | `0 2px 4px rgba(14,23,41,.06), 0 16px 40px -12px rgba(14,23,41,.18)` — меню, диалоги, тосты, открытый мобильный сайдбар |
| Кнопка primary | `0 1px 2px rgba(42,92,232,.3), inset 0 1px 0 rgba(255,255,255,.12)` |
| Топбар при скролле | `0 4px 12px -8px rgba(14,23,41,.12)` |
| Focus | `0 0 0 3px var(--vf-accent-ring)` |

Принцип глубины: тонкий бордер (`--vf-border`) + мягкая тень. Никаких жирных теней и
градиентных подложек в кабинете; градиент только в wordmark и на лендинге.

### 3.3 Отступы (фактические, не токенизированы)

| Место | Значение |
|---|---|
| Контент страницы `.vf-content` | `24px 28px 40px` (мобайл `14px 12px 32px`) |
| Топбар | высота 64px, паддинг `0 28px` (мобайл `0 12px`) |
| Сайдбар | ширина 256px, nav `12px 12px`, пункт `9px 12px`, футер 12px |
| Карточка | head `16px 20px`, body `20px`, foot `14px 20px` |
| Диалог | head `20px 22px 0`, body `14px 22px 20px`, foot `0 22px 20px` |
| Модалка легаси | header `18px 22px`, body `20px 22px`, footer `14px 22px` |
| Поле формы `.field` | gap 6px, margin-bottom 16px; `.form-row` две колонки gap 16px |
| Секция `.section` | margin-bottom 28px |
| Сетка карточек `.grid-auto` | `repeat(auto-fill, minmax(240px, 1fr))`, gap 14px |
| Сетка ассистентов (grid-mode) | `minmax(280px, 1fr)`, gap 14px |
| Меню `.vf-menu` | padding 6px, пункт `8px 10px`, margin-top 6px |
| Таблица | th `10px 12px`, td `12px 12px` |
| Тост | `11px 12px 11px 14px`, min-width 280, max-width 420 |

### 3.4 Движение

| Токен | Значение | Где |
|---|---|---|
| `--vf-fast` | `140ms cubic-bezier(.2,.7,.2,1)` | hover кнопок/полей/табов, тултипы |
| `--vf-normal` | `220ms cubic-bezier(.2,.7,.2,1)` | сайдбар, модалки, тосты, пункты меню |
| страница агента `--dur-fast/--dur/--dur-slow` | 120 / 200 / 340ms | с `--ease-out: cubic-bezier(.22,1,.36,1)` и `--ease-spring: cubic-bezier(.34,1.56,.64,1)` |

Анимации-ключи: `vf-shimmer` (скелетоны 1.3s), `vf-wave` (загрузчик 1.1s, 5 полос с шагом
120ms), `vf-spin` (спиннер .8s / 1s), `ddIn` (дропдаун агента .15s). `prefers-reduced-motion`
обнуляет все transition/animation. `html { scroll-behavior: smooth; scrollbar-gutter: stable }`.

Микровзаимодействия, которые нужно повторить:
- пункт сайдбара: подложка `::before` выезжает `scaleX(.96) → 1` слева; иконка при hover
  сдвигается на 2px и увеличивается до 1.06; у активного пункта слева полоска 3×20px
  цвета акцента (`::after`);
- `.btn:active { transform: translateY(1px); box-shadow: none }`;
- `.vf-menu`: `opacity 0, translateY(-4px) scale(.98)` → открыто;
- `.vf-dialog`: `translateY(8px) scale(.985)` → открыто, backdrop `rgba(14,23,41,.45)` + blur 3px;
- тост: `translateY(8px)` → 0;
- топбар: полупрозрачный `color-mix(in srgb, var(--vf-bg) 86%, transparent)` + `backdrop-filter: saturate(160%) blur(10px)`; граница и тень появляются только при `scrollY > 4` (класс `.scrolled`, вешает `ui.js`).

### 3.5 Слои (z-index)

| Слой | z |
|---|---|
| Мобильный скрим `.vf-scrim` | 29 |
| Сайдбар `.vf-sidebar` | 30 |
| Топбар | 20 |
| Тултипы | 50 |
| Меню `.vf-menu` (в потоке) | 60 |
| Страничные модалки `.modal-overlay` (легаси) | 1000 |
| `VF.confirm` / `VF.modal` backdrop | 1100 |
| Тосты | 1120 |
| Загрузчик страницы `.vf-loader` | 1200 |
| Полоса прогресса `.vf-progress` | 1210 |
| Портал `VF.select` | 1300 |
| Модалка пополнения кошелька (sidebar.js) | 10000 |

### 3.6 Брейкпоинты

| Порог | Что меняется |
|---|---|
| `max-width: 960px` | сайдбар уезжает `translateX(-100%)`, открывается кнопкой `#sidebar-toggle` со скримом; `.vf-main { margin-left: 0 }`; топбар паддинг 12px, h1 17px, у primary-кнопки в топбаре прячется текст (`span.btn-text`); `.form-row` в одну колонку; тосты растягиваются на ширину |
| `max-width: 1100px` (страница агента) | скрываются боковые панели, появляются drawer и бургер |
| `max-width: 720px` (voice-assistants, crm) | сетка списка/редактора в одну колонку |
| лендинг | 1100 / 900 / 640px, см. `05-landing.md` |

---

## 4. Иконки и логотипы

### 4.1 Lucide-спрайт (`icons/ui.svg`)

Все иконки кабинета — символы Lucide в одном SVG-спрайте (125 штук), подключаются через
`<use>`:

```html
<svg class="ic" aria-hidden="true"><use href="/static/icons/ui.svg#i-house"></use></svg>
```

или из JS: `VF.icon('house')`, `VF.icon('x', 'ic-sm')`. Размеры: `.ic` 18px, `.ic-sm` 15px,
`.ic-lg` 22px; в кнопке 16px, в табе 15px, в чипе 13px. Цвет наследуется (`stroke: currentColor`,
`stroke-width: 2`).

Полный список имён (все с префиксом `i-` в спрайте): arrow-left, arrow-right, arrow-up,
arrow-down, audio-lines, badge-check, bell, book, book-open, bookmark, bot, brain, briefcase,
building, building-2, banknote, calendar, calendar-plus, chart-bar, chart-column, check, chevron-down,
chevron-left, chevron-right, circle, circle-alert, circle-check, circle-help, circle-minus, circle-x,
clock, code, coins, columns-2, contact-round, copy, cpu, credit-card, crown, download, external-link,
eye, eye-off, file, file-text, filter, fish, flame, folder, gem, globe, globe-2, handshake, headphones,
headset, history, hourglass, house, image, inbox, infinity, info, key, layers, layout-grid, link, link-2,
list, loader-circle, lock, log-out, mail, map-pin, menu, message-circle, message-square,
messages-square, mic, pen, pencil, phone, phone-call, phone-incoming, phone-off, phone-outgoing,
piggy-bank, play, plug, plus, puzzle, receipt, refresh-cw, rocket, rotate-ccw, route, russian-ruble,
save, search, send, settings, shield-check, shopping-cart, sliders-horizontal, smartphone, sparkles,
square-check, square-pen, star, sticky-note, tag, trash-2, trending-up, triangle-alert, unlink,
upload, user, user-check, user-round, users, volume-2, wallet, wand-2, x, zap, activity.

Для сома вместо `russian-ruble` добавьте символ Lucide `banknote` или `coins` (уже есть).

### 4.2 Мост Font Awesome → Lucide

Старые страницы размечены `<i class="fas fa-…">`. Font Awesome 6.4.0 подключён с cdnjs как
запасной вариант, но `ui.js` (`faShim`) заменяет содержимое каждого `<i class="fa-…">` на SVG
из спрайта по таблице `FA_MAP` (например `fa-check-circle → circle-check`, `fa-trash → trash-2`,
`fa-sync-alt → refresh-cw`, `fa-gear → settings`) и следит за динамической разметкой через
`MutationObserver`. Поэтому визуально весь кабинет — Lucide. На странице агента FA-глиф
гасится: `i.vf-fa::before { content: none }`.

Новые страницы можно размечать сразу Lucide-спрайтом, Font Awesome не нужен.

### 4.3 Логотипы голосовых моделей (`icons/models/`)

| Код | Файл цветной | Файл моно | Название |
|---|---|---|---|
| `openai` | — | `openai.svg` | OpenAI |
| `gemini` | `gemini-color.svg` | `gemini.svg` | Gemini |
| `fish` | `fishaudio-color.svg` | `fishaudio.svg` | Fish Audio |
| `yandex` | `yandex-color.svg` | `yandex.svg` | Яндекс |
| `cascade` | `cascade-color.svg` | `cascade.svg` | Каскад |

Рендер: `VF.logo('gemini')` → `<span class="logo-wrap"><img class="logo logo-color" src=".../gemini-color.svg"></span>`.
`.logo-wrap` 36×36, радиус 10px, фон surface-2, бордер; `.logo` 22×22, `object-fit: contain`.
Опции: `{mono: true}` — монохромный файл, `{bare: true}` — без обёртки, `{size: N}` — размер
картинки (обёртка N+14). Неизвестный код → обёртка с иконкой `bot`.

### 4.4 Бренд-ассеты

| Ассет | Voicyfy | Что сделать для VoksiAI |
|---|---|---|
| Wordmark | текст «Voicyfy» Syne 800 с градиентом акцента | текст «VoksiAI» тем же стилем; в `.vf-logo` перед ним `<img>` 30×30 радиус 8px (логотип-картинка) либо `.mark` (квадрат акцента с иконкой `audio-lines`) |
| Favicon | `favicon.ico`, `favicon-16/32/48/64/96/192/512.png`, `android-chrome-192/512.png`, `apple-touch-icon.png`, `site.webmanifest`, `manifest.json` | нарисовать свои; `theme-color` `#2a5ce8` |
| OG-картинка | `og-image.png` 1200×630 | своя |
| Загрузчик | «волна» из 5 полос акцента + wordmark + подпись «Загружаем кабинет…» | оставить, заменить текст |

---

## 5. Подключение на странице (шаблон `<head>`)

```html
<!DOCTYPE html>
<html lang="ru" data-vf-loader>          <!-- data-vf-loader="auto" — прятать загрузчик после первых fetch -->
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="robots" content="noindex, nofollow">    <!-- все страницы кабинета -->
  <title>Голосовые ассистенты | VoksiAI</title>
  <link rel="icon" type="image/x-icon" href="/static/favicon.ico">
  <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">
  <link rel="manifest" href="/static/manifest.json">
  <meta name="theme-color" content="#2a5ce8">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Syne:wght@700;800&display=swap">
  <link rel="stylesheet" href="/static/css/voksiai.css">
  <script src="/static/js/ui.js"></script>   <!-- в <head>: сразу рисует загрузчик -->
  <style>/* стили только этой страницы */</style>
</head>
<body class="vf">
  …каркас из 03-layouts.md…
  <script src="/static/js/sidebar.js"></script>
</body>
</html>
```

Порядок для легаси-страниц: инлайновые стили страницы → `voksiai.css` → `voksiai-legacy.css`
(мост должен идти **после** инлайна, чтобы перебить старые классы) → `ui.js`.
