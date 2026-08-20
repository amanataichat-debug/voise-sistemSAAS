"""
Управление миграциями базы данных.
Содержит функции для управления миграциями с помощью Alembic.
"""

import os
import logging
from pathlib import Path
from typing import List, Optional

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

# Путь к корню проекта
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()
# Путь к директории с миграциями
MIGRATIONS_DIR = os.path.join(PROJECT_ROOT, "backend", "migrations")
# Путь к конфигурационному файлу Alembic
ALEMBIC_INI = os.path.join(PROJECT_ROOT, "backend", "alembic.ini")


def get_alembic_config() -> Config:
    """
    Получить конфигурацию Alembic.
    
    Returns:
        Config: Объект конфигурации Alembic
    """
    config = Config(ALEMBIC_INI)
    config.set_main_option("script_location", MIGRATIONS_DIR)
    return config


def create_migration(message: str) -> None:
    """
    Создать новую миграцию.
    
    Args:
        message (str): Сообщение для миграции
    """
    try:
        config = get_alembic_config()
        command.revision(config, message=message, autogenerate=True)
        logger.info(f"Миграция с сообщением '{message}' успешно создана")
    except Exception as e:
        logger.error(f"Ошибка при создании миграции: {str(e)}")
        raise


def upgrade_database(revision: str = "head") -> None:
    """
    Обновить базу данных до указанной ревизии.
    
    Args:
        revision (str, optional): Ревизия для обновления. По умолчанию "head".
    """
    try:
        config = get_alembic_config()
        command.upgrade(config, revision)
        logger.info(f"База данных успешно обновлена до ревизии '{revision}'")
    except Exception as e:
        logger.error(f"Ошибка при обновлении базы данных: {str(e)}")
        raise


def downgrade_database(revision: str) -> None:
    """
    Откатить базу данных до указанной ревизии.
    
    Args:
        revision (str): Ревизия для отката.
    """
    try:
        config = get_alembic_config()
        command.downgrade(config, revision)
        logger.info(f"База данных успешно откачена до ревизии '{revision}'")
    except Exception as e:
        logger.error(f"Ошибка при откате базы данных: {str(e)}")
        raise


def get_current_revision() -> str:
    """
    Получить текущую ревизию базы данных.
    
    Returns:
        str: Текущая ревизия
    """
    try:
        from alembic.migration import MigrationContext
        from sqlalchemy import create_engine
        from backend.core.config import settings
        
        engine = create_engine(settings.DATABASE_URL)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            return context.get_current_revision() or "None"
    except Exception as e:
        logger.error(f"Ошибка при получении текущей ревизии: {str(e)}")
        return "Error"


def get_history() -> List[dict]:
    """
    Получить историю миграций.
    
    Returns:
        List[dict]: Список миграций с их метаданными
    """
    try:
        from alembic.script import ScriptDirectory
        
        config = get_alembic_config()
        script_directory = ScriptDirectory.from_config(config)
        
        history = []
        for revision in script_directory.walk_revisions():
            history.append({
                "revision": revision.revision,
                "down_revision": revision.down_revision,
                "message": revision.doc,
                "created_date": revision.created_date
            })
        
        return history
    except Exception as e:
        logger.error(f"Ошибка при получении истории миграций: {str(e)}")
        return []


def create_initial_migration() -> None:
    """
    Создать начальную миграцию, если она отсутствует.
    """
    history = get_history()
    if not history:
        create_migration("Initial migration")
        logger.info("Создана начальная миграция")
    else:
        logger.info("Начальная миграция уже существует")


def check_migrations() -> bool:
    """
    Проверить, требуются ли миграции.
    
    Returns:
        bool: True, если требуются миграции, иначе False
    """
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from backend.core.config import settings
    
    try:
        engine = create_engine(settings.DATABASE_URL)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current_revision = context.get_current_revision()
            
            config = get_alembic_config()
            script_directory = ScriptDirectory.from_config(config)
            head_revision = script_directory.get_current_head()
            
            return current_revision != head_revision
    except Exception as e:
        logger.error(f"Ошибка при проверке миграций: {str(e)}")
        return True  # В случае ошибки лучше предположить, что миграции нужны
