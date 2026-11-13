"""
Оповещения владельцу в Telegram-бот агента (Старт и Profi).

Три события, каждое включается отдельно в настройках агента:
  notify_new_lead      — новый клиент впервые написал или позвонил;
  notify_call_summary  — итог телефонного разговора (входящего или исходящего);
  notify_daily_digest  — сводка за день в 20:00 по Бишкеку.

Отправка идёт через бота агента (fn_send_telegram_notification): если бот не
подключён или выключен, оповещения молча не уходят. Всё здесь best-effort:
ошибка оповещения не должна ломать разбор звонка или переписки.

Дедуп ежедневных заданий — в БД (agent_configs.jobs_ran_on), а не в памяти
процесса: на Render работает несколько воркеров, и каждый крутит фоновые циклы.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DIGEST_HOUR = 20  # Бишкек
NEW_LEAD_WINDOW = timedelta(hours=6)

CHANNEL_TITLES = {
    "whatsapp_inbound": "WhatsApp",
    "telegram_inbound": "Telegram",
    "instagram_inbound": "Instagram",
    "sms_inbound": "SMS",
    "inbound": "входящий звонок",
}

STAGE_TITLES = {
    "new": "Новый",
    "active": "В работе",
    "success": "Успех",
    "rejected": "Отказ",
    "do_not_call": "Не звонить",
}


def _enabled(agent, field: str) -> bool:
    return bool(getattr(agent, field, True))


def _clip(value: Optional[str], limit: int) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _stage(agent, status: Optional[str]) -> str:
    try:
        from backend.core.pipeline_stages import get_agent_stages
        for stage in get_agent_stages(agent) or []:
            if stage.get("key") == status and stage.get("label"):
                return str(stage["label"])
    except Exception:
        pass
    return STAGE_TITLES.get(status or "", status or "—")


def _duration(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _who(contact) -> str:
    name = (contact.name or "").strip()
    phone = (contact.phone or "").strip()
    if phone.startswith(("tg:", "ig:", "wa:")):
        phone = ""
    return " · ".join(p for p in (name or "Без имени", phone) if p)


async def _send(agent, db: Session, message: str) -> bool:
    from backend.services.agent_tools import fn_send_telegram_notification
    try:
        res = await fn_send_telegram_notification({"message": message}, agent, db)
        return bool(res and res.get("ok"))
    except Exception as e:
        logger.warning(f"[OWNER-NOTIFY] send failed for agent {getattr(agent, 'id', '?')}: {e}")
        return False


_BLOCK_TEXT = {
    "credits": ("⚠️ У агента «{name}» закончились кредиты — он перестал отвечать клиентам и разбирать звонки. "
                "Пополните кредиты в кабинете, иначе новые обращения останутся без ответа."),
    "access": ("⚠️ Агент «{name}» не отвечает клиентам: подписка истекла или не оформлена. "
               "Продлите тариф в кабинете, иначе новые обращения останутся без ответа."),
}


async def agent_blocked(db: Session, agent, reason: str) -> bool:
    """
    Агент не может работать (кредиты / подписка) — владельцу один раз в сутки (ветка 1111-clean-system).
    Раньше разбор молча падал: клиент не получал ответа, владелец не знал почему.
    """
    from backend.core.timezone_utils import now_bishkek
    if agent is None or reason not in _BLOCK_TEXT:
        return False
    if not claim_agent_job(db, agent.id, f"blocked_{reason}", now_bishkek().date()):
        return False
    return await _send(agent, db, _BLOCK_TEXT[reason].format(name=getattr(agent, "name", None) or "агент"))


# ---------------------------------------------------------------- после разбора

def format_new_lead(agent, contact, channel: str, first_text: str) -> str:
    lines = [f"🆕 **Новый клиент** · {CHANNEL_TITLES.get(channel, channel)}", _who(contact)]
    if first_text:
        lines.append(f"Пишет: «{_clip(first_text, 220)}»")
    lines.append(f"Стадия: {_stage(agent, contact.status)}")
    return "\n".join(lines)


def format_call_summary(agent, contact, call, direction: str) -> str:
    kind = "Входящий звонок" if direction == "inbound" else "Исходящий звонок"
    answered = (call.status or "") == "answered"
    head = f"📞 **{kind}** · {_duration(call.duration_seconds)}" if answered else f"📞 **{kind}** · не дозвонились"
    lines = [head, _who(contact)]
    memory = contact.memory if isinstance(contact.memory, dict) else {}
    summary = memory.get("summary") if isinstance(memory.get("summary"), str) else ""
    if answered and summary:
        lines.append(f"Итог: {_clip(summary, 300)}")
    lines.append(f"Стадия: {_stage(agent, contact.status)}")
    return "\n".join(lines)


def _is_first_contact(db: Session, contact) -> bool:
    from backend.models.agent_call import AgentCall
    created = contact.created_at
    if created is None or datetime.utcnow() - created > NEW_LEAD_WINDOW:
        return False
    return db.query(AgentCall.id).filter(AgentCall.agent_contact_id == contact.id).count() <= 1


async def _append_lead_row(agent, contact, direction: str, first_text: str) -> None:
    import asyncio
    from backend.services import sheets_leads
    phone = (contact.phone or "").strip()
    if phone.startswith(("tg:", "ig:", "wa:")):
        phone = ""
    try:
        await asyncio.to_thread(
            sheets_leads.append_lead, agent.leads_sheet_id, CHANNEL_TITLES.get(direction, direction),
            (contact.name or "").strip(), phone, _stage(agent, contact.status), first_text,
        )
    except Exception as e:
        logger.warning(f"[OWNER-NOTIFY] lead row not written for agent {agent.id}: {e}")


async def after_postcall(agent_call_id, call_direction: str, first_text: str = "") -> None:
    """Вызывается после разбора звонка или сообщения. Своя сессия БД, без исключений наружу."""
    from backend.db.session import SessionLocal
    from backend.models.agent_call import AgentCall
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact

    direction = (call_direction or "").lower()
    db = SessionLocal()
    try:
        call = db.query(AgentCall).filter(AgentCall.id == agent_call_id).first()
        if call is None or call.agent_contact_id is None:
            return
        agent = db.query(AgentConfig).filter(AgentConfig.id == call.agent_config_id).first()
        contact = db.query(AgentContact).filter(AgentContact.id == call.agent_contact_id).first()
        if agent is None or contact is None:
            return

        is_inbound = direction == "inbound" or direction.endswith("_inbound")
        new_lead = is_inbound and _is_first_contact(db, contact)
        if new_lead and getattr(agent, "leads_sheet_id", None):
            await _append_lead_row(agent, contact, direction, first_text)
        if not agent.telegram_enabled:
            return
        if new_lead and _enabled(agent, "notify_new_lead"):
            await _send(agent, db, format_new_lead(agent, contact, direction, first_text))
            return  # итог первого звонка не дублируем отдельным сообщением

        if direction in ("inbound", "outbound") and _enabled(agent, "notify_call_summary"):
            # Недозвоны на исходящих не шлём по одному — они есть в итогах дня
            if direction == "outbound" and (call.status or "") != "answered":
                return
            await _send(agent, db, format_call_summary(agent, contact, call, direction))
    except Exception as e:
        logger.warning(f"[OWNER-NOTIFY] after_postcall failed for call {agent_call_id}: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------- дедуп заданий

def claim_agent_job(db: Session, agent_id, job: str, day: date) -> bool:
    """Атомарно отметить задание агента за день. True — этот воркер его выполняет."""
    try:
        row = db.execute(text(
            "UPDATE agent_configs "
            "SET jobs_ran_on = jsonb_set(COALESCE(jobs_ran_on, '{}'::jsonb), ARRAY[:job], to_jsonb(CAST(:day AS text))) "
            "WHERE id = :id AND COALESCE(jobs_ran_on ->> :job, '') <> :day "
            "RETURNING id"
        ), {"job": job, "day": day.isoformat(), "id": str(agent_id)}).first()
        db.commit()
        return row is not None
    except Exception as e:
        db.rollback()
        logger.warning(f"[OWNER-NOTIFY] claim {job} failed for agent {agent_id}: {e}")
        return False


# ---------------------------------------------------------------- сводка за день

def _day_bounds_utc(day_local: date):
    from backend.core.timezone_utils import BISHKEK
    start_local = datetime.combine(day_local, datetime.min.time(), tzinfo=BISHKEK)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1)


def collect_daily_stats(db: Session, agent, day_local: date) -> dict:
    from backend.models.agent_call import AgentCall
    from backend.models.agent_contact import AgentContact

    start, end = _day_bounds_utc(day_local)
    calls = db.query(AgentCall).filter(
        AgentCall.agent_config_id == agent.id,
        AgentCall.created_at >= start, AgentCall.created_at < end,
    ).all()
    phone = [c for c in calls if (c.direction or "") in ("inbound", "outbound")]
    chats = [c for c in calls if (c.direction or "") not in ("inbound", "outbound")]
    new_contacts = db.query(AgentContact).filter(
        AgentContact.agent_config_id == agent.id,
        AgentContact.created_at >= start, AgentContact.created_at < end,
    ).count()
    success = db.query(AgentContact).filter(
        AgentContact.agent_config_id == agent.id,
        AgentContact.status == "success",
        AgentContact.updated_at >= start, AgentContact.updated_at < end,
    ).count()
    return {
        "new_contacts": new_contacts,
        "calls": len(phone),
        "calls_answered": sum(1 for c in phone if (c.status or "") == "answered"),
        "calls_missed": sum(1 for c in phone if (c.status or "") in ("no_answer", "failed")),
        "minutes": round(sum(int(c.duration_seconds or 0) for c in phone) / 60),
        "chats": len(chats),
        "success": success,
    }


def format_daily_digest(agent, day_local: date, s: dict) -> str:
    lines = [
        f"📊 **Итоги дня** · {day_local.strftime('%d.%m')}",
        f"Новых клиентов: {s['new_contacts']}",
        f"Переписок обработано: {s['chats']}",
        f"Звонков: {s['calls']} (ответили {s['calls_answered']}, не дозвонились {s['calls_missed']}), {s['minutes']} мин",
        f"Перешли в «Успех»: {s['success']}",
    ]
    return "\n".join(lines)


async def run_daily_digest(db: Session, day_local: date) -> int:
    from backend.models.agent_config import AgentConfig

    sent = 0
    agents = db.query(AgentConfig).filter(
        AgentConfig.is_active == True,  # noqa: E712
        AgentConfig.telegram_enabled == True,  # noqa: E712
    ).all()
    for agent in agents:
        if not _enabled(agent, "notify_daily_digest"):
            continue
        stats = collect_daily_stats(db, agent, day_local)
        if not (stats["new_contacts"] or stats["calls"] or stats["chats"]):
            continue  # пустой день — без шума
        if not claim_agent_job(db, agent.id, "daily_digest", day_local):
            continue
        text = format_daily_digest(agent, day_local, stats)
        try:
            # Склад в итогах дня: что закончилось и где осталось мало (каталог как у магазина, 13.11.2026)
            from backend.services import catalog_service
            ov = catalog_service.catalog_overview(db, agent.id, limit=8)
            if ov.get("out_of_stock_count") or ov.get("low_stock_count"):
                text += "\n\n" + catalog_service.overview_text(ov)
        except Exception as e:
            logger.warning(f"[OWNER-NOTIFY] склад в итогах дня: {e}")
            db.rollback()
        if await _send(agent, db, text):
            sent += 1
    return sent


# ── Напоминания владельцу (задачи channel="owner") ─────────────────────────────────────────────
# Аудит 11.11.2026: напоминание («позвоните клиенту в 15:00» от агента при передаче клиента или
# «напомнить» из инбокса) только висело в списке задач — в назначенное время владелец ничего не
# получал. Теперь планировщик на каждом тике шлёт созревшие напоминания в Telegram-бот владельца один
# раз (атомарная бронь call_started_at — между 4 воркерами), задача остаётся в списке до закрытия.
REMINDER_MAX_AGE = timedelta(days=2)   # старые (до этой правки) не присылаем пачкой


def format_reminder(task, contact) -> str:
    lines = [f"⏰ Напоминание: {_clip(task.title, 200)}"]
    if contact is not None:
        lines.append(f"Клиент: {_who(contact)}")
    if task.description:
        lines.append(_clip(task.description, 600))
    return "\n".join(lines)


async def send_due_reminders(db: Session, now_utc: Optional[datetime] = None) -> int:
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact
    from backend.models.task import Task, TaskStatus
    now_utc = now_utc or datetime.utcnow()
    due = db.query(Task).filter(
        Task.channel == "owner",
        Task.status == TaskStatus.SCHEDULED,
        Task.scheduled_time <= now_utc,
        Task.scheduled_time >= now_utc - REMINDER_MAX_AGE,
        Task.call_started_at.is_(None),
    ).limit(50).all()
    sent = 0
    for task in due:
        claimed = db.query(Task).filter(Task.id == task.id, Task.call_started_at.is_(None)).update(
            {"call_started_at": now_utc}, synchronize_session=False)
        db.commit()
        if not claimed:
            continue  # другой воркер уже отправил
        contact = db.get(AgentContact, task.agent_contact_id) if task.agent_contact_id else None
        agent = db.get(AgentConfig, contact.agent_config_id) if contact is not None and contact.agent_config_id else None
        if agent is None:
            agent = db.query(AgentConfig).filter(AgentConfig.user_id == task.user_id).order_by(
                AgentConfig.created_at.asc()).first()
        if agent is None:
            continue
        if await _send(agent, db, format_reminder(task, contact)):
            sent += 1
            logger.info(f"[OWNER-NOTIFY] reminder {task.id} sent to owner of agent {agent.id}")
    return sent
