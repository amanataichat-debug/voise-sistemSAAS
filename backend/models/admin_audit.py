"""
AdminAuditLog — журнал владельца платформы по клиентам (ветка 1811-niche-admin-speed).

Что сюда пишется:
  impersonate  — админ вошёл в кабинет клиента кнопкой «Войти в кабинет» (кто, когда, IP);
  note         — заметка админа по клиенту («позвонил, перезвонить в пятницу»);
  status       — смена статуса клиента в мини-CRM админки (Новый → Позвонил → …);
  profile      — админ поправил телефон / имя / компанию / нишу клиента.

Лента показывается в карточке клиента в админке. Строки клиента удаляются вместе с ним.
Таблицу создаёт app.ensure_user_profile_columns() в начале старта.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    target_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    action = Column(String(32), nullable=False)
    text = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    ip = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_admin_audit_target_created", "target_user_id", "created_at"),
    )
