from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Импорт моделей и конфигурации проекта
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# config первым: пакет backend.core целиком загружается раньше моделей (иначе циклический импорт
# backend.models.file → backend.core → task_scheduler → services → backend.models.file при `alembic upgrade`).
from backend.core.config import settings
from backend.models.base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Логи alembic.ini — только при запуске из командной строки. Из app.py (run_migrations) логирование
# уже настроено: fileConfig с параметрами по умолчанию ОТКЛЮЧАЛ все логгеры приложения
# (disable_existing_loggers=True) — в воркере, который делал миграции, пропадали WARNING/ERROR
# в логах Render и в «Логах звонка».
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# Переопределяем URL из настроек приложения (вместо хардкода в alembic.ini)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
