"""
Номера клиента агента и факты звонков по ним (ветка 2110-client-memory).

Один источник правды для вопросов «какие номера у клиента» и «звонили ли ему
сегодня по-настоящему». Раньше «сегодня уже звонили» смотрели по
AgentContact.last_called_at, а его PostCall пишет в КОНЦЕ анализа: пока модель
разбирала недозвон, проверка видела вчерашнюю дату и пропускала второй звонок
на сегодня (живой тест 21.10.2026). Строка sip_calls появляется в момент
постановки звонка в очередь — по ней факт звонка виден сразу.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.core.logging import get_logger

logger = get_logger(__name__)


def phone_digits(value) -> str:
    """Канонический вид номера (996XXXXXXXXX для КР), как в sip_calls."""
    from backend.models.sip_gateway import normalize_sip_number
    return normalize_sip_number(str(value or ""))


def contact_phone_digits(contact) -> List[str]:
    """Все номера клиента в каноническом виде: основной + дополнительные."""
    out: List[str] = []
    raw = [getattr(contact, "phone", None)]
    raw.extend((getattr(contact, "alt_phones", None) or "").split(","))
    for value in raw:
        digits = phone_digits(value)
        if len(digits) >= 9 and digits not in out:
            out.append(digits)
    return out


def bishkek_day_bounds_utc(local_date) -> tuple:
    """Начало и конец бишкекских суток в UTC (aware: колонки sip_calls — timestamptz)."""
    from backend.core.timezone_utils import BISHKEK
    start_local = datetime.combine(local_date, datetime.min.time(), tzinfo=BISHKEK)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _was_dialed(call) -> bool:
    """Звонок реально ушёл клиенту (или вот-вот уйдёт), а не сломался до набора."""
    from backend.models.sip_gateway import SipCallStatus
    if call.dialed_at is not None or call.answered_at is not None:
        return True
    return call.status in (SipCallStatus.QUEUED, SipCallStatus.DIALING, SipCallStatus.RINGING)


def missed_call_today(db: Session, contact, user_id, local_date) -> Optional[object]:
    """
    Недозвон этому клиенту в бишкекский день local_date, после которого клиент
    на связь не выходил. Возвращает строку SipCall этого недозвона или None.

    Считаются реальные исходящие звонки на любой номер клиента (в том числе
    ручные со страницы «Телефония»): набранные, но без ответа, и стоящие в
    очереди. Если после недозвона был отвеченный звонок (клиент перезвонил сам
    или взял трубку позже) — клиент вышел на связь, блокировки нет: его просьбу
    «перезвоните через час» агент выполнит, как и раньше.
    """
    from backend.models.sip_gateway import SipCall

    numbers = contact_phone_digits(contact)
    if not numbers or user_id is None:
        return None
    start, end = bishkek_day_bounds_utc(local_date)
    try:
        rows = (
            db.query(SipCall)
            .filter(
                SipCall.user_id == user_id,
                SipCall.created_at >= start,
                SipCall.created_at < end,
                or_(
                    (SipCall.direction == "outbound") & SipCall.to_number.in_(numbers),
                    (SipCall.direction == "inbound") & SipCall.caller.in_(numbers),
                ),
            )
            .order_by(SipCall.created_at.asc())
            .all()
        )
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] missed_call_today: sip_calls не прочитаны: {e}")
        return None
    missed = None
    for call in rows:
        if call.answered_at is not None:
            missed = None  # клиент на связи — прежний недозвон больше не держит
        elif call.direction == "outbound" and _was_dialed(call):
            missed = call
    return missed
