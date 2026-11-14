"""Разбор звонка af6e4d6a (10.11.2026, Марат, авто из Китая и Кореи) — регрессия без сети и БД.

Что было на звонке: 18-секундный монолог вместо вопроса о деле клиента; трижды «одну секунду» и раз «сейчас
уточню» на вопросы, ответы на которые лежали в промпте (цена, «это программа?», «нужен пароль?»); два похода к
бэкенду без единой функции; сбой подсказки Native Decisions (decision_failed) пометил весь звонок ERROR; обрывок
«Идея простая: пока» читался как прощание; знания стояли в конце промпта после 14 тыс. символов правил; в базовых
правилах карточки висело указание про send_sms, которого давно нет.
"""
import asyncio
import logging
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")
logging.disable(logging.CRITICAL)

import backend.websockets.handler_live as h
import backend.websockets.live_client as lc
import backend.services.agent_voice_context as avc
from backend.services import agent_prompts
from backend.websockets.call_log import CallLogRecorder

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


class FC:
    session_id = "s"
    io_rate = 8000
    enabled_functions = ["hangup_call"]
    delegation_model = "gpt-6-luna"

    def __init__(self):
        self.results, self.thinking = [], []

    async def send_function_result(self, cid, res):
        self.results.append((cid, res))
        return {"success": True}

    async def append_thinking(self, text):
        self.thinking.append(text)
        return True

    def decode_output_b64(self, x):
        return x


def session():
    cl = CallLogRecorder(channel="phone")
    s = h.LiveVoiceSession(websocket=None, assistant=types.SimpleNamespace(), client=FC(), db=None,
                           client_id="c", telephony=True, call_log=cl)
    out = []

    async def em(d):
        out.append(d)
    s.emit = em
    return s, out, cl


DECISION_ERR = {"type": "error", "error": {"type": "server_error", "code": "decision_failed",
                                           "message": "Decision sampling failed for trg_EiXp0tGy1aMn3HH4fNU88."}}


async def decision_error():
    s, out, cl = session()
    await s._on_live_event(DECISION_ERR)
    await s._on_live_event(DECISION_ERR)
    check("decision_failed: в мост ошибка НЕ уходит (иначе звонок помечается ERROR)", not any(d.get("type") == "error" for d in out), out)
    check("decision_failed: не считается ошибкой GPT-Live, считается отдельно", s.errors == 0 and s.decision_errors == 2,
          (s.errors, s.decision_errors))
    lines = [e for e in cl.events if "Decisions" in e["text"]]
    check("decision_failed: в журнале одна строка INFO, без ERROR",
          len(lines) == 1 and lines[0]["level"] == "info" and not any(e["level"] == "error" for e in cl.events), cl.events)
    s2, out2, _ = session()
    await s2._on_live_event({"type": "error", "error": {"code": "invalid_value", "message": "bad"}})
    check("другие ошибки GPT-Live по-прежнему видны", s2.errors == 1 and any(d.get("type") == "error" for d in out2))
    check("is_decision_error узнаёт сбой решения и не трогает прочее",
          lc.is_decision_error(DECISION_ERR["error"]) and not lc.is_decision_error({"code": "invalid_value"})
          and not lc.is_decision_error(None))

    # Клиент Live: на сбой решения не просит голос «сказать, что проверить не удалось»
    client = lc.OpenAILiveClient("k", types.SimpleNamespace(id="x", system_prompt="p", functions={}, voice="cedar",
                                                            greeting_message=None), "cid", telephony=True)
    asked = []

    async def fake_append(text):
        asked.append(text)
    client.append_instructions = fake_append
    await client._handle_error_event({"error": {**DECISION_ERR["error"], "client_event_id": "fnres_1"}})
    check("клиент Live: сбой решения не превращается в «проверить не получилось»", asked == [], asked)


def farewell_and_fillers():
    check("обрывок «Идея простая: пока» — не прощание агента", not h.is_assistant_closing("Идея простая: пока"))
    check("настоящее прощание агента по-прежнему узнаётся",
          h.is_assistant_closing("Договорились, отправлю. Хорошего дня, Марат.") and h.is_assistant_closing("До свидания."))
    s, _, _ = session()
    for t in ["Одну секунду.", "Секундочку, сейчас уточню.", "Бир секунд.", "Через минуту перезвоню? Нет."]:
        s._log_buf["assistant"] = t
        s._flush_log("assistant")
    check("«одну секунду», «секундочку», «бир секунд» считаются", s.repeats.get("одну секунду/минутку") == 3, s.repeats)


def diagnostics():
    cl = CallLogRecorder(channel="phone"); cl.latencies_ms.extend([800, 900])
    s = h.LiveVoiceSession(websocket=None, assistant=types.SimpleNamespace(), client=FC(), db=None, client_id="c", call_log=cl)
    s.voiced_bytes = int(90 * 8000 * 2)
    s.delegations = 4
    s._tool_delegations = {"d3", "d4"}
    s.repeats = {"одну секунду/минутку": 3, "сейчас уточню/посмотрю": 1}
    s.decision_errors = 1
    s._log_diagnostics(206)
    texts = " | ".join(e["text"] for e in cl.events)
    check("диагностика: 2 похода к бэкенду без функции видны", "2 раз уходила к бэкенду без функции" in texts, texts)
    check("диагностика: «одну секунду» видно отдельной строкой", "тянул время" in texts and "4 раз" in texts, texts)
    check("диагностика: сбой Decisions — в итогах, не в замечаниях",
          "сбоев подсказок Decisions 1" in texts and "ошибок GPT-Live" not in texts, texts)


async def tool_delegation_tracked():
    s, _, _ = session()
    s._last_user_voice_at = __import__("time").time()
    await s._on_function_call({"name": "hangup_call", "call_id": "c1", "delegation_id": "dlg_1",
                               "arguments": {"reason": "conversation_completed"}})
    check("делегирование с функцией запоминается", "dlg_1" in s._tool_delegations)


def hints():
    # 14.11.2026 (1411-voice-lean): слой платформы GPT-Live — LIVE_VOICE_HEAD + LIVE_VOICE_RULES (≈5 тыс. символов)
    hint = lc.VOICE_DELEGATION_HINT
    head = lc.LIVE_VOICE_HEAD
    check("подсказка: «одну секунду» запрещена поимённо (твёрдое правило 3)",
          "Не говори «одну секунду», «секундочку», «сейчас уточню»" in hint)
    check("подсказка: «секунду» не разрешена при ожидании бэкенда",
          "максимум одно короткое слово" not in hint and "Пока ждёшь бэкенд — продолжай разговор" in hint)
    check("подсказка: делегирование только за функцией или фактом, которого нет",
          "Delegate to the backend when" in hint and "Do not delegate to the backend when" in hint
          and "факт, которого нет" in hint)
    check("цена цифрой сразу и факты только из базы (твёрдые правила 1–2)",
          "назови цифру сразу" in hint and "Не выдумывай" in hint)
    check("правила ссылаются на настоящий заголовок раздела знаний", avc.KNOWLEDGE_HEADING in hint)
    check("кыргызский: разговорный бишкекский, деловые слова по-русски, без книжных",
          "как говорят в Бишкеке" in head and "«коммерциялык сунуш»" in head and "албетте" in head)
    check("продажи: вариант и цену называет, а не «только вместе с пользой»",
          "назови подходящий вариант и цену" in hint and "ТОЛЬКО вместе с пользой" not in hint)
    check("подсказки голосу ужаты (было 12,7 тыс. в склейке GPT, затем ~11 тыс.)", len(head) + len(hint) < 6000,
          len(head) + len(hint))
    check("бэкенд тоже не говорит «одну секунду»", "«одну секунду»" in lc.BACKEND_SYSTEM_PROMPT)


def knowledge_first():
    agent = types.SimpleNamespace(id=None, voice_additional_instructions="Ты Анри, менеджер Voksy AI.",
                                  doc_who_am_i="Voksy AI — голосовые агенты и чат-боты для бизнеса в Кыргызстане.")
    block, stats = avc.build_voice_knowledge_block(None, agent)
    check("блок знаний начинается заголовком «наизусть» и правилом отвечать сразу",
          block.startswith(f"# {avc.KNOWLEDGE_HEADING}") and "без «одну секунду»" in block, block[:120])
    old_card = agent_prompts.VOICE_AGENT_PROMPT_BASE.replace(
        "# НАША ТЕХНОЛОГИЯ", "- Отправка SMS (send_sms): вызови send_sms.\n\n# НАША ТЕХНОЛОГИЯ")
    assistant = types.SimpleNamespace(system_prompt=old_card + "\n\n# ДОПОЛНИТЕЛЬНЫЕ ИНСТРУКЦИИ ОТ ВЛАДЕЛЬЦА\nСТАРАЯ_ИНСТРУКЦИЯ_XYZ")
    p, st = avc.build_call_system_prompt(None, assistant, agent, call_context="Исходящий звонок Марату.",
                                         max_chars=24000)
    check("порядок промпта звонка: знания → правила карточки → контекст звонка",
          p.index(avc.KNOWLEDGE_HEADING) < p.index("Ты разговариваешь с человеком по телефону") < p.index("[Контекст звонка]"))
    check("правила карточки собраны по текущему коду (без send_sms) и с текущими инструкциями агента",
          "send_sms" not in p and "Ты Анри, менеджер Voksy AI." in p and "СТАРАЯ_ИНСТРУКЦИЯ_XYZ" not in p)
    manual = types.SimpleNamespace(system_prompt="Ты Алия, администратор салона. Отвечай коротко.")
    check("промпт, написанный владельцем руками, не перезаписывается",
          avc.fresh_card_prompt(manual, agent) == manual.system_prompt)
    check("без агента — сохранённый промпт как есть", avc.fresh_card_prompt(assistant, None) == assistant.system_prompt.strip())
    check("базовые правила звонка без send_sms", "send_sms" not in agent_prompts.VOICE_AGENT_PROMPT_BASE)


async def main():
    await decision_error()
    farewell_and_fillers()
    diagnostics()
    await tool_delegation_tracked()
    hints()
    knowledge_first()


asyncio.run(main())
print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
