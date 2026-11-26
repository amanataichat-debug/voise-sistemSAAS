"""
ИИ-управляющий салона (Voksy Beauty, шаг 1.6б) — /api/manager, и push-уведомления — /api/push.
Только владелец (сотрудникам — 403: правил в team_service.STAFF_RULES нет). Логика —
services/salon_manager.py и services/push_service.py. Обработчики без await — def (идут в потоках).

  GET    /api/manager/overview          — настройки, что доступно (сценарии — автономный агент), последний
                                          отчёт, сколько ждёт одобрения, есть ли push на этом аккаунте
  PUT    /api/manager/settings          — {autonomy, winback{on,days,per_day}, fill_gaps{on,per_day},
                                          repeat_review{on}, morning_report{on,hour}, weekly_report{on}, max_per_day}
  GET    /api/manager/reports           — отчёты (последние 30)
  POST   /api/manager/reports/{id}/read — отметить прочитанным
  POST   /api/manager/reports/now       — составить утренний отчёт сейчас (без отправки в Telegram, для проверки)
  GET    /api/manager/actions?status=   — действия за 14 дней (proposed | done | … через запятую)
  POST   /api/manager/actions/decide    — {ids?: [...], approve: bool} — одобрить / отклонить (без ids — все)
  POST   /api/manager/plan-now          — найти кандидатов сейчас (не ждать 10:00), с учётом лимитов

  GET    /api/push/key                  — публичный ключ VAPID
  POST   /api/push/subscribe            — {subscription} из PushManager.subscribe
  POST   /api/push/unsubscribe          — {endpoint}
  POST   /api/push/test                 — прислать проверочное уведомление
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.dependencies import ensure_owner, get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.services import booking_service as bs
from backend.services import push_service
from backend.services import salon_manager as sm

router = APIRouter(prefix="/api/manager", tags=["Salon manager"])
push_router = APIRouter(prefix="/api/push", tags=["Push"])


def _agent(db: Session, user: User):
    ensure_owner(user, "ИИ-управляющий")
    agent = bs._first_agent(db, user.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Сначала создайте агента — ИИ-управляющий работает через него")
    return agent


@router.get("/overview")
def manager_overview(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    agent = _agent(db, current_user)
    reports = sm.list_reports(db, current_user.id, limit=1)
    return {
        "settings": sm.settings_of(agent),
        "scenarios_available": sm.can_run_scenarios(current_user),
        "booking_on": sm.booking_on(db, current_user),
        "telegram": bool(getattr(agent, "telegram_enabled", False)),
        "pending": sm._pending(db, current_user.id),
        "last_report": reports[0] if reports else None,
        "unread_reports": int(db.execute(text("SELECT count(*) FROM owner_reports WHERE user_id = :u AND read_at IS NULL"),
                                         {"u": str(current_user.id)}).scalar() or 0),
        "push_devices": push_service.count(db, current_user.id),
        "scenarios": sm.SCENARIOS,
        "work_hours": list(sm.WORK_HOURS),
    }


@router.put("/settings")
def manager_settings(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    agent = _agent(db, current_user)
    new = sm.clean_settings(sm.settings_of(agent), data)
    if not sm.can_run_scenarios(current_user):
        for k in ("winback", "fill_gaps", "repeat_review"):
            if new[k]["on"] and k != "repeat_review":
                raise HTTPException(status_code=402, detail="Клиентские сценарии ИИ-управляющего — на тарифе Beauty Pro")
    _log_settings(db, current_user, sm.settings_of(agent), new)
    agent.manager_settings = new
    db.commit()
    return {"ok": True, "settings": sm.settings_of(agent)}


def _log_settings(db: Session, user: User, old: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Журнал действий: что поменяли в ИИ-управляющем (автономность, сценарии, отчёты)."""
    from backend.services import activity
    parts = []
    if old.get("autonomy") != new.get("autonomy"):
        parts.append("решает: " + ("сам" if new.get("autonomy") == "auto" else "на одобрение владельца"))
    titles = dict(sm.SCENARIOS, repeat_review="Повтор услуги и отзыв", morning_report="утренний отчёт",
                  weekly_report="итоги недели")
    for k in ("winback", "fill_gaps", "repeat_review", "morning_report", "weekly_report"):
        a, b = (old.get(k) or {}), (new.get(k) or {})
        if bool(a.get("on")) != bool(b.get("on")):
            parts.append(f"{str(titles.get(k, k)).lower()}: {'включено' if b.get('on') else 'выключено'}")
        elif a != b:
            parts.append(f"{str(titles.get(k, k)).lower()}: настройки")
    if old.get("max_per_day") != new.get("max_per_day"):
        parts.append(f"в день не больше: {old.get('max_per_day')} → {new.get('max_per_day')}")
    if parts:
        activity.log(db, user.id, "manager.settings", "manager", "; ".join(parts), user=user)


@router.get("/reports")
def manager_reports(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    _agent(db, current_user)
    return {"reports": sm.list_reports(db, current_user.id)}


@router.post("/reports/{report_id}/read")
def manager_report_read(report_id: str, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    ensure_owner(current_user, "ИИ-управляющий")
    try:
        db.execute(text("UPDATE owner_reports SET read_at = now() WHERE id = CAST(:id AS uuid) AND user_id = :u "
                        "AND read_at IS NULL"), {"id": report_id, "u": str(current_user.id)})
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return {"ok": True}


@router.post("/reports/now")
async def manager_report_now(current_user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)) -> Dict[str, Any]:
    agent = _agent(db, current_user)
    day = sm.now_local().date()
    title, body, data = sm.build_morning(db, current_user, agent, day)
    return {"report": {"id": None, "kind": "morning", "day": day.isoformat(), "title": title, "text": body,
                       "data": data, "preview": True}}


@router.get("/actions")
def manager_actions(status: Optional[str] = Query(None), current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    _agent(db, current_user)
    statuses = [s for s in (status or "").split(",") if s in sm.STATUS_TITLES] or None
    return {"actions": sm.list_actions(db, current_user.id, statuses)}


@router.post("/actions/decide")
def manager_decide(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    _agent(db, current_user)
    ids = data.get("ids")
    if ids is not None and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids — список")
    n = sm.decide(db, current_user.id, ids, bool(data.get("approve")), user=current_user)
    return {"ok": True, "changed": n, "pending": sm._pending(db, current_user.id)}


@router.post("/plan-now")
def manager_plan_now(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    agent = _agent(db, current_user)
    if not sm.can_run_scenarios(current_user):
        raise HTTPException(status_code=402, detail="Клиентские сценарии ИИ-управляющего — на тарифе Beauty Pro")
    rows = sm.plan_actions(db, current_user, agent)
    return {"ok": True, "planned": len(rows), "autonomy": sm.settings_of(agent)["autonomy"],
            "actions": sm.list_actions(db, current_user.id, ["proposed", "approved"])}


# ───────────────────────────── push ─────────────────────────────

@push_router.get("/key")
def push_key(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    return {"key": push_service.public_key(db)}


@push_router.post("/subscribe")
def push_subscribe(request: Request, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    ensure_owner(current_user, "Уведомления владельца")
    if not push_service.subscribe(db, current_user.id, data.get("subscription") or data,
                                  request.headers.get("user-agent")):
        raise HTTPException(status_code=400, detail="Браузер прислал неполную подписку — попробуйте ещё раз")
    return {"ok": True, "devices": push_service.count(db, current_user.id)}


@push_router.post("/unsubscribe")
def push_unsubscribe(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    push_service.unsubscribe(db, current_user.id, str(data.get("endpoint") or ""))
    return {"ok": True, "devices": push_service.count(db, current_user.id)}


@push_router.post("/test")
def push_test(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    ensure_owner(current_user, "Уведомления владельца")
    n = push_service.send_to_user_sync(db, current_user.id, "Voksy AI", "Уведомления работают: утренний отчёт "
                                       "придёт сюда же.", "/static/manager.html#reports", tag="test")
    return {"ok": n > 0, "sent": n}
