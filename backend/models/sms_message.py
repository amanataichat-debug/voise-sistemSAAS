# backend/models/sms_message.py
"""
SmsMessage model — маппинг на существующую таблицу sms_messages.
Хранит входящие SMS, полученные через Voximplant webhook.
"""

import uuid
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base, BaseModel


class SmsMessage(Base, BaseModel):
    """Входящее SMS-сообщение, привязанное к дочернему аккаунту Voximplant."""
    __tablename__ = "sms_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    child_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("voximplant_child_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_number = Column(String(20), nullable=False)
    to_number = Column(String(20), nullable=False)
    body = Column(Text, nullable=False)
    direction = Column(String(10), default="inbound")
    is_read = Column(Boolean, default=False)
    vox_account_id = Column(String(50), nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    child_account = relationship("VoximplantChildAccount")

    def __repr__(self):
        return f"<SmsMessage {self.id} from={self.from_number} to={self.to_number}>"

    def to_dict(self):
        data = super().to_dict()
        if isinstance(data.get("id"), uuid.UUID):
            data["id"] = str(data["id"])
        if isinstance(data.get("child_account_id"), uuid.UUID):
            data["child_account_id"] = str(data["child_account_id"])
        return data
