# alembic — миграции схемы БД (основная система)

## Назначение
Основная система версионирования схемы PostgreSQL через Alembic. Содержит конфигурацию окружения миграций и цепочку ревизий в `versions/`. Применяется автоматически при старте приложения: `main.py` (а также startup-логика) вызывает `alembic upgrade head`. Это источник истины по изменениям схемы; параллельно существуют легаси-пути (raw-SQL `backend/migrations/`, авто-фиксы колонок в startup-событии `app.py`).

## Состав
- `env.py` — конфигурация Alembic. Добавляет корень проекта в `sys.path`, импортирует `Base` из `backend/models/base.py` (`target_metadata = Base.metadata`) и **переопределяет `sqlalchemy.url` значением `settings.DATABASE_URL`** (хардкод из `alembic.ini` игнорируется).
- `script.py.mako` — шаблон для генерации новых файлов ревизий.
- `versions/` — файлы ревизий (см. ниже). Документировать содержимое отдельными файлами не нужно.

Конфиг `alembic.ini` лежит в корне репозитория (`script_location = alembic`, `sqlalchemy.url` — заглушка, реально берётся из env).

## Ревизии (versions/)
Цепочка не строго линейна — несколько корней с `down_revision = None`:
- `add_default_caller_id_to_agent_configs.py` (`add_default_caller_id`, down=None) — `default_caller_id` в `agent_configs`.
- `add_fields_to_pinecone_configs.py` (`0b5e2a3d4c1f`, down=None) — `full_content`, `name` в `pinecone_configs`.
- `fix_start_plan_price_1490.py` (down=`0b5e2a3d4c1f`) — цена плана `start` = 1490.
- `add_caller_id_to_tasks.py` (down=`fix_start_plan_price_1490`) — колонка `caller_id` в `tasks`.
- `add_elevenlabs_models.py` (down=`fix_start_plan_price_1490`) — таблицы ElevenLabs + user api key.
- `add_sip_registration_fields.py` (down=`add_elevenlabs_models`) — SIP-поля в Voximplant-моделях.

## Ключевые сущности / точки входа
- `target_metadata = Base.metadata` — автогенерация миграций сверяется со всеми моделями из `backend/models/` (поэтому важно, чтобы `models/__init__.py` импортировал каждую модель).
- `run_migrations_online` / `run_migrations_offline` — стандартные точки запуска Alembic.
- Программная обёртка над командами Alembic — `backend/db/migrations_manager.py` (`upgrade_database`, `check_migrations` и т.д.).

## Связи с другими частями проекта
- Используется: `main.py` (авто-`upgrade head` при старте), `backend/db/migrations_manager.py`, `app.py` (startup).
- Использует: `backend/models/base.py` (`Base`), `backend/core/config.py` (`settings.DATABASE_URL`).

## На что обратить внимание
- **URL берётся из env, не из ini.** `env.py` затирает `sqlalchemy.url` значением `settings.DATABASE_URL`; заглушка `postgresql://user:password@localhost/dbname` в `alembic.ini` нерабочая и так задумано.
- **Несколько корней цепочки** (`down_revision = None`) — при `upgrade head` Alembic может ругаться на множественные heads. Перед добавлением новой ревизии проверьте текущие heads (`alembic heads`) и привяжите `down_revision` корректно.
- **Три параллельных механизма изменения схемы:** Alembic (здесь), raw-SQL `backend/migrations/add_agent_v2.sql` (вне цепочки ревизий), и авто-добавление недостающих колонок в startup-событии `app.py`. При расхождении ориентируйтесь на Alembic, но помните о двух других.
- Авто-применение на старте означает, что битая миграция может уронить деплой — логика в `main.py` ловит ошибки и продолжает запуск, но схема останется неконсистентной.

## Связанные файлы документации
- `../claude-index.md` — корневой индекс
- `../backend/db/claude-db.md` — слой БД и `migrations_manager`
- `../backend/models/claude-models.md` — модели (target_metadata)
- `../backend/migrations/claude-migrations.md` — легаси raw-SQL миграция
