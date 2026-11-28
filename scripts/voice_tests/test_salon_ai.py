"""ИИ-администратор салона (Voksy Beauty, шаг 1.6а).

Часть 1 (без базы): согласие клиента словами (ru/ky, эмодзи, «да, но…?», «нет, другое время»), разбор ленты
«итог показан → клиент ответил», нужны ли два шага (переписка — да; чат владельца и разбор звонка — нет),
описания инструментов, блок промпта, Beauty Pro = 10 000 кредитов.

Часть 2 (PostgreSQL, SALON_AI_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА) — через настоящий execute_tool агента:
- create_booking в переписке: первый вызов НЕ пишет запись, проверяет время пробной записью, отдаёт итог
  (услуга, мастер, время, цена, предоплата) и confirmation_id; уведомление журнала не уходит;
- без отправленного итога, без ответа клиента, на «нет, другое время», на вопрос — не выполняется;
- клиент ответил «да» после итога → запись есть; повтор с тем же id — отказ (итог использован);
- id забыт → после «да» хватает confirmation_id из блока «ЖДЁТ ПОДТВЕРЖДЕНИЯ» в разборе;
- время заняли, пока клиент думал → понятный отказ, двойной записи нет;
- визит из двух услуг: свободное время всего визита, запись обеих услуг одним «да»;
- перенос и отмена в два шага; правило «не позже чем за N ч» → отказ и передача администратору;
- чат владельца: запись сразу, без подтверждения;
- get_services / get_staff / get_customer_history / get_salon_information из базы;
- send_staff_portfolio: фото работ мастера уходят в мессенджер клиента, копии не видны в библиотеке файлов;
- инструменты салона в наборе бота только при включённой записи.
"""
import asyncio
import io
import json
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("SALON_AI_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="salon-ai-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import booking_confirm as bc  # noqa: E402
from backend.services import booking_tools, salon_ai_tools, plan_catalog as pc  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
yes = ["да", "Да, записывайте", "ок", "Окей 👍", "👍", "записывайте", "Ооба", "макул", "жарайт", "хорошо",
       "Давайте", "подходит", "да конечно", "Туура, жазыңыз", "супер, договорились"]
no = ["нет", "Нет, давайте другое время", "не подходит", "подождите", "керек эмес", "передумала",
      "можно попозже?", "давайте другой день"]
unclear = ["а сколько стоит?", "Да, а можно на 16:00 вместо этого?", "", "Мен ойлонуп көрөйүн, бир аз кийин жазам"]
check("согласие клиента: да / ок / ооба / макул / 👍 …", all(bc.client_consent(t) == "yes" for t in yes),
      [t for t in yes if bc.client_consent(t) != "yes"])
check("отказ: нет / другое время / подождите / керек эмес …", all(bc.client_consent(t) == "no" for t in no),
      [t for t in no if bc.client_consent(t) != "no"])
check("не согласие: вопрос, «да, а можно…?», пусто", all(bc.client_consent(t) is None for t in unclear),
      [(t, bc.client_consent(t)) for t in unclear if bc.client_consent(t) is not None])


def msgs(*pairs):
    return [{"direction": d, "sent_by": None, "body": b, "created_at": None} for d, b in pairs]


check("лента: итог не отправлен → not_shown", bc.consent_state(msgs(("inbound", "да")))[0] == "not_shown")
check("лента: итог отправлен, ответа нет → wait", bc.consent_state(msgs(("outbound", "Итог: …")))[0] == "wait")
check("лента: «да» после итога → yes", bc.consent_state(msgs(("outbound", "Итог"), ("inbound", "да")))[0] == "yes")
check("лента: «да», потом «нет, в субботу» → no",
      bc.consent_state(msgs(("outbound", "Итог"), ("inbound", "да"), ("inbound", "нет, давайте другой день")))[0] == "no")
check("лента: вопрос после итога → unclear",
      bc.consent_state(msgs(("outbound", "Итог"), ("inbound", "а парковка есть?")))[0] == "unclear")
check("лента: «да» ДО итога не считается",
      bc.consent_state(msgs(("inbound", "да"), ("outbound", "Итог")))[0] == "wait")
check("два шага: только в разборе переписки",
      bc.needs_client_confirmation({"surface": "postcall", "call_direction": "whatsapp_inbound"})
      and bc.needs_client_confirmation({"surface": "postcall", "call_direction": "instagram_inbound"})
      and not bc.needs_client_confirmation({"surface": "postcall", "call_direction": "inbound"})
      and not bc.needs_client_confirmation({"surface": "chat"}))
names = {t["name"] for t in booking_tools.TOOLS} | {t["name"] for t in salon_ai_tools.TOOLS}
check("инструменты: запись + салон (из ТЗ)", {"find_booking_slots", "create_booking", "reschedule_booking",
      "cancel_booking", "list_bookings", "get_services", "get_staff", "get_customer_history",
      "get_salon_information", "send_staff_portfolio"} <= names)
cb = next(t for t in booking_tools.TOOLS if t["name"] == "create_booking")
check("create_booking: confirmation_id и несколько услуг, start не обязателен (второй шаг)",
      "confirmation_id" in cb["parameters"]["properties"] and "services" in cb["parameters"]["properties"]
      and "start" not in cb["parameters"].get("required", []))
check("промпт записи: два шага и только из инструментов", "ДВА шага" in booking_tools.PROMPT_BLOCK
      and "confirmation_id" in booking_tools.PROMPT_BLOCK and "get_customer_history" in salon_ai_tools.PROMPT_BLOCK)
from backend.services import agent_mode  # noqa: E402
from backend.services.agent_orchestrator import CLIENT_REPLY_TOOLS  # noqa: E402
check("фото работ мастера — отправка клиенту (пауза бота её останавливает)",
      "send_staff_portfolio" in agent_mode.SEND_TOOLS and "send_staff_portfolio" in CLIENT_REPLY_TOOLS)
check("Beauty Pro: 10 000 кредитов в месяц", pc.BEAUTY_DEFAULTS[2]["limits"]["credits_month"] == 10000)

# Где салон настраивает агента (27.11.2026: владелец не находил агента обзвона у салона)
ROOT_ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def src(rel):
    return open(os.path.join(ROOT_, rel), encoding="utf-8").read()


sb = src("backend/static/js/sidebar.js")
check("меню салона без Beauty Pro: «Агент обзвона» не прячется, а с замком «Beauty Pro»",
      "beautyLock: true" in sb and "#plans:beauty_pro" in sb and "'Beauty Pro'" in sb
      and "if (item.profi && biz && biz.beauty && !profi) return;" not in sb)
dh = src("backend/static/dashboard.html")
check("ссылка #plans:<код> открывает выбор тарифа на этом тарифе", "#plans(?::([a-z0-9_]+))?$" in dh)
ob, wz, sm = src("backend/static/agent/onboarding.js"), src("backend/static/agent/wizard.js"), src("backend/static/agent/salon-mode.js")
check("Beauty Pro без агента: салонная карусель (запись, звонки, память), а не «обзвон продаж»",
      "OB_SLIDES_SALON" in ob and "obSlides()" in ob and "Записывает по вашему журналу" in ob and "OB_SLIDES_SALON.length" not in ob)
check("мастер создания у салона: салонные поля, «Что предлагаем» можно пропустить, без «тарифа Profi»",
      "WIZ_DOCS_SALON" in wz and "optional:true" in wz and "!s.optional" in wz and "if(wizSalon())" in wz)
check("салонный вид переодевает уже открытую карусель и мастер", "renderOnboarding()" in sm and "renderWizard()" in sm)
bj = src("backend/static/start/bot.js")
check("«Чат-бот» салона: примеры про салон и где агент обзвона (Beauty Pro)",
      "DOCS_SALON_PH" in bj and "proNote()" in bj and "#plans:beauty_pro" in bj)

if not PG:
    print("SKIP часть 2: SALON_AI_TEST_DATABASE_URL не задан")
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
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.models.agent_whatsapp import AgentWhatsAppMessage  # noqa: E402
from backend.models.agent_file import AgentFile  # noqa: E402
from backend.models.booking import Booking, BookingConfirmation  # noqa: E402
from backend.services import booking_service as bs, agent_tools, salon_media  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
app_module.ensure_booking_tables()
cl = TestClient(app_module.app, raise_server_exceptions=False)
B = bs.BISHKEK
db = SessionLocal()

d0 = datetime.now(B).date() + timedelta(days=2)
while d0.weekday() != 2:
    d0 += timedelta(days=1)
WED = d0.isoformat()
THU = (d0 + timedelta(days=1)).isoformat()


def at(day_iso, hm):
    return datetime.combine(date.fromisoformat(day_iso), time.fromisoformat(hm), B).isoformat()


plan = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p",
                        is_active=True, product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
db.add(plan)
db.flush()
u = User(email="salon-ai@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
         subscription_plan_id=plan.id, subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20),
         business_niche="beauty_salon", business_profile={"answers": {}})
db.add(u)
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор")
db.add(agent)
db.flush()
from backend.services.business_profile import apply_to_agent  # noqa: E402
apply_to_agent(db, u, agent)
nails = AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Маникюр с покрытием", body="Снятие, форма, покрытие",
                            price=1200, price_to=1500, duration_min=90, collection="Ногти / Маникюр",
                            prepay={"type": "fixed", "value": 300})
pedi = AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Педикюр", body="",
                           price=1500, duration_min=60, collection="Ногти / Педикюр")
cut = AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Женская стрижка", body="",
                          price=1000, duration_min=60, collection="Волосы")
client = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Нурай", phone="+996555111222", status="new")
other = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Чужой", phone="+996555000999", status="new")
db.add_all([nails, pedi, cut, client, other])
db.commit()
H = {"Authorization": "Bearer " + __import__("backend.core.security", fromlist=["x"]).create_jwt_token(u.id)}
ov = cl.get(f"/api/booking/overview?date={WED}&days=2", headers=H).json()
m_main = ov["resources"][0]["id"]
cl.patch(f"/api/booking/resources/{m_main}", headers=H, json={"name": "Айгуль", "role": "Мастер маникюра",
         "experience_since": datetime.now().year - 7, "bio": "Нежные нюдовые оттенки и укрепление",
         "schedule": {d: [["09:00", "19:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat")},
         "service_prices": {str(nails.id): {"price": 1400}}})
r = cl.post("/api/booking/resources", headers=H, json={"name": "Бегимай", "role": "Стилист",
            "schedule": {d: [["10:00", "18:00"]] for d in ("wed", "thu")}, "service_ids": [str(cut.id)]})
m_hair = r.json()["resource"]["id"]
db.expire_all()


def ctx(direction="whatsapp_inbound", contact=client, surface="postcall"):
    return {"agent_config_id": str(agent.id), "user_id": str(u.id), "user": db.query(User).get(u.id),
            "agent_config": agent, "surface": surface,
            "current_contact_id": str(contact.id) if contact else None, "call_direction": direction}


def run(tool, args, c=None):
    out = asyncio.run(agent_tools.execute_tool(tool, args, c or ctx(), db))
    return json.loads(out)


def say(direction, body, contact=client, minutes_later=0):
    db.add(AgentWhatsAppMessage(agent_config_id=agent.id, agent_contact_id=contact.id, direction=direction, body=body,
                                created_at=datetime.utcnow() + timedelta(minutes=minutes_later, seconds=1)))
    db.commit()


def count_bookings(contact=client):
    db.expire_all()
    return db.query(Booking).filter(Booking.contact_id == contact.id, Booking.status != "cancelled").count()


# ── поиск времени и первый шаг записи
s = run("find_booking_slots", {"service": "маникюр", "date": WED})
check("find_booking_slots: варианты и цена мастера", s.get("ok") and s["slots"] and s["slots"][0].get("price") == 1400,
      str(s)[:200])
import psycopg2  # noqa: E402
listen = psycopg2.connect(PG)          # своё соединение, не из пула (автокоммит не должен вернуться в пул)
listen.set_isolation_level(0)
cur = listen.cursor()
cur.execute("LISTEN voksy_booking")
first = run("create_booking", {"start": at(WED, "11:00"), "service": "маникюр"})
listen.poll()
note_count = len(listen.notifies)
check("первый шаг: запись НЕ создана, есть итог и confirmation_id",
      first.get("needs_confirmation") and first.get("confirmation_id") and count_bookings() == 0, str(first)[:300])
check("итог: услуга, мастер, время, цена мастера, предоплата",
      "Маникюр с покрытием" in first["summary"] and "Айгуль" in first["summary"] and "11:00" in first["summary"]
      and "1 400" in first["summary"] and "300" in first["summary"] and first.get("prepay", {}).get("amount") == 300,
      first.get("summary"))
check("пробная запись не будит журналы (pg_notify откатился)", note_count == 0, note_count)
cur.close()
listen.close()
cid = first["confirmation_id"]

r = run("create_booking", {"confirmation_id": cid})
check("итог ещё не отправлен клиенту → не выполнено", not r.get("ok") and "отправь клиенту итог" in r["error"]
      and count_bookings() == 0, r)
say("outbound", "Записываю вас: маникюр с покрытием, Айгуль, ср 11:00, 1 400 сом, предоплата 300. Верно?")
r = run("create_booking", {"confirmation_id": cid})
check("клиент ещё не ответил → не выполнено", not r.get("ok") and "не ответил" in r["error"] and count_bookings() == 0, r)
say("inbound", "а парковка у вас есть?", minutes_later=1)
r = run("create_booking", {"confirmation_id": cid})
check("вопрос вместо ответа → не выполнено, бот отвечает и переспрашивает",
      not r.get("ok") and "не ясно" in r["error"] and count_bookings() == 0, r)
r = run("create_booking", {"confirmation_id": cid}, ctx(contact=other))
check("чужой клиент не может подтвердить чужой итог", not r.get("ok") and count_bookings() == 0, r)
say("inbound", "Да, записывайте 👍", minutes_later=2)
r = run("create_booking", {"confirmation_id": cid})
check("«да» после итога → запись создана (мастер и время из итога)", r.get("ok") and count_bookings() == 1
      and r["booking"]["resource"] == "Айгуль" and r["booking"]["label"].endswith("11:00") and r["booking"]["source"] == "bot",
      str(r)[:300])
check("ответ: предоплата и как внести", r.get("prepay", {}).get("amount") == 300 and r["prepay"]["how"], r.get("prepay"))
r = run("create_booking", {"confirmation_id": cid})
check("повтор с тем же id — отказ (итог использован)", not r.get("ok") and count_bookings() == 1, r)

# ── отказ клиента
first = run("create_booking", {"start": at(THU, "15:00"), "service": "Педикюр"})
check("педикюр: итог", first.get("needs_confirmation"), first)
say("outbound", "Педикюр, чт 15:00, 1 500 сом. Записываю?", minutes_later=3)
say("inbound", "Нет, давайте другое время", minutes_later=4)
r = run("create_booking", {"confirmation_id": first["confirmation_id"]})
check("«нет, другое время» → не выполнено, подобрать новое", not r.get("ok") and "НЕ согласился" in r["error"]
      and count_bookings() == 1, r)

# ── бот забыл id: блок в разборе + «да»
first = run("create_booking", {"start": at(THU, "16:00"), "service": "Педикюр"})
block = bc.pending_block(db, u.id, client.id)
check("разбор переписки видит, что ждёт «да» (и id)", first["confirmation_id"] in block and "Педикюр" in block, block)
db.query(BookingConfirmation).filter(BookingConfirmation.id == uuid.UUID(first["confirmation_id"])).update(
    {BookingConfirmation.created_at: datetime.now(timezone.utc) + timedelta(minutes=4, seconds=30)})
db.commit()
say("outbound", "Педикюр, чт 16:00. Верно?", minutes_later=5)
say("inbound", "ооба", minutes_later=6)
r = run("create_booking", {"confirmation_id": first["confirmation_id"]})
check("кыргызское «ооба» → запись", r.get("ok") and count_bookings() == 2, str(r)[:200])

# ── время заняли, пока клиент думал
first = run("create_booking", {"start": at(THU, "11:00"), "service": "маникюр"})
db.query(BookingConfirmation).filter(BookingConfirmation.id == uuid.UUID(first["confirmation_id"])).update(
    {BookingConfirmation.created_at: datetime.now(timezone.utc) + timedelta(minutes=6, seconds=30)})
db.commit()
bs.create_booking(db, db.query(User).get(u.id), start=at(THU, "11:00"), service_id=str(nails.id),
                  resource_id=m_main, client_name="Позвонила сама", source="owner")
db.commit()
say("outbound", "Маникюр чт 11:00?", minutes_later=7)
say("inbound", "да", minutes_later=8)
r = run("create_booking", {"confirmation_id": first["confirmation_id"]})
check("время заняли → понятный отказ, двойной записи нет", not r.get("ok") and "занят" in r["error"].lower()
      and count_bookings() == 2, r)

# ── визит из двух услуг
s = run("find_booking_slots", {"services": ["маникюр", "педикюр"], "date": THU})
check("свободное время всего визита (маникюр + педикюр)", s.get("ok") and s.get("visit") and s["slots"]
      and s["slots"][0].get("end_time"), str(s)[:250])
first = run("create_booking", {"start": s["slots"][0]["start"], "services": ["маникюр", "педикюр"]})
check("итог визита — обе услуги", first.get("needs_confirmation") and "Маникюр" in first["summary"]
      and "Педикюр" in first["summary"], first.get("summary"))
db.query(BookingConfirmation).filter(BookingConfirmation.id == uuid.UUID(first["confirmation_id"])).update(
    {BookingConfirmation.created_at: datetime.now(timezone.utc) + timedelta(minutes=8, seconds=30)})
db.commit()
say("outbound", "Визит: маникюр и педикюр. Записываю?", minutes_later=9)
say("inbound", "Да", minutes_later=10)
r = run("create_booking", {"confirmation_id": first["confirmation_id"]})
check("визит записан одним «да»: две записи, общий визит", r.get("ok") and len(r.get("bookings", [])) == 2
      and r["bookings"][0]["visit_id"] and r["bookings"][0]["visit_id"] == r["bookings"][1]["visit_id"], str(r)[:300])
visit_id = r["bookings"][0]["visit_id"]

# ── перенос в два шага
first_b = db.query(Booking).filter(Booking.contact_id == client.id, Booking.status != "cancelled").order_by(Booking.start_at).first()
r1 = run("reschedule_booking", {"booking_id": str(first_b.id), "new_start": at(WED, "15:00")})
db.expire_all()
check("перенос, шаг 1: итог «было → стало», запись не тронута",
      r1.get("needs_confirmation") and "→" in r1["summary"] and "15:00" in r1["summary"]
      and bs.to_local(db.query(Booking).get(first_b.id).start_at).strftime("%H:%M") == "11:00", str(r1)[:300])
db.query(BookingConfirmation).filter(BookingConfirmation.id == uuid.UUID(r1["confirmation_id"])).update(
    {BookingConfirmation.created_at: datetime.now(timezone.utc) + timedelta(minutes=10, seconds=30)})
db.commit()
say("outbound", "Переношу на 15:00?", minutes_later=11)
say("inbound", "ок", minutes_later=12)
r = run("reschedule_booking", {"confirmation_id": r1["confirmation_id"]})
db.expire_all()
check("перенос после «ок»", r.get("ok") and bs.to_local(db.query(Booking).get(first_b.id).start_at).strftime("%H:%M") == "15:00", r)

# ── отмена: правило «за N часов» и в два шага
page = bs.get_page(db, db.query(User).get(u.id))
page.settings = dict(page.settings or {}, cancel_hours=24 * 7)
db.commit()
r = run("cancel_booking", {"booking_id": str(first_b.id)})
check("правило салона: поздно отменять → отказ и передать администратору",
      not r.get("ok") and "администратор" in r["error"] and count_bookings() == 4, r)
page.settings = dict(page.settings or {}, cancel_hours=2)
db.commit()
r1 = run("cancel_booking", {"booking_id": str(first_b.id), "reason": "заболела"})
check("отмена, шаг 1: итог, запись цела", r1.get("needs_confirmation") and "отмена" in r1["summary"]
      and count_bookings() == 4, str(r1)[:200])
db.query(BookingConfirmation).filter(BookingConfirmation.id == uuid.UUID(r1["confirmation_id"])).update(
    {BookingConfirmation.created_at: datetime.now(timezone.utc) + timedelta(minutes=12, seconds=30)})
db.commit()
say("outbound", "Отменяю запись на ср 15:00?", minutes_later=13)
say("inbound", "да, отмените", minutes_later=14)
r = run("cancel_booking", {"confirmation_id": r1["confirmation_id"]})
check("отмена после «да»", r.get("ok") and count_bookings() == 3, r)
vb = db.query(Booking).filter(Booking.visit_id == uuid.UUID(visit_id)).first()
r1 = run("cancel_booking", {"booking_id": str(vb.id)})
check("отмена части визита предлагает отменить весь визит", r1.get("needs_confirmation")
      and "Маникюр" in r1["summary"] and "Педикюр" in r1["summary"], r1.get("summary"))

# ── чат владельца и разбор звонка — сразу
r = run("create_booking", {"start": at(WED, "16:30"), "service": "стрижка", "agent_contact_id": str(other.id)},
        ctx(contact=None, surface="chat", direction=None))
check("чат владельца: запись сразу, без подтверждения", r.get("ok") and not r.get("needs_confirmation")
      and r["booking"]["resource"] in ("Айгуль", "Бегимай"), str(r)[:200])
r = run("create_booking", {"start": at(THU, "17:00"), "service": "стрижка"}, ctx(direction="inbound", contact=other))
check("разбор звонка (клиент согласился голосом): сразу", r.get("ok") and not r.get("needs_confirmation"), str(r)[:200])

# ── инструменты салона
sv = run("get_services", {"query": "маникюр"})
check("get_services: прайс из базы — диапазон, цена мастера, предоплата, категория",
      sv.get("ok") and sv["services"] and sv["services"][0]["price_text"] == "1 200–1 500 сом"
      and sv["services"][0]["master_prices"][0]["price"] == 1400 and "300" in sv["services"][0]["prepay"]
      and sv["services"][0]["category"] == "Ногти / Маникюр", str(sv)[:300])
sv2 = run("get_services", {"query": "ботокс"})
check("get_services: нет услуги — не выдумывать", sv2.get("ok") and not sv2["services"] and "не выдумывай" in sv2["note"])
st = run("get_staff", {})
check("get_staff: мастера, специализация, опыт", st.get("ok") and {m["name"] for m in st["masters"]} == {"Айгуль", "Бегимай"}
      and next(m for m in st["masters"] if m["name"] == "Айгуль")["experience_years"] == 7, str(st)[:300])
st = run("get_staff", {"service": "стрижка"})
check("get_staff по услуге: только кто делает", st.get("ok") and [m["name"] for m in st["masters"]] == ["Айгуль", "Бегимай"]
      or [m["name"] for m in st["masters"]] == ["Бегимай", "Айгуль"], [m["name"] for m in st.get("masters", [])])
for b in db.query(Booking).filter(Booking.contact_id == client.id, Booking.status != "cancelled").all():
    b.status = "completed"
db.commit()
h = run("get_customer_history", {})
check("get_customer_history: визиты, любимый мастер и услуги, сумма", h.get("ok") and h["visits"] == 3
      and h["favorite_master"] == "Айгуль" and "Педикюр" in h["favorite_services"] and h["spent_text"], str(h)[:300])
h2 = run("get_customer_history", {"agent_contact_id": str(other.id)})
check("в переписке история только текущего клиента", h2.get("visits") == 3, h2.get("visits"))
cl.put("/api/booking/page", headers=H, json={"address": "Бишкек, Киевская 95", "phone": "+996700123456",
       "instagram": "@aidana.salon", "amenities": ["wifi", "parking"], "tagline": "Ногти и волосы"})
cl.put("/api/booking/settings", headers=H, json={"settings": {"address": "Бишкек, Киевская 95", "prepay_info": "Mbank +996700123456"}})
info = run("get_salon_information", {})
check("get_salon_information: адрес, карта, часы, контакты, правила",
      info.get("ok") and info.get("address") == "Бишкек, Киевская 95" and info.get("map_url") and info.get("hours")
      and info.get("instagram", "").endswith("aidana.salon") and info.get("prepay_how") and info.get("cancel_rule")
      and info.get("page_url", "").startswith("https://t.voksyai.online/beauty/"), str(info)[:400])

# ── фото работ мастера
from PIL import Image  # noqa: E402


def jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), color).save(buf, "JPEG")
    return buf.getvalue()


ids = []
for c in ("red", "green", "blue", "pink", "white"):
    row = salon_media.store(db, u.id, "portfolio", jpeg(c), resource_id=uuid.UUID(m_main))
    ids.append({"id": str(row.id), "caption": f"Работа {c}"})
res_row = db.query(__import__("backend.models.booking", fromlist=["x"]).BookingResource).get(uuid.UUID(m_main))
res_row.portfolio = ids
db.commit()
from backend.services.r2_storage import R2StorageService  # noqa: E402
store = {}
R2StorageService.objects_available = staticmethod(lambda: True)


async def _put(key, data, mime=None):
    store[key] = data
    return True
R2StorageService.put_object = staticmethod(_put)
sent = []


async def _wa_file(args, user_id, agent_config, db_):
    sent.append(args)
    return {"ok": True}
agent_tools.fn_whatsapp_send_file = _wa_file
r = run("send_staff_portfolio", {"master": "айгуль"})
check("send_staff_portfolio: 4 фото в WhatsApp, первое с подписью «Работы мастера Айгуль … опыт 7 лет»",
      r.get("ok") and r["sent_photos"] == 4 and len(sent) == 4 and "Работы мастера Айгуль" in sent[0]["caption"]
      and "7 лет" in sent[0]["caption"], (r, [s.get("caption") for s in sent]))
sent.clear()
r = run("send_staff_portfolio", {"master": "Айгуль", "count": 2})
copies = db.query(AgentFile).filter(AgentFile.source == "salon").count()
check("повторная отправка берёт те же копии (не плодит файлы)", r.get("ok") and copies == 4 and len(sent) == 2, copies)
lib = cl.get("/api/agent/files?source=all", headers=H).json()
check("копии фото не видны в библиотеке файлов агента", not any(f.get("source") == "salon" for f in lib.get("files", [])),
      str(lib)[:200])
r = run("send_staff_portfolio", {"master": "Бегимай"})
check("нет фото у мастера — расскажи словами", not r.get("ok") and "словами" in r["error"], r)

# ── отчёт салона владельцу
past = db.query(Booking).filter(Booking.contact_id == client.id).order_by(Booking.start_at).all()
for b in past:
    shift = timedelta(days=80)
    b.start_at, b.end_at = b.start_at - shift, b.end_at - shift
db.commit()
rep = run("get_salon_report", {"period": "quarter"}, ctx(contact=None, surface="chat", direction=None))
check("get_salon_report: записи, выручка, мастера, услуги",
      rep.get("ok") and rep["bookings"]["came"] >= 3 and rep["bookings"]["revenue_text"].endswith("сом")
      and any(m["name"] == "Айгуль" for m in rep["masters"]) and rep["top_services"], str(rep)[:400])
check("get_salon_report: давно не приходила — Нурай (визиты, последняя услуга, мастер)",
      any(x["name"] == "Нурай" and x["visits"] == 3 and x["last_master"] == "Айгуль" and x["days_since"] >= 45
          for x in rep["lapsed_clients"]), rep.get("lapsed_clients"))
r = run("get_salon_report", {}, ctx())
check("отчёт салона клиенту в переписке не отдаётся", not r.get("ok"), r)
chat_tools = asyncio.run(agent_tools.build_chat_tools(agent, db))
ctn = {(t.get("function") or {}).get("name") for t in chat_tools}
pc_tools = asyncio.run(agent_tools.build_postcall_tools(agent, db))
ptn = {(t.get("function") or {}).get("name") for t in pc_tools}
check("отчёт салона: в чате владельца есть, в разборе переписки нет", "get_salon_report" in ctn and "get_salon_report" not in ptn)

# ── команды владельца с телефона (28.11.2026): закрыть время, расход, пустые окна
OWN = ctx(contact=None, surface="chat", direction=None)
check("команды владельца: в чате есть, в разборе переписки нет",
      {"block_master_time", "add_expense", "offer_free_slots"} <= ctn
      and not ({"block_master_time", "add_expense", "offer_free_slots"} & ptn))
r = run("block_master_time", {"master": "Айгуль"}, ctx())
check("закрыть время клиенту в переписке нельзя", not r.get("ok"), r)
from backend.models.booking import BookingBlock  # noqa: E402
r = run("block_master_time", {"master": "Бегимай", "date_from": THU, "kind": "sick", "note": "заболела"}, OWN)
check("мастер заболел: весь день закрыт", r.get("ok") and r.get("master") == "Бегимай" and "весь день" in r.get("when", ""), r)
db.expire_all()
blk = db.query(BookingBlock).filter(BookingBlock.user_id == u.id, BookingBlock.kind == "sick").first()
check("блок в базе: у мастера, с начала до конца дня", blk is not None and str(blk.resource_id) == m_hair
      and bs.to_local(blk.start_at).hour == 0 and (blk.end_at - blk.start_at).total_seconds() == 86400, blk)
slots = run("find_booking_slots", {"service": "Женская стрижка", "date": THU, "master": "Бегимай"}, OWN)
check("в закрытый день к мастеру не записать",
      not [x for x in slots.get("slots") or [] if bs.to_local(bs.parse_start(x["start"])).date().isoformat() == THU], slots)
r = run("block_master_time", {"date_from": WED, "time_from": "14:00", "time_to": "16"}, OWN)
check("закрыть время всего салона 14–16", r.get("ok") and r.get("master") == "весь салон" and "14:00–16:00" in r.get("when", ""), r)
r = run("block_master_time", {"master": "Нет-такого", "date_from": WED}, OWN)
check("неизвестный мастер — понятный отказ", not r.get("ok") and "мастер" in r.get("error", "").lower(), r)
r = run("block_master_time", {"date_from": WED, "time_from": "14:00"}, OWN)
check("только «с» без «до» — отказ", not r.get("ok"), r)
r = run("add_expense", {"amount": 3000, "title": "Краска", "category": "supplies"}, OWN)
check("расход записан", r.get("ok") and r.get("amount") == 3000 and r.get("title") == "Краска" and not r.get("monthly"), r)
r = run("add_expense", {"amount": 40000, "title": "Аренда", "category": "rent", "monthly": True}, OWN)
check("ежемесячный расход", r.get("ok") and r.get("monthly"), r)
r = run("add_expense", {"amount": 0}, OWN)
check("расход без суммы — отказ", not r.get("ok"), r)
from backend.models.activity import ActivityLog  # noqa: E402
acts = db.query(ActivityLog).filter(ActivityLog.user_id == u.id, ActivityLog.action.in_(("block.create", "expense.create"))).all()
check("журнал действий: «ИИ по команде владельца»", acts and all("владельца" in (a.actor_name or "") for a in acts),
      [(a.action, a.actor_name) for a in acts])
r = run("offer_free_slots", {"master": "Айгуль", "day": WED}, OWN)
check("пустые окна: ответ с числом предложений", r.get("ok") and r.get("master") == "Айгуль" and "proposed" in r, r)

# ── история чата для экрана «AI» на телефоне
ag = db.query(AgentConfig).get(agent.id)
ag.chat_history = [{"role": "user", "content": "Итоги дня", "ts": "2026-11-28T10:00:00"},
                   {"role": "assistant", "content": "Сегодня 3 записи", "ts": "2026-11-28T10:00:05"},
                   {"role": "tool", "content": "x"}, {"role": "assistant", "content": "🎙 (голосом) ок", "voice": True}]
db.commit()
r = cl.get("/api/agent/chat/history", headers=H)
msgs = r.json().get("messages") if r.status_code == 200 else None
check("GET /chat/history: реплики владельца и агента, служебные — нет", msgs is not None and [m["role"] for m in msgs] ==
      ["user", "assistant", "assistant"] and msgs[2]["voice"] is True, r.text[:300])
check("/chat/history без входа — 401/403", cl.get("/api/agent/chat/history").status_code in (401, 403))

# ── набор инструментов бота
tools = asyncio.run(agent_tools.build_postcall_tools(agent, db))
tn = {(t.get("function") or {}).get("name") for t in tools}
check("набор бота салона: запись + инструменты салона", {"create_booking", "get_services", "get_staff",
      "get_customer_history", "get_salon_information", "send_staff_portfolio"} <= tn)
fresh = db.query(User).get(u.id)
fresh.business_profile = {"answers": {}, "modules": {"booking": False}}
db.commit()
from backend.services.booking_tools import booking_enabled  # noqa: E402
if not booking_enabled(db, fresh):
    tools = asyncio.run(agent_tools.build_postcall_tools(agent, db))
    tn = {(t.get("function") or {}).get("name") for t in tools}
    check("запись выключена — инструментов салона нет", "get_services" not in tn and "create_booking" not in tn)
else:
    check("запись выключена — модуль выключился", False, fresh.business_profile)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
