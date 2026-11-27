"""Голос и записи клиента (Voksy Beauty, шаг 2.6): Beauty Pro = Profi — кому и когда звонить решает агент
по инструкции владельца; платформа даёт ему инструменты, а голосовому агенту на звонке — функции записи.

Часть 1 (без базы): функции зарегистрированы и включаются перед звонком вместе с записью, схемы годятся для
Gemini, описания нейтральны (без нашего бренда и готовых реплик), list_bookings умеет «только неподтверждённые»,
в блоке промпта записи — звонки-подтверждения только по инструкции владельца.

Часть 2 (PostgreSQL, VOICE_BOOKING_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- client_bookings: записи клиента по номеру звонка (визит одной строкой, запись без карточки с этим номером),
  чужих нет; номер не определился — понятный отказ;
- change_booking: подтверждение — сразу (журнал: «Голосовой агент»); перенос и отмена — первый вызов только
  итог (запись не тронута), второй с client_agreed без ответа клиента по стенограмме — отказ, после ответа —
  выполняется; владельцу — «на звонке»; занятое время — «подбери другое»; правило «не позже чем за N ч» —
  «передай администратору»; чужая запись — «нет такой записи»;
- оркестратор: list_bookings(unconfirmed_only) — только неподтверждённые будущие, can_call по номеру O!;
- контекст звонка: строка «Записи клиента впереди: …» с подтверждена / не подтверждена.
"""
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("VOICE_BOOKING_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="voice-booking-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.functions.registry import discover_functions, registry  # noqa: E402
from backend.services import agent_voice_context as avc  # noqa: E402
from backend.services import booking_tools  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
discover_functions()
get_function = registry.get_function
NEW = ("client_bookings", "change_booking")
fns = {n: get_function(n) for n in NEW + ("find_booking_slots", "book_appointment")}
check("функции зарегистрированы", all(fns.values()), [n for n, f in fns.items() if not f])
check("включаются перед звонком вместе с записью", all(n in avc.BOOKING_VOICE_FUNCTIONS for n in NEW))
from backend.websockets.gemini_client import gemini_schema  # noqa: E402
for n in NEW:
    sch = gemini_schema(fns[n].get_parameters())
    check(f"{n}: схема для Gemini без лишних полей", "examples" not in json.dumps(sch) and "default" not in json.dumps(sch))
desc = " ".join(fns[n].get_description() for n in NEW) + " ".join(avc.BOOKING_VOICE_FUNCTIONS[n] for n in NEW)
check("описания нейтральны: без нашего бренда, без готовых реплик в кавычках",
      not re.search(r"Voksy|Вокси|Анри", desc) and "«" not in desc, desc[:120])
params = fns["change_booking"].get_parameters()["properties"]
check("change_booking: действие, новое время, согласие клиента", params["action"]["enum"] == ["confirm", "reschedule", "cancel"]
      and "client_agreed" in params and "new_start" in params)
lb = next(t for t in booking_tools.TOOLS if t["name"] == "list_bookings")
check("оркестратор: list_bookings(unconfirmed_only)", "unconfirmed_only" in lb["parameters"]["properties"])
check("промпт записи: звонки-подтверждения — только по инструкции владельца, функции голоса названы",
      "инструкции владельца" in booking_tools.PROMPT_BLOCK and "change_booking" in booking_tools.PROMPT_BLOCK
      and "unconfirmed_only" in booking_tools.PROMPT_BLOCK)
src = open(os.path.join(ROOT, "backend/api/sip_gateway.py"), encoding="utf-8").read()
check("звонок отдаёт стенограмму функциям", "socket.transcript = transcript" in src)

if not PG:
    print("SKIP часть 2: VOICE_BOOKING_TEST_DATABASE_URL не задан")
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
from backend.models.sip_gateway import SipCall  # noqa: E402
from backend.models.activity import ActivityLog  # noqa: E402
from backend.services import beauty_services, client_cabinet as cc  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services.call_transcript import CallTranscript  # noqa: E402
from backend.services.sip_gateway_service import SipGatewayService  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
app_module.ensure_booking_tables()
app_module.ensure_user_profile_columns()
B = bs.BISHKEK
db = SessionLocal()
NOW = bs.now_utc()
TODAY = bs.to_local(NOW).date()
D = lambda n: TODAY + timedelta(days=n)   # noqa: E731
SENT = []
cc.NOTIFY = lambda owner_id, txt: SENT.append(txt)


def at(day: date, hm: str):
    return datetime.combine(day, time.fromisoformat(hm), B).astimezone(timezone.utc)


def hm(dt) -> str:
    return bs.to_local(dt).strftime("%H:%M")


plan = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p",
                        is_active=True, product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
db.add(plan)
db.flush()
u = User(email="vb@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон",
         subscription_plan_id=plan.id, subscription_end_date=NOW + timedelta(days=20), business_niche="beauty_salon",
         business_profile={"answers": {}})
db.add(u)
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор", is_active=True)
db.add(agent)
db.commit()
user = db.query(User).get(u.id)
page = bs.get_page(db, user, create=True)
page.enabled = True
page.settings = dict(page.settings or {}, title="Салон", lead_min=0, step_min=30)
db.commit()
mani = beauty_services.save(db, user, None, {"title": "Маникюр", "price": 1400, "duration_min": 60, "category": "Ногти"})
pedi = beauty_services.save(db, user, None, {"title": "Педикюр", "price": 1800, "duration_min": 60, "category": "Ногти"})
db.commit()
full = {dd: [["10:00", "19:00"]] for dd in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
m1 = BookingResource(user_id=u.id, name="Айгуль", schedule=full, is_active=True)
db.add(m1)
db.commit()


def contact(name, phone):
    c = AgentContact(user_id=u.id, agent_config_id=agent.id, name=name, phone=phone, status="active", memory={})
    db.add(c)
    db.commit()
    return c


def booking(c, svc, day, t, phone=None, status="booked"):
    b = Booking(user_id=u.id, resource_id=m1.id, service_entry_id=svc.id, service_title=svc.title,
                contact_id=c.id if c else None, client_name=c.name if c else "Гость", client_phone=phone or (c.phone if c else None),
                start_at=at(day, t), end_at=at(day, t) + timedelta(minutes=60), status=status, price=svc.price, source="owner")
    db.add(b)
    db.commit()
    return b


def get(bid):
    db.expire_all()
    return db.query(Booking).get(bid)


aizhan = contact("Айжан", "+996700100100")          # O!
nurai = contact("Нурай", "+996555200200")           # не O!
b1 = booking(aizhan, mani, D(1), "11:00")
b2 = booking(aizhan, pedi, D(3), "15:00", status="confirmed")
nb = booking(nurai, mani, D(1), "13:00")
guest = booking(None, pedi, D(2), "12:00", phone="+996700100100")    # без карточки, тот же номер

call = SipCall(direction="inbound", status="answered", user_id=u.id, caller="996700100100", did="996312000000")
db.add(call)
db.commit()


class Sock:
    def __init__(self, call_id, transcript=None):
        self.call_id = str(call_id)
        self.transcript = transcript


tr = CallTranscript()
tr.add("user", "Саламатсызбы, жазылуумду которгум келет")
CTX = {"assistant_config": agent, "websocket": Sock(call.id, tr)}


def run(name, args, ctx=None):
    return asyncio.run(get_function(name).execute(args, ctx or CTX))


r = run("client_bookings", {})
ids = [x["booking_id"] for x in r.get("bookings", [])]
check("client_bookings: записи клиента по номеру звонка, без чужих", r.get("ok") and ids == [str(b1.id), str(guest.id), str(b2.id)]
      and str(nb.id) not in ids, r)
c1 = r["bookings"][0]
check("client_bookings: когда словами, мастер, не подтверждена, можно менять", "завтра в 11:00" in c1["when"]
      and c1["masters"] == ["Айгуль"] and not c1["confirmed"] and c1["can_move"] and c1["can_cancel"], c1)
check("client_bookings: подтверждённая отмечена", r["bookings"][2]["confirmed"])
nophone = run("client_bookings", {}, {"assistant_config": agent, "websocket": Sock(uuid.uuid4())})
check("номер звонка не определился — отказ", not nophone.get("ok") and "Номер" in nophone.get("error", ""), nophone)

r = run("change_booking", {"booking_id": str(b1.id), "action": "confirm"})
check("подтверждение — сразу", r.get("ok") and r.get("done") == "confirmed" and get(b1.id).status == "confirmed", r)
db.expire_all()
log = db.query(ActivityLog).filter(ActivityLog.object_id == str(b1.id), ActivityLog.action == "booking.status").first()
check("журнал действий: «Голосовой агент»", log is not None and log.actor_kind == "call", log.actor_kind if log else None)

new = at(D(1), "16:00").isoformat()
r = run("change_booking", {"booking_id": str(b1.id), "action": "reschedule", "new_start": new})
check("перенос, шаг 1: итог, запись не тронута", not r.get("ok") and r.get("needs_client_answer") and "16:00" in r.get("summary", "")
      and "11:00" in r.get("summary", "") and hm(get(b1.id).start_at) == "11:00", r)
r = run("change_booking", {"booking_id": str(b1.id), "action": "reschedule", "new_start": new, "client_agreed": True})
check("перенос, шаг 2 без ответа клиента — отказ", not r.get("ok") and hm(get(b1.id).start_at) == "11:00", r)
tr.add("assistant", "Перенесём на завтра в 16:00, подходит?")
tr.add("user", "Ооба, болот")
r = run("change_booking", {"booking_id": str(b1.id), "action": "reschedule", "new_start": new, "client_agreed": True})
check("перенос после ответа клиента — выполнен", r.get("ok") and r.get("done") == "rescheduled" and hm(get(b1.id).start_at) == "16:00", r)
check("владельцу — «перенёс … на звонке»", SENT and "на звонке" in SENT[-1] and "перенёс" in SENT[-1], SENT[-1:])
r = run("change_booking", {"booking_id": str(b1.id), "action": "reschedule", "new_start": at(D(1), "13:00").isoformat()})
check("перенос в занятое время — подбери другое", not r.get("ok") and "find_booking_slots" in r.get("error", "")
      and not r.get("needs_client_answer"), r)
r = run("change_booking", {"booking_id": str(nb.id), "action": "cancel"})
check("чужая запись — «нет такой записи»", not r.get("ok") and "нет" in r.get("error", "") and get(nb.id).status == "booked", r)

r = run("change_booking", {"booking_id": str(b2.id), "action": "cancel", "reason": "заболела"})
check("отмена, шаг 1: итог", r.get("needs_client_answer") and "Педикюр" in r.get("summary", "") and get(b2.id).status == "confirmed", r)
tr.add("user", "Да, отмените")
r = run("change_booking", {"booking_id": str(b2.id), "action": "cancel", "reason": "заболела", "client_agreed": True})
check("отмена после ответа — выполнена, причина «на звонке»", r.get("ok") and get(b2.id).status == "cancelled"
      and "на звонке" in (get(b2.id).cancel_reason or "") and "заболела" in (get(b2.id).cancel_reason or ""), r)

page = bs.get_page(db, user)
page.settings = dict(page.settings, cancel_hours=48)
db.commit()
r = run("change_booking", {"booking_id": str(guest.id), "action": "cancel"})
check("«не позже чем за 48 ч» — передать администратору", not r.get("ok") and "48" in r.get("error", "")
      and "администратор" in r.get("error", "") and get(guest.id).status == "booked", r)
page.settings = dict(page.settings, cancel_hours=0)
db.commit()

# оркестратор: неподтверждённые и can_call
ctx = {"user": user, "user_id": str(u.id), "surface": "chat"}
out = json.loads(asyncio.run(booking_tools.execute("list_bookings", {"date": D(1).isoformat(), "days": 3, "unconfirmed_only": True}, ctx, db)))
rows = {x["id"]: x for x in out.get("bookings", [])}
check("list_bookings(unconfirmed_only): только неподтверждённые будущие", set(rows) == {str(nb.id), str(guest.id)}, list(rows))
check("can_call: номер O! — да, другой — нет", rows.get(str(guest.id), {}).get("can_call") is True
      and rows.get(str(nb.id), {}).get("can_call") is False)
allrows = json.loads(asyncio.run(booking_tools.execute("list_bookings", {"date": D(1).isoformat(), "days": 3}, ctx, db)))["bookings"]
check("list_bookings без фильтра — с подтверждёнными", any(x["id"] == str(b1.id) and x["confirmed"] for x in allrows))

# контекст звонка
b3 = booking(aizhan, pedi, D(5), "12:00")
line = SipGatewayService.contact_bookings_line(db, aizhan)
check("контекст звонка: записи клиента впереди с подтверждена / не подтверждена",
      line.startswith("Записи клиента впереди:") and "(подтверждена)" in line and "(не подтверждена)" in line, line)
block = SipGatewayService.inbound_known_contact_block(call, agent, db)
check("блок известного клиента на звонке включает его записи", "Записи клиента впереди" in block, block[-200:])

print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
