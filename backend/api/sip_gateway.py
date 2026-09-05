"""
Собственная SIP-телефония: эндпоинты для моста шлюза и API номеров/звонков.

WebSocket (для моста, авторизация по токену SIP_GATEWAY_TOKEN):
  * /ws/sip-gateway/control  — одно постоянное соединение на шлюз. Мост шлёт
    hello и события звонков; бэкенд шлёт команды originate/hangup. Очередь
    исходящих живёт в таблице sip_calls, воркер с этим сокетом её разгребает.
  * /ws/sip/{call_id}        — медиа одного звонка. Первым сообщением "start",
    дальше бинарный PCM16 8 кГц. Звонок заворачивается в браузерный хендлер
    OpenAI/Gemini через backend.websockets.sip_media_adapter.

HTTP (JWT пользователя):
  * /api/sip/numbers         — номера от оператора и привязка к ассистентам
  * /api/sip/calls           — журнал и ручной запуск исходящего
  * /api/sip/gateways        — состояние подключённых шлюзов (этого воркера)

Протокол моста описан в infra/sip-gateway/README.md.
ВАЖНО: роутер должен подключаться в app.py ДО websocket.router, иначе
/ws/{assistant_id} перехватит /ws/sip/... и /ws/sip-gateway/control.
"""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from backend.core.config import settings
from backend.core.dependencies import get_current_user
from backend.core.logging import get_logger
from backend.db.session import SessionLocal, get_db
from backend.models.user import User
from backend.models.sip_gateway import (
    SipPhoneNumber,
    SipCall,
    SipCallStatus,
    SIP_SUPPORTED_ASSISTANT_TYPES,
    normalize_sip_number,
)
from backend.models.task import Task, TaskStatus
from backend.services.sip_gateway_service import SipGatewayService
from backend.websockets.sip_media_adapter import HandlerSocket
from backend.websockets.handler_realtime_new import handle_websocket_connection_new
from backend.websockets.handler_gemini import handle_gemini_websocket_connection

logger = get_logger(__name__)
router = APIRouter()

# Шлюзы, подключённые к ЭТОМУ воркеру: gateway_id -> состояние
GATEWAYS: Dict[str, Dict[str, Any]] = {}

POLL_INTERVAL = 1.0
STALE_DIALING_SECONDS = 180
STALE_ANSWERED_SECONDS = 2 * 3600


_tables_ready = False


def _ensure_tables() -> None:
    """
    Создать sip_phone_numbers / sip_calls, если их нет. Идемпотентно и дёшево.
    Страховка: воркер, который делает create_all на старте, на Render иногда
    убивается по таймауту, и остальные стартуют без миграций.
    """
    global _tables_ready
    if _tables_ready:
        return
    try:
        from backend.models.base import Base, engine
        Base.metadata.create_all(engine, tables=[SipPhoneNumber.__table__, SipCall.__table__], checkfirst=True)
        _tables_ready = True
    except Exception as exc:
        logger.error(f"[SIP] ensure tables failed: {exc}")


def _token_ok(token: Optional[str]) -> bool:
    expected = settings.SIP_GATEWAY_TOKEN
    if not expected:
        logger.error("[SIP] SIP_GATEWAY_TOKEN is not configured on the backend")
        return False
    return bool(token) and token == expected


# =============================================================================
# Управляющий сокет шлюза
# =============================================================================


@router.websocket("/ws/sip-gateway/control")
async def sip_gateway_control(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    gateway: str = Query("sip-gw-1"),
):
    if not _token_ok(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    _ensure_tables()
    gateway_id = gateway or settings.SIP_GATEWAY_DEFAULT_ID
    state: Dict[str, Any] = {
        "gateway_id": gateway_id,
        "connected_at": time.time(),
        "last_seen": time.time(),
        "max_outbound": 4,
        "public_ip": None,
        "version": None,
        "worker_pid": os.getpid(),
        "events": 0,
        "websocket": websocket,
    }
    GATEWAYS[gateway_id] = state
    logger.info(f"[SIP] gateway '{gateway_id}' control socket connected (worker {os.getpid()})")

    poll_task = asyncio.create_task(_originate_loop(websocket, state))
    try:
        while True:
            message = await websocket.receive_text()
            state["last_seen"] = time.time()
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            mtype = data.get("type")
            if mtype == "hello":
                state["max_outbound"] = int(data.get("max_outbound") or 4)
                state["public_ip"] = data.get("public_ip")
                state["version"] = data.get("version")
                logger.info(
                    f"[SIP] gateway '{gateway_id}' hello: version={state['version']} "
                    f"ip={state['public_ip']} max_outbound={state['max_outbound']} "
                    f"active_calls={len(data.get('active_calls') or [])}"
                )
            elif mtype == "call.event":
                state["events"] += 1
                data.setdefault("gateway_id", gateway_id)
                db = SessionLocal()
                try:
                    SipGatewayService.apply_bridge_event(db, data)
                except Exception as exc:
                    db.rollback()
                    logger.error(f"[SIP] failed to apply event {data.get('event')} for {data.get('call_id')}: {exc}", exc_info=True)
                finally:
                    db.close()
            elif mtype in ("pong", "status"):
                pass
            else:
                logger.debug(f"[SIP] gateway '{gateway_id}' sent unknown message type {mtype}")
    except WebSocketDisconnect:
        logger.info(f"[SIP] gateway '{gateway_id}' control socket disconnected")
    except Exception as exc:
        logger.error(f"[SIP] gateway '{gateway_id}' control socket error: {exc}", exc_info=True)
    finally:
        poll_task.cancel()
        if GATEWAYS.get(gateway_id) is state:
            GATEWAYS.pop(gateway_id, None)


async def _originate_loop(websocket: WebSocket, state: Dict[str, Any]) -> None:
    """Раз в секунду забирает исходящие из очереди и отправляет на шлюз. Раз в 30 с — ping и уборка зависших."""
    gateway_id = state["gateway_id"]
    last_ping = time.time()
    last_sweep = time.time()
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)
            db = SessionLocal()
            try:
                active = SipGatewayService.active_outbound_count(db, gateway_id)
                free = int(state.get("max_outbound") or 4) - active
                calls = SipGatewayService.claim_queued_calls(db, gateway_id, free) if free > 0 else []
                for call in calls:
                    payload = SipGatewayService.originate_payload(call)
                    await websocket.send_text(json.dumps(payload))
                    logger.info(f"[SIP] originate sent to '{gateway_id}': {call.did} -> {call.to_number} (call {call.id})")
                if time.time() - last_sweep > 30:
                    last_sweep = time.time()
                    _sweep_stale_calls(db, gateway_id)
            finally:
                db.close()
            if time.time() - last_ping > 30:
                last_ping = time.time()
                await websocket.send_text(json.dumps({"type": "ping"}))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(f"[SIP] originate loop for '{gateway_id}': {exc}")
            await asyncio.sleep(2)


def _sweep_stale_calls(db: Session, gateway_id: str) -> None:
    """Звонки, по которым шлюз так и не прислал результат (перезапуск моста и т.п.)."""
    now = datetime.utcnow()
    stale_dialing = (
        db.query(SipCall)
        .filter(
            SipCall.gateway_id == gateway_id,
            SipCall.status == SipCallStatus.DIALING,
            SipCall.dialed_at < now - timedelta(seconds=STALE_DIALING_SECONDS),
        )
        .all()
    )
    for call in stale_dialing:
        SipGatewayService.apply_bridge_event(db, {
            "call_id": str(call.id), "event": "failed", "reason": "gateway_timeout", "gateway_id": gateway_id,
        })
    stale_answered = (
        db.query(SipCall)
        .filter(
            SipCall.gateway_id == gateway_id,
            SipCall.status == SipCallStatus.ANSWERED,
            SipCall.answered_at < now - timedelta(seconds=STALE_ANSWERED_SECONDS),
        )
        .all()
    )
    for call in stale_answered:
        SipGatewayService.apply_bridge_event(db, {
            "call_id": str(call.id), "event": "ended", "reason": "stale", "gateway_id": gateway_id,
        })


# =============================================================================
# Медиа-сокет одного звонка
# =============================================================================


@router.websocket("/ws/sip/{call_id}")
async def sip_media(
    websocket: WebSocket,
    call_id: str,
    token: Optional[str] = Query(None),
    gateway: str = Query("sip-gw-1"),
    db: Session = Depends(get_db),
):
    if not _token_ok(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    _ensure_tables()
    gateway_id = gateway or settings.SIP_GATEWAY_DEFAULT_ID

    async def reject(reason: str) -> None:
        logger.warning(f"[SIP-MEDIA] call {call_id} rejected: {reason}")
        try:
            await websocket.send_text(json.dumps({"type": "hangup", "reason": reason}))
            await websocket.close()
        except Exception:
            pass

    # 1. Первое сообщение — "start"
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        start = json.loads(first)
        if start.get("type") != "start":
            raise ValueError("first message is not start")
    except Exception as exc:
        await reject(f"bad_start: {exc}")
        return

    try:
        call_uuid = uuid.UUID(call_id)
    except ValueError:
        await reject("bad_call_id")
        return

    direction = start.get("direction") or "inbound"
    did = str(start.get("did") or "")
    caller = str(start.get("caller") or "")

    # 2. Строка звонка и номер
    number: Optional[SipPhoneNumber] = None
    if direction == "inbound":
        number = SipGatewayService.find_number(db, did)
        if number is None:
            await reject("unknown_number")
            return
        call = SipGatewayService.get_or_create_inbound_call(
            db, call_id=call_uuid, gateway_id=gateway_id, did=did, caller=caller, number=number,
        )
        if call.assistant_id is None and number.assistant_id is not None:
            call.assistant_type = number.assistant_type
            call.assistant_id = number.assistant_id
            call.user_id = number.user_id
            db.commit()
    else:
        call = db.get(SipCall, call_uuid)
        if call is None:
            await reject("unknown_call")
            return
        if call.phone_number_id:
            number = db.get(SipPhoneNumber, call.phone_number_id)

    if call.status != SipCallStatus.ANSWERED:
        call.status = SipCallStatus.ANSWERED
        call.answered_at = call.answered_at or datetime.utcnow()
        db.commit()

    # 3. Ассистент
    if not call.assistant_type or call.assistant_type not in SIP_SUPPORTED_ASSISTANT_TYPES:
        await reject("assistant_type_unsupported")
        return
    assistant = SipGatewayService.load_assistant(db, call.assistant_type, call.assistant_id)
    if assistant is None:
        await reject("assistant_not_found")
        return

    # 4. Подмена приветствия и контекста звонка в памяти, без записи в БД.
    #    expire_on_commit=False, чтобы коммиты внутри хендлера не перечитали объект из базы.
    db.expire_on_commit = False
    greeting = SipGatewayService.resolve_greeting(call, number, assistant)
    if greeting:
        set_committed_value(assistant, "greeting_message", greeting)
    context = SipGatewayService.call_context_text(call)
    base_prompt = getattr(assistant, "system_prompt", None) or ""
    set_committed_value(assistant, "system_prompt", f"{base_prompt}\n\n[Контекст звонка] {context}".strip())
    assistant.telephony_mode = True  # не колонка: клиенты провайдеров включают телефонный профиль VAD

    logger.info(
        f"[SIP-MEDIA] call {call_id} {direction}: did={call.did} caller={call.caller} to={call.to_number} "
        f"assistant={call.assistant_type}/{assistant.id} greeting={'yes' if greeting else 'no'}"
    )

    # 5. Запуск браузерного хендлера через адаптер
    socket = HandlerSocket(websocket, call.assistant_type, call_id)
    socket.start()
    started_at = datetime.utcnow()
    handler = handle_websocket_connection_new if call.assistant_type == "openai" else handle_gemini_websocket_connection
    try:
        await handler(socket, str(assistant.id), db)
    except Exception as exc:
        logger.error(f"[SIP-MEDIA] call {call_id}: handler crashed: {exc}", exc_info=True)
    finally:
        await socket.finish()
        # 6. Пост-обработка
        try:
            db.rollback()
            fresh = db.get(SipCall, call_uuid)
            if fresh is not None:
                tagged = SipGatewayService.tag_conversations(db, fresh, started_at)
                if fresh.status == SipCallStatus.ANSWERED and not socket.ended_by_bridge:
                    # Мост ещё пришлёт событие ended; если нет — закроем здесь
                    pass
                logger.info(
                    f"[SIP-MEDIA] call {call_id} finished: frames_in={socket.frames_in} deltas_out={socket.frames_out} "
                    f"audio_out={socket.audio_bytes_out / 16000:.1f}s barge_ins={socket.barge_ins} "
                    f"reason={socket.end_reason} tagged_conversations={tagged}"
                    + (f" handler_error={socket.handler_error}" if socket.handler_error else "")
                )
        except Exception as exc:
            logger.warning(f"[SIP-MEDIA] call {call_id}: post-processing failed: {exc}")


# =============================================================================
# HTTP API
# =============================================================================


class SipNumberCreate(BaseModel):
    phone_number: str = Field(..., description="Номер от оператора, любой формат")
    label: Optional[str] = None
    user_id: Optional[str] = Field(None, description="Владелец (только для админа; по умолчанию текущий пользователь)")
    gateway_id: Optional[str] = None
    assistant_type: Optional[str] = None
    assistant_id: Optional[str] = None
    first_phrase: Optional[str] = None
    allow_outbound: bool = True


class SipNumberUpdate(BaseModel):
    label: Optional[str] = None
    assistant_type: Optional[str] = None
    assistant_id: Optional[str] = None
    first_phrase: Optional[str] = None
    allow_outbound: Optional[bool] = None
    is_active: Optional[bool] = None


class SipCallCreate(BaseModel):
    to: str = Field(..., description="Кому звоним, любой формат")
    caller_id: Optional[str] = Field(None, description="С какого нашего номера; по умолчанию первый доступный")
    assistant_type: Optional[str] = Field(None, description="openai | gemini; по умолчанию как у номера")
    assistant_id: Optional[str] = None
    contact_name: Optional[str] = None
    custom_greeting: Optional[str] = None
    task_title: Optional[str] = None
    task_description: Optional[str] = None


def _validate_assistant(db: Session, current_user: User, assistant_type: Optional[str], assistant_id: Optional[str]) -> None:
    if assistant_type is None and assistant_id is None:
        return
    if assistant_type not in SIP_SUPPORTED_ASSISTANT_TYPES:
        raise HTTPException(status_code=400, detail=f"assistant_type must be one of {SIP_SUPPORTED_ASSISTANT_TYPES}")
    assistant = SipGatewayService.load_assistant(db, assistant_type, assistant_id)
    if assistant is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    if assistant.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Assistant belongs to another user")


@router.get("/api/sip/numbers")
async def list_numbers(
    all_users: bool = Query(False, alias="all"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    query = db.query(SipPhoneNumber)
    if not (all_users and current_user.is_admin):
        query = query.filter(SipPhoneNumber.user_id == current_user.id)
    rows = query.order_by(SipPhoneNumber.created_at.asc()).all()
    return {"numbers": [n.to_dict() for n in rows]}


@router.post("/api/sip/numbers", status_code=201)
async def create_number(
    body: SipNumberCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Только администратор добавляет номера оператора")
    digits = normalize_sip_number(body.phone_number)
    if len(digits) < 9:
        raise HTTPException(status_code=400, detail="Некорректный номер")
    if db.query(SipPhoneNumber).filter(SipPhoneNumber.phone_number == digits).first():
        raise HTTPException(status_code=409, detail="Номер уже добавлен")
    owner_id = current_user.id
    if body.user_id:
        try:
            owner_id = uuid.UUID(body.user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Некорректный user_id")
        if db.get(User, owner_id) is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
    _validate_assistant(db, current_user, body.assistant_type, body.assistant_id)
    number = SipPhoneNumber(
        user_id=owner_id,
        phone_number=digits,
        label=body.label,
        gateway_id=body.gateway_id or settings.SIP_GATEWAY_DEFAULT_ID,
        assistant_type=body.assistant_type,
        assistant_id=uuid.UUID(body.assistant_id) if body.assistant_id else None,
        first_phrase=body.first_phrase,
        allow_outbound=body.allow_outbound,
    )
    db.add(number)
    db.commit()
    db.refresh(number)
    return number.to_dict()


def _get_own_number(db: Session, current_user: User, number_id: str) -> SipPhoneNumber:
    try:
        nid = uuid.UUID(number_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный id")
    number = db.get(SipPhoneNumber, nid)
    if number is None or (number.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=404, detail="Номер не найден")
    return number


@router.patch("/api/sip/numbers/{number_id}")
async def update_number(
    number_id: str,
    body: SipNumberUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    number = _get_own_number(db, current_user, number_id)
    data = body.dict(exclude_unset=True)
    if "assistant_type" in data or "assistant_id" in data:
        a_type = data.get("assistant_type", number.assistant_type)
        a_id = data.get("assistant_id", str(number.assistant_id) if number.assistant_id else None)
        if a_type is None and a_id is None:
            number.assistant_type = None
            number.assistant_id = None
        else:
            _validate_assistant(db, current_user, a_type, a_id)
            number.assistant_type = a_type
            number.assistant_id = uuid.UUID(a_id)
        data.pop("assistant_type", None)
        data.pop("assistant_id", None)
    for key, value in data.items():
        setattr(number, key, value)
    db.commit()
    db.refresh(number)
    return number.to_dict()


@router.delete("/api/sip/numbers/{number_id}")
async def delete_number(
    number_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Только администратор удаляет номера")
    number = _get_own_number(db, current_user, number_id)
    db.delete(number)
    db.commit()
    return {"success": True}


@router.get("/api/sip/calls")
async def list_calls(
    limit: int = Query(50, ge=1, le=500),
    all_users: bool = Query(False, alias="all"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    query = db.query(SipCall)
    if not (all_users and current_user.is_admin):
        query = query.filter(SipCall.user_id == current_user.id)
    rows = query.order_by(SipCall.created_at.desc()).limit(limit).all()
    return {"calls": [c.to_dict() for c in rows]}


@router.get("/api/sip/calls/{call_id}")
async def get_call(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    try:
        call = db.get(SipCall, uuid.UUID(call_id))
    except ValueError:
        call = None
    if call is None or (call.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=404, detail="Звонок не найден")
    return call.to_dict()


@router.post("/api/sip/calls", status_code=201)
async def create_call(
    body: SipCallCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    """Ручной запуск исходящего звонка (тест или звонок из интерфейса)."""
    number = SipGatewayService.outbound_number_for_user(db, current_user.id, body.caller_id)
    if number is None:
        raise HTTPException(status_code=400, detail="Нет номера с разрешёнными исходящими")
    assistant_type = body.assistant_type or number.assistant_type
    assistant_id = body.assistant_id or (str(number.assistant_id) if number.assistant_id else None)
    if not assistant_type or not assistant_id:
        raise HTTPException(status_code=400, detail="Укажите ассистента или привяжите его к номеру")
    _validate_assistant(db, current_user, assistant_type, assistant_id)
    to_digits = normalize_sip_number(body.to)
    if len(to_digits) < 9:
        raise HTTPException(status_code=400, detail="Некорректный номер назначения")
    metadata = {
        k: v for k, v in {
            "contact_name": body.contact_name,
            "custom_greeting": body.custom_greeting,
            "task_title": body.task_title,
            "task_description": body.task_description,
            "source": "api",
        }.items() if v
    }
    call = SipGatewayService.queue_outbound_call(
        db,
        user_id=current_user.id,
        to_number=to_digits,
        caller_number=number,
        assistant_type=assistant_type,
        assistant_id=assistant_id,
        metadata=metadata,
    )
    return call.to_dict()


@router.post("/api/sip/calls/{call_id}/hangup")
async def hangup_call(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    """Положить трубку. Работает, если шлюз подключён к этому воркеру; иначе звонок завершит сам ассистент."""
    try:
        call = db.get(SipCall, uuid.UUID(call_id))
    except ValueError:
        call = None
    if call is None or (call.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=404, detail="Звонок не найден")
    state = GATEWAYS.get(call.gateway_id)
    if call.status == SipCallStatus.QUEUED:
        call.status = SipCallStatus.FAILED
        call.end_reason = "cancelled"
        call.ended_at = datetime.utcnow()
        db.commit()
        return {"success": True, "cancelled": True}
    if not state:
        raise HTTPException(status_code=409, detail="Шлюз не подключён к этому воркеру, повторите запрос")
    ws: WebSocket = state.get("websocket")  # type: ignore[assignment]
    if ws is None:
        raise HTTPException(status_code=409, detail="Управляющий сокет недоступен")
    await ws.send_text(json.dumps({"type": "hangup", "call_id": str(call.id), "reason": "user_request"}))
    return {"success": True}


@router.get("/api/sip/gateways")
async def list_gateways(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    now = datetime.utcnow()
    since = now - timedelta(hours=24)
    stats = {
        "queued": db.query(SipCall).filter(SipCall.status == SipCallStatus.QUEUED).count(),
        "active": db.query(SipCall).filter(SipCall.status.in_([SipCallStatus.DIALING, SipCallStatus.ANSWERED])).count(),
        "completed_24h": db.query(SipCall).filter(SipCall.status == SipCallStatus.COMPLETED, SipCall.created_at >= since).count(),
        "failed_24h": db.query(SipCall).filter(SipCall.status == SipCallStatus.FAILED, SipCall.created_at >= since).count(),
    }
    gateways = [
        {k: v for k, v in g.items() if k != "websocket"} | {"seconds_since_seen": round(time.time() - g["last_seen"])}
        for g in GATEWAYS.values()
    ]
    return {"worker_pid": os.getpid(), "gateways_on_this_worker": gateways, "token_configured": bool(settings.SIP_GATEWAY_TOKEN), "stats": stats}
