"""Тарифы Voksy Beauty (ветка 2511-beauty, шаг 1): каталог в базе, оплата, лимит сотрудников, редактор.

Часть 1 (без базы): признаки салона и кода, проверка правки тарифа из админки, лимиты и режим ИИ,
склонение «сотрудник», значения по умолчанию, расчёт цены за период, режим доступа User с
Beauty-тарифом (reactive / full / истёк).

Часть 2 (PostgreSQL, BEAUTY_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- старт сервера добавляет колонки каталога в «старую» таблицу и заводит Beauty-тарифы ровно один раз,
  синхронизация цен прежних тарифов Beauty не трогает;
- /api/payments/my-plans: салону — Beauty (рекомендованный по умолчанию), остальным — AI Voice/Старт/Profi;
- оплата: салон не может купить Старт, магазин — Beauty; цена за 1/6/12 мес из базы; после оплаты —
  тариф, срок, режим reactive, кредиты только если суперадмин включил их в тариф (идемпотентно);
- сроки Beauty (26.11.2026): 1 · 3 · 6 мес · год со скидками 0/10/20/30%, плашка «Выгодно» на 3 месяцах,
  экономия в сомах в ответе окна оплаты; 3 месяца = 90 дней; у Старта 3 месяцев нет (400), срок 2 — 422;
- индивидуальный тариф видит и оплачивает только свой клиент; скрытый — только продление; выключенный
  не продаётся; бесплатный нельзя «оплатить»;
- редактор суперадмина: доступ только админу, правка цены/лимита/текста видна салону сразу, прежние
  тарифы не правятся, неверные значения — понятный отказ, журнал admin_audit_log;
- сотрудники салона: бесплатно в пределах лимита (без списаний с кошелька), срок места = срок тарифа,
  сверх лимита — 402 plan_limit, выключили одного — место свободно; продление вместе с тарифом,
  понижение тарифа выключает самых новых («limit»), тариф кончился — «plan»; пробный период салона —
  лимиты тарифа с меткой trial; у других ниш — прежние 150 сом;
- мастера в журнале записи — в пределах лимита тарифа (402), у других ниш без лимита тарифа;
- админ назначает Beauty-тариф вручную; /api/users/me → business.beauty и сводка главной знают о салоне.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("BEAUTY_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="beauty-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import plan_catalog as pc  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.user import User  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except pc.PlanError as e:
        return e.message
    return None


# ───────────────────────── часть 1: без базы ─────────────────────────
check("код Beauty по префиксу", pc.is_beauty_code("beauty_ai") and pc.is_beauty_code("beauty_1a2b3c4d")
      and not pc.is_beauty_code("start") and not pc.is_beauty_code(None))
check("салонные ниши", all(pc.is_beauty_niche(n) for n in ("beauty_salon", "barbershop", "nails", "massage_spa", "cosmetology"))
      and not pc.is_beauty_niche("dentistry") and not pc.is_beauty_niche("clothing") and not pc.is_beauty_niche(None))
check("склонение «сотрудник»", [pc.staff_word(n) for n in (1, 2, 4, 5, 11, 12, 21, 22, 25, 101)] ==
      ["сотрудник", "сотрудника", "сотрудника", "сотрудников", "сотрудников", "сотрудников", "сотрудник",
       "сотрудника", "сотрудников", "сотрудник"])
check("«до N»: родительный падеж", [pc.upto(n) for n in (1, 3, 10, 11, 21, 22)] ==
      ["1 сотрудника", "3 сотрудников", "10 сотрудников", "11 сотрудников", "21 сотрудника", "22 сотрудников"]
      and pc.upto(1, "мастера", "мастеров") == "1 мастера")
check("текст лимита мастеров", "до 3 мастеров в журнале" in pc.limit_message({"plan_name": "Beauty Start", "limit": 3}, "masters"))

ok = pc.clean_admin_patch({"name": "  Beauty   AI ", "price": "3990", "staff": "10", "agent_mode": "full", "credits_month": 500,
                           "features": "Журнал\n\n  ИИ  \n", "badge": "Хит", "unknown": 1, "is_public": 0})
check("правка: поля нормализованы", ok["name"] == "Beauty AI" and ok["price"] == 3990 and ok["features"] == ["Журнал", "ИИ"]
      and ok["limits"] == {"staff": 10, "agent_mode": "full", "credits_month": 500} and ok["is_public"] is False
      and "unknown" not in ok, ok)
check("правка: пустой лимит = без лимита", pc.clean_admin_patch({"staff": ""})["limits"]["staff"] is None
      and pc.clean_admin_patch({"staff": None})["limits"]["staff"] is None)
check("правка: короткое название — отказ", raises(pc.clean_admin_patch, {"name": "A"}) is not None)
check("правка: цена не число / отрицательная / огромная — отказ",
      all(raises(pc.clean_admin_patch, {"price": v}) for v in ("abc", -1, 2_000_000, None)))
check("правка: сотрудников 0..500", raises(pc.clean_admin_patch, {"staff": 501}) and raises(pc.clean_admin_patch, {"staff": "x"}))
check("правка: режим ИИ только reactive/full", raises(pc.clean_admin_patch, {"agent_mode": "god"}) is not None)
check("правка: кредиты в пределах", raises(pc.clean_admin_patch, {"credits_month": -5}) and raises(pc.clean_admin_patch, {"credits_month": 10**9}))
check("правка: не больше 15 строк «что входит»", raises(pc.clean_admin_patch, {"features": [str(i) for i in range(16)]}) is not None)
check("создание: нужны название, цена, сотрудники", raises(pc.clean_admin_patch, {"name": "Тариф", "price": 100}, creating=True) is not None
      and raises(pc.clean_admin_patch, {"price": 100, "staff": 3}, creating=True) is not None)

p_full = SubscriptionPlan(code="beauty_x", name="X", price=1000, max_assistants=1, product="beauty",
                          limits={"staff": 5, "agent_mode": "full", "credits_month": "700"})
p_bad = SubscriptionPlan(code="beauty_y", name="Y", price=1000, max_assistants=1, product="beauty", limits={"staff": "много"})
check("лимиты тарифа читаются", pc.staff_limit_of(p_full) == 5 and pc.agent_mode_of(p_full) == "full" and pc.credits_month_of(p_full) == 700)
check("кривые лимиты — безопасные значения", pc.staff_limit_of(p_bad) is None and pc.agent_mode_of(p_bad) == "reactive"
      and pc.credits_month_of(p_bad) == 0 and pc.limits_of(None) == {})

codes = [d["code"] for d in pc.BEAUTY_DEFAULTS]
check("по умолчанию: три тарифа салона с ценами владельца", codes == ["beauty_start", "beauty_ai", "beauty_pro"]
      and [d["price"] for d in pc.BEAUTY_DEFAULTS] == [1990, 3990, 5990]
      and [d["limits"]["staff"] for d in pc.BEAUTY_DEFAULTS] == [3, 10, 20])
check("уровни: Beauty Start и AI — как Старт (reactive), Beauty Pro — как Profi (full)",
      [d["limits"]["agent_mode"] for d in pc.BEAUTY_DEFAULTS] == ["reactive", "reactive", "full"]
      and any("сам пишет" in f for f in pc.BEAUTY_DEFAULTS[2]["features"])
      and not any("сам пишет" in f for d in pc.BEAUTY_DEFAULTS[:2] for f in d["features"]))
check("по умолчанию: «что входит» не длиннее лимита редактора",
      all(len(d["features"]) <= pc.MAX_FEATURES and all(len(f) <= pc.MAX_FEATURE_LEN for f in d["features"])
          for d in pc.BEAUTY_DEFAULTS))
check("по умолчанию: Beauty Pro — 10 000 кредитов в месяц, Start/AI — 0, один тариф пробного периода, коды ≤ 20",
      [d["limits"]["credits_month"] for d in pc.BEAUTY_DEFAULTS] == [0, 0, 10000]
      and pc.PRO_CREDITS_FEATURE in pc.BEAUTY_DEFAULTS[2]["features"]
      and sum(1 for d in pc.BEAUTY_DEFAULTS if d["limits"]["trial"]) == 1
      and all(len(c) <= 20 and c.startswith(pc.BEAUTY_PREFIX) for c in codes)
      and all(len(d["name"]) <= 50 for d in pc.BEAUTY_DEFAULTS))

from backend.api.payments import (PERIOD_DISCOUNTS, _periods_text, calculate_period_price,  # noqa: E402
                                  get_all_plans_with_prices)
from backend.services.payment_service import get_subscription_days_by_duration  # noqa: E402
view = pc.beauty_view(SubscriptionPlan(code="beauty_ai", name="Beauty AI", price=3990, max_assistants=5, product="beauty",
                                       limits={"staff": 10}, features=["Журнал", " "], badge="Хит", sort_order=20))
BD = pc.BEAUTY_PERIOD_DISCOUNTS
check("вид тарифа: цена за период тем же расчётом скидок",
      view["periods"][6]["price"] == calculate_period_price(3990, 6, BD)["final_price"]
      and view["periods"][12]["price"] == calculate_period_price(3990, 12, BD)["final_price"] and view["periods"][1]["price"] == 3990)

# сроки Beauty (решение владельца 26.11.2026)
per = view["periods"]
check("сроки Beauty: 1 · 3 · 6 мес · год, скидки 0/10/20/30%", list(per) == [1, 3, 6, 12]
      and BD == {1: 0, 3: 10, 6: 20, 12: 30} and pc.BEAUTY_PERIOD_BADGES == {3: "Выгодно"})
check("3 месяца: −10%, 90 дней, экономия в сомах и плашка «Выгодно»", per[3]["price"] == 10773 and per[3]["savings"] == 1197
      and per[3]["savings_formatted"] == "1 197 сом" and per[3]["full_price"] == 11970 and per[3]["days"] == 90
      and per[3]["badge"] == "Выгодно" and per[3]["label"] == "3 месяца" and per[3]["discount_percent"] == 10, per[3])
check("6 мес и год: экономия сразу в ответе окна оплаты", per[6]["price"] == 19152 and per[6]["savings_formatted"] == "4 788 сом"
      and per[12]["price"] == 33516 and per[12]["savings"] == 14364 and per[12]["label"] == "1 год"
      and per[1]["savings_formatted"] is None and per[1]["badge"] is None and per[6]["badge"] is None)
odd = pc.beauty_view(SubscriptionPlan(code="beauty_o", name="O", price=2495, max_assistants=1, product="beauty", limits={}))["periods"][3]
check("нецелая сумма из админки: цена + экономия = полная цена (окно не пересчитывает)",
      odd["price"] + odd["savings"] == odd["full_price"] == 7485, odd)
check("3 месяца = 90 дней подписки", get_subscription_days_by_duration(3) == 90 and get_subscription_days_by_duration(6) == 180)
try:
    calculate_period_price(7500, 3)
    no3 = False
except ValueError:
    no3 = True
check("прежние тарифы — по-прежнему 1 · 6 мес · год", no3 and 3 not in PERIOD_DISCOUNTS
      and sorted(get_all_plans_with_prices(None)["plans"]["start"]["periods"]) == [1, 6, 12])
check("отказ называет сроки словами", _periods_text(PERIOD_DISCOUNTS) == "1 месяц, 6 месяцев или 1 год"
      and _periods_text(BD) == "1 месяц, 3 месяца, 6 месяцев или 1 год")
check("вид тарифа: лимит словами, пустые строки убраны", view["staff_label"] == "До 10 сотрудников" and view["features"] == ["Журнал"]
      and view["product"] == "beauty" and view["individual"] is False)
check("текст лимита", "Beauty Start" in pc.limit_message({"plan_name": "Beauty Start", "limit": 3}) and "до 3" in
      pc.limit_message({"plan_name": "Beauty Start", "limit": 3}))

future = datetime.now(timezone.utc) + timedelta(days=10)
u = User(email="x@test.kg")
u.subscription_plan_rel = SubscriptionPlan(code="beauty_ai", name="Beauty AI", price=3990, max_assistants=5, product="beauty",
                                           limits={"staff": 10, "agent_mode": "reactive"})
u.subscription_end_date = future
check("Beauty-тариф: бот отвечает и записывает (reactive), без автономности",
      u.is_beauty_plan() and u.agent_mode() == "reactive" and not u.has_agent_access() and u.agent_subscription_status() == "active")
u.subscription_plan_rel.limits = {"staff": 10, "agent_mode": "full"}
check("Beauty-тариф с автономным агентом (настройка суперадмина) = full", u.agent_mode() == "full" and u.has_agent_access())
u.subscription_end_date = datetime.now(timezone.utc) - timedelta(days=1)
check("истёкший Beauty-тариф — доступа нет", not u.is_beauty_plan() and u.agent_mode() is None)
u2 = User(email="y@test.kg")
u2.subscription_plan_rel = SubscriptionPlan(code="start", name="Старт", price=7500, max_assistants=5, product="voksy")
u2.subscription_end_date = future
check("Старт по-прежнему reactive и не Beauty", u2.agent_mode() == "reactive" and not u2.is_beauty_plan())
u3 = User(email="z@test.kg", business_niche="nails")
check("салон по нише, даже без тарифа", pc.is_beauty_user(u3) and not pc.is_beauty_user(u2) and pc.is_beauty_user(u))

if not PG:
    print("SKIP: часть 2 — BEAUTY_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ───────────────────────── часть 2: PostgreSQL ─────────────────────────
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.subscription import PaymentTransaction  # noqa: E402
from backend.models.team import TeamMember  # noqa: E402
from backend.models.wallet_transaction import WalletTransaction  # noqa: E402
from backend.models.booking import BookingResource  # noqa: E402
from backend.models.admin_audit import AdminAuditLog  # noqa: E402
from backend.core.security import create_jwt_token, hash_password  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)

import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# «Старая» таблица прода: без колонок каталога и без Beauty-тарифов
with engine.begin() as conn:
    for col in ("product", "limits", "features", "badge", "sort_order", "is_public", "for_user_id", "admin_updated_at"):
        conn.execute(text(f"ALTER TABLE subscription_plans DROP COLUMN IF EXISTS {col}"))
    conn.execute(text("INSERT INTO subscription_plans (id, code, name, price, max_assistants, description, is_active) "
                      "VALUES (gen_random_uuid(), 'start', 'Тариф Старт', 10, 5, 'старт', true)"))
app_module.ensure_plan_catalog()
app_module.ensure_plan_catalog()     # второй воркер / повторный старт
app_module.ensure_user_profile_columns()
app_module.ensure_team_tables()
app_module.ensure_booking_tables()
app_module.ensure_plan_prices()
with engine.begin() as conn:
    cols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='subscription_plans'"))}
    seeded = conn.execute(text("SELECT code, price, product, (limits->>'staff')::int FROM subscription_plans WHERE product='beauty' ORDER BY sort_order")).fetchall()
    start_row = conn.execute(text("SELECT price, product FROM subscription_plans WHERE code='start'")).first()
check("старт: колонки каталога добавлены в старую таблицу", {"product", "limits", "features", "badge", "sort_order", "is_public",
                                                             "for_user_id", "admin_updated_at"} <= cols)
check("старт: Beauty-тарифы заведены ровно один раз", [(r[0], float(r[1]), r[3]) for r in seeded] ==
      [("beauty_start", 1990.0, 3), ("beauty_ai", 3990.0, 10), ("beauty_pro", 5990.0, 20)], seeded)
# Прод 25.11.2026: Beauty Pro заведён с 0 кредитов → один раз становится 10 000; поставленные потом
# суперадмином 0 повторный старт не трогает.
with engine.begin() as conn:
    conn.execute(text("UPDATE subscription_plans SET limits = jsonb_set(limits - 'credits_default_v', '{credits_month}', '0'), "
                      "features = features - 0 WHERE code='beauty_pro'"))
    conn.execute(text("UPDATE subscription_plans SET features = (SELECT jsonb_agg(f) FROM jsonb_array_elements(features) f "
                      "WHERE f::text NOT LIKE '%кредитов ИИ%') WHERE code='beauty_pro'"))
app_module.ensure_plan_catalog()
with engine.begin() as conn:
    pro_row = conn.execute(text("SELECT (limits->>'credits_month')::int, features::text FROM subscription_plans WHERE code='beauty_pro'")).first()
check("старт: Beauty Pro с 0 кредитов → 10 000 и строка «что входит»", pro_row[0] == 10000 and "10 000 кредитов ИИ" in pro_row[1]
      and pro_row[1].count("кредитов ИИ") == 1, pro_row)
with engine.begin() as conn:
    conn.execute(text("UPDATE subscription_plans SET limits = jsonb_set(limits, '{credits_month}', '0') WHERE code='beauty_pro'"))
app_module.ensure_plan_catalog()
with engine.begin() as conn:
    pro_c = conn.execute(text("SELECT (limits->>'credits_month')::int FROM subscription_plans WHERE code='beauty_pro'")).scalar()
    conn.execute(text("UPDATE subscription_plans SET limits = jsonb_set(limits, '{credits_month}', '10000') WHERE code='beauty_pro'"))
check("старт: 0, поставленный суперадмином после обновления, не перезаписывается", pro_c == 0, pro_c)
# Прод 28.11.2026: у Beauty Pro в базе agent_mode «reactive» — салон видел «Агент обзвона» с замком.
# Старт один раз делает его «full»; осознанную правку суперадмина потом не трогает.
with engine.begin() as conn:
    conn.execute(text("UPDATE subscription_plans SET limits = jsonb_set(limits - 'agent_mode_v', '{agent_mode}', '\"reactive\"') "
                      "WHERE code='beauty_pro'"))
app_module.ensure_plan_catalog()
with engine.begin() as conn:
    pro_mode = conn.execute(text("SELECT limits->>'agent_mode' FROM subscription_plans WHERE code='beauty_pro'")).scalar()
    conn.execute(text("UPDATE subscription_plans SET limits = jsonb_set(limits, '{agent_mode}', '\"reactive\"') WHERE code='beauty_pro'"))
app_module.ensure_plan_catalog()
with engine.begin() as conn:
    pro_mode2 = conn.execute(text("SELECT limits->>'agent_mode' FROM subscription_plans WHERE code='beauty_pro'")).scalar()
    conn.execute(text("UPDATE subscription_plans SET limits = jsonb_set(limits, '{agent_mode}', '\"full\"') WHERE code='beauty_pro'"))
check("старт: Beauty Pro с режимом «как Старт» → автономный агент", pro_mode == "full", pro_mode)
check("старт: режим, поставленный суперадмином после починки, не перезаписывается", pro_mode2 == "reactive", pro_mode2)
check("старт: прежний тариф остался voksy, цена из конфига", start_row and start_row[1] == "voksy" and float(start_row[0]) == 7500.0, start_row)

cl = TestClient(app_module.app, raise_server_exceptions=False)
now = datetime.now(timezone.utc)
db = SessionLocal()


def plan(code):
    return db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).first()


def voksy_plan(code):
    p = plan(code)
    if not p:
        p = SubscriptionPlan(code=code, name=code.title(), price=100, max_assistants=3, description=code, is_active=True)
        db.add(p)
        db.flush()
    return p


def mk(email, plan_code=None, days=20, wallet_som=0, **kw):
    u = User(email=email, password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
             wallet_balance=int(wallet_som * 100), **kw)
    if plan_code:
        p = plan(plan_code) or voksy_plan(plan_code)
        u.subscription_plan_id = p.id
        u.subscription_end_date = now + timedelta(days=days)
    db.add(u)
    db.flush()
    return u


def H(u):
    return {"Authorization": f"Bearer {create_jwt_token(u.id)}"}


def err(r):
    try:
        d = r.json()
    except Exception:
        return ""
    x = d.get("detail") or d.get("message")
    if isinstance(x, dict):
        return x.get("message") or ""
    return x or ""


def fresh(u):
    db.expire_all()
    return db.query(User).filter(User.id == u.id).first()


salon = mk("salon@beauty.kg", None, business_niche="beauty_salon", first_name="Айпери")
salon2 = mk("salon2@beauty.kg", None, business_niche="barbershop", first_name="Бек")
shop = mk("shop@clothes.kg", "start", business_niche="clothing", wallet_som=1000, first_name="Магазин")
admin = mk("admin@voksy.test", None, is_admin=True, first_name="Админ")
db.commit()

# ── окно оплаты
r = cl.get("/api/payments/my-plans", headers=H(salon))
j = r.json() if r.status_code == 200 else {}
check("салону — тарифы Voksy Beauty", r.status_code == 200 and j.get("product") == "beauty"
      and j.get("order") == ["beauty_start", "beauty_ai", "beauty_pro"], r.text[:200])
check("по умолчанию выделен «Хит» Beauty AI, лимит словами", j.get("default_plan") == "beauty_ai"
      and (j.get("plans") or {}).get("beauty_start", {}).get("staff_label") == "До 3 сотрудников")
per_j = (j.get("plans") or {}).get("beauty_ai", {}).get("periods") or {}
check("окно оплаты салона: 4 срока, цена, экономия и плашка — с сервера", sorted(per_j, key=int) == ["1", "3", "6", "12"]
      and per_j["3"]["price"] == 10773 and per_j["3"]["badge"] == "Выгодно" and per_j["12"]["savings"] == 14364
      and j.get("discounts") == {"1": 0, "3": 10, "6": 20, "12": 30}, (list(per_j), j.get("discounts")))
r = cl.get("/api/payments/my-plans", headers=H(shop))
j = r.json() if r.status_code == 200 else {}
check("остальным — прежние тарифы", r.status_code == 200 and j.get("product") == "voksy"
      and set(j.get("plans") or {}) == {"ai_voice", "start", "profi"}, r.text[:160])
check("остальным — без 3 месяцев", sorted((j.get("plans") or {}).get("start", {}).get("periods") or {}, key=int) == ["1", "6", "12"])
check("без входа — 401/403", cl.get("/api/payments/my-plans").status_code in (401, 403))

# ── оплата (Finik подменён)
from backend.api import payments as pay_api  # noqa: E402
from backend.services.payment_service import FinikPaymentService  # noqa: E402


async def _fake_create(**kw):
    return "https://pay.test/" + kw["payment_id"]
pay_api.FinikService.is_configured = staticmethod(lambda: True)
pay_api.FinikService.create_payment = staticmethod(_fake_create)


def buy(u, code, months=1):
    return cl.post("/api/payments/create-payment", headers=H(u), json={"plan_code": code, "duration_months": months})


r = buy(salon, "start")
check("салон не может купить Старт", r.status_code == 400 and "Beauty" in err(r), r.text[:160])
r = buy(shop, "beauty_ai")
check("магазин не может купить Beauty", r.status_code == 400 and "салон" in err(r).lower(), r.text[:160])
r = buy(salon, "beauty_nope")
check("несуществующий тариф — отказ", r.status_code == 400, r.text[:120])
r = buy(salon, "x" * 30)
check("слишком длинный код — 422", r.status_code == 422)
r = buy(salon, "beauty_ai", 6)
j = r.json() if r.status_code == 200 else {}
check("салон покупает Beauty AI на 6 мес: сумма из базы со скидкой", r.status_code == 200 and j.get("amount") == 19152
      and j.get("plan_info", {}).get("name") == "Beauty AI", r.text[:200])
r = buy(shop, "start")
check("магазин покупает Старт как раньше", r.status_code == 200 and r.json().get("amount") == 7500, r.text[:160])
r = buy(shop, "start", 3)
check("Старт на 3 месяца — понятный отказ со сроками", r.status_code == 400 and "Такого срока" in err(r)
      and "6 месяцев" in err(r) and "3 месяца" not in err(r), r.text[:200])
check("срок 2 месяца — 422", buy(salon, "beauty_ai", 2).status_code == 422)


def apply_last(u):
    tx = (db.query(PaymentTransaction).filter(PaymentTransaction.user_id == u.id)
          .order_by(PaymentTransaction.created_at.desc()).first())
    tx.status = "success"
    db.commit()
    ok_ = asyncio.run(FinikPaymentService.apply_successful_payment(db, tx))
    db.commit()
    return ok_, tx


r = buy(salon, "beauty_ai", 1)
ok_, tx = apply_last(salon)
s = fresh(salon)
check("после оплаты: Beauty AI на 30 дней, бот reactive", ok_ and s.subscription_plan_rel.code == "beauty_ai"
      and s.is_beauty_plan() and s.agent_mode() == "reactive"
      and 29 <= (s.subscription_end_date - now).days <= 30, s.subscription_end_date)
check("после оплаты: кредиты не начислены (ИИ — по кредитам отдельно)", int(s.credits_balance or 0) == 0, s.credits_balance)
check("повторный webhook не продлевает второй раз", asyncio.run(FinikPaymentService.apply_successful_payment(db, tx))
      and 29 <= (fresh(salon).subscription_end_date - now).days <= 30)

# 3 месяца — только у Beauty: −10% и 90 дней
salon3 = mk("salon3@beauty.kg", None, business_niche="cosmetology", first_name="Жылдыз")
db.commit()
r = buy(salon3, "beauty_start", 3)
j = r.json() if r.status_code == 200 else {}
check("салон покупает Beauty Start на 3 месяца: −10%, 90 дней", r.status_code == 200 and j.get("amount") == 5373
      and (j.get("period_info") or {}).get("days") == 90 and j["period_info"]["discount_percent"] == 10
      and j["period_info"]["label"] == "3 месяца" and j["period_info"]["savings"] == 597, r.text[:200])
ok_, tx3 = apply_last(salon3)
s3 = fresh(salon3)
check("после оплаты 3 месяцев: тариф на 90 дней", ok_ and s3.subscription_plan_rel.code == "beauty_start"
      and 89 <= (s3.subscription_end_date - now).days <= 90, s3.subscription_end_date)

# ── редактор суперадмина
check("редактор: не админу — 403", cl.get("/api/admin/plans/catalog", headers=H(salon)).status_code == 403
      and cl.patch("/api/admin/plans/catalog/beauty_ai", headers=H(salon), json={"price": 1}).status_code == 403)
r = cl.get("/api/admin/plans/catalog", headers=H(admin))
j = r.json() if r.status_code == 200 else {}
rows = {p["code"]: p for p in j.get("beauty") or []}
check("редактор: Beauty с правкой + прежние без правки + подписчики", r.status_code == 200
      and set(rows) == {"beauty_start", "beauty_ai", "beauty_pro"} and rows["beauty_ai"]["subscribers"] == 1
      and {p["code"] for p in j.get("voksy") or []} == {"ai_voice", "start", "profi"}
      and not any(p["editable"] for p in j.get("voksy") or []), r.text[:200])
check("редактор: скидки за срок — Beauty (с 3 мес) и прежние отдельно",
      j.get("discounts") == {"1": 0, "3": 10, "6": 20, "12": 30} and j.get("voksy_discounts") == {"1": 0, "6": 20, "12": 30},
      (j.get("discounts"), j.get("voksy_discounts")))
r = cl.patch("/api/admin/plans/catalog/beauty_pro", headers=H(admin),
             json={"price": 6490, "credits_month": 1000, "features": ["До 20 сотрудников", "Журнал записи"], "badge": "Новинка"})
check("редактор: правка цены/кредитов/текста", r.status_code == 200
      and {p["code"]: p for p in r.json()["beauty"]}["beauty_pro"]["price"] == 6490.0, r.text[:200])
j = cl.get("/api/payments/my-plans", headers=H(salon2)).json()
check("салон видит новую цену сразу", j["plans"]["beauty_pro"]["base_price"] == 6490.0
      and j["plans"]["beauty_pro"]["features"] == ["До 20 сотрудников", "Журнал записи"] and j["plans"]["beauty_pro"]["badge"] == "Новинка")
check("редактор: прежний тариф не правится", cl.patch("/api/admin/plans/catalog/start", headers=H(admin), json={"price": 1}).status_code == 400)
r = cl.patch("/api/admin/plans/catalog/beauty_pro", headers=H(admin), json={"price": -5})
check("редактор: неверная цена — понятный отказ", r.status_code == 400 and "Цена" in err(r), r.text[:120])
check("редактор: неизвестный тариф — 404", cl.patch("/api/admin/plans/catalog/beauty_zzz", headers=H(admin), json={"price": 5}).status_code == 404)
audit = db.query(AdminAuditLog).filter(AdminAuditLog.action == "plan").all()
check("редактор: правка в журнале с «было/стало»", len(audit) == 1 and audit[0].details["diff"]["price"] == [5990.0, 6490],
      [a.details for a in audit])
check("ensure_plan_prices не трогает Beauty", (app_module.ensure_plan_prices() or True) and float(plan("beauty_pro").price) == 6490.0)

# кредиты, включённые в тариф, — за каждый оплаченный месяц, один раз на платёж
buy(salon2, "beauty_pro", 6)
apply_last(salon2)
check("кредиты из тарифа: 1000 × 6 мес", int(fresh(salon2).credits_balance or 0) == 6000, fresh(salon2).credits_balance)
check("Beauty Pro = уровень Profi: автономный агент", fresh(salon2).agent_mode() == "full" and fresh(salon2).has_agent_access())

# индивидуальный тариф
r = cl.post("/api/admin/plans/catalog", headers=H(admin), json={"name": "Салон Айпери", "price": 2500, "staff": 7,
                                                                  "features": ["Особые условия"], "for_user": "SALON@beauty.kg"})
ind_code = r.json().get("created") if r.status_code == 200 else None
check("индивидуальный тариф создан для клиента", r.status_code == 200 and ind_code and ind_code.startswith("beauty_"), r.text[:200])
j = cl.get("/api/payments/my-plans", headers=H(salon)).json()
check("свой клиент видит его первым и выбранным", j["order"][0] == ind_code and j["default_plan"] == ind_code
      and j["plans"][ind_code]["individual"] is True and j["plans"][ind_code]["staff_label"] == "До 7 сотрудников")
check("другой салон его не видит и не купит", ind_code not in cl.get("/api/payments/my-plans", headers=H(salon2)).json()["plans"]
      and buy(salon2, ind_code).status_code == 400)
check("свой клиент может оплатить", buy(salon, ind_code).status_code == 200)
r = cl.post("/api/admin/plans/catalog", headers=H(admin), json={"name": "Кому-то", "price": 100, "staff": 1, "for_user": "nobody@x.kg"})
check("индивидуальный: неизвестная почта — отказ", r.status_code == 400 and "не найден" in err(r))
r = cl.post("/api/admin/plans/catalog", headers=H(admin), json={"name": "Бесплатный", "price": 0, "staff": 2, "for_user": "salon2@beauty.kg"})
free_code = r.json().get("created")
check("бесплатный тариф нельзя «оплатить»", buy(salon2, free_code).status_code == 400 and "бесплатн" in err(buy(salon2, free_code)))

# скрытый и выключенный
cl.patch("/api/admin/plans/catalog/beauty_ai", headers=H(admin), json={"is_public": False})
check("скрытый тариф: новые салоны не видят", "beauty_ai" not in cl.get("/api/payments/my-plans", headers=H(salon2)).json()["plans"])
check("скрытый тариф: кто на нём — видит и продлевает", "beauty_ai" in cl.get("/api/payments/my-plans", headers=H(fresh(salon))).json()["plans"]
      and buy(salon, "beauty_ai").status_code == 200)
check("скрытый тариф: другому салону купить нельзя", buy(salon2, "beauty_ai").status_code == 400)
cl.patch("/api/admin/plans/catalog/beauty_ai", headers=H(admin), json={"is_public": True, "is_active": False})
check("выключенный тариф не продаётся никому", buy(salon, "beauty_ai").status_code == 400
      and "beauty_ai" not in cl.get("/api/payments/my-plans", headers=H(salon2)).json()["plans"])
cl.patch("/api/admin/plans/catalog/beauty_ai", headers=H(admin), json={"is_active": True})

# ── сотрудники салона: в пределах лимита и без денег
owner = mk("owner@studio.kg", "beauty_start", days=15, business_niche="nails", wallet_som=0, first_name="Нуриза")
db.commit()
plan_end = fresh(owner).subscription_end_date


def add(u, name, email):
    return cl.post("/api/team", headers=H(u), json={"name": name, "email": email, "permissions": ["inbox", "booking"]})


res = [add(owner, f"Мастер {i}", f"m{i}@studio.kg") for i in range(1, 4)]
check("салон: 3 сотрудника на Beauty Start без денег на кошельке", all(r_.status_code == 200 for r_ in res), [r_.text[:120] for r_ in res])
check("салон: с кошелька ничего не списано", db.query(WalletTransaction).filter(WalletTransaction.user_id == owner.id).count() == 0)
mem = db.query(TeamMember).filter(TeamMember.owner_id == owner.id).order_by(TeamMember.created_at).all()
check("салон: срок места = срок тарифа", all(m.paid_until and abs((m.paid_until - plan_end).total_seconds()) < 2 for m in mem))
r = add(owner, "Мастер 4", "m4@studio.kg")
check("салон: четвёртый — 402 plan_limit с текстом тарифа", r.status_code == 402 and "Beauty Start" in err(r) and "до 3" in err(r), r.text[:200])
t = cl.get("/api/team", headers=H(owner)).json()
check("салон: сводка «входит в тариф 3 из 3»", t.get("included") is True and t.get("limit") == 3 and t.get("seat_som") == 0
      and t.get("monthly_som") == 0 and t.get("can_add") is False and t.get("plan_name") == "Beauty Start", t)
cl.post(f"/api/team/{mem[2].id}/deactivate", headers=H(owner))
check("салон: выключили одного — место свободно", add(owner, "Мастер 4", "m4@studio.kg").status_code == 200)
r = cl.post(f"/api/team/{mem[2].id}/activate", headers=H(owner))
check("салон: включить третьего сверх лимита нельзя", r.status_code == 402, r.text[:160])

# продление вместе с тарифом
o = fresh(owner)
o.subscription_end_date = now + timedelta(days=45)       # оплатил ещё месяц
for m in db.query(TeamMember).filter(TeamMember.owner_id == owner.id, TeamMember.is_active.is_(True)):
    m.paid_until = now - timedelta(minutes=1)
db.commit()
from backend.services import team_service as ts  # noqa: E402
out = ts.renew_due_seats()
act = db.query(TeamMember).filter(TeamMember.owner_id == owner.id, TeamMember.is_active.is_(True)).all()
check("продление: места продлены до нового срока тарифа, без списаний", out["renewed"] == 3 and len(act) == 3
      and all(abs((m.paid_until - fresh(owner).subscription_end_date).total_seconds()) < 2 for m in act)
      and db.query(WalletTransaction).filter(WalletTransaction.user_id == owner.id).count() == 0, out)

# понижение лимита: остаются первые по дате добавления
cl.patch("/api/admin/plans/catalog/beauty_start", headers=H(admin), json={"staff": 2})
for m in db.query(TeamMember).filter(TeamMember.owner_id == owner.id, TeamMember.is_active.is_(True)):
    m.paid_until = now - timedelta(minutes=1)
db.commit()
out = ts.renew_due_seats()
db.expire_all()
act = db.query(TeamMember).filter(TeamMember.owner_id == owner.id, TeamMember.is_active.is_(True)).order_by(TeamMember.created_at).all()
off = db.query(TeamMember).filter(TeamMember.owner_id == owner.id, TeamMember.status_reason == "limit").all()
check("понижение: остались двое первых, новый выключен «лимит»", [m.name for m in act] == ["Мастер 1", "Мастер 2"]
      and [m.name for m in off] == ["Мастер 4"], [(m.name, m.is_active, m.status_reason) for m in db.query(TeamMember).filter(TeamMember.owner_id == owner.id)])
check("понижение: владельцу сообщение с лимитом", any("до 2" in txt for _, txt in out["notify"]), out["notify"])
cl.patch("/api/admin/plans/catalog/beauty_start", headers=H(admin), json={"staff": 3})

# тариф кончился
o = fresh(owner)
o.subscription_end_date = now - timedelta(hours=1)
for m in db.query(TeamMember).filter(TeamMember.owner_id == owner.id, TeamMember.is_active.is_(True)):
    m.paid_until = now - timedelta(hours=1)
db.commit()
ts.renew_due_seats()
db.expire_all()
check("тариф кончился: сотрудники выключены «тариф»", db.query(TeamMember).filter(TeamMember.owner_id == owner.id,
                                                                                    TeamMember.is_active.is_(True)).count() == 0
      and db.query(TeamMember).filter(TeamMember.owner_id == owner.id, TeamMember.status_reason == "plan").count() == 2)

# пробный период салона — лимиты тарифа с меткой trial (Beauty AI, 10)
trial_salon = mk("trial@spa.kg", "free", days=3, business_niche="massage_spa", wallet_som=0)
trial_shop = mk("trial@shop.kg", "free", days=3, business_niche="clothing", wallet_som=0)
db.commit()
t = cl.get("/api/team", headers=H(trial_salon)).json()
check("пробный салон: места входят, лимит Beauty AI", t.get("included") is True and t.get("limit") == 10 and t.get("trial") is True, t)
check("пробный салон: добавить без денег", add(trial_salon, "Админ", "adm@spa.kg").status_code == 200)
r = add(trial_shop, "Продавец", "seller@shop.kg")
check("пробный магазин: прежние правила (нужно 150 сом)", r.status_code == 402 and "150" in err(r), r.text[:160])

# ── мастера в журнале
paid = mk("paid@barber.kg", "beauty_start", business_niche="barbershop")
db.commit()
codes_ = [cl.post("/api/booking/resources", headers=H(paid), json={"name": f"Барбер {i}"}).status_code for i in range(1, 4)]
r = cl.post("/api/booking/resources", headers=H(paid), json={"name": "Барбер 4"})
check("журнал: три мастера в тарифе, четвёртый — 402", codes_ == [200, 200, 200] and r.status_code == 402 and "мастеров" in err(r), (codes_, r.text[:160]))
r = cl.post("/api/booking/resources", headers=H(paid), json={"name": "Выключенный", "is_active": False})
check("журнал: выключенного мастера завести можно", r.status_code == 200, r.text[:120])
rid = r.json().get("resource", {}).get("id")
check("журнал: включить его сверх лимита нельзя", cl.patch(f"/api/booking/resources/{rid}", headers=H(paid), json={"is_active": True}).status_code == 402)
codes_ = [cl.post("/api/booking/resources", headers=H(shop), json={"name": f"Продавец {i}"}).status_code for i in range(1, 6)]
check("журнал: у других ниш лимита тарифа нет", all(c == 200 for c in codes_), codes_)

# ── админ назначает Beauty вручную; меню и главная знают о салоне
r = cl.post(f"/api/admin/users/{salon2.id}/subscription", headers=H(admin), json={"plan_code": "beauty_pro", "duration_days": 30, "mode": "reset"})
check("админ назначил Beauty Pro вручную (уровень Profi)", r.status_code == 200
      and fresh(salon2).subscription_plan_rel.code == "beauty_pro" and fresh(salon2).agent_mode() == "full", r.text[:200])
check("список тарифов админки: Beauty Pro даёт автономного агента", any(
    p["code"] == "beauty_pro" and p.get("grants_agent_access") for p in cl.get("/api/admin/plans", headers=H(admin)).json()))

# Beauty Pro кредитов в тарифе не имеет — стартовые 1500 при первом боте получает (Profi — нет, у него свои)
pro_new = mk("pro@beauty.kg", "beauty_pro", business_niche="cosmetology")
db.commit()
r = cl.post("/api/agent/create", headers=H(pro_new), json={
    "name": "Бот салона", "assistant_type": "eleven", "doc_who_am_i": "Салон", "doc_who_we_call": "клиенты",
    "doc_how_we_talk": "вежливо", "doc_what_we_offer": "чистка лица", "doc_rules_and_goals": "записывать"})
check("Beauty Pro: первый бот со стартовыми кредитами", r.status_code == 200 and r.json().get("trial_activated") is True
      and int(fresh(pro_new).credits_balance or 0) >= 1500 and fresh(pro_new).agent_mode() == "full", r.text[:200])
check("цена Beauty при ручном назначении не «чинится»", float(plan("beauty_pro").price) == 6490.0)
lst = cl.get("/api/admin/plans", headers=H(admin)).json()
check("список тарифов админки включает Beauty", {"beauty_start", "beauty_ai", "beauty_pro"} <= {p["code"] for p in lst})
me = cl.get("/api/users/me", headers=H(salon2)).json()
check("/api/users/me → business.beauty у салона", (me.get("business") or {}).get("beauty") is True, me.get("business"))
check("/api/users/me → business.beauty у магазина нет", (cl.get("/api/users/me", headers=H(shop)).json().get("business") or {}).get("beauty") is False)
dash = cl.get("/api/dashboard/summary", headers=H(salon2)).json()
check("главная: тариф салона помечен (без апселла Profi)", (dash.get("plan") or {}).get("beauty") is True
      and (dash.get("plan") or {}).get("product") == "beauty", dash.get("plan"))

# оплаченный тариф без даты начала (ручная правка, старые записи) не заменяется пробным периодом
nostart = mk("nostart@beauty.kg", "beauty_ai", business_niche="beauty_salon")
db.commit()
r = cl.get("/api/subscriptions/my-subscription", headers=H(nostart))
check("my-subscription не затирает оплаченный тариф пробным", r.status_code == 200
      and fresh(nostart).subscription_plan_rel.code == "beauty_ai", r.text[:160])

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
