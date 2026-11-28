"""Зарплата мастеров v2 (Voksy Beauty, шаг 3.3).

Часть 1 (без базы): права сотрудника (ведомость, ручные строки, выплаты, схема — бухгалтер; выход — ещё и
администратор; мастеру и «Клиентам» — нет), страница «Зарплата» в разделах бухгалтера, % по категориям (точная →
верхняя → общий), разбор схемы, журнал действий, файлы экранов.

Часть 2 (PostgreSQL, PAYROLL_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- до кассы база процента — визиты «пришёл / завершён»; свой % категории, верхняя категория, общий %;
- выходы × ставка (отметка администратора, повтор не задваивает, снять), доля оклада по дням;
- аванс / штраф (с причиной) / премия; начислено и к выплате; выплата переводом закрывает ведомость, повтор нажатия —
  та же выплата, сверх остатка — отказ; выплата наличными — изъятие в смене, без смены — отказ; бухгалтер наличными
  не платит; выплата за 1–15 видна в ведомости за месяц;
- после начала кассы база — оплаты по записям мастера минус возвраты; визит до кассы, оплаченный после, не дважды;
- «Финансы» берут те же начисления; кабинет мастера — заработок и «к выплате» за месяц;
- права сотрудников, журнал действий.
"""
import os
import sys
import uuid
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("PAYROLL_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="payroll-test-secret-0123456789abcdef012")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import activity  # noqa: E402
from backend.services import payroll as pr  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
A = ts.staff_allowed
U = str(uuid.uuid4())
check("ведомость, мастер, схема, ручные строки, выплаты — бухгалтер (finance)",
      A(["finance"], "GET", "/api/payroll/sheet") and A(["finance"], "GET", f"/api/payroll/masters/{U}")
      and A(["finance"], "PUT", f"/api/payroll/masters/{U}/scheme") and A(["finance"], "POST", "/api/payroll/adjustments")
      and A(["finance"], "DELETE", f"/api/payroll/adjustments/{U}") and A(["finance"], "POST", "/api/payroll/payouts"))
check("администратору и мастеру ведомость и выплаты закрыты",
      not A(["booking"], "GET", "/api/payroll/sheet") and not A(["booking"], "POST", "/api/payroll/payouts")
      and not A(["booking_own"], "GET", "/api/payroll/sheet") and not A(["booking_setup"], "PUT", f"/api/payroll/masters/{U}/scheme"))
check("выход мастера отмечает администратор (и бухгалтер), мастер — нет",
      A(["booking"], "PUT", "/api/payroll/attendance") and A(["booking"], "GET", "/api/payroll/attendance")
      and A(["finance"], "PUT", "/api/payroll/attendance") and not A(["booking_own"], "PUT", "/api/payroll/attendance"))
sec = lambda perms: [s["id"] for s in ts.sections(perms)]   # noqa: E731
check("страница «Зарплата» — у бухгалтера, не у администратора", "payroll" in sec(["finance"]) and "payroll" not in sec(["booking"]))
sc = {"percent": 40, "categories": {"Волосы / Окрашивание": 50, "Ногти": 35}, "shift": 500, "fixed": 0}
check("% по категориям: точная → верхняя → общий", pr.pct_for(sc, "Волосы / Окрашивание") == 50 and pr.pct_for(sc, "Ногти / Педикюр") == 35
      and pr.pct_for(sc, "Ногти") == 35 and pr.pct_for(sc, "Волосы") == 40 and pr.pct_for(sc, None) == 40 and pr.pct_for(sc, "") == 40)
check("схема словами", pr.scheme_text(sc) == "40% с услуг, Волосы / Окрашивание — 50%, Ногти — 35%, выход 500 сом", pr.scheme_text(sc))
for bad, msg in (({"percent": 150}, "от 0 до 100"), ({"shift": -1}, "от 0"), ({"categories": {"Ногти": "abc"}}, "число")):
    try:
        pr._clean_scheme(bad, {"percent": 0, "categories": {}, "shift": 0, "fixed": 0})
        ok = False
    except pr.CashError as e:
        ok = msg in str(e)
    check(f"схема: проверка «{msg}»", ok)
cleaned = pr._clean_scheme({"categories": [{"category": "  Ногти ", "percent": "35"}, {"category": "", "percent": 10}]},
                           {"percent": 40, "categories": {}, "shift": 0, "fixed": 0})
check("категории списком: пробелы, пустые строки отбрасываются", cleaned["categories"] == {"Ногти": 35.0}, cleaned)
a0, b0 = pr.parse_period("2026-11-01", "2026-11-15")
check("период: 1–15", (a0, b0) == (date(2026, 11, 1), date(2026, 11, 15)) and pr.period_label(a0, b0) == "1–15 ноя")
for bad in (("2026-11-15", "2026-11-01"), ("2026-01-01", "2026-12-31"), ("abc", None)):
    try:
        pr.parse_period(*bad)
        ok = False
    except pr.CashError:
        ok = True
    check(f"период: отказ {bad}", ok)
check("журнал действий: зарплата — в группе «Деньги»", "payroll" in activity.GROUPS["money"][1]
      and all(activity.ACTION_TITLES.get(k) for k in ("payroll.shift", "payroll.adjust", "payroll.payout", "payroll.update")))


def src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


for f in ("backend/static/payroll.html", "backend/static/payroll/payroll.js", "backend/static/payroll/payroll.css"):
    check(f"файл есть: {f}", os.path.exists(os.path.join(ROOT, f)))
pj = src("backend/static/payroll/payroll.js") if os.path.exists(os.path.join(ROOT, "backend/static/payroll/payroll.js")) else ""
check("«Зарплата»: ведомость, аванс/штраф/премия, выплата наличными или переводом, схема с категориями",
      "/api/payroll/sheet" in pj and "/api/payroll/adjustments" in pj and "/api/payroll/payouts" in pj
      and "/scheme" in pj and "transfer" in pj and "idem_key" in pj)
bj, mj, fj, sb, ms = (src("backend/static/booking/booking.js"), src("backend/static/master/master.js"),
                      src("backend/static/finance/finance.js"), src("backend/static/js/sidebar.js"),
                      src("backend/static/booking/masters.js"))
check("журнал записи: «Кто сегодня работает»", "/api/payroll/attendance" in bj and "Кто сегодня работает" in bj)
check("кабинет мастера: зарплата за месяц — начислено, авансы, штрафы, к выплате", "payroll" in mj and "К выплате" in mj)
check("«Финансы»: зарплаты → «Ведомость»", "/static/payroll.html" in fj)
check("меню: «Зарплата» у ниш с записью", "payroll-nav-item" in sb and "/static/payroll.html" in sb)
check("карточка мастера: ставка за выход, схема через /api/payroll", "pay_shift" in ms and "/api/payroll/masters/" in ms)

if not PG:
    print("SKIP часть 2: PAYROLL_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ───────────────────────────── часть 2 ─────────────────────────────
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_contact import AgentContact  # noqa: E402
from backend.models.booking import Booking, BookingResource  # noqa: E402
from backend.models.cashbox import CashReceipt  # noqa: E402
from backend.models.payroll import MasterShift, PayrollPayout  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402
from backend.core import background_loop  # noqa: E402
from backend.services import beauty_services, cashbox, finance_service, master_cabinet  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services.finance_service import month_share  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
app_module.ensure_booking_tables()
app_module.ensure_user_profile_columns()
app_module.ensure_branding_columns()
cl = TestClient(app_module.app, raise_server_exceptions=False)
background_loop.submit = lambda coro: coro.close()
cashbox.NOTIFY_DIFF = lambda owner_id, shift_id: None
db = SessionLocal()
NOW = bs.now_utc()
TODAY = bs.to_local(NOW).date()
tables = {r[0] for r in db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).fetchall()}
rcols = {r[0] for r in db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'booking_resources'")).fetchall()}
check("таблицы и колонки зарплаты созданы при старте", {"master_shifts", "payroll_adjustments", "payroll_payouts"} <= tables
      and {"pay_categories", "pay_shift"} <= rcols)

plan = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p",
                        is_active=True, product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
db.add(plan)
db.flush()
u = User(email="payroll@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
         subscription_plan_id=plan.id, subscription_end_date=NOW + timedelta(days=20), business_niche="beauty_salon",
         business_profile={"answers": {}})
db.add(u)
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор", is_active=True)
db.add(agent)
db.commit()
HO = {"Authorization": "Bearer " + create_jwt_token(u.id)}
user = db.query(User).get(u.id)
page = bs.get_page(db, user, create=True)
page.enabled = True
page.settings = dict(page.settings or {}, title="Салон Айдана", lead_min=0, step_min=15)
db.commit()
mani = beauty_services.save(db, user, None, {"title": "Маникюр", "price": 1400, "duration_min": 60, "category": "Ногти"})
dye = beauty_services.save(db, user, None, {"title": "Окрашивание", "price": 5000, "duration_min": 120, "category": "Волосы",
                                            "subcategory": "Окрашивание"})
cut = beauty_services.save(db, user, None, {"title": "Стрижка", "price": 2000, "duration_min": 60, "category": "Волосы"})
db.commit()
db.expire_all()
check("услуги в категориях", (dye.collection or "").startswith("Волосы") and mani.collection == "Ногти", (dye.collection, mani.collection))
m1 = BookingResource(user_id=u.id, name="Айгуль", schedule={}, is_active=True, created_at=NOW - timedelta(days=200))
m2 = BookingResource(user_id=u.id, name="Бермет", schedule={}, is_active=True, created_at=NOW - timedelta(days=200))
db.add_all([m1, m2])
db.commit()
c1 = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Айжан", phone="+996555100100", status="active", memory={})
db.add(c1)
db.commit()

A0 = TODAY - timedelta(days=20)
day_at = lambda d, h: datetime.combine(d, datetime.min.time(), bs.BISHKEK).replace(hour=h)   # noqa: E731


def mk(master, svc, start, status="completed", price=None, visit=None):
    b = Booking(user_id=u.id, resource_id=master.id, service_entry_id=svc.id, service_title=svc.title, contact_id=c1.id,
                client_name="Айжан", client_phone=c1.phone, start_at=start, end_at=start + timedelta(minutes=60),
                status=status, price=svc.price if price is None else price, source="owner", visit_id=visit)
    db.add(b)
    db.commit()
    return b


def K():
    return uuid.uuid4().hex


r = cl.put(f"/api/payroll/masters/{m1.id}/scheme", headers=HO,
           json={"percent": 40, "categories": {dye.collection: 50}, "shift": 500, "fixed": 0})
check("схема Айгуль: 40%, своё «Окрашивание» 50%, выход 500", r.status_code == 200 and r.json()["scheme"]["shift"] == 500
      and r.json()["scheme"]["categories"] == {dye.collection: 50.0}, r.text[:200])
r = cl.put(f"/api/payroll/masters/{m2.id}/scheme", headers=HO, json={"fixed": 30000})
check("схема Бермет: оклад 30 000", r.status_code == 200 and r.json()["scheme"]["fixed"] == 30000)
check("схема: неверный процент — 400", cl.put(f"/api/payroll/masters/{m1.id}/scheme", headers=HO, json={"percent": 300}).status_code == 400)
cats = cl.get(f"/api/payroll/masters/{m1.id}", headers=HO).json()["categories"]
check("категории салона для «своего %»: верхние и с подкатегорией", "Волосы" in cats and dye.collection in cats and "Ногти" in cats, cats)

# до кассы — визиты
v1 = mk(m1, mani, day_at(A0 + timedelta(days=1), 10))               # 1400 × 40% = 560
v2 = mk(m1, dye, day_at(A0 + timedelta(days=2), 10), "arrived")      # 5000 × 50% = 2500
v3 = mk(m1, cut, day_at(A0 + timedelta(days=3), 10))                # 2000 × 40% (верхняя «Волосы» не задана) = 800
v4 = mk(m1, mani, day_at(A0 + timedelta(days=4), 10), "no_show")    # не считается
v5 = mk(m1, mani, day_at(A0 - timedelta(days=5), 10))               # вне периода
for d in (A0 + timedelta(days=1), A0 + timedelta(days=2)):
    r = cl.put("/api/payroll/attendance", headers=HO, json={"resource_id": str(m1.id), "date": d.isoformat(), "worked": True})
r = cl.put("/api/payroll/attendance", headers=HO, json={"resource_id": str(m1.id), "date": (A0 + timedelta(days=2)).isoformat(), "worked": True})
check("выход: повторная отметка не задваивает", r.status_code == 200
      and db.query(MasterShift).filter(MasterShift.resource_id == m1.id).count() == 2)
cl.put("/api/payroll/attendance", headers=HO, json={"resource_id": str(m1.id), "date": (A0 + timedelta(days=3)).isoformat(), "worked": True})
cl.put("/api/payroll/attendance", headers=HO, json={"resource_id": str(m1.id), "date": (A0 + timedelta(days=3)).isoformat(), "worked": False})
check("выход: снять отметку", db.query(MasterShift).filter(MasterShift.resource_id == m1.id).count() == 2)
r = cl.put("/api/payroll/attendance", headers=HO, json={"resource_id": str(m1.id), "date": (TODAY + timedelta(days=5)).isoformat(), "worked": True})
check("выход: на неделю вперёд нельзя — 400", r.status_code == 400)
att = cl.get(f"/api/payroll/attendance?date={(A0 + timedelta(days=1)).isoformat()}", headers=HO).json()
row1 = next(x for x in att["masters"] if x["id"] == str(m1.id))
row2 = next(x for x in att["masters"] if x["id"] == str(m2.id))
check("«Кто сегодня работает»: отмечена, подсказка по записям, ставка", row1["worked"] and row1["suggested"] and row1["rate"] == 500
      and not row2["worked"] and not row2["suggested"] and att["any_rate"] is True, att)

r = cl.post("/api/payroll/adjustments", headers=HO, json={"resource_id": str(m1.id), "kind": "advance", "amount": 1000,
                                                         "day": (A0 + timedelta(days=5)).isoformat(), "comment": "аванс"})
check("аванс 1 000", r.status_code == 200, r.text[:160])
r = cl.post("/api/payroll/adjustments", headers=HO, json={"resource_id": str(m1.id), "kind": "fine", "amount": 200,
                                                         "day": (A0 + timedelta(days=5)).isoformat()})
check("штраф без причины — 400", r.status_code == 400)
r = cl.post("/api/payroll/adjustments", headers=HO, json={"resource_id": str(m1.id), "kind": "fine", "amount": 200,
                                                         "day": (A0 + timedelta(days=5)).isoformat(), "comment": "опоздание"})
FINE = r.json().get("id")
cl.post("/api/payroll/adjustments", headers=HO, json={"resource_id": str(m1.id), "kind": "bonus", "amount": 300,
                                                    "day": (A0 + timedelta(days=6)).isoformat(), "comment": "за отзывы"})
check("неверный вид строки — 400", cl.post("/api/payroll/adjustments", headers=HO, json={"resource_id": str(m1.id), "kind": "gift", "amount": 1}).status_code == 400)

P = f"from={A0.isoformat()}&to={TODAY.isoformat()}"
sh = cl.get(f"/api/payroll/sheet?{P}", headers=HO).json()
L = {x["name"]: x for x in sh["masters"]}
g = L["Айгуль"]
cats_amt = {x["category"]: (x["pct"], x["amount"]) for x in g["by_cat"]}
check("до кассы — база по визитам: 1400 + 5000 + 2000, неявка и визит вне периода не считаются",
      g["base"] == 8400 and g["visits"] == 3 and sh["basis"] == "visits", (g["base"], g["visits"]))
check("% по категориям: Ногти 40% (560), Окрашивание 50% (2500), Волосы → общий 40% (800)",
      cats_amt.get("Ногти") == (40.0, 560) and cats_amt.get(dye.collection) == (50.0, 2500) and cats_amt.get("Волосы") == (40.0, 800)
      and g["percent_amount"] == 3860, cats_amt)
check("выходы: 2 × 500", g["shifts"]["count"] == 2 and g["shifts"]["amount"] == 1000)
check("начислено = 3860 + 1000 + премия 300 − штраф 200 = 4960; к выплате = − аванс 1000 = 3960",
      g["accrued"] == 4960 and g["advances"] == 1000 and g["to_pay"] == 3960 and g["closed"] is False, (g["accrued"], g["to_pay"]))
bm = L["Бермет"]
exp_fixed = round(month_share(30000, A0, TODAY, A0, None), 2)
check("оклад Бермет — доля по дням периода", bm["fixed_amount"] == exp_fixed and bm["to_pay"] == exp_fixed, (bm["fixed_amount"], exp_fixed))
check("итоги ведомости", sh["totals"]["to_pay"] == round(3960 + exp_fixed, 2) and sh["totals"]["shifts"] == 1000)

fin = finance_service.summary(db, user, period="custom", date_from=A0.isoformat(), date_to=TODAY.isoformat(), with_series=False)
fp = {x["name"]: x for x in fin["payroll"]}
check("«Финансы»: зарплата = начислено по ведомости", fp["Айгуль"]["salary"] == 4960 and fp["Бермет"]["salary"] == exp_fixed
      and fin["payroll_total"] == round(4960 + exp_fixed, 2), (fp["Айгуль"]["salary"], fin["payroll_total"]))

r = cl.delete(f"/api/payroll/adjustments/{FINE}", headers=HO)
g2 = {x["name"]: x for x in cl.get(f"/api/payroll/sheet?{P}", headers=HO).json()["masters"]}["Айгуль"]
check("удалили штраф — начислено 5160", r.status_code == 200 and g2["accrued"] == 5160 and g2["to_pay"] == 4160)

# выплаты
k = K()
body = {"resource_id": str(m1.id), "from": A0.isoformat(), "to": TODAY.isoformat(), "method": "transfer", "idem_key": k}
r1 = cl.post("/api/payroll/payouts", headers=HO, json=body)
r2 = cl.post("/api/payroll/payouts", headers=HO, json=body)
check("выплата переводом всей суммы; повтор нажатия — та же выплата", r1.status_code == 200 and r1.json()["payout"]["amount"] == 4160
      and r2.json()["created"] is False and r2.json()["payout"]["id"] == r1.json()["payout"]["id"]
      and db.query(PayrollPayout).count() == 1, r1.text[:200])
g3 = {x["name"]: x for x in cl.get(f"/api/payroll/sheet?{P}", headers=HO).json()["masters"]}["Айгуль"]
check("после выплаты ведомость закрыта: к выплате 0, выплачено 4 160", g3["to_pay"] == 0 and g3["paid"] == 4160 and g3["closed"] is True
      and g3["payouts"][0]["method"] == "transfer", g3["payouts"])
r = cl.post("/api/payroll/payouts", headers=HO, json=dict(body, idem_key=K()))
check("второй раз за тот же период — 409 «выплачивать нечего»", r.status_code == 409 and r.json()["message"]["code"] == "paid")
r = cl.post("/api/payroll/payouts", headers=HO, json={"resource_id": str(m2.id), "from": A0.isoformat(), "to": TODAY.isoformat(),
                                                    "method": "transfer", "amount": exp_fixed + 100, "idem_key": K()})
check("больше, чем к выплате, — 400", r.status_code == 400 and r.json()["message"]["code"] == "too_much")
half_b = A0 + timedelta(days=9)
r = cl.post("/api/payroll/payouts", headers=HO, json={"resource_id": str(m2.id), "from": A0.isoformat(), "to": half_b.isoformat(),
                                                    "method": "cash", "idem_key": K()})
check("наличными без открытой смены — отказ", r.status_code in (400, 409) and "смен" in r.text, r.text[:200])

# касса: смена, выплата наличными, база — оплаты
r = cl.post("/api/cash/shifts/open", headers=HO, json={"opening_cash": 20000})
day0 = bs.day_bounds(TODAY)[0]
db.execute(text("UPDATE cash_shifts SET opened_at = :t WHERE user_id = :u"), {"t": day0 + timedelta(minutes=1), "u": str(u.id)})
db.commit()
cashbox._START_CACHE.clear()
T = cashbox.cash_start(db, u.id)
half = round(month_share(30000, A0, half_b, A0, None), 2)
r = cl.post("/api/payroll/payouts", headers=HO, json={"resource_id": str(m2.id), "from": A0.isoformat(), "to": half_b.isoformat(),
                                                    "method": "cash", "idem_key": K(), "comment": "за первую половину"})
po = r.json().get("payout") or {}
rc = db.query(CashReceipt).filter(CashReceipt.id == uuid.UUID(po["receipt_id"])).first() if po.get("receipt_id") else None
check("выплата наличными за 1–10 — изъятие в смене (чек «Зарплата: …»)", r.status_code == 200 and po.get("amount") == half
      and rc is not None and rc.kind == "cash_out" and "Зарплата: Бермет" in (rc.comment or ""), r.text[:200])
bm2 = {x["name"]: x for x in cl.get(f"/api/payroll/sheet?{P}", headers=HO).json()["masters"]}["Бермет"]
check("выплата за 1–10 видна в ведомости за весь период: к выплате — остаток", bm2["paid"] == half
      and bm2["to_pay"] == round(exp_fixed - half, 2), (bm2["paid"], bm2["to_pay"]))

# после начала кассы — оплаты
v6 = mk(m1, mani, T + timedelta(minutes=5), "completed")
g4 = {x["name"]: x for x in cl.get(f"/api/payroll/sheet?{P}", headers=HO).json()["masters"]}["Айгуль"]
check("после начала кассы визит без оплаты в базу не идёт", g4["base"] == 8400 and g4["visits"] == 4, (g4["base"], g4["visits"]))
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(v6.id), "lines": [{"method": "card", "amount": 1400}], "idem_key": K()})
rid_ = r.json()["receipt"]["id"]
g5 = {x["name"]: x for x in cl.get(f"/api/payroll/sheet?{P}", headers=HO).json()["masters"]}["Айгуль"]
check("оплатили 1 400 — база +1 400, процент +560", g5["base"] == 9800 and g5["percent_amount"] == 3860 + 560, (g5["base"], g5["percent_amount"]))
cl.post(f"/api/cash/receipts/{rid_}/refund", headers=HO, json={"amount": 400, "reason": "скидка", "method": "card", "idem_key": K()})
g6 = {x["name"]: x for x in cl.get(f"/api/payroll/sheet?{P}", headers=HO).json()["masters"]}["Айгуль"]
check("возврат 400 — база 9 400, процент −160", g6["base"] == 9400 and g6["percent_amount"] == 3860 + 400 and g6["to_pay"] == 400,
      (g6["base"], g6["percent_amount"], g6["to_pay"]))
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(v1.id), "lines": [{"method": "card", "amount": 1400}], "idem_key": K()})
g7 = {x["name"]: x for x in cl.get(f"/api/payroll/sheet?{P}", headers=HO).json()["masters"]}["Айгуль"]
check("визит до кассы, оплаченный после, дважды не считается", r.status_code == 200 and g7["base"] == 9400, g7["base"])

# кабинет мастера
db.expire_all()
e = master_cabinet.earnings(db, db.query(BookingResource).get(m1.id), owner=db.query(User).get(u.id))
mp = e["payroll"]
check("кабинет мастера: заработок сегодня — тот же расчёт (оплата 1 000 × 40% = 400)", e["today"]["salary"] == 400
      and e["today"]["revenue"] == 1000, e["today"])
check("кабинет мастера: за месяц — начислено, авансы, штрафы, к выплате; схема словами", "accrued" in mp and "to_pay" in mp
      and "advances" in mp and "fines" in mp and "выход 500 сом" in e["scheme_text"], mp)

# сотрудники
adm, _ = ts.create_member(db, user, name="Айпери", email="adm@kg.kg", password="secret12", role_title="Администратор",
                          permissions=["booking"])
acc, _ = ts.create_member(db, user, name="Бухгалтер", email="acc@kg.kg", password="secret12", role_title="Бухгалтер",
                          permissions=["finance"])
mst, _ = ts.create_member(db, user, name="Мастер", email="mst@kg.kg", password="secret12", role_title="Мастер",
                          permissions=["booking_own"])
db.commit()


def sh_(m):
    return {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(m.id), "stv": m.token_version})}


check("бухгалтер видит ведомость", cl.get(f"/api/payroll/sheet?{P}", headers=sh_(acc)).status_code == 200)
r = cl.post("/api/payroll/adjustments", headers=sh_(acc), json={"resource_id": str(m2.id), "kind": "bonus", "amount": 500, "comment": "праздник"})
check("бухгалтер добавляет премию", r.status_code == 200, r.text[:120])
r = cl.post("/api/payroll/payouts", headers=sh_(acc), json={"resource_id": str(m2.id), "from": A0.isoformat(), "to": TODAY.isoformat(),
                                                          "method": "cash", "idem_key": K()})
check("бухгалтер наличными не платит (нет смены в кассе) — понятный отказ", r.status_code in (400, 409)
      and "Переводом" in r.text, r.text[:200])
r = cl.post("/api/payroll/payouts", headers=sh_(acc), json={"resource_id": str(m2.id), "from": A0.isoformat(), "to": TODAY.isoformat(),
                                                          "method": "transfer", "amount": 500, "idem_key": K()})
check("бухгалтер платит переводом (часть)", r.status_code == 200 and r.json()["payout"]["amount"] == 500, r.text[:160])
check("администратору ведомость — 403", cl.get(f"/api/payroll/sheet?{P}", headers=sh_(adm)).status_code == 403)
r = cl.put("/api/payroll/attendance", headers=sh_(adm), json={"resource_id": str(m2.id), "date": TODAY.isoformat(), "worked": True})
check("администратор отмечает выход", r.status_code == 200, r.text[:120])
check("мастеру ведомость и выход — 403", cl.get(f"/api/payroll/sheet?{P}", headers=sh_(mst)).status_code == 403
      and cl.put("/api/payroll/attendance", headers=sh_(mst), json={"resource_id": str(m2.id), "worked": True}).status_code == 403)
check("администратор схему не меняет — 403", cl.put(f"/api/payroll/masters/{m1.id}/scheme", headers=sh_(adm), json={"percent": 99}).status_code == 403)
res = cl.get("/api/booking/resources", headers=HO).json()
res = res.get("resources", res) if isinstance(res, dict) else res
rr = next((x for x in res if x.get("id") == str(m1.id)), {})
check("журнал записи (владельцу): у мастера ставка за выход и % по категориям", rr.get("pay_shift") == 500
      and rr.get("pay_categories") == {dye.collection: 50.0}, {k: rr.get(k) for k in ("pay_shift", "pay_categories")})

acts = [x["action"] for x in cl.get("/api/beauty/activity?group=money&period=all", headers=HO).json()["items"]]
check("журнал действий: выход, строки, выплаты, схема", {"payroll.shift", "payroll.adjust", "payroll.payout", "payroll.update"} <= set(acts),
      sorted(set(acts)))

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
