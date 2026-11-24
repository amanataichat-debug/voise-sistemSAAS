"""GPT-Live-1.1 (20.11.2026): модель по умолчанию и запасная gpt-live-1.

Changelog OpenAI: gpt-live-1.1 ставится в ту же конфигурацию session.start, что и
gpt-live-1. Если ключ новую модель не открыл (нет доступа, модель не найдена), старт
один раз повторяется на gpt-live-1 — звонок важнее. Отказ бэкенд-модели делегирования
голосовую модель не меняет. Без сети и БД.
"""
import asyncio
import json
import os
import sys
import re
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("LIVE_MODEL", None)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")

import backend.websockets.live_client as lc

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


check("модель по умолчанию — gpt-live-1.1", lc.LIVE_MODEL == "gpt-live-1.1", lc.LIVE_MODEL)
check("запасная модель — gpt-live-1", lc.STABLE_LIVE_MODEL == "gpt-live-1")

ACCESS = {"type": "invalid_request_error", "code": "model_not_found",
          "message": "The model `gpt-live-1.1` does not exist or you do not have access to it.",
          "param": "session.model"}
DELEG = {"type": "invalid_request_error", "code": "model_not_found",
         "message": "The model `gpt-9-luna` does not exist.", "param": "session.delegation.responses.model"}
check("is_model_error: нет доступа к gpt-live-1.1", lc.is_model_error(ACCESS))
check("is_model_error: param session.model без кода", lc.is_model_error({"param": "session.model", "message": "x"}))
check("is_model_error: отказ модели делегирования — не голосовая", not lc.is_model_error(DELEG))
check("is_model_error: прочие ошибки не трогает",
      not lc.is_model_error({"code": "invalid_value", "message": "Invalid voice", "param": "session.audio.output.voice"}))


def make_client():
    return lc.OpenAILiveClient("k", types.SimpleNamespace(id="x", system_prompt="p", functions={}, voice="cedar",
                                                          greeting_message=None), "cid", telephony=True)


check("session.start уходит с моделью клиента", make_client().build_session_config()["model"] == "gpt-live-1.1")


class FakeWS:
    def __init__(self, reject):
        self.reject = reject
        self.sent = []
        self._replies = []

    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg.get("type") == "session.start":
            model = msg["session"]["model"]
            err = self.reject.get(model)
            self._replies.append({"type": "error", "error": err} if err
                                 else {"type": "session.started", "session": {"id": "s1", "model": model}})

    async def recv(self):
        while not self._replies:
            await asyncio.sleep(0.01)
        return json.dumps(self._replies.pop(0))

    async def close(self, *a, **k):
        pass


async def run(reject):
    sockets = []

    async def fake_connect(*a, **k):
        ws = FakeWS(reject)
        sockets.append(ws)
        return ws

    orig = lc.websockets.connect
    lc.websockets.connect = fake_connect
    try:
        c = make_client()
        c._create_conversation_record = lambda: None
        ok = await c.connect(start_timeout=2)
        models = [m["session"]["model"] for ws in sockets for m in ws.sent if m.get("type") == "session.start"]
        return ok, c, models
    finally:
        lc.websockets.connect = orig


ok, c, models = asyncio.run(run({}))
check("ключ открыл 1.1 — один старт на gpt-live-1.1", ok and models == ["gpt-live-1.1"] and c.model == "gpt-live-1.1"
      and not c.model_fallback_reason, models)

ok, c, models = asyncio.run(run({"gpt-live-1.1": ACCESS}))
check("нет доступа к 1.1 — повтор на gpt-live-1, причина сохранена",
      ok and models == ["gpt-live-1.1", "gpt-live-1"] and c.model == "gpt-live-1"
      and "does not exist" in c.model_fallback_reason, models)

ok, c, models = asyncio.run(run({"gpt-live-1.1": ACCESS, "gpt-live-1": ACCESS}))
check("обе модели не открылись — без бесконечных повторов", not ok and models == ["gpt-live-1.1", "gpt-live-1"], models)

ok, c, models = asyncio.run(run({"gpt-live-1.1": DELEG}))
check("отказ модели делегирования не переключает голосовую модель (повтор — на запасном бэкенде)",
      models == ["gpt-live-1.1", "gpt-live-1.1"] and c.model == "gpt-live-1.1"
      and c.delegation_model == lc.STABLE_DELEGATION_MODEL, models)

# ── Бэкенд делегирования: gpt-6-luna, отказ → gpt-5.6-luna (21.11.2026) ─────────────────────────────────
check("бэкенд по умолчанию — gpt-6-luna, запасной — gpt-5.6-luna",
      lc.LIVE_DELEGATION_MODEL == "gpt-6-luna" and lc.STABLE_DELEGATION_MODEL == "gpt-5.6-luna")
DELEG6 = {"code": "invalid_value", "message": "service_tier 'priority' is not supported for gpt-6-luna",
          "param": "session.delegation.responses.service_tier"}


class FakeWS2(FakeWS):
    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg.get("type") == "session.start":
            dm = msg["session"]["delegation"]["responses"]["model"]
            self._replies.append({"type": "error", "error": DELEG6} if dm == "gpt-6-luna"
                                 else {"type": "session.started", "session": {"id": "s2"}})


async def run_deleg():
    sockets = []

    async def fake_connect(*a, **k):
        ws = FakeWS2({})
        sockets.append(ws)
        return ws
    orig = lc.websockets.connect
    lc.websockets.connect = fake_connect
    try:
        c = make_client()
        c._create_conversation_record = lambda: None
        ok = await c.connect(start_timeout=2)
        models = [m["session"]["delegation"]["responses"]["model"] for ws in sockets for m in ws.sent
                  if m.get("type") == "session.start"]
        return ok, c, models
    finally:
        lc.websockets.connect = orig

ok, c, models = asyncio.run(run_deleg())
check("настройку gpt-6-luna не приняли — повтор на gpt-5.6-luna, голосовая модель прежняя",
      ok and models == ["gpt-6-luna", "gpt-5.6-luna"] and c.model == "gpt-live-1.1" and c.delegation_fallback_reason,
      models)


async def mid_call():
    c = make_client()
    sent = []

    async def fake_send(payload):
        sent.append(payload)
        return True
    c._send = fake_send
    env = {"delegation_id": "d1", "event": {"type": "response.failed", "response": {"error": {
        "code": "model_not_found", "message": "The model gpt-6-luna does not exist or you do not have access to it."}}}}
    async for _ in c._handle_backend_event(env):
        pass
    async for _ in c._handle_backend_event(env):
        pass
    return c, sent

c, sent = asyncio.run(mid_call())
check("отказ gpt-6-luna посреди звонка — один session.update на gpt-5.6-luna",
      c.delegation_model == "gpt-5.6-luna" and len(sent) == 1
      and sent[0]["session"]["delegation"]["responses"]["model"] == "gpt-5.6-luna", sent)
check("отказ голосовой модели не считается отказом бэкенда",
      not lc.is_delegation_error(ACCESS, "gpt-6-luna"))

# ── Свои голоса prompt-to-voice ─────────────────────────────────────────────────────────────────────────
import backend.services.live_voices as lv
lv.set_cache([{"key": "aigul", "voice_id": "voice_abc", "title": "Айгуль — Бишкек", "gender": "f",
               "description": "An original adult feminine voice from Bishkek."}])
a = types.SimpleNamespace(id="x", system_prompt="Ты Алия.", functions={}, voice="aigul", greeting_message=None)
cl = lc.OpenAILiveClient("k", a, "cid", telephony=True)
cfg = cl.build_session_config()
check("свой голос: в session.start объект {id}", cfg["audio"]["output"]["voice"] == {"id": "voice_abc"},
      cfg["audio"]["output"]["voice"])
check("свой голос: описание голоса в instructions (совет OpenAI), после слоя платформы и до карточки",
      "# Твой голос (только звучание; как вести разговор — в инструкциях ниже)\nAn original adult feminine voice "
      "from Bishkek." in cfg["instructions"]
      and cfg["instructions"].startswith(lc.LIVE_VOICE_HEAD)
      and cfg["instructions"].index("# Твой голос") < cfg["instructions"].index("Ты Алия."))
check("свой голос: род из реестра (f → женский)", "женском роде" in cfg["instructions"])
check("встроенный голос: строка, без описания голоса",
      lc.OpenAILiveClient("k", types.SimpleNamespace(id="x", system_prompt="p", functions={}, voice="cedar",
                                                    greeting_message=None), "cid").build_session_config()
      ["audio"]["output"]["voice"] == "cedar")
check("реестр: свой голос в списке и проходит проверку выбора, чужой — нет",
      "aigul" in lv.all_live_voices() and lv.is_live_voice("AIGUL") and not lv.is_live_voice("nope")
      and lv.labels() == {"aigul": "Айгуль — Бишкек"})
lv.set_cache([])
check("голос выключили — карточка звонит голосом по умолчанию",
      lc.OpenAILiveClient("k", a, "cid").build_session_config()["audio"]["output"]["voice"] == lc.LIVE_DEFAULT_VOICE)

# ── Запасной голос того же рода (24.11.2026: свой мужской «Азим» не открылся → звучал женский marin) ─────
lv.set_cache([{"key": "azim", "voice_id": "voice_m", "title": "Азим", "gender": "m", "description": "d"},
              {"key": "aliya", "voice_id": "voice_f", "title": "Алия", "gender": "f", "description": "d"}])
def _fb(voice):
    c = lc.OpenAILiveClient("k", types.SimpleNamespace(id="x", system_prompt="p", functions={}, voice=voice,
                                                       greeting_message=None), "cid")
    c.build_session_config()
    return c._fallback_voice()
check("запасной голос: свой мужской → cedar, свой женский → голос по умолчанию",
      _fb("azim") == "cedar" and _fb("aliya") == lc.LIVE_DEFAULT_VOICE, [_fb("azim"), _fb("aliya")])
check("запасной голос: встроенный мужской (ash) → cedar, женский (marin) → по умолчанию",
      _fb("ash") == "cedar" and _fb("marin") == lc.LIVE_DEFAULT_VOICE)
_src = open(os.path.join(ROOT, "backend/websockets/live_client.py"), encoding="utf-8").read()
check("отказ голоса: повтор голосом того же рода и строка «не открылся на ключе» в журнале",
      "fallback = self._fallback_voice()" in _src and "не открылся на ключе" in _src)
lv.set_cache([])

# ── Свои голоса v2: харизма, смех, оба языка (scripts/create_live_voice.py) ──────────────────────────
sys.argv = ["x"]
import scripts.create_live_voice as clv
check("готовые голоса: azim, azim2, aliya, aliya2", set(clv.PRESETS) == {"azim", "azim2", "aliya", "aliya2"})
for _k, _p in clv.PRESETS.items():
    _ok = (len(_p["prompt"]) <= 2000 and 300 <= len(_p["script"]) <= 2000
           and ("masculine" in _p["prompt"]) == (_p["gender"] == "m")
           and "laugh" in _p["prompt"] and "Bishkek" in _p["prompt"] and "smile" in _p["prompt"]
           and "caricature" in _p["prompt"] and re.search("[ңөү]", _p["script"]) and "здравствуйте" in _p["script"])
    check(f"голос {_k}: описание ≤2000, род, смех, улыбка, Бишкек без пародии, образец ru+ky", bool(_ok))
check("описания своих голосов — только звучание, без черт характера (OpenAI: sound и behavior отдельно)",
      not any(w in p["prompt"] for p in clv.PRESETS.values() for w in ("humour", "curiosity", "quick wit", "loves")))
check("образец речи в роде голоса (рад / рада)",
      "очень рад," in clv.PRESETS["azim"]["script"] and "очень рада," in clv.PRESETS["aliya"]["script"])
import scripts.voice_samples as vsm
check("прослушивание: сцены чтения ru/ky и живые (шутка, сочувствие, радость)",
      {"read_ru", "read_ky", "joke_ru", "joke_ky", "pain_ky", "news_ky"} <= set(vsm.SCENES))
_pp = vsm.phone_path(b"\x10\x00" * 24000)
check("прослушивание: телефонная версия — 8 кГц через ту же обработку, что на звонке", len(_pp) == 16000, len(_pp))

# ── Слой платформы: язык по гайду OpenAI для 1.1 ───────────────────────────────────────────────────────
head = lc.LIVE_VOICE_HEAD
check("язык: последняя ясная фраза, короткое слово/число/имя язык не меняют",
      "последней ясной фразы" in head and "число" in head and "держи его до конца звонка" in head)
check("кыргызский: бишкекский даже при другом говоре собеседника", "южным или другим говором" in head)
# 22.11.2026 (2211-live-kyrgyz): пары «так, а не так» убраны — модель повторяла их дословно (OpenAI: описывайте поведение,
# слова пусть выбирает модель); кыргызское правило написано по-кыргызски и описывает бишкекский аралаш
check("без пар-образцов: слова модель подбирает сама, кыргызское правило — по-кыргызски, аралаш с русскими словами",
      "«ооба, болот», а не «албетте»" not in head and "ни одну фразу из этих инструкций не произноси дословно" in head
      and "Бишкекте кадимки адамдар" in head and "орусча сөздөр кадимкидей кошулат" in head)
check("смена языка — тот же разговор, без переспросов", "Смена языка — тот же разговор" in head
      and "не переспрашивай" in head)
check("суммы словами на языке разговора с валютой", "«пять тысяч пятьсот сомов» / «беш миң беш жүз сом»" in head)
check("делегирование: строка шаблона gpt-live-1.1",
      "Delegate to the backend before giving an answer that depends on backend work." in lc.LIVE_VOICE_RULES)
check("промпт бэкенда: бишкекский кыргызский, без «без диалекта»",
      "разговорный бишкекский" in lc.BACKEND_SYSTEM_PROMPT and "диалекта" not in lc.BACKEND_SYSTEM_PROMPT)
from backend.services import agent_prompts as ap
check("оркестратор: клиенту на его языке, не «всегда по-русски»",
      "Всегда отвечай на русском языке" not in ap.ORCHESTRATOR_PROMPT_BASE
      and "Клиенту (переписка, звонок) — на его языке" in ap.ORCHESTRATOR_PROMPT_BASE)
check("промпт звонка: перешёл целой фразой — отвечай на нём",
      "Перешёл человек целой фразой" in ap.VOICE_AGENT_PROMPT_BASE)

# ── 22.11.2026, звонок a72651fc: «кабельский» вместо «кыргызский», ответ про связь ───────────────────────
check("просьба о кыргызском узнаётся и в искажённом виде («кабельский»), это про язык, а не про связь",
      "«кабельский»" in lc.LIVE_VOICE_HEAD and "а не о связи" in lc.LIVE_VOICE_HEAD)
ky = types.SimpleNamespace(id="x", system_prompt="Ты Анри.", functions={}, voice="cedar", greeting_message=None,
                           language="ky")
ru = types.SimpleNamespace(id="x", system_prompt="Ты Анри.", functions={}, voice="cedar", greeting_message=None,
                           language="ru")
cfg_ky = lc.OpenAILiveClient("k", ky, "cid", telephony=True).build_session_config()["instructions"]
cfg_ru = lc.OpenAILiveClient("k", ru, "cid", telephony=True).build_session_config()["instructions"]
check("карточка ky: заметка «кыргызская линия» до карточки, русским — по-русски",
      lc.KYRGYZ_LINE_NOTE in cfg_ky and cfg_ky.index("# Линия") < cfg_ky.index("Ты Анри.")
      and "отвечай по-русски" in lc.KYRGYZ_LINE_NOTE)
check("карточка ru и старые ассистенты без языка — без заметки",
      "# Линия" not in cfg_ru and lc.line_language_note(types.SimpleNamespace()) == "")

print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
sys.exit(1 if failures else 0)
