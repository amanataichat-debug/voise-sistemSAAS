# backend/static — фронтенд приложения (vanilla HTML/JS), виджеты и собранный лендинг

## Назначение
Весь фронтенд, который отдаёт FastAPI напрямую через `StaticFiles` (монтируется в `app.py` на `/static` и `/js`). Здесь живут внутренние страницы кабинета (дашборд, управление ассистентами всех провайдеров, CRM, телефония, аналитика, настройки, админка) на чистом HTML/CSS/JS, встраиваемые голосовые виджеты (`*-widget.js`, `widget.js`) и собранный React-лендинг в `landing/`. Бизнес-логика страниц — в инлайновых скриптах и модулях `js/`, `agents/`, `conversations/`; backend они дёргают по `/api/...` и `/ws/...`.

## Состав (верхний уровень)
### HTML-страницы кабинета
- `dashboard.html` — дашборд пользователя.
- `agents.html` — OpenAI-ассистенты; `gemini-agents.html`, `grok-agents.html`, `fish-agents.html`, `eleven-agents.html`, `cartesia-agents.html`, `yandex-agents.html`, `cascade.html`, `translate.html` — страницы по провайдерам; `gemini-agents_old.html` — легаси.
- `fish-agents.html` — Fish-агенты на серверных ключах (карточка ключей убрана; статус сервера через `GET /api/fish-assistants/status`), кнопка «Тест» открывает `fish-test.html?id=<uuid>` — тот же `widget.js`, но со `data-ws-path="/ws/fish/"`. `widget.js` понимает атрибут `data-ws-path` (по умолчанию `/ws/`), так один виджет обслуживает все провайдеры с протоколом виджета.
- `eleven-agents.html` — ElevenLabs-агенты на серверном ключе (копия страницы Fish, API `/api/eleven-assistants`): селектор модели синтеза с ценой (`/options`), голос — `<select>` из аккаунта ElevenLabs (`/voices?language=`, рекомендованные для языка первыми, превью `<audio>`), панель «Найти в библиотеке» (`/voices/library`, поиск/пол/пагинация, «Добавить» → `/voices/library/add` и голос сразу выбран), язык (по умолчанию кыргызский, при смене список голосов перезагружается), стабильность (Creative/Natural/Robust), диалоговая модель. Приветствие по умолчанию на кыргызском. Кнопка «Тест» открывает `eleven-test.html?id=<uuid>` (`widget.js` с `data-ws-path="/ws/eleven/"`). Сайдбар всех страниц кабинета содержит и «Fish агенты», и «ElevenLabs агенты» (после «Яндекс агенты»).
- `telephony.html` — телефония через собственный SIP-шлюз, целиком на `/api/sip/*`: таблица номеров пользователя (`GET /api/sip/numbers`), модалка привязки к ассистенту OpenAI/Gemini/Fish/Eleven или к агенту обзвона (`PATCH /api/sip/numbers/{id}` с `assistant_type`+`assistant_id` или `agent_config_id`), форма исходящего звонка (`POST /api/sip/calls`, номер абонента проверяется на префиксы O! `050/070/099` и на странице, и в API) с живым статусом (опрос `GET /api/sip/calls/{id}` раз в 2 с, отбой `POST /api/sip/calls/{id}/hangup`), журнал звонков (`GET /api/sip/calls`). Номера заводит только админ через API. Верификации, баланса, покупки номеров и SMS больше нет.
- `outbound-calls.html`, `test_outbound-calls.html` — страницы Voximplant, **мёртвые** (Voximplant не используется, см. корневой `CLAUDE.md`).
- `crm.html`, `crm-contact.html` — CRM (список и карточка контакта).
- `conversations.html` — история диалогов.
- `telephony.html` — телефония (SIP-шлюз); `outbound-calls.html`, `test_outbound-calls.html` — старый обзвон Voximplant (мёртвые).
- `agent.html` — страница Voksy AI Agent (оркестратор/обзвон).
- `knowledge-base.html`, `integrations.html`, `settings.html`, `admin.html` — база знаний, интеграции, настройки, админка.
- `index.html`, `index_original.html` — входные/легаси страницы; `widget.html` — демо виджета.
- Юридические/контентные: `privacy-policy.html`, `public-offer.html`, `terms-of-service.html`, `payment-terms.html`, `prompts-wiki.html`, `api-docs.html`, и др.
- Тестовые: `test-ga-api.html`. `cascade-test.html` — заглушка-редирект на
  `cascade.html` (страница переименована; заглушка сохраняет query-параметры
  старых ссылок).

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
- `css/`, `images/` — стили и изображения (`logo.png` и т.п.).
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
