"""Онлайн-запись (ветка 1811-niche-admin-speed) на настоящей PostgreSQL.

Проверяет одну логику на всех входах:
- свободное время по графику мастера (шаг, перерыв, выходной день, длительность услуги, «не раньше чем»);
- запись из кабинета, двойная запись на занятое время → отказ, «любой свободный» мастер подбирается сам;
- два одновременных запроса на одно время → записывается ровно один (блокировка строки мастера);
- бот (инструменты агента): find → create для клиента обращения (стадия «Записан»), перенос, отмена;
- бот не записывает вне графика и в прошлое, владелец — может;
- клиент по ссылке: страница, свободное время, запись (новая карточка CRM), лимит записей с одного IP;
- голосовой агент: book_appointment берёт номер клиента из звонка (sip_calls) и находит карточку;
- напоминания: за сутки / за 2 часа / «спасибо» — один раз, не тому, кто записался впритык;
- запись выключена у владельца — инструменты отказывают.

Нужен PostgreSQL: BOOKING_TEST_DATABASE_URL (или ADMIN_TEST_DATABASE_URL). Без него — SKIP.
"""
import asyncio
import json
import os
import sys
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("BOOKING_TEST_DATABASE_URL") or os.environ.get("ADMIN_TEST_DATABASE_URL")
if not PG:
    print("SKIP: BOOKING_TEST_DATABASE_URL не задан")
    print("ПРОВАЛОВ: 0")
    sys.exit(0)
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG, PRODUCTION="true", OPENAI_API_KEY="k",
                  JWT_SECRET_KEY="booking-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_contact import AgentContact  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.models.booking import Booking, BookingResource  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app_module.ensure_booking_tables()
cl = TestClient(app_module.app, raise_server_exceptions=False)
B = bs.BISHKEK
db = SessionLocal()

# ближайшая среда не раньше чем через 2 дня (график пн–сб 9–19)
d0 = datetime.now(B).date() + timedelta(days=2)
while d0.weekday() != 2:
    d0 += timedelta(days=1)
WED = d0.isoformat()
SUN = (d0 + timedelta(days=4)).isoformat()


def at(day_iso, hm):
    return datetime.combine(date.fromisoformat(day_iso), time.fromisoformat(hm), B).isoformat()


plan = SubscriptionPlan(code="start", name="Start", price=7500, max_assistants=3, description="s", is_active=True)
db.add(plan)
db.flush()
u = User(email="salon@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
         subscription_plan_id=plan.id, subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20),
         business_niche="beauty_salon", business_profile={"answers": {"crm": "no", "reminders": "yes"}})
db.add(u)
db.flush()
agent = AgentConfig(user_id=u.id, name="Бот салона")
db.add(agent)
db.flush()
from backend.services.business_profile import apply_to_agent  # noqa: E402
apply_to_agent(db, u, agent)
svc_cut = AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Женская стрижка", body="",
                              price=1200, duration_min=60, collection="Волосы")
svc_nails = AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Маникюр с покрытием", body="",
                                price=900, duration_min=90, collection="Ногти")
client = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Нурай", phone="+996555111222", status="new")
db.add_all([svc_cut, svc_nails, client])
db.commit()
H = {"Authorization": f"Bearer {create_jwt_token(u.id)}"}

# ── кабинет: мастера и свободное время ──
r = cl.get(f"/api/booking/overview?date={WED}&days=7", headers=H)
ov = r.json() if r.status_code == 200 else {}
check("обзор: создан мастер по умолчанию, услуги из каталога, ссылка", r.status_code == 200 and len(ov.get("resources", [])) == 1
      and len(ov.get("services", [])) == 2 and ov.get("page", {}).get("url", "").startswith("https://t.voksyai.online/b/salon-aydana-"),
      (r.status_code, str(ov)[:200]))
m1 = ov["resources"][0]["id"]
r = cl.post("/api/booking/resources", headers=H, json={"name": "Бегимай", "role": "Мастер маникюра",
            "schedule": {"wed": [["10:00", "13:00"], ["14:00", "18:00"]], "thu": [["10:00", "18:00"]]},
            "service_ids": [str(svc_nails.id)]})
check("второй мастер с перерывом и своими услугами", r.status_code == 200, r.text[:200])
m2 = r.json()["resource"]["id"]
r = cl.patch(f"/api/booking/resources/{m1}", headers=H, json={"days_off": [SUN, "мусор"], "name": "Айдана"})
check("выходной день мастера (кривая дата отброшена)", r.status_code == 200 and r.json()["resource"]["days_off"] == [SUN], r.text[:150])

slots = cl.get(f"/api/booking/slots?date={WED}&service_id={svc_nails.id}", headers=H).json()["days"][0]["slots"]
times = [s["time"] for s in slots]
check("маникюр 90 мин: у мастера с перерывом нет окна 12:00 (не влезает до 13:00)",
      "11:30" in times and not any(s["time"] == "12:00" and m2 in s["resource_ids"] and m1 not in s["resource_ids"] for s in slots), times[:12])
check("маникюр: Бегимай не работает в 13:00 (перерыв), Айдана — работает",
      all(m2 not in s["resource_ids"] for s in slots if s["time"] in ("13:00",)) and any(s["time"] == "13:00" for s in slots))
cut = cl.get(f"/api/booking/slots?date={WED}&service_id={svc_cut.id}", headers=H).json()["days"][0]["slots"]
check("стрижку делает только Айдана (у Бегимай свои услуги)", all(s["resource_ids"] == [m1] for s in cut), cut[:2])
check("последнее окно стрижки 18:00 (до 19:00)", cut[-1]["time"] == "18:00", cut[-1])
check("воскресенье: выходной — окон нет", cl.get(f"/api/booking/slots?date={SUN}", headers=H).json()["days"][0]["slots"] == [])

# ── кабинет: запись, двойная запись, «любой свободный» ──
r = cl.post("/api/booking/bookings", headers=H, json={"start": at(WED, "11:00"), "service_id": str(svc_cut.id),
            "resource_id": m1, "client_name": "Асель", "client_phone": "0700 111 222"})
check("запись из кабинета", r.status_code == 200 and r.json()["booking"]["label"].endswith("11:00"), r.text[:200])
b1 = r.json()["booking"]
r = cl.post("/api/booking/bookings", headers=H, json={"start": at(WED, "11:30"), "service_id": str(svc_cut.id),
            "resource_id": m1, "client_name": "Двойник"})
check("на занятое время — отказ «уже занято»", r.status_code == 400 and "занято" in r.text, r.text[:150])
r = cl.post("/api/booking/bookings", headers=H, json={"start": at(WED, "11:00"), "service_id": str(svc_nails.id),
            "client_name": "Мээрим"})
check("«любой свободный»: Айдана занята — записал к Бегимай", r.status_code == 200 and r.json()["booking"]["resource"] == "Бегимай", r.text[:200])
slots = cl.get(f"/api/booking/slots?date={WED}&service_id={svc_cut.id}", headers=H).json()["days"][0]["slots"]
check("занятое время исчезло из свободных", not any(s["time"] in ("10:30", "11:00", "11:30") for s in slots), [s["time"] for s in slots][:6])
r = cl.post("/api/booking/bookings", headers=H, json={"start": at(WED, "20:30"), "resource_id": m1, "client_name": "Поздний"})
check("владелец может записать вне графика", r.status_code == 200, r.text[:150])

# ── одновременно два запроса на одно время ──
results = []


def _race(name):
    s = SessionLocal()
    try:
        owner = s.get(User, u.id)
        bs.create_booking(s, owner, start=at(WED, "15:00"), resource_id=m1, client_name=name, source="online")
        s.commit()
        results.append("ok")
    except bs.BookingError as e:
        s.rollback()
        results.append(str(e))
    finally:
        s.close()


ts = [threading.Thread(target=_race, args=(f"Гонка {i}",)) for i in range(2)]
[t.start() for t in ts]
[t.join() for t in ts]
check("два одновременных запроса на одно время → одна запись", sorted(results)[0] == "ok" and results.count("ok") == 1, results)

# ── бот в переписке ──
from backend.services import booking_tools  # noqa: E402
ctx = {"surface": "postcall", "current_contact_id": str(client.id), "user": db.get(User, u.id), "user_id": str(u.id),
       "agent_config_id": str(agent.id), "call_direction": "whatsapp_inbound"}
out = json.loads(asyncio.run(booking_tools.execute("find_booking_slots", {"service": "маникюр", "date": WED}, ctx, db)))
check("бот: find_booking_slots по словам «маникюр» → 3 варианта и услуга", out.get("ok") and len(out["slots"]) == 3
      and out["service"]["title"] == "Маникюр с покрытием", str(out)[:200])
first = out["slots"][0]["start"]
out = json.loads(asyncio.run(booking_tools.execute("create_booking", {"start": first, "service": "маникюр с покрытием",
                                                                        "agent_contact_id": str(uuid.uuid4())}, ctx, db)))
check("бот: create_booking записал клиента ЭТОГО обращения (чужой id проигнорирован)", out.get("ok") and out["booking"]["contact_id"] == str(client.id), str(out)[:200])
db.expire_all()
check("клиент перешёл на стадию «Записан»", db.get(AgentContact, client.id).status == "active")
bot_booking = out["booking"]
check("источник — бот, канал — WhatsApp", bot_booking["source"] == "bot" and bot_booking["channel"] == "whatsapp", bot_booking)
out = json.loads(asyncio.run(booking_tools.execute("reschedule_booking", {"new_start": at(WED, "16:00")}, ctx, db)))
check("бот: перенос ближайшей записи клиента", out.get("ok") and out["booking"]["label"].endswith("16:00"), str(out)[:150])
out = json.loads(asyncio.run(booking_tools.execute("create_booking", {"start": at(WED, "21:30"), "service": "стрижка"}, ctx, db)))
check("бот: вне графика — отказ", out.get("ok") is False and "нерабочее" in out.get("error", ""), out)
yesterday = (datetime.now(B) - timedelta(days=1)).replace(hour=12, minute=0).isoformat()
out = json.loads(asyncio.run(booking_tools.execute("create_booking", {"start": yesterday}, ctx, db)))
check("бот: в прошлое — отказ", out.get("ok") is False and "прошло" in out.get("error", ""), out)
out = json.loads(asyncio.run(booking_tools.execute("list_bookings", {}, ctx, db)))
check("бот: записи клиента", out.get("ok") and len(out["bookings"]) == 1, str(out)[:150])
out = json.loads(asyncio.run(booking_tools.execute("cancel_booking", {"reason": "заболела"}, ctx, db)))
check("бот: отмена", out.get("ok") and out["booking"]["status"] == "cancelled", str(out)[:150])

# ── инструменты агента и промпт ──
from backend.services.agent_tools import build_postcall_tools  # noqa: E402
names = [t["function"]["name"] for t in asyncio.run(build_postcall_tools(db.get(AgentConfig, agent.id), db))]
check("инструменты записи у агента ниши с записью", {"find_booking_slots", "create_booking", "cancel_booking"} <= set(names), names[-8:])
from backend.services.agent_prompts import build_orchestrator_prompt  # noqa: E402
check("в промпте блок «Онлайн-запись»", "## Онлайн-запись" in build_orchestrator_prompt(db.get(AgentConfig, agent.id), False, db))

# ── клиент по ссылке ──
slug = ov["page"]["slug"]
r = cl.get(f"/api/public/booking/{slug}")
check("страница записи: название, услуги, мастера", r.status_code == 200 and r.json()["title"] == "Салон Айдана"
      and len(r.json()["services"]) == 2 and len(r.json()["resources"]) == 2, r.text[:200])
r = cl.get(f"/api/public/booking/{slug}/slots?date={WED}&service_id={svc_cut.id}")
check("свободное время по ссылке без «кому»", r.status_code == 200 and "resource_ids" not in json.dumps(r.json()), r.text[:150])
r = cl.post(f"/api/public/booking/{slug}", json={"start": at(WED, "17:00"), "service_id": str(svc_cut.id), "name": "Жылдыз",
                                                  "phone": "0777 333 444"}, headers={"X-Forwarded-For": "10.0.0.7"})
check("запись по ссылке", r.status_code == 200 and r.json()["booking"]["label"].endswith("17:00"), r.text[:200])
c = db.query(AgentContact).filter(AgentContact.phone == "+996777333444").first()
check("по ссылке завелась карточка CRM", c is not None and c.name == "Жылдыз")
r = cl.post(f"/api/public/booking/{slug}", json={"start": at(WED, "17:00"), "service_id": str(svc_cut.id), "name": "Х",
                                                  "phone": "0777 333 445"}, headers={"X-Forwarded-For": "10.0.0.8"})
check("по ссылке на занятое — 400", r.status_code == 400, r.status_code)
codes = [cl.post(f"/api/public/booking/{slug}", json={"start": at(WED, "09:00"), "name": "Спам", "phone": "0700000000"},
                 headers={"X-Forwarded-For": "10.9.9.9"}).status_code for _ in range(10)]
check("лимит записей с одного IP → 429", 429 in codes, codes)
r = cl.put("/api/booking/settings", headers=H, json={"enabled": False})
check("ссылку можно выключить", r.status_code == 200 and cl.get(f"/api/public/booking/{slug}").status_code == 404)
cl.put("/api/booking/settings", headers=H, json={"enabled": True})
r = cl.put("/api/booking/settings", headers=H, json={"slug": "Салон Айдана!"})
check("свой адрес ссылки (транслит)", r.status_code == 200 and r.json()["page"]["slug"] == "salon-aydana", r.text[:150])

# ── голосовой агент на звонке ──
from backend.models.sip_gateway import SipCall  # noqa: E402
from backend.functions.booking_voice import BookAppointmentFunction, FindBookingSlotsFunction  # noqa: E402
call = SipCall(direction="inbound", user_id=u.id, caller="996555111222", did="996312000000", status="answered")
db.add(call)
db.commit()


class _WS:
    call_id = call.id


class _Card:
    user_id = u.id


vctx = {"assistant_config": _Card(), "websocket": _WS()}
out = asyncio.run(FindBookingSlotsFunction.execute({"service": "стрижка", "date": WED}, vctx))
check("голос: свободное время с подписью для речи", out.get("ok") and out["slots"] and "say" in out["slots"][0], str(out)[:150])
out = asyncio.run(BookAppointmentFunction.execute({"start": at(WED, "12:30"), "service": "стрижка", "client_name": "Нурай"}, vctx))
check("голос: записал", out.get("ok") and "12:30" in out.get("say", ""), out)
db.expire_all()
vb = db.query(Booking).filter(Booking.source == "call").first()
check("голос: номер из звонка → карточка клиента найдена", vb is not None and vb.contact_id == client.id and vb.client_phone == "+996555111222",
      (vb and vb.contact_id, vb and vb.client_phone))

# ── напоминания ──
from backend.services import booking_notify as bn  # noqa: E402
now = datetime.now(timezone.utc)
early = Booking(user_id=u.id, resource_id=uuid.UUID(m1), contact_id=client.id, client_name="Нурай", service_title="Стрижка",
                start_at=now + timedelta(hours=23), end_at=now + timedelta(hours=24), status="booked")
soon = Booking(user_id=u.id, resource_id=uuid.UUID(m1), contact_id=client.id, client_name="Нурай",
               start_at=now + timedelta(hours=1, minutes=30), end_at=now + timedelta(hours=2, minutes=30), status="booked")
past = Booking(user_id=u.id, resource_id=uuid.UUID(m1), contact_id=client.id, client_name="Нурай",
               start_at=now - timedelta(hours=3), end_at=now - timedelta(hours=2), status="arrived")
# у другого мастера: запись «впритык» к «soon» у того же мастера база не пропустит (ограничение «нет пересечений»)
last_minute = Booking(user_id=u.id, resource_id=uuid.UUID(m2), contact_id=client.id, client_name="Нурай",
                      start_at=now + timedelta(hours=1, minutes=40), end_at=now + timedelta(hours=2, minutes=40), status="booked")
db.add_all([early, soon, past, last_minute])
db.flush()
early.created_at = now - timedelta(days=3)
soon.created_at = now - timedelta(days=1)
past.created_at = now - timedelta(days=2)
last_minute.created_at = now - timedelta(minutes=30)
db.commit()
kinds = {(str(i), k) for i, k in bn.due(db, now)}
check("за сутки — заранее записавшемуся", (str(early.id), "24h") in kinds, kinds)
check("за 2 часа", (str(soon.id), "2h") in kinds)
check("«спасибо» после визита", (str(past.id), "thanks") in kinds)
check("записавшемуся за 30 мин до — без напоминания", not any(i == str(last_minute.id) for i, _ in kinds))
sent = []


async def fake_send(db_, user_, contact_, message):
    sent.append(message)
    return True

bn.send_to_client = fake_send
n1 = asyncio.run(bn.run_once(now))
n2 = asyncio.run(bn.run_once(now))
check("напоминания уходят один раз", n1 == 3 and n2 == 0, (n1, n2))
check("текст напоминания: время, услуга, мастер", any("Стрижка" in m and "Айдана" in m for m in sent), sent[:1])

# ── голосовой помощник владельца: «кто записан» (Старт и Profi) ──
from backend.services import owner_voice as ov  # noqa: E402
from backend.websockets import handler_owner_voice as hov  # noqa: E402
db.expire_all()
u_now = db.get(User, u.id)
ag_now = db.get(AgentConfig, agent.id)
today_rows = [b for b in bs.list_bookings(db, u.id, *bs.day_bounds(bs.parse_day(None)), include_cancelled=False)]
vb = ov.fast_get_bookings(db, u_now, ag_now, {"day": "today"})
check("голос: кто записан сегодня — столько же, сколько в календаре", vb.get("ok") and vb.get("enabled")
      and vb["total"] == len(today_rows) and vb["card"]["type"] == "list", (vb.get("total"), len(today_rows)))
check("голос: в строке записи — время, клиент, услуга и мастер", any(i["client"] == "Нурай" and i["master"] == "Айдана"
      and i["time"] for i in vb["items"]), vb["items"][:2])
vm = ov.fast_get_bookings(db, u_now, ag_now, {"day": "today", "master": "Бегимай"})
check("голос: фильтр по мастеру", vm.get("ok") and all(i["master"] == "Бегимай" for i in vm.get("items", [])), vm.get("items"))
vx = ov.fast_get_bookings(db, u_now, ag_now, {"day": "today", "master": "Несуществующая"})
check("голос: неизвестный мастер — подсказка, кто есть", vx.get("found_master") is False and "Айдана" in vx.get("message", ""),
      vx.get("message"))
brief = ov.build_brief(db, u_now, ag_now)
check("голос: записи сегодня и завтра — в сводке дел", "Записи сегодня" in brief and "Записи завтра" in brief, brief[-400:])
rep = ov.fast_get_report(db, u_now, ag_now, {"period": "today"})
check("голос: отчёт с блоком онлайн-записи", any(sc["id"] == "bookings" for sc in rep["card"]["scenes"])
      and any("Записей" in line for line in rep["script"]), [sc["id"] for sc in rep["card"]["scenes"]])
cards = ov.dashboard_cards(db, u_now, ag_now)
check("голос: на старте разговора — карточка «Записи сегодня»", any(c.get("title", "").startswith("Записи сегодня") for c in cards)
      or not today_rows, [c.get("title") for c in cards])
_acc = hov.access_error(u_now, ag_now)
check("голос открыт Старту (дальше проверяются только кредиты)", _acc is None or _acc.get("code") == "insufficient_credits", _acc)
prompt = ov.build_voice_prompt(u_now, ag_now, brief)
check("голос на Старте: в промпте — что можно и что только на Profi", "ТАРИФ СТАРТ" in prompt and "get_bookings" in prompt)

# ── QR и кнопка на сайт ──
r = cl.get("/api/booking/link-kit", headers=H)
kit = r.json() if r.status_code == 200 else {}
slug_now = (kit.get("page") or {}).get("slug") or ""
check("link-kit: QR (SVG) и код виджета с адресом записи",
      r.status_code == 200 and str(kit.get("qr") or "").startswith("data:image/svg+xml")
      and f'data-slug="{slug_now}"' in (kit.get("widget_code") or "") and "booking-widget.js" in kit.get("widget_code", ""))
r = cl.get("/static/booking-widget.js")
check("виджет записи отдаётся статикой", r.status_code == 200 and "VoksyBooking" in r.text)
r = cl.get(f"/b/{slug_now}?embed=1")
check("страницу записи можно открыть окном на чужом сайте (нет X-Frame-Options)",
      r.status_code == 200 and "x-frame-options" not in {k.lower() for k in r.headers})

# ── запись выключена у владельца ──
cl.put("/api/business/modules", headers=H, json={"modules": {"booking": False}})
db.expire_all()
ctx["user"] = db.get(User, u.id)
out = json.loads(asyncio.run(booking_tools.execute("find_booking_slots", {}, ctx, db)))
check("модуль выключен — бот не записывает", out.get("ok") is False, out)
names = [t["function"]["name"] for t in asyncio.run(build_postcall_tools(db.get(AgentConfig, agent.id), db))]
check("модуль выключен — инструментов записи нет", "create_booking" not in names)

db.close()
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
