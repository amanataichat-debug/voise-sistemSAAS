"""
SMS history — единый тред исходящих/входящих SMS поверх таблицы sms_messages.

Используется в нескольких местах:
- точки отправки (agent_tools.fn_send_sms, functions/send_sms) — сохраняют
  исходящие SMS (direction='outbound'), чтобы тред был полным;
- оркестратор (PreCall/PostCall) — кладёт переписку в контекст агента;
- карточка контакта в UI — показывает тред.

Связь с конкретным контактом агента — по номеру (последние 10 цифр), без FK:
один номер однозначно резолвится в контакт (вариант А).
"""

from typing import Optional, List
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.core.timezone_utils import utc_to_msk
from backend.models.sms_message import SmsMessage

logger = get_logger(__name__)


def phone_suffix(phone: str) -> str:
    """Последние 10 цифр номера — ключ матчинга SMS ↔ контакт."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def store_outbound_sms(
    db: Session,
    child_account_id,
    vox_account_id,
    from_number: str,
    to_number: str,
    body: str,
) -> Optional[SmsMessage]:
    """
    Сохранить исходящее SMS в sms_messages (direction='outbound').

    Best-effort: ошибка записи не должна ронять саму отправку — логируем и
    возвращаем None.
    """
    try:
        sms = SmsMessage(
            child_account_id=child_account_id,
            vox_account_id=str(vox_account_id) if vox_account_id else "",
            from_number=(from_number or "")[:20],
            to_number=(to_number or "")[:20],
            body=body or "",
            direction="outbound",
            is_read=True,
        )
        db.add(sms)
        db.commit()
        return sms
    except Exception as e:
        logger.error(f"[SMS-HISTORY] store_outbound_sms failed: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def get_sms_thread(db: Session, child_account_id, phone: str, limit: int = 20) -> List[SmsMessage]:
    """
    Последние `limit` SMS обоих направлений с контактом (по последним 10 цифрам
    номера), в хронологическом порядке (старые → новые).
    """
    suffix = phone_suffix(phone)
    if not child_account_id or not suffix:
        return []
    rows = (
        db.query(SmsMessage)
        .filter(
            SmsMessage.child_account_id == child_account_id,
            or_(
                SmsMessage.from_number.like(f"%{suffix}"),
                SmsMessage.to_number.like(f"%{suffix}"),
            ),
        )
        .order_by(SmsMessage.received_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def sms_thread_to_dicts(rows: List[SmsMessage]) -> List[dict]:
    """Сериализация треда для фронта (карточка контакта)."""
    out = []
    for m in rows:
        ts = m.received_at or m.created_at
        out.append({
            "id": str(m.id),
            "direction": m.direction or "inbound",
            "from_number": m.from_number,
            "to_number": m.to_number,
            "body": m.body,
            "ts": ts.isoformat() if ts else None,
        })
    return out


def build_sms_thread_text(db: Session, child_account_id, phone: str, limit: int = 20) -> str:
    """
    Текстовый блок переписки для промпта оркестратора (pre/post-call).
    Пустая строка, если переписки нет.
    """
    rows = get_sms_thread(db, child_account_id, phone, limit)
    if not rows:
        return ""
    lines = []
    for m in rows:
        ts = m.received_at or m.created_at
        ts_str = utc_to_msk(ts).strftime("%d.%m %H:%M") if ts else "?"
        who = "Агент → клиенту" if (m.direction or "inbound") == "outbound" else "Клиент → агенту"
        text = (m.body or "").strip().replace("\n", " ")
        lines.append(f"[{ts_str}] {who}: {text}")
    return "\n".join(lines)
