"""
Заказы (ветка 1811-niche-admin-speed) — /api/orders. Владелец и сотрудники с разделом «Заказы».

  GET    /api/orders?status=&q=&contact_id=&period=&limit=&offset=  — список, счётчики по статусам, названия статусов
  POST   /api/orders                     — оформить (позиции из каталога или свободной строкой)
  GET    /api/orders/products?q=         — позиции каталога для заказа (размеры/цвета в наличии)
  GET    /api/orders/summary?period=     — сводка: заказы, выручка, средний чек, топ (главная, голос)
  GET    /api/orders/settings, PUT       — сообщать клиенту о статусе, доставка, самовывоз, как оплатить
  GET    /api/orders/{id}                — заказ
  PATCH  /api/orders/{id}                — правка: клиент, доставка, оплата, позиции
  POST   /api/orders/{id}/status         — сменить статус {status, reason}; клиенту — сообщение (если включено)

Логика — services/order_service.py (одна на кабинет, бота и голос). Обработчики без await — def
(выполняются в потоках, не останавливают звук звонков).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.dependencies import ensure_owner, get_current_user, is_staff
from backend.db.session import get_db
from backend.models.user import User
from backend.services import order_service as osvc

router = APIRouter(prefix="/api/orders", tags=["Orders"])


def _err(e: Exception, status: int = 400):
    raise HTTPException(status_code=status, detail=str(e))


def _author(user: User) -> Optional[str]:
    return (getattr(user, "_staff", None) or {}).get("name") if is_staff(user) else None


def _period(period: Optional[str]):
    """today / 7d / 30d → (since, until) по Бишкеку; пусто — без ограничения."""
    if not period:
        return None, None
    today = datetime.now(timezone.utc).astimezone(osvc.BISHKEK).date()
    start = datetime.combine(today, datetime.min.time(), osvc.BISHKEK)
    days = {"today": 1, "yesterday": 1, "7d": 7, "30d": 30, "90d": 90}.get(period)
    if not days:
        return None, None
    if period == "yesterday":
        return (start - timedelta(days=1)).astimezone(timezone.utc), start.astimezone(timezone.utc)
    return (start - timedelta(days=days - 1)).astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


class ItemIn(BaseModel):
    entry_id: Optional[str] = None
    title: Optional[str] = Field(None, max_length=300)
    size: Optional[str] = Field(None, max_length=20)
    color: Optional[str] = Field(None, max_length=40)
    qty: Optional[int] = 1
    price: Optional[float] = None


class OrderIn(BaseModel):
    items: List[ItemIn]
    contact_id: Optional[str] = None
    client_name: Optional[str] = Field(None, max_length=200)
    client_phone: Optional[str] = Field(None, max_length=40)
    delivery_type: Optional[str] = "delivery"
    address: Optional[str] = Field(None, max_length=1000)
    delivery_when: Optional[str] = Field(None, max_length=120)
    payment_method: Optional[str] = None
    paid: Optional[bool] = False
    note: Optional[str] = Field(None, max_length=2000)
    delivery_fee: Optional[float] = None
    discount: Optional[float] = 0


class OrderPatch(BaseModel):
    items: Optional[List[ItemIn]] = None
    client_name: Optional[str] = Field(None, max_length=200)
    client_phone: Optional[str] = Field(None, max_length=40)
    delivery_type: Optional[str] = None
    address: Optional[str] = Field(None, max_length=1000)
    delivery_when: Optional[str] = Field(None, max_length=120)
    payment_method: Optional[str] = None
    paid: Optional[bool] = None
    note: Optional[str] = Field(None, max_length=2000)
    delivery_fee: Optional[float] = None
    discount: Optional[float] = None


class StatusIn(BaseModel):
    status: str
    reason: Optional[str] = Field(None, max_length=300)
    notify: Optional[bool] = True


class SettingsIn(BaseModel):
    notify_client: Optional[bool] = None
    delivery_fee: Optional[float] = None
    free_delivery_from: Optional[float] = None
    pickup_address: Optional[str] = Field(None, max_length=400)
    payment_note: Optional[str] = Field(None, max_length=400)


def _out(db: Session, user: User, o) -> Dict[str, Any]:
    d = osvc.order_to_dict(o, osvc.status_titles(user))
    d["next_status"] = osvc.next_status(o)
    return d


@router.get("")
def orders_list(status: Optional[str] = None, q: Optional[str] = Query(None, max_length=100),
                contact_id: Optional[str] = None, period: Optional[str] = None,
                limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    since, until = _period(period)
    rows, total = osvc.list_orders(db, current_user.id, status=status, q=q, contact_id=contact_id,
                                   since=since, until=until, limit=limit, offset=offset)
    return {"orders": [_out(db, current_user, o) for o in rows], "total": total,
            "counts": osvc.counts(db, current_user.id), "titles": osvc.status_titles(current_user),
            "enabled": osvc.orders_enabled(current_user), "food": osvc.is_food(current_user),
            "settings": osvc.get_settings(db, current_user.id)}


@router.post("")
def orders_create(body: OrderIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        o = osvc.create_order(db, current_user, items=[i.model_dump() for i in body.items], contact_id=body.contact_id,
                              client_name=body.client_name, client_phone=body.client_phone,
                              delivery_type=body.delivery_type or "delivery", address=body.address,
                              delivery_when=body.delivery_when, payment_method=body.payment_method, paid=bool(body.paid),
                              note=body.note, source="staff" if is_staff(current_user) else "owner",
                              created_by=_author(current_user), delivery_fee=body.delivery_fee, discount=body.discount or 0)
        db.commit()
    except osvc.OrderError as e:
        db.rollback()
        _err(e)
    db.refresh(o)
    return {"ok": True, "order": _out(db, current_user, o)}


@router.get("/products")
def orders_products(q: Optional[str] = Query(None, max_length=100), current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    return {"products": [osvc.product_to_dict(e) for e in osvc.products(db, current_user.id, q, limit=40)]}


@router.get("/summary")
def orders_summary(period: str = Query("today", max_length=16), current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    since, until = _period(period)
    if since is None:
        since, until = _period("today")
    return {"period": period, "stats": osvc.stats(db, current_user.id, since, until),
            "counts": osvc.counts(db, current_user.id)}


@router.get("/settings")
def orders_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return osvc.get_settings(db, current_user.id)


@router.put("/settings")
def orders_settings_save(body: SettingsIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_owner(current_user, "Настройки заказов")
    try:
        out = osvc.save_settings(db, current_user.id, body.model_dump(exclude_none=True))
        db.commit()
    except osvc.OrderError as e:
        db.rollback()
        _err(e)
    return out


@router.get("/{order_id}")
def orders_get(order_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return {"order": _out(db, current_user, osvc.get_order(db, current_user.id, order_id))}
    except osvc.OrderError as e:
        _err(e, 404)


@router.patch("/{order_id}")
def orders_patch(order_id: str, body: OrderPatch, current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    if data.get("items") is not None:
        data["items"] = [dict(i) for i in data["items"]]
    try:
        o = osvc.update_order(db, current_user, order_id, data)
        db.commit()
    except osvc.OrderError as e:
        db.rollback()
        _err(e, 404 if "не найден" in str(e) else 400)
    db.refresh(o)
    return {"ok": True, "order": _out(db, current_user, o)}


@router.post("/{order_id}/status")
def orders_status(order_id: str, body: StatusIn, current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    try:
        o, prev = osvc.set_status(db, current_user, order_id, body.status, body.reason)
        db.commit()
    except osvc.OrderError as e:
        db.rollback()
        _err(e, 404 if "не найден" in str(e) else 400)
    db.refresh(o)
    notify = bool(body.notify) and prev != o.status and o.status in osvc.NOTIFY_ON
    if notify:
        osvc.schedule(osvc.notify_client_status(current_user.id, o.id, o.status))
    return {"ok": True, "order": _out(db, current_user, o), "client_notified": notify,
            "counts": osvc.counts(db, current_user.id)}
