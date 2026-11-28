"""
Voksy Beauty, шаг 1.7 — /api/beauty: журнал действий и главная салона. Обработчики без await — def (в потоках).

  GET  /api/beauty/activity        — журнал действий: ?period=today|week|month|all, actor=owner|ai|call|client|admin|
                                     system|staff:<id>, group=bookings|schedule|services|page|team|money|ai|admin,
                                     object_type=&object_id= (история одного объекта; у записи — вместе с её визитом),
                                     before=<ISO> (следующая страница), limit. Владелец и сотрудник с разделом
                                     «Журнал действий» (activity, в роли «Управляющий»); мастер — нет. IP — только владельцу.
  GET  /api/beauty/home?since=     — главная салона (services/salon_home.py), только владелец
  POST /api/beauty/home/offer-gaps — {resource_id, date?}: ИИ-управляющий предлагает окна мастера давним клиентам —
                                     на одобрение владельца (Beauty Pro / Profi)

Шаг 2.1 — клиенты салона (services/salon_clients.py):
  GET  /api/beauty/clients          — список с цифрами из журнала: ?segment=all|regular|new|vip|sleeping|lost|no_show|
                                     birthday|upcoming|never, q, master, service, source, gender, tag,
                                     sort=last_visit|spent|visits|next|name|created|birthday, limit, offset, counts=0|1.
                                     Владелец и сотрудник с разделом «Клиенты»
  GET  /api/beauty/clients/export.csv — те же фильтры, CSV для Excel; только владелец
  GET  /api/beauty/clients/{id}     — салонная часть карточки: цифры, любимые, ДР, источник, сегменты, история визитов

Шаг 2.2 — автоматизации и очередь отправок (services/automations.py, notify_queue.py). Владелец и сотрудник с
разделом «Мастера и график» (booking_setup):
  GET  /api/beauty/automations                 — правила, тексты RU/KY, параметры, статистика за 30 дней, лимиты
  PUT  /api/beauty/automations/limits          — {max_per_day, gap_days, lang}
  PUT  /api/beauty/automations/{key}           — {enabled, params, template_ru, template_ky} (пустой текст — по умолчанию)
  POST /api/beauty/automations/{key}/preview   — {template, lang} → как увидит клиент (пример)
  GET  /api/beauty/automations/queue           — журнал отправок: ?status=pending|sent|failed|skipped|problems, rule,
                                                 contact_id, before, limit
  POST /api/beauty/automations/queue/{id}/retry  — отправить ещё раз (не доставлено / пропущено / отменено)
  POST /api/beauty/automations/queue/{id}/cancel — снять из очереди

Шаг 2.3 — отзывы (services/reviews_service.py). Владелец и сотрудник с разделом «Мастера и график»:
  GET  /api/beauty/reviews                 — шапка (рейтинг, счётчики, мастера, просят ли оценку, показываются ли) +
                                             лента: ?status=pending|published|hidden, master, score (1–5, -1 = 1–3), offset
  POST /api/beauty/reviews/{id}/publish    — показать на странице салона (в т.ч. оценку 1–3 после проверки)
  POST /api/beauty/reviews/{id}/hide       — скрыть со страницы
  POST /api/beauty/reviews/{id}/reply      — {text, send, publish?}: ответ салона (виден под отзывом); send — ещё и
                                             клиенту в мессенджер через очередь; пусто — убрать ответ
  PUT  /api/beauty/reviews/settings        — {show_on_page}
Без входа — страница «Оцените визит» по ссылке из сообщения (/r/<токен>, вставка {оценить}):
  GET  /api/public/review/{token}          — салон, визит, уже оставленный отзыв
  POST /api/public/review/{token}          — {score, text, name, anonymous}: 4–5 — сразу на странице, 1–3 — владельцу
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.core.dependencies import ensure_owner, get_current_user, is_staff
from backend.db.session import get_db
from backend.models.booking import Booking
from backend.models.user import User
from backend.services import activity
from backend.services import booking_service as bs
from backend.services import automations, notify_queue, reviews_service, salon_clients, salon_home

router = APIRouter(prefix="/api/beauty", tags=["Voksy Beauty"])
review_public_router = APIRouter(prefix="/api/public/review", tags=["Voksy Beauty"])


@router.get("/activity")
def beauty_activity(period: Optional[str] = Query("week"), actor: Optional[str] = Query(None),
                    group: Optional[str] = Query(None), object_type: Optional[str] = Query(None),
                    object_id: Optional[str] = Query(None), before: Optional[str] = Query(None),
                    limit: int = Query(50, ge=1, le=200), current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    if is_staff(current_user):
        perms = (getattr(current_user, "_staff", None) or {}).get("permissions") or []
        if "activity" not in perms:      # middleware уже проверил; вторая проверка — на случай прямого вызова
            raise HTTPException(status_code=403, detail={"error": "staff_forbidden",
                                                         "message": "Журнал действий вам не открыт"})
    if period not in activity.PERIODS:
        period = "week"
    visit_id = None
    if object_type == "booking" and object_id:
        try:
            visit_id = db.query(Booking.visit_id).filter(Booking.id == uuid.UUID(object_id),
                                                         Booking.user_id == current_user.id).scalar()
        except ValueError:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        period = "all"
    res = activity.query(db, current_user.id, period=period, actor=actor, group=group, object_type=object_type,
                         object_id=object_id, visit_id=str(visit_id) if visit_id else None, before=before,
                         limit=limit, show_ip=not is_staff(current_user))
    if not before and not object_id:
        res["people"] = activity.people(db, current_user.id)
        res["groups"] = activity.groups()
        res["keep_days"] = activity.ACTIVITY_KEEP_DAYS
    return res


@router.get("/home")
def beauty_home(since: Optional[str] = Query(None), current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    ensure_owner(current_user, "Главная салона")
    s = None
    if since:
        try:
            s = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if s.tzinfo is None:
                s = s.replace(tzinfo=bs.BISHKEK)
        except ValueError:
            s = None
    out = salon_home.build(db, current_user, s)
    db.rollback()
    return out


@router.post("/home/offer-gaps")
def beauty_offer_gaps(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    ensure_owner(current_user, "ИИ-управляющий")
    if not data.get("resource_id"):
        raise HTTPException(status_code=400, detail="Не выбран мастер")
    try:
        return salon_home.offer_gaps(db, current_user, str(data["resource_id"]), data.get("date"))
    except bs.BookingError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


# ───────────────────────────── клиенты салона (шаг 2.1) ─────────────────────────────

def _client_filters(segment, q, master, service, source, gender, tag, sort) -> Dict[str, Any]:
    return {"segment": segment or "all", "q": q, "master": master, "service": service, "source": source,
            "gender": gender, "tag": tag, "sort": sort or ""}     # пусто — сервис выберет (у «ДР скоро» — по ДР)


@router.get("/clients")
def beauty_clients(segment: Optional[str] = Query("all"), q: Optional[str] = Query(None),
                   master: Optional[str] = Query(None), service: Optional[str] = Query(None),
                   source: Optional[str] = Query(None), gender: Optional[str] = Query(None),
                   tag: Optional[str] = Query(None), sort: Optional[str] = Query(None),
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), counts: int = Query(1),
                   current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    flt = _client_filters(segment, q, master, service, source, gender, tag, sort)
    out = salon_clients.list_clients(db, current_user, limit=limit, offset=offset, with_counts=bool(counts), **flt)
    out["can_export"] = not is_staff(current_user)
    db.rollback()
    return out


@router.get("/clients/export.csv")
def beauty_clients_export(segment: Optional[str] = Query("all"), q: Optional[str] = Query(None),
                          master: Optional[str] = Query(None), service: Optional[str] = Query(None),
                          source: Optional[str] = Query(None), gender: Optional[str] = Query(None),
                          tag: Optional[str] = Query(None), sort: Optional[str] = Query(None),
                          current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    ensure_owner(current_user, "Выгрузка клиентов")
    body = salon_clients.export_csv(db, current_user, **_client_filters(segment, q, master, service, source, gender,
                                                                        tag, sort))
    db.rollback()
    name = f"clients_{bs.to_local(bs.now_utc()).strftime('%Y-%m-%d')}.csv"
    return Response(content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/clients/{contact_id}")
def beauty_client_card(contact_id: str, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    card = salon_clients.client_card(db, current_user, contact_id)
    db.rollback()
    if card is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return card


# ───────────────────────────── автоматизации (шаг 2.2) ─────────────────────────────

def _automation_access(user) -> None:
    if is_staff(user):
        perms = (getattr(user, "_staff", None) or {}).get("permissions") or []
        if "booking_setup" not in perms:
            raise HTTPException(status_code=403, detail={"error": "staff_forbidden",
                                                         "message": "Автоматизации вам не открыты"})


@router.get("/automations")
def beauty_automations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    _automation_access(current_user)
    out = automations.overview(db, current_user)
    db.rollback()
    return out


@router.put("/automations/limits")
def beauty_automation_limits(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)) -> Dict[str, Any]:
    _automation_access(current_user)
    try:
        out = automations.save_limits(db, current_user, data or {})
    except automations.AutomationError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"limits": out}


@router.put("/automations/{key}")
def beauty_automation_save(key: str, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)) -> Dict[str, Any]:
    _automation_access(current_user)
    try:
        automations.save_rule(db, current_user, key, data or {})
    except automations.AutomationError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    rule = next((r for r in automations.overview(db, current_user)["rules"] if r["key"] == key), None)
    db.rollback()
    return {"rule": rule}


@router.post("/automations/{key}/preview")
def beauty_automation_preview(key: str, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)) -> Dict[str, Any]:
    _automation_access(current_user)
    try:
        txt = automations.preview(db, current_user, key, (data or {}).get("template"),
                                  "ky" if (data or {}).get("lang") == "ky" else "ru")
    except automations.AutomationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.rollback()
    return {"text": txt}


@router.get("/automations/queue")
def beauty_automation_queue(status: Optional[str] = Query(None), rule: Optional[str] = Query(None),
                            contact_id: Optional[str] = Query(None), before: Optional[str] = Query(None),
                            limit: int = Query(50, ge=1, le=200), current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    _automation_access(current_user)
    out = notify_queue.listing(db, current_user.id, status=status, rule=rule, contact_id=contact_id, before=before,
                               limit=limit)
    db.rollback()
    return out


@router.post("/automations/queue/{item_id}/retry")
def beauty_automation_retry(item_id: str, current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    _automation_access(current_user)
    try:
        uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    if not notify_queue.retry(db, current_user.id, item_id):
        raise HTTPException(status_code=400, detail="Это сообщение нельзя отправить ещё раз")
    return {"ok": True}


@router.post("/automations/queue/{item_id}/cancel")
def beauty_automation_cancel(item_id: str, current_user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)) -> Dict[str, Any]:
    _automation_access(current_user)
    try:
        uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    if not notify_queue.cancel(db, current_user.id, item_id):
        raise HTTPException(status_code=400, detail="Сообщение уже отправлено или снято")
    return {"ok": True}


# ───────────────────────────── отзывы (шаг 2.3) ─────────────────────────────

def _reviews_access(user) -> None:
    if is_staff(user):
        perms = (getattr(user, "_staff", None) or {}).get("permissions") or []
        if "booking_setup" not in perms:
            raise HTTPException(status_code=403, detail={"error": "staff_forbidden",
                                                         "message": "Отзывы вам не открыты"})


@router.get("/reviews")
def beauty_reviews(status: Optional[str] = Query(None), master: Optional[str] = Query(None),
                   score: Optional[int] = Query(None, ge=-1, le=5), offset: int = Query(0, ge=0),
                   limit: int = Query(30, ge=1, le=100), head: int = Query(1),
                   current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    _reviews_access(current_user)
    out = reviews_service.owner_list(db, current_user.id, status=status, master_id=master, score=score or None,
                                     offset=offset, limit=limit)
    if head:
        out["overview"] = reviews_service.overview(db, current_user)
    db.rollback()
    return out


@router.put("/reviews/settings")
def beauty_reviews_settings(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    _reviews_access(current_user)
    try:
        show = reviews_service.set_show_on_page(db, current_user, bool((data or {}).get("show_on_page")))
    except reviews_service.ReviewError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    from backend.services.booking_service import notify_changed
    notify_changed(db, current_user.id)
    db.commit()
    return {"show_on_page": show}


def _review_action(fn, *args):
    try:
        return fn(*args)
    except reviews_service.ReviewError as e:
        raise HTTPException(status_code=404 if "не найден" in str(e) else 400, detail=str(e))


@router.post("/reviews/{review_id}/publish")
def beauty_review_publish(review_id: str, current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)) -> Dict[str, Any]:
    _reviews_access(current_user)
    out = _review_action(reviews_service.moderate, db, current_user, review_id, "publish")
    db.commit()
    return out


@router.post("/reviews/{review_id}/hide")
def beauty_review_hide(review_id: str, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    _reviews_access(current_user)
    out = _review_action(reviews_service.moderate, db, current_user, review_id, "hide")
    db.commit()
    return out


@router.post("/reviews/{review_id}/reply")
def beauty_review_reply(review_id: str, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    _reviews_access(current_user)
    d = data or {}
    publish = d.get("publish")
    out = _review_action(reviews_service.reply, db, current_user, review_id, d.get("text"), bool(d.get("send")),
                         None if publish is None else bool(publish))
    db.commit()
    return out


_REVIEW_HITS: Dict[str, list] = {}


def _review_rate(ip: str, limit: int = 20, window: float = 3600.0) -> None:
    import time as _t
    now = _t.time()
    q = [x for x in _REVIEW_HITS.get(ip, []) if now - x < window]
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Слишком много попыток. Попробуйте позже.")
    q.append(now)
    _REVIEW_HITS[ip] = q


@review_public_router.get("/{token}")
def public_review_state(token: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        st = reviews_service.link_state(db, token)
    except reviews_service.ReviewError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    st.pop("_booking", None)
    db.rollback()
    return st


@review_public_router.post("/{token}")
def public_review_save(token: str, request: Request, data: Dict[str, Any] = Body(...),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else "?")
    _review_rate(ip)
    d = data or {}
    try:
        return reviews_service.save_from_link(db, token, d.get("score"), d.get("text"), d.get("name"),
                                              bool(d.get("anonymous")))
    except reviews_service.ReviewError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
