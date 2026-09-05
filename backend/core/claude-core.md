# backend/core — ядро приложения: конфигурация, безопасность, зависимости, планировщики

## Назначение
Базовый инфраструктурный слой backend. Здесь живут Pydantic-настройки из env (`config.py`), JWT и хеширование паролей (`security.py`), переиспользуемые FastAPI-зависимости для аутентификации и проверки подписок/лимитов (`dependencies.py`), настройка логирования (`logging.py`) и два фоновых планировщика — проверка истёкших подписок (`scheduler.py`) и автоматический запуск запланированных звонков (`task_scheduler.py`). Почти весь остальной код проекта импортирует `settings` и `get_logger` отсюда.

## Состав
- `config.py` — класс `Settings(BaseSettings)` и глобальный экземпляр `settings`. Загружает env (через `dotenv`), валидирует HOST_URL, DATABASE_URL, Finik (мягко), Voximplant Partner, Cloudflare R2, Email. Печатает диагностику конфигурации при импорте.
- `security.py` — JWT (`create_jwt_token`, `decode_jwt_token`), хеширование пароля SHA-256 (`hash_password`, `verify_password`), FastAPI-зависимость `get_current_user_id` (HTTPBearer).
- `dependencies.py` — зависимости уровня запроса: `get_current_user` (JWT), `get_current_user_flexible` (JWT **или** персональный API-ключ `X-Api-Key`), `get_assistant_by_id`, `check_admin_access`, проверки подписки и лимитов ассистентов.
- `logging.py` — `setup_logging`, `get_logger`, `get_context_logger`. Консоль (текст) + файл (JSON) в `logs/`.
- `scheduler.py` — фоновая корутина проверки истёкших подписок (раз в час) с PostgreSQL advisory lock против дублей между воркерами.
- `task_scheduler.py` — класс `TaskScheduler`: каждые 30 сек выбирает SCHEDULED-задачи и инициирует исходящие звонки через Voximplant (партнёрская + legacy интеграции), запускает Pre/PostCall-оркестраторы агента. Агентские задачи с `channel="telegram"` уходят не в звонилку, а в `execute_agent_telegram_task` → `PostCallOrchestrator.run_for_scheduled_telegram` (составление и отправка сообщения с личного TG-аккаунта).

- `task_scheduler.py` (дополнение): перед Voximplant проверяется собственный SIP-шлюз — `_sip_number_for()`, `_execute_via_sip_gateway()`, `_agent_call_via_sip_gateway()`; звонок ставится в очередь `sip_calls`, дальше его забирает control-сокет моста в `api/sip_gateway.py`. `config.py`: `SIP_GATEWAY_TOKEN`, `SIP_GATEWAY_DEFAULT_ID`.

## Ключевые сущности / точки входа
- `settings` — единый объект конфигурации (импортируется почти везде как `from backend.core.config import settings`).
- `create_jwt_token(user_id, expires_delta_minutes)` / `decode_jwt_token(token)` — выпуск и проверка access-токена (`type: access_token`, алгоритм HS256).
- `get_current_user_id` (security) → `get_current_user` (dependencies) — цепочка извлечения пользователя из токена с конвертацией строки в UUID.
- `check_subscription_active`, `check_subscription_active_for_assistants`, `check_assistant_limit`, `check_assistant_limit_flexible`, `check_subscription_or_show_popup` — гейты по подписке/лимитам. Возвращают 402/403 при блокировке. Обе версии лимита — тонкие обёртки над `enforce_assistant_limit(db, user)`, различаются только способом авторизации; считают ассистентов **по всем провайдерам** через `services/assistant_limit_service.count_user_assistants` (раньше считались только строки `assistant_configs`).
- `get_logger(name)` — стандартная точка получения логгера во всём проекте.
- `start_subscription_checker()` — запуск цикла из `scheduler.py` (стартует с задержкой 30 сек, дальше раз в час).
- `TaskScheduler` / `start_task_scheduler(check_interval=30)` / `stop_task_scheduler()` — управление планировщиком звонков.

## Связи с другими частями проекта
- Используется: повсеместно — `backend/api/*`, `backend/services/*`, `backend/websockets/*`, `backend/db/*`, `backend/utils/*` импортируют `settings` и `get_logger`. Планировщики запускаются из `app.py` (startup events).
- Использует: `backend/db/session.py` (`SessionLocal`, `get_db`), модели `backend/models/*` (`User`, `AssistantConfig`, `GeminiAssistantConfig`, `CartesiaAssistantConfig`, `Task`, `Contact`, `VoximplantChildAccount`, `AgentConfig`, `AgentContact`, `AgentCall`, `SubscriptionEventLog`), сервисы `backend/services/*` (`UserService`, `SubscriptionService`, `NotificationService`, `voximplant_partner`, `agent_orchestrator`).

## На что обратить внимание
- `config.py` падает при импорте (`raise`), если не задан `HOST_URL` (или он localhost). Finik-переменные (`FINIK_API_KEY`, `FINIK_PRIVATE_PEM`, `FINIK_ACCOUNT_ID`) проверяются мягко — при отсутствии только предупреждение в консоль.
- Пароли хешируются простым SHA-256 без соли (`hash_password`) — легаси, не bcrypt/argon2.
- В `dependencies.py` зашиты привилегированные email'ы (`PRIVILEGED_UNLIMITED_EMAILS`) и спец-лимиты ассистентов (`SPECIAL_ASSISTANT_LIMITS`) — обходят проверки подписки/лимитов.
- Правки лимита ассистентов делай в `enforce_assistant_limit`, а не в обёртках: иначе кабинет и внешний API разъедутся в правилах.
- `scheduler.py` использует advisory lock с magic-числом `12345`; запуск защищён ещё и process-level флагом `_scheduler_running`. Истёкшие подписки не удаляют даты (сохраняются для истории), сбрасывается только `is_trial`.
- `task_scheduler.py` поддерживает две интеграции Voximplant: новую (`VoximplantChildAccount`, партнёрская) и legacy (`user.get_voximplant_config()`); выбор по `can_make_outbound_calls`. Gemini-ассистенту в legacy добавляется префикс `gemini_` к id.
- Ключевые env: `JWT_SECRET_KEY`, `DATABASE_URL`, `HOST_URL`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `FINIK_*`, `VOXIMPLANT_PARENT_*`, `R2_*`, `EMAIL_*`, `DEBUG`/`FORCE_DEBUG`, `PRODUCTION`.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../db/claude-db.md` — `SessionLocal`/`get_db`, используемые здесь
- `../models/claude-models.md` — модели, с которыми работают планировщики и зависимости
- `../services/claude-services.md` — `UserService`, `SubscriptionService`, оркестраторы агента
- `../api/claude-api.md` — потребители зависимостей аутентификации
