"""Update start plan price to 1490

Revision ID: fix_start_plan_price_1490
Revises: 0b5e2a3d4c1f
Create Date: 2025-06-11 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'fix_start_plan_price_1490'
down_revision = '0b5e2a3d4c1f'  # Ссылается на предыдущую миграцию
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Обновляем цену плана 'start' на 1490 рублей
    """
    # Проверяем существование таблицы subscription_plans
    # Если таблицы нет - создаем её с правильными планами
    connection = op.get_bind()
    
    # Проверяем существование таблицы
    result = connection.execute(sa.text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'subscription_plans'
        );
    """)).fetchone()
    
    table_exists = result[0] if result else False
    
    if not table_exists:
        # Создаем таблицу subscription_plans если её нет
        op.create_table(
            'subscription_plans',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
            sa.Column('name', sa.String(50), nullable=False),
            sa.Column('code', sa.String(20), nullable=False, unique=True),
            sa.Column('price', sa.Numeric(10, 2), nullable=False),
            sa.Column('max_assistants', sa.Integer(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('is_active', sa.Boolean(), default=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True)
        )
        
        # Добавляем базовые планы с правильными ценами
        op.execute(sa.text("""
            INSERT INTO subscription_plans (code, name, price, max_assistants, description, is_active)
            VALUES 
                ('free', 'Free Trial', 0, 1, 'Бесплатный пробный период с базовыми функциями', true),
                ('start', 'Тариф Старт', 1490, 3, 'Стартовый план с расширенными возможностями', true),
                ('pro', 'Тариф Про', 4990, 10, 'Профессиональный план со всеми функциями', true)
            ON CONFLICT (code) DO NOTHING;
        """))
    else:
        # Таблица существует - обновляем цену плана 'start'
        op.execute(sa.text("""
            UPDATE subscription_plans 
            SET price = 1490,
                updated_at = NOW()
            WHERE code = 'start';
        """))
        
        # Также убеждаемся что у нас есть все нужные планы
        op.execute(sa.text("""
            INSERT INTO subscription_plans (code, name, price, max_assistants, description, is_active)
            VALUES 
                ('free', 'Free Trial', 0, 1, 'Бесплатный пробный период с базовыми функциями', true),
                ('start', 'Тариф Старт', 1490, 3, 'Стартовый план с расширенными возможностями', true),
                ('pro', 'Тариф Про', 4990, 10, 'Профессиональный план со всеми функциями', true)
            ON CONFLICT (code) DO UPDATE SET
                price = EXCLUDED.price,
                name = EXCLUDED.name,
                max_assistants = EXCLUDED.max_assistants,
                description = EXCLUDED.description,
                updated_at = NOW();
        """))
    
    # Логируем что сделали
    print("✅ Цена плана 'start' обновлена на 1490 рублей")


def downgrade() -> None:
    """
    Откат - возвращаем цену плана 'start' на 1990 рублей
    (на случай если понадобится откатить миграцию)
    """
    op.execute(sa.text("""
        UPDATE subscription_plans 
        SET price = 1990,
            updated_at = NOW()
        WHERE code = 'start';
    """))
    
    print("⚠️ Цена плана 'start' возвращена на 1990 рублей")
