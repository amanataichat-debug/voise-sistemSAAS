"""Add knowledge base (Pinecone) columns to agent_configs

Revision ID: add_agent_knowledge_base
Revises: None
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_agent_knowledge_base'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('agent_configs', sa.Column('kb_namespace', sa.String(length=64), nullable=True))
    op.add_column('agent_configs', sa.Column('kb_char_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('agent_configs', sa.Column('kb_content', sa.Text(), nullable=True))
    op.add_column('agent_configs', sa.Column('kb_name', sa.String(length=100), nullable=True))
    op.add_column('agent_configs', sa.Column('kb_updated_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('agent_configs', 'kb_updated_at')
    op.drop_column('agent_configs', 'kb_name')
    op.drop_column('agent_configs', 'kb_content')
    op.drop_column('agent_configs', 'kb_char_count')
    op.drop_column('agent_configs', 'kb_namespace')
