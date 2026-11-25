"""
Журнал записи v2 (Voksy Beauty, шаг 1.4).

Часть 1 (без базы): права сотрудников на новые эндпоинты, статусы.
Часть 2 (PostgreSQL, JOURNAL_TEST_DATABASE_URL): миграция (таблицы блоков и кабинетов, колонки записи,
ограничение «нет пересечений» в базе), гонка — две записи на одно время (вторая — «занято», в т.ч. мимо
блокировки строки), блоки времени (мастер и все мастера, «перерыв/отпуск» в причине отказа, предупреждение
о записях в блоке), кабинеты (два мастера — один кабинет), визит из нескольких услуг (по порядку, у того же
мастера — после уборки и перерыва, всё или ничего, отмена визита), растяжка длительности, статус
«завершён» в выручке, финансах, аналитике и заработке мастера, pg_notify и поток обновлений.
"""

import asyncio
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("JOURNAL_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="journal-test-secret-0123456789abcdef",
                  KB_VECTOR_ENABLED="0")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import booking_service as bs  # noqa: E402
from backend.services import team_service as ts  # noqa: E402
from backend.models.booking import ACTIVE_STATUSES, BOOKING_STATUSES, VISITED_STATUSES  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


A = ts.staff_allowed
check("статус «завершён» занимает время и считается визитом", "completed" in BOOKING_STATUSES
      and "completed" in ACTIVE_STATUSES and set(VISITED_STATUSES) == {"arrived", "completed"} and bs.visited("completed")
      and not bs.visited("booked"))
check("права: поток — журналу и мастеру", A(["booking"], "GET", "/api/booking/stream") and A(["booking_own"], "GET", "/api/booking/stream")
      and not A(["inbox"], "GET", "/api/booking/stream"))
check("права: блоки и визиты — у журнала", A(["booking"], "POST", "/api/booking/blocks") and A(["booking"], "DELETE", "/api/booking/blocks/1")
      and A(["booking"], "POST", "/api/booking/visits") and A(["booking"], "POST", "/api/booking/visit-slots")
      and A(["booking"], "DELETE", "/api/booking/visits/1") and not A(["booking_own"], "POST", "/api/booking/blocks"))
check("права: кабинеты меняет «Мастера и график»", A(["booking_setup"], "POST", "/api/booking/rooms")
      and A(["booking_setup"], "PATCH", "/api/booking/rooms/1") and not A(["booking"], "POST", "/api/booking/rooms")
      and A(["booking"], "GET", "/api/booking/rooms"))

# Интерфейс (без браузера): значки страниц записи есть в спрайте, обе страницы подписаны на поток,
# журнал понимает все виды блоков и статус «завершён»
import re  # noqa: E402
STATIC = os.path.join(ROOT, "backend", "static")
sprite = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', open(os.path.join(STATIC, "icons", "ui.svg"), encoding="utf-8").read()))
used = set()
for rel in ("booking/booking.js", "booking/masters.js", "booking/services.js", "master/master.js", "start/common.js"):
    used |= set(re.findall(r"icon\('([a-z0-9-]+)'", open(os.path.join(STATIC, rel), encoding="utf-8").read()))
check("значки журнала, услуг и кабинета мастера есть в спрайте", used <= sprite, ", ".join(sorted(used - sprite)))
bj = open(os.path.join(STATIC, "booking", "booking.js"), encoding="utf-8").read()
mj = open(os.path.join(STATIC, "master", "master.js"), encoding="utf-8").read()
cj = open(os.path.join(STATIC, "start", "common.js"), encoding="utf-8").read()
check("журнал и кабинет мастера слушают поток обновлений", "S.live = function" in cj
      and "S.live('/api/booking/stream'" in bj and "S.live('/api/booking/stream'" in mj)
check("журнал знает все виды блоков и «завершён»", all(f"['{k}'," in bj for k in bs.BLOCK_KINDS)
      and "completed: 'Завершён'" in bj and "completed: 'Завершён'" in mj)

if not PG:
    print("SKIP: часть 2 — JOURNAL_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.booking import Booking  # noqa: E402
from backend.core.security import create_jwt_token, hash_password  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
with engine.begin() as conn:     # старая база: колонок журнала v2 нет
    conn.execute(text("ALTER TABLE bookings DROP COLUMN room_id"))
    conn.execute(text("ALTER TABLE bookings DROP COLUMN visit_id"))
    conn.execute(text("ALTER TABLE agent_knowledge_entries DROP COLUMN room_ids"))
    conn.execute(text("DROP TABLE booking_blocks"))
    conn.execute(text("DROP TABLE booking_rooms"))

import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app_module.ensure_user_profile_columns()
app_module.ensure_booking_tables()
app_module.ensure_booking_tables()
with engine.connect() as conn:
    tables = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    bcols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='bookings'"))}
    cons = {r[0] for r in conn.execute(text("SELECT conname FROM pg_constraint WHERE conrelid = 'bookings'::regclass"))}
check("миграция: блоки, кабинеты, колонки записи", {"booking_blocks", "booking_rooms"} <= tables and {"room_id", "visit_id"} <= bcols)
check("защита в базе: ограничения «нет пересечений»", {"bookings_no_overlap_master", "bookings_no_overlap_room"} <= cons, sorted(cons))
app_module.ensure_team_tables()
app_module.ensure_finance_tables()
app_module.ensure_branding_columns()

cl = TestClient(app_module.app, raise_server_exceptions=False)
now = datetime.now(timezone.utc)
db = SessionLocal()
plan = SubscriptionPlan(code="start", name="Start", price=100, max_assistants=3, description="s", is_active=True)
db.add(plan)
db.flush()
owner = User(email="owner@salon.kg", password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
             wallet_balance=100000, first_name="Айгерим", company_name="Салон", business_niche="beauty_salon",
             business_profile={"modules": {"booking": True}}, subscription_plan_id=plan.id,
             subscription_end_date=now + timedelta(days=20))
db.add(owner)
db.flush()
db.add(AgentConfig(user_id=owner.id, name="Бот"))
db.commit()
HO = {"Authorization": f"Bearer {create_jwt_token(owner.id)}"}


def err(r):
    try:
        d = r.json()
    except Exception:
        return ""
    x = d.get("detail") or d.get("message")
    return x if isinstance(x, str) else str(x)


ov = cl.get("/api/booking/overview", headers=HO).json()
aigul = ov["resources"][0]["id"]
check("обзор журнала: блоки и кабинеты", ov.get("blocks") == [] and ov.get("rooms") == [])
cl.patch(f"/api/booking/resources/{aigul}", headers=HO, json={"name": "Айгуль", "schedule": {d: [["09:00", "19:00"]] for d in bs.DAYS}})
meerim = cl.post("/api/booking/resources", headers=HO, json={"name": "Мээрим"}).json()["resource"]["id"]
cl.patch(f"/api/booking/resources/{meerim}", headers=HO, json={"schedule": {d: [["09:00", "19:00"]] for d in bs.DAYS}})
mani = cl.post("/api/booking/services", headers=HO, json={"title": "Маникюр", "category": "Ногти", "price": 1000, "duration_min": 60,
                                                         "after_min": 15, "masters": [{"id": aigul}, {"id": meerim}]}).json()["service"]
pedi = cl.post("/api/booking/services", headers=HO, json={"title": "Педикюр", "category": "Ногти", "price": 1500, "duration_min": 90,
                                                         "masters": [{"id": aigul}, {"id": meerim}]}).json()["service"]
D = (bs.to_local(bs.now_utc()) + timedelta(days=1)).date().isoformat()
D2 = (bs.to_local(bs.now_utc()) + timedelta(days=2)).date().isoformat()


def book(start, svc=None, res=None, name="Клиент", hdr=HO):
    return cl.post("/api/booking/bookings", headers=hdr, json={"start": start, "service_id": svc, "resource_id": res, "client_name": name})


# ── гонка: две записи на одно время
results = []


def racer(name):
    s = SessionLocal()
    try:
        u = s.query(User).filter(User.id == owner.id).first()
        bs.create_booking(s, u, start=f"{D}T10:00", service_id=mani["id"], resource_id=aigul, client_name=name)
        s.commit()
        results.append("ok")
    except bs.BookingError as e:
        s.rollback()
        results.append("busy:" + str(e))
    finally:
        s.close()


ths = [threading.Thread(target=racer, args=(f"Гонка {i}",)) for i in range(4)]
[t.start() for t in ths]
[t.join() for t in ths]
check("гонка: из четырёх одновременных записей прошла одна", results.count("ok") == 1
      and all(x.startswith("busy:") for x in results if x != "ok"), results)
# мимо блокировки строки (как если бы проверка пропустила): ловит ограничение базы
orig = bs._check_free
bs._check_free = lambda *a, **k: None
try:
    u = db.query(User).filter(User.id == owner.id).first()
    bs.create_booking(db, u, start=f"{D}T10:30", service_id=mani["id"], resource_id=aigul, client_name="Мимо проверки")
    db.commit()
    check("ограничение базы ловит пересечение мимо проверки", False)
except bs.BookingError as e:
    db.rollback()
    check("ограничение базы ловит пересечение мимо проверки", "занято" in str(e), str(e))
finally:
    bs._check_free = orig

# ── блоки времени
r = cl.post("/api/booking/blocks", headers=HO, json={"resource_id": aigul, "kind": "break", "start": f"{D}T13:00", "end": f"{D}T14:00", "note": "обед"})
blk = r.json().get("block") or {}
check("блок «перерыв» у мастера", r.status_code == 200 and blk.get("kind_title") == "Перерыв" and r.json().get("clash") == 0, r.text[:160])
slots = [x["time"] for x in cl.get(f"/api/booking/slots?service_id={pedi['id']}&resource_id={aigul}&date={D}", headers=HO).json()["days"][0]["slots"]]
check("в перерыв окон нет (и педикюр 90 мин не заходит в него)", "13:00" not in slots and "12:00" not in slots and "14:00" in slots
      and "11:30" in slots, slots)
r = book(f"{D}T13:00", mani["id"], aigul)
check("в перерыв записать нельзя — понятная причина", r.status_code == 400 and "перерыв" in err(r), err(r))
r = cl.post("/api/booking/blocks", headers=HO, json={"kind": "training", "start": f"{D}T17:00", "end": f"{D}T19:00"})
check("блок на всех мастеров", r.status_code == 200 and r.json()["block"]["resource_id"] is None, r.text[:120])
r = book(f"{D}T17:30", mani["id"], meerim)
check("общий блок действует и на другого мастера", r.status_code == 400, err(r))
book(f"{D2}T11:00", mani["id"], meerim, name="Будущая")
r = cl.post("/api/booking/blocks", headers=HO, json={"resource_id": meerim, "kind": "vacation", "start": f"{D2}T00:00", "end": f"{D2}T23:59"})
check("отпуск поверх записей — блок создан и предупреждение о записях", r.status_code == 200 and r.json().get("clash") == 1, r.text[:160])
vac = r.json()["block"]["id"]
ov = cl.get(f"/api/booking/overview?date={D}&days=3", headers=HO).json()
check("обзор: блоки видны", len(ov["blocks"]) == 3, [b["kind"] for b in ov["blocks"]])
r = cl.delete(f"/api/booking/blocks/{vac}", headers=HO)
check("блок удалён", r.status_code == 200 and len(cl.get(f"/api/booking/overview?date={D}&days=3", headers=HO).json()["blocks"]) == 2)
r = cl.post("/api/booking/blocks", headers=HO, json={"kind": "block", "start": f"{D}T12:00", "end": f"{D}T11:00"})
check("блок: конец раньше начала — отказ", r.status_code == 400)
r = cl.post("/api/booking/blocks", headers=HO, json={"kind": "party", "start": f"{D}T12:00", "end": f"{D}T13:00"})
check("блок: неизвестный вид — отказ", r.status_code == 400)

# ── кабинеты
room = cl.post("/api/booking/rooms", headers=HO, json={"name": "Кабинет 1", "kind": "room"}).json()["room"]
laser = cl.post("/api/booking/services", headers=HO, json={"title": "Лазер", "category": "Косметология", "price": 3000, "duration_min": 60,
                                                          "room_ids": [room["id"], str(uuid.uuid4())], "masters": [{"id": aigul}, {"id": meerim}]}).json()["service"]
check("услуге назначен кабинет (чужие id отброшены)", laser.get("room_ids") == [room["id"]], laser.get("room_ids"))
r = book(f"{D}T15:00", laser["id"], aigul, "Лазер 1")
b1 = r.json().get("booking") or {}
check("запись занимает кабинет", r.status_code == 200 and b1.get("room_id") == room["id"], r.text[:160])
r = book(f"{D}T15:00", laser["id"], meerim, "Лазер 2")
check("второй мастер в то же время — кабинет занят", r.status_code == 400 and "кабинет" in err(r), err(r))
slots = [x["time"] for x in cl.get(f"/api/booking/slots?service_id={laser['id']}&resource_id={meerim}&date={D}", headers=HO).json()["days"][0]["slots"]]
check("свободное время учитывает кабинет", "15:00" not in slots and "16:00" in slots, slots[:12])
room2 = cl.post("/api/booking/rooms", headers=HO, json={"name": "Кабинет 2"}).json()["room"]
cl.patch(f"/api/booking/services/{laser['id']}", headers=HO, json={"room_ids": [room["id"], room2["id"]]})
r = book(f"{D}T15:00", laser["id"], meerim, "Лазер 2")
check("второй кабинет — можно", r.status_code == 200 and r.json()["booking"]["room_id"] == room2["id"], r.text[:160])
r = cl.delete(f"/api/booking/rooms/{room2['id']}", headers=HO)
check("кабинет с записями выключается", r.json().get("disabled") is True, r.text[:120])
r = cl.patch(f"/api/booking/rooms/{room['id']}", headers=HO, json={"is_active": False})
slots = cl.get(f"/api/booking/slots?service_id={laser['id']}&date={D}", headers=HO).json()["days"]
check("все кабинеты услуги выключены — окон нет", slots == [], slots[:1])
cl.patch(f"/api/booking/rooms/{room['id']}", headers=HO, json={"is_active": True})
r = cl.post("/api/booking/rooms", headers=HO, json={"name": "X", "kind": "garage"})
check("кабинет: неизвестный вид — отказ", r.status_code == 400)

# ── визит из нескольких услуг
r = cl.post("/api/booking/visit-slots", headers=HO, json={"items": [{"service_id": mani["id"], "resource_id": meerim},
                                                                    {"service_id": pedi["id"], "resource_id": meerim}], "date": D2})
vs = r.json().get("slots") or []
check("окна визита: обе услуги подряд, мимо записи в 11:00 — с 12:30 до 15:15", r.status_code == 200 and vs
      and vs[0]["time"] == "12:30" and vs[0]["end_time"] == "15:15", vs[:2])
r = cl.post("/api/booking/visits", headers=HO, json={"items": [{"service_id": mani["id"], "resource_id": meerim},
                                                               {"service_id": pedi["id"], "resource_id": meerim}],
                                                     "start": f"{D2}T14:00", "client_name": "Айжан", "client_phone": "+996700555666"})
vb = r.json().get("bookings") or []
check("визит записан: у того же мастера педикюр — после уборки 15 мин", r.status_code == 200 and len(vb) == 2
      and vb[0]["visit_id"] and vb[0]["visit_id"] == vb[1]["visit_id"] and vb[1]["start"].endswith("09:15:00+00:00")
      and vb[0]["contact_id"] == vb[1]["contact_id"], [(b["start"], b["visit_id"]) for b in vb])
r = cl.post("/api/booking/visits", headers=HO, json={"items": [{"service_id": mani["id"], "resource_id": aigul},
                                                               {"service_id": pedi["id"], "resource_id": meerim}],
                                                     "start": f"{D2}T11:15", "client_name": "Сезим"})
vb2 = r.json().get("bookings") or []
check("разные мастера: вторая услуга — сразу после первой", r.status_code == 200 and len(vb2) == 2
      and vb2[1]["start"].endswith("06:15:00+00:00"), r.text[:200])
before = db.query(Booking).count()
r = cl.post("/api/booking/visits", headers=HO, json={"items": [{"service_id": mani["id"], "resource_id": aigul},
                                                               {"service_id": pedi["id"], "resource_id": meerim}],
                                                     "start": f"{D2}T13:00", "client_name": "Не влезет"})
db.expire_all()
check("визит не помещается целиком — не пишется ничего", r.status_code == 400 and db.query(Booking).count() == before, err(r))
r = cl.delete(f"/api/booking/visits/{vb[0]['visit_id']}", headers=HO)
db.expire_all()
check("отмена всего визита", r.status_code == 200 and r.json().get("cancelled") == 2
      and all(b.status == "cancelled" for b in db.query(Booking).filter(Booking.visit_id == uuid.UUID(vb[0]["visit_id"]))))

# ── растяжка
bid = book(f"{D2}T16:00", mani["id"], aigul, "Растяжка").json()["booking"]["id"]
r = cl.patch(f"/api/booking/bookings/{bid}", headers=HO, json={"duration_min": 90})
check("растяжка: 60 → 90 минут", r.status_code == 200 and r.json()["booking"]["duration_min"] == 90, r.text[:160])
book(f"{D2}T18:00", None, aigul, "Сосед")
r = cl.patch(f"/api/booking/bookings/{bid}", headers=HO, json={"duration_min": 120})
check("растяжка на соседнюю запись — отказ", r.status_code == 400 and "занято" in err(r), err(r))
r = cl.patch(f"/api/booking/bookings/{bid}", headers=HO, json={"start": f"{D2}T15:30", "resource_id": meerim})
check("перетаскивание: другое время и мастер", r.status_code == 200 and r.json()["booking"]["resource_id"] == meerim
      and r.json()["booking"]["duration_min"] == 90, r.text[:160])

# ── «завершён» — визит и выручка
today = bs.to_local(bs.now_utc()).date()
past = datetime.combine(today, datetime.min.time()).replace(tzinfo=bs.BISHKEK) + timedelta(hours=6)
pb = Booking(user_id=owner.id, resource_id=uuid.UUID(aigul), service_title="Маникюр", client_name="Готово",
             start_at=past, end_at=past + timedelta(hours=1), status="booked", price=1000)
db.add(pb)
db.commit()
r = cl.patch(f"/api/booking/bookings/{pb.id}", headers=HO, json={"status": "completed"})
check("статус «Завершён»", r.status_code == 200 and r.json()["booking"]["status_title"] == "Завершён", r.text[:120])
st = cl.get(f"/api/booking/overview?date={today.isoformat()}", headers=HO).json()["stats"]
check("выручка дня считает завершённые", st.get("arrived") == 1 and st.get("revenue") == 1000, st)
fin = cl.get("/api/finance/summary?period=30d", headers=HO).json()
check("финансы: выручка записей — с завершёнными", fin.get("revenue", 0) >= 1000, {k: fin.get(k) for k in ("revenue", "bookings_revenue")})
cl.patch(f"/api/finance/payroll/{aigul}", headers=HO, json={"pay_percent": 50})
me = cl.get(f"/api/booking/me?resource_id={aigul}", headers=HO).json()
check("заработок мастера: завершённый визит", (me.get("earnings") or {}).get("today", {}).get("salary") == 500, me.get("earnings"))
an = cl.get("/api/analytics/report?period=7d&sections=bookings", headers=HO).json()
bk = (an.get("bookings") or {})
check("аналитика: «пришли» включает завершённые", bk.get("arrived", 0) >= 1, {k: bk.get(k) for k in ("arrived", "revenue")})

# ── уведомления и поток
import psycopg2  # noqa: E402
from backend.services import booking_stream  # noqa: E402
lconn = psycopg2.connect(booking_stream._dsn(), sslmode="prefer")
lconn.set_isolation_level(0)
lcur = lconn.cursor()
lcur.execute("LISTEN voksy_booking")
book(f"{D2}T10:00", None, meerim, "Уведомление")
import select  # noqa: E402
select.select([lconn], [], [], 3)
lconn.poll()
payloads = [n.payload for n in lconn.notifies]
check("изменение журнала → pg_notify с id владельца", str(owner.id) in payloads, payloads)
lconn.notifies.clear()
s2 = SessionLocal()
try:
    u = s2.query(User).filter(User.id == owner.id).first()
    bs.create_booking(s2, u, start=f"{D2}T08:00", resource_id=meerim, client_name="Откат")
    s2.rollback()
finally:
    s2.close()
select.select([lconn], [], [], 1)
lconn.poll()
check("откат — уведомления нет", not lconn.notifies, [n.payload for n in lconn.notifies])
lconn.close()


async def stream_case():
    disconnected = {"v": False}

    async def is_disc():
        return disconnected["v"]
    gen = booking_stream.events(str(owner.id), is_disc)
    first = [await gen.__anext__(), await gen.__anext__()]
    booking_stream._fanout(str(owner.id))
    booking_stream._fanout(str(owner.id))
    ev = await asyncio.wait_for(gen.__anext__(), timeout=3)
    subs = booking_stream.stats()["subscribers"]
    await gen.aclose()
    return first, ev, subs, booking_stream.stats()["subscribers"]

first, ev, subs, after = asyncio.run(stream_case())
check("поток: готово, затем одно событие на два изменения", "event: ready" in first[1] and ev.startswith("event: changed")
      and subs == 1 and after == 0, (first, ev, subs, after))
booking_stream.MAX_SECONDS = 1.0          # тестовый клиент не сообщает об отключении — поток закроется сам
with cl.stream("GET", "/api/booking/stream", headers=HO) as r:
    head = r.headers.get("content-type", "")
    chunk = next(r.iter_text())
check("GET /stream — text/event-stream", r.status_code == 200 and head.startswith("text/event-stream") and "retry" in chunk, (r.status_code, head, chunk[:40]))
r = cl.get("/api/booking/stream")
check("поток без входа — 401", r.status_code in (401, 403), r.status_code)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
