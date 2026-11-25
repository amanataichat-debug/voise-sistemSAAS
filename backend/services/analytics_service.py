"""
Аналитика кабинета (ветка 1811-niche-admin-speed) — ОДИН серверный отчёт на все места:
страница «Аналитика» (Старт, Profi, базовый тариф), интерактивные виджеты главной и голосовой
помощник. Раньше страница Старта считала цифры в браузере по последним 200 клиентам и 300 событиям
(на живом аккаунте воронка и суммы врали), а главная — отдельным запросом за 14 дней.

Период — даты Бишкека (UTC+6): today, yesterday, 7d, 30d, 90d (квартал), 180d (полгода),
365d (год), mtd (этот месяц), custom (from/to). Шаг графика: hour (один день), day, week (с
понедельника), month — допустимые зависят от длины периода (`granularities`). Каждый период
сравнивается с предыдущим такой же длины (дельты KPI, серая линия «прошлый период»).

Что считается (каждый блок отдельно и при ошибке отдаёт пустое — отчёт не падает целиком):
  messages   — сообщения WhatsApp / Telegram / Instagram: от клиентов, ответы бота, ваши ответы
  calls      — звонки по телефонии (sip_calls): входящие, исходящие, с разговором, пропущенные, минуты
  outcomes   — итог звонков агента по разбору (успех / перезвон / не ответил / отказ)
  clients    — новые клиенты: откуда пришли (первое касание), что с ними стало (когорта)
  replies    — время ответа (медиана по каждому каналу), без ответа, кто ответил первым — бот или вы
  heatmap    — когда пишут и звонят: день недели × час
  funnel     — воронка сейчас, суммы сделок, причины отказа, теги
  bookings   — онлайн-запись (визиты, неявки, выручка, услуги, загрузка мастеров, источники)
  catalog    — склад каталога (сколько позиций, нет в наличии, мало, без фото, без цены)
  money      — потрачено из кошелька на звонки и кредитов агента
  insights   — короткие выводы словами («больше всего пишут во вторник с 10 до 12»)

Кэш — 60 с на (пользователь, период, шаг): повторное открытие страницы и виджеты главной не
пересчитывают одно и то же.
"""

from __future__ import annotations

import logging
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

BISHKEK_OFFSET = timedelta(hours=6)
BISHKEK = timezone(BISHKEK_OFFSET)

PERIODS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "7d": "7 дней",
    "30d": "30 дней",
    "90d": "3 месяца",
    "180d": "Полгода",
    "365d": "Год",
    "mtd": "Этот месяц",
    "custom": "Свой период",
}
_LENGTHS = {"7d": 7, "30d": 30, "90d": 90, "180d": 182, "365d": 365}
MAX_DAYS = 731

CHANNELS = ("whatsapp", "telegram", "instagram")
CHANNEL_TITLES = {"whatsapp": "WhatsApp", "telegram": "Telegram", "instagram": "Instagram",
                  "call": "Звонки", "booking": "Онлайн-запись", "manual": "Добавлены вручную"}
MESSAGE_DIRECTIONS = ("sms_inbound", "telegram_inbound", "telegram_outbound", "instagram_inbound",
                      "whatsapp_inbound", "followup_outbound")
OUTCOME_TITLES = {"SUCCESS": "Успех", "FOLLOWUP": "Нужен перезвон", "NO_ANSWER": "Не ответил",
                  "REJECTED": "Отказ"}

MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
              "октябрь", "ноябрь", "декабрь"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEKDAYS_ACC = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу",
                "в воскресенье"]

CACHE_TTL = 60.0
_CACHE: Dict[tuple, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()


# ───────────────────────────── период и шаг ─────────────────────────────

@dataclass
class Range:
    key: str
    a: date            # первый день (Бишкек)
    b: date            # последний день включительно
    since: datetime    # naive UTC
    until: datetime    # naive UTC, не включая
    prev_a: date
    prev_b: date
    prev_since: datetime
    prev_until: datetime
    label: str
    prev_label: str

    @property
    def days(self) -> int:
        return (self.b - self.a).days + 1

    def aware(self, prev: bool = False) -> Tuple[datetime, datetime]:
        s, u = (self.prev_since, self.prev_until) if prev else (self.since, self.until)
        return s.replace(tzinfo=timezone.utc), u.replace(tzinfo=timezone.utc)


def today_bk(now: Optional[datetime] = None) -> date:
    now = now or datetime.utcnow()
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    return (now + BISHKEK_OFFSET).date()


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day) - BISHKEK_OFFSET


def _fmt_d(d: date, year: bool = False) -> str:
    s = f"{d.day} {MONTHS_SHORT[d.month - 1]}"
    return s + f" {d.year}" if year else s


def _range_label(a: date, b: date, today: date) -> str:
    if a == b:
        if a == today:
            return f"сегодня, {_fmt_d(a)}"
        if a == today - timedelta(days=1):
            return f"вчера, {_fmt_d(a)}"
        return _fmt_d(a, a.year != today.year)
    year = a.year != b.year or b.year != today.year
    if a.year == b.year and a.month == b.month and not year:
        return f"{a.day}–{b.day} {MONTHS_SHORT[a.month - 1]}"
    return f"{_fmt_d(a, year)} – {_fmt_d(b, year)}"


def resolve_period(period: Optional[str], date_from: Optional[str] = None, date_to: Optional[str] = None,
                   now: Optional[datetime] = None) -> Range:
    """Период в датах Бишкека + предыдущий такой же длины (для сравнения)."""
    today = today_bk(now)
    key = (period or "30d").strip().lower()
    aliases = {"week": "7d", "month": "30d", "quarter": "90d", "half": "180d", "half_year": "180d",
               "year": "365d", "this_month": "mtd", "day": "today"}
    key = aliases.get(key, key)
    if key not in PERIODS:
        key = "30d"
    if key == "today":
        a = b = today
    elif key == "yesterday":
        a = b = today - timedelta(days=1)
    elif key == "mtd":
        a, b = today.replace(day=1), today
    elif key == "custom":
        try:
            a = datetime.strptime((date_from or "").strip()[:10], "%Y-%m-%d").date()
            b = datetime.strptime((date_to or date_from or "").strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            a, b = today - timedelta(days=29), today
            key = "30d"
        if a > b:
            a, b = b, a
        b = min(b, today)
        a = min(a, b)
        a = max(a, b - timedelta(days=MAX_DAYS - 1))
    else:
        b = today
        a = today - timedelta(days=_LENGTHS[key] - 1)

    length = (b - a).days + 1
    if key == "mtd":
        # «этот месяц» сравниваем с теми же днями прошлого месяца (1–18 ноября ↔ 1–18 октября)
        pb_month_last = a - timedelta(days=1)
        prev_a = pb_month_last.replace(day=1)
        prev_b = min(prev_a + timedelta(days=length - 1), pb_month_last)
    else:
        prev_b = a - timedelta(days=1)
        prev_a = prev_b - timedelta(days=length - 1)

    if length == 1:
        prev_label = "чем накануне"
    elif key == "mtd":
        prev_label = "чем в прошлом месяце"
    elif length == 7:
        prev_label = "чем неделей раньше"
    elif 28 <= length <= 31:
        prev_label = "чем месяцем раньше"
    elif length >= 360:
        prev_label = "чем годом раньше"
    elif 175 <= length <= 186:
        prev_label = "чем полгода назад"
    elif 88 <= length <= 92:
        prev_label = "чем кварталом раньше"
    else:
        prev_label = f"чем {length} дней до этого"
    return Range(key=key, a=a, b=b, since=_utc(a), until=_utc(b) + timedelta(days=1),
                 prev_a=prev_a, prev_b=prev_b, prev_since=_utc(prev_a), prev_until=_utc(prev_b) + timedelta(days=1),
                 label=_range_label(a, b, today), prev_label=prev_label)


def granularities(days: int) -> List[str]:
    if days <= 1:
        return ["hour"]
    if days < 14:
        return ["day"]
    if days <= 62:
        return ["day", "week"]
    if days <= 186:
        return ["day", "week", "month"]
    return ["week", "month"]


def default_granularity(days: int) -> str:
    if days <= 1:
        return "hour"
    if days <= 62:
        return "day"
    if days <= 186:
        return "week"
    return "month"


def pick_granularity(days: int, wanted: Optional[str]) -> str:
    allowed = granularities(days)
    return wanted if wanted in allowed else default_granularity(days)


@dataclass
class Bucket:
    start: date
    end: date          # включительно
    hour: Optional[int] = None

    def to_dict(self, gran: str, today: date, now_hour: int) -> Dict[str, Any]:
        if gran == "hour":
            label = f"{self.hour:02d}:00"
            full = f"{_fmt_d(self.start)}, {self.hour:02d}:00–{self.hour:02d}:59"
            future = self.start > today or (self.start == today and self.hour > now_hour)
        elif gran == "day":
            label = f"{self.start.day} {MONTHS_SHORT[self.start.month - 1]}"
            full = f"{WEEKDAYS_SHORT[self.start.weekday()]}, {_fmt_d(self.start, self.start.year != today.year)}"
            future = self.start > today
        elif gran == "week":
            if self.start.month == self.end.month:
                label = f"{self.start.day}–{self.end.day} {MONTHS_SHORT[self.start.month - 1]}"
            else:
                label = f"{_fmt_d(self.start)} – {_fmt_d(self.end)}"
            full = f"неделя {label}"
            future = self.start > today
        else:
            label = MONTHS_SHORT[self.start.month - 1] + (f" {self.start.year % 100:02d}" if self.start.year != today.year else "")
            full = f"{MONTHS_NOM[self.start.month - 1]} {self.start.year}"
            month_last = (self.start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            if self.start.day != 1 or self.end != month_last:
                full += f" ({self.start.day}–{self.end.day})"
            future = self.start > today
        return {"label": label, "title": full, "from": self.start.isoformat(), "to": self.end.isoformat(),
                "hour": self.hour, "future": bool(future)}


def make_buckets(a: date, b: date, gran: str) -> List[Bucket]:
    out: List[Bucket] = []
    if gran == "hour":
        return [Bucket(a, a, h) for h in range(24)]
    if gran == "day":
        d = a
        while d <= b:
            out.append(Bucket(d, d))
            d += timedelta(days=1)
        return out
    if gran == "week":
        d = a
        while d <= b:
            end = min(b, d + timedelta(days=6 - d.weekday()))
            out.append(Bucket(d, end))
            d = end + timedelta(days=1)
        return out
    d = a
    while d <= b:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = min(b, nxt - timedelta(days=1))
        out.append(Bucket(d, end))
        d = end + timedelta(days=1)
    return out


class BucketIndex:
    """Дата (+ час) Бишкека → номер столбца графика."""

    def __init__(self, buckets: List[Bucket], gran: str):
        self.gran = gran
        self.n = len(buckets)
        self._by_day: Dict[date, int] = {}
        if gran != "hour":
            for i, bk in enumerate(buckets):
                d = bk.start
                while d <= bk.end:
                    self._by_day[d] = i
                    d += timedelta(days=1)
        self._day = buckets[0].start if buckets else None

    def index(self, d: Any, hour: Any = None) -> Optional[int]:
        if d is None:
            return None
        if isinstance(d, datetime):
            d = d.date()
        if self.gran == "hour":
            if d != self._day or hour is None:
                return None
            h = int(hour)
            return h if 0 <= h < 24 else None
        return self._by_day.get(d)


# ───────────────────────────── SQL-помощники ─────────────────────────────

def _bk_naive(col: str) -> str:
    """Колонка DateTime без зоны (в БД лежит UTC) → время Бишкека."""
    return f"({col} + interval '6 hours')"


def _bk_tz(col: str) -> str:
    """Колонка timestamptz → время Бишкека."""
    return f"(({col} AT TIME ZONE 'UTC') + interval '6 hours')"


_TABLE_CACHE: Dict[str, bool] = {}


def _has_table(db: Session, name: str) -> bool:
    if name in _TABLE_CACHE:
        return _TABLE_CACHE[name]
    try:
        ok = db.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}).scalar() is not None
    except Exception:
        db.rollback()
        ok = False
    if ok:   # отсутствие не кэшируем: таблицу создаст старт приложения
        _TABLE_CACHE[name] = True
    return ok


_MSG_TABLES = {"whatsapp": "agent_whatsapp_messages", "telegram": "agent_telegram_messages",
               "instagram": "agent_instagram_messages"}


AD_MARK = "[Клиент пришёл из рекламы"


def _messages_union(since_param: str = "s", until_param: str = "u", with_ad: bool = False) -> str:
    """Сообщения всех мессенджеров владельца за [:s, :u) — время отбирается внутри каждой части.
    with_ad — колонка ad: входящее WhatsApp с карточкой объявления Click-to-WhatsApp (whatsapp_service)."""
    parts = []
    for ch, table in _MSG_TABLES.items():
        ad = ""
        if with_ad:
            ad = (f", (x.direction = 'inbound' AND left(x.body, {len(AD_MARK)}) = '{AD_MARK}') AS ad") if ch == "whatsapp" \
                else ", false AS ad"
        parts.append(
            f"SELECT x.agent_contact_id AS cid, '{ch}' AS ch, x.direction AS direction, x.sent_by AS sent_by, "
            f"x.created_at AS created_at{ad} FROM {table} x JOIN agent_contacts c0 ON c0.id = x.agent_contact_id "
            f"WHERE c0.user_id = :uid AND x.created_at >= :{since_param} AND x.created_at < :{until_param}"
        )
    return " UNION ALL ".join(parts)


def _safe(db: Session, name: str, fn, default):
    try:
        return fn()
    except Exception as e:
        logger.warning(f"[ANALYTICS] block {name} failed: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return default


def _zeros(n: int) -> List[int]:
    return [0] * n


def _pct(part: float, whole: float) -> Optional[int]:
    if not whole:
        return None
    return int(round(part * 100.0 / whole))


def _delta(cur: float, prev: float) -> Dict[str, Any]:
    cur, prev = float(cur or 0), float(prev or 0)
    diff = cur - prev
    pct = None if prev == 0 else round(diff * 100.0 / prev)
    return {"diff": round(diff, 1) if isinstance(diff, float) and diff % 1 else int(diff), "pct": pct,
            "dir": "same" if diff == 0 else ("up" if diff > 0 else "down")}


# ───────────────────────────── блоки ─────────────────────────────

def _first_agent(db: Session, user_id):
    from backend.models.agent_config import AgentConfig
    return (db.query(AgentConfig).filter(AgentConfig.user_id == user_id)
            .order_by(AgentConfig.created_at.asc()).first())


def _empty_grid() -> List[List[int]]:
    return [[0] * 24 for _ in range(7)]


def _messages_block(db: Session, uid: str, rng: Range, idx: BucketIndex, pidx: BucketIndex) -> Dict[str, Any]:
    n, pn = idx.n, pidx.n
    series = {f"{ch}_in": _zeros(n) for ch in CHANNELS}
    series.update({"bot_out": _zeros(n), "owner_out": _zeros(n)})
    prev_in = _zeros(pn)
    totals = {ch: {"in": 0, "bot": 0, "owner": 0, "prev_in": 0} for ch in CHANNELS}
    heat = {ch: _empty_grid() for ch in CHANNELS}
    bk = _bk_naive("m.created_at")
    # один проход за прошлый + текущий период: столбцы графика, итоги, тепловая карта (день × час)
    sql = (f"WITH m AS ({_messages_union()}) "
           f"SELECT date({bk}) AS d, CAST(extract(hour FROM {bk}) AS int) AS h, m.ch, m.direction, "
           f"(COALESCE(m.sent_by, '') = 'owner') AS owner, count(*) AS n FROM m GROUP BY 1, 2, 3, 4, 5")
    rows = db.execute(text(sql), {"uid": uid, "s": rng.prev_since, "u": rng.until}).fetchall()
    for d, h, ch, direction, owner, cnt in rows:
        cnt = int(cnt or 0)
        cur = rng.a <= d <= rng.b
        if direction == "inbound":
            if cur:
                totals[ch]["in"] += cnt
                if h is not None:
                    heat[ch][d.weekday()][int(h)] += cnt
                i = idx.index(d, h)
                if i is not None:
                    series[f"{ch}_in"][i] += cnt
            else:
                totals[ch]["prev_in"] += cnt
                i = pidx.index(d, h)
                if i is not None:
                    prev_in[i] += cnt
        elif cur:
            key = "owner_out" if owner else "bot_out"
            totals[ch]["owner" if owner else "bot"] += cnt
            i = idx.index(d, h)
            if i is not None:
                series[key][i] += cnt

    # второй проход (только текущий период): уникальные написавшие по каналам, «отвечал только бот», реклама
    sql_c = (
        f"WITH m AS ({_messages_union(with_ad=True)}), "
        "per AS (SELECT m.cid, m.ch, count(*) FILTER (WHERE m.direction = 'inbound') AS n_in, "
        "  count(*) FILTER (WHERE m.direction = 'outbound' AND m.sent_by = 'owner') AS n_own, "
        "  count(*) FILTER (WHERE m.direction = 'outbound') AS n_out, count(*) FILTER (WHERE m.ad) AS n_ad "
        "  FROM m GROUP BY m.cid, m.ch), "
        "pc AS (SELECT cid, sum(n_in) AS n_in, sum(n_own) AS n_own, sum(n_out) AS n_out, sum(n_ad) AS n_ad "
        "  FROM per GROUP BY cid) "
        "SELECT 'all' AS k, count(*) FILTER (WHERE n_in > 0), count(*) FILTER (WHERE n_in > 0 AND n_own = 0 AND n_out > 0), "
        "  count(*) FILTER (WHERE n_ad > 0), COALESCE(sum(n_ad), 0) FROM pc "
        "UNION ALL SELECT ch, count(*) FILTER (WHERE n_in > 0), 0, 0, 0 FROM per GROUP BY ch"
    )
    writers = {"all": 0}
    bot_only = 0
    ads = {"clients": 0, "messages": 0}
    for k, w, solo, ad_c, ad_m in db.execute(text(sql_c), {"uid": uid, "s": rng.since, "u": rng.until}).fetchall():
        writers[k] = int(w or 0)
        if k == "all":
            bot_only = int(solo or 0)
            ads = {"clients": int(ad_c or 0), "messages": int(ad_m or 0)}
    return {"series": series, "prev_in": prev_in, "totals": totals, "writers": writers, "bot_only": bot_only,
            "ads": ads, "heat": heat}


def _calls_block(db: Session, uid: str, rng: Range, idx: BucketIndex, pidx: BucketIndex) -> Dict[str, Any]:
    n, pn = idx.n, pidx.n
    s = {"calls_in": _zeros(n), "calls_out": _zeros(n), "answered": _zeros(n), "missed": _zeros(n),
         "talk_min": [0.0] * n}
    prev_total = _zeros(pn)
    prev_in = _zeros(pn)
    t = {"in": 0, "out": 0, "answered": 0, "missed": 0, "talk_sec": 0.0, "prev_total": 0, "prev_answered": 0,
         "prev_talk_sec": 0.0, "answered_in": 0, "out_answered": 0, "prev_in": 0}
    bk = _bk_tz("created_at")
    a_s, a_u = rng.aware(prev=True)[0], rng.aware()[1]
    rows = db.execute(text(
        f"SELECT date({bk}) AS d, CAST(extract(hour FROM {bk}) AS int) AS h, direction, "
        f"(answered_at IS NOT NULL) AS ans, count(*) AS n, COALESCE(sum(duration_sec) FILTER (WHERE answered_at IS NOT NULL), 0) AS sec "
        f"FROM sip_calls WHERE user_id = :uid AND created_at >= :s AND created_at < :u "
        f"AND status NOT IN ('queued', 'dialing', 'ringing') GROUP BY 1, 2, 3, 4"
    ), {"uid": uid, "s": a_s, "u": a_u}).fetchall()
    heat = _empty_grid()
    by_hour = _zeros(24)
    for d, h, direction, ans, cnt, sec in rows:
        cnt, sec = int(cnt or 0), float(sec or 0)
        inbound = direction == "inbound"
        if rng.a <= d <= rng.b:
            i = idx.index(d, h)
            t["in" if inbound else "out"] += cnt
            if inbound and h is not None:
                heat[d.weekday()][int(h)] += cnt
                by_hour[int(h)] += cnt
            if ans:
                t["answered"] += cnt
                t["talk_sec"] += sec
                if inbound:
                    t["answered_in"] += cnt
                else:
                    t["out_answered"] += cnt
            elif inbound:
                t["missed"] += cnt
            if i is not None:
                s["calls_in" if inbound else "calls_out"][i] += cnt
                if ans:
                    s["answered"][i] += cnt
                    s["talk_min"][i] += sec / 60.0
                elif inbound:
                    s["missed"][i] += cnt
        else:
            t["prev_total"] += cnt
            if inbound:
                t["prev_in"] += cnt
            if ans:
                t["prev_answered"] += cnt
                t["prev_talk_sec"] += sec
            i = pidx.index(d, h)
            if i is not None:
                prev_total[i] += cnt
                if inbound:
                    prev_in[i] += cnt
    s["talk_min"] = [round(x, 1) for x in s["talk_min"]]
    unique_callers = int(db.execute(text(
        "SELECT count(DISTINCT caller) FROM sip_calls WHERE user_id = :uid AND direction = 'inbound' "
        "AND created_at >= :s AND created_at < :u"
    ), {"uid": uid, "s": rng.aware()[0], "u": rng.aware()[1]}).scalar() or 0)
    return {"series": s, "prev_total": prev_total, "prev_in": prev_in, "totals": t, "by_hour": by_hour,
            "unique_callers": unique_callers, "heat": heat}


def _outcomes_block(db: Session, uid: str, rng: Range) -> Dict[str, int]:
    out: Dict[str, int] = {}
    rows = db.execute(text(
        "SELECT COALESCE(post_call_decision, ''), count(*) FROM agent_calls WHERE user_id = :uid "
        "AND created_at >= :s AND created_at < :u AND direction NOT IN :dirs GROUP BY 1"
    ).bindparams(_expanding("dirs")), {"uid": uid, "s": rng.since, "u": rng.until,
                                       "dirs": list(MESSAGE_DIRECTIONS)}).fetchall()
    for k, cnt in rows:
        key = (k or "").upper()
        if key in OUTCOME_TITLES:
            out[key] = out.get(key, 0) + int(cnt or 0)
        elif key:
            out["OTHER"] = out.get("OTHER", 0) + int(cnt or 0)
    return out


def _expanding(name: str):
    from sqlalchemy import bindparam
    return bindparam(name, expanding=True)


def _clients_block(db: Session, uid: str, rng: Range, idx: BucketIndex, pidx: BucketIndex,
                   kinds: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """Новые клиенты: по столбцам, откуда пришли (первое касание) и что с ними стало (когорта)."""
    has_bookings = _has_table(db, "bookings")
    union = _messages_union("ms", "mu")
    booking_part = ""
    if has_bookings:
        booking_part = (" UNION ALL SELECT b.contact_id AS cid, CASE WHEN b.source = 'online' THEN 'booking' ELSE NULL END AS ch, "
                        "(b.created_at AT TIME ZONE 'UTC') AS created_at FROM bookings b WHERE b.contact_id IN (SELECT id FROM nc)")
    sql = (
        "WITH nc AS (SELECT id, agent_config_id, status, deal_amount, created_at FROM agent_contacts "
        "  WHERE user_id = :uid AND created_at >= :s AND created_at < :u), "
        f"m AS ({union}), "
        "ev AS ("
        "  SELECT m.cid, m.ch, m.created_at FROM m WHERE m.cid IN (SELECT id FROM nc) "
        "  UNION ALL SELECT ac.agent_contact_id AS cid, 'call' AS ch, ac.created_at FROM agent_calls ac "
        "  WHERE ac.agent_contact_id IN (SELECT id FROM nc) AND ac.direction NOT IN :dirs"
        f"{booking_part}"
        "), "
        "fst AS (SELECT DISTINCT ON (ev.cid) ev.cid, ev.ch FROM ev WHERE ev.ch IS NOT NULL ORDER BY ev.cid, ev.created_at) "
        f"SELECT nc.id, nc.agent_config_id, nc.status, nc.deal_amount, date({_bk_naive('nc.created_at')}) AS d, "
        f"CAST(extract(hour FROM {_bk_naive('nc.created_at')}) AS int) AS h, fst.ch "
        "FROM nc LEFT JOIN fst ON fst.cid = nc.id"
    )
    params = {"uid": uid, "s": rng.since, "u": rng.until, "ms": rng.since - timedelta(days=2),
              "mu": rng.until + timedelta(days=2), "dirs": list(MESSAGE_DIRECTIONS)}
    rows = db.execute(text(sql).bindparams(_expanding("dirs")), params).fetchall()
    series = _zeros(idx.n)
    first: Dict[str, int] = {}
    cohort = {"new": 0, "won": 0, "lost": 0, "open": 0, "won_amount": 0.0}
    for _id, agent_id, status, amount, d, h, ch in rows:
        cohort["new"] += 1
        i = idx.index(d, h)
        if i is not None:
            series[i] += 1
        key = ch or "manual"
        first[key] = first.get(key, 0) + 1
        kind = (kinds.get(str(agent_id)) or {}).get(status) or ("won" if status == "success" else
                                                               "lost" if status in ("rejected", "do_not_call") else "open")
        cohort[kind] = cohort.get(kind, 0) + 1
        if kind == "won" and amount is not None:
            cohort["won_amount"] += float(amount)
    prev_series = _zeros(pidx.n)
    prev_total = 0
    nc_bk = _bk_naive("created_at")
    for d, h, cnt in db.execute(text(
        f"SELECT date({nc_bk}), CAST(extract(hour FROM {nc_bk}) AS int), count(*) FROM agent_contacts "
        f"WHERE user_id = :uid AND created_at >= :s AND created_at < :u GROUP BY 1, 2"
    ), {"uid": uid, "s": rng.prev_since, "u": rng.prev_until}).fetchall():
        prev_total += int(cnt or 0)
        i = pidx.index(d, h)
        if i is not None:
            prev_series[i] += int(cnt or 0)
    cohort["won_amount"] = round(cohort["won_amount"], 2)
    return {"series": series, "prev_series": prev_series, "total": cohort["new"], "prev_total": prev_total,
            "first_channel": first, "cohort": cohort}


def _replies_block(db: Session, uid: str, rng: Range) -> Dict[str, Any]:
    """Время ответа: от первого сообщения клиента в серии до первого ответа (бота или вашего)."""
    union = _messages_union()
    sql = (
        f"WITH m AS ({union}), "
        "x AS (SELECT cid, ch, direction, sent_by, created_at, "
        "  CASE WHEN direction = lag(direction) OVER w THEN 0 ELSE 1 END AS chg "
        "  FROM m WINDOW w AS (PARTITION BY cid, ch ORDER BY created_at)), "
        "y AS (SELECT *, sum(chg) OVER (PARTITION BY cid, ch ORDER BY created_at ROWS UNBOUNDED PRECEDING) AS run FROM x), "
        "runs AS (SELECT cid, ch, run, min(direction) AS direction, min(created_at) AS t0, "
        "  (array_agg(COALESCE(sent_by, '') ORDER BY created_at))[1] AS first_by FROM y GROUP BY cid, ch, run), "
        "pairs AS (SELECT a.ch, a.t0 AS t_in, b.t0 AS t_out, b.first_by FROM runs a "
        "  LEFT JOIN runs b ON b.cid = a.cid AND b.ch = a.ch AND b.run = a.run + 1 "
        "  WHERE a.direction = 'inbound' AND a.t0 >= :cs AND a.t0 < :cu) "
        "SELECT ch, "
        "  count(*) AS total, "
        "  count(*) FILTER (WHERE t_out IS NULL AND t_in < :late) AS unanswered, "
        "  count(*) FILTER (WHERE t_out IS NULL AND t_in >= :late) AS waiting, "
        "  count(*) FILTER (WHERE t_out IS NOT NULL AND first_by = 'owner') AS by_owner, "
        "  percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM t_out - t_in)) "
        "    FILTER (WHERE t_out IS NOT NULL) AS med, "
        "  percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM t_out - t_in)) "
        "    FILTER (WHERE t_out IS NOT NULL AND first_by <> 'owner') AS med_bot, "
        "  percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM t_out - t_in)) "
        "    FILTER (WHERE t_out IS NOT NULL AND first_by = 'owner') AS med_owner "
        "FROM pairs GROUP BY ROLLUP (ch)"
    )
    late = datetime.utcnow() - timedelta(hours=1)
    rows = db.execute(text(sql), {"uid": uid, "s": rng.since - timedelta(days=1), "u": rng.until + timedelta(days=1),
                                  "cs": rng.since, "cu": rng.until, "late": late}).fetchall()
    out: Dict[str, Any] = {}
    for ch, total, unanswered, waiting, by_owner, med, med_bot, med_owner in rows:
        out[ch or "all"] = {
            "threads": int(total or 0), "unanswered": int(unanswered or 0), "waiting": int(waiting or 0),
            "by_owner": int(by_owner or 0),
            "median_sec": int(round(med)) if med is not None else None,
            "median_bot_sec": int(round(med_bot)) if med_bot is not None else None,
            "median_owner_sec": int(round(med_owner)) if med_owner is not None else None,
        }
    return out


def _heatmap(msgs: Optional[Dict[str, Any]], calls: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Когда пишут и звонят: [день недели 0=пн][час] — сообщения клиентов и входящие звонки (время Бишкека)."""
    grids = {ch: ((msgs or {}).get("heat") or {}).get(ch) or _empty_grid() for ch in CHANNELS}
    grids["call"] = (calls or {}).get("heat") or _empty_grid()
    grids["all"] = [[sum(grids[k][dw][h] for k in (*CHANNELS, "call")) for h in range(24)] for dw in range(7)]
    return {"grids": grids, "max": {k: max(max(r) for r in g) for k, g in grids.items()}}


def _stage_kinds(db: Session, uid) -> Tuple[Dict[str, Dict[str, str]], Optional[Any]]:
    from backend.core.pipeline_stages import get_agent_stages
    from backend.models.agent_config import AgentConfig
    agents = (db.query(AgentConfig).filter(AgentConfig.user_id == uid)
              .order_by(AgentConfig.created_at.asc()).all())
    kinds = {str(a.id): {s["key"]: s.get("kind") or "open" for s in get_agent_stages(a)} for a in agents}
    return kinds, (agents[0] if agents else None)


def _funnel_block(db: Session, uid: str, rng: Range, agent, light: bool = False) -> Dict[str, Any]:
    """Воронка сейчас. light=True — только стадии и суммы (для KPI главной), без причин отказа и тегов."""
    from backend.core.pipeline_stages import get_agent_stages
    stages = get_agent_stages(agent)
    counts: Dict[str, Tuple[int, float]] = {}
    for status, cnt, amount in db.execute(text(
        "SELECT status, count(*), COALESCE(sum(deal_amount), 0) FROM agent_contacts WHERE user_id = :uid GROUP BY 1"
    ), {"uid": uid}).fetchall():
        counts[status] = (int(cnt or 0), float(amount or 0))
    known = {s["key"] for s in stages}
    out_stages = [{"key": s["key"], "label": s["label"], "color": s.get("color"), "kind": s.get("kind") or "open",
                   "count": counts.get(s["key"], (0, 0))[0], "amount": round(counts.get(s["key"], (0, 0.0))[1], 2)}
                  for s in stages]
    other = sum(v[0] for k, v in counts.items() if k not in known)
    total = sum(v[0] for v in counts.values())
    pipeline = sum(s["amount"] for s in out_stages if s["kind"] == "open")
    won_amount = sum(s["amount"] for s in out_stages if s["kind"] == "won")
    won = sum(s["count"] for s in out_stages if s["kind"] == "won")
    if light:
        return {"stages": out_stages, "other": other, "total": total, "won": won, "won_amount": round(won_amount, 2),
                "pipeline_amount": round(pipeline, 2), "lost_reasons": [], "tags": [], "paused": 0, "spam_blocked": 0}
    lost_keys = [s["key"] for s in out_stages if s["kind"] == "lost"] or ["rejected"]
    reasons: Dict[str, int] = {}
    for reason, cnt in db.execute(text(
        "SELECT lower(trim(left(lost_reason, 80))), count(*) FROM agent_contacts WHERE user_id = :uid "
        "AND lost_reason IS NOT NULL AND trim(lost_reason) <> '' AND updated_at >= :s AND updated_at < :u "
        "AND status IN :keys GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
    ).bindparams(_expanding("keys")), {"uid": uid, "s": rng.since, "u": rng.until, "keys": lost_keys}).fetchall():
        reasons[reason] = int(cnt or 0)
    tags: Dict[str, int] = {}
    for (raw,) in db.execute(text(
        "SELECT tags FROM agent_contacts WHERE user_id = :uid AND tags IS NOT NULL AND tags <> '' "
        "AND updated_at >= :s AND updated_at < :u LIMIT 5000"
    ), {"uid": uid, "s": rng.since, "u": rng.until}).fetchall():
        for t in str(raw).split(","):
            t = t.strip()
            if t:
                tags[t] = tags.get(t, 0) + 1
    paused = int(db.execute(text(
        "SELECT count(*) FROM agent_contacts WHERE user_id = :uid AND ai_paused_until > :now"
    ), {"uid": uid, "now": datetime.utcnow()}).scalar() or 0)
    spam = int(db.execute(text(
        "SELECT count(*) FROM agent_contacts WHERE user_id = :uid AND ai_pause_reason = 'spam' "
        "AND updated_at >= :s AND updated_at < :u"
    ), {"uid": uid, "s": rng.since, "u": rng.until}).scalar() or 0)
    return {
        "stages": out_stages, "other": other, "total": total, "won": won,
        "won_amount": round(won_amount, 2), "pipeline_amount": round(pipeline, 2),
        "lost_reasons": [{"text": k, "count": v} for k, v in reasons.items()],
        "tags": [{"tag": k, "count": v} for k, v in sorted(tags.items(), key=lambda kv: -kv[1])[:10]],
        "paused": paused, "spam_blocked": spam,
    }


def _orders_block(db: Session, user, rng: Range, idx: BucketIndex, pidx: BucketIndex,
                  enabled: bool) -> Optional[Dict[str, Any]]:
    """Заказы за период (модуль «Заказы»): по дате оформления; выручка — не отменённые заказы."""
    if not _has_table(db, "orders"):
        return None
    uid = str(user.id)
    s, u = rng.aware()
    ps, _pu = rng.aware(prev=True)
    bk = _bk_tz("created_at")
    series = {"orders": _zeros(idx.n), "orders_done": _zeros(idx.n), "orders_open": _zeros(idx.n), "order_revenue": _zeros(idx.n)}
    prev_orders = _zeros(pidx.n)
    tot = {"total": 0, "done": 0, "open": 0, "cancelled": 0, "revenue": 0.0, "prev_total": 0, "prev_revenue": 0.0}
    rows = db.execute(text(
        f"SELECT date({bk}) AS d, CAST(extract(hour FROM {bk}) AS int) AS h, status, count(*), COALESCE(sum(total), 0) "
        f"FROM orders WHERE user_id = :uid AND created_at >= :s AND created_at < :u GROUP BY 1, 2, 3"
    ), {"uid": uid, "s": ps, "u": u}).fetchall()
    if not rows and not enabled:
        return None
    for d, h, status, cnt, total in rows:
        cnt, total = int(cnt or 0), float(total or 0)
        cur = rng.a <= d <= rng.b
        if status == "cancelled":
            if cur:
                tot["cancelled"] += cnt
            continue
        if cur:
            tot["total"] += cnt
            tot["revenue"] += total
            tot["done" if status == "done" else "open"] += cnt
            i = idx.index(d, h)
            if i is not None:
                series["orders"][i] += cnt
                series["orders_done" if status == "done" else "orders_open"][i] += cnt
                series["order_revenue"][i] += total
        else:
            tot["prev_total"] += cnt
            tot["prev_revenue"] += total
            j = pidx.index(d, h)
            if j is not None:
                prev_orders[j] += cnt
    sources = {str(k or "owner"): int(v or 0) for k, v in db.execute(text(
        "SELECT source, count(*) FROM orders WHERE user_id = :uid AND created_at >= :s AND created_at < :u "
        "AND status <> 'cancelled' GROUP BY 1"), {"uid": uid, "s": s, "u": u}).fetchall()}
    top = [{"title": t, "qty": int(q or 0), "amount": round(float(a or 0), 2)} for t, q, a in db.execute(text(
        "SELECT it->>'title', sum(COALESCE((it->>'qty')::int, 1)), "
        "sum(COALESCE((it->>'qty')::int, 1) * COALESCE((it->>'price')::float, 0)) "
        "FROM orders o, jsonb_array_elements(o.items) it WHERE o.user_id = :uid AND o.created_at >= :s "
        "AND o.created_at < :u AND o.status <> 'cancelled' GROUP BY 1 ORDER BY 2 DESC, 3 DESC LIMIT 8"),
        {"uid": uid, "s": s, "u": u}).fetchall()]
    delivery = {str(k): int(v or 0) for k, v in db.execute(text(
        "SELECT delivery_type, count(*) FROM orders WHERE user_id = :uid AND created_at >= :s AND created_at < :u "
        "AND status <> 'cancelled' GROUP BY 1"), {"uid": uid, "s": s, "u": u}).fetchall()}
    clients = int(db.execute(text(
        "SELECT count(DISTINCT COALESCE(CAST(contact_id AS text), client_phone, CAST(id AS text))) FROM orders "
        "WHERE user_id = :uid AND created_at >= :s AND created_at < :u AND status <> 'cancelled'"),
        {"uid": uid, "s": s, "u": u}).scalar() or 0)
    return {
        **tot, "revenue": round(tot["revenue"], 2), "prev_revenue": round(tot["prev_revenue"], 2),
        "avg_check": round(tot["revenue"] / tot["total"]) if tot["total"] else 0,
        "cancel_pct": _pct(tot["cancelled"], tot["total"] + tot["cancelled"]),
        "by_bot": sources.get("bot", 0), "sources": sources, "top": top, "delivery": delivery, "clients": clients,
        "series": series, "prev_orders": prev_orders,
    }


def _bookings_block(db: Session, user, rng: Range, idx: BucketIndex, pidx: BucketIndex) -> Optional[Dict[str, Any]]:
    if not _has_table(db, "bookings"):
        return None
    uid = str(user.id)
    s, u = rng.aware()
    ps, pu = rng.aware(prev=True)
    bk = _bk_tz("start_at")
    series = {"visits": _zeros(idx.n), "arrived": _zeros(idx.n), "no_show": _zeros(idx.n)}
    prev_visits = _zeros(pidx.n)
    tot = {"total": 0, "arrived": 0, "no_show": 0, "cancelled": 0, "upcoming": 0, "revenue": 0.0, "expected": 0.0,
           "prev_total": 0, "prev_revenue": 0.0, "booked_min": 0.0}
    rows = db.execute(text(
        f"SELECT date({bk}) AS d, CAST(extract(hour FROM {bk}) AS int) AS h, status, count(*), "
        f"COALESCE(sum(price), 0), COALESCE(sum(extract(epoch FROM end_at - start_at)) / 60.0, 0) "
        f"FROM bookings WHERE user_id = :uid AND start_at >= :s AND start_at < :u GROUP BY 1, 2, 3"
    ), {"uid": uid, "s": ps, "u": u}).fetchall()
    if not rows and not db.execute(text("SELECT 1 FROM booking_resources WHERE user_id = :uid LIMIT 1"),
                                   {"uid": uid}).fetchone():
        return None
    for d, h, status, cnt, price, minutes in rows:
        cnt, price, minutes = int(cnt or 0), float(price or 0), float(minutes or 0)
        cur = rng.a <= d <= rng.b
        if status == "cancelled":
            if cur:
                tot["cancelled"] += cnt
            continue
        if cur:
            i = idx.index(d, h)
            tot["total"] += cnt
            tot["booked_min"] += minutes
            if status in ("arrived", "completed"):
                tot["arrived"] += cnt
                tot["revenue"] += price
            elif status == "no_show":
                tot["no_show"] += cnt
            else:
                tot["upcoming"] += cnt
                tot["expected"] += price
            if i is not None:
                series["visits"][i] += cnt
                if status in ("arrived", "completed"):
                    series["arrived"][i] += cnt
                elif status == "no_show":
                    series["no_show"][i] += cnt
        else:
            tot["prev_total"] += cnt
            if status in ("arrived", "completed"):
                tot["prev_revenue"] += price
            i = pidx.index(d, h)
            if i is not None:
                prev_visits[i] += cnt

    sources: Dict[str, int] = {}
    for src, cnt in db.execute(text(
        "SELECT COALESCE(source, 'owner'), count(*) FROM bookings WHERE user_id = :uid AND created_at >= :s "
        "AND created_at < :u GROUP BY 1"
    ), {"uid": uid, "s": s, "u": u}).fetchall():
        sources[src] = int(cnt or 0)
    services = [{"title": t or "Без услуги", "count": int(c or 0), "revenue": round(float(r or 0), 2)}
                for t, c, r in db.execute(text(
                    "SELECT service_title, count(*), COALESCE(sum(price) FILTER (WHERE status IN ('arrived', 'completed')), 0) "
                    "FROM bookings WHERE user_id = :uid AND start_at >= :s AND start_at < :u AND status <> 'cancelled' "
                    "GROUP BY 1 ORDER BY 2 DESC LIMIT 8"), {"uid": uid, "s": s, "u": u}).fetchall()]
    by_res: Dict[str, Dict[str, float]] = {}
    for rid, cnt, minutes, rev in db.execute(text(
        "SELECT resource_id, count(*), COALESCE(sum(extract(epoch FROM end_at - start_at)) / 60.0, 0), "
        "COALESCE(sum(price) FILTER (WHERE status IN ('arrived', 'completed')), 0) FROM bookings "
        "WHERE user_id = :uid AND start_at >= :s AND start_at < :u AND status <> 'cancelled' GROUP BY 1"
    ), {"uid": uid, "s": s, "u": u}).fetchall():
        by_res[str(rid)] = {"count": int(cnt or 0), "minutes": float(minutes or 0), "revenue": float(rev or 0)}
    repeat = int(db.execute(text(
        "SELECT count(DISTINCT b.contact_id) FROM bookings b WHERE b.user_id = :uid AND b.start_at >= :s "
        "AND b.start_at < :u AND b.status <> 'cancelled' AND b.contact_id IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM bookings p WHERE p.contact_id = b.contact_id AND p.user_id = b.user_id "
        "AND p.start_at < :s AND p.status IN ('arrived', 'completed'))"
    ), {"uid": uid, "s": s, "u": u}).scalar() or 0)
    clients = int(db.execute(text(
        "SELECT count(DISTINCT COALESCE(CAST(contact_id AS text), client_phone, CAST(id AS text))) FROM bookings "
        "WHERE user_id = :uid AND start_at >= :s AND start_at < :u AND status <> 'cancelled'"
    ), {"uid": uid, "s": s, "u": u}).scalar() or 0)

    masters = []
    from backend.services import booking_service as bs
    capacity_total = 0.0
    for r in bs.resources(db, user.id, active_only=False):
        cap = 0.0
        d = rng.a
        while d <= rng.b:
            for wa, wb in bs._work_intervals(r, d):
                cap += (wb - wa).total_seconds() / 60.0
            d += timedelta(days=1)
        st = by_res.get(str(r.id), {"count": 0, "minutes": 0.0, "revenue": 0.0})
        if not r.is_active and not st["count"]:
            continue
        capacity_total += cap
        masters.append({"id": str(r.id), "name": r.name, "color": r.color, "count": st["count"],
                        "booked_min": int(round(st["minutes"])), "capacity_min": int(round(cap)),
                        "load_pct": _pct(st["minutes"], cap), "revenue": round(st["revenue"], 2)})
    masters.sort(key=lambda m: -m["count"])
    decided = tot["arrived"] + tot["no_show"]
    return {
        "series": series, "prev_visits": prev_visits,
        "total": tot["total"], "prev_total": tot["prev_total"], "arrived": tot["arrived"],
        "no_show": tot["no_show"], "cancelled": tot["cancelled"], "upcoming": tot["upcoming"],
        "no_show_pct": _pct(tot["no_show"], decided),
        "revenue": round(tot["revenue"], 2), "prev_revenue": round(tot["prev_revenue"], 2),
        "expected_revenue": round(tot["expected"], 2),
        "avg_check": round(tot["revenue"] / tot["arrived"], 2) if tot["arrived"] else None,
        "sources": sources, "services": services, "masters": masters,
        "load_pct": _pct(tot["booked_min"], capacity_total), "clients": clients, "repeat_clients": repeat,
    }


def _catalog_block(db: Session, uid: str) -> Optional[Dict[str, Any]]:
    row = db.execute(text(
        "SELECT count(*) FILTER (WHERE is_active), "
        "count(*) FILTER (WHERE is_active AND NOT in_stock), "
        "count(*) FILTER (WHERE is_active AND in_stock AND stock_qty IS NOT NULL AND stock_qty BETWEEN 1 AND 3), "
        "count(*) FILTER (WHERE is_active AND (CASE WHEN jsonb_typeof(media_file_ids) = 'array' "
        "  THEN jsonb_array_length(media_file_ids) ELSE 0 END) = 0 "
        "  AND COALESCE(CASE WHEN jsonb_typeof(attributes) = 'object' THEN attributes->>'_photo_url' END, '') = ''), "
        "count(*) FILTER (WHERE is_active AND (price IS NULL OR price <= 0)), "
        "count(*) FILTER (WHERE is_active AND old_price IS NOT NULL AND old_price > COALESCE(price, 0)), "
        "COALESCE(sum(price * stock_qty) FILTER (WHERE is_active AND stock_qty > 0 AND price > 0), 0) "
        "FROM agent_knowledge_entries WHERE user_id = :uid AND kind = 'product'"
    ), {"uid": uid}).fetchone()
    if not row or not row[0]:
        return None
    collections = [{"title": t or "Без коллекции", "count": int(c or 0), "out": int(o or 0)}
                   for t, c, o in db.execute(text(
                       "SELECT collection, count(*), count(*) FILTER (WHERE NOT in_stock) FROM agent_knowledge_entries "
                       "WHERE user_id = :uid AND kind = 'product' AND is_active GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
                   ), {"uid": uid}).fetchall()]
    return {"active": int(row[0] or 0), "out_of_stock": int(row[1] or 0), "low_stock": int(row[2] or 0),
            "no_photo": int(row[3] or 0), "no_price": int(row[4] or 0), "on_sale": int(row[5] or 0),
            "stock_value": round(float(row[6] or 0), 2), "collections": collections}


def _money_block(db: Session, user, rng: Range) -> Dict[str, Any]:
    uid = str(user.id)
    s, u = rng.aware()
    row = db.execute(text(
        "SELECT COALESCE(-sum(amount) FILTER (WHERE type = 'charge'), 0), "
        "COALESCE(sum(seconds) FILTER (WHERE type = 'charge'), 0), "
        "COALESCE(sum(amount) FILTER (WHERE type IN ('topup', 'bonus', 'admin') AND amount > 0), 0) "
        "FROM wallet_transactions WHERE user_id = :uid AND created_at >= :s AND created_at < :u"
    ), {"uid": uid, "s": s, "u": u}).fetchone()
    credits = 0
    try:
        credits = int(db.execute(text(
            "SELECT COALESCE(-sum(amount), 0) FROM credit_transactions WHERE user_id = :uid AND amount < 0 "
            "AND created_at >= :s AND created_at < :u"
        ), {"uid": uid, "s": s, "u": u}).scalar() or 0)
    except Exception:
        db.rollback()
    from backend.services import wallet_service as ws
    spent_tyiyn = int(row[0] or 0) if row else 0
    return {"wallet_spent": ws.to_som(spent_tyiyn), "paid_minutes": round(float(row[1] or 0) / 60.0, 1) if row else 0,
            "wallet_topup": ws.to_som(int(row[2] or 0)) if row else 0, "credits_spent": credits,
            "wallet_balance": ws.to_som(int(getattr(user, "wallet_balance", 0) or 0)),
            "credits_balance": int(getattr(user, "credits_balance", 0) or 0)}


def _dialogs_block(db: Session, uid: str, rng: Range, idx: BucketIndex) -> Dict[str, Any]:
    """Разговоры голосовых ассистентов (сайт и телефон) — для кабинета без чат-бота."""
    series = _zeros(idx.n)
    total = 0
    bk = _bk_naive("min_at")
    for d, h, cnt in db.execute(text(
        "WITH s AS (SELECT e.session_id, min(e.created_at) AS min_at FROM eleven_conversations e "
        "JOIN eleven_assistant_configs a ON a.id = e.assistant_id WHERE a.user_id = :uid "
        "AND e.created_at >= :s AND e.created_at < :u GROUP BY e.session_id) "
        f"SELECT date({bk}), CAST(extract(hour FROM {bk}) AS int), count(*) FROM s GROUP BY 1, 2"
    ), {"uid": uid, "s": rng.since, "u": rng.until}).fetchall():
        total += int(cnt or 0)
        i = idx.index(d, h)
        if i is not None:
            series[i] += int(cnt or 0)
    return {"series": series, "total": total}


def _gaps_count(db: Session, uid: str, rng: Range) -> int:
    return int(db.execute(text(
        "SELECT count(*) FROM agent_knowledge_gaps g JOIN agent_configs a ON a.id = g.agent_config_id "
        "WHERE a.user_id = :uid AND NOT g.resolved AND g.last_asked_at >= :s AND g.last_asked_at < :u"
    ), {"uid": uid, "s": rng.since, "u": rng.until}).scalar() or 0)


# ───────────────────────────── выводы словами ─────────────────────────────

def _fmt_sec(sec: Optional[int]) -> str:
    if sec is None:
        return "—"
    if sec < 60:
        return f"{sec} с"
    if sec < 3600:
        m, s = divmod(sec, 60)
        return f"{m} мин" + (f" {s} с" if s and m < 10 else "")
    h, rem = divmod(sec, 3600)
    return f"{h} ч" + (f" {rem // 60} мин" if rem // 60 else "")


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)) % 100
    n1 = n % 10
    if 10 < n < 20:
        return many
    if 1 < n1 < 5:
        return few
    if n1 == 1:
        return one
    return many


def build_insights(rep: Dict[str, Any]) -> List[Dict[str, str]]:
    """Короткие выводы, которые владелец поймёт без графиков (максимум 5)."""
    out: List[Dict[str, str]] = []
    hm = (rep.get("heatmap") or {}).get("grids", {}).get("all")
    if hm:
        best, best_v = None, 0
        for dow in range(7):
            for h in range(23):
                v = hm[dow][h] + hm[dow][h + 1]
                if v > best_v:
                    best, best_v = (dow, h), v
        total = sum(sum(r) for r in hm)
        if best and total >= 10:
            out.append({"icon": "clock", "tone": "info",
                        "text": f"Чаще всего пишут и звонят {WEEKDAYS_ACC[best[0]]} с {best[1]}:00 до {best[1] + 2}:00 — "
                                f"в эти часы важно отвечать быстро"})
    ch = rep.get("channels") or []
    new_total = sum(c.get("new_clients", 0) for c in ch)
    if new_total >= 5:
        top = max(ch, key=lambda c: c.get("new_clients", 0))
        share = _pct(top.get("new_clients", 0), new_total)
        if share and share >= 40:
            out.append({"icon": "users", "tone": "info",
                        "text": f"{top['title']} приносит {share}% новых клиентов ({top['new_clients']} из {new_total})"})
    rep_all = (rep.get("replies") or {}).get("all") or {}
    if rep_all.get("median_bot_sec") is not None:
        out.append({"icon": "bot", "tone": "good",
                    "text": f"Бот отвечает в среднем за {_fmt_sec(rep_all['median_bot_sec'])}"
                            + (f", вы — за {_fmt_sec(rep_all['median_owner_sec'])}" if rep_all.get("median_owner_sec") else "")})
    if rep_all.get("unanswered"):
        n = rep_all["unanswered"]
        out.append({"icon": "inbox", "tone": "warn",
                    "text": f"{n} {_plural(n, 'обращение осталось', 'обращения остались', 'обращений осталось')} без ответа — "
                            f"проверьте Инбокс, папку «Ждут ответа»"})
    calls = rep.get("calls") or {}
    if calls.get("missed") and calls.get("in", 0) >= 5:
        miss_pct = _pct(calls["missed"], calls["in"])
        if miss_pct and miss_pct >= 15:
            out.append({"icon": "phone-off", "tone": "warn",
                        "text": f"Пропущено {miss_pct}% входящих звонков ({calls['missed']}) — проверьте, что номер привязан к ассистенту"})
    b = rep.get("bookings") or {}
    if b.get("no_show_pct") is not None and b.get("no_show_pct", 0) >= 10 and (b.get("arrived", 0) + b.get("no_show", 0)) >= 5:
        out.append({"icon": "calendar", "tone": "warn",
                    "text": f"Не пришли {b['no_show_pct']}% записанных — включите напоминание за 2 часа в настройках записи"})
    if b.get("load_pct") is not None and b.get("total", 0) >= 5:
        if b["load_pct"] < 40:
            out.append({"icon": "calendar", "tone": "info",
                        "text": f"Загрузка мастеров {b['load_pct']}% — есть свободные окна: отправьте клиентам ссылку на запись"})
        elif b["load_pct"] >= 85:
            out.append({"icon": "calendar", "tone": "good",
                        "text": f"Загрузка мастеров {b['load_pct']}% — почти без окон, можно расширять график"})
    f = rep.get("funnel") or {}
    co = (rep.get("clients") or {}).get("cohort") or {}
    if co.get("new", 0) >= 5 and co.get("won") is not None and rep.get("modules", {}).get("crm", True):
        conv = _pct(co["won"], co["new"])
        if conv is not None:
            out.append({"icon": "circle-check", "tone": "good" if conv >= 10 else "info",
                        "text": f"Из {co['new']} новых клиентов купили {co['won']} ({conv}%)"
                                + (f", ещё {co['open']} в работе" if co.get("open") else "")})
    gaps = (rep.get("bot") or {}).get("gaps") or 0
    if gaps:
        out.append({"icon": "book-open", "tone": "warn",
                    "text": f"Бот не нашёл ответа на {gaps} {_plural(gaps, 'вопрос', 'вопроса', 'вопросов')} клиентов — "
                            f"допишите ответы в базу знаний"})
    cat = rep.get("catalog") or {}
    if cat.get("out_of_stock"):
        out.append({"icon": "package", "tone": "warn",
                    "text": f"Нет в наличии: {cat['out_of_stock']} {_plural(cat['out_of_stock'], 'позиция', 'позиции', 'позиций')}"
                            + (f", заканчивается ещё {cat['low_stock']}" if cat.get("low_stock") else "")})
    if f.get("lost_reasons"):
        r0 = f["lost_reasons"][0]
        if r0["count"] >= 3:
            out.append({"icon": "circle-x", "tone": "info",
                        "text": f"Частая причина отказа: «{r0['text']}» ({r0['count']})"})
    order = {"warn": 0, "good": 1, "info": 2}
    out.sort(key=lambda x: order.get(x["tone"], 3))
    return out[:5]


# ───────────────────────────── сборка отчёта ─────────────────────────────

def _sum(xs: Iterable[float]) -> float:
    return float(sum(xs or []))


def _spark(xs: List[float], limit: int = 24) -> List[float]:
    """Короткий ряд для мини-графика плитки: не больше `limit` точек (соседние столбцы складываются)."""
    xs = list(xs or [])
    if len(xs) <= limit:
        return xs
    step = (len(xs) + limit - 1) // limit
    return [sum(xs[i:i + step]) for i in range(0, len(xs), step)]


def _kpi(kid: str, label: str, value, prev, *, unit: str = "", good: str = "up", spark=None, sub: str = "",
         section: str = "", fmt: str = "num") -> Dict[str, Any]:
    # ноль и раньше ноль — сравнивать нечего, значок «как раньше» был бы шумом
    d = _delta(value or 0, prev or 0) if prev is not None and (value or prev) else None
    return {"id": kid, "label": label, "value": value, "prev": prev, "delta": d, "unit": unit, "good": good,
            "spark": _spark(spark or []), "sub": sub, "section": section, "fmt": fmt}


SECTIONS = ("kpis", "series", "channels", "replies", "heatmap", "funnel", "calls", "bookings", "orders", "catalog",
            "money", "bot", "finance")


def build_report(db: Session, user, period: Optional[str] = "30d", date_from: Optional[str] = None,
                 date_to: Optional[str] = None, granularity: Optional[str] = None,
                 sections: Optional[Iterable[str]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    want = set(sections or SECTIONS)
    rng = resolve_period(period, date_from, date_to, now)
    gran = pick_granularity(rng.days, granularity)
    buckets = make_buckets(rng.a, rng.b, gran)
    prev_buckets = make_buckets(rng.prev_a, rng.prev_b, gran)
    idx = BucketIndex(buckets, gran)
    pidx = BucketIndex(prev_buckets, gran)
    uid = str(user.id)
    now_dt = now or datetime.utcnow()
    if now_dt.tzinfo is not None:
        now_dt = now_dt.astimezone(timezone.utc).replace(tzinfo=None)
    tday = today_bk(now_dt)
    now_hour = (now_dt + BISHKEK_OFFSET).hour

    try:
        mode = user.agent_mode()
    except Exception:
        mode = None
    try:
        from backend.services.business_profile import modules_of, resolve as biz_resolve
        modules = modules_of(user)
        biz = biz_resolve(user)
    except Exception:
        modules, biz = {"crm": True, "booking": False, "orders": False}, {}

    kinds, agent = _safe(db, "stages", lambda: _stage_kinds(db, user.id), ({}, None))
    has_bot = agent is not None
    n = len(buckets)

    msgs = _safe(db, "messages", lambda: _messages_block(db, uid, rng, idx, pidx), None) if has_bot else None
    calls = _safe(db, "calls", lambda: _calls_block(db, uid, rng, idx, pidx), None)
    clients = _safe(db, "clients", lambda: _clients_block(db, uid, rng, idx, pidx, kinds), None) if has_bot else None
    replies = (_safe(db, "replies", lambda: _replies_block(db, uid, rng), {})
               if has_bot and want & {"replies", "kpis", "channels", "bot"} else {})
    heat = _heatmap(msgs, calls) if "heatmap" in want else None
    funnel = (_safe(db, "funnel", lambda: _funnel_block(db, uid, rng, agent, light=not want & {"funnel", "bot"}), None)
              if has_bot and want & {"funnel", "kpis", "bot"} else None)
    outcomes = _safe(db, "outcomes", lambda: _outcomes_block(db, uid, rng), {}) if has_bot and "calls" in want else {}
    bookings = (_safe(db, "bookings", lambda: _bookings_block(db, user, rng, idx, pidx), None)
                if (modules.get("booking") or "bookings" in want) and want & {"bookings", "kpis", "series"} else None)
    if bookings and not modules.get("booking") and not bookings.get("total") and not bookings.get("prev_total"):
        bookings = None   # запись выключена и записей нет — пустой раздел «Записи» магазину не нужен
    orders = (_safe(db, "orders", lambda: _orders_block(db, user, rng, idx, pidx, bool(modules.get("orders"))), None)
              if want & {"orders", "kpis", "series"} else None)
    catalog = _safe(db, "catalog", lambda: _catalog_block(db, uid), None) if has_bot and "catalog" in want else None
    money = _safe(db, "money", lambda: _money_block(db, user, rng), None) if "money" in want else None
    finance = None
    if want & {"finance", "kpis"} and (bookings or orders):
        def _fin():
            from backend.services import finance_service
            return finance_service.summary(db, user, rng=rng, with_series=False)
        finance = _safe(db, "finance", _fin, None)
    dialogs = (_safe(db, "dialogs", lambda: _dialogs_block(db, uid, rng, idx), None)
               if (not has_bot) and want & {"series", "kpis"} else None)
    gaps = _safe(db, "gaps", lambda: _gaps_count(db, uid, rng), 0) if has_bot and want & {"bot", "kpis"} else 0

    # ── ряды графика ──
    series: Dict[str, List[float]] = {}
    prev: Dict[str, List[float]] = {}
    ms = (msgs or {}).get("series") or {}
    cs = (calls or {}).get("series") or {}
    for ch in CHANNELS:
        series[f"{ch}_in"] = ms.get(f"{ch}_in") or _zeros(n)
    series["calls_in"] = cs.get("calls_in") or _zeros(n)
    series["calls_out"] = cs.get("calls_out") or _zeros(n)
    series["answered"] = cs.get("answered") or _zeros(n)
    series["missed"] = cs.get("missed") or _zeros(n)
    series["talk_min"] = cs.get("talk_min") or _zeros(n)
    series["bot_out"] = ms.get("bot_out") or _zeros(n)
    series["owner_out"] = ms.get("owner_out") or _zeros(n)
    series["messages_in"] = [sum(series[f"{ch}_in"][i] for ch in CHANNELS) for i in range(n)]
    series["inbound"] = [series["messages_in"][i] + series["calls_in"][i] for i in range(n)]
    series["calls"] = [series["calls_in"][i] + series["calls_out"][i] for i in range(n)]
    series["new_clients"] = (clients or {}).get("series") or _zeros(n)
    if bookings:
        series["visits"] = bookings["series"]["visits"]
        series["arrived"] = bookings["series"]["arrived"]
        series["no_show"] = bookings["series"]["no_show"]
    if orders:
        series.update(orders["series"])
    if dialogs:
        series["dialogs"] = dialogs["series"]

    pn = len(prev_buckets)
    pmsg = (msgs or {}).get("prev_in") or _zeros(pn)
    pcalls = (calls or {}).get("prev_total") or _zeros(pn)
    pcalls_in = (calls or {}).get("prev_in") or _zeros(pn)
    prev["inbound"] = [pmsg[i] + pcalls_in[i] for i in range(pn)]   # как текущий ряд: сообщения + входящие звонки
    prev["messages_in"] = pmsg
    prev["calls"] = pcalls
    prev["new_clients"] = (clients or {}).get("prev_series") or _zeros(pn)
    if bookings:
        prev["visits"] = bookings["prev_visits"]
    if orders:
        prev["orders"] = orders["prev_orders"]

    # ── каналы ──
    channels: List[Dict[str, Any]] = []
    mt = (msgs or {}).get("totals") or {}
    writers = (msgs or {}).get("writers") or {}
    first = (clients or {}).get("first_channel") or {}
    for ch in CHANNELS:
        t = mt.get(ch) or {"in": 0, "bot": 0, "owner": 0, "prev_in": 0}
        r = replies.get(ch) or {}
        channels.append({
            "key": ch, "title": CHANNEL_TITLES[ch], "messages_in": t["in"], "prev_in": t["prev_in"],
            "delta": _delta(t["in"], t["prev_in"]), "writers": writers.get(ch, 0), "bot_out": t["bot"],
            "owner_out": t["owner"], "new_clients": first.get(ch, 0), "median_reply_sec": r.get("median_sec"),
            "unanswered": r.get("unanswered", 0), "waiting": r.get("waiting", 0), "threads": r.get("threads", 0),
        })
    ct = (calls or {}).get("totals") or {}
    channels.append({
        "key": "call", "title": CHANNEL_TITLES["call"], "messages_in": ct.get("in", 0), "prev_in": None,
        "delta": None, "writers": (calls or {}).get("unique_callers", 0), "bot_out": ct.get("out", 0), "owner_out": 0,
        "new_clients": first.get("call", 0), "median_reply_sec": None, "unanswered": ct.get("missed", 0),
        "waiting": 0, "threads": ct.get("in", 0), "answered": ct.get("answered", 0),
    })
    if bookings or first.get("booking"):
        channels.append({
            "key": "booking", "title": CHANNEL_TITLES["booking"], "messages_in": (bookings or {}).get("sources", {}).get("online", 0),
            "prev_in": None, "delta": None, "writers": 0, "bot_out": 0, "owner_out": 0,
            "new_clients": first.get("booking", 0), "median_reply_sec": None, "unanswered": 0, "waiting": 0, "threads": 0,
        })

    total_in = _sum(series["inbound"])
    prev_in_total = _sum(prev["inbound"])
    calls_total = ct.get("in", 0) + ct.get("out", 0)
    rep_all = replies.get("all") or {}

    # ── KPI: набор зависит от того, что есть у владельца ──
    kpis: List[Dict[str, Any]] = []
    if has_bot:
        kpis.append(_kpi("inbound", "Обращения", int(total_in), int(prev_in_total), spark=series["inbound"],
                         sub=f"от {writers.get('all', 0) + (calls or {}).get('unique_callers', 0)} клиентов", section="channels"))
        kpis.append(_kpi("new_clients", "Новые клиенты", (clients or {}).get("total", 0), (clients or {}).get("prev_total", 0),
                         spark=series["new_clients"], sub=_first_channel_sub(first), section="clients"))
    kpis.append(_kpi("calls", "Звонки", calls_total, ct.get("prev_total", 0), spark=series["calls"],
                     sub=f"{ct.get('answered', 0)} с разговором, {round(ct.get('talk_sec', 0) / 60)} мин", section="calls"))
    if has_bot and rep_all.get("median_sec") is not None:
        kpis.append(_kpi("reply", "Время ответа", rep_all.get("median_sec"), None, good="down", fmt="sec",
                         sub=(f"без ответа {rep_all.get('unanswered', 0)}" if rep_all.get("unanswered")
                              else "все обращения с ответом"), section="bot"))
    if bookings:
        kpis.append(_kpi("visits", "Записи", bookings["total"], bookings["prev_total"], spark=series.get("visits"),
                         sub=f"{bookings['arrived']} пришли, {bookings['no_show']} не пришли", section="bookings"))
        kpis.append(_kpi("revenue", "Выручка по записям", bookings["revenue"], bookings["prev_revenue"], fmt="som",
                         sub=(f"ещё {_money(bookings['expected_revenue'])} в записях" if bookings.get("expected_revenue")
                              else (f"средний чек {_money(bookings['avg_check'])}" if bookings.get("avg_check") else "")),
                         section="bookings"))
    if orders:
        kpis.append(_kpi("orders", "Заказы", orders["total"], orders["prev_total"], spark=series.get("orders"),
                         sub=(f"{orders['done']} выдано" + (f", {orders['by_bot']} оформил бот" if orders["by_bot"] else "")),
                         section="orders"))
        kpis.append(_kpi("order_revenue", "Выручка по заказам", orders["revenue"], orders["prev_revenue"], fmt="som",
                         sub=(f"средний чек {_money(orders['avg_check'])}" if orders.get("avg_check") else "заказов пока нет"),
                         section="orders"))
    if finance and (finance.get("expenses_count") or finance.get("payroll_total")):
        kpis.append(_kpi("profit", "Прибыль", finance["profit"], finance["prev_profit"], fmt="som",
                         sub=f"выручка {_money(finance['revenue'])} − расходы {_money(finance['expenses'])}",
                         section="finance"))
    if has_bot and funnel and modules.get("crm", True) and not bookings and not orders:
        co = (clients or {}).get("cohort") or {}
        kpis.append(_kpi("won", "Купили", co.get("won", 0), None,
                         sub=(f"{_pct(co.get('won', 0), co.get('new', 0))}% новых клиентов" if co.get("new") else "из новых клиентов"),
                         section="clients"))
        if funnel.get("pipeline_amount"):
            kpis.append(_kpi("pipeline", "В работе на сумму", funnel["pipeline_amount"], None, fmt="som",
                             sub=f"выиграно {_money(funnel['won_amount'])}", section="clients"))
    if dialogs is not None:
        kpis.append(_kpi("dialogs", "Разговоры ассистентов", dialogs["total"], None, spark=dialogs["series"],
                         sub="на сайте и по телефону", section="calls"))
    if money and not has_bot:
        kpis.append(_kpi("spent", "Потрачено на минуты", money["wallet_spent"], None, fmt="som",
                         sub=f"{money['paid_minutes']} мин разговора", section="money"))

    if bookings or orders:
        # Салон, магазин, доставка: сначала деньги и записи/заказы, потом обращения
        _prio = ["revenue", "order_revenue", "profit", "visits", "orders", "new_clients", "inbound", "reply", "calls"]
        kpis.sort(key=lambda k: _prio.index(k["id"]) if k.get("id") in _prio else 99)
    rep: Dict[str, Any] = {
        "period": {
            "key": rng.key, "title": PERIODS.get(rng.key), "label": rng.label, "from": rng.a.isoformat(),
            "to": rng.b.isoformat(), "days": rng.days, "prev_from": rng.prev_a.isoformat(), "prev_to": rng.prev_b.isoformat(),
            "compare": rng.prev_label, "granularity": gran, "granularities": granularities(rng.days),
            "today": tday.isoformat(),
        },
        "agent_mode": mode,
        "has_bot": has_bot,
        "modules": {"crm": bool(modules.get("crm", True)), "booking": bool(bookings) or bool(modules.get("booking")),
                    "orders": bool(modules.get("orders"))},
        "niche": {"key": biz.get("niche"), "title": biz.get("niche_title"), "group": biz.get("group"),
                  "catalog_title": ((biz.get("catalog") or {}).get("title"))},
        "buckets": [b.to_dict(gran, tday, now_hour) for b in buckets],
        "kpis": kpis[:6],
        "series": series,
        "prev": prev,
        "channels": channels,
        "ads": (msgs or {}).get("ads") or {"clients": 0, "messages": 0},
        "replies": replies,
        "calls": {
            "in": ct.get("in", 0), "out": ct.get("out", 0), "answered": ct.get("answered", 0),
            "missed": ct.get("missed", 0), "answered_in": ct.get("answered_in", 0), "out_answered": ct.get("out_answered", 0),
            "answer_rate": _pct(ct.get("answered_in", 0), ct.get("in", 0)),
            "out_answer_rate": _pct(ct.get("out_answered", 0), ct.get("out", 0)),
            "talk_min": round(ct.get("talk_sec", 0) / 60, 1),
            "avg_sec": int(ct.get("talk_sec", 0) / ct["answered"]) if ct.get("answered") else 0,
            "prev_total": ct.get("prev_total", 0), "unique_callers": (calls or {}).get("unique_callers", 0),
            "by_hour": (calls or {}).get("by_hour") or _zeros(24),
            "outcomes": [{"key": k, "title": OUTCOME_TITLES.get(k, "Другое"), "count": v}
                         for k, v in sorted(outcomes.items(), key=lambda kv: list(OUTCOME_TITLES).index(kv[0])
                                            if kv[0] in OUTCOME_TITLES else 9)],
        },
        "clients": {"total": (clients or {}).get("total", 0), "prev_total": (clients or {}).get("prev_total", 0),
                    "first_channel": [{"key": k, "title": CHANNEL_TITLES.get(k, k), "count": v}
                                      for k, v in sorted(first.items(), key=lambda kv: -kv[1])],
                    "cohort": (clients or {}).get("cohort") or {"new": 0, "won": 0, "lost": 0, "open": 0, "won_amount": 0}},
        "funnel": funnel if "funnel" in want else None,
        "heatmap": heat,
        "bookings": bookings,
        "orders": ({k: v for k, v in orders.items() if k not in ("series", "prev_orders")} if orders else None),
        "catalog": catalog,
        "money": money,
        "finance": ({k: v for k, v in finance.items() if k not in ("items", "categories_all")} if finance else None),
        "bot": {
            "replies": int(_sum(series["bot_out"])), "owner_replies": int(_sum(series["owner_out"])),
            "bot_share": _pct(_sum(series["bot_out"]), _sum(series["bot_out"]) + _sum(series["owner_out"])),
            "writers": writers.get("all", 0),
            "bot_only_clients": (msgs or {}).get("bot_only", 0),
            "median_bot_sec": rep_all.get("median_bot_sec"), "median_owner_sec": rep_all.get("median_owner_sec"),
            "unanswered": rep_all.get("unanswered", 0), "waiting": rep_all.get("waiting", 0),
            "gaps": gaps, "paused": (funnel or {}).get("paused", 0), "spam_blocked": (funnel or {}).get("spam_blocked", 0),
        } if has_bot else None,
        "generated_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
    }
    rep["insights"] = build_insights(rep)
    return rep


def _money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{int(round(v)):,}".replace(",", " ") + " сом"


def _first_channel_sub(first: Dict[str, int]) -> str:
    if not first:
        return "за период"
    k, v = max(first.items(), key=lambda kv: kv[1])
    return f"чаще всего — {CHANNEL_TITLES.get(k, k)}"


def cached_report(db: Session, user, **kw) -> Dict[str, Any]:
    key = (str(user.id), kw.get("period"), kw.get("date_from"), kw.get("date_to"), kw.get("granularity"),
           tuple(sorted(kw.get("sections") or ())), today_bk().isoformat())
    nowt = _time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and nowt - hit[0] < CACHE_TTL:
            return hit[1]
    rep = build_report(db, user, **kw)
    with _CACHE_LOCK:
        if len(_CACHE) > 400:
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:100]:
                _CACHE.pop(k, None)
        _CACHE[key] = (nowt, rep)
    return rep


def invalidate(user_id) -> None:
    with _CACHE_LOCK:
        for k in [k for k in _CACHE if k[0] == str(user_id)]:
            _CACHE.pop(k, None)


# ───────────────────────────── CSV ─────────────────────────────

CSV_COLUMNS = [
    ("inbound", "Обращения"), ("whatsapp_in", "WhatsApp, сообщений"), ("telegram_in", "Telegram, сообщений"),
    ("instagram_in", "Instagram, сообщений"), ("calls_in", "Входящие звонки"), ("calls_out", "Исходящие звонки"),
    ("answered", "С разговором"), ("missed", "Пропущенные"), ("talk_min", "Минут разговора"),
    ("new_clients", "Новые клиенты"), ("bot_out", "Ответов бота"), ("owner_out", "Ваших ответов"),
    ("visits", "Записи"), ("arrived", "Пришли"), ("no_show", "Не пришли"), ("dialogs", "Разговоры ассистентов"),
]


def report_csv(rep: Dict[str, Any]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    cols = [(k, t) for k, t in CSV_COLUMNS if k in rep["series"]]
    w.writerow(["Период", "С", "По"] + [t for _, t in cols])
    for i, b in enumerate(rep["buckets"]):
        w.writerow([b["title"], b["from"], b["to"]] + [rep["series"][k][i] for k, _ in cols])
    w.writerow([])
    w.writerow(["Итого", rep["period"]["from"], rep["period"]["to"]] +
               [round(sum(rep["series"][k]), 1) for k, _ in cols])
    return "﻿" + buf.getvalue()   # BOM: Excel открывает кириллицу без «кракозябр»
