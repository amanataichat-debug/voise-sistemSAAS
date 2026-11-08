"""Голосовой режим оркестратора (ветка 0811-voice-jarvis): владелец ↔ Gemini Live ↔ оркестратор. Без сети и БД.

Проверяем: разделение инструментов (чтение / действие / голосом нельзя), проверку «да» перед действием,
промпт и функции Gemini, крючки GeminiLiveClient (свои функции в обход реестра, свой исполнитель) через
настоящий handle_gemini_messages, запрос к оркестратору (запрещённый инструмент не выполняется), доступ
только Profi, кредиты за минуты и запись разговора в историю чата.
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
check("три функции голосового помощника", ov.FUNCTION_NAMES == ["ask_agent", "do_action", "send_to_telegram"])
for f in ov.GEMINI_FUNCTIONS:
    check(f"схема {f['name']} не меняется очисткой под Gemini", gemini_schema(f["parameters"]) == f["parameters"])
check("do_action требует confirmed", "confirmed" in ov.GEMINI_FUNCTIONS[1]["parameters"]["required"])

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
check("в setup ушли ровно три функции помощника", [d["name"] for d in decl] == ov.FUNCTION_NAMES, [d["name"] for d in decl])
check("описания и параметры — наши, не из реестра", decl[0]["description"] == ov.GEMINI_FUNCTIONS[0]["description"]
      and decl[1]["parameters"]["properties"].get("confirmed"))
check("функции не блокирующие (агент говорит «секунду, смотрю»)", all(d["behavior"] == "NON_BLOCKING" for d in decl)
      if not ov.BLOCKING_TOOLS else all(d["behavior"] == "BLOCKING" for d in decl))
check("enabled_functions = функции помощника", c.enabled_functions == ov.FUNCTION_NAMES, c.enabled_functions)
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
    saved = (hov._load_owner, ov.build_brief, GeminiLiveClient.connect, GeminiLiveClient.close,
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
    ov.build_brief = lambda db_, user_, agent_: "Сегодня: звонков 2."
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
        (hov._load_owner, ov.build_brief, GeminiLiveClient.connect, GeminiLiveClient.close,
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

br2, toks2, made2, fin2 = run(ws_flow({"type": "auth", "token": "bad"}), timeout=10)
err2 = [m for m in br2.sent if m.get("type") == "error"]
check("сокет: чужой/битый токен — отказ без Gemini", err2 and err2[0]["error"]["code"] == "unauthorized" and not made2, err2)
br3, _, made3, _ = run(ws_flow({"type": "auth", "token": "jwt-ok"}, mode="reactive"), timeout=10)
err3 = [m for m in br3.sent if m.get("type") == "error"]
check("сокет: тариф Старт — «только Profi», Gemini не открывается", err3 and err3[0]["error"]["code"] == "profi_required"
      and not made3, err3)

print(f"\nИТОГО провалов: {failures}")
sys.exit(1 if failures else 0)
