"""
Пауза агента в переписке с клиентом и настройки мессенджеров (ветка 1210-chat-v2).

Пауза хранится на клиенте (agent_contacts.ai_paused_until / ai_pause_reason),
поэтому действует во всех каналах сразу: владелец ведёт клиента в WhatsApp —
агент молчит с ним и в Telegram, и в Instagram.

Кто ставит паузу:
  owner_message — владелец сам написал клиенту (WhatsApp-вебхук, поллеры
                  Telegram и Instagram); длительность — настройка коннектора
                  owner_pause_minutes, каждое новое сообщение продлевает паузу;
  manual        — кнопка «Остановить агента» в карточке клиента;
  command       — команда владельца в чате агента (pause_agent_for_contact).

Пока пауза: входящие клиента сохраняются в тред, но оркестратор не запускается
(handle_inbound_* в agent_orchestrator), запланированные Telegram-сообщения не
уходят. Звонки и отправка по прямой команде владельца не затрагиваются.
Пауза снимается сама, когда ai_paused_until в прошлом.
"""

from datetime import datetime, timedelta
from typing import Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

# «Навсегда» — дата, до которой пауза не истечёт.
AI_PAUSE_FOREVER = datetime(9999, 12, 31)

# Варианты в UI и тулзах: минуты; -1 — навсегда.
PAUSE_CHOICES = (10, 30, 60, 1440, -1)
DEFAULT_OWNER_PAUSE_MINUTES = 30

# «Отвечать через N секунд после последнего сообщения клиента».
REPLY_DELAY_CHOICES = (5, 10, 15, 20, 30, 60)
DEFAULT_REPLY_DELAY_SECONDS = 15

REASONS = ("owner_message", "manual", "command")


def owner_pause_minutes(row) -> int:
    """Настройка коннектора (строка подключения) с запасным значением."""
    v = getattr(row, "owner_pause_minutes", None)
    return int(v) if v is not None else DEFAULT_OWNER_PAUSE_MINUTES


def reply_delay_seconds(row) -> int:
    v = getattr(row, "reply_delay_seconds", None)
    return int(v) if v is not None else DEFAULT_REPLY_DELAY_SECONDS


def valid_pause_minutes(v) -> bool:
    return v in PAUSE_CHOICES


def valid_reply_delay(v) -> bool:
    return v in REPLY_DELAY_CHOICES


def apply_messenger_settings(row, body) -> None:
    """
    PATCH настроек коннектора (WhatsApp / личный Telegram / Instagram): поля
    owner_pause_minutes, reply_delay_seconds и voice_reply_mode (только
    WhatsApp и Telegram). Неверное значение → HTTP 400.
    """
    from fastapi import HTTPException
    v = getattr(body, "owner_pause_minutes", None)
    if v is not None:
        if not valid_pause_minutes(v):
            raise HTTPException(status_code=400, detail="invalid_owner_pause")
        row.owner_pause_minutes = v
    v = getattr(body, "reply_delay_seconds", None)
    if v is not None:
        if not valid_reply_delay(v):
            raise HTTPException(status_code=400, detail="invalid_reply_delay")
        row.reply_delay_seconds = v
    v = getattr(body, "voice_reply_mode", None)
    if v is not None:
        from backend.services.agent_voice_service import VOICE_REPLY_MODES
        if v not in VOICE_REPLY_MODES or not hasattr(row, "voice_reply_mode"):
            raise HTTPException(status_code=400, detail="invalid_voice_reply_mode")
        row.voice_reply_mode = v


def is_paused(contact, now: Optional[datetime] = None) -> bool:
    if contact is None:
        return False
    until = getattr(contact, "ai_paused_until", None)
    return until is not None and until > (now or datetime.utcnow())


def pause_until_for(minutes: int, start: Optional[datetime] = None) -> datetime:
    if minutes is None or minutes < 0:
        return AI_PAUSE_FOREVER
    return (start or datetime.utcnow()) + timedelta(minutes=int(minutes))


def on_owner_message(db, contact_id, minutes: int, msg_ts: Optional[datetime] = None) -> bool:
    """
    Владелец сам написал клиенту: пауза до max(текущая, время сообщения +
    длительность). Старые сообщения (досинхронизация истории) паузу не ставят —
    их «время + длительность» уже в прошлом. Без commit. True — пауза продлена.
    """
    from backend.models.agent_contact import AgentContact

    if not contact_id:
        return False
    contact = db.query(AgentContact).filter(AgentContact.id == contact_id).first()
    if contact is None:
        return False
    now = datetime.utcnow()
    start = min(msg_ts or now, now)
    until = pause_until_for(minutes, start)
    if until <= now:
        return False
    current = contact.ai_paused_until if is_paused(contact, now) else None
    if current is not None and current >= until:
        return False
    contact.ai_paused_until = until
    contact.ai_pause_reason = "owner_message"
    logger.info(f"[AGENT-PAUSE] contact {contact.id}: owner wrote → agent silent until {until:%Y-%m-%d %H:%M} UTC")
    return True


def set_pause(contact, minutes: int, reason: str) -> datetime:
    """Ручная пауза (кнопка / команда): ставится ровно на срок, без max. Без commit."""
    until = pause_until_for(minutes)
    contact.ai_paused_until = until
    contact.ai_pause_reason = reason if reason in REASONS else "manual"
    logger.info(f"[AGENT-PAUSE] contact {contact.id}: paused ({contact.ai_pause_reason}) until {until:%Y-%m-%d %H:%M} UTC")
    return until


def clear_pause(contact) -> None:
    contact.ai_paused_until = None
    contact.ai_pause_reason = None
    logger.info(f"[AGENT-PAUSE] contact {contact.id}: agent resumed")


def describe(contact) -> str:
    """Короткая строка для тулз и логов: «на паузе до 18:30 Бишкек» / «работает»."""
    if not is_paused(contact):
        return "агент работает"
    until = contact.ai_paused_until
    if until >= AI_PAUSE_FOREVER - timedelta(days=1):
        return "агент отключён в этом чате навсегда"
    from backend.core.timezone_utils import utc_to_bishkek
    return f"агент на паузе до {utc_to_bishkek(until).strftime('%d.%m %H:%M')} Бишкек"
