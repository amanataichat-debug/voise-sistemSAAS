"""Лист ожидания салона (Voksy Beauty, шаг 2.4).

Часть 1 (без базы): подписанная ссылка /w/<токен>, права сотрудников, текст предложения RU/KY, страницы и скрипты,
значки экрана, журнал действий.

Часть 2 (PostgreSQL, WAITLIST_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- очередь: окна нет — никому ничего; отменили запись → окно ПЕРВОМУ в очереди (сообщение со ссылкой в очередь
  отправок), второму в это время — нет; не ответил 30 минут → окно следующему; «не подходит» → то же окно ему
  больше не предлагаем; время суток («после 11») учитывается; ответ по ссылке → запись (источник online, канал
  waitlist); повторное «записаться» — без второй записи; окно успели занять → честный отказ, клиент снова ждёт;
  «больше не нужно» → убран; прошли желаемые дни → «срок прошёл»; нет мессенджера → сами не предлагаем, подсказка;
- кабинет: список с местом в очереди и счётчиками, добавить (повтор того же клиента на ту же услугу — обновляет),
  изменить, убрать, «предложить сейчас», журнал записи показывает число ждущих, журнал действий;
- со страницы салона: встать в лист (карточка клиента заводится), неверные даты — 400;
- бот: add_to_waitlist, answer_waitlist_offer — без «да» клиента после предложения не записывает, с «да» — записывает;
- сотрудник «Онлайн-запись» — да, «Клиенты» — 403; страница /w/<токен> — noindex.
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
PG = os.environ.get("WAITLIST_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="waitlist-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import automations as au  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services import notify_queue as nq  # noqa: E402
from backend.services import team_service as ts  # noqa: E402
from backend.services import waitlist_service as wl  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
eid = uuid.uuid4()
tok = wl.link_token(eid)
check("ссылка: туда и обратно, подделка и обрезка — отказ", wl.entry_id_from_token(tok) == eid
      and wl.entry_id_from_token(tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None
      and wl.entry_id_from_token(tok[:40]) is None and wl.link(eid) == "https://t.voksyai.online/w/" + tok)
check("права: лист ожидания — «Онлайн-запись» и «Мастера и график», «Клиенты» — нет",
      ts.staff_allowed(["booking"], "GET", "/api/booking/waitlist") and ts.staff_allowed(["booking"], "POST", "/api/booking/waitlist")
      and ts.staff_allowed(["booking_setup"], "POST", f"/api/booking/waitlist/{eid}/offer")
      and ts.staff_allowed(["booking"], "DELETE", f"/api/booking/waitlist/{eid}")
      and not ts.staff_allowed(["crm", "inbox"], "GET", "/api/booking/waitlist")
      and not ts.staff_allowed(["booking_own"], "POST", "/api/booking/waitlist"))
for lang in ("ru", "ky"):
    txt = au.render(wl.OFFER_TEXT[lang], {"имя": "Айжан", "когда": "завтра в 10:00", "услуга": "Маникюр", "мастер": "",
                                          "ссылка": "https://t.voksyai.online/w/x"})
    check(f"текст предложения ({lang}): время, услуга, ссылка, 30 минут; пустой мастер выпадает",
          "10:00" in txt and "Маникюр" in txt and "/w/x" in txt and "30" in txt and "{" not in txt and "[" not in txt, txt)
html = open(os.path.join(ROOT, "backend/static/booking.html"), encoding="utf-8").read()
check("журнал записи подключает экран листа ожидания", "/static/booking/waitlist.js" in html)
wh = open(os.path.join(ROOT, "backend/static/wait.html"), encoding="utf-8").read()
check("страница ответа: свои js и css, noindex", "/static/wait/wait.js" in wh and "/static/wait/wait.css" in wh and "noindex" in wh)
sj = open(os.path.join(ROOT, "backend/static/salon/salon.js"), encoding="utf-8").read()
check("страница салона: кнопка и шаг «Лист ожидания»", "data-wl" in sj and "/waitlist'" in sj and "function step6" in sj)
sprite = open(os.path.join(ROOT, "backend/static/icons/ui.svg"), encoding="utf-8").read()
have = set(re.findall(r'id="i-([a-z0-9-]+)"', sprite))
src = open(os.path.join(ROOT, "backend/static/booking/waitlist.js"), encoding="utf-8").read()
used = set(re.findall(r"icon\('([a-z0-9-]+)'", src))
check("значки экрана есть в спрайте", not (used - have), sorted(used - have))
from backend.services import activity as act  # noqa: E402
check("журнал действий: лист ожидания — в группе «Записи»", "waitlist" in act.GROUPS["bookings"][1]
      and act.ACTION_TITLES.get("waitlist.offer"))

if not PG:
    print("SKIP часть 2: WAITLIST_TEST_DATABASE_URL не задан")
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
from backend.models.automation import NotificationQueue  # noqa: E402
from backend.models.booking import Booking, BookingResource  # noqa: E402
from backend.models.waitlist import WaitlistEntry  # noqa: E402
from backend.models.activity import ActivityLog  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402
from backend.core import background_loop  # noqa: E402
from backend.services import agent_tools, beauty_services  # noqa: E402

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
TOM = TODAY + timedelta(days=1)
background_loop.submit = lambda coro: coro.close()          # владельцу в Telegram — не в тесте
nq.SERVICE_WINDOW = (0, 24)
REAL_CAN = wl._can_message
wl._can_message = lambda db_, c: True


def at(day: date, hm: str):
    return datetime.combine(day, time.fromisoformat(hm), B).astimezone(timezone.utc)


plan = SubscriptionPlan(code="beauty_start", name="Beauty Start", price=1990, max_assistants=3, description="s",
                        is_active=True, product="beauty", limits={"staff": 3, "agent_mode": "reactive", "credits_month": 0})
db.add(plan)
db.flush()
u = User(email="wl@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
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
page.settings = dict(page.settings or {}, title="Салон Айдана", lead_min=0)
db.commit()
SLUG = page.slug
svc = beauty_services.save(db, user, None, {"title": "Маникюр", "price": 1400, "duration_min": 60, "category": "Ногти"})
db.commit()
two_hours = {dd: [["10:00", "12:00"]] for dd in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
m1 = BookingResource(user_id=u.id, name="Айгуль", schedule=two_hours, is_active=True)
db.add(m1)
db.commit()


def contact(name, phone):
    c = AgentContact(user_id=u.id, agent_config_id=agent.id, name=name, phone=phone, status="new", memory={})
    db.add(c)
    db.commit()
    return c


def busy(c, hm, day=None):
    day = day or TOM
    b = Booking(user_id=u.id, resource_id=m1.id, service_entry_id=svc.id, service_title="Маникюр", contact_id=c.id,
                client_name=c.name, start_at=at(day, hm), end_at=at(day, hm) + timedelta(minutes=60), status="booked",
                price=1400, source="owner")
    db.add(b)
    db.commit()
    return b


def entry(e_id):
    db.expire_all()
    return db.query(WaitlistEntry).get(e_id)


def offers(contact_id):
    db.expire_all()
    return db.query(NotificationQueue).filter(NotificationQueue.rule == "waitlist_offer",
                                              NotificationQueue.contact_id == contact_id).all()


def tick():
    return wl.tick(db, bs.now_utc(), force=True)


other = contact("Занята", "+996555000001")
b10 = busy(other, "10:00")
b11 = busy(other, "11:00")
aizhan, nurai, saltanat = contact("Айжан Токтогулова", "+996555100100"), contact("Нурай", "+996700200200"), contact("Салтанат", "+996777300300")

r = cl.post("/api/booking/waitlist", headers=HO, json={"service_id": str(svc.id), "resource_id": str(m1.id), "contact_id": str(aizhan.id),
                                                      "date_from": TOM.isoformat(), "date_to": TOM.isoformat()})
check("кабинет: в лист ожидания", r.status_code == 200 and r.json()["entry"]["status"] == "waiting"
      and r.json()["entry"]["resource"] == "Айгуль", r.text[:300])
A = uuid.UUID(r.json()["entry"]["id"])
Bn = uuid.UUID(cl.post("/api/booking/waitlist", headers=HO, json={"service_id": str(svc.id), "contact_id": str(nurai.id),
                                                                   "date_from": TOM.isoformat(), "date_to": TOM.isoformat()}).json()["entry"]["id"])
C = uuid.UUID(cl.post("/api/booking/waitlist", headers=HO, json={"service_id": str(svc.id), "contact_id": str(saltanat.id),
                                                                  "date_from": TOM.isoformat(), "date_to": TOM.isoformat(),
                                                                  "time_from": "11:00"}).json()["entry"]["id"])
r = cl.post("/api/booking/waitlist", headers=HO, json={"service_id": str(svc.id), "contact_id": str(aizhan.id),
                                                      "date_from": TOM.isoformat(), "date_to": (TOM + timedelta(days=1)).isoformat()})
check("тот же клиент на ту же услугу — обновили, не задвоили", r.status_code == 200 and r.json()["entry"]["id"] == str(A)
      and db.query(WaitlistEntry).filter(WaitlistEntry.contact_id == aizhan.id).count() == 1
      and entry(A).resource_id is None)
cl.patch(f"/api/booking/waitlist/{A}", headers=HO, json={"resource_id": str(m1.id), "date_to": TOM.isoformat()})
check("изменить: мастер и дни", entry(A).resource_id == m1.id and entry(A).date_to == TOM)
lst = cl.get("/api/booking/waitlist", headers=HO).json()
check("список: по очереди с местами, счётчики", [x["client_name"] for x in lst["items"]] == ["Айжан Токтогулова", "Нурай", "Салтанат"]
      and [x["position"] for x in lst["items"]] == [1, 2, 3] and lst["counts"]["active"] == 3 and lst["counts"]["waiting"] == 3)
ov = cl.get(f"/api/booking/overview?date={TOM.isoformat()}", headers=HO).json()
check("журнал записи знает, сколько ждут", ov.get("waitlist") == 3)
check("журнал действий: «В лист ожидания»", db.query(ActivityLog).filter(ActivityLog.action == "waitlist.add").count() == 3)

tick()
check("окна нет — никому ничего не предлагаем", entry(A).status == "waiting" and not offers(aizhan.id))

# отменили запись на 10:00 → окно первому
cl.delete(f"/api/booking/bookings/{b10.id}", headers=HO)
n = tick()
ea = entry(A)
q = offers(aizhan.id)
check("освободилось 10:00 → предложено ПЕРВОМУ, сообщение со ссылкой в очереди",
      n == 1 and ea.status == "offered" and bs.to_local(ea.offer_start).strftime("%H:%M") == "10:00" and len(q) == 1
      and wl.link(A) in q[0].text and "завтра в 10:00" in q[0].text and q[0].expires_at is not None,
      (n, ea.status, q[0].text if q else None))
check("второму в очереди то же окно не предлагаем, «после 11» не подходит 10:00",
      entry(Bn).status == "waiting" and entry(C).status == "waiting" and not offers(nurai.id))
check("журнал действий: «Предложено из листа»", db.query(ActivityLog).filter(ActivityLog.action == "waitlist.offer").count() == 1)
g = cl.get(f"/api/public/waitlist/{wl.link_token(A)}")
G = g.json() if g.status_code == 200 else {}
check("ссылка: предложение — время, мастер, сколько осталось", g.status_code == 200 and G["offer"]["time"] == "10:00"
      and G["offer"]["master"]["name"] == "Айгуль" and 25 <= G["offer"]["minutes_left"] <= 30 and "_entry_id" not in G, g.text[:300])
pg = cl.get(f"/w/{wl.link_token(A)}")
check("страница /w/<токен>: открывается, noindex", pg.status_code == 200 and "noindex" in pg.headers.get("x-robots-tag", "")
      and "/static/wait/wait.js" in pg.text)

# 30 минут без ответа → следующему
db.execute(text("UPDATE waitlist_entries SET offer_expires_at = now() - interval '1 minute' WHERE id = :i"), {"i": A})
db.commit()
tick()
check("не ответил 30 минут → окно следующему, первый снова ждёт", entry(A).status == "waiting"
      and "30 минут" in (entry(A).last_note or "") and entry(Bn).status == "offered"
      and bs.to_local(entry(Bn).offer_start).strftime("%H:%M") == "10:00" and len(offers(nurai.id)) == 1)
r = cl.post(f"/api/public/waitlist/{wl.link_token(Bn)}", json={"action": "decline"})
tick()
check("«не подходит» → ждёт другое; то же окно больше не предлагаем ни ему, ни первому",
      r.status_code == 200 and entry(Bn).status == "waiting" and entry(A).status == "waiting" and len(offers(nurai.id)) == 1)

# освободилось 11:00 → первому; «после 11» тоже подходит, но очередь
cl.delete(f"/api/booking/bookings/{b11.id}", headers=HO)
tick()
A_AT = bs.to_local(entry(A).offer_start).strftime("%H:%M") if entry(A).offer_start else None
check("освободилось ещё → снова первому, самое раннее окно, кроме уже предложенного 10:00; «после 11» ждёт очереди",
      entry(A).status == "offered" and A_AT == "10:30" and entry(C).status == "waiting", A_AT)
r = cl.post(f"/api/public/waitlist/{wl.link_token(A)}", json={"action": "accept"})
ea = entry(A)
bk = db.query(Booking).get(ea.booking_id) if ea.booking_id else None
check("«Записаться» по ссылке → запись (по ссылке, из листа ожидания)", r.status_code == 200 and r.json()["status"] == "booked"
      and ea.status == "booked" and bk is not None and bs.to_local(bk.start_at).strftime("%H:%M") == A_AT
      and bk.source == "online" and bk.channel == "waitlist" and bk.contact_id == aizhan.id, r.text[:200])
r = cl.post(f"/api/public/waitlist/{wl.link_token(A)}", json={"action": "accept"})
check("повторное «записаться» — без второй записи", r.status_code == 200 and r.json().get("already")
      and db.query(Booking).filter(Booking.contact_id == aizhan.id).count() == 1)
check("журнал действий: запись от клиента по ссылке", db.query(ActivityLog).filter(
    ActivityLog.action == "booking.create", ActivityLog.object_id == str(bk.id), ActivityLog.actor_kind == "client").count() == 1)

# послезавтра свободно — новая клиентка на свой день получает окно сразу
DAY2 = TOM + timedelta(days=1)
dana = contact("Дана", "+996555400400")
D = wl.add(db, user, service_id=svc.id, contact_id=dana.id, date_from=DAY2, date_to=DAY2).id
db.commit()
tick()
check("новый в очереди на свободный день — окно сразу", entry(D).status == "offered"
      and bs.to_local(entry(D).offer_start).strftime("%H:%M") == "10:00" and bs.to_local(entry(D).offer_start).date() == DAY2)
# пока думала — администратор занял это время
busy(other, "10:00", DAY2)
r = cl.post(f"/api/public/waitlist/{wl.link_token(D)}", json={"action": "accept"})
check("окно успели занять → честный отказ, клиент снова ждёт", r.status_code == 400 and "успели занять" in r.json()["message"]
      and entry(D).status == "waiting" and not entry(D).booking_id, r.text[:200])
r = cl.post(f"/api/public/waitlist/{wl.link_token(D)}", json={"action": "stop"})
check("«больше не нужно» → убран из листа", r.status_code == 200 and entry(D).status == "cancelled"
      and cl.get(f"/api/public/waitlist/{wl.link_token(D)}").json()["entry"]["status"] == "cancelled")

# прошли желаемые дни
old_c = contact("Старая", "+996555500500")
O = wl.add(db, user, service_id=svc.id, contact_id=old_c.id, date_from=TODAY, date_to=TODAY).id
db.commit()
db.execute(text("UPDATE waitlist_entries SET date_from = date_from - 3, date_to = date_to - 2 WHERE id = :i"), {"i": O})
db.commit()
tick()
check("прошли желаемые дни → «срок прошёл»", entry(O).status == "expired")

# нет мессенджера
wl._can_message = REAL_CAN
nom = contact("Без мессенджера", "+996555600600")
E = wl.add(db, user, service_id=svc.id, contact_id=nom.id, date_from=TOM, date_to=TOM + timedelta(days=3)).id
db.commit()
tick()
check("нет мессенджера → сами не предлагаем, подсказка «предложите сами»", entry(E).status == "waiting"
      and "мессенджер" in (entry(E).last_note or ""), entry(E).last_note)
wl._can_message = lambda db_, c: True
r = cl.post(f"/api/booking/waitlist/{E}/offer", headers=HO)
check("«Предложить сейчас» — находит окно и пишет", r.status_code == 200 and r.json()["offered"] and entry(E).status == "offered",
      r.text[:200])
r = cl.post(f"/api/booking/waitlist/{E}/offer", headers=HO)
check("«Предложить сейчас» второй раз — уже ждём ответа", r.status_code == 400 and "ждём" in r.json()["message"])
check("убрать из листа", cl.delete(f"/api/booking/waitlist/{E}", headers=HO).status_code == 200 and entry(E).status == "cancelled")
check("чужая / неверная запись — 404", cl.delete(f"/api/booking/waitlist/{uuid.uuid4()}", headers=HO).status_code == 404)

# со страницы салона
r = cl.post(f"/api/public/booking/{SLUG}/waitlist", json={"service_id": str(svc.id), "date_from": TOM.isoformat(),
                                                          "date_to": (TOM + timedelta(days=6)).isoformat(), "time_from": "17:00",
                                                          "client_name": "Айпери", "client_phone": "+996 770 111 222"})
pw = db.query(WaitlistEntry).filter(WaitlistEntry.client_name == "Айпери").first()
cc = db.query(AgentContact).filter(AgentContact.id == pw.contact_id).first() if pw else None
check("страница салона: встать в лист → запись листа (сама), карточка клиента заведена", r.status_code == 200 and pw is not None
      and pw.source == "online" and pw.time_from == "17:00" and cc is not None and cc.source == "booking", r.text[:200])
r = cl.post(f"/api/public/booking/{SLUG}/waitlist", json={"service_id": str(svc.id), "date_from": TOM.isoformat(),
                                                          "date_to": TODAY.isoformat(), "client_name": "X", "client_phone": "+996770111223"})
check("страница салона: «по» раньше «с» — 400 с понятным текстом", r.status_code == 400 and "раньше" in r.json()["message"])

# бот
bc = contact("Бегимай", "+996555700700")
ctx = {"agent_config_id": str(agent.id), "user_id": str(u.id), "user": user, "agent_config": agent, "surface": "postcall",
       "current_contact_id": str(bc.id), "call_direction": "whatsapp_inbound"}
DAY3 = TOM + timedelta(days=2)
r = json.loads(asyncio.run(agent_tools.execute_tool("add_to_waitlist", {"service": "маникюр", "date_from": DAY3.isoformat(),
                                                                         "date_to": DAY3.isoformat()}, ctx, db)))
be = db.query(WaitlistEntry).filter(WaitlistEntry.contact_id == bc.id).first()
check("бот: add_to_waitlist → запись листа (бот), ответ — что сказать", r.get("ok") and be is not None and be.source == "bot"
      and "30 минут" in r.get("next", ""), r)
db.execute(text("UPDATE waitlist_entries SET status = 'cancelled' WHERE status IN ('waiting','offered') AND id <> :i"), {"i": be.id})
db.commit()
tick()
check("бот: клиенту из бота предложено окно", entry(be.id).status == "offered")
r = json.loads(asyncio.run(agent_tools.execute_tool("answer_waitlist_offer", {"accept": True}, ctx, db)))
check("бот: «да» ещё не пришло после предложения — не записывает", not r.get("ok") and entry(be.id).status == "offered", r)
t0 = datetime.utcnow()
db.add(AgentWhatsAppMessage(agent_contact_id=bc.id, agent_config_id=agent.id, direction="outbound",
                            body="Освободилось время: завтра в 10:00. Записать вас?", created_at=t0 + timedelta(seconds=1)))
db.add(AgentWhatsAppMessage(agent_contact_id=bc.id, agent_config_id=agent.id, direction="inbound", body="Да, записывайте",
                            created_at=t0 + timedelta(seconds=2)))
db.commit()
r = json.loads(asyncio.run(agent_tools.execute_tool("answer_waitlist_offer", {"accept": True}, ctx, db)))
bb = db.query(Booking).get(entry(be.id).booking_id) if entry(be.id).booking_id else None
check("бот: «да, записывайте» → запись, источник bot", r.get("ok") and entry(be.id).status == "booked" and bb is not None
      and bb.source == "bot" and bb.channel == "waitlist", r)
r = json.loads(asyncio.run(agent_tools.execute_tool("answer_waitlist_offer", {"accept": False}, ctx, db)))
check("бот: нечего отвечать — понятный отказ", not r.get("ok") and "ничего не предложено" in r.get("error", ""))

# сотрудники
adm, _ = ts.create_member(db, user, name="Админ", email="adm@kg.kg", password="secret12", role_title="Администратор",
                          permissions=["booking", "inbox"])
crm_only, _ = ts.create_member(db, user, name="Менеджер", email="crm@kg.kg", password="secret12", role_title="Менеджер",
                               permissions=["crm"])
db.commit()


def sh(m):
    return {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(m.id), "stv": m.token_version})}


r = cl.post("/api/booking/waitlist", headers=sh(adm), json={"service_id": str(svc.id), "client_name": "Чолпон",
                                                            "client_phone": "+996555800800", "date_from": TOM.isoformat()})
check("сотрудник «Онлайн-запись»: видит и ставит в лист (источник — администратор)", r.status_code == 200
      and cl.get("/api/booking/waitlist", headers=sh(adm)).status_code == 200
      and entry(uuid.UUID(r.json()["entry"]["id"])).source == "staff", r.text[:200])
check("сотрудник «Клиенты» — 403", cl.get("/api/booking/waitlist", headers=sh(crm_only)).status_code == 403)
db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
