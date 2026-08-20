# backend/api — FastAPI-роутеры (HTTP + WebSocket эндпоинты)

## Назначение
Транспортный слой: все HTTP- и WebSocket-эндпоинты приложения. Каждый файл — это `APIRouter`, который регистрируется в `app.py` (строки ~168–199) с префиксом. Роутеры тонкие: валидируют вход (схемы из `backend/schemas/` или inline), проверяют доступ через зависимости из `backend/core/dependencies.py`, дергают `backend/services/` и отдают результат. Голосовые WS-роутеры лишь принимают соединение и делегируют в `backend/websockets/`.

## Состав
### Аутентификация и пользователи
- `auth.py` — `/api/auth` — регистрация, логин (JWT), сброс пароля, обработка UTM/рефералов при регистрации.
- `users.py` — `/api/users` — профиль, пользовательские API-ключи провайдеров, настройки.
- `email_verification.py` — `/api/email-verification` — отправка/проверка кода подтверждения email.

### Ассистенты по провайдерам (CRUD + embed)
- `assistants.py` — `/api/assistants` — OpenAI Realtime ассистенты.
- `gemini_assistants.py` — `/api/gemini-assistants` — Google Gemini Live.
- `grok_assistants.py` — `/api/grok-assistants` — xAI Grok Voice **и каскад-ассистенты**: `/cascade*` (CRUD, справочник TTS, кошелёк кредитов каскада). Каскад хранится в той же таблице, что и Grok, и различается `assistant_type='cascade'`; порядок роутов важен — все `/cascade/*` объявлены до `/{assistant_id}`. CRUD каскада и `/cascade/credits/balance` принимают авторизацию `get_current_user_flexible` (JWT или `X-Api-Key`), остальные роуты — только JWT.
- `cartesia_assistants.py` — `/api/cartesia-assistants` — Cartesia TTS.
- `translate_assistants.py` — `/api/translate-assistants` — ассистент синхронного перевода.
- `elevenlabs.py` — `/api/elevenlabs` — ElevenLabs-агенты (данные в основном на стороне ElevenLabs API).

### Голосовые WebSocket-эндпоинты (делегируют в backend/websockets/)
- `websocket.py` — `/ws/{assistant_id}`, `/ws/demo` — OpenAI Realtime (хендлер `handler_realtime_new`). Регистрируется ПОСЛЕ gemini_ws/translate_ws.
- `gemini_ws.py` — `/ws/gemini/{id}`, `/ws/gemini-31/{id}`, `/ws/gemini-browser/{id}`, `/ws/vox-gemini/{id}`, `/ws/llm-stream` — Gemini Live + текстовый LLM-стрим. Регистрируется ДО websocket.py.
- `grok_ws.py` — `/ws/grok/{id}`, `/ws/grok/voximplant/{id}`, `/ws/grok/custom/{id}` — Grok Voice.
- `translate_ws.py` — `/ws/translate/{id}` — перевод. Регистрируется ДО websocket.py.

### Voicyfy Agent v5.0 (автономный обзвон)
- `agent.py` — `/api/agent` — CRUD конфига агента, `/chat` (диалог владельца с оркестратором), `/stats`, `/orchestrator-models`, `/tasks`, `/contacts` (CRUD), `/calls`, `/phone-numbers`.
- `agent_telegram.py` — `/api/agent/telegram` — интеграция Telegram-бота агента (webhook, настройки).
- `credits.py` — `/api/credits` (префикс встроен) — `/balance`, `/packages`, `/transactions`, `/purchase`, `/subscribe`. Кредиты оркестратора.
- `llm_streaming.py` — префикс встроен (`/api/llm/...`) — `/stream`, `/models`, `/status`, CRUD `/agent-config`. Текстовый LLM + конфиг агента.

### Телефония
- `telephony.py` — `/api/telephony` — номера, настройка (`/setup`), статус, баланс, история звонков, покупка номеров, webhook-регистрация, анализ логов.
- `voximplant.py` — `/api/voximplant` (Production v3.9) — конфиг ассистента для сценария, `/functions/execute` (исполнение AI-функций из телефонии), `/webhook/transcript`, `/log`, запуск исходящего звонка, аналитика стоимости, пересчёт стоимости.
- `voximplant_settings.py` — `/api/users` (тот же префикс, что users) — настройки Voximplant пользователя.

### CRM, диалоги, база знаний, файлы
- `contacts.py` — `/api/contacts` — CRM-контакты и заметки.
- `conversations.py` — `/api/conversations` — история и аналитика диалогов.
- `knowledge_base.py` — `/api/knowledge-base` — база знаний (Pinecone).
- `files.py` — `/api/files` — загрузка/управление файлами ассистента (R2).
- `functions.py` — `/api/functions` — список доступных AI-функций для UI (из реестра `backend/functions/`).
- `function_logs.py` — логи вызовов AI-функций. ⚠️ В `app.py` напрямую НЕ зарегистрирован — проверяйте подключение.
- `embeds.py` — встраиваемые виджеты (`/embed/{embed_code}` отдаёт HTML), CRUD embed-конфигов. Префикс задаётся внутри.

### Подписки, платежи, партнёры
- `subscriptions.py` — `/api/subscriptions` — планы подписки, `/my-subscription`, `/assistants-usage` (единый расход лимита ассистентов по всем провайдерам — этим эндпоинтом пользуются все страницы агентов и дашборд).
- `subscription_logs.py` — `/api/subscription-logs` — лог событий подписки.
- `subscription_status.py` — `/check-access`, `/force-check`. ⚠️ В `app.py` напрямую НЕ зарегистрирован — проверяйте подключение.
- `payments.py` — `/api/payments` — планы (цены из БД, в сомах KGS), создание платежа Finik (302 → `payment_url`), **`/finik-webhook`** (webhook платёжки Finik — подпись, идемпотентность, сверка суммы), success-страница (только UX), статус, `/config-check`.
- `partners.py` — `/api/partners` — реферальная программа, генерация ссылок, комиссии.

### Прочее
- `admin.py` — `/api/admin` — админ-панель (требует привилегированный доступ). Помимо управления пользователями/подписками содержит аналитику оркестратора: `/agent-usage` (расход кредитов по пользователям за сегодня МСК/7/30 дней), `/users/{id}/agent-usage` (дневная серия, разбивки по ref_type/моделям, агенты, воронка, транзакции), блок `orchestrator` в `/stats`.
- `healthcheck.py` — health-эндпоинты для деплой-платформ.
- `__init__.py` — агрегирует роутеры для импорта в `app.py`.

## Ключевые сущности / точки входа
- **Webhooks (внешние POST'ы, без JWT):** `payments.py` `/api/payments/finik-webhook` (Finik, проверка RSA-подписи по публичному ключу), `voximplant.py` `/api/voximplant/webhook/transcript` и `/log`, `agent_telegram.py` Telegram webhook. Эти эндпоинты — точки входа извне, к ним особое внимание по безопасности.
- **Аутентификация:** `auth.py` (`/register`, `/login`) выдаёт JWT; остальные роутеры защищены зависимостью `get_current_user` из `core/dependencies.py`, плюс гейты по подписке/лимитам.
- **Голосовые WS:** единственная их работа — принять соединение и вызвать соответствующий handler из `backend/websockets/` (см. его доку).
- **Агент:** `agent.py` `/chat` запускает `ChatOrchestrator`; `/create` собирает `AgentConfig` из документов-промптов; `credits.py` управляет балансом, на котором держится весь обзвон.
- **Telephony vs Voximplant:** `telephony.py` — высокоуровневое управление номерами/настройкой пользователя; `voximplant.py` — низкоуровневая интеграция со сценариями Voximplant и обработка их колбэков.

## Связи с другими частями проекта
- Используется: `app.py` (регистрация всех роутеров, монтирование статики).
- Использует: `backend/services/*` (вся бизнес-логика), `backend/models/*`, `backend/schemas/*` (валидация; часть роутеров — inline-схемы/dict), `backend/core/*` (`dependencies`, `security`, `config`, `logging`), `backend/websockets/*` (голосовые роутеры), `backend/functions/*` (`functions.py`, `voximplant.py`).

## На что обратить внимание
- **Порядок include_router критичен.** `gemini_ws` и `translate_ws` регистрируются ДО `websocket` (строки 176–178 `app.py`), иначе `/ws/llm-stream`, `/ws/gemini/*`, `/ws/translate/*` перехватит роут `/ws/{assistant_id}`. В `websocket.py` есть явная проверка на эту коллизию.
- **Встроенные префиксы.** `credits.py`, `llm_streaming.py`, `embeds.py`, `*_ws.py` объявляют пути целиком внутри роутера (в `app.py` подключены без `prefix=` или с тегами). Не добавляйте префикс повторно.
- **Незарегистрированные роутеры.** `function_logs.py` и `subscription_status.py` присутствуют в папке, но в списке `app.py` их нет — это либо легаси, либо подключаются иначе; не считайте их эндпоинты живыми без проверки.
- **Платёжка — Finik (finik.kg, QR-эквайринг, валюта KGS)**. Webhook `/api/payments/finik-webhook` проверяет RSA-подпись и идемпотентность — критичный для безопасности путь.
- **Дублирование по провайдерам.** Пять почти одинаковых `*_assistants.py` — общие правки нужно вносить во все. Объединённой абстракции нет.
- **`voximplant.py` большой** (v3.9, ~2000 строк) — ищите конкретный эндпоинт, не читайте целиком.
- Привилегированные email'ы и спец-лимиты зашиты в `core/dependencies.py` — влияют на доступ к `admin.py` и обход проверок подписки.
- Создание ассистентов у всех провайдеров закрыто зависимостью `check_assistant_limit` / `check_assistant_limit_flexible`; лимит общий на все типы. Добавляя нового провайдера, не забудь повесить её на его POST-роут.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../websockets/claude-websockets.md` — голосовые хендлеры (делегаты `*_ws.py`)
- `../services/claude-services.md` — бизнес-логика за роутерами
- `../models/claude-models.md` — ORM-модели
- `../schemas/claude-schemas.md` — Pydantic-схемы запросов/ответов
- `../core/claude-core.md` — зависимости аутентификации и доступа
- `../functions/claude-functions.md` — реестр AI-функций (`functions.py`)
- `../../claude-index.md` — корневой индекс
