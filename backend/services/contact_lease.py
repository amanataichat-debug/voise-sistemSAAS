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

# Аренда короткая и продлевается, пока разбор жив (_renew_loop). Раньше было 6 минут без
# продления: разбор, оборванный деплоем или падением воркера, держал клиента до 6 минут,
# и его следующее сообщение в Telegram/Instagram ждало LEASE_WAIT_SECONDS (150 с) —
# ответ приходил через 2–3 минуты (15.11.2026).
LEASE = timedelta(seconds=75)
RENEW_SECONDS = 25
# Дольше этого аренду не продлеваем даже без release (страховка от забытого release).
RENEW_MAX_SECONDS = 600
POLL_SECONDS = 1.0

# contact_id → задача продления аренды в этом процессе.
_RENEW: dict = {}

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
            _start_renew(contact_id)
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


def _start_renew(contact_id) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    key = str(contact_id)
    old = _RENEW.get(key)
    if old is not None and not old.done():
        return
    _RENEW[key] = loop.create_task(_renew_loop(key), name=f"lease-{key[:8]}")


def _stop_renew(contact_id) -> None:
    task = _RENEW.pop(str(contact_id), None)
    if task is None or task.done():
        return
    try:
        task.get_loop().call_soon_threadsafe(task.cancel)
    except Exception:
        pass


async def _renew_loop(key: str) -> None:
    """Продлевать аренду, пока разбор идёт (отмена — в release)."""
    from backend.db.session import SessionLocal
    from backend.models.agent_contact import AgentContact
    for _ in range(int(RENEW_MAX_SECONDS // RENEW_SECONDS)):
        await asyncio.sleep(RENEW_SECONDS)
        db = SessionLocal()
        try:
            db.query(AgentContact).filter(
                AgentContact.id == key,
                AgentContact.ai_busy_until.isnot(None),
            ).update({"ai_busy_until": datetime.utcnow() + LEASE}, synchronize_session=False)
            db.commit()
        except Exception as e:
            logger.warning(f"[LEASE] renew failed for contact {key}: {e}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()


def release(db, contact_id) -> None:
    from backend.models.agent_contact import AgentContact
    _mark(contact_id, False)
    _stop_renew(contact_id)
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
