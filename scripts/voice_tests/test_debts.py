"""Долги клиентов и отчёт дня (Voksy Beauty, шаг 3.2).

Часть 1 (без базы): права сотрудника (долги видят администратор и бухгалтер, напоминает администратор, списывает и
настраивает отчёт только владелец), журнал действий (группа «Деньги»), текст напоминания RU/KY, текст отчёта дня,
быстрая функция голоса и инструмент чата владельца, файлы экранов.

Часть 2 (PostgreSQL, DEBTS_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- до кассы долгов нет (выручка по визитам, визит без оплаты — не долг);
- с первой смены: визит без оплаты — долг; визит из двух услуг — ОДИН долг; оплата заранее за ещё не отмеченную
  услугу визита гасит долг; оплаченный, отменённый, «не пришёл», визит до кассы — не долг; частичная оплата уменьшает
  долг, полная — убирает;
- карточка клиента «Долг N сом», главная салона (плитка), «Мои записи» («к оплате»);
- «Напомнить»: текст для окна, очередь отправок один раз в день по визиту, нет карточки / нет мессенджера — понятный
  отказ, журнал действий; бухгалтер и мастер не напоминают;
- «Списать»: только владелец, с причиной, у всех услуг визита, журнал действий, выручкой не считается, второй раз — нет;
- итог дня и текст отчёта, настройки (17–23 ч, только владелец), отметка «отправлен» одна на все воркеры, тик:
  в выбранный час — один раз, раньше — нет, выключен — нет, пустой день — нет;
- голос владельца (get_cash) и чат (get_cash_report; клиенту в переписке — отказ).
"""
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("DEBTS_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="debts-test-secret-0123456789abcdef0123")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import activity  # noqa: E402
from backend.services import cash_debts as cd  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
A = ts.staff_allowed
U = str(uuid.uuid4())
check("долги: видят администратор (booking) и бухгалтер (finance), мастер — нет",
      A(["booking"], "GET", "/api/cash/debts") and A(["finance"], "GET", "/api/cash/debts")
      and not A(["booking_own"], "GET", "/api/cash/debts") and not A(["crm"], "GET", "/api/cash/debts"))
check("напомнить о долге — администратор; бухгалтер и мастер — нет",
      A(["booking"], "POST", f"/api/cash/debts/{U}/remind") and not A(["finance"], "POST", f"/api/cash/debts/{U}/remind")
      and not A(["booking_own"], "POST", f"/api/cash/debts/{U}/remind"))
check("списать долг и настроить отчёт дня — ни один раздел сотрудника (только владелец)",
      not any(A([p], "POST", f"/api/cash/debts/{U}/write-off") for p in ("booking", "finance", "booking_setup", "crm"))
      and not any(A([p], "PUT", "/api/cash/day-report") for p in ("booking", "finance")))
check("журнал действий: долги — в группе «Деньги», есть названия действий",
      "debt" in activity.GROUPS["money"][1] and activity.ACTION_TITLES.get("debt.write_off")
      and activity.ACTION_TITLES.get("debt.remind") and activity.ACTION_TITLES.get("cash.settings"))
when = datetime(2026, 11, 25, 6, 0)          # 12:00 по Бишкеку
ru = cd.remind_text("ru", name="Айжан", salon="Салон Айдана", when=when, services=["Маникюр", "Педикюр"], amount=2200,
                    how="Mbank 0700 123 456")
ky = cd.remind_text("ky", name="Айжан", salon="Салон Айдана", when=when, services=["Маникюр"], amount=1400)
check("напоминание RU: имя, салон, дата словами, услуги, сумма, как оплатить",
      "Айжан" in ru and "Салон Айдана" in ru and "25 ноября" in ru and "Маникюр, Педикюр" in ru and "2 200 сом" in ru
      and "Mbank 0700 123 456" in ru and "{" not in ru, ru)
check("напоминание KY: по-кыргызски, дата цифрами, без «как оплатить», если не задано",
      "Саламатсызбы" in ky and "25.11" in ky and "1 400 сом" in ky and "төлөнө элек" in ky and "Төлөө жолу" not in ky
      and "{" not in ky, ky)
check("напоминание без имени — без лишней запятой", cd.remind_text("ru", name="", salon="С", when=when, services=[],
                                                                    amount=500).startswith("Здравствуйте! Это С."))
fake = {"day_words": "27 ноября", "revenue": 12400, "refunds": 500,
        "by_method": {"cash": 6000, "card": 4400, "qr": 2000, "transfer": 0},
        "shifts": [{"cashier": "Айгерим", "status": "closed", "diff": 0}, {"cashier": "Бермет", "status": "closed", "diff": -200},
                   {"cashier": "Владелец", "status": "open", "diff": None}],
        "new_debts": {"count": 2, "total": 3200, "items": [{"client_name": "Айжан", "left": 1400}, {"client_name": "Нурлан", "left": 1800}]},
        "no_shows": {"count": 1, "items": [{"client": "Мээрим", "time": "15:00"}]}, "diffs": 1}
head, body = cd.report_text(fake, "Салон Айдана")
check("отчёт дня: выручка по способам, возвраты, смены и расхождение, новые долги, неявки",
      "Итог дня · 27 ноября · Салон Айдана" in body and "12 400 сом" in body and "наличные 6 000 сом" in body
      and "перевод" not in body and "Возвраты: −500 сом" in body and "Айгерим — сходится" in body
      and "Бермет — не хватает 200 сом" in body and "смена не закрыта" in body and "Новые долги: 2 · 3 200 сом" in body
      and "Айжан — 1 400 сом" in body and "Не пришли: 1 (Мээрим 15:00)" in body, body)
check("push отчёта: коротко — выручка, долги, неявки, расхождение",
      head == "Выручка 12 400 сом · долги 3 200 сом · не пришли 1 · есть расхождение", head)
from backend.services import owner_voice as ov  # noqa: E402
from backend.services import owner_voice_demo as ovd  # noqa: E402
from backend.services import salon_ai_tools  # noqa: E402
fn = {f["name"]: f for f in ov.GEMINI_FUNCTIONS}
check("голос владельца: get_cash — быстрая (BLOCKING), в демо есть ответ без базы",
      "get_cash" in ov.FAST_FUNCTIONS and fn["get_cash"]["behavior"] == "BLOCKING" and "get_cash" in ov.FAST_IMPL
      and ovd.run_demo_function("get_cash", {"day": "today"}).get("enabled") is False)
check("чат владельца: get_cash_report только владельцу", "get_cash_report" in salon_ai_tools.OWNER_TOOL_NAMES
      and any(t["name"] == "get_cash_report" for t in salon_ai_tools.OWNER_TOOLS))


def src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


cj, dh, cs, mj, aj, crm = (src("backend/static/cash/cash.js"), src("backend/static/js/dashboard-home.js"),
                           src("backend/static/start/crm-salon.js"), src("backend/static/my/my.js"),
                           src("backend/static/automations/automations.js"), src("backend/static/start-crm.html"))
check("экран «Касса»: вкладка «Долги», «Напомнить», «Списать», «Итог дня», время отчёта",
      "'debts'" in cj and "/api/cash/debts" in cj and "/remind" in cj and "/write-off" in cj and "Итог дня" in cj
      and "/api/cash/day-report" in cj)
check("главная салона: плитка «Долги клиентов»", "Долги клиентов" in dh and "cash.html#debts" in dh)
check("карточка клиента: «Долг» и «Принять оплату»; на странице клиентов есть окно оплаты",
      "c.debt" in cs and "BookingPay" in cs and "/static/booking/pay.js" in crm and "/static/booking/pay.css" in crm)
check("«Мои записи»: «к оплате»", "due" in mj and "К оплате" in mj)
check("журнал отправок: название правила «Напоминание о долге»", "Напоминание о долге" in aj)

if not PG:
    print("SKIP часть 2: DEBTS_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ───────────────────────────── часть 2 ─────────────────────────────
import asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_contact import AgentContact  # noqa: E402
from backend.models.automation import NotificationQueue  # noqa: E402
from backend.models.booking import Booking, BookingResource  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402
from backend.core import background_loop  # noqa: E402
from backend.services import beauty_services, cashbox, client_cabinet, finance_service, inbox_service, salon_home  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402

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
cols = {r[0] for r in db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'bookings'")).fetchall()}
pcols = {r[0] for r in db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'booking_pages'")).fetchall()}
check("колонки: bookings.debt_off, booking_pages.cash_settings", "debt_off" in cols and "cash_settings" in pcols)

plan = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p",
                        is_active=True, product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
db.add(plan)
db.flush()
u = User(email="debts@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
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
page.settings = dict(page.settings or {}, title="Салон Айдана", lead_min=0, step_min=15, prepay_info="Mbank 0700 123 456")
db.commit()
mani = beauty_services.save(db, user, None, {"title": "Маникюр", "price": 1400, "duration_min": 60, "category": "Ногти"})
pedi = beauty_services.save(db, user, None, {"title": "Педикюр", "price": 1800, "duration_min": 60, "category": "Ногти"})
db.commit()
m1 = BookingResource(user_id=u.id, name="Айгуль", schedule={}, is_active=True)
m2 = BookingResource(user_id=u.id, name="Бермет", schedule={}, is_active=True)
db.add_all([m1, m2])
db.commit()
c1 = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Айжан Токтосунова", phone="+996555100100", status="active", memory={})
c2 = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Нурлан", phone="+996700200200", status="active", memory={})
db.add_all([c1, c2])
db.commit()


def mk(master, svc, start, status="completed", contact=c1, visit=None, price=None, prepay=None):
    b = Booking(user_id=u.id, resource_id=master.id, service_entry_id=svc.id, service_title=svc.title,
                contact_id=contact.id if contact is not None else None,
                client_name=contact.name if contact is not None else "Гость", client_phone=contact.phone if contact is not None else "+996777000111",
                start_at=start, end_at=start + timedelta(minutes=60), status=status,
                price=svc.price if price is None else price, source="owner", visit_id=visit, prepay_amount=prepay)
    db.add(b)
    db.commit()
    return b


def K():
    return uuid.uuid4().hex


def debts():
    db.expire_all()
    return cl.get("/api/cash/debts", headers=HO).json()


# до кассы
old = mk(m1, mani, NOW - timedelta(days=2))
r = debts()
check("до кассы: долгов нет (визит без оплаты — не долг), касса не включена", r["enabled"] is False and r["count"] == 0
      and r["items"] == [], r)
check("главная салона до кассы: плитки долгов нет", salon_home.now_block(db, user, bs.now_utc(), {}, {})["debts"] is None)

# первая смена — начало кассы
r = cl.post("/api/cash/shifts/open", headers=HO, json={"opening_cash": 1000})
check("смена открыта", r.status_code == 200, r.text[:120])
# смена открыта 14 часов назад: визиты дня — в прошлом (их видно в «Моих записях»), но после начала кассы
db.execute(text("UPDATE cash_shifts SET opened_at = :t WHERE user_id = :u"), {"t": NOW - timedelta(hours=14), "u": str(u.id)})
db.commit()
cashbox._START_CACHE.clear()
T = cashbox.cash_start(db, u.id)
check("начало кассы — время смены", T is not None and abs((T - (NOW - timedelta(hours=14))).total_seconds()) < 2)
B0 = NOW - timedelta(hours=13)
H = lambda h, m=0: B0 + timedelta(hours=h, minutes=m)   # noqa: E731

vA = mk(m1, mani, H(0))                                                          # 1400, без оплаты
vid = uuid.uuid4()
vB1 = mk(m1, mani, H(1), visit=vid)                                              # визит 1400 + 1800
vB2 = mk(m2, pedi, H(2), visit=vid)
vC = mk(m2, pedi, H(3), contact=c2)                                              # 1800, оплачен полностью
vD = mk(m1, mani, H(2), status="cancelled")                                      # отменён
vE = mk(m2, mani, H(4), status="no_show", contact=c2)                            # не пришёл
vid2 = uuid.uuid4()
vF1 = mk(m1, mani, H(3), contact=c2, visit=vid2)                                 # пришёл 1400
vF2 = mk(m2, pedi, H(5), status="booked", contact=c2, visit=vid2, prepay=500)    # ещё не отмечен
vG = mk(m1, mani, H(4), contact=None)                                            # без карточки клиента

r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(vB1.id), "lines": [{"method": "card", "amount": 1000}], "idem_key": K()})
check("частичная оплата визита из двух услуг", r.status_code == 200, r.text[:160])
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(vC.id), "lines": [{"method": "cash", "amount": 1800}], "idem_key": K()})
check("полная оплата визита C", r.status_code == 200, r.text[:160])
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(vF2.id), "lines": [{"method": "qr", "amount": 500}], "idem_key": K(), "prepay": True})
check("предоплата за ещё не отмеченную услугу визита F", r.status_code == 200, r.text[:160])

r = debts()
items = {x["booking_id"]: x for x in r["items"]}
check("долги: A (1400), B — один долг на визит (3200 − 1000), F (1400 − 500), без карточки G; не долг: C, D, E, до кассы",
      set(items) == {str(vA.id), str(vB1.id), str(vF1.id), str(vG.id)} and items[str(vA.id)]["left"] == 1400
      and items[str(vB1.id)]["left"] == 2200 and items[str(vB1.id)]["due"] == 3200 and items[str(vB1.id)]["paid"] == 1000
      and items[str(vF1.id)]["left"] == 900 and items[str(vG.id)]["left"] == 1400, [(x["client_name"], x["left"]) for x in r["items"]])
check("итог: 4 долга на 5 900 сом, клиентов 3", r["count"] == 4 and r["total"] == 5900 and r["clients"] == 3, (r["count"], r["total"], r["clients"]))
check("строка долга: за что (услуги визита, мастера), с какого дня", items[str(vB1.id)]["services"] == ["Маникюр", "Педикюр"]
      and items[str(vB1.id)]["masters"] == ["Айгуль", "Бермет"] and items[str(vB1.id)]["date"] == bs.to_local(vB1.start_at).date().isoformat()
      and items[str(vB1.id)]["phone"] == "+996555100100")
check("владельцу: можно списать; администратору список с флагом can_take", r["can_write_off"] is True and r["can_take"] is True)

card = cl.get(f"/api/beauty/clients/{c1.id}", headers=HO).json()
check("карточка клиента: «Долг 3 600 сом» — два визита", (card.get("debt") or {}).get("total") == 3600
      and len(card["debt"]["items"]) == 2 and card["debt"]["can_take"] is True, card.get("debt"))
card2 = cl.get(f"/api/beauty/clients/{c2.id}", headers=HO).json()
check("карточка клиента без долга по оплаченному визиту: только F (900)", (card2.get("debt") or {}).get("total") == 900)
home = salon_home.now_block(db, user, bs.now_utc(), {}, {})
check("главная салона: плитка «Долги клиентов» 4 · 5 900", home["debts"] == {"count": 4, "total": 5900}, home.get("debts"))

tok = client_cabinet.token_for_contact(c1.id)
mine = cl.get(f"/api/public/client/{tok}").json()
dues = {h["id"]: h.get("due") for h in mine.get("history", [])}
check("«Мои записи»: «к оплате» у визитов с долгом, итог 3 600", dues.get(str(vA.id)) == 1400 and dues.get(str(vB1.id)) == 2200
      and mine.get("due_total") == 3600, (dues, mine.get("due_total")))

# напомнить
SENT_CHANNELS = {str(c1.id): {"whatsapp": {"can_send": True}}, str(c2.id): {"whatsapp": {"can_send": False, "reason": "нет"}}}
inbox_service.send_channels = lambda d, contact, ag: SENT_CHANNELS.get(str(contact.id), {})
r = cl.post(f"/api/cash/debts/{vB2.id}/remind", headers=HO, json={"send": False})
pv = r.json()
check("напомнить: текст для окна по любой услуге визита, WhatsApp, сумма и как оплатить, ничего не отправлено",
      r.status_code == 200 and pv["channel"] == "whatsapp" and "2 200 сом" in pv["text"] and "Маникюр, Педикюр" in pv["text"]
      and "Mbank 0700 123 456" in pv["text"] and "Айжан" in pv["text"] and not pv.get("queued")
      and db.query(NotificationQueue).count() == 0, r.text[:300])
r = cl.post(f"/api/cash/debts/{vB1.id}/remind", headers=HO, json={"send": True})
q = db.query(NotificationQueue).filter(NotificationQueue.rule == "debt").all()
check("отправить: одно сообщение в очереди (правило debt, по визиту, сервисное окно)", r.status_code == 200 and r.json().get("queued")
      and len(q) == 1 and str(q[0].booking_id) == str(vB1.id) and q[0].kind == "service" and "2 200 сом" in q[0].text, r.text[:200])
r = cl.post(f"/api/cash/debts/{vB2.id}/remind", headers=HO, json={"send": True})
check("второй раз за день по тому же визиту — 409, второго сообщения нет", r.status_code == 409
      and r.json()["message"]["code"] == "reminded" and db.query(NotificationQueue).count() == 1, r.text[:200])
check("в списке долгов — «напомнили сегодня»", {x["booking_id"]: x for x in debts()["items"]}[str(vB1.id)]["reminded_today"] is True)
r = cl.post(f"/api/cash/debts/{vG.id}/remind", headers=HO, json={"send": False})
check("клиент без карточки — понятный отказ с телефоном", r.status_code == 400 and r.json()["message"]["code"] == "no_contact"
      and "+996777000111" in r.json()["message"]["message"], r.text[:200])
r = cl.post(f"/api/cash/debts/{vF1.id}/remind", headers=HO, json={"send": True})
check("некуда написать — «напомните по телефону», в очереди ничего", r.status_code == 400
      and r.json()["message"]["code"] == "no_channel" and db.query(NotificationQueue).count() == 1, r.text[:200])
r = cl.post(f"/api/cash/debts/{vC.id}/remind", headers=HO, json={"send": False})
check("оплаченный визит — долга нет (409)", r.status_code == 409 and r.json()["message"]["code"] == "no_debt")
acts = [x["action"] for x in cl.get("/api/beauty/activity?group=money&period=all", headers=HO).json()["items"]]
check("журнал действий: напоминание о долге", "debt.remind" in acts, acts[:8])

# оплата долга
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(vB2.id), "lines": [{"method": "cash", "amount": 1200}], "idem_key": K()})
check("частично оплатили долг B — осталось 1 000", r.status_code == 200
      and {x["booking_id"]: x for x in debts()["items"]}[str(vB1.id)]["left"] == 1000, r.text[:160])
r = cl.post("/api/cash/pay", headers=HO, json={"booking_id": str(vB1.id), "lines": [{"method": "card", "amount": 1000}], "idem_key": K()})
check("оплатили остаток — долг B ушёл", r.status_code == 200 and str(vB1.id) not in {x["booking_id"] for x in debts()["items"]})

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


ra = cl.get("/api/cash/debts", headers=sh(adm)).json()
check("администратор видит долги, списать не может", ra["count"] == 3 and ra["can_write_off"] is False and ra["can_take"] is True)
rb = cl.get("/api/cash/debts", headers=sh(acc)).json()
check("бухгалтер видит долги, принять оплату не может", rb["count"] == 3 and rb["can_take"] is False)
check("мастер долгов не видит — 403", cl.get("/api/cash/debts", headers=sh(mst)).status_code == 403)
check("бухгалтер не напоминает — 403", cl.post(f"/api/cash/debts/{vA.id}/remind", headers=sh(acc), json={"send": False}).status_code == 403)
r = cl.post(f"/api/cash/debts/{vA.id}/remind", headers=sh(adm), json={"send": True})
check("администратор напоминает (сообщение в очереди)", r.status_code == 200 and r.json().get("queued"), r.text[:160])
for who, hdr in (("администратор", sh(adm)), ("бухгалтер", sh(acc))):
    rr = cl.post(f"/api/cash/debts/{vA.id}/write-off", headers=hdr, json={"reason": "Подарок"})
    check(f"{who} списать долг не может — 403", rr.status_code == 403, rr.text[:100])
check("отчёт дня настраивает только владелец — сотруднику 403",
      cl.put("/api/cash/day-report", headers=sh(adm), json={"hour": 20}).status_code == 403)

# списать
rev_before = finance_service.summary(db, user, period="7d", with_series=False)["revenue_parts"]["bookings"]
r = cl.post(f"/api/cash/debts/{vA.id}/write-off", headers=HO, json={"reason": ""})
check("списать без причины — 400", r.status_code == 400 and r.json()["message"]["code"] == "reason")
r = cl.post(f"/api/cash/debts/{vF2.id}/write-off", headers=HO, json={"reason": "Подарок / комплимент"})
db.expire_all()
f1, f2 = db.query(Booking).get(vF1.id), db.query(Booking).get(vF2.id)
check("владелец списал долг визита F (по любой услуге визита): отметка у обеих услуг, сумма 900", r.status_code == 200
      and r.json()["amount"] == 900 and f1.debt_off and f2.debt_off and f1.debt_off["reason"] == "Подарок / комплимент"
      and f1.debt_off["by"] == "Айдана", r.text[:200])
check("списанный долг ушёл из списка", str(vF1.id) not in {x["booking_id"] for x in debts()["items"]})
rev_after = finance_service.summary(db, user, period="7d", with_series=False)["revenue_parts"]["bookings"]
check("списание выручкой не считается", rev_after == rev_before, (rev_before, rev_after))
r = cl.post(f"/api/cash/debts/{vF1.id}/write-off", headers=HO, json={"reason": "ещё раз"})
check("второй раз списать — 409", r.status_code == 409 and r.json()["message"]["code"] == "no_debt")
check("напомнить о списанном — 409", cl.post(f"/api/cash/debts/{vF1.id}/remind", headers=HO, json={"send": False}).status_code == 409)
acts = cl.get("/api/beauty/activity?group=money&period=all", headers=HO).json()["items"]
w = [x for x in acts if x["action"] == "debt.write_off"]
check("журнал действий: «Долг списан: 900 сом … причина»", w and "900 сом" in w[0]["summary"] and "Подарок" in w[0]["summary"],
      [x["summary"] for x in w])
mine = cl.get(f"/api/public/client/{client_cabinet.token_for_contact(c2.id)}").json()
check("«Мои записи» после списания: к оплате ничего", mine.get("due_total") == 0, mine.get("due_total"))

# итог дня
st = cl.get("/api/cash/state", headers=HO).json()
day = st.get("day") or {}
left_now = debts()["items"]
exp_new = [x for x in left_now if x["date"] == TODAY.isoformat()]
exp_ns = 1 if bs.to_local(vE.start_at).date() == TODAY else 0
check("экран «Касса»: итог дня — выручка как у итогов кассы, смена, неявка, новые долги этого дня",
      day.get("revenue") == st["today"]["revenue"] and day.get("open_shifts") == 1 and day["no_shows"]["count"] == exp_ns
      and day["new_debts"]["count"] == len(exp_new) and day["new_debts"]["total"] == sum(x["left"] for x in exp_new)
      and day["empty"] is False and day["revenue"] > 0, (day.get("new_debts", {}).get("count"), len(exp_new), day.get("no_shows")))
check("экран «Касса»: число долгов и настройки отчёта (по умолчанию 21:00, вкл.)", st["debts"] == {"count": 2, "total": 2800}
      and st["day_report"] == {"on": True, "hour": 21} and st["report_hours"] == list(range(17, 24)), (st.get("debts"), st.get("day_report")))
check("администратору итог дня по салону не показывается", cl.get("/api/cash/state", headers=sh(adm)).json().get("day") is None)
rc = cl.get(f"/api/cash/receipts?date={TODAY.isoformat()}", headers=HO).json()
check("чеки дня: «Итог дня» за выбранный день", (rc.get("report") or {}).get("date") == TODAY.isoformat())
r = cl.put("/api/cash/day-report", headers=HO, json={"hour": 10})
check("время отчёта вне 17–23 — 400", r.status_code == 400)
r = cl.put("/api/cash/day-report", headers=HO, json={"hour": 20})
check("время отчёта 20:00 сохранено", r.status_code == 200 and r.json()["day_report"] == {"on": True, "hour": 20}
      and cd.day_report_settings(db, u.id)["hour"] == 20)
r = cl.put("/api/booking/settings", headers=HO, json={"step_min": 30})
check("«Правила записи» не стирают настройки отчёта", cd.day_report_settings(db, u.id)["hour"] == 20, r.status_code)

ok_claims = []


def claim_in_thread():
    s = SessionLocal()
    try:
        ok_claims.append(cd.claim_day_report(s, u.id, TODAY))
    finally:
        s.close()


ths = [threading.Thread(target=claim_in_thread) for _ in range(4)]
[t.start() for t in ths]
[t.join() for t in ths]
check("отметка «отчёт отправлен» — ровно один из четырёх воркеров", ok_claims.count(True) == 1, ok_claims)
db.execute(text("UPDATE booking_pages SET cash_settings = cash_settings - 'day_report_sent' WHERE user_id = :u"), {"u": str(u.id)})
db.commit()

SENT = []


async def fake_deliver(s, usr, rep):
    SENT.append((str(usr.id), rep["date"], cd.report_text(rep)[1]))
    return {"telegram": True, "push": True}


cd.DELIVER = fake_deliver
at = lambda h, m=5: datetime(TODAY.year, TODAY.month, TODAY.day, h, m, tzinfo=bs.BISHKEK)   # noqa: E731
n0 = asyncio.run(cd.tick(db, at(19)))
check("тик в 19:05 при времени 20:00 — отчёта нет", n0 == 0 and not SENT)
n1 = asyncio.run(cd.tick(db, at(20)))
n2 = asyncio.run(cd.tick(db, at(20, 10)))
check("тик в 20:05 — один отчёт, в 20:10 — повтора нет", n1 == 1 and n2 == 0 and len(SENT) == 1 and SENT[0][1] == TODAY.isoformat(), SENT)
check("текст отчёта: выручка, новые долги и неявки этого дня", "Выручка" in SENT[0][2]
      and (("Новые долги: " + str(len(exp_new))) in SENT[0][2] if exp_new else "Новые долги" not in SENT[0][2])
      and (("Не пришли: 1") in SENT[0][2] if exp_ns else "Не пришли" not in SENT[0][2]), SENT[0][2])
cl.put("/api/cash/day-report", headers=HO, json={"on": False})
db.execute(text("UPDATE booking_pages SET cash_settings = cash_settings - 'day_report_sent' WHERE user_id = :u"), {"u": str(u.id)})
db.commit()
check("отчёт выключен — не отправляется", asyncio.run(cd.tick(db, at(22))) == 0 and len(SENT) == 1)
cl.put("/api/cash/day-report", headers=HO, json={"on": True})
empty = datetime(TODAY.year, TODAY.month, TODAY.day, 22, 5, tzinfo=bs.BISHKEK) + timedelta(days=5)
check("пустой день (ни чеков, ни смен, ни визитов) — без отчёта", asyncio.run(cd.tick(db, empty)) == 0 and len(SENT) == 1)

# голос и чат владельца
agent_row = db.query(AgentConfig).get(agent.id)
v = ov.fast_get_cash(db, user, agent_row, {"day": "today"})
check("голос: get_cash — выручка, долги, карточка-список с должниками", v["enabled"] and v["debts"]["count"] == 2
      and v["card"]["type"] == "list" and any("Айжан" in x["title"] for x in v["card"]["items"]) and "Касса" in v["card"]["title"], v.get("card"))
out = asyncio.run(salon_ai_tools.execute("get_cash_report", {"day": "today"}, {"user": user, "surface": "owner_chat"}, db))
check("чат владельца: get_cash_report — сводка и долги", '"enabled": true' in out and "Должны 2" in out, out[:200])
out = asyncio.run(salon_ai_tools.execute("get_cash_report", {}, {"user": user, "surface": "postcall"}, db))
check("клиенту в переписке — отказ", "только владельцу" in out)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
