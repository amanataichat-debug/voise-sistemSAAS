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

import re
import uuid
from datetime import datetime, timedelta, timezone
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
    is_o_number,
    O_MOBILE_PREFIXES,
)
from backend.models.assistant import AssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
from backend.models.fish_assistant import FishAssistantConfig, FishConversation
from backend.models.eleven_assistant import ElevenAssistantConfig, ElevenConversation
from backend.models.agent_config import AgentConfig
from backend.models.conversation import Conversation
from backend.models.task import Task, TaskStatus
from backend.models.agent_call import AgentCall
from backend.models.agent_contact import AgentContact

logger = get_logger(__name__)

# Причины отказа, при которых исходящий имеет смысл повторить позже.
# ВАЖНО (баг B1, долбёж): сюда входят ТОЛЬКО причины, при которых мост вообще
# не набирал номер (все линии заняты, AMI не подключён — отказ ДО originate).
# "congestion" убран намеренно: оператор отдаёт его при СБРОСЕ клиентом /
# выключенном телефоне (AMI Reason 8) — повтор через 2 минуты = клиенту звонят
# ещё раз. "trunk_unavailable" (оба сервера оператора не приняли звонок — мост
# уже попробовал второй сам) и "ami_error" (команда могла уйти на Asterisk) тоже
# не повторяем: лишний набор опаснее, чем звонок завтра по политике недозвона.
# Сброс / занято / нет ответа НЕ повторяем: один звонок, дальше — политика
# недозвона (services/agent_call_policy.py): сообщение клиенту + один звонок завтра.
RETRYABLE_FAIL_REASONS = {"channel_limit", "ami_unavailable"}

# Причины, при которых звонок до телефона клиента точно НЕ дошёл (наша сторона):
# такой «недозвон» не считается попыткой против клиента и не ведёт к «Не звонить».
# trunk_unavailable — сервера оператора звонок не приняли, телефон не звонил.
# gateway_timeout сюда НЕ входит: мост мог успеть набрать номер — считаем попыткой.
NOT_REACHED_FAIL_REASONS = {"channel_limit", "ami_unavailable", "bad_number", "trunk_unavailable"}
MAX_ORIGINATE_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 120

# Жёсткий анти-долбёж (карта, баг B1): минимальный интервал между исходящими на
# ОДИН номер. Это последний рубеж — через queue_outbound_call проходят ВСЕ пути
# (планировщик, немедленный звонок, CRM, ручной), и он НЕ зависит от
# last_called_at (тот обновляется только после PostCall и отстаёт, из-за чего
# быстрый цикл повторов проскакивал). Защищает номер оператора от блокировки.
OUTBOUND_MIN_GAP_PER_NUMBER = timedelta(minutes=30)

# Жёсткий дневной предел для АВТОМАТИЧЕСКИХ звонков (агент обзвона / планировщик,
# т.е. с task_id): не больше ОДНОГО звонка в день на номер, при ЛЮБОМ исходе
# (ответил / не ответил / сбросил — неважно). Это последний рубеж в ЕДИНСТВЕННОЙ
# точке, через которую проходят все наборы, и он не зависит ни от LLM, ни от
# last_called_at (тот отстаёт до PostCall). То же правило «1 звонок/день» уже
# стоит в слое тулзы (_check_call_guards.already_called_today), здесь оно
# продублировано жёстко и независимо — ровно от этого «агент долбит, пока клиент
# не возьмёт трубку». Если сегодня (по Бишкеку) этому номеру уже ставили
# автоматический исходящий — второй сегодня НЕ ставим.
#
# Ручной звонок владельца (без task_id) под это правило не попадает: он защищён
# только 30-минутным зазором, чтобы владелец мог позвонить/протестировать сам.
OUTBOUND_AGENT_MAX_PER_DAY = 1


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
    def describe_number(db: Session, number: SipPhoneNumber) -> Dict[str, Any]:
        """to_dict() номера + имена привязанного ассистента и агента для интерфейса."""
        data = number.to_dict()
        assistant = SipGatewayService.load_assistant(db, number.assistant_type, number.assistant_id)
        data["assistant_name"] = getattr(assistant, "name", None)
        data["assistant_active"] = bool(getattr(assistant, "is_active", False)) if assistant else None
        agent = db.get(AgentConfig, number.agent_config_id) if number.agent_config_id else None
        data["agent_name"] = agent.name if agent else None
        return data

    @staticmethod
    def bind_number_to_agent(number: SipPhoneNumber, agent: AgentConfig) -> None:
        """Привязать номер к агенту обзвона: входящие идут через его голосового ассистента.

        Тип ассистента агента должен поддерживаться телефонией, иначе ValueError.
        """
        if agent.assistant_type not in SIP_SUPPORTED_ASSISTANT_TYPES:
            raise ValueError(f"Тип ассистента агента «{agent.assistant_type}» не поддерживается телефонией")
        voice_id = agent.get_voice_assistant_id()
        if not voice_id:
            raise ValueError("У агента нет голосового ассистента")
        number.agent_config_id = agent.id
        number.assistant_type = agent.assistant_type
        number.assistant_id = voice_id

    @staticmethod
    def sync_agent_numbers(db: Session, agent_id: uuid.UUID, assistant_type: Optional[str], assistant_id: Any) -> int:
        """Перевести номера агента на его нового голосового ассистента (после смены типа).

        Если новый тип телефонией не поддерживается, номера отвязываются целиком,
        чтобы входящий не ушёл на несуществующий хендлер.
        """
        numbers = db.query(SipPhoneNumber).filter(SipPhoneNumber.agent_config_id == agent_id).all()
        for number in numbers:
            if assistant_type in SIP_SUPPORTED_ASSISTANT_TYPES and assistant_id:
                number.assistant_type = assistant_type
                number.assistant_id = _parse_uuid(assistant_id)
            else:
                number.agent_config_id = None
                number.assistant_type = None
                number.assistant_id = None
        return len(numbers)

    @staticmethod
    def unbind_agent_numbers(db: Session, agent_id: uuid.UUID) -> int:
        """Отвязать номера агента перед его удалением (assistant_id без FK сам не обнулится)."""
        return SipGatewayService.sync_agent_numbers(db, agent_id, None, None)

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
        if assistant_type == "eleven":
            return db.query(ElevenAssistantConfig).filter(ElevenAssistantConfig.id == aid).first()
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
        """Поставить исходящий звонок в очередь. Отправит его на шлюз воркер с управляющим сокетом.

        Транк оператора пропускает только номера O!, поэтому остальные отклоняются сразу
        (ValueError), а не падают на транке: так же для ручного звонка, CRM и агента.
        """
        to_digits = normalize_sip_number(to_number)
        if not is_o_number(to_digits):
            raise ValueError(
                "Исходящие возможны только на номера O! (префиксы " + ", ".join(O_MOBILE_PREFIXES) + ")"
            )
        # Жёсткий анти-долбёж: не ставим второй исходящий на тот же номер, если по
        # нему уже был звонок за последние OUTBOUND_MIN_GAP_PER_NUMBER. Срабатывает
        # на ЛЮБОМ пути постановки и не зависит от last_called_at. Защищает от
        # блокировки номера оператором (клиент сбросил → агент не долбит подряд).
        recent_call = (
            db.query(SipCall.id)
            .filter(
                SipCall.user_id == user_id,
                SipCall.direction == "outbound",
                SipCall.to_number == to_digits,
                SipCall.created_at >= _utcnow() - OUTBOUND_MIN_GAP_PER_NUMBER,
            )
            .first()
        )
        if recent_call is not None:
            logger.warning(
                f"[SIP] anti-flood: отклонён повторный исходящий на {to_digits} "
                f"(user {user_id}) — был звонок менее {int(OUTBOUND_MIN_GAP_PER_NUMBER.total_seconds() // 60)} мин назад"
            )
            raise ValueError(
                "Этому номеру уже звонили только что — повторный звонок будет позже, "
                "чтобы не беспокоить клиента подряд."
            )
        # Дневной предел для автоматических (агентских) наборов: один звонок в день
        # на номер при любом исходе. Окно — календарный день по Бишкеку. Ручной
        # звонок владельца (task_id=None) сюда не попадает.
        if task_id is not None:
            from backend.core.timezone_utils import BISHKEK
            now_local = _utcnow().replace(tzinfo=timezone.utc).astimezone(BISHKEK)
            day_start_utc = (
                now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                .astimezone(timezone.utc).replace(tzinfo=None)
            )
            calls_today = (
                db.query(SipCall.id)
                .filter(
                    SipCall.user_id == user_id,
                    SipCall.direction == "outbound",
                    SipCall.to_number == to_digits,
                    SipCall.task_id.isnot(None),
                    SipCall.created_at >= day_start_utc,
                )
                .count()
            )
            if calls_today >= OUTBOUND_AGENT_MAX_PER_DAY:
                logger.warning(
                    f"[SIP] daily-cap: отклонён автоматический исходящий на {to_digits} "
                    f"(user {user_id}) — сегодня уже было {calls_today} набор(а/ов), "
                    f"предел {OUTBOUND_AGENT_MAX_PER_DAY}/день"
                )
                raise ValueError(
                    "Этому клиенту сегодня уже звонили — больше одного звонка в день "
                    "не делаем, чтобы не беспокоить. Следующий звонок — не раньше завтра."
                )
        if assistant_type == "eleven":
            # Минуты Eleven-карточек (и агентов) платятся из кошелька: пустой — не набираем номер
            from backend.services.wallet_service import WalletService
            if not WalletService.can_start_call(db, user_id):
                raise ValueError("Баланс кошелька исчерпан — пополните кошелёк, чтобы звонить")
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
            to_number=to_digits,
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
                try:
                    from backend.services.agent_call_finalizer import AgentCallFinalizer
                    AgentCallFinalizer.on_call_failed(db, call)
                except Exception as exc:
                    logger.warning(f"[SIP] agent finalization for failed call {call.id} failed: {exc}")
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
        model = {"gemini": GeminiConversation, "fish": FishConversation, "eleven": ElevenConversation}.get(
            call.assistant_type, Conversation)
        if call.conversation_session_id:
            # Хендлер писал диалог под session_id = id звонка (Fish/Eleven) — привязка точная.
            rows = db.query(model).filter(
                model.assistant_id == call.assistant_id,
                model.session_id == call.conversation_session_id,
            ).all()
        else:
            # Остальные хендлеры: по ассистенту и окну времени звонка.
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
        from backend.services.conversation_service import ConversationService
        phone = ConversationService._normalize_phone(phone)
        if phone == "unknown":
            return 0
        # Контакт CRM: хендлеры сохраняют диалог без номера, поэтому создаём
        # (или обновляем) карточку здесь, когда номер звонка уже известен.
        contact_id = SipGatewayService._ensure_crm_contact(db, call, phone)
        for conv in rows:
            conv.caller_number = phone
            if hasattr(conv, "call_direction"):
                conv.call_direction = direction
            if contact_id and hasattr(conv, "contact_id") and not conv.contact_id:
                conv.contact_id = contact_id
        if rows or contact_id:
            db.commit()
        return len(rows)

    @staticmethod
    def _ensure_crm_contact(db: Session, call: SipCall, phone: str):
        """Найти/создать контакт CRM владельца ассистента по номеру звонка (без commit)."""
        from backend.services.conversation_service import ConversationService
        owner_id = call.user_id
        if not owner_id and call.assistant_id:
            assistant, _ = ConversationService._find_assistant_by_id(db, str(call.assistant_id))
            owner_id = getattr(assistant, "user_id", None)
        if not owner_id:
            return None
        try:
            with db.begin_nested():
                return ConversationService._get_or_create_contact(db, owner_id, phone)
        except Exception as exc:
            logger.warning(f"[SIP] call {call.id}: CRM contact upsert failed: {exc}")
            return None

    @staticmethod
    def link_conversation_session(db: Session, call: SipCall) -> None:
        """
        Найти session_id диалога, записанного хендлером за время звонка, и сохранить его в
        call.conversation_session_id (без commit). Fish/Eleven проставляют его при старте
        звонка; OpenAI/Gemini — нет, их диалог ищем по ассистенту, номеру и окну звонка
        (tag_conversations к этому моменту уже проставил номер).
        """
        if call.conversation_session_id or not call.assistant_id:
            return
        phone = call.caller if call.direction == "inbound" else call.to_number
        if not phone:
            return
        from backend.services.conversation_service import ConversationService
        phone = ConversationService._normalize_phone(phone)
        model = {"gemini": GeminiConversation, "fish": FishConversation, "eleven": ElevenConversation}.get(
            call.assistant_type, Conversation)
        start = call.answered_at or call.created_at
        end = call.ended_at or _utcnow()
        try:
            row = (
                db.query(model.session_id)
                .filter(
                    model.assistant_id == call.assistant_id,
                    model.caller_number == phone,
                    model.created_at >= start - timedelta(seconds=10),
                    model.created_at <= end + timedelta(seconds=60),
                )
                .order_by(model.created_at.asc())
                .first()
            )
        except Exception as exc:
            logger.warning(f"[SIP] call {call.id}: conversation lookup failed: {exc}")
            db.rollback()
            return
        if row and row.session_id:
            call.conversation_session_id = row.session_id

    @staticmethod
    def recording_urls_for_sessions(db: Session, session_ids) -> Dict[str, str]:
        """{session_id диалога: ссылка на запись звонка} для пачки диалогов одним запросом."""
        ids = [str(s) for s in session_ids if s]
        if not ids:
            return {}
        try:
            rows = (
                db.query(SipCall.conversation_session_id, SipCall.recording_url)
                .filter(SipCall.conversation_session_id.in_(ids), SipCall.recording_url.isnot(None))
                .all()
            )
        except Exception as exc:
            logger.warning(f"[SIP] recording lookup failed: {exc}")
            db.rollback()
            return {}
        return {r.conversation_session_id: r.recording_url for r in rows}

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
            if meta.get("call_strategy"):
                parts.append(f"Стратегия разговора: {meta['call_strategy']}")
        else:
            parts.append("Это входящий телефонный звонок.")
            if call.caller:
                parts.append(f"Номер звонящего: +{call.caller}.")
        parts.append("Говори коротко, как по телефону. Когда разговор завершён, вызови функцию hangup_call.")
        return " ".join(parts)

    @staticmethod
    def inbound_recent_contacts_hint(call: SipCall, agent, db: Session) -> str:
        """Подсказка голосовому агенту для входящего с незнакомого номера.

        Ветка 1810-anri-sales-logic. Если клиент звонит с номера, который не
        совпадает ни с одним контактом агента, но за последний час другие
        контакты писали или звонили — высока вероятность, что он кто-то из
        них с другого номера. Передаём короткий список имён + ниш, чтобы
        после «как вас зовут?» модель быстро состыковала.

        Возвращает пустую строку, если непонятно, что подсказывать (нет
        agent, нет caller, такой номер уже привязан к известному контакту).
        """
        if call.direction != "inbound" or agent is None or not call.caller:
            return ""
        phone = call.caller
        phone_tail = phone[-9:] if len(phone) >= 9 else phone
        try:
            known = (
                db.query(AgentContact.id)
                .filter(
                    AgentContact.agent_config_id == agent.id,
                    AgentContact.phone.like(f"%{phone_tail}"),
                )
                .first()
            )
            if known is not None:
                return ""
            hour_ago = datetime.utcnow() - timedelta(hours=1)
            recent = (
                db.query(AgentContact)
                .filter(
                    AgentContact.agent_config_id == agent.id,
                    AgentContact.updated_at >= hour_ago,
                )
                .order_by(AgentContact.updated_at.desc())
                .limit(5)
                .all()
            )
        except Exception as e:
            logger.warning(f"[SIP] inbound_recent_contacts_hint failed: {e}")
            return ""
        if not recent:
            return ""
        lines = [
            "Клиент звонит с незнакомого номера. Поздоровайся коротко как с новым, "
            "уточни имя и чем занимается. За последний час нам писали или звонили:"
        ]
        for c in recent:
            name = (c.name or "—").strip()
            niche = ""
            if isinstance(c.memory, dict):
                niche = str(c.memory.get("niche") or c.memory.get("industry") or "").strip()
            marker = f"— {name}"
            if niche:
                marker += f" ({niche})"
            lines.append(marker)
        lines.append(
            "Если имя или ниша совпали — найди этого контакта в CRM "
            "(search_contact_by_phone по его прежнему номеру из списка) и продолжай "
            "с места переписки: клиент уже знает контекст."
        )
        return "\n".join(lines)

    @staticmethod
    def inbound_known_contact_block(call: SipCall, agent, db: Session) -> str:
        """Узнавание клиента на входящем звонке с ИЗВЕСТНОГО номера (этап 3).

        До этой правки входящий звонок от уже знакомого контакта шёл «вхолодную»:
        в контекст попадал только номер звонящего, без имени, без памяти и без
        истории переписки. Клиент, который только что консультировался в
        WhatsApp/Telegram, звонил — и агент начинал с нуля («кто вы, чем
        занимаетесь»). Теперь по номеру поднимаем карточку контакта и единую
        межканальную хронологию (`build_conversation_timeline` — те же звонки +
        WhatsApp/Telegram/Instagram/SMS, что видит оркестратор) и отдаём их
        голосовой модели: «я вас помню, вы писали про …».

        Ключ связки — номер телефона (последние 9 цифр). Возвращает пустую
        строку, если звонок не входящий, нет agent/caller, или номер не совпал
        ни с одним контактом (тогда отрабатывает inbound_recent_contacts_hint).
        Best-effort — любой сбой не должен рвать звонок (ловится выше).
        """
        if call.direction != "inbound" or agent is None or not call.caller:
            return ""
        phone = call.caller
        phone_tail = phone[-9:] if len(phone) >= 9 else phone
        contact = (
            db.query(AgentContact)
            .filter(
                AgentContact.agent_config_id == agent.id,
                AgentContact.phone.like(f"%{phone_tail}"),
            )
            .order_by(AgentContact.updated_at.desc())
            .first()
        )
        if contact is None:
            return ""

        name = (contact.name or "").strip()
        company = (contact.company or "").strip()
        memory = contact.memory if isinstance(contact.memory, dict) else {}

        def _mem(*keys: str) -> str:
            for k in keys:
                v = memory.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return ""

        niche = _mem("niche", "industry")
        need = _mem("direction_need", "needs")
        summary = _mem("summary")
        best_time = _mem("best_time")

        head = "Звонит ЗНАКОМЫЙ клиент — ты уже общался с ним раньше."
        if name:
            head += f" Имя: {name}" + (f" ({company})" if company else "") + "."
        elif company:
            head += f" Компания: {company}."
        lines = [head]
        if niche:
            lines.append(f"Ниша: {niche}.")
        if need:
            lines.append(f"Запрос клиента: {need}.")
        if summary:
            lines.append(f"Что знаем о клиенте: {summary}")
        if best_time:
            lines.append(f"Удобное время: {best_time}.")
        lines.append(
            "Поздоровайся как со знакомым, по имени, и продолжай с места — НЕ "
            "спрашивай заново, кто он и чем занимается. Опирайся на хронологию ниже."
        )

        # Единая межканальная история — компактно (голосовой слой видит только
        # начало промпта, длинная лента вытеснила бы знания о компании).
        timeline = ""
        try:
            from backend.services.agent_orchestrator import build_conversation_timeline
            timeline = build_conversation_timeline(db, contact, max_events=10)
        except Exception as e:
            logger.warning(f"[SIP] inbound_known_contact_block timeline failed: {e}")

        return "\n".join(lines) + (timeline or "")

    @staticmethod
    def resolve_greeting(call: SipCall, number: Optional[SipPhoneNumber], assistant,
                         db: Optional[Session] = None) -> Optional[str]:
        """
        Первая фраза звонка. Исходящий: custom_greeting задачи (PreCall агента) →
        first_phrase номера → приветствие ассистента. Входящий на номер агента:
        first_phrase номера → inbound_first_phrase агента → приветствие ассистента;
        {name} — имя из метаданных звонка или из контакта агента с этим номером.
        """
        meta = call.call_metadata or {}
        contact_name = meta.get("contact_name") or ""
        greeting = None
        if call.direction == "outbound":
            greeting = meta.get("custom_greeting") or None
        if not greeting and number is not None and number.first_phrase:
            greeting = number.first_phrase
        agent = None
        if call.direction == "inbound" and db is not None and number is not None and number.agent_config_id:
            agent = db.get(AgentConfig, number.agent_config_id)
            if not greeting and agent is not None and agent.inbound_first_phrase:
                greeting = agent.inbound_first_phrase
        if not greeting:
            greeting = getattr(assistant, "greeting_message", None)
        if greeting and "{name}" in greeting:
            if not contact_name and agent is not None and call.caller:
                contact = (
                    db.query(AgentContact)
                    .filter(AgentContact.agent_config_id == agent.id,
                            AgentContact.phone.like(f"%{call.caller[-9:]}"))
                    .order_by(AgentContact.created_at.desc())
                    .first()
                )
                contact_name = (contact.name or "") if contact else ""
            if contact_name:
                greeting = greeting.replace("{name}", contact_name)
            else:
                # «Салам, {name}!» без имени → «Салам!», а не «Салам, !»
                greeting = re.sub(r",?\s*\{name\}", "", greeting)
            greeting = " ".join(greeting.split())
        return greeting
