"""
Сотрудники кабинета (ветка 1811-niche-admin-speed): права, проверка запросов, вход, оплата мест.

Как это устроено:
  * Владелец добавляет сотрудника (имя, email, пароль, разделы). Место стоит TEAM_SEAT_SOM сом за
    TEAM_SEAT_DAYS дней и списывается с кошелька владельца сразу; дальше — фоновый цикл
    start_team_seats_loop продлевает. Не хватило денег / закончился тариф владельца — сотрудник
    выключается и владелец получает сообщение в Telegram-бот агента.
  * Сотрудник входит на той же форме (api/auth.py → authenticate). Токен — данные владельца
    (sub = id владельца) + staff = id сотрудника + stv = версия токена.
  * Каждый запрос с токеном сотрудника проходит middleware app.staff_access_guard: ЗАПРЕЩЕНО ВСЁ,
    чего нет в STAFF_RULES (новый эндпоинт сотруднику закрыт, пока его сюда не добавят). Снимок
    сотрудника (активен ли, версия, права) кэшируется на SNAPSHOT_TTL секунд в воркере.
  * Внутри разрешённых эндпоинтов — точечные запреты: в каталоге сотрудник меняет только позиции
    (не «вопрос-ответ» и не инструкции), в аналитике не видит расходов, /api/users/me не отдаёт ключи.

Voksy Beauty (ветка 2511-beauty): у салона на Beauty-тарифе места ВХОДЯТ в тариф — бесплатно, но не
больше limits.staff тарифа (Beauty Start 3, AI 10, Pro 20; на пробном — лимит тарифа с limits.trial).
Срок места = срок тарифа: цикл продления переносит его на новый срок после оплаты тарифа; тариф
кончился — сотрудник выключается («тариф»), тариф понизили — выключаются самые новые сверх лимита
(«лимит»). Условия — plan_catalog.staff_terms, проверка — pay_seat под блокировкой строки владельца.

Разделы (PERMISSIONS): inbox — чаты, crm — клиенты/задачи/звонки агента, booking — записи,
booking_setup — мастера, график и правила записи, booking_own — свой журнал мастера (Voksy Beauty:
только свои записи, клиенты и заработок, /static/master.html), catalog — каталог, analytics —
аналитика, finance — финансы (выручка, расходы, зарплаты мастеров; бухгалтер и управляющий).
Кошелёк, тарифы, настройки бота, каналы, телефония, голосовой помощник — только владелец.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.core.logging import get_logger

logger = get_logger(__name__)

TEAM_SEAT_SOM = int(os.getenv("TEAM_SEAT_SOM", "150") or 150)
TEAM_SEAT_DAYS = int(os.getenv("TEAM_SEAT_DAYS", "30") or 30)
TEAM_MAX_MEMBERS = int(os.getenv("TEAM_MAX_MEMBERS", "10") or 10)
SNAPSHOT_TTL = 15.0                 # с: смена прав/выключение доходит до других воркеров не дольше
SEEN_EVERY = timedelta(minutes=5)   # как часто обновлять «был в сети»
RENEW_TICK_SECONDS = 1800
MIN_PASSWORD = 6

# ───────────────────────────── разделы ─────────────────────────────

PERMISSIONS: List[Dict[str, str]] = [
    {"key": "inbox", "title": "Чаты с клиентами", "icon": "inbox",
     "hint": "WhatsApp, Telegram и Instagram: читать, отвечать, ставить бота на паузу"},
    {"key": "crm", "title": "Клиенты", "icon": "users",
     "hint": "Карточки клиентов, этапы, задачи, звонки и их записи"},
    {"key": "booking", "title": "Онлайн-запись", "icon": "calendar",
     "hint": "Календарь: записать, перенести, отменить, отметить визит"},
    {"key": "booking_setup", "title": "Мастера и график", "icon": "clock",
     "hint": "Мастера, график работы и правила записи"},
    {"key": "booking_own", "title": "Свой журнал (мастер)", "icon": "user-round",
     "hint": "Только свои записи, клиенты, заработок и статистика — кабинет мастера на телефоне"},
    {"key": "orders", "title": "Заказы", "icon": "shopping-cart",
     "hint": "Заказы клиентов: оформить, собрать, отправить, отметить выдачу"},
    {"key": "catalog", "title": "Каталог", "icon": "package",
     "hint": "Позиции каталога: цены, наличие, фото"},
    {"key": "analytics", "title": "Аналитика", "icon": "chart-column",
     "hint": "Отчёты по обращениям, клиентам и записям — без расходов"},
    {"key": "finance", "title": "Финансы", "icon": "banknote",
     "hint": "Выручка, расходы, зарплаты мастеров и прибыль — для бухгалтера и управляющего"},
]
PERM_KEYS = tuple(p["key"] for p in PERMISSIONS)
BOOKING_PERMS = ("booking", "booking_setup")
BOOKING_MODULE_PERMS = ("booking", "booking_setup", "booking_own")   # видны, когда включена запись

# Страница кабинета раздела: меню и нижние вкладки сотрудника, «домашняя» страница после входа
SECTION_PAGES = [
    ("inbox", "/static/inbox.html", "inbox", "Чаты", "Чаттар"),
    ("booking", "/static/booking.html", "calendar", "Записи", "Жазуулар"),
    ("booking_own", "/static/master.html", "user-round", "Мой день", "Менин күнүм"),
    ("orders", "/static/orders.html", "shopping-cart", "Заказы", "Заказдар"),
    ("crm", "/static/start-crm.html", "users", "Клиенты", "Кардарлар"),
    ("catalog", "/static/catalog.html", "package", "Каталог", "Каталог"),
    ("analytics", "/static/analytics.html", "chart-column", "Аналитика", "Аналитика"),
    ("finance", "/static/finance.html", "banknote", "Финансы", "Каржы"),
]


def _rule(methods: str, pattern: str, *perms: str):
    return frozenset(methods.split()), re.compile(pattern), frozenset(perms)


# Что можно сотруднику. Пустой набор разделов — любому сотруднику. ВСЁ ОСТАЛЬНОЕ ЗАПРЕЩЕНО.
STAFF_RULES = [
    # — всегда: профиль, ниша, бот для выбора агента, свой пароль —
    _rule("GET", r"^/api/users/me/?$"),
    _rule("GET", r"^/api/business/profile/?$"),
    _rule("GET", r"^/api/agent/list/?$"),
    _rule("GET POST", r"^/api/team/me(/password)?/?$"),
    # — чаты —
    _rule("GET POST PUT DELETE", r"^/api/inbox(/.*)?$", "inbox"),
    # — клиенты (CRM): карточки, этапы, задачи, звонки агента —
    _rule("GET POST", r"^/api/agent/contacts/?$", "crm"),
    _rule("GET", r"^/api/agent/contacts/?$", "inbox", "booking", "booking_setup", "orders"),   # поиск клиента
    _rule("GET PUT", r"^/api/agent/contacts/[^/]+/?$", "crm", "inbox"),
    _rule("PATCH", r"^/api/agent/contacts/[^/]+/status/?$", "crm", "inbox"),
    _rule("POST DELETE", r"^/api/agent/contacts/[^/]+/ai-pause/?$", "crm", "inbox"),
    _rule("POST", r"^/api/agent/contacts/[^/]+/tasks/?$", "crm"),
    _rule("GET", r"^/api/agent/pipeline/stages/?$"),                        # названия этапов — любому
    _rule("GET", r"^/api/agent/tasks/?$", "crm"),
    _rule("PUT DELETE", r"^/api/agent/tasks/[^/]+/?$", "crm", "inbox"),
    _rule("GET", r"^/api/agent/calls(/[^/]+)?/?$", "crm"),
    _rule("GET", r"^/api/agent/stats/?$", "crm"),
    # — файлы библиотеки: отправить клиенту из чата, фото позиций каталога —
    _rule("GET", r"^/api/agent/files/?$", "inbox", "catalog"),
    _rule("GET", r"^/api/agent/files/[^/]+(/link)?/?$", "inbox", "catalog", "crm"),
    _rule("POST", r"^/api/agent/files/?$", "catalog"),
    # — каталог (вид записи проверяет сам эндпоинт: сотрудник меняет только позиции каталога) —
    _rule("GET POST", r"^/api/agent/knowledge/?$", "catalog"),
    _rule("PUT DELETE", r"^/api/agent/knowledge/[^/]+/?$", "catalog"),
    _rule("POST", r"^/api/agent/knowledge/(bulk|products/batch|collections/set|collections/rename)/?$", "catalog"),
    _rule("DELETE", r"^/api/agent/knowledge/gaps/[^/]+/?$", "catalog"),
    # — онлайн-запись —
    _rule("GET", r"^/api/booking/(overview|slots|resources)/?$", "booking", "booking_setup"),
    _rule("POST", r"^/api/booking/bookings/?$", "booking", "booking_setup"),
    _rule("PATCH DELETE", r"^/api/booking/bookings/[^/]+/?$", "booking", "booking_setup"),
    _rule("POST", r"^/api/booking/resources/?$", "booking_setup"),
    _rule("PATCH DELETE", r"^/api/booking/resources/[^/]+/?$", "booking_setup"),
    _rule("PUT", r"^/api/booking/settings/?$", "booking_setup"),
    _rule("GET", r"^/api/booking/link-kit/?$", "booking_setup"),
    # — заказы (настройки заказов — только владелец: api/orders.py → ensure_owner) —
    _rule("GET POST", r"^/api/orders/?$", "orders"),
    _rule("GET", r"^/api/orders/(products|summary|settings)/?$", "orders"),
    _rule("GET PATCH", r"^/api/orders/[^/]+/?$", "orders"),
    _rule("POST", r"^/api/orders/[^/]+/status/?$", "orders"),
    # — услуги салона (над каталогом): смотреть — журнал и каталог, менять — «Мастера и график» и каталог —
    _rule("GET", r"^/api/booking/services/?$", "booking", "booking_setup", "catalog"),
    _rule("POST", r"^/api/booking/services(/categories/(rename|order))?/?$", "booking_setup", "catalog"),
    _rule("PATCH DELETE", r"^/api/booking/services/[^/]+/?$", "booking_setup", "catalog"),
    # — кабинет мастера: только своё (какой мастер — по привязке входа, api/booking.py → _me_resource) —
    _rule("GET", r"^/api/booking/me(/clients|/slots)?/?$", "booking_own"),
    _rule("POST", r"^/api/booking/me/(bookings|media)/?$", "booking_own"),
    _rule("PATCH", r"^/api/booking/me/(bookings/[^/]+|profile)/?$", "booking_own"),
    _rule("DELETE", r"^/api/booking/me/media/[^/]+/?$", "booking_own"),
    # — фото мастеров (портфолио, сертификаты) — у кого «Мастера и график» —
    _rule("POST", r"^/api/booking/resources/[^/]+/media/?$", "booking_setup"),
    _rule("DELETE", r"^/api/booking/resources/[^/]+/media/[^/]+/?$", "booking_setup"),
    # — аналитика (расходы сотруднику не отдаются — api/analytics.py) —
    _rule("GET", r"^/api/analytics/(report|export\.csv)/?$", "analytics"),
    # — финансы: бухгалтер и управляющий —
    _rule("GET", r"^/api/finance/(summary|expenses)/?$", "finance"),
    _rule("POST", r"^/api/finance/expenses/?$", "finance"),
    _rule("PATCH DELETE", r"^/api/finance/expenses/[^/]+/?$", "finance"),
    _rule("PATCH", r"^/api/finance/payroll/[^/]+/?$", "finance"),
]


def staff_allowed(perms: Iterable[str], method: str, path: str) -> bool:
    """Разрешён ли сотруднику с разделами perms запрос method path. Нет правила — нельзя."""
    have = frozenset(perms or ())
    m = (method or "GET").upper()
    if m in ("HEAD", "OPTIONS"):
        m = "GET"
    for methods, rx, need in STAFF_RULES:
        if m in methods and rx.match(path or ""):
            if not need or (have & need):
                return True
    return False


def clean_permissions(perms) -> List[str]:
    """Только известные разделы, в порядке PERMISSIONS, без повторов."""
    got = {str(p).strip() for p in (perms or []) if p}
    return [k for k in PERM_KEYS if k in got]


def sections(perms: Iterable[str], catalog_title: Optional[str] = None) -> List[Dict[str, str]]:
    """Разделы кабинета сотрудника по порядку: меню, вкладки телефона, первая страница после входа."""
    have = set(perms or ())
    out = []
    for key, href, icon, label, ky in SECTION_PAGES:
        ok = bool(have & set(BOOKING_PERMS)) if key == "booking" else key in have
        if not ok:
            continue
        if key == "catalog" and catalog_title:
            label = ky = catalog_title
        out.append({"id": key, "href": href, "icon": icon, "label": label, "ky": ky})
    return out


def home_page(perms: Iterable[str]) -> str:
    s = sections(perms)
    return s[0]["href"] if s else "/static/inbox.html"


# ───────────────────────────── снимок сотрудника (кэш) ─────────────────────────────

_CACHE: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_CACHE_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def invalidate(member_id=None) -> None:
    with _CACHE_LOCK:
        if member_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(str(member_id), None)


def _load_snapshot(member_id: str) -> Optional[Dict[str, Any]]:
    from backend.db.session import SessionLocal
    from backend.models.team import TeamMember
    from backend.models.user import User
    try:
        mid = uuid.UUID(str(member_id))
    except (ValueError, TypeError):
        return None
    db = SessionLocal()
    try:
        m = db.query(TeamMember).filter(TeamMember.id == mid).first()
        if m is None:
            return None
        owner_active = db.query(User.is_active).filter(User.id == m.owner_id).scalar()
        snap = {
            "id": str(m.id), "owner_id": str(m.owner_id), "name": m.name, "email": m.email,
            "role_title": m.role_title, "active": bool(m.is_active), "reason": m.status_reason,
            "version": int(m.token_version or 1), "permissions": clean_permissions(m.permissions),
            "owner_active": bool(owner_active),
        }
        now = _now()
        seen = _aware(m.last_seen_at)
        if m.is_active and (seen is None or now - seen > SEEN_EVERY):
            m.last_seen_at = now
            db.commit()
        return snap
    except Exception as e:
        db.rollback()
        logger.warning(f"[TEAM] snapshot {member_id}: {e}")
        raise
    finally:
        db.close()


def snapshot(member_id) -> Optional[Dict[str, Any]]:
    """Снимок сотрудника из кэша воркера (SNAPSHOT_TTL) или из базы."""
    key = str(member_id)
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    snap = _load_snapshot(key)
    with _CACHE_LOCK:
        _CACHE[key] = (now + SNAPSHOT_TTL, snap)
        if len(_CACHE) > 5000:
            _CACHE.clear()
    return snap


async def snapshot_async(member_id) -> Optional[Dict[str, Any]]:
    key = str(member_id)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    return await asyncio.to_thread(snapshot, key)


def session_error(snap: Optional[Dict[str, Any]], owner_id: str, version: int) -> Optional[str]:
    """Почему токен сотрудника больше не действует (None — действует)."""
    if snap is None or str(snap.get("owner_id")) != str(owner_id):
        return "staff_not_found"
    if not snap.get("owner_active"):
        return "owner_inactive"
    if not snap.get("active"):
        return "staff_disabled"
    if int(version or 0) != int(snap.get("version") or 0):
        return "staff_session_expired"
    return None


SESSION_MESSAGES = {
    "staff_not_found": "Учётная запись сотрудника удалена. Обратитесь к владельцу кабинета.",
    "owner_inactive": "Кабинет владельца отключён.",
    "staff_disabled": "Владелец выключил вам доступ.",
    "staff_session_expired": "Пароль изменён — войдите заново.",
}


def staff_of(user) -> Optional[str]:
    """id сотрудника, если запрос сделан сотрудником (иначе None)."""
    return getattr(user, "_staff_member_id", None)


# ───────────────────────────── вход ─────────────────────────────

DISABLED_MESSAGES = {
    "owner": "Владелец кабинета выключил вам доступ. Обратитесь к нему.",
    "unpaid": "Доступ приостановлен: место сотрудника не оплачено. Попросите владельца пополнить кошелёк.",
    "plan": "Доступ приостановлен: у владельца закончился тариф.",
}


def normalize_email(email) -> str:
    return str(email or "").strip().lower()


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def _email_login_ok(email: str) -> bool:
    """Тот же разбор email, что у формы входа (EmailStr): иначе сотрудника можно было бы завести,
    а войти он не смог бы (например, домены .test / .local форма входа не принимает)."""
    try:
        from pydantic import EmailStr, TypeAdapter
        TypeAdapter(EmailStr).validate_python(email)
        return True
    except Exception:
        return False


def find_member_by_email(db: Session, email):
    from backend.models.team import TeamMember
    key = normalize_email(email)
    if not key:
        return None
    return db.query(TeamMember).filter(TeamMember.email == key).first()


def authenticate(db: Session, email: str, password: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    Вход сотрудника. (сотрудник, None) — пустили; (None, код) — нет:
    invalid (нет такого или неверный пароль), owner_inactive, disabled:<причина>.
    """
    from backend.core.security import hash_password, needs_rehash, verify_password
    from backend.models.user import User
    m = find_member_by_email(db, email)
    if m is None or not verify_password(password or "", m.password_hash):
        return None, "invalid"
    owner = db.query(User).filter(User.id == m.owner_id).first()
    if owner is None or not owner.is_active:
        return None, "owner_inactive"
    if not m.is_active:
        return None, "disabled:" + (m.status_reason or "owner")
    try:
        if needs_rehash(m.password_hash):
            m.password_hash = hash_password(password)
    except Exception as e:
        logger.warning(f"[TEAM] rehash skipped: {e}")
    m.last_login_at = m.last_seen_at = _now()
    db.commit()
    return m, None


def owner_label(owner) -> str:
    name = " ".join(x for x in [getattr(owner, "first_name", None), getattr(owner, "last_name", None)] if x).strip()
    return name or getattr(owner, "company_name", None) or (getattr(owner, "email", "") or "").split("@")[0]


def staff_profile(db: Session, owner, member_id) -> Optional[Dict[str, Any]]:
    """Кто работает (для /api/users/me и ответа на вход): имя, разделы, меню, домашняя страница."""
    from backend.models.team import TeamMember
    try:
        m = db.query(TeamMember).filter(TeamMember.id == uuid.UUID(str(member_id))).first()
    except (ValueError, TypeError):
        return None
    if m is None:
        return None
    perms = clean_permissions(m.permissions)
    catalog_title = None
    try:
        from backend.services import business_profile
        catalog_title = (business_profile.resolve(owner).get("catalog") or {}).get("title")
    except Exception:
        catalog_title = None
    menu = sections(perms, catalog_title)
    return {
        "id": str(m.id), "name": m.name, "email": m.email, "role_title": m.role_title,
        "permissions": perms, "menu": menu, "home": menu[0]["href"] if menu else "/static/inbox.html",
        "owner_name": owner_label(owner), "company": getattr(owner, "company_name", None),
    }


# ───────────────────────────── места: оплата ─────────────────────────────

def seat_price_tyiyn() -> int:
    return max(0, int(TEAM_SEAT_SOM)) * 100


def owner_is_free(owner) -> bool:
    from backend.services.wallet_service import is_free_user
    return is_free_user(owner)


def owner_plan_ok(owner) -> bool:
    """Сотрудники — на тарифах с ботом (Старт, Profi, Voksy Beauty, пробный)."""
    try:
        return owner.agent_mode() in ("full", "reactive")
    except Exception:
        return False


def included_terms(db: Session, owner) -> Optional[Dict[str, Any]]:
    """Места входят в тариф (Voksy Beauty): {limit, until, plan_name, trial}. None — прежние правила
    (место за TEAM_SEAT_SOM с кошелька)."""
    try:
        from backend.services.plan_catalog import staff_terms
        return staff_terms(db, owner)
    except Exception as e:
        logger.warning(f"[TEAM] staff terms of {getattr(owner, 'id', '?')}: {e}")
        return None


def _total_cap(terms: Optional[Dict[str, Any]]) -> int:
    """Сколько сотрудников (вместе с выключенными) можно держать в списке."""
    if terms is None:
        return TEAM_MAX_MEMBERS
    limit = terms.get("limit")
    return (int(limit) + 20) if limit else 200


def _active_others(db: Session, owner_id, member, older_only: bool = False) -> int:
    """Сколько ещё сотрудников владельца включено (older_only — только добавленных раньше этого)."""
    from backend.models.team import TeamMember
    q = db.query(TeamMember.id).filter(TeamMember.owner_id == owner_id, TeamMember.is_active.is_(True),
                                       TeamMember.id != member.id)
    if older_only and member.created_at is not None:
        q = q.filter(TeamMember.created_at < member.created_at)
    return q.count()


class TeamError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, extra: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra or {}


def _som(tyiyn: int) -> str:
    v = round((tyiyn or 0) / 100, 2)
    return (f"{v:,.0f}" if v == int(v) else f"{v:,.2f}").replace(",", " ")


def pay_seat(db: Session, owner_id, member, *, now: Optional[datetime] = None, renewal: bool = False) -> str:
    """
    Оплатить (продлить) место сотрудника. Строка владельца — под блокировкой (как у кошелька), поэтому
    два одновременных добавления не обойдут ни баланс, ни лимит тарифа. Коммитит сам.

      "ok"         — место продлено (paid_until);
      "no_money"   — на кошельке меньше TEAM_SEAT_SOM (прежние тарифы);
      "over_limit" — Beauty: включённых сотрудников уже столько, сколько даёт тариф (при продлении —
                     считаются только добавленные раньше: остаются первые по дате добавления);
      "plan_ended" — Beauty: срок тарифа уже прошёл (или владелец не найден).
    При отказе ничего не меняется (транзакция откатана). Админу места бесплатны и без срока.
    """
    from backend.models.wallet_transaction import WalletTransactionType
    from backend.services.wallet_service import WalletService
    now = now or _now()
    owner = WalletService._lock_user(db, owner_id)
    if owner is None:
        db.rollback()
        return "plan_ended"
    base = _aware(member.paid_until)
    base = base if base and base > now else now
    if owner_is_free(owner):
        member.paid_until = None   # без срока: продлевать нечего
        db.commit()
        return "ok"
    terms = included_terms(db, owner)
    if terms is not None:
        until = _aware(terms.get("until"))
        if until is None or until <= now:
            db.rollback()
            return "plan_ended"
        limit = terms.get("limit")
        if limit and _active_others(db, owner.id, member, older_only=renewal) >= int(limit):
            db.rollback()
            return "over_limit"
        member.paid_until = until    # место живёт, пока живёт тариф; продление — вместе с тарифом
        db.commit()
        logger.info(f"[TEAM] seat {member.id} included in {terms.get('plan_name')} → owner {owner.id}, until {until}")
        return "ok"
    price = seat_price_tyiyn()
    if int(owner.wallet_balance or 0) < price:
        db.rollback()
        return "no_money"
    WalletService._add(db, owner, WalletTransactionType.TEAM, -price,
                       note=f"Сотрудник «{member.name}» — {TEAM_SEAT_DAYS} дней")
    member.paid_until = base + timedelta(days=TEAM_SEAT_DAYS)
    db.commit()
    logger.info(f"[TEAM] seat {member.id} paid {price / 100:.0f} KGS → owner {owner.id}, until {member.paid_until}")
    return "ok"


def charge_seat(db: Session, owner_id, member, *, now: Optional[datetime] = None) -> bool:
    """Совместимость: True — место оплачено/продлено (см. pay_seat)."""
    return pay_seat(db, owner_id, member, now=now) == "ok"


def _limit_error(terms: Dict[str, Any]) -> TeamError:
    from backend.services.plan_catalog import limit_message
    return TeamError("plan_limit", limit_message(terms), 402,
                     {"limit": terms.get("limit"), "plan": terms.get("plan_name")})


def _seat_error(result: str, owner, terms: Optional[Dict[str, Any]]) -> TeamError:
    if result == "over_limit" and terms:
        return _limit_error(terms)
    if result == "plan_ended":
        return TeamError("plan_required", "Тариф закончился — продлите его, чтобы включить сотрудников", 402)
    return _need_money_error(owner)


def _need_money_error(owner, count: int = 1) -> TeamError:
    need = seat_price_tyiyn() * count
    have = int(getattr(owner, "wallet_balance", 0) or 0)
    return TeamError(
        "wallet_low",
        f"Место сотрудника стоит {TEAM_SEAT_SOM} сом в месяц. На кошельке {_som(have)} сом — пополните кошелёк.",
        402, {"need_som": need / 100, "balance_som": have / 100},
    )


# ───────────────────────────── владелец: список и правка ─────────────────────────────

def _seen_text(dt: Optional[datetime], now: datetime) -> Optional[str]:
    dt = _aware(dt)
    if dt is None:
        return None
    return dt.isoformat()


def member_dict(m, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    paid = _aware(m.paid_until)
    seen = _aware(m.last_seen_at)
    return {
        "id": str(m.id), "email": m.email, "name": m.name, "phone": m.phone, "role_title": m.role_title,
        "permissions": clean_permissions(m.permissions), "is_active": bool(m.is_active),
        "status_reason": m.status_reason,
        "paid_until": paid.isoformat() if paid else None,
        "days_left": max(0, int((paid - now).total_seconds() // 86400)) if paid else None,
        "last_login_at": _seen_text(m.last_login_at, now), "last_seen_at": seen.isoformat() if seen else None,
        "online": bool(m.is_active and seen and now - seen < timedelta(minutes=10)),
        "created_at": _aware(m.created_at).isoformat() if m.created_at else None,
    }


def available_permissions(owner) -> List[Dict[str, Any]]:
    """Разделы для галочек владельца: запись — только если модуль записи включён."""
    booking_on = orders_on = False
    try:
        from backend.services import business_profile
        mods = business_profile.modules_of(owner)
        booking_on = bool(mods.get("booking")) and business_profile.page_ready("booking.html")
        orders_on = bool(mods.get("orders")) and business_profile.page_ready("orders.html")
        catalog_title = (business_profile.resolve(owner).get("catalog") or {}).get("title")
    except Exception:
        catalog_title = None
    out = []
    for p in PERMISSIONS:
        item = dict(p)
        if p["key"] == "catalog" and catalog_title:
            item["title"] = catalog_title
        if p["key"] in BOOKING_MODULE_PERMS:
            item["available"] = booking_on
        elif p["key"] == "orders":
            item["available"] = orders_on
        elif p["key"] == "finance":
            item["available"] = booking_on or orders_on      # страница «Финансы» — у записи и заказов
        else:
            item["available"] = True
        out.append(item)
    return out


def list_members(db: Session, owner_id) -> List[Any]:
    from backend.models.team import TeamMember
    return (db.query(TeamMember).filter(TeamMember.owner_id == owner_id)
            .order_by(TeamMember.created_at.asc()).all())


def team_summary(db: Session, owner) -> Dict[str, Any]:
    now = _now()
    members = list_members(db, owner.id)
    active = [m for m in members if m.is_active]
    free = owner_is_free(owner)
    terms = None if free else included_terms(db, owner)
    out = {
        "members": [member_dict(m, now) for m in members],
        "seat_som": TEAM_SEAT_SOM, "seat_days": TEAM_SEAT_DAYS, "max_members": TEAM_MAX_MEMBERS,
        "active_count": len(active), "monthly_som": 0 if free else TEAM_SEAT_SOM * len(active),
        "can_add": len(members) < TEAM_MAX_MEMBERS, "free": free, "plan_ok": owner_plan_ok(owner) or free,
        "wallet_som": round(int(owner.wallet_balance or 0) / 100, 2),
        "permissions": available_permissions(owner),
        "included": False,
    }
    if terms is not None:
        # Voksy Beauty: места входят в тариф — без оплаты, но не больше лимита тарифа
        limit = terms.get("limit")
        until = _aware(terms.get("until"))
        out.update({
            "included": True, "seat_som": 0, "monthly_som": 0, "limit": limit,
            "plan_name": terms.get("plan_name"), "trial": bool(terms.get("trial")),
            "plan_until": until.isoformat() if until else None,
            "max_members": limit or _total_cap(terms),
            "can_add": (not limit or len(active) < int(limit)) and len(members) < _total_cap(terms),
        })
    return out


def generate_password() -> str:
    """Пароль, который легко продиктовать: 4 буквы, дефис, 4 цифры (без похожих l/1, o/0)."""
    letters = "abcdefghjkmnpqrstuvwxyz"
    digits = "23456789"
    return "".join(secrets.choice(letters) for _ in range(4)) + "-" + "".join(secrets.choice(digits) for _ in range(4))


def _check_password(pw: Optional[str]) -> str:
    pw = (pw or "").strip()
    if len(pw) < MIN_PASSWORD:
        raise TeamError("weak_password", f"Пароль — не короче {MIN_PASSWORD} символов")
    if len(pw) > 128:
        raise TeamError("weak_password", "Слишком длинный пароль")
    return pw


def _check_email(db: Session, email: str, exclude_id=None) -> str:
    from backend.models.user import find_user_by_email
    key = normalize_email(email)
    if not EMAIL_RE.match(key) or len(key) > 255 or not _email_login_ok(key):
        raise TeamError("invalid_email", "Проверьте email сотрудника")
    if find_user_by_email(db, key) is not None:
        raise TeamError("email_taken", "Этот email уже зарегистрирован как отдельный кабинет. Укажите другой email.")
    other = find_member_by_email(db, key)
    if other is not None and str(other.id) != str(exclude_id or ""):
        raise TeamError("email_taken", "Этот email уже есть у сотрудника — укажите другой.")
    return key


def _clean_name(name: Optional[str]) -> str:
    v = re.sub(r"\s+", " ", (name or "").strip())
    if not v:
        raise TeamError("name_required", "Укажите имя сотрудника")
    return v[:120]


def _clean_phone(phone: Optional[str]) -> Optional[str]:
    if not phone or not str(phone).strip():
        return None
    try:
        from backend.utils.phone import normalize_phone_e164
        return normalize_phone_e164(phone) or str(phone).strip()[:32]
    except Exception:
        return str(phone).strip()[:32]


def _get_member(db: Session, owner_id, member_id, lock: bool = False):
    from backend.models.team import TeamMember
    try:
        mid = uuid.UUID(str(member_id))
    except (ValueError, TypeError):
        raise TeamError("not_found", "Сотрудник не найден", 404)
    q = db.query(TeamMember).filter(TeamMember.id == mid, TeamMember.owner_id == owner_id)
    if lock:
        q = q.with_for_update()
    m = q.first()
    if m is None:
        raise TeamError("not_found", "Сотрудник не найден", 404)
    return m


def create_member(db: Session, owner, *, name: str, email: str, password: Optional[str] = None,
                  phone: Optional[str] = None, role_title: Optional[str] = None,
                  permissions=None) -> Tuple[Any, str]:
    """Добавить сотрудника и оплатить место. Возвращает (сотрудник, пароль — показать один раз)."""
    from backend.core.security import hash_password
    from backend.models.team import TeamMember
    free = owner_is_free(owner)
    if not owner_plan_ok(owner) and not free:
        raise TeamError("plan_required", "Сотрудники доступны на тарифах Старт, Profi и Voksy Beauty", 402)
    perms = clean_permissions(permissions)
    if not perms:
        raise TeamError("no_permissions", "Отметьте хотя бы один раздел, с которым будет работать сотрудник")
    clean_name = _clean_name(name)
    key = _check_email(db, email)
    pw = _check_password(password) if password else generate_password()
    terms = None if free else included_terms(db, owner)
    members = list_members(db, owner.id)
    if terms is not None:
        limit = terms.get("limit")
        if limit and sum(1 for x in members if x.is_active) >= int(limit):
            raise _limit_error(terms)
        if len(members) >= _total_cap(terms):
            raise TeamError("limit", f"В списке уже {len(members)} сотрудников вместе с выключенными — удалите лишних")
    else:
        if len(members) >= TEAM_MAX_MEMBERS:
            raise TeamError("limit", f"Можно добавить до {TEAM_MAX_MEMBERS} сотрудников")
        if not free and int(owner.wallet_balance or 0) < seat_price_tyiyn():
            raise _need_money_error(owner)
    m = TeamMember(owner_id=owner.id, email=key, name=clean_name, phone=_clean_phone(phone),
                   role_title=(role_title or "").strip()[:80] or None, password_hash=hash_password(pw),
                   permissions=perms, is_active=True, token_version=1)
    db.add(m)
    db.flush()
    result = pay_seat(db, owner.id, m)   # при отказе rollback внутри — сотрудник не создан
    if result != "ok":
        db.rollback()
        raise _seat_error(result, owner, terms)
    db.refresh(m)
    logger.info(f"[TEAM] owner {owner.id} added member {m.id} ({key}), perms={perms}")
    return m, pw


def update_member(db: Session, owner, member_id, *, name=None, phone=None, role_title=None,
                  permissions=None, email=None):
    m = _get_member(db, owner.id, member_id)
    if name is not None:
        m.name = _clean_name(name)
    if phone is not None:
        m.phone = _clean_phone(phone)
    if role_title is not None:
        m.role_title = (role_title or "").strip()[:80] or None
    if email is not None and normalize_email(email) != m.email:
        m.email = _check_email(db, email, exclude_id=m.id)
        m.token_version = int(m.token_version or 1) + 1      # вход по старому email больше не действует
    if permissions is not None:
        perms = clean_permissions(permissions)
        if not perms:
            raise TeamError("no_permissions", "Отметьте хотя бы один раздел")
        m.permissions = perms
    db.commit()
    db.refresh(m)
    invalidate(m.id)
    return m


def set_password(db: Session, owner, member_id, password: Optional[str] = None) -> Tuple[Any, str]:
    """Новый пароль сотруднику (или сгенерировать). Старые входы сотрудника перестают действовать."""
    from backend.core.security import hash_password
    m = _get_member(db, owner.id, member_id)
    pw = _check_password(password) if password else generate_password()
    m.password_hash = hash_password(pw)
    m.token_version = int(m.token_version or 1) + 1
    db.commit()
    db.refresh(m)
    invalidate(m.id)
    return m, pw


def deactivate(db: Session, owner, member_id, reason: str = "owner"):
    m = _get_member(db, owner.id, member_id)
    m.is_active = False
    m.status_reason = reason
    m.token_version = int(m.token_version or 1) + 1
    db.commit()
    db.refresh(m)
    invalidate(m.id)
    return m


def activate(db: Session, owner, member_id):
    """Включить сотрудника. Срок места прошёл — сначала оплатить новый."""
    now = _now()
    free = owner_is_free(owner)
    if not owner_plan_ok(owner) and not free:
        raise TeamError("plan_required", "Сотрудники доступны на тарифах Старт, Profi и Voksy Beauty", 402)
    terms = None if free else included_terms(db, owner)
    m = _get_member(db, owner.id, member_id, lock=True)
    if m.is_active:
        db.rollback()
        return m
    paid = _aware(m.paid_until)
    if terms is not None:
        # Beauty: место входит в тариф — проверяем лимит и ставим срок места = срок тарифа
        result = pay_seat(db, owner.id, m, now=now)
        if result != "ok":
            raise _seat_error(result, owner, terms)
        m = _get_member(db, owner.id, member_id, lock=True)
    elif not free and (paid is None or paid <= now):
        result = pay_seat(db, owner.id, m, now=now)
        if result != "ok":
            raise _seat_error(result, owner, terms)
        m = _get_member(db, owner.id, member_id, lock=True)
    m.is_active = True
    m.status_reason = None
    db.commit()
    db.refresh(m)
    invalidate(m.id)
    return m


def delete_member(db: Session, owner, member_id) -> None:
    from sqlalchemy import text as _text
    m = _get_member(db, owner.id, member_id)
    mid = m.id
    try:   # мастер журнала, привязанный к этому входу, остаётся без входа
        db.execute(_text("UPDATE booking_resources SET team_member_id = NULL WHERE user_id = :o AND team_member_id = :m"),
                   {"o": str(owner.id), "m": str(mid)})
    except Exception as e:
        db.rollback()
        logger.warning(f"[TEAM] unlink master of {mid}: {e}")
        m = _get_member(db, owner.id, member_id)
    db.delete(m)
    db.commit()
    invalidate(mid)


def change_own_password(db: Session, member_id, old: str, new: str) -> None:
    """Сотрудник сам меняет пароль (старый обязателен). Другие его входы перестают действовать."""
    from backend.core.security import hash_password, verify_password
    from backend.models.team import TeamMember
    m = db.query(TeamMember).filter(TeamMember.id == uuid.UUID(str(member_id))).first()
    if m is None:
        raise TeamError("not_found", "Сотрудник не найден", 404)
    if not verify_password(old or "", m.password_hash):
        raise TeamError("wrong_password", "Текущий пароль неверный")
    m.password_hash = hash_password(_check_password(new))
    m.token_version = int(m.token_version or 1) + 1
    db.commit()
    invalidate(m.id)
    return m


# ───────────────────────────── инбокс: личные настройки сотрудника ─────────────────────────────

def member_prefs(db: Session, member_id) -> Dict[str, Any]:
    from backend.models.team import TeamMember
    try:
        m = db.query(TeamMember).filter(TeamMember.id == uuid.UUID(str(member_id))).first()
    except (ValueError, TypeError):
        return {}
    return dict(m.prefs or {}) if m is not None and isinstance(m.prefs, dict) else {}


def save_member_prefs(db: Session, member_id, key: str, value: Dict[str, Any]) -> None:
    from backend.models.team import TeamMember
    m = db.query(TeamMember).filter(TeamMember.id == uuid.UUID(str(member_id))).first()
    if m is None:
        return
    prefs = dict(m.prefs or {}) if isinstance(m.prefs, dict) else {}
    prefs[key] = value
    m.prefs = prefs
    db.commit()


# ───────────────────────────── продление мест (фоновый цикл) ─────────────────────────────

async def _notify_owner(owner_id, text: str) -> None:
    from backend.db.session import SessionLocal
    from backend.models.agent_config import AgentConfig
    from backend.services.owner_notifications import _send
    db = SessionLocal()
    try:
        agent = (db.query(AgentConfig).filter(AgentConfig.user_id == owner_id)
                 .order_by(AgentConfig.created_at.asc()).first())
        if agent is not None:
            await _send(agent, db, text)
    except Exception as e:
        logger.warning(f"[TEAM] owner notify failed: {e}")
    finally:
        db.close()


def renew_due_seats(now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Продлить места, у которых кончился срок. Каждый сотрудник — своей транзакцией под блокировкой
    строки (SKIP LOCKED): воркеров несколько, место оплачивается один раз. Вернёт счётчики и
    список уведомлений владельцам [(owner_id, текст)].
    """
    from backend.db.session import SessionLocal
    from backend.models.team import TeamMember
    from backend.models.user import User
    now = now or _now()
    out: Dict[str, Any] = {"renewed": 0, "disabled": 0, "notify": []}
    db = SessionLocal()
    try:
        ids = [r[0] for r in db.query(TeamMember.id).filter(
            TeamMember.is_active.is_(True), TeamMember.paid_until.isnot(None), TeamMember.paid_until <= now,
        ).order_by(TeamMember.paid_until.asc()).limit(500).all()]
        db.rollback()
        for mid in ids:
            try:
                m = (db.query(TeamMember).filter(
                    TeamMember.id == mid, TeamMember.is_active.is_(True), TeamMember.paid_until <= now)
                    .with_for_update(skip_locked=True).first())
                if m is None:
                    db.rollback()
                    continue
                owner = db.query(User).filter(User.id == m.owner_id).first()
                free = owner is not None and owner_is_free(owner)
                if owner is None or (not owner_plan_ok(owner) and not free):
                    m.is_active, m.status_reason = False, "plan"
                    m.token_version = int(m.token_version or 1) + 1
                    db.commit()
                    invalidate(mid)
                    out["disabled"] += 1
                    if owner is not None:
                        out["notify"].append((str(owner.id), f"👤 Сотрудник «{m.name}» отключён: закончился тариф. "
                                              "Продлите тариф и включите сотрудника в Настройках → Сотрудники."))
                    continue
                name = m.name
                terms = None if free else included_terms(db, owner)
                result = pay_seat(db, owner.id, m, now=now, renewal=True)
                if result == "ok":
                    out["renewed"] += 1
                    continue
                m = db.query(TeamMember).filter(TeamMember.id == mid).with_for_update().first()
                if m is None:
                    db.rollback()
                    continue
                reason = {"over_limit": "limit", "plan_ended": "plan"}.get(result, "unpaid")
                m.is_active, m.status_reason = False, reason
                m.token_version = int(m.token_version or 1) + 1
                db.commit()
                invalidate(mid)
                out["disabled"] += 1
                if reason == "limit" and terms:
                    from backend.services.plan_catalog import upto
                    text_ = (f"👤 Сотрудник «{name}» отключён: в тарифе {terms.get('plan_name')} — до "
                             f"{upto(terms.get('limit') or 0)}. Перейдите на тариф больше или выключите другого "
                             "сотрудника и включите этого в разделе «Сотрудники».")
                elif reason == "plan":
                    text_ = (f"👤 Сотрудник «{name}» отключён: закончился тариф. Продлите тариф и включите "
                             "сотрудника в разделе «Сотрудники».")
                else:
                    text_ = (f"👤 Сотрудник «{name}» отключён: на кошельке не хватило {TEAM_SEAT_SOM} сом на "
                             "продление места. Пополните кошелёк и включите сотрудника в Настройках → Сотрудники.")
                out["notify"].append((str(owner.id), text_))
            except Exception as e:
                db.rollback()
                logger.error(f"[TEAM] renew {mid}: {e}")
    finally:
        db.close()
    if out["renewed"] or out["disabled"]:
        logger.info(f"[TEAM] seats renewed={out['renewed']} disabled={out['disabled']}")
    return out


async def start_team_seats_loop() -> None:
    logger.info(f"[TEAM] seats loop started (every {RENEW_TICK_SECONDS}s)")
    await asyncio.sleep(60)
    while True:
        try:
            res = await asyncio.to_thread(renew_due_seats)
            for owner_id, text in res.get("notify") or []:
                await _notify_owner(owner_id, text)
        except Exception as e:
            logger.error(f"[TEAM] seats loop error: {e}")
        await asyncio.sleep(RENEW_TICK_SECONDS)
