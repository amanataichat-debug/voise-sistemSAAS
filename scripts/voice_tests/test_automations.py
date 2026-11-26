"""Очередь отправок и автоматизации клиентам (Voksy Beauty, шаг 2.2).

Часть 1 (без базы): шаблоны (вставки, [необязательные куски], чистка пробелов и знаков), «когда» и «прошло» по-русски
и по-кыргызски, проверка своих текстов (неизвестная вставка, скобки, длина), у каждого правила есть RU и KY без
лишних скобок, окна отправки, права сотрудников, экран и пункт меню, значки, словари.

Часть 2 (PostgreSQL, AUTOMATIONS_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- подтверждение записи: владелец записал → сообщение со временем, услугой и мастером; бот записал — нет;
  визит из двух услуг — одно сообщение с обеими; повторный план и два воркера разом — одна строка (idem_key);
- напоминания и «спасибо»: свои часы правила, выключенное правило молчит, старые галочки «напоминания» уважаются;
  «просить оценку» → просьба оценить, после отправки review_asked_at; «не пришёл»;
- отправка: окна по Бишкеку (ночью ждём утра, опоздавшее — пропуск), сбой → повтор через 5 минут → … →
  «не доставлено», нет мессенджера — «пропущено», зависшая «отправляется» возвращается, предел 30 за 10 минут,
  бот выключен навсегда — ничего;
- возврат клиентов: ДР (подарок в тексте), отказ от рассылок, «одному клиенту не чаще», дневной лимит, кыргызский
  клиент получает KY, «пора повторить» (у услуги repeat_days; записан вперёд — нет), «давно не были», сценарий у
  ИИ-управляющего — правило молчит;
- API: правила, сохранение (неверный вариант и неизвестная вставка — 400), пример текста, лимиты, журнал отправок
  с фильтрами, «отправить ещё раз», «не отправлять», журнал действий; сотрудник «Мастера и график» — да, «Клиенты»
  и мастер — 403; отказ от рассылок из карточки и ботом; чистка старой очереди.
"""
import asyncio
import json
import os
import re
import sys
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("AUTOMATIONS_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="automations-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import automations as au  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services import notify_queue as nq  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
vals = {"имя": "Айжан", "когда": "завтра в 15:00", "услуга": "Маникюр", "мастер": "", "адрес": ""}
txt = au.render("Здравствуйте[, {имя}]! Вы записаны {когда}[ на {услуга}][ к мастеру {мастер}].[ Адрес: {адрес}.] Ждём!", vals)
check("шаблон: пустые куски в скобках выпадают", txt == "Здравствуйте, Айжан! Вы записаны завтра в 15:00 на Маникюр. Ждём!", txt)
check("шаблон: без имени — без запятой", au.render("Здравствуйте[, {имя}]!", {}) == "Здравствуйте!")
check("шаблон: неизвестное значение — пусто, пробелы и знаки чистые", au.render("Привет {имя} , как дела ?", {}) == "Привет, как дела?")
now = datetime(2026, 11, 26, 8, 0, tzinfo=timezone.utc)          # 14:00 Бишкек, четверг
check("когда: сегодня / завтра / дата (RU)", au.when_words(now + timedelta(hours=2), now) == "сегодня в 16:00"
      and au.when_words(now + timedelta(days=1), now) == "завтра в 14:00"
      and au.when_words(now + timedelta(days=3), now) == "вс 29.11 в 14:00", au.when_words(now + timedelta(days=3), now))
check("когда: по-кыргызски", au.when_words(now + timedelta(days=1), now, "ky") == "эртең саат 14:00"
      and au.when_words(now, now, "ky") == "бүгүн саат 14:00")
check("прошло: дни, недели, месяцы", au.ago_words(5) == "5 дней" and au.ago_words(21) == "3 недели" and au.ago_words(90) == "3 месяца"
      and au.ago_words(35, "ky") == "5 жума")
for bad, why in (("Привет {фамилия}", "Неизвестная вставка"), ("Привет [ {имя}", "скобки"), ("x" * 1001, "длиннее")):
    try:
        au.clean_template(bad)
        check(f"свой текст с ошибкой ({why}) — отказ", False)
    except au.AutomationError as e:
        check(f"свой текст с ошибкой ({why}) — понятный отказ", why in str(e), str(e))
check("свой текст: пусто → вернуть по умолчанию", au.clean_template("  ") is None)
full = {k: "X" for k, _ in au.PLACEHOLDERS}
ky_letters = set("өүңӨҮҢ")
for r in au.RULES:
    for lang in ("ru", "ky"):
        out = au.render(r[lang], full)
        check(f"правило {r['key']} {lang}: вставки известны, скобок не осталось",
              au.clean_template(r[lang]) and "{" not in out and "[" not in out, out[:80])
    check(f"правило {r['key']}: KY по-кыргызски", any(ch in ky_letters for ch in r["ky"]))
night = datetime(2026, 11, 26, 17, 30, tzinfo=timezone.utc)      # 23:30 Бишкек
check("окна: ночью — ждём 08:00 (по записи) и 10:00 (рассылки) завтра",
      bs.to_local(nq.next_window("service", night)).strftime("%d %H:%M") == "27 08:00"
      and bs.to_local(nq.next_window("marketing", night)).strftime("%d %H:%M") == "27 10:00")
check("окна: 14:00 — можно", nq._window_ok("service", now) and nq._window_ok("marketing", now) and not nq._window_ok("service", night))
check("права: автоматизации — «Мастера и график»", ts.staff_allowed(["booking_setup"], "GET", "/api/beauty/automations")
      and ts.staff_allowed(["booking_setup"], "PUT", "/api/beauty/automations/birthday")
      and ts.staff_allowed(["booking_setup"], "GET", "/api/beauty/automations/queue")
      and not ts.staff_allowed(["crm", "booking", "inbox"], "GET", "/api/beauty/automations")
      and not ts.staff_allowed(["booking_own"], "PUT", "/api/beauty/automations/birthday"))
html = open(os.path.join(ROOT, "backend/static/automations.html"), encoding="utf-8").read()
check("страница: свои js и css", "/static/automations/automations.js" in html and "/static/automations/automations.css" in html
      and "<title>Автоматизации" in html)
side = open(os.path.join(ROOT, "backend/static/js/sidebar.js"), encoding="utf-8").read()
check("меню: «Автоматизации» у ниш с записью (оба меню)", side.count("automations-nav-item") == 2)
sprite = open(os.path.join(ROOT, "backend/static/icons/ui.svg"), encoding="utf-8").read()
have = set(re.findall(r'id="i-([a-z0-9-]+)"', sprite))
src = open(os.path.join(ROOT, "backend/static/automations/automations.js"), encoding="utf-8").read()
used = set(re.findall(r"icon\('([a-z0-9-]+)'", src))
check("значки экрана есть в спрайте", not (used - have), sorted(used - have))
for lang in ("ru", "ky"):
    d = json.load(open(os.path.join(ROOT, f"backend/static/i18n/{lang}.json"), encoding="utf-8"))
    check(f"словарь {lang}: sidebar.automations", bool(d["sidebar"].get("automations")))

if not PG:
    print("SKIP часть 2: AUTOMATIONS_TEST_DATABASE_URL не задан")
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
from backend.models.automation import NotificationQueue, AutomationRule  # noqa: E402
from backend.models.booking import Booking, BookingResource  # noqa: E402
from backend.models.activity import ActivityLog  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402
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
REAL_NOW = bs.now_utc()
TODAY = bs.to_local(REAL_NOW).date()


def at(day: date, hm: str):
    return datetime.combine(day, time.fromisoformat(hm), B).astimezone(timezone.utc)


NOON = at(TODAY, "12:00")                 # «сейчас» для отправки — середина дня по Бишкеку

start_plan = SubscriptionPlan(code="beauty_start", name="Beauty Start", price=1990, max_assistants=3, description="s",
                              is_active=True, product="beauty", limits={"staff": 3, "agent_mode": "reactive", "credits_month": 0})
pro = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p", is_active=True,
                       product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
db.add_all([start_plan, pro])
db.flush()
u = User(email="auto@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
         subscription_plan_id=start_plan.id, subscription_end_date=REAL_NOW + timedelta(days=20),
         business_niche="beauty_salon", business_profile={"answers": {}})
free = User(email="free@kg.kg", password_hash="x", email_verified=True, first_name="Без тарифа", business_niche="beauty_salon")
db.add_all([u, free])
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор", is_active=True)
agent_free = AgentConfig(user_id=free.id, name="Бот", is_active=True)
db.add_all([agent, agent_free])
db.commit()
HO = {"Authorization": "Bearer " + create_jwt_token(u.id)}
user = db.query(User).get(u.id)
check("подготовка: доступ к боту по тарифу", user.has_agent_chat_access() and not db.query(User).get(free.id).has_agent_chat_access())

page = bs.get_page(db, user, create=True)
page.settings = dict(page.settings or {}, address="Бишкек, Киевская 1")
db.commit()
svc = beauty_services.save(db, user, None, {"title": "Маникюр", "price": 1400, "duration_min": 60, "category": "Ногти"})
svc2 = beauty_services.save(db, user, None, {"title": "Брови", "price": 700, "duration_min": 30, "category": "Брови"})
db.commit()
svc.repeat_days = 21
db.commit()
fullday = {dd: [["00:00", "23:59"]] for dd in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
m1 = BookingResource(user_id=u.id, name="Айгуль", schedule=fullday, is_active=True)
m2 = BookingResource(user_id=u.id, name="Бермет", schedule=fullday, is_active=True)
db.add_all([m1, m2])
db.commit()


def contact(name, phone, owner=None, ag=None, **kw):
    c = AgentContact(user_id=(owner or u).id, agent_config_id=(ag or agent).id, name=name, phone=phone, status="new",
                     memory={}, **kw)
    db.add(c)
    db.commit()       # API (свой сеанс базы) должен видеть карточку
    return c


slot = [0]


def booking(c, start, status="booked", master=None, service=None, source="owner", created=None, visit_id=None, owner=None):
    s = service or svc
    b = Booking(user_id=(owner or u).id, resource_id=(master or m1).id, service_entry_id=s.id if s else None,
                service_title=s.title if s else None, contact_id=c.id, client_name=c.name, start_at=start,
                end_at=start + timedelta(minutes=50), status=status, price=1400, source=source, visit_id=visit_id)
    db.add(b)
    db.flush()
    if created is not None:
        b.created_at = created
    db.commit()
    return b


sent = []
OUTCOME = {}           # contact_id → (ok, channel, error)


async def fake_send(db_, user_, contact_, message):
    sent.append((str(contact_.id), message))
    return OUTCOME.get(str(contact_.id), (True, "whatsapp", None))


nq.SENDER = fake_send


def q(**flt):
    db.expire_all()
    qq = db.query(NotificationQueue)
    for k, v in flt.items():
        qq = qq.filter(getattr(NotificationQueue, k) == v)
    return qq.order_by(NotificationQueue.created_at).all()


def process(at_time):
    return asyncio.run(nq.process(db, at_time))


def process_now():
    """Отправить то, что встало в очередь «сейчас» (запись через API), — без оглядки на часы запуска теста."""
    old = nq.SERVICE_WINDOW
    nq.SERVICE_WINDOW = (0, 24)
    try:
        return process(bs.now_utc() + timedelta(seconds=1))
    finally:
        nq.SERVICE_WINDOW = old


# ── подтверждение записи ──
aizhan = contact("Айжан Токтогулова", "+996555100100")
TOM = TODAY + timedelta(days=1)
r = cl.post("/api/booking/bookings", headers=HO, json={"start": at(TOM, "15:00").isoformat(), "service_id": str(svc.id),
                                                        "resource_id": str(m1.id), "contact_id": str(aizhan.id)})
check("подготовка: запись владельцем", r.status_code == 200, r.text[:200])
b_owner = r.json()["booking"]["id"]
au.plan_bookings(db, bs.now_utc())
conf = q(rule="confirm")
check("подтверждение: встало в очередь со временем, услугой, мастером, адресом",
      len(conf) == 1 and "завтра в 15:00" in conf[0].text and "Маникюр" in conf[0].text and "Айгуль" in conf[0].text
      and "Киевская" in conf[0].text and conf[0].kind == "service", conf[0].text if conf else None)
au.plan_bookings(db, bs.now_utc())
check("подтверждение: повторный план — та же одна строка", len(q(rule="confirm")) == 1)
bot_client = contact("Нурай", "+996700200200")
booking(bot_client, at(TOM, "17:00"), source="bot", master=m2)
au.plan_bookings(db, bs.now_utc())
check("подтверждение: записал бот — не дублируем", not [x for x in q(rule="confirm") if x.contact_id == bot_client.id])
visit_client = contact("Мээрим", "+996550300300")
vid = uuid.uuid4()
booking(visit_client, at(TOM, "10:00"), visit_id=vid, master=m1)
booking(visit_client, at(TOM, "11:00"), visit_id=vid, master=m2, service=svc2)
au.plan_bookings(db, bs.now_utc())
vc = [x for x in q(rule="confirm") if x.contact_id == visit_client.id]
check("подтверждение визита из двух услуг — одно сообщение с обеими", len(vc) == 1 and "Маникюр и Брови" in vc[0].text,
      [x.text for x in vc])
# два воркера разом
db.query(NotificationQueue).delete()
db.commit()
errs = []


def worker():
    s = SessionLocal()
    try:
        au.plan_bookings(s, bs.now_utc())
    except Exception as e:
        errs.append(e)
    finally:
        s.close()


ths = [threading.Thread(target=worker) for _ in range(4)]
[t.start() for t in ths]
[t.join() for t in ths]
check("4 воркера разом — по одной строке на сообщение", not errs and len(q(rule="confirm")) == 2, (errs, len(q(rule="confirm"))))
n = process_now()
check("отправка подтверждений", n == 2 and all(x.status == "sent" and x.channel == "whatsapp" for x in q(rule="confirm")), n)

# ── напоминания, «спасибо», «не пришёл» ──
nr = contact("Бакыт", "+996777400400")
early = booking(nr, NOON + timedelta(hours=20), created=NOON - timedelta(days=3), master=m2, service=svc2)
soon = booking(nr, NOON + timedelta(hours=1, minutes=30), created=NOON - timedelta(days=1), master=m1)
due = {(str(i), k) for i, k in au.due_bookings(db, NOON)}
check("накануне и в день визита — по правилам по умолчанию", (str(early.id), "reminder_24h") in due and (str(soon.id), "reminder_2h") in due, due)
# старые галочки: напоминания выключены в правилах записи → правило молчит, пока владелец его не включит
page.settings = dict(page.settings, reminders=False)
db.commit()
due = {(str(i), k) for i, k in au.due_bookings(db, NOON)}
check("старые «напоминания» выключены → не напоминаем", (str(early.id), "reminder_24h") not in due)
au.save_rule(db, user, "reminder_24h", {"enabled": True, "params": {"hours": 24}})
db.commit()
due = {(str(i), k) for i, k in au.due_bookings(db, NOON)}
check("правило включено в автоматизациях → главнее старой галочки", (str(early.id), "reminder_24h") in due
      and (str(soon.id), "reminder_2h") not in due)
page.settings = dict(page.settings, reminders=True)
db.commit()
au.save_rule(db, user, "reminder_2h", {"params": {"hours": 1}})
db.commit()
due = {(str(i), k) for i, k in au.due_bookings(db, NOON)}
check("свои часы: «за 1 час» — за полтора часа ещё рано", (str(soon.id), "reminder_2h") not in due)
due = {(str(i), k) for i, k in au.due_bookings(db, NOON + timedelta(minutes=35))}
check("свои часы: за 55 минут — пора", (str(soon.id), "reminder_2h") in due)
try:
    au.save_rule(db, user, "reminder_2h", {"params": {"hours": 5}})
    check("неверный вариант часов — отказ", False)
except au.AutomationError:
    db.rollback()
    check("неверный вариант часов — отказ", True)
past = booking(nr, NOON - timedelta(hours=3), status="arrived", master=m1, created=NOON - timedelta(days=2))
au.plan_bookings(db, NOON)
th = q(rule="thanks")
check("«спасибо» после визита в очереди", len(th) == 1 and th[0].booking_id == past.id, [x.text for x in th])
au.save_rule(db, user, "thanks", {"params": {"ask_review": True}})
db.commit()
past2 = booking(nr, NOON - timedelta(hours=5), status="completed", master=m2, service=svc2, created=NOON - timedelta(days=2))
au.plan_bookings(db, NOON)
rv = q(rule="review")
check("с оценкой: просьба оценить от 1 до 5 у мастера", len(rv) == 1 and "от 1 до 5" in rv[0].text and "Бермет" in rv[0].text,
      [x.text for x in rv])
process(NOON)
db.expire_all()
check("после отправки просьбы — review_asked_at у записи", db.query(Booking).get(past2.id).review_asked_at is not None)
ns = booking(nr, NOON - timedelta(hours=3), status="no_show", master=m2, service=svc2)
au.plan_bookings(db, NOON)
check("«не пришёл» выключено по умолчанию — молчим", not q(rule="no_show"))
au.save_rule(db, user, "no_show", {"enabled": True})
db.commit()
au.plan_bookings(db, NOON)
nsq = q(rule="no_show")
check("«не пришёл» включено — предложение другого времени", len(nsq) == 1 and "другое время" in nsq[0].text, [x.text for x in nsq])

# ── отправка: окна, повторы, пропуски ──
db.query(NotificationQueue).delete()
db.commit()
c_night = contact("Ночная", "+996555500500")
qid = nq.enqueue(db, user_id=u.id, contact_id=c_night.id, rule="reminder_24h", text_="Напоминаем", idem_key="t:night",
                 send_after=at(TODAY, "22:30"), expires_at=at(TOM, "15:00"))
db.commit()
process(at(TODAY, "22:31"))
it = q(idem_key="t:night")[0]
check("ночью: ждём 08:00, попытка не списана", it.status == "pending" and bs.to_local(it.send_after).strftime("%H:%M") == "08:00"
      and it.attempts == 0, (it.status, it.send_after, it.attempts))
nq.enqueue(db, user_id=u.id, contact_id=c_night.id, rule="reminder_2h", text_="Ждём", idem_key="t:late",
           send_after=at(TODAY, "22:30"), expires_at=at(TOM, "07:00"))
db.commit()
process(at(TODAY, "22:31"))
check("к утру уже поздно — пропуск", q(idem_key="t:late")[0].status == "skipped")
c_fail = contact("Не доходит", "+996555600600")
OUTCOME[str(c_fail.id)] = (False, None, "Не отправилось: WhatsApp")
nq.enqueue(db, user_id=u.id, contact_id=c_fail.id, rule="thanks", text_="Спасибо", idem_key="t:fail", send_after=NOON)
db.commit()
process(NOON)
it = q(idem_key="t:fail")[0]
check("сбой → повтор через 5 минут", it.status == "pending" and it.attempts == 1 and abs((it.send_after - NOON).total_seconds() - 300) < 2
      and "WhatsApp" in (it.last_error or ""), (it.status, it.attempts, it.send_after))
t = NOON
for _ in range(5):
    t = t + timedelta(hours=2)
    process(t)
it = q(idem_key="t:fail")[0]
check("после всех попыток — «не доставлено»", it.status == "failed" and it.attempts == nq.MAX_ATTEMPTS, (it.status, it.attempts))
c_none = contact("Без мессенджера", "+996555700700")
OUTCOME[str(c_none.id)] = (False, None, "no_channel")
nq.enqueue(db, user_id=u.id, contact_id=c_none.id, rule="thanks", text_="Спасибо", idem_key="t:none", send_after=NOON)
db.commit()
process(NOON)
check("нет мессенджера — «пропущено» с причиной", q(idem_key="t:none")[0].status == "skipped"
      and "мессенджера" in q(idem_key="t:none")[0].last_error)
c_stuck = contact("Зависло", "+996555800800")
nq.enqueue(db, user_id=u.id, contact_id=c_stuck.id, rule="thanks", text_="Спасибо", idem_key="t:stuck", send_after=NOON)
db.commit()
db.execute(text("UPDATE notification_queue SET status = 'sending', lease_until = :l WHERE idem_key = 't:stuck'"),
           {"l": NOON - timedelta(minutes=1)})
db.commit()
process(NOON)
check("зависшая «отправляется» (воркер упал) — отправлена снова", q(idem_key="t:stuck")[0].status == "sent")
c_spam = contact("Спамер", "+996555900900", ai_paused_until=datetime(9999, 12, 31), ai_pause_reason="spam")
nq.enqueue(db, user_id=u.id, contact_id=c_spam.id, rule="thanks", text_="Спасибо", idem_key="t:spam", send_after=NOON)
db.commit()
process(NOON)
check("бот выключен навсегда — ничего", q(idem_key="t:spam")[0].status == "skipped")
c_free = contact("Клиент без тарифа", "+996555000111", owner=free, ag=agent_free)
nq.enqueue(db, user_id=free.id, contact_id=c_free.id, rule="thanks", text_="Спасибо", idem_key="t:free", send_after=NOON)
db.commit()
process(NOON)
check("у салона нет тарифа с ботом — «пропущено»", q(idem_key="t:free")[0].status == "skipped")
# предел 30 за 10 минут
db.query(NotificationQueue).delete()
db.commit()
burst = contact("Много", "+996555000222")
for i in range(nq.SEND_BURST + 3):
    nq.enqueue(db, user_id=u.id, contact_id=burst.id, rule="thanks", text_=f"Сообщение {i}", idem_key=f"t:burst:{i}",
               send_after=NOON)
db.commit()
sent.clear()
n1 = process(NOON)
n2 = process(NOON + timedelta(seconds=30))
check(f"предел {nq.SEND_BURST} за 10 минут — остальные ждут", n1 == nq.SEND_BURST and n2 == 0
      and len(q(status="pending")) == 3, (n1, n2, len(q(status="pending"))))
process(NOON + timedelta(minutes=11))
check("через 10 минут — ушли и остальные", len(q(status="sent")) == nq.SEND_BURST + 3)

# ── возврат клиентов ──
db.query(NotificationQueue).delete()
db.commit()
bday = contact("Салтанат Омурбекова", "+996555111000", birthday=date(1990, TODAY.month, TODAY.day) if (TODAY.month, TODAY.day) != (2, 29) else date(1992, 2, 29))
opt = contact("Отписалась", "+996555111001", birthday=bday.birthday, marketing_opt_out_at=datetime.utcnow())
ky = contact("Айбек", "+996555111002", birthday=bday.birthday)
db.add(AgentWhatsAppMessage(agent_contact_id=ky.id, agent_config_id=agent.id, direction="inbound",
                            body="Саламатсызбы, эртең келсем болобу? Канча турат?"))
db.commit()
check("ДР выключено по умолчанию — ничего", au.plan_marketing(db, NOON, force=True) == 0)
r = cl.put("/api/beauty/automations/birthday", headers=HO, json={"enabled": True, "params": {"hour": 11, "gift": "скидка 15% на любую услугу"}})
check("API: ДР включено, подарок сохранён", r.status_code == 200 and r.json()["rule"]["enabled"]
      and r.json()["rule"]["params"]["gift"] == "скидка 15% на любую услугу", r.text[:200])
au.plan_marketing(db, NOON, force=True)
bq = q(rule="birthday")
by = {x.contact_id: x for x in bq}
check("ДР: в очереди именинники, отписавшейся — нет", bday.id in by and ky.id in by and opt.id not in by, [x.text for x in bq])
check("ДР: подарок в тексте, рассылка, час — 11:00", "скидка 15%" in by[bday.id].text and by[bday.id].kind == "marketing"
      and bs.to_local(by[bday.id].send_after).strftime("%H:%M") == "12:00", (by[bday.id].text, by[bday.id].send_after))
check("ДР: кыргызскому клиенту — по-кыргызски", by[ky.id].lang == "ky" and "Туулган күнүңүз" in by[ky.id].text, by[ky.id].text)
au.plan_marketing(db, NOON, force=True)
check("ДР: второй план — без повторов", len(q(rule="birthday")) == 2)
lim = cl.put("/api/beauty/automations/limits", headers=HO, json={"max_per_day": 1, "gap_days": 3})
check("API: лимиты сохранены", lim.status_code == 200 and lim.json()["limits"]["max_per_day"] == 1)
process(NOON)
st = {x.contact_id: x for x in q(rule="birthday")}
sent_n = sum(1 for x in st.values() if x.status == "sent")
waiting = [x for x in st.values() if x.status == "pending"]
check("дневной лимит рассылок: одно ушло, второе ждёт завтра 10:00", sent_n == 1 and len(waiting) == 1
      and bs.to_local(waiting[0].send_after).strftime("%H:%M") == "10:00" and "лимит" in (waiting[0].last_error or ""),
      (sent_n, [(w.send_after, w.last_error) for w in waiting]))
cl.put("/api/beauty/automations/limits", headers=HO, json={"max_per_day": 50})
first = [x for x in st.values() if x.status == "sent"][0]
nq.enqueue(db, user_id=u.id, contact_id=first.contact_id, rule="winback", text_="Давно не виделись", idem_key="t:gap",
           kind="marketing", send_after=NOON)
db.commit()
process(NOON)
check("одному клиенту не чаще раза в 3 дня — пропуск", q(idem_key="t:gap")[0].status == "skipped"
      and "3 дн" in q(idem_key="t:gap")[0].last_error)
# пора повторить
rep = contact("Асель", "+996555222000")
booking(rep, NOON - timedelta(days=25), status="completed", master=m1)
rep_future = contact("Гүлзат", "+996555222001")
booking(rep_future, NOON - timedelta(days=25, hours=2), status="completed", master=m1)
booking(rep_future, NOON + timedelta(days=5), master=m1)
au.save_rule(db, user, "repeat", {"enabled": True})
db.commit()
au.plan_marketing(db, NOON, force=True)
rq = {x.contact_id: x for x in q(rule="repeat")}
check("повтор: прошло 25 из 21 дня — приглашение с услугой, мастером и ссылкой", rep.id in rq
      and "Маникюр" in rq[rep.id].text and "Айгуль" in rq[rep.id].text and "/b/" in rq[rep.id].text, [x.text for x in rq.values()])
check("повтор: записан вперёд — не зовём", rep_future.id not in rq)
check("повтор: «прошло 4 недели»", "4 недели" in rq[rep.id].text if rep.id in rq else False)
# давно не были
wb = contact("Эрмек", "+996555333000")
booking(wb, NOON - timedelta(days=70), status="completed", master=m2, service=svc2)
au.save_rule(db, user, "winback", {"enabled": True, "params": {"days": 60}})
db.commit()
au.plan_marketing(db, NOON, force=True)
check("давно не были (70 > 60 дней) — приглашение", any(x.contact_id == wb.id for x in q(rule="winback")))
# у ИИ-управляющего тот же сценарий (Beauty Pro) — правило молчит
db.query(NotificationQueue).filter(NotificationQueue.rule == "winback").delete()
db.commit()
user.subscription_plan_id = pro.id
agent.manager_settings = {"winback": {"on": True, "days": 60, "per_day": 8}}
db.commit()
au.plan_marketing(db, NOON, force=True)
check("сценарий у ИИ-управляющего включён — правило «давно не были» молчит", not q(rule="winback"))
ov = cl.get("/api/beauty/automations", headers=HO).json()
check("экран: правило помечено «делает ИИ-управляющий»", next(x for x in ov["rules"] if x["key"] == "winback").get("covered_by_manager"))
user.subscription_plan_id = start_plan.id
agent.manager_settings = {}
db.commit()

# ── API ──
# свежие строки журнала: три подтверждения ушли, одно не доставлено, одно пропущено (нет мессенджера)
T_API = NOON + timedelta(hours=4)
for i in range(3):
    nq.enqueue(db, user_id=u.id, contact_id=aizhan.id, rule="confirm", text_=f"Вы записаны {i}", idem_key=f"t:api:{i}",
               send_after=T_API)
nq.enqueue(db, user_id=u.id, contact_id=c_none.id, rule="confirm", text_="Вы записаны", idem_key="t:api:none", send_after=T_API)
db.commit()
process(T_API)
r = cl.get("/api/beauty/automations", headers=HO)
d = r.json()
keys = [x["key"] for x in d["rules"]]
check("API: правила по порядку, «оценка» скрыта внутри «спасибо»", r.status_code == 200 and keys[:3] == ["confirm", "reminder_24h", "reminder_2h"]
      and "review" not in keys and "review_text_ru" in next(x for x in d["rules"] if x["key"] == "thanks"), keys)
check("API: статистика за 30 дней", next(x for x in d["rules"] if x["key"] == "confirm")["stats"]["sent"] >= 2)
r = cl.put("/api/beauty/automations/confirm", headers=HO, json={"template_ru": "Привет {фамилия}"})
check("API: неизвестная вставка — 400", r.status_code == 400 and "фамилия" in r.text)
r = cl.put("/api/beauty/automations/confirm", headers=HO, json={"template_ru": "Ждём вас[, {имя}], {когда}! [{адрес}]"})
check("API: свой текст сохранён", r.status_code == 200 and r.json()["rule"]["custom_ru"])
r = cl.post("/api/beauty/automations/confirm/preview", headers=HO, json={"template": "Ждём вас[, {имя}], {когда}!", "lang": "ru"})
check("API: пример текста — с данными примера", r.status_code == 200 and r.json()["text"].startswith("Ждём вас, Айжан, завтра в 15:00"),
      r.text[:200])
r = cl.post("/api/beauty/automations/confirm/preview", headers=HO, json={"template": "", "lang": "ky"})
check("API: пример по умолчанию по-кыргызски", r.status_code == 200 and "Саламатсызбы" in r.json()["text"])
r = cl.put("/api/beauty/automations/confirm", headers=HO, json={"template_ru": ""})
check("API: пустой текст — вернулся по умолчанию", not r.json()["rule"]["custom_ru"])
check("API: неизвестное правило — 400", cl.put("/api/beauty/automations/sms", headers=HO, json={"enabled": True}).status_code == 400)
acts = db.query(ActivityLog).filter(ActivityLog.user_id == u.id, ActivityLog.object_type == "automation").all()
check("журнал действий: изменения автоматизаций записаны", len(acts) >= 3 and any("текст" in a.summary for a in acts), [a.summary for a in acts][:3])
r = cl.get("/api/beauty/automations/queue?status=problems", headers=HO)
probs = r.json()["items"]
check("журнал отправок: «проблемы» — не доставлено и пропущено", r.status_code == 200 and probs
      and all(x["status"] in ("failed", "skipped") for x in probs))
r = cl.get("/api/beauty/automations/queue?status=sent&limit=2", headers=HO)
check("журнал отправок: страницы", len(r.json()["items"]) == 2 and r.json()["next"])
from urllib.parse import quote  # noqa: E402
r2 = cl.get("/api/beauty/automations/queue?status=sent&limit=2&before=" + quote(r.json()["next"]), headers=HO)
check("журнал отправок: следующая страница без повторов", not ({x["id"] for x in r.json()["items"]} & {x["id"] for x in r2.json()["items"]}))
bad = probs[0]["id"]
r = cl.post(f"/api/beauty/automations/queue/{bad}/retry", headers=HO)
check("«отправить ещё раз» — снова в очереди", r.status_code == 200 and q(id=uuid.UUID(bad))[0].status == "pending")
r = cl.post(f"/api/beauty/automations/queue/{bad}/cancel", headers=HO)
check("«не отправлять» — снято", r.status_code == 200 and q(id=uuid.UUID(bad))[0].status == "cancelled")
check("«не отправлять» отправленное — 400", cl.post(f"/api/beauty/automations/queue/{q(status='sent')[0].id}/cancel", headers=HO).status_code == 400)
other_item = nq.enqueue(db, user_id=free.id, contact_id=c_free.id, rule="thanks", text_="x", idem_key="t:other", send_after=NOON)
db.commit()
check("чужое сообщение — не трогаем", cl.post(f"/api/beauty/automations/queue/{other_item}/cancel", headers=HO).status_code == 400)

# ── сотрудники ──
mgr, _ = ts.create_member(db, user, name="Управляющий", email="mgr@kg.kg", password="secret12", role_title="Управляющий",
                          permissions=["booking", "booking_setup", "crm"])
crm_only, _ = ts.create_member(db, user, name="Админ", email="crm@kg.kg", password="secret12", role_title="Администратор",
                               permissions=["booking", "crm", "inbox"])
db.commit()


def sh(m):
    return {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(m.id), "stv": m.token_version})}


check("сотрудник «Мастера и график»: видит и меняет", cl.get("/api/beauty/automations", headers=sh(mgr)).status_code == 200
      and cl.put("/api/beauty/automations/no_show", headers=sh(mgr), json={"enabled": False}).status_code == 200)
check("сотрудник без «Мастера и график» — 403", cl.get("/api/beauty/automations", headers=sh(crm_only)).status_code == 403
      and cl.get("/api/beauty/automations/queue", headers=sh(crm_only)).status_code == 403)

# ── отказ от рассылок ──
r = cl.put(f"/api/agent/contacts/{rep.id}", headers=HO, json={"marketing_opt_out": True})
check("карточка: «не присылать поздравления и акции»", r.status_code == 200 and r.json()["marketing_opt_out"] is True)
nq.enqueue(db, user_id=u.id, contact_id=rep.id, rule="winback", text_="x", idem_key="t:opt", kind="marketing", send_after=NOON)
nq.enqueue(db, user_id=u.id, contact_id=rep.id, rule="reminder_24h", text_="Напоминаем", idem_key="t:opt-service", send_after=NOON)
db.commit()
process(NOON + timedelta(minutes=1))
check("отказавшемуся: рассылка — пропуск, напоминание о записи — ушло", q(idem_key="t:opt")[0].status == "skipped"
      and q(idem_key="t:opt-service")[0].status == "sent")
from backend.services import agent_tools  # noqa: E402
res = asyncio.run(agent_tools.fn_update_contact_info({"agent_contact_id": str(rep.id), "marketing_opt_out": False},
                                                     str(u.id), str(agent.id), db))
db.expire_all()
check("бот: клиент снова согласен на рассылки", res.get("ok") and db.query(AgentContact).get(rep.id).marketing_opt_out_at is None, res)

# ── чистка ──
db.execute(text("UPDATE notification_queue SET created_at = now() - interval '200 days' WHERE idem_key = 't:api:none'"))
db.commit()
removed = nq.cleanup(db, force=True)
check("чистка: старше 180 дней удалены, свежие на месте", removed == 1 and not q(idem_key="t:api:none") and q(idem_key="t:api:0"),
      removed)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
