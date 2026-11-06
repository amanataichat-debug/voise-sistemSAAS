"""
Dashboard API — сводка для главной страницы кабинета (/static/dashboard.html).

Префикс: /api/dashboard
  GET /summary — одна выдача на всю панель: кошелёк, звонки (по дням за 14 дней), диалоги,
                 ассистенты, номера, CRM и переписка бота (Старт / пробный период / Profi),
                 последние звонки и чек-лист «что настроить».

Каждый блок считается отдельно и при ошибке отдаёт пустые значения — панель не падает
целиком из-за одного запроса. Сутки считаются по времени Бишкека (UTC+6).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.models.user import User

logger = get_logger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

BISHKEK = timezone(timedelta(hours=6))
DAYS = 14


def _bishkek_midnight_utc(days_ago: int = 0) -> datetime:
    """Полночь по Бишкеку N дней назад, в UTC (aware)."""
    now = datetime.now(BISHKEK)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return start.astimezone(timezone.utc)


def _naive(dt: datetime) -> datetime:
    """Для колонок DateTime без зоны (в БД лежит UTC)."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _day_key(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BISHKEK).strftime("%Y-%m-%d")


def _empty_days() -> List[str]:
    today = datetime.now(BISHKEK).date()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS - 1, -1, -1)]


def _iso(dt) -> str:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------- блоки

def _wallet(db: Session, user: User) -> Dict[str, Any]:
    from backend.models.wallet_transaction import WalletTransaction
    from backend.services import wallet_service as ws

    balance = int(user.wallet_balance or 0)
    rate = ws.som_per_minute("eleven") or 0
    since = _bishkek_midnight_utc(29)
    spent = db.query(func.coalesce(func.sum(WalletTransaction.amount), 0)).filter(
        WalletTransaction.user_id == user.id,
        WalletTransaction.type == "charge",
        WalletTransaction.created_at >= since,
    ).scalar() or 0
    return {
        "balance": ws.to_som(balance),
        "som_per_minute": rate,
        "minutes_left": int((balance / 100) // rate) if rate and balance > 0 else 0,
        "spent_30d": ws.to_som(-int(spent)),
        "free": ws.is_free_user(user),
    }


def _plan(user: User) -> Dict[str, Any]:
    plan = user.subscription_plan_rel
    end = user.subscription_end_date
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    left = None
    if end is not None:
        left = max(0, (end - datetime.now(timezone.utc)).total_seconds())
    trial = bool(plan and plan.code in ("free", "referral_trial"))
    return {
        "code": plan.code if plan else None,
        "name": ("Пробный период" if trial else plan.name) if plan else None,
        "is_trial": trial,
        "end_date": _iso(end),
        "days_left": int(left // 86400) if left is not None else None,
        "hours_left": int(left // 3600) if left is not None else None,
        "max_assistants": plan.max_assistants if plan else None,
        "admin": bool(user.is_admin),
    }


def _assistants(db: Session, user: User) -> Dict[str, Any]:
    from backend.models.eleven_assistant import ElevenAssistantConfig

    rows = (db.query(ElevenAssistantConfig)
            .filter(ElevenAssistantConfig.user_id == user.id)
            .order_by(ElevenAssistantConfig.created_at.desc())
            .all())
    titles = {"eleven": "Voksy Voice", "gpt_live": "Voksy Live", "gemini_live": "Voksy Live Go"}
    return {
        "total": len(rows),
        "active": sum(1 for r in rows if r.is_active),
        "items": [{
            "id": str(r.id),
            "name": r.name,
            "engine": titles.get(r.voice_engine or "eleven", "Voksy Voice"),
            "is_active": bool(r.is_active),
        } for r in rows[:5]],
    }


def _numbers(db: Session, user: User) -> List[Dict[str, Any]]:
    from backend.models.sip_gateway import SipPhoneNumber

    rows = db.query(SipPhoneNumber).filter(SipPhoneNumber.user_id == user.id).all()
    return [{
        "phone": r.phone_number,
        "label": r.label,
        "bound": bool(r.assistant_id or r.agent_config_id),
        "is_active": bool(r.is_active),
    } for r in rows]


def _calls(db: Session, user: User) -> Dict[str, Any]:
    from backend.models.sip_gateway import SipCall

    since = _bishkek_midnight_utc(29)
    rows = (db.query(SipCall.direction, SipCall.created_at, SipCall.answered_at, SipCall.duration_sec)
            .filter(SipCall.user_id == user.id, SipCall.created_at >= since)
            .all())
    today_key = datetime.now(BISHKEK).strftime("%Y-%m-%d")
    week_from = _bishkek_midnight_utc(6)
    days = {d: {"date": d, "inbound": 0, "outbound": 0} for d in _empty_days()}
    out = {"today": 0, "week": 0, "month": 0, "answered_month": 0, "inbound_month": 0,
           "outbound_month": 0, "minutes_month": 0, "avg_sec": 0}
    talk = 0.0
    for direction, created, answered, dur in rows:
        key = _day_key(created)
        out["month"] += 1
        c = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        if c >= week_from:
            out["week"] += 1
        if key == today_key:
            out["today"] += 1
        d = "inbound" if direction == "inbound" else "outbound"
        out[d + "_month"] += 1
        if answered is not None:
            out["answered_month"] += 1
            talk += float(dur or 0)
        if key in days:
            days[key][d] += 1
    out["minutes_month"] = int(round(talk / 60))
    out["avg_sec"] = int(talk / out["answered_month"]) if out["answered_month"] else 0
    out["by_day"] = list(days.values())
    return out


def _recent_calls(db: Session, user: User) -> List[Dict[str, Any]]:
    from backend.models.sip_gateway import SipCall

    rows = (db.query(SipCall)
            .filter(SipCall.user_id == user.id)
            .order_by(SipCall.created_at.desc())
            .limit(6).all())
    return [{
        "at": _iso(r.created_at),
        "direction": r.direction,
        "number": (r.caller if r.direction == "inbound" else r.to_number) or "",
        "answered": r.answered_at is not None,
        "duration_sec": int(r.duration_sec or 0),
        "status": r.status,
        "has_record": bool(r.recording_url),
    } for r in rows]


def _dialogs(db: Session, user: User) -> Dict[str, Any]:
    from backend.models.eleven_assistant import ElevenAssistantConfig, ElevenConversation

    ids = [r[0] for r in db.query(ElevenAssistantConfig.id).filter(ElevenAssistantConfig.user_id == user.id).all()]
    if not ids:
        return {"today": 0, "week": 0}
    base = db.query(func.count(func.distinct(ElevenConversation.session_id))).filter(
        ElevenConversation.assistant_id.in_(ids))
    return {
        "today": base.filter(ElevenConversation.created_at >= _naive(_bishkek_midnight_utc(0))).scalar() or 0,
        "week": base.filter(ElevenConversation.created_at >= _naive(_bishkek_midnight_utc(6))).scalar() or 0,
    }


def _crm(db: Session, user: User) -> Dict[str, Any]:
    from backend.core.pipeline_stages import get_agent_stages
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact
    from backend.models.task import Task, TaskStatus

    agent = (db.query(AgentConfig).filter(AgentConfig.user_id == user.id)
             .order_by(AgentConfig.created_at.asc()).first())
    if agent is None:
        return {"has_bot": False}

    q = db.query(AgentContact).filter(AgentContact.agent_config_id == agent.id)
    total = q.count()
    new_week = q.filter(AgentContact.created_at >= _naive(_bishkek_midnight_utc(6))).count()
    counts = dict(db.query(AgentContact.status, func.count(AgentContact.id))
                  .filter(AgentContact.agent_config_id == agent.id)
                  .group_by(AgentContact.status).all())
    stages = [{"key": s["key"], "label": s["label"], "color": s.get("color"), "kind": s.get("kind"),
               "count": int(counts.get(s["key"], 0))} for s in get_agent_stages(agent)]
    pipeline = db.query(func.coalesce(func.sum(AgentContact.deal_amount), 0)).filter(
        AgentContact.agent_config_id == agent.id,
        AgentContact.status.in_([s["key"] for s in stages if s.get("kind") == "open"]),
    ).scalar() or 0
    won = sum(s["count"] for s in stages if s.get("kind") == "won")
    paused = q.filter(AgentContact.ai_paused_until.isnot(None),
                      AgentContact.ai_paused_until > datetime.utcnow()).count()
    tasks = (db.query(Task).filter(Task.user_id == user.id, Task.status == TaskStatus.SCHEDULED,
                                   Task.scheduled_time >= datetime.now(timezone.utc))
             .order_by(Task.scheduled_time.asc()).limit(4).all())

    channels = {}
    try:
        from backend.services import inbox_service
        st = inbox_service.channel_status(db, agent)
        channels = {k: bool(v.get("connected")) for k, v in st.items()}
    except Exception as e:  # каналы — не повод ронять панель
        logger.warning(f"[DASHBOARD] channel status failed: {e}")

    return {
        "has_bot": True,
        "bot_name": agent.name,
        "bot_active": bool(agent.is_active),
        "contacts_total": total,
        "contacts_new_week": new_week,
        "won": won,
        "paused": paused,
        "pipeline_amount": float(pipeline or 0),
        "stages": stages,
        "channels": channels,
        "tasks": [{
            "title": t.title,
            "channel": t.channel or "call",
            "at": _iso(t.scheduled_time),
        } for t in tasks],
        "credits": int(user.credits_balance or 0),
    }


def _messages(db: Session, user: User) -> Dict[str, Any]:
    from backend.models.agent_contact import AgentContact
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.agent_telegram_account import AgentTelegramMessage
    from backend.models.agent_whatsapp import AgentWhatsAppMessage

    since = _naive(_bishkek_midnight_utc(DAYS - 1))
    days = {d: {"date": d, "inbound": 0, "outbound": 0} for d in _empty_days()}
    by_channel = {}
    week_from = _naive(_bishkek_midnight_utc(6))
    totals = {"week_in": 0, "week_out": 0}
    for name, model in (("whatsapp", AgentWhatsAppMessage), ("telegram", AgentTelegramMessage),
                        ("instagram", AgentInstagramMessage)):
        rows = (db.query(model.direction, model.created_at)
                .join(AgentContact, AgentContact.id == model.agent_contact_id)
                .filter(AgentContact.user_id == user.id, model.created_at >= since)
                .all())
        n = 0
        for direction, created in rows:
            d = "inbound" if direction == "inbound" else "outbound"
            key = _day_key(created)
            if key in days:
                days[key][d] += 1
            if created >= week_from:
                n += 1
                totals["week_in" if d == "inbound" else "week_out"] += 1
        by_channel[name] = n
    return {**totals, "by_channel": by_channel, "by_day": list(days.values())}


def _safe(db: Session, name: str, fn, default):
    try:
        return fn()
    except Exception as e:
        logger.warning(f"[DASHBOARD] block {name} failed: {e}", exc_info=True)
        try:
            db.rollback()  # PostgreSQL: после ошибки транзакция сломана, следующие блоки упали бы тоже
        except Exception:
            pass
        return default


@router.get("/summary")
async def summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user = current_user
    mode = user.agent_mode()
    data: Dict[str, Any] = {
        "agent_mode": mode,
        "plan": _safe(db, "plan", lambda: _plan(user), {}),
        "wallet": _safe(db, "wallet", lambda: _wallet(db, user), {}),
        "assistants": _safe(db, "assistants", lambda: _assistants(db, user), {"total": 0, "active": 0, "items": []}),
        "numbers": _safe(db, "numbers", lambda: _numbers(db, user), []),
        "calls": _safe(db, "calls", lambda: _calls(db, user), {}),
        "recent_calls": _safe(db, "recent_calls", lambda: _recent_calls(db, user), []),
        "dialogs": _safe(db, "dialogs", lambda: _dialogs(db, user), {"today": 0, "week": 0}),
    }
    if mode is not None:
        data["crm"] = _safe(db, "crm", lambda: _crm(db, user), {"has_bot": False})
        data["messages"] = _safe(db, "messages", lambda: _messages(db, user), {})
    return data
