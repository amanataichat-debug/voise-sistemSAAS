"""Голосовой режим оркестратора (ветка 0811-voice-jarvis): владелец ↔ Gemini Live ↔ оркестратор. Без сети и БД.

Проверяем: разделение инструментов (чтение / действие / голосом нельзя), проверку «да» перед действием,
промпт и функции Gemini, крючки GeminiLiveClient (свои функции в обход реестра, свой исполнитель) через
настоящий handle_gemini_messages, запрос к оркестратору (запрещённый инструмент не выполняется), доступ
только Profi, кредиты за минуты и запись разговора в историю чата.

Часть 2 — сводка дел и быстрые функции на настоящем PostgreSQL, если задан OWNER_VOICE_TEST_DATABASE_URL
(отдельная пустая база: схема public пересоздаётся тестом):

    OWNER_VOICE_TEST_DATABASE_URL=postgresql://postgres@/voksy_test?host=/tmp python scripts/voice_tests/test_owner_voice.py
"""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, json, logging, sys, types

sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="https://example.com", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401 — первым, как в app.py (иначе циклический импорт backend.core)
from backend.services import owner_voice as ov
from backend.websockets import handler_gemini
from backend.websockets import handler_owner_voice as hov
from backend.websockets.gemini_client import GeminiLiveClient, gemini_schema

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def run(coro, timeout=5):
    return asyncio.run(asyncio.wait_for(coro, timeout))


# ---------- инструменты оркестратора: что голосом можно ----------
for name in ("get_period_report", "get_agent_stats", "search_contacts", "get_contact_timeline", "whatsapp_get_thread",
             "telegram_get_thread", "instagram_get_thread", "find_client", "search_knowledge_base", "list_agent_files",
             "read_agent_file", "check_phone_operator", "get_upcoming_schedule"):
    check(f"чтение: {name}", ov.classify_tool(name) == "read", ov.classify_tool(name))
for name in ("whatsapp_send_message", "telegram_send_message", "trigger_immediate_call", "create_agent_task",
             "move_contact_stage", "append_contact_note", "pause_agent_for_contact", "send_voice_message", "send_proposal",
             "update_agent_task", "send_telegram_notification", "create_document"):
    check(f"действие: {name}", ov.classify_tool(name) == "action", ov.classify_tool(name))
for name in ("delete_agent_contact", "delete_agent_task", "bulk_schedule_calls", "bulk_create_contacts", "merge_contacts",
             "send_webhook", "send_sms", "GOOGLECALENDAR_DELETE_EVENT", ""):
    check(f"голосом нельзя: {name or 'пустое имя'}", ov.classify_tool(name) == "forbidden", ov.classify_tool(name))
check("коннектор: поиск в календаре — чтение", ov.classify_tool("GOOGLECALENDAR_FIND_EVENT") == "read")
check("коннектор: письма — чтение", ov.classify_tool("GMAIL_FETCH_EMAILS") == "read")
check("коннектор: отправить письмо — действие", ov.classify_tool("GMAIL_SEND_EMAIL") == "action")
check("коннектор: создать событие — действие", ov.classify_tool("GOOGLECALENDAR_CREATE_EVENT") == "action")

def _t(n):
    return {"type": "function", "function": {"name": n, "description": "", "parameters": {"type": "object"}}}

sample = [_t(n) for n in ("get_agent_stats", "whatsapp_send_message", "delete_agent_contact", "search_contacts",
                          "bulk_schedule_calls", "trigger_immediate_call")]
names_read = [t["function"]["name"] for t in ov.tools_for("read", sample)]
names_act = [t["function"]["name"] for t in ov.tools_for("action", sample)]
check("вопрос: только чтение", names_read == ["get_agent_stats", "search_contacts"], names_read)
check("действие: чтение + действия, без удаления и массовых", names_act ==
      ["get_agent_stats", "whatsapp_send_message", "search_contacts", "trigger_immediate_call"], names_act)

# ---------- подтверждение «да» ----------
for text in ("Да", "да.", "Да, отправляй", "Ага", "Ооба", "ооба, жибер", "Давай", "Хорошо, отправляй", "Ну да",
             "Окей", "Да, и добавь, что скидка десять процентов", "Конечно"):
    check(f"«{text}» — подтверждение", ov.owner_confirmation(text) == "yes", ov.owner_confirmation(text))
for text in ("Нет", "Нет, подожди", "Не надо", "Стоп", "Подожди, не отправляй", "Жок", "Керек эмес", "Да нет, не надо"):
    check(f"«{text}» — отказ", ov.owner_confirmation(text) == "no", ov.owner_confirmation(text))
for text in ("", "   ", "Напиши Нурлану, что перезвоню в три", "Давай напиши Нурлану, что перезвоню в три",
             "Отправь Айбеку прайс в WhatsApp и поставь задачу на завтра", "Что писал Нурлан?"):
    check(f"«{text}» — не подтверждение", ov.owner_confirmation(text) is None, ov.owner_confirmation(text))

check("действие без confirmed — отказ", ov.action_refusal(False, "да") is not None)
check("действие с confirmed, но владелец сказал команду, а не «да» — отказ",
      ov.action_refusal(True, "напиши Нурлану, что перезвоню в три") is not None)
check("действие с confirmed и «да» — можно", ov.action_refusal(True, "Да, отправляй") is None)
check("действие с confirmed, владелец сказал «нет» — отказ с объяснением",
      "НЕ выполнено" in (ov.action_refusal(True, "нет, подожди") or ""))
check("confirmed строкой 'true' понимается", ov.action_refusal("true", "да") is None)
check("пустая реплика — отказ (не расслышали «да»)", ov.action_refusal(True, "") is not None)

# ---------- функции и промпт голосового помощника ----------
check("функции голосового помощника", ov.FUNCTION_NAMES == ["get_stats", "client_info", "list_dialogs", "get_schedule",
                                                             "ask_agent", "do_action", "send_to_telegram"], ov.FUNCTION_NAMES)
for f in ov.GEMINI_FUNCTIONS:
    check(f"схема {f['name']} не меняется очисткой под Gemini", gemini_schema(f["parameters"]) == f["parameters"])
_fn = {f["name"]: f for f in ov.GEMINI_FUNCTIONS}
check("do_action требует confirmed", "confirmed" in _fn["do_action"]["parameters"]["required"])
check("быстрые функции ждут результат молча (BLOCKING)",
      all(_fn[n]["behavior"] == "BLOCKING" for n in ov.FAST_FUNCTIONS) and ov.FAST_FUNCTIONS <= set(ov.FUNCTION_NAMES))
check("оркестратор и действия не блокируют (агент говорит «секунду, смотрю»)",
      _fn["ask_agent"]["behavior"] == "NON_BLOCKING" and _fn["do_action"]["behavior"] == "NON_BLOCKING")
check("в функциях нет записи и удаления, кроме do_action", all(n in ov.FAST_FUNCTIONS or n in ("ask_agent", "do_action", "send_to_telegram")
                                                          for n in ov.FUNCTION_NAMES))
check("промпт: быстрые функции названы", all(n in ov.VOICE_ROLE for n in ("get_stats", "client_info", "list_dialogs", "get_schedule")))
check("промпт: вчера/неделя — из сводки", "сколько звонков вчера" in ov.VOICE_ROLE)

user = types.SimpleNamespace(first_name="Аман", last_name="", id="u1", credits_balance=5000, is_admin=False)
agent = types.SimpleNamespace(id="a1", name="Мой агент", brand_company_name="Voksy AI", chat_history=[
    {"role": "user", "content": "Покажи воронку"}, {"role": "assistant", "content": "Новых 5, в работе 3"}])
brief = "Сейчас в Бишкеке воскресенье.\n\nСегодня: звонков 4 (входящих 1, исходящих 3, с разговором 2)."
prompt = ov.build_voice_prompt(user, agent, brief, agent.chat_history, voice="Kore")
check("промпт: говорит с владельцем по имени", "С тобой говорит Аман" in prompt)
check("промпт: сводка внутри", "СВОДКА ДЕЛ" in prompt and "звонков 4" in prompt)
check("промпт: контекст текстового чата", "Покажи воронку" in prompt)
check("промпт: компания", "Voksy AI" in prompt)
check("промпт: женский голос → женский род", "женском роде" in prompt)
check("промпт: мужской голос → мужской род", "мужском роде" in ov.build_voice_prompt(user, agent, brief, voice="Puck"))
check("промпт: действия только после «да»", "Только после явного «да»" in prompt)
check("промпт: до результата функции не выдумывать", "Ничего не выдумывай" in prompt)
check("промпт: удаление и массовое — не голосом", "Удалять клиентов и задачи" in prompt)
check("промпт: без провайдеров", "не называй технологии и провайдеров" in prompt)
big = ov.build_voice_prompt(user, agent, "x" * ov.BRIEF_MAX_CHARS, agent.chat_history * 10, voice="Kore")
check("промпт с полной сводкой и историей в разумных пределах", len(big) < 14000, len(big))
check("приветствие по имени", ov.greeting_text(user) == "Здравствуйте, Аман! Слушаю вас.")
check("приветствие без имени", ov.greeting_text(types.SimpleNamespace(first_name="", full_name="")) == "Здравствуйте! Слушаю вас.")
check("имя агента по умолчанию в промпт не идёт",
      "Мой агент" not in ov.build_voice_prompt(user, types.SimpleNamespace(id="a", name="Мой агент", brand_company_name=None), ""))


# ---------- сводка: сбой одного блока не роняет разговор ----------
class BrokenDB:
    def __init__(self): self.rollbacks = 0
    def query(self, *a, **k): raise RuntimeError("db down")
    def execute(self, *a, **k): raise RuntimeError("db down")
    def rollback(self): self.rollbacks += 1

bdb = BrokenDB()
b = ov.build_brief(bdb, user, agent)
check("сводка при сбое базы: время есть, разговор не падает", b.startswith("Сейчас в Бишкеке") and bdb.rollbacks >= 4, b)


# ---------- клиент Gemini: свои функции в обход реестра и коннекторов ----------
class FakeGeminiWS:
    def __init__(self, events=()):
        self.events = [json.dumps(e) for e in events]
        self.sent = []
    async def send(self, data): self.sent.append(json.loads(data))
    async def recv(self):
        if self.events:
            return self.events.pop(0)
        await asyncio.sleep(3600)
    async def close(self): pass


def make_client(events=()):
    cfg = types.SimpleNamespace(id="a1", user_id="u1", name="Голосовой режим", system_prompt="p",
                                greeting_message=None, functions=[{"name": n} for n in ov.FUNCTION_NAMES],
                                google_sheet_id=None, voice="Kore")
    c = GeminiLiveClient("key", cfg, "owner-1", None, "", voice_override="Kore", record_conversation=False,
                         realtime_text=True, function_definitions=ov.GEMINI_FUNCTIONS)
    c.ws = FakeGeminiWS(events)
    c.is_connected = True
    return c


async def setup_tools():
    c = make_client()
    await c.setup_session(voice="Kore", system_message="p", functions=c.assistant_config.functions)
    return c, [m for m in c.ws.sent if "setup" in m][0]

c, sp = run(setup_tools())
decl = [t["function_declarations"][0] for t in sp["setup"]["tools"]]
check("в setup ушли ровно функции помощника", [d["name"] for d in decl] == ov.FUNCTION_NAMES, [d["name"] for d in decl])
_decl = {d["name"]: d for d in decl}
check("описания и параметры — наши, не из реестра", _decl["ask_agent"]["description"] == _fn["ask_agent"]["description"]
      and _decl["do_action"]["parameters"]["properties"].get("confirmed"))
check("поведение у каждой функции своё: быстрые BLOCKING, оркестратор NON_BLOCKING",
      _decl["get_stats"]["behavior"] == "BLOCKING" and _decl["client_info"]["behavior"] == "BLOCKING"
      and _decl["ask_agent"]["behavior"] == "NON_BLOCKING" and _decl["do_action"]["behavior"] == "NON_BLOCKING",
      {k: v["behavior"] for k, v in _decl.items()})
check("в setup не уходит служебное поле behavior внутрь параметров", all("behavior" not in d["parameters"] for d in decl))
check("enabled_functions = функции помощника", c.enabled_functions == ov.FUNCTION_NAMES, c.enabled_functions)


async def tool_results():
    c2 = make_client()
    await c2.setup_session(voice="Kore", system_message="p", functions=c2.assistant_config.functions)
    c2.register_function_call("f1", "get_stats")
    c2.register_function_call("f2", "ask_agent")
    await c2.send_function_result("f1", {"ok": True})
    await c2.send_function_result("f2", {"ok": True})
    return [m["toolResponse"]["functionResponses"][0] for m in c2.ws.sent if "toolResponse" in m]

_tr = run(tool_results())
check("результат быстрой функции — без scheduling (BLOCKING)", _tr and "scheduling" not in _tr[0] and _tr[0]["name"] == "get_stats", _tr)
check("результат ask_agent — со scheduling (озвучить, когда освободится)", len(_tr) == 2 and _tr[1].get("scheduling"), _tr)

_card = make_client()
_card.custom_functions = None   # карточки ElevenLabs и Gemini-ассистенты: поведение по-старому, поле behavior не читается
check("у обычных клиентов поведение по-старому", _card.use_blocking_tools is False)
from backend.functions import get_enabled_functions
check("функций помощника НЕТ в общем реестре (их не включить публичному виджету)",
      get_enabled_functions(ov.FUNCTION_NAMES) == [])


# ---------- свой исполнитель через настоящий handle_gemini_messages ----------
class FakeClientSocket:
    def __init__(self): self.sent = []
    async def send_json(self, data, mode="text"): self.sent.append(data)


async def tool_scenario():
    calls = []
    events = [
        {"setupComplete": {}},
        {"toolCall": {"functionCalls": [{"id": "call-1", "name": "ask_agent", "args": {"question": "кто писал сегодня"}}]}},
    ]
    client = make_client(events)
    client.turn_saver = lambda u, a: asyncio.sleep(0, result=True)

    async def executor(call_id, name, args):
        calls.append((call_id, name, args))
        return {"ok": True, "answer": "Писали Нурлан и Айбек"}

    client.function_executor = executor
    sock = FakeClientSocket()
    state = {"is_user_speaking": False, "is_assistant_speaking": False, "last_speech_start": 0, "last_speech_stop": 0,
             "interruption_count": 0, "last_interruption_time": 0, "assistant_audio_ever": False, "greeting_retries": 0}
    task = asyncio.create_task(handler_gemini.handle_gemini_messages(client, sock, state))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if any("toolResponse" in m for m in client.ws.sent):
            break
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return calls, client, sock

calls, client, sock = run(tool_scenario())
check("функция ушла в свой исполнитель, а не в реестр", calls == [("call-1", "ask_agent", {"question": "кто писал сегодня"})], calls)
resp = [m for m in client.ws.sent if "toolResponse" in m]
fr = resp[0]["toolResponse"]["functionResponses"][0] if resp else {}
check("результат вернулся в Gemini (toolResponse с id и именем)",
      fr.get("id") == "call-1" and fr.get("name") == "ask_agent" and fr.get("response", {}).get("answer"), fr)
types_sent = [m.get("type") for m in sock.sent]
check("браузер видит started → executing → completed",
      "function_call.executing" in types_sent and "function_call.completed" in types_sent, types_sent)


# ---------- сессия: последняя реплика владельца и проверка «да» ----------
async def session_flow():
    c = make_client()
    s = hov.OwnerVoiceSession(FakeClientSocket(), "u1", "a1", c)
    s.observe({"type": "transcript.delta", "role": "user", "delta": "Напиши Нурлану, "})
    s.observe({"type": "transcript.delta", "role": "user", "delta": "что перезвоню в три"})
    first = s.last_owner_text()
    r1 = await s.execute("c1", "do_action", {"command": "написать Нурлану", "confirmed": True})
    s.observe({"type": "transcript.delta", "role": "assistant", "delta": "Пишу Нурлану в WhatsApp. Отправляю?"})
    s.observe({"type": "transcript.delta", "role": "user", "delta": "Да"})
    s.observe({"type": "transcript.delta", "role": "assistant", "delta": "Отправляю."})
    second = s.last_owner_text()
    return s, first, r1, second

s, first, r1, second = run(session_flow(), timeout=10)
check("последняя реплика владельца собирается из кусочков", first == "Напиши Нурлану, что перезвоню в три", first)
check("действие без «да» не выполняется (сразу после команды)", r1.get("status") == "rejected", r1)
check("после повтора и «да» последняя реплика — «Да», даже когда агент уже сказал «Отправляю»", second == "Да", second)


# подменяем базу и оркестратор: действие после «да» доходит до оркестратора
class FakeQuery:
    def __init__(self, obj): self.obj = obj
    def filter(self, *a, **k): return self
    def first(self): return self.obj

class FakeSession:
    def __init__(self): self.closed = False
    def query(self, model):
        return FakeQuery(user if model.__name__ == "User" else agent)
    def close(self): self.closed = True

import backend.db.session as dbs
_orig_sl, _orig_run = dbs.SessionLocal, ov.run_orchestrator_task
seen = []
async def fake_run(kind, request, agent_, user_, db_, pairs=None):
    seen.append((kind, request))
    return {"ok": True, "answer": "Отправил Нурлану в WhatsApp", "tools": ["whatsapp_send_message"]}
dbs.SessionLocal = FakeSession
ov.run_orchestrator_task = fake_run
try:
    r2 = run(s.execute("c2", "do_action", {"command": "Написать Нурлану в WhatsApp: перезвоню в три", "confirmed": True}))
    r3 = run(s.execute("c3", "ask_agent", {"question": "Что писал Нурлан?"}))
    r4 = run(s.execute("c4", "unknown_fn", {}))
finally:
    dbs.SessionLocal, ov.run_orchestrator_task = _orig_sl, _orig_run
check("после «да» действие выполнено оркестратором", r2.get("ok") and seen[0] == ("action", "Написать Нурлану в WhatsApp: перезвоню в три"), seen)
check("вопрос идёт к оркестратору в режиме чтения", seen[1:2] == [("read", "Что писал Нурлан?")], seen)
check("счётчик действий", s.actions_done == 1, s.actions_done)
check("неизвестная функция — отказ", r4.get("ok") is False)


# ---------- запрос к оркестратору: запрещённое не выполняется, кредиты списываются ----------
import backend.services.agent_prompts as ap
import backend.services.agent_tools as at
import backend.services.openrouter_client as orc
from backend.services.credit_service import CreditService

executed, charged = [], []
class FakeOR:
    def __init__(self, script): self.script = list(script); self.tools_seen = None
    async def chat_completion(self, model, messages, tools=None, temperature=0.7):
        self.tools_seen = [t["function"]["name"] for t in (tools or [])]
        return self.script.pop(0)

def tc(name, args):
    return {"id": f"t-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

script = [
    {"choices": [{"message": {"content": "", "tool_calls": [tc("delete_agent_contact", {"agent_contact_id": "x"}),
                                                            tc("whatsapp_send_message", {"text": "hi"}),
                                                            tc("search_contacts", {"query": "Нурлан"})]}}],
     "usage": {"prompt_tokens": 100, "completion_tokens": 10}},
    {"choices": [{"message": {"content": "**Нурлан** писал в 14:05: спрашивал цену."}}],
     "usage": {"prompt_tokens": 120, "completion_tokens": 20}},
]
fake_or = FakeOR(script)
saved = (ap.build_orchestrator_prompt, at.build_chat_tools, at.execute_tool, orc.get_openrouter_client,
         CreditService.precheck, CreditService.charge)

async def fake_tools(agent_, db_):
    return [_t(n) for n in ("search_contacts", "whatsapp_send_message", "delete_agent_contact", "get_agent_stats")]
async def fake_exec(name, args, context, db_):
    executed.append((name, context.get("surface")))
    return json.dumps({"ok": True, "contacts": [{"name": "Нурлан"}]})

ap.build_orchestrator_prompt = lambda agent_, include_time_block=True, db=None: "SYSTEM"
at.build_chat_tools = fake_tools
at.execute_tool = fake_exec
orc.get_openrouter_client = lambda: fake_or
CreditService.precheck = classmethod(lambda cls, db_, user_: None)
CreditService.charge = classmethod(lambda cls, **kw: charged.append(kw))
try:
    agent.orchestrator_model = "deepseek/deepseek-v4-pro"
    res = run(ov.run_orchestrator_task("read", "Что писал Нурлан?", agent, user, None, [{"user": "привет", "assistant": "слушаю"}]))
finally:
    (ap.build_orchestrator_prompt, at.build_chat_tools, at.execute_tool, orc.get_openrouter_client,
     CreditService.precheck, CreditService.charge) = saved
check("вопрос: модель видит только инструменты чтения", fake_or.tools_seen == ["search_contacts", "get_agent_stats"], fake_or.tools_seen)
check("вопрос: удаление и отправка НЕ выполнены, поиск выполнен", executed == [("search_contacts", "owner_chat")], executed)
check("ответ без markdown для голоса", res.get("answer") == "Нурлан писал в 14:05: спрашивал цену.", res)
check("кредиты списаны как у чата (voice_chat)", charged and charged[0]["ref_type"] == "voice_chat"
      and charged[0]["prompt_tokens"] == 220, charged)


# ---------- доступ, кредиты за минуты, история чата ----------
def u(mode, credits=5000):
    return types.SimpleNamespace(agent_mode=lambda: mode, credits_balance=credits, is_admin=False)

check("Profi — можно", hov.access_error(u("full"), agent) is None)
check("Старт — только Profi", (hov.access_error(u("reactive"), agent) or {}).get("code") == "profi_required")
check("без доступа — только Profi", (hov.access_error(u(None), agent) or {}).get("code") == "profi_required")
check("мало кредитов — отказ", (hov.access_error(u("full", 50), agent) or {}).get("code") == "insufficient_credits")
check("чужой/нет агента — отказ", (hov.access_error(u("full"), None) or {}).get("code") == "not_found")
check("нет пользователя — отказ", (hov.access_error(None, agent) or {}).get("code") == "unauthorized")
check("голос: регистр восстанавливается", hov.resolve_voice("puck") == "Puck")
check("голос: чужой → по умолчанию Kore", hov.resolve_voice("marin") == "Kore")

check("минута голоса стоит кредиты (≈57)", 40 <= ov.voice_credits(60) <= 80, ov.voice_credits(60))
check("10 минут дороже одной", ov.voice_credits(600) > ov.voice_credits(60))
check("пустой разговор — бесплатно", ov.voice_credits(0) == 0)

class CommitDB:
    def __init__(self): self.commits = 0
    def commit(self): self.commits += 1

hist_agent = types.SimpleNamespace(chat_history=[{"role": "user", "content": str(i)} for i in range(19)])
cdb = CommitDB()
n = ov.append_voice_history(cdb, hist_agent, [{"user": "Дай отчёт", "assistant": "Сегодня четыре звонка"},
                                              {"user": "Отправь в Telegram", "assistant": "Отправила"}])
last2 = hist_agent.chat_history[-2:]
check("разговор голосом записан в историю чата одной парой", n == 2 and last2[0]["role"] == "user"
      and "Дай отчёт" in last2[0]["content"] and "Отправила" in last2[1]["content"] and cdb.commits == 1, last2)
check("история чата не длиннее 20", len(hist_agent.chat_history) == 20)
check("пустой разговор историю не трогает", ov.append_voice_history(cdb, hist_agent, []) == 0 and cdb.commits == 1)

# ---------- обработчик /ws/agent-voice целиком (поддельный браузер и Gemini) ----------
class FakeBrowser:
    def __init__(self, first, frames):
        self.first, self.frames = first, list(frames)
        self.sent, self.accepted, self.closed = [], False, False
        self.query_params = {"voice": "puck", "rate": "24000"}
        self.headers = {"user-agent": "test"}
    async def accept(self): self.accepted = True
    async def receive_json(self): return self.first
    async def receive(self):
        await asyncio.sleep(0.05)
        if self.frames:
            return self.frames.pop(0)
        return {"type": "websocket.disconnect"}
    async def send_json(self, data, mode="text"): self.sent.append(data)
    async def close(self, code=1000): self.closed = True


async def ws_flow(first, mode="full"):
    owner = types.SimpleNamespace(id="u1", first_name="Аман", credits_balance=5000, is_admin=False,
                                  agent_mode=lambda: mode, gemini_api_key=None)
    ag = types.SimpleNamespace(id="a1", name="Мой агент", brand_company_name=None, chat_history=[])
    tokens = []
    finished = {}
    saved = (hov._load_owner, ov.build_session_context, GeminiLiveClient.connect, GeminiLiveClient.close,
             ov.charge_voice_minutes, ov.append_voice_history, dbs.SessionLocal)
    from backend.core.config import settings
    old_key = settings.GEMINI_API_KEY
    settings.GEMINI_API_KEY = "server-key"
    created = []

    def fake_load(db_, token, agent_id):
        tokens.append(token)
        return (owner, ag) if token == "jwt-ok" else (None, None)

    async def fake_connect(self):
        self.ws = FakeGeminiWS([{"setupComplete": {}}])
        self.is_connected = True
        created.append(self)
        return True

    async def fake_close(self):
        self.is_connected = False

    hov._load_owner = fake_load
    ov.build_session_context = lambda db_, user_, agent_: {
        "brief": "Сегодня: звонков 2.",
        "cards": [{"type": "stats", "title": "Сегодня", "tiles": [{"label": "Звонки", "value": 2}]}]}
    GeminiLiveClient.connect = fake_connect
    GeminiLiveClient.close = fake_close
    ov.charge_voice_minutes = lambda db_, user_, agent_, secs: finished.setdefault("charged", secs) and 1
    ov.append_voice_history = lambda db_, agent_, pairs: finished.setdefault("pairs", list(pairs)) and len(pairs)

    class _S:
        def query(self, model):
            return FakeQuery(owner if model.__name__ == "User" else ag)
        def close(self): pass
        def rollback(self): pass
    dbs.SessionLocal = _S
    pcm = b"\x01\x00" * 2400   # 100 мс при 24 кГц
    browser = FakeBrowser(first, [{"type": "websocket.receive", "bytes": pcm},
                                  {"type": "websocket.receive", "text": json.dumps({"type": "ping"})}])
    try:
        await hov.handle_owner_voice_connection(browser, types.SimpleNamespace(close=lambda: None))
    finally:
        (hov._load_owner, ov.build_session_context, GeminiLiveClient.connect, GeminiLiveClient.close,
         ov.charge_voice_minutes, ov.append_voice_history, dbs.SessionLocal) = saved
        settings.GEMINI_API_KEY = old_key
    return browser, tokens, created, finished

br, toks, made, fin = run(ws_flow({"type": "auth", "token": "jwt-ok"}), timeout=10)
st = [m for m in br.sent if m.get("type") == "connection_status"]
check("сокет: токен берётся из первого сообщения, не из адреса", toks == ["jwt-ok"] and "token" not in br.query_params, toks)
check("сокет: подключено, full-duplex, голос Puck", st and st[0].get("full_duplex") is True and st[0].get("voice") == "Puck", st)
check("сокет: Gemini получил промпт помощника с именем и сводкой", made and "С тобой говорит Аман" in made[0].assistant_config.system_prompt
      and "звонков 2" in made[0].assistant_config.system_prompt)
audio = [m for m in (made[0].ws.sent if made else []) if "realtimeInput" in m and "audio" in json.dumps(m)]
check("сокет: звук браузера ушёл в Gemini (24 → 16 кГц)", len(audio) >= 1, len(audio))
check("сокет: приветствие по имени после setupComplete", made and any("Здравствуйте, Аман" in json.dumps(m, ensure_ascii=False)
                                                                   for m in made[0].ws.sent))
check("сокет: на ping отвечает pong", any(m.get("type") == "pong" for m in br.sent))
check("сокет: в конце — списание минут и запись истории", "charged" in fin and "pairs" in fin, fin)
_dash = [m for m in br.sent if m.get("type") == "owner.dashboard"]
check("сокет: сразу после подключения — карточки дня на экран", _dash and _dash[0]["cards"][0]["title"] == "Сегодня", _dash)
check("сокет: в connection_status — настройки обращения и имя", st and st[0].get("prefs", {}).get("address") == "name"
      and st[0].get("owner_name") == "Аман", st)

br2, toks2, made2, fin2 = run(ws_flow({"type": "auth", "token": "bad"}), timeout=10)
err2 = [m for m in br2.sent if m.get("type") == "error"]
check("сокет: чужой/битый токен — отказ без Gemini", err2 and err2[0]["error"]["code"] == "unauthorized" and not made2, err2)
br3, _, made3, _ = run(ws_flow({"type": "auth", "token": "jwt-ok"}, mode="reactive"), timeout=10)
err3 = [m for m in br3.sent if m.get("type") == "error"]
check("сокет: тариф Старт — «только Profi», Gemini не открывается", err3 and err3[0]["error"]["code"] == "profi_required"
      and not made3, err3)

# ---------- периоды и цифры для get_stats ----------
from datetime import datetime as _dt, timedelta as _td
_now = _dt(2026, 11, 8, 15, 0)   # 21:00 в Бишкеке, воскресенье
a, b, label, d1, d2 = ov.period_range("yesterday", now=_now)
check("вчера: сутки по Бишкеку в UTC", a == _dt(2026, 11, 6, 18, 0) and b == _dt(2026, 11, 7, 18, 0) and "07.11" in label, (a, b, label))
a, b, label, d1, d2 = ov.period_range("today", now=_now)
check("сегодня: с полуночи по Бишкеку", a == _dt(2026, 11, 7, 18, 0) and b == _dt(2026, 11, 8, 18, 0), (a, b))
a, b, label, d1, d2 = ov.period_range("week", now=_now)
check("неделя: 7 дней включая сегодня", (d2 - d1).days == 6 and "7 дней" in label, label)
a, b, label, d1, d2 = ov.period_range("this_month", now=_now)
check("этот месяц: с 1-го", d1.day == 1 and d1.month == 11, (d1, d2))
a, b, label, d1, d2 = ov.period_range("custom", "2026-11-05", "2026-11-01", now=_now)
check("свои даты: перепутанный порядок исправляется", d1.day == 1 and d2.day == 5, (d1, d2))
a, b, label, d1, d2 = ov.period_range("custom", "2026-12-01", "2027-01-01", now=_now)
check("свои даты: будущее обрезается сегодняшним днём", d2 <= (_now + _td(hours=6)).date(), (d1, d2))
a, b, label, d1, d2 = ov.period_range("custom", "ерунда", None, now=_now)
check("свои даты: ерунда → сегодня", d1 == d2 == (_now + _td(hours=6)).date())
_st = ov._empty_day(); _st.update(calls_in=2, calls_out=3, answered=4, talk_sec=600, msgs_in=7, writers=3, bot_out=5, owner_out=1, new_clients=2)
_sent = ov._stats_sentence(_st)
check("фраза цифр: звонки, разговоры, сообщения, новые", "звонков 5" in _sent and "10 мин" in _sent and "от 3 клиентов" in _sent
      and "новых клиентов в CRM 2" in _sent, _sent)
check("сумма по дням не складывает «написавших» (их считает отдельный запрос)",
      ov._sum_days({1: dict(_st), 2: dict(_st)})["writers"] == 0 and ov._sum_days({1: dict(_st), 2: dict(_st)})["msgs_in"] == 14)


# ---------- быстрые функции идут в поток, медленные — в фоновый loop ----------
async def fast_route():
    c = make_client()
    sess = hov.OwnerVoiceSession(FakeClientSocket(), "u1", "a1", c)
    import threading
    seen_threads = []
    def fake_fast(name, args, uid, aid):
        seen_threads.append(threading.current_thread().name)
        return {"ok": True, "name": name, "args": args, "card": {"type": "stats"}}
    orig = ov.run_fast_function
    ov.run_fast_function = fake_fast
    try:
        r = await sess.execute("x1", "get_stats", {"period": "yesterday"})
    finally:
        ov.run_fast_function = orig
    return r, seen_threads, threading.current_thread().name

_r, _threads, _main = run(fast_route())
check("быстрая функция выполнена", _r.get("ok") and _r.get("name") == "get_stats", _r)
check("быстрая функция — не в потоке звука (asyncio.to_thread)", _threads and _threads[0] != _main, _threads)


async def slow_route():
    import threading
    c = make_client()
    sess = hov.OwnerVoiceSession(FakeClientSocket(), "u1", "a1", c)
    where = []
    async def fake_slow(name, args, pairs):
        where.append(threading.current_thread().name)
        return {"ok": True, "answer": "готово"}
    sess._execute_slow = fake_slow
    r = await sess.execute("x2", "ask_agent", {"question": "что нового"})
    return r, where

_r2, _where = run(slow_route())
check("оркестратор выполняется в фоновом loop'е (звук не стоит)", _r2.get("ok") and _where == ["background-jobs"], _where)


# ---------- быстрая команда из окна ----------
async def typed_flow():
    c = make_client()
    sent_texts = []
    async def fake_send_text(t):
        sent_texts.append(t); return True
    c.send_text_message = fake_send_text
    frames = [{"type": "websocket.receive", "text": json.dumps({"type": "text.input", "text": "Отчёт за сегодня"})}]
    class Sock(FakeClientSocket):
        async def receive(self):
            await asyncio.sleep(0.01)
            return frames.pop(0) if frames else {"type": "websocket.disconnect"}
    sess = hov.OwnerVoiceSession(Sock(), "u1", "a1", c)
    try:
        await sess.handle_client_messages()
    except Exception:
        pass
    await sess.save_turn("", "Сегодня четыре звонка")
    return sent_texts, sess

_texts, _sess = run(typed_flow())
check("быстрая команда ушла в Gemini текстом", _texts == ["Отчёт за сегодня"], _texts)
check("быстрая команда считается репликой владельца (для «да»)", _sess.last_owner_text() == "Отчёт за сегодня")
check("в историю: команда + ответ одной парой", _sess.pairs[-1] == {"user": "Отчёт за сегодня", "assistant": "Сегодня четыре звонка"},
      _sess.pairs[-1:])


# ---------- голосовой токен Mini App ----------
import time as _time
tok = ov.make_voice_token("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222")
rd = ov.read_voice_token(tok)
check("токен Mini App читается", rd == {"user_id": "11111111-1111-1111-1111-111111111111",
                                        "agent_id": "22222222-2222-2222-2222-222222222222"}, rd)
check("токен Mini App — не JWT кабинета (свой формат ov1.)", tok.startswith("ov1.") and tok.count(".") == 2)
_parts = tok.split(".")
check("подделанный токен отклоняется", ov.read_voice_token(_parts[0] + "." + _parts[1] + "x." + _parts[2]) is None)
check("токен с чужой подписью отклоняется", ov.read_voice_token(_parts[0] + "." + _parts[1] + "." + "A" * len(_parts[2])) is None)
check("просроченный токен отклоняется", ov.read_voice_token(tok, now=_time.time() + ov.VOICE_TOKEN_TTL + 5) is None)
check("мусор вместо токена — None", ov.read_voice_token("abc") is None and ov.read_voice_token("") is None)


class OwnerDB:
    def __init__(self, user_obj, agent_obj): self.u, self.a, self.filters = user_obj, agent_obj, []
    def query(self, model):
        db = self
        class Q:
            def filter(self, *conds):
                db.filters.append(conds); return self
            def order_by(self, *a): return self
            def first(self): return db.u if model.__name__ == "User" else db.a
        return Q()

_owner = types.SimpleNamespace(id="11111111-1111-1111-1111-111111111111")
_ag = types.SimpleNamespace(id="22222222-2222-2222-2222-222222222222")
u1, a1 = hov._load_owner(OwnerDB(_owner, _ag), tok, None)
check("сокет: вход по токену Mini App", u1 is _owner and a1 is _ag)
u2, a2 = hov._load_owner(OwnerDB(_owner, _ag), tok[:-3] + "AAA", None)
check("сокет: подделанный токен Mini App — отказ", u2 is None and a2 is None)


# ---------- подпись initData Telegram ----------
import hashlib as _hl, hmac as _hm
from urllib.parse import urlencode as _ue

def init_data(bot_token, user_id=777, age=10, tamper=False):
    fields = {"auth_date": str(int(_time.time()) - age), "query_id": "AAH", "signature": "sig",
              "user": json.dumps({"id": user_id, "first_name": "Аман"}, ensure_ascii=False, separators=(",", ":"))}
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = _hm.new(b"WebAppData", bot_token.encode(), _hl.sha256).digest()
    fields["hash"] = _hm.new(secret, check_str.encode(), _hl.sha256).hexdigest()
    if tamper:
        fields["user"] = fields["user"].replace("777", "778")
    return _ue(fields)

_bot = "123456:ABCDEF"
_ok = ov.verify_webapp_init_data(init_data(_bot), _bot)
check("initData с верной подписью принимается", _ok and _ok["user"]["id"] == 777, _ok)
check("initData с подменённым пользователем отклоняется", ov.verify_webapp_init_data(init_data(_bot, tamper=True), _bot) is None)
check("initData чужого бота отклоняется", ov.verify_webapp_init_data(init_data(_bot), "999:OTHER") is None)
check("старые initData (больше суток) отклоняются", ov.verify_webapp_init_data(init_data(_bot, age=2 * 86400), _bot) is None)
check("пустые initData отклоняются", ov.verify_webapp_init_data("", _bot) is None and ov.verify_webapp_init_data("a=b", _bot) is None)
from backend.core.config import settings as _settings
_old_host = _settings.HOST_URL
_settings.HOST_URL = "https://voksyai.online/"
check("адрес Mini App", ov.webapp_url("a1") == "https://voksyai.online/static/voice-live.html?agent=a1", ov.webapp_url("a1"))
_settings.HOST_URL = _old_host


# ---------- голосовой ответ в Telegram ----------
from backend.services import owner_telegram_voice as otv
_md = "**Итог дня**\n\n| Канал | Сообщений |\n|---|---|\n| WhatsApp | 12 |\n\n- Нурлан ждёт ответа\n- [Ссылка](https://x.y)"
_pl = otv.plain_text(_md)
check("пересказ без markdown, таблиц и ссылок", "**" not in _pl and "|" not in _pl and "https" not in _pl and "Нурлан" in _pl, _pl)
check("длинный ответ обрезается по предложению", len(otv.plain_text("Раз два три. " * 200)) <= otv.MAX_SPOKEN + 1)

async def spoken_fallback():
    import backend.services.openrouter_client as _orc
    orig = _orc.get_openrouter_client
    class Boom:
        async def chat_completion(self, **kw): raise RuntimeError("down")
    _orc.get_openrouter_client = lambda: Boom()
    try:
        return await otv.spoken_version("**Сегодня** 4 звонка")
    finally:
        _orc.get_openrouter_client = orig

_sp, _usage, _model = run(spoken_fallback())
check("модель пересказа недоступна → простой текст, без списания", _sp == "Сегодня 4 звонка" and _usage == (0, 0) and _model is None,
      (_sp, _usage, _model))


# ---------- обращение к владельцу: по имени / шеф / босс / своё, на вы / на ты ----------
check("настройки по умолчанию: по имени, на вы", ov.clean_prefs(None) == {"address": "name", "formality": "vy"})
check("настройки: мусор отбрасывается", ov.clean_prefs({"address": "king", "formality": "xx", "voice": "Ko re!", "evil": 1})
      == {"address": "name", "formality": "vy"})
check("настройки: своё обращение без текста → по имени", ov.clean_prefs({"address": "custom"})["address"] == "name")
_ct = ov.clean_prefs({"address": "custom", "address_text": "«Аман-ака»\n<b>" + "я" * 60})["address_text"]
check("настройки: текст обращения чистится и обрезается", _ct.startswith("Аман-ака b") and len(_ct) <= 40
      and not any(ch in _ct for ch in "«»<>\n"), _ct)
check("настройки: голос сохраняется", ov.clean_prefs({"voice": "Puck"})["voice"] == "Puck")
_u = types.SimpleNamespace(first_name="Аман")
check("обращение по имени", ov.address_word({"address": "name"}, _u) == "Аман"
      and "по имени — «Аман»" in ov.address_note({"address": "name"}, _u))
check("обращение по имени — своё имя из настроек", ov.address_word({"address": "name", "address_text": "Аманат"}, _u) == "Аманат")
check("обращение «шеф»: не по имени", ov.address_word({"address": "chef"}, _u) == "шеф" and "по имени не называй" in ov.address_note({"address": "chef"}, _u))
check("обращение «босс»", ov.address_word({"address": "boss"}, _u) == "босс")
check("своё обращение", ov.address_word({"address": "custom", "address_text": "Аман-ака"}, _u) == "Аман-ака")
check("на «ты» — дружески", "на «ты»" in ov.address_note({"formality": "ty"}, _u))
check("приветствие: шеф", ov.greeting_text(_u, {"address": "chef"}) == "Слушаю вас, шеф!")
check("приветствие: по имени на ты", ov.greeting_text(_u, {"formality": "ty"}) == "Привет, Аман! Слушаю.")
check("приветствие: без имени", ov.greeting_text(types.SimpleNamespace(first_name=""), None) == "Здравствуйте! Слушаю вас.")
_chg = ov.address_change_text({"address": "chef", "formality": "ty"}, _u)
check("смена обращения в разговоре: подсказка модели", "«шеф»" in _chg and "на «ты»" in _chg and "не вопрос" in _chg, _chg)
_pp = ov.build_voice_prompt(_u, agent, "сводка", voice="Kore", prefs={"address": "boss"})
check("промпт: блок ОБРАЩЕНИЕ с выбранным словом", "ОБРАЩЕНИЕ" in _pp and "«босс»" in _pp)
check("промпт: цифры показывать карточкой через быструю функцию", "ПОКАЗЫВАЮТ владельцу карточку" in _pp and "даже если ответ есть в сводке" in _pp)
check("дашборд при сбое базы — пустой, без падения", ov.dashboard_cards(BrokenDB(), user, agent) == [])


async def prefs_flow():
    c = make_client()
    texts = []
    async def fake_send_text(t):
        texts.append(t); return True
    c.send_text_message = fake_send_text
    sock = FakeClientSocket()
    sess = hov.OwnerVoiceSession(sock, "u1", "a1", c, prefs={"address": "name"}, owner=types.SimpleNamespace(first_name="Аман"))
    saved_args = []
    orig = ov.save_prefs
    ov.save_prefs = lambda uid, aid, p: saved_args.append((uid, aid, p)) or True
    try:
        await sess._save_prefs({"address": "chef", "formality": "ty", "voice": "Puck"})
        await sess._save_prefs({"address": "chef", "formality": "ty", "voice": "Kore"})   # только голос — без реплики модели
    finally:
        ov.save_prefs = orig
    return sock.sent, texts, saved_args, sess

_sent, _texts, _saved, _sess = run(prefs_flow())
check("настройки из окна сохраняются в базу", len(_saved) == 2 and _saved[0][2]["address"] == "chef" and _saved[0][2]["voice"] == "Puck", _saved)
check("окну отвечает prefs.saved с приветствием", _sent and _sent[0]["type"] == "prefs.saved" and _sent[0]["ok"] is True
      and _sent[0]["greeting"] == "Привет, шеф! Слушаю.", _sent[:1])
check("новое обращение сразу уходит в разговор, смена голоса — нет", len(_texts) == 1 and "«шеф»" in _texts[0], _texts)


# ---------- Telegram: кнопка «Поговорить с агентом» без /voice ----------
import backend.api.agent_telegram as atg

async def menu_flow(mode):
    calls = []
    orig_call = atg.AgentTelegramService._call
    async def fake_call(token, method, payload):
        calls.append((method, payload)); return {"ok": True}
    atg.AgentTelegramService._call = staticmethod(fake_call)
    orig_host = _settings.HOST_URL
    _settings.HOST_URL = "https://voksyai.online"
    atg._VOICE_MENU_DONE.clear()
    ag2 = types.SimpleNamespace(id="a9", user_id="u9", telegram_bot_token="123:x")
    own = types.SimpleNamespace(agent_mode=lambda: mode)
    class D:
        def query(self, m):
            class Q:
                def filter(self, *a): return self
                def first(self): return own
            return Q()
    try:
        r1 = await atg._ensure_voice_menu(ag2, "777", D())
        r2 = await atg._ensure_voice_menu(ag2, "777", D())
        r3 = await atg._ensure_voice_menu(ag2, None, D())
    finally:
        atg.AgentTelegramService._call = orig_call
        _settings.HOST_URL = orig_host
    return r1, r2, r3, calls

_r1, _r2, _r3, _calls = run(menu_flow("full"))
_menu = [p for m, p in _calls if m == "setChatMenuButton"]
check("Telegram: кнопка меню ставится сама для чата владельца", _r1 and _menu and _menu[0]["chat_id"] == 777
      and _menu[0]["menu_button"]["text"] == "🎙 Поговорить с агентом"
      and _menu[0]["menu_button"]["web_app"]["url"] == "https://voksyai.online/static/voice-live.html?agent=a9", _menu[:1])
check("Telegram: второй раз тем же воркером — без лишних запросов", _r2 is False and len(_menu) == 2, len(_menu))
check("Telegram: кнопка по умолчанию для бота + команда в меню", _r3 and "chat_id" not in _menu[1]
      and any(m == "setMyCommands" for m, _ in _calls))
_n1, _n2, _n3, _ncalls = run(menu_flow("reactive"))
check("Telegram: на Старте кнопку живого голоса не ставим", not _n1 and not _ncalls)


# ============================================================ часть 2: настоящий PostgreSQL
PG = os.environ.get("OWNER_VOICE_TEST_DATABASE_URL")
if not PG:
    print("SKIP часть 2: OWNER_VOICE_TEST_DATABASE_URL не задан")
else:
    import uuid as _uuid
    from sqlalchemy import create_engine, text as _text
    from sqlalchemy.orm import sessionmaker
    from backend.models.base import Base
    import backend.models  # noqa: регистрирует все модели
    from backend.models.user import User
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact
    from backend.models.agent_call import AgentCall
    from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppChat, AgentWhatsAppMessage
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.task import Task, TaskStatus

    engine = create_engine(PG)
    with engine.begin() as conn:
        conn.execute(_text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = _dt.utcnow()
    owner = User(email=f"o{_uuid.uuid4().hex[:6]}@x.kg", password_hash="x", credits_balance=5000, first_name="Аман")
    db.add(owner); db.flush()
    ag = AgentConfig(user_id=owner.id, name="Анри", is_active=True)
    db.add(ag); db.flush()

    def mk(name, phone, status="new", created=None, **kw):
        kw.setdefault("memory", {})
        c = AgentContact(agent_config_id=ag.id, user_id=owner.id, name=name, phone=phone, status=status, **kw)
        if created:
            c.created_at = created
        db.add(c); db.flush(); return c
    nurlan = mk("Нурлан Асанов", "+996700111222", status="in_progress", company="Кафе Ала-Тоо",
                memory={"niche": "кафе", "direction_need": "автоответы в WhatsApp"})
    aibek = mk("Айбек", "+996555333444", created=now - _td(days=1))
    wa = AgentWhatsAppAccount(agent_config_id=ag.id, user_id=owner.id, instance_name="i1", webhook_secret="s",
                              status="connected", wa_phone="996700000001")
    db.add(wa); db.flush()
    chat = AgentWhatsAppChat(account_id=wa.id, remote_jid="996700111222@s.whatsapp.net", phone="996700111222",
                             agent_contact_id=nurlan.id)
    db.add(chat); db.flush()

    def wam(d, body, mins, by=None):
        db.add(AgentWhatsAppMessage(agent_config_id=ag.id, account_id=wa.id, chat_id=chat.id, agent_contact_id=nurlan.id,
                                    direction=d, body=body, sent_by=by, created_at=now - _td(minutes=mins)))
    wam("inbound", "Сколько стоит Profi на год?", 30)
    wam("outbound", "Profi на год — со скидкой 30 процентов", 29)
    wam("inbound", "А можно оплатить картой?", 5)
    db.add(AgentInstagramMessage(agent_config_id=ag.id, agent_contact_id=aibek.id, direction="inbound",
                                 body="Можно демо в четверг?", created_at=now - _td(days=1, minutes=10)))
    db.add(AgentCall(agent_contact_id=nurlan.id, agent_config_id=ag.id, user_id=owner.id, direction="inbound",
                     status="answered", duration_seconds=185, call_result="Интересуется Profi на год, просит счёт",
                     created_at=now - _td(minutes=60)))
    db.add(AgentCall(agent_contact_id=aibek.id, agent_config_id=ag.id, user_id=owner.id, direction="outbound",
                     status="no_answer", duration_seconds=0, created_at=now - _td(days=1, hours=1)))
    db.add(AgentCall(agent_contact_id=aibek.id, agent_config_id=ag.id, user_id=owner.id, direction="outbound",
                     status="answered", duration_seconds=95, created_at=now - _td(days=1, minutes=30)))
    # переписка, которую разобрал агент, — не звонок
    db.add(AgentCall(agent_contact_id=nurlan.id, agent_config_id=ag.id, user_id=owner.id, direction="whatsapp_inbound",
                     status="answered", created_at=now - _td(minutes=29)))
    db.add(Task(is_agent_task=True, agent_contact_id=nurlan.id, user_id=owner.id, status=TaskStatus.SCHEDULED,
                scheduled_time=now + _td(hours=3), title="Отправить счёт", channel="call"))
    db.commit()

    brief_pg = ov.build_brief(db, owner, ag)
    check("PG сводка: сегодня 1 звонок, сообщения клиента", "Сегодня: звонков 1" in brief_pg and "сообщений от клиентов 2" in brief_pg,
          brief_pg[:400])
    check("PG сводка: переписка агента не считается звонком", "входящих 1, исходящих 0" in brief_pg, brief_pg[:300])
    check("PG сводка: есть разбивка по дням и «вчера»", "По дням за неделю" in brief_pg and "вчера: звонков 2" in brief_pg,
          brief_pg[brief_pg.find("По дням"):brief_pg.find("По дням") + 300])
    check("PG сводка: последние диалоги и звонки", "Нурлан Асанов" in brief_pg and "просит счёт" in brief_pg)
    check("PG сводка: задачи и воронка", "Отправить счёт" in brief_pg and "Воронка CRM: всего клиентов 2" in brief_pg)
    check("PG сводка в пределах бюджета", len(brief_pg) <= ov.BRIEF_MAX_CHARS, len(brief_pg))

    st = ov.fast_get_stats(db, owner, ag, {"period": "yesterday"})
    check("PG get_stats вчера: 2 звонка, 1 разговор, 1 сообщение, 1 новый клиент",
          st["calls"]["total"] == 2 and st["calls"]["with_talk"] == 1 and st["messages"]["from_clients"] == 1
          and st["new_clients"] == 1, st)
    wk = ov.fast_get_stats(db, owner, ag, {"period": "week"})
    check("PG get_stats неделя: 3 звонка, 7 дней в разбивке, карточка", wk["calls"]["total"] == 3 and len(wk["by_day"]) == 7
          and wk["card"]["type"] == "stats" and len(wk["card"]["bars"]) == 7, wk.get("calls"))
    check("PG get_stats: клиентов написало — уникальные", wk["messages"]["clients_wrote"] == 2, wk["messages"])

    ci = ov.fast_client_info(db, owner, ag, {"query": "Нурлан"})
    check("PG client_info по имени: карточка, этап, что знает агент", ci["ok"] and ci["client"]["name"] == "Нурлан Асанов"
          and "кафе" in ci["client"].get("agent_knows", ""), ci.get("client"))
    check("PG client_info: последние сообщения всех каналов", any("картой" in x for x in ci["client"]["recent"]), ci["client"]["recent"])
    check("PG client_info: итог звонка", any("просит счёт" in x for x in ci["client"].get("call_results", [])))
    check("PG client_info: разбор переписки не выдаётся за звонок, итог по-русски",
          sum("звонок" in x for x in ci["client"]["recent"]) == 1 and any("был разговор" in x for x in ci["client"]["recent"]),
          ci["client"]["recent"])
    ci2 = ov.fast_client_info(db, owner, ag, {"query": "+996 700 111 222"})
    check("PG client_info по номеру в любом формате", ci2["ok"] and ci2["client"]["name"] == "Нурлан Асанов", ci2.get("message"))
    ci3 = ov.fast_client_info(db, owner, ag, {"query": "Несуществующий"})
    check("PG client_info: не нашёл — так и говорит", ci3["ok"] is False and "не нашёл" in ci3["message"])

    ld = ov.fast_list_dialogs(db, owner, ag, {"folder": "waiting"})
    check("PG list_dialogs: ждут ответа — Нурлан и Айбек", [d["name"] for d in ld["dialogs"]] == ["Нурлан Асанов", "Айбек"],
          [d["name"] for d in ld["dialogs"]])
    ld2 = ov.fast_list_dialogs(db, owner, ag, {"folder": "all", "channel": "instagram"})
    check("PG list_dialogs: фильтр по каналу", [d["name"] for d in ld2["dialogs"]] == ["Айбек"], ld2["dialogs"])
    cards_pg = ov.dashboard_cards(db, owner, ag)
    _types = [c["type"] for c in cards_pg]
    check("PG дашборд: сегодня, ждут ответа, воронка", _types == ["stats", "list", "funnel"], _types)
    _tiles = {t["label"]: t["value"] for t in cards_pg[0]["tiles"]}
    check("PG дашборд: плитки сегодня (звонок 1, сообщений 2, ждут 2) и график 7 дней",
          _tiles.get("Звонки") == 1 and _tiles.get("Сообщения") == 2 and _tiles.get("Ждут ответа") == 2
          and len(cards_pg[0]["bars"]) == 7 and cards_pg[0]["bars"][-1]["label"] == "сег", (_tiles, cards_pg[0]["bars"]))
    check("PG дашборд: в воронке все клиенты", sum(st["value"] for st in cards_pg[2]["stages"]) == 2, cards_pg[2])
    check("PG дашборд: у клиентов в списке есть канал (цвет точки)", cards_pg[1]["items"][0]["channel"] == "whatsapp", cards_pg[1]["items"][:1])
    gs = ov.fast_get_schedule(db, owner, ag, {"days": 2})
    check("PG get_schedule: задача на звонок", gs["count"] == 1 and gs["items"][0]["client"] == "Нурлан Асанов", gs)
    db.close()
    engine.dispose()


print(f"\nИТОГО провалов: {failures}")
sys.exit(1 if failures else 0)
