"""
Утилиты работы со временем для Voksy AI.

Подход (РФ-рынок, без DST, жёстко МСК):
- В БД и API всё хранится/передаётся в UTC (ISO-8601 с явным маркером).
- В UI и xlsx ввод/отображение в МСК (UTC+3).
- Рабочие часы агента (working_hours_start/end) трактуются как МСК.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

# МСК фиксированная (UTC+3, без перехода на летнее время)
MSK = timezone(timedelta(hours=3))


def msk_to_utc(dt: datetime) -> datetime:
    """МСК (naive или aware) → UTC aware."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt.astimezone(timezone.utc)


def utc_to_msk(dt: datetime) -> datetime:
    """UTC (naive считается UTC) → МСК aware."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK)


def now_msk() -> datetime:
    """Текущее время в МСК."""
    return datetime.now(MSK)


def now_utc() -> datetime:
    """Текущее время в UTC (для записи в БД)."""
    return datetime.now(timezone.utc)


def iso_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Сериализация datetime для API с явным UTC-маркером.

    - naive datetime → считается UTC, добавляется +00:00.
    - aware datetime → конвертируется в UTC.
    Возвращает ISO-8601 строку или None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def adjust_to_working_hours(
    dt_utc: datetime,
    wh_start: int,
    wh_end: int,
) -> Tuple[datetime, bool]:
    """
    Привести UTC-время звонка к рабочим часам агента (трактуются как МСК).

    Если час по МСК попадает в [wh_start, wh_end) — оставляем как есть.
    Иначе переносим:
      - раньше начала рабочего дня → этот же день на wh_start МСК;
      - после конца рабочего дня → следующий день на wh_start МСК.

    Возвращает (utc_dt_aware, shifted: bool).

    Агенты работают круглосуточно: ограничение по рабочим часам отключено,
    поэтому время звонка никогда не сдвигается.
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    # Круглосуточный режим — звонки не переносим.
    return dt_utc.astimezone(timezone.utc), False
