"""
Механика без гонок (ветка 1111-clean-system) — на настоящем PostgreSQL:
  * задачу планировщика выполняет ровно один воркер (атомарный захват);
  * один разбор на клиента за раз (contact_lease);
  * WhatsApp: неотвеченный чат подбирает подметание (деплой посреди паузы), и только один раз;
  * кончились кредиты — владелец получает оповещение один раз в сутки.

Без базы часть пропускается:
    MECH_TEST_DATABASE_URL=postgresql://postgres@/voksy_test?host=/var/tmp/pgtest&port=5433 \
        python scripts/voice_tests/test_mechanics.py
"""
import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("MECH_TEST_DATABASE_URL")
os.environ.update(HOST_URL="http://x", DATABASE_URL=PG or "sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")
logging.disable(logging.CRITICAL)

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


if not PG:
    print("SKIP: MECH_TEST_DATABASE_URL не задан")
    sys.exit(0)

import backend.core.config  # noqa: F401 — первым, иначе цикл импортов models ↔ core
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.models.base import Base
import backend.models  # noqa: F401
from backend.models.user import User
from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppChat, AgentWhatsAppMessage
from backend.models.task import Task, TaskStatus

engine = create_engine(PG)
with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
db = Session()

u = User(email=f"o{uuid.uuid4().hex[:6]}@x.kg", password_hash="x", credits_balance=0)
db.add(u); db.flush()
agent = AgentConfig(user_id=u.id, name="Бот", is_active=True)
db.add(agent); db.flush()
contact = AgentContact(agent_config_id=agent.id, user_id=u.id, name="Айбек", phone="+996700111222",
                       status="new", memory={})
db.add(contact); db.commit()

# --- 1. захват задачи ---
from backend.core.task_scheduler import TaskScheduler
task = Task(user_id=u.id, agent_contact_id=contact.id, is_agent_task=True, channel="call",
            title="звонок", status=TaskStatus.SCHEDULED, scheduled_time=datetime.utcnow())
db.add(task); db.commit()
db2 = Session()
t2 = db2.query(Task).filter(Task.id == task.id).first()
first = TaskScheduler._claim_task(db, task)
second = TaskScheduler._claim_task(db2, t2)
check("задачу занимает ровно один воркер", first and not second, (first, second))
TaskScheduler._release_task(db, task, datetime.utcnow() + timedelta(hours=1))
db2.expire_all()
check("перенос возвращает задачу в очередь", db2.query(Task).get(task.id).status == TaskStatus.SCHEDULED)
db2.close()

# --- 2. аренда клиента ---
from backend.services import contact_lease
dbA, dbB = Session(), Session()
check("аренда: первый разбор получает клиента", contact_lease.try_acquire(dbA, contact.id))
check("аренда: второй ждёт", not contact_lease.try_acquire(dbB, contact.id))
got = asyncio.run(contact_lease.acquire(dbB, contact.id, wait_seconds=0))
check("аренда: без ожидания — отказ", got is False)
contact_lease.release(dbA, contact.id)
check("аренда: после освобождения второй получает", contact_lease.try_acquire(dbB, contact.id))
dbB.query(AgentContact).filter(AgentContact.id == contact.id).update(
    {"ai_busy_until": datetime.utcnow() - timedelta(seconds=1)})
dbB.commit()
check("аренда: зависшая (упавший процесс) истекает сама", contact_lease.try_acquire(dbA, contact.id))
contact_lease.release(dbA, contact.id)
dbA.close(); dbB.close()

# --- 3. WhatsApp: подметание неотвеченных ---
from backend.services import whatsapp_inbound as wi
import backend.services.agent_orchestrator as ao
calls = []


async def fake_handle(account_id, contact_id, text_):
    calls.append(text_)

ao.handle_inbound_whatsapp = fake_handle
acc = AgentWhatsAppAccount(agent_config_id=agent.id, user_id=u.id, instance_name="i1", webhook_secret="s",
                           status="connected", connected_at=datetime.utcnow() - timedelta(days=1),
                           auto_reply_enabled=True, reply_delay_seconds=15)
db.add(acc); db.flush()
chat = AgentWhatsAppChat(account_id=acc.id, agent_contact_id=contact.id, remote_jid="996700111222@s.whatsapp.net",
                         phone="996700111222")
db.add(chat); db.flush()
old = datetime.utcnow() - timedelta(minutes=3)
db.add(AgentWhatsAppMessage(agent_config_id=agent.id, account_id=acc.id, chat_id=chat.id, agent_contact_id=contact.id,
                            direction="inbound", body="Сколько стоит?", created_at=old))
db.commit()

found = wi._unanswered_chats(datetime.utcnow())
check("подметание видит чат без ответа (деплой посреди паузы)", len(found) == 1, found)
lease_db = Session()
contact_lease.try_acquire(lease_db, contact.id)
asyncio.run(wi.sweep_unanswered())
check("клиента разбирают — подметание не запускает второй разбор", calls == [], calls)
contact_lease.release(lease_db, contact.id)
lease_db.close()
n = asyncio.run(wi.sweep_unanswered())
check("подметание запускает разбор с текстом клиента", n == 1 and calls == ["Сколько стоит?"], calls)
n = asyncio.run(wi.sweep_unanswered())
check("повторно тот же чат не разбирается", n == 0 and len(calls) == 1, calls)
db.expire_all()
check("после разбора аренда снята", db.query(AgentContact).get(contact.id).ai_busy_until is None)
fresh = datetime.utcnow() - timedelta(seconds=5)
db.add(AgentWhatsAppMessage(agent_config_id=agent.id, account_id=acc.id, chat_id=chat.id, agent_contact_id=contact.id,
                            direction="inbound", body="ещё вопрос", created_at=fresh))
db.commit()
check("свежее сообщение ждёт обычную паузу, подметание его не трогает",
      wi._unanswered_chats(datetime.utcnow()) == [])

# --- 4. кредиты кончились — владельцу раз в сутки ---
from backend.services import owner_notifications as on
sent = []


async def fake_send(agent_, db_, message):
    sent.append(message)
    return True

on._send = fake_send
asyncio.run(on.agent_blocked(db, agent, "credits"))
asyncio.run(on.agent_blocked(db, agent, "credits"))
check("кончились кредиты: владельцу одно оповещение в сутки", len(sent) == 1 and "кредит" in sent[0], sent)

db.close()
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
