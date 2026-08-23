# backend — основной пакет приложения (FastAPI SaaS Voice AI)

## Назначение
Корневой Python-пакет всего серверного приложения Voksy AI. Содержит все слои: транспортный (API-роутеры, WebSocket-хендлеры), бизнес-логику (services), доступ к данным (models, db, schemas), инфраструктуру (core), модульные AI-функции и весь фронтенд, отдаваемый сервером (static). Сам по себе пакет не запускается — точки входа лежат в корне репозитория (`main.py` → `app.py`), которые импортируют отсюда роутеры, модели и сервисы.

## Состав
- `api/` — FastAPI-роутеры: все HTTP- и WebSocket-эндпоинты (~36 файлов). См. `api/claude-api.md`.
- `websockets/` — реал-тайм голосовые хендлеры и провайдерские WS-клиенты (OpenAI/Gemini/Grok + Voximplant). См. `websockets/claude-websockets.md`.
- `services/` — слой бизнес-логики: оркестратор агента, кредиты/биллинг, интеграции внешних API. См. `services/claude-services.md` (+ `services/llm_streaming/`).
- `models/` — SQLAlchemy ORM-модели (схема БД). См. `models/claude-models.md`.
- `schemas/` — Pydantic-схемы запросов/ответов API. См. `schemas/claude-schemas.md`.
- `functions/` — модульная система AI-функций (function calling, авто-дискавери). См. `functions/claude-functions.md`.
- `core/` — конфиг, JWT/безопасность, зависимости, логирование, планировщики. См. `core/claude-core.md`.
- `db/` — engine, сессии, declarative base, репозитории, обёртка Alembic. См. `db/claude-db.md`.
- `utils/` — stateless-утилиты (аудио, ошибки, валидация, файлы). См. `utils/claude-utils.md`.
- `static/` — фронтенд приложения (vanilla HTML/JS), виджеты, собранный лендинг. См. `static/claude-static.md`.
- `migrations/` — легаси raw-SQL миграция (вне Alembic). См. `migrations/claude-migrations.md`.

## Ключевые сущности / точки входа
- **Точки входа вне пакета:** `../main.py` (запуск Uvicorn/Gunicorn, авто-миграции Alembic, кастомный import-redirect) и `../app.py` (создание FastAPI, регистрация роутеров, middleware, startup-события). Сюда стоит идти первым делом.
- **Поток запроса (HTTP):** `api/` (роутер) → зависимости из `core/dependencies.py` → `services/` (логика) → `models/`/`db/` (данные) → `schemas/` (ответ).
- **Поток голоса (WS):** `api/*_ws.py` (приём соединения) → `websockets/handler_*` (проксирование к провайдеру) → `functions/` (исполнение AI-функций) → `services/` (запись диалога, кредиты, уведомления).
- **Voksy AI Agent v5.0** — автономный обзвон: `models/agent_*` + `services/agent_orchestrator` (мозг) + `core/task_scheduler` (запуск звонков) + `services/credit_service` (биллинг по кредитам) + `api/agent.py`/`api/credits.py`.
- **Фоновые процессы:** `core/scheduler.py` (истёкшие подписки), `core/task_scheduler.py` (запланированные звонки), `services/subscription_blocker.py` (блокировка agent-подписок) — стартуют из `app.py`.

## Связи с другими частями проекта
- Используется: `../main.py`, `../app.py` (импортируют роутеры/модели/сервисы/планировщики), `../alembic/` (миграции по `models/base.Base`).
- Использует: внешние API (OpenAI, Google Gemini, xAI Grok, ElevenLabs, Cartesia, OpenRouter, Pinecone, Cloudflare R2, Finik, Voximplant, Telegram, Google Sheets, SMTP), PostgreSQL.

## На что обратить внимание
- **Import-redirect.** `../main.py` ставит `MetaPathFinder`, который перенаправляет «голые» импорты (`core.config`, `models.user`) в `backend.*`. Поэтому в коде встречается и `from backend.core...`, и `from core...` — это одно и то же.
- **Слоистость не строгая.** Часть роутеров (агент, кредиты, voximplant) держит логику/inline-схемы прямо в `api/`, минуя `services/`/`schemas/`. Не предполагайте полную чистоту слоёв.
- **Дублирование по провайдерам.** Пять видов ассистентов (OpenAI/Gemini/Grok/Cartesia/Translate) + ElevenLabs дублируются на всех слоях (model/api/widget/handler). Общая правка = правка во всех.
- **Версионные дубли** в `websockets/` (актуальные/легаси/экспериментальные хендлеры) — источник истины — что импортирует роутер.
- **Платёжка — Finik** (finik.kg, QR-эквайринг Кыргызстана, валюта KGS). Чувствительные данные (API-ключи, Voximplant-пароли) в `users` хранятся без шифрования.
- **Авто-фиксы схемы** на старте в `app.py` (добавление колонок, фикс цен планов) — третий механизм изменения схемы помимо Alembic и `migrations/`.

## Связанные файлы документации
- `../claude-index.md` — корневой индекс всей документации
- Дочерние: `api/claude-api.md`, `websockets/claude-websockets.md`, `services/claude-services.md` (+ `services/llm_streaming/claude-llm-streaming.md`), `models/claude-models.md`, `schemas/claude-schemas.md`, `functions/claude-functions.md`, `core/claude-core.md`, `db/claude-db.md`, `utils/claude-utils.md`, `static/claude-static.md`, `migrations/claude-migrations.md`
- Смежные: `../alembic/claude-alembic.md`, `../frontend/claude-frontend.md`
