"""sip_phone_numbers.agent_config_id — привязка номера SIP-шлюза к агенту обзвона

Номер, привязанный к агенту, ходит через голосового ассистента агента
(assistant_type/assistant_id копируются при привязке и обновляются при смене
типа у агента). Дублирует ALTER в backend/api/sip_gateway.py::_ensure_tables
на случай, если миграции не применились.

Revision ID: add_sip_number_agent_binding
Revises: None
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'add_sip_number_agent_binding'
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
    if "sip_phone_numbers" not in sa.inspect(bind).get_table_names():
        return
    if not _has_column(bind, "sip_phone_numbers", "agent_config_id"):
        op.add_column(
            "sip_phone_numbers",
            sa.Column(
                "agent_config_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agent_configs.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def downgrade():
    bind = op.get_bind()
    if _has_column(bind, "sip_phone_numbers", "agent_config_id"):
        op.drop_column("sip_phone_numbers", "agent_config_id")
