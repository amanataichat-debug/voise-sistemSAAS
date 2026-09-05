# claude-index — оглавление документации для AI-агентов

Это корневой индекс документации проекта **Voksy AI (WellcomeAI)** — SaaS-платформы голосовых AI-ассистентов (FastAPI + PostgreSQL, мульти-провайдер: OpenAI Realtime, Google Gemini Live, Fish Audio, xAI Grok, ElevenLabs; телефония — собственный SIP-шлюз, **Voximplant не используется**; биллинг Finik (KGS)).

Документация лежит рядом с кодом: в каждой значимой папке — файл `claude-*.md`, объясняющий её назначение, состав, точки входа, связи и подводные камни. Начинайте с этого индекса, спускайтесь к нужному слою.

## С чего начать (точки входа проекта)
- `main.py` — запуск Uvicorn/Gunicorn, авто-миграции Alembic при старте, кастомный import-redirect (`core.*` → `backend.core.*`).
- `app.py` — инициализация FastAPI: регистрация всех роутеров (строки ~168–199), middleware, startup/shutdown-события, монтирование статики, запуск планировщиков.
- `CLAUDE.md` — обзор проекта и техстека (платёжка — **Finik**, finik.kg).
- `gunicorn_config.py`, `render.yaml`, `requirements.txt`, `runtime.txt` — деплой (Render, Frankfurt) и зависимости.

## Backend
- [`backend/claude-backend.md`](backend/claude-backend.md) — корневой пакет приложения, карта слоёв и потоков (HTTP/WS/агент).

### Транспортный слой
- [`backend/api/claude-api.md`](backend/api/claude-api.md) — FastAPI-роутеры: все HTTP- и WS-эндпоинты, префиксы, webhooks (Finik, Telegram).
- [`backend/websockets/claude-websockets.md`](backend/websockets/claude-websockets.md) — реал-тайм голосовые хендлеры (OpenAI, Gemini, Fish, Grok) и WS-клиенты провайдеров + адаптер телефонии `sip_media_adapter`.

### Бизнес-логика
- [`backend/services/claude-services.md`](backend/services/claude-services.md) — сервисный слой: оркестратор Voksy AI Agent, кредиты/биллинг, интеграции внешних API.
- [`backend/services/llm_streaming/claude-llm-streaming.md`](backend/services/llm_streaming/claude-llm-streaming.md) — low-latency стриминг текстовой LLM (для функции `query_llm`).
- [`backend/functions/claude-functions.md`](backend/functions/claude-functions.md) — модульная система AI-функций (function calling, авто-дискавери реестром).

### Данные
- [`backend/models/claude-models.md`](backend/models/claude-models.md) — SQLAlchemy ORM-модели (схема PostgreSQL).
- [`backend/schemas/claude-schemas.md`](backend/schemas/claude-schemas.md) — Pydantic-схемы запросов/ответов API.
- [`backend/db/claude-db.md`](backend/db/claude-db.md) — engine, сессии, declarative base, репозитории, обёртка Alembic.

### Инфраструктура
- [`backend/core/claude-core.md`](backend/core/claude-core.md) — конфиг (env), JWT/безопасность, зависимости доступа, логирование, планировщики.
- [`backend/utils/claude-utils.md`](backend/utils/claude-utils.md) — stateless-утилиты: аудио, обработка ошибок, валидаторы, файлы.

### Фронтенд приложения (server-served)
- [`backend/static/claude-static.md`](backend/static/claude-static.md) — vanilla HTML/JS-страницы кабинета, голосовые виджеты, собранный лендинг.

## Миграции БД
- [`alembic/claude-alembic.md`](alembic/claude-alembic.md) — основная система миграций (Alembic, авто-`upgrade head` на старте).
- [`backend/migrations/claude-migrations.md`](backend/migrations/claude-migrations.md) — легаси raw-SQL миграция Voksy AI Agent v2 (вне цепочки Alembic).
- Примечание: третий механизм изменения схемы — авто-добавление колонок в startup-событии `app.py`.

## Инфраструктура вне Render
- [`infra/sip-gateway/claude-sip-gateway.md`](infra/sip-gateway/claude-sip-gateway.md) — собственная SIP-телефония: VPS Hetzner с Asterisk 20 и мостом `bridge.py`, протокол мост⇄бэкенд, файлы бэкенда (`api/sip_gateway.py`, `websockets/sip_media_adapter.py`, `services/sip_gateway_service.py`, `models/sip_gateway.py`), текущее состояние и что не сделано. Памятка по серверу для человека — `infra/sip-gateway/SERVER.md`, протокол — `infra/sip-gateway/README.md`.

## Лендинг (React)
- [`frontend/claude-frontend.md`](frontend/claude-frontend.md) — React + Vite лендинг (сборка → `backend/static/landing/`).
- [`frontend/src/claude-frontend-src.md`](frontend/src/claude-frontend-src.md) — исходники: компоненты, хуки (auth/email/referral), API-клиент, стили.

## Клиенты
- [`chrome-extension/claude-chrome-extension.md`](chrome-extension/claude-chrome-extension.md) — Chrome-расширение (MV3): popup-логин + side panel с голосовым WS.

## Папки без отдельной документации
- `alembic/versions/` — файлы ревизий миграций (перечислены в `alembic/claude-alembic.md`).
- `backend/static/landing/`, `css/`, `images/`, шрифты/иконки — статические ассеты и артефакты сборки.
- `docs/` — `credits_system.md` (человеческая документация системы кредитов).
- `static/states/` — отдельные HTML-фрагменты (`voice_ai_assistants.html`, `voicyfy_why_choose.html`).
- Тесты в корне (`test_*.py`) — разрозненные интеграционные скрипты, не структурированная сюита.

## Сквозные замечания (важно для всего проекта)
- **Мульти-провайдер с дублированием.** Виды голосовых ассистентов (OpenAI/Gemini/Grok/Cascade/Cartesia/Yandex/Translate) + ElevenLabs дублируются на каждом слое (model → api → handler → widget). Общая правка = правка во всех ветках. Каскад — особый случай: живёт в таблице Grok (`assistant_type='cascade'`) и обслуживается тем же роутером `/api/grok-assistants`.
- **Лимит ассистентов — один на всех провайдеров.** Считается в `services/assistant_limit_service.py` (единственный источник правды для `check_assistant_limit` и `GET /api/subscriptions/assistants-usage`). Голосовые ассистенты мастера Voksy AI Agent из подсчёта исключены.
- **Voksy AI Agent v5.0** — автономный обзвон: оркестратор (`services/agent_orchestrator`) + планировщик звонков (`core/task_scheduler`) + кредиты (`services/credit_service`). Это самостоятельная подсистема со своим биллингом (кредиты, не подписочные минуты).
- **Телефония одна — собственный SIP-шлюз** (`infra/sip-gateway/`, VPS + Asterisk). **Voximplant не используется**; его код (api/telephony.py, api/voximplant.py, services/voximplant_partner.py, websockets/voximplant_*, voximplant_scenarios/, типы `cascade`/`cartesia`/`yandex`) — мёртвый до общей чистки, список в `CLAUDE.md`. Планировщик `core/task_scheduler.py` ставит звонок в очередь шлюза, если у пользователя есть активный номер в `sip_phone_numbers` с `allow_outbound` и ассистент OpenAI/Gemini/Fish. Телефонный звонок проходит через те же браузерные хендлеры, что и виджет — поведение должно быть одинаковым.
- **Fish на серверных ключах.** `websockets/handler_fish.py`: OpenAI Realtime текстом + Fish TTS, ключи `OPENAI_API_KEY`/`FISH_API_KEY` из env, диалоги в `fish_conversations`, тест в браузере `/static/fish-test.html?id=`. Образец для переноса остальных Voximplant-only типов.
- **Версионные дубликаты** в `websockets/` — ориентируйтесь на то, что реально импортирует роутер, а не на имя/комментарий файла.
- **Платежи — Finik (finik.kg, KGS)**. **Пароли хешируются SHA-256 без соли**; API-ключи провайдеров в БД без шифрования.
- **Import-redirect** в `main.py` делает эквивалентными `from backend.x...` и `from x...`.
