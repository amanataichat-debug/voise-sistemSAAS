"""
Еженедельные фоновые задачи Анри (ветка 1810-anri-sales-logic).

Два задания, оба идут по бишкекскому времени:

* **Еженедельный отчёт** — воскресенье 21:00±15 мин. Для каждого активного
  агента собирает статистику за прошедшие 7 дней и шлёт карточку в
  Telegram-бот владельца. Содержит: сколько звонков всего, сколько успешных
  (post_call_decision=SUCCESS), сколько недозвонов, сколько новых контактов,
  сколько клиентов перешло в success/rejected, конверсия в success.

* **Понедельничный подогрев** — понедельник 10:00±15 мин. Для контактов в
  memory.funnel_stage == "nurture" (куда их переводит Анри после 3 дней
  неответа) шлёт одно короткое напоминание в последний активный канал. Если
  клиент снова проигнорит две недели — Анри сам закроет его в rejected.

Дедуп по дате и ключу задания хранится в памяти процесса. На Render воркер
живёт сутками, нулевой риск, что воркер перезапустится ровно в окно
отправки и мы пошлём отчёт дважды. Если это случится — не катастрофа.

Запускается из `app.py` → `start_background_jobs` вместе с другими
фоновыми циклами. Тик — раз в 5 минут.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.db.session import SessionLocal
from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.agent_call import AgentCall

logger = logging.getLogger(__name__)

TICK_SECONDS = 300  # 5 минут — достаточно, чтобы попасть в окно ±15 минут

# Окна срабатывания по бишкекскому времени: воскресенье 21:00–21:15,
# понедельник 10:00–10:15. Дедуп по date().
_LAST_RUN: dict[str, Optional[date]] = {
    "weekly_report": None,
    "monday_nurture": None,
}


def _now_bishkek() -> datetime:
    from backend.core.timezone_utils import now_bishkek
    return now_bishkek()


def _already_ran_today(job: str, today_local: date) -> bool:
    return _LAST_RUN.get(job) == today_local


def _mark_ran(job: str, today_local: date) -> None:
    _LAST_RUN[job] = today_local


# ============================================================================
# Еженедельный отчёт
# ============================================================================

def _week_bounds_utc(today_local: date) -> tuple[datetime, datetime]:
    """Границы «прошедших 7 дней» (включая сегодняшний день до момента запуска)."""
    from backend.core.timezone_utils import BISHKEK
    today_start_local = datetime.combine(today_local, datetime.min.time(), tzinfo=BISHKEK)
    week_start_utc = (today_start_local - timedelta(days=6)).astimezone(timezone.utc).replace(tzinfo=None)
    now_utc = datetime.utcnow()
    return week_start_utc, now_utc


def _format_weekly_report(agent: AgentConfig, stats: dict) -> str:
    agent_name = (agent.name or "").strip() or "агент"
    lines = [
        f"📊 Отчёт за неделю · {agent_name}",
        f"Период: {stats['start'].strftime('%d.%m')} — {stats['end'].strftime('%d.%m')} Бишкек",
        "",
        f"Звонки всего: {stats['calls_total']}",
        f"  успешно (success): {stats['calls_success']}",
        f"  недозвоны: {stats['calls_no_answer']}",
        f"  отказы: {stats['calls_rejected']}",
        f"Новых контактов: {stats['contacts_new']}",
        f"Подключили (success): {stats['stage_success']}",
        f"Закрыли отказом: {stats['stage_rejected']}",
    ]
    if stats["calls_total"] > 0:
        conv = stats["stage_success"] * 100.0 / stats["calls_total"]
        lines.append(f"Конверсия звонок → подключение: {conv:.1f}%")
    return "\n".join(lines)


def _collect_weekly_stats(agent: AgentConfig, db: Session) -> dict:
    from backend.core.timezone_utils import BISHKEK
    today_local = _now_bishkek().date()
    week_start_utc, now_utc = _week_bounds_utc(today_local)
    base_calls = db.query(AgentCall).filter(
        AgentCall.agent_config_id == agent.id,
        AgentCall.created_at >= week_start_utc,
        AgentCall.created_at < now_utc,
    )
    calls_total = base_calls.count()
    calls_success = base_calls.filter(AgentCall.post_call_decision == "SUCCESS").count()
    calls_rejected = base_calls.filter(AgentCall.post_call_decision == "REJECTED").count()
    calls_no_answer = base_calls.filter(AgentCall.status.in_(("no_answer", "failed"))).count()
    contacts_new = db.query(AgentContact).filter(
        AgentContact.agent_config_id == agent.id,
        AgentContact.created_at >= week_start_utc,
        AgentContact.created_at < now_utc,
    ).count()
    stage_success = db.query(AgentContact).filter(
        AgentContact.agent_config_id == agent.id,
        AgentContact.status == "success",
        AgentContact.updated_at >= week_start_utc,
    ).count()
    stage_rejected = db.query(AgentContact).filter(
        AgentContact.agent_config_id == agent.id,
        AgentContact.status == "rejected",
        AgentContact.updated_at >= week_start_utc,
    ).count()
    return {
        "start": (week_start_utc + timedelta(hours=6)),
        "end": (now_utc + timedelta(hours=6)),
        "calls_total": calls_total,
        "calls_success": calls_success,
        "calls_rejected": calls_rejected,
        "calls_no_answer": calls_no_answer,
        "contacts_new": contacts_new,
        "stage_success": stage_success,
        "stage_rejected": stage_rejected,
    }


async def _run_weekly_reports(db: Session) -> int:
    """Отправить еженедельные отчёты по всем активным агентам."""
    from backend.services.agent_tools import fn_send_telegram_notification

    sent = 0
    agents = db.query(AgentConfig).filter(AgentConfig.is_active == True).all()
    for agent in agents:
        try:
            stats = _collect_weekly_stats(agent, db)
            # Не шлём отчёт по полностью пустому агенту — только лишний шум.
            if stats["calls_total"] == 0 and stats["contacts_new"] == 0:
                continue
            message = _format_weekly_report(agent, stats)
            await fn_send_telegram_notification({"message": message}, agent, db)
            sent += 1
            logger.info(f"[WEEKLY] report sent for agent {agent.id} ({agent.name})")
        except Exception as e:
            logger.warning(f"[WEEKLY] report failed for agent {agent.id}: {e}")
    return sent


# ============================================================================
# Понедельничный подогрев
# ============================================================================

NURTURE_TEMPLATE = (
    "{greeting}! Вспомнил о вас — на прошлой неделе обсуждали автоматизацию "
    "продаж для вашего бизнеса. Если готовы вернуться к разговору — я на связи, "
    "могу коротко рассказать, что нового."
)


async def _send_nurture_message(contact: AgentContact, agent_config: AgentConfig, db: Session) -> bool:
    from backend.services import agent_tools

    channel = agent_tools._detect_recent_channel(db, str(agent_config.id), str(contact.id))
    if not channel:
        return False
    name = (contact.name or "").strip()
    greeting = f"{name}, здравствуйте" if name else "Здравствуйте"
    text = NURTURE_TEMPLATE.format(greeting=greeting)
    send_args = {"agent_contact_id": str(contact.id), "text": text}
    try:
        if channel == "telegram":
            res = await agent_tools.fn_telegram_send_message(
                send_args, user_id=str(contact.user_id), agent_config=agent_config, db=db,
            )
        elif channel == "whatsapp":
            res = await agent_tools.fn_whatsapp_send_message(
                send_args, user_id=str(contact.user_id), agent_config=agent_config, db=db,
            )
        elif channel == "instagram":
            res = await agent_tools.fn_instagram_send_message(
                send_args, user_id=str(contact.user_id), agent_config=agent_config, db=db,
            )
        else:
            return False
    except Exception as e:
        logger.warning(f"[NURTURE] send to {channel} for contact {contact.id} failed: {e}")
        return False
    return bool(res and res.get("ok"))


def _nurture_candidates(agent: AgentConfig, db: Session) -> list[AgentContact]:
    """Контакты в стадии nurture, которых не трогали больше 6 дней."""
    cutoff = datetime.utcnow() - timedelta(days=6)
    contacts = db.query(AgentContact).filter(
        AgentContact.agent_config_id == agent.id,
        AgentContact.status == "active",
        AgentContact.updated_at < cutoff,
    ).all()
    out = []
    for c in contacts:
        mem = c.memory if isinstance(c.memory, dict) else {}
        if mem.get("funnel_stage") == "nurture":
            out.append(c)
    return out


def _mark_nurture_touched(contact: AgentContact) -> None:
    mem = dict(contact.memory or {})
    mem["last_nurture_at"] = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    nurture_count = int(mem.get("nurture_touch_count") or 0) + 1
    mem["nurture_touch_count"] = nurture_count
    contact.memory = mem
    flag_modified(contact, "memory")


async def _run_monday_nurture(db: Session) -> int:
    """Отправить подогрев всем контактам в nurture по всем агентам."""
    sent = 0
    agents = db.query(AgentConfig).filter(AgentConfig.is_active == True).all()
    for agent in agents:
        try:
            candidates = _nurture_candidates(agent, db)
            for contact in candidates:
                ok = await _send_nurture_message(contact, agent, db)
                if ok:
                    _mark_nurture_touched(contact)
                    db.commit()
                    sent += 1
                    logger.info(
                        f"[NURTURE] sent to contact {contact.id} ({contact.name or contact.phone})"
                    )
        except Exception as e:
            logger.warning(f"[NURTURE] agent {agent.id} failed: {e}")
            try:
                db.rollback()
            except Exception:
                pass
    return sent


# ============================================================================
# Диспетчер + loop
# ============================================================================

async def _tick() -> None:
    db = SessionLocal()
    try:
        now_local = _now_bishkek()
        today_local = now_local.date()
        # Воскресенье 21:00–21:15 Бишкек
        if now_local.weekday() == 6 and now_local.hour == 21 and now_local.minute < 15:
            if not _already_ran_today("weekly_report", today_local):
                try:
                    await _run_weekly_reports(db)
                finally:
                    _mark_ran("weekly_report", today_local)
        # Понедельник 10:00–10:15 Бишкек
        if now_local.weekday() == 0 and now_local.hour == 10 and now_local.minute < 15:
            if not _already_ran_today("monday_nurture", today_local):
                try:
                    await _run_monday_nurture(db)
                finally:
                    _mark_ran("monday_nurture", today_local)
    finally:
        db.close()


async def start_weekly_jobs_loop() -> None:
    """Цикл-тикер: каждые 5 минут проверяет окна и фирит задания."""
    logger.info(f"[WEEKLY-JOBS] started (tick every {TICK_SECONDS}s)")
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.error(f"[WEEKLY-JOBS] tick failed: {e}", exc_info=True)
        await asyncio.sleep(TICK_SECONDS)
