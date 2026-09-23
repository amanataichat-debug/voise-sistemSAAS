# backend/static — фронтенд приложения (vanilla HTML/JS), виджеты и собранный лендинг

## Назначение
Весь фронтенд, который отдаёт FastAPI напрямую через `StaticFiles` (монтируется в `app.py` на `/static` и `/js`). Здесь живут внутренние страницы кабинета (дашборд, управление ассистентами всех провайдеров, CRM, телефония, аналитика, настройки, админка) на чистом HTML/CSS/JS, встраиваемые голосовые виджеты (`*-widget.js`, `widget.js`) и собранный React-лендинг в `landing/`. Бизнес-логика страниц — в инлайновых скриптах и модулях `js/`, `agents/`, `conversations/`; backend они дёргают по `/api/...` и `/ws/...`.

## Состав (верхний уровень)
### HTML-страницы кабинета
- `dashboard.html` — дашборд пользователя.
- `agents.html` — OpenAI-ассистенты; `gemini-agents.html`, `grok-agents.html`, `fish-agents.html`, `eleven-agents.html`, `cartesia-agents.html`, `yandex-agents.html`, `cascade.html`, `translate.html` — страницы по провайдерам; `gemini-agents_old.html` — легаси.
- `voice-assistants.html` + `js/voice-assistants.js` — **голосовые ассистенты ElevenLabs** (дизайн-система, этапы 2–3; спека `design-system/04-pages-cabinet.md` §2). В кабинете остались только ассистенты ElevenLabs: витрина карточек → редактор с вкладками Настройки (имя, приветствие, промпт, язык, модель синтеза, голос аккаунта с превью, поиск/добавление из библиотеки, стабильность, диалоговая модель) · Функции · База знаний (своя у каждого ассистента: `/api/knowledge-base/assistant/eleven/{id}`, при создании включается `search_pinecone`) · Тестирование (`widget.js` с `data-ws-path="/ws/eleven/"`, `data-server` обязателен) · Встраивание (код собирается на клиенте). URL: `?id=&tab=`, `?new=1`. Строки — раздел `va` словарей `i18n/*.json`. Закрытые страницы редиректятся сюда из `app.py` (`LEGACY_ASSISTANT_PAGES`): `agents`, `gemini-agents`, `fish-agents`, `eleven-agents`, `grok-agents`, `fish-test`, `knowledge-base`, `translate`, `voice_llm_interface/` (`?id` сохраняется, `?mode=create` → `?new=1`). Файлы этих страниц остались в дереве, но не открываются; бэкенд других провайдеров и переводчика не трогали — их ассистенты продолжают работать на привязанных номерах и в уже встроенных виджетах.
- `fish-agents.html` — Fish-агенты на серверных ключах (карточка ключей убрана; статус сервера через `GET /api/fish-assistants/status`), кнопка «Тест» открывает `fish-test.html?id=<uuid>` — тот же `widget.js`, но со `data-ws-path="/ws/fish/"`. `widget.js` понимает атрибут `data-ws-path` (по умолчанию `/ws/`), так один виджет обслуживает все провайдеры с протоколом виджета.
- `eleven-agents.html` — ElevenLabs-агенты на серверном ключе (копия страницы Fish, API `/api/eleven-assistants`): селектор модели синтеза с ценой (`/options`), голос — `<select>` из аккаунта ElevenLabs (`/voices?language=`, рекомендованные для языка первыми, превью `<audio>`), панель «Найти в библиотеке» (`/voices/library`, поиск/пол/пагинация, «Добавить» → `/voices/library/add` и голос сразу выбран), язык (по умолчанию кыргызский, при смене список голосов перезагружается), стабильность (Creative/Natural/Robust), диалоговая модель. Приветствие по умолчанию на кыргызском. Кнопка «Тест» открывает `eleven-test.html?id=<uuid>` (`widget.js` с `data-ws-path="/ws/eleven/"`).
- `telephony.html` + `js/telephony.js` — телефония через собственный SIP-шлюз (дизайн-система, каркас A, этап 3), целиком на `/api/sip/*`: таблица номеров пользователя (`GET /api/sip/numbers`), модалка привязки (`VF.modal`) к ассистенту **ElevenLabs** или агенту обзвона (`PATCH /api/sip/numbers/{id}` с `assistant_type`+`assistant_id` или `agent_config_id`; номер, уже привязанный к ассистенту другого провайдера, показывается как «текущая привязка» и продолжает работать), исходящий звонок (`POST /api/sip/calls`, только номера O! `050/070/099` — проверка и на странице, и в API) с живым статусом (опрос `GET /api/sip/calls/{id}` раз в 2 с, отбой `POST /api/sip/calls/{id}/hangup`), журнал (`GET /api/sip/calls`). Номера заводит только админ. Строки — раздел `sip` словарей.
- `outbound-calls.html`, `test_outbound-calls.html` — страницы Voximplant, **мёртвые** (Voximplant не используется, см. корневой `CLAUDE.md`).
- `crm.html`, `crm-contact.html` — CRM (список и карточка контакта).
- `conversations.html` — история диалогов.
- `agent.html` — страница Voksy AI Agent (оркестратор/обзвон).
- `knowledge-base.html`, `integrations.html`, `settings.html`, `admin.html` — база знаний, интеграции, настройки, админка.
- `index.html`, `index_original.html` — входные/легаси страницы; `widget.html` — демо виджета.
- Юридические/контентные: `privacy-policy.html`, `public-offer.html`, `terms-of-service.html`, `payment-terms.html`, `prompts-wiki.html`, `api-docs.html`, и др.
- Тестовые: `test-ga-api.html`. `cascade-test.html` — заглушка-редирект на
  `cascade.html` (страница переименована; заглушка сохраняет query-параметры
  старых ссылок).

### Дизайн-система VoksiAI (кабинет)
Спеки и исходники пакета — в корневом `design-system/` (читать `README.md`). В кабинет перенесено:
- `css/voksiai.css` — токены `--vf-*` и компоненты; `css/voksiai-legacy.css` — мост старых классов
  страниц (`--primary-blue`, `.btn`, `.card`, `.top-nav` …) на токены + нормализация сайдбара и
  мобильной шапки старых страниц. Оба подключаются **после** инлайнового `<style>` страницы.
  Базовые `h1–h4`/`p` в `voksiai.css` заданы через `:where(.vf)`, чтобы классы старых страниц их перебивали.
- `js/i18n.js` — RU/KY: `I18N.t('section.key', params, def)`, разметка `data-i18n`, `data-i18n-html`,
  `data-i18n-ph`, `data-i18n-title`; язык из `?lang=` → `localStorage.vs_lang` (общий с лендингом) →
  язык браузера → `ru`. Словари `i18n/ru.json`, `i18n/ky.json` (одинаковые ключи). Подключать до `ui.js`.
- `js/ui.js` — `VF.*` (тосты, confirm, модалки, селекты, иконки), мост Font Awesome → Lucide, автозамена
  `<select class="form-control|filter-select|form-select|status-dropdown">` на `VF.select` на `body.vf`
  (нативный select остаётся в DOM и синхронизирован). Отключить для select — атрибут `data-vf-skip`.
- `js/sidebar.js` — **единое меню** (константа `MENU`): страница держит пустой
  `<nav class="sidebar-nav" id="sidebar-nav">`, скрипт рисует пункты с прежними `id`
  (`telephony-nav-item`, `crm-nav-item`, `data-feature` …), раздел «Администрирование» (is_admin или
  e-mail админа), единый выход и переключатель RU | KY в топбаре. Новый пункт меню — только здесь.
  Кошелёк и онбординг из пакета не перенесены (нет бэкенда `/api/wallet/*`).
- `icons/ui.svg` (спрайт Lucide, `<svg class="ic"><use href="/static/icons/ui.svg#i-name">`),
  `icons/models/*.svg` (логотипы моделей).
- На дизайн-системе: `voice-assistants.html`, `telephony.html` — каркас A (`.vf-app`, без моста); каркас B из `design-system/03-layouts.md`
  (`<body class="vf">` + мост): dashboard, knowledge-base, telephony, conversations, crm, crm-contact, settings,
  admin, integrations; `login.html` — на токенах без каркаса. Мёртвые страницы (cartesia, yandex,
  cascade, outbound-calls) остались на старой `css/voicesystem-theme.css`. `agent.html` ещё не переведён.

### Встраиваемые виджеты (JS)
- `widget.js` — основной голосовой web-виджет (v5.0). Протокол виджета общий для OpenAI (GPT-Live, `/ws/{id}`), Fish (`data-ws-path="/ws/fish/"`) и Eleven (`data-ws-path="/ws/eleven/"`). Если сервер прислал `connection_status.full_duplex: true` (GPT-Live), виджет стримит микрофон непрерывно, включая время речи ассистента (эхо гасит AEC браузера), и воспроизводит аудио gapless по таймлайну AudioContext (`scheduleLiveAudio`, запас 200 мс); без флага — прежний half-duplex режим с паузой микрофона и очередью `playNextAudio`. `widget-test-new.js` — старая тестовая копия.
- `gemini-widget.js`, `gemini-widget-fullscreen.js` — виджеты Gemini (оба ходят в `/ws/gemini/{id}`, модель `gemini-3.8-live`; атрибут `data-model` у fullscreen-виджета устарел и игнорируется). Виджеты 3.1, browser-агента и захвата экрана удалены.
- `grok-widget.js` — Grok; `widget-translate.js` — перевод. (`wigetelevanlabs.js` старой интеграции ElevenLabs удалён.)

### Подпапки
- `landing/` — **собранный** React-лендинг (артефакт Vite-сборки из `frontend/`, + `assets/`). Не редактировать вручную.
- `agents/` — JS-модули страницы агентов: `index.js` (логика), `api.js` (клиент), `ui.js` (рендер).
- `conversations/` — `index.js` для страницы диалогов.
- `js/` — общие JS-модули страниц: `crm.js`, `crm-contact.js` и др. (семейство `elevenlabs-agents-*.js` и `elevenlabs-*.js` старой интеграции удалено).
- `index/` — `css/` и `js/` для входной страницы.
- `voice_llm_interface/` — отдельный голосовой LLM-интерфейс: `index.html`, `jarvis-ui.html`, `main.js`, `audio.js`, `config.js`, `styles.css`.
- `css/`, `images/` — стили и изображения; `icons/` и `i18n/` — дизайн-система (см. выше).
- Иконки/манифесты PWA (`favicon*`, `android-chrome-*`, `site.webmanifest`, `manifest.json`), аудио-сэмпл `zvuki-razgovorov...mp3`.

## Ключевые сущности / точки входа
- **Монтирование статики** — в `app.py`: `/static` → `backend/static` (с `html=True`), `/js` → каталог JS. Отдельный маршрут `/static/voice_llm_interface.html`.
- **Лендинг `/`** — `app.py` отдаёт `backend/static/landing/index.html` (собран из `frontend/`).
- **Виджеты** — встраиваются клиентами на свои сайты; подключаются к `/ws/{assistant_id}` (и аналогам по провайдерам). `widget.js` — эталон протокола, на нём же основан Chrome-расширение.
- **Страницы кабинета** — каждая обычно дёргает свой `/api/...` через инлайн-скрипт или модуль из `js/`/`agents/`.

## Связи с другими частями проекта
- Используется: `app.py` (StaticFiles + явные маршруты), чат-виджеты встраиваются на внешние сайты, `chrome-extension/` опирается на `widget.js`.
- Использует: backend API (`/api/...`) и голосовые WebSocket'ы (`/ws/...`, `backend/websockets/`). Токен — в `localStorage`.

## На что обратить внимание
- **`landing/` — артефакт сборки.** Источник — `frontend/`; ручные правки в `landing/` затрутся при `npm run build`. Правьте React, не сборку.
- **Vanilla, а не React.** Все страницы кабинета — обычный HTML/JS без фреймворка и без сборки; логика часто инлайнится в `<script>`. Изменения деплоятся как есть.
- **Много легаси/дублей** (`*_old.html`, `index_original.html`, несколько gemini-виджетов, тестовые файлы) — перед правкой убедитесь, какой файл реально подключён со страницы.
- **Много вариантов виджетов по провайдерам** — общей абстракции нет; правки протокола нужно повторять в каждом.
- Шрифты/бинарники/иконки и `assets/` лендинга документировать не нужно — это статические ассеты.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../../frontend/claude-frontend.md` — исходники React-лендинга (источник `landing/`)
- `../api/claude-api.md` — API, который дёргают страницы
- `../websockets/claude-websockets.md` — WS-протокол виджетов
- `../../chrome-extension/claude-chrome-extension.md` — расширение на базе `widget.js`
- `../../claude-index.md` — корневой индекс
