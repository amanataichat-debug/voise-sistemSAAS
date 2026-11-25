"""
Услуги салона (Voksy Beauty, шаг 1.3).

Часть 1 (без сети и базы): категория/подкатегория в одной строке, цена «от — до» словами, правило
предоплаты, сумма предоплаты, права сотрудника на /api/booking/services.

Часть 2 (PostgreSQL, SERVICES_TEST_DATABASE_URL): миграция колонок, создание услуги с категорией и
мастерами (свои цены), подготовка до и перерыв после в свободном времени и при записи (и общий перерыв
в обе стороны), «только через администратора» (ссылка, бот, звонок — нет; кабинет — да), предоплата в
записи и отметка «получена», цена диапазоном в прайсе звонка и в поиске бота, порядок и переименование
категорий, удаление услуги (с записями — выключение), публичная страница (категории, онлайн-услуги,
как внести предоплату), права сотрудников.
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("SERVICES_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="services-test-secret-0123456789abcdef",
                  KB_VECTOR_ENABLED="0")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import booking_service as bs  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────── часть 1 ─────────────────────────
check("категория и подкатегория", bs.split_category("Волосы / Окрашивание") == ("Волосы", "Окрашивание")
      and bs.split_category("Ногти") == ("Ногти", "") and bs.split_category(None) == ("", ""))
check("склейка категории без «/» внутри", bs.join_category("Волосы", "Окраш/ивание") == "Волосы / Окраш ивание"
      and bs.join_category("", "Стрижки") is None and bs.join_category("  Ногти ", "") == "Ногти")
check("цена словами", bs.price_text(1500, 2500) == "1 500–2 500 сом" and bs.price_text(1500) == "1 500 сом"
      and bs.price_text(1500, 1500) == "1 500 сом" and bs.price_text(None) == "")
check("правило предоплаты", bs.clean_prepay({"type": "percent", "value": "30"}) == {"type": "percent", "value": 30.0}
      and bs.clean_prepay({"type": "percent", "value": 130}) is None and bs.clean_prepay({"type": "fixed", "value": 0}) is None
      and bs.clean_prepay("x") is None)


class _S:
    def __init__(self, prepay):
        self.prepay = prepay


check("сумма предоплаты", bs.prepay_amount(_S({"type": "percent", "value": 30}), 2000) == 600
      and bs.prepay_amount(_S({"type": "fixed", "value": 500}), None) == 500
      and bs.prepay_amount(_S(None), 2000) is None and bs.prepay_amount(None, 2000) is None)
check("предоплата словами", bs.prepay_text({"type": "percent", "value": 30}) == "предоплата 30%"
      and bs.prepay_text({"type": "fixed", "value": 500}) == "предоплата 500 сом")
A = ts.staff_allowed
check("услуги: смотреть — журнал и каталог", A(["booking"], "GET", "/api/booking/services") and A(["catalog"], "GET", "/api/booking/services")
      and not A(["inbox"], "GET", "/api/booking/services"))
check("услуги: менять — «Мастера и график» и каталог", A(["booking_setup"], "POST", "/api/booking/services")
      and A(["catalog"], "PATCH", "/api/booking/services/1") and A(["booking_setup"], "POST", "/api/booking/services/categories/rename")
      and not A(["booking"], "POST", "/api/booking/services") and not A(["booking"], "DELETE", "/api/booking/services/1")
      and not A(["booking_own"], "PATCH", "/api/booking/services/1"))

if not PG:
    print("SKIP: часть 2 — SERVICES_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ───────────────────────── часть 2: PostgreSQL ─────────────────────────
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.models.booking import Booking  # noqa: E402
from backend.models.team import TeamMember  # noqa: E402
from backend.core.security import create_jwt_token, hash_password  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)

import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

with engine.begin() as conn:   # старая база: колонок услуг и записи ещё нет
    for col in ("price_to", "prep_min", "after_min", "online_booking", "prepay"):
        conn.execute(text(f"ALTER TABLE agent_knowledge_entries DROP COLUMN {col}"))
    for col in ("prep_min", "after_min", "prepay_amount", "prepaid_at"):
        conn.execute(text(f"ALTER TABLE bookings DROP COLUMN {col}"))
app_module.ensure_user_profile_columns()
app_module.ensure_booking_tables()
app_module.ensure_booking_tables()
with engine.connect() as conn:
    kcols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='agent_knowledge_entries'"))}
    bcols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='bookings'"))}
check("миграция: колонки услуг и записи", {"price_to", "prep_min", "after_min", "online_booking", "prepay"} <= kcols
      and {"prep_min", "after_min", "prepay_amount", "prepaid_at"} <= bcols, (sorted(kcols)[-6:], sorted(bcols)[-6:]))
app_module.ensure_team_tables()
app_module.ensure_finance_tables()
app_module.ensure_branding_columns()

cl = TestClient(app_module.app, raise_server_exceptions=False)
now = datetime.now(timezone.utc)
db = SessionLocal()


def plan(code):
    p = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).first()
    if not p:
        p = SubscriptionPlan(code=code, name=code.title(), price=100, max_assistants=3, description=code, is_active=True)
        db.add(p)
        db.flush()
    return p


owner = User(email="owner@salon.kg", password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
             wallet_balance=100000, first_name="Айгерим", company_name="Салон Айгерим", business_niche="beauty_salon",
             business_profile={"modules": {"booking": True}})
owner.subscription_plan_id = plan("start").id
owner.subscription_end_date = now + timedelta(days=20)
db.add(owner)
noagent = User(email="new@salon.kg", password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
               first_name="Без бота", business_niche="nails")
db.add(noagent)
db.flush()
agent = AgentConfig(user_id=owner.id, name="Бот салона")
db.add(agent)
db.commit()
HO = {"Authorization": f"Bearer {create_jwt_token(owner.id)}"}
HN = {"Authorization": f"Bearer {create_jwt_token(noagent.id)}"}


def err(r):
    try:
        d = r.json()
    except Exception:
        return ""
    x = d.get("detail") or d.get("message")
    return x if isinstance(x, str) else str(x)


r = cl.get("/api/booking/services", headers=HN)
check("без бота: список пуст и подсказка создать бота", r.status_code == 200 and r.json()["has_agent"] is False, r.text[:160])
r = cl.post("/api/booking/services", headers=HN, json={"title": "Маникюр"})
check("без бота: услугу не создать — понятная причина", r.status_code == 400 and "бота" in err(r), r.text[:160])

# мастера
ov = cl.get("/api/booking/overview", headers=HO).json()
aigul = ov["resources"][0]
cl.patch(f"/api/booking/resources/{aigul['id']}", headers=HO, json={"name": "Айгуль", "schedule": {d: [["09:00", "19:00"]] for d in bs.DAYS}})
meerim = cl.post("/api/booking/resources", headers=HO, json={"name": "Мээрим"}).json()["resource"]
cl.patch(f"/api/booking/resources/{meerim['id']}", headers=HO, json={"schedule": {d: [["09:00", "19:00"]] for d in bs.DAYS}})

r = cl.post("/api/booking/services", headers=HO, json={
    "title": "Окрашивание в один тон", "category": "Волосы", "subcategory": "Окрашивание", "description": "Краска L'Oreal",
    "price": 3000, "price_to": 5000, "duration_min": 120, "prep_min": 15, "after_min": 15,
    "prepay": {"type": "percent", "value": 30},
    "masters": [{"id": aigul["id"], "does": True}, {"id": meerim["id"], "does": True, "price": 4000}]})
color = r.json().get("service") or {}
check("услуга создана с категорией, ценой «от–до», временем и предоплатой", r.status_code == 200
      and color.get("category") == "Волосы" and color.get("subcategory") == "Окрашивание"
      and color.get("price_text") == "3 000–5 000 сом" and color.get("prep_min") == 15 and color.get("after_min") == 15
      and color.get("prepay") == {"type": "percent", "value": 30.0} and color.get("prepay_text") == "предоплата 30%", r.text[:300])
check("мастера услуги и свои цены", sorted((m["id"], m["price"]) for m in color.get("masters", []))
      == sorted([(aigul["id"], None), (meerim["id"], 4000.0)]), color.get("masters"))
r = cl.post("/api/booking/services", headers=HO, json={
    "title": "Женская стрижка", "category": "Волосы", "subcategory": "Стрижки", "price": 1000, "duration_min": 60,
    "masters": [{"id": meerim["id"], "does": True}]})
cut = r.json().get("service") or {}
r = cl.post("/api/booking/services", headers=HO, json={
    "title": "Сложное окрашивание", "category": "Волосы", "subcategory": "Окрашивание", "price": 8000, "duration_min": 240,
    "online_booking": False, "masters": [{"id": aigul["id"], "does": True}]})
hard = r.json().get("service") or {}
check("услуга «только через администратора»", r.status_code == 200 and hard.get("online_booking") is False, r.text[:160])
r = cl.post("/api/booking/services", headers=HO, json={"title": "Маникюр", "category": "Ногти", "price": 1200, "duration_min": 60,
                                                       "masters": [{"id": aigul["id"]}]})
mani = r.json().get("service") or {}
db.expire_all()
ra = cl.get("/api/booking/resources", headers=HO).json()["resources"]
ra = {x["id"]: x for x in ra}
check("стрижку Айгуль не делает (была «делает всё»)", cut["id"] not in (ra[aigul["id"]]["service_ids"] or [])
      and color["id"] in ra[aigul["id"]]["service_ids"], ra[aigul["id"]]["service_ids"])
check("у Мээрим своя цена окрашивания", ra[meerim["id"]]["service_prices"].get(color["id"]) == {"price": 4000.0}, ra[meerim["id"]]["service_prices"])

for bad, why in [({"title": ""}, "название"), ({"title": "X", "price_to": 100}, "от"),
                 ({"title": "X", "duration_min": 2}, "Длительность"), ({"title": "X", "prepay": {"type": "percent", "value": 300}}, "Предоплата"),
                 ({"title": "X", "prep_min": 500}, "Подготовка")]:
    r = cl.post("/api/booking/services", headers=HO, json=bad)
    check(f"проверка поля: {why}", r.status_code == 400 and why.lower() in err(r).lower(), (r.status_code, err(r)))

r = cl.get("/api/booking/services", headers=HO)
j = r.json()
check("список: категории в порядке и подкатегории", [c["name"] for c in j["categories"]] == ["Волосы", "Ногти"]
      and j["categories"][0]["subs"] == ["Окрашивание", "Стрижки"], j["categories"])

# подготовка и перерыв в свободном времени
tomorrow = (bs.to_local(bs.now_utc()) + timedelta(days=1)).date().isoformat()
r = cl.get(f"/api/booking/slots?service_id={color['id']}&resource_id={aigul['id']}&date={tomorrow}", headers=HO)
times = [x["time"] for x in r.json()["days"][0]["slots"]]
check("подготовка 15 мин — первое окно не в начале смены", times and times[0] == "09:30" and times[-1] == "17:00", times[:3] + times[-2:])
r = cl.post("/api/booking/bookings", headers=HO, json={"start": f"{tomorrow}T10:00", "service_id": color["id"],
                                                       "resource_id": aigul["id"], "client_name": "Жылдыз", "client_phone": "+996700111222"})
b1 = r.json().get("booking") or {}
check("запись хранит подготовку, перерыв и предоплату 30% от 3000", b1.get("prep_min") == 15 and b1.get("after_min") == 15
      and b1.get("prepay_amount") == 900 and b1.get("prepaid") is False and b1.get("duration_min") == 120, b1)
r = cl.get(f"/api/booking/slots?service_id={mani['id']}&resource_id={aigul['id']}&date={tomorrow}", headers=HO)
times = [x["time"] for x in r.json()["days"][0]["slots"]]
check("занято с 9:45 до 12:15 (подготовка + услуга + перерыв)", "08:30" not in times and "09:00" not in times
      and "12:00" not in times and "12:30" in times and "08:00" not in times, times[:6])
check("перед записью: маникюр 60 мин не влезает в 9:00–9:45", "09:00" not in times, times[:3])
r = cl.post("/api/booking/bookings", headers=HO, json={"start": f"{tomorrow}T12:00", "service_id": mani["id"],
                                                       "resource_id": aigul["id"], "client_name": "Нурай"})
check("в перерыв после окрашивания записать нельзя", r.status_code == 400 and "занято" in err(r), r.text[:160])
r = cl.post("/api/booking/bookings", headers=HO, json={"start": f"{tomorrow}T12:15", "service_id": mani["id"],
                                                       "resource_id": aigul["id"], "client_name": "Нурай"})
check("сразу после перерыва — можно", r.status_code == 200, r.text[:160])

# общий перерыв в обе стороны
cl.put("/api/booking/settings", headers=HO, json={"settings": {"buffer_min": 15}})
r = cl.post("/api/booking/bookings", headers=HO, json={"start": f"{tomorrow}T16:00", "service_id": cut["id"],
                                                       "resource_id": meerim["id"], "client_name": "Асель"})
check("запись у Мээрим 16:00", r.status_code == 200, r.text[:120])
r = cl.post("/api/booking/bookings", headers=HO, json={"start": f"{tomorrow}T15:00", "service_id": cut["id"],
                                                       "resource_id": meerim["id"], "client_name": "Перед"})
check("общий перерыв 15 мин действует и ПЕРЕД записью (15:00–16:00 вплотную — нельзя)", r.status_code == 400, r.text[:160])
r = cl.post("/api/booking/bookings", headers=HO, json={"start": f"{tomorrow}T14:45", "service_id": cut["id"],
                                                       "resource_id": meerim["id"], "client_name": "Перед"})
check("с перерывом 15 мин — можно", r.status_code == 200, r.text[:160])
cl.put("/api/booking/settings", headers=HO, json={"settings": {"buffer_min": 0}})

# только через администратора
page = bs.get_page(db, owner)
db.commit()
r = cl.get(f"/api/public/booking/{page.slug}")
pub = r.json()
titles = [s["title"] for s in pub["services"]]
check("на странице записи нет услуги «только через админа»", "Сложное окрашивание" not in titles and "Маникюр" in titles, titles)
check("публичная услуга: категория, подкатегория, цена «от–до», предоплата",
      any(s["title"] == "Окрашивание в один тон" and s["subcategory"] == "Окрашивание" and s["price_to"] == 5000
          and s["prepay"] == {"type": "percent", "value": 30.0} for s in pub["services"]), pub["services"][:1])
r = cl.get(f"/api/public/booking/{page.slug}/slots?service_id={hard['id']}&date={tomorrow}")
check("по ссылке на неё нет времени", r.json()["days"] == [], r.text[:120])
db.expire_all()
try:
    bs.create_booking(db, owner, start=f"{tomorrow}T14:00", service_id=hard["id"], client_name="Бот", source="bot")
    check("бот на неё не запишет", False)
except bs.BookingError as e:
    db.rollback()
    check("бот на неё не запишет", "администратор" in str(e), str(e))
r = cl.post("/api/booking/bookings", headers=HO, json={"start": f"{tomorrow}T14:00", "service_id": hard["id"],
                                                       "resource_id": aigul["id"], "client_name": "Каныкей"})
check("администратор записывает из кабинета", r.status_code == 200, r.text[:160])

# публичная запись с предоплатой и «как оплатить»
cl.put("/api/booking/settings", headers=HO, json={"settings": {"prepay_info": "Mbank +996 700 000 000"}})
day2 = (bs.to_local(bs.now_utc()) + timedelta(days=2)).date().isoformat()
r = cl.post(f"/api/public/booking/{page.slug}", json={"start": f"{day2}T11:00", "service_id": color["id"], "resource_id": meerim["id"],
                                                      "name": "Алтынай", "phone": "+996 700 222 333"})
pb = r.json().get("booking") or {}
check("онлайн-запись: цена мастера 4000, предоплата 1200 и как оплатить", r.status_code == 200 and pb.get("price") == 4000
      and pb.get("prepay_amount") == 1200 and pb.get("prepay_info") == "Mbank +996 700 000 000", r.text[:240])
r = cl.get(f"/api/public/booking/{page.slug}")
check("страница записи знает, как внести предоплату", r.json().get("prepay_info") == "Mbank +996 700 000 000")

# отметка «предоплата получена»
r = cl.patch(f"/api/booking/bookings/{b1['id']}", headers=HO, json={"prepaid": True})
check("отметка «предоплата получена»", r.status_code == 200 and r.json()["booking"]["prepaid"] is True, r.text[:120])
r = cl.patch(f"/api/booking/bookings/{b1['id']}", headers=HO, json={"prepaid": False})
check("отметку можно снять", r.json()["booking"]["prepaid"] is False)

# бот: диапазон цены и предоплата
from backend.services import booking_tools  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
db.expire_all()
out = json.loads(asyncio.run(booking_tools.execute("find_booking_slots", {"service": "окрашивание в один тон", "date": day2},
                                                   {"user_id": owner.id, "surface": "chat"}, db)))
check("бот видит цену диапазоном и предоплату", out.get("ok") and (out.get("service") or {}).get("price_text") == "3 000–5 000 сом"
      and (out.get("service") or {}).get("prepay_text") == "предоплата 30%", out.get("service"))
day3 = (bs.to_local(bs.now_utc()) + timedelta(days=3)).date().isoformat()
out = json.loads(asyncio.run(booking_tools.execute("create_booking", {"start": f"{day3}T10:00", "service": "окрашивание в один тон",
                                                                      "client_name": "Сезим", "client_phone": "+996 700 333 444"},
                                                   {"user_id": owner.id, "surface": "chat"}, db)))
check("бот после записи знает сумму предоплаты и как внести", out.get("ok") and (out.get("prepay") or {}).get("amount")
      and "Mbank" in (out.get("prepay") or {}).get("how", ""), out)
check("в правилах бота — про цену диапазоном и предоплату", "price_text" in booking_tools.PROMPT_BLOCK and "prepay" in booking_tools.PROMPT_BLOCK)

# прайс звонка и поиск по каталогу
from backend.services import agent_voice_context as avc  # noqa: E402
from backend.services import agent_knowledge_service as aks  # noqa: E402
block = avc.build_price_block(db, agent.id)
check("прайс звонка: диапазон и предоплата", "3 000–5 000 сом" in block and "предоплата 30%" in block, block[:400])
row = db.query(AgentKnowledgeEntry).filter(AgentKnowledgeEntry.id == uuid.UUID(color["id"])).first()
d = aks.entry_for_agent(row)
check("поиск бота: цена диапазоном, длительность, предоплата", d.get("price") == "3 000–5 000 сом" and d.get("duration_min") == 120
      and d.get("prepay") == "30%", d)
hrow = db.query(AgentKnowledgeEntry).filter(AgentKnowledgeEntry.id == uuid.UUID(hard["id"])).first()
hd = aks.entry_for_agent(hrow)
check("поиск бота: «записывает только администратор»", hd.get("booking") == "записывает только администратор", hd)

# категории: порядок и переименование
r = cl.post("/api/booking/services/categories/order", headers=HO, json={"names": ["Ногти", "Волосы"]})
j = cl.get("/api/booking/services", headers=HO).json()
check("порядок категорий", r.status_code == 200 and [c["name"] for c in j["categories"]] == ["Ногти", "Волосы"]
      and j["services"][0]["category"] == "Ногти", [c["name"] for c in j["categories"]])
r = cl.post("/api/booking/services/categories/rename", headers=HO, json={"old": "Волосы", "new": "Парикмахерский зал"})
j = cl.get("/api/booking/services", headers=HO).json()
check("переименование категории вместе с подкатегориями", r.json().get("renamed") == 3
      and {s["collection"] for s in j["services"] if s["category"] != "Ногти"}
      == {"Парикмахерский зал / Окрашивание", "Парикмахерский зал / Стрижки"}
      and [c["name"] for c in j["categories"]] == ["Ногти", "Парикмахерский зал"], [c["name"] for c in j["categories"]])
r = cl.post("/api/booking/services/categories/rename", headers=HO, json={"old": "Парикмахерский зал", "new": "", "sub_old": "Стрижки"})
j = cl.get("/api/booking/services", headers=HO).json()
check("подкатегорию можно убрать — услуга остаётся в категории", any(s["title"] == "Женская стрижка" and s["collection"] == "Парикмахерский зал"
                                                                 for s in j["services"]), [s["collection"] for s in j["services"]])

# удаление
r = cl.delete(f"/api/booking/services/{color['id']}", headers=HO)
check("услуга с будущими записями выключается", r.status_code == 200 and r.json().get("disabled") and r.json().get("upcoming") >= 1, r.text[:160])
style = cl.post("/api/booking/services", headers=HO, json={"title": "Укладка", "category": "Ногти", "price": 800, "duration_min": 45,
                                                           "masters": [{"id": meerim["id"], "price": 900}]}).json()["service"]
ra = {x["id"]: x for x in cl.get("/api/booking/resources", headers=HO).json()["resources"]}
check("укладку делает Мээрим со своей ценой", style["id"] in ra[meerim["id"]]["service_ids"]
      and ra[meerim["id"]]["service_prices"].get(style["id"]) == {"price": 900.0})
r = cl.delete(f"/api/booking/services/{style['id']}", headers=HO)
db.expire_all()
ra = {x["id"]: x for x in cl.get("/api/booking/resources", headers=HO).json()["resources"]}
check("без записей — удаляется и пропадает у мастеров (и их цены)", r.json().get("deleted") and style["id"] not in (ra[meerim["id"]]["service_ids"] or [])
      and style["id"] not in (ra[meerim["id"]]["service_prices"] or {}), ra[meerim["id"]]["service_ids"])
r = cl.patch(f"/api/booking/services/{uuid.uuid4()}", headers=HO, json={"title": "X"})
check("чужая/несуществующая услуга — 400", r.status_code == 400, r.status_code)

# сотрудники
m = TeamMember(owner_id=owner.id, email="admin@salon.kg", name="Админ", role_title="Администратор",
               password_hash=hash_password("x12345"), permissions=["booking"], is_active=True, token_version=1)
m2 = TeamMember(owner_id=owner.id, email="setup@salon.kg", name="Управляющий", role_title="Управляющий",
                password_hash=hash_password("x12345"), permissions=["booking", "booking_setup"], is_active=True, token_version=1)
db.add_all([m, m2])
db.commit()
H1 = {"Authorization": f"Bearer {create_jwt_token(owner.id, extra={'staff': str(m.id), 'stv': 1})}"}
H2 = {"Authorization": f"Bearer {create_jwt_token(owner.id, extra={'staff': str(m2.id), 'stv': 1})}"}
check("администратор журнала видит прайс", cl.get("/api/booking/services", headers=H1).status_code == 200)
check("…но не меняет", cl.patch(f"/api/booking/services/{mani['id']}", headers=H1, json={"price": 1}).status_code == 403)
r = cl.patch(f"/api/booking/services/{mani['id']}", headers=H2, json={"price": 1300})
check("управляющий меняет цену", r.status_code == 200 and r.json()["service"]["price"] == 1300, r.text[:160])

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
