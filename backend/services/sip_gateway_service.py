"""
Сервис собственной SIP-телефонии (шлюз Asterisk + мост, см. infra/sip-gateway/README.md).

Отвечает за работу с БД вокруг звонков через шлюз:
  * поиск номера → пользователь/ассистент;
  * постановка исходящих в очередь и атомарный «захват» очереди тем воркером,
    который держит управляющий сокет шлюза (SELECT ... FOR UPDATE SKIP LOCKED);
  * применение событий моста (started / answered / ended / failed) к sip_calls,
    tasks и agent_calls;
  * пост-обработка диалогов: проставить номер и направление в conversations,
    чтобы CRM-контакт и PostCall-оркестратор нашли транскрипт.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.sip_gateway import (
    SipPhoneNumber,
    SipCall,
    SipCallStatus,
    SIP_SUPPORTED_ASSISTANT_TYPES,
    normalize_sip_number,
)
from backend.models.assistant import AssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
from backend.models.fish_assistant import FishAssistantConfig, FishConversation
from backend.models.conversation import Conversation
from backend.models.task import Task, TaskStatus
from backend.models.agent_call import AgentCall

logger = get_logger(__name__)

# Причины отказа шлюза, при которых исходящий имеет смысл повторить позже
RETRYABLE_FAIL_REASONS = {"channel_limit", "ami_unavailable", "ami_error", "trunk_unavailable", "congestion"}
MAX_ORIGINATE_ATTEMPTS = 6
RETRY_DELAY_SECONDS = 30


def _utcnow() -> datetime:
    return datetime.utcnow()


def _parse_uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, TypeError):
        return None


class SipGatewayService:
    # ------------------------------------------------------------------ numbers
    @staticmethod
    def find_number(db: Session, did: str) -> Optional[SipPhoneNumber]:
        """Найти активный номер по набранному DID в любом формате (полный, с +, без кода страны)."""
        digits = normalize_sip_number(did)
        if len(digits) < 6:
            return None
        record = db.query(SipPhoneNumber).filter(
            SipPhoneNumber.phone_number == digits,
            SipPhoneNumber.is_active == True,  # noqa: E712
        ).first()
        if record:
            return record
        suffix = digits[-9:]
        return db.query(SipPhoneNumber).filter(
            SipPhoneNumber.phone_number.like(f"%{suffix}"),
            SipPhoneNumber.is_active == True,  # noqa: E712
        ).first()

    @staticmethod
    def outbound_number_for_user(db: Session, user_id: uuid.UUID, preferred: Optional[str] = None) -> Optional[SipPhoneNumber]:
        """Выбрать номер пользователя для caller ID исходящего звонка."""
        query = db.query(SipPhoneNumber).filter(
            SipPhoneNumber.user_id == user_id,
            SipPhoneNumber.is_active == True,  # noqa: E712
            SipPhoneNumber.allow_outbound == True,  # noqa: E712
        )
        if preferred:
            digits = normalize_sip_number(preferred)
            record = query.filter(SipPhoneNumber.phone_number == digits).first()
            if record:
                return record
        return query.order_by(SipPhoneNumber.created_at.asc()).first()

    @staticmethod
    def load_assistant(db: Session, assistant_type: Optional[str], assistant_id: Any):
        """Вернуть объект ассистента нужного типа (или None). Только поддерживаемые телефонией типы."""
        aid = _parse_uuid(assistant_id)
        if not aid or assistant_type not in SIP_SUPPORTED_ASSISTANT_TYPES:
            return None
        if assistant_type == "openai":
            return db.query(AssistantConfig).filter(AssistantConfig.id == aid).first()
        if assistant_type == "gemini":
            return db.query(GeminiAssistantConfig).filter(GeminiAssistantConfig.id == aid).first()
        if assistant_type == "fish":
            return db.query(FishAssistantConfig).filter(FishAssistantConfig.id == aid).first()
        return None

    # ------------------------------------------------------------------ calls
    @staticmethod
    def queue_outbound_call(
        db: Session,
        *,
        user_id: uuid.UUID,
        to_number: str,
        caller_number: SipPhoneNumber,
        assistant_type: str,
        assistant_id: Any,
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[uuid.UUID] = None,
        gateway_id: Optional[str] = None,
    ) -> SipCall:
        """Поставить исходящий звонок в очередь. Отправит его на шлюз воркер с управляющим сокетом."""
        call = SipCall(
            id=uuid.uuid4(),
            gateway_id=gateway_id or caller_number.gateway_id or settings.SIP_GATEWAY_DEFAULT_ID,
            direction="outbound",
            status=SipCallStatus.QUEUED,
            user_id=user_id,
            phone_number_id=caller_number.id,
            task_id=task_id,
            assistant_type=assistant_type,
            assistant_id=_parse_uuid(assistant_id),
            did=caller_number.phone_number,
            to_number=normalize_sip_number(to_number),
            call_metadata=metadata or {},
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        logger.info(f"[SIP] queued outbound call {call.id}: {call.did} -> {call.to_number} via {call.gateway_id}")
        return call

    @staticmethod
    def claim_queued_calls(db: Session, gateway_id: str, limit: int) -> List[SipCall]:
        """
        Атомарно забрать до `limit` исходящих из очереди шлюза и перевести их в 'dialing'.
        FOR UPDATE SKIP LOCKED — несколько воркеров Gunicorn не возьмут один звонок дважды.
        """
        if limit <= 0:
            return []
        try:
            rows = (
                db.query(SipCall)
                .filter(
                    SipCall.gateway_id == gateway_id,
                    SipCall.status == SipCallStatus.QUEUED,
                    SipCall.created_at <= _utcnow(),  # отложенные повторы ждут своего времени
                )
                .order_by(SipCall.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(limit)
                .all()
            )
            now = _utcnow()
            for call in rows:
                call.status = SipCallStatus.DIALING
                call.dialed_at = now
                call.attempts = (call.attempts or 0) + 1
            db.commit()
            return rows
        except Exception as exc:
            db.rollback()
            logger.error(f"[SIP] claim_queued_calls failed: {exc}")
            return []

    @staticmethod
    def active_outbound_count(db: Session, gateway_id: str) -> int:
        return (
            db.query(SipCall)
            .filter(
                SipCall.gateway_id == gateway_id,
                SipCall.direction == "outbound",
                SipCall.status.in_([SipCallStatus.DIALING, SipCallStatus.RINGING, SipCallStatus.ANSWERED]),
            )
            .count()
        )

    @staticmethod
    def originate_payload(call: SipCall) -> Dict[str, Any]:
        return {
            "type": "originate",
            "call_id": str(call.id),
            "to": call.to_number,
            "caller_id": call.did,
            "assistant_id": str(call.assistant_id) if call.assistant_id else "",
            "assistant_type": call.assistant_type or "",
            "metadata": call.call_metadata or {},
        }

    @staticmethod
    def get_or_create_inbound_call(
        db: Session,
        *,
        call_id: uuid.UUID,
        gateway_id: str,
        did: str,
        caller: str,
        number: Optional[SipPhoneNumber],
    ) -> SipCall:
        """Строка входящего звонка. Может быть создана и событием 'started', и медиа-сокетом — кто первый."""
        call = db.get(SipCall, call_id)
        if call:
            return call
        call = SipCall(
            id=call_id,
            gateway_id=gateway_id,
            direction="inbound",
            status=SipCallStatus.ANSWERED,
            user_id=number.user_id if number else None,
            phone_number_id=number.id if number else None,
            assistant_type=number.assistant_type if number else None,
            assistant_id=number.assistant_id if number else None,
            did=normalize_sip_number(did) or did,
            caller=normalize_sip_number(caller) or caller,
            call_metadata={},
        )
        db.add(call)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            call = db.get(SipCall, call_id)
        return call

    # ------------------------------------------------------------------ events from the bridge
    @staticmethod
    def apply_bridge_event(db: Session, event: Dict[str, Any]) -> Optional[SipCall]:
        """Применить событие call.event от моста к sip_calls / tasks / agent_calls."""
        call_id = _parse_uuid(event.get("call_id"))
        if not call_id:
            return None
        kind = event.get("event")
        gateway_id = event.get("gateway_id") or settings.SIP_GATEWAY_DEFAULT_ID
        call = db.get(SipCall, call_id)

        if call is None:
            if event.get("direction") == "inbound":
                number = SipGatewayService.find_number(db, event.get("did", ""))
                call = SipGatewayService.get_or_create_inbound_call(
                    db, call_id=call_id, gateway_id=gateway_id,
                    did=event.get("did", ""), caller=event.get("caller", ""), number=number,
                )
            else:
                logger.warning(f"[SIP] event {kind} for unknown outbound call {call_id}")
                return None

        now = _utcnow()
        if event.get("trunk_host"):
            call.trunk_host = event["trunk_host"]

        if kind == "started":
            if call.direction == "outbound" and call.status == SipCallStatus.QUEUED:
                call.status = SipCallStatus.DIALING
                call.dialed_at = call.dialed_at or now
        elif kind == "answered":
            call.status = SipCallStatus.ANSWERED
            call.answered_at = call.answered_at or now
        elif kind == "ended":
            call.status = SipCallStatus.COMPLETED
            call.ended_at = now
            call.end_reason = event.get("reason") or "ended"
            if event.get("duration_sec") is not None:
                call.duration_sec = float(event["duration_sec"])
            elif call.answered_at:
                call.duration_sec = round((now - call.answered_at).total_seconds(), 1)
            SipGatewayService._finish_task(db, call, success=True)
        elif kind == "failed":
            reason = event.get("reason") or "failed"
            if (
                call.direction == "outbound"
                and reason in RETRYABLE_FAIL_REASONS
                and (call.attempts or 0) < MAX_ORIGINATE_ATTEMPTS
            ):
                # Вернуть в очередь, следующая попытка не раньше чем через RETRY_DELAY_SECONDS
                call.status = SipCallStatus.QUEUED
                call.end_reason = reason
                call.created_at = now + timedelta(seconds=RETRY_DELAY_SECONDS)
                logger.info(f"[SIP] call {call.id} requeued after '{reason}' (attempt {call.attempts})")
            else:
                call.status = SipCallStatus.FAILED
                call.ended_at = now
                call.end_reason = reason
                SipGatewayService._finish_task(db, call, success=False)
        db.commit()
        return call

    @staticmethod
    def _finish_task(db: Session, call: SipCall, success: bool) -> None:
        """Обновить Task и AgentCall, если звонок был запущен планировщиком/CRM."""
        if not call.task_id:
            return
        task = db.get(Task, call.task_id)
        if task:
            if success:
                task.status = TaskStatus.COMPLETED
                task.call_completed_at = _utcnow()
                task.call_result = f"Call completed via SIP gateway. Duration: {call.duration_sec or 0}s. Session: {call.id}"
            else:
                task.status = TaskStatus.FAILED
                task.call_result = f"Call failed via SIP gateway: {call.end_reason}"
        agent_call = db.query(AgentCall).filter(AgentCall.source_task_id == call.task_id).first()
        if agent_call:
            agent_call.call_session_id = str(call.id)
            if success:
                # Статус оставляем 'calling': PostCall-финализация забирает звонок только из него.
                agent_call.duration_seconds = int(call.duration_sec or 0)
                agent_call.completed_at = _utcnow()
            else:
                agent_call.status = "no_answer" if call.end_reason in ("busy", "no_answer") else "failed"
                agent_call.completed_at = _utcnow()

    # ------------------------------------------------------------------ post-call
    @staticmethod
    def tag_conversations(db: Session, call: SipCall, started_at: datetime) -> int:
        """
        Проставить номер и направление в conversations, записанных хендлером за время звонка.
        Хендлеры пишут caller_number=None; PostCall и CRM ищут транскрипт по номеру.
        """
        if not call.assistant_id:
            return 0
        if call.direction == "inbound":
            phone, direction = call.caller, "INBOUND"
        else:
            phone, direction = call.to_number, "OUTBOUND"
        if not phone:
            return 0
        model = {"gemini": GeminiConversation, "fish": FishConversation}.get(call.assistant_type, Conversation)
        rows = (
            db.query(model)
            .filter(
                model.assistant_id == call.assistant_id,
                model.created_at >= started_at - timedelta(seconds=10),
                model.created_at <= _utcnow() + timedelta(seconds=5),
                or_(model.caller_number.is_(None), model.caller_number == "", model.caller_number == "unknown"),
            )
            .all()
        )
        for conv in rows:
            conv.caller_number = phone
            if hasattr(conv, "call_direction"):
                conv.call_direction = direction
        if rows:
            db.commit()
        return len(rows)

    @staticmethod
    def call_context_text(call: SipCall) -> str:
        """Текст контекста звонка, который дописывается к системному промпту."""
        meta = call.call_metadata or {}
        parts = []
        if call.direction == "outbound":
            parts.append("Это исходящий телефонный звонок, который ты инициируешь.")
            if meta.get("contact_name"):
                parts.append(f"Имя собеседника: {meta['contact_name']}.")
            if meta.get("task_title"):
                parts.append(f"Цель звонка: {meta['task_title']}.")
            if meta.get("task_description"):
                parts.append(f"Подробности: {meta['task_description']}")
        else:
            parts.append("Это входящий телефонный звонок.")
            if call.caller:
                parts.append(f"Номер звонящего: +{call.caller}.")
        parts.append("Говори коротко, как по телефону. Когда разговор завершён, вызови функцию hangup_call.")
        return " ".join(parts)

    @staticmethod
    def resolve_greeting(call: SipCall, number: Optional[SipPhoneNumber], assistant) -> Optional[str]:
        meta = call.call_metadata or {}
        greeting = None
        if call.direction == "outbound":
            greeting = meta.get("custom_greeting") or None
        if not greeting and number is not None and number.first_phrase:
            greeting = number.first_phrase
        if not greeting:
            greeting = getattr(assistant, "greeting_message", None)
        if greeting and "{name}" in greeting:
            greeting = greeting.replace("{name}", meta.get("contact_name") or "").replace("  ", " ").strip()
        return greeting
