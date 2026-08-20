"""Add voice_additional_instructions to agent_configs

Revision ID: add_voice_instructions
Revises: None
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_voice_instructions'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'agent_configs',
        sa.Column('voice_additional_instructions', sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column('agent_configs', 'voice_additional_instructions')
