"""Сообщение владельцу в Telegram о новой регистрации (ветка 1811-niche-admin-speed).

Проверяет на настоящей PostgreSQL с поддельным Telegram:
- уходит в бот агента админа (чаты владельца), с именем, телефоном, нишей и кнопками;
- ровно одно сообщение на клиента, даже если «воркеров» несколько (атомарный флаг);
- рано (меньше 3 минут) и давние регистрации — не трогает; админов — не трогает;
- Telegram не принял — флаг снимается, следующая проверка пробует снова;
- явные ADMIN_TELEGRAM_BOT_TOKEN / ADMIN_TELEGRAM_CHAT_IDS главнее бота агента;
- выключатель SIGNUP_TELEGRAM_NOTIFY=0.

Нужен PostgreSQL: ADMIN_TEST_DATABASE_URL (или AUTH_TEST_DATABASE_URL). Без него — SKIP.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("ADMIN_TEST_DATABASE_URL") or os.environ.get("AUTH_TEST_DATABASE_URL")
if not PG:
    print("SKIP: ADMIN_TEST_DATABASE_URL не задан")
    print("ПРОВАЛОВ: 0")
    sys.exit(0)
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG, PRODUCTION="true", OPENAI_API_KEY="k",
                  JWT_SECRET_KEY="signup-test-secret-0123456789abcdef")
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
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.services import signup_notify as sn  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)

sent = []
mode = {"ok": True}


async def fake_send(token, chat_id, text_html, keyboard):
    sent.append({"token": token, "chat": chat_id, "text": text_html, "kb": keyboard})
    return mode["ok"]

sn._send = fake_send
now = datetime.now(timezone.utc)
db = SessionLocal()
admin = User(email="boss@voksy.test", password_hash="x", is_admin=True, email_verified=True)
db.add(admin)
db.flush()
db.add(AgentConfig(user_id=admin.id, name="Анри", telegram_bot_token="111:AAA", telegram_enabled=True,
                   telegram_chat_ids=[{"chat_id": "5001", "name": "Владелец"}]))


def mk(email, minutes_ago, **kw):
    u = User(email=email, password_hash="x", email_verified=True, **kw)
    db.add(u)
    db.flush()
    u.created_at = now - timedelta(minutes=minutes_ago)
    return u


ready = mk("aibek@shop.kg", 5, first_name="Айбек", company_name="Норка KG", phone="+996700123456", business_niche="clothing")
early = mk("fresh@shop.kg", 1, first_name="Свежий", phone="+996555000111")
old = mk("old@shop.kg", 60 * 24 * 3, first_name="Старый")
other_admin = mk("admin2@voksy.test", 10, is_admin=True)
db.commit()

n = asyncio.run(sn.sweep_once())
check("ушло одно сообщение (готовый клиент)", n == 1 and len(sent) == 1, (n, len(sent)))
msg = sent[0] if sent else {}
check("в бот агента админа, в чат владельца", msg.get("token") == "111:AAA" and msg.get("chat") == "5001", msg.get("chat"))
txt = msg.get("text", "")
check("текст: имя, компания, телефон, ниша", "Айбек" in txt and "Норка KG" in txt and "+996 700 123 456" in txt
      and "Одежда и обувь" in txt, txt)
btns = [b for row in msg.get("kb", []) for b in row]
check("кнопки: админка с id клиента и WhatsApp", any("admin.html#client=" + str(ready.id) in b["url"] for b in btns)
      and any(b["url"].startswith("https://wa.me/996700123456") for b in btns), btns)

# два «воркера» одновременно — сообщение не дублируется
sent.clear()
async def both():
    return await asyncio.gather(sn.sweep_once(), sn.sweep_once())
asyncio.run(both())
check("повторная проверка и второй воркер: дублей нет", sent == [], len(sent))

# рано / давно / админ — не трогаем
db.expire_all()
check("рано (1 мин) — ещё ждём", db.get(User, early.id).signup_notified_at is None)
check("давняя регистрация — не трогаем", db.get(User, old.id).signup_notified_at is None)
check("админ — не трогаем", db.get(User, other_admin.id).signup_notified_at is None)

# выбор ниши раньше 3 минут — сразу
sent.clear()
db2 = SessionLocal()
ok = asyncio.run(sn.notify_signup(db2, early.id))
check("после выбора ниши — сразу, даже раньше 3 мин", ok and len(sent) == 1, (ok, len(sent)))
ok = asyncio.run(sn.notify_signup(db2, early.id))
check("второй раз — нет", ok is False and len(sent) == 1)

# Telegram не принял — флаг снимается, потом уходит
late = mk("late@shop.kg", 4, first_name="Поздний", phone="+996777111222")
db.commit()
mode["ok"] = False
sent.clear()
asyncio.run(sn.sweep_once())
db.expire_all()
check("Telegram не принял: флаг снят для повтора", db.get(User, late.id).signup_notified_at is None and len(sent) == 1)
mode["ok"] = True
sent.clear()
asyncio.run(sn.sweep_once())
db.expire_all()
check("следующая проверка отправила", db.get(User, late.id).signup_notified_at is not None and len(sent) == 1)

# явный бот из env главнее
os.environ["ADMIN_TELEGRAM_BOT_TOKEN"] = "999:ENV"
os.environ["ADMIN_TELEGRAM_CHAT_IDS"] = "77, 88"
env_user = mk("env@shop.kg", 4, first_name="Env")
db.commit()
sent.clear()
asyncio.run(sn.sweep_once())
check("ADMIN_TELEGRAM_*: в оба чата из env", sorted(m["chat"] for m in sent) == ["77", "88"]
      and all(m["token"] == "999:ENV" for m in sent), [(m["token"], m["chat"]) for m in sent])
os.environ.pop("ADMIN_TELEGRAM_BOT_TOKEN")
os.environ.pop("ADMIN_TELEGRAM_CHAT_IDS")

# выключатель
os.environ["SIGNUP_TELEGRAM_NOTIFY"] = "0"
mk("off@shop.kg", 4, first_name="Выкл")
db.commit()
sent.clear()
check("SIGNUP_TELEGRAM_NOTIFY=0: тишина", asyncio.run(sn.sweep_once()) == 0 and sent == [])
os.environ.pop("SIGNUP_TELEGRAM_NOTIFY")

db.close()
db2.close()
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
