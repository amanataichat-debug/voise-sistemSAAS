"""
Промпт и цикл разбора переписки (ветка 1111-clean-system) — без сети и БД.

Проверяется поведение сборки, а не точные фразы:
  * системный промпт: ядро + блок «клиент» или «владелец», инструкция владельца, контакты компании;
    нет устаревшего (русский всегда, «железное правило», SMS, «уточню у специалиста», «главнее»);
  * событие WhatsApp: первым действием ответ клиенту, CRM в том же ходе, вывод «можешь/не можешь позвонить»;
  * разбор звонка: обещанное — сейчас, время клиента — задача на это время, касания воронки ставит система;
  * тариф Старт и Profi отвечают на «позвоните» согласованно;
  * деление ответа только по «---», не больше 3 сообщений, переносы и прайс столбцом целы;
  * клиент написал, а модель не ответила — одна напоминалка, потом WARNING в postcall_log.
"""
import asyncio
import json
import logging
import os
import sys
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401
from backend.services import agent_prompts as ap
from backend.services import agent_mode as am
from backend.services import chat_split as cs
from backend.services import chat_conscience as cc
import backend.services.agent_orchestrator as ao
from backend.models.agent_call import AgentCall
from backend.models.agent_contact import AgentContact

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(extra)[:300]}]" if extra else ""))
    failures += 0 if cond else 1


def agent(**kw):
    base = dict(id="a1", user_id="u1", name="Бот", doc_who_am_i="Салон «Айгерим», Бишкек.", doc_what_we_offer="",
                doc_who_we_call="", doc_how_we_talk="", doc_rules_and_goals="",
                additional_instructions="Ты Айгерим, администратор салона. Записываешь на стрижку {без шаблонов}.",
                brand_company_name="Айгерим", brand_contacts="aigerim.kg · +996 700 123 456",
                kb_namespace=None, kb_name=None, webhook_url=None, pipeline_stages=None,
                orchestrator_model="m", uses_hardcoded_prompt=True, voice_additional_instructions="")
    base.update(kw)
    return NS(**base)


# ── системный промпт ────────────────────────────────────────────────────────
client = ap.build_orchestrator_prompt(agent(), include_time_block=False, surface="client")
owner = ap.build_orchestrator_prompt(agent(), include_time_block=False, surface="owner")
check("клиент: инструкция владельца в промпте (фигурные скобки не ломают сборку)",
      "Ты Айгерим, администратор" in client and "{без шаблонов}" in client)
check("клиент: контакты компании из настроек агента", "aigerim.kg · +996 700 123 456" in client)
check("клиент: правила переписки есть, справочника владельца нет",
      ap.CLIENT_BLOCK.strip() in client and "bulk_schedule_calls" not in client)
check("владелец: справочник инструментов есть, правил переписки нет",
      "bulk_schedule_calls" in owner and ap.CLIENT_BLOCK.strip() not in owner)
for label, text in (("клиент", client), ("владелец", owner)):
    low = text.lower()
    check(f"{label}: нет устаревшего (всегда русский, железное правило, SMS, специалист, «главнее»)",
          "всегда отвечай на русском" not in low and "железное" not in low and "send_sms" not in low
          and "специалист" not in low and "главнее" not in low and "выше любых" not in low)
check("язык ответа = язык собеседника", "на языке собеседника" in client.lower())
check("клиент: ответ первым, CRM после; 1–3 строки; «---» для второго сообщения",
      "Сначала ответ клиенту" in client and "1–3 строки" in client and "«---»" in client)
empty = ap.build_orchestrator_prompt(agent(doc_who_am_i="", additional_instructions="", brand_contacts=""),
                                     include_time_block=False, surface="client")
check("пустая инструкция владельца — понятная пометка, без пустых заголовков и без контактов",
      "не заполнил" in empty and "## Кто мы" not in empty and "КОНТАКТЫ КОМПАНИИ" not in empty)
check("размер клиентского промпта без знаний — компактный (< 6 тыс. символов)", len(client) < 6000, len(client))

# ── тариф Старт: «позвоните» ────────────────────────────────────────────────
check("Старт: сам не звонит, даёт номер и передаёт владельцу",
      "не звонишь" in am.PROMPT_BLOCK and "номер" in am.PROMPT_BLOCK and "notify_owner_handoff" in am.PROMPT_BLOCK)
check("Старт и Profi согласованы: не может позвонить → номер + notify_owner_handoff",
      "notify_owner_handoff" in cc.call_ability_line(False, "тариф") and "номер" in cc.call_ability_line(False, "x"))
check("send_sms не инициатива и не инструмент", "send_sms" not in am.INITIATIVE_TOOLS)

# ── событие разбора ─────────────────────────────────────────────────────────
ao.build_conversation_timeline = lambda *a, **k: ""
import backend.services.whatsapp_service as ws
ws.get_thread = lambda *a, **k: []


class FakeDB:
    def query(self, *a, **k):
        raise RuntimeError("no db in test")

    def rollback(self):
        pass

    def commit(self):
        pass


def contact():
    return AgentContact(id="c1", name="Айбек", phone="+996700111222", company=None, memory={}, attempts_count=0)


def call(direction="whatsapp_inbound"):
    return AgentCall(id="k1", user_id="u1", agent_contact_id="c1", postcall_log={"call_direction": direction})


pc = ao.PostCallOrchestrator()
wa = pc._build_postcall_input(call(), contact(), 'Клиент написал в WhatsApp: "Сколько стоит стрижка?"',
                              "answered", 0, FakeDB(), "whatsapp_inbound")
plan = wa[wa.index("Что сделать"):]
check("WhatsApp: первое действие — ответ whatsapp_send_message в этом же ходе",
      plan.index("whatsapp_send_message") < plan.index("update_contact_memory") and "ЭТОМ ЖЕ ходе" in plan)
check("WhatsApp: нет SMS, check_phone_operator, «уточню у специалиста»",
      "send_sms" not in wa and "check_phone_operator" not in wa and "специалист" not in wa)
out = pc._build_postcall_input(call("outbound"), contact(), "агент: здравствуйте…", "answered", 60, FakeDB(), "outbound")
check("звонок: обещанные материалы — сейчас, время клиента — задача на это время, сегодня тоже",
      "СЕЙЧАС" in out and "ровно на это время" in out and "сегодня тоже" in out)
check("звонок: касания воронки не планируются моделью, менеджер — notify_owner_handoff",
      "notify_owner_handoff" in out and "Следующий шаг" in out)
sms = pc._build_postcall_input(call("sms_inbound"), contact(), "текст", "answered", 0, FakeDB(), "sms_inbound")
check("SMS-событие: ответить SMS нельзя — мессенджер или владелец", "Ответить SMS нельзя" in sms)

# ── деление на сообщения ────────────────────────────────────────────────────
price = "Цены:\nСтрижка: 800 сом\nОкрашивание: 2 500 сом"
check("без маркера — одно сообщение, переносы и прайс целы", cs.split_reply(price) == [price])
long_text = "Очень длинное объяснение. " * 20
check("длинный текст без маркера не режется кодом", cs.split_reply(long_text) == [long_text.strip()])
parts = cs.split_reply("Ответ\n---\nСсылка: aigerim.kg\n---\nтри\n---\nчетыре")
check("маркер «---» делит, не больше 3 частей (лишнее склеено)", len(parts) == 3 and parts[2] == "три\n\nчетыре", parts)

# ── цикл разбора: ответ не ушёл → напоминание → WARNING ──────────────────────
class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    async def chat_completion(self, model, messages, tools, temperature):
        self.seen.append(messages[-1])
        msg = self.script.pop(0) if self.script else {"content": "готово"}
        return {"choices": [{"message": msg}], "usage": {}}


async def fake_tools(*a, **k):
    return []


executed = []


async def fake_exec(name, args, context, db):
    executed.append(name)
    return json.dumps({"ok": True})

ao.build_postcall_tools = fake_tools
ao.execute_tool = fake_exec
ao.CreditService = NS(precheck=lambda db, u: None, charge=lambda **k: None)
import backend.services.agent_voice_service as avs
avs.prompt_hint = lambda *a, **k: ""


def run(script):
    client = FakeClient(script)
    ao.get_openrouter_client = lambda: client
    c, k = contact(), call()
    asyncio.run(pc._analyze_v3_openrouter(k, c, agent(), NS(id="u1"), None, 'Клиент написал: "привет"',
                                          "answered", 0, FakeDB(), "whatsapp_inbound"))
    return k, client


send = {"content": "", "tool_calls": [{"id": "t1", "function": {
    "name": "whatsapp_send_message", "arguments": json.dumps({"agent_contact_id": "c1", "text": "Здравствуйте!"})}}]}
executed.clear()
k, cl = run([{"content": "Клиенту ответ не нужен"}, send])
check("модель не ответила — одна напоминалка, после неё ответ ушёл",
      executed == ["whatsapp_send_message"] and k.postcall_log.get("reply_sent") is True
      and any(ao.REPLY_NUDGE == (m.get("content") if isinstance(m, dict) else None) for m in cl.seen), k.postcall_log)
executed.clear()
k, cl = run([{"content": "нет"}, {"content": "всё равно нет"}])
check("ответа так и нет — WARNING в postcall_log, напоминание одно",
      k.postcall_log.get("reply_sent") is False and k.postcall_log.get("warning") == "no_reply_to_client"
      and sum(1 for m in cl.seen if isinstance(m, dict) and m.get("content") == ao.REPLY_NUDGE) == 1, k.postcall_log)

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
