"""Касса салона (Voksy Beauty, шаг 3.1): смены администраторов, оплата визита, возвраты, выручка по оплатам.

Часть 1 (без базы): права сотрудника на /api/cash/*, страница «Касса» в разделах сотрудника, проверка сумм и
способов оплаты, журнал действий (группа «Деньги»), файлы страницы и окна оплаты в журнале записи.

Часть 2 (PostgreSQL, CASH_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- до кассы выручка по визитам (как раньше); наличные без смены — отказ; смена открывается один раз (и при гонке);
- оплата: два способа в одном чеке, повтор того же нажатия — тот же чек; визит уже оплачен — отказ; больше, чем
  осталось, — отказ; частичная оплата визита из двух услуг раскладывается по порядку; отменённая запись — отказ;
- два кассира одновременно платят за один визит: проходит одна оплата; одно и то же нажатие из двух потоков — один чек;
- предоплата будущей записи: отметка «получена»; возврат части — отметка снимается;
- возврат: нужна причина, не больше оплаченного по чеку, повтор — тот же чек; внесение и изъятие (не больше, чем в кассе);
- закрытие смены: ожидаемые наличные по чекам, расхождение, сообщение владельцу, «Требует внимания» на главной,
  второе закрытие — отказ;
- выручка по оплатам: визит, начавшийся до кассы и оплаченный после, не считается дважды; визит без оплаты в
  выручку не попадает; финансы, аналитика и журнал записи показывают одно и то же;
- журнал записи отдаёт «оплачено» по записи; история записи — с оплатой и возвратом;
- сотрудники: администратор ведёт свою смену и платит, чужую смену не закрывает и не видит; бухгалтер видит все
  смены, но не платит; мастер в кассу не попадает; владелец закрывает смену администратора.
"""
import os
import sys
import threading
import uuid
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("CASH_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="cash-test-secret-0123456789abcdef0123")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import activity  # noqa: E402
from backend.services import cashbox  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
A = ts.staff_allowed
check("администратор (booking): смена, оплата, возврат, карточка оплаты",
      A(["booking"], "GET", "/api/cash/state") and A(["booking"], "POST", "/api/cash/shifts/open")
      and A(["booking"], "POST", "/api/cash/pay") and A(["booking"], "POST", "/api/cash/moves")
      and A(["booking"], "POST", f"/api/cash/receipts/{uuid.uuid4()}/refund")
      and A(["booking"], "GET", f"/api/cash/booking/{uuid.uuid4()}")
      and A(["booking"], "POST", f"/api/cash/shifts/{uuid.uuid4()}/close"))
check("бухгалтер (finance): смены и чеки видит, оплату не принимает",
      A(["finance"], "GET", "/api/cash/shifts") and A(["finance"], "GET", f"/api/cash/shifts/{uuid.uuid4()}")
      and A(["finance"], "GET", "/api/cash/receipts") and not A(["finance"], "POST", "/api/cash/pay")
      and not A(["finance"], "POST", "/api/cash/shifts/open"))
check("мастер, «Мастера и график», «Клиенты» — в кассу нельзя",
      not A(["booking_own"], "GET", "/api/cash/state") and not A(["booking_setup"], "POST", "/api/cash/pay")
      and not A(["crm", "inbox"], "GET", "/api/cash/state"))
sec = lambda perms: [s["id"] for s in ts.sections(perms)]   # noqa: E731
check("страница «Касса» в меню администратора и бухгалтера, не мастера",
      "cash" in sec(["booking"]) and "cash" in sec(["finance"]) and "cash" not in sec(["booking_own"])
      and "cash" not in sec(["booking_setup"]))
for bad, msg in (([], "Укажите сумму"), ([{"method": "bitcoin", "amount": 10}], "способ"),
                 ([{"method": "cash", "amount": 0}], "больше нуля"), ([{"method": "cash", "amount": -5}], "отрицательной"),
                 ([{"method": "cash", "amount": "abc"}], "число"), ([{"method": "cash", "amount": 1}] * 5, "Не больше")):
    try:
        cashbox._clean_lines(bad)
        ok = False
    except cashbox.CashError as e:
        ok = msg in str(e)
    check(f"проверка строк оплаты: {msg}", ok)
check("сумма «1 500,5» понимается", cashbox._clean_lines([{"method": "card", "amount": "1 500,5"}]) == [("card", 1500.5)])
check("журнал действий: оплаты, возвраты, смены — в группе «Деньги»",
      {"payment", "shift", "cash"} <= set(activity.GROUPS["money"][1])
      and activity.ACTION_TITLES.get("payment.refund") and activity.ACTION_TITLES.get("shift.close"))
for f in ("backend/static/cash.html", "backend/static/cash/cash.js", "backend/static/cash/cash.css",
          "backend/static/booking/pay.js"):
    check(f"файл есть: {f}", os.path.exists(os.path.join(ROOT, f)))
sb = open(os.path.join(ROOT, "backend/static/js/sidebar.js"), encoding="utf-8").read()
check("пункт меню «Касса» у ниш с записью", "cash-nav-item" in sb and "/static/cash.html" in sb)
bj = open(os.path.join(ROOT, "backend/static/booking/booking.js"), encoding="utf-8").read()
pj = open(os.path.join(ROOT, "backend/static/booking/pay.js"), encoding="utf-8").read()
check("карточка записи: блок «Оплата» и окно оплаты", "BookingPay" in bj and "bk-pay" in bj and "data-pay" in pj
      and "idem_key" in pj and "shift_required" in pj)
bh = open(os.path.join(ROOT, "backend/static/booking.html"), encoding="utf-8").read()
check("журнал записи подключает окно оплаты", "/static/booking/pay.js" in bh)

if not PG:
    print("SKIP часть 2: CASH_TEST_DATABASE_URL не задан")
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
from backend.models.cashbox import CashPayment, CashReceipt, CashShift  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402
from backend.core import background_loop  # noqa: E402
from backend.services import beauty_services  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services import finance_service, analytics_service, salon_home  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
app_module.ensure_booking_tables()
app_module.ensure_user_profile_columns()
cl = TestClient(app_module.app, raise_server_exceptions=False)
background_loop.submit = lambda coro: coro.close()
DIFFS = []
cashbox.NOTIFY_DIFF = lambda owner_id, shift_id: DIFFS.append(str(shift_id))
db = SessionLocal()
NOW = bs.now_utc()
TODAY = bs.to_local(NOW).date()
check("таблицы кассы созданы при старте", {"cash_shifts", "cash_receipts", "cash_payments"} <= set(
    r[0] for r in db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).fetchall()))

plan = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p",
                        is_active=True, product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
db.add(plan)
db.flush()
u = User(email="cash@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
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
pedi = beauty_services.save(db, user, None, {"title": "Педикюр", "price": 1800, "duration_min": 60, "category": "Ногти"})
db.commit()
m1 = BookingResource(user_id=u.id, name="Айгуль", schedule={}, is_active=True)
m2 = BookingResource(user_id=u.id, name="Бермет", schedule={}, is_active=True)
db.add_all([m1, m2])
db.commit()
c1 = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Айжан", phone="+996555100100", status="active", memory={})
db.add(c1)
db.commit()


def mk(master, svc, start, status="booked", visit=None, price=None, prepay=None):
    b = Booking(user_id=u.id, resource_id=master.id, service_entry_id=svc.id, service_title=svc.title, contact_id=c1.id,
                client_name="Айжан", client_phone=c1.phone, start_at=start, end_at=start + timedelta(minutes=60),
                status=status, price=svc.price if price is None else price, source="owner", visit_id=visit,
                prepay_amount=prepay)
    db.add(b)
    db.commit()
    return b


def fresh(model, oid):
    db.expire_all()
    return db.query(model).get(oid)


def K():
    return uuid.uuid4().hex


def rev7():
    db.expire_all()
    return finance_service.summary(db, user, period="7d", with_series=False)


# до кассы: выручка по визитам
legacy = mk(m2, mani, NOW - timedelta(days=3), "completed", price=2000)
bA = mk(m1, mani, NOW - timedelta(hours=3), "arrived")
vid = uuid.uuid4()
vA1 = mk(m1, mani, NOW - timedelta(hours=2), "completed", visit=vid)
vA2 = mk(m1, pedi, NOW - timedelta(hours=1), "completed", visit=vid)
s0 = rev7()
check("до кассы: выручка по визитам (2000 + 1400 + 1400 + 1800)", s0["revenue_parts"]["bookings"] == 6600
      and s0["revenue_basis"] == "visits", s0["revenue_parts"])
st = cl.get("/api/cash/state", headers=HO).json()
check("экран кассы: смены нет, подсказки нет, способы оплаты", st["shift"] is None and st["suggest_opening"] is None
      and [m["key"] for m in st["methods"]] == ["cash", "card", "qr", "transfer"] and st["can_manage"] and st["can_take"])
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(bA.id), "lines": [{"method": "cash", "amount": 1400}], "idem_key": K()})
check("наличные без смены — 409 shift_required", r.status_code == 409 and r.json()["message"]["code"] == "shift_required", r.text[:160])

# гонка открытия смены
ok_open = []


def open_in_thread():
    s = SessionLocal()
    try:
        us = s.query(User).get(u.id)
        sh, created = cashbox.open_shift(s, us, 1000)
        s.commit()
        ok_open.append((str(sh.id), created))
    except Exception as e:  # noqa: BLE001
        ok_open.append(("err", repr(e)))
    finally:
        s.close()


ths = [threading.Thread(target=open_in_thread) for _ in range(3)]
[t.start() for t in ths]
[t.join() for t in ths]
ids_open = {x[0] for x in ok_open}
check("три одновременных «Открыть смену» — одна смена", len(ids_open) == 1 and "err" not in ids_open
      and sum(1 for x in ok_open if x[1] is True) == 1, ok_open)
SH = ok_open[0][0]
r = cl.post("/api/cash/shifts/open", headers=HO, json={"opening_cash": 5000})
check("повторное открытие — та же смена (created=false), при открытии 1000", r.json()["shift"]["id"] == SH
      and r.json()["created"] is False and r.json()["shift"]["opening_cash"] == 1000)
T = cashbox.cash_start(db, u.id)
check("начало кассы — время открытия смены", T is not None and abs((T - NOW).total_seconds()) < 120)
s1 = rev7()
check("после открытия смены выручка по визитам до кассы не изменилась", s1["revenue_parts"]["bookings"] == 6600
      and s1["revenue_basis"] == "payments", s1["revenue_parts"])

# визит после начала кассы — выручка только по оплате
bL = mk(m2, mani, NOW + timedelta(minutes=30), "arrived", price=1500)
check("визит после начала кассы без оплаты — в выручку не попал", rev7()["revenue_parts"]["bookings"] == 6600)
k1 = K()
body = {"booking_id": str(bL.id), "lines": [{"method": "card", "amount": 500}, {"method": "cash", "amount": 1000}], "idem_key": k1}
r1 = cl.post("/api/cash/pay", headers=HO, json=body)
r2 = cl.post("/api/cash/pay", headers=HO, json=body)
rc = r1.json().get("receipt") or {}
check("оплата: карта 500 + наличные 1000 → чек 1500, состояние «оплачено»", r1.status_code == 200 and rc.get("total") == 1500
      and r1.json()["money"]["state"] == "paid" and r1.json()["money"]["left"] == 0
      and sorted((x["method"], x["amount"]) for x in rc["lines"]) == [("card", 500), ("cash", 1000)], r1.text[:200])
check("повтор того же нажатия — тот же чек, второй оплаты нет", r2.status_code == 200 and r2.json()["created"] is False
      and r2.json()["receipt"]["id"] == rc["id"]
      and db.query(CashReceipt).filter(CashReceipt.idem_key == k1).count() == 1
      and db.query(CashPayment).filter(CashPayment.booking_id == bL.id).count() == 2)
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(bL.id), "lines": [{"method": "card", "amount": 10}], "idem_key": K()})
check("визит уже оплачен — 409 paid", r.status_code == 409 and r.json()["message"]["code"] == "paid", r.text[:120])
check("выручка +1500 (оплата визита после начала кассы)", rev7()["revenue_parts"]["bookings"] == 8100)

# визит до начала кассы, оплачен после — не дважды
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(bA.id), "lines": [{"method": "card", "amount": 1400}], "idem_key": K()})
check("оплата визита, начавшегося до кассы, — принята", r.status_code == 200, r.text[:120])
check("…но выручку не удваивает (визит уже учтён по цене)", rev7()["revenue_parts"]["bookings"] == 8100)

# частичная оплата визита из двух услуг (после начала кассы)
v2 = uuid.uuid4()
p1 = mk(m1, mani, NOW + timedelta(hours=2), "booked", visit=v2)
p2 = mk(m1, pedi, NOW + timedelta(hours=3), "booked", visit=v2)
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(p2.id), "lines": [{"method": "card", "amount": 5000}], "idem_key": K()})
check("больше, чем к оплате (3200), — 400 too_much", r.status_code == 400 and r.json()["message"]["code"] == "too_much", r.text[:160])
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(p2.id), "lines": [{"method": "cash", "amount": 2000}], "idem_key": K()})
mon = r.json()["money"]
paid_parts = {x["service"]: x["paid"] for x in mon["parts"]}
check("частично 2000 из 3200: маникюр 1400 оплачен, педикюр 600, осталось 1200", r.status_code == 200
      and paid_parts == {"Маникюр": 1400, "Педикюр": 600} and mon["left"] == 1200 and mon["state"] == "partial"
      and r.json()["receipt"]["prepay"] is True, mon)

# два кассира одновременно за один визит
v3 = uuid.uuid4()
q1 = mk(m2, mani, NOW + timedelta(hours=5), "booked", visit=v3)
q2 = mk(m2, pedi, NOW + timedelta(hours=6), "booked", visit=v3)
Q1, Q2 = q1.id, q2.id          # id — до потоков: объекты главной сессии из потоков не трогаем
res = []


def pay_thread(idem):
    s = SessionLocal()
    try:
        us = s.query(User).get(u.id)
        rr, created = cashbox.pay(s, us, Q1, [{"method": "card", "amount": 3200}], idem)
        s.commit()
        res.append(("ok", rr["id"], created))
    except cashbox.CashError as e:
        s.rollback()
        res.append(("err", e.code, None))
    finally:
        s.close()


ths = [threading.Thread(target=pay_thread, args=(K(),)) for _ in range(4)]
[t.start() for t in ths]
[t.join() for t in ths]
check("4 кассира одновременно платят полную сумму — проходит одна оплата", sum(1 for x in res if x[0] == "ok") == 1
      and all(x[1] in ("paid", "too_much") for x in res if x[0] == "err")
      and float(db.execute(text("SELECT sum(amount) FROM cash_payments WHERE booking_id IN (:a, :b)"),
                           {"a": str(Q1), "b": str(Q2)}).scalar()) == 3200, res)
v4 = uuid.uuid4()
w1 = mk(m2, mani, NOW + timedelta(hours=8), "booked", visit=v4)
W1 = w1.id
res = []
same = K()
ths = [threading.Thread(target=lambda: pay_thread_same(same)) for _ in range(3)]


def pay_thread_same(idem):
    s = SessionLocal()
    try:
        us = s.query(User).get(u.id)
        rr, created = cashbox.pay(s, us, W1, [{"method": "qr", "amount": 700}], idem)
        s.commit()
        res.append(("ok", rr["id"], created))
    except cashbox.CashError as e:
        s.rollback()
        res.append(("err", e.code, None))
    finally:
        s.close()


[t.start() for t in ths]
[t.join() for t in ths]
check("одно нажатие из трёх потоков — один чек", len(res) == 3 and len({x[1] for x in res}) == 1 and all(x[0] == "ok" for x in res)
      and sum(1 for x in res if x[2]) == 1 and db.query(CashReceipt).filter(CashReceipt.idem_key == same).count() == 1, res)

# отменённая запись
cx = mk(m1, mani, NOW + timedelta(days=1), "cancelled")
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(cx.id), "lines": [{"method": "card", "amount": 100}], "idem_key": K()})
check("отменённая запись — оплату не принять", r.status_code == 400 and r.json()["message"]["code"] == "cancelled")
v5 = uuid.uuid4()
z1 = mk(m1, mani, NOW + timedelta(days=1, hours=2), "booked", visit=v5)
z2 = mk(m1, pedi, NOW + timedelta(days=1, hours=3), "booked", visit=v5)
cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(z1.id), "lines": [{"method": "card", "amount": 1400}], "idem_key": K()})
z1.status = "cancelled"
db.commit()
mz = cl.get(f"/api/cash/booking/{z2.id}", headers=HO).json()
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(z2.id), "lines": [{"method": "card", "amount": 1800}], "idem_key": K()})
check("услугу визита отменили после оплаты: остаток другой услуги 1800 — и в карточке, и при оплате",
      mz["left"] == 1800 and mz["state"] == "partial" and r.status_code == 200, (mz["left"], mz["state"], r.text[:120]))

# предоплата и возврат
bF = mk(m1, mani, NOW + timedelta(days=2), "booked", prepay=500)
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(bF.id), "lines": [{"method": "transfer", "amount": 500}], "idem_key": K()})
rF = r.json()["receipt"]
check("предоплата переводом 500: чек «предоплата», отметка «получена»", r.status_code == 200 and rF["prepay"] is True
      and fresh(Booking, bF.id).prepaid_at is not None and r.json()["money"]["prepay_due"] == 0)
r = cl.post(f"/api/cash/receipts/{rF['id']}/refund", headers=HO, json={"amount": 200, "reason": "", "idem_key": K()})
check("возврат без причины — отказ", r.status_code == 400 and "причину" in r.json()["message"]["message"])
kr = K()
r = cl.post(f"/api/cash/receipts/{rF['id']}/refund", headers=HO, json={"amount": 200, "reason": "Клиент перенёс", "idem_key": kr})
r2 = cl.post(f"/api/cash/receipts/{rF['id']}/refund", headers=HO, json={"amount": 200, "reason": "Клиент перенёс", "idem_key": kr})
check("возврат 200 переводом: отметка предоплаты снята, повтор — тот же чек", r.status_code == 200
      and r.json()["receipt"]["lines"][0]["method"] == "transfer" and fresh(Booking, bF.id).prepaid_at is None
      and r2.json()["created"] is False and r2.json()["receipt"]["id"] == r.json()["receipt"]["id"], r.text[:160])
r = cl.post(f"/api/cash/receipts/{rF['id']}/refund", headers=HO, json={"amount": 400, "reason": "ещё", "idem_key": K()})
check("вернуть больше оставшегося по чеку (300) — отказ", r.status_code == 400 and "300" in r.json()["message"]["message"], r.text[:160])
mb = cl.get(f"/api/cash/booking/{bF.id}", headers=HO).json()
check("карточка оплаты записи: чек с «вернули 200, можно ещё 300» и чек возврата", len(mb["receipts"]) == 2
      and mb["receipts"][0]["refunded"] == 200 and mb["receipts"][0]["refundable"] == 300
      and mb["receipts"][1]["kind"] == "refund" and mb["paid"] == 300, mb["receipts"])
r = cl.post(f"/api/cash/receipts/{rc['id']}/refund", headers=HO, json={"amount": 300, "reason": "Скидка за ожидание", "method": "cash", "idem_key": K()})
check("возврат наличными 300 по чеку визита", r.status_code == 200 and r.json()["receipt"]["total"] == 300)

# внесение и изъятие
r = cl.post("/api/cash/moves", headers=HO, json={"kind": "cash_out", "amount": 100, "comment": "", "idem_key": K()})
check("изъятие без комментария — отказ", r.status_code == 400)
r = cl.post("/api/cash/moves", headers=HO, json={"kind": "cash_out", "amount": 999999, "comment": "Забрал владелец", "idem_key": K()})
check("изъять больше, чем в кассе, — отказ", r.status_code == 400 and r.json()["message"]["code"] == "too_much")
check("внесение 200", cl.post("/api/cash/moves", headers=HO, json={"kind": "cash_in", "amount": 200, "comment": "Размен", "idem_key": K()}).status_code == 200)
check("изъятие 300 «Купили воду»", cl.post("/api/cash/moves", headers=HO, json={"kind": "cash_out", "amount": 300, "comment": "Купили воду", "idem_key": K()}).status_code == 200)

# итоги смены и закрытие
st = cl.get("/api/cash/state", headers=HO).json()
n = st["shift"]["numbers"]
exp_cash = 1000 + 1000 + 2000 - 300 + 200 - 300
check("итоги смены: наличные ожидаются 3600, по способам, возвраты, движение кассы", n["expected_cash"] == exp_cash
      and n["by_method"]["cash"] == 2700 and n["by_method"]["card"] == 500 + 1400 + 3200 + 1400 + 1800
      and n["by_method"]["qr"] == 700 and n["by_method"]["transfer"] == 300 and n["refunds"] == 500
      and n["cash_in"] == 200 and n["cash_out"] == 300, n)
r = cl.post(f"/api/cash/shifts/{SH}/close", headers=HO, json={"counted_cash": exp_cash - 500, "note": "Не хватает, разберусь"})
shd = r.json()["shift"]
check("закрытие: насчитали на 500 меньше → расхождение −500, владельцу сообщение", r.status_code == 200 and shd["diff"] == -500
      and shd["status"] == "closed" and shd["expected_cash"] == exp_cash and DIFFS == [SH], shd)
r = cl.post(f"/api/cash/shifts/{SH}/close", headers=HO, json={"counted_cash": 0})
check("второе закрытие — 409", r.status_code == 409)
att = salon_home.attention_block(db, user, bs.now_utc(), None, {}, {}, True)
check("главная салона: «Касса: расхождение»", any(x.get("key") == "cash_diff" and "−500" in x["hint"] for x in att),
      [(x.get("key"), x.get("hint")) for x in att])
st = cl.get("/api/cash/state", headers=HO).json()
check("после закрытия: смены нет, подсказка «в кассе оставили» = насчитанное", st["shift"] is None
      and st["suggest_opening"] == exp_cash - 500)
r = cl.post("/api/cash/shifts/open", headers=HO, json={})
check("новая смена без суммы — открывается с тем, что оставили", r.json()["shift"]["opening_cash"] == exp_cash - 500
      and r.json()["shift"]["id"] != SH)
SH2 = r.json()["shift"]["id"]

# выручка по оплатам везде одинаково
paid_after_T = float(db.execute(text(
    "SELECT COALESCE(sum(cp.amount), 0) FROM cash_payments cp JOIN bookings b ON b.id = cp.booking_id "
    "WHERE cp.kind IN ('payment', 'refund') AND b.start_at >= :t"), {"t": T}).scalar())
s2 = rev7()
check("финансы: выручка = визиты до кассы (6600) + оплаты после (минус возвраты)", s2["revenue_parts"]["bookings"] == 6600 + paid_after_T
      and paid_after_T == 1500 + 2000 + 3200 + 700 + 500 - 200 - 300 + 1400 + 1800, (s2["revenue_parts"], paid_after_T))
a_rep = analytics_service.build_report(db, user, period="7d", sections=["kpis", "bookings"])
check("аналитика: выручка по записям та же, основа — оплаты", a_rep["bookings"]["revenue"] == s2["revenue_parts"]["bookings"]
      and a_rep["bookings"]["revenue_basis"] == "payments", a_rep["bookings"].get("revenue"))
ov = cl.get(f"/api/booking/overview?date={TODAY.isoformat()}&days=3", headers=HO).json()
paid_map = {x["id"]: x["paid"] for x in ov["bookings"]}
check("журнал записи: «оплачено» по каждой записи", paid_map.get(str(bL.id)) == 1200 and paid_map.get(str(p1.id)) == 1400
      and paid_map.get(str(p2.id)) == 600 and paid_map.get(str(bA.id)) == 1400, paid_map)
hist = cl.get(f"/api/beauty/activity?object_type=booking&object_id={bL.id}&period=all", headers=HO).json()["items"]
acts = [x["action"] for x in hist]
check("история записи: оплата и возврат", "payment.create" in acts and "payment.refund" in acts, acts)
hist_v = cl.get(f"/api/beauty/activity?object_type=booking&object_id={p1.id}&period=all", headers=HO).json()["items"]
check("история части визита: оплата визита (оплачивали из другой части)", any(x["action"] == "payment.create" for x in hist_v))
money_feed = cl.get("/api/beauty/activity?group=money&period=all", headers=HO).json()["items"]
check("журнал действий «Деньги»: смены, внесение, изъятие", {"shift.open", "shift.close", "cash.in", "cash.out"} <= {x["action"] for x in money_feed})
day = cl.get("/api/cash/receipts", headers=HO).json()
check("чеки дня владельцу: все кассиры, итог дня по способам", len(day["items"]) >= 10 and day["day"]["revenue"] > 0
      and set(day["day"]["by_method"]) == {"cash", "card", "qr", "transfer"}, day.get("day"))

# сотрудники
adm, _ = ts.create_member(db, user, name="Айпери", email="adm@kg.kg", password="secret12", role_title="Администратор",
                          permissions=["booking", "crm", "inbox"])
acc, _ = ts.create_member(db, user, name="Бухгалтер", email="acc@kg.kg", password="secret12", role_title="Бухгалтер",
                          permissions=["finance"])
mst, _ = ts.create_member(db, user, name="Мастер", email="mst@kg.kg", password="secret12", role_title="Мастер",
                          permissions=["booking_own"])
db.commit()


def sh(m):
    return {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(m.id), "stv": m.token_version})}


r = cl.post("/api/cash/shifts/open", headers=sh(adm), json={"opening_cash": 300})
ADM_SH = r.json()["shift"]["id"]
check("администратор открывает СВОЮ смену (у владельца своя открыта)", r.status_code == 200 and ADM_SH != SH2
      and r.json()["shift"]["cashier"] == "Айпери"
      and db.query(CashShift).filter(CashShift.user_id == u.id, CashShift.status == "open").count() == 2)
bS = mk(m1, mani, NOW + timedelta(hours=10), "arrived")
r = cl.post("/api/cash/pay", headers=sh(adm), json={"booking_id": str(bS.id), "lines": [{"method": "cash", "amount": 1400}], "idem_key": K()})
check("администратор принимает наличные — в своей смене, чек подписан им", r.status_code == 200
      and r.json()["receipt"]["shift_id"] == ADM_SH and r.json()["receipt"]["by"] == "Айпери", r.text[:160])
check("администратор не закрывает смену владельца — 403", cl.post(f"/api/cash/shifts/{SH2}/close", headers=sh(adm), json={"counted_cash": 0}).status_code == 403)
own_list = cl.get("/api/cash/shifts", headers=sh(adm)).json()["items"]
check("администратор видит только свои смены", [x["id"] for x in own_list] == [ADM_SH])
check("администратор: чужую смену не открыть — 403", cl.get(f"/api/cash/shifts/{SH}", headers=sh(adm)).status_code == 403)
all_list = cl.get("/api/cash/shifts", headers=sh(acc)).json()["items"]
check("бухгалтер видит все смены", {SH, SH2, ADM_SH} <= {x["id"] for x in all_list})
check("бухгалтер оплату не принимает — 403", cl.post("/api/cash/pay", headers=sh(acc), json={"booking_id": str(bS.id), "lines": [{"method": "card", "amount": 1}], "idem_key": K()}).status_code == 403)
check("мастер в кассу не попадает — 403", cl.get("/api/cash/state", headers=sh(mst)).status_code == 403)
st_owner = cl.get("/api/cash/state", headers=HO).json()
check("владелец видит открытую смену администратора", [x["id"] for x in st_owner.get("open_shifts", [])] == [ADM_SH])
r = cl.post(f"/api/cash/shifts/{ADM_SH}/close", headers=HO, json={"counted_cash": 1700})
check("владелец закрывает смену администратора без расхождения (300 + 1400)", r.status_code == 200 and r.json()["shift"]["diff"] == 0
      and r.json()["shift"]["closed_by"] == "Айдана" and len(DIFFS) == 1, r.text[:200])
dd = cl.get(f"/api/cash/shifts/{ADM_SH}", headers=HO).json()
check("смена: итоги сохранены при закрытии, чек в ней", dd["shift"]["numbers"]["revenue"] == 1400 and len(dd["receipts"]) == 1)
r = cl.post("/api/cash/pay", headers=sh(adm), json={"booking_id": str(bS.id), "lines": [{"method": "card", "amount": 1}], "idem_key": K()})
check("после закрытия смены администратора: оплата сверх цены — отказ", r.status_code == 409)
bT = mk(m2, pedi, NOW + timedelta(hours=11), "arrived")
r = cl.post("/api/cash/pay", headers=sh(adm), json={"booking_id": str(bT.id), "lines": [{"method": "card", "amount": 1800}], "idem_key": K()})
check("без смены карта принимается (чек без смены)", r.status_code == 200 and r.json()["receipt"]["shift_id"] is None, r.text[:120])

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
