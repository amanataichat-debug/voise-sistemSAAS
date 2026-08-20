"""Add full_content and name to pinecone_configs

Revision ID: 0b5e2a3d4c1f
Revises: 
Create Date: 2023-05-20 10:40:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0b5e2a3d4c1f'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавление поля full_content (текст полного содержимого)
    op.add_column('pinecone_configs', sa.Column('full_content', sa.Text(), nullable=True))
    
    # Добавление поля name (название базы знаний)
    op.add_column('pinecone_configs', sa.Column('name', sa.String(100), nullable=True))
    
    # Опционально: обновить существующие записи, копируя content_preview в full_content
    op.execute("""
    UPDATE pinecone_configs 
    SET full_content = content_preview,
        name = 'База знаний'
    """)


def downgrade() -> None:
    # Удаление добавленных полей при откате миграции
    op.drop_column('pinecone_configs', 'name')
    op.drop_column('pinecone_configs', 'full_content')
