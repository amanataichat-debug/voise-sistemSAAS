"""Финансы (ветка 1811-niche-admin-speed, 19.11.2026): выручка, расходы, зарплаты мастеров, прибыль.

Часть 1 (без базы): доля ежемесячного расхода по дням (month_share) — неделя, месяц целиком,
стык двух месяцев, начало посреди периода, дата окончания.

Часть 2 (PostgreSQL, FINANCE_TEST_DATABASE_URL или ANALYTICS_TEST_DATABASE_URL):
- выручка = записи «пришёл» + заказы (не отменённые); «не пришёл» и «записан» в выручку не идут;
- расходы: разовый в периоде / вне периода, ежемесячный долей; зарплата мастера = % с его выручки + оклад;
- прибыль, рентабельность, категории, ряд по дням; сравнение с прошлым периодом;
- API: добавить / изменить / удалить расход, оплата мастера; неверные данные — 400; чужой расход — 404;
- сотрудник кабинета в финансы не попадает (403 до эндпоинта);
- отчёт аналитики: раздел finance и плитка «Прибыль»; у магазина без записей нет пустого раздела «Записи».
"""
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("FINANCE_TEST_DATABASE_URL") or os.environ.get("ANALYTICS_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="finance-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import finance_service as fs  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def near(a, b, eps=0.6):
    return abs(float(a) - float(b)) <= eps


# ── часть 1: доля ежемесячного расхода ──
nov1, nov30 = date(2026, 11, 1), date(2026, 11, 30)
check("месяц целиком = вся сумма", near(fs.month_share(30000, nov1, nov30, date(2026, 1, 1), None), 30000))
check("неделя ноября = 7/30", near(fs.month_share(30000, date(2026, 11, 2), date(2026, 11, 8), date(2026, 1, 1), None), 7000))
check("стык месяцев: 31 окт + 1 ноя = 1/31 + 1/30",
      near(fs.month_share(31000, date(2026, 10, 31), date(2026, 11, 1), date(2026, 1, 1), None), 1000 + 31000 / 30))
check("начался 16 ноября — половина месяца", near(fs.month_share(30000, nov1, nov30, date(2026, 11, 16), None), 15000))
check("кончился 10 ноября — треть месяца", near(fs.month_share(30000, nov1, nov30, date(2026, 1, 1), date(2026, 11, 10)), 10000))
check("начался после периода — ноль", fs.month_share(30000, nov1, nov30, date(2026, 12, 5), None) == 0)

if not PG:
    print("SKIP часть 2: FINANCE_TEST_DATABASE_URL не задан")
    print("ПРОВАЛОВ:", failures)
    sys.exit(1 if failures else 0)

from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.models.booking import Booking, BookingResource  # noqa: E402
from backend.models.order import Order  # noqa: E402
from backend.core.security import create_jwt_token, hash_password  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services import analytics_service as an  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app_module.ensure_booking_tables()
app_module.ensure_order_tables()
app_module.ensure_team_tables()
app_module.ensure_finance_tables()
cl = TestClient(app_module.app, raise_server_exceptions=False)
db = SessionLocal()
B = bs.BISHKEK
now = datetime.now(timezone.utc)
today = datetime.now(B).date()

plan = SubscriptionPlan(code="start", name="Start", price=7500, max_assistants=3, description="s", is_active=True)
db.add(plan)
db.flush()


def mk_user(email, niche):
    u = User(email=email, password_hash=hash_password("Passw0rd!"), email_verified=True, first_name="Тест",
             subscription_plan_id=plan.id, subscription_start_date=now - timedelta(days=40),
             subscription_end_date=now + timedelta(days=20), business_niche=niche, business_profile={"answers": {}},
             wallet_balance=100000)
    db.add(u)
    db.flush()
    a = AgentConfig(user_id=u.id, name="Бот")
    db.add(a)
    db.flush()
    return u, a


owner, agent = mk_user("salon@fin.kg", "beauty_salon")
other, _oa = mk_user("other@fin.kg", "beauty_salon")
shop, _sa = mk_user("shop@fin.kg", "clothing")
svc = AgentKnowledgeEntry(user_id=owner.id, agent_config_id=agent.id, kind="product", title="Маникюр", body="", price=1000, duration_min=60)
db.add(svc)
m1 = BookingResource(user_id=owner.id, name="Айдана", schedule={}, is_active=True, pay_percent=40)
m2 = BookingResource(user_id=owner.id, name="Бегимай", schedule={}, is_active=True, pay_fixed=30000)
db.add_all([m1, m2])
db.flush()


def booking(res, day, status, price):
    st = datetime.combine(day, time(11, 0), B)
    db.add(Booking(user_id=owner.id, resource_id=res.id, service_entry_id=svc.id, service_title="Маникюр", client_name="К",
                   start_at=st.astimezone(timezone.utc), end_at=(st + timedelta(hours=1)).astimezone(timezone.utc),
                   status=status, price=price, source="owner"))


# Текущий 7-дневный период: today-6 … today
d1, d2 = today - timedelta(days=1), today - timedelta(days=2)
booking(m1, d1, "arrived", 2000)
booking(m1, d2, "arrived", 3000)
booking(m2, d1, "arrived", 1500)
booking(m2, d2, "no_show", 9999)          # не пришёл — не выручка
booking(m1, today + timedelta(days=1), "booked", 7777)   # будущая — не в периоде и не выручка
booking(m1, today - timedelta(days=9), "arrived", 4000)  # прошлый период
db.add(Order(user_id=owner.id, number=1001, items=[], total=2500, status="done",
             created_at=datetime.combine(d1, time(12, 0), B).astimezone(timezone.utc)))
db.add(Order(user_id=owner.id, number=1002, items=[], total=8000, status="cancelled",
             created_at=datetime.combine(d1, time(13, 0), B).astimezone(timezone.utc)))
db.commit()

HO = {"Authorization": f"Bearer {create_jwt_token(owner.id)}"}
HX = {"Authorization": f"Bearer {create_jwt_token(other.id)}"}

# ── API расходов
r = cl.post("/api/finance/expenses", headers=HO, json={"category": "rent", "title": "Аренда", "amount": 30000, "repeat": "month",
                                                       "spent_on": (today - timedelta(days=60)).isoformat()})
check("добавить ежемесячную аренду", r.status_code == 200 and r.json()["expense"]["repeat"] == "month", r.text[:200])
rent_id = r.json()["expense"]["id"]
r = cl.post("/api/finance/expenses", headers=HO, json={"category": "supplies", "amount": 1200, "spent_on": d2.isoformat()})
check("разовый расход без названия — название по категории", r.status_code == 200 and r.json()["expense"]["title"] == "Материалы и закупки", r.text[:200])
sup_id = r.json()["expense"]["id"]
r = cl.post("/api/finance/expenses", headers=HO, json={"category": "ads", "amount": 5000, "spent_on": (today - timedelta(days=20)).isoformat()})
check("разовый расход вне периода", r.status_code == 200)
for bad in ({"amount": 0}, {"amount": "abc"}, {"amount": -5}, {"amount": 100, "spent_on": "32-13-2026"}):
    r = cl.post("/api/finance/expenses", headers=HO, json=bad)
    check(f"неверные данные {bad} — 400", r.status_code == 400, (r.status_code, r.text[:120]))
r = cl.patch(f"/api/finance/expenses/{rent_id}", headers=HX, json={"amount": 1})
check("чужой расход — 404", r.status_code == 404, r.status_code)
r = cl.delete(f"/api/finance/expenses/{rent_id}", headers=HX)
check("удалить чужой — 404", r.status_code == 404, r.status_code)
r = cl.get("/api/finance/expenses", headers=HX)
check("чужие расходы не видны", r.status_code == 200 and r.json()["expenses"] == [], r.text[:120])
r = cl.patch(f"/api/finance/payroll/{m1.id}", headers=HX, json={"pay_percent": 99})
check("оплату чужого мастера не изменить — 404", r.status_code == 404, r.status_code)
r = cl.patch(f"/api/finance/payroll/{m1.id}", headers=HO, json={"pay_percent": 140})
check("процент > 100 — 400", r.status_code == 400, r.status_code)

# ── сводка за 7 дней
r = cl.get("/api/finance/summary?period=7d", headers=HO)
check("сводка 200", r.status_code == 200, r.text[:200])
s = r.json()
rev = 2000 + 3000 + 1500 + 2500
check("выручка: записи «пришёл» + заказы, без отменённых и неявок", near(s["revenue"], rev), s["revenue"])
check("части выручки", near(s["revenue_parts"]["bookings"], 6500) and near(s["revenue_parts"]["orders"], 2500), s["revenue_parts"])
rng = an.resolve_period("7d")
rent_part = fs.month_share(30000, rng.a, rng.b, today - timedelta(days=60), None)
fixed_part = fs.month_share(30000, rng.a, rng.b, rng.a, None)
pay = {m["name"]: m for m in s["payroll"]}
check("зарплата Айданы = 40% от 5000", near(pay["Айдана"]["salary"], 2000), pay.get("Айдана"))
check("зарплата Бегимай = доля оклада", near(pay["Бегимай"]["salary"], fixed_part), (pay.get("Бегимай"), fixed_part))
exp = rent_part + 1200 + 2000 + fixed_part
check("расходы = аренда долей + материалы + зарплаты", near(s["expenses"], exp, 1.0), (s["expenses"], exp))
check("прибыль = выручка − расходы", near(s["profit"], rev - exp, 1.0), s["profit"])
cats = {c["key"]: c["amount"] for c in s["categories"]}
check("категории: аренда, зарплаты, материалы; реклама вне периода", set(cats) == {"rent", "salary", "supplies"}, cats)
check("ряд по дням: 7 точек, сумма выручки сходится", s["series"]["step"] == "day" and len(s["series"]["revenue"]) == 7
      and near(sum(s["series"]["revenue"]), rev), (s["series"]["step"], sum(s["series"]["revenue"])))
check("прошлый период: выручка 4000", near(s["prev_revenue"], 4000), s["prev_revenue"])

# правка и удаление
r = cl.patch(f"/api/finance/expenses/{sup_id}", headers=HO, json={"amount": 2200, "repeat": "once"})
check("изменить расход", r.status_code == 200 and r.json()["expense"]["amount"] == 2200)
r = cl.patch(f"/api/finance/expenses/{rent_id}", headers=HO, json={"is_active": False})
s2 = cl.get("/api/finance/summary?period=7d", headers=HO).json()
check("выключенная аренда не считается, правка видна сразу", near(s2["expenses"], 2200 + 2000 + fixed_part, 1.0), s2["expenses"])
r = cl.delete(f"/api/finance/expenses/{sup_id}", headers=HO)
check("удалить расход", r.status_code == 200 and all(e["id"] != sup_id for e in cl.get("/api/finance/expenses", headers=HO).json()["expenses"]))

# ── отчёт аналитики
an._CACHE.clear() if hasattr(an, "_CACHE") else None
rep = cl.get("/api/analytics/report?period=7d", headers=HO).json()
check("отчёт: раздел finance с прибылью", rep.get("finance") and "profit" in rep["finance"], str(rep.get("finance"))[:120])
check("отчёт: плитка «Прибыль» среди первых трёх у салона", "profit" in [k["id"] for k in rep["kpis"][:3]], [k["id"] for k in rep["kpis"]])
check("отчёт: у мастеров есть выручка", any(m.get("revenue") for m in (rep.get("bookings") or {}).get("masters", [])))
HS = {"Authorization": f"Bearer {create_jwt_token(shop.id)}"}
db.add(BookingResource(user_id=shop.id, name="Мастер по умолчанию", schedule={}, is_active=True))  # как после захода на booking.html
db.commit()
rep = cl.get("/api/analytics/report?period=7d", headers=HS).json()
check("магазин без записей: пустого раздела «Записи» нет", rep.get("bookings") is None, str(rep.get("bookings"))[:80])

# ── сотрудник в финансы не попадает
r = cl.post("/api/team", headers=HO, json={"name": "Асель", "email": "asel@fin.kg", "permissions": ["inbox", "booking", "analytics"]})
check("сотрудник добавлен", r.status_code == 200, r.text[:200])
pw = r.json().get("password")
tok = cl.post("/api/auth/login", json={"email": "asel@fin.kg", "password": pw}).json().get("token")
HST = {"Authorization": f"Bearer {tok}"}
check("сотрудник: сводка финансов — 403", cl.get("/api/finance/summary", headers=HST).status_code == 403)
check("сотрудник: добавить расход — 403", cl.post("/api/finance/expenses", headers=HST, json={"amount": 1}).status_code == 403)
check("сотрудник: оплата мастера — 403", cl.patch(f"/api/finance/payroll/{m1.id}", headers=HST, json={"pay_percent": 1}).status_code == 403)

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
