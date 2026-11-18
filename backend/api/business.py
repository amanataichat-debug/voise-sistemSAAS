"""
Профиль бизнеса клиента (ветка 1811-niche-admin-speed), префикс /api/business.

  GET  /niches   — ниши по группам, у каждой 2–3 вопроса с вариантами и значениями по умолчанию
  GET  /profile  — итоговая конфигурация кабинета под бизнес (модули, каталог, вкладки телефона)
  POST /setup    — экран «Чем занимается ваш бизнес?» / «Настройки → Мой бизнес»:
                   {niche, niche_other?, answers?, phone?, company_name?} → сохранить и настроить ботов
  PUT  /modules  — включить/выключить запись, CRM-воронку, заказы вручную

Логика — services/business_profile.py, пресеты — services/niche_presets.py.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.services import business_profile as bp
from backend.services import niche_presets

logger = logging.getLogger(__name__)
router = APIRouter()


class SetupRequest(BaseModel):
    niche: str = Field(..., max_length=40)
    niche_other: Optional[str] = Field(None, max_length=120)
    answers: Optional[Dict[str, Any]] = None
    phone: Optional[str] = Field(None, max_length=40)
    company_name: Optional[str] = Field(None, max_length=100)


@router.get("/niches")
def business_niches(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return niche_presets.catalog_payload()


@router.get("/profile")
def business_profile(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return bp.resolve(current_user)


@router.post("/setup")
async def business_setup(body: SetupRequest, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        result = bp.setup(db, current_user, body.niche, body.niche_other, body.answers, body.phone,
                          body.company_name, force=False)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    # Владельцу платформы — сообщение о регистрации сразу, уже с нишей (если ещё не уходило)
    if not getattr(current_user, "_impersonator_id", None):
        try:
            from backend.core.spawn import spawn
            from backend.services.signup_notify import notify_signup_new_session
            spawn(notify_signup_new_session(current_user.id))
        except Exception as e:
            logger.warning(f"[BUSINESS] signup notify not scheduled: {e}")
    return result


@router.put("/modules")
def business_modules(modules: Dict[str, Any] = Body(..., embed=True), current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    return bp.set_modules(db, current_user, modules)
