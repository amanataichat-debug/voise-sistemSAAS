"""
Agent Telegram Account API — подключение ЛИЧНОГО Telegram-аккаунта агента.

Префикс: /api/agent/telegram-account

НЕ путать с /api/agent/telegram (agent_telegram.py) — там Telegram-БОТ агента
(Bot API, фронтенд чата владельца). Здесь — личный аккаунт владельца через
MTProto (Telethon): агент пишет клиентам от имени владельца и отвечает на
входящие сообщения (поллер backend/core/telegram_user_poller.py).

Авторизация трёхшаговая (в отличие от OAuth-коннекторов Composio):
  POST /start           {phone}    → Telegram присылает код в приложение
  POST /verify-code     {code}     → либо connected, либо нужен пароль 2FA
  POST /verify-password {password} → connected

Промежуточное состояние (частичная сессия + phone_code_hash) живёт в строке
AgentTelegramAccount между шагами. Сессии шифруются (TELEGRAM_SESSION_KEY).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.core.dependencies import get_current_user
from backend.models.user import User
from backend.models.agent_config import AgentConfig
from backend.models.agent_telegram_account import (
    AgentTelegramAccount,
    AgentTelegramDialog,
)
from backend.services import telegram_user_service as tg_user

logger = get_logger(__name__)

router = APIRouter()

REPLY_SCOPES = {"contacts", "all"}


# ============================================================================
# SCHEMAS
# ============================================================================

class StartRequest(BaseModel):
    phone: str = Field(..., min_length=5, max_length=32)


class CodeRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=10)


class PasswordRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


class SettingsRequest(BaseModel):
    auto_reply_enabled: Optional[bool] = None
    reply_scope: Optional[str] = None


# ============================================================================
# HELPERS
# ============================================================================

def _get_agent(current_user: User, db: Session, agent_id: Optional[str] = None) -> AgentConfig:
    """Резолв агента пользователя (как в agent_telegram._get_agent)."""
    q = db.query(AgentConfig).filter(AgentConfig.user_id == current_user.id)
    if agent_id:
        agent = q.filter(AgentConfig.id == agent_id).first()
    else:
        agent = q.order_by(AgentConfig.created_at.asc()).first()
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return agent


def _get_row(db: Session, agent: AgentConfig) -> Optional[AgentTelegramAccount]:
    return db.query(AgentTelegramAccount).filter(
        AgentTelegramAccount.agent_config_id == agent.id
    ).first()


def _status_dict(row: Optional[AgentTelegramAccount]) -> dict:
    base = {"configured": tg_user.is_configured()}
    if row is None:
        base.update({
            "status": "not_connected",
            "phone_masked": None,
            "tg_username": None,
            "tg_first_name": None,
            "auto_reply_enabled": False,
            "reply_scope": "contacts",
            "last_error": None,
        })
        return base
    base.update(row.to_dict())
    return base


def _require_configured():
    if not tg_user.is_configured():
        raise HTTPException(status_code=400, detail="not_configured")


async def _baseline_dialogs(db: Session, row: AgentTelegramAccount, session_str: str) -> None:
    """
    Снимок диалогов при подключении: записываем текущие top_message_id, чтобы
    поллер не начал отвечать на старую переписку. Заодно связываем диалоги с
    контактами агента по видимому номеру телефона. Best-effort.
    """
    from backend.models.agent_contact import AgentContact
    from backend.services.sms_history import phone_suffix

    try:
        snap = await tg_user.poll_dialogs(session_str, known_last_ids={})
        if not snap.get("ok"):
            logger.warning(f"[TG-ACCOUNT] baseline poll failed: {snap.get('error')}")
            return
        for d in snap.get("dialogs", []):
            contact_id = None
            suf = phone_suffix(d.get("phone") or "")
            if suf:
                contact = db.query(AgentContact).filter(
                    AgentContact.agent_config_id == row.agent_config_id,
                    AgentContact.phone.like(f"%{suf}"),
                ).order_by(AgentContact.created_at.desc()).first()
                if contact:
                    contact_id = contact.id
            db.add(AgentTelegramDialog(
                account_id=row.id,
                agent_contact_id=contact_id,
                tg_peer_id=d["peer_id"],
                tg_username=d.get("username"),
                tg_name=d.get("name"),
                last_processed_msg_id=d.get("top_id") or 0,
                created_via="baseline",
            ))
        db.commit()
        logger.info(f"[TG-ACCOUNT] baseline: {len(snap.get('dialogs', []))} dialogs for account {row.id}")
    except Exception as e:
        logger.warning(f"[TG-ACCOUNT] baseline failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def _finalize_connected(db: Session, row: AgentTelegramAccount, result: dict) -> None:
    """Общая финализация после успешного sign_in (шаг 2 без 2FA или шаг 3)."""
    row.session_encrypted = tg_user.encrypt_session(result["session"])
    row.status = "connected"
    row.phone_code_hash = None
    row.tg_user_id = result.get("tg_user_id")
    row.tg_username = result.get("tg_username")
    row.tg_first_name = result.get("tg_first_name")
    row.last_error = None
    db.commit()


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("")
@router.get("/")
async def get_status(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Статус подключения личного Telegram у агента."""
    agent = _get_agent(current_user, db, agent_id)
    return _status_dict(_get_row(db, agent))


@router.post("/start")
async def start_auth(
    body: StartRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Шаг 1: отправить код подтверждения на номер (придёт в приложение Telegram)."""
    _require_configured()
    agent = _get_agent(current_user, db, agent_id)

    row = _get_row(db, agent)
    if row and row.status == "connected":
        raise HTTPException(status_code=400, detail="already_connected")

    phone = body.phone.strip()
    result = await tg_user.start_login(phone)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "telegram_error")

    if row is None:
        row = AgentTelegramAccount(agent_config_id=agent.id, user_id=current_user.id)
        db.add(row)

    row.phone = phone
    row.session_encrypted = tg_user.encrypt_session(result["session"])
    row.phone_code_hash = result["phone_code_hash"]
    row.status = "pending_code"
    row.last_error = None
    db.commit()

    logger.info(f"[TG-ACCOUNT] code sent for agent {agent.id} (•••{phone[-4:]})")
    return _status_dict(row)


@router.post("/verify-code")
async def verify_code(
    body: CodeRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Шаг 2: подтвердить код. Может потребоваться пароль 2FA (status=pending_password)."""
    _require_configured()
    agent = _get_agent(current_user, db, agent_id)

    row = _get_row(db, agent)
    if not row or row.status not in ("pending_code",) or not row.session_encrypted:
        raise HTTPException(status_code=400, detail="auth_not_started")

    session_str = tg_user.decrypt_session(row.session_encrypted)
    result = await tg_user.confirm_code(
        session_str, row.phone, body.code.strip(), row.phone_code_hash or ""
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "telegram_error")

    if result.get("needs_password"):
        row.session_encrypted = tg_user.encrypt_session(result["session"])
        row.status = "pending_password"
        db.commit()
        return _status_dict(row)

    _finalize_connected(db, row, result)
    await _baseline_dialogs(db, row, result["session"])
    logger.info(f"[TG-ACCOUNT] connected @{row.tg_username} for agent {agent.id}")
    return _status_dict(row)


@router.post("/verify-password")
async def verify_password(
    body: PasswordRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Шаг 3 (только для аккаунтов с 2FA): облачный пароль."""
    _require_configured()
    agent = _get_agent(current_user, db, agent_id)

    row = _get_row(db, agent)
    if not row or row.status != "pending_password" or not row.session_encrypted:
        raise HTTPException(status_code=400, detail="password_not_expected")

    session_str = tg_user.decrypt_session(row.session_encrypted)
    result = await tg_user.confirm_password(session_str, body.password)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "telegram_error")

    _finalize_connected(db, row, result)
    await _baseline_dialogs(db, row, result["session"])
    logger.info(f"[TG-ACCOUNT] connected @{row.tg_username} (2FA) for agent {agent.id}")
    return _status_dict(row)


@router.patch("/settings")
async def update_settings(
    body: SettingsRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Настройки: тумблер автоответа и охват (contacts / all)."""
    agent = _get_agent(current_user, db, agent_id)
    row = _get_row(db, agent)
    if not row:
        raise HTTPException(status_code=404, detail="not_connected")

    if body.reply_scope is not None:
        if body.reply_scope not in REPLY_SCOPES:
            raise HTTPException(status_code=400, detail="invalid_reply_scope")
        row.reply_scope = body.reply_scope
    if body.auto_reply_enabled is not None:
        if body.auto_reply_enabled and row.status != "connected":
            raise HTTPException(status_code=400, detail="not_connected")
        row.auto_reply_enabled = bool(body.auto_reply_enabled)

    db.commit()
    return _status_dict(row)


@router.delete("")
@router.delete("/")
async def disconnect(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Отключить личный Telegram: разлогинить сессию на стороне Telegram
    (best-effort) и удалить строку аккаунта (диалоги уйдут каскадом,
    переписка в agent_telegram_messages остаётся для истории).
    """
    agent = _get_agent(current_user, db, agent_id)
    row = _get_row(db, agent)
    if not row:
        return _status_dict(None)

    if row.session_encrypted and row.status == "connected":
        try:
            await tg_user.logout(tg_user.decrypt_session(row.session_encrypted))
        except Exception as e:
            logger.warning(f"[TG-ACCOUNT] logout failed (ignored): {e}")

    db.delete(row)
    db.commit()
    logger.info(f"[TG-ACCOUNT] disconnected for agent {agent.id}")
    return _status_dict(None)
