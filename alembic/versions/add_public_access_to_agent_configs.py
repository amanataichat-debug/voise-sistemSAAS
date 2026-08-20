"""Add public HTTP channel columns to agent_configs

Revision ID: add_public_access
Revises: None
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_public_access'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'agent_configs',
        sa.Column('public_api_key', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'agent_configs',
        sa.Column('public_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        'ix_agent_configs_public_api_key',
        'agent_configs',
        ['public_api_key'],
        unique=True,
    )


def downgrade():
    op.drop_index('ix_agent_configs_public_api_key', table_name='agent_configs')
    op.drop_column('agent_configs', 'public_enabled')
    op.drop_column('agent_configs', 'public_api_key')
