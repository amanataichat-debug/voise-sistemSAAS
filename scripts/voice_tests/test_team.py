"""Сотрудники кабинета (ветка 1811-niche-admin-speed): вход по почте, права по разделам, место 150 сом/мес.

Часть 1 (без базы): правила доступа сотрудника (разрешено только явно перечисленное), разделы,
меню и домашняя страница, пароль «продиктовать».

Часть 2 (PostgreSQL, TEAM_TEST_DATABASE_URL или AUTH_TEST_DATABASE_URL):
- владелец добавляет сотрудника: место списывается с кошелька (тип team), пароль виден один раз;
  повтор email / email кабинета / без разделов / плохой email / мало денег / без тарифа / больше 10 — отказ;
- сотрудник входит на общей форме: токен с данными владельца, /api/users/me без ключей и админки;
- разрешено только своё: чаты, клиенты, каталог (только позиции), аналитика без расходов;
  кошелёк, настройки бота, сотрудники, админка, удаление клиента — 403 до эндпоинта;
- личные настройки инбокса сотрудника не трогают настройки владельца; ответ сотрудника подписан его именем;
- выключение, новый пароль, удаление — старый токен 401; вход выключенного — понятный отказ;
- продление мест: хватает — продлено и списано, не хватает — выключен «не оплачено», тариф кончился —
  выключен «тариф»; у админа места бесплатные;
- голосовой помощник владельца сотрудника не пускает; регистрация и сброс пароля на email сотрудника — отказ;
- админ в кабинете клиента не оплачивает места за клиента.
"""
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("TEAM_TEST_DATABASE_URL") or os.environ.get("AUTH_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="team-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────── часть 1: правила ─────────────────────────
A = ts.staff_allowed
check("чаты: инбокс целиком", A(["inbox"], "GET", "/api/inbox/dialogs") and A(["inbox"], "POST", "/api/inbox/dialogs/x/send"))
check("без раздела чатов инбокс закрыт", not A(["crm"], "GET", "/api/inbox/overview"))
check("профиль и бот — любому сотруднику", A([], "GET", "/api/users/me") and A(["crm"], "GET", "/api/agent/list"))
check("кошелёк закрыт всем", not any(A(ts.PERM_KEYS, m, "/api/wallet/" + p) for m, p in
                                     [("GET", "balance"), ("POST", "topup"), ("GET", "transactions")]))
check("настройки бота закрыты", not A(ts.PERM_KEYS, "GET", "/api/agent/") and not A(ts.PERM_KEYS, "PUT", "/api/agent/"))
check("сотрудники закрыты (кроме себя)", not A(ts.PERM_KEYS, "GET", "/api/team")
      and not A(ts.PERM_KEYS, "POST", "/api/team") and A([], "POST", "/api/team/me/password"))
check("админка, тарифы, ключи закрыты", not A(ts.PERM_KEYS, "GET", "/api/admin/clients")
      and not A(ts.PERM_KEYS, "POST", "/api/payments/create-payment") and not A(ts.PERM_KEYS, "GET", "/api/users/api-key")
      and not A(ts.PERM_KEYS, "PUT", "/api/users/me") and not A(ts.PERM_KEYS, "PUT", "/api/users/me/password"))
check("удалить клиента — только владелец", not A(["crm"], "DELETE", "/api/agent/contacts/123"))
check("импорт клиентов — только владелец", not A(["crm"], "POST", "/api/agent/contacts/import/execute")
      and not A(["crm"], "GET", "/api/agent/contacts/import/template"))
check("CRM: карточка, этап, пауза, задачи, звонки", all(A(["crm"], m, p) for m, p in [
    ("GET", "/api/agent/contacts"), ("POST", "/api/agent/contacts"), ("PUT", "/api/agent/contacts/1"),
    ("PATCH", "/api/agent/contacts/1/status"), ("POST", "/api/agent/contacts/1/ai-pause"),
    ("GET", "/api/agent/tasks"), ("DELETE", "/api/agent/tasks/1"), ("GET", "/api/agent/calls"),
    ("GET", "/api/agent/calls/1"), ("GET", "/api/agent/stats"), ("GET", "/api/agent/pipeline/stages")]))
check("правка этапов воронки — только владелец", not A(["crm"], "PUT", "/api/agent/pipeline/stages"))
check("поиск клиента при записи — с правом записи", A(["booking"], "GET", "/api/agent/contacts")
      and not A(["booking"], "POST", "/api/agent/contacts"))
check("запись: календарь да, мастера и правила — только с «Мастера и график»",
      A(["booking"], "POST", "/api/booking/bookings") and A(["booking"], "PATCH", "/api/booking/bookings/1")
      and not A(["booking"], "POST", "/api/booking/resources") and not A(["booking"], "PUT", "/api/booking/settings")
      and A(["booking_setup"], "PUT", "/api/booking/settings") and A(["booking_setup"], "DELETE", "/api/booking/resources/1"))
check("каталог: позиции, массово, фото", all(A(["catalog"], m, p) for m, p in [
    ("GET", "/api/agent/knowledge"), ("POST", "/api/agent/knowledge"), ("PUT", "/api/agent/knowledge/1"),
    ("POST", "/api/agent/knowledge/bulk"), ("POST", "/api/agent/knowledge/products/batch"),
    ("POST", "/api/agent/files"), ("GET", "/api/agent/files")]))
check("каталог: проверка базы, переиндексация, документы — только владелец",
      not A(["catalog"], "POST", "/api/agent/knowledge/reindex") and not A(["catalog"], "POST", "/api/agent/knowledge/document")
      and not A(["catalog"], "POST", "/api/agent/knowledge/test") and not A(["catalog"], "DELETE", "/api/agent/files/1"))
check("аналитика — только отчёт", A(["analytics"], "GET", "/api/analytics/report")
      and A(["analytics"], "GET", "/api/analytics/export.csv") and not A(["analytics"], "GET", "/api/dashboard/summary"))
check("неизвестный путь закрыт", not A(ts.PERM_KEYS, "GET", "/api/agent/whatsapp")
      and not A(ts.PERM_KEYS, "POST", "/api/agent/chat") and not A(ts.PERM_KEYS, "GET", "/api/conversations/sessions"))
check("права чистятся и упорядочиваются", ts.clean_permissions(["catalog", "x", "inbox", "inbox"]) == ["inbox", "catalog"])
secs = ts.sections(["analytics", "booking_setup", "crm"], "Услуги")
check("разделы сотрудника по порядку", [s["id"] for s in secs] == ["booking", "crm", "analytics"], secs)
check("каталог называется по нише", ts.sections(["catalog"], "Услуги")[0]["label"] == "Услуги")
check("домашняя страница — первый раздел", ts.home_page(["catalog", "inbox"]) == "/static/inbox.html")
pw = ts.generate_password()
check("пароль удобно продиктовать", re.fullmatch(r"[a-z]{4}-[2-9]{4}", pw) is not None and not set("lo01") & set(pw), pw)

if not PG:
    print("SKIP: часть 2 — TEAM_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ───────────────────────── часть 2: PostgreSQL ─────────────────────────
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_contact import AgentContact  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.models.team import TeamMember  # noqa: E402
from backend.models.wallet_transaction import WalletTransaction  # noqa: E402
from backend.core.security import create_jwt_token, create_impersonation_token, hash_password  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)

import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app_module.ensure_user_profile_columns()
app_module.ensure_team_tables()
app_module.ensure_team_tables()   # идемпотентно
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


def mk(email, plan_code=None, days=20, wallet_som=0, **kw):
    u = User(email=email, password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
             wallet_balance=int(wallet_som * 100), **kw)
    if plan_code:
        u.subscription_plan_id = plan(plan_code).id
        u.subscription_end_date = now + timedelta(days=days)
    db.add(u)
    db.flush()
    return u


owner = mk("owner@salon.kg", "start", wallet_som=1000, first_name="Айгерим", company_name="Салон Айгерим",
           business_niche="beauty_salon", openai_api_key="sk-secret-owner")
poor = mk("poor@shop.kg", "start", wallet_som=100, first_name="Бедный")
noplan = mk("noplan@shop.kg", None, wallet_som=1000, first_name="Без тарифа")
admin = mk("admin@voksy.test", None, wallet_som=0, is_admin=True, first_name="Админ")
agent = AgentConfig(user_id=owner.id, name="Бот салона", orchestrator_prompt="СЕКРЕТНЫЙ ПРОМПТ",
                    additional_instructions="инструкции владельца")
db.add(agent)
db.flush()
contact = AgentContact(user_id=owner.id, agent_config_id=agent.id, name="Клиентка Жылдыз", phone="+996700111222")
db.add(contact)
qa = AgentKnowledgeEntry(user_id=owner.id, agent_config_id=agent.id, kind="qa", title="Где вы?", body="Бишкек")
db.add(qa)
db.commit()

HO = {"Authorization": f"Bearer {create_jwt_token(owner.id)}"}
HP = {"Authorization": f"Bearer {create_jwt_token(poor.id)}"}
HN = {"Authorization": f"Bearer {create_jwt_token(noplan.id)}"}
HA = {"Authorization": f"Bearer {create_jwt_token(admin.id)}"}


def wallet(u):
    db.expire_all()
    return round(db.query(User).filter(User.id == u.id).first().wallet_balance / 100, 2)


def login(email, password):
    return cl.post("/api/auth/login", json={"email": email, "password": password})


def err(r):
    """Ошибка API: в проде detail отдаётся полем message (app.http_exception_handler)."""
    try:
        d = r.json()
    except Exception:
        return {}
    x = d.get("detail") or d.get("message")
    return x if isinstance(x, dict) else {"message": x}


# ── добавить сотрудника
r = cl.get("/api/team", headers=HO)
j = r.json() if r.status_code == 200 else {}
check("список сотрудников: 200, цена 150, до 10", r.status_code == 200 and j.get("seat_som") == 150
      and j.get("max_members") == 10 and j.get("members") == [], r.text[:200])
perm_keys = {p["key"]: p for p in j.get("permissions") or []}
check("галочки: запись доступна салону", perm_keys.get("booking", {}).get("available") is True, perm_keys.get("booking"))

r = cl.post("/api/team", headers=HO, json={"name": "Асель", "email": "  Asel@Salon.KG ", "role_title": "Администратор",
                                           "permissions": ["inbox", "crm", "catalog", "analytics"]})
j = r.json() if r.status_code == 200 else {}
asel_pw = j.get("password")
asel_id = (j.get("member") or {}).get("id")
check("сотрудник добавлен, пароль показан", r.status_code == 200 and asel_pw and asel_id
      and j["member"]["email"] == "asel@salon.kg", r.text[:300])
check("место списано с кошелька: 1000 → 850", wallet(owner) == 850, wallet(owner))
tx = db.query(WalletTransaction).filter(WalletTransaction.user_id == owner.id, WalletTransaction.type == "team").all()
check("операция кошелька «team» с именем", len(tx) == 1 and tx[0].amount == -15000 and "Асель" in (tx[0].note or ""),
      [(t.amount, t.note) for t in tx])
m = db.query(TeamMember).filter(TeamMember.id == uuid.UUID(asel_id)).first()
check("место оплачено на 30 дней", m.paid_until and 29 <= (m.paid_until - now).days <= 30, m.paid_until)

for body, code, name in [
    ({"name": "Дубль", "email": "asel@salon.kg", "permissions": ["inbox"]}, "email_taken", "повтор email сотрудника"),
    ({"name": "Владелец2", "email": "poor@shop.kg", "permissions": ["inbox"]}, "email_taken", "email чужого кабинета"),
    ({"name": "Без прав", "email": "x1@salon.kg", "permissions": []}, "no_permissions", "без разделов"),
    ({"name": "Плохой", "email": "not-an-email", "permissions": ["inbox"]}, "invalid_email", "плохой email"),
    ({"name": "Тест", "email": "x@site.local", "permissions": ["inbox"]}, "invalid_email", "email, с которым не войти"),
    ({"name": " ", "email": "x2@salon.kg", "permissions": ["inbox"]}, "name_required", "без имени"),
    ({"name": "Короткий", "email": "x3@salon.kg", "password": "123", "permissions": ["inbox"]}, "weak_password", "короткий пароль"),
]:
    r = cl.post("/api/team", headers=HO, json=body)
    d = err(r)
    check(f"отказ: {name}", r.status_code == 400 and d.get("error") == code, (r.status_code, r.text[:160]))
check("отказы ничего не списали", wallet(owner) == 850, wallet(owner))

r = cl.post("/api/team", headers=HP, json={"name": "Нурик", "email": "nur@shop.kg", "permissions": ["inbox"]})
d = err(r)
check("мало денег на кошельке: 402, сотрудник не создан", r.status_code == 402 and d.get("error") == "wallet_low"
      and "пополните" in d.get("message", "") and db.query(TeamMember).filter(TeamMember.owner_id == poor.id).count() == 0,
      r.text[:200])
check("кошелёк бедного не тронут", wallet(poor) == 100, wallet(poor))
r = cl.post("/api/team", headers=HN, json={"name": "Нурик", "email": "nur2@shop.kg", "permissions": ["inbox"]})
check("без тарифа: 402 plan_required", r.status_code == 402 and err(r).get("error") == "plan_required",
      r.text[:200])

# ── вход сотрудника
r = login("ASEL@salon.kg", asel_pw)
j = r.json() if r.status_code == 200 else {}
HS = {"Authorization": f"Bearer {j.get('token')}"}
check("сотрудник вошёл на общей форме", r.status_code == 200 and j.get("token") and j.get("redirect_to") == "/static/inbox.html"
      and (j.get("staff") or {}).get("name") == "Асель", r.text[:300])
r = login("asel@salon.kg", "wrong-password")
check("неверный пароль сотрудника — общий отказ", r.status_code == 401 and "Invalid" in r.text, r.text[:120])

r = cl.get("/api/users/me", headers=HS)
me = r.json() if r.status_code == 200 else {}
check("/me сотрудника: его имя и почта, без ключей и админки", r.status_code == 200 and me.get("email") == "asel@salon.kg"
      and me.get("first_name") == "Асель" and me.get("openai_api_key") is None and me.get("is_admin") is False
      and (me.get("staff") or {}).get("permissions") == ["inbox", "crm", "catalog", "analytics"], r.text[:300])
check("/me сотрудника: режим и ниша владельца (меню)", me.get("agent_mode") == "reactive"
      and (me.get("business") or {}).get("niche") == "beauty_salon", (me.get("agent_mode"), me.get("business")))
check("/me сотрудника: меню по разделам", [x["id"] for x in (me.get("staff") or {}).get("menu", [])]
      == ["inbox", "crm", "catalog", "analytics"], (me.get("staff") or {}).get("menu"))
r = cl.get("/api/users/me", headers=HO)
check("/me владельца не изменился", r.status_code == 200 and r.json().get("staff") is None
      and r.json().get("openai_api_key") == "sk-secret-owner", r.text[:200])

# ── разрешено своё, остальное 403
r = cl.get("/api/inbox/overview", headers=HS)
check("сотрудник: инбокс открыт, отмечен staff", r.status_code == 200 and r.json().get("staff") is True, r.text[:200])
for method, path, name in [
    ("GET", "/api/wallet/balance", "кошелёк"), ("POST", "/api/wallet/topup", "пополнение"),
    ("GET", "/api/agent/", "настройки бота"), ("PUT", "/api/agent/", "правка бота"),
    ("GET", "/api/team", "сотрудники"), ("POST", "/api/team", "добавить сотрудника"),
    ("GET", "/api/admin/clients", "админка"), ("GET", "/api/dashboard/summary", "дашборд с кошельком"),
    ("DELETE", f"/api/agent/contacts/{contact.id}", "удалить клиента"), ("GET", "/api/users/api-key", "API-ключ"),
    ("PUT", "/api/users/me/password", "пароль владельца"), ("DELETE", "/api/users/me", "удалить аккаунт"),
    ("POST", "/api/agent/chat", "чат с оркестратором"), ("GET", "/api/booking/overview", "запись без права"),
]:
    r = cl.request(method, path, headers=HS, json={})
    check(f"сотруднику закрыто: {name} → 403", r.status_code == 403 and "staff_forbidden" in r.text, (r.status_code, r.text[:120]))

r = cl.get("/api/agent/list", headers=HS)
j = r.json() if r.status_code == 200 else {}
check("список ботов сотруднику — без промпта и инструкций", r.status_code == 200 and j.get("agents")
      and "orchestrator_prompt" not in j["agents"][0] and "СЕКРЕТНЫЙ" not in r.text and "инструкции владельца" not in r.text,
      r.text[:200])
r = cl.get("/api/agent/contacts", headers=HS)
check("CRM: клиенты владельца видны сотруднику", r.status_code == 200 and "Жылдыз" in r.text, r.text[:200])
r = cl.put(f"/api/agent/contacts/{contact.id}", headers=HS, json={"notes": "перезвонить после 18"})
check("CRM: сотрудник правит карточку", r.status_code == 200, (r.status_code, r.text[:160]))

r = cl.post("/api/agent/knowledge", headers=HS, json={"kind": "product", "title": "Маникюр", "price": 800})
pid = ((r.json() or {}).get("entry") or {}).get("id") if r.status_code == 200 else None
check("каталог: сотрудник добавляет позицию", r.status_code == 200 and pid, r.text[:160])
r = cl.put(f"/api/agent/knowledge/{pid}", headers=HS, json={"price": 900})
check("каталог: сотрудник меняет цену позиции", r.status_code == 200, r.text[:160])
r = cl.post("/api/agent/knowledge", headers=HS, json={"kind": "qa", "title": "Вопрос", "body": "Ответ"})
check("каталог: «вопрос-ответ» сотруднику нельзя", r.status_code == 403, r.text[:160])
r = cl.put(f"/api/agent/knowledge/{qa.id}", headers=HS, json={"body": "взлом"})
check("каталог: править «вопрос-ответ» нельзя", r.status_code == 403, r.text[:160])
r = cl.delete(f"/api/agent/knowledge/{qa.id}", headers=HS)
check("каталог: удалить «вопрос-ответ» нельзя", r.status_code == 403, r.text[:160])
r = cl.delete(f"/api/agent/knowledge/{pid}", headers=HS)
check("каталог: удалить свою позицию можно", r.status_code == 200, r.text[:160])

r = cl.get("/api/analytics/report?period=7d&sections=kpis,money", headers=HS)
check("аналитика сотруднику — без расходов", r.status_code == 200 and r.json().get("money") is None, r.text[:200])
r = cl.get("/api/analytics/report?period=7d&sections=kpis,money", headers=HO)
check("аналитика владельцу — с расходами", r.status_code == 200 and r.json().get("money") is not None, r.text[:200])

# ── личные настройки инбокса и подпись ответа
r = cl.put("/api/inbox/settings", headers=HS, json={"sound": False, "my_language": "ky"})
check("сотрудник сохранил свои настройки инбокса", r.status_code == 200 and r.json().get("sound") is False, r.text[:200])
r = cl.get("/api/inbox/settings", headers=HO)
check("у владельца настройки прежние", r.status_code == 200 and r.json().get("sound") is True
      and r.json().get("my_language") != "ky", r.text[:200])
r = cl.get("/api/inbox/settings", headers=HS)
check("у сотрудника — свои", r.status_code == 200 and r.json().get("sound") is False and r.json().get("my_language") == "ky")
from backend.services import inbox_service  # noqa: E402
from backend.models.inbox import InboxTranslation  # noqa: E402
from backend.models.agent_whatsapp import AgentWhatsAppMessage  # noqa: E402
msg = AgentWhatsAppMessage(agent_config_id=agent.id, agent_contact_id=contact.id, direction="outbound", sent_by="owner",
                           body="Здравствуйте! Ждём вас в 15:00")
db.add(msg)
db.commit()
inbox_service._save_translation(db, "whatsapp", msg.id, "author", "Асель", None)
tl = inbox_service.timeline(db, contact)
item = next((x for x in tl.get("items", []) if x.get("id") == str(msg.id)), {})
check("в ленте ответ сотрудника подписан его именем", item.get("author") == "Асель" and item.get("who") == "owner", item)

# ── вход владельца с телефона сотрудника (лендинг шлёт сохранённый токен)
r = cl.post("/api/auth/login", headers=HS, json={"email": "owner@salon.kg", "password": "Passw0rd!"})
check("владелец входит, даже если в браузере токен сотрудника", r.status_code == 200 and not r.json().get("staff"), r.text[:200])

# ── выключить → токен 401, вход — понятный отказ; включить — без нового списания
r = cl.post(f"/api/team/{asel_id}/deactivate", headers=HO)
check("выключить сотрудника", r.status_code == 200 and r.json()["member"]["is_active"] is False, r.text[:200])
r = cl.get("/api/inbox/overview", headers=HS)
check("выключенный: старый токен 401", r.status_code == 401, (r.status_code, r.text[:120]))
r = login("asel@salon.kg", asel_pw)
check("выключенный: вход 403 с причиной", r.status_code == 403 and "выключил" in r.text, r.text[:200])
before = wallet(owner)
r = cl.post(f"/api/team/{asel_id}/activate", headers=HO)
check("включить в оплаченный срок — без списания", r.status_code == 200 and r.json()["member"]["is_active"] is True
      and wallet(owner) == before, (r.status_code, wallet(owner)))

# ── новый пароль от владельца
r = login("asel@salon.kg", asel_pw)
HS = {"Authorization": f"Bearer {r.json().get('token')}"}
r = cl.post(f"/api/team/{asel_id}/password", headers=HO, json={})
new_pw = r.json().get("password") if r.status_code == 200 else None
check("владелец выдал новый пароль", r.status_code == 200 and new_pw and new_pw != asel_pw, r.text[:200])
r = cl.get("/api/users/me", headers=HS)
check("после смены пароля старый токен 401", r.status_code == 401, r.status_code)
check("старый пароль не подходит", login("asel@salon.kg", asel_pw).status_code == 401)
r = login("asel@salon.kg", new_pw)
HS = {"Authorization": f"Bearer {r.json().get('token')}"}
check("новый пароль подходит", r.status_code == 200)

# ── сотрудник сам меняет пароль
r = cl.post("/api/team/me/password", headers=HS, json={"current_password": "nope", "new_password": "newpass77"})
check("свой пароль: неверный текущий — 400", r.status_code == 400 and "неверный" in r.text, r.text[:160])
r = cl.post("/api/team/me/password", headers=HS, json={"current_password": new_pw, "new_password": "newpass77"})
tok2 = r.json().get("token") if r.status_code == 200 else None
check("свой пароль сменён, выдан новый токен", r.status_code == 200 and tok2, r.text[:160])
check("старый токен сотрудника после своей смены — 401", cl.get("/api/users/me", headers=HS).status_code == 401)
HS = {"Authorization": f"Bearer {tok2}"}
check("новый токен работает", cl.get("/api/users/me", headers=HS).status_code == 200)
r = cl.get("/api/team/me", headers=HS)
check("/api/team/me — кто я", r.status_code == 200 and (r.json().get("staff") or {}).get("name") == "Асель", r.text[:160])

# ── права: только запись
r = cl.post("/api/team", headers=HO, json={"name": "Мастер Бек", "email": "bek@salon.kg", "password": "master77",
                                           "permissions": ["booking"]})
bek_id = (r.json().get("member") or {}).get("id") if r.status_code == 200 else None
check("второй сотрудник со своим паролем", r.status_code == 200 and r.json().get("password") == "master77", r.text[:200])
HB = {"Authorization": f"Bearer {login('bek@salon.kg', 'master77').json().get('token')}"}
r = cl.get("/api/agent/contacts?search=Жыл", headers=HB)
check("запись: поиск клиента разрешён", r.status_code == 200, r.status_code)
r = cl.post("/api/agent/contacts", headers=HB, json={"name": "Новый"})
check("запись: создать клиента в CRM — нет", r.status_code == 403, r.status_code)
r = cl.get("/api/booking/overview", headers=HB)
check("запись: календарь открыт", r.status_code == 200, (r.status_code, r.text[:160]))
r = cl.put("/api/booking/settings", headers=HB, json={"step_min": 15})
check("запись: правила записи — нет (нужно «Мастера и график»)", r.status_code == 403, r.status_code)
r = cl.patch(f"/api/team/{bek_id}", headers=HO, json={"permissions": ["booking", "booking_setup"]})
check("владелец добавил раздел", r.status_code == 200 and r.json()["member"]["permissions"] == ["booking", "booking_setup"])
r = cl.put("/api/booking/settings", headers=HB, json={"step_min": 15})
check("новый раздел работает сразу (кэш воркера сброшен)", r.status_code == 200, (r.status_code, r.text[:160]))

# ── 10 сотрудников максимум
db.expire_all()
db.query(User).filter(User.id == owner.id).first().wallet_balance = 150000   # 1500 сом
db.commit()
for i in range(8):
    r = cl.post("/api/team", headers=HO, json={"name": f"Сотрудник {i}", "email": f"s{i}@salon.kg", "permissions": ["inbox"]})
check("до 10 сотрудников", db.query(TeamMember).filter(TeamMember.owner_id == owner.id).count() == 10)
r = cl.post("/api/team", headers=HO, json={"name": "Лишний", "email": "s99@salon.kg", "permissions": ["inbox"]})
check("11-й — отказ «до 10»", r.status_code == 400 and err(r).get("error") == "limit", r.text[:160])
check("8 мест оплачено: 1500 − 8×150", wallet(owner) == 300, wallet(owner))
r = cl.get("/api/team", headers=HO)
check("в месяц 1500 сом за 10 мест", r.status_code == 200 and r.json().get("monthly_som") == 1500, r.json().get("monthly_som"))

# ── удаление
r = cl.delete(f"/api/team/{bek_id}", headers=HO)
check("удалить сотрудника", r.status_code == 200 and db.query(TeamMember).filter(TeamMember.id == uuid.UUID(bek_id)).count() == 0)
check("удалённый: токен 401", cl.get("/api/booking/overview", headers=HB).status_code == 401)
check("удалённый: вход как у незнакомца", login("bek@salon.kg", "master77").status_code == 401)

# ── продление мест
db.expire_all()
ms = db.query(TeamMember).filter(TeamMember.owner_id == owner.id).order_by(TeamMember.created_at).all()
for x in ms[:3]:
    x.paid_until = now - timedelta(hours=1)
db.commit()
owner_row = db.query(User).filter(User.id == owner.id).first()
owner_row.wallet_balance = 30000   # 300 сом: хватит на два продления из трёх
db.commit()
res = ts.renew_due_seats()
check("продление: два продлено, один выключен", res["renewed"] == 2 and res["disabled"] == 1, res)
check("продление: списано 300", wallet(owner) == 0, wallet(owner))
db.expire_all()
ms = db.query(TeamMember).filter(TeamMember.owner_id == owner.id).order_by(TeamMember.created_at).all()
renewed = [x for x in ms[:3] if x.is_active]
off = [x for x in ms[:3] if not x.is_active]
check("продлённым — новый срок от старого", all(29 <= (x.paid_until - now).days <= 30 for x in renewed),
      [x.paid_until for x in renewed])
check("не хватило — выключен «не оплачено»", len(off) == 1 and off[0].status_reason == "unpaid", [(x.name, x.status_reason) for x in off])
check("владельцу — сообщение про отключение", res["notify"] and "не хватило" in res["notify"][0][1], res["notify"])
r = login(off[0].email, "anything")
check("второй прогон ничего не списывает", ts.renew_due_seats()["renewed"] == 0 and wallet(owner) == 0)
r = cl.post(f"/api/team/{off[0].id}/activate", headers=HO)
check("включить неоплаченного без денег — 402", r.status_code == 402, (r.status_code, r.text[:160]))
owner_row = db.query(User).filter(User.id == owner.id).first()
owner_row.wallet_balance = 20000
db.commit()
r = cl.post(f"/api/team/{off[0].id}/activate", headers=HO)
check("после пополнения включён и оплачен", r.status_code == 200 and r.json()["member"]["is_active"] is True
      and wallet(owner) == 50, (r.status_code, wallet(owner)))

# тариф владельца кончился
db.expire_all()
x = db.query(TeamMember).filter(TeamMember.owner_id == owner.id, TeamMember.is_active.is_(True)).first()
x.paid_until = now - timedelta(minutes=5)
owner_row = db.query(User).filter(User.id == owner.id).first()
owner_row.subscription_end_date = now - timedelta(days=1)
owner_row.subscription_plan_id = None
db.commit()
res = ts.renew_due_seats()
db.expire_all()
x = db.query(TeamMember).filter(TeamMember.id == x.id).first()
check("тариф кончился — выключен «тариф», без списания", res["disabled"] == 1 and x.status_reason == "plan"
      and wallet(owner) == 50, (res, x.status_reason))

# ── админ: места бесплатно
r = cl.post("/api/team", headers=HA, json={"name": "Помощник", "email": "help@voksy.kg", "permissions": ["inbox", "analytics"]})
check("у админа место без списания", r.status_code == 200 and r.json()["member"]["paid_until"] is None and wallet(admin) == 0,
      r.text[:200])
HAS = {"Authorization": f"Bearer {login('help@voksy.kg', r.json().get('password')).json().get('token')}"}
r = cl.get("/api/admin/clients", headers=HAS)
check("сотрудник админа не получает админку", r.status_code == 403, r.status_code)

# ── вход админа в кабинет клиента: места за клиента не оплачивает
HI = {"Authorization": f"Bearer {create_impersonation_token(poor.id, admin.id)}"}
db.expire_all()
pr = db.query(User).filter(User.id == poor.id).first()
pr.wallet_balance = 100000
db.commit()
r = cl.post("/api/team", headers=HI, json={"name": "Через админку", "email": "imp@shop.kg", "permissions": ["inbox"]})
check("админ в кабинете клиента: добавить сотрудника — 403", r.status_code == 403, (r.status_code, r.text[:160]))

# ── голосовой помощник, регистрация, сброс пароля
from backend.websockets import handler_owner_voice  # noqa: E402
tok = login("s5@salon.kg", "x").status_code   # неверный пароль — просто 401
staff_token = create_jwt_token(owner.id, extra={"staff": asel_id, "stv": 3})
u, a = handler_owner_voice._load_owner(db, staff_token, None)
check("голосовой помощник владельца сотрудника не пускает", u is None and a is None)
u, a = handler_owner_voice._load_owner(db, create_jwt_token(owner.id), None)
check("…а владельца пускает", u is not None)
r = cl.post("/api/auth/register", json={"email": "asel@salon.kg", "password": "Passw0rd!", "first_name": "Асель",
                                        "phone": "+996700000001"})
check("регистрация на email сотрудника — отказ", r.status_code == 400 and "сотрудник" in r.text, r.text[:200])
r = cl.post("/api/auth/reset-password", json={"email": "asel@salon.kg"})
check("сброс пароля сотрудника — к владельцу", r.status_code == 400 and "владелец" in r.text, r.text[:200])

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
