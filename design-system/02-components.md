# 02 · Компоненты

Все классы ниже реализованы в `css/voksiai.css`, поведение — в `js/ui.js` (глобальный объект
`VF`). Разметка копируется как есть. Значения размеров даны для сверки, но менять их не надо:
они уже в CSS.

---

## 1. Кнопки `.btn`

```html
<button class="btn">Отмена</button>
<button class="btn btn-primary"><svg class="ic"><use href="/static/icons/ui.svg#i-plus"></use></svg><span class="btn-text">Создать ассистента</span></button>
<button class="btn btn-ghost">Выйти</button>
<button class="btn btn-danger">Удалить</button>
<button class="btn btn-danger btn-solid">Удалить навсегда</button>
<button class="btn btn-sm">Мелкая</button>
<button class="btn btn-lg">Крупная</button>
<button class="btn btn-icon" aria-label="Обновить"><svg class="ic"><use href="…#i-refresh-cw"></use></svg></button>
<button class="btn btn-primary is-loading"><span class="spin"></span> Сохраняем…</button>
```

| Вариант | Внешний вид |
|---|---|
| `.btn` (secondary по умолчанию) | высота 36, паддинг 0 14, радиус 8, бордер border-strong, фон surface, тень shadow-1; hover фон surface-2; active `translateY(1px)` без тени |
| `.btn-primary` | фон accent, белый текст, тень `0 1px 2px rgba(42,92,232,.3) + inset 0 1px 0 rgba(255,255,255,.12)`; hover accent-hover |
| `.btn-ghost` | без бордера и тени, цвет text-2; hover фон surface-3 |
| `.btn-danger` | контурная, текст danger; hover фон danger-soft, бордер danger |
| `.btn-danger.btn-solid` | залитая danger, белый текст; hover `#b91c1c` |
| `.btn-success` (легаси) | залитая success |
| `.btn-sm` | 30px, паддинг 0 10, 12.5px, радиус 7 |
| `.btn-lg` | 42px, паддинг 0 18, 14.5px, радиус 10 |
| `.btn-icon` | ширина 34 (`.btn-sm` — 30), без паддинга |
| `[disabled]`, `.is-loading` | opacity .55, `pointer-events: none` |
| `.spin` | спиннер 16px, `border: 2px solid rgba(255,255,255,.35); border-top-color: currentColor`; в не-primary кнопке бордер border-strong / верх accent |

Иконка в кнопке 16px, gap 8px. Внутри топбара на мобайле `span.btn-text` у primary-кнопки
прячется, остаётся иконка.

Страница агента дополнительно: `.icon-btn` (32×32, радиус 8, прозрачная, text-3; hover
surface-3; `.is-on` accent-soft/accent; `.danger:hover` danger-soft), `.sub-action`
(26px, радиус 7, фон accent, 12px 600 — мини-кнопка внутри карточек).

---

## 2. Поля формы

```html
<div class="field">
  <label class="label" for="name">Название</label>
  <input class="input" id="name" placeholder="Например, Менеджер по продажам">
  <div class="hint">Видно только вам</div>
</div>

<div class="field">
  <label class="label">Инструкция</label>
  <textarea class="textarea" maxlength="4000"></textarea>
  <div class="char-count">0 / 4000</div>
</div>

<div class="input-wrap">
  <svg class="ic"><use href="…#i-search"></use></svg>
  <input class="input" placeholder="Поиск…">
</div>

<div class="form-row">   <!-- две колонки, на мобайле одна -->
  <div class="field">…</div><div class="field">…</div>
</div>

<label class="switch"><input type="checkbox"><span class="track"></span></label>
<input type="range" class="range" min="0" max="1" step="0.1">
```

| Класс | Спека |
|---|---|
| `.input`, `.select-native`, `.textarea` | высота 38 (textarea auto, min 140, паддинг 10 12, line-height 1.55, resize vertical), паддинг 0 12, радиус 8, бордер border-strong, фон surface, 14px, тень shadow-1; hover бордер `#b8c3d4`; focus бордер accent + кольцо 3px accent-ring, outline none |
| `.label` | 12.5px 600 text-2 |
| `.hint` | 12px text-4 |
| `.char-count` | 11.5px text-4, справа |
| `.input-wrap > .ic` | абсолютно слева 11px, text-4; поле получает `padding-left: 36px` |
| `.switch` | 38×22, трек `#cbd5e1` радиус 999, бегунок 18px белый с тенью `0 1px 2px rgba(0,0,0,.25)`, checked → трек accent, бегунок `translateX(16px)`; focus кольцо. На странице агента вариант `.slider` с треком success (тумблер «Активен»), обёрнутый в `.toggle-wrap` (пилюля 32px с подписью; при checked фон success-soft) |
| `.range` | нативный, `accent-color: var(--vf-accent)` |

Легаси-эквиваленты (мост): `.form-group`, `.form-label`, `.form-control`, `.form-input`,
`.form-select`, `.form-textarea`, `.filter-select`, `.form-helper`/`.form-hint`. Нативный
`select.form-control` получает стрелку-шеврон data-URI справа (`padding-right: 34px`).

---

## 3. Кастомный селект `VF.select` и меню `VF.menu`

На страницах с `body.vf` каждый нативный `<select>` автоматически оборачивается в
`VF.select` (`enhanceSelect`; исключение — `data-vf-native`, `data-vf-skip`, `multiple`).
Нативный элемент остаётся скрытым (`.vf-native-hidden`) и синхронизируется в обе стороны,
так что JS страницы читает `.value` и слушает `change` как обычно.

```js
VF.select('voice-select', {
  options: [{ value: 'alloy', label: 'Alloy', sub: 'нейтральный', icon: VF.icon('mic') }],
  // или groups: [{ label: 'OpenAI', options: [...] }]
  value: 'alloy', placeholder: 'Выберите голос', search: true,
  onChange: function (value, option) {}
});
VF.menu(triggerEl, [
  { label: 'Переименовать', icon: 'pen', onClick: fn },
  { sep: true },
  { label: 'Удалить', icon: 'trash-2', danger: true, onClick: fn }
]);
```

| Класс | Спека |
|---|---|
| `.vf-select-btn` | как `.input`, паддинг `0 36px 0 12px`, каретка `.caret` справа 10px (поворот 180° при открытии), `.placeholder` text-4 |
| `.vf-menu` | `position: absolute` (портал `.fixed` для `VF.menu` и селектов внутри модалок), min-width 100%, max-height 320, паддинг 6, радиус 12, фон surface, бордер, тень shadow-3; анимация появления из `translateY(-4px) scale(.98)` |
| `.vf-menu-search` | липкая строка поиска, инпут 32px без бордера на surface-2, иконка search слева |
| `.vf-menu-group` | 11px 600 uppercase text-4 |
| `.vf-menu-item` | паддинг 8 10, радиус 7, 13.5px, gap 10; hover/`.hl` surface-3; `.selected` accent-soft + accent 500; `.sub` справа 12px text-4; `.danger` цвет danger; `.disabled` opacity .5 |
| `.vf-menu-sep` | 1px border, margin 6 0 |
| `.vf-menu-empty` | «Ничего не найдено», 13px text-4 по центру |

Контекстное меню `VF.menu` рисуется под триггером (`top: bottom + 4px`), min-width 200,
закрывается кликом вне, ресайзом и скроллом контейнера.

---

## 4. Поверхности

```html
<div class="card">
  <div class="card-head"><h3>Заголовок</h3><button class="btn btn-sm">Действие</button></div>
  <div class="card-body">…</div>
  <div class="card-foot"><span class="muted small">Подпись</span><button class="btn btn-primary">Сохранить</button></div>
</div>

<div class="section">
  <div class="section-title"><svg class="ic ic-sm"><use href="…#i-sparkles"></use></svg> Голосовые ассистенты</div>
  <div class="grid-auto" style="--min:280px">…карточки…</div>
</div>
<div class="divider"></div>
```

| Класс | Спека |
|---|---|
| `.card` | фон surface, бордер border, радиус 16, тень shadow-1; `.card-raised` shadow-2 |
| `.card-head` | flex между, паддинг 16 20, нижний бордер |
| `.card-body` | паддинг 20 |
| `.card-foot` | паддинг 14 20, верхний бордер, flex между |
| `.section` | margin-bottom 28 |
| `.section-title` | 13px 600 uppercase .04em text-2, gap 8, margin-bottom 12 |
| `.divider` | 1px border, margin 20 0 |
| `.grid-auto` | `repeat(auto-fill, minmax(var(--min, 240px), 1fr))`, gap 14 |
| `.note` / `.note-warning` / `.note-danger` | информационная строка: паддинг 10 12, радиус 8, 12.5px, иконка text-3 сверху; warning — фон warning-soft, текст `#7c4a03`; danger — фон danger-soft, текст `#7f1d1d` |

### Стат-карточка (легаси-класс `.stat-card`, используется на дашборде/CRM/админке)

```html
<div class="stat-card">
  <div class="stat-icon blue"><i class="fas fa-phone"></i></div>
  <div class="stat-value">1 248</div>
  <div class="stat-label">Звонков за месяц</div>
  <div class="stat-subtext">+12% к прошлому</div>
</div>
```

`.stat-icon` 38×38, радиус 10, фон surface-2, бордер; модификаторы `.blue/.purple` (accent-soft
+ accent), `.green` (success), `.orange/.warning`, `.red/.danger`. `.stat-value` Syne 26px 700,
`.stat-label` 12.5px 500 text-3, `.stat-subtext` 12px text-4. Hover карточки → shadow-2.

---

## 5. Чипы, бейджи, точки

```html
<span class="chip">Черновик</span>
<span class="chip chip-accent"><svg class="ic"><use href="…#i-sparkles"></use></svg> Gemini</span>
<span class="chip chip-success"><span class="dot dot-success"></span> Активен</span>
<span class="chip chip-warning">Trial</span>
<span class="chip chip-danger">Ошибка</span>
<span class="chip chip-outline">+7 900 …</span>
```

`.chip`: высота 24, паддинг 0 9, радиус 999, 12px 500, фон surface-3, текст text-2, иконка 13px.
`.dot` 8px круг text-4; `.dot-success`, `.dot-danger`.

Легаси-статусы (`.status-badge`, `.assistant-badge`, `.badge`, `.usage-chip`, `.plan-badge`):
паддинг 3 9; `.active/.success/.paid` → success-soft/success; `.inactive/.error/.expired` →
danger-soft/danger; `.pending/.trial` → warning-soft/warning; `.assistant-badge.not-bound` →
warning.

Страница агента: `.type-badge` (22px, surface-3), `.sub-badge` (пилюля подписки 32px с
`.sub-status.active/.trial/.expired/.none` и `.sub-credits` с иконкой монет warning),
`.agent-dropdown-pill` (20px контурная; `.full` warning).

---

## 6. Табы

```html
<div class="tabs">
  <div class="tab active" data-tab="general"><svg class="ic"><use href="…#i-settings"></use></svg> Основное</div>
  <div class="tab" data-tab="voice">Голос</div>
  <div class="tab disabled">База знаний</div>
</div>
<div class="tab-pane active" id="tab-general">…</div>
<div class="tab-pane" id="tab-voice">…</div>
```

`.tabs`: flex, gap 2, паддинг 0 8, нижний бордер, горизонтальный скролл без полосы.
`.tab`: паддинг 12 12, 13.5px 500 text-3, нижняя граница 2px прозрачная, `margin-bottom: -1px`;
hover text; `.active` accent + граница accent + 600; `.disabled` opacity .45. Иконка 15px.

---

## 7. Таблицы

```html
<div class="table-wrap">
  <table class="table">
    <thead><tr><th>Контакт</th><th>Телефон</th><th>Статус</th><th></th></tr></thead>
    <tbody>
      <tr><td>Иван</td><td class="mono">+996 …</td><td><span class="chip chip-success">Новый</span></td><td><button class="btn btn-sm btn-ghost">…</button></td></tr>
    </tbody>
  </table>
</div>
```

`.table-wrap` бордер + радиус 12 + overflow auto. `th` 11.5px 600 uppercase text-4 на surface-2,
паддинг 10 12, скруглённые верхние углы; `td` паддинг 12, нижний бордер; hover строки surface-2;
у последней строки бордера нет. `.mono` 12.5px моно. `.kbd` для клавиш.

Внутри `.card` легаси-`.table-container` без бордера и радиуса.

---

## 8. Пустые состояния и скелетоны

```html
<div class="empty">
  <svg class="ic ic-lg faint"><use href="…#i-inbox"></use></svg>
  <div class="empty-title">Ассистентов пока нет</div>
  <div class="empty-text">Создайте первого ассистента, и он появится здесь.</div>
  <button class="btn btn-primary">Создать</button>
</div>

<div class="skeleton skeleton-card"></div>
<div class="skeleton skeleton-line" style="width:60%"></div>
```

`.empty` паддинг 40 20, центр, text-3; заголовок 600 text; текст 13px max-width 420.
`.skeleton` фон surface-3, радиус 6, shimmer 1.3s (белая полоса .55, в тёмной .08);
`.skeleton-line` 12px высоты, `.skeleton-card` 112px радиус 16. `VF.skeleton(container, n, cls)`.
Легаси `.empty-state`: бордер, радиус 16, фон surface-2, иконка цвета border-strong.

---

## 9. Модалки и диалоги

### 9.1 `VF.modal` (новые страницы)

```html
<div class="vf-backdrop" id="assistant-modal">
  <div class="vf-dialog vf-dialog-lg" role="dialog" aria-modal="true">
    <div class="vf-dialog-head">
      <div class="row" style="gap:14px;align-items:flex-start">
        <span class="vf-dialog-icon"><svg class="ic"><use href="…#i-bot"></use></svg></span>
        <div><h3>Новый ассистент</h3><div class="muted small">Подзаголовок</div></div>
      </div>
      <button class="btn btn-ghost btn-icon btn-sm" data-close aria-label="Закрыть"><svg class="ic"><use href="…#i-x"></use></svg></button>
    </div>
    <div class="vf-dialog-body">…</div>
    <div class="vf-dialog-foot"><button class="btn" data-close>Отмена</button><button class="btn btn-primary">Создать</button></div>
  </div>
</div>
<script>var m = VF.modal('assistant-modal', { onClose: fn, closeOnBackdrop: true }); m.open(); m.close();</script>
```

| Класс | Спека |
|---|---|
| `.vf-backdrop` | fixed, z 1100, `rgba(14,23,41,.45)` + blur 3px, паддинг 16, центрирование; `.open` opacity 1 |
| `.vf-dialog` | max-width 560 (`-sm` 440, `-lg` 760), max-height 92vh, overflow auto, фон surface, радиус 16, бордер, shadow-3; появляется из `translateY(8px) scale(.985)` |
| `.vf-dialog-head` | паддинг 20 22 0, h3 16px |
| `.vf-dialog-body` | паддинг 14 22 20, text-2 14px |
| `.vf-dialog-foot` | flex right, gap 8, паддинг 0 22 20 |
| `.vf-dialog-icon` | 40×40, радиус 12, accent-soft/accent; `.danger`, `.warning` |

Фокус-ловушка, Esc закрывает верхнюю модалку стека, `[data-close]` закрывает, фокус
возвращается на элемент-инициатор, `[autofocus]` получает фокус через 30ms.

### 9.2 `VF.confirm` / `VF.alert`

```js
if (await VF.confirm({ title: 'Удалить ассистента?', message: 'Действие необратимо.', confirmText: 'Удалить', cancelText: 'Отмена', danger: true })) …
await VF.alert({ title: 'Готово', message: 'Ключ сохранён', okText: 'Понятно' });
```

Диалог `.vf-dialog-sm`, иконка `circle-alert` (danger → `trash-2`, alert → `info`/`circle-x`),
тело с `padding-left: 78px`, кнопки «Отмена» + primary/«danger solid». Тексты по умолчанию:
«Подтвердите действие», «Отмена», «Подтвердить», «Сообщение», «Понятно».

### 9.3 Легаси-модалка (страницы дашборда, телефонии, настроек, CRM, админки)

```html
<div class="modal-overlay" id="…">
  <div class="modal">
    <div class="modal-header"><h3 class="modal-title">…</h3><button class="modal-close">&times;</button></div>
    <div class="modal-body">…</div>
    <div class="modal-footer"><button class="btn btn-secondary">Отмена</button><button class="btn btn-primary">Сохранить</button></div>
  </div>
</div>
```

Мост задаёт: overlay `rgba(14,23,41,.45)` + blur 3px, z 1000; `.modal` радиус 16, бордер,
shadow-3; header паддинг 18 22 с нижним бордером, title 16px 700; `.modal-close` 30×30 радиус 7,
символ «×» 20px; body 20 22 text-2; footer 14 22 с верхним бордером, gap 8.

---

## 10. Тосты `VF.toast`

```js
VF.toast('Сохранено', { type: 'success' });           // success | error | warning | info
VF.toast('Контакт удалён', { type: 'info', action: undo, actionLabel: 'Отменить', duration: 6000 });
```

Контейнер `.vf-toasts` fixed справа-снизу 20px (на мобайле по ширине), z 1120. Тост: фон `#0e1729`,
текст `#e7ecf5`, радиус 12, shadow-3, 13.5px, min 280 / max 420, иконка слева
(success `#4ade80`, error `#f87171`, warning `#fbbf24`, info без цвета), кнопка действия
`#93c5fd` 600, крестик `#93a2b8`. Авто-закрытие 3.8s (error 6s). Легаси `.notification`
стилизован так же.

---

## 11. Загрузчик, прогресс, тултипы

- `.vf-loader` — полноэкранная заставка на старте: wordmark Syne 800 20px + `.vf-wave` (5 полос
  4×8→26px цвета accent, анимация 1.1s с шагом 120ms) + подпись `.sub` 12.5px text-4
  «Загружаем кабинет…». Показывается сразу при `<html data-vf-loader>`, прячется `VF.ready()`
  или автоматически (`data-vf-loader="auto"`: после DOMContentLoaded и завершения первых
  fetch), страховка 6 с. Минимальное время показа 250ms.
- `.vf-progress` — полоса 2px сверху цвета accent со свечением `0 0 8px`; `VF.progress.start()/done()`.
- `[data-tip="Текст"]` — CSS-тултип над элементом: фон `#0e1729`, белый 12px, паддинг 5 8,
  радиус 6, появляется на hover со сдвигом 4px.

---

## 12. Утилиты

`.row` (flex center gap 8) · `.row-between` · `.stack` (column gap 8) · `.grow` (flex 1, min-width 0)
· `.muted` (text-3) · `.faint` (text-4) · `.small` (12.5px) · `.truncate` · `.hidden`
(`display:none !important`) · `.mono` · `.ic / .ic-sm / .ic-lg` · `.logo / .logo-wrap / .logo-color`.

---

## 13. Сайдбар-виджеты, которые рисует `js/sidebar.js`

### 13.1 Меню

Страница держит пустой `<nav class="sidebar-nav" id="sidebar-nav"></nav>`; скрипт синхронно
рисует пункты из константы `MENU` (см. `03-layouts.md`), подсвечивает активный по URL (с
алиасами), добавляет раздел «Администрирование» для `user.is_admin` (кэш в
`sessionStorage.vf_is_admin`, чтобы меню не мигало), навешивает `plan-locked-feature` на
пункты с `data-feature` по матрице `FEATURE_ACCESS`, единый выход по `#logout-button`,
`#dropdown-logout`, `[data-logout]` (удаляет `auth_token`, редирект на `/`).

### 13.2 Карточка кошелька `.vf-wallet`

```
┌──────────────────────────────┐
│ 🪙 КОШЕЛЁК VOKSIAI            │  11px uppercase text-4
│ 1 250 сом                    │  Syne 20px 700
│ [ + Пополнить ]              │  кнопка 32px accent, радиус 8
│ История операций             │  ссылка 12px text-3 → settings.html#wallet
└──────────────────────────────┘
```

margin `0 12px 10px`, паддинг 12 14, радиус 12, фон surface-2, бордер. Вставляется перед
`.sidebar-footer`. Баланс подгружается из `GET /api/wallet/balance` (`balance_kopeks`,
`min_topup_rub`, `max_topup_rub`), событие `vf:wallet`.

### 13.3 Модалка пополнения `#vf-topup-modal`

Overlay `rgba(14,23,41,.55)` z 10000; окно max-width 420, паддинг 24, радиус 16, тень
`0 20px 40px rgba(0,0,0,.2)`. Заголовок «Пополнить кошелёк», абзац с балансом, чипы пресетов
`300 / 500 / 1000 / 3000` (`.vf-chip` пилюли, активный accent), `input[type=number]`,
подсказка «От {min} до {max}. Оплата через платёжный шлюз.», кнопки «Отмена» и
«Перейти к оплате». Успешный ответ `POST /api/wallet/topup` → автосабмит скрытой формы на
`payment_url` с `form_params` (в Voicyfy — Robokassa; для КР подставить свой шлюз).
Суммы и валюта в копии заменены на «сом» (`fmtMoney`), пресеты оставлены как в оригинале —
пересмотреть под местные цены.

---

## 14. Библиотеки (CDN), использованные в кабинете

| Библиотека | Версия | Где | Зачем |
|---|---|---|---|
| Font Awesome Free | 6.4.0 (cdnjs) | 27 страниц | запасная иконочная разметка `<i class="fas …">`, подменяется на Lucide |
| Lucide | спрайт из пакета `lucide` (ISC) | все страницы на DS | иконки |
| marked | 12.0.2 (cdnjs) | agent.html | markdown ответов оркестратора |
| DOMPurify | 3.1.6 (cdnjs) | agent.html | санитизация markdown |
| SortableJS | 1.15.0 (jsdelivr) | crm.html | drag-and-drop колонок/канбана |
| three.js | r128 (cdnjs) | index.html (старый лендинг, не используется) | 3D-фон |
| Google Fonts | — | все | Inter, Syne, Unbounded |

Никаких CSS-фреймворков (Bootstrap/Tailwind) и JS-фреймворков в кабинете нет: карточки,
табы, модалки, селекты, тосты — собственные компоненты из этого файла. Лендинг: React 18 +
Vite 5 + `motion` 13 + `lenis` 1.3 (см. `05-landing.md`).
