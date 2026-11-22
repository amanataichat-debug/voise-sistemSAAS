# backend/db/session.py

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

from backend.core.config import settings
from backend.core.logging import get_logger

logger = get_logger(__name__)

# Build the database URL
DATABASE_URL = os.getenv("DATABASE_URL", settings.DATABASE_URL)
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# Пул соединений (22.11.2026). Было NullPool: каждая сессия заново открывала SSL-соединение с Postgres,
# на Render это 1–2,5 с. Синхронные запросы кабинета (авторизация, опрос инбокса) шли в том же цикле событий,
# что и звук звонка, и каждый такой запрос замораживал звонок на секунды (журнал звонка a72651fc: «Сервер
# замер на 2.4 с … psycopg2 connect»): модель слышала клиента поздно и пачкой — «не слушает», «кабельский»
# вместо «кыргызский». Пул держит соединения открытыми, запрос занимает миллисекунды. DB_POOL=0 — прежний NullPool.
DB_POOL = (os.getenv("DB_POOL", "1").strip().lower() not in ("0", "false", "no", "off")
           and DATABASE_URL.startswith(("postgres", "postgresql")))
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "3") or 3)          # держим открытыми на воркер
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "17") or 17)  # сверх пула под пик (итого ≤ 20 на воркер)

try:
    pool_kwargs = (
        dict(pool_size=DB_POOL_SIZE, max_overflow=DB_MAX_OVERFLOW, pool_recycle=300, pool_timeout=30)
        if DB_POOL else dict(poolclass=NullPool)
    )
    engine = create_engine(
        DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,                          # Detect and refresh stale DB connections
        connect_args={"sslmode": "require"},         # Adjust sslmode as needed (e.g. 'disable' in dev)
        **pool_kwargs,
    )
    # Воркер gunicorn после fork не должен делить соединения родителя (preload_app): дочерний процесс
    # забывает унаследованный пул, не закрывая чужие сокеты (рекомендация SQLAlchemy).
    if DB_POOL and hasattr(os, "register_at_fork"):
        os.register_at_fork(after_in_child=lambda: engine.dispose(close=False))

    # Mask sensitive parts for logging
    database_url_masked = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else "database"
    logger.info(f"Database engine created for {database_url_masked} "
                f"(pool={'on ' + str(DB_POOL_SIZE) + '+' + str(DB_MAX_OVERFLOW) if DB_POOL else 'off'})")

except Exception as e:
    logger.error(f"Failed to create database engine: {e}")
    raise

# Configure a session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a database session and ensures it's closed.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
