"""«Мои записи» клиента салона по ссылке /m/<токен> (Voksy Beauty, шаг 2.5).

Часть 1 (без базы): токены карточки и записи (подделка, чужой вид, обрезка — отказ), вставка {мои_записи} в
автоматизациях (тексты по умолчанию, пустая — кусок выпадает), страница и скрипты, кнопки на странице салона.

Часть 2 (PostgreSQL, CLIENT_CABINET_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- страница /m/<токен>: 200, noindex, название салона в заголовке; подделка — 404;
- данные: ближайшие записи (визит из двух услуг — одна карточка), записи без карточки с тем же номером, история
  с «оценить» и «записаться снова», лист ожидания; чужих записей нет;
- чужая запись по своей ссылке — 404 на перенос, отмену, подтверждение (и запись не тронута);
- перенос: своё время при подборе не мешает (окно, пересекающее свою запись, предлагается), в занятое — отказ,
  «любой мастер» — к другому мастеру; прошлое время — отказ; журнал действий — «клиент»; владельцу — сообщение;
- визит из двух услуг у одного мастера сдвигается целиком (и раньше, и позже, через своё же время);
- правило салона cancel_hours: позже — нельзя (кнопки скрыты, причина и телефон); онлайн-запись выключена —
  перенос нельзя, отмена можно; мастер «только через администратора» — перенос нельзя;
- подтверждение, отмена визита целиком (с причиной), «записаться снова» к тому же мастеру (источник online,
  канал cabinet), убрать себя из листа ожидания (чужую запись листа — 404);
- автоматизации: {мои_записи} у будущей записи — ссылка, у отменённой — пусто; запись по ссылке со страницы
  салона отдаёт my_url, который открывает её же; карточка клиента в кабинете отдаёт ссылку владельцу.
"""
import os
import re
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("CLIENT_CABINET_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="client-cabinet-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import automations as au  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services import client_cabinet as cc  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
cid, bid = uuid.uuid4(), uuid.uuid4()
tc, tb = cc.token_for_contact(cid), cc.token_for_booking(bid)
check("токен карточки и записи: туда и обратно", cc.parse_token(tc) == ("c", cid) and cc.parse_token(tb) == ("b", bid)
      and len(tc) == 45 and cc.link_for_contact(cid) == "https://t.voksyai.online/m/" + tc)
flip = tc[:-1] + ("0" if tc[-1] != "0" else "1")
check("подделка подписи, чужой вид, обрезка, мусор — отказ", cc.parse_token(flip) is None
      and cc.parse_token("b" + tc[1:]) is None and cc.parse_token(tc[:40]) is None and cc.parse_token("../etc") is None
      and cc.parse_token(tc.upper()) == ("c", cid))
names = [k for k, _ in au.PLACEHOLDERS]
check("автоматизации: вставка {мои_записи} есть и принимается в своём тексте", "мои_записи" in names
      and au.clean_template("Ваши записи: {мои_записи}") == "Ваши записи: {мои_записи}")
for key in ("confirm", "reminder_24h"):
    for lang in ("ru", "ky"):
        tpl = au.RULE_BY_KEY[key][lang]
        with_link = au.render(tpl, {"имя": "Айжан", "когда": "завтра в 15:00", "мои_записи": "https://t.voksyai.online/m/x"})
        without = au.render(tpl, {"имя": "Айжан", "когда": "завтра в 15:00", "мои_записи": ""})
        check(f"текст по умолчанию «{key}» ({lang}): ссылка есть, без ссылки кусок выпадает",
              "{мои_записи}" in tpl and "/m/x" in with_link and "/m/" not in without and "[" not in without, without)
mh = open(os.path.join(ROOT, "backend/static/my.html"), encoding="utf-8").read()
check("страница: свои js и css, чат салона, место под заголовок", "/static/my/my.js" in mh and "/static/my/my.css" in mh
      and "/static/salon/chat.js" in mh and "<!--MY_HEAD-->" in mh and "viewport-fit=cover" in mh)
mj = open(os.path.join(ROOT, "backend/static/my/my.js"), encoding="utf-8").read()
check("скрипт: API по токену, перенос, отмена, подтверждение, повтор, лист ожидания, «Назад»",
      "'/api/public/client/'" in mj and "/reschedule'" in mj and "/cancel'" in mj and "/confirm'" in mj
      and "'/repeat'" in mj and "/stop'" in mj and "popstate" in mj and "SalonChat.init" in mj)
sj = open(os.path.join(ROOT, "backend/static/salon/salon.js"), encoding="utf-8").read()
check("страница салона: «Мои записи» после записи и кнопка для вернувшегося клиента",
      "r.my_url" in sj and "vxs_my_" in sj and "function myUrl" in sj)
cs = open(os.path.join(ROOT, "backend/static/start/crm-salon.js"), encoding="utf-8").read()
check("карточка клиента в кабинете: «Скопировать ссылку»", "data-copy-my" in cs and "c.my_url" in cs)

if not PG:
    print("SKIP часть 2: CLIENT_CABINET_TEST_DATABASE_URL не задан")
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
from backend.models.waitlist import WaitlistEntry  # noqa: E402
from backend.models.activity import ActivityLog  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402
from backend.core import background_loop  # noqa: E402
from backend.services import beauty_services  # noqa: E402

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
D = lambda n: TODAY + timedelta(days=n)   # noqa: E731
background_loop.submit = lambda coro: coro.close()
SENT, NEW = [], []
cc.NOTIFY = lambda owner_id, txt: SENT.append(txt)
cc.NOTIFY_NEW = lambda owner_id, booking_id: NEW.append(str(booking_id))


def at(day: date, hm: str):
    return datetime.combine(day, time.fromisoformat(hm), B).astimezone(timezone.utc)


def hm(dt) -> str:
    return bs.to_local(dt).strftime("%H:%M")


plan = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p",
                        is_active=True, product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
db.add(plan)
db.flush()
u = User(email="my@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
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
page.settings = dict(page.settings or {}, title="Салон Айдана", lead_min=0, step_min=30, address="Бишкек, Киевская 1")
page.profile = {"phone": "+996700111222", "whatsapp": "+996700111222", "accent": "#7c3aed"}
db.commit()
SLUG = page.slug
mani = beauty_services.save(db, user, None, {"title": "Маникюр", "price": 1400, "duration_min": 60, "category": "Ногти"})
pedi = beauty_services.save(db, user, None, {"title": "Педикюр", "price": 1800, "duration_min": 60, "category": "Ногти"})
db.commit()
full_day = {dd: [["10:00", "19:00"]] for dd in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
m1 = BookingResource(user_id=u.id, name="Айгуль", schedule=full_day, is_active=True, color="#db2777")
m2 = BookingResource(user_id=u.id, name="Бермет", schedule=full_day, is_active=True)
db.add_all([m1, m2])
db.commit()


def contact(name, phone):
    c = AgentContact(user_id=u.id, agent_config_id=agent.id, name=name, phone=phone, status="active", memory={})
    db.add(c)
    db.commit()
    return c


def booking(c, master, svc, day, t, status="booked", phone=None, minutes=60):
    b = Booking(user_id=u.id, resource_id=master.id, service_entry_id=svc.id, service_title=svc.title,
                contact_id=c.id if c else None, client_name=(c.name if c else "Гуля"),
                client_phone=phone or (c.phone if c else None), start_at=at(day, t),
                end_at=at(day, t) + timedelta(minutes=minutes), status=status, price=svc.price, source="owner")
    db.add(b)
    db.commit()
    return b


def get(b_id):
    db.expire_all()
    return db.query(Booking).get(b_id)


aizhan = contact("Айжан Токтогулова", "+996555100100")
nurai = contact("Нурай", "+996700200200")
b1 = booking(aizhan, m1, mani, D(1), "11:00")
nb = booking(nurai, m1, mani, D(1), "13:00")
visit = bs.create_visit(db, user, [{"service_id": str(mani.id), "resource_id": str(m1.id)},
                                   {"service_id": str(pedi.id), "resource_id": str(m1.id)}], at(D(3), "14:00"),
                        contact_id=str(aizhan.id), source="owner")
db.commit()
V1, V2 = visit[0].id, visit[1].id
nocard = booking(None, m2, mani, D(5), "16:00", phone="+996555100100")      # без карточки, тот же номер
past = booking(aizhan, m2, mani, D(-10), "12:00", status="completed")
gulya = booking(None, m2, pedi, D(2), "10:00", phone="+996777999888")       # другой клиент без карточки
TOK = cc.token_for_contact(aizhan.id)
API = "/api/public/client/" + TOK

# страница
r = cl.get("/m/" + TOK)
check("страница /m/<токен>: 200, noindex, салон в заголовке", r.status_code == 200 and "noindex" in r.headers.get("x-robots-tag", "")
      and "Мои записи — Салон Айдана" in r.text and "<!--MY_HEAD-->" not in r.text and "/static/my/my.js" in r.text, r.status_code)
check("подделанная ссылка: страница открывается, данные — 404", cl.get("/m/" + TOK[:-1] + "0").status_code == 200
      and cl.get("/api/public/client/" + TOK[:-1] + ("0" if TOK[-1] != "0" else "1")).status_code == 404
      and cl.get("/api/public/client/" + cc.token_for_contact(uuid.uuid4())).status_code == 404)

st = cl.get(API).json()
up = st.get("upcoming") or []
ids = [x["id"] for x in up]
check("данные: имя, салон, цвет, контакты, правило отмены", st["client"]["name"] == "Айжан" and st["salon"]["name"] == "Салон Айдана"
      and st["salon"]["accent"] == "#7c3aed" and st["salon"]["phone"] and st["salon"]["book_url"] == f"/b/{SLUG}"
      and st["salon"]["cancel_hours"] == 0, st.get("salon"))
check("ближайшие: своя запись, визит одной карточкой (2 услуги), запись без карточки с тем же номером",
      ids == [str(b1.id), str(V1), str(nocard.id)] and len(up[1]["items"]) == 2 and up[1]["time"] == "14:00"
      and up[1]["end_time"] == "16:00" and up[1]["price"] == 3200, [(x["label"], len(x["items"])) for x in up])
check("чужих записей нет (Нурай, Гуля)", str(nb.id) not in ids and str(gulya.id) not in ids)
check("кнопки: подтвердить, перенести, отменить", up[0]["can_confirm"] and up[0]["can_move"] and up[0]["can_cancel"]
      and up[0]["items"][0]["master"]["name"] == "Айгуль")
h = st.get("history") or []
check("история: визит, «оценить» (ссылка /r/), «записаться снова»", len(h) == 1 and h[0]["id"] == str(past.id)
      and (h[0]["review"] or {}).get("url", "").startswith("/r/") and h[0]["can_repeat"], h)

# чужая запись по своей ссылке
for path, body in ((f"/bookings/{nb.id}/reschedule", {"start": at(D(1), "15:00").isoformat()}),
                   (f"/bookings/{nb.id}/cancel", {}), (f"/bookings/{nb.id}/confirm", None)):
    rr = cl.post(API + path, json=body) if body is not None else cl.post(API + path)
    check(f"чужая запись: {path.split('/')[-1]} — 404", rr.status_code == 404, rr.text[:120])
check("чужая запись не тронута", get(nb.id).status == "booked" and hm(get(nb.id).start_at) == "13:00")
check("слоты по чужой записи — 404", cl.get(API + f"/slots?booking_id={nb.id}").status_code == 404)

# перенос одной записи
r = cl.get(API + f"/slots?booking_id={b1.id}&days=14")
days = r.json().get("days") or []
tom = next((d for d in days if d["date"] == D(1).isoformat()), {"slots": []})
times = [s["time"] for s in tom["slots"]]
check("слоты переноса: 14 дней, своё время не мешает (11:30 через свою запись), занятое Нурай (13:00) — нет",
      r.status_code == 200 and len(days) == 14 and "11:30" in times and "11:00" in times and "12:30" not in times
      and "13:00" not in times, times[:12])
r = cl.post(API + f"/bookings/{b1.id}/reschedule", json={"start": at(D(1), "11:30").isoformat()})
check("перенос на 11:30 через свою запись", r.status_code == 200 and hm(get(b1.id).start_at) == "11:30"
      and "11:30" in r.json().get("label", ""), r.text[:200])
check("владельцу — «клиент перенёс»", SENT and "перенёс" in SENT[-1] and "Айжан" in SENT[-1], SENT[-1:])
db.expire_all()
log = db.query(ActivityLog).filter(ActivityLog.object_id == str(b1.id), ActivityLog.action == "booking.move").first()
check("журнал действий: перенос от клиента", log is not None and log.actor_kind == "client" and "Айжан" in (log.actor_name or ""),
      (log.actor_kind, log.actor_name) if log else None)
r = cl.post(API + f"/bookings/{b1.id}/reschedule", json={"start": at(D(1), "13:00").isoformat()})
check("перенос в занятое время — понятный отказ", r.status_code == 400 and "занят" in r.text.lower()
      and hm(get(b1.id).start_at) == "11:30", r.text[:200])
r = cl.post(API + f"/bookings/{b1.id}/reschedule", json={"start": at(D(1), "13:00").isoformat(), "any_master": True})
check("«любой мастер» — к Бермет на 13:00", r.status_code == 200 and get(b1.id).resource_id == m2.id
      and hm(get(b1.id).start_at) == "13:00", r.text[:200])
r = cl.post(API + f"/bookings/{b1.id}/reschedule", json={"start": (NOW - timedelta(hours=1)).isoformat()})
check("прошлое время — отказ", r.status_code == 400 and hm(get(b1.id).start_at) == "13:00", r.text[:160])

# визит из двух услуг
r = cl.get(API + f"/slots?booking_id={V1}&days=7")
d3 = next((d for d in r.json().get("days") or [] if d["date"] == D(3).isoformat()), {"slots": []})
check("слоты визита: 13:00 и 15:00 через своё же время, «до» у каждого", r.status_code == 200 and r.json().get("visit")
      and any(s["time"] == "13:00" and s["end_time"] == "15:00" for s in d3["slots"])
      and any(s["time"] == "15:00" for s in d3["slots"]), [s["time"] for s in d3["slots"]][:10])
r = cl.post(API + f"/bookings/{V1}/reschedule", json={"start": at(D(3), "13:00").isoformat()})
check("визит сдвинут на час раньше целиком", r.status_code == 200 and hm(get(V1).start_at) == "13:00"
      and hm(get(V2).start_at) == "14:00", r.text[:200])
r = cl.post(API + f"/bookings/{V2}/reschedule", json={"start": at(D(3), "15:00").isoformat()})
check("визит сдвинут на 2 часа позже (по ссылке на вторую услугу)", r.status_code == 200 and hm(get(V1).start_at) == "15:00"
      and hm(get(V2).start_at) == "16:00" and get(V1).status == "booked", r.text[:200])

# правило салона и выключатели
page = bs.get_page(db, user)
page.settings = dict(page.settings, cancel_hours=48)
db.commit()
st = cl.get(API).json()
c1 = next(x for x in st["upcoming"] if x["id"] == str(b1.id))
check("cancel_hours 48: завтрашнюю нельзя — кнопок нет, причина", not c1["can_move"] and not c1["can_cancel"]
      and "48" in (c1["change_note"] or "") and c1["can_confirm"], c1.get("change_note"))
r = cl.post(API + f"/bookings/{b1.id}/cancel", json={})
check("cancel_hours: отмена завтрашней — отказ", r.status_code == 400 and "48" in r.text and get(b1.id).status == "booked")
check("cancel_hours: слоты для переноса — отказ", cl.get(API + f"/slots?booking_id={b1.id}").status_code == 400)
page.settings = dict(page.settings, cancel_hours=0)
page.enabled = False
db.commit()
st = cl.get(API).json()
c1 = next(x for x in st["upcoming"] if x["id"] == str(b1.id))
check("онлайн-запись выключена: перенос нельзя, отмена можно", not c1["can_move"] and c1["can_cancel"]
      and cl.post(API + f"/bookings/{b1.id}/reschedule", json={"start": at(D(1), "16:00").isoformat()}).status_code == 400)
page.enabled = True
m2.online_booking = False
db.commit()
st = cl.get(API).json()
c1 = next(x for x in st["upcoming"] if x["id"] == str(b1.id))
check("мастер «только через администратора»: перенос нельзя, подсказка", not c1["can_move"] and c1["can_cancel"]
      and "администратор" in (c1["change_note"] or ""), c1.get("change_note"))
m2.online_booking = True
db.commit()

# подтверждение, отмена визита
r = cl.post(API + f"/bookings/{b1.id}/confirm")
check("подтвердить визит", r.status_code == 200 and get(b1.id).status == "confirmed")
st = cl.get(API).json()
check("после подтверждения кнопки «подтвердить» нет, статус «вы подтвердили»",
      not next(x for x in st["upcoming"] if x["id"] == str(b1.id))["can_confirm"])
r = cl.post(API + f"/bookings/{V1}/cancel", json={"reason": "Заболела"})
check("отмена визита — обе услуги, с причиной", r.status_code == 200 and get(V1).status == "cancelled"
      and get(V2).status == "cancelled" and "Заболела" in (get(V1).cancel_reason or ""), r.text[:200])
check("владельцу — «клиент отменил» с причиной", "отменил" in SENT[-1] and "Заболела" in SENT[-1], SENT[-1:])
check("отменённый визит пропал из ближайших", str(V1) not in [x["id"] for x in cl.get(API).json()["upcoming"]])

# записаться снова
r = cl.get(API + f"/slots?booking_id={past.id}&mode=repeat&days=14")
check("«записаться снова»: время мастера Бермет", r.status_code == 200 and any(d["slots"] for d in r.json()["days"]), r.text[:200])
r = cl.post(API + "/repeat", json={"booking_id": str(past.id), "start": at(D(2), "16:00").isoformat()})
nbid = (r.json() or {}).get("booking_id")
nb2 = get(uuid.UUID(nbid)) if nbid else None
check("новая запись: к Бермет, онлайн, канал cabinet, та же карточка", r.status_code == 200 and nb2 is not None
      and nb2.resource_id == m2.id and nb2.source == "online" and nb2.channel == "cabinet" and nb2.contact_id == aizhan.id
      and nb2.service_entry_id == mani.id, r.text[:200])
check("владельцу — «новая онлайн-запись»", NEW and NEW[-1] == nbid)

# лист ожидания
we = WaitlistEntry(user_id=u.id, contact_id=aizhan.id, client_name=aizhan.name, client_phone=aizhan.phone,
                   service_entry_id=pedi.id, service_title="Педикюр", date_from=D(1), date_to=D(7), status="waiting", source="online")
wo = WaitlistEntry(user_id=u.id, contact_id=nurai.id, client_name="Нурай", service_entry_id=pedi.id, service_title="Педикюр",
                   date_from=D(1), date_to=D(7), status="waiting", source="online")
db.add_all([we, wo])
db.commit()
wl = cl.get(API).json()["waitlist"]
check("лист ожидания: своя запись видна, чужой нет", [x["id"] for x in wl] == [str(we.id)] and wl[0]["service"] == "Педикюр", wl)
check("убрать чужую запись листа — 404", cl.post(API + f"/waitlist/{wo.id}/stop").status_code == 404)
r = cl.post(API + f"/waitlist/{we.id}/stop")
db.expire_all()
check("«больше не нужно» — убран из листа", r.status_code == 200 and db.query(WaitlistEntry).get(we.id).status == "cancelled", r.text[:160])

# запись без карточки — своя ссылка по записи
TB = cc.token_for_booking(gulya.id)
st = cl.get("/api/public/client/" + TB).json()
check("ссылка по записи без карточки: только записи Гули", [x["id"] for x in st["upcoming"]] == [str(gulya.id)]
      and st["client"]["name"] == "Гуля")
r = cl.post("/api/public/client/" + TB + f"/bookings/{gulya.id}/reschedule", json={"start": at(D(2), "17:00").isoformat()})
check("Гуля переносит свою запись", r.status_code == 200 and hm(get(gulya.id).start_at) == "17:00", r.text[:160])
check("Гуля не видит записи Айжан", cl.post("/api/public/client/" + TB + f"/bookings/{b1.id}/cancel", json={}).status_code == 404)

# автоматизации и вход со страницы салона
salon_vals = au._salon_values(db, user)
vals = au.values_for_booking(db, user, get(b1.id), bs.now_utc(), "ru", salon_vals, {})
check("{мои_записи} у будущей записи — ссылка на карточку", vals["мои_записи"] == cc.link_for_contact(aizhan.id))
vals_c = au.values_for_booking(db, user, get(V1), bs.now_utc(), "ru", salon_vals, {})
check("{мои_записи} у отменённой — пусто", vals_c["мои_записи"] == "")
txt = au.render(au.RULE_BY_KEY["confirm"]["ru"], vals)
check("подтверждение записи по умолчанию — со ссылкой «Мои записи»", "/m/" + TOK in txt, txt)
r = cl.post(f"/api/public/booking/{SLUG}", json={"start": at(D(4), "10:00").isoformat(), "service_id": str(mani.id),
                                                "name": "Айжан", "phone": "+996 555 100 100"})
my = (r.json() or {}).get("my_url") or ""
check("запись со страницы салона отдаёт my_url (та же карточка по номеру)", r.status_code == 200 and my == "/m/" + TOK, r.text[:200])
check("my_url открывает и новую запись", any(x["date"] == D(4).isoformat() and x["time"] == "10:00" for x in cl.get("/api/public/client/" + my[3:]).json()["upcoming"]))
r = cl.get(f"/api/beauty/clients/{aizhan.id}", headers=HO)
check("карточка клиента в кабинете: ссылка «Мои записи»", r.status_code == 200 and r.json().get("my_url") == cc.link_for_contact(aizhan.id), r.text[:160])

print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
