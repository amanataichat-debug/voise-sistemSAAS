"""
Конец звонка через собственный SIP-шлюз → звонок агента обзвона → PostCall.

Две точки входа:
  - on_media_finished — медиа-сокет звонка закрылся (api/sip_gateway.py::sip_media, finally):
    стенограмма уже собрана (sip_calls.transcript). Исходящий звонок агента находится по
    AgentCall.source_task_id == sip_calls.task_id; входящий на номер, привязанный к агенту
    (sip_phone_numbers.agent_config_id), заводит AgentContact (если новый) + AgentCall.
  - on_call_failed — мост окончательно не дозвонился (failed без повтора): PostCall с
    no_answer, чтобы оркестратор решил, перезванивать ли.

PostCallOrchestrator.finalize_sip_call забирает звонок атомарно, повторный вызов безопасен.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.models.agent_call import AgentCall
from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.sip_gateway import SipCall, SipPhoneNumber
from backend.services.call_transcript import transcript_as_text

logger = get_logger(__name__)


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _call_duration(call: SipCall) -> int:
    if call.duration_sec:
        return int(call.duration_sec)
    answered = _naive_utc(call.answered_at)
    if answered:
        return max(0, int((datetime.utcnow() - answered).total_seconds()))
    return 0


def _schedule(agent_call_id: str, transcript: str, call_status: str, duration: int, direction: str) -> None:
    from backend.services.agent_orchestrator import PostCallOrchestrator
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(f"[AGENT-FINALIZER] no running loop, PostCall for {agent_call_id} not scheduled")
        return
    asyncio.create_task(PostCallOrchestrator.finalize_sip_call(
        agent_call_id, transcript, call_status, duration, call_direction=direction,
    ))


class AgentCallFinalizer:
    # ------------------------------------------------------------------ helpers
    @staticmethod
    def agent_for_number(db: Session, number: Optional[SipPhoneNumber]) -> Optional[AgentConfig]:
        if number is None or not getattr(number, "agent_config_id", None):
            return None
        return db.query(AgentConfig).filter(AgentConfig.id == number.agent_config_id).first()

    @staticmethod
    def _inbound_agent_call(db: Session, call: SipCall, agent: AgentConfig) -> Optional[AgentCall]:
        """Входящий на номер агента: найти/создать контакт и завести AgentCall (как делал Voximplant /log)."""
        phone = call.caller or ""
        suffix = phone[-9:]
        if not suffix:
            logger.info(f"[AGENT-FINALIZER] inbound call {call.id} without caller number, skip")
            return None
        existing = db.query(AgentCall).filter(AgentCall.call_session_id == str(call.id)).first()
        if existing:
            return existing
        contact = (
            db.query(AgentContact)
            .filter(AgentContact.agent_config_id == agent.id, AgentContact.phone.like(f"%{suffix}"))
            .order_by(AgentContact.created_at.desc())
            .first()
        )
        if contact is None:
            contact = AgentContact(
                agent_config_id=agent.id,
                user_id=agent.user_id,
                phone=f"+{phone}" if phone.isdigit() else phone,
                status="new",
            )
            db.add(contact)
            db.flush()
            logger.info(f"[AGENT-FINALIZER] new AgentContact {contact.id} for inbound {phone}")
        agent_call = AgentCall(
            agent_contact_id=contact.id,
            agent_config_id=agent.id,
            user_id=agent.user_id,
            source_task_id=None,
            call_session_id=str(call.id),
            status="calling",
            direction="inbound",
            started_at=_naive_utc(call.answered_at) or datetime.utcnow(),
        )
        db.add(agent_call)
        db.flush()
        return agent_call

    @staticmethod
    def _find_agent_call(db: Session, call: SipCall, number: Optional[SipPhoneNumber]) -> Tuple[Optional[AgentCall], str]:
        if call.direction == "outbound":
            if not call.task_id:
                return None, "outbound"
            return db.query(AgentCall).filter(AgentCall.source_task_id == call.task_id).first(), "outbound"
        agent = AgentCallFinalizer.agent_for_number(db, number)
        if agent is None:
            return None, "inbound"
        return AgentCallFinalizer._inbound_agent_call(db, call, agent), "inbound"

    # ------------------------------------------------------------------ entry points
    @staticmethod
    def on_media_finished(db: Session, call: SipCall, number: Optional[SipPhoneNumber],
                          has_user_speech: bool) -> Optional[str]:
        """Медиа звонка закончилось. Возвращает id AgentCall, если звонок принадлежит агенту."""
        agent_call, direction = AgentCallFinalizer._find_agent_call(db, call, number)
        if agent_call is None:
            return None
        transcript = transcript_as_text(call.transcript)
        duration = _call_duration(call)
        agent_call.call_session_id = str(call.id)
        agent_call.transcript = transcript or agent_call.transcript
        agent_call.duration_seconds = duration
        db.commit()
        call_status = "answered" if has_user_speech else "no_answer"
        logger.info(f"[AGENT-FINALIZER] call {call.id} → AgentCall {agent_call.id} ({direction}, {call_status}, {duration}s)")
        _schedule(str(agent_call.id), transcript, call_status, duration, direction)
        return str(agent_call.id)

    @staticmethod
    def on_call_failed(db: Session, call: SipCall) -> Optional[str]:
        """Исходящий звонок агента окончательно не состоялся (занято, не ответил, ошибка шлюза)."""
        if call.direction != "outbound" or not call.task_id:
            return None
        agent_call = db.query(AgentCall).filter(AgentCall.source_task_id == call.task_id).first()
        if agent_call is None:
            return None
        logger.info(f"[AGENT-FINALIZER] call {call.id} failed ({call.end_reason}) → AgentCall {agent_call.id} no_answer")
        _schedule(str(agent_call.id), "", "no_answer", 0, "outbound")
        return str(agent_call.id)


# ---------------------------------------------------------------------------
# Итог звонка для интерфейса агента: что именно случилось с SIP-звонком.
# kind: ok (разговор состоялся) | warn (не дозвонились: занято, не взял, молчал)
#       | error (проблема со связью/номером) | progress (звонок ещё идёт)
# ---------------------------------------------------------------------------

_NETWORK_REASONS = {
    "trunk_unavailable": "линия оператора недоступна",
    "congestion": "сеть оператора перегружена",
    "channel_limit": "все линии заняты",
    "ami_unavailable": "шлюз телефонии не отвечает",
    "ami_error": "ошибка шлюза телефонии",
    "gateway_timeout": "шлюз не ответил вовремя",
    "failed": "звонок не удалось начать",
}
_BROKEN_REASONS = {
    "backend_unavailable": "сервер голосового агента недоступен",
    "backend_closed": "соединение с голосовым агентом прервалось",
    "asterisk_disconnected": "обрыв соединения со шлюзом",
    "gateway_shutdown": "шлюз перезапускался",
    "stale": "звонок завис и был закрыт",
}
_END_BY = {
    "asterisk_hangup": "клиент положил трубку",
    "backend_hangup": "агент завершил разговор",
    "handler_finished": "агент завершил разговор",
    "handler_closed": "агент завершил разговор",
    "hangup_call": "агент завершил разговор",
    "max_duration": "достигнут лимит длительности",
}


def _fmt_duration(sec: float) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def call_outcome(agent_call, sip_call: Optional[SipCall]) -> Optional[dict]:
    """Понятный итог звонка для истории агента (или None для текстовых событий)."""
    channel = agent_call._resolve_channel() if hasattr(agent_call, "_resolve_channel") else "call"
    if channel != "call":
        return None
    if sip_call is None:
        if agent_call.status == "failed":
            return {"kind": "error", "label": "Звонок не состоялся",
                    "detail": agent_call.call_result or "причина не записана"}
        return None

    status = sip_call.status
    reason = sip_call.end_reason or ""
    attempts = sip_call.attempts or 0
    if status == "queued":
        detail = "ждёт свободную линию"
        if reason in _NETWORK_REASONS and attempts:
            detail = f"повтор после ошибки: {_NETWORK_REASONS[reason]} (попытка {attempts + 1})"
        return {"kind": "progress", "label": "В очереди", "detail": detail}
    if status in ("dialing", "ringing"):
        return {"kind": "progress", "label": "Набираем номер", "detail": f"попытка {max(attempts, 1)}"}
    if status == "answered" and not sip_call.ended_at and agent_call.status in ("calling", "finalizing"):
        return {"kind": "progress", "label": "Идёт разговор", "detail": ""}
    if status == "failed":
        if reason == "busy":
            return {"kind": "warn", "label": "Занято", "detail": "абонент разговаривал или сбросил"}
        if reason == "no_answer":
            return {"kind": "warn", "label": "Не взял трубку", "detail": ""}
        if reason == "bad_number":
            return {"kind": "error", "label": "Неверный номер", "detail": sip_call.to_number or ""}
        detail = _NETWORK_REASONS.get(reason) or _BROKEN_REASONS.get(reason) or reason or "неизвестная ошибка"
        if attempts > 1:
            detail += f" · попыток: {attempts}"
        return {"kind": "error", "label": "Проблема со связью", "detail": detail}

    # completed / answered-and-ended
    duration = sip_call.duration_sec or agent_call.duration_seconds or 0
    if reason in _BROKEN_REASONS:
        return {"kind": "error", "label": "Обрыв связи",
                "detail": f"{_BROKEN_REASONS[reason]} · {_fmt_duration(duration)}"}
    turns = sip_call.transcript or []
    client_spoke = any(t.get("role") == "user" for t in turns) if turns else agent_call.status == "answered"
    if not client_spoke:
        return {"kind": "warn", "label": "Взял трубку, но молчал",
                "detail": f"{_fmt_duration(duration)} · {_END_BY.get(reason, 'звонок завершён')}"}
    return {"kind": "ok", "label": "Разговор состоялся",
            "detail": f"{_fmt_duration(duration)} · {_END_BY.get(reason, 'звонок завершён')}"}


def sip_calls_for(db: Session, agent_calls) -> dict:
    """{call_session_id: SipCall} для пачки звонков агента одним запросом."""
    ids = []
    for c in agent_calls:
        try:
            ids.append(uuid.UUID(str(c.call_session_id)))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}
    return {str(s.id): s for s in db.query(SipCall).filter(SipCall.id.in_(ids)).all()}
