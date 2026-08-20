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

try:
    # Create SQLAlchemy engine with SSL and no connection pooling
    engine = create_engine(
        DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,                          # Detect and refresh stale DB connections
        poolclass=NullPool,                          # Disable pooling so each checkout is a fresh connection
        connect_args={"sslmode": "require"}          # Adjust sslmode as needed (e.g. 'disable' in dev)
    )

    # Mask sensitive parts for logging
    database_url_masked = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else "database"
    logger.info(f"Database engine created for {database_url_masked}")

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
