"""
Шлюз звонков WhatsApp (VPS wa-call-1, infra/wa-call-gateway/, ТЗ docs/tz_whatsapp_calls.md).

  * /ws/wa-call-gateway/control — одно постоянное соединение на шлюз, токен WA_CALL_GATEWAY_TOKEN
    (отдельный путь и токен, чтобы не смешивать с выбором SIP-шлюза для исходящих O!).
    Шлюз шлёт hello, account.state (qr / connected / logged_out / banned …), call.event
    (started / ringing / answered / ended / failed), pong. Бэкенд отвечает на входящий
    accept / reject и сам шлёт originate, hangup, login, logout, ping.
  * Звук звонка идёт по общему медиа-сокету /ws/sip/{call_id} (api/sip_gateway.py) — тем же
    голосовым агентам, что телефон и виджет; там же — приём записи звонка.

Логика (решение по входящему, лимиты исходящих, аккаунты) — services/wa_call_service.py.
ВАЖНО: роутер подключается в app.py ДО websocket.router (/ws/{assistant_id} перехватил бы путь).
"""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from backend.core.config import settings
from backend.core.dependencies import get_current_user
from backend.core.logging import get_logger
from backend.db.session import SessionLocal
from backend.models.user import User
from backend.services import wa_call_service

logger = get_logger(__name__)
router = APIRouter()

# Шлюзы звонков WhatsApp, подключённые к ЭТОМУ воркеру: gateway_id -> состояние
WA_GATEWAYS: Dict[str, Dict[str, Any]] = {}

POLL_INTERVAL = 1.0

_tables_ready = False


def _ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    try:
        from backend.models.base import Base, engine
        from backend.models.wa_call import AgentWaCallAccount
        Base.metadata.create_all(engine, tables=[AgentWaCallAccount.__table__], checkfirst=True)
        _tables_ready = True
    except Exception as exc:
        logger.error(f"[WA-CALL] ensure tables failed: {exc}")


def token_ok(token: Optional[str]) -> bool:
    expected = settings.WA_CALL_GATEWAY_TOKEN
    return bool(expected) and bool(token) and token == expected


def _with_db(fn, *args):
    """Синхронная работа с БД — в потоке: в этом воркере идут звонки (см. sip_gateway._claim_outbound_sync)."""
    db = SessionLocal()
    try:
        return fn(db, *args)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _claim_sync(db, gateway_id: str, max_calls: int, sweep: bool) -> List[Dict[str, Any]]:
    from backend.services.sip_gateway_service import SipGatewayService
    out: List[Dict[str, Any]] = list(wa_call_service.pending_account_commands(db, gateway_id))
    free = max_calls - SipGatewayService.active_outbound_count(db, gateway_id)
    for call in (SipGatewayService.claim_queued_calls(db, gateway_id, free) if free > 0 else []):
        out.append(wa_call_service.originate_payload(call))
    if sweep:
        from backend.api.sip_gateway import _sweep_stale_calls
        _sweep_stale_calls(db, gateway_id)
    return out


@router.websocket("/ws/wa-call-gateway/control")
async def wa_call_gateway_control(websocket: WebSocket, token: Optional[str] = Query(None),
                                  gateway: str = Query("wa-call-1")):
    if not token_ok(token):
        if not settings.WA_CALL_GATEWAY_TOKEN:
            logger.error("[WA-CALL] WA_CALL_GATEWAY_TOKEN is not configured on the backend")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    await asyncio.to_thread(_ensure_tables)
    gateway_id = gateway or settings.WA_CALL_GATEWAY_DEFAULT_ID
    state: Dict[str, Any] = {
        "gateway_id": gateway_id,
        "connected_at": time.time(),
        "last_seen": time.time(),
        "max_calls": 10,
        "version": None,
        "worker_pid": os.getpid(),
        "events": 0,
        "accounts": [],
        "websocket": websocket,
    }
    WA_GATEWAYS[gateway_id] = state
    logger.info(f"[WA-CALL] gateway '{gateway_id}' control socket connected (worker {os.getpid()})")
    send_lock = asyncio.Lock()

    async def send(msg: Dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_text(json.dumps(msg))

    poll = asyncio.create_task(_poll_loop(send, state))
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
                state["max_calls"] = int(data.get("max_calls") or 10)
                state["version"] = data.get("version")
                state["accounts"] = data.get("accounts") or []
                logger.info(f"[WA-CALL] gateway '{gateway_id}' hello: version={state['version']} "
                            f"max_calls={state['max_calls']} accounts={len(state['accounts'])} "
                            f"active_calls={len(data.get('active_calls') or [])}")
                await asyncio.to_thread(_with_db, wa_call_service.sync_gateway_accounts, gateway_id,
                                        state["accounts"])
            elif mtype == "call.event":
                state["events"] += 1
                if data.get("event") == "started" and data.get("direction") == "inbound":
                    # Шлюз ждёт решения несколько секунд: отвечаем сразу, ошибка = отказ, а не тишина
                    try:
                        reply = await asyncio.to_thread(_with_db, wa_call_service.decide_inbound, gateway_id, data)
                    except Exception as exc:
                        logger.error(f"[WA-CALL] inbound decision failed: {exc}", exc_info=True)
                        reply = {"type": "reject", "call_id": data.get("call_id"), "reason": "backend_error"}
                    await send(reply)
                else:
                    try:
                        await asyncio.to_thread(_with_db, wa_call_service.apply_call_event, gateway_id, data)
                    except Exception as exc:
                        logger.error(f"[WA-CALL] event {data.get('event')} for {data.get('call_id')} failed: {exc}",
                                     exc_info=True)
            elif mtype == "account.state":
                try:
                    await asyncio.to_thread(_with_db, wa_call_service.apply_account_state, gateway_id, data)
                except Exception as exc:
                    logger.error(f"[WA-CALL] account state failed: {exc}", exc_info=True)
            elif mtype in ("pong", "status"):
                pass
    except WebSocketDisconnect:
        logger.info(f"[WA-CALL] gateway '{gateway_id}' control socket disconnected")
    except Exception as exc:
        logger.error(f"[WA-CALL] gateway '{gateway_id}' control socket error: {exc}", exc_info=True)
    finally:
        poll.cancel()
        if WA_GATEWAYS.get(gateway_id) is state:
            WA_GATEWAYS.pop(gateway_id, None)


async def _poll_loop(send, state: Dict[str, Any]) -> None:
    """Раз в секунду: команды аккаунтам (login/logout) и исходящие из очереди; раз в 30 с — ping и уборка."""
    gateway_id = state["gateway_id"]
    last_ping = last_sweep = time.time()
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)
            sweep = time.time() - last_sweep > 30
            if sweep:
                last_sweep = time.time()
            commands = await asyncio.to_thread(_with_db, _claim_sync, gateway_id, int(state.get("max_calls") or 10),
                                               sweep)
            for cmd in commands:
                await send(cmd)
                logger.info(f"[WA-CALL] {cmd.get('type')} sent to '{gateway_id}' "
                            f"(account {cmd.get('account_id')}, call {cmd.get('call_id', '—')})")
            if time.time() - last_ping > 30:
                last_ping = time.time()
                await send({"type": "ping"})
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(f"[WA-CALL] poll loop for '{gateway_id}': {exc}")
            await asyncio.sleep(2)


@router.get("/api/wa-calls/gateways")
def wa_call_gateways(current_user: User = Depends(get_current_user)):
    """Шлюзы звонков WhatsApp, подключённые к этому воркеру (админ)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    now = time.time()
    return [{
        "gateway_id": gid, "version": st.get("version"), "max_calls": st.get("max_calls"),
        "connected_sec": int(now - st["connected_at"]), "last_seen_sec": int(now - st["last_seen"]),
        "events": st.get("events"), "accounts": st.get("accounts"), "worker_pid": st.get("worker_pid"),
    } for gid, st in WA_GATEWAYS.items()]
