"""Add default_caller_id to agent_configs

Revision ID: add_default_caller_id
Revises: None
Create Date: 2026-03-12
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_default_caller_id'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('agent_configs', sa.Column('default_caller_id', sa.String(50), nullable=True))


def downgrade():
    op.drop_column('agent_configs', 'default_caller_id')
