"""
Политика недозвона агента обзвона: «один звонок — не долбим клиента».

Правило владельца (20.10): по задаче (своей или владельца) агент делает ОДИН
звонок. Клиент не взял трубку / сбросил / молчал →
  1) задача закрыта, сегодня этому клиенту больше не звоним;
  2) клиенту пишем в WhatsApp / Telegram / Instagram: «не дозвонились, когда
     удобно созвониться?» (пишет агент по своему промпту; если не написал —
     пишет код шаблоном);
  3) на завтра назначается ОДИН повторный звонок (в окно звонков агента);
  4) завтра снова без ответа → контакт уходит в «Не звонить», звонки прекращены.
Итого не больше двух звонков, и только в разные дни. Клиент ответил на звонок
или сам написал в любом канале — счётчик обнуляется (agent_orchestrator).

Раньше это было инструкцией LLM в PostCall, а «звонили сегодня» проставлялось
ПОСЛЕ анализа — модель успевала поставить повторный звонок на сегодня. Теперь
всё, что касается звонков, делает код ДО анализа (before_postcall), а модели
остаётся только написать клиенту.

Вызывается из PostCallOrchestrator.finalize_sip_call (собственный SIP-шлюз —
единственная рабочая телефония).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.logging import get_logger
from backend.core.timezone_utils import BISHKEK
from backend.models.agent_call import AgentCall
from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.task import Task, TaskStatus

logger = get_logger(__name__)

# Тулзы, которыми агент пишет клиенту: если хоть одна сработала — шаблон не шлём.
MESSAGE_TOOLS = {
    "whatsapp_send_message", "whatsapp_send_file",
    "telegram_send_message", "telegram_send_file",
    "instagram_send_message", "instagram_send_file",
    "send_voice_message",
}
# Порядок каналов для шаблонного сообщения: WhatsApp находит клиента по номеру,
# Telegram — по диалогу/username/номеру, Instagram — только существующий тред.
FALLBACK_CHANNELS = ("whatsapp_send_message", "telegram_send_message", "instagram_send_message")


def _scheduled_call_tasks(db: Session, contact_id) -> List[Task]:
    return (
        db.query(Task)
        .filter(
            Task.agent_contact_id == contact_id,
            Task.status == TaskStatus.SCHEDULED,
            Task.is_agent_task == True,  # noqa: E712
            Task.channel == "call",
        )
        .order_by(Task.scheduled_time.asc())
        .all()
    )


def _local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BISHKEK)


def pending_followup_call(db: Session, contact: AgentContact) -> Optional[Task]:
    """Повторный звонок после недозвона, который уже стоит в очереди (или None).

    Есть, только пока клиент не ответил и не написал (no_answer_streak ≥ 1 или
    метка followup_task_id — её ставит before_postcall, снимает «клиент на связи»
    в _record_call_event): второй такой звонок агент поставить не может
    (agent_tools.fn_create_agent_task / fn_trigger_immediate_call).
    """
    mem = contact.memory if isinstance(contact.memory, dict) else {}
    if int(mem.get("no_answer_streak") or 0) < 1 and not mem.get("followup_task_id"):
        return None
    tasks = _scheduled_call_tasks(db, contact.id)
    return tasks[0] if tasks else None


async def before_postcall(
    db: Session,
    agent_call: AgentCall,
    agent_contact: AgentContact,
    agent_config: AgentConfig,
    task: Optional[Task],
    call_status: str,
    fail_reason: str = "",
) -> Optional[Dict[str, Any]]:
    """Исходящий звонок агента без ответа → учёт попытки, отмена лишних звонков,
    один повторный звонок на завтра или «Не звонить». Возвращает состояние для
    подсказки LLM и шаблонного сообщения; None — политика не применялась.
    """
    if call_status == "answered" or not agent_call.source_task_id:
        return None
    from backend.services.agent_orchestrator import _record_call_event
    from backend.services.sip_gateway_service import NOT_REACHED_FAIL_REASONS

    reached = (fail_reason or "") not in NOT_REACHED_FAIL_REASONS
    _record_call_event(agent_contact, agent_call, "no_answer", "outbound", reached=reached)
    mem = agent_contact.memory if isinstance(agent_contact.memory, dict) else {}
    streak = int(mem.get("no_answer_streak") or 0)
    gave_up = (agent_contact.status or "") == "do_not_call" and bool(mem.get("giveup_reason"))
    terminal = (agent_contact.status or "") in ("do_not_call", "rejected")

    # Лишние звонки: при «Не звонить» — все, иначе — всё, что стоит на сегодня.
    today = _local(datetime.utcnow()).date()
    cancelled = 0
    remaining: List[Task] = []
    for t in _scheduled_call_tasks(db, agent_contact.id):
        if task is not None and t.id == task.id:
            continue
        if terminal or _local(t.scheduled_time).date() <= today:
            t.status = TaskStatus.CANCELLED
            t.call_result = json.dumps({"skipped": "missed_call_policy",
                                        "reason": "gave_up" if terminal else "already_called_today"})
            cancelled += 1
        else:
            remaining.append(t)
    flag_modified(agent_contact, "memory")
    db.commit()

    followup_task_id = None
    followup_local = None
    if not terminal:
        if remaining:
            # Повторный звонок уже стоит (агент/владелец ставили раньше) — он и есть
            # следующая попытка; новый не добавляем.
            followup_task_id = str(remaining[0].id)
            followup_local = _local(remaining[0].scheduled_time)
        else:
            followup_task_id, followup_local = await _create_followup(
                db, agent_contact, agent_config, task, reached,
            )
        if followup_task_id:
            mem = dict(agent_contact.memory) if isinstance(agent_contact.memory, dict) else {}
            mem["followup_task_id"] = followup_task_id
            # Отдельная метка «повтор поставлен автоматикой»: её НЕ снимает ответ
            # клиента — если он договорится о другом времени, авто-повтор заменится
            # (agent_tools._replace_auto_followup), а не останется вторым звонком.
            mem["auto_followup_task_id"] = followup_task_id
            agent_contact.memory = mem
            flag_modified(agent_contact, "memory")
            db.commit()

    state = {
        "reached": reached,
        "fail_reason": fail_reason or "",
        "streak": streak,
        "gave_up": gave_up,
        "terminal": terminal,
        "cancelled": cancelled,
        "followup_task_id": followup_task_id,
        "followup_local": followup_local.strftime("%d.%m %H:%M") if followup_local else None,
    }
    logger.info(
        f"[CALL-POLICY] contact {agent_contact.id}: недозвон (reason={fail_reason or '-'}, "
        f"streak={streak}, gave_up={gave_up}, отменено {cancelled}, "
        f"повтор={state['followup_local'] or 'нет'})"
    )
    return state


async def _create_followup(db, contact, agent_config, task, reached: bool):
    """Один повторный звонок на завтра в то же время (сдвинется в окно звонков агента)."""
    from backend.services.agent_tools import fn_create_agent_task

    when = datetime.now(timezone.utc) + timedelta(days=1)
    goal = ((task.description or "") if task is not None else "").strip()
    why = "Вчера клиент не ответил на звонок (не взял трубку или сбросил)." if reached \
        else "Вчера звонок не прошёл по технической причине на нашей стороне."
    notes = f"{why} Это повторная попытка — если снова не ответит, звонки прекращаются."
    if goal:
        notes += f"\nЦель звонка: {goal[:1500]}"
    result = await fn_create_agent_task(
        {
            "agent_contact_id": str(contact.id),
            "scheduled_at": when.isoformat(),
            "title": "Повторный звонок (вчера не дозвонились)",
            "notes": notes,
        },
        str(agent_config.user_id), str(agent_config.id), db,
    )
    if not result.get("ok"):
        logger.warning(f"[CALL-POLICY] contact {contact.id}: повторный звонок не поставлен: {result}")
        return None, None
    return result.get("task_id"), _local(datetime.fromisoformat(result["scheduled_at"]))


def postcall_note(state: Optional[Dict[str, Any]]) -> str:
    """Блок для входа PostCall LLM: что код уже сделал со звонками и что осталось агенту."""
    if not state:
        return ""
    lines = ["", "", "АВТОМАТИКА НЕДОЗВОНА (уже сделано кодом, не меняй):"]
    if not state["reached"]:
        lines.append(
            "- Звонок НЕ дошёл до телефона клиента (техническая причина на нашей стороне). "
            "Клиенту о недозвоне НЕ пиши — он звонка не видел."
        )
    if state["terminal"]:
        if state["gave_up"]:
            lines.append(
                f"- Это {state['streak']}-й звонок подряд без ответа. Клиент переведён в «Не звонить», "
                "все звонки ему отменены. Новых звонков НЕ планируй."
            )
        else:
            lines.append("- Клиент в стадии «Не звонить»/«Отказ» — звонки ему отменены, новых НЕ планируй.")
        if state["reached"]:
            lines.append(
                "- Если у клиента есть WhatsApp/Telegram/Instagram — напиши ОДНО короткое сообщение: "
                "дозвониться не получилось, звонками больше беспокоить не будем, если удобно — "
                "пусть напишет сюда, договоримся. Без давления."
            )
    else:
        when = state.get("followup_local")
        lines.append(
            "- Сегодня этому клиенту больше НЕ звоним (один звонок в день). "
            + (f"Повторный звонок уже назначен на {when} (Бишкек) — он единственный, "
               "create_agent_task / trigger_immediate_call для звонка НЕ вызывай."
               if when else "Повторный звонок поставить не удалось — не пытайся ставить его сам на сегодня.")
        )
        if state["reached"]:
            lines.append(
                "- Твоя задача сейчас: написать клиенту в его канал (whatsapp_send_message, "
                "telegram_send_message или instagram_send_message — что доступно) коротко и "
                "по-человечески: не получилось дозвониться, когда удобно созвониться? "
                "Если не напишешь — система отправит шаблонное сообщение сама."
            )
    lines.append("- Обнови память клиента (update_contact_memory): недозвон и что сделано.")
    return "\n".join(lines)


def _sent_by_agent(agent_call: AgentCall) -> bool:
    log = agent_call.postcall_log if isinstance(agent_call.postcall_log, dict) else {}
    for entry in log.get("tool_calls") or []:
        if entry.get("tool") not in MESSAGE_TOOLS:
            continue
        result = entry.get("result")
        if isinstance(result, dict) and result.get("ok"):
            return True
    return False


def _template_text(contact: AgentContact, final: bool) -> str:
    first = ((contact.name or "").strip().split() or [""])[0]
    hello = f"Здравствуйте, {first}!" if first else "Здравствуйте!"
    if final:
        return (f"{hello} Снова не получилось до вас дозвониться — больше не буду беспокоить звонками. "
                "Если будет удобно, напишите сюда, и договоримся о времени.")
    return f"{hello} Не получилось до вас дозвониться. Подскажите, когда вам удобно созвониться?"


async def after_postcall(db: Session, agent_call_id: str, state: Optional[Dict[str, Any]]) -> None:
    """После анализа LLM: закрепить «Не звонить» и убедиться, что клиенту написали.

    Если агент не написал клиенту о недозвоне (или анализ упал) — пишем шаблоном.
    """
    if not state:
        return
    db.expire_all()
    agent_call = db.query(AgentCall).filter(AgentCall.id == agent_call_id).first()
    if agent_call is None:
        return
    contact = db.query(AgentContact).filter(AgentContact.id == agent_call.agent_contact_id).first()
    agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_call.agent_config_id).first()
    if contact is None or agent_config is None:
        return

    # Предел недозвонов — код, не мнение модели: если она в этом же анализе
    # перевела клиента обратно в работу, возвращаем «Не звонить». Клиент, который
    # сам написал, снимает его через _lift_auto_giveup (тот убирает giveup_reason).
    mem = contact.memory if isinstance(contact.memory, dict) else {}
    if state["gave_up"] and mem.get("giveup_reason") and (contact.status or "") != "do_not_call":
        logger.info(f"[CALL-POLICY] контакт {contact.id}: {contact.status} → do_not_call (предел недозвонов)")
        contact.status = "do_not_call"
        db.commit()

    if not state["reached"]:
        return
    if state["terminal"] and not state["gave_up"]:
        return  # «Не звонить»/«Отказ» по просьбе клиента — молчим
    if _sent_by_agent(agent_call) or not agent_config.is_active:
        return
    from backend.services import agent_pause
    from backend.services.agent_tools import execute_tool
    if agent_pause.is_paused(contact):
        return  # клиента ведёт владелец сам
    text = _template_text(contact, final=state["gave_up"])
    context = {
        "agent_config_id": str(agent_config.id),
        "user_id": str(agent_config.user_id),
        "agent_config": agent_config,
    }
    for tool in FALLBACK_CHANNELS:
        try:
            raw = await execute_tool(tool, {"agent_contact_id": str(contact.id), "text": text}, context, db)
            result = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as exc:
            db.rollback()
            logger.warning(f"[CALL-POLICY] {tool} для контакта {contact.id} упал: {exc}")
            continue
        if result.get("ok"):
            log = dict(agent_call.postcall_log) if isinstance(agent_call.postcall_log, dict) else {}
            log["auto_message"] = {"tool": tool, "text": text, "at": datetime.utcnow().isoformat()}
            agent_call.postcall_log = log
            flag_modified(agent_call, "postcall_log")
            db.commit()
            logger.info(f"[CALL-POLICY] контакт {contact.id}: шаблон о недозвоне отправлен через {tool}")
            return
    logger.info(f"[CALL-POLICY] контакт {contact.id}: ни один мессенджер не доступен — сообщение не отправлено")
