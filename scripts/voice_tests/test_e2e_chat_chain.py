"""
Сквозная цепочка переписки WhatsApp на настоящем PostgreSQL (без сети: шлюз Evolution и модель — заглушки):

  вебхук Evolution (настоящий маршрут, токен) → сообщение в базе → новый клиент (reply_scope=all) →
  пауза-склейка серии → разбор (PostCall) → модель отвечает whatsapp_send_message, пишет память и имя →
  ответ ушёл в шлюз и лежит в переписке → карточка CRM заполнена → попытка ЗВОНКА не посчитана.
  Второе сообщение «позвоните завтра в 15:00» → задача на звонок ровно на это время.
  Повтор того же вебхука → второго ответа нет.

    E2E_TEST_DATABASE_URL=postgresql://... python scripts/voice_tests/test_e2e_chat_chain.py
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("E2E_TEST_DATABASE_URL")
if not PG:
    print("SKIP: E2E_TEST_DATABASE_URL не задан")
    sys.exit(0)
os.environ.update(HOST_URL="https://e2e.voksyai.online", DATABASE_URL=PG, PRODUCTION="true",
                  JWT_SECRET_KEY="e2e-secret-0123456789abcdef0123456789", OPENAI_API_KEY="sk-e2e",
                  OPENROUTER_API_KEY="or-e2e")
logging.disable(logging.CRITICAL if not os.environ.get("E2E_DEBUG") else logging.NOTSET)
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
from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppMessage  # noqa: E402
from backend.models.task import Task, TaskStatus  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402

db = Session()
boss = User(email=f"e2c{uuid.uuid4().hex[:6]}@example.com", password_hash="x", email_verified=True,
            is_admin=True, credits_balance=5000, first_name="Аман")
db.add(boss); db.commit()
client = TestClient(app_module.app, raise_server_exceptions=False)
H = {"Authorization": f"Bearer {create_jwt_token(boss.id)}"}
r = client.post("/api/agent/create", headers=H, json={
    "name": "Анри", "assistant_type": "eleven", "eleven_language": "ru",
    "doc_who_am_i": "Я Анри", "doc_who_we_call": "Бизнес", "doc_how_we_talk": "Коротко",
    "doc_what_we_offer": "Агент", "doc_rules_and_goals": "Демо",
})
check("кабинет: бот создан", r.status_code < 400, r.status_code)
db.expire_all()
agent = db.query(AgentConfig).filter(AgentConfig.user_id == boss.id).first()
agent.call_window_enabled = False
acc = AgentWhatsAppAccount(agent_config_id=agent.id, user_id=boss.id, instance_name="e2e", webhook_secret="sek",
                           status="connected", connected_at=datetime.utcnow() - timedelta(days=1),
                           auto_reply_enabled=True, reply_scope="all", wa_phone="996700000001")
db.add(acc); db.commit()
ACC = str(acc.id)

# --- заглушки: шлюз WhatsApp, пауза-склейка, модель ---
from backend.services import whatsapp_service as wa  # noqa: E402
from backend.services import agent_pause  # noqa: E402
sent = []


async def fake_check(instance, digits):
    return {"ok": True, "exists": True, "jid": f"{digits}@s.whatsapp.net"}


async def fake_send_text(instance, to, text_, delay_ms=None, **k):
    sent.append(text_)
    return {"ok": True, "id": f"OUT{len(sent)}", "key": {"id": f"OUT{len(sent)}"}}

wa.check_number = fake_check
wa.is_configured = lambda: True
wa.send_text = fake_send_text
agent_pause.reply_delay_seconds = lambda account: 0

import backend.services.openrouter_client as orc  # noqa: E402
script = []  # очередь ответов модели: список (tool_calls | None, content)
seen_prompts = []


def tool(tool_name, **args):
    return {"id": f"c{uuid.uuid4().hex[:6]}", "type": "function", "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)}}


async def fake_completion(self, model=None, messages=None, tools=None, **k):
    seen_prompts.append(messages)
    calls, content = script.pop(0) if script else (None, "")
    if calls:
        cid = str(contact_row().id)
        calls = [{**c_, "function": {**c_["function"], "arguments": c_["function"]["arguments"].replace("__CID__", cid)}}
                 for c_ in calls]
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

orc.OpenRouterClient.chat_completion = fake_completion
if os.environ.get("E2E_DEBUG"):
    import backend.services.agent_tools as _at_dbg
    _orig_exec = _at_dbg.execute_tool

    async def _dbg_exec(name, args, context, db):
        res = await _orig_exec(name, args, context, db)
        print("TOOL", name, "→", str(res)[:300])
        return res
    _at_dbg.execute_tool = _dbg_exec
    import backend.services.agent_orchestrator as _ao_dbg
    _ao_dbg.execute_tool = _dbg_exec


def webhook(msg_id, text_, name="Нурлан"):
    payload = {"event": "messages.upsert", "data": {
        "key": {"remoteJid": "996700222444@s.whatsapp.net", "fromMe": False, "id": msg_id},
        "pushName": name, "message": {"conversation": text_},
        "messageTimestamp": int(time.time()),
    }}
    return client.post(f"/api/whatsapp/webhook/{ACC}", json=payload, headers={"X-Voksy-Token": "sek"})


def wait_for(cond, seconds=25):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.3)
    return False


def contact_row():
    s = Session()
    try:
        return s.query(AgentContact).filter(AgentContact.agent_config_id == agent.id).first()
    finally:
        s.close()


check("чужой токен вебхука отклонён", client.post(f"/api/whatsapp/webhook/{ACC}", json={},
                                                 headers={"X-Voksy-Token": "bad"}).status_code == 403)

# 1) первое сообщение: ответ + память + имя
script[:] = [
    ([tool("whatsapp_send_message", agent_contact_id="__CID__", text="Здравствуйте, Нурлан! Старт: 7 500 сом в месяц."),
      tool("update_contact_memory", agent_contact_id="__CID__", key_facts=["кафе", "спросил цену"],
           summary="Спросил цену Старта"),
      tool("update_contact_info", agent_contact_id="__CID__", name="Нурлан", company="Кафе")], ""),
    (None, "готово"),
]
r = webhook("IN1", "Здравствуйте, сколько стоит ваш бот? Меня Нурлан зовут, у меня кафе")
check("вебхук принят", r.status_code == 200, r.text[:100])
check("новый клиент заведён из WhatsApp", wait_for(lambda: contact_row() is not None))
c = contact_row()
check("бот ответил клиенту в WhatsApp", wait_for(lambda: len(sent) >= 1), sent)
check("в ответе нет тире-разметки и пояса", sent and "—" not in sent[0] and "Бишкек" not in sent[0], sent)
def outbound_count():
    s_ = Session()
    try:
        return s_.query(AgentWhatsAppMessage).filter(AgentWhatsAppMessage.direction == "outbound",
                                                     AgentWhatsAppMessage.agent_contact_id == c.id).count()
    finally:
        s_.close()


ok_thread = wait_for(lambda: outbound_count() == 1)
check("ответ лежит в переписке клиента", ok_thread)
wait_for(lambda: (contact_row().name or "") == "Нурлан" and "кафе" in json.dumps(contact_row().memory or {}, ensure_ascii=False))
c = contact_row()
check("CRM: имя и компания записаны", c.name == "Нурлан" and (c.company or "") == "Кафе", (c.name, c.company))
check("CRM: память — факты", "кафе" in json.dumps(c.memory or {}, ensure_ascii=False), c.memory)
check("переписка не считается звонком (попыток 0)", (c.attempts_count or 0) == 0 and c.last_called_at is None,
      (c.attempts_count, c.last_called_at))
first_prompt = json.dumps(seen_prompts[0], ensure_ascii=False) if seen_prompts else ""
check("модель видит текст клиента в разборе", "сколько стоит ваш бот" in first_prompt)

# 2) повтор того же вебхука — второго ответа нет
webhook("IN1", "Здравствуйте, сколько стоит ваш бот? Меня Нурлан зовут, у меня кафе")
time.sleep(3)
check("дубль вебхука не даёт второго ответа", len(sent) == 1, sent)

# 3) «позвоните завтра в 15:00» → задача ровно на это время
from backend.core.timezone_utils import BISHKEK  # noqa: E402
tomorrow15 = (datetime.now(timezone.utc).astimezone(BISHKEK) + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
script[:] = [
    ([tool("whatsapp_send_message", agent_contact_id=str(c.id), text="Договорились, позвоню завтра в 15:00."),
      tool("create_agent_task", agent_contact_id=str(c.id), scheduled_at=tomorrow15.astimezone(timezone.utc).isoformat(),
           title="Звонок по договорённости", notes="клиент просил в 15:00")], ""),
    (None, ""),
]
webhook("IN2", "Позвоните завтра в 15:00")
check("второй ответ ушёл", wait_for(lambda: len(sent) >= 2), sent)


def call_tasks():
    s = Session()
    try:
        return s.query(Task).filter(Task.agent_contact_id == c.id, Task.status == TaskStatus.SCHEDULED,
                                    Task.channel == "call").all()
    finally:
        s.close()

check("задача на звонок создана", wait_for(lambda: len(call_tasks()) == 1), [t.scheduled_time for t in call_tasks()])
t = call_tasks()[0] if call_tasks() else None
if t:
    when = t.scheduled_time.replace(tzinfo=timezone.utc).astimezone(BISHKEK)
    check("звонок ровно на время клиента (15:00 Бишкек завтра)", when == tomorrow15, when)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
