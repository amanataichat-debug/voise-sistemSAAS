"""
Админка «Клиенты» (ветка 1811-niche-admin-speed), префикс /api/admin.

  GET   /clients                    — список: поиск, сегменты, статус, ниша, тариф, сортировка
  GET   /clients/summary            — сводка: регистрации сегодня/неделя, кому звонить, тарифы, статусы
  GET   /clients/{id}               — карточка: профиль, что настроил, переписка/звонки, оплаты, лента
  PATCH /clients/{id}               — статус, заметка, дата следующего звонка, телефон/имя/компания/email/ниша
  POST  /clients/{id}/impersonate   — войти в кабинет клиента (токен клиента с отметкой админа, 2 ч)
  GET   /niches                     — справочник ниш (для фильтра и правки)

Обработчики — обычные def: FastAPI выполняет их в потоках, синхронные запросы к БД не
замораживают цикл событий (там идут звонки).
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.core.dependencies import check_admin_access
from backend.db.session import get_db
from backend.models.user import User
from backend.services import admin_clients as svc
from backend.services.niche_catalog import catalog_payload

logger = logging.getLogger(__name__)
router = APIRouter()


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/clients")
def admin_clients_list(
    q: Optional[str] = Query(None, max_length=200),
    segment: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    niche: Optional[str] = Query(None),
    plan: Optional[str] = Query(None),
    sort: str = Query("created_desc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if segment and segment not in svc.SEGMENTS:
        raise HTTPException(status_code=400, detail="Неизвестный сегмент")
    return svc.list_clients(db, q=q, segment=segment, status=status, niche=niche, plan=plan,
                            sort=sort, offset=offset, limit=limit)


@router.get("/clients/summary")
def admin_clients_summary(
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return svc.summary(db)


@router.get("/niches")
def admin_niches(current_user: User = Depends(check_admin_access)) -> Dict[str, Any]:
    return catalog_payload()


@router.get("/clients/{user_id}")
def admin_client_details(
    user_id: str,
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    data = svc.client_details(db, user_id)
    if not data:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return data


def _get_user(db: Session, user_id: str) -> User:
    import uuid as _uuid
    try:
        uid = _uuid.UUID(str(user_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return user


@router.patch("/clients/{user_id}")
def admin_client_update(
    user_id: str,
    data: Dict[str, Any] = Body(...),
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    user = _get_user(db, user_id)
    try:
        changed = svc.update_client(db, current_user, user, data or {})
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "changed": changed, "client": svc.client_details(db, user_id)}


@router.post("/clients/{user_id}/impersonate")
def admin_client_impersonate(
    user_id: str,
    request: Request,
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Вход в кабинет клиента без пароля. Токен клиента на 2 часа с отметкой «imp» = id админа:
    кабинет показывает полосу «Вы в кабинете …», а пароль, удаление аккаунта, API-ключ и оплата
    под таким токеном недоступны (ensure_not_impersonating). Каждый вход — строка в журнале.
    """
    from backend.core.security import IMPERSONATION_MINUTES, create_impersonation_token
    if getattr(current_user, "_impersonator_id", None):
        raise HTTPException(status_code=400, detail="Сначала вернитесь в свой аккаунт")
    user = _get_user(db, user_id)
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Это ваш собственный аккаунт")
    if user.is_admin:
        raise HTTPException(status_code=403, detail="В кабинет другого администратора входить нельзя")
    token = create_impersonation_token(user.id, current_user.id)
    svc.log_action(db, current_user, user.id, "impersonate", None,
                   {"minutes": IMPERSONATION_MINUTES,
                    "user_agent": (request.headers.get("user-agent") or "")[:200]},
                   ip=_client_ip(request))
    db.commit()
    logger.warning(f"[ADMIN] {current_user.email} вошёл в кабинет клиента {user.email} ({user.id})")
    name = " ".join(x for x in [user.first_name, user.last_name] if x) or None
    return {
        "token": token,
        "expires_in": IMPERSONATION_MINUTES * 60,
        "user": {"id": str(user.id), "email": user.email, "name": name, "company_name": user.company_name},
    }
