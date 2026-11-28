"""
Звонки WhatsApp голосового агента — кабинет владельца (Старт, Profi, Beauty AI, Beauty Pro).

  GET    /api/agent/wa-calls            — можно ли, номер, состояние, QR (SVG), настройки
  POST   /api/agent/wa-calls/connect    — подключить номер: шлюз пришлёт QR («Связанные устройства»)
  PATCH  /api/agent/wa-calls/settings   — агент отвечает на звонки (вкл/выкл), исходящие, лимит, первая фраза
  DELETE /api/agent/wa-calls            — отвязать номер от шлюза (настройки остаются)

Команды login/logout шлюзу уходят через поля аккаунта login_requested_at / logout_requested_at —
их забирает управляющий сокет любого воркера (api/wa_call_gateway.py → pending_account_commands).
Логика звонков — services/wa_call_service.py, ТЗ — docs/tz_whatsapp_calls.md.
Сотрудникам закрыто (нет правила в team_service.STAFF_RULES) — только владелец.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.dependencies import get_current_user
from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.models.agent_config import AgentConfig
from backend.models.user import User
from backend.models.wa_call import AgentWaCallAccount, WaCallStatus
from backend.services import wa_call_service as wcs

logger = get_logger(__name__)
router = APIRouter()


class SettingsRequest(BaseModel):
    inbound_enabled: Optional[bool] = None
    outbound_enabled: Optional[bool] = None
    daily_outbound_limit: Optional[int] = None
    first_phrase: Optional[str] = None


def _agent(db: Session, user: User, agent_id: Optional[str]) -> AgentConfig:
    q = db.query(AgentConfig).filter(AgentConfig.user_id == user.id)
    agent = None
    if agent_id:
        try:
            agent = q.filter(AgentConfig.id == agent_id).first()
        except Exception:
            db.rollback()
            agent = None
    else:
        agent = q.order_by(AgentConfig.created_at.asc()).first()
    if agent is None:
        raise HTTPException(status_code=404, detail="Сначала создайте агента")
    return agent


def _mask(phone: Optional[str]) -> str:
    d = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(d) < 7:
        return f"+{d}" if d else ""
    return f"+{d[:3]} {d[3:6]} ··· {d[-3:]}"


def _qr(acc: AgentWaCallAccount) -> dict:
    if acc.status != WaCallStatus.PENDING_QR or not acc.qr_code or not acc.qr_expires_at:
        return {"qr": None}
    exp = acc.qr_expires_at
    if exp.tzinfo is not None:  # колонка с часовым поясом, шлюз пишет naive UTC
        exp = exp.astimezone(timezone.utc).replace(tzinfo=None)
    left = (exp - datetime.utcnow()).total_seconds()
    if left <= 1:
        return {"qr": None}
    try:
        import base64
        import io
        import segno
        buf = io.BytesIO()
        segno.make(acc.qr_code, error="l").save(buf, kind="svg", scale=5, border=2, dark="#0e1729", light="#ffffff")
        return {"qr": "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode(),
                "qr_expires_in": int(left)}
    except Exception as exc:
        logger.warning(f"[WA-CALL] QR render failed: {exc}")
        return {"qr": None, "qr_error": "qr_render_failed"}


def _messenger_phone(db: Session, agent: AgentConfig) -> Optional[str]:
    try:
        from backend.models.agent_whatsapp import AgentWhatsAppAccount
        row = db.query(AgentWhatsAppAccount).filter(AgentWhatsAppAccount.agent_config_id == agent.id).first()
        return row.wa_phone if row is not None and row.status == "connected" else None
    except Exception:
        db.rollback()
        return None


def _state(db: Session, user: User, agent: AgentConfig) -> dict:
    available = bool(settings.WA_CALL_GATEWAY_TOKEN) and wcs.feature_enabled_for(user.id)
    plan_ok = wcs.plan_allows(user)
    acc = wcs.account_for_agent(db, agent.id)
    _, _, _, voice_problem = (wcs._resolve_voice(db, acc) if acc is not None
                              else wcs._resolve_voice(db, AgentWaCallAccount(agent_config_id=agent.id)))
    out = {
        "available": available,
        "plan_ok": plan_ok,
        "plans_text": wcs.plans_text(),
        "can_outbound": wcs.can_call_out(user),
        "agent": {"id": str(agent.id), "name": agent.name, "is_active": bool(agent.is_active),
                  "has_voice": voice_problem is None},
        "messenger_phone": _mask(_messenger_phone(db, agent)) or None,
        "account": None,
        "status": "not_connected",
        "default_daily_limit": settings.WA_CALLS_DAILY_OUTBOUND,
        "hourly_limit": settings.WA_CALLS_HOURLY_OUTBOUND,
    }
    if acc is not None:
        data = acc.to_dict()
        data.pop("user_id", None)
        data["phone_masked"] = _mask(acc.phone)
        data["waiting_gateway"] = acc.login_requested_at is not None
        out["account"] = data
        out["status"] = acc.status
        out.update(_qr(acc))
    return out


def _guard(user: User) -> None:
    if not (bool(settings.WA_CALL_GATEWAY_TOKEN) and wcs.feature_enabled_for(user.id)):
        raise HTTPException(status_code=403, detail="Звонки WhatsApp пока не включены для вашего кабинета — "
                                                    "напишите в поддержку")
    if not wcs.plan_allows(user):
        raise HTTPException(status_code=402, detail=f"Звонки WhatsApp — на тарифах {wcs.plans_text()}")


@router.get("")
def wa_calls_state(agent_id: Optional[str] = Query(None), current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    agent = _agent(db, current_user, agent_id)
    return _state(db, current_user, agent)


@router.post("/connect")
def wa_calls_connect(agent_id: Optional[str] = Query(None), current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Подключить номер или показать новый QR. Номер уже на связи — ничего не трогаем."""
    _guard(current_user)
    agent = _agent(db, current_user, agent_id)
    probe = AgentWaCallAccount(agent_config_id=agent.id)
    if wcs._resolve_voice(db, probe)[3] is not None:
        raise HTTPException(status_code=400, detail="У агента не настроен голос звонков — сначала выберите голос "
                                                    "в разделе «Звонки»")
    acc = wcs.account_for_agent(db, agent.id)
    now = datetime.utcnow()
    if acc is not None and acc.logout_requested_at is not None:
        raise HTTPException(status_code=409, detail="Номер ещё отвязывается — попробуйте через 10 секунд")
    if acc is None:
        acc = AgentWaCallAccount(
            user_id=current_user.id, agent_config_id=agent.id, gateway_id=settings.WA_CALL_GATEWAY_DEFAULT_ID,
            status=WaCallStatus.PENDING_QR, inbound_enabled=True, outbound_enabled=False,
            daily_outbound_limit=settings.WA_CALLS_DAILY_OUTBOUND,
        )
        db.add(acc)
    if acc.status != WaCallStatus.CONNECTED:
        acc.status = WaCallStatus.PENDING_QR
        acc.qr_code = None
        acc.qr_expires_at = None
        acc.last_error = None
        acc.logout_requested_at = None
        acc.login_requested_at = now
    db.commit()
    logger.info(f"[WA-CALL] owner {current_user.id} connects WhatsApp calls for agent {agent.id} (account {acc.id})")
    return _state(db, current_user, agent)


@router.patch("/settings")
def wa_calls_settings(body: SettingsRequest, agent_id: Optional[str] = Query(None),
                      current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    agent = _agent(db, current_user, agent_id)
    acc = wcs.account_for_agent(db, agent.id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Номер для звонков WhatsApp ещё не подключён")
    if body.inbound_enabled is not None:
        acc.inbound_enabled = bool(body.inbound_enabled)
    if body.outbound_enabled is not None:
        if body.outbound_enabled and not wcs.can_call_out(current_user):
            raise HTTPException(status_code=402, detail="Звонить клиентам самому через WhatsApp агент может на "
                                                        "тарифах Profi и Beauty Pro. На вашем тарифе — только "
                                                        "ответ на входящие")
        acc.outbound_enabled = bool(body.outbound_enabled)
    if body.daily_outbound_limit is not None:
        acc.daily_outbound_limit = max(1, min(50, int(body.daily_outbound_limit)))
    if body.first_phrase is not None:
        acc.first_phrase = body.first_phrase.strip()[:300] or None
    db.commit()
    return _state(db, current_user, agent)


@router.delete("")
def wa_calls_disconnect(agent_id: Optional[str] = Query(None), current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Отвязать номер: шлюз выходит из WhatsApp (устройство пропадёт из «Связанных устройств»)."""
    agent = _agent(db, current_user, agent_id)
    acc = wcs.account_for_agent(db, agent.id)
    if acc is None:
        return _state(db, current_user, agent)
    acc.logout_requested_at = datetime.utcnow()
    acc.login_requested_at = None
    acc.status = WaCallStatus.LOGGED_OUT
    acc.qr_code = None
    acc.qr_expires_at = None
    acc.outbound_enabled = False
    db.commit()
    logger.info(f"[WA-CALL] owner {current_user.id} disconnected WhatsApp calls account {acc.id}")
    return _state(db, current_user, agent)
