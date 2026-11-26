"""Клиенты салона: карточка, сегменты, выгрузка (Voksy Beauty, шаг 2.1).

Часть 1 (без базы): разбор ДР (с годом и без, 29 февраля, неверные даты), ближайшие ДР, возраст, защита ячеек CSV
от формул, права сотрудников (список — раздел «Клиенты», карточка — ещё «Чаты» и «Записи», выгрузка — только
владелец), страница CRM подключает салонный модуль, значки есть в спрайте, сегменты описаны одинаково.

Часть 2 (PostgreSQL, SALON_CLIENTS_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- цифры из журнала: визит из двух услуг = один визит, сумма, средний чек, неявки, отмены, последний и первый
  визит, ближайшая запись, любимые мастер и услуга;
- сегменты: постоянные, новые, VIP, давно не были, ушли, с неявками, ДР скоро, записаны, не записывались —
  счётчики и список совпадают;
- фильтры (имя, цифры телефона, мастер, услуга, источник, пол, тег), сортировки, страницы без повторов;
- выгрузка CSV: BOM, «;», столбцы, формулы экранированы, сотруднику — 403;
- карточка: сегменты, история, будущие записи, обычный интервал, оценки; чужой клиент — 404;
- ДР / пол / источник в PUT: верные, неверные (400), очистка; бот пишет ДР (update_contact_info), история клиента
  для бота отдаёт ДР и источник;
- источник ставится при создании карточки (запись по ссылке, вручную, импорт), досчёт старых — по первому
  касанию (WhatsApp, запись по ссылке, звонок; без касаний — «вручную»; свежие не трогаются);
- права: администратор с разделом «Клиенты» видит список, сотрудник «Записи» — только карточку, мастер — ничего.
"""
import asyncio
import csv
import io
import os
import re
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("SALON_CLIENTS_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="clients-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.models.agent_contact import BIRTHDAY_NO_YEAR  # noqa: E402
from backend.services import salon_clients as sc  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def raises(fn, *a):
    try:
        fn(*a)
        return False
    except ValueError:
        return True


# ───────────────────────────── часть 1 ─────────────────────────────
check("ДР: 12.03.1990", sc.parse_birthday("12.03.1990") == date(1990, 3, 12))
check("ДР без года → год-метка", sc.parse_birthday("12.03") == date(BIRTHDAY_NO_YEAR, 3, 12))
check("ДР: ISO и --ММ-ДД", sc.parse_birthday("1990-3-5") == date(1990, 3, 5) and sc.parse_birthday("--02-29") == date(1904, 2, 29))
check("ДР: двузначный год", sc.parse_birthday("5/7/95") == date(1995, 7, 5))
check("ДР: пусто → None", sc.parse_birthday("") is None and sc.parse_birthday(None) is None)
check("ДР: неверные даты — понятная ошибка", raises(sc.parse_birthday, "31.02") and raises(sc.parse_birthday, "12.03.1850")
      and raises(sc.parse_birthday, "завтра") and raises(sc.parse_birthday, "12.13"))
check("ДР 29.02 в невисокосный год рядом с 28.02", "02-29" in sc.birthday_days(date(2027, 2, 20)))
info = sc.birthday_info(date(1990, 11, 27), date(2026, 11, 26))
check("ДР: через день, сейчас 35, исполнится 36", info["in_days"] == 1 and info["age"] == 35 and info["turns"] == 36, info)
info = sc.birthday_info(date(1904, 2, 29), date(2027, 2, 20))
check("ДР без года: без возраста, 29.02 → 1 марта", info["in_days"] == 9 and "age" not in info and info["text"] == "29 февраля", info)
check("ДР словами", sc.birthday_text(date(1990, 3, 12)) == "12 марта 1990" and sc.birthday_text(date(1904, 3, 12)) == "12 марта")
check("CSV: формула экранирована, телефон нет", sc._cell("=SUM(A1)") == "'=SUM(A1)" and sc._cell("+996 555 123") == "+996 555 123"
      and sc._cell("@x") == "'@x")
check("сегменты: условия есть для каждого", set(sc.SEGMENT_SQL) == sc.SEGMENT_KEYS)
check("источники: системные и ручные", {"whatsapp", "booking", "recommendation", "walk_in", "2gis"} <= set(sc.SOURCE_TITLES))
check("права: список — раздел «Клиенты»", ts.staff_allowed(["crm"], "GET", "/api/beauty/clients")
      and not ts.staff_allowed(["booking", "inbox"], "GET", "/api/beauty/clients"))
cid = str(uuid.uuid4())
check("права: карточка — «Клиенты», «Чаты», «Записи»; мастеру нет",
      all(ts.staff_allowed([p], "GET", f"/api/beauty/clients/{cid}") for p in ("crm", "inbox", "booking"))
      and not ts.staff_allowed(["booking_own"], "GET", f"/api/beauty/clients/{cid}"))
check("права: выгрузка — только владелец", not ts.staff_allowed(ts.PERM_KEYS, "GET", "/api/beauty/clients/export.csv"))
html = open(os.path.join(ROOT, "backend/static/start-crm.html"), encoding="utf-8").read()
check("CRM подключает салонный модуль раньше crm.js", html.find("/static/start/crm-salon.js") != -1
      and html.find("/static/start/crm-salon.js") < html.find("/static/start/crm.js"))
sprite = open(os.path.join(ROOT, "backend/static/icons/ui.svg"), encoding="utf-8").read()
have = set(re.findall(r'id="i-([a-z0-9-]+)"', sprite))
src = open(os.path.join(ROOT, "backend/static/start/crm-salon.js"), encoding="utf-8").read()
used = set(re.findall(r"icon\('([a-z0-9-]+)'", src))
check("значки модуля есть в спрайте", not (used - have), sorted(used - have))
bk = open(os.path.join(ROOT, "backend/static/booking/booking.js"), encoding="utf-8").read()
check("журнал записи понимает ?new=1&contact= (клиент уже выбран)", "preClient" in bk and "pre.client" in bk)

if not PG:
    print("SKIP часть 2: SALON_CLIENTS_TEST_DATABASE_URL не задан")
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
from backend.models.agent_whatsapp import AgentWhatsAppMessage  # noqa: E402
from backend.models.booking import Booking, BookingResource  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services import beauty_services, salon_ai_tools  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
app_module.ensure_booking_tables()
app_module.ensure_user_profile_columns()
cl = TestClient(app_module.app, raise_server_exceptions=False)
B = bs.BISHKEK
db = SessionLocal()
NOW = bs.now_utc()
TODAY = bs.to_local(NOW).date()

pro = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p", is_active=True,
                       product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
db.add(pro)
db.flush()
u = User(email="cl@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
         subscription_plan_id=pro.id, subscription_end_date=NOW + timedelta(days=20), business_niche="beauty_salon",
         business_profile={"answers": {}})
other = User(email="other@kg.kg", password_hash="x", email_verified=True, first_name="Чужой",
             subscription_plan_id=pro.id, subscription_end_date=NOW + timedelta(days=20), business_niche="beauty_salon")
db.add_all([u, other])
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор", is_active=True)
agent2 = AgentConfig(user_id=other.id, name="Чужой бот", is_active=True)
db.add_all([agent, agent2])
db.commit()
HO = {"Authorization": "Bearer " + create_jwt_token(u.id)}
user = db.query(User).get(u.id)

svc_m = beauty_services.save(db, user, None, {"title": "Маникюр", "price": 1200, "duration_min": 60, "category": "Ногти"})
svc_h = beauty_services.save(db, user, None, {"title": "Стрижка", "price": 900, "duration_min": 45, "category": "Волосы"})
db.commit()
full = {dd: [["00:00", "23:59"]] for dd in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
m1 = BookingResource(user_id=u.id, name="Айгуль", schedule=full, is_active=True)
m2 = BookingResource(user_id=u.id, name="Бермет", schedule=full, is_active=True)
db.add_all([m1, m2])
db.commit()


def contact(name, phone, **kw):
    c = AgentContact(user_id=u.id, agent_config_id=agent.id, name=name, phone=phone, status="new", memory={}, **kw)
    db.add(c)
    db.flush()
    return c


slot = [0]


def book(c, days_ago, status, master=None, service=None, price=None, visit_id=None, hour=None):
    """Запись в прошлом (days_ago > 0) или будущем (< 0); время разнесено, чтобы не пересекаться у мастера."""
    slot[0] += 1
    st = datetime.combine(TODAY - timedelta(days=days_ago), time(8, 0), B).astimezone(timezone.utc) + timedelta(minutes=13 * slot[0])
    if hour is not None:
        st = datetime.combine(TODAY - timedelta(days=days_ago), time(hour, 0), B).astimezone(timezone.utc)
    s = service or svc_m
    b = Booking(user_id=u.id, resource_id=(master or m1).id, service_entry_id=s.id, service_title=s.title, contact_id=c.id,
                client_name=c.name, start_at=st, end_at=st + timedelta(minutes=10), status=status,
                price=price if price is not None else s.price, source="owner", visit_id=visit_id)
    db.add(b)
    db.flush()
    return b


# Айжан — постоянная: 4 визита у Айгуль (маникюр), последний 10 дней назад; один визит из двух услуг
aizhan = contact("Айжан Токтогулова", "+996555100100", gender="f", tags="vip,маникюр")
book(aizhan, 100, "completed", price=1500)
book(aizhan, 70, "arrived", price=1500)
vid = uuid.uuid4()
book(aizhan, 40, "completed", price=1500, visit_id=vid)
book(aizhan, 40, "completed", master=m2, service=svc_h, price=900, visit_id=vid)
book(aizhan, 10, "completed", price=1500)
book(aizhan, 25, "cancelled")
# Нурай — новая: один визит 5 дней назад + будущая запись
nurai = contact("Нурай", "+996700200200", gender="f", source="instagram")
book(nurai, 5, "arrived", master=m2, service=svc_h)
book(nurai, -3, "booked", master=m2, service=svc_h, hour=15)
# Бакыт — давно не был: 60 дней, есть неявка
bakyt = contact("Бакыт", "+996777300300", gender="m", source="walk_in")
book(bakyt, 60, "completed", master=m2, service=svc_h, price=900)
book(bakyt, 50, "no_show", master=m2, service=svc_h)
# Салтанат — ушла: 200 дней назад, дорогие визиты (VIP по сумме)
d5 = TODAY + timedelta(days=5)
saltanat = contact("Салтанат", "+996555400400", gender="f",
                   birthday=date(1995, d5.month, d5.day if not (d5.month == 2 and d5.day == 29) else 28))
book(saltanat, 260, "completed", price=9000)
book(saltanat, 200, "completed", price=9000)
# Эрмек — писал, но не записывался; ДР без года через 2 дня
d2 = TODAY + timedelta(days=2)
ermek = contact("Эрмек", "+996550500500", birthday=date(BIRTHDAY_NO_YEAR, d2.month, d2.day) if not (d2.month == 2 and d2.day == 29) else date(1904, 2, 29))
# Гость сайта без телефона
guest = contact("Гость сайта", "web:abc123")
# Чужой клиент (другой салон)
alien = AgentContact(user_id=other.id, agent_config_id=agent2.id, name="Чужая", phone="+996555999999", status="new", memory={})
db.add(alien)
db.commit()

# — салон проверяет Saltanat's birthday falls within 14 days —
db.expire_all()
sal_bd = db.query(AgentContact).get(saltanat.id).birthday
check("подготовка: ДР Салтанат в ближайшие 14 дней", sc.birthday_info(sal_bd, TODAY)["in_days"] <= 14, sc.birthday_info(sal_bd, TODAY))

# — список: цифры из журнала —
r = cl.get("/api/beauty/clients?limit=50", headers=HO)
check("список: 200 и все клиенты салона (чужих нет)", r.status_code == 200 and r.json()["total"] == 6, r.text[:300])
data = r.json()
by = {x["name"]: x for x in data["items"]}
a = by["Айжан Токтогулова"]["stats"]
check("Айжан: визит из двух услуг = один визит (4 визита)", a["visits"] == 4, a)
check("Айжан: сумма и средний чек", a["spent"] == 1500 * 4 + 900 and a["avg_check"] == round((1500 * 4 + 900) / 4, 2), a)
check("Айжан: отмена посчитана, неявок нет", a["cancelled"] == 1 and a["no_shows"] == 0, a)
check("Айжан: любимый мастер и услуга", a["favorite_master"] == "Айгуль" and a["favorite_service"] == "Маникюр", a)
check("Айжан: последний визит 10 дней назад", a["days_since_last"] == 10, a)
n = by["Нурай"]["stats"]
check("Нурай: ближайшая запись есть", n["next_at"] is not None and n["visits"] == 1, n)
check("гость сайта: телефон спрятан", by["Гость сайта"]["phone"] is None)
check("каналы и источник в строке", "channels" in by["Нурай"] and by["Нурай"]["source_title"] == "Instagram")
check("выгрузка доступна владельцу", data["can_export"] is True)

# — сегменты —
segs = {s["key"]: s["count"] for s in data["segments"]}


def seg_names(key):
    rr = cl.get(f"/api/beauty/clients?segment={key}&limit=50&counts=0", headers=HO).json()
    return {x["name"] for x in rr["items"]}, rr["total"]


expect = {
    "regular": {"Айжан Токтогулова"}, "new": {"Нурай"}, "sleeping": {"Бакыт"}, "lost": {"Салтанат"},
    "no_show": {"Бакыт"}, "upcoming": {"Нурай"}, "never": {"Эрмек", "Гость сайта"},
    "birthday": {"Салтанат", "Эрмек"}, "vip": {"Салтанат"},
}
for key, want in expect.items():
    got, total = seg_names(key)
    check(f"сегмент {key}: {sorted(want)}", got == want and total == len(want) and segs[key] == len(want), (sorted(got), segs.get(key)))
check("сегмент «все» = 6", segs["all"] == 6)
rb = cl.get("/api/beauty/clients?segment=birthday&counts=0", headers=HO).json()
check("ДР скоро: сортировка — ближайший первым", [x["name"] for x in rb["items"]][0] == "Эрмек" and rb["sort"] == "birthday",
      [x["name"] for x in rb["items"]])

# — фильтры —


def names(qs):
    return {x["name"] for x in cl.get("/api/beauty/clients?counts=0&limit=50&" + qs, headers=HO).json()["items"]}


check("поиск по имени", names("q=айж") == {"Айжан Токтогулова"})
check("поиск по цифрам телефона", names("q=0700 200 200") == {"Нурай"}, names("q=0700 200 200"))
check("фильтр: у мастера Бермет были", names(f"master={m2.id}") == {"Айжан Токтогулова", "Нурай", "Бакыт"})
check("фильтр: услуга «стриж»", names("service=стриж") == {"Айжан Токтогулова", "Нурай", "Бакыт"})
check("фильтр: источник", names("source=walk_in") == {"Бакыт"} and "Айжан Токтогулова" in names("source=none"))
check("фильтр: пол", names("gender=m") == {"Бакыт"} and names("gender=none") == {"Эрмек", "Гость сайта"})
check("фильтр: тег", names("tag=vip") == {"Айжан Токтогулова"})
srt = [x["name"] for x in cl.get("/api/beauty/clients?counts=0&sort=spent", headers=HO).json()["items"]]
check("сортировка по сумме: Салтанат, потом Айжан", srt[:2] == ["Салтанат", "Айжан Токтогулова"], srt)
srt = [x["name"] for x in cl.get("/api/beauty/clients?counts=0&sort=last_visit", headers=HO).json()["items"]]
check("сортировка «недавно были»: Нурай первая, без визитов — в конце", srt[0] == "Нурай" and set(srt[-2:]) == {"Эрмек", "Гость сайта"}, srt)
p1 = cl.get("/api/beauty/clients?counts=0&limit=4&offset=0&sort=name", headers=HO).json()
p2 = cl.get("/api/beauty/clients?counts=0&limit=4&offset=4&sort=name", headers=HO).json()
ids = [x["id"] for x in p1["items"] + p2["items"]]
check("страницы без повторов и пропусков", len(ids) == 6 == len(set(ids)) and p2["total"] == 6, (len(ids), p2["total"]))

# — выгрузка —
r = cl.get("/api/beauty/clients/export.csv?segment=all", headers=HO)
body = r.content.decode("utf-8")
check("выгрузка: 200, CSV, BOM", r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and body.startswith("﻿"))
rows = list(csv.reader(io.StringIO(body.lstrip("﻿")), delimiter=";"))
check("выгрузка: заголовок и 6 клиентов", rows[0] == sc.EXPORT_COLUMNS and len(rows) == 7, (rows[0][:3], len(rows)))
row = [x for x in rows if x[0] == "Айжан Токтогулова"][0]
check("выгрузка: цифры Айжан", row[5] == "4" and row[6] == "6900" and row[14] == "Айгуль" and row[3] == "Женщина", row)
check("выгрузка по сегменту", len(list(csv.reader(io.StringIO(cl.get("/api/beauty/clients/export.csv?segment=lost", headers=HO)
                                                         .content.decode("utf-8").lstrip("﻿")), delimiter=";"))) == 2)
db.query(AgentContact).filter(AgentContact.id == ermek.id).update({"notes": "=HYPERLINK(\"x\")"})
db.commit()
body = cl.get("/api/beauty/clients/export.csv", headers=HO).content.decode("utf-8")
check("выгрузка: формула в заметке экранирована", "'=HYPERLINK" in body)

# — карточка —
r = cl.get(f"/api/beauty/clients/{aizhan.id}", headers=HO)
card = r.json()
check("карточка: 200, сегмент «Постоянные»", r.status_code == 200 and "regular" in [s["key"] for s in card["segments"]], card.get("segments"))
check("карточка: история и будущие", len(card["history"]) == 6 and card["upcoming"] == [], len(card["history"]))
check("карточка: обычный интервал", card["stats"].get("usual_interval_days") == 30, card["stats"].get("usual_interval_days"))
card = cl.get(f"/api/beauty/clients/{saltanat.id}", headers=HO).json()
check("карточка: VIP и «Ушли» у Салтанат", {"vip", "lost", "birthday"} <= {s["key"] for s in card["segments"]}, card["segments"])
check("карточка: ДР и возраст", card["birthday"] and card["birthday"]["age"] >= 29, card["birthday"])
card = cl.get(f"/api/beauty/clients/{nurai.id}", headers=HO).json()
check("карточка: будущая запись Нурай", len(card["upcoming"]) == 1 and card["upcoming"][0]["status"] == "booked")
check("карточка: чужой клиент — 404", cl.get(f"/api/beauty/clients/{alien.id}", headers=HO).status_code == 404)
check("карточка: неверный id — 404", cl.get("/api/beauty/clients/" + str(uuid.uuid4()), headers=HO).status_code == 404)

# — ДР / пол / источник —
r = cl.put(f"/api/agent/contacts/{ermek.id}", headers=HO, json={"birthday": "07.05.1993", "gender": "m", "source": "recommendation"})
check("PUT: ДР, пол, источник сохранены", r.status_code == 200 and r.json()["birthday"] == "1993-05-07"
      and r.json()["gender"] == "m" and r.json()["source"] == "recommendation", r.text[:200])
r = cl.put(f"/api/agent/contacts/{ermek.id}", headers=HO, json={"birthday": "07.05"})
check("PUT: ДР без года → --05-07", r.json()["birthday"] == "--05-07", r.json().get("birthday"))
r = cl.put(f"/api/agent/contacts/{ermek.id}", headers=HO, json={"birthday": "31.02"})
check("PUT: неверная дата — 400 с объяснением", r.status_code == 400 and "даты" in r.text, r.text[:200])
check("PUT: неверный пол и источник — 400", cl.put(f"/api/agent/contacts/{ermek.id}", headers=HO, json={"gender": "x"}).status_code == 400
      and cl.put(f"/api/agent/contacts/{ermek.id}", headers=HO, json={"source": "tv"}).status_code == 400)
r = cl.put(f"/api/agent/contacts/{ermek.id}", headers=HO, json={"birthday": "", "gender": "", "source": ""})
check("PUT: очистка", r.json()["birthday"] is None and r.json()["gender"] is None and r.json()["source"] is None, r.json())
r = cl.put(f"/api/agent/contacts/{ermek.id}", headers=HO, json={"name": "Эрмек Асанов"})
check("PUT: старые поля работают как раньше", r.status_code == 200 and r.json()["name"] == "Эрмек Асанов")

# — бот —
from backend.services import agent_tools  # noqa: E402
res = asyncio.run(agent_tools.fn_update_contact_info({"agent_contact_id": str(guest.id), "birthday": "14.02"}, str(u.id), str(agent.id), db))
db.expire_all()
check("бот: ДР записан из переписки", res.get("ok") and db.query(AgentContact).get(guest.id).birthday == date(1904, 2, 14), res)
res = asyncio.run(agent_tools.fn_update_contact_info({"agent_contact_id": str(guest.id), "birthday": "45.13"}, str(u.id), str(agent.id), db))
check("бот: неверная дата — ошибка, не падение", res.get("ok") is False and "birthday" in res.get("error", ""), res)
check("бот: в описании инструмента есть день рождения", "birthday" in agent_tools.UPDATE_CONTACT_INFO_TOOL["parameters"]["properties"])
hist = salon_ai_tools.get_customer_history(db, user, str(bakyt.id))
check("бот: история клиента — источник", hist.get("source") == "Зашёл с улицы" and "birthday" in hist, hist.get("source"))
hist = salon_ai_tools.get_customer_history(db, user, str(guest.id))
check("бот: новый клиент — тоже ДР", hist.get("visits") == 0 and hist.get("birthday") == "14 февраля", hist)
check("бот: подсказка про ДР в салонном блоке промпта", "update_contact_info, birthday" in salon_ai_tools.PROMPT_BLOCK)

# — источник при создании и досчёт старых —
page = bs.get_page(db, user, create=True)
db.commit()
c_new = bs.resolve_contact(db, u.id, None, "Клиент по ссылке", "+996555600600", source="online")
c_own = bs.resolve_contact(db, u.id, None, "Записала админ", "+996555600601", source="owner")
db.commit()
check("источник: запись по ссылке → «Запись по ссылке», админ → «вручную»", c_new.source == "booking" and c_own.source == "manual")
r = cl.post("/api/agent/contacts", headers=HO, json={"phone": "+996555600602", "name": "Вручную"})
check("источник: «Добавить клиента» → manual", r.status_code == 200 and r.json()["source"] == "manual", r.text[:200])
old = datetime.utcnow() - timedelta(days=3)
w1 = contact("Старый WhatsApp", "+996555700700", created_at=old)
w2 = contact("Старый по ссылке", "+996555700701", created_at=old)
w3 = contact("Старый без касаний", "+996555700702", created_at=old)
w4 = contact("Свежий", "+996555700703")
db.add(AgentWhatsAppMessage(agent_contact_id=w1.id, agent_config_id=agent.id, direction="inbound", body="Салам",
                            created_at=old + timedelta(minutes=5)))
bw = book(w2, 1, "arrived")
bw.source = "online"
book(w1, 2, "arrived")       # запись ПОЗЖЕ сообщения — первое касание всё равно WhatsApp
db.commit()
n_bf = sc.backfill_sources(db, force=True)
db.expire_all()
got = {c.name: c.source for c in db.query(AgentContact).filter(AgentContact.id.in_([w1.id, w2.id, w3.id, w4.id])).all()}
check("досчёт: WhatsApp, по ссылке, без касаний — вручную", got["Старый WhatsApp"] == "whatsapp" and got["Старый по ссылке"] == "booking"
      and got["Старый без касаний"] == "manual", got)
check("досчёт: свежая карточка (моложе часа) не тронута", got["Свежий"] is None, got)
check("досчёт: повтор не чаще раза в 15 минут", sc.backfill_sources(db) == 0 and n_bf >= 3)

# — права сотрудников —
member, _ = ts.create_member(db, user, name="Айгерим", email="adm@kg.kg", password="secret12", role_title="Администратор",
                             permissions=["booking", "crm"])
bmember, _ = ts.create_member(db, user, name="Кассир", email="kassa@kg.kg", password="secret12", role_title="Запись",
                              permissions=["booking"])
mmember, _ = ts.create_member(db, user, name="Мастер", email="m@kg.kg", password="secret12", role_title="Мастер",
                              permissions=["booking_own"])
db.commit()


def staff_h(m):
    return {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(m.id), "stv": m.token_version})}


r = cl.get("/api/beauty/clients", headers=staff_h(member))
check("администратор с «Клиентами»: список есть, выгрузки нет", r.status_code == 200 and r.json()["can_export"] is False, r.status_code)
check("администратор: выгрузка — 403", cl.get("/api/beauty/clients/export.csv", headers=staff_h(member)).status_code == 403)
check("сотрудник «Записи»: список — 403, карточка — 200",
      cl.get("/api/beauty/clients", headers=staff_h(bmember)).status_code == 403
      and cl.get(f"/api/beauty/clients/{aizhan.id}", headers=staff_h(bmember)).status_code == 200)
check("мастер: ни списка, ни карточки", cl.get("/api/beauty/clients", headers=staff_h(mmember)).status_code == 403
      and cl.get(f"/api/beauty/clients/{aizhan.id}", headers=staff_h(mmember)).status_code == 403)

# — скорость: 2000 клиентов и 6000 записей —
import time as _t  # noqa: E402
rows_c = []
for i in range(2000):
    rows_c.append({"id": str(uuid.uuid4()), "uid": str(u.id), "aid": str(agent.id), "n": f"Клиент {i}", "p": f"+99655{i:07d}"})
db.execute(text("INSERT INTO agent_contacts (id, user_id, agent_config_id, name, phone, status, memory, attempts_count, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), CAST(:aid AS uuid), :n, :p, 'new', '{}', 0, now(), now())"), rows_c)
bk_rows = []
for i, c in enumerate(rows_c):
    for k in range(3):
        st = NOW - timedelta(days=5, minutes=(i * 3 + k) * 97)     # у мастера записи не пересекаются
        bk_rows.append({"id": str(uuid.uuid4()), "uid": str(u.id), "r": str((m1 if k % 2 else m2).id), "c": c["id"], "s": st,
                        "e": st + timedelta(minutes=1), "st": "completed" if (i + k) % 7 else "no_show", "pr": 800 + (i % 9) * 100})
db.execute(text("INSERT INTO bookings (id, user_id, resource_id, contact_id, service_title, start_at, end_at, status, price, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), CAST(:r AS uuid), CAST(:c AS uuid), 'Маникюр', :s, :e, :st, :pr, now())"),
           bk_rows)
db.commit()
t0 = _t.perf_counter()
r = cl.get("/api/beauty/clients?segment=sleeping&sort=spent", headers=HO)
ms = (_t.perf_counter() - t0) * 1000
check(f"скорость: список с сегментами на 2000 клиентов — {ms:.0f} мс", r.status_code == 200 and ms < 1500, r.status_code)
t0 = _t.perf_counter()
r = cl.get("/api/beauty/clients/export.csv", headers=HO)
ms = (_t.perf_counter() - t0) * 1000
check(f"скорость: выгрузка 2000+ строк — {ms:.0f} мс", r.status_code == 200 and r.content.decode("utf-8").count("\n") > 2000 and ms < 4000)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
