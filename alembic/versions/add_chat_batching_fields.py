"""Add chat batching + split fields

Добавляет поля для:
- Debounce входящих сообщений личного Telegram (MTProto): клиент часто
  пишет мысль в несколько коротких сообщений — поллер складывает их в
  pending_messages, а отдельный flush-воркер отдаёт пачкой оркестратору
  спустя окно тишины.
- Split исходящих ответов: ответ модели режется по маркеру `---` и
  шлётся несколькими сообщениями с паузой — чтобы не выглядело как
  простыня-спам.

Параметры окна и разбиения — per-agent, в agent_configs.

Revision ID: add_chat_batching_fields
Revises: None
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_chat_batching_fields'
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

    # ── agent_telegram_dialogs: очередь входящих и момент flush ──
    if _has_column(bind, "agent_telegram_dialogs", "id"):
        if not _has_column(bind, "agent_telegram_dialogs", "pending_messages"):
            op.execute(
                "ALTER TABLE agent_telegram_dialogs "
                "ADD COLUMN IF NOT EXISTS pending_messages JSONB NOT NULL DEFAULT '[]'::jsonb"
            )
        if not _has_column(bind, "agent_telegram_dialogs", "pending_flush_at"):
            op.execute(
                "ALTER TABLE agent_telegram_dialogs "
                "ADD COLUMN IF NOT EXISTS pending_flush_at TIMESTAMP NULL"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_agent_telegram_dialogs_pending_flush_at "
                "ON agent_telegram_dialogs (pending_flush_at)"
            )
        if not _has_column(bind, "agent_telegram_dialogs", "pending_first_at"):
            op.execute(
                "ALTER TABLE agent_telegram_dialogs "
                "ADD COLUMN IF NOT EXISTS pending_first_at TIMESTAMP NULL"
            )
        if not _has_column(bind, "agent_telegram_dialogs", "last_flushed_at"):
            op.execute(
                "ALTER TABLE agent_telegram_dialogs "
                "ADD COLUMN IF NOT EXISTS last_flushed_at TIMESTAMP NULL"
            )

    # ── agent_configs: настройки batching и split ──
    if _has_column(bind, "agent_configs", "id"):
        for col, ddl in (
            ("chat_batching_enabled",
             "ADD COLUMN IF NOT EXISTS chat_batching_enabled BOOLEAN NOT NULL DEFAULT TRUE"),
            ("chat_batching_window_seconds",
             "ADD COLUMN IF NOT EXISTS chat_batching_window_seconds INTEGER NOT NULL DEFAULT 15"),
            ("chat_batching_max_window_seconds",
             "ADD COLUMN IF NOT EXISTS chat_batching_max_window_seconds INTEGER NOT NULL DEFAULT 60"),
            ("chat_split_response",
             "ADD COLUMN IF NOT EXISTS chat_split_response BOOLEAN NOT NULL DEFAULT TRUE"),
            ("chat_split_delay_min_ms",
             "ADD COLUMN IF NOT EXISTS chat_split_delay_min_ms INTEGER NOT NULL DEFAULT 1000"),
            ("chat_split_delay_max_ms",
             "ADD COLUMN IF NOT EXISTS chat_split_delay_max_ms INTEGER NOT NULL DEFAULT 2000"),
        ):
            if not _has_column(bind, "agent_configs", col):
                op.execute(f"ALTER TABLE agent_configs {ddl}")


def downgrade():
    # Данные (очереди) при откате теряются; это ожидаемо для чисто рантаймовой
    # очереди. Настройки с agent_configs тоже сносим — иначе ORM без колонок
    # падает, а код задним числом ссылаться на несуществующие поля не должен.
    for col in (
        "pending_messages", "pending_flush_at",
        "pending_first_at", "last_flushed_at",
    ):
        op.execute(f"ALTER TABLE agent_telegram_dialogs DROP COLUMN IF EXISTS {col}")
    op.execute("DROP INDEX IF EXISTS ix_agent_telegram_dialogs_pending_flush_at")
    for col in (
        "chat_batching_enabled", "chat_batching_window_seconds",
        "chat_batching_max_window_seconds", "chat_split_response",
        "chat_split_delay_min_ms", "chat_split_delay_max_ms",
    ):
        op.execute(f"ALTER TABLE agent_configs DROP COLUMN IF EXISTS {col}")
