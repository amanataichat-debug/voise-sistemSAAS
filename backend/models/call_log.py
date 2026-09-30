# backend/models/call_log.py
"""
Журнал событий голосовой сессии (таблица call_logs).

Одна строка = один разговор (звонок через SIP-шлюз или сессия виджета):
таймлайн событий (реплики, задержки, перебивания, функции, предупреждения и
ошибки провайдеров) и сводка. Пишет CallLogRecorder
(backend/websockets/call_log.py) в конце разговора; страница «Диалоги»
показывает журнал рядом с диалогом и отдаёт его файлом .txt
(GET /api/conversations/{id}/log).

Связь с диалогом — по session_id (тот же, что в *_conversations.session_id).
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, JSON, String
from sqlalchemy.dialects.postgresql import UUID

from backend.models.base import Base


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(String(100), nullable=False, index=True)
    assistant_type = Column(String(20), nullable=True)
    assistant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    channel = Column(String(20), nullable=True)          # phone | widget
    sip_call_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    duration_sec = Column(Float, nullable=True)
    meta = Column(JSON, nullable=True)                   # ассистент, модели, режим распознавания, номер
    summary = Column(JSON, nullable=True)                # счётчики и задержки
    events = Column(JSON, nullable=True)                 # [{t, level, kind, text, data?}]
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_call_logs_created", "created_at"),
    )
