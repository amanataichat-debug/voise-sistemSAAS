"""Движок «Google Gemini Live» карточки ElevenLabs (websockets/handler_gemini_live.py), без сети и БД.

Поддельный Gemini присылает события (setupComplete, звук, транскрипты, turnComplete) в настоящий
разбор handle_gemini_messages; проверяем, что звук и реплики доходят до клиента, пары копятся в сессии,
приветствие уходит после setupComplete, а Gemini-ассистенты без turn_saver ведут себя как раньше.
"""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, audioop, base64, json, sys, types

sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="https://example.com", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")

from backend.core.config import settings
from backend.models import eleven_assistant as ea
from backend.services import wallet_service
from backend.websockets import handler_gemini, handler_gemini_live as gl
from backend.websockets.gemini_client import GeminiLiveClient

failures = 0
def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ---------- конфигурация движка ----------
check("движок gemini_live в списке карточки", ea.VOICE_ENGINE_GEMINI_LIVE in ea.VOICE_ENGINE_IDS)
check("колонка voice_engine вмещает 'gemini_live'", len("gemini_live") <= ea.ElevenAssistantConfig.voice_engine.type.length)
check("цена минуты Gemini Live есть и ниже ElevenLabs",
      0 < wallet_service.som_per_minute("gemini_live") < wallet_service.som_per_minute("eleven"),
      wallet_service.som_per_minute("gemini_live"))
check("Gemini Live в тарифах кошелька", any(t["engine"] == "gemini_live" for t in wallet_service.tariffs()))
check("голос: регистр восстанавливается", gl.resolve_voice("aoede") == "Aoede" and gl.resolve_voice("KORE") == "Kore")
check("голос GPT-Live (marin) → голос по умолчанию Gemini", gl.resolve_voice("marin") == ea.GEMINI_LIVE_DEFAULT_VOICE)
check("пустой голос → по умолчанию", gl.resolve_voice(None) == ea.GEMINI_LIVE_DEFAULT_VOICE)

settings.GEMINI_API_KEY = "server-key"
check("ключ: серверный в приоритете", gl.resolve_api_key(None, types.SimpleNamespace(user_id=None)) == "server-key")
settings.GEMINI_API_KEY = ""
check("ключ: нигде нет → None", gl.resolve_api_key(None, types.SimpleNamespace(user_id=None)) is None)


# ---------- подставной сокет и клиент ----------
class FakeClientSocket:
    """Куда сессия шлёт события клиенту (виджет / SIP-адаптер)."""
    def __init__(self): self.sent = []
    async def send_json(self, data, mode="text"): self.sent.append(data)
    def types(self): return [m.get("type") for m in self.sent]


class FakeGeminiWS:
    """Сервер Gemini: выдаёт заготовленные события, запоминает всё, что ему отправили."""
    def __init__(self, events):
        self.events = [json.dumps(e) for e in events]
        self.sent = []
    async def send(self, data): self.sent.append(json.loads(data))
    async def recv(self):
        if self.events:
            return self.events.pop(0)
        await asyncio.sleep(3600)   # дальше тишина, пока задачу не отменят
    async def close(self): pass


def pcm24(ms=100):
    return base64.b64encode(b"\x01\x00" * (24 * ms)).decode()


def make_client(events, saver=None, realtime_text=True):
    assistant = types.SimpleNamespace(
        id="a1", user_id=None, name="Тест", greeting_message="Саламатсызбы!", google_sheet_id=None,
        functions=None, voice=None, system_prompt="Ты ассистент.", live_voice="aoede")
    c = GeminiLiveClient("key", assistant, "client-1", db_session=None, voice_override="Aoede", record_conversation=False,
                         realtime_text=realtime_text)
    c.ws = FakeGeminiWS(events)
    c.is_connected = True
    c.turn_saver = saver
    return c


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 5))


async def scenario(saver):
    events = [
        {"setupComplete": {}},
        {"serverContent": {"outputTranscription": {"text": "Саламатсызбы! "}}},
        {"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": pcm24()}}]}}},
        {"serverContent": {"turnComplete": True}},
        {"serverContent": {"inputTranscription": {"text": "Салам, баасы канча?"}}},
        {"serverContent": {"outputTranscription": {"text": "Баасы 500 сом."}}},
        {"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": pcm24()}}]}}},
        {"serverContent": {"turnComplete": True}},
    ]
    client = make_client(events, saver)
    sock = FakeClientSocket()
    state = {"is_user_speaking": False, "is_assistant_speaking": False, "last_speech_start": 0, "last_speech_stop": 0,
             "interruption_count": 0, "last_interruption_time": 0, "assistant_audio_ever": False, "greeting_retries": 0}
    task = asyncio.create_task(handler_gemini.handle_gemini_messages(client, sock, state))
    for _ in range(100):               # ждём, пока обработаны все заготовленные события
        await asyncio.sleep(0.02)
        if not client.ws.events:
            await asyncio.sleep(0.1)
            break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return client, sock, state


# ---------- сценарий через настоящий handle_gemini_messages ----------
pairs = []
async def saver(user, assistant):
    pairs.append((user, assistant))
    return True

client, sock, state = run(scenario(saver))
check("звук ассистента дошёл до клиента", sock.types().count("response.audio.delta") == 2, sock.types())
check("клиенту отправлено assistant.speech.started", "assistant.speech.started" in sock.types())
check("assistant_audio_ever выставлен", state["assistant_audio_ever"] is True)
check("реплики ушли в turn_saver двумя парами", len(pairs) == 2, pairs)
check("первая пара — приветствие без реплики клиента", pairs and pairs[0][0] in ("", "[no user input]") and "Саламатсызбы" in pairs[0][1], pairs[:1])
check("вторая пара — вопрос и ответ", len(pairs) == 2 and "баасы" in pairs[1][0] and "500" in pairs[1][1], pairs[1:] )
greeting = [m for m in client.ws.sent if "realtimeInput" in m or "clientContent" in m]
check("приветствие отправлено после setupComplete", len(greeting) == 1 and "Саламатсызбы!" in json.dumps(greeting[0], ensure_ascii=False), greeting)
check("карточка ElevenLabs: приветствие через realtimeInput.text", "realtimeInput" in greeting[0] and "text" in greeting[0]["realtimeInput"], greeting)
old_style = make_client([], realtime_text=False)
check("Gemini-ассистент: приветствие прежним clientContent (не менялось)", "clientContent" in old_style._text_payload("x"))
check("клиенту ушёл turn.saved", "turn.saved" in sock.types())


# ---------- Gemini-ассистент без turn_saver: прежний путь (ConversationService) ----------
calls = []
async def fake_save(**kw):
    calls.append(kw); return "saved"
orig = handler_gemini.ConversationService.save_conversation
handler_gemini.ConversationService.save_conversation = staticmethod(fake_save)
try:
    client2 = make_client([])
    r = run(handler_gemini._save_turn(client2, db=None, assistant_id="a1", user_message="привет",
                                      assistant_message="здравствуйте", session_id="s", caller_number=None, tokens_used=0))
    check("без turn_saver реплика идёт в ConversationService", r == "saved" and len(calls) == 1)
    client2.turn_saver = saver
    n = len(calls)
    run(handler_gemini._save_turn(client2, db=None, assistant_id="a1", user_message="привет", assistant_message="ок"))
    check("с turn_saver ConversationService не трогаем", len(calls) == n)
    check("с turn_saver Sheets пропускаются (пишет сессия)", run(handler_gemini._log_sheets(client2, sheet_id="x")) is True)
finally:
    handler_gemini.ConversationService.save_conversation = orig


# ---------- сессия: заглушки, ресемплинг, вход ----------
class DummyLog:
    def __init__(self): self.rows = []
    def add(self, kind, text, level="info", **d): self.rows.append((kind, text))

asst = types.SimpleNamespace(id="a1", user_id=None, name="Тест")
cl = make_client([])
log = DummyLog()
widget = gl.GeminiLiveVoiceSession(FakeClientSocket(), asst, cl, None, "c1", telephony=False, call_log=log)
phone = gl.GeminiLiveVoiceSession(FakeClientSocket(), asst, cl, None, "c1", telephony=True, call_log=None)
pcm = b"\x10\x00" * 2400   # 100 мс при 24 кГц
check("виджет: вход 24 кГц сжимается до 16 кГц", len(widget._to_gemini(pcm)) in range(3190, 3210), len(widget._to_gemini(pcm)))
check("телефон: вход уже 16 кГц, без пересчёта", phone._to_gemini(pcm) is pcm)

run(widget.save_turn("[no user input]", "Здравствуйте"))
run(widget.save_turn("Привет", "[no response]"))
run(widget.save_turn("", ""))
check("заглушки «[no …]» не попадают в диалог", widget.pairs == [{"user": "", "assistant": "Здравствуйте"}, {"user": "Привет", "assistant": ""}], widget.pairs)
check("реплики попали в журнал звонка", [k for k, _ in log.rows] == ["assistant", "user"], log.rows)

# цикл входа от клиента: звук уходит в Gemini как realtimeInput.audio, ping получает pong
class ScriptSocket(FakeClientSocket):
    def __init__(self, msgs): super().__init__(); self.msgs = list(msgs)
    async def receive(self):
        if self.msgs: return self.msgs.pop(0)
        return {"type": "websocket.disconnect"}

audio_msg = {"text": json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode(), "event_id": "e1"})}
sess = gl.GeminiLiveVoiceSession(ScriptSocket([{"text": json.dumps({"type": "ping"})}, audio_msg, {"bytes": pcm}]),
                                 asst, cl, None, "c1", telephony=False, call_log=None)
try:
    run(sess.handle_client_messages())
except Exception as exc:
    check("конец цикла входа — отключение клиента", type(exc).__name__ == "WebSocketDisconnect", type(exc).__name__)
sent_audio = [m for m in cl.ws.sent if "realtimeInput" in m and "audio" in m["realtimeInput"]]
check("звук виджета ушёл в Gemini (текстом и бинарно)", len(sent_audio) == 2, len(cl.ws.sent))
check("Gemini получает PCM 16 кГц", all(m["realtimeInput"]["audio"]["mimeType"] == "audio/pcm;rate=16000" for m in sent_audio))
check("клиент получил pong и ack", {"pong", "input_audio_buffer.append.ack"} <= set(sess.websocket.types()), sess.websocket.types())


# ---------- отказ Google на setup: больше не тишина, а понятная причина ----------
import websockets as _ws
from websockets.frames import Close
from backend.websockets import gemini_client as gc_mod


class FakeSetupWS(FakeGeminiWS):
    """Google на setup: закрывает сессию с кодом и причиной ИЛИ молчит ИЛИ отвечает setupComplete."""
    def __init__(self, mode):
        super().__init__([])
        self.mode = mode
    async def recv(self):
        if self.mode == "closed":
            raise _ws.exceptions.ConnectionClosedError(Close(1007, "API key not valid. Please pass a valid API key."), None)
        if self.mode == "silent" or getattr(self, "_answered", False):
            await asyncio.sleep(3600)   # Google ответил один раз, дальше тишина
        self._answered = True
        return json.dumps({"setupComplete": {}})


def connect_with(mode, timeout=None):
    holder = {}
    async def fake_connect(*a, **kw):
        holder["ws"] = FakeSetupWS(mode)
        return holder["ws"]
    orig = gc_mod.websockets.connect
    gc_mod.websockets.connect = fake_connect
    try:
        asst = types.SimpleNamespace(id="a1", user_id=None, name="T", greeting_message="Салам", google_sheet_id=None,
                                     functions=None, voice=None, system_prompt="p", live_voice="aoede")
        c = GeminiLiveClient("bad-key", asst, "c", db_session=None, voice_override="Aoede", record_conversation=False,
                             realtime_text=True, await_setup=True)
        if timeout is not None:
            orig_wait = c._wait_setup_reply
            c._wait_setup_reply = lambda t=timeout: orig_wait(t)
        return c, run(c.connect())
    finally:
        gc_mod.websockets.connect = orig

c1, ok1 = connect_with("closed")
check("неверный ключ: connect() возвращает False, а не тишину", ok1 is False)
check("причина Google попала в connect_error (код и текст)", "1007" in (c1.connect_error or "") and "API key not valid" in (c1.connect_error or ""), c1.connect_error)
c2, ok2 = connect_with("silent", timeout=0.2)
check("тишина Google на setup: отказ с понятной причиной", ok2 is False and "не ответил" in (c2.connect_error or ""), c2.connect_error)
c3, ok3 = connect_with("ok")
check("setupComplete: сессия открыта", ok3 is True and c3.connect_error is None)
check("прочитанный setupComplete сохранён для цикла событий", len(c3._pending) == 1 and "setupComplete" in c3._pending[0])
# цикл событий разбирает сохранённый setupComplete и шлёт приветствие
sock3 = FakeClientSocket()
st3 = {"is_user_speaking": False, "is_assistant_speaking": False, "last_speech_start": 0, "last_speech_stop": 0,
       "interruption_count": 0, "last_interruption_time": 0, "assistant_audio_ever": False, "greeting_retries": 0}
async def drain():
    t = asyncio.create_task(handler_gemini.handle_gemini_messages(c3, sock3, st3))
    await asyncio.sleep(0.3); t.cancel(); await asyncio.gather(t, return_exceptions=True)
run(drain())
check("отложенный setupComplete → приветствие отправлено", any("realtimeInput" in m for m in c3.ws.sent), c3.ws.sent)


# ---------- промпт: GPT-Live не изменился, Gemini получил те же правила манеры ----------
import hashlib
from backend.websockets import live_client as lc, voice_hints as vh

hint = lc.VOICE_DELEGATION_HINT
check("GPT-Live: VOICE_DELEGATION_HINT побайтно прежний (после разделения на куски)",
      hashlib.sha256(hint.encode()).hexdigest() == "28eb639a49388dd8be476d7c5a20430ae40823d49ba99201afdf7815cd70b286", len(hint))
check("GPT-Live: телефонная подсказка прежняя",
      hashlib.sha256(lc.VOICE_TELEPHONY_HINT.encode()).hexdigest() == "987187f3747507d2578f880ee5d35194c36f279702faad801a982df246ed08fa")
check("куски склеиваются в тот же текст для GPT",
      hint == lc.VOICE_MANNER_HINT + lc.VOICE_DELEGATION_POLICY + lc.VOICE_DIALOG_RULES + lc.VOICE_ALREADY_KNOW_HINT + lc.VOICE_GREETING_RULE)

fn_list = [{"name": "search_knowledge_base", "description": "Ищет ответ в базе знаний компании. Возвращает текст."},
           {"name": "hangup_call", "description": "Завершает звонок."}]
g = vh.build_gemini_voice_hints(fn_list, "Aoede", telephony=True)
check("Gemini получает манеру: краткость, эмоции, живая речь, язык, кыргызский",
      all(k in g for k in ("ГЛАВНОЕ ПРАВИЛО — КРАТКОСТЬ", "ЭМОЦИИ И ЮМОР", "ЖИВАЯ РЕЧЬ", "ЯЗЫК.", "КЫРГЫЗСКИЙ.", "СТАБИЛЬНЫЙ ГОЛОС")))
check("Gemini получает правила диалога: перебивания, завершение, материалы, приветствие",
      all(k in g for k in ("Interruption policy", "ЗАВЕРШЕНИЕ", "МАТЕРИАЛЫ", "ПРИВЕТСТВИЕ")))
check("Gemini: правило «не знаю» только после поиска и список функций",
      "не говори «не знаю»" in g and "- search_knowledge_base:" in g and "- hangup_call:" in g)
check("Gemini: правило разговора под проактивный звук 3.8 (отвечай сразу, один язык, UNMISTAKABLY)",
      "КАК ВЕСТИ РАЗГОВОР" in g and "UNMISTAKABLY" in g and "не молчи" in g
      and vh.GEMINI_CONVERSATION_HINT not in lc.VOICE_DELEGATION_HINT)
check("Gemini: пока клиент говорит — молчать (никаких «мм/угу» поверх речи)", "МОЛЧИ" in g and "поверх его речи" in g)
_ln = vh.language_note("ky")
check("Gemini: язык карточки — только язык приветствия, дальше язык собеседника, без отказа от русского",
      "Приветствие" in _ln and "на том языке, на котором говорит" in _ln and "плохо владеешь" in _ln
      and "Начинай разговор" not in _ln and vh.language_note("") == "")
check("Gemini: телефонная подсказка и женский род для Aoede", "телефонный звонок" in g and "женском роде" in g)
check("Gemini: без правил про бэкенд делегирования GPT",
      "Delegation policy" not in g and "Do not delegate" not in g and "бэкенда" not in g)
check("Gemini: голос Puck → мужской род; на сайте без телефона нет телефонной подсказки",
      "мужском роде" in vh.build_gemini_voice_hints(fn_list, "Puck", telephony=False)
      and "телефонный звонок" not in vh.build_gemini_voice_hints(fn_list, "Puck", telephony=False))
check("голоса Gemini размечены по роду", all(lc.voice_gender_hint(v) for v in ("aoede", "kore", "leda", "puck", "charon", "fenrir", "orus")))

# ---------- функции карточки: BLOCKING, без scheduling; у Gemini-ассистентов прежнее NON_BLOCKING ----------
async def setup_payload(blocking):
    c = make_client([], realtime_text=True)
    c.blocking_tools = blocking
    c.assistant_config.functions = [{"name": "search_knowledge_base", "description": "d", "parameters": {"type": "object", "properties": {}}}]
    await c.setup_session(voice="Aoede", system_message="p", functions=c.assistant_config.functions)
    sent = [m for m in c.ws.sent if "setup" in m][0]
    return c, sent

cb, sp = run(setup_payload(True))
beh = sp["setup"]["tools"][0]["function_declarations"][0]["behavior"]
_vad = sp["setup"]["realtimeInputConfig"]["automaticActivityDetection"]
check("VAD по умолчанию: ждёт дольше (800 мс, конец речи LOW, начало LOW) — не вступает на паузе клиента",
      _vad["silenceDurationMs"] == 800 and _vad["endOfSpeechSensitivity"] == "END_SENSITIVITY_LOW"
      and _vad["startOfSpeechSensitivity"] == "START_SENSITIVITY_LOW", str(_vad))
check("карточка ElevenLabs: функции BLOCKING", beh == "BLOCKING", beh)
_flat = json.dumps(sp)
check("setup 3.8: нет enableAffectiveDialog, thinkingConfig, proactivity и languageCode (модель их не принимает)",
      not any(k in _flat for k in ("ffectiveDialog", "affective_dialog", "thinkingConfig", "thinking_level", "proactiv", "languageCode")))
cn, sp2 = run(setup_payload(False))
check("Gemini-ассистент: функции NON_BLOCKING как раньше", sp2["setup"]["tools"][0]["function_declarations"][0]["behavior"] == "NON_BLOCKING")
run(cb.send_function_result("id1", {"ok": True}))
fr = [m for m in cb.ws.sent if "toolResponse" in m][0]["toolResponse"]["functionResponses"][0]
check("BLOCKING: в toolResponse нет scheduling", "scheduling" not in fr, fr)
run(cn.send_function_result("id2", {"ok": True}))
fr2 = [m for m in cn.ws.sent if "toolResponse" in m][0]["toolResponse"]["functionResponses"][0]
check("NON_BLOCKING: scheduling передаётся", "scheduling" in fr2, fr2)

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
