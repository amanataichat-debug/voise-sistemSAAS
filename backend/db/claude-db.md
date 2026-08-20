# backend/db — слой доступа к БД: engine, сессии, declarative base, репозитории, обёртка Alembic

## Назначение
Слой работы с PostgreSQL через SQLAlchemy 2.x. Создаёт engine и фабрику сессий (`session.py`), объявляет общий declarative `Base` и базовые CRUD-абстракции (`base.py`), предоставляет конкретные репозитории для основных моделей (`repositories.py`) и тонкую обёртку над командами Alembic (`migrations_manager.py`). Точка, через которую остальной код получает сессию БД.

## Состав
- `session.py` — `create_engine` (NullPool, `pool_pre_ping`, `sslmode=require`), фабрика `SessionLocal`, FastAPI-зависимость `get_db()` (yield + close).
- `base.py` — `Base = declarative_base()`, абстрактный `BaseModel` (поля `id: UUID`, `created_at`, `updated_at`, метод `get_by_id`), дженерик `CRUDBase[Model, Create, Update]` с `get/get_multi/create/update/remove`.
- `repositories.py` — конкретные репозитории поверх `CRUDBase`: `UserRepository`, `AssistantRepository`, `ConversationRepository`, `FileRepository` + готовые синглтон-экземпляры.
- `migrations_manager.py` — функции-обёртки над Alembic: `upgrade_database`, `downgrade_database`, `create_migration`, `get_current_revision`, `get_history`, `check_migrations`, `create_initial_migration`.

## Ключевые сущности / точки входа
- `engine` — глобальный SQLAlchemy engine; `DATABASE_URL` берётся из env или `settings`, при отсутствии — `RuntimeError`.
- `SessionLocal` — фабрика сессий (`autocommit=False`, `autoflush=False`). Используется планировщиками и сервисами напрямую.
- `get_db()` — FastAPI-зависимость, выдающая сессию на запрос.
- `Base` — общий declarative base для всех моделей в `backend/models/*`.
- `CRUDBase` / `BaseModel` — базовые классы для моделей и CRUD.
- Синглтоны репозиториев: `user_repository`, `assistant_repository`, `conversation_repository`, `file_repository`.
- `upgrade_database(revision="head")` / `check_migrations()` — управление миграциями программно.

## Связи с другими частями проекта
- Используется: `backend/core/dependencies.py` и `backend/api/*` (через `get_db`), `backend/core/scheduler.py` и `backend/core/task_scheduler.py` (через `SessionLocal`), `backend/services/*` (репозитории и сессии), все `backend/models/*` наследуют `Base`/`BaseModel`.
- Использует: `backend/core/config.py` (`settings.DATABASE_URL`, `settings.DEBUG`), `backend/core/logging.py` (`get_logger`), `backend/models/*` и `backend/schemas/*` (в `repositories.py`), пакет `alembic` (в `migrations_manager.py`).

## На что обратить внимание
- `poolclass=NullPool` — пулинг отключён, каждый checkout создаёт свежее соединение (выбор под Render/managed Postgres). `pool_pre_ping=True` отсеивает мёртвые коннекты.
- `connect_args={"sslmode": "require"}` зашит — в локальной разработке без SSL может потребоваться правка.
- Основная система миграций — каталог `../../alembic/` (Alembic). `migrations_manager.py` указывает `script_location` на `backend/migrations` и `backend/alembic.ini` — это альтернативный/легаси путь; не путать с корневым `alembic/`. Многие изменения схемы также авто-применяются в startup-событии `app.py`.
- `CRUDBase.update` итерирует по `db_obj.__dict__` — поля, отсутствующие в загруженном объекте, не обновятся; это легаси-реализация.
- `UserRepository.create_with_hashed_password` проставляет `subscription_plan="free"` по умолчанию.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../models/claude-models.md` — модели, наследующие `Base`/`BaseModel`
- `../../alembic/claude-alembic.md` — основная система миграций
- `../migrations/claude-migrations.md` — raw-SQL миграция (легаси)
- `../core/claude-core.md` — `settings`, `get_logger`, потребители `SessionLocal`
- `../services/claude-services.md` — потребители репозиториев
