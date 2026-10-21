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
        if ":" in str(value or ""):
            continue  # «ig:<id>» и подобные — не телефон
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


# ---------------------------------------------------------------------------
# Второй номер клиента и поиск карточки по любому его номеру
# ---------------------------------------------------------------------------

PHONE_TAIL_DIGITS = 9  # 700111222: код страны и ведущий 0 пишут по-разному


def phone_tail(value) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-PHONE_TAIL_DIGITS:] if len(digits) >= PHONE_TAIL_DIGITS else ""


def alt_phone_list(contact) -> List[str]:
    """Дополнительные номера карточки (канонические цифры), без основного."""
    return [p for p in (getattr(contact, "alt_phones", None) or "").split(",") if p]


def phone_condition(phone):
    """
    SQL-условие «у карточки есть этот номер» — основной или дополнительный.
    None, если номер слишком короткий для надёжного сравнения.
    """
    from backend.models.agent_contact import AgentContact
    tail = phone_tail(phone)
    if not tail:
        return None
    return or_(
        AgentContact.phone.like(f"%{tail}"),
        AgentContact.alt_phones.like(f"%{tail},%"),
    )


def find_contact_by_phone(db: Session, agent_config_id, phone):
    """Карточка клиента агента по любому его номеру (основному или второму)."""
    from backend.models.agent_contact import AgentContact
    cond = phone_condition(phone)
    if cond is None or not agent_config_id:
        return None
    return (
        db.query(AgentContact)
        .filter(AgentContact.agent_config_id == agent_config_id, cond)
        .order_by(AgentContact.updated_at.desc())
        .first()
    )


def has_phone(contact, phone) -> bool:
    tail = phone_tail(phone)
    return bool(tail) and any(p.endswith(tail) for p in contact_phone_digits(contact))


def add_alt_phone(contact, phone) -> bool:
    """
    Добавить номер в карточку вторым. False — номер пустой/кривой или уже есть
    у карточки (основным или дополнительным). Коммит — за вызывающим.
    """
    digits = phone_digits(phone)
    if len(digits) < PHONE_TAIL_DIGITS or has_phone(contact, digits):
        return False
    phones = alt_phone_list(contact) + [digits]
    contact.alt_phones = "," + ",".join(phones) + ","
    return True


def dial_number(contact) -> str:
    """
    На какой номер звонить клиенту: номер «для звонков» из карточки → основной,
    если он на O! → первый дополнительный на O! → основной (его отклонит проверка
    оператора с понятной подсказкой). Пример: WhatsApp клиента на Beeline,
    в переписке он дал номер O! — звоним на него.
    """
    from backend.models.sip_gateway import is_o_number
    preferred = phone_digits(getattr(contact, "call_phone", None))
    if len(preferred) >= PHONE_TAIL_DIGITS:
        return preferred
    main = phone_digits(getattr(contact, "phone", None))
    if is_o_number(main):
        return main
    for alt in alt_phone_list(contact):
        if is_o_number(alt):
            return alt
    return main or (getattr(contact, "phone", None) or "")
