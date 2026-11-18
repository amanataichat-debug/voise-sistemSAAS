"""
Аналитика кабинета: /api/analytics (ветка 1811-niche-admin-speed).

  GET /report      — отчёт за период (страница «Аналитика», виджеты главной). Параметры:
                     period=today|yesterday|7d|30d|90d|180d|365d|mtd|custom, from, to (YYYY-MM-DD),
                     granularity=hour|day|week|month, sections=kpis,series,… (пусто — всё)
  GET /export.csv  — те же ряды таблицей для Excel (по столбцам графика + итог)

Обработчики синхронные (def): FastAPI выполняет их в пуле потоков, тяжёлые запросы к БД не
останавливают звук звонков в общем цикле событий (см. backend/core/loop_watchdog.py).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.services import analytics_service as an

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


def _sections(raw: Optional[str]):
    if not raw:
        return None
    out = [s.strip() for s in raw.split(",") if s.strip() in an.SECTIONS]
    return out or None


@router.get("/report")
def report(period: str = Query("30d", max_length=16),
           date_from: Optional[str] = Query(None, alias="from", max_length=10),
           date_to: Optional[str] = Query(None, alias="to", max_length=10),
           granularity: Optional[str] = Query(None, max_length=8),
           sections: Optional[str] = Query(None, max_length=200),
           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return an.cached_report(db, current_user, period=period, date_from=date_from, date_to=date_to,
                            granularity=granularity, sections=_sections(sections))


@router.get("/export.csv")
def export_csv(period: str = Query("30d", max_length=16),
               date_from: Optional[str] = Query(None, alias="from", max_length=10),
               date_to: Optional[str] = Query(None, alias="to", max_length=10),
               granularity: Optional[str] = Query(None, max_length=8),
               current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rep = an.cached_report(db, current_user, period=period, date_from=date_from, date_to=date_to,
                           granularity=granularity, sections=None)
    name = f"voksy-analytics-{rep['period']['from']}_{rep['period']['to']}.csv"
    return Response(content=an.report_csv(rep).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
