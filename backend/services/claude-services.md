# services/ — слой бизнес-логики между API/WebSocket-ами и моделями

## Назначение
`backend/services/` содержит сервисный слой Voksy AI: вся доменная логика, которая не должна жить в роутерах FastAPI или в ORM-моделях. Здесь же находится «мозг» автономного обзвона — оркестратор Voksy AI Agent (PreCall / PostCall / Chat), кредитный/биллинговый учёт, интеграции с внешними API (OpenRouter, OpenAI, Robokassa, Pinecone, Cloudflare R2, Voximplant Partner, Telegram, Google Sheets, ElevenLabs, SMTP). Сервисы вызываются из `backend/api/`, `backend/websockets/`, `backend/functions/` и фоновых планировщиков из `backend/core/`. Большинство сервисов — классы со `@staticmethod`/`@classmethod` либо синглтоны; собственного состояния обычно не держат, работают через переданную `Session`.

## Состав

### Подсистема Voksy AI Agent v5.0 (оркестратор автономного обзвона)
- `agent_models.py` — справочник `ORCHESTRATOR_MODELS` (slug'и OpenRouter). Ставки списания НЕ хардкодятся: у каждой модели задаётся её себестоимость в $ за 1k токенов, а `_rates()` считает кредиты по формуле `$/1k × ORCHESTRATOR_MARGIN × USD_RUB / CREDIT_PRICE_RUB` (≈×1900.63), плюс `credits_per_call` и `tier` для показа цены в UI. Helpers: `get_default_model`, `is_valid_model`, `get_model_rates`, `resolve_slug` (+ карта `LEGACY_MODEL_ALIASES` для снятых с OpenRouter слагов). Подробности — `docs/credits_system.md`.
- `agent_prompts.py` — захардкоженные промпты оркестратора и голосового агента; `build_orchestrator_prompt(agent_config)` собирает системный промпт из «документов» компании (кто мы / кому звоним / как говорим / что предлагаем / правила).
- `agent_orchestrator.py` — три фазы оркестратора: `PreCallOrchestrator` (стратегия перед звонком), `PostCallOrchestrator` (анализ транскрипта после звонка, решение SUCCESS/FOLLOWUP/NO_ANSWER, перезвон), `ChatOrchestrator` (диалог владельца с агентом в чате/Telegram). Каждая фаза имеет ветки v3 (OpenRouter) и legacy v2 (OpenAI Responses API).
- `agent_tools.py` — определения tools для агента (`AGENT_CHAT_TOOLS`, `AGENT_POSTCALL_TOOLS`), их реализации (create/get контактов, задач, статистики, память контакта, Telegram-уведомление) и диспетчер `execute_tool`; конвертер `to_chat_completions_tools` (Responses API → Chat Completions/OpenRouter).
- `agent_telegram_service.py` — весь Telegram Bot API агента (REST через httpx): отправка сообщений во все chat_id агента, обработка входящих (`process_telegram_message`), конвертер `markdown_to_telegram_html`, генерация webhook-секрета. Бот агента служит и фронтендом для chat, и каналом уведомлений PostCall.

### Кредиты / биллинг / подписки
- `credit_service.py` — атомарный учёт кредитов (`users.credits_balance`) через `SELECT ... FOR UPDATE`; `precheck`, `calculate_cost`, `charge`, гранты (trial/subscription/purchase), `refund`, `manual_adjust`; исключения `InsufficientCreditsError`, `SubscriptionExpiredError`, `SubscriptionRequiredError`; `activate_agent_trial`.
- `cascade_credit_service.py` — второй, независимый кошелёк: кредиты каскад-ассистентов (`users.cascade_credits_balance`, транзакции с `product='cascade'`). Не связан ни с кредитами оркестратора, ни с подпиской `agent` — доступен на всех тарифах, включая free. `calculate_cost`, `charge`, `grant_trial` (разовый стартовый пакет), `grant_purchase`, `manual_adjust`, `get_balance`, `get_transactions`. Гейт по балансу стоит не в CRUD, а на старте звонка (`telephony.py`: outbound-config / config).
- `assistant_limit_service.py` — единый подсчёт ассистентов пользователя по всем таблицам провайдеров (OpenAI/Gemini/Grok/Cascade/Cartesia/Yandex/Translate) для лимита тарифа: `count_user_assistants`, `get_assistants_breakdown`, `get_assistants_usage`. Голосовые ассистенты мастера Voksy AI Agent исключаются по внешним ключам в `agent_configs`. Используется `core/dependencies.enforce_assistant_limit` и `GET /api/subscriptions/assistants-usage`.
- `subscription_blocker.py` — фоновый раннер (каждые 5 мин): блокирует истёкшие подписки тарифа `agent` и отменяет их SCHEDULED-задачи. Кредиты не сгорают.
- `subscription_service.py` — управление планами подписки, активация триалов (в т.ч. реферальных), лог событий подписки.
- `payment_service.py` — `RobokassaService`: формирование платёжных ссылок, проверка подписи, расчёт длительности подписки по сумме/периоду, интеграция с партнёрскими комиссиями. ⚠️ Провайдер — Robokassa (а не YooKassa, как указано в корневом CLAUDE.md).
- `partner_service.py` — партнёрская/реферальная программа: генерация кодов, реферальные связи, начисление комиссий.
- `voximplant_partner.py` — `VoximplantPartnerService`: Voximplant Partner API (дочерние аккаунты, SubUsers, верификация/биллинг-ссылки, номера телефонов, сценарии/правила маршрутизации, исходящие звонки, Service Account JWT для secure-записей). Самый крупный файл папки.

### Ассистенты / диалоги / контент
- `assistant_service.py` — CRUD OpenAI-ассистентов (`AssistantConfig`), генерация embed-кода.
- `conversation_service.py` — трекинг и анализ диалогов; нормализация телефонов, направление звонка, авто-создание CRM-контактов, поддержка OpenAI/Gemini/Cartesia ассистентов.
- `elevenlabs_service.py` — клиент ElevenLabs API (агенты, документы базы знаний/RAG, кеширование).
- `function_log_service.py` — запись логов вызовов AI-функций (`FunctionLog`).
- `integration_service.py` — CRUD интеграций ассистента.

### Внешние хранилища / поиск / уведомления
- `pinecone_service.py` — векторный поиск базы знаний: инициализация Pinecone, эмбеддинги OpenAI, чанкинг текста, создание/удаление namespace.
- `r2_storage.py` — Cloudflare R2 (S3-совместимое): загрузка записей звонков, secure-записи Voximplant через JWT (`VoximplantAuth`), удаление/листинг.
- `telegram_notification.py` — уведомления о завершённых звонках в Telegram (диалог, длительность, стоимость, ссылка на запись); `send_call_notification_safe`.
- `webhook_notification.py` — POST-уведомления `conversation.completed` на URL клиента (телефония + веб-чат); `send_webhook_safe`. Без HMAC-подписи.
- `notification_service.py` — внутренние уведомления о состоянии подписки (скоро истекает и т.п.).
- `email_service.py` — SMTP: коды подтверждения email, JWT-токены верификации.
- `google_sheets_service.py` — запись строк в Google Sheets (service account), детальное логирование, трекинг номера звонящего.

### Пользователи / файлы / стриминг
- `auth_service.py` — регистрация/логин, JWT, обработка UTM и реферальных кодов при регистрации.
- `user_service.py` — управление аккаунтом и пользовательскими API-ключами (gemini/grok/elevenlabs).
- `file_service.py` — загрузка/обработка файлов базы знаний (валидация расширений/типов).
- `browser_agent_service.py` — ⚠️ несмотря на имя, это LLM-стриминг-сервис: стриминг текстовых ответов OpenAI Chat API на фронтенд через WebSocket во время голосового взаимодействия Gemini (`get_browser_agent_service`, `stream_response`, события `llm.stream.*`). Реального браузера/Playwright не использует.
- `openrouter_client.py` — тонкий async-клиент OpenRouter (`chat_completion`) на системном ключе `settings.OPENROUTER_API_KEY`; синглтон `get_openrouter_client`.
- `llm_streaming/` — отдельный пакет low-latency стриминга OpenAI Chat для функции `query_llm` (см. дочернюю доку).

### Собственная SIP-телефония
- `sip_gateway_service.py` — очередь исходящих `sip_calls` (`FOR UPDATE SKIP LOCKED`), применение событий моста (`apply_bridge_event`, requeue до 6 попыток), обновление `Task`/`AgentCall`, простановка номера/направления в `conversations`/`gemini_conversations` (`tag_conversations`), выбор приветствия. Подробно: `infra/sip-gateway/claude-sip-gateway.md`.
- `conversation_service.save_conversation(assistant_type="gemini")` пишет в `gemini_conversations`, а не в `conversations` (там FK на `assistant_configs`).

## Ключевые сущности / точки входа

- **Оркестратор:** `PreCallOrchestrator.run(...)`, `PostCallOrchestrator.poll_and_run(...)` / `.run_for_scheduled_telegram(...)` (исполнение Task с channel="telegram": один прогон v3, составляет и отправляет сообщение с личного TG-аккаунта, `call_direction="telegram_outbound"`, без инкремента attempts_count и авто-стадии), `ChatOrchestrator.run(...)` / `.run_telegram(...)`. Все три выбирают ветку v3 (OpenRouter, `_run_v3_openrouter` / `_analyze_v3_openrouter` / `_run_telegram_v3`) или v2 (OpenAI Responses API, `store=True`). Usage токенов достаётся через `_extract_usage`.
- **Tools агента:** `execute_tool(tool_name, tool_args, context, db)` — единая точка диспетчеризации; `context` обязан содержать `agent_config_id`, `user_id`, `user`. PostCall после исходящего звонка ВСЕГДА планирует следующее касание (перезвон `create_agent_task` или отложенное сообщение `schedule_telegram_message` — только при подключённом личном TG-аккаунте), кроме случая достижения цели; `fn_create_agent_task` отменяет дубли SCHEDULED-задач этого контакта (в пределах одного канала). `schedule_telegram_message` создаёт `Task(channel="telegram")`: в `description` — инструкция, текст составляется при отправке; рабочие часы не применяются.
- **Кредиты:** `CreditService.precheck(db, user)` (gate: тариф `agent` + активная подписка + баланс ≥ `MIN_PRECHECK_BALANCE=100`), `CreditService.charge(...)` (атомарное списание, в ноль а не в минус, при нехватке помечает `partial`), `CreditService.calculate_cost(model_slug, prompt, completion)` (минимум 1 кредит).
- **Платежи:** `RobokassaService` (классовые константы `MERCHANT_LOGIN`/`PASSWORD_1`/`PASSWORD_2`, `PAYMENT_URL`, `RESULT_URL`), `get_subscription_days_by_amount`, `get_subscription_days_by_duration`.
- **Voximplant:** `VoximplantPartnerService` + синглтон `get_voximplant_partner_service()`.
- **R2:** `R2StorageService.upload_recording/delete_recording/list_recordings`, `is_configured()`, `VoximplantAuth` (JWT для secure-URL).
- **Pinecone:** `PineconeService.create_or_update_knowledge_base`, `create_embeddings`, `delete_knowledge_base`.
- **OpenRouter:** `OpenRouterClient.chat_completion(model, messages, tools, ...)`.
- **Telegram-нотификации звонков:** `TelegramNotificationService.send_call_notification(...)` / `send_call_notification_safe(...)`.
- **Прочие сервисы** в основном экспонируют статические async-методы CRUD-вида (`AssistantService`, `ConversationService`, `UserService`, `SubscriptionService`, `PartnerService`, `EmailService`, `FileService`, `IntegrationService`, `FunctionLogService`, `NotificationService`).
- **Реэкспорт:** часть сервисов агрегируется в `__init__.py` (`AuthService`, `UserService`, `R2StorageService`, `VoximplantPartnerService`, `TelegramNotificationService`, `WebhookNotificationService` и др.).

## Связи с другими частями проекта
- **Используется:** `backend/api/` (роутеры дергают сервисы), `backend/websockets/` (handler'ы голоса вызывают conversation/telegram/webhook/credit-сервисы), `backend/functions/` (модульные AI-функции используют `pinecone_service`, `google_sheets_service`, `llm_streaming`, `telegram_notification`), `backend/core/` (планировщики `scheduler`, `task_scheduler`, фоновый `subscription_blocker`).
- **Использует:** `backend/models/` (User, AgentConfig, AgentContact, AgentCall, Task, Conversation, Subscription/Plan, CreditTransaction, CreditPackage, Partner и др.), `backend/core/` (`config.settings`, `logging`, `db.session.SessionLocal`), `backend/schemas/` (Pydantic-схемы запросов/ответов), внешние API: OpenRouter, OpenAI (Realtime/Responses/Chat/Embeddings), Pinecone, Cloudflare R2, Robokassa, Voximplant Partner, Telegram Bot API, Google Sheets, ElevenLabs, SMTP.

## На что обратить внимание
- **Ключи и конфиг:** `OPENROUTER_API_KEY` (оркестратор), `OPENAI_API_KEY`, `PINECONE_API_KEY`/`PINECONE_ENVIRONMENT`, `ROBOKASSA_MERCHANT_LOGIN`/`PASSWORD_1`/`PASSWORD_2`, R2-креды, Voximplant Partner-креды. Часть берётся из `settings` (Pydantic), часть — напрямую из `os.environ` (Pinecone).
- **Платёжный провайдер:** код реализует **Robokassa**, а не YooKassa (расхождение с корневым CLAUDE.md). Подпись MD5/HMAC проверяется в `RobokassaService`.
- **Именование-ловушка:** `browser_agent_service.py` — это LLM-стриминг (OpenAI Chat → WebSocket), а НЕ управление браузером. Реальная браузерная задача живёт в функции `backend/functions/start_browser_task.py`.
- **Версионность оркестратора:** в коде сосуществуют ветки v2 (OpenAI Responses API, `store=True`) и v3 (OpenRouter). При правках следить, какая ветка активна для агента (зависит от модели/конфига). PostCall ищет транскрипт по номеру телефона + временному окну, а не по `session_id` (изменение v2.1).
- **Инварианты кредитов:** все мутации баланса строго через `SELECT ... FOR UPDATE`; каждая операция фиксируется в `credit_transactions` (источник правды). `charge` никогда не уводит баланс в минус и не бросает исключений (оркестратор уже отработал). Гранты trial идемпотентны (по типу транзакции).
- **Блокировка подписок:** `subscription_blocker` отменяет SCHEDULED agent-задачи истёкших подписок, но НЕ списывает кредиты. Админ освобождён от проверок подписки, но НЕ от проверки баланса.
- **Telegram:** агентский Telegram (`agent_telegram_service`) — это отдельный механизм от уведомлений о звонках (`telegram_notification`). Оба используют только REST через httpx, без python-telegram-bot. Markdown-таблицы в Telegram запрещены (узкий экран) — конвертеры это учитывают.
- **Размер файлов:** `voximplant_partner.py` (~75 КБ) и `elevenlabs_service.py` (~51 КБ) — крупные; перед правкой искать конкретный метод, а не читать целиком.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `./llm_streaming/claude-llm-streaming.md` — дочерняя
- смежные: `../api/claude-api.md`, `../models/claude-models.md`, `../functions/claude-functions.md`, `../core/claude-core.md`, `../websockets/claude-websockets.md`, `../schemas/claude-schemas.md`, `../../claude-index.md`
