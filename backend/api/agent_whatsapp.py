"""
Agent WhatsApp API — подключение номера WhatsApp к агенту обзвона.

Неофициальный способ (Evolution API на VPS wa-gateway-1, как WhatsApp Web):
только переписка, звонков нет. Подключение по QR-коду.

router (префикс /api/agent/whatsapp, JWT владельца):
  GET    ""         статус подключения
  POST   /connect   создать instance на шлюзе (или новый QR после выхода) → статус + QR
  GET    /qr        текущий QR / подтверждение подключения (UI опрашивает раз в 3 с)
  PATCH  /settings  автоответ, охват (contacts/all), лимит новых чатов в сутки
  DELETE ""         разлогинить номер и удалить instance

webhook_router (префикс /api/whatsapp):
  POST /webhook/{account_id}  события Evolution; заголовок X-Voksy-Token =
                              account.webhook_secret. Обработка — в фоновом
                              loop'е (services/whatsapp_inbound.py).
"""

import hmac
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.core.logging import get_logger
from backend.db.session import SessionLocal, get_db
from backend.models.agent_config import AgentConfig
from backend.models.agent_whatsapp import AgentWhatsAppAccount
from backend.models.user import User
from backend.services import whatsapp_service as wa

logger = get_logger(__name__)

router = APIRouter()
webhook_router = APIRouter()

REPLY_SCOPES = {"contacts", "all"}


class SettingsRequest(BaseModel):
    auto_reply_enabled: Optional[bool] = None
    reply_scope: Optional[str] = None
    daily_new_chats_limit: Optional[int] = None


def _get_agent(current_user: User, db: Session, agent_id: Optional[str] = None) -> AgentConfig:
    q = db.query(AgentConfig).filter(AgentConfig.user_id == current_user.id)
    if agent_id:
        agent = q.filter(AgentConfig.id == agent_id).first()
    else:
        agent = q.order_by(AgentConfig.created_at.asc()).first()
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return agent


def _get_row(db: Session, agent: AgentConfig) -> Optional[AgentWhatsAppAccount]:
    return db.query(AgentWhatsAppAccount).filter(
        AgentWhatsAppAccount.agent_config_id == agent.id
    ).first()


def _status_dict(row: Optional[AgentWhatsAppAccount], qr: Optional[dict] = None) -> dict:
    base = {"configured": wa.is_configured()}
    if row is None:
        base.update({
            "status": "not_connected", "phone_masked": None, "wa_name": None,
            "auto_reply_enabled": True, "reply_scope": "contacts",
            "daily_new_chats_limit": 20, "last_error": None,
        })
    else:
        base.update(row.to_dict())
    if qr:
        base["qr"] = qr.get("qr")
        base["pairing_code"] = qr.get("pairing_code")
    return base


async def _mark_connected(db: Session, row: AgentWhatsAppAccount) -> None:
    owner = await wa.fetch_owner(row.instance_name)
    row.status = "connected"
    row.wa_jid = owner.get("jid") or row.wa_jid
    row.wa_phone = owner.get("phone") or row.wa_phone
    row.wa_name = owner.get("name") or row.wa_name
    row.last_error = None
    if row.connected_at is None:
        row.connected_at = datetime.utcnow()
    db.commit()


def _require_configured():
    if not wa.is_configured():
        raise HTTPException(status_code=400, detail="not_configured")


@router.get("")
@router.get("/")
async def get_status(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_agent(current_user, db, agent_id)
    return _status_dict(_get_row(db, agent))


@router.post("/connect")
async def connect(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создать подключение и получить QR (повторно — новый QR после выхода)."""
    _require_configured()
    agent = _get_agent(current_user, db, agent_id)
    row = _get_row(db, agent)
    if row and row.status == "connected":
        raise HTTPException(status_code=400, detail="already_connected")

    if row is None:
        row = AgentWhatsAppAccount(
            agent_config_id=agent.id,
            user_id=current_user.id,
            instance_name=wa.new_instance_name(agent.id),
            webhook_secret=wa.new_webhook_secret(),
            status="pending_qr",
        )
        db.add(row)
        db.commit()

    created = await wa.create_instance(row.instance_name, row.id, row.webhook_secret)
    if not created.get("ok"):
        raise HTTPException(status_code=502, detail=created.get("error") or "whatsapp_error")

    qr = await wa.get_qr(row.instance_name)
    if not qr.get("ok"):
        raise HTTPException(status_code=502, detail=qr.get("error") or "whatsapp_error")
    if qr.get("state") == "open":
        await _mark_connected(db, row)
        return _status_dict(row)

    row.status = "pending_qr"
    db.commit()
    logger.info(f"[WA-ACCOUNT] QR issued for agent {agent.id} ({row.instance_name})")
    return _status_dict(row, qr)


@router.get("/qr")
async def poll_qr(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Опрос из модалки: свежий QR или подтверждение, что номер подключён."""
    _require_configured()
    agent = _get_agent(current_user, db, agent_id)
    row = _get_row(db, agent)
    if row is None:
        return _status_dict(None)
    if row.status == "connected":
        return _status_dict(row)

    state = await wa.connection_state(row.instance_name)
    if state == "open":
        await _mark_connected(db, row)
        logger.info(f"[WA-ACCOUNT] connected {row.wa_phone} for agent {agent.id}")
        return _status_dict(row)

    qr = await wa.get_qr(row.instance_name)
    if not qr.get("ok"):
        raise HTTPException(status_code=502, detail=qr.get("error") or "whatsapp_error")
    if qr.get("state") == "open":
        await _mark_connected(db, row)
        return _status_dict(row)
    return _status_dict(row, qr)


@router.patch("/settings")
async def update_settings(
    body: SettingsRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_agent(current_user, db, agent_id)
    row = _get_row(db, agent)
    if not row:
        raise HTTPException(status_code=404, detail="not_connected")
    if body.reply_scope is not None:
        if body.reply_scope not in REPLY_SCOPES:
            raise HTTPException(status_code=400, detail="invalid_reply_scope")
        row.reply_scope = body.reply_scope
    if body.auto_reply_enabled is not None:
        row.auto_reply_enabled = bool(body.auto_reply_enabled)
    if body.daily_new_chats_limit is not None:
        if not 0 <= body.daily_new_chats_limit <= 100:
            raise HTTPException(status_code=400, detail="invalid_limit")
        row.daily_new_chats_limit = body.daily_new_chats_limit
    db.commit()
    return _status_dict(row)


@router.delete("")
@router.delete("/")
async def disconnect(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отключить номер: выход на шлюзе + удаление instance; переписка остаётся."""
    agent = _get_agent(current_user, db, agent_id)
    row = _get_row(db, agent)
    if not row:
        return _status_dict(None)
    try:
        await wa.logout_and_delete(row.instance_name)
    except Exception as e:
        logger.warning(f"[WA-ACCOUNT] logout failed (ignored): {e}")
    db.delete(row)
    db.commit()
    logger.info(f"[WA-ACCOUNT] disconnected for agent {agent.id}")
    return _status_dict(None)


# ============================================================================
# WEBHOOK (Evolution API → бэкенд)
# ============================================================================

def _check_secret_sync(account_id: str, token: str) -> bool:
    db = SessionLocal()
    try:
        row = db.query(AgentWhatsAppAccount.webhook_secret).filter(
            AgentWhatsAppAccount.id == account_id
        ).first()
        return bool(row and token and hmac.compare_digest(row[0], token))
    except Exception:
        return False
    finally:
        db.close()


@webhook_router.post("/webhook/{account_id}")
async def whatsapp_webhook(account_id: str, request: Request):
    import asyncio
    from backend.core import background_loop
    from backend.services.whatsapp_inbound import handle_webhook

    token = request.headers.get(wa.WEBHOOK_TOKEN_HEADER) or request.query_params.get("token") or ""
    if not await asyncio.to_thread(_check_secret_sync, account_id, token):
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    if isinstance(payload, dict):
        background_loop.submit(handle_webhook(account_id, payload))
    return {"ok": True}
