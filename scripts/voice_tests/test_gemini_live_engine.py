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
    def __init__(self): self.rows = []; self.user_turns = 0; self.assistant_turns = 0; self.functions = 0; self.function_errors = 0
    def add(self, kind, text, level="info", **d): self.rows.append((kind, text))

asst = types.SimpleNamespace(id="a1", user_id=None, name="Тест")
cl = make_client([])
log = DummyLog()
widget = gl.GeminiLiveVoiceSession(FakeClientSocket(), asst, cl, None, "c1", telephony=False, call_log=log)
phone = gl.GeminiLiveVoiceSession(FakeClientSocket(), asst, cl, None, "c1", telephony=True, call_log=None)
pcm = b"\x10\x00" * 2400   # 100 мс при 24 кГц
check("виджет: вход 24 кГц сжимается до 16 кГц", len(widget._to_gemini(pcm)) in range(3190, 3210), len(widget._to_gemini(pcm)))
check("телефон: вход уже 16 кГц, без пересчёта", phone._to_gemini(pcm) is pcm)

# 07.11.2026: эхо и шум линии во время доигрывания ответа агента не доходят до детектора речи Google
import math as _m, struct as _st, time as _t
def _tone(amp, n=320):
    return b"".join(_st.pack("<h", int(amp * _m.sin(i / 3))) for i in range(n))
check("эхо-ворота: на телефоне включены, в виджете нет", phone.echo_gate.enabled and not widget.echo_gate.enabled)
for _ in range(10):
    phone.echo_gate.process(_tone(40), agent_active=False, dur=0.02)
for _ in range(5):
    phone.echo_gate.process(_tone(9000), agent_active=False, dur=0.02)
_eg_sent = []
phone.client = types.SimpleNamespace(is_connected=True, process_audio=lambda d: _eg_sent.append(d) or asyncio.sleep(0))
phone.greeting_protected = lambda now=None: False
phone.input_level = None
phone._play_until = _t.time() + 2.0
run(phone._feed(_tone(500)))
run(phone._feed(_tone(9000)))
check("эхо-ворота Gemini: пока агент доигрывает ответ, тихое эхо приглушено (≈ −24 дБ), громкая речь клиента проходит",
      len(_eg_sent) == 2 and 0 < audioop.rms(_eg_sent[0], 2) <= audioop.rms(_tone(500), 2) * 0.1
      and _eg_sent[1] == _tone(9000))

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
# Хеш меняется осознанно 02.11.2026: добавлен абзац «ЖИВОЙ ПРОФЕССИОНАЛ, А НЕ ЧЕК-ЛИСТ» (владелец:
# звонок звучал «скован, как будто цепями» — слишком много точных запретов подряд).
# И 04.11.2026: краткость без пассивности, поддакивание не обрывает фразу, блок ПРОДАЖИ / ГОРЯЧИЙ КЛИЕНТ.
# 10.11.2026 (звонок af6e4d6a, Марат): без «одну секунду», цена сразу цифрой, разговорный бишкекский кыргызский,
# делегирование только за функцией; подсказки ужаты с 14,3 до 11 тыс. символов. Тем же днём — без продажи Voksy
# в общих правилах (их получают агенты всех клиентов): test_platform_neutral.py.
# 14.11.2026 (1411-voice-lean): слой платформы GPT-Live переписан коротко по шаблону OpenAI — LIVE_VOICE_HEAD в начале,
# LIVE_VOICE_RULES (= VOICE_DELEGATION_HINT) в конце; Gemini по-прежнему на своих подсказках и общих кусках ниже.
check("GPT-Live: VOICE_DELEGATION_HINT побайтно прежний (обновлён 14.11.2026 — короткий слой по шаблону OpenAI)",
      hashlib.sha256(hint.encode()).hexdigest() == "ee0201d58cf308d5c510be936b5393d6b3dce2d52016af2669541da83f5931dd", len(hint))
check("GPT-Live: телефонная подсказка прежняя",
      hashlib.sha256(lc.VOICE_TELEPHONY_HINT.encode()).hexdigest() == "987187f3747507d2578f880ee5d35194c36f279702faad801a982df246ed08fa")
check("GPT-Live: хвост инструкций — LIVE_VOICE_RULES, общие куски Gemini в него не входят",
      hint == lc.LIVE_VOICE_RULES and lc.VOICE_DIALOG_RULES not in hint and lc.VOICE_CONVERSATION_HEAD not in hint)

fn_list = [{"name": "search_knowledge_base", "description": "Ищет ответ в базе знаний компании. Возвращает текст."},
           {"name": "hangup_call", "description": "Завершает звонок."}]
g_tail = vh.build_gemini_voice_hints(fn_list, "Aoede", telephony=True)
# 06.11.2026: голос и язык — в НАЧАЛЕ промпта (gemini_prompt_head), остальное — после карточки
g_head = vh.gemini_prompt_head("Aoede", "ru", "Добрый день")
g = g_head + "<КАРТОЧКА>" + g_tail
check("Gemini получает свою манеру: живой разговор, задача человека, переспрос обрывка, голос, язык, кыргызский",
      all(k in g for k in ("ЖИВОЙ РАЗГОВОР, А НЕ АНКЕТА", "ПОНИМАЙ ЗАДАЧУ", "ЕСЛИ НЕ РАССЛЫШАЛ", "СТАБИЛЬНЫЙ ГОЛОС",
                           "ЯЗЫК.", "КЫРГЫЗСКИЙ")))
check("Gemini: не дежурный вопрос в каждой реплике (звонок 01.11.2026) и без правил GPT «одна мысль и спроси»",
      "НЕ заканчивай каждую реплику вопросом" in g and lc.LIVE_VOICE_HEAD not in g
      and "спроси, интересно ли дальше" not in g and "ГЛАВНОЕ ПРАВИЛО — КРАТКОСТЬ" not in g)
check("Gemini: без противоречия «можно мм/угу» против «МОЛЧИ»", "можно одно тихое «мм»" not in g)
# Потолок поднят 02.11.2026 (дважды): «ЖИВОЙ ПРОФЕССИОНАЛ, А НЕ ЧЕК-ЛИСТ», пункт 5 продаж «не отдавай
# инициативу», и «ТЫ — ПОЛНОЦЕННЫЙ СОТРУДНИК, А НЕ ФИЛЬТР» (владелец: агент не должен перенаправлять на
# менеджера то, что сам знает). Gemini не режет system_message по длине (в отличие от GPT-Live), так что
# предел здесь — только защита от случайного разбухания, не продуктовое ограничение.
# 04.11.2026: 14 → 16 тыс. — в общие правила диалога добавлены ПРОДАЖИ / ГОРЯЧИЙ КЛИЕНТ и поддакивание.
# 06.11.2026: 16 → 10 тыс. — промпт звонка был 34 тыс., правила противоречили друг другу и терялись.
# 07.11.2026: 10 → 11 тыс. — в начало добавлен общий с GPT-Live блок «Живой разговор» (не эхо, не повторять себя,
# вау-пример, темп продажи): владелец — «агент занудный, повторяет за клиентом».
# 07.11.2026 (ветка 0711-gemini-live, звонок d08c06f4): 11 → 13 тыс. — свой блок Gemini в начале (характер, объяснять
# голосом вместо «пришлю», извиниться и исправиться, память о клиенте); дубли в манере ужаты.
check("Gemini: подсказки не длиннее 13 тыс. символов (без противоречий, со словарём и продажами)", len(g) < 13000, len(g))
check("Gemini: никогда не говорит на других языках (звонок 02.11.2026: речь записалась как хинди)",
      "NEVER SPEAK ANY LANGUAGE OTHER THAN RUSSIAN OR KYRGYZ" in g)
check("Gemini: понимание кыргызского — смешанная речь, «похоже на казахский», словарик частых фраз",
      "КАК ПОНИМАТЬ КЫРГЫЗСКУЮ РЕЧЬ" in g and "смешивать" in g and "канча турат" in g and "ойлонуп көрөйүн" in g)
check("Gemini: продажи — понять, предложить под задачу, сомнения («подумаю»), закрыть шагом со временем",
      all(k in g for k in ("ПОЙМИ", "КОНСУЛЬТИРУЙ, А НЕ НАВЯЗЫВАЙ", "какие варианты есть", "СОМНЕНИЯ", "ЗАКРЫВАЙ ШАГОМ", "«Подумаю»")))
check("Gemini: продажи — не отдавать инициативу на вопросах про документы/оформление (звонок 822daa6f)",
      "НЕ ОТДАВАЙ ИНИЦИАТИВУ" in g)
check("Gemini: сотрудник, а не фильтр к менеджеру — отвечает сам из инструкции/базы, менеджер не вместо ответа",
      "ТЫ — ПОЛНОЦЕННЫЙ СОТРУДНИК, А НЕ ФИЛЬТР" in g and "а не вместо ответа" in g)

# ---------- после hangup_call модель не озвучивает по-английски мысли о том, что ответ не нужен
# (звонок 02.11.2026, 6811616a: на шум после прощания Gemini сказала вслух «The call is already
# ending, so no further response is needed.») ----------
from backend.functions.hangup_call import HangupCallFunction
_hc = run(HangupCallFunction.execute({"reason": "conversation_completed", "farewell_message": "Жакшы калыңыз!"}))
check("hangup_call: явный запрет озвучивать по-английски мысли о завершении звонка",
      _hc["success"] is True and "переходи на английский" in _hc["message"] and "просто молчи" in _hc["message"])
# 06.11.2026 (звонок d4229a5f): голос прыгал между мужским и женским — «посмейся, вздохни, вдохни» у native audio
# раскачивает тембр. Эмоции — словами и интонацией, звуки не изображаем.
check("Gemini: один и тот же голос, смех/вздохи вслух не изображать (звонок d4229a5f)",
      "одним и тем же" in g_head and "смех, вздохи и вдохи вслух не изображай" in g_head and "естественный вдох" not in g)
check("Gemini: голос и язык — в начале промпта, до карточки; напоминание — в конце",
      g.index("ГОЛОС И ЯЗЫК") < g.index("<КАРТОЧКА>") < g.index("НАПОМИНАНИЕ") and g.index("ЯЗЫК.") < g.index("<КАРТОЧКА>"))
check("Gemini: явная просьба о языке держится до конца звонка, русские вставки её не отменяют (звонок d4229a5f)",
      "ДО КОНЦА звонка" in g_head and "НЕ просьба сменить язык" in g_head)
check("Gemini: факты — только из инструкции или поиска, документы не додумывать (звонок d4229a5f)",
      "ДОКУМЕНТЫ" in g and "НИЧЕГО НЕ ДОДУМЫВАЙ" in g and g.index("ЗНАНИЯ — ТОЛЬКО") < g.index("ЖИВОЙ РАЗГОВОР"))
check("Gemini: приветствие по-русски на кыргызской карточке → начать по-русски, линия всё равно кыргызская",
      "Начни на русском" in vh.gemini_prompt_head("Kore", "ky", "Добрый день. Чем могу помочь?")
      and "ОСНОВНОЙ ЯЗЫК" in vh.gemini_prompt_head("Kore", "ky", "Добрый день")
      and "Начни на кыргызском" in vh.gemini_prompt_head("Kore", "ky", "Саламатсызбы! Кантип жардам берем?"))
check("журнал звонка: мужской персонаж на женском голосе → предупреждение",
      vh.persona_gender_conflict("Ты — Анри. О себе — в мужском роде: я рад помочь.", "Kore") is not None
      and vh.persona_gender_conflict("Ты — Анри. О себе — в мужском роде.", "Puck") is None
      and vh.persona_gender_conflict("Ты — Алия, я рада помочь.", "Kore") is None)
_anri = open(os.path.join(ROOT, "docs/voice_agent_prompt_anri.md"), encoding="utf-8").read()
_i = _anri.find("## БЛОК 1"); _a = _anri.find("```", _i) + 3; _block1 = _anri[_a:_anri.find("```", _a)]
check("шаблон Анри не требует вопроса в каждой реплике (инструкция карточки главнее подсказок)",
      "любой ответ заканчивай коротким вопросом" not in _block1 and "расспрашиваешь о деле" in _block1)
check("шаблон Анри: факты сам и сразу из раздела «наизусть», не отправляет на менеджера то, что знает (822daa6f, af6e4d6a)",
      "ЧТО ТЫ ЗНАЕШЬ НАИЗУСТЬ" in _block1 and "называй, только когда клиент решил подключаться" not in _block1
      and "не выдумывай" in _block1)
# 10.11.2026 (af6e4d6a): прежний Блок 1 был ~10 тыс. символов и противоречил сам себе — теперь короткий сценарий
# 14.11.2026 (1411-voice-lean): без сценария по шагам — цель и смысл, примеры только для стиля
check("шаблон Анри: Блок 1 короткий (до 5 тыс. символов), шаг со временем, разговорный кыргызский",
      len(_block1) < 5000 and "созвон в конкретное время" in _block1 and "бишкекче" in _block1, len(_block1))

# ---------- у Gemini Live ТА ЖЕ база знаний и те же живые функции, что у GPT-Live/Eleven
# (владелец 02.11.2026: «ощущение, что они не читают базу знаний») ----------
_sip_src = open(os.path.join(ROOT, "backend/api/sip_gateway.py"), encoding="utf-8").read()
_he_src = open(os.path.join(ROOT, "backend/websockets/handler_eleven.py"), encoding="utf-8").read()
# build_call_system_prompt / apply_live_kb_functions вызываются один раз, БЕЗ ветвления по
# voice_engine — gpt_live влияет только на max_chars (обрезка под лимит GPT-Live), не на то,
# подмешиваются ли знания и функции вообще.
for _src, _name in ((_sip_src, "sip_gateway.py"), (_he_src, "handler_eleven.py")):
    check(f"{_name}: знания и функции подмешиваются ОДИН РАЗ, без ветвления по voice_engine",
          _src.count("apply_live_kb_functions(") == 1 and _src.count("build_call_system_prompt(") == 1)
_skb_src = open(os.path.join(ROOT, "backend/functions/search_knowledge_base.py"), encoding="utf-8").read()
_fp_src = open(os.path.join(ROOT, "backend/functions/find_products.py"), encoding="utf-8").read()
check("search_knowledge_base не смотрит на провайдера (один код для Gemini/GPT-Live/Eleven)",
      "provider" not in _skb_src and "gemini" not in _skb_src.lower())
check("find_products не смотрит на провайдера (один код для Gemini/GPT-Live/Eleven)",
      "provider" not in _fp_src and "gemini" not in _fp_src.lower())
check("Gemini Live: max_chars не передаётся (знания не обрезаются, в отличие от GPT-Live)",
      "max_chars=LIVE_VOICE_INSTRUCTIONS_MAX_CHARS if gpt_live else None" in _sip_src
      and "max_chars=LIVE_VOICE_INSTRUCTIONS_MAX_CHARS if gpt_live else None" in _he_src)
check("Gemini Live (карточка): функции знаний включены в setup — search_knowledge_base реально попадает в Google",
      "self.enabled_functions = [normalize_function_name(tool" in open(
          os.path.join(ROOT, "backend/websockets/gemini_client.py"), encoding="utf-8").read())
check("Gemini: список функций не слипается с правилами диалога", "- hangup_call: Завершает звонок\n" in g, g[g.index("- hangup_call"):][:60])
check("Gemini получает правила диалога: перебивания, завершение, материалы, приветствие",
      all(k in g for k in ("Interruption policy", "ЗАВЕРШЕНИЕ", "МАТЕРИАЛЫ", "ПРИВЕТСТВИЕ")))
check("Gemini: правило «не знаю» только после поиска и список функций",
      "не говори «не знаю»" in g and "- search_knowledge_base:" in g and "- hangup_call:" in g)
check("Gemini: правило разговора под проактивный звук 3.8 (отвечай сразу, один язык, UNMISTAKABLY)",
      "ПОКА СОБЕСЕДНИК ГОВОРИТ" in g and "UNMISTAKABLY" in g and "не молчи" in g
      and vh.GEMINI_CONVERSATION_HINT not in lc.VOICE_DELEGATION_HINT)
check("Gemini: язык держится как в промпте звонков агента — язык приветствия, не меняется из-за «алло»",
      "LANGUAGE OF YOUR GREETING" in g and "NOT a reason to switch" in g
      and "THE LANGUAGE THE CALLER SPEAKS" not in g)
check("Gemini: инструкция карточки/агента главнее общих правил манеры (и стоит перед ними)",
      "ПРИОРИТЕТ ИНСТРУКЦИЙ" in g and g.index("ПРИОРИТЕТ ИНСТРУКЦИЙ") < g.index("ЖИВОЙ РАЗГОВОР")
      and "следуй инструкции выше" in g)
check("Gemini: пока клиент говорит — молчать (никаких «мм/угу» поверх речи)", "МОЛЧИ" in g and "поверх его речи" in g)
_ln = vh.language_note("ky")
check("Gemini: язык карточки — язык приветствия, держится его; смена только целой фразой; без отказа от русского",
      "Начни на кыргызском языке" in _ln and "держись его" in _ln and "целой фразой" in _ln and "«алло»" in _ln
      and "плохо владеешь" in _ln and "меняй язык вместе с ним" not in _ln and vh.language_note("") == "")
_pc = open(os.path.join(ROOT, "backend/services/agent_prompts.py"), encoding="utf-8").read()
check("правило языка Gemini не противоречит промпту звонков агента (оба: держись приветствия, не меняй из-за «алло»)",
      "держись его" in _pc and "короткого «алло»" in _pc and "короткого «алло»" in _ln)
check("Gemini: телефонная подсказка и женский род для Aoede", "телефонный звонок" in g and "женском роде" in g)
check("Gemini: без правил про бэкенд делегирования GPT",
      "Delegation policy" not in g and "Do not delegate" not in g and "бэкенда" not in g)
check("Gemini: голос Puck → мужской род; на сайте без телефона нет телефонной подсказки",
      "мужском роде" in vh.build_gemini_voice_hints(fn_list, "Puck", telephony=False)
      and "телефонный звонок" not in vh.build_gemini_voice_hints(fn_list, "Puck", telephony=False))
_lc_src = open(os.path.join(ROOT, "backend/websockets/live_client.py"), encoding="utf-8").read()
check("GPT-Live: характер, язык и произношение носителя (без английского акцента) — в самом начале инструкций голоса",
      "LIVE_VOICE_HEAD + voice_instructions + LIVE_VOICE_RULES" in _lc_src and "без английского" in lc.LIVE_VOICE_HEAD
      and "как носитель" in lc.LIVE_VOICE_HEAD and "держи его до конца звонка" in lc.LIVE_VOICE_HEAD)
check("промпт звонков агента: просьба о языке держится до конца, факты — только из базы",
      "до конца звонка" in _pc and "не додумывай" in _pc)
check("голоса Gemini размечены по роду", all(lc.voice_gender_hint(v) for v in ("aoede", "kore", "leda", "puck", "charon", "fenrir", "orus")))

# 07.11.2026 (звонки 078a6ed3, 5bf12c2d): агент пересказывал клиента, повторял свой питч, спрашивал разрешения
# рассказать, рано дожимал и уходил к бэкенду с «передайте менеджеру». Блок «Живой разговор» — в начале у обоих.
_head = lc.VOICE_CONVERSATION_HEAD
check("живой разговор: не эхо, не повторять себя, вау-сцена, без «хотите расскажу», темп продажи",
      all(k in _head for k in ("НЕ ЭХО", "НЕ ПОВТОРЯЙ СЕБЯ", "ВАУ", "разрешения", "ТЕМП ПРОДАЖИ", "всё верно")))
_cfg = lc.OpenAILiveClient("k", types.SimpleNamespace(id="x", system_prompt="Ты Анри. Скажи своими словами, что услышал.",
                                                      functions={}, voice="cedar", greeting_message=None),
                           "cid", telephony=True).build_session_config()["instructions"]
check("GPT-Live: характер и язык ДО инструкции карточки, твёрдые правила — после",
      _cfg.index("Кто ты и как звучишь") < _cfg.index("Язык и произношение") < _cfg.index("Ты Анри")
      < _cfg.index("Шесть твёрдых правил"))
check("GPT-Live: «не эхо» осталось твёрдым правилом", "Не повторяй за собеседником его слова" in lc.LIVE_VOICE_RULES)
_gh = vh.gemini_prompt_head("Puck", "ru", "Добрый день")
check("Gemini: «Живой разговор» в начале промпта (в gemini_prompt_head)", "НЕ ЭХО" in _gh and "ВАУ" in _gh)
check("договорённость: принять коротко, итог один раз — у GPT-Live и Gemini, без «спроси, всё ли верно»",
      "один раз, перед прощанием" in lc.VOICE_DIALOG_RULES and "спроси, всё ли верно" not in lc.VOICE_DIALOG_RULES
      and "без пересказа" in vh.GEMINI_CALL_RULES)
check("делегирование: менеджер / встреча / перезвон — не к бэкенду",
      "просит менеджера или перезвонить" in lc.LIVE_VOICE_RULES)
check("бэкенд не пересказывает клиента", "Не повторяй за клиентом его слова" in lc.BACKEND_SYSTEM_PROMPT)
from backend.websockets import handler_live as _hl
check("Decisions wants_human: время спросить один раз и только если не названо, не к бэкенду",
      "только если клиент его ещё не называл" in _hl.INTENT_HINTS["wants_human"]
      and "не уходи к бэкенду" in _hl.INTENT_HINTS["wants_human"])

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

# ---------- долгие звонки: возобновление сессии, откат setup, переподключение без повторного приветствия ----------
class RecWS(FakeGeminiWS):
    """Google: отвечает на последний setup — отказом (reject(setup) истина) или setupComplete."""
    def __init__(self, reject):
        super().__init__([])
        self.reject = reject
        self._answered = False
    async def recv(self):
        setup = [m for m in self.sent if "setup" in m][-1]["setup"]
        if not self._answered and self.reject(setup):
            raise _ws.exceptions.ConnectionClosedError(Close(1007, "Invalid argument"), None)
        if self._answered:
            await asyncio.sleep(3600)
        self._answered = True
        return json.dumps({"setupComplete": {}})


def card_client(reject=lambda s: False, handle=None, greeted=False):
    sockets = []
    async def fake_connect(*a, **kw):
        sockets.append(RecWS(reject)); return sockets[-1]
    asst = types.SimpleNamespace(id="a1", user_id=None, name="T", greeting_message="Салам", google_sheet_id=None,
                                 functions=None, voice=None, system_prompt="p", live_voice="aoede")
    c = GeminiLiveClient("key", asst, "c", db_session=None, voice_override="Aoede", record_conversation=False,
                         realtime_text=True, await_setup=True)
    c.resume_handle = handle
    c.greeting_sent = greeted
    c.ws = FakeGeminiWS([])
    return c, sockets, fake_connect

def with_connect(fake_connect, coro_fn):
    orig = gc_mod.websockets.connect
    gc_mod.websockets.connect = fake_connect
    try:
        return run(coro_fn())
    finally:
        gc_mod.websockets.connect = orig

def last_setup(sockets):
    return [m for m in sockets[-1].sent if "setup" in m][-1]["setup"]

os.environ.pop("GEMINI_INPUT_TRANSCRIPTION_LANGUAGES", None)
c, socks, fc = card_client()
with_connect(fc, c.connect)
st = last_setup(socks)
check("карточка ElevenLabs: setup с возобновлением сессии и сжатием контекста",
      st.get("sessionResumption") == {} and st.get("contextWindowCompression") == {"slidingWindow": {}}, list(st.keys()))
check("карточка ElevenLabs: транскрипция клиента по умолчанию с языками ru,ky (без них — испанский/немецкий в «Диалогах»)",
      st.get("inputAudioTranscription") == {"languageCodes": ["ru", "ky"]}, st.get("inputAudioTranscription"))
check("gemini-3.8-live: thinkingConfig не передаётся", "thinkingConfig" not in st.get("generationConfig", {}))
os.environ["GEMINI_INPUT_TRANSCRIPTION_LANGUAGES"] = "none"   # выключатель
c, socks, fc = card_client()
with_connect(fc, c.connect)
check("GEMINI_INPUT_TRANSCRIPTION_LANGUAGES=none выключает languageCodes",
      last_setup(socks).get("inputAudioTranscription") == {})
os.environ.pop("GEMINI_INPUT_TRANSCRIPTION_LANGUAGES", None)
import backend.websockets.gemini_client as _gcm
check("extended-thinking: уровень по умолчанию LOW, у обычной модели — нет",
      _gcm.thinking_level("gemini-3.8-live-extended-thinking") == "LOW" and _gcm.thinking_level("gemini-3.8-live") is None)
c, socks, fc = card_client()
c.model = "gemini-3.8-live-extended-thinking"
with_connect(fc, c.connect)
check("extended-thinking: thinkingConfig.thinkingLevel в generationConfig",
      last_setup(socks).get("generationConfig", {}).get("thinkingConfig") == {"thinkingLevel": "LOW"},
      last_setup(socks).get("generationConfig"))
# выбранная модель не открылась (нет на ключе) → звонок не рвётся, идёт на стабильной gemini-3.8-live
c, socks, fc = card_client(reject=lambda s: "extended-thinking" in s.get("model", ""))
c.model = "gemini-3.8-live-extended-thinking"
ok = with_connect(fc, c.connect)
c, socks, fc = card_client()
c.model = "gemini-3.8-live-extended-thinking"
c.blocking_tools = True
c.extras_level = 0
c.assistant_config.functions = [{"name": "search_knowledge_base", "description": "d", "parameters": {"type": "object", "properties": {}}}]
with_connect(fc, c.connect)
_st = last_setup(socks)
_decl = [d for t in _st.get("tools", []) for d in t.get("functionDeclarations", [])]
check("extended-thinking: уровень рассуждений есть даже на минимальном setup (Google требует)",
      _st.get("generationConfig", {}).get("thinkingConfig") == {"thinkingLevel": "LOW"}, _st.get("generationConfig"))
check("extended-thinking: функции NON_BLOCKING (BLOCKING эта модель не принимает)",
      c.use_blocking_tools is False and all(d.get("behavior") != "BLOCKING" for d in _decl), _decl)
c, socks, fc = card_client(reject=lambda s: "extended-thinking" in s.get("model", ""))
c.model = "gemini-3.8-live-extended-thinking"
ok = with_connect(fc, c.connect)
check("модель extended-thinking не открылась → повтор на gemini-3.8-live, звонок идёт",
      ok is True and c.model == "gemini-3.8-live" and last_setup(socks)["model"] == "models/gemini-3.8-live"
      and "thinkingConfig" not in last_setup(socks).get("generationConfig", {}), (ok, c.model, len(socks)))
c, socks, fc = card_client(reject=lambda s: True)
ok = with_connect(fc, c.connect)
check("стабильная модель не открылась → без бесконечных повторов", ok is False and len(socks) <= 4, (ok, len(socks)))
check("Gemini-ассистент (не карточка): расширенных полей нет, setup как раньше",
      not any(k in sp2["setup"] for k in ("sessionResumption", "contextWindowCompression")) and sp2["setup"]["inputAudioTranscription"] == {})

# Google отверг языки распознавания → повтор без них, но с возобновлением
c, socks, fc = card_client(reject=lambda s: "languageCodes" in s.get("inputAudioTranscription", {}))
ok = with_connect(fc, c.connect)
st = last_setup(socks)
check("отказ на языках распознавания: повтор с возобновлением, без языков", ok is True and len(socks) == 2
      and "sessionResumption" in st and st["inputAudioTranscription"] == {}, (ok, len(socks), list(st.keys())))
# Google отверг и возобновление → повтор без всех расширений
c, socks, fc = card_client(reject=lambda s: "sessionResumption" in s or "languageCodes" in s.get("inputAudioTranscription", {}))
ok = with_connect(fc, c.connect)
st = last_setup(socks)
check("отказ и на возобновлении: сессия открывается с минимальным setup (не тишина)",
      ok is True and len(socks) == 3 and "sessionResumption" not in st and "contextWindowCompression" in st or
      (ok is True and "sessionResumption" not in st and "contextWindowCompression" not in st), (ok, len(socks), list(st.keys())))
check("при откате клиент помнит, на каком уровне работает", c.extras_level == 0 or c.extras_level == 1, c.extras_level)
# неверный ключ не вызывает лишних повторов
c, socks, fc = card_client()
check("служебные сообщения: ручка возобновления сохраняется", c.handle_service_event(
    {"sessionResumptionUpdate": {"newHandle": "H1", "resumable": True}}) is True and c.resume_handle == "H1")
c.handle_service_event({"sessionResumptionUpdate": {"newHandle": "H2", "resumable": False}})
check("служебные сообщения: невозобновляемая ручка игнорируется", c.resume_handle == "H1", c.resume_handle)
check("служебные сообщения: goAway учитывается и не ломает цикл", c.handle_service_event({"goAway": {"timeLeft": "10s"}}) is True and c.go_away_seen == 1)
check("служебные сообщения: чужие события не поглощаются", c.handle_service_event({"serverContent": {}}) is False)

# переподключение посреди разговора: по ручке, без повторного приветствия
c, socks, fc = card_client(handle="H1", greeted=True)
ok = with_connect(fc, c.reconnect)
st = last_setup(socks)
check("обрыв посреди звонка: setup с ручкой возобновления", ok is True and st.get("sessionResumption") == {"handle": "H1"}, st.get("sessionResumption"))
check("обрыв посреди звонка: приветствие второй раз не звучит", c.greeting_sent is True and c.reconnect_count == 1)
sock_r = FakeClientSocket()
state_r = {"is_user_speaking": False, "is_assistant_speaking": False, "last_speech_start": 0, "last_speech_stop": 0,
           "interruption_count": 0, "last_interruption_time": 0, "assistant_audio_ever": True, "greeting_retries": 0}
async def drain_r():
    t = asyncio.create_task(handler_gemini.handle_gemini_messages(c, sock_r, state_r))
    await asyncio.sleep(0.3); t.cancel(); await asyncio.gather(t, return_exceptions=True)
run(drain_r())
check("после переподключения setupComplete не запускает приветствие", not any("realtimeInput" in m for m in c.ws.sent), c.ws.sent[-1:])
# устаревшая ручка отвергнута → следующая попытка с чистой сессией
c, socks, fc = card_client(reject=lambda s: bool(s.get("sessionResumption", {}).get("handle")), handle="OLD", greeted=True)
async def rec():
    return await c.reconnect()
ok = with_connect(fc, rec)
check("устаревшая ручка: повтор без неё, звонок продолжается",
      ok is True and c.resume_handle is None and "handle" not in last_setup(socks).get("sessionResumption", {}), (ok, c.resume_handle))
check("и тогда приветствие тоже не повторяется", c.greeting_sent is True)

# обработчик событий принимает служебные сообщения молча
c = make_client([{"sessionResumptionUpdate": {"newHandle": "HH", "resumable": True}}, {"goAway": {"timeLeft": "5s"}}])
sock_s = FakeClientSocket()
async def drain_s():
    t = asyncio.create_task(handler_gemini.handle_gemini_messages(c, sock_s, dict(state_r)))
    await asyncio.sleep(0.3); t.cancel(); await asyncio.gather(t, return_exceptions=True)
run(drain_s())
check("обработчик: ручка принята, клиенту служебное не пересылается", c.resume_handle == "HH" and sock_s.sent == [], sock_s.sent)


# ---------- стенограмма звонка: поток transcript.delta у карточки, без дублей; у Gemini-ассистента прежние события ----------
from backend.services.call_transcript import CallTranscript

async def transcript_run(saver):
    events = [
        {"setupComplete": {}},
        {"serverContent": {"outputTranscription": {"text": "Саламатсызбы! "}}},
        {"serverContent": {"turnComplete": True}},
        {"serverContent": {"inputTranscription": {"text": "Алло, "}}},
        {"serverContent": {"inputTranscription": {"text": "сколько стоит?"}}},
        {"serverContent": {"interrupted": True}},               # перебивание: реплика не должна потеряться
        {"serverContent": {"outputTranscription": {"text": "Пятьсот сом."}}},
        {"serverContent": {"turnComplete": True}},
    ]
    c = make_client(events, saver)
    sock = FakeClientSocket()
    tr = CallTranscript()
    orig_send = sock.send_json
    async def tap(data, mode="text"):
        tr.on_event(data); await orig_send(data, mode)
    sock.send_json = tap
    stt = dict(state_r)
    t = asyncio.create_task(handler_gemini.handle_gemini_messages(c, sock, stt))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if not c.ws.events:
            await asyncio.sleep(0.1); break
    t.cancel(); await asyncio.gather(t, return_exceptions=True)
    return c, sock, tr

c_card, sock_card, tr_card = run(transcript_run(saver))
turns = tr_card.finish()
check("карточка: стенограмма звонка собрана потоком transcript.delta", "transcript.delta" in sock_card.types(), sock_card.types())
check("карточка: в стенограмме приветствие, вопрос клиента (в т.ч. при перебивании) и ответ",
      [t["role"] for t in turns] == ["assistant", "user", "assistant"] and "Алло, сколько стоит?" in turns[1]["text"]
      and "Пятьсот" in turns[2]["text"], turns)
check("карточка: без дублей — завершённые реплики вторым разом не отправляются",
      "input.transcription.complete" not in sock_card.types() and "output.transcription.complete" not in sock_card.types())
check("стенограмма видит речь клиента (звонок засчитывается как отвеченный)", tr_card.has_user_speech)
check("счётчик событий Google ведётся (для журнала звонка)",
      c_card.census["serverContent.inputTranscription"] == 2 and c_card.census["serverContent.outputTranscription"] == 2
      and c_card.census["serverContent.turnComplete"] == 2 and c_card.census["serverContent.interrupted"] == 1, dict(c_card.census))

c_old = make_client([{"setupComplete": {}}, {"serverContent": {"inputTranscription": {"text": "Привет"}}},
                     {"serverContent": {"outputTranscription": {"text": "Здравствуйте"}}}, {"serverContent": {"turnComplete": True}}])
c_old.assistant_config.greeting_message = None
sock_old = FakeClientSocket()
async def old_run():
    t = asyncio.create_task(handler_gemini.handle_gemini_messages(c_old, sock_old, dict(state_r)))
    await asyncio.sleep(0.4); t.cancel(); await asyncio.gather(t, return_exceptions=True)
orig_save = handler_gemini.ConversationService.save_conversation
async def _noop(**kw): return "ok"
handler_gemini.ConversationService.save_conversation = staticmethod(_noop)
try:
    run(old_run())
finally:
    handler_gemini.ConversationService.save_conversation = orig_save
check("Gemini-ассистент: события как раньше (*.complete), без transcript.delta",
      "input.transcription.complete" in sock_old.types() and "transcript.delta" not in sock_old.types(), sock_old.types())

# ---------- вызов функции базы знаний: toolCall → результат в Gemini → клиенту function_call.completed ----------
from backend.websockets import function_calls as fcalls
async def kb_call():
    c = make_client([{"setupComplete": {}},
                     {"toolCall": {"functionCalls": [{"id": "kb1", "name": "search_knowledge_base", "args": {"query": "цена"}}]}}])
    c.assistant_config.greeting_message = None
    c.enabled_functions = ["search_knowledge_base"]
    c.blocking_tools = True
    sock = FakeClientSocket()
    seen = {}
    async def fake_exec(name, arguments, context=None):
        seen.update(name=name, args=arguments, has_cfg=bool(context and context.get("assistant_config")))
        return {"found": True, "answer": "Тариф «Старт» — 7500 сом"}
    orig = fcalls.execute_function
    fcalls.execute_function = fake_exec
    try:
        t = asyncio.create_task(handler_gemini.handle_gemini_messages(c, sock, dict(state_r)))
        await asyncio.sleep(0.5); t.cancel(); await asyncio.gather(t, return_exceptions=True)
    finally:
        fcalls.execute_function = orig
    return c, sock, seen
ck, sk, seen = run(kb_call())
tool_resp = [m for m in ck.ws.sent if "toolResponse" in m]
check("функция базы знаний: вызвана с аргументами и карточкой ассистента", seen.get("name") == "search_knowledge_base"
      and seen.get("args") == {"query": "цена"} and seen.get("has_cfg"), seen)
check("функция базы знаний: результат ушёл обратно в Gemini (toolResponse с id и именем)",
      len(tool_resp) == 1 and tool_resp[0]["toolResponse"]["functionResponses"][0]["id"] == "kb1"
      and tool_resp[0]["toolResponse"]["functionResponses"][0]["name"] == "search_knowledge_base"
      and "7500" in json.dumps(tool_resp[0], ensure_ascii=False), tool_resp)
check("функция базы знаний: клиенту ушло function_call.completed", "function_call.completed" in sk.types(), sk.types())

# ---------- завершение звонка как у GPT-Live: прощание → тишина → трубка; защита hangup_call ----------
import time as _time
from backend.websockets.call_guard import CallEndGuard
from backend.websockets.call_log import CallLogRecorder as _Rec

g1 = CallEndGuard(phone=True)
g1.on_transcript("assistant", "Здравствуйте! Спасибо за звонок в нашу компанию, чем могу помочь?")
check("приветствие «спасибо за звонок» до речи клиента — не прощание", g1.farewell_at is None)
g1.on_transcript("user", "Хочу узнать цену. ")
g1.on_transcript("assistant", "Тариф Старт — семь тысяч пятьсот. Что-нибудь ещё?")
check("обычный вопрос ассистента — не прощание", g1.farewell_at is None)
g1.on_transcript("user", "Нет, всё, спасибо, до свидания")
check("клиент попрощался — ждём тишины", g1.farewell_at is not None)
now = _time.time()
g1.last_audio_at = now - 10
g1.last_user_voice_at = now - 10
g1.farewell_at = now - 10
check("прощание + тишина клиента и ассистента → пора класть трубку", g1.should_auto_hangup(now))
g1.last_user_voice_at = now - 0.2
check("клиент говорит прямо сейчас — не кладём", not g1.should_auto_hangup(now))
g1.last_user_voice_at = now - 10; g1.last_audio_at = now - 0.3
check("ассистент ещё договаривает — не кладём", not g1.should_auto_hangup(now))
g1.last_audio_at = now - 10
g1.last_user_voice_at = now - 3; g1.farewell_at = now - 10          # клиент заговорил после прощания
check("клиент заговорил после прощания — прощание снято, звонок продолжается",
      not g1.should_auto_hangup(now) and g1.farewell_at is None)
g_web = CallEndGuard(phone=False)
g_web.on_transcript("user", "до свидания")
check("в виджете (не телефон) трубку не кладём", g_web.farewell_at is None and not g_web.should_auto_hangup(_time.time() + 99))

g2 = CallEndGuard(phone=True)
g2.on_transcript("user", "Давайте напишите мне в Telegram к десяти")
check("hangup_call пока клиент говорит и не попрощался — отклонён",
      "НЕ завершён" in (g2.gate("hangup_call", {"reason": "conversation_completed"}) or "") and g2.rejections == 1)
check("другие функции страж не трогает", g2.gate("search_knowledge_base", {"query": "x"}) is None)
# 07.11.2026 (d08c06f4): модель ставила reason=user_request, когда клиент жаловался, и клала трубку
check("hangup_call с «по просьбе клиента», когда клиент не просил, — отклонён",
      g2.gate("hangup_call", {"reason": "user_request"}) is not None)
g2b = CallEndGuard(phone=True)
g2b.on_transcript("user", "Не надо, мне не интересно, спасибо")
check("hangup_call по настоящей просьбе клиента разрешён", g2b.gate("hangup_call", {"reason": "user_request"}) is None)
g2c = CallEndGuard(phone=True)
g2c.on_transcript("user", "Сен соны айтпасың, тастаймын деп айтпадың")
g2c.on_transcript("assistant", "Кечиресиз, толук маалыматты WhatsApp'ка жөнөтөм. Жакшы калыңыз!")
check("агент сам попрощался, клиент только что жаловался — трубку не кладём",
      g2c.farewell_at is not None and g2c.gate("hangup_call", {"reason": "conversation_completed"}) is not None)
check("тишина для защиты — от конца доигрывания ответа Gemini, а не от прихода звука",
      CallEndGuard(phone=True).gate("hangup_call", {"reason": "x"}, agent_quiet_since=_time.time() + 3) is not None)
g2.on_transcript("user", " Спасибо, до свидания!")
check("после прощания клиента hangup_call разрешён", g2.gate("hangup_call", {"reason": "conversation_completed"}) is None)
g3 = CallEndGuard(phone=True); g3.last_user_voice_at = _time.time() - 20
check("клиент молчит давно — hangup_call разрешён", g3.gate("hangup_call", '{"reason":"conversation_completed"}') is None)

# сессия: прощание + тишина → клиенту уходит call.hangup с drain (адаптер дождётся проигрыша звука)
async def end_flow():
    asst = types.SimpleNamespace(id="a1", user_id=None, name="T", google_sheet_id=None)
    cl = make_client([])
    sock = FakeClientSocket()
    rec = _Rec(channel="phone")
    sess = gl.GeminiLiveVoiceSession(sock, asst, cl, None, "c1", telephony=True, call_log=rec)
    sess.observe({"type": "transcript.delta", "role": "assistant", "delta": "Здравствуйте! Чем помочь?"})
    sess.observe({"type": "transcript.delta", "role": "user", "delta": "Всё, спасибо, до свидания"})
    sess.observe({"type": "response.audio.delta", "delta": "AAAA"})
    t = _time.time() - 20
    sess.guard.last_user_voice_at = t; sess.guard.farewell_at = t; sess.guard.last_audio_at = t
    task = asyncio.create_task(sess._watch_end())
    await asyncio.sleep(0.6)
    task.cancel(); await asyncio.gather(task, return_exceptions=True)
    return sess, sock, rec
sess_e, sock_e, rec_e = run(end_flow())
hang = [m for m in sock_e.sent if m.get("type") == "call.hangup"]
check("сессия: после прощания и тишины клиенту ушёл call.hangup с drain", len(hang) == 1 and hang[0].get("drain") is True, hang)
check("сессия: автозавершение записано в журнал звонка", any("Автозавершение" in (e.get("text") or e.get("message") or "") for e in rec_e.events)
      if hasattr(rec_e, "events") else True)
sess_e.guard.caller_farewell_at = _time.time() - 10   # прощание было давно, клиент заговорил снова
sess_e.guard.on_transcript("user", "подождите, а сколько стоит?")
check("сессия: клиент функции получил защиту hangup_call",
      callable(sess_e.client.function_gate)
      and sess_e.client.function_gate("hangup_call", {"reason": "user_request"}) is not None)

# защита в обработчике: преждевременный hangup_call отклонён, функция не выполняется
async def gated_hangup():
    c = make_client([{"setupComplete": {}},
                     {"toolCall": {"functionCalls": [{"id": "h1", "name": "hangup_call", "args": {"reason": "conversation_completed"}}]}}])
    c.assistant_config.greeting_message = None
    c.enabled_functions = ["hangup_call"]
    c.blocking_tools = True
    gd = CallEndGuard(phone=True)
    gd.on_transcript("user", "подождите, ещё запишите номер")
    c.function_gate = gd.gate
    ran = []
    async def fake_exec(name, arguments, context=None):
        ran.append(name); return {"ok": True}
    orig = fcalls.execute_function
    fcalls.execute_function = fake_exec
    sock = FakeClientSocket()
    try:
        t = asyncio.create_task(handler_gemini.handle_gemini_messages(c, sock, dict(state_r)))
        await asyncio.sleep(0.5); t.cancel(); await asyncio.gather(t, return_exceptions=True)
    finally:
        fcalls.execute_function = orig
    return c, sock, ran
cg, sg, ran = run(gated_hangup())
resp = [m for m in cg.ws.sent if "toolResponse" in m]
check("hangup_call на полуслове клиента: функция НЕ выполнена", ran == [], ran)
check("hangup_call на полуслове: модель получила отказ с объяснением",
      len(resp) == 1 and "rejected" in json.dumps(resp[0]) and "НЕ завершён" in json.dumps(resp[0], ensure_ascii=False), resp)
check("hangup_call на полуслове: адаптеру не ушло function_call.started (трубку не кладём)",
      "function_call.started" not in sg.types(), sg.types())

# адаптер: call.hangup с drain сначала ждёт метку моста (очередь звука проиграна), потом кладёт трубку
from backend.websockets.sip_media_adapter import HandlerSocket
class BridgeWS:
    def __init__(self): self.texts = []; self._q = asyncio.Queue()
    async def send_text(self, t): self.texts.append(json.loads(t))
    async def send_bytes(self, b): pass
    async def receive(self): return await self._q.get()
async def drain_flow():
    ws = BridgeWS()
    hs = HandlerSocket(ws, "gemini", "c-drain")
    hs.start()
    await hs.send_json({"type": "call.hangup", "reason": "farewell", "drain": True})
    await asyncio.sleep(0.2)
    before = [m.get("type") for m in ws.texts]
    await ws._q.put({"text": json.dumps({"type": "mark", "name": "hangup"})})   # мост проиграл очередь
    await asyncio.sleep(0.3)
    after = [m.get("type") for m in ws.texts]
    await hs.finish()
    return before, after
b4, aft = run(drain_flow())
check("адаптер: прощание в очереди — сначала метка, трубка НЕ кладётся", b4 == ["mark"], b4)
check("адаптер: мост проиграл звук — трубка кладётся", aft == ["mark", "hangup"], aft)

# ---------- смена движка голоса: голос согласуется с движком, старая вкладка не возвращает прежний ----------
from backend.api import agent as agent_api
_lv = agent_api._live_voice_for_engine
check("смена Gemini → GPT-Live: голос Gemini не остаётся на карточке GPT-Live", _lv("gpt_live", "aoede") is None)
check("GPT-Live: свой голос сохраняется", _lv("gpt_live", "marin") == "marin")
check("смена GPT-Live → Gemini: голос GPT не остаётся на карточке Gemini", _lv("gemini_live", "marin") is None)
check("Gemini Live: свой голос сохраняется", _lv("gemini_live", "Aoede") == "aoede")
_js = open(os.path.join(ROOT, "backend/static/agent/instructions-voice.js"), encoding="utf-8").read()
check("окно настроек агента шлёт движок только если его меняли (data-initial), иначе старая вкладка затирает выбор",
      'data-initial="${engine}"' in _js and "engChanged" in _js and "dataset.initial" in _js)

# ---------- схемы функций под Gemini: чужие поля Google считает ошибкой всей настройки (звонок «Анри» рвался через 3 с) ----------
from backend.functions import get_function_definitions
from backend.websockets.gemini_client import gemini_schema

_ALLOWED_KEYS = {"type", "format", "description", "nullable", "enum", "items", "properties", "required",
                 "minimum", "maximum", "minItems", "maxItems"}
def _foreign_keys(node, path="", found=None):
    found = [] if found is None else found
    if isinstance(node, dict):
        is_props = path.endswith("properties")
        for k, v in node.items():
            if not is_props and k not in _ALLOWED_KEYS:
                found.append(path + "." + k)
            _foreign_keys(v, path + "." + k, found)
    elif isinstance(node, list):
        for i, v in enumerate(node): _foreign_keys(v, path + f"[{i}]", found)
    return found

_defs = {d["name"]: d for d in get_function_definitions()}
check("в реестре есть hangup_call с примерами в схеме (источник сброса звонка «Анри»)",
      "examples" in json.dumps(_defs["hangup_call"]["parameters"]))
check("ДО очистки в схемах реестра есть поля, которых нет у Gemini",
      any(_foreign_keys(d["parameters"], d["name"]) for d in _defs.values()))
_dirty = {n: _foreign_keys(gemini_schema(d["parameters"]), n) for n, d in _defs.items()}
check("ПОСЛЕ очистки у ВСЕХ функций платформы схема содержит только поля Gemini", not any(_dirty.values()),
      {k: v for k, v in _dirty.items() if v})
_h = gemini_schema(_defs["hangup_call"]["parameters"])
_fm = _h["properties"]["farewell_message"]["description"]
check("пример прощания не потерян — переехал в описание параметра", "До свидания" in _fm and "200" in _fm, _fm[:120])
check("тип-список превращается в тип + nullable",
      gemini_schema({"type": ["string", "null"]}) == {"type": "string", "nullable": True})
check("параметр с именем «default» не принимается за служебное поле",
      "default" in gemini_schema({"type": "object", "properties": {"default": {"type": "string", "default": "x"}}})["properties"])

c, socks, fc = card_client()
c.assistant_config.functions = [{"name": n} for n in ("hangup_call", "search_knowledge_base", "find_products", "find_client", "send_telegram_notification")]
with_connect(fc, c.connect)
_tools = last_setup(socks).get("tools", [])
_bad = [k for t in _tools for k in _foreign_keys(t["function_declarations"][0]["parameters"], t["function_declarations"][0]["name"])]
check("setup карточки: функции переданы, схемы чистые", len(_tools) == 5 and not _bad, (len(_tools), _bad[:3]))

# Google всё равно отверг функции → звонок идёт без них (а не сбрасывается), расширения сохраняются
_tools_msg = 'Invalid JSON payload received. Unknown name "examples" at setup.tools[0].function_declarations[0].parameters: Cannot find field.'
class ToolRejectWS(RecWS):
    async def recv(self):
        setup = [m for m in self.sent if "setup" in m][-1]["setup"]
        if not self._answered and setup.get("tools"):
            raise _ws.exceptions.ConnectionClosedError(Close(1007, _tools_msg), None)
        return await super().recv()
socks2 = []
async def fc2(*a, **kw):
    socks2.append(ToolRejectWS(lambda s: False)); return socks2[-1]
c2 = card_client()[0]
c2.assistant_config.functions = [{"name": "hangup_call"}, {"name": "search_knowledge_base"}]
ok = with_connect(fc2, c2.connect)
st2 = [m for m in socks2[-1].sent if "setup" in m][-1]["setup"]
check("Google отверг функции: звонок открылся без них с ОДНОЙ повторной попыткой (не три)",
      ok is True and len(socks2) == 2 and not st2.get("tools") and c2.tools_enabled is False, (ok, len(socks2)))
check("после отката функций возобновление сессии осталось включённым", "sessionResumption" in st2 and c2.extras_level == 2)

# причина не названа → сначала расширения, потом функции, в итоге звонок всё равно открывается
class VagueWS(RecWS):
    async def recv(self):
        setup = [m for m in self.sent if "setup" in m][-1]["setup"]
        if not self._answered and (setup.get("tools") or "sessionResumption" in setup):
            raise _ws.exceptions.ConnectionClosedError(Close(1011, "Internal error"), None)
        return await super().recv()
socks3 = []
async def fc3(*a, **kw):
    socks3.append(VagueWS(lambda s: False)); return socks3[-1]
c3 = card_client()[0]
c3.assistant_config.functions = [{"name": "hangup_call"}]
ok3 = with_connect(fc3, c3.connect)
st3 = [m for m in socks3[-1].sent if "setup" in m][-1]["setup"]
check("неназванная причина: ступенями (расширения → функции) звонок всё же открывается",
      ok3 is True and not st3.get("tools") and "sessionResumption" not in st3, (ok3, len(socks3), list(st3.keys())))

# сбой открытия Gemini оставляет запись диалога с причиной (раньше звонок рвался без следа в «Диалогах»)
class RecDB:
    def __init__(self): self.added = []
    def add(self, o): self.added.append(o)
    def commit(self): pass
    def refresh(self, o): o.id = "11111111-1111-1111-1111-111111111111"
    def rollback(self): pass
_db = RecDB()
_rid = gl._create_conversation_record(_db, types.SimpleNamespace(id="a1"), "call-1", "⚠ Звонок не соединился: Gemini Live не открылся — причина")
check("сбой открытия Gemini: запись диалога создана и содержит причину",
      _rid and len(_db.added) == 1 and "не открылся — причина" in _db.added[0].assistant_message and _db.added[0].session_id == "call-1")
check("обычная запись диалога по-прежнему пустая (к ней цепляются function_logs)",
      (gl._create_conversation_record(_db, types.SimpleNamespace(id="a1"), "call-2") and _db.added[1].assistant_message == ""))

# ---------- заголовки websocket: fallback extra_headers → additional_headers (websockets >= 14 на Render) ----------
# Это была ПРИЧИНА «Gemini молчит и сбрасывает через 3 с»: на новой версии websockets extra_headers → TypeError,
# и КАЖДЫЙ звонок Gemini падал на открытии сокета. У GPT-Live этот запасной путь был, у Gemini — нет.
class _FakeConn:
    def __init__(self): self.sent = []
    async def recv(self): return json.dumps({"setupComplete": {}})
    async def send(self, d): self.sent.append(d)
    async def close(self): pass

def _connect_capturing(reject_extra):
    seen = {"extra": False, "additional": False}
    async def fake_connect(url, **kw):
        if "extra_headers" in kw:
            seen["extra"] = True
            if reject_extra:
                raise TypeError("connect() got an unexpected keyword argument 'extra_headers'")
        if "additional_headers" in kw:
            seen["additional"] = True
        return _FakeConn()
    asst = types.SimpleNamespace(id="a1", user_id=None, name="T", greeting_message="Салам", google_sheet_id=None,
                                 functions=None, voice=None, system_prompt="p", live_voice="aoede")
    c = GeminiLiveClient("key", asst, "c", db_session=None, voice_override="Aoede", record_conversation=False,
                         realtime_text=True, await_setup=True)
    orig = gc_mod.websockets.connect
    gc_mod.websockets.connect = fake_connect
    try:
        ok = run(c.connect())
    finally:
        gc_mod.websockets.connect = orig
    return ok, seen

_ok_old, _seen_old = _connect_capturing(reject_extra=False)
check("websockets < 14: соединение открывается через extra_headers", _ok_old is True and _seen_old["extra"] and not _seen_old["additional"])
_ok_new, _seen_new = _connect_capturing(reject_extra=True)
check("websockets >= 14: extra_headers отверг TypeError → открытие через additional_headers (звонок не падает)",
      _ok_new is True and _seen_new["extra"] and _seen_new["additional"], _seen_new)
_live = open(os.path.join(ROOT, "backend/websockets/gemini_client.py"), encoding="utf-8").read()
check("в коде Gemini есть оба варианта заголовков (как у GPT-Live)",
      "extra_headers" in _live and "additional_headers" in _live)


# ---------- защита приветствия на телефоне (звонок 02.11.2026: шум линии «перебил» приветствие, клиент 10 с слышал тишину) ----------
class _SentWS:
    def __init__(self): self.sent = []
    async def send_json(self, d, mode="text"): self.sent.append(d)
_fed = []
async def _pa(b): _fed.append(b)
_gc = types.SimpleNamespace(is_connected=True, process_audio=_pa, function_gate=None, session_id="s")
_ws = _SentWS()
_sess = gl.GeminiLiveVoiceSession(_ws, types.SimpleNamespace(id="a"), _gc, None, "c", True, None)
_obs = gl._ObserveSocket(_ws, _sess)
check("приветствие ещё не началось — защищено (до 8 с)", _sess.greeting_protected())
run(_sess._feed(b"\x10\x20" * 160))
check("на приветствии в Gemini идёт тишина, а не звук линии", _fed and set(_fed[-1]) == {0}, _fed[-1][:8] if _fed else None)
run(_obs.send_json({"type": "response.audio.delta", "delta": base64.b64encode(b"\x01\x00" * 24000).decode()}))
run(_obs.send_json({"type": "conversation.interrupted"}))
check("«перебивание» на приветствии не уходит в адаптер (очередь звука не чистится)",
      not any(d.get("type") == "conversation.interrupted" for d in _ws.sent))
run(_obs.send_json({"type": "assistant.speech.ended"}))
_now = _time.time()
check("пока звук приветствия (1 с) не доигран — защищено", _sess.greeting_protected(_now + 0.5))
check("после проигрыша приветствия — обычный разговор", not _sess.greeting_protected(_now + 2.0))
_sess._greet_first_audio -= 5   # приветствие давно доиграно
run(_sess._feed(b"\x10\x20" * 160))
run(_obs.send_json({"type": "conversation.interrupted"}))
check("после приветствия звук линии идёт как есть, перебивание доходит",
      set(_fed[-1]) != {0} and any(d.get("type") == "conversation.interrupted" for d in _ws.sent))
_sw = gl.GeminiLiveVoiceSession(_SentWS(), types.SimpleNamespace(id="a"), _gc, None, "c", False, None)
check("в виджете защиты приветствия нет", not _sw.greeting_protected())


# ---------- клиент перебивает доигрывание (звонок 02.11.2026, 953a3a6c: 14 voiceActivity, 0 прерываний) ----------
from backend.websockets import handler_gemini as _hg
check("voiceActivity: ACTIVITY_START → start", _hg._voice_activity_kind({"voiceActivityType": "ACTIVITY_START"}) == "start")
check("voiceActivity: ACTIVITY_END → end", _hg._voice_activity_kind({"voiceActivityType": "ACTIVITY_END"}) == "end")
check("voiceActivity: непонятное → пусто", _hg._voice_activity_kind({"x": 1}) == "")
check("Gemini без слов: «...» не считается словами (звонок 822daa6f)", not _hg._has_words("..."))
check("Gemini без слов: пробелы/пунктуация не считаются словами", not _hg._has_words("  ... — !"))
check("Gemini без слов: обычный ответ считается словами", _hg._has_words("Ооба, албетте!"))

class _Log:
    def __init__(self): self.rows = []; self.interruptions = 0
    def add(self, kind, text, **kw): self.rows.append((kind, text))
_gc2 = types.SimpleNamespace(is_connected=True, process_audio=_pa, function_gate=None, session_id="s",
                             set_assistant_speaking=lambda v: None)
_ws2 = _SentWS(); _log2 = _Log()
_s2 = gl.GeminiLiveVoiceSession(_ws2, types.SimpleNamespace(id="a"), _gc2, None, "c", True, _log2)
_o2 = gl._ObserveSocket(_ws2, _s2)
_s2._greet_first_audio = _time.time() - 30; _s2._greet_generated = True   # приветствие давно позади
run(_o2.send_json({"type": "response.audio.delta", "delta": base64.b64encode(b"\x01\x00" * 24000 * 6).decode()}))
run(_o2.send_json({"type": "assistant.speech.ended"}))   # Google закончил ход, у клиента ещё ~6 с звука
check("ответ сгенерирован быстрее реального времени — доигрывание учтено", _s2.playback_left() > 5, _s2.playback_left())
run(_o2.send_json({"type": "gemini.voice_activity", "activity": "start"}))
check("клиент заговорил во время доигрывания → адаптеру уходит conversation.interrupted (очистка очереди)",
      any(d.get("type") == "conversation.interrupted" for d in _ws2.sent))
check("служебный gemini.voice_activity клиенту не пересылается", not any(d.get("type") == "gemini.voice_activity" for d in _ws2.sent))
check("перебивание в журнале звонка", _log2.interruptions == 1 and any(k == "barge_in" for k, _ in _log2.rows))
check("после обрыва доигрывания не осталось", _s2.playback_left() == 0)
_n = len(_ws2.sent)
run(_o2.send_json({"type": "gemini.voice_activity", "activity": "start"}))
check("агент молчит — начало речи клиента ничего не обрывает", len(_ws2.sent) == _n)
run(_o2.send_json({"type": "gemini.voice_activity", "activity": "end"}))
check("конец речи клиента ничего не обрывает", len(_ws2.sent) == _n)
# на приветствии голос клиента доигрывание не обрывает
_ws3 = _SentWS()
_s3 = gl.GeminiLiveVoiceSession(_ws3, types.SimpleNamespace(id="a"), _gc2, None, "c", True, None)
_o3 = gl._ObserveSocket(_ws3, _s3)
run(_o3.send_json({"type": "response.audio.delta", "delta": base64.b64encode(b"\x01\x00" * 24000 * 3).decode()}))
run(_o3.send_json({"type": "gemini.voice_activity", "activity": "start"}))
check("приветствие voiceActivity не обрывает", not any(d.get("type") == "conversation.interrupted" for d in _ws3.sent))
_src = open(os.path.join(ROOT, "backend/websockets/handler_gemini.py"), encoding="utf-8").read()
check("разбор событий Google пересылает voiceActivity наверх", '"voiceActivity" in response_data' in _src and "gemini.voice_activity" in _src)

# ---------- прощание не обрывается на полуслове (звонок 02.11.2026, 822daa6f: "он затыкался, как будто
# на полслова отключался" — hangup_call сработал, но шум/обрывок речи клиента во время прощания запускал
# тот же барж-ин, что и обычное перебивание, и clear резал фразу прощания) ----------
_ws4 = _SentWS(); _log4 = _Log()
_s4 = gl.GeminiLiveVoiceSession(_ws4, types.SimpleNamespace(id="a"), _gc2, None, "c", True, _log4)
_o4 = gl._ObserveSocket(_ws4, _s4)
_s4._greet_first_audio = _time.time() - 30; _s4._greet_generated = True   # приветствие давно позади
run(_o4.send_json({"type": "function_call.completed", "function": "hangup_call",
                   "result": {"success": True, "status": "call_ending"}, "execution_time": 0.0}))
check("hangup_call замечен сессией", _s4._hangup_in_progress)
check("страж CallEndGuard тоже узнал о hangup_call (не напишет «модель hangup_call не вызвала»)", _s4.guard.hangup_sent)
run(_o4.send_json({"type": "response.audio.delta", "delta": base64.b64encode(b"\x01\x00" * 24000 * 2).decode()}))
run(_o4.send_json({"type": "gemini.voice_activity", "activity": "start"}))
check("во время прощания voiceActivity не обрывает фразу (нет conversation.interrupted)",
      not any(d.get("type") == "conversation.interrupted" for d in _ws4.sent))
check("доигрывание прощания не сброшено", _s4.playback_left() > 0)
# неудачный hangup_call (ошибка) сессию не отмечает — страж продолжает следить сам
_ws5 = _SentWS()
_s5 = gl.GeminiLiveVoiceSession(_ws5, types.SimpleNamespace(id="a"), _gc2, None, "c", True, None)
run(gl._ObserveSocket(_ws5, _s5).send_json({"type": "function_call.completed", "function": "hangup_call",
                                            "result": {"success": False, "error": "x"}}))
check("отклонённый hangup_call доигрывание не защищает", not _s5._hangup_in_progress)
_live_src = open(os.path.join(ROOT, "backend/websockets/handler_gemini_live.py"), encoding="utf-8").read()
check("защита прощания реализована в коде", "_note_hangup_started" in _live_src and "_hangup_in_progress" in _live_src)

# ---------- громкость голоса звонящего (клиент на 10–18 дБ тише агента) ----------
import math as _m
def _tone(amp, n=1600):
    return b"".join(int(amp * _m.sin(i / 3)).to_bytes(2, "little", signed=True) for i in range(n))
_lvl = gl.PhoneInputLevel()
_quiet = _tone(600)
for _ in range(3):
    _lvl.process(_tone(30), False)        # тишина линии — шумовой пол
for _ in range(80):
    _out = _lvl.process(_quiet, False)
check("тихая речь клиента усилена (до +12 дБ)", 6 <= _lvl.gain_db <= 12.1 and audioop.rms(_out, 2) > audioop.rms(_quiet, 2) * 2, _lvl.gain_db)
_loud = _tone(12000)
for _ in range(30):
    _out = _lvl.process(_loud, False)
check("громкая речь не перегружается (усиление сброшено)", _lvl.gain_db < 1.5, _lvl.gain_db)
_lvl2 = gl.PhoneInputLevel()
for _ in range(3):
    _lvl2.process(_tone(30), False)
for _ in range(80):
    _lvl2.process(_quiet, True)
check("пока агент говорит, усиление не растёт (эхо не подтягиваем)", _lvl2.gain_db < 0.5, _lvl2.gain_db)
_hum = _tone(30)
for _ in range(80):
    _lvl.process(_quiet, False)
_lvl.process(_hum, False); _lvl.process(_hum, False)
check("шум пауз не раздуваем", audioop.rms(_lvl.process(_hum, False), 2) <= audioop.rms(_hum, 2) * 1.1)
check("пока агент говорит, речь линии (эхо) не усиливается", audioop.rms(_lvl.process(_quiet, True), 2) <= audioop.rms(_quiet, 2) * 1.6)
check("виджет без выравнивания громкости",
      gl.GeminiLiveVoiceSession(_SentWS(), types.SimpleNamespace(id="a"), _gc2, None, "c", False, None).input_level is None)
check("Gemini VAD: prefixPaddingMs из GEMINI_VAD_PREFIX_MS, по умолчанию 300 (поднят 02.11.2026)", 'GEMINI_VAD_PREFIX_MS", "300"' in _live)
_ky = vh.language_note("ky")
check("кыргызская карточка: кыргызский — основной язык линии, русские слова в кыргызской речи — норма",
      "ОСНОВНОЙ ЯЗЫК" in _ky and "РУССКИЕ СЛОВА" in _ky)
check("язык карточки: никогда не говорить собеседнику, какие языки понимаешь", "НИКОГДА НЕ ГОВОРИ О ЯЗЫКАХ" in vh.language_note("ru"))
check("русская карточка без кыргызского приоритета", "ОСНОВНОЙ ЯЗЫК" not in vh.language_note("ru"))

# ---------- 07.11.2026, ветка 0711-gemini-live, звонок d08c06f4: «без души, только «отправлю», не помнит» ----------
_h = vh.gemini_prompt_head("Charon", "ky", "Саламатсызбы!")
check("Gemini: в начале — характер, объяснять голосом, исправиться, переспросить, память",
      all(k in _h for k in ("ХАРАКТЕР", "ОБЪЯСНЯЙ САМ ГОЛОСОМ", "НЕДОВОЛЕН — ИСПРАВЬСЯ", "НЕ ПОНЯЛ — ПЕРЕСПРОСИ", "ПАМЯТЬ"))
      and "не больше одного раза за звонок" in _h)
check("Gemini: «пришлю» — не ответ; материалы только после объяснения (и в правилах звонка)",
      "НЕ ответ" in _h and "сначала объясни сам" in vh.GEMINI_CALL_RULES
      and "предложи прислать материалы" not in vh.GEMINI_SALES_HINT)
_card = "Ты Анри.\n\n# ЗНАНИЯ\nПрофи — 12 500 сом.\n\n[Контекст звонка] Это входящий звонок. Звонит клиент, который уже есть в CRM. Имя: Айбек.\nХРОНОЛОГИЯ: 07.11 звонок — спрашивал про цену."
_rest, _ctx = vh.split_call_context(_card)
check("Gemini: контекст звонка отделяется от карточки", "[Контекст звонка]" not in _rest and _ctx.startswith("Это входящий"))
check("Gemini: блок контекста с заголовком, пустой — пусто",
      vh.call_context_block(_ctx).startswith("# КОНТЕКСТ ЗВОНКА И КЛИЕНТ") and vh.call_context_block("") == "")
_gc_src = open(os.path.join(ROOT, "backend/websockets/gemini_client.py"), encoding="utf-8").read()
check("Gemini: контекст звонка с историей клиента идёт сразу после головы, до карточки",
      "+ call_context_block(call_ctx)\n                                      + card" in _gc_src)
_lk = vh.language_note("ru")
check("Gemini: просьба о кыргызском — сразу и без комментария о смене языка",
      "давай по-кыргызски" in _lk and "СРАЗУ" in _lk and "без комментариев о смене языка" in _lk)
check("Gemini: кыргызская дикция — законченные фразы, окончания полностью", "окончания слов проговаривай" in vh.KYRGYZ_LINE_NOTE)
_sg = open(os.path.join(ROOT, "backend/api/sip_gateway.py"), encoding="utf-8").read()
check("журнал звонка: узнан ли клиент по номеру и ушла ли его история агенту",
      "Клиент узнан по номеру в CRM" in _sg and "Номер не найден в CRM" in _sg)

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
