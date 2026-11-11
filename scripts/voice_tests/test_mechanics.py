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
# Каждый «воркер» — свой контекст (на проде это разные процессы/задачи asyncio): аренда повторно входима
# только внутри одной задачи.
import contextvars
from backend.services import contact_lease


def worker(fn, *a):
    return contextvars.Context().run(fn, *a)


dbA, dbB = Session(), Session()
ctxA = contextvars.Context()
check("аренда: первый разбор получает клиента", ctxA.run(contact_lease.try_acquire, dbA, contact.id))
check("аренда: тот же разбор входит повторно (разбор внутри обработчика)",
      ctxA.run(contact_lease.try_acquire, dbA, contact.id))
check("аренда: второй ждёт", not worker(contact_lease.try_acquire, dbB, contact.id))
got = worker(asyncio.run, contact_lease.acquire(dbB, contact.id, wait_seconds=0))
check("аренда: без ожидания — отказ", got is False)
ctxA.run(contact_lease.release, dbA, contact.id)
check("аренда: после освобождения второй получает", worker(contact_lease.try_acquire, dbB, contact.id))
dbB.query(AgentContact).filter(AgentContact.id == contact.id).update(
    {"ai_busy_until": datetime.utcnow() - timedelta(seconds=1)})
dbB.commit()
check("аренда: зависшая (упавший процесс) истекает сама", worker(contact_lease.try_acquire, dbA, contact.id))
contact_lease.release(dbA, contact.id)
dbA.close(); dbB.close()

# --- 2б. разбор звонка/касания тоже по одному на клиента ---
import backend.services.agent_orchestrator as ao_mod
from backend.models.agent_call import AgentCall
body_runs = []


async def fake_body(self, *a, **k):
    body_runs.append(k.get("call_direction") if "call_direction" in k else a[-1])

ao_mod.PostCallOrchestrator._analyze_v3_body = fake_body
busy_db = Session()
busy_ctx = contextvars.Context()
busy_ctx.run(contact_lease.try_acquire, busy_db, contact.id)       # идёт разбор переписки в другом воркере
fcall = AgentCall(agent_contact_id=contact.id, agent_config_id=agent.id, user_id=u.id, direction="followup_outbound",
                  status="calling")
db.add(fcall); db.commit()
fcall_id = fcall.id
worker(asyncio.run, ao_mod.PostCallOrchestrator()._analyze_v3_openrouter(
    fcall, db.query(AgentContact).get(contact.id), agent, u, None, "касание", "answered", 0, db, "followup_outbound"))
check("касание воронки при занятом клиенте пропущено", body_runs == [], body_runs)
check("пустая строка касания удалена", db.query(AgentCall).filter(AgentCall.id == fcall_id).first() is None)
busy_ctx.run(contact_lease.release, busy_db, contact.id)
busy_db.close()
ccall = AgentCall(agent_contact_id=contact.id, agent_config_id=agent.id, user_id=u.id, direction="inbound",
                  status="calling")
db.add(ccall); db.commit()
worker(asyncio.run, ao_mod.PostCallOrchestrator()._analyze_v3_openrouter(
    ccall, db.query(AgentContact).get(contact.id), agent, u, None, "звонок", "answered", 30, db, "inbound"))
db.expire_all()
check("свободный клиент: разбор звонка идёт и снимает аренду",
      body_runs == ["inbound"] and db.query(AgentContact).get(contact.id).ai_busy_until is None, body_runs)

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
lease_ctx = contextvars.Context()
lease_ctx.run(contact_lease.try_acquire, lease_db, contact.id)
worker(asyncio.run, wi.sweep_unanswered())
check("клиента разбирают — подметание не запускает второй разбор", calls == [], calls)
lease_ctx.run(contact_lease.release, lease_db, contact.id)
lease_db.close()
n = worker(asyncio.run, wi.sweep_unanswered())
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

# --- 5. повтор после недозвона: страховка в коде (call_retry) ---
from backend.services import call_retry
from backend.core.timezone_utils import BISHKEK
from datetime import timezone as _tz
import backend.services.agent_tools as _at
_at.trigger_immediate_call = None  # не нужен
boss = User(email=f"p{uuid.uuid4().hex[:6]}@x.kg", password_hash="x", credits_balance=5000, is_admin=True)
db.add(boss); db.flush()
bagent = AgentConfig(user_id=boss.id, name="Profi", is_active=True, working_hours_start=9, working_hours_end=20)
db.add(bagent); db.flush()
rc = AgentContact(agent_config_id=bagent.id, user_id=boss.id, name="Нурлан", phone="+996700555666",
                  status="active", memory={})
db.add(rc); db.commit()
# «сейчас» — 11:00 Бишкека сегодня, чтобы проверка не зависела от часа запуска
now_bk = datetime.now(_tz.utc).astimezone(BISHKEK).replace(hour=11, minute=0, second=0, microsecond=0)
now_utc = now_bk.astimezone(_tz.utc)


def no_answer_call(minutes_ago):
    c = AgentCall(agent_contact_id=rc.id, agent_config_id=bagent.id, user_id=boss.id, direction="outbound",
                  status="no_answer", created_at=datetime.utcnow() - timedelta(minutes=minutes_ago))
    db.add(c); db.commit()


def scheduled_calls():
    db.expire_all()
    return db.query(Task).filter(Task.agent_contact_id == rc.id, Task.status == TaskStatus.SCHEDULED,
                                 Task.channel == "call").all()


no_answer_call(30)
r1 = asyncio.run(call_retry.ensure_retry_after_no_answer(db, bagent, rc, boss, "(Разговора не было.)", now_utc=now_utc))
t1 = scheduled_calls()
check("недозвон 1: повтор поставлен кодом", r1 and r1.get("ok") and len(t1) == 1, r1)
if t1:
    when = t1[0].scheduled_time.replace(tzinfo=_tz.utc).astimezone(BISHKEK)
    check("недозвон 1: завтра утром в окне звонков", when.date() == (now_bk + timedelta(days=1)).date()
          and 9 <= when.hour < 12, when)
r_again = asyncio.run(call_retry.ensure_retry_after_no_answer(db, bagent, rc, boss, "", now_utc=now_utc))
check("звонок уже стоит — второй не ставится", r_again is None and len(scheduled_calls()) == 1, r_again)
for t in scheduled_calls():
    t.status = TaskStatus.COMPLETED
db.commit()
no_answer_call(20)
r2 = asyncio.run(call_retry.ensure_retry_after_no_answer(db, bagent, rc, boss, "", now_utc=now_utc))
t2 = scheduled_calls()
check("недозвон 2 подряд: повтор послезавтра днём", r2 and r2.get("ok") and len(t2) == 1
      and t2[0].scheduled_time.replace(tzinfo=_tz.utc).astimezone(BISHKEK).date() == (now_bk + timedelta(days=2)).date()
      and t2[0].scheduled_time.replace(tzinfo=_tz.utc).astimezone(BISHKEK).hour >= 14, r2)
for t in scheduled_calls():
    t.status = TaskStatus.COMPLETED
db.commit()
no_answer_call(10)
r3 = asyncio.run(call_retry.ensure_retry_after_no_answer(db, bagent, rc, boss, "", now_utc=now_utc))
check("недозвон 3 подряд: больше не звоним, в карточке причина",
      r3 and r3.get("reason") == "max_attempts" and not scheduled_calls() and "ждём" in (rc.next_step_text or ""), r3)
db.add(AgentCall(agent_contact_id=rc.id, agent_config_id=bagent.id, user_id=boss.id, direction="whatsapp_inbound",
                 status="answered", created_at=datetime.utcnow() - timedelta(minutes=5)))
db.commit()
no_answer_call(1)
check("клиент написал после недозвонов — счёт заново", call_retry.no_answer_streak(db, rc.id) == 1,
      call_retry.no_answer_streak(db, rc.id))
r_bad = asyncio.run(call_retry.ensure_retry_after_no_answer(
    db, bagent, rc, boss, "(Разговора не было. Итог дозвона: номер не существует или набран неверно.)", now_utc=now_utc))
check("неверный номер — повтор не ставится", r_bad is None and not scheduled_calls())
rc.status = "rejected"; db.commit()
check("клиент в «Отказ» — повтор не ставится",
      asyncio.run(call_retry.ensure_retry_after_no_answer(db, bagent, rc, boss, "", now_utc=now_utc)) is None)

db.close()
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
