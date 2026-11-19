"""
Сотрудники кабинета (ветка 1811-niche-admin-speed) — /api/team.

Владелец:
  GET    /api/team                          — сотрудники, цена места, сколько в месяц, разделы для галочек
  POST   /api/team                          — добавить (оплата места сразу) → пароль показывается один раз
  PATCH  /api/team/{id}                     — имя, телефон, должность, email, разделы
  POST   /api/team/{id}/password            — новый пароль (свой или сгенерировать), старые входы сбрасываются
  POST   /api/team/{id}/deactivate          — выключить (место не возвращается, срок оплаты сохраняется)
  POST   /api/team/{id}/activate            — включить (срок прошёл — оплатить новый месяц)
  DELETE /api/team/{id}                     — удалить
Сотрудник:
  GET    /api/team/me                       — кто я, мои разделы
  POST   /api/team/me/password              — сменить свой пароль

Оплата и продление мест, проверка прав сотрудника на каждом запросе — services/team_service.py.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.dependencies import (ensure_not_impersonating, ensure_owner, get_current_user,
                                       is_staff)
from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.models.user import User
from backend.services import team_service as ts

logger = get_logger(__name__)
router = APIRouter(prefix="/api/team", tags=["Team"])


class MemberCreate(BaseModel):
    name: str = Field(..., max_length=120)
    email: str = Field(..., max_length=255)
    password: Optional[str] = Field(None, max_length=128)
    phone: Optional[str] = Field(None, max_length=40)
    role_title: Optional[str] = Field(None, max_length=80)
    permissions: List[str] = Field(default_factory=list)


class MemberUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=40)
    role_title: Optional[str] = Field(None, max_length=80)
    permissions: Optional[List[str]] = None


class PasswordSet(BaseModel):
    password: Optional[str] = Field(None, max_length=128)


class OwnPassword(BaseModel):
    current_password: str = Field(..., max_length=128)
    new_password: str = Field(..., max_length=128)


def _fail(e: ts.TeamError):
    raise HTTPException(status_code=e.status, detail={"error": e.code, "message": e.message, **e.extra})


def _owner(user: User) -> User:
    ensure_owner(user, "Управление сотрудниками")
    return user


# ───────────────────────────── сотрудник ─────────────────────────────

@router.get("/me")
def team_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not is_staff(current_user):
        return {"staff": None}
    return {"staff": ts.staff_profile(db, current_user, current_user._staff_member_id)}


@router.post("/me/password")
def team_me_password(body: OwnPassword, current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if not is_staff(current_user):
        raise HTTPException(status_code=400, detail={"error": "not_staff", "message": "Свой пароль владелец меняет в Настройках"})
    try:
        m = ts.change_own_password(db, current_user._staff_member_id, body.current_password, body.new_password)
    except ts.TeamError as e:
        _fail(e)
    # Новый токен: старые входы сотрудника (другие телефоны) после смены пароля не действуют
    from backend.core.security import create_staff_token
    return {"success": True, "token": create_staff_token(current_user.id, m.id, m.token_version)}


# ───────────────────────────── владелец ─────────────────────────────

@router.get("")
def team_list(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(current_user)
    return ts.team_summary(db, current_user)


@router.post("")
def team_create(body: MemberCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(current_user)
    ensure_not_impersonating(current_user, "Оплата места сотрудника")
    try:
        m, pw = ts.create_member(db, current_user, name=body.name, email=body.email, password=body.password,
                                 phone=body.phone, role_title=body.role_title, permissions=body.permissions)
    except ts.TeamError as e:
        _fail(e)
    db.refresh(current_user)
    return {"success": True, "member": ts.member_dict(m), "password": pw, "team": ts.team_summary(db, current_user)}


@router.patch("/{member_id}")
def team_update(member_id: str, body: MemberUpdate, current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _owner(current_user)
    try:
        m = ts.update_member(db, current_user, member_id, name=body.name, phone=body.phone,
                             role_title=body.role_title, permissions=body.permissions, email=body.email)
    except ts.TeamError as e:
        _fail(e)
    return {"success": True, "member": ts.member_dict(m)}


@router.post("/{member_id}/password")
def team_password(member_id: str, body: PasswordSet, current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    _owner(current_user)
    try:
        m, pw = ts.set_password(db, current_user, member_id, body.password)
    except ts.TeamError as e:
        _fail(e)
    return {"success": True, "member": ts.member_dict(m), "password": pw}


@router.post("/{member_id}/deactivate")
def team_deactivate(member_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(current_user)
    try:
        m = ts.deactivate(db, current_user, member_id, "owner")
    except ts.TeamError as e:
        _fail(e)
    return {"success": True, "member": ts.member_dict(m), "team": ts.team_summary(db, current_user)}


@router.post("/{member_id}/activate")
def team_activate(member_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(current_user)
    ensure_not_impersonating(current_user, "Оплата места сотрудника")
    try:
        m = ts.activate(db, current_user, member_id)
    except ts.TeamError as e:
        _fail(e)
    db.refresh(current_user)
    return {"success": True, "member": ts.member_dict(m), "team": ts.team_summary(db, current_user)}


@router.delete("/{member_id}")
def team_delete(member_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(current_user)
    try:
        ts.delete_member(db, current_user, member_id)
    except ts.TeamError as e:
        _fail(e)
    return {"success": True, "team": ts.team_summary(db, current_user)}
