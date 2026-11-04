"""
Воронка Анри v3: этап «Думает» и дожим до финала (ветка 0311-call-guard, 04.11.2026).

Владелец: «у агента есть начало, он звонит, но нет конца». Цепочка:

  КП отправлено (send_proposal)      → карточка сама в «Думает», старт дожима;
  клиент молчит сутки после нашего   → касание 1 «успели посмотреть КП?»;
  ещё сутки тишины                   → касание 2: расчёт «менеджер против агента»;
  ещё сутки тишины                   → касание 3, последнее: «напишите, когда актуально»;
  сутки после касания 3              → «Отказ», причина «нет ответа»;
  клиент сам написал из «Отказа (нет ответа)» → снова «В работе».

Клиент ответил — разговор ведёт обычный разбор входящего, часы касаний считаются
заново от последнего сообщения агента. Тексты касаний пишет сам агент (оркестратор)
под клиента и историю; система только следит, чтобы касание не забылось.

Состояние — в memory карточки: memory["nurture"] = {started_at, touches, last_touch_at}.
Бронь касания между воркерами gunicorn — атомарный UPDATE (как у прогрева), иначе
4 воркера отправили бы 4 одинаковых сообщения.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text as _sql
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

logger = logging.getLogger(__name__)

TOUCH_GAP = timedelta(hours=24)       # тишина клиента перед следующим касанием
MAX_TOUCHES = 3                       # касаний после КП, потом «Отказ»
WORK_HOURS = (10, 19)                 # касания пишем только днём по Бишкеку
NO_REPLY_REASON = "нет ответа"

TOUCH_INSTRUCTIONS = {
    1: (
        "Касание 1 из 3 после КП. Клиент получил презентацию и КП и молчит сутки. "
        "Коротко и по-человечески спроси, успел ли он посмотреть КП и что скажет, "
        "что-то смущает или есть вопросы. Одно сообщение, 1–2 строки, без давления."
    ),
    2: (
        "Касание 2 из 3 после КП. Клиент молчит. Найди в list_agent_files расчёт "
        "«менеджер против агента» (сравнение стоимости и эффективности) и отправь его "
        "тем же каналом с одной строкой вывода под его бизнес: сколько стоит менеджер "
        "и сколько агент, цифры только из файла. Если такого файла нет — напиши одну "
        "конкретную пользу под его нишу и предложи созвониться на 10 минут."
    ),
    3: (
        "Касание 3 из 3, последнее. Клиент так и не ответил. Одно спокойное сообщение: "
        "не будешь отвлекать, если станет актуально, пусть напишет сюда, подключим за "
        "1–2 дня. Без вопросов и без давления."
    ),
}


# ── этапы ────────────────────────────────────────────────────────────────

def thinking_stage_key(agent) -> Optional[str]:
    """Ключ этапа «Думает» у агента (своя стадия владельца, ищем по названию)."""
    from backend.core.pipeline_stages import get_agent_stages
    for s in get_agent_stages(agent):
        label = (s.get("label") or "").lower()
        if s.get("kind") == "open" and ("дума" in label or s.get("key") in ("thinking", "think")):
            return s["key"]
    return None


def lost_stage_key(agent) -> Optional[str]:
    """«Отказ»: ключ rejected, если есть, иначе первая стадия-потеря, кроме «Не звонить»."""
    from backend.core.pipeline_stages import get_agent_stages
    stages = get_agent_stages(agent)
    keys = [s["key"] for s in stages]
    if "rejected" in keys:
        return "rejected"
    for s in stages:
        if s.get("kind") == "lost" and s["key"] != "do_not_call":
            return s["key"]
    return None


def work_stage_key(agent) -> Optional[str]:
    from backend.core.pipeline_stages import get_agent_stage_keys
    return "active" if "active" in get_agent_stage_keys(agent) else None


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ── события ─────────────────────────────────────────────────────────────

def on_proposal_sent(db: Session, agent, contact) -> Optional[str]:
    """КП ушло: карточка в «Думает», дожим с нуля. Возвращает новый этап или None."""
    key = thinking_stage_key(agent)
    if not key or contact is None:
        return None
    from backend.core.pipeline_stages import _TERMINAL_KEYS, get_agent_stages
    lost_or_won = {s["key"] for s in get_agent_stages(agent) if s.get("kind") in ("won", "lost")} | set(_TERMINAL_KEYS)
    if contact.status in lost_or_won:
        return None
    mem = dict(contact.memory or {})
    now = _now()
    mem["nurture"] = {"started_at": _iso(now), "touches": 0, "last_touch_at": _iso(now)}
    contact.memory = mem
    flag_modified(contact, "memory")
    contact.status = key
    contact.next_step_text = "Касание 1: спросить про КП"
    contact.next_step_at = now + TOUCH_GAP
    db.commit()
    logger.info(f"[FUNNEL] contact {contact.id}: КП отправлено → «{key}», дожим начат")
    return key


def on_client_message(db: Session, agent, contact) -> Optional[str]:
    """Клиент сам вышел на связь: из «Отказа (нет ответа)» — снова «В работе»."""
    if contact is None:
        return None
    mem = dict(contact.memory or {})
    if mem.get("lost_by") != "no_reply" or contact.status != lost_stage_key(agent):
        return None
    key = work_stage_key(agent) or "active"
    mem.pop("lost_by", None)
    mem.pop("nurture", None)
    contact.memory = mem
    flag_modified(contact, "memory")
    contact.status = key
    contact.lost_reason = None
    db.commit()
    logger.info(f"[FUNNEL] contact {contact.id}: написал после «нет ответа» → «{key}»")
    return key


# ── даты переписки ──────────────────────────────────────────────────────

def _last_message_at(db: Session, contact_id, direction: str) -> Optional[datetime]:
    from sqlalchemy import func
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.agent_telegram_account import AgentTelegramMessage
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    best = None
    for model in (AgentWhatsAppMessage, AgentTelegramMessage, AgentInstagramMessage):
        try:
            v = db.query(func.max(model.created_at)).filter(
                model.agent_contact_id == contact_id, model.direction == direction,
            ).scalar()
        except Exception:
            v = None
        if v is not None:
            if v.tzinfo is not None:
                v = v.astimezone(timezone.utc).replace(tzinfo=None)
            best = v if best is None or v > best else best
    return best


INIT_LOOKBACK = timedelta(days=14)


def ensure_nurture(db: Session, contact) -> bool:
    """
    Клиент попал в «Думает» не через send_proposal (агент двинул сам, владелец
    перетащил в канбане): заводим дожим. Отсчёт — с последнего сообщения агента;
    если переписки не было 14 дней — не трогаем (старый лид, первым не пишем).
    """
    mem = contact.memory if isinstance(contact.memory, dict) else {}
    if isinstance(mem.get("nurture"), dict):
        return False
    last = max(filter(None, [_last_message_at(db, contact.id, "outbound"),
                             _last_message_at(db, contact.id, "inbound")]), default=None)
    if last is None or _now() - last > INIT_LOOKBACK:
        return False
    mem = dict(mem)
    mem["nurture"] = {"started_at": _iso(_now()), "touches": 0, "last_touch_at": _iso(_now())}
    contact.memory = mem
    flag_modified(contact, "memory")
    db.commit()
    return True


def _has_future_task(db: Session, contact_id) -> bool:
    from backend.models.task import Task, TaskStatus
    return db.query(Task.id).filter(
        Task.agent_contact_id == contact_id,
        Task.status == TaskStatus.SCHEDULED,
        Task.is_agent_task == True,
    ).first() is not None


def next_action(db: Session, contact, now: Optional[datetime] = None) -> Optional[tuple]:
    """
    Что делать с клиентом в «Думает» сейчас:
      ("touch", n)  — пора касание n;
      ("close", None) — цепочка пройдена, клиент молчит → «Отказ»;
      None — ждём (клиент отвечает, рано, есть план агента, пауза).
    """
    now = now or _now()
    mem = contact.memory if isinstance(contact.memory, dict) else {}
    nur = mem.get("nurture")
    if not isinstance(nur, dict):
        return None
    from backend.services import agent_pause
    if agent_pause.is_paused(contact):
        return None
    if _has_future_task(db, contact.id):
        return None  # у агента свой план (звонок, сообщение) — не мешаем
    last_out = max(filter(None, [_parse(nur.get("last_touch_at")), _last_message_at(db, contact.id, "outbound")]),
                   default=None)
    if last_out is None:
        return None
    last_in = _last_message_at(db, contact.id, "inbound")
    if last_in is not None and last_in > last_out:
        return None  # клиент ответил последним — его сообщение разбирает обычный поток
    if now - last_out < TOUCH_GAP:
        return None
    touches = int(nur.get("touches") or 0)
    if touches >= MAX_TOUCHES:
        return ("close", None)
    return ("touch", touches + 1)


def _claim_touch(db: Session, contact_id, n: int) -> bool:
    """Атомарно: touches = n-1 → n. True — касание делает этот воркер."""
    try:
        row = db.execute(_sql(
            "UPDATE agent_contacts SET memory = jsonb_set(jsonb_set(memory, '{nurture,touches}', to_jsonb(CAST(:n AS int))),"
            " '{nurture,last_touch_at}', to_jsonb(CAST(:ts AS text))) "
            "WHERE id = :cid AND COALESCE((memory->'nurture'->>'touches')::int, 0) = :prev "
            "RETURNING id"
        ), {"cid": str(contact_id), "n": n, "prev": n - 1, "ts": _iso(_now())}).first()
        db.commit()
        return row is not None
    except Exception as e:
        db.rollback()
        logger.warning(f"[FUNNEL] claim touch {n} for {contact_id} failed: {e}")
        return False


def close_no_reply(db: Session, agent, contact) -> bool:
    key = lost_stage_key(agent)
    if not key:
        return False
    try:
        row = db.execute(_sql(
            "UPDATE agent_contacts SET status = :st, lost_reason = :why, "
            " memory = (memory - 'nurture') || jsonb_build_object('lost_by', 'no_reply') "
            "WHERE id = :cid AND memory->'nurture' IS NOT NULL RETURNING id"
        ), {"st": key, "why": NO_REPLY_REASON, "cid": str(contact.id)}).first()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[FUNNEL] close {contact.id} failed: {e}")
        return False
    if row is not None:
        logger.info(f"[FUNNEL] contact {contact.id}: 3 касания без ответа → «{key}» ({NO_REPLY_REASON})")
    return row is not None


async def run_touch(db: Session, agent, contact, n: int) -> None:
    """Касание n: один прогон оркестратора, текст пишет агент под клиента."""
    from backend.models.agent_call import AgentCall
    from backend.models.user import User
    from backend.services.agent_orchestrator import PostCallOrchestrator
    from backend.services.agent_tools import _detect_recent_channel

    user = db.query(User).filter(User.id == contact.user_id).first()
    if user is None:
        return
    channel = _detect_recent_channel(db, str(agent.id), str(contact.id)) or "whatsapp"
    call = AgentCall(
        agent_contact_id=contact.id,
        agent_config_id=agent.id,
        user_id=user.id,
        status="calling",
        direction="outbound",
        started_at=_now(),
        postcall_log={"call_direction": "followup_outbound", "channel": channel},
    )
    db.add(call)
    db.commit()
    instruction = f"[Канал переписки: {channel}] " + TOUCH_INSTRUCTIONS[n]
    contact.next_step_text = (f"Касание {n + 1} дожима" if n < MAX_TOUCHES else "Закрыть: нет ответа")
    contact.next_step_at = _now() + TOUCH_GAP
    db.commit()
    await PostCallOrchestrator()._analyze_v3_openrouter(
        agent_call=call, agent_contact=contact, agent_config=agent, user=user, task=None,
        transcript=instruction, call_status="answered", duration_seconds=0, db=db,
        call_direction="followup_outbound",
    )


def _in_work_hours(now_utc: datetime) -> bool:
    from backend.core.timezone_utils import BISHKEK
    h = now_utc.replace(tzinfo=timezone.utc).astimezone(BISHKEK).hour
    return WORK_HOURS[0] <= h < WORK_HOURS[1]


async def run_nurture(db: Session) -> int:
    """Проход по всем агентам: касания и закрытие молчунов. Возвращает число действий."""
    if not _in_work_hours(_now()):
        return 0
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact
    from backend.services import agent_mode

    done = 0
    for agent in db.query(AgentConfig).filter(AgentConfig.is_active == True).all():
        try:
            if agent_mode.mode_for_agent(db, agent) not in (agent_mode.FULL, agent_mode.REACTIVE):
                continue
            key = thinking_stage_key(agent)
            if not key:
                continue
            contacts = db.query(AgentContact).filter(
                AgentContact.agent_config_id == agent.id, AgentContact.status == key,
            ).all()
            for contact in contacts:
                ensure_nurture(db, contact)
                act = next_action(db, contact)
                if act is None:
                    continue
                if act[0] == "close":
                    done += int(close_no_reply(db, agent, contact))
                    continue
                n = act[1]
                if not _claim_touch(db, contact.id, n):
                    continue
                db.refresh(contact)
                logger.info(f"[FUNNEL] contact {contact.id}: касание {n} из {MAX_TOUCHES}")
                await run_touch(db, agent, contact, n)
                done += 1
        except Exception as e:
            logger.warning(f"[FUNNEL] agent {agent.id}: {e}", exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
    return done
