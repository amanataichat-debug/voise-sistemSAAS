"""
Каталог тарифов (ветка 2511-beauty): какие тарифы видит бизнес, сколько стоят, что дают.

Два вида тарифов в одной таблице subscription_plans:
  • product="voksy"  — AI Voice, Старт, Profi. Цена и текст — по-прежнему в SUBSCRIPTION_PLANS_CONFIG
                       (backend/api/payments.py), их же показывает лендинг. Здесь только читаются.
  • product="beauty" — Voksy Beauty для салонов: Beauty Start / Beauty AI / Beauty Pro и
                       индивидуальные тарифы. Живут ТОЛЬКО в базе: цену, лимиты, текст «что
                       входит», показ и выключение правит суперадмин (/api/admin/plans), код их
                       не перезаписывает. При первом старте заводятся значения по умолчанию
                       (BEAUTY_DEFAULTS) — один раз, дальше база главнее.

Кто какие тарифы видит: салонные ниши (BEAUTY_NICHES) и те, кто уже на Beauty, — только Beauty
(решение владельца 25.11.2026: салоны полностью на новом формате); остальные — прежние тарифы.
Индивидуальный тариф (for_user_id) видит и может оплатить только его клиент.

Что даёт Beauty-тариф (limits):
  staff          — сколько сотрудников со своим входом и мастеров в журнале (None — без лимита);
                   входы в пределах лимита бесплатны (плата за место 150 сом — только у других ниш);
  agent_mode     — "reactive" (бот отвечает и записывает, уровень Старта: Beauty Start, Beauty AI) |
                   "full" (автономный агент, уровень Profi: Beauty Pro);
  credits_month  — кредиты бота, начисляемые за каждый оплаченный месяц (по решению владельца 0:
                   ИИ-переписка оплачивается кредитами отдельно, голос — кошельком);
  trial          — эти лимиты действуют у салона на пробном периоде.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PRODUCT_VOKSY = "voksy"
PRODUCT_BEAUTY = "beauty"
BEAUTY_PREFIX = "beauty_"          # коды всех Beauty-тарифов, в т.ч. индивидуальных
BEAUTY_NICHES = ("beauty_salon", "barbershop", "nails", "massage_spa", "cosmetology")
AGENT_MODES = ("reactive", "full")

MAX_PRICE = 1_000_000
MAX_STAFF = 500
MAX_CREDITS = 10_000_000
MAX_FEATURES = 15
MAX_FEATURE_LEN = 120

# Только то, что уже работает (при каждом шаге Beauty суперадмин дописывает новое в редакторе).
_COMMON_FEATURES = [
    "Журнал записи: мастера, график, перерывы и выходные",
    "Страница салона: фото, мастера, услуги, акции и онлайн-запись; QR-код и кнопка для сайта",
    "ИИ-администратор в WhatsApp, Instagram и Telegram: консультирует и записывает",
    "Напоминания клиентам за сутки и за 2 часа до визита",
    "Клиенты, финансы, зарплаты мастеров и аналитика",
    "Голосовой помощник владельца: отчёты и записи голосом",
    "Голосовой агент на звонки — минуты с кошелька",
    "ИИ-переписка — по кредитам бота",
]

# Уровни доступа (решение владельца 25.11.2026): Beauty Start и Beauty AI — уровень тарифа Старт
# (бот отвечает и записывает, сам первым не звонит), Beauty Pro — уровень Profi (автономный агент).
BEAUTY_DEFAULTS: List[Dict[str, Any]] = [
    {"code": "beauty_start", "name": "Beauty Start", "price": 1990, "max_assistants": 3, "sort_order": 10,
     "badge": None, "description": "Для частного мастера и небольшой студии",
     "limits": {"staff": 3, "agent_mode": "reactive", "credits_month": 0, "trial": False},
     "features": ["До 3 сотрудников со своим входом"] + _COMMON_FEATURES},
    {"code": "beauty_ai", "name": "Beauty AI", "price": 3990, "max_assistants": 5, "sort_order": 20,
     "badge": "Хит", "description": "Для салона с командой мастеров",
     "limits": {"staff": 10, "agent_mode": "reactive", "credits_month": 0, "trial": True},
     "features": ["До 10 сотрудников со своим входом"] + _COMMON_FEATURES},
    {"code": "beauty_pro", "name": "Beauty Pro", "price": 5990, "max_assistants": 10, "sort_order": 30,
     "badge": None, "description": "Для большого салона: ИИ-агент работает сам, как на Profi",
     "limits": {"staff": 20, "agent_mode": "full", "credits_month": 0, "trial": False},
     "features": ["До 20 сотрудников со своим входом",
                  "Автономный ИИ-агент: сам пишет клиентам и звонит (номера O!)"] + _COMMON_FEATURES},
]


class PlanError(Exception):
    """Понятный отказ (показывается человеку как есть)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


# ───────────────────────────── простые признаки ─────────────────────────────

def is_beauty_code(code: Optional[str]) -> bool:
    return bool(code) and str(code).startswith(BEAUTY_PREFIX)


def is_beauty_niche(niche: Optional[str]) -> bool:
    return (niche or "") in BEAUTY_NICHES


def plan_product(plan) -> str:
    return (getattr(plan, "product", None) or PRODUCT_VOKSY) if plan is not None else PRODUCT_VOKSY


def is_beauty_user(user) -> bool:
    """Салон: салонная ниша или уже Beauty-тариф (даже истёкший — продлевать будет его же)."""
    if user is None:
        return False
    if is_beauty_niche(getattr(user, "business_niche", None)):
        return True
    try:
        return plan_product(getattr(user, "subscription_plan_rel", None)) == PRODUCT_BEAUTY
    except Exception:
        return False


def limits_of(plan) -> Dict[str, Any]:
    raw = getattr(plan, "limits", None) if plan is not None else None
    return dict(raw) if isinstance(raw, dict) else {}


def staff_limit_of(plan) -> Optional[int]:
    v = limits_of(plan).get("staff")
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def agent_mode_of(plan) -> str:
    return "full" if limits_of(plan).get("agent_mode") == "full" else "reactive"


def credits_month_of(plan) -> int:
    try:
        return max(0, int(limits_of(plan).get("credits_month") or 0))
    except (TypeError, ValueError):
        return 0


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def staff_word(n: int) -> str:
    """«3 сотрудника», «5 сотрудников» — счёт (именительный падеж)."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "сотрудник"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "сотрудника"
    return "сотрудников"


def upto(n: int, one: str = "сотрудника", many: str = "сотрудников") -> str:
    """«до 21 сотрудника», «до 3 сотрудников» — после «до» и «из» (родительный падеж)."""
    n = abs(int(n))
    return f"{n} {one if n % 10 == 1 and n % 100 != 11 else many}"


# ───────────────────────────── схема и значения по умолчанию ─────────────────────────────

SCHEMA_SQL = [
    "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS product VARCHAR(20) NOT NULL DEFAULT 'voksy'",
    "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS limits JSONB",
    "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS features JSONB",
    "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS badge VARCHAR(40)",
    "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS for_user_id UUID",
    "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS admin_updated_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS ix_subscription_plans_for_user ON subscription_plans (for_user_id)",
]


def ensure_schema(engine) -> None:
    """Колонки каталога + Beauty-тарифы по умолчанию. Идемпотентно, безопасно для 4 воркеров сразу:
    одна транзакция под advisory-замком; существующие тарифы НЕ меняются (база главнее кода)."""
    import json
    from sqlalchemy import inspect, text
    if not inspect(engine).has_table("subscription_plans"):
        return
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(25112026)"))
        for sql in SCHEMA_SQL:
            conn.execute(text(sql))
        for d in BEAUTY_DEFAULTS:
            res = conn.execute(text("""
                INSERT INTO subscription_plans (id, code, name, price, max_assistants, description, is_active,
                    product, limits, features, badge, sort_order, is_public, created_at)
                SELECT CAST(:id AS UUID), :code, :name, :price, :max_assistants, :description, TRUE,
                    'beauty', CAST(:limits AS JSONB), CAST(:features AS JSONB), :badge, :sort_order, TRUE, now()
                WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE code = :code)
            """), {"id": str(uuid.uuid4()), "code": d["code"], "name": d["name"], "price": d["price"],
                   "max_assistants": d["max_assistants"], "description": d["description"],
                   "limits": json.dumps(d["limits"]), "features": json.dumps(d["features"], ensure_ascii=False),
                   "badge": d["badge"], "sort_order": d["sort_order"]})
            if res.rowcount:
                logger.info(f"💅 Beauty plan seeded: {d['code']} ({d['price']} KGS)")


# ───────────────────────────── цены и вид тарифа ─────────────────────────────

def _periods(base_price: float) -> Dict[int, Dict[str, Any]]:
    from backend.api.payments import PERIOD_DISCOUNTS, calculate_period_price, format_som
    out: Dict[int, Dict[str, Any]] = {}
    for months in PERIOD_DISCOUNTS.keys():
        p = calculate_period_price(base_price, months)
        out[months] = {
            "months": months, "days": p["days"], "price": p["final_price"],
            "price_formatted": format_som(p["final_price"]), "discount_percent": p["discount_percent"],
            "savings": p["savings"],
            "savings_formatted": format_som(p["savings"]) if p["savings"] > 0 else None,
            "monthly_effective": p["monthly_effective"],
            "monthly_formatted": format_som(p["monthly_effective"]) + "/мес", "label": p["label"],
        }
    return out


def beauty_view(plan) -> Dict[str, Any]:
    """Beauty-тариф в том же виде, что тарифы /api/payments/plans (+ лимиты для окна оплаты)."""
    from backend.api.payments import format_som
    base = float(plan.price or 0)
    staff = staff_limit_of(plan)
    feats = [str(f) for f in (plan.features or []) if str(f).strip()]
    return {
        "code": plan.code, "name": plan.name, "product": PRODUCT_BEAUTY,
        "base_price": base, "base_price_formatted": format_som(base) + "/мес",
        "max_assistants": int(plan.max_assistants or 0), "description": plan.description or "",
        "features": feats, "restricted_features": [], "periods": _periods(base),
        "staff_limit": staff,
        "staff_label": (f"До {upto(staff)}" if staff else "Без лимита сотрудников"),
        "badge": plan.badge or None, "sort_order": int(plan.sort_order or 0),
        "credits_month": credits_month_of(plan), "agent_mode": agent_mode_of(plan),
        "individual": plan.for_user_id is not None,
    }


def _beauty_query(db):
    from backend.models.subscription import SubscriptionPlan
    return db.query(SubscriptionPlan).filter(SubscriptionPlan.product == PRODUCT_BEAUTY)


def beauty_plans_for(db, user) -> List[Any]:
    """Beauty-тарифы, которые этот клиент видит в окне оплаты: публичные включённые, его
    индивидуальные и текущий (чтобы продлить даже скрытый/старый)."""
    from backend.models.subscription import SubscriptionPlan
    from sqlalchemy import or_, and_
    uid = getattr(user, "id", None)
    rows = (_beauty_query(db).filter(SubscriptionPlan.is_active.is_(True))
            .filter(or_(and_(SubscriptionPlan.is_public.is_(True), SubscriptionPlan.for_user_id.is_(None)),
                        SubscriptionPlan.for_user_id == uid))
            .all())
    cur = getattr(user, "subscription_plan_rel", None)
    if (cur is not None and plan_product(cur) == PRODUCT_BEAUTY and bool(cur.is_active)
            and cur.id not in {r.id for r in rows} and (cur.for_user_id is None or cur.for_user_id == uid)):
        rows.append(cur)
    rows.sort(key=lambda p: (p.for_user_id is None, int(p.sort_order or 0), float(p.price or 0)))
    return rows


def catalog_for(db, user) -> Dict[str, Any]:
    """Ответ /api/payments/my-plans: тарифы для окна оплаты именно этого клиента."""
    from backend.api.payments import CURRENCY_CODE, CURRENCY_LABEL, PERIOD_DISCOUNTS, get_all_plans_with_prices
    cur = getattr(user, "subscription_plan_rel", None)
    current_code = cur.code if cur is not None else None
    if not is_beauty_user(user):
        data = get_all_plans_with_prices(db)
        data.update(product=PRODUCT_VOKSY, current_plan_code=current_code, default_plan=None)
        return data
    rows = beauty_plans_for(db, user)
    views = [beauty_view(p) for p in rows]
    default = next((v["code"] for v in views if v["individual"]), None) \
        or next((v["code"] for v in views if v["code"] == current_code), None) \
        or next((v["code"] for v in views if v["badge"]), None) \
        or (views[0]["code"] if views else None)
    return {"product": PRODUCT_BEAUTY, "plans": {v["code"]: v for v in views}, "order": [v["code"] for v in views],
            "default_plan": default, "current_plan_code": current_code, "discounts": PERIOD_DISCOUNTS,
            "currency": CURRENCY_CODE, "currency_label": CURRENCY_LABEL, "agent_price": None}


def public_beauty_catalog(db) -> List[Dict[str, Any]]:
    """Публичные Beauty-тарифы (для будущей страницы Voksy Beauty на сайте)."""
    from backend.models.subscription import SubscriptionPlan
    rows = (_beauty_query(db).filter(SubscriptionPlan.is_active.is_(True), SubscriptionPlan.is_public.is_(True),
                                     SubscriptionPlan.for_user_id.is_(None))
            .order_by(SubscriptionPlan.sort_order, SubscriptionPlan.price).all())
    return [beauty_view(p) for p in rows]


def purchasable(db, user, code: str):
    """Можно ли этому клиенту оплатить тариф code. Вернёт строку SubscriptionPlan Beauty-тарифа или
    None для прежних тарифов (их цена — из конфига); иначе PlanError с понятной причиной."""
    from backend.api.payments import SUBSCRIPTION_PLANS_CONFIG
    from backend.models.subscription import SubscriptionPlan
    code = (code or "").strip()
    cur = getattr(user, "subscription_plan_rel", None)
    current_code = cur.code if cur is not None else None
    if code in SUBSCRIPTION_PLANS_CONFIG:
        if is_beauty_user(user) and code != current_code:
            raise PlanError("Для салонов красоты доступны тарифы Voksy Beauty")
        return None
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).first() if code else None
    if plan is None or plan_product(plan) != PRODUCT_BEAUTY:
        raise PlanError("Такого тарифа нет")
    if not plan.is_active:
        raise PlanError("Этот тариф больше не продаётся — выберите другой")
    if plan.for_user_id is not None:
        if plan.for_user_id != getattr(user, "id", None):
            raise PlanError("Такого тарифа нет")
        return plan
    if not is_beauty_user(user):
        raise PlanError("Тарифы Voksy Beauty — для салонов красоты. Укажите сферу «Салон красоты» в настройках кабинета")
    if not plan.is_public and code != current_code:
        raise PlanError("Этот тариф больше не продаётся — выберите другой")
    return plan


# ───────────────────────────── сотрудники и мастера ─────────────────────────────

def trial_plan(db):
    """Чьи лимиты у салона на пробном периоде: Beauty-тариф с limits.trial, иначе самый большой."""
    from backend.models.subscription import SubscriptionPlan
    rows = (_beauty_query(db).filter(SubscriptionPlan.is_active.is_(True), SubscriptionPlan.for_user_id.is_(None))
            .order_by(SubscriptionPlan.sort_order).all())
    marked = [p for p in rows if limits_of(p).get("trial")]
    if marked:
        return marked[0]
    return max(rows, key=lambda p: staff_limit_of(p) or MAX_STAFF) if rows else None


def _trial_end(user) -> Optional[datetime]:
    ends = []
    try:
        if user.base_trial_active():
            ends.append(_aware(user.subscription_end_date))
    except Exception:
        pass
    try:
        if user.agent_trial_active() and user.agent_trial_started_at:
            ends.append(_aware(user.agent_trial_started_at) + timedelta(days=user.AGENT_TRIAL_DAYS))
    except Exception:
        pass
    ends = [e for e in ends if e]
    return max(ends) if ends else None


def staff_terms(db, user) -> Optional[Dict[str, Any]]:
    """Сотрудники «входят в тариф» (Beauty): лимит, до какого числа, какой тариф. None — у бизнеса
    прежние правила (место 150 сом с кошелька) или доступа к сотрудникам нет вовсе."""
    plan = user.beauty_plan() if hasattr(user, "beauty_plan") else None
    if plan is not None:
        return {"plan_code": plan.code, "plan_name": plan.name, "limit": staff_limit_of(plan),
                "until": _aware(user.subscription_end_date), "trial": False}
    try:
        on_trial = is_beauty_niche(getattr(user, "business_niche", None)) and user.start_trial_active()
    except Exception:
        on_trial = False
    if not on_trial:
        return None
    tp = trial_plan(db)
    if tp is None:
        return None
    return {"plan_code": tp.code, "plan_name": f"пробный период ({tp.name})", "limit": staff_limit_of(tp),
            "until": _trial_end(user), "trial": True}


def limit_message(terms: Dict[str, Any], what: str = "staff") -> str:
    """what: "staff" — сотрудники со входом, "masters" — мастера в журнале записи."""
    n = int(terms.get("limit") or 0)
    count = upto(n, "мастера", "мастеров") + " в журнале" if what == "masters" else upto(n)
    head = f"В тарифе {terms.get('plan_name')}" if not terms.get("trial") else f"На {terms.get('plan_name')}"
    return (f"{head} — до {count}. Чтобы добавить ещё, перейдите на тариф больше "
            "(Главная → Сменить тариф) или выключите кого-то из текущих.")


def master_limit_error(db, user, *, adding: int = 1) -> Optional[str]:
    """Можно ли включить ещё `adding` мастеров в журнале записи. Строка владельца блокируется до
    конца транзакции, чтобы два одновременных запроса не обошли лимит. None — можно."""
    from backend.models.booking import BookingResource
    from backend.models.user import User
    terms = staff_terms(db, user)
    if not terms or not terms.get("limit"):
        return None
    db.query(User.id).filter(User.id == user.id).with_for_update().first()
    active = (db.query(BookingResource.id)
              .filter(BookingResource.user_id == user.id, BookingResource.is_active.is_(True)).count())
    if active + adding > terms["limit"]:
        return limit_message(terms, "masters")
    return None


# ───────────────────────────── суперадмин: правка тарифов ─────────────────────────────

_NAME_RX = re.compile(r"\s+")


def _clean_text(v: Any, maxlen: int) -> str:
    return _NAME_RX.sub(" ", str(v or "")).strip()[:maxlen]


def clean_admin_patch(data: Dict[str, Any], *, creating: bool = False) -> Dict[str, Any]:
    """Проверить и нормализовать правку тарифа из админки. Только известные поля; PlanError при
    неверных значениях. Возвращает словарь колонок/лимитов к записи."""
    out: Dict[str, Any] = {}
    data = data or {}
    if "name" in data or creating:
        name = _clean_text(data.get("name"), 50)
        if len(name) < 2:
            raise PlanError("Название тарифа — от 2 символов")
        out["name"] = name
    if "price" in data or creating:
        try:
            price = round(float(data.get("price")))
        except (TypeError, ValueError):
            raise PlanError("Цена — число в сомах")
        if not 0 <= price <= MAX_PRICE:
            raise PlanError(f"Цена — от 0 до {MAX_PRICE:,} сом".replace(",", " "))
        out["price"] = price
    if "description" in data:
        out["description"] = _clean_text(data.get("description"), 300) or None
    if "badge" in data:
        out["badge"] = _clean_text(data.get("badge"), 40) or None
    if "features" in data:
        raw = data.get("features")
        if isinstance(raw, str):
            raw = raw.splitlines()
        if not isinstance(raw, list):
            raise PlanError("«Что входит» — список строк")
        feats = [_clean_text(x, MAX_FEATURE_LEN) for x in raw]
        feats = [f for f in feats if f]
        if len(feats) > MAX_FEATURES:
            raise PlanError(f"«Что входит» — не больше {MAX_FEATURES} строк")
        out["features"] = feats
    if "max_assistants" in data:
        try:
            ma = int(data.get("max_assistants"))
        except (TypeError, ValueError):
            raise PlanError("Голосовых ассистентов — целое число")
        if not 0 <= ma <= 100:
            raise PlanError("Голосовых ассистентов — от 0 до 100")
        out["max_assistants"] = ma
    if "sort_order" in data:
        try:
            out["sort_order"] = max(0, min(10_000, int(data.get("sort_order"))))
        except (TypeError, ValueError):
            raise PlanError("Порядок — целое число")
    for key in ("is_public", "is_active"):
        if key in data:
            out[key] = bool(data.get(key))
    limits: Dict[str, Any] = {}
    if "staff" in data:
        v = data.get("staff")
        if v in (None, "", 0, "0"):
            limits["staff"] = None
        else:
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise PlanError("Сотрудников — целое число (пусто — без лимита)")
            if not 1 <= n <= MAX_STAFF:
                raise PlanError(f"Сотрудников — от 1 до {MAX_STAFF} (пусто — без лимита)")
            limits["staff"] = n
    elif creating:
        raise PlanError("Укажите, сколько сотрудников входит в тариф")
    if "agent_mode" in data:
        mode = str(data.get("agent_mode") or "")
        if mode not in AGENT_MODES:
            raise PlanError("Режим ИИ — reactive или full")
        limits["agent_mode"] = mode
    if "credits_month" in data:
        try:
            c = int(data.get("credits_month") or 0)
        except (TypeError, ValueError):
            raise PlanError("Кредиты в месяц — целое число")
        if not 0 <= c <= MAX_CREDITS:
            raise PlanError("Кредиты в месяц — от 0 до 10 000 000")
        limits["credits_month"] = c
    if "trial" in data:
        limits["trial"] = bool(data.get("trial"))
    if limits:
        out["limits"] = limits
    return out


def _admin_row(plan, subscribers: int, owner_email: Optional[str] = None) -> Dict[str, Any]:
    lim = limits_of(plan)
    return {
        "code": plan.code, "name": plan.name, "product": plan_product(plan), "editable": True,
        "price": float(plan.price or 0), "description": plan.description or "", "badge": plan.badge or "",
        "features": [str(f) for f in (plan.features or [])], "max_assistants": int(plan.max_assistants or 0),
        "staff": staff_limit_of(plan), "agent_mode": agent_mode_of(plan), "credits_month": credits_month_of(plan),
        "trial": bool(lim.get("trial")), "sort_order": int(plan.sort_order or 0),
        "is_public": bool(plan.is_public), "is_active": bool(plan.is_active),
        "for_user_id": str(plan.for_user_id) if plan.for_user_id else None, "for_user_email": owner_email,
        "subscribers": subscribers,
        "admin_updated_at": plan.admin_updated_at.isoformat() if plan.admin_updated_at else None,
    }


def admin_overview(db) -> Dict[str, Any]:
    """Список для вкладки «Тарифы» админки: Beauty — с правкой, прежние — только показ."""
    from sqlalchemy import func
    from backend.api.payments import SUBSCRIPTION_PLANS_CONFIG
    from backend.models.subscription import SubscriptionPlan
    from backend.models.user import User
    now = _now()
    counts = dict(db.query(User.subscription_plan_id, func.count(User.id))
                  .filter(User.subscription_end_date > now, User.subscription_plan_id.isnot(None))
                  .group_by(User.subscription_plan_id).all())
    beauty = _beauty_query(db).order_by(SubscriptionPlan.for_user_id.isnot(None), SubscriptionPlan.sort_order,
                                        SubscriptionPlan.price).all()
    owner_ids = [p.for_user_id for p in beauty if p.for_user_id]
    emails = dict(db.query(User.id, User.email).filter(User.id.in_(owner_ids)).all()) if owner_ids else {}
    rows = [_admin_row(p, int(counts.get(p.id, 0)), emails.get(p.for_user_id)) for p in beauty]
    voksy_rows = {p.code: p for p in db.query(SubscriptionPlan).filter(SubscriptionPlan.code.in_(list(SUBSCRIPTION_PLANS_CONFIG))).all()}
    voksy = []
    for code, cfg in SUBSCRIPTION_PLANS_CONFIG.items():
        row = voksy_rows.get(code)
        voksy.append({"code": code, "name": cfg["name"], "product": PRODUCT_VOKSY, "editable": False,
                      "price": float(cfg["base_price"]), "description": cfg.get("description") or "",
                      "features": list(cfg.get("features") or []), "max_assistants": cfg.get("max_assistants"),
                      "subscribers": int(counts.get(row.id, 0)) if row is not None else 0})
    from backend.api.payments import PERIOD_DISCOUNTS
    return {"beauty": rows, "voksy": voksy, "discounts": PERIOD_DISCOUNTS, "beauty_niches": list(BEAUTY_NICHES)}


def _audit(db, admin, action_text: str, details: Dict[str, Any], target_user_id=None) -> None:
    try:
        from backend.models.admin_audit import AdminAuditLog
        db.add(AdminAuditLog(admin_user_id=getattr(admin, "id", None), target_user_id=target_user_id,
                             action="plan", text=action_text[:500], details=details))
    except Exception as e:   # журнал не должен ломать правку
        logger.warning(f"[PLANS] audit skipped: {e}")


def _apply(plan, clean: Dict[str, Any]) -> Dict[str, Any]:
    """Записать правку в строку тарифа; вернуть {поле: [было, стало]} для журнала."""
    diff: Dict[str, Any] = {}
    for key, val in clean.items():
        if key == "limits":
            lim = limits_of(plan)
            for lk, lv in val.items():
                if lim.get(lk) != lv:
                    diff[lk] = [lim.get(lk), lv]
                    lim[lk] = lv
            plan.limits = lim
            continue
        old = getattr(plan, key)
        old_cmp = float(old) if key == "price" and old is not None else old
        if old_cmp != val:
            diff[key] = [old_cmp, val]
            setattr(plan, key, val)
    return diff


def admin_update(db, admin, code: str, data: Dict[str, Any]) -> Dict[str, Any]:
    from backend.models.subscription import SubscriptionPlan
    plan = (db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).with_for_update().first())
    if plan is None:
        raise PlanError("Тариф не найден", 404)
    if plan_product(plan) != PRODUCT_BEAUTY:
        raise PlanError("Цены AI Voice, Старт и Profi задаются в коде вместе с лендингом — здесь их не меняем")
    clean = clean_admin_patch(data)
    diff = _apply(plan, clean)
    if diff:
        plan.admin_updated_at = _now()
        _audit(db, admin, f"Тариф {plan.code}: изменено {', '.join(diff)}", {"code": plan.code, "diff": diff},
               plan.for_user_id)
    db.commit()
    db.refresh(plan)
    logger.info(f"[PLANS] {getattr(admin, 'email', '?')} updated {plan.code}: {diff}")
    return _admin_row(plan, 0)


def admin_create(db, admin, data: Dict[str, Any]) -> Dict[str, Any]:
    """Новый Beauty-тариф: общий (виден всем салонам) или индивидуальный для одного клиента
    (for_user — email или id)."""
    from backend.models.subscription import SubscriptionPlan
    from backend.models.user import User
    clean = clean_admin_patch(data, creating=True)
    owner = None
    who = str((data or {}).get("for_user") or "").strip()
    if who:
        q = db.query(User)
        try:
            owner = q.filter(User.id == uuid.UUID(who)).first()
        except ValueError:
            owner = q.filter(User.email == who.lower()).first()
        if owner is None:
            raise PlanError("Клиент с такой почтой не найден")
    limits = {"staff": None, "agent_mode": "reactive", "credits_month": 0, "trial": False}
    limits.update(clean.pop("limits", {}))
    if owner is not None:
        limits["trial"] = False
    for _ in range(5):
        code = BEAUTY_PREFIX + uuid.uuid4().hex[:8]
        if not db.query(SubscriptionPlan.id).filter(SubscriptionPlan.code == code).first():
            break
    top = db.query(SubscriptionPlan.sort_order).filter(SubscriptionPlan.product == PRODUCT_BEAUTY) \
        .order_by(SubscriptionPlan.sort_order.desc()).first()
    plan = SubscriptionPlan(
        code=code, name=clean["name"], price=clean["price"], max_assistants=clean.get("max_assistants", 3),
        description=clean.get("description"), is_active=clean.get("is_active", True), product=PRODUCT_BEAUTY,
        limits=limits, features=clean.get("features") or [], badge=clean.get("badge"),
        sort_order=clean.get("sort_order", (int(top[0]) + 10) if top else 10),
        is_public=False if owner is not None else clean.get("is_public", True),
        for_user_id=owner.id if owner is not None else None, admin_updated_at=_now(),
    )
    db.add(plan)
    db.flush()
    _audit(db, admin, f"Создан тариф {plan.code} «{plan.name}»" + (f" для {owner.email}" if owner else ""),
           {"code": plan.code, "price": float(plan.price), "limits": limits}, owner.id if owner else None)
    db.commit()
    db.refresh(plan)
    logger.info(f"[PLANS] {getattr(admin, 'email', '?')} created {plan.code} for={getattr(owner, 'email', None)}")
    return _admin_row(plan, 0, getattr(owner, "email", None))
