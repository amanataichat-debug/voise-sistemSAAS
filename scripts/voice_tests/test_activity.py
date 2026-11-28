"""Журнал действий и главная салона (Voksy Beauty, шаг 1.7).

Часть 1 (без базы): «кто сделал» (владелец, сотрудник, вход поддержки, бот, звонок, клиент, явный acting),
было → стало, график словами, права сотрудника (журнал — раздел «activity», у роли «Управляющий»; администратор
и мастер — нет), страница журнала, пункт меню, значки есть в спрайте.

Часть 2 (PostgreSQL, ACTIVITY_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- каждый вход пишет строку с правильным «кто»: владелец (кабинет), сотрудник (имя, IP), мастер из своего кабинета,
  бот в чате владельца, голосовой агент, клиент по ссылке, вход поддержки Voksy (действие + входы из admin_audit_log);
- запись: создана, перенесена (было → стало), статус, предоплата, отмена; визит одной строкой; блоки времени;
  услуги и цены, мастер и график, правила записи, страница салона и чат, сотрудники, расходы и зарплата,
  решения ИИ-управляющего;
- откат действия (занятое время, пробная запись бота в два шага) — строки нет;
- лента: фильтры (кто, что, период), страницы без повторов, история одной записи вместе с её визитом;
- доступ: владелец (с IP), управляющий (без IP), администратор и мастер — 403;
- чистка: строки старше 180 дней удаляются, свежие остаются;
- главная салона: сейчас в салоне, требует внимания (не отмечены, завтра без подтверждения, предоплата, одобрение,
  новые без вас, низкая оценка), свободные окна, загрузка мастеров, последние действия; «Предложить клиентам» —
  предложения на одобрение (Beauty Pro), на Beauty Start — отказ; сотруднику главная не отдаётся.
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
PG = os.environ.get("ACTIVITY_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="activity-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import activity  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
class U:
    id = uuid.uuid4()
    first_name = "Айдана"
    last_name = None


u0 = U()
check("кто: владелец по имени", activity.actor_for(u0) == {"kind": "owner", "id": None, "name": "Айдана"})
u1 = U()
u1._staff_member_id, u1._staff = "m-1", {"name": "Айгуль"}
check("кто: сотрудник — имя и id", activity.actor_for(u1) == {"kind": "staff", "id": "m-1", "name": "Айгуль"})
u2 = U()
u2._impersonator_id = "adm-1"
check("кто: вход поддержки Voksy главнее", activity.actor_for(u2)["kind"] == "admin" and activity.actor_for(u2)["id"] == "adm-1")
check("кто: бот / звонок / клиент по ссылке",
      activity.actor_for(u0, "bot")["kind"] == "ai" and activity.actor_for(u0, "call")["kind"] == "call"
      and activity.actor_for(u0, "online", "Нурай") == {"kind": "client", "id": None, "name": "Нурай"})
with activity.acting("ai", "ИИ по команде владельца"):
    inner = activity.actor_for(u1, "online")
check("кто: явный acting главнее всего", inner == {"kind": "ai", "id": None, "name": "ИИ по команде владельца"})
check("кто: после acting — снова как было", activity.actor_for(u1)["kind"] == "staff")
check("кто: без пользователя — система", activity.actor_for(None)["kind"] == "system")
check("было → стало: только изменившиеся", activity.diff({"a": 1, "b": 2, "c": None}, {"a": 1, "b": 3, "c": None}) == {"b": [2, 3]})
dt = datetime(2026, 11, 26, 5, 0, tzinfo=timezone.utc)
check("было → стало: дата — ISO", activity.diff({"s": None}, {"s": dt}) == {"s": [None, dt.isoformat()]})
check("время по Бишкеку словами", activity.when(dt) == "чт 26.11 11:00", activity.when(dt))
check("деньги словами", activity.money(1400) == "1 400 сом" and activity.money(None) == "—")
sched = {d: [["09:00", "19:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}
sched["sat"] = [["10:00", "16:00"]]
check("график словами", bs.schedule_text(sched) == "пн–пт 09:00–19:00, сб 10:00–16:00, вс выходной", bs.schedule_text(sched))
check("права: журнал — раздел «Журнал действий»",
      ts.staff_allowed(["activity"], "GET", "/api/beauty/activity") and not ts.staff_allowed(["booking", "booking_setup", "finance"], "GET", "/api/beauty/activity")
      and not ts.staff_allowed(["booking_own"], "GET", "/api/beauty/activity"))
check("права: главная салона сотруднику закрыта", not ts.staff_allowed(ts.PERM_KEYS, "GET", "/api/beauty/home"))
check("раздел activity есть, страница — журнал", "activity" in ts.PERM_KEYS
      and ("activity", "/static/activity.html") in [(k, h) for k, h, *_ in ts.SECTION_PAGES])
team_js = open(os.path.join(ROOT, "backend/static/team/team.js"), encoding="utf-8").read()
check("роль «Управляющий» видит журнал, «Администратор» — нет",
      re.search(r"id: 'manager'[^\n]*'activity'", team_js) is not None and re.search(r"id: 'admin'[^\n]*'activity'", team_js) is None)
side = open(os.path.join(ROOT, "backend/static/js/sidebar.js"), encoding="utf-8").read()
check("меню: «Журнал действий» у ниш с записью (оба меню)", side.count("activity-nav-item") == 2 and "sidebar.activity" in side)
html = open(os.path.join(ROOT, "backend/static/activity.html"), encoding="utf-8").read()
check("страница журнала: свои js и css", "/static/activity/activity.js" in html and "/static/activity/activity.css" in html
      and "<title>Журнал действий" in html)
sprite = open(os.path.join(ROOT, "backend/static/icons/ui.svg"), encoding="utf-8").read()
have = set(re.findall(r'id="i-([a-z0-9-]+)"', sprite))
used = set()
for f in ("backend/static/activity/activity.js", "backend/static/js/dashboard-home.js"):
    src = open(os.path.join(ROOT, f), encoding="utf-8").read()
    used |= set(re.findall(r"icon\('([a-z0-9-]+)'", src)) | set(re.findall(r"ic\('([a-z0-9-]+)'", src))
    used |= set(re.findall(r":\s*'([a-z0-9-]+)'", src[src.find("ACT_ICON"):src.find("ACT_ICON") + 600])) if "ACT_ICON" in src else set()
check("значки журнала и главной есть в спрайте", not (used - have), sorted(used - have))
for lang in ("ru", "ky"):
    d = json.load(open(os.path.join(ROOT, f"backend/static/i18n/{lang}.json"), encoding="utf-8"))
    check(f"словарь {lang}: sidebar.activity и staff.activity", bool(d["sidebar"].get("activity")) and bool(d["staff"].get("activity")))

if not PG:
    print("SKIP часть 2: ACTIVITY_TEST_DATABASE_URL не задан")
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
from backend.models.booking import Booking  # noqa: E402
from backend.models.activity import ActivityLog  # noqa: E402
from backend.models.salon_manager import ManagerAction  # noqa: E402
from backend.core.security import create_jwt_token, create_impersonation_token  # noqa: E402
from backend.services import booking_tools, beauty_services, salon_home  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
app_module.ensure_booking_tables()
cl = TestClient(app_module.app, raise_server_exceptions=False)
B = bs.BISHKEK
db = SessionLocal()
NOW = bs.now_utc()
TODAY = bs.to_local(NOW).date()


def at_day(day: date, hm: str):
    return datetime.combine(day, time.fromisoformat(hm), B).astimezone(timezone.utc)


pro = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p", is_active=True,
                       product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
start_plan = SubscriptionPlan(code="beauty_start", name="Beauty Start", price=1990, max_assistants=3, description="s",
                              is_active=True, product="beauty", limits={"staff": 3, "agent_mode": "reactive", "credits_month": 0})
db.add_all([pro, start_plan])
db.flush()
u = User(email="act@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
         subscription_plan_id=pro.id, subscription_end_date=NOW + timedelta(days=20), business_niche="beauty_salon",
         business_profile={"answers": {}})
admin = User(email="root@voksy.kg", password_hash="x", email_verified=True, first_name="Поддержка", is_admin=True)
db.add_all([u, admin])
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор", is_active=True)
db.add(agent)
db.flush()
from backend.services.business_profile import apply_to_agent  # noqa: E402
apply_to_agent(db, u, agent)
db.commit()
HO = {"Authorization": "Bearer " + create_jwt_token(u.id), "X-Forwarded-For": "10.1.1.1"}


def rows(**flt):
    db.expire_all()
    q = db.query(ActivityLog).filter(ActivityLog.user_id == u.id)
    for k, v in flt.items():
        q = q.filter(getattr(ActivityLog, k) == v)
    return q.order_by(ActivityLog.created_at.desc()).all()


def last(action):
    r = rows(action=action)
    return r[0] if r else None


# — услуга и мастер —
user = db.query(User).get(u.id)
svc = beauty_services.save(db, user, None, {"title": "Маникюр", "price": 1200, "duration_min": 60, "category": "Ногти"})
db.commit()
check("услуга создана → строка владельца", last("service.create") is not None and last("service.create").actor_kind == "owner"
      and "Маникюр" in last("service.create").summary)
full = {dd: [["00:00", "23:59"]] for dd in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
r = cl.post("/api/booking/resources", headers=HO, json={"name": "Айгуль", "schedule": full, "role": "Мастер маникюра"})
m1 = r.json()["resource"]["id"]
check("мастер создан → строка с графиком и IP", last("master.create") is not None and "Айгуль" in last("master.create").summary
      and last("master.create").ip == "10.1.1.1", last("master.create").summary if last("master.create") else r.text)
r = cl.patch(f"/api/booking/resources/{m1}", headers=HO, json={"role": "Топ-мастер", "service_prices": {str(svc.id): {"price": 1500}}})
mu = last("master.update")
check("мастер изменён: было → стало (специализация, свои цены)", mu is not None and "специализация: Мастер маникюра → Топ-мастер" in mu.summary
      and "свои цены" in mu.summary and "role" in (mu.changes or {}), mu.summary if mu else r.text)
r = cl.patch(f"/api/booking/services/{svc.id}", headers=HO, json={"price": 1400, "prep_min": 10})
su = last("service.update")
check("услуга: цена было → стало", su is not None and "цена от 1 200 сом → 1 400 сом" in su.summary and su.changes.get("price") == [1200.0, 1400.0],
      su.summary if su else r.text)
cl.put("/api/booking/settings", headers=HO, json={"settings": {"buffer_min": 10, "reminders": False}, "enabled": True, "slug": "aidana"})
st_row = last("settings.update")
check("правила записи: что изменилось", st_row is not None and "перерыв между записями, мин: 0 → 10" in st_row.summary
      and "напоминания: да → нет" in st_row.summary, st_row.summary if st_row else "")

# — запись владельцем —
D2 = TODAY + timedelta(days=2)
r = cl.post("/api/booking/bookings", headers=HO, json={"start": at_day(D2, "11:00").isoformat(), "service_id": str(svc.id),
                                                        "resource_id": m1, "client_name": "Нурай Асанова", "client_phone": "+996555123456"})
b1 = r.json()["booking"]["id"]
bc = last("booking.create")
check("запись владельцем → «кто» владелец, клиент, услуга, мастер, цена мастера",
      bc is not None and bc.actor_kind == "owner" and bc.actor_name == "Айдана" and bc.object_id == b1
      and "Нурай Асанова · Маникюр · Айгуль" in bc.summary and "1 500 сом" in bc.summary, bc.summary if bc else r.text)

# — сотрудник-администратор переносит —
db.expire_all()
member, _pw = ts.create_member(db, user, name="Айгерим", email="aigerim@kg.kg", password="secret12",
                               role_title="Администратор", permissions=["booking", "booking_setup", "crm"])
db.commit()
check("сотрудник добавлен → строка «Новый сотрудник» с разделами", last("team.create") is not None
      and "Айгерим" in last("team.create").summary and "Онлайн-запись" in last("team.create").summary)
HS = {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(member.id), "stv": member.token_version}),
      "X-Forwarded-For": "10.2.2.2"}
r = cl.patch(f"/api/booking/bookings/{b1}", headers=HS, json={"start": at_day(D2, "15:00").isoformat()})
mv = last("booking.move")
check("перенос сотрудником → его имя, IP, было → стало", mv is not None and mv.actor_kind == "staff" and mv.actor_name == "Айгерим"
      and mv.actor_id == str(member.id) and mv.ip == "10.2.2.2" and "11:00 → " in mv.summary and "15:00" in mv.summary
      and "start" in (mv.changes or {}), (mv.summary if mv else r.text))
r = cl.patch(f"/api/booking/bookings/{b1}", headers=HS, json={"status": "confirmed"})
check("статус: Записан → Подтвердил", last("booking.status") is not None and "Записан → Подтвердил" in last("booking.status").summary)
r = cl.patch(f"/api/booking/bookings/{b1}", headers=HS, json={"duration_min": 90})
check("растяжка: длительность 60 → 90 мин", last("booking.resize") is not None and "60 → 90 мин" in last("booking.resize").summary)
before_n = len(rows(object_id=b1))
r = cl.patch(f"/api/booking/bookings/{b1}", headers=HS, json={"status": "confirmed"})
check("ничего не изменилось — новой строки нет", len(rows(object_id=b1)) == before_n)

# — откат: время занято → строки нет —
n0 = len(rows(action="booking.create"))
r = cl.post("/api/booking/bookings", headers=HO, json={"start": at_day(D2, "15:30").isoformat(), "service_id": str(svc.id),
                                                        "resource_id": m1, "client_name": "Занятое Время"})
check("занятое время: запись не создана и строки нет", r.status_code == 400 and len(rows(action="booking.create")) == n0, r.status_code)

# — мастер из своего кабинета —
r = cl.post(f"/api/booking/resources/{m1}/login", headers=HO, json={"email": "aigul@kg.kg", "password": "secret12"})
mlog = last("master.login")
check("вход мастеру → строка «Вход мастера»", r.status_code == 200 and mlog is not None and "aigul@kg.kg" in mlog.summary, r.text[:200])
db.expire_all()
mm = db.execute(text("SELECT id, token_version FROM team_members WHERE email = 'aigul@kg.kg'")).first()
HM = {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(mm[0]), "stv": mm[1]})}
D3 = TODAY + timedelta(days=3)
r = cl.post("/api/booking/me/bookings", headers=HM, json={"start": at_day(D3, "10:00").isoformat(), "service_id": str(svc.id),
                                                           "client_name": "Самал", "client_phone": "+996555777000"})
mb = last("booking.create")
check("мастер записал к себе → «кто» мастер (сотрудник Айгуль)", r.status_code == 200 and mb.actor_kind == "staff" and mb.actor_name == "Айгуль",
      (r.status_code, mb.actor_name if mb else None))
b_master = r.json()["booking"]["id"] if r.status_code == 200 else None

# — бот в чате владельца, пробная запись бота в переписке, голосовой агент, клиент по ссылке —
ctx_owner = {"user_id": str(u.id), "surface": "chat", "agent_config_id": str(agent.id)}
res = json.loads(asyncio.run(booking_tools.execute("create_booking", {"service": "Маникюр", "start": at_day(D3, "13:00").isoformat(),
                                                                      "client_name": "Алтынай", "client_phone": "+996555000111"}, ctx_owner, db)))
bot_row = last("booking.create")
check("бот по команде владельца → «ИИ по команде владельца»", res.get("ok") and bot_row.actor_kind == "ai"
      and bot_row.actor_name == "ИИ по команде владельца", (res, bot_row.actor_name if bot_row else None))
cc = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Жибек", phone="+996555222333", status="new")
db.add(cc)
db.commit()
n_bot = len(rows(action="booking.create"))
ctx_pc = {"user_id": str(u.id), "surface": "postcall", "agent_config_id": str(agent.id), "current_contact_id": str(cc.id),
          "call_direction": "whatsapp_inbound"}
res = json.loads(asyncio.run(booking_tools.execute("create_booking", {"service": "Маникюр", "start": at_day(D3, "16:00").isoformat()}, ctx_pc, db)))
check("пробная запись бота (два шага) — откат, строки в журнале нет",
      res.get("needs_confirmation") or res.get("confirmation_id") and len(rows(action="booking.create")) == n_bot,
      (res, len(rows(action="booking.create")), n_bot))
check("пробная запись: ни одной новой строки", len(rows(action="booking.create")) == n_bot)
db.expire_all()
call_b = bs.create_booking(db, db.query(User).get(u.id), start=at_day(D3, "18:00"), service_id=str(svc.id), resource_id=m1,
                           client_name="Звонившая", client_phone="+996700111222", source="call", channel="call")
db.commit()
check("голосовой агент → «Голосовой агент»", last("booking.create").actor_kind == "call")
r = cl.post("/api/public/booking/aidana", json={"start": at_day(TODAY + timedelta(days=4), "12:00").isoformat(),
                                                "service_id": str(svc.id), "resource_id": m1, "name": "Гүлнара", "phone": "+996555444333"})
pub = last("booking.create")
check("клиент по ссылке → «Клиент» с его именем", r.status_code == 200 and pub.actor_kind == "client" and pub.actor_name == "Гүлнара",
      (r.status_code, r.text[:200]))

# — предоплата и отмена —
db.execute(text("UPDATE bookings SET prepay_amount = 300 WHERE id = CAST(:i AS uuid)"), {"i": b1})
db.commit()
cl.patch(f"/api/booking/bookings/{b1}", headers=HO, json={"prepaid": True})
check("предоплата получена", last("booking.prepaid") is not None and "предоплата получена 300 сом" in last("booking.prepaid").summary)
r = cl.delete(f"/api/booking/bookings/{call_b.id}?reason=заболела", headers=HO)
cx = last("booking.cancel")
check("отмена: клиент, время, причина", cx is not None and "Звонившая" in cx.summary and "причина: заболела" in cx.summary, cx.summary if cx else r.text)

# — визит из двух услуг — одна строка; история записи визита —
svc2 = beauty_services.save(db, db.query(User).get(u.id), None, {"title": "Педикюр", "price": 1500, "duration_min": 60})
db.commit()
r = cl.post("/api/booking/visits", headers=HO, json={"items": [{"service_id": str(svc.id)}, {"service_id": str(svc2.id)}],
                                                      "start": at_day(TODAY + timedelta(days=5), "10:00").isoformat(),
                                                      "client_name": "Мээрим", "client_phone": "+996555999000"})
vis = r.json().get("bookings") or []
vrow = last("visit.create")
check("визит → одна строка «Новый визит» с обеими услугами", r.status_code == 200 and vrow is not None and "Маникюр" in vrow.summary
      and "Педикюр" in vrow.summary and len((vrow.meta or {}).get("booking_ids") or []) == 2, (r.status_code, r.text[:200]))
if vis:
    vb = vis[1]["id"]
    cl.patch(f"/api/booking/bookings/{vb}", headers=HO, json={"note": "аллергия на лак"})
    h = cl.get(f"/api/beauty/activity?object_type=booking&object_id={vb}", headers=HO).json()
    acts = [x["action"] for x in h["items"]]
    check("история записи: её изменения + создание визита", acts[:2] == ["booking.note", "visit.create"] and len(acts) == 2, acts)

# — блоки времени —
r = cl.post("/api/booking/blocks", headers=HO, json={"start": at_day(TODAY + timedelta(days=6), "13:00").isoformat(),
                                                      "end": at_day(TODAY + timedelta(days=6), "14:00").isoformat(),
                                                      "resource_id": m1, "kind": "break", "note": "обед"})
blk = r.json().get("block", {}).get("id")
check("блок времени → «Перерыв · Айгуль … · обед»", last("block.create") is not None and "Перерыв · Айгуль" in last("block.create").summary
      and "обед" in last("block.create").summary, last("block.create").summary if last("block.create") else r.text)
cl.delete(f"/api/booking/blocks/{blk}", headers=HO)
check("блок снят", last("block.delete") is not None and "Перерыв" in last("block.delete").summary)

# — страница салона и чат —
cl.put("/api/booking/page", headers=HO, json={"tagline": "Лучший маникюр", "phone": "+996555100200", "chat_enabled": False})
check("страница: изменённые поля", last("page.update") is not None and "слоган" in last("page.update").summary
      and "телефон" in last("page.update").summary)
check("чат на сайте: выключен", last("chat.update") is not None and "чат: выключен" in last("chat.update").summary)

# — финансы —
r = cl.post("/api/finance/expenses", headers=HO, json={"category": "rent", "title": "Аренда", "amount": 30000, "repeat": "month"})
eid = r.json()["expense"]["id"]
check("расход: «Аренда · 30 000 сом в месяц»", last("expense.create") is not None and last("expense.create").summary == "Аренда · 30 000 сом в месяц")
cl.patch(f"/api/finance/expenses/{eid}", headers=HO, json={"amount": 32000})
check("расход: сумма было → стало", last("expense.update") is not None and "30 000 сом → 32 000 сом" in last("expense.update").summary)
cl.patch(f"/api/finance/payroll/{m1}", headers=HO, json={"pay_percent": 40})
check("оплата мастера: не задана → 40%", last("payroll.update") is not None and "не задана → 40% с выручки" in last("payroll.update").summary)
cl.delete(f"/api/finance/expenses/{eid}", headers=HO)
check("расход удалён", last("expense.delete") is not None)

# — сотрудник: правка, выключение —
r = cl.patch(f"/api/team/{member.id}", headers=HO, json={"permissions": ["booking", "crm"]})
tu = last("team.update")
check("сотрудник: разделы было → стало", tu is not None and "разделы:" in tu.summary and "Мастера и график" in tu.summary, tu.summary if tu else r.text)

# — решения ИИ-управляющего —
ma = ManagerAction(user_id=u.id, agent_config_id=agent.id, contact_id=cc.id, scenario="winback", kind="message", status="proposed",
                   payload={"reason": "давно не была"}, created_at=NOW)
db.add(ma)
db.commit()
r = cl.post("/api/manager/actions/decide", headers=HO, json={"ids": [str(ma.id)], "approve": True})
ap = last("manager.approve")
check("ИИ-управляющий: одобрено 1 предложение (Жибек)", r.json().get("changed") == 1 and ap is not None and "Жибек" in ap.summary,
      (r.text[:200], ap.summary if ap else None))

# — вход поддержки Voksy —
HI = {"Authorization": "Bearer " + create_impersonation_token(u.id, admin.id)}
cl.patch(f"/api/booking/bookings/{b1}", headers=HI, json={"note": "проверка поддержки"})
check("действие под входом поддержки → «Поддержка Voksy»", last("booking.note").actor_kind == "admin"
      and last("booking.note").actor_id == str(admin.id))
db.execute(text("INSERT INTO admin_audit_log (id, admin_user_id, target_user_id, action, ip, created_at) "
                "VALUES (:i, :a, :t, 'impersonate', '9.9.9.9', clock_timestamp())"), {"i": str(uuid.uuid4()), "a": str(admin.id), "t": str(u.id)})
db.commit()

# ── лента ──
feed = cl.get("/api/beauty/activity?period=all&limit=200", headers=HO).json()
items = feed["items"]
check("лента: новые сверху, вход поддержки из admin_audit_log", items and items[0]["action"] == "admin.impersonate"
      and items[0]["actor"]["kind"] == "admin" and items[0].get("ip") == "9.9.9.9", items[0] if items else feed)
times = [x["at"] for x in items]
check("лента: порядок по времени", times == sorted(times, reverse=True))
check("лента: люди для фильтра (владелец, Айгерим, Айгуль, ИИ, звонок, клиент)",
      {p["key"] for p in feed["people"]} >= {"owner", f"staff:{member.id}", "ai", "call", "client"}, feed["people"])
check("лента: IP владельцу виден", any(x.get("ip") == "10.2.2.2" for x in items))
f_staff = cl.get(f"/api/beauty/activity?period=all&actor=staff:{member.id}", headers=HO).json()["items"]
check("фильтр «кто»: только Айгерим", f_staff and all(x["actor"]["id"] == str(member.id) for x in f_staff), len(f_staff))
f_money = cl.get("/api/beauty/activity?period=all&group=money", headers=HO).json()["items"]
check("фильтр «что»: деньги", f_money and {x["object_type"] for x in f_money} <= {"expense", "payroll"}, {x["object_type"] for x in f_money})
f_today = cl.get("/api/beauty/activity?period=today", headers=HO).json()["items"]
check("период «сегодня»: всё, что сделали сегодня", len(f_today) == len(items), (len(f_today), len(items)))
p1 = cl.get("/api/beauty/activity?period=all&limit=5", headers=HO).json()
p2 = cl.get(f"/api/beauty/activity?period=all&limit=5&before={p1['next']}", headers=HO).json()
ids1, ids2 = {x["id"] for x in p1["items"]}, {x["id"] for x in p2["items"]}
check("страницы: по 5, без повторов, курсор", len(ids1) == 5 and len(ids2) == 5 and not (ids1 & ids2) and p1["next"])
h1 = cl.get(f"/api/beauty/activity?object_type=booking&object_id={b1}", headers=HO).json()["items"]
check("история записи Нурай: создана → перенос → статус → длительность → предоплата → заметка",
      [x["action"] for x in reversed(h1)] == ["booking.create", "booking.move", "booking.status", "booking.resize", "booking.prepaid", "booking.note"],
      [x["action"] for x in reversed(h1)])

# ── доступ ──
db.expire_all()
mgr, _ = ts.create_member(db, db.query(User).get(u.id), name="Управляющая Аида", email="aida@kg.kg", password="secret12",
                          role_title="Управляющий", permissions=["booking", "booking_setup", "finance", "activity"])
db.commit()
HG = {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(mgr.id), "stv": mgr.token_version})}
g = cl.get("/api/beauty/activity?period=all", headers=HG)
check("управляющий видит журнал, но без IP", g.status_code == 200 and g.json()["items"] and not any("ip" in x for x in g.json()["items"]))
HS2 = {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(member.id), "stv": db.query(type(member)).get(member.id).token_version})}
check("администратор без раздела — 403", cl.get("/api/beauty/activity", headers=HS2).status_code == 403)
check("мастер — 403", cl.get("/api/beauty/activity", headers=HM).status_code == 403)
check("главная салона сотруднику — 403", cl.get("/api/beauty/home", headers=HG).status_code == 403)
OTHER = User(email="other@kg.kg", password_hash="x", email_verified=True, first_name="Чужой")
db.add(OTHER)
db.commit()
oth = cl.get("/api/beauty/activity?period=all", headers={"Authorization": "Bearer " + create_jwt_token(OTHER.id)}).json()
check("чужой кабинет журнала не видит", oth["items"] == [])

# ── чистка ──
old_id = uuid.uuid4()
db.execute(text("INSERT INTO activity_log (id, user_id, created_at, actor_kind, action, object_type, summary) "
                "VALUES (:i, :u, now() - interval '200 days', 'owner', 'booking.create', 'booking', 'старая')"),
           {"i": str(old_id), "u": str(u.id)})
db.commit()
n_before = db.execute(text("SELECT count(*) FROM activity_log")).scalar()
gone = activity.cleanup(db, force=True)
n_after = db.execute(text("SELECT count(*) FROM activity_log")).scalar()
check("чистка: строка старше 180 дней удалена, свежие на месте", gone == 1 and n_after == n_before - 1
      and db.execute(text("SELECT count(*) FROM activity_log WHERE id = CAST(:i AS uuid)"), {"i": str(old_id)}).scalar() == 0)
check("чистка: повторно в тот же час не идёт", activity.cleanup(db) == 0)

# ── главная салона ──
same_day = bs.to_local(NOW - timedelta(minutes=40)).date() == TODAY == bs.to_local(NOW + timedelta(minutes=150)).date()
db.expire_all()
user = db.query(User).get(u.id)
if same_day:
    here = bs.create_booking(db, user, start=NOW - timedelta(minutes=40), service_id=str(svc.id), resource_id=m1,
                             client_name="Сейчас Здесь", duration_min=90)
    here.status = "arrived"
    nxt = bs.create_booking(db, user, start=NOW + timedelta(minutes=120), service_id=str(svc.id), resource_id=m1,
                            client_name="Следующая", duration_min=30)
past = bs.create_booking(db, user, start=NOW - timedelta(days=1, hours=2), service_id=str(svc.id), resource_id=m1,
                         client_name="Не отмечена", duration_min=60)
tm = bs.create_booking(db, user, start=at_day(TODAY + timedelta(days=1), "09:00"), service_id=str(svc.id), resource_id=m1,
                       client_name="Завтрашняя", duration_min=60)
low = bs.create_booking(db, user, start=NOW - timedelta(days=2), service_id=str(svc.id), resource_id=m1, client_name="Недовольная",
                        duration_min=60)
low.status = "completed"
from backend.services import reviews_service as _rv   # шаг 2.3: оценка 1–3 → отзыв «ждёт проверки»
_rv.record(db, low, 2, "долго ждала", "chat")
tm.prepay_amount = 300
db.add(ManagerAction(user_id=u.id, agent_config_id=agent.id, contact_id=cc.id, scenario="fill_gaps", kind="message",
                     status="proposed", payload={}, created_at=NOW))
db.commit()
hm = cl.get("/api/beauty/home", headers=HO)
H = hm.json() if hm.status_code == 200 else {}
check("главная: ответ владельцу", hm.status_code == 200, hm.text[:300])
att = {a["key"]: a for a in H.get("attention") or []}
if same_day:
    nw = H.get("now") or {}
    check("сейчас в салоне: «пришёл» + следующая запись", [x["client"] for x in nw.get("in_salon", [])] == ["Сейчас Здесь"]
          and nw.get("next") and nw["next"][0]["client"] == "Следующая" and nw["counts"]["in_salon"] == 1, nw.get("counts"))
    check("сегодня: выручка по визитам, ожидается — неотмеченные записи дня", nw.get("revenue") == 1500 and nw.get("expected") == 1500, (nw.get("revenue"), nw.get("expected")))
check("внимание: прошедшая запись не отмечена", "not_marked" in att and "Не" in att["not_marked"]["hint"], list(att))
check("внимание: завтра без подтверждения", "unconfirmed" in att and "Завтрашняя" in att["unconfirmed"]["hint"])
check("внимание: ждём предоплату (Завтрашняя, 300 сом)", att.get("prepay", {}).get("count") == 1 and "300 сом" in att["prepay"]["hint"]
      and "Завтрашняя" in att["prepay"]["hint"], att.get("prepay"))
check("внимание: ждут одобрения ИИ-управляющего", att.get("approve", {}).get("count") == 1)
check("внимание: новые записи без вас (бот, ссылка; отменённая со звонка — нет)", att.get("new_online", {}).get("count") == 2
      and "бот 1" in att["new_online"]["hint"] and "по ссылке 1" in att["new_online"]["hint"], att.get("new_online"))
check("внимание: низкая оценка", "low_reviews" in att and "Недовольная — 2" in att["low_reviews"]["hint"])
since = cl.get("/api/beauty/home?since=" + (bs.now_utc() + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"), headers=HO).json()
check("«новые без вас» с прошлого захода — пусто, если с тех пор ничего", "new_online" not in {a["key"] for a in since["attention"]})
gp = H.get("gaps") or {}
g0 = gp.get("days", [{}])[0]
check("окна: сегодня и завтра, у Айгуль есть свободное время", len(gp.get("days") or []) == 2 and g0.get("title") == "Сегодня"
      and g0["masters"][0]["name"] == "Айгуль" and (g0["masters"][0]["free_min"] > 0 or bs.to_local(NOW).hour >= 23),
      g0.get("masters"))
g1 = gp["days"][1]["masters"][0]
check("окна завтра: запись 09:00–10:00 вырезана из свободного", g1["ranges"] and g1["ranges"][0]["from"] in ("00:00", "10:10")
      and not any(rg["from"] < "10:00" < rg["to"] and rg["from"] >= "09:00" for rg in g1["ranges"]), g1["ranges"][:3])
check("окна: можно предложить клиентам (Beauty Pro)", gp.get("can_offer") is True)
ld = H.get("load") or {}
check("загрузка: сегодня и неделя по мастеру", ld.get("masters") and ld["masters"][0]["week"]["work_min"] > 0
      and ld["masters"][0]["week"]["pct"] is not None, ld.get("week"))
check("последние действия: 5 строк журнала", len(H.get("recent") or []) == 5)

# — «Предложить клиентам» —
old = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Асель", phone="+996555000901", status="active")
db.add(old)
db.flush()
db.add(AgentWhatsAppMessage(agent_config_id=agent.id, agent_contact_id=old.id, direction="inbound", body="Здравствуйте",
                            created_at=datetime.utcnow() - timedelta(days=60)))
st0 = at_day(TODAY - timedelta(days=40), "12:00")
db.add(Booking(user_id=u.id, resource_id=uuid.UUID(m1), service_entry_id=svc.id, service_title="Маникюр", contact_id=old.id,
               client_name="Асель", start_at=st0, end_at=st0 + timedelta(hours=1), status="completed", price=1500, source="owner"))
db.commit()
r = cl.post("/api/beauty/home/offer-gaps", headers=HO, json={"resource_id": m1, "date": (TODAY + timedelta(days=1)).isoformat()})
of = r.json()
check("«Предложить клиентам»: Асель — на одобрение", r.status_code == 200 and of.get("proposed") == 1
      and of["actions"][0]["contact_name"] == "Асель" and of["actions"][0]["status"] == "proposed", r.text[:300])
check("«Предложить клиентам» — строка в журнале", last("manager.offer") is not None and "на одобрение" in last("manager.offer").summary)
db.expire_all()
uu = db.query(User).get(u.id)
uu.subscription_plan_id = start_plan.id
db.commit()
r = cl.post("/api/beauty/home/offer-gaps", headers=HO, json={"resource_id": m1})
check("на Beauty Start «Предложить клиентам» — отказ с причиной", r.status_code == 400 and "Beauty Pro" in r.text, r.text[:200])
check("на Beauty Start окна без кнопки", cl.get("/api/beauty/home", headers=HO).json()["gaps"]["can_offer"] is False)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
