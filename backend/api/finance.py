"""
Доходы, расходы, прибыль (ветка 1811-niche-admin-speed, 19.11.2026) — /api/finance. Владелец и
сотрудник с разделом «Финансы» (бухгалтер, управляющий — Voksy Beauty, шаг 1.2): правила в
team_service.STAFF_RULES, здесь — вторая проверка (_owner).

  GET    /api/finance/summary?period=&from=&to=  — выручка, расходы по категориям, зарплаты мастеров, прибыль, график
  GET    /api/finance/expenses                    — все расходы (ежемесячные и разовые)
  POST   /api/finance/expenses                    — добавить {category, title, amount, repeat, spent_on, ends_on, note}
  PATCH  /api/finance/expenses/{id}               — изменить
  DELETE /api/finance/expenses/{id}               — удалить
  PATCH  /api/finance/payroll/{resource_id}       — оплата мастера {pay_percent, pay_fixed}

Логика — services/finance_service.py. Обработчики без await — def (идут в потоках, не тормозят звонки).
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.dependencies import ensure_owner, get_current_user, is_staff
from backend.db.session import get_db
from backend.models.user import User
from backend.services import finance_service as fs

router = APIRouter(prefix="/api/finance", tags=["Finance"])


def _owner(user: User) -> User:
    """Владелец или сотрудник с разделом «Финансы»; остальным сотрудникам — 403."""
    if is_staff(user):
        perms = set((getattr(user, "_staff", None) or {}).get("permissions") or [])
        if "finance" not in perms:
            ensure_owner(user, "Финансы")
    return user


@router.get("/summary")
def finance_summary(period: Optional[str] = Query("30d"), date_from: Optional[str] = Query(None, alias="from"),
                    date_to: Optional[str] = Query(None, alias="to"),
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(current_user)
    return fs.summary(db, current_user, period, date_from, date_to)


@router.get("/expenses")
def finance_expenses(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(current_user)
    return {"expenses": [fs.expense_to_dict(e) for e in fs.list_expenses(db, current_user.id)],
            "categories": [{"key": k, "title": t, "icon": fs.CATEGORY_ICONS.get(k, "circle")}
                           for k, t in fs.EXPENSE_CATEGORIES.items()]}


@router.post("/expenses")
def finance_add(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _owner(current_user)
    try:
        return {"expense": fs.expense_to_dict(fs.create_expense(db, current_user, data))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/expenses/{expense_id}")
def finance_edit(expense_id: str, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    _owner(current_user)
    try:
        return {"expense": fs.expense_to_dict(fs.update_expense(db, current_user, expense_id, data))}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/expenses/{expense_id}")
def finance_delete(expense_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(current_user)
    try:
        fs.delete_expense(db, current_user, expense_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@router.patch("/payroll/{resource_id}")
def finance_pay(resource_id: str, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _owner(current_user)
    try:
        return {"master": fs.set_pay(db, current_user, resource_id, data.get("pay_percent"), data.get("pay_fixed"))}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
