# backend/models/base.py - ПРАВИЛЬНАЯ версия с полным отслеживанием

"""
backend/models/base.py

Базовый модуль для SQLAlchemy-моделей:
- объявляет движок `engine`
- декларативный базовый класс `Base`
- миксин `BaseModel` с to_dict()
- функцию create_tables_with_full_tracking() для ПОЛНОГО отслеживания
"""

import os
import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError, ProgrammingError
from sqlalchemy.orm import declarative_base

logger = logging.getLogger(__name__)

# URL базы берётся из окружения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# создаём движок
engine = create_engine(DATABASE_URL, future=True)
logger.info(f"Database engine created for {DATABASE_URL}")

# декларативный базовый класс
Base = declarative_base()

class BaseModel:
    """
    Миксин для SQLAlchemy-моделей, добавляет метод to_dict().
    """
    def to_dict(self):
        return {col.name: getattr(self, col.name) for col in self.__table__.columns}

def create_tables_with_full_tracking(engine):
    """
    ✅ ПРАВИЛЬНАЯ ФУНКЦИЯ: создаёт таблицы с ПОЛНЫМ отслеживанием подписок
    Устраняет ошибку 500 И сохраняет всю функциональность отслеживания!
    """
    try:
        logger.info("🚀 Starting database initialization with FULL tracking...")
        
        # ✅ Создаём все таблицы по описанным моделям
        Base.metadata.create_all(engine)
        logger.info("✅ All model tables created successfully")
        
        # ✅ Проверяем и создаём недостающие таблицы вручную
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()
        logger.info(f"📋 Existing tables: {existing_tables}")
        
        with engine.connect() as conn:
            # ✅ Создаём таблицу subscription_plans
            if 'subscription_plans' not in existing_tables:
                create_subscription_plans_sql = """
                CREATE TABLE IF NOT EXISTS subscription_plans (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name VARCHAR(50) NOT NULL,
                    code VARCHAR(20) NOT NULL UNIQUE,
                    price NUMERIC(10, 2) NOT NULL,
                    max_assistants INTEGER NOT NULL,
                    description TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE
                );
                """
                conn.execute(text(create_subscription_plans_sql))
                logger.info("✅ Created subscription_plans table")
                
                # Добавляем базовые планы
                insert_plans_sql = """
                INSERT INTO subscription_plans (code, name, price, max_assistants, description, is_active)
                VALUES 
                    ('free', 'Free Trial', 0, 1, 'Бесплатный пробный период с базовыми функциями', true),
                    ('start', 'Тариф Старт', 1490, 3, 'Стартовый план с расширенными возможностями', true),
                    ('pro', 'Тариф Про', 4990, 10, 'Профессиональный план со всеми функциями', true)
                ON CONFLICT (code) DO NOTHING;
                """
                conn.execute(text(insert_plans_sql))
                logger.info("✅ Inserted default subscription plans")
            
            # ✅ Создаём таблицу subscription_logs (КРИТИЧЕСКИ ВАЖНО!)
            if 'subscription_logs' not in existing_tables:
                create_subscription_logs_sql = """
                CREATE TABLE IF NOT EXISTS subscription_logs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    action VARCHAR(50) NOT NULL,
                    plan_id UUID REFERENCES subscription_plans(id),
                    plan_code VARCHAR(20),
                    amount NUMERIC(10, 2),
                    payment_id VARCHAR(100),
                    details TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                
                -- Индексы для быстрого поиска
                CREATE INDEX IF NOT EXISTS idx_subscription_logs_user_id ON subscription_logs(user_id);
                CREATE INDEX IF NOT EXISTS idx_subscription_logs_action ON subscription_logs(action);
                CREATE INDEX IF NOT EXISTS idx_subscription_logs_created_at ON subscription_logs(created_at);
                """
                conn.execute(text(create_subscription_logs_sql))
                logger.info("✅ Created subscription_logs table with indexes")
            
            # ✅ Создаём таблицу payment_transactions (ДЛЯ ОТЧЕТНОСТИ!)
            if 'payment_transactions' not in existing_tables:
                create_payment_transactions_sql = """
                CREATE TABLE IF NOT EXISTS payment_transactions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    plan_id UUID REFERENCES subscription_plans(id),
                    external_payment_id VARCHAR(100),
                    payment_system VARCHAR(50) DEFAULT 'robokassa',
                    amount NUMERIC(10, 2) NOT NULL,
                    currency VARCHAR(3) DEFAULT 'RUB',
                    status VARCHAR(20) DEFAULT 'pending',
                    is_processed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    paid_at TIMESTAMP WITH TIME ZONE,
                    processed_at TIMESTAMP WITH TIME ZONE,
                    payment_details VARCHAR(500),
                    error_message VARCHAR(500)
                );
                
                -- Индексы для аналитики
                CREATE INDEX IF NOT EXISTS idx_payment_transactions_user_id ON payment_transactions(user_id);
                CREATE INDEX IF NOT EXISTS idx_payment_transactions_status ON payment_transactions(status);
                CREATE INDEX IF NOT EXISTS idx_payment_transactions_external_id ON payment_transactions(external_payment_id);
                CREATE INDEX IF NOT EXISTS idx_payment_transactions_created_at ON payment_transactions(created_at);
                """
                conn.execute(text(create_payment_transactions_sql))
                logger.info("✅ Created payment_transactions table with indexes")
            
            # ✅ Обновляем таблицу users, если нужно
            try:
                # Проверяем наличие поля subscription_plan_id
                user_columns = [col['name'] for col in inspector.get_columns('users')]
                
                if 'subscription_plan_id' not in user_columns:
                    alter_users_sql = """
                    ALTER TABLE users 
                    ADD COLUMN IF NOT EXISTS subscription_plan_id UUID REFERENCES subscription_plans(id);
                    """
                    conn.execute(text(alter_users_sql))
                    logger.info("✅ Added subscription_plan_id to users table")
                
            except Exception as e:
                logger.warning(f"⚠️ Could not update users table: {e}")
            
            conn.commit()
        
        logger.info("🎉 Database initialization with FULL tracking completed successfully!")
        
        # ✅ Логируем статистику
        with engine.connect() as conn:
            tables_count = conn.execute(text("""
                SELECT COUNT(*) FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)).scalar()
            logger.info(f"📊 Total tables in database: {tables_count}")
        
    except Exception as e:
        logger.error(f"❌ Error during database initialization: {str(e)}")
        raise


def create_tables(engine):
    """
    ✅ ГЛАВНАЯ ФУНКЦИЯ: создаёт таблицы с полным отслеживанием
    """
    create_tables_with_full_tracking(engine)
