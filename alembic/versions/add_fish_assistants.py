"""Add fish_assistant_configs table and users.fish_api_key

Fish Audio — TTS-провайдер. Диалог ведёт OpenAI Realtime прямо в сценарии
Voximplant, озвучка идёт через прокси /ws/fish/tts/{assistant_id}, который
читает ключ владельца из users.fish_api_key.

Дублирует startup-проверку app.py::create_fish_tables на случай, если
миграции не применились (в продакшене ошибки alembic проглатываются).

Revision ID: add_fish_assistants
Revises: None
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'add_fish_assistants'
down_revision = None
branch_labels = None
depends_on = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()

    if not _has_column(bind, "users", "fish_api_key"):
        op.add_column("users", sa.Column("fish_api_key", sa.String(), nullable=True))

    if not _has_table(bind, "fish_assistant_configs"):
        op.create_table(
            "fish_assistant_configs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.String(500), nullable=True),
            sa.Column("system_prompt", sa.Text(), nullable=True),
            # Голос Fish (reference_id из библиотеки fish.audio, в т.ч. клон)
            sa.Column("fish_voice_id", sa.String(255), nullable=True),
            sa.Column("fish_model", sa.String(50), nullable=False, server_default="s2.1-pro"),
            sa.Column("fish_latency", sa.String(20), nullable=False, server_default="balanced"),
            sa.Column("sample_rate", sa.Integer(), nullable=False, server_default="8000"),
            sa.Column("voice_speed", sa.Float(), nullable=True, server_default="1.0"),
            # Диалоговая модель OpenAI Realtime (на ключе пользователя)
            sa.Column("llm_model", sa.String(100), nullable=False, server_default="gpt-realtime-2.1-mini"),
            sa.Column("language", sa.String(10), nullable=False, server_default="ru"),
            sa.Column("temperature", sa.Float(), nullable=True, server_default="0.7"),
            sa.Column("greeting_message", sa.String(500), nullable=True),
            sa.Column("google_sheet_id", sa.String(255), nullable=True),
            sa.Column("functions", sa.JSON(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )


def downgrade():
    bind = op.get_bind()

    if _has_table(bind, "fish_assistant_configs"):
        op.drop_table("fish_assistant_configs")

    if _has_column(bind, "users", "fish_api_key"):
        op.drop_column("users", "fish_api_key")
