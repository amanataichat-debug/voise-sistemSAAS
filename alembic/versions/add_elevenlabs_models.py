"""Add ElevenLabs models and user api key

Revision ID: add_elevenlabs_models
Revises: fix_start_plan_price_1490
Create Date: 2025-07-18 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_elevenlabs_models'
down_revision = 'fix_start_plan_price_1490'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Создание таблиц ElevenLabs и добавление поля elevenlabs_api_key к пользователям
    """
    connection = op.get_bind()
    
    # ✅ 1. Добавляем поле elevenlabs_api_key в таблицу users (если его нет)
    try:
        # Проверяем существование колонки
        result = connection.execute(sa.text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'users' 
            AND column_name = 'elevenlabs_api_key'
        """)).fetchone()
        
        if not result:
            print("➕ Добавляем поле elevenlabs_api_key в таблицу users...")
            op.add_column('users', sa.Column('elevenlabs_api_key', sa.String(), nullable=True))
            print("✅ Поле elevenlabs_api_key добавлено в таблицу users")
        else:
            print("✅ Поле elevenlabs_api_key уже существует в таблице users")
    except Exception as e:
        print(f"❌ Ошибка при добавлении поля elevenlabs_api_key: {str(e)}")
    
    # ✅ 2. Создаем таблицу elevenlabs_agents (если её нет)
    try:
        # Проверяем существование таблицы
        result = connection.execute(sa.text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'elevenlabs_agents'
            );
        """)).fetchone()
        
        if not result[0]:
            print("➕ Создаем таблицу elevenlabs_agents...")
            op.create_table(
                'elevenlabs_agents',
                sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
                sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
                sa.Column('elevenlabs_agent_id', sa.String(), nullable=True),
                sa.Column('name', sa.String(), nullable=False),
                sa.Column('system_prompt', sa.Text(), nullable=True),
                sa.Column('voice_id', sa.String(), nullable=False),
                sa.Column('voice_name', sa.String(), nullable=True),
                sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
                sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
            )
            print("✅ Таблица elevenlabs_agents создана")
        else:
            print("✅ Таблица elevenlabs_agents уже существует")
    except Exception as e:
        print(f"❌ Ошибка при создании таблицы elevenlabs_agents: {str(e)}")
    
    # ✅ 3. Создаем таблицу elevenlabs_conversations (если её нет)
    try:
        # Проверяем существование таблицы
        result = connection.execute(sa.text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'elevenlabs_conversations'
            );
        """)).fetchone()
        
        if not result[0]:
            print("➕ Создаем таблицу elevenlabs_conversations...")
            op.create_table(
                'elevenlabs_conversations',
                sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
                sa.Column('agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('elevenlabs_agents.id', ondelete='CASCADE'), nullable=False),
                sa.Column('elevenlabs_conversation_id', sa.String(), nullable=True),
                sa.Column('user_message', sa.Text(), nullable=True),
                sa.Column('agent_response', sa.Text(), nullable=True),
                sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
            )
            print("✅ Таблица elevenlabs_conversations создана")
        else:
            print("✅ Таблица elevenlabs_conversations уже существует")
    except Exception as e:
        print(f"❌ Ошибка при создании таблицы elevenlabs_conversations: {str(e)}")
    
    print("🎉 Миграция ElevenLabs моделей завершена!")


def downgrade() -> None:
    """
    Откат миграции - удаляем созданные таблицы и поля
    """
    print("⚠️ Откат миграции ElevenLabs...")
    
    # Удаляем таблицы в обратном порядке
    try:
        op.drop_table('elevenlabs_conversations')
        print("✅ Таблица elevenlabs_conversations удалена")
    except Exception as e:
        print(f"❌ Ошибка при удалении таблицы elevenlabs_conversations: {str(e)}")
    
    try:
        op.drop_table('elevenlabs_agents')
        print("✅ Таблица elevenlabs_agents удалена")
    except Exception as e:
        print(f"❌ Ошибка при удалении таблицы elevenlabs_agents: {str(e)}")
    
    try:
        op.drop_column('users', 'elevenlabs_api_key')
        print("✅ Поле elevenlabs_api_key удалено из таблицы users")
    except Exception as e:
        print(f"❌ Ошибка при удалении поля elevenlabs_api_key: {str(e)}")
    
    print("🔄 Откат миграции ElevenLabs завершен")
