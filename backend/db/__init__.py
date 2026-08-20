"""
Инициализация модуля базы данных.
Содержит общие функции и объекты для работы с базой данных.
"""

from backend.db.session import get_db, SessionLocal, engine
from backend.db.base import Base

__all__ = ["get_db", "SessionLocal", "engine", "Base"]
