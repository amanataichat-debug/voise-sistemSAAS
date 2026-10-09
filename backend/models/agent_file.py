"""
AgentFile — файлы агента обзвона: библиотека владельца, вложения клиентов и
документы, созданные самим агентом.

source:
  library   — владелец загрузил в кабинете (прайс, презентация, договор);
              агент отправляет их клиентам тулзами telegram_send_file /
              instagram_send_file.
  inbound   — клиент прислал в личный Telegram или Instagram (голосовое, фото,
              документ, видео). Голос распознаётся Whisper, картинки и сканы —
              OCR/vision-моделью, из документов извлекается текст
              (services/agent_media_service.py). Результат — extracted_text.
  generated — агент создал сам тулзой create_document (PDF/DOCX/XLSX/TXT).

Сами байты лежат в R2 (storage_key, префикс agent-files/), в БД — только
метаданные и извлечённый текст. Таблица создаётся стартовым create_all.
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, Float, BigInteger, Index
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


# Виды вложений (kind)
FILE_KINDS = ("voice", "audio", "video", "image", "document", "other")


class AgentFile(Base):
    __tablename__ = "agent_files"
    __table_args__ = (
        Index("ix_agent_files_agent_source", "agent_config_id", "source"),
        Index("ix_agent_files_contact", "agent_contact_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Контакт, от которого пришло вложение / для которого создан документ.
    agent_contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_contacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    source = Column(String(16), default="library", nullable=False)
    channel = Column(String(16), nullable=True)          # telegram / instagram / whatsapp
    external_message_id = Column(String(255), nullable=True)

    kind = Column(String(16), default="document", nullable=False)
    filename = Column(String(255), nullable=False)
    mime_type = Column(String(128), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    storage_key = Column(String(512), nullable=True)

    # Для библиотеки: как файл называется для агента и когда его отправлять.
    title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)

    # Расшифровка голосового / текст документа / OCR и описание картинки.
    extracted_text = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # processing / ready / failed / skipped
    status = Column(String(16), default="processing", nullable=False)
    error = Column(Text, nullable=True)
    credits_charged = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self, with_text: bool = False) -> dict:
        d = {
            "id": str(self.id),
            "source": self.source,
            "channel": self.channel,
            "kind": self.kind,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "credits_charged": self.credits_charged or 0,
            "has_text": bool(self.extracted_text),
            "agent_contact_id": str(self.agent_contact_id) if self.agent_contact_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if with_text:
            d["extracted_text"] = self.extracted_text
        return d
