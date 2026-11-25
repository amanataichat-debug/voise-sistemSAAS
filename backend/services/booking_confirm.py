"""
Подтверждение записи клиентом в два шага (Voksy Beauty, шаг 1.6а; план — docs/voksy_beauty_plan.md, 2.9).

Бот в переписке (WhatsApp / Telegram / Instagram) не записывает, не переносит и не отменяет сразу:
  1) инструмент проверяет действие ПРОБНОЙ записью (savepoint → откат: время, мастер, услуга, цена,
     предоплата — всё как при настоящей записи) и возвращает итог словами + confirmation_id;
  2) бот отправляет итог клиенту и спрашивает «Записываю?»;
  3) выполнить можно, только если ПОСЛЕ отправленного итога клиент ответил согласием — это проверяет
     код по ленте сообщений (а не модель): «да», «ок», «записывайте», «ооба», «макул», 👍 …
     «нет / подождите / другое время» — отказ, ничего не меняется. Время проверяется ещё раз.

Владелец в своём чате и голосовой агент на звонке подтверждают иначе (владелец сам командует, на звонке
клиент соглашается голосом) — для них два шага не нужны.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PENDING_HOURS = 48               # итог живёт двое суток: клиент может ответить завтра
ACTIONS = {"create": "записать", "reschedule": "перенести", "cancel": "отменить"}

# Согласие клиента. Сильные «да» — в начале любой фразы, мягкие — только в коротком ответе
# («давайте, а цена какая?» — уже вопрос, а не согласие).
_STRONG_YES = {
    "да", "даа", "ага", "угу", "конечно", "ок", "окей", "ok", "okay", "верно", "правильно", "подтверждаю",
    "именно", "точно", "yes", "ооба", "оба", "ообо", "макул", "туура", "албетте", "записывайте", "запишите",
    "записывай", "запиши", "подходит", "устраивает", "договорились", "отлично", "супер", "жазыңыз", "жазып",
    "жаз", "жазгыла", "бронируйте", "забронируйте", "отменяйте", "отмените", "переносите", "перенесите",
}
_SOFT_YES = {
    "давайте", "давай", "хорошо", "можно", "пойдёт", "пойдет", "норм", "нормально", "го", "жарайт", "жарайсың",
    "мейли", "болот", "болуптур", "сонун", "ладно", "согласна", "согласен", "конечно",
}
_NO = {
    "нет", "не", "неа", "стоп", "подождите", "погодите", "подожди", "стой", "жок", "токто", "күтө", "no",
    "передумала", "передумал",
}
_NO_PHRASES = (
    "не надо", "не нужно", "не подходит", "не устраивает", "другое время", "другой день", "другого мастера",
    "керек эмес", "болбойт", "ыңгайсыз", "не записывайте", "не отменяйте", "не переносите", "попозже",
    "позже", "давайте другое", "можно другое",
)
_YES_EMOJI = ("👍", "👌", "✅", "🙏", "❤", "🔥", "💯")
_WORD = re.compile(r"[a-zа-яёөүңәіұқғһ]+", re.IGNORECASE)
SHORT_REPLY_WORDS = 5


def client_consent(message: Optional[str]) -> Optional[str]:
    """'yes' — клиент согласился, 'no' — отказался или просит другое, None — ни то ни другое."""
    raw = (message or "").strip()
    low = raw.lower().replace("ё", "е")
    if not low:
        return None
    if any(p in low for p in _NO_PHRASES):
        return "no"
    words = _WORD.findall(low)
    if not words:
        return "yes" if any(e in raw for e in _YES_EMOJI) else None
    if words[0] in _NO:
        return "no"
    if "?" in raw and len(words) > 3:          # «да, а можно на 16:00?» — уже вопрос, а не согласие
        return None
    if words[0] in _STRONG_YES:
        return "yes"
    if len(words) <= SHORT_REPLY_WORDS:
        if any(w in _NO for w in words):
            return "no"
        if any(w in _STRONG_YES or w in _SOFT_YES for w in words):
            return "yes"
        if any(e in raw for e in _YES_EMOJI):
            return "yes"
    return None


# ───────────────────────────── хранение ─────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create_pending(db: Session, user_id, contact_id, action: str, payload: Dict[str, Any], summary: str):
    """Новый итог ждёт «да». Прежние неиспользованные итоги этого клиента на то же действие — снимаются."""
    from backend.models.booking import BookingConfirmation
    cid = uuid.UUID(str(contact_id)) if contact_id else None
    if cid is not None:
        (db.query(BookingConfirmation)
         .filter(BookingConfirmation.agent_contact_id == cid, BookingConfirmation.action == action,
                 BookingConfirmation.used_at.is_(None))
         .update({BookingConfirmation.used_at: _now()}, synchronize_session=False))
    row = BookingConfirmation(id=uuid.uuid4(), user_id=user_id, agent_contact_id=cid, action=action,
                              payload=payload, summary=summary[:1000], created_at=_now())
    db.add(row)
    db.flush()
    return row


def find_pending(db: Session, user_id, contact_id, action: str, confirmation_id: Optional[str] = None):
    """Живой итог этого клиента: по id или последний на это действие (бот мог не запомнить id между сообщениями)."""
    from backend.models.booking import BookingConfirmation
    if not contact_id:
        return None
    q = db.query(BookingConfirmation).filter(
        BookingConfirmation.user_id == user_id,
        BookingConfirmation.agent_contact_id == uuid.UUID(str(contact_id)),
        BookingConfirmation.action == action,
        BookingConfirmation.used_at.is_(None),
        BookingConfirmation.created_at >= _now() - timedelta(hours=PENDING_HOURS),
    )
    if confirmation_id:
        try:
            q = q.filter(BookingConfirmation.id == uuid.UUID(str(confirmation_id)))
        except ValueError:
            return None
    return q.order_by(BookingConfirmation.created_at.desc()).first()


def mark_used(row, booking_id=None) -> None:
    row.used_at = _now()
    if booking_id:
        row.booking_id = uuid.UUID(str(booking_id))


# ───────────────────────────── лента переписки ─────────────────────────────

_THREAD_SQL = """
SELECT direction, sent_by, body, created_at FROM (
    SELECT direction, sent_by, body, created_at FROM agent_whatsapp_messages WHERE agent_contact_id = :cid AND created_at > :t
    UNION ALL
    SELECT direction, sent_by, body, created_at FROM agent_telegram_messages WHERE agent_contact_id = :cid AND created_at > :t
    UNION ALL
    SELECT direction, sent_by, body, created_at FROM agent_instagram_messages WHERE agent_contact_id = :cid AND created_at > :t
) m ORDER BY created_at
"""


def messages_since(db: Session, contact_id, since: datetime) -> List[Dict[str, Any]]:
    t = _aware(since).astimezone(timezone.utc).replace(tzinfo=None)     # в таблицах сообщений — UTC без пояса
    rows = db.execute(text(_THREAD_SQL), {"cid": str(contact_id), "t": t}).fetchall()
    return [{"direction": r[0], "sent_by": r[1], "body": r[2] or "", "created_at": r[3]} for r in rows]


def consent_state(messages: List[Dict[str, Any]]) -> Tuple[str, Optional[str]]:
    """По сообщениям после создания итога: ('not_shown' | 'wait' | 'unclear' | 'no' | 'yes', последний ответ).

    Итог должен уйти клиенту (исходящее после создания), а согласие — прийти ПОСЛЕ него. Последнее
    «нет» после «да» побеждает: «да… хотя нет, давайте в субботу»."""
    shown = None
    for i, m in enumerate(messages):
        if m["direction"] == "outbound":
            shown = i
            break
    if shown is None:
        return "not_shown", None
    answers = [m for m in messages[shown + 1:] if m["direction"] == "inbound" and (m["body"] or "").strip()]
    if not answers:
        return "wait", None
    verdict = None
    for m in answers:
        v = client_consent(m["body"])
        if v is not None:
            verdict = v
    last = answers[-1]["body"]
    return (verdict or "unclear"), last


def check_consent(db: Session, row) -> Optional[str]:
    """None — клиент согласился, действие можно выполнять; иначе объяснение для бота."""
    if row.agent_contact_id is None:
        return None
    state, last = consent_state(messages_since(db, row.agent_contact_id, row.created_at))
    what = ACTIONS.get(row.action, "выполнить")
    if state == "not_shown":
        return ("Ещё не выполнено: сначала отправь клиенту итог одним сообщением — «" + row.summary +
                "» — и спроси, всё ли верно. " + what.capitalize() + " можно после его «да».")
    if state == "wait":
        return ("Ещё не выполнено: клиент пока не ответил на итог. Ничего не пиши повторно — дождись ответа; "
                "действие выполнится по его «да».")
    if state == "no":
        return ("Клиент НЕ согласился (" + (last or "")[:120] + ") — ничего не изменено. Спроси, что поменять, "
                "или подбери другое время через find_booking_slots.")
    if state == "unclear":
        return ("Из ответа клиента (" + (last or "")[:120] + ") не ясно, согласен ли он. Ответь на его вопрос "
                "и коротко спроси, " + what + " ли по итогу; выполнить можно после явного «да».")
    return None


def pending_block(db: Session, user_id, contact_id) -> str:
    """Строка для разбора переписки: что бот предложил клиенту и ждёт ли «да»."""
    try:
        from backend.models.booking import BookingConfirmation
        if not contact_id:
            return ""
        rows = (db.query(BookingConfirmation)
                .filter(BookingConfirmation.user_id == user_id,
                        BookingConfirmation.agent_contact_id == uuid.UUID(str(contact_id)),
                        BookingConfirmation.used_at.is_(None),
                        BookingConfirmation.created_at >= _now() - timedelta(hours=PENDING_HOURS))
                .order_by(BookingConfirmation.created_at.desc()).limit(3).all())
        if not rows:
            return ""
        tools = {"create": "create_booking", "reschedule": "reschedule_booking", "cancel": "cancel_booking"}
        lines = [f"- {ACTIONS.get(r.action, r.action)}: {r.summary} → после «да» клиента вызови "
                 f"{tools.get(r.action)} с confirmation_id={r.id}" for r in rows]
        return ("\n\nЖДЁТ ПОДТВЕРЖДЕНИЯ КЛИЕНТА (запись):\n" + "\n".join(lines) +
                "\nСогласился — выполни, отказался или просит другое — подбери новое, не выполняй старое.")
    except Exception as e:
        logger.warning(f"[BOOKING-CONFIRM] pending block: {e}")
        return ""


def needs_client_confirmation(context: Dict[str, Any]) -> bool:
    """Два шага нужны только в разборе ПЕРЕПИСКИ с клиентом (не в чате владельца и не после звонка)."""
    if context.get("surface") != "postcall":
        return False
    try:
        from backend.services.agent_orchestrator import is_voice_direction
        return not is_voice_direction(context.get("call_direction"))
    except Exception:
        return (context.get("call_direction") or "").lower().endswith(("_inbound", "_outbound")) and \
            (context.get("call_direction") or "").lower() not in ("inbound", "outbound")
