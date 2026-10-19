"""
Утилиты работы со временем для Voksy AI.

Подход:
- В БД и API всё хранится/передаётся в UTC (ISO-8601 с явным маркером).
- Пользователю (UI, xlsx, тексты от агента, отчёты) показываем Бишкек (UTC+6).
- Рабочие часы агента (working_hours_start/end) трактуются как бишкекские.

Старые имена MSK / now_msk / msk_to_utc / utc_to_msk сохранены как алиасы на
Бишкек — платформа была переведена с MSK на бишкекское время в октябре 2026
(ветка 1810-anri-sales-logic). Новый код пишется через now_bishkek() и
BISHKEK; старые имена постепенно вытесняются.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

# Бишкек фиксированный (UTC+6, без перехода на летнее время).
BISHKEK = timezone(timedelta(hours=6))

# Алиас для обратной совместимости (бывший Москва = UTC+3; теперь = Бишкек).
MSK = BISHKEK


def bishkek_to_utc(dt: datetime) -> datetime:
    """Бишкек (naive или aware) → UTC aware."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BISHKEK)
    return dt.astimezone(timezone.utc)


def utc_to_bishkek(dt: datetime) -> datetime:
    """UTC (naive считается UTC) → Бишкек aware."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BISHKEK)


def now_bishkek() -> datetime:
    """Текущее время в Бишкеке."""
    return datetime.now(BISHKEK)


def now_utc() -> datetime:
    """Текущее время в UTC (для записи в БД)."""
    return datetime.now(timezone.utc)


# --- Обратно-совместимые алиасы (имя оставлено, семантика — Бишкек) ---

def msk_to_utc(dt: datetime) -> datetime:
    """DEPRECATED: алиас на bishkek_to_utc. Использовать bishkek_to_utc()."""
    return bishkek_to_utc(dt)


def utc_to_msk(dt: datetime) -> datetime:
    """DEPRECATED: алиас на utc_to_bishkek. Использовать utc_to_bishkek()."""
    return utc_to_bishkek(dt)


def now_msk() -> datetime:
    """DEPRECATED: алиас на now_bishkek. Использовать now_bishkek()."""
    return now_bishkek()


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
    Привести UTC-время звонка к рабочим часам агента (трактуются как Бишкек).

    Если час по Бишкеку попадает в [wh_start, wh_end) — оставляем как есть.
    Иначе переносим:
      - раньше начала рабочего дня → этот же день на wh_start Бишкек;
      - после конца рабочего дня → следующий день на wh_start Бишкек.

    Возвращает (utc_dt_aware, shifted: bool).

    Агенты работают круглосуточно: ограничение по рабочим часам отключено,
    поэтому время звонка никогда не сдвигается.
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    # Круглосуточный режим — звонки не переносим.
    return dt_utc.astimezone(timezone.utc), False
