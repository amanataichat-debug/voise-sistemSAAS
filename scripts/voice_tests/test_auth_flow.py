"""Регистрация и вход (13.11.2026: «клиенты не могут ни зарегистрироваться, ни войти»).

Причины, которые закрывает тест:
- письмо с кодом не ушло → клиент оставался без входа (теперь впускаем, AUTH_EMAIL_FALLBACK);
- повторная регистрация неподтверждённого email оставляла пароль ПЕРВОЙ попытки → «верный» пароль не подходил;
- вход отвергал пароль короче 8 символов ещё до проверки;
- кривой реферальный код / UTM из ссылки ломал регистрацию с этого телефона.

Нужен PostgreSQL: AUTH_TEST_DATABASE_URL (или CATALOG_TEST_DATABASE_URL). Без него — SKIP.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("AUTH_TEST_DATABASE_URL") or os.environ.get("CATALOG_TEST_DATABASE_URL")
if not PG:
    print("SKIP: AUTH_TEST_DATABASE_URL не задан")
    print("ПРОВАЛОВ: 0")
    sys.exit(0)
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG, PRODUCTION="true", OPENAI_API_KEY="k",
                  JWT_SECRET_KEY="auth-test-secret-0123456789")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: E402,F401
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.core.security import hash_password  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)

from fastapi.testclient import TestClient  # noqa: E402
from app import app  # noqa: E402
from backend.services import email_service  # noqa: E402
import app as app_module  # noqa: E402

cl = TestClient(app)
mail = {"ok": True, "sent": []}
orig_send = email_service.EmailService.send_verification_code


async def fake_send(db, user_id, user_email):
    if not mail["ok"]:
        raise RuntimeError("SMTP down")
    mail["sent"].append(user_email)
    return {"success": True, "expires_in_minutes": 10, "max_attempts": 3}


email_service.EmailService.send_verification_code = staticmethod(fake_send)


def post(path, body):
    app_module._auth_hits.clear()   # лимит попыток с одного IP здесь не проверяем
    return cl.post(path, json=body)


# 1. Почта работает: код ушёл, токена нет; вход неподтверждённого → 403 и свежий код
r = post("/api/auth/register", {"email": "Bek@Gmail.com ", "password": "Norka2026", "first_name": "Бек"})
check("регистрация: код отправлен, без токена", r.status_code in (200, 201) and r.json().get("verification_sent") and not r.json().get("token"), r.text[:200])
mail["sent"].clear()
r = post("/api/auth/login", {"email": "bek@gmail.com", "password": "Norka2026"})
check("вход неподтверждённого: 403 «не подтверждён» + новый код", r.status_code == 403 and "not verified" in r.text and mail["sent"] == ["bek@gmail.com"], (r.status_code, mail["sent"]))

# 2. Письмо снова не ушло, клиент регистрируется ЗАНОВО с другим паролем → впускаем, новый пароль работает
mail["ok"] = False
r = post("/api/auth/register", {"email": "bek@gmail.com", "password": "Shapka777", "first_name": "Бек"})
j = r.json()
check("повторная регистрация при сбое почты: токен сразу", r.status_code in (200, 201) and j.get("token") and j.get("email_fallback"), r.text[:200])
r = post("/api/auth/login", {"email": "BEK@gmail.com", "password": "Shapka777"})
check("вход с НОВЫМ паролем", r.status_code == 200 and r.json().get("token"), r.text[:200])
r = post("/api/auth/login", {"email": "bek@gmail.com", "password": "Norka2026"})
check("старый пароль больше не подходит", r.status_code == 401, r.status_code)

# 3. Новая регистрация при сбое почты — сразу внутрь
r = post("/api/auth/register", {"email": "new@mail.ru", "password": "Market2026"})
check("новая регистрация при сбое почты: токен", r.status_code in (200, 201) and r.json().get("token"), r.text[:200])
db = SessionLocal()
u = db.query(User).filter(User.email == "new@mail.ru").first()
check("почта отмечена подтверждённой", u is not None and u.email_verified is True)

# 4. Неподтверждённый пользователь, почта лежит → вход впускает
mail["ok"] = True
post("/api/auth/register", {"email": "late@gmail.com", "password": "Bazar2026"})
mail["ok"] = False
r = post("/api/auth/login", {"email": "late@gmail.com", "password": "Bazar2026"})
check("вход неподтверждённого при сбое почты: впускаем", r.status_code == 200 and r.json().get("token"), r.text[:200])
r = post("/api/auth/login", {"email": "late@gmail.com", "password": "wrong-pass1"})
check("неверный пароль по-прежнему 401", r.status_code == 401, r.status_code)

# 5. Старый короткий пароль (выдан админом) — вход работает
db.add(User(email="old@gmail.com", password_hash=hash_password("12345"), email_verified=True, is_active=True))
db.commit()
r = post("/api/auth/login", {"email": "old@gmail.com", "password": "12345"})
check("короткий старый пароль: вход", r.status_code == 200, r.text[:200])

# 6. Кривая партнёрская ссылка / UTM не ломают регистрацию
mail["ok"] = True
r = post("/api/auth/register", {"email": "ref@gmail.com", "password": "Ref12345", "referral_code": "BADCODE",
                                 "utm_data": {"utm_source": "partner", "utm_campaign": "BADCODE", "x": 5}})
check("кривой реф. код и UTM: регистрация проходит", r.status_code in (200, 201), r.text[:200])

# 7. Выключатель: AUTH_EMAIL_FALLBACK=0 — понятная ошибка, а не 500
os.environ["AUTH_EMAIL_FALLBACK"] = "0"
mail["ok"] = False
r = post("/api/auth/register", {"email": "strict@gmail.com", "password": "Strict2026"})
check("без запасного пути: 400 с понятным текстом по-русски", r.status_code == 400 and "почту" in r.text, r.text[:200])
os.environ.pop("AUTH_EMAIL_FALLBACK", None)

db.close()
email_service.EmailService.send_verification_code = orig_send
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
