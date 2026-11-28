"""
Звонки WhatsApp голосового агента через свой шлюз (VPS wa-call-1, infra/wa-call-gateway/,
ТЗ docs/tz_whatsapp_calls.md).

Одна таблица — agent_wa_call_accounts: номер WhatsApp, подключённый к шлюзу звонков
как связанное устройство (переписка этого номера по-прежнему идёт через Evolution —
agent_whatsapp_accounts), и его привязка к агенту. Сами звонки пишутся в sip_calls с
call_metadata.channel = "whatsapp": журнал, «Диалоги», запись, стенограмма и PostCall
уже работают на этой таблице. Сессия WhatsApp живёт ТОЛЬКО на шлюзе, в базе её нет.

Таблицу создаёт app.ensure_wa_call_tables() в начале старта в каждом воркере.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


class WaCallStatus:
    PENDING_QR = "pending_qr"      # ждём сканирования QR (шлюз прислал код или ещё пришлёт)
    CONNECTED = "connected"        # номер на связи, звонки принимаются
    DISCONNECTED = "disconnected"  # связь шлюза с WhatsApp прервалась, переподключается сам
    LOGGED_OUT = "logged_out"      # устройство удалено с телефона — нужен новый QR
    BANNED = "banned"              # WhatsApp временно заблокировал номер
    ERROR = "error"


# Состояние аккаунта от шлюза (account.state) → статус в базе
GATEWAY_STATE_TO_STATUS = {
    "qr": WaCallStatus.PENDING_QR,
    "qr_expired": WaCallStatus.PENDING_QR,
    "connecting": WaCallStatus.DISCONNECTED,
    "connected": WaCallStatus.CONNECTED,
    "disconnected": WaCallStatus.DISCONNECTED,
    "logged_out": WaCallStatus.LOGGED_OUT,
    "banned": WaCallStatus.BANNED,
    "error": WaCallStatus.ERROR,
}


class AgentWaCallAccount(Base):
    __tablename__ = "agent_wa_call_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_config_id = Column(UUID(as_uuid=True), ForeignKey("agent_configs.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    gateway_id = Column(String(50), nullable=False, default="wa-call-1")

    # Номер WhatsApp (E.164 без плюса) — приходит от шлюза после входа по QR
    phone = Column(String(32), nullable=True, index=True)
    push_name = Column(String(255), nullable=True)

    status = Column(String(20), nullable=False, default=WaCallStatus.PENDING_QR)
    last_error = Column(Text, nullable=True)
    # QR для входа: код (строка, рисуется в кабинете / скрипте) и до какого времени он живёт
    qr_code = Column(Text, nullable=True)
    qr_expires_at = Column(DateTime(timezone=True), nullable=True)
    # Попросили шлюз показать новый QR / отвязать номер: управляющий сокет отправит команду и обнулит
    login_requested_at = Column(DateTime(timezone=True), nullable=True)
    logout_requested_at = Column(DateTime(timezone=True), nullable=True)

    inbound_enabled = Column(Boolean, nullable=False, default=True)    # принимать звонки
    outbound_enabled = Column(Boolean, nullable=False, default=False)  # звонить клиентам самому
    daily_outbound_limit = Column(Integer, nullable=False, default=20)
    first_phrase = Column(Text, nullable=True)  # первая фраза входящего (иначе — inbound_first_phrase агента)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    connected_at = Column(DateTime(timezone=True), nullable=True)
    last_state_at = Column(DateTime(timezone=True), nullable=True)

    def is_connected(self) -> bool:
        return self.status == WaCallStatus.CONNECTED

    def to_dict(self, with_qr: bool = False) -> dict:
        data = {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "agent_config_id": str(self.agent_config_id),
            "gateway_id": self.gateway_id,
            "phone": self.phone,
            "push_name": self.push_name,
            "status": self.status,
            "last_error": self.last_error,
            "inbound_enabled": bool(self.inbound_enabled),
            "outbound_enabled": bool(self.outbound_enabled),
            "daily_outbound_limit": self.daily_outbound_limit,
            "first_phrase": self.first_phrase,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
        }
        if with_qr:
            data["qr_code"] = self.qr_code
            data["qr_expires_at"] = self.qr_expires_at.isoformat() if self.qr_expires_at else None
        return data
