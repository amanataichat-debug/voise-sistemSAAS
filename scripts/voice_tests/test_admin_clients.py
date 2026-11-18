"""Админка «Клиенты» и вход в кабинет клиента (ветка 1811-niche-admin-speed).

Проверяет на настоящей PostgreSQL:
- список: телефон, ниша, тариф, «что настроил клиент»; поиск по номеру в любом написании;
  сегменты (без телефона, новые сегодня, истекают, кому звонить); 300 клиентов — мало запросов;
- сводка регистраций и статусов; карточка клиента с лентой;
- статус / заметка / дата звонка / телефон пишутся в журнал, кривые значения → 400;
- вход в кабинет: токен клиента с отметкой админа, /me → impersonated, пароль / API-ключ /
  кошелёк / удаление под таким токеном → 403, админские разделы клиенту → 403,
  в кабинет другого админа и в свой — нельзя, каждый вход в журнале с IP;
- не-админу всё закрыто.

Нужен PostgreSQL: ADMIN_TEST_DATABASE_URL (или AUTH_TEST_DATABASE_URL). Без него — SKIP.
"""
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("ADMIN_TEST_DATABASE_URL") or os.environ.get("AUTH_TEST_DATABASE_URL")
if not PG:
    print("SKIP: ADMIN_TEST_DATABASE_URL не задан")
    print("ПРОВАЛОВ: 0")
    sys.exit(0)
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG, PRODUCTION="true", OPENAI_API_KEY="k",
                  JWT_SECRET_KEY="admin-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from sqlalchemy import event, text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.core.security import create_jwt_token, hash_password  # noqa: E402

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

app_module.ensure_user_profile_columns()   # идемпотентно на готовой схеме
app_module.ensure_user_profile_columns()
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


def mk(email, plan_code=None, days=10, **kw):
    u = User(email=email, password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True, **kw)
    if plan_code:
        u.subscription_plan_id = plan(plan_code).id
        u.subscription_end_date = now + timedelta(days=days)
    db.add(u)
    db.flush()
    return u


admin = mk("boss@voksy.test", is_admin=True, first_name="Аманат")
admin2 = mk("boss2@voksy.test", is_admin=True, first_name="Второй")
a = mk("aibek@shop.kg", "start", 20, first_name="Айбек", company_name="Норка KG", phone="+996700123456",
       business_niche="clothing")
b = mk("nophone@mail.ru", "free", 3, first_name="Без", is_trial=True)
c = mk("profi@corp.kg", "profi", 2, first_name="Профи", phone="+996555000111", business_niche="real_estate")
c.created_at = now - timedelta(days=20)
agent = AgentConfig(user_id=a.id, name="Бот Норка", telegram_bot_token="123:abc")
db.add(agent)
db.flush()
from backend.models.agent_whatsapp import AgentWhatsAppAccount  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.models.subscription import PaymentTransaction  # noqa: E402
db.add(AgentWhatsAppAccount(agent_config_id=agent.id, user_id=a.id, instance_name="wa-test-1",
                            webhook_secret="s", status="connected"))
for i in range(3):
    db.add(AgentKnowledgeEntry(user_id=a.id, agent_config_id=agent.id, kind="product", title=f"Шапка {i}", body=""))
db.add(PaymentTransaction(user_id=a.id, plan_id=plan("start").id, amount=7500, status="success"))
db.commit()

H = {"Authorization": f"Bearer {create_jwt_token(admin.id)}"}
HA = {"Authorization": f"Bearer {create_jwt_token(a.id)}"}

# ── не-админу закрыто
r = cl.get("/api/admin/clients", headers=HA)
check("список клиентов не-админу: 403", r.status_code == 403, r.status_code)
r = cl.post(f"/api/admin/clients/{b.id}/impersonate", headers=HA)
check("вход в чужой кабинет не-админу: 403", r.status_code == 403, r.status_code)

# ── список
r = cl.get("/api/admin/clients", headers=H)
j = r.json() if r.status_code == 200 else {}
items = {x["email"]: x for x in j.get("items", [])}
check("список: 200, все клиенты без админов", r.status_code == 200 and j.get("total") == 3, (r.status_code, j.get("total"), r.text[:200]))
r = cl.get("/api/admin/clients", headers=H, params={"segment": "admins"})
check("сегмент «Админы»", {x["email"] for x in r.json().get("items", [])} == {"boss@voksy.test", "boss2@voksy.test"})
ia = items.get("aibek@shop.kg", {})
check("телефон, ниша, компания в списке", ia.get("phone") == "+996700123456" and ia.get("niche_title") == "Одежда и обувь"
      and ia.get("company_name") == "Норка KG", ia)
s = ia.get("setup", {})
check("что настроил: бот, WhatsApp, Telegram-бот, каталог, оплата",
      s.get("agents") == 1 and s.get("whatsapp") is True and s.get("tg_bot") is True and s.get("products") == 3
      and s.get("paid_total") == 7500.0, s)
check("шаги запуска 3 из 4 (нет номера)", ia.get("setup_score", {}).get("done") == 3, ia.get("setup_score"))
check("тариф Старт активен, дней ~19", ia.get("plan", {}).get("kind") == "paid" and 18 <= ia["plan"]["days_left"] <= 20, ia.get("plan"))
check("статус по умолчанию «Новый»", ia.get("admin_status") == "new")

for q, want in [("0700 12 34", "aibek@shop.kg"), ("+996 (700) 123", "aibek@shop.kg"), ("норка", "aibek@shop.kg"),
                ("PROFI@", "profi@corp.kg")]:
    r = cl.get("/api/admin/clients", headers=H, params={"q": q})
    got = [x["email"] for x in r.json().get("items", [])]
    check(f"поиск «{q}»", got == [want], got)

for seg, want in [("no_phone", {"nophone@mail.ru"}), ("expiring", {"profi@corp.kg", "nophone@mail.ru"}),
                  ("trial", {"nophone@mail.ru"}), ("paying", {"aibek@shop.kg", "profi@corp.kg"}),
                  ("no_setup", {"nophone@mail.ru", "profi@corp.kg"})]:
    r = cl.get("/api/admin/clients", headers=H, params={"segment": seg})
    got = {x["email"] for x in r.json().get("items", [])}
    check(f"сегмент {seg}", got == want, got)
r = cl.get("/api/admin/clients", headers=H, params={"segment": "new_today"})
got = {x["email"] for x in r.json().get("items", [])}
check("новые сегодня: без давнего Профи и без админов", "profi@corp.kg" not in got and "aibek@shop.kg" in got and "boss@voksy.test" not in got, got)
r = cl.get("/api/admin/clients", headers=H, params={"segment": "bogus"})
check("неизвестный сегмент: 400", r.status_code == 400, r.status_code)
r = cl.get("/api/admin/clients", headers=H, params={"niche": "real_estate"})
check("фильтр по нише", [x["email"] for x in r.json().get("items", [])] == ["profi@corp.kg"])

# ── сводка
r = cl.get("/api/admin/clients/summary", headers=H)
sm = r.json() if r.status_code == 200 else {}
cnt = sm.get("counts", {})
check("сводка: всего без админов, новые, без телефона, платят, истекают",
      cnt.get("total") == 3 and cnt.get("no_phone") == 1 and cnt.get("paying") == 2 and cnt.get("expiring") == 2
      and cnt.get("new_today") >= 4, cnt)
check("сводка: 14 дней регистраций и статусы", len(sm.get("signups_by_day", [])) == 14 and sm.get("by_status", {}).get("new") == 3,
      (len(sm.get("signups_by_day", [])), sm.get("by_status")))

# ── правка: статус, заметка, дата звонка
nxt = (now - timedelta(hours=1)).isoformat()   # срок прошёл — звонить сегодня
r = cl.patch(f"/api/admin/clients/{a.id}", headers=H, json={"admin_status": "contacted", "note": "Позвонил, просит каталог для норки",
                                                          "next_contact_at": nxt})
check("правка статуса и заметки: 200", r.status_code == 200 and r.json().get("changed", {}).get("admin_status") == "contacted", r.text[:300])
tl = r.json().get("client", {}).get("timeline", [])
acts = [t["action"] for t in tl]
check("лента: статус, заметка, дата звонка", {"status", "note", "next_contact"} <= set(acts), acts)
check("последняя заметка в карточке", r.json().get("client", {}).get("admin_note") == "Позвонил, просит каталог для норки")
r = cl.get("/api/admin/clients", headers=H, params={"segment": "call_today"})
got = {x["email"] for x in r.json().get("items", [])}
check("кому звонить: клиент с датой звонка сегодня + новые", "aibek@shop.kg" in got and "nophone@mail.ru" in got and "profi@corp.kg" not in got, got)
r = cl.patch(f"/api/admin/clients/{a.id}", headers=H, json={"admin_status": "vip"})
check("неизвестный статус: 400", r.status_code == 400, r.status_code)
r = cl.patch(f"/api/admin/clients/{b.id}", headers=H, json={"phone": "12"})
check("кривой телефон: 400", r.status_code == 400, r.status_code)
r = cl.patch(f"/api/admin/clients/{b.id}", headers=H, json={"phone": "0777 12 34 56", "niche": "flowers"})
check("телефон и ниша от админа: E.164", r.status_code == 200 and r.json()["client"]["phone"] == "+996777123456"
      and r.json()["client"]["niche"] == "flowers", r.text[:200])
r = cl.patch(f"/api/admin/clients/{b.id}", headers=H, json={"niche": "spaceships"})
check("неизвестная ниша: 400", r.status_code == 400, r.status_code)

# ── карточка
r = cl.get(f"/api/admin/clients/{a.id}", headers=H)
d = r.json() if r.status_code == 200 else {}
check("карточка: агенты, оплаты, переписка за 30 дней", r.status_code == 200 and d.get("agents", [{}])[0].get("name") == "Бот Норка"
      and d.get("payments", [{}])[0].get("amount") == 7500.0 and "whatsapp" in d.get("messages_30d", {}), str(d)[:300])
r = cl.get(f"/api/admin/clients/{uuid.uuid4()}", headers=H)
check("карточка несуществующего: 404", r.status_code == 404, r.status_code)

# ── вход в кабинет клиента
r = cl.post(f"/api/admin/clients/{a.id}/impersonate", headers={**H, "X-Forwarded-For": "92.62.70.1"})
check("вход в кабинет: токен", r.status_code == 200 and r.json().get("token") and r.json()["user"]["email"] == "aibek@shop.kg", r.text[:200])
HI = {"Authorization": f"Bearer {r.json().get('token')}"}
r = cl.get("/api/users/me", headers=HI)
check("под входом /me — клиент, impersonated=true", r.status_code == 200 and r.json().get("email") == "aibek@shop.kg"
      and r.json().get("impersonated") is True and r.json().get("phone") == "+996700123456", r.text[:200])
r = cl.get("/api/users/me", headers=HA)
check("обычный вход клиента: impersonated=false", r.status_code == 200 and r.json().get("impersonated") is False, r.text[:120])
r = cl.put("/api/users/me/password", headers=HI, json={"current_password": "Passw0rd!", "new_password": "Hacked123"})
check("смена пароля под входом: 403", r.status_code == 403 and "админки" in r.text, (r.status_code, r.text[:120]))
r = cl.post("/api/users/api-key/regenerate", headers=HI)
check("API-ключ под входом: 403", r.status_code == 403, r.status_code)
r = cl.post("/api/wallet/topup", headers=HI, json={"amount": 500})
check("пополнение кошелька под входом: 403", r.status_code == 403, r.status_code)
r = cl.delete("/api/users/me", headers=HI)
check("удаление аккаунта под входом: 403", r.status_code == 403, r.status_code)
r = cl.get("/api/admin/clients", headers=HI)
check("админка под токеном клиента: 403", r.status_code == 403, r.status_code)
r = cl.put("/api/users/me", headers=HI, json={"company_name": "Норка KG (настроил админ)"})
check("обычная настройка под входом работает", r.status_code == 200 and r.json().get("company_name") == "Норка KG (настроил админ)", r.text[:150])
r = cl.post(f"/api/admin/clients/{admin2.id}/impersonate", headers=H)
check("в кабинет другого админа: 403", r.status_code == 403, r.status_code)
r = cl.post(f"/api/admin/clients/{admin.id}/impersonate", headers=H)
check("в свой кабинет: 400", r.status_code == 400, r.status_code)
row = db.execute(text("""SELECT action, ip, admin_user_id FROM admin_audit_log
                         WHERE target_user_id = :u AND action = 'impersonate'"""), {"u": a.id}).mappings().first()
check("вход записан в журнал с IP и админом", row is not None and row["ip"] == "92.62.70.1" and row["admin_user_id"] == admin.id, dict(row or {}))
r = cl.put("/api/users/me", headers=HA, json={"phone": "0700 11 22 33"})
check("клиент сам указывает телефон (E.164)", r.status_code == 200 and r.json().get("phone") == "+996700112233", r.text[:150])
r = cl.put("/api/users/me", headers=HA, json={"phone": "abc"})
check("кривой телефон в профиле: 422", r.status_code == 422, r.status_code)

# ── 300 клиентов: страница списка — мало запросов и быстро
for i in range(300):
    db.add(User(email=f"bulk{i}@x.kg", password_hash="x", email_verified=True, is_active=True, first_name=f"Клиент {i}",
                phone=f"+99670{i:07d}"))
db.commit()
statements = []


def _count(conn, cursor, statement, parameters, context, executemany):
    statements.append(statement)


event.listen(engine, "before_cursor_execute", _count)
t0 = time.perf_counter()
r = cl.get("/api/admin/clients", headers=H, params={"limit": 50})
dt = time.perf_counter() - t0
event.remove(engine, "before_cursor_execute", _count)
check("303 клиента: страница 50 строк", r.status_code == 200 and r.json()["total"] == 303 and len(r.json()["items"]) == 50, r.status_code)
check(f"страница списка: ≤ 16 запросов к БД (было {len(statements)})", len(statements) <= 16, len(statements))
check(f"страница списка быстрее 1 с ({dt:.2f} с)", dt < 1.0)
r = cl.get("/api/admin/clients", headers=H, params={"limit": 50, "offset": 300})
check("последняя страница", r.status_code == 200 and len(r.json()["items"]) == 3 and r.json()["total"] == 303, r.text[:100])

db.close()
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
