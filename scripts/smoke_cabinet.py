"""
Дымовой тест кабинета «как клиент»: настоящее приложение (app.py) + настоящая PostgreSQL.

Что делает:
  1. заводит четырёх пользователей — пробный период, Старт, Profi, админ;
  2. создаёт Profi-агента и ElevenLabs-ассистента через API (как мастер в кабинете);
  3. обходит КАЖДЫЙ GET-маршрут приложения (параметры пути подставляются из созданных объектов)
     от имени каждого пользователя и каждую страницу кабинета /static/*.html;
  4. считает провалом любой ответ 5xx и любое исключение.

Запуск (база будет ИЗМЕНЕНА — только тестовая):
    SMOKE_DATABASE_URL=postgresql://... python scripts/smoke_cabinet.py

Без SMOKE_DATABASE_URL тест пропускается (код 0).
"""
import os
import sys
import uuid
import re
from datetime import datetime, timedelta, timezone

DB = os.environ.get("SMOKE_DATABASE_URL")
if not DB:
    print("SKIP: SMOKE_DATABASE_URL не задан")
    sys.exit(0)

os.environ["DATABASE_URL"] = DB
os.environ.setdefault("JWT_SECRET_KEY", "smoke-secret-0123456789")
os.environ.setdefault("HOST_URL", "https://smoke.voksyai.online")
os.environ.setdefault("PRODUCTION", "true")
os.environ.setdefault("OPENAI_API_KEY", "sk-smoke")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.core.config  # noqa: E402,F401  (первым — иначе циклический импорт)
import logging  # noqa: E402

logging.disable(logging.WARNING)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app as app_module  # noqa: E402
from backend.models.base import Base  # noqa: E402
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.core.security import create_jwt_token, hash_password  # noqa: E402

engine = create_engine(DB)
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine)
db = S()

fails = []
checked = 0


def plan(code, price):
    p = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).first()
    if not p:
        p = SubscriptionPlan(code=code, name=code, price=price, max_assistants=5, description=code, is_active=True)
        db.add(p)
        db.flush()
    return p


def user(tag, plan_code=None, admin=False, trial=False):
    u = User(email=f"smoke-{tag}-{uuid.uuid4().hex[:6]}@example.com", password_hash=hash_password("Passw0rd!"),
             first_name=tag, email_verified=True, is_admin=admin, credits_balance=5000)
    if plan_code:
        u.subscription_plan_id = plan(plan_code, 100).id
        u.subscription_end_date = datetime.now(timezone.utc) + timedelta(days=20)
        u.is_trial = trial
    db.add(u)
    db.flush()
    return u


users = {
    "trial": user("trial", "free", trial=True),
    "start": user("start", "start"),
    "profi": user("profi", "profi"),
    "admin": user("admin", admin=True),
}
db.commit()

client = TestClient(app_module.app, raise_server_exceptions=False)


def hdr(u):
    return {"Authorization": f"Bearer {create_jwt_token(u.id)}"}


def call(method, path, u=None, **kw):
    global checked
    checked += 1
    try:
        r = client.request(method, path, headers=hdr(u) if u else {}, **kw)
    except Exception as e:  # noqa: BLE001
        fails.append(f"{method} {path} [{u.first_name if u else 'anon'}] EXC {type(e).__name__}: {e}")
        return None
    if r.status_code >= 500 and not (r.status_code == 503 and "/voices" in path):  # 503 = нет ключа ElevenLabs
        fails.append(f"{method} {path} [{u.first_name if u else 'anon'}] {r.status_code}: {r.text[:300]}")
    return r


# --- 0. регистрация нового клиента, как на сайте: регистрация → код на почту → подтверждение → вход ---
from backend.services.email_service import EmailService  # noqa: E402
_codes = []
_orig_gen = EmailService._generate_verification_code


def _gen():
    c = _orig_gen()
    _codes.append(c)
    return c

EmailService._generate_verification_code = staticmethod(_gen)
EmailService._send_email_smtp = staticmethod(lambda *a, **k: None)
new_email = f"newclient-{uuid.uuid4().hex[:6]}@example.com"
r = call("POST", "/api/auth/register", json={"email": new_email, "password": "Passw0rd!x", "first_name": "Айгерим"})
if r is None or r.status_code >= 400:
    fails.append(f"регистрация: {getattr(r, 'status_code', None)} {getattr(r, 'text', '')[:200]}")
r = call("POST", "/api/auth/login", json={"email": new_email, "password": "Passw0rd!x"})
if r is not None and r.status_code < 400 and (r.json().get("token") or r.json().get("access_token")):
    fails.append("вход без подтверждения почты выдал токен")
r = call("POST", "/api/email-verification/verify", json={"email": new_email, "code": _codes[-1] if _codes else "000000"})
tok = None
if r is not None and r.status_code < 400:
    tok = ((r.json().get("data") or {}).get("token")) or r.json().get("token")
if not tok:
    fails.append(f"подтверждение кода: {getattr(r, 'status_code', None)} {getattr(r, 'text', '')[:200]}")
r = call("POST", "/api/auth/login", json={"email": new_email, "password": "Passw0rd!x"})
if r is None or r.status_code >= 400:
    fails.append(f"вход после подтверждения: {getattr(r, 'status_code', None)} {getattr(r, 'text', '')[:200]}")
else:
    j = r.json()
    login_tok = j.get("token") or j.get("access_token") or (j.get("data") or {}).get("token")
    me = client.get("/api/users/me", headers={"Authorization": f"Bearer {login_tok}"})
    if me.status_code != 200:
        fails.append(f"кабинет нового клиента /api/users/me: {me.status_code} {me.text[:200]}")
    else:
        print("новый клиент:", {k: me.json().get(k) for k in ("email", "agent_mode", "subscription_plan")})
    # Регистр букв в email (12.11.2026): телефон пишет первую букву заглавной — вход строчными должен
    # работать, и наоборот; повторная регистрация тем же адресом в другом регистре — не второй аккаунт.
    for variant in (new_email.upper(), new_email.capitalize()):
        rv = call("POST", "/api/auth/login", json={"email": variant, "password": "Passw0rd!x"})
        if rv is None or rv.status_code >= 400:
            fails.append(f"вход с email в другом регистре ({variant!r}): {getattr(rv, 'status_code', None)}")
_cap_email = f"Mobile.Client-{uuid.uuid4().hex[:6]}@Gmail.com"
call("POST", "/api/auth/register", json={"email": _cap_email, "password": "Passw0rd!x", "first_name": "Мирлан"})
rv = call("POST", "/api/email-verification/verify", json={"email": _cap_email.lower(), "code": _codes[-1] if _codes else "0"})
if rv is None or rv.status_code >= 400:
    fails.append(f"подтверждение кода строчными после регистрации с заглавной: {getattr(rv, 'status_code', None)} "
                 f"{getattr(rv, 'text', '')[:200]}")
rv = call("POST", "/api/auth/login", json={"email": _cap_email.lower(), "password": "Passw0rd!x"})
if rv is None or rv.status_code >= 400:
    fails.append(f"вход строчными после регистрации с заглавной буквы: {getattr(rv, 'status_code', None)} "
                 f"{getattr(rv, 'text', '')[:200]}")
rv = call("POST", "/api/auth/register", json={"email": _cap_email.lower(), "password": "Passw0rd!x"})
if rv is None or rv.status_code < 400:
    fails.append("повторная регистрация тем же email в другом регистре создала второй аккаунт")
if r is not None and r.status_code < 400:
    for pth in ("/api/dashboard/summary", "/api/wallet/balance", "/api/subscriptions/my-subscription",
                "/api/eleven-assistants/", "/api/agent/list"):
        rr = client.get(pth, headers={"Authorization": f"Bearer {login_tok}"})
        if rr.status_code >= 500:
            fails.append(f"новый клиент {pth}: {rr.status_code} {rr.text[:200]}")

# --- 1. создать агента Profi и ассистента, как в кабинете ---------------------------------------------
ids = {}
DOCS = {"doc_who_am_i": "Я Анри, менеджер компании Смоук", "doc_who_we_call": "Владельцы малого бизнеса",
        "doc_how_we_talk": "Коротко и по делу", "doc_what_we_offer": "Голосовой агент, 7500 сом",
        "doc_rules_and_goals": "Записать на демо"}
for tag in ("profi", "start", "trial"):
    r = call("POST", "/api/agent/create", users[tag],
             json={"name": f"Агент {tag}", "assistant_type": "eleven", "eleven_language": "ru", **DOCS})
    if r is not None:
        if r.status_code >= 400:
            fails.append(f"create agent {tag}: {r.status_code} {r.text[:200]}")
        else:
            data = r.json()
            ids[f"agent_{tag}"] = str(data.get("id") or data.get("agent_id") or (data.get("agent") or {}).get("id") or "")

r = call("POST", "/api/eleven-assistants/", users["profi"],
         json={"name": "Смоук", "system_prompt": "Ты помощник", "greeting_message": "Здравствуйте!"})
if r is not None and r.status_code < 400:
    ids["assistant"] = str(r.json().get("id"))
elif r is not None:
    print(f"create eleven assistant: {r.status_code} {r.text[:200]}")

r = call("POST", "/api/agent/contacts", users["profi"], json={"name": "Клиент Смоук", "phone": "+996700123456"})
if r is not None and r.status_code < 400:
    j = r.json()
    ids["contact"] = str(j.get("id") or (j.get("contact") or {}).get("id") or "")
elif r is not None:
    print(f"create contact: {r.status_code} {r.text[:200]}")

r = call("POST", "/api/agent/knowledge", users["profi"],
         json={"kind": "qa", "title": "Сколько стоит?", "body": "Старт 7500 сом в месяц"})
if r is not None and r.status_code >= 400:
    fails.append(f"knowledge add: {r.status_code} {r.text[:200]}")
if ids.get("contact"):
    c = ids["contact"]
    for m, pth, body in (
        ("POST", f"/api/agent/contacts/{c}/tasks", {"title": "Перезвонить", "channel": "owner",
                                                     "scheduled_time": (datetime.utcnow() + timedelta(hours=2)).isoformat()}),
        ("POST", f"/api/agent/contacts/{c}/ai-pause", {"minutes": 60}),
        ("DELETE", f"/api/agent/contacts/{c}/ai-pause", None),
    ):
        r = call(m, pth, users["profi"], json=body) if body is not None else call(m, pth, users["profi"])
        if r is not None and r.status_code >= 400:
            fails.append(f"{m} {pth}: {r.status_code} {r.text[:200]}")
r = call("PUT", "/api/agent/", users["profi"], json={"additional_instructions": "Пиши коротко",
                                                     "voice_additional_instructions": "Говори спокойно"})
if r is not None and r.status_code >= 400:
    fails.append(f"PUT /api/agent/: {r.status_code} {r.text[:200]}")
print("созданы:", ids)

# --- 2. все GET-маршруты от имени каждого пользователя --------------------------------------------------
SKIP_PREFIX = ("/ws", "/docs", "/redoc", "/openapi")
SKIP_PATH = re.compile(r"(stream|/events|sse|download|export|recording|/qr$|/log$)")


def fill(path):
    def rep(m):
        name = m.group(1).split(":")[0]
        if "contact" in name:
            return ids.get("contact", str(uuid.uuid4()))
        if "assistant" in name:
            return ids.get("assistant", str(uuid.uuid4()))
        if name in ("agent_id",):
            return ids.get("agent_profi", str(uuid.uuid4()))
        if name in ("type", "assistant_type"):
            return "eleven"
        if name in ("email",):
            return "nobody@example.com"
        if name in ("plan", "plan_code", "code"):
            return "start"
        if name in ("phone", "caller"):
            return "+996700123456"
        if name in ("lang",):
            return "ru"
        return str(uuid.uuid4())
    return re.sub(r"\{([^}]+)\}", rep, path)


routes = []
for rt in app_module.app.routes:
    methods = getattr(rt, "methods", None) or set()
    path = getattr(rt, "path", "")
    if "GET" in methods and not path.startswith(SKIP_PREFIX) and not SKIP_PATH.search(path):
        routes.append(path)
routes = sorted(set(routes))

for path in routes:
    p = fill(path)
    for tag in ("trial", "start", "profi", "admin"):
        call("GET", p, users[tag])
    call("GET", p, None)

# --- 3. страницы кабинета ----------------------------------------------------------------------------------
static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "static")
for name in sorted(os.listdir(static_dir)):
    if name.endswith(".html"):
        r = call("GET", f"/static/{name}")

for p in ("/", "/ru/", "/voice-agent/", "/ru/whatsapp-chatbot/", "/robots.txt", "/sitemap.xml", "/llms.txt"):
    r = call("GET", p)
    if r is not None and r.status_code >= 400:
        fails.append(f"GET {p} [anon] {r.status_code}")

print(f"маршрутов GET: {len(routes)}, запросов: {checked}")
for f in fails:
    print("FAIL", f)
print(f"ПРОВАЛОВ: {len(fails)}")
sys.exit(1 if fails else 0)
