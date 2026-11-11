"""
Сквозная цепочка звонка агента на настоящем PostgreSQL (без сети: модель и SIP-мост — заглушки):

  кабинет: POST /api/agent/create (как «Чат-бот»/мастер) → номер SIP привязан к агенту →
  клиент на O! → задача на звонок (fn_create_agent_task, как у модели) → планировщик (тик) →
  PreCall → строка sip_calls в очереди шлюза → мост: «не взял трубку» → AgentCallFinalizer →
  PostCall (модель недоступна) → код сам ставит повтор на завтра.

Ловит то, что модульные тесты не видят: разрыв между звеньями (имя поля, статус, привязка ассистента).
    E2E_TEST_DATABASE_URL=postgresql://... python scripts/voice_tests/test_e2e_call_chain.py
"""
import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("E2E_TEST_DATABASE_URL")
if not PG:
    print("SKIP: E2E_TEST_DATABASE_URL не задан")
    sys.exit(0)
os.environ.update(HOST_URL="https://e2e.voksyai.online", DATABASE_URL=PG, PRODUCTION="true",
                  JWT_SECRET_KEY="e2e-secret-0123456789abcdef0123456789", OPENAI_API_KEY="sk-e2e")
logging.disable(logging.CRITICAL)
import backend.core.config  # noqa: E402,F401

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

engine = create_engine(PG)
with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
import app as app_module  # noqa: E402
from backend.models.base import Base  # noqa: E402
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

from backend.models.user import User  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_contact import AgentContact  # noqa: E402
from backend.models.agent_call import AgentCall  # noqa: E402
from backend.models.task import Task, TaskStatus  # noqa: E402
from backend.models.sip_gateway import SipPhoneNumber, SipCall  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402

db = Session()
boss = User(email=f"e2e{uuid.uuid4().hex[:6]}@example.com", password_hash="x", email_verified=True,
            is_admin=True, credits_balance=5000, first_name="Аман")
db.add(boss); db.commit()

client = TestClient(app_module.app, raise_server_exceptions=False)
H = {"Authorization": f"Bearer {create_jwt_token(boss.id)}"}
r = client.post("/api/agent/create", headers=H, json={
    "name": "Анри", "assistant_type": "eleven", "eleven_language": "ru",
    "doc_who_am_i": "Я Анри", "doc_who_we_call": "Бизнес", "doc_how_we_talk": "Коротко",
    "doc_what_we_offer": "Агент", "doc_rules_and_goals": "Демо",
})
check("кабинет: бот создан", r.status_code < 400, f"{r.status_code} {r.text[:200]}")
db.expire_all()
agent = db.query(AgentConfig).filter(AgentConfig.user_id == boss.id).first()
check("у агента есть голосовая карточка для звонков", agent is not None and bool(agent.eleven_assistant_id),
      getattr(agent, "eleven_assistant_id", None))
agent.call_window_enabled = False  # тест не зависит от часа
db.add(SipPhoneNumber(user_id=boss.id, phone_number="996700900900", agent_config_id=agent.id,
                      assistant_type="eleven", assistant_id=agent.eleven_assistant_id,
                      allow_outbound=True, is_active=True))
contact = AgentContact(agent_config_id=agent.id, user_id=boss.id, name="Нурлан", phone="+996700111333",
                       status="active", memory={})
db.add(contact); db.commit()

from backend.services.agent_tools import fn_create_agent_task  # noqa: E402
res = asyncio.run(fn_create_agent_task({"agent_contact_id": str(contact.id), "delay_minutes": 5,
                                        "title": "Звонок агента", "notes": "уточнить задачу"},
                                       str(boss.id), str(agent.id), db))
check("задача на звонок создана как у модели", res.get("ok"), res)
task = db.query(Task).filter(Task.agent_contact_id == contact.id, Task.status == TaskStatus.SCHEDULED).first()
check("в задаче есть голосовая карточка агента", task is not None and task.eleven_assistant_id == agent.eleven_assistant_id)
task.scheduled_time = datetime.utcnow() - timedelta(seconds=5)
db.commit()

# --- планировщик: один тик ---
import backend.services.agent_orchestrator as ao  # noqa: E402
from backend.core.task_scheduler import TaskScheduler  # noqa: E402


async def fake_precall(self, task_, contact_, call_, agent_, user_, db_):
    call_.call_strategy = "Узнать задачу клиента"
    db_.commit()
    return {"call_strategy": call_.call_strategy}

ao.PreCallOrchestrator.run = fake_precall
import backend.core.task_scheduler as ts_mod  # noqa: E402
ts_mod.PreCallOrchestrator.run = fake_precall
asyncio.run(TaskScheduler().check_and_execute_tasks())
db.expire_all()
sip = db.query(SipCall).filter(SipCall.task_id == task.id).first()
check("планировщик поставил звонок в очередь SIP-шлюза", sip is not None and sip.status == "queued",
      (getattr(sip, "status", None), db.get(Task, task.id).status, db.get(Task, task.id).call_result))
check("звонок на номер клиента с номера агента", sip is not None and sip.to_number.endswith("700111333")
      and (sip.did or "").endswith("700900900"), (getattr(sip, "to_number", None), getattr(sip, "did", None)))
check("задача в работе, повторно не берётся", db.get(Task, task.id).status == TaskStatus.PENDING)
# --- что услышит голосовой агент на этом исходящем: память о клиенте, инструкция, знания (без PreCall) ---
from backend.services.sip_gateway_service import SipGatewayService  # noqa: E402
from backend.services import agent_voice_context  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
contact.memory = {"summary": "Спрашивал про рассрочку, у него кафе", "niche": "кафе"}
agent.voice_additional_instructions = "Ты Анри. Цель звонка — записать на демо."
db.add(AgentKnowledgeEntry(agent_config_id=agent.id, user_id=boss.id, kind="qa", title="Есть ли рассрочка?",
                           body="Да, на 3 месяца без переплаты.", is_active=True))
db.commit()
mem_block = SipGatewayService.inbound_known_contact_block(sip, agent, db)
check("исходящий: карточка и память клиента идут агенту из кода (не только через PreCall)",
      "Нурлан" in mem_block and "рассрочку" in mem_block and "Ты звонишь" in mem_block, mem_block[:200])
from backend.models.eleven_assistant import ElevenAssistantConfig  # noqa: E402
asst = db.get(ElevenAssistantConfig, agent.eleven_assistant_id)
call_prompt, _st = agent_voice_context.build_call_system_prompt(
    db, asst, agent, call_context=SipGatewayService.call_context_text(sip) + "\n\n" + mem_block)
check("промпт звонка: инструкция для звонков, база знаний, цель задачи и память клиента",
      all(k in call_prompt for k in ("записать на демо", "на 3 месяца без переплаты", "Нурлан", "кафе")), len(call_prompt))
asyncio.run(TaskScheduler().check_and_execute_tasks())
check("второй тик не набирает клиента второй раз", db.query(SipCall).filter(SipCall.task_id == task.id).count() == 1)

# --- мост: не взял трубку → разбор → повтор ---
sip.status = "failed"
sip.end_reason = "no_answer"
db.commit()
from backend.services import agent_call_finalizer as fin  # noqa: E402

ran = []


def fake_schedule(agent_call_id, transcript, call_status, duration, direction):
    ran.append((agent_call_id, call_status, direction))
    asyncio.run(ao.PostCallOrchestrator.finalize_sip_call(agent_call_id, transcript, call_status, duration,
                                                           call_direction=direction))

fin._schedule = fake_schedule


async def no_llm(*a, **k):
    raise RuntimeError("модель недоступна (тест)")

import backend.services.openrouter_client as orc  # noqa: E402
orc.OpenRouterClient.chat_completion = no_llm
orc.OpenRouterClient.chat_completion_stream = no_llm
fin.AgentCallFinalizer.on_call_failed(db, sip)
check("недозвон дошёл до разбора как исходящий no_answer", ran and ran[0][1:] == ("no_answer", "outbound"), ran)
db2 = Session()
ac = db2.query(AgentCall).filter(AgentCall.source_task_id == task.id).first()
check("строка звонка закрыта (не висит «calling»)", ac is not None and ac.status in ("no_answer", "failed"),
      getattr(ac, "status", None))
nxt = db2.query(Task).filter(Task.agent_contact_id == contact.id, Task.status == TaskStatus.SCHEDULED,
                             Task.channel == "call").all()
check("модель не ответила — повторный звонок всё равно поставлен", len(nxt) == 1,
      [(t.title, t.scheduled_time) for t in nxt])
c2 = db2.get(AgentContact, contact.id)
check("попытка звонка посчитана", (c2.attempts_count or 0) == 1 and c2.last_called_at is not None,
      (c2.attempts_count, c2.last_called_at))
db2.close()
st = client.get("/api/agent/stats", headers=H).json()
check("статистика: недозвон считается звонком", st.get("total_calls") == 1 and st.get("no_answer_calls") == 1, st)
db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
