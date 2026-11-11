"""
Один разбор на клиента за раз (ветка 1111-clean-system).

Раньше, пока оркестратор 10–40 секунд разбирал сообщение клиента, следующее его
сообщение запускало второй разбор параллельно: клиент получал два ответа подряд,
иногда противоречивых, задачи и этапы ставились дважды. Сейчас перед разбором
клиент «арендуется» атомарным UPDATE по agent_contacts.ai_busy_until — это
работает между всеми воркерами gunicorn. Аренда сама истекает через LEASE
(если процесс упал посреди разбора), снимается в release().

acquire(wait_seconds=0) — попробовать один раз (WhatsApp: не вышло — подметание
повторит позже); wait_seconds>0 — подождать, пока предыдущий разбор закончится
(Telegram/Instagram: их сообщения уже отмечены поллером как полученные).

Аренда повторно входимая в пределах одной задачи asyncio (contextvar _HELD): обработчик входящего
арендует клиента, а сам разбор (PostCallOrchestrator._analyze_v3_openrouter) арендует его ещё раз —
второй вызов сразу проходит. Так разбор после ЗВОНКА и касания воронки тоже идут по одному на клиента
(раньше они шли параллельно с разбором переписки и клиент получал два ответа).
"""

import asyncio
from contextvars import ContextVar
from datetime import datetime, timedelta

from sqlalchemy import or_

from backend.core.logging import get_logger

logger = get_logger(__name__)

LEASE = timedelta(minutes=6)   # с запасом на 2 повтора запроса к модели (openrouter_client)
POLL_SECONDS = 2.0

# Клиенты, арендованные текущей задачей asyncio (повторный вход не ждёт сам себя).
_HELD: ContextVar[frozenset] = ContextVar("contact_lease_held", default=frozenset())


def held(contact_id) -> bool:
    return str(contact_id) in _HELD.get()


def _mark(contact_id, on: bool) -> None:
    cur = _HELD.get()
    key = str(contact_id)
    _HELD.set(cur | {key} if on else cur - {key})


def try_acquire(db, contact_id) -> bool:
    from backend.models.agent_contact import AgentContact
    if held(contact_id):
        return True
    now = datetime.utcnow()
    try:
        claimed = db.query(AgentContact).filter(
            AgentContact.id == contact_id,
            or_(AgentContact.ai_busy_until.is_(None), AgentContact.ai_busy_until < now),
        ).update({"ai_busy_until": now + LEASE}, synchronize_session=False)
        db.commit()
        if claimed:
            _mark(contact_id, True)
        return bool(claimed)
    except Exception as e:
        # Нет колонки / сбой БД — не блокируем ответ клиенту.
        logger.warning(f"[LEASE] acquire failed for contact {contact_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return True


async def acquire(db, contact_id, wait_seconds: float = 0) -> bool:
    deadline = asyncio.get_event_loop().time() + max(0.0, wait_seconds)
    while True:
        if try_acquire(db, contact_id):
            return True
        if asyncio.get_event_loop().time() >= deadline:
            return False
        await asyncio.sleep(POLL_SECONDS)


def release(db, contact_id) -> None:
    from backend.models.agent_contact import AgentContact
    _mark(contact_id, False)
    try:
        db.query(AgentContact).filter(AgentContact.id == contact_id).update(
            {"ai_busy_until": None}, synchronize_session=False)
        db.commit()
    except Exception as e:
        logger.warning(f"[LEASE] release failed for contact {contact_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
