"""
Журнал действий кабинета (Voksy Beauty, шаг 1.7): кто что сделал — одна функция log() на все входы.

Пишут сервисы (не страницы), поэтому строку оставляет любой вход: кабинет владельца, сотрудник,
мастер из своего кабинета, бот в переписке, голосовой агент на звонке, клиент по ссылке и в чате на
сайте, ИИ-управляющий. Строка добавляется в ТУ ЖЕ транзакцию, что и само действие: откатилось действие
(в т.ч. пробная запись бота в два шага — db.begin_nested() → rollback) — исчезла и строка.

Кто сделал (actor_for):
  • явное ACTOR (контекст acting(...)) — фоновые задачи, ИИ-управляющий;
  • source действия: bot → ИИ-администратор, call → голосовой агент, online → клиент (по ссылке);
  • вход админа в кабинет клиента (user._impersonator_id) → «Поддержка Voksy»;
  • сотрудник (user._staff_member_id, имя из снимка team_service) → его имя;
  • иначе владелец кабинета.
IP — из REQUEST_IP (ставит middleware app.staff_access_guard для каждого /api/ запроса).

Хранится ACTIVITY_KEEP_DAYS (180) дней: cleanup() раз в час из weekly_jobs._tick, порциями.
Читает query() → GET /api/beauty/activity (+ входы админа из admin_audit_log в той же ленте).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ACTIVITY_KEEP_DAYS = int(os.getenv("ACTIVITY_KEEP_DAYS", "180") or 180)
CLEANUP_EVERY_SEC = 3600
CLEANUP_BATCH = 5000
BISHKEK = timezone(timedelta(hours=6))
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

ACTOR: ContextVar[Optional[Dict[str, Any]]] = ContextVar("voksy_activity_actor", default=None)
REQUEST_IP: ContextVar[Optional[str]] = ContextVar("voksy_request_ip", default=None)

ACTOR_TITLES = {
    "owner": "Владелец", "staff": "Сотрудник", "admin": "Поддержка Voksy", "ai": "ИИ-администратор",
    "call": "Голосовой агент", "client": "Клиент", "system": "Система",
}

# Вид действия для фильтра ленты: группа → типы объектов
GROUPS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "bookings": ("Записи", ("booking", "visit")),
    "schedule": ("График и мастера", ("block", "master", "room", "settings")),
    "services": ("Услуги и цены", ("service", "category")),
    "page": ("Страница салона", ("page",)),
    "team": ("Сотрудники", ("team",)),
    "money": ("Деньги", ("expense", "payroll")),
    "ai": ("ИИ-управляющий", ("manager",)),
    "automation": ("Автоматизации", ("automation",)),
    "reviews": ("Отзывы", ("review",)),
    "admin": ("Входы поддержки", ("admin",)),
}

ACTION_TITLES = {
    "booking.create": "Новая запись", "booking.move": "Перенос записи", "booking.resize": "Длительность записи",
    "booking.status": "Статус записи", "booking.cancel": "Отмена записи", "booking.prepaid": "Предоплата",
    "booking.price": "Цена записи", "booking.service": "Услуга в записи", "booking.note": "Заметка к записи",
    "visit.create": "Новый визит", "visit.cancel": "Отмена визита",
    "block.create": "Блок времени", "block.delete": "Блок снят",
    "service.create": "Новая услуга", "service.update": "Услуга изменена", "service.delete": "Услуга удалена",
    "service.disable": "Услуга выключена", "category.rename": "Категория переименована",
    "category.order": "Порядок категорий",
    "master.create": "Новый мастер", "master.update": "Мастер изменён", "master.delete": "Мастер удалён",
    "master.login": "Вход мастера",
    "room.create": "Новый кабинет", "room.update": "Кабинет изменён", "room.delete": "Кабинет удалён",
    "settings.update": "Правила записи",
    "page.update": "Страница салона", "page.media": "Фото страницы", "chat.update": "Чат на сайте",
    "team.create": "Новый сотрудник", "team.update": "Сотрудник изменён", "team.password": "Пароль сотрудника",
    "team.deactivate": "Сотрудник выключен", "team.activate": "Сотрудник включён", "team.delete": "Сотрудник удалён",
    "expense.create": "Новый расход", "expense.update": "Расход изменён", "expense.delete": "Расход удалён",
    "payroll.update": "Оплата мастера",
    "manager.approve": "Одобрено ИИ-управляющему", "manager.reject": "Отклонено ИИ-управляющему",
    "manager.settings": "Настройки ИИ-управляющего", "manager.offer": "Предложить окна клиентам",
    "admin.impersonate": "Вход поддержки в кабинет",
    "review.publish": "Отзыв на странице", "review.hide": "Отзыв скрыт", "review.reply": "Ответ на отзыв",
    "review.settings": "Отзывы на странице",
}


# ───────────────────────────── кто сделал ─────────────────────────────

def _actor(kind: str, name: Optional[str] = None, actor_id: Optional[str] = None) -> Dict[str, Any]:
    return {"kind": kind, "id": (str(actor_id)[:64] if actor_id else None),
            "name": (str(name).strip()[:120] if name else None) or ACTOR_TITLES.get(kind, kind)}


@contextmanager
def acting(kind: str, name: Optional[str] = None, actor_id: Optional[str] = None):
    """Всё, что записано в журнал внутри блока, — от имени этого «кто» (фоновые задачи, ИИ-управляющий)."""
    token = ACTOR.set(_actor(kind, name, actor_id))
    try:
        yield
    finally:
        ACTOR.reset(token)


def owner_name(user) -> str:
    name = " ".join(x for x in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if x)
    return name.strip() or "Владелец"


def actor_for(user=None, source: Optional[str] = None, client_name: Optional[str] = None) -> Dict[str, Any]:
    explicit = ACTOR.get()
    if explicit:
        return dict(explicit)
    if source == "bot":
        return _actor("ai")
    if source == "call":
        return _actor("call")
    if source == "online":
        return _actor("client", client_name or "Клиент")
    if user is None:
        return _actor("system")
    imp = getattr(user, "_impersonator_id", None)
    if imp:
        return _actor("admin", None, imp)
    sid = getattr(user, "_staff_member_id", None)
    if sid:
        snap = getattr(user, "_staff", None) or {}
        return _actor("staff", snap.get("name") or "Сотрудник", sid)
    return _actor("owner", owner_name(user))


# ───────────────────────────── форматирование ─────────────────────────────

def _aw(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def when(dt: Optional[datetime]) -> str:
    """«ср 26.11 11:00» по Бишкеку."""
    if dt is None:
        return "—"
    loc = _aw(dt).astimezone(BISHKEK)
    return f"{WEEKDAYS[loc.weekday()]} {loc.strftime('%d.%m %H:%M')}"


def hm(dt: Optional[datetime]) -> str:
    return _aw(dt).astimezone(BISHKEK).strftime("%H:%M") if dt else "—"


def money(v) -> str:
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f"{f:,.0f}".replace(",", " ")
    return f"{s} сом"


def _plain(v):
    """Значение для JSON (было → стало): даты — ISO, UUID — строкой."""
    if isinstance(v, datetime):
        return _aw(v).isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    return v


def diff(before: Dict[str, Any], after: Dict[str, Any], keys: Optional[Iterable[str]] = None) -> Dict[str, List[Any]]:
    """{поле: [было, стало]} только по изменившимся полям."""
    out: Dict[str, List[Any]] = {}
    for k in (keys if keys is not None else sorted(set(before) | set(after))):
        a, b = _plain(before.get(k)), _plain(after.get(k))
        if a != b:
            out[k] = [a, b]
    return out


# ───────────────────────────── запись ─────────────────────────────

def log(db: Session, owner_id, action: str, object_type: str, summary: str, *, object_id=None,
        changes: Optional[Dict[str, Any]] = None, meta: Optional[Dict[str, Any]] = None, user=None,
        source: Optional[str] = None, client_name: Optional[str] = None,
        actor: Optional[Dict[str, Any]] = None) -> None:
    """Строка журнала в текущую транзакцию. Сбой журнала не мешает самому действию (только WARNING)."""
    if not owner_id or not summary:
        return
    try:
        from backend.models.activity import ActivityLog
        a = actor or actor_for(user, source, client_name)
        db.add(ActivityLog(
            id=uuid.uuid4(), user_id=owner_id if isinstance(owner_id, uuid.UUID) else uuid.UUID(str(owner_id)),
            created_at=datetime.now(timezone.utc),     # время самого действия, а не начала транзакции (now() в базе)
            actor_kind=a["kind"][:16], actor_id=a.get("id"), actor_name=(a.get("name") or "")[:120] or None,
            action=action[:40], object_type=object_type[:24],
            object_id=(str(object_id)[:64] if object_id else None), summary=str(summary)[:1000],
            changes=_plain(changes) if changes else None, meta=_plain(meta) if meta else None,
            ip=(REQUEST_IP.get() or None) and str(REQUEST_IP.get())[:64]))
    except Exception as e:
        logger.warning(f"[ACTIVITY] {action}: {e}")


# ───────────────────────────── чтение ─────────────────────────────

PERIODS = {"today": 0, "week": 7, "month": 30, "all": None}


def _period_start(period: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    now = now or datetime.now(timezone.utc)
    if period == "today":
        loc = now.astimezone(BISHKEK)
        return datetime(loc.year, loc.month, loc.day, tzinfo=BISHKEK).astimezone(timezone.utc)
    days = PERIODS.get(period or "all")
    return now - timedelta(days=days) if days else None


def _iso(dt) -> Optional[str]:
    return _aw(dt).isoformat() if dt else None


def item_to_dict(r, show_ip: bool = False) -> Dict[str, Any]:
    kind = r["actor_kind"]
    return {
        "id": str(r["id"]), "at": _iso(r["created_at"]), "time": hm(r["created_at"]),
        "actor": {"kind": kind, "id": r["actor_id"], "name": r["actor_name"] or ACTOR_TITLES.get(kind, kind),
                  "title": ACTOR_TITLES.get(kind, kind)},
        "action": r["action"], "action_title": ACTION_TITLES.get(r["action"], r["action"]),
        "object_type": r["object_type"], "object_id": r["object_id"], "summary": r["summary"],
        "changes": r["changes"] or None, "meta": r["meta"] or None,
        **({"ip": r["ip"]} if show_ip and r.get("ip") else {}),
    }


def _cursor(r) -> str:
    return f"{_iso(r['created_at'])}|{r['id']}"


def _parse_cursor(before: Optional[str]) -> Tuple[Optional[datetime], Optional[str]]:
    """«ISO|id» → (время, id). Время одно у нескольких строк — id различает (страницы без пропусков и повторов)."""
    if not before:
        return None, None
    ts, _, rid = str(before).partition("|")
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00").replace(" ", "+")), (str(uuid.UUID(rid)) if rid else None)
    except ValueError:
        return None, None


def _admin_rows(db: Session, owner_id, start: Optional[datetime], before: Optional[datetime], limit: int,
                before_id: Optional[str] = None):
    sql = ("SELECT l.id, l.created_at, l.admin_user_id, l.ip, coalesce(nullif(a.first_name, ''), 'Поддержка Voksy') AS name "
           "FROM admin_audit_log l LEFT JOIN users a ON a.id = l.admin_user_id "
           "WHERE l.target_user_id = :uid AND l.action = 'impersonate'")
    p: Dict[str, Any] = {"uid": str(owner_id), "lim": limit}
    if start is not None:
        sql += " AND l.created_at >= :st"
        p["st"] = start
    if before is not None:
        if before_id:
            sql += " AND (l.created_at, l.id) < (:bf, CAST(:bid AS uuid))"
            p["bid"] = before_id
        else:
            sql += " AND l.created_at < :bf"
        p["bf"] = before
    sql += " ORDER BY l.created_at DESC, l.id DESC LIMIT :lim"
    out = []
    try:
        with db.begin_nested():
            rows = db.execute(text(sql), p).mappings().all()
    except Exception as e:
        logger.warning(f"[ACTIVITY] admin rows: {e}")
        return []
    for r in rows:
        out.append({"id": r["id"], "created_at": r["created_at"], "actor_kind": "admin",
                    "actor_id": str(r["admin_user_id"]) if r["admin_user_id"] else None,
                    "actor_name": "Поддержка Voksy", "action": "admin.impersonate", "object_type": "admin",
                    "object_id": None, "summary": "вход поддержки Voksy в кабинет (по кнопке в админке, на 2 часа)",
                    "changes": None, "meta": None, "ip": r["ip"]})
    return out


def query(db: Session, owner_id, *, period: Optional[str] = "week", actor: Optional[str] = None,
          group: Optional[str] = None, object_type: Optional[str] = None, object_id: Optional[str] = None,
          visit_id: Optional[str] = None, before: Optional[str] = None, limit: int = 50,
          show_ip: bool = False) -> Dict[str, Any]:
    """Лента журнала: новые сверху, постранично (next = время последней строки → ?before=)."""
    limit = max(1, min(int(limit or 50), 200))
    start = _period_start(period)
    bf, bid = _parse_cursor(before)
    where = ["user_id = :uid"]
    p: Dict[str, Any] = {"uid": str(owner_id), "lim": limit + 1}
    if start is not None:
        where.append("created_at >= :st")
        p["st"] = start
    if bf is not None:
        if bid:
            where.append("(created_at, id) < (:bf, CAST(:bid AS uuid))")
            p["bid"] = bid
        else:
            where.append("created_at < :bf")
        p["bf"] = bf
    types: Optional[Tuple[str, ...]] = None
    if group and group in GROUPS:
        types = GROUPS[group][1]
    if object_type:
        types = (object_type,)
    if object_id:
        # история одной записи: сама запись + её визит (создание/отмена визита целиком)
        if visit_id:
            where.append("((object_type = 'booking' AND object_id = :oid) OR (object_type = 'visit' AND object_id = :vid))")
            p.update(oid=str(object_id), vid=str(visit_id))
        else:
            where.append("object_id = :oid")
            p["oid"] = str(object_id)
        if object_type and not visit_id:
            where.append("object_type = :ot")
            p["ot"] = object_type
    elif types is not None:
        where.append("object_type = ANY(:types)")
        p["types"] = list(types)
    want_admin = (not object_id) and (group in (None, "", "admin")) and (not object_type or object_type == "admin")
    if actor:
        if actor.startswith("staff:"):
            where.append("actor_kind = 'staff' AND actor_id = :aid")
            p["aid"] = actor[6:]
            want_admin = False
        elif actor in ACTOR_TITLES:
            where.append("actor_kind = :ak")
            p["ak"] = actor
            want_admin = want_admin and actor == "admin"
    rows: List[Dict[str, Any]] = []
    if group != "admin":
        rows = [dict(r) for r in db.execute(text(
            "SELECT id, created_at, actor_kind, actor_id, actor_name, action, object_type, object_id, summary, "
            f"changes, meta, ip FROM activity_log WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT :lim"),
            p).mappings().all()]
    if want_admin:
        rows += _admin_rows(db, owner_id, start, bf, limit + 1, bid)
        rows.sort(key=lambda r: (_aw(r["created_at"]), str(r["id"])), reverse=True)
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [item_to_dict(r, show_ip) for r in rows],
            "next": _cursor(rows[-1]) if more and rows else None}


def people(db: Session, owner_id, days: int = ACTIVITY_KEEP_DAYS) -> List[Dict[str, Any]]:
    """Кто есть в журнале (для фильтра «люди»): владелец, сотрудники по именам, ИИ, звонок, клиенты."""
    rows = db.execute(text("""
        SELECT actor_kind, CASE WHEN actor_kind = 'staff' THEN actor_id END AS aid,
               (array_agg(actor_name ORDER BY created_at DESC))[1] AS name, count(*) AS n
        FROM activity_log WHERE user_id = :uid AND created_at > now() - make_interval(days => :d)
        GROUP BY 1, 2 ORDER BY count(*) DESC LIMIT 40
    """), {"uid": str(owner_id), "d": int(days)}).mappings().all()
    out = []
    for r in rows:
        kind = r["actor_kind"]
        key = f"staff:{r['aid']}" if kind == "staff" and r["aid"] else kind
        name = r["name"] if kind in ("staff", "owner") else ACTOR_TITLES.get(kind, kind)
        out.append({"key": key, "kind": kind, "name": name or ACTOR_TITLES.get(kind, kind), "count": int(r["n"])})
    return out


def groups() -> List[Dict[str, str]]:
    return [{"key": k, "title": v[0]} for k, v in GROUPS.items()]


# ───────────────────────────── чистка ─────────────────────────────

_last_cleanup = 0.0


def cleanup(db: Session, keep_days: int = ACTIVITY_KEEP_DAYS, force: bool = False) -> int:
    """Удалить строки старше keep_days (порциями, не чаще раза в час на воркер). Возвращает сколько удалено."""
    global _last_cleanup
    if not force and time.monotonic() - _last_cleanup < CLEANUP_EVERY_SEC:
        return 0
    _last_cleanup = time.monotonic()
    total = 0
    try:
        for _ in range(20):
            n = db.execute(text(
                "DELETE FROM activity_log WHERE id IN (SELECT id FROM activity_log "
                "WHERE created_at < now() - make_interval(days => :d) LIMIT :b)"),
                {"d": int(keep_days), "b": CLEANUP_BATCH}).rowcount or 0
            db.commit()
            total += n
            if n < CLEANUP_BATCH:
                break
    except Exception as e:
        db.rollback()
        logger.warning(f"[ACTIVITY] cleanup: {e}")
    if total:
        logger.info(f"[ACTIVITY] cleanup: удалено {total} строк старше {keep_days} дн.")
    return total
