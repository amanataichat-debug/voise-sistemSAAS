"""Add caller_id column to tasks table

Revision ID: add_caller_id_to_tasks
Revises: fix_start_plan_price_1490
Create Date: 2026-02-22 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_caller_id_to_tasks'
down_revision = 'fix_start_plan_price_1490'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add caller_id column to tasks table for caller ID selection."""
    connection = op.get_bind()

    # Check if column already exists
    result = connection.execute(sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'tasks'
            AND column_name = 'caller_id'
        );
    """)).fetchone()

    column_exists = result[0] if result else False

    if not column_exists:
        op.add_column('tasks', sa.Column('caller_id', sa.String(20), nullable=True))


def downgrade() -> None:
    """Remove caller_id column from tasks table."""
    op.drop_column('tasks', 'caller_id')
