# backend/migrations — разовая raw-SQL миграция для Voksy AI Agent v2

## Назначение
Каталог содержит одну ручную SQL-миграцию `add_agent_v2.sql`, добавляющую поля для функционала Voksy AI Agent v2. Это вспомогательный/легаси путь применения изменений схемы: основная система миграций проекта — Alembic (`../../alembic/`). SQL написан идемпотентно (`IF NOT EXISTS`), чтобы безопасно запускаться поверх существующей БД.

## Состав
- `add_agent_v2.sql` — `ALTER TABLE`/`UPDATE`/`CREATE INDEX` для трёх таблиц.

## Что делает миграция
- `agent_configs`: добавляет онбординг-документы `doc_who_am_i`, `doc_who_we_call`, `doc_how_we_talk`, `doc_what_we_offer`, `doc_rules_and_goals` (TEXT); рабочие часы `working_hours_start` (DEFAULT 9), `working_hours_end` (DEFAULT 21); `chat_history` (JSONB DEFAULT `'[]'`).
- `contacts`: добавляет `agent_memory` (JSONB DEFAULT `'{}'`).
- `tasks`: добавляет `pre_call_response_id` (VARCHAR 255), `post_call_decision` (VARCHAR 50), `retry_count` (INTEGER DEFAULT 0).
- Обновляет `orchestrator_model` на `gpt-5-2025-08-07` для существующих записей и ставит это значение DEFAULT.
- Создаёт частичный индекс `ix_tasks_pre_call_response` на `tasks(pre_call_response_id)` (WHERE NOT NULL) — для быстрого PostCall-поллинга.

## Связи с другими частями проекта
- Используется: модели `backend/models/agent_config.py`, `backend/models/contact.py`, `backend/models/task.py`; поля `pre_call_response_id`/`post_call_decision` фигурируют в `backend/core/task_scheduler.py` и оркестраторах агента.
- Использует: применяется напрямую к PostgreSQL (psql / разовый запуск), вне Alembic-цепочки ревизий.

## На что обратить внимание
- Не является Alembic-ревизией: не имеет revision/down_revision, не отслеживается в `alembic_version`. Параллельные изменения схемы могут также авто-применяться startup-событием в `app.py`.
- При расхождении со схемой Alembic ориентируйся на `../../alembic/` как на источник истины.
- `orchestrator_model` сбрасывается на `gpt-5-2025-08-07` для всех строк — учитывай при повторном запуске.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../../alembic/claude-alembic.md` — основная система миграций
- `../db/claude-db.md` — слой БД и обёртка `migrations_manager`
- `../models/claude-models.md` — затронутые модели
