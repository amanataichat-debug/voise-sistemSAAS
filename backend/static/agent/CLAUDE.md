# Voicyfy Agent — фронтенд страницы `/static/agent.html`

Дашборд **автономного агента для обзвонов** (не путать с `agents.html` — страницей
управления OpenAI-ассистентами, у которой своя папка `agents/`).

Голосовой ассистент агента — один из шести провайдеров:
`gemini | openai | cartesia | yandex | cascade | fish` (`AgentConfig.assistant_type`).
Каскад работает на серверном ключе OpenAI (gpt-realtime-2.1-mini) + VoxTTS, оплата —
кредитами каскада (`users.cascade_credits_balance`); хранится в
`grok_assistant_configs (assistant_type='cascade')`, исходящие идут через отдельный
rule `outbound_cascade` (цепочка с `vox-turn-taking`).
Fish — половинный каскад на ключах пользователя (OpenAI Realtime ведёт диалог,
озвучивает Fish Audio через прокси `/ws/fish/tts/{id}`); хранится в
`fish_assistant_configs`, голос задаётся `fish_voice_id` (reference_id из
библиотеки fish.audio), исходящие идут через rule `outbound_fish`.

Эта папка (`backend/static/agent/`) содержит результат разбиения исходного
монолитного `agent.html` (~3700 строк) на стили + доменные скрипты.

- **Прод-URL:** `https://voicyfy.ru/static/agent.html`
- **Как раздаётся:** статикой через `app.mount("/static", StaticFiles(...))` в `app.py`.
  Отдельного backend-роута для самой страницы нет — это чистый фронтенд.

---

## 1. Архитектура разбиения

`agent.html` теперь — только разметка (`<body>`: nav, wizard, 3-колоночный layout,
mobile drawer, ~13 модалок). Стили вынесены в `agent.css`, логика — в 14 JS-файлов.

### Важно: это КЛАССИЧЕСКИЕ скрипты, не ES-модули
- Подключаются обычными `<script src>` (без `type="module"`).
- **Все функции и top-level `let/const` — глобальные** и видны между всеми
  файлами `agent/*.js` (классические скрипты делят одно глобальное лексическое
  окружение) и из inline-`onclick="fn()"` в разметке.
- Поэтому **разнести логику можно было без единой правки 63 inline-onclick**.

### Правила, которые НЕЛЬЗЯ нарушать
1. **Каждое имя `let/const` объявляется ровно один раз** во всём наборе файлов.
   Дубликат → `SyntaxError: redeclaration` и вся страница падает.
2. **Каждая функция определяется один раз.** Имена — глобальные, коллизии молча
   перетирают друг друга.
3. **Порядок подключения:** `core.js` первым, `init.js` последним. Между ними
   порядок не критичен (функции вызываются в runtime, не на загрузке), но менять
   без нужды не стоит. Список и порядок — в конце `agent.html`.
4. Любую новую глобальную переменную состояния клади в файл её домена (или в
   `core.js`, если шарится между доменами) — и только туда.

---

## 2. Карта файлов

| Файл | Домен | Основные функции / состояние | Backend |
|------|-------|------------------------------|---------|
| `agent.css` | Все стили страницы | — | — |
| `core.js` | Ядро + общие хелперы. **Грузится первым.** | `apiFetch`, `withAgentId`, `handle402`, `getToken`; константы `API`, `TG_API`, `CREDITS_API`, `STAGE_META`, `STAGE_ORDER`, `VOICE_META`*; состояние `agentData`, `currentAgentId`, `agentsList`, `creditsState`…; хелперы `esc`, `fmtDate`, `mskParts`, `relTime`, `pluralRu`, `mskInputToUtc`, `utcToMskInput`, `renderMarkdown`, `showToast`, `stageBadge`, `decisionRu/Badge`, `taskChannelBadge` (бейдж Telegram у задач с `channel="telegram"`), `errText`, `downloadAuthedFile`; цена моделей оркестратора — `fmtCredits`, `modelCallCost`, `modelOptionsHtml`, `modelHintHtml` (используются обоими селектами модели — в модалке настроек и в визарде; только текст, без цветовой индикации) | — |
| `credits.js` | Кредиты и подписка оркестратора | `loadCredits`, `renderCreditsBadge`, `onSubAction`, `openCreditsModal`, `purchasePackage`, `subscribeAgent`, `openBillingModal`, `submitRobokassaForm` | `/api/credits/*` |
| `agent-switcher.js` | Мультиагент (v3.1) | `initAgents`, `renderAgentSwitcher`, dropdown, `loadCurrentAgent`, `selectAgent`, `openNewAgentWizard`, `showDashboard`, `renderAgentHeader`, `renderDocsGrid`, `deleteAgent` | `/api/agent` (`/list`, `/`, `/create`) |
| `dashboard.js` | Раскладка/drawer + дашборд | `applyLayout`, `openDrawer`, `closeDrawer`, `_collectMigrations`, `loadStats`, `loadRecentCalls`, `loadTasks` | `/api/agent/stats`, `/calls`, `/tasks` |
| `tasks-calendar.js` | Календарь задач (модалка) | `openTasksCalendar`, `tcalDeleteAllTasks`, `tcalDeleteDayTasks`, семейство `_tcal*` | `/api/agent/tasks` (GET/DELETE bulk), `/tasks/{id}` |
| `chat.js` | Чат с оркестратором (стриминг) + голосовой ввод (STT) | `sendMessage`, `handleStreamEvent`, `createStreamingBubble`, `addAgentBubble`, `newChat`, `renderWelcome`, `suggestionClick`, `showTyping`, `toggleRecording`, `onRecordingStop` (запись с микрофона → распознанный текст в поле ввода) | `/api/agent/chat`, `/chat/stream`, `/chat/clear`, `/transcribe` |
| `instructions-voice.js` | Edit-модалка + инструкции + выбор голоса | `openEditModal`, `saveEdit`, `openInstructionsModal`, `saveInstructions`, `voiceControlHtml`, `readVoiceBody`, `loadModels`, `loadPhoneNumbers`, `fillCallerIdSelect`, `toggleActive` | `/api/agent/` (PUT), `/orchestrator-models`, `/phone-numbers` |
| `telegram.js` | Telegram-бот агента | `loadTelegramStatus`, `renderTelegramModal`, `connectTelegramBot`, `toggleTelegramEnabled`, `addTelegramChat`, `testTelegram`, `disconnectTelegramBot` | `/api/agent/telegram/*` |
| `telegram-account.js` | ЛИЧНЫЙ Telegram-аккаунт агента (MTProto): трёхшаговая модалка (телефон → код → пароль 2FA), тумблер автоответа, охват contacts/all. Строка живёт в списке коннекторов — `connectors.js` зовёт `tgAccountConnectorRowHtml`/`tgAccountSummaryHtml`/`tgAccountAvailable` | `loadTgAccount`, `openTgAccountModal`, `renderTgAccountModal`, `tgAccStart/VerifyCode/VerifyPassword/Restart`, `tgAccToggleAutoReply`, `tgAccSetScope`, `tgAccDisconnect`; состояние `tgAccState`, константа `TG_ACC_API` | `/api/agent/telegram-account/*` |
| `contacts.js` | CRM-контакты | `saveContact`, `openContactsListModal`, `loadContactsList`, `openContactDetailsModal`, `renderContactDetails`, `cdTask*`, `_cdSmsBubble` (SMS-переписка в карточке), `saveContactInfo`, `changeContactStage` | `/api/agent/contacts*` |
| `calls.js` | История звонков (модалка) | `openCallsModal`, `renderCallExpanded` (channel-aware: `call.channel` sms/telegram/call; запланированная отправка — `postcall_log.call_direction === 'telegram_outbound'`, бейджи «Отправлено/Без отправки», подпись «Инструкция запланированного сообщения»), `toggleCallCard`; `POSTCALL_TOOL_LABELS` (все тулы PostCall, вкл. `schedule_telegram_message`) | `/api/agent/calls`, `/calls/{id}` |
| `history.js` | История работы агента (правая колонка): лента последних 100 фоновых задач PreCall/PostCall — звонки + обработка входящих SMS, с раскрытием в `renderCallExpanded` | `loadAgentHistory`; константа `AGENT_HISTORY_LIMIT` | `/api/agent/calls?limit=100` |
| `import.js` | Импорт контактов (xlsx/csv, 3 шага) | `openImportModal`, `handleImportFile`, `renderImportPreview`, `onImportTasksToggle` (ползунок авто-задач), `executeImport`, `finishImport`; состояние `importState` | `/api/agent/contacts/import/*` (execute: `create_tasks`) |
| `pipeline.js` | Воронка-канбан (drag&drop) | `openPipelineModal`, `loadPipeline`, `pipelineCard`, `plDragStart/End/Over/Leave`, `plDrop` | `/api/agent/pipeline`, `PATCH /contacts/{id}/status` |
| `knowledge-base.js` | База данных (векторная БД Pinecone) | `loadKnowledgeBaseStatus`, `renderKnowledgeBaseBlock`, `openKnowledgeBaseModal`, `saveKnowledgeBase`, `deleteKnowledgeBase`; состояние `knowledgeBaseState` | `/api/agent/knowledge-base` (GET/POST/DELETE) |
| `connectors.js` | Внешние коннекторы (Google Календарь, Gmail через Composio) | `loadConnectors`, `renderConnectorsBlock`, `openConnectorsModal`, `renderConnectorsList`, `connectConnector`, `disconnectConnector`; состояние `connectorsState`, `CONNECTOR_META`. OAuth-возврат ловится через `postMessage` и `?connector=&status=` | `/api/agent/connectors` (GET / `{toolkit}/connect` POST / `callback` GET / `{toolkit}` DELETE) |
| `onboarding.js` | Обучающая карусель перед мастером (5 слайдов про суть автономного агента). Показывается из `showWizard()` всегда при создании, с «Пропустить». | `startOnboarding(onDone)`, `renderOnboarding`, `obNext/obBack/obSkip`, `finishOnboarding`; состояние `obStep`, `OB_SLIDES` | — |
| `wizard.js` | Мастер создания агента (9 шагов 0..8) | `showWizard` (→ `startOnboarding` → `openWizardSteps`), `renderWizard`, `renderStep0`, `saveWizardKeys`, `submitCreate`, `renderCreation`, `persistWizard`; состояние `wizardData`, `wizardStep` | `/api/agent/create` |
| `init.js` | **Точки входа. Грузится последним.** | главный `DOMContentLoaded`, `window 'focus'` (рефреш кредитов), `keydown Esc` (закрыть модалки) | — |

\* `VOICE_META`/`OPENAI_VOICES`/`GEMINI_VOICES` физически лежат в `instructions-voice.js`
(рядом с UI выбора голоса); прочие общие константы — в `core.js`.

---

## 3. Поток данных и общие механизмы

Всё в `core.js`:

- **`apiFetch(url, options)`** — единственная обёртка над `fetch`. Делает:
  - подставляет `Authorization: Bearer <token>` из `localStorage.auth_token`;
  - `401` → чистит токен и редиректит на `/static/login.html`;
  - `402` → `handle402()` (см. ниже);
  - прогоняет URL через `withAgentId()`.
- **`withAgentId(url)`** — для мультиагентности добавляет `?agent_id=<currentAgentId>`
  ко всем запросам к `/api/agent*`, **кроме** `/list` и `/create` (там агент ещё не
  выбран/создаётся). Если агент не выбран — URL без изменений.
- **`handle402(resp)`** — биллинг-гейты:
  - `subscription_expired` / `subscription_required` → предложение перейти к тарифам;
  - `insufficient_credits` → открыть модалку пополнения (`openCreditsModal`).
- **`currentAgentId`** — выбранный агент; задаётся в `agent-switcher.js`
  (`selectAgent`/`initAgents`), читается через `withAgentId`.

---

## 4. Backend-связки

| Роутер | Префикс | Файл backend |
|--------|---------|--------------|
| Основной API агента | `/api/agent` | `backend/api/agent.py` |
| Telegram-бот агента | `/api/agent/telegram` | `backend/api/agent_telegram.py` |
| Личный Telegram агента | `/api/agent/telegram-account` | `backend/api/agent_telegram_account.py` |
| База данных (Pinecone) | `/api/agent/knowledge-base` | `backend/api/agent.py` |
| Кредиты оркестратора | `/api/credits` | `backend/api/credits.py` |

Регистрация роутеров — в `app.py` (`include_router`).

### Зеркала, которые надо держать синхронными
- **`STAGE_META` / `STAGE_ORDER`** (`core.js`) ↔ `backend/core/pipeline_stages.py`
  — фиксированный набор стадий воронки. Меняешь стадии на бэке — поправь и здесь.
- **`VOICE_META` / `OPENAI_VOICES` / `GEMINI_VOICES` / `YANDEX_VOICES` / `CASCADE_VOICES`**
  (`instructions-voice.js`) ↔ списки голосов в `backend/api/agent.py` (см. комментарий
  в нём: «Доступные голоса по провайдерам (должны совпадать со списками в agent.html)»).
  У Cartesia и Fish списка голосов нет — там текстовое поле с id голоса провайдера;
  `FISH_LATENCY_MODES` ↔ одноимённый список в `backend/models/fish_assistant.py`.

### Внешние зависимости (в `<head>` agent.html)
- `marked` + `dompurify` — рендер markdown в чате (`renderMarkdown` в `core.js`).
  Подключены до скриптов агента, доступны на момент вызова.
- Font Awesome, шрифт Syne.

---

## 5. Как добавлять функциональность

1. **Новая фича в существующем домене** → правь соответствующий файл из таблицы §2.
2. **Новый домен** → создай `agent/<домен>.js`, добавь `<script src>` в `agent.html`
   (перед `init.js`), задокументируй в §2. Состояние домена объявляй в этом файле.
3. **Новая разметка/модалка** → добавляй в `agent.html` (разметка осознанно НЕ
   выносилась в шаблоны — она читаемая и правится по месту).
4. **Новый общий хелпер**, нужный нескольким доменам → в `core.js`.
5. Для inline-обработчиков (`onclick="fn()"`) функция должна быть **глобальной**
   `function fn(){…}` в любом из `agent/*.js` — этого достаточно.

## 6. Чек-лист после правок
- Нет дублей имён `let/const` и функций между файлами.
- `node --check agent/<файл>.js` проходит (или просто проверь в браузере — консоль
  не должна ругаться на `redeclaration`/`is not defined`).
- Открыть `/static/agent.html`, проверить: загрузка дашборда, переключение
  агентов, чат, открытие основных модалок (звонки, контакты, воронка, импорт,
  настройки, telegram), wizard создания.
