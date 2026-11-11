"""
Повторный звонок после недозвона — страховка в коде (аудит 11.11.2026).

Раньше после недозвона следующий звонок ставила только модель разбора (create_agent_task). Если она
его не поставила (отправила сообщение «не дозвонились» и всё, ошиблась во времени, разбор упал),
клиент выпадал навсегда: в CRM «В работе», а звонить ему больше некому.

Теперь после разбора ИСХОДЯЩЕГО звонка без ответа, если у клиента нет запланированного звонка:
  1-й недозвон → завтра в начале окна звонков + 1 ч;
  2-й подряд   → послезавтра во второй половине дня (другое время суток — выше шанс дозвониться);
  3-й подряд   → больше не звоним, в карточке «Не дозвонились 3 раза — ждём, когда клиент выйдет на связь».
Задача ставится через fn_create_agent_task — со всеми его проверками (окно звонков, лимиты, «не звонить»,
замена прежних задач). Клиент написал или ответил — счёт заново (считается от последнего разговора).
Только режим full (Profi): Старт звонить первым не может.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

MAX_NO_ANSWER_STREAK = 3


def no_answer_streak(db, contact_id) -> int:
    """Сколько исходящих звонков подряд без ответа (с последнего разговора)."""
    from backend.models.agent_call import AgentCall
    rows = (db.query(AgentCall.status, AgentCall.direction)
            .filter(AgentCall.agent_contact_id == contact_id,
                    AgentCall.status.in_(("answered", "no_answer")))
            .order_by(AgentCall.created_at.desc()).limit(10).all())
    streak = 0
    for status, direction in rows:
        d = (direction or "outbound").lower()
        if d in ("followup_outbound", "telegram_outbound"):
            continue  # наши сообщения — не ответ клиента и не попытка дозвона
        if status == "answered":
            break  # разговор по телефону или клиент написал сам — счёт с нуля
        if d == "outbound":
            streak += 1
    return streak


def _has_scheduled_call(db, contact_id) -> bool:
    from backend.models.task import Task, TaskStatus
    return db.query(Task.id).filter(
        Task.agent_contact_id == contact_id,
        Task.status == TaskStatus.SCHEDULED,
        Task.channel == "call",
    ).first() is not None


def _retry_time(agent_config, streak: int, now_utc: datetime) -> datetime:
    from backend.core.timezone_utils import BISHKEK
    from backend.services.agent_tools import call_window, CALL_WINDOW_START_HOUR, CALL_WINDOW_END_HOUR
    window = call_window(agent_config) or (CALL_WINDOW_START_HOUR, CALL_WINDOW_END_HOUR)
    start, end = window
    today = now_utc.astimezone(BISHKEK).date()
    if streak <= 1:
        day, hour = today + timedelta(days=1), min(start + 1, end - 1)
    else:
        day, hour = today + timedelta(days=2), max(start, min(16, end - 2))
    local = datetime(day.year, day.month, day.day, hour, 7, tzinfo=BISHKEK)
    return local.astimezone(timezone.utc)


async def ensure_retry_after_no_answer(db, agent_config, agent_contact, user, transcript: str = "",
                                       now_utc: Optional[datetime] = None) -> Optional[dict]:
    """Вызывается после разбора исходящего звонка без ответа. Возвращает результат постановки или None."""
    try:
        if "номер не существует" in (transcript or ""):
            return None  # неверный номер: повтор бесполезен, разбор сказал владельцу
        if user is None or user.agent_mode() != "full":
            return None
        from backend.core.pipeline_stages import _TERMINAL_KEYS, get_agent_stages
        closed = {s["key"] for s in get_agent_stages(agent_config) if s.get("kind") in ("won", "lost")} | set(_TERMINAL_KEYS)
        if agent_contact.status in closed:
            return None
        if _has_scheduled_call(db, agent_contact.id):
            return None  # модель уже поставила звонок (или клиент назвал время)
        streak = no_answer_streak(db, agent_contact.id)
        if streak >= MAX_NO_ANSWER_STREAK:
            agent_contact.next_step_text = (f"Не дозвонились {streak} раза подряд — ждём, когда клиент "
                                            "выйдет на связь (напишет или перезвонит)")
            agent_contact.next_step_at = None
            db.commit()
            logger.info(f"[CALL-RETRY] contact {agent_contact.id}: {streak} недозвона подряд — повторов больше нет")
            return {"ok": False, "reason": "max_attempts", "streak": streak}
        now_utc = now_utc or datetime.now(timezone.utc)
        when = _retry_time(agent_config, streak, now_utc)
        from backend.services.agent_tools import fn_create_agent_task
        result = await fn_create_agent_task(
            {
                "agent_contact_id": str(agent_contact.id),
                "scheduled_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "title": f"Повторный звонок (недозвон {streak} из {MAX_NO_ANSWER_STREAK})",
                "notes": "Поставлено системой: после недозвона следующий звонок не был запланирован.",
            },
            str(user.id), str(agent_config.id), db, channel="call",
        )
        logger.info(f"[CALL-RETRY] contact {agent_contact.id}: недозвон {streak} — повтор "
                    f"{when.isoformat()} → {result.get('ok')} {result.get('error') or ''}")
        return result
    except Exception as e:  # noqa: BLE001 — страховка не должна ронять разбор
        logger.warning(f"[CALL-RETRY] contact {getattr(agent_contact, 'id', '?')}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None
