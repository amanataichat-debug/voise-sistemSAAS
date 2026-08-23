"""Add personal Voksy AI API key columns to users

Ключ формата vfy_... не хранится в открытом виде: в БД лежит только
SHA-256-хэш (api_key_hash) и префикс для отображения в настройках.

Revision ID: add_user_api_key
Revises: None
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_user_api_key'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'users',
        sa.Column('api_key_hash', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('api_key_prefix', sa.String(length=20), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('api_key_created_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_users_api_key_hash',
        'users',
        ['api_key_hash'],
        unique=True,
    )


def downgrade():
    op.drop_index('ix_users_api_key_hash', table_name='users')
    op.drop_column('users', 'api_key_created_at')
    op.drop_column('users', 'api_key_prefix')
    op.drop_column('users', 'api_key_hash')
