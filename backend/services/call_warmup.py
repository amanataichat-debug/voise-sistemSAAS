"""
Прогрев клиента за 25–35 минут до запланированного звонка (ветка 1810-anri-sales-logic).

Что делаем: сканируем SCHEDULED call-задачи, у которых scheduled_time в окне
25–35 минут от «сейчас», и отправляем клиенту короткое сообщение в последний
активный канал («у нас созвон через полчаса, вы на связи?»). Клиент ответил
«ок» — агент (в своём обычном потоке) видит сообщение и звонит по плану.
Ответил «перенесите на 15:00» — агент перенесёт задачу обычным
update_agent_task. Молчит — всё равно звоним по плану.

Дубли отсекаем по `contact.memory["warmed_up_task_ids"]` (список id задач,
по которым прогрев уже отправлен). Это достаточно: окно сканирования 10
минут × тик планировщика 30 с → задача попадает ~20 раз, и каждая проверка
отрабатывает по списку.

Планировщик вызывает `scan_and_send(db)` из `check_and_execute_tasks` раз
в тик (30 с). При любой ошибке молчим в warning — тикер не роняем.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)

# Окно прогрева: от 25 до 35 минут до звонка. Сузили в сравнении с «за 30»
# ровно, чтобы один скан (раз в 30 с) успел захватить задачу хотя бы раз.
WARMUP_WINDOW_MIN = 25
WARMUP_WINDOW_MAX = 35

WARMUP_TEMPLATE = (
    "{greeting}! У нас созвон через полчаса. Вы на связи? "
    "Если неудобно — скажите, когда будет лучше, перенесу."
)


def _greeting(contact: AgentContact) -> str:
    name = (contact.name or "").strip()
    if name:
        return f"{name}, здравствуйте"
    return "Здравствуйте"


def _already_warmed(contact: AgentContact, task_id) -> bool:
    mem = contact.memory if isinstance(contact.memory, dict) else {}
    warmed = mem.get("warmed_up_task_ids") or []
    return str(task_id) in {str(x) for x in warmed}


def _mark_warmed(contact: AgentContact, task_id) -> None:
    mem = dict(contact.memory or {})
    warmed = list(mem.get("warmed_up_task_ids") or [])
    if str(task_id) not in {str(x) for x in warmed}:
        warmed.append(str(task_id))
    # Храним только последние 50 id, чтобы memory не распухал.
    mem["warmed_up_task_ids"] = warmed[-50:]
    contact.memory = mem
    flag_modified(contact, "memory")


async def _send_warmup_message(
    channel: str,
    text: str,
    agent_contact_id,
    user_id,
    agent_config,
    db: Session,
) -> bool:
    """Отправить короткое сообщение в выбранный канал. True — отправлено."""
    # Лениво импортируем, чтобы избежать циклической зависимости со
    # scheduler → agent_tools → services.
    from backend.services import agent_tools

    send_args = {"agent_contact_id": str(agent_contact_id), "text": text}
    if channel == "telegram":
        res = await agent_tools.fn_telegram_send_message(
            send_args, user_id=str(user_id), agent_config=agent_config, db=db,
        )
    elif channel == "whatsapp":
        res = await agent_tools.fn_whatsapp_send_message(
            send_args, user_id=str(user_id), agent_config=agent_config, db=db,
        )
    elif channel == "instagram":
        res = await agent_tools.fn_instagram_send_message(
            send_args, user_id=str(user_id), agent_config=agent_config, db=db,
        )
    else:
        logger.warning(f"[WARMUP] unsupported channel: {channel}")
        return False
    ok = bool(res and res.get("ok"))
    if not ok:
        logger.warning(f"[WARMUP] send to {channel} failed: {res}")
    return ok


async def scan_and_send(db: Session) -> int:
    """Один проход: найти задачи в окне и отправить прогрев. Возвращает число отправленных."""
    from backend.services import agent_tools  # lazy

    now = datetime.utcnow()
    window_start = now + timedelta(minutes=WARMUP_WINDOW_MIN)
    window_end = now + timedelta(minutes=WARMUP_WINDOW_MAX)
    try:
        tasks = db.query(Task).filter(
            Task.status == TaskStatus.SCHEDULED,
            Task.is_agent_task == True,
            Task.channel == "call",
            Task.scheduled_time >= window_start,
            Task.scheduled_time < window_end,
        ).all()
    except Exception as e:
        logger.warning(f"[WARMUP] scan failed: {e}")
        return 0

    sent = 0
    for task in tasks:
        if not task.agent_contact_id:
            continue
        try:
            contact = db.query(AgentContact).filter(
                AgentContact.id == task.agent_contact_id
            ).first()
            if contact is None:
                continue
            if _already_warmed(contact, task.id):
                continue
            agent_config = db.query(AgentConfig).filter(
                AgentConfig.id == contact.agent_config_id
            ).first() if contact.agent_config_id else None
            if agent_config is None:
                continue

            channel = agent_tools._detect_recent_channel(
                db, str(agent_config.id), str(contact.id)
            )
            if not channel:
                # Клиент никогда нам не писал — прогрев отправлять некуда.
                # Помечаем задачу как «обработанную» через memory, чтобы не
                # возвращаться к ней каждый тик.
                _mark_warmed(contact, task.id)
                db.commit()
                continue

            text = WARMUP_TEMPLATE.format(greeting=_greeting(contact))
            ok = await _send_warmup_message(
                channel, text, contact.id, contact.user_id, agent_config, db,
            )
            if ok:
                _mark_warmed(contact, task.id)
                db.commit()
                sent += 1
                logger.info(
                    f"[WARMUP] sent to {channel} for task {task.id} "
                    f"(contact {contact.id}, call at {task.scheduled_time.isoformat()})"
                )
            else:
                # Не отправилось — на следующем тике попробуем ещё раз, пока
                # не выйдем из окна. Не помечаем как warmed, чтобы retry был.
                pass
        except Exception as e:
            logger.warning(f"[WARMUP] task {task.id}: {e}")
            try:
                db.rollback()
            except Exception:
                pass
    return sent
