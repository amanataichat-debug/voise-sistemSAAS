"""Add fish_assistant_id to agent_configs and tasks

Позволяет агенту обзвона использовать Fish Audio как голосового провайдера:
AgentConfig ссылается на голос агента, Task — на ассистента запланированного
звонка (по нему планировщик резолвит, кем звонить).

Дублирует startup-проверку app.py::ensure_agent_fish_voice_columns на случай,
если миграции не применились (в продакшене ошибки alembic проглатываются).

Revision ID: add_fish_voice_to_agent
Revises: add_fish_assistants
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'add_fish_voice_to_agent'
down_revision = 'add_fish_assistants'
branch_labels = None
depends_on = None

_TABLES = ("agent_configs", "tasks")


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()

    if not _has_table(bind, "fish_assistant_configs"):
        # Таблицы ассистентов ещё нет — колонке некуда ссылаться. Её добавит
        # startup-проверка, когда таблица появится.
        return

    for table in _TABLES:
        if not _has_table(bind, table) or _has_column(bind, table, "fish_assistant_id"):
            continue
        op.add_column(
            table,
            sa.Column("fish_assistant_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table}_fish_assistant_id",
            table,
            "fish_assistant_configs",
            ["fish_assistant_id"],
            ["id"],
            ondelete="SET NULL",
        )
        if table == "tasks":
            # Планировщик выбирает задачи по ассистенту — как у остальных
            # провайдеров, колонка индексируется.
            op.create_index(
                "ix_tasks_fish_assistant_id", "tasks", ["fish_assistant_id"], unique=False
            )


def downgrade():
    bind = op.get_bind()

    if _has_column(bind, "tasks", "fish_assistant_id"):
        op.drop_index("ix_tasks_fish_assistant_id", table_name="tasks")

    for table in _TABLES:
        if _has_column(bind, table, "fish_assistant_id"):
            op.drop_constraint(f"fk_{table}_fish_assistant_id", table, type_="foreignkey")
            op.drop_column(table, "fish_assistant_id")
