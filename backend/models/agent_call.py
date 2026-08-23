"""
AgentCall — запись о каждом звонке агента.
Связывает AgentContact -> Task -> Conversation (транскрипт).

✅ v2.1: Добавлены поля precall_log и postcall_log (JSONB)
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from .base import Base


class AgentCall(Base):
    """
    Запись о звонке автономного агента.
    status: scheduled / calling / answered / no_answer / failed
    post_call_decision: SUCCESS / FOLLOWUP / NO_ANSWER / REJECTED / DO_NOT_CALL
    """
    __tablename__ = "agent_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    agent_contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    # Задача, которая породила звонок
    source_task_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True
    )
    # Задача, созданная PostCall (follow-up)
    next_task_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True
    )

    call_session_id = Column(String(255), nullable=True)
    vox_history_id = Column(String(255), nullable=True)
    pre_call_response_id = Column(String(255), nullable=True)

    custom_greeting = Column(Text, nullable=True)
    call_strategy = Column(Text, nullable=True)

    transcript = Column(Text, nullable=True)
    duration_seconds = Column(Integer, default=0, nullable=False)

    status = Column(String(50), default="scheduled", nullable=False)
    post_call_decision = Column(String(50), nullable=True)
    call_result = Column(Text, nullable=True)

    # Направление звонка: "outbound" (агент позвонил) / "inbound" (позвонили агенту)
    direction = Column(String(20), default="outbound", nullable=False)

    scheduled_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ✅ v2.1: Логи рассуждений оркестратора
    # precall_log: что GPT думал перед звонком (стратегия, тон, ключевые факты)
    precall_log = Column(JSONB, nullable=True)
    # postcall_log: какие tools вызывал, с какими аргументами, финальное решение
    postcall_log = Column(JSONB, nullable=True)

    # Relationships
    contact = relationship("AgentContact", back_populates="calls", foreign_keys=[agent_contact_id])
    agent_config = relationship("AgentConfig", foreign_keys=[agent_config_id])
    source_task = relationship("Task", foreign_keys=[source_task_id])
    next_task = relationship("Task", foreign_keys=[next_task_id])

    __table_args__ = (
        Index("idx_agent_calls_session", "call_session_id"),
        Index("idx_agent_calls_contact", "agent_contact_id"),
        Index("idx_agent_calls_config", "agent_config_id"),
    )

    def __repr__(self):
        return f"<AgentCall {self.id} status={self.status} decision={self.post_call_decision}>"

    def _resolve_channel(self):
        """
        Канал события для UI: "sms", "telegram" или "call".

        Новые записи помечаются через postcall_log.call_direction ("sms_inbound"
        / "telegram_inbound" / "telegram_outbound" — запланированная отправка,
        см. PostCallOrchestrator._analyze / run_for_scheduled_telegram). Для
        старых записей без пометки — fallback по префиксу транскрипта.
        """
        post = self.postcall_log or {}
        if isinstance(post, dict):
            if post.get("call_direction") == "sms_inbound":
                return "sms"
            if post.get("call_direction") in ("telegram_inbound", "telegram_outbound"):
                return "telegram"
            if post.get("call_direction") == "instagram_inbound":
                return "instagram"
        if self.transcript:
            t = self.transcript.strip()
            if t.startswith("Клиент прислал SMS"):
                return "sms"
            if t.startswith("Клиент написал в Telegram"):
                return "telegram"
            if t.startswith("Клиент написал в Instagram"):
                return "instagram"
        return "call"

    def to_dict(self):
        return {
            "id": str(self.id),
            "channel": self._resolve_channel(),
            "agent_contact_id": str(self.agent_contact_id) if self.agent_contact_id else None,
            "agent_config_id": str(self.agent_config_id) if self.agent_config_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "source_task_id": str(self.source_task_id) if self.source_task_id else None,
            "next_task_id": str(self.next_task_id) if self.next_task_id else None,
            "call_session_id": self.call_session_id,
            "vox_history_id": self.vox_history_id,
            "pre_call_response_id": self.pre_call_response_id,
            "custom_greeting": self.custom_greeting,
            "call_strategy": self.call_strategy,
            "transcript": self.transcript,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "direction": self.direction or "outbound",
            "post_call_decision": self.post_call_decision,
            "call_result": self.call_result,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            # ✅ v2.1: Логи оркестратора
            "precall_log": self.precall_log or {},
            "postcall_log": self.postcall_log or {},
        }
