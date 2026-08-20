"""
AgentTelegramChatHistory — per-chat история диалогов агента в Telegram.
Отдельно от agent_configs.chat_history (это веб-чат).

v2.2 Telegram bot integration
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from .base import Base


class AgentTelegramChatHistory(Base):
    __tablename__ = "agent_telegram_chat_histories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_id = Column(String(50), nullable=False)
    chat_title = Column(String(255), nullable=True)
    chat_type = Column(String(20), nullable=True)
    last_sender_user_id = Column(String(50), nullable=True)
    last_sender_username = Column(String(50), nullable=True)
    history = Column(JSONB, default=list, nullable=False)
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agent_config = relationship("AgentConfig", foreign_keys=[agent_config_id])

    __table_args__ = (
        UniqueConstraint("agent_config_id", "chat_id", name="uq_agent_chat"),
        Index("idx_agent_tg_history_last_msg", "last_message_at"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "agent_config_id": str(self.agent_config_id),
            "chat_id": self.chat_id,
            "chat_title": self.chat_title,
            "chat_type": self.chat_type,
            "last_sender_username": self.last_sender_username,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "history_length": len(self.history or []),
        }
