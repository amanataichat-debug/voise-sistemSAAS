"""Add silence_duration_ms to grok_assistant_configs (cascade agents)

Пауза перед ответом каскад-агента — пресет, который владелец выбирает на
странице агента: 300 мс (быстрая), 650 мс (сбалансированная), 1000 мс
(терпеливая). Значение уезжает в VoxTurnTaking как minSilenceDurationMs.

Revision ID: add_silence_duration
Revises: None
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_silence_duration'
down_revision = None
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()
    if _has_column(bind, "grok_assistant_configs", "silence_duration_ms"):
        return
    op.add_column(
        "grok_assistant_configs",
        sa.Column("silence_duration_ms", sa.Integer(), nullable=True, server_default="300"),
    )


def downgrade():
    bind = op.get_bind()
    if not _has_column(bind, "grok_assistant_configs", "silence_duration_ms"):
        return
    op.drop_column("grok_assistant_configs", "silence_duration_ms")
