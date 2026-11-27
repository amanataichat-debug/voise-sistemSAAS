"""
Агент слушает и ведёт к цели — звонок f9b4a269 (25.11.2026, демо «сыграй суши-кафе»). Без сети и БД.

Что было: клиент говорил поверх агента ~45 с, «азыр токто» («стой») — агент договаривал; Decisions принял
«азыр токтотосу» за прощание; четыре ответа длиннее 15 с; «на три порции» — «давайте на одну»; заказ не оформлен.
Проверяем: остановку агента кодом (barge_in.py + handler_live), просьбу замолчать ≠ прощание, короткие ответы после
монолога и жалобы, новый слой промпта, раздел «ЦЕЛЬ ЗВОНКА», голоса Алия / Алия 2 / Азим, сценку Анри.
"""
import asyncio
import audioop
import base64
import logging
import os
import re
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.websockets.barge_in as bi
import backend.websockets.handler_live as h
import backend.websockets.live_client as lc
from backend.services import agent_voice_context as avc
from backend.websockets.call_log import CallLogRecorder

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def pcm(rms: int, ms: int = 20, rate: int = 8000) -> bytes:
    """Прямоугольный сигнал заданной громкости (RMS PCM16)."""
    n = rate * ms // 1000
    out = bytearray()
    for i in range(n):
        out += int(rms if (i // 4) % 2 else -rms).to_bytes(2, "little", signed=True)
    return bytes(out)


def b64(x: bytes) -> str:
    return base64.b64encode(x).decode()


# ── 1. Детектор перебивания ──────────────────────────────────────────────────────────────────────────
d = bi.BargeInDetector()
hits = [d.feed(3000, 0.02, True, 600, 3000) for _ in range(15)]   # 0,3 с — «ага», «ооба»
check("поддакивание 0,3 с поверх агента — не перебивание", not any(hits))
d = bi.BargeInDetector()
hits = [d.feed(3000, 0.02, True, 600, 3000) for _ in range(40)]   # 0,8 с речи
check("речь клиента 0,65+ с поверх звучащего агента — перебивание, один раз на ответ",
      hits.count(True) == 1 and 0.64 <= (hits.index(True) + 1) * 0.02 <= 0.7, hits.index(True) if True in hits else None)
check("после перебивания детектор ждёт новый ответ агента (rearm)",
      not d.feed(3000, 0.4, True, 600, 3000) and (d.rearm() or True) and d.armed)
d = bi.BargeInDetector()
check("эхо агента тише порога (0,45 обычной громкости клиента) — не перебивание",
      not any(d.feed(1000, 0.02, True, 600, 3000) for _ in range(80)))
d = bi.BargeInDetector()
check("агент молчит — это обычная речь клиента, не перебивание",
      not any(d.feed(3000, 0.02, False, 600, 3000) for _ in range(80)))
d = bi.BargeInDetector()
seq = []
for _ in range(4):
    seq += [d.feed(3000, 0.02, True, 600, 3000) for _ in range(12)]   # 0,24 с речи
    seq += [d.feed(100, 0.02, True, 600, 3000) for _ in range(15)]    # 0,3 с паузы — счёт сброшен
check("короткие слова с паузами больше 0,25 с — не копятся в перебивание", not any(seq))
d = bi.BargeInDetector()
hits = [d.feed(3000, 0.02, True, 600, 0) for _ in range(60)]
check("клиент ещё не говорил отдельно (приветствие) — нужно дольше: 0,9 с",
      True in hits and 0.88 <= (hits.index(True) + 1) * 0.02 <= 0.94)
d = bi.BargeInDetector()
check("приветствие на громкой связи: эхо агента ~−38 dBFS, голос клиента ещё не измерен — не перебивание",
      not any(d.feed(600, 0.02, True, 300, 0) for _ in range(100)))
from backend.websockets.echo_gate import LineEchoGate
g = LineEchoGate(enabled=False)
for _ in range(30):
    g.observe(pcm(3000))
check("эхо-ворота выключены — уровень речи клиента для остановки агента всё равно меряется", g.speech_rms > 2000)
d = bi.BargeInDetector()
d.expect_words(100.0)
check("слова клиента пришли — не ложное", (d.words_heard() or True) and not d.check_false(200.0) and d.false_count == 0)
d.expect_words(100.0)
f1 = d.check_false(103.5)
d.expect_words(110.0)
f2 = d.check_false(113.5)
check("два срабатывания без слов клиента (эхо/шум) — механизм выключается до конца звонка",
      f1 and f2 and d.false_count == 2 and not d.enabled and d.disabled_reason)

# ── 2. Просьба замолчать ≠ прощание ──────────────────────────────────────────────────────────────────
RX = h._STOP_TALK_RE
for text in ("Азыр токтотосу", "азыр токто", "стоп, стоп", "подождите секунду", "дайте сказать", "можно я скажу",
             "сен көп сүйлөйсүң", "күтө туруңуз", "токтоңузчу", "токтогула"):
    check(f"«{text}» — просьба замолчать", bool(RX.search(text)))
for text in ("мен токтом кабыл алдым", "хватит одной порции", "какая погода", "три порции", "подход хороший",
             "сеттерге акция барбы"):
    check(f"«{text}» — не просьба замолчать", not RX.search(text))
q0 = lc.CALL_DECISION_TRIGGER["decisions"]["questions"][0]
check("Decisions: есть stop_talking, «просьба замолчать — не прощание»",
      {"value": "stop_talking"} in q0["choices"] and "This is NOT goodbye" in q0["instructions"]
      and "A request to stop talking or to wait is not goodbye" in q0["instructions"])
check("подсказки перебивания и краткости — на английском, без готовых фраз, на языке разговора",
      all(not re.search(r"[А-Яа-яЁё]", x) and h._HINT_LANG in x for x in (h.BARGE_HINT, h.BREVITY_HINT)))


# ── 3. Обработчик звонка: настоящий LiveVoiceSession ─────────────────────────────────────────────────
class FC:
    session_id = "s"
    io_rate = 8000
    enabled_functions = []
    delegation_model = "gpt-6-luna"

    def __init__(self):
        self.thinking, self.instructions = [], []

    async def append_thinking(self, text, delegation_id=None):
        self.thinking.append(text)
        return True

    async def append_instructions(self, text, delegation_id=None):
        self.instructions.append(text)
        return True

    def decode_output_b64(self, x):
        return x


def session(phone=True):
    cl = CallLogRecorder(channel="phone" if phone else "widget")
    s = h.LiveVoiceSession(websocket=None, assistant=types.SimpleNamespace(), client=FC(), db=None,
                           client_id="c", telephony=phone, call_log=cl)
    s.sent = []

    async def em(data):
        s.sent.append(data)
    s.emit = em
    return s, cl


def journal(cl):
    return " | ".join(e.get("text", "") for e in cl.events)


def decision(intent="none", mood="neutral"):
    return {"event": {"type": "decision.completed", "decision": {"answers": [
        {"name": "caller_intent", "choice": intent}, {"name": "caller_mood", "choice": mood},
        {"name": "caller_cue", "choice": "none"}]}}}


async def say(s, role, text):
    await s._on_live_event({"type": f"session.{'input' if role == 'user' else 'output'}_transcript.delta",
                            "delta": text, "start_ms": 0, "end_ms": 1})


VOICE, QUIET = b64(pcm(3000)), b64(pcm(20))


async def caller_talks(s, seconds, rms=3000):
    """Звук клиента идёт тем же путём, что в handle_client_messages: замер, ворота, перебивание."""
    for _ in range(int(seconds / 0.02)):
        audio = b64(pcm(rms))
        s._track_input_level(audio)
        s._gate_input(audio, 0.02)
        if s._barge_pending:
            src, s._barge_pending = s._barge_pending, ""
            await s._hard_barge_in(src)


async def scenario():
    # клиент сначала говорит один (громкость его речи меряют ворота), потом агент отвечает долго
    s, cl = session()
    for _ in range(50):
        s.echo_gate.process(pcm(3000), False, 0.02)
    await s._on_audio_delta(VOICE)
    await say(s, "assistant", "Бизде запечённая Филадельфия бар, үстүндө сыр, ичинде лосось менен крем-чиз. ")
    check("агент говорит — звук уходит в линию", any(e["type"] == "response.audio.delta" for e in s.sent))
    await caller_talks(s, 0.3)
    check("клиент поддакнул 0,3 с — агент продолжает", not s._barge_drop and s.barge_total == 0)
    await caller_talks(s, 0.8)
    check("клиент заговорил поверх агента — звук агента остановлен кодом", s._barge_drop and s.barge_total == 1)
    check("мост сбрасывает очередь: conversation.interrupted", any(e["type"] == "conversation.interrupted" for e in s.sent))
    check("модели — инструкция замолчать и ответить на слова клиента (прерывает речь)",
          s.client.instructions and s.client.instructions[-1] == h.BARGE_HINT)
    check("перебивание посчитано («перебиваний» в журнале больше не ноль)", cl.interruptions == 1)
    check("строка журнала: клиент заговорил поверх агента", "заговорил поверх агента — звук агента остановлен" in journal(cl))
    n = len(s.sent)
    await s._on_audio_delta(VOICE)
    await say(s, "assistant", "Бир порциясы сегиз даана, кайсы жакка жеткирели")
    check("пока клиент говорит — звук модели в линию не идёт", len(s.sent) == n)
    check("непрозвучавшие слова не попадают в диалог и стенограмму",
          "сегиз даана" not in " ".join(t.get("text", "") for t in s.transcript.turns()) and "сегиз даана" in s._dropped_text)
    await say(s, "user", "Үч порцияга, үч порцияга")
    # модель уступила (тишина), клиент договорил
    s._barge_voice_at -= 1.0
    s._last_user_audio_at -= 1.0
    s._barge_drop_at -= 1.0
    await s._on_audio_delta(QUIET)
    check("модель замолчала и клиент договорил — звук снова идёт", not s._barge_drop)
    await s._on_audio_delta(VOICE)
    check("новый ответ агента звучит", s.sent[-1]["type"] == "response.audio.delta" and s.assistant_speaking)
    check("журнал: что не прозвучало", "Не прозвучало (клиент перебил): «Бир порциясы сегиз даана" in journal(cl))
    check("слова клиента пришли — перебивание настоящее, не эхо", s.barge.false_count == 0
          and s.barge._pending_verify_at is None)

    # «Азыр токто» поверх агента — сразу стоп + короткие ответы; то же «токто» не глушит следующий ответ
    s2, cl2 = session()
    await s2._on_audio_delta(VOICE)
    await say(s2, "assistant", "Темпура креветка сыр менен запечённый вариантын сунуштайм, жумшак, ")
    await say(s2, "user", "Азыр токто")
    check("«азыр токто» поверх агента — звук остановлен сразу, не дожидаясь Decisions",
          s2._barge_drop and s2.barge_by["words"] == 1)
    check("и до конца звонка — короткие ответы", s2._brevity_locked and h.BREVITY_HINT in s2.client.instructions)
    s2._barge_voice_at -= 1.0
    s2._last_user_audio_at = 0.0
    s2._barge_drop_at -= 1.0
    await s2._on_audio_delta(QUIET)
    await s2._on_audio_delta(VOICE)
    await say(s2, "user", " ")
    check("то же «токто» в реплике не глушит следующий ответ", not s2._barge_drop and s2.barge_total == 1)
    # Decisions принял «азыр токтотосу» за прощание — для кода это просьба замолчать, трубку не кладём
    s3, cl3 = session()
    await say(s3, "user", "Азыр токтотосу")
    await s3._on_decision(decision(intent="goodbye", mood="irritated"))
    check("Decisions «goodbye» на «азыр токтотосу» → не прощание (автозавершение не ждёт тишины)",
          s3._farewell_at is None and s3._caller_farewell_at is None)
    check("журнал: «просит замолчать — это не прощание»", "это не прощание" in journal(cl3))
    s4, _ = session()
    await say(s4, "user", "сен көп сүйлөйсүң")
    await s4._on_decision(decision(intent="stop_talking"))
    check("жалоба «много говоришь», агент уже молчит — всё равно короткие ответы до конца звонка",
          s4._brevity_locked and s4.client.instructions == [h.BREVITY_HINT])
    await s4._on_decision(decision(intent="stop_talking"))
    check("короткие ответы — одна инструкция на звонок", s4.client.instructions == [h.BREVITY_HINT])

    # монолог 16 с → короткие ответы
    s5, cl5 = session()
    s5.assistant_speaking = True
    s5._reply_started_at = time.time() - 17
    s5._last_audio_at = time.time() - 1.2
    task = asyncio.create_task(s5._speech_watch())
    await asyncio.sleep(0.25)
    s5.stop_event.set()
    await task
    await asyncio.sleep(0)
    check("ответ длиннее 14 с — агент переходит на короткие ответы", "монолог" in s5._brevity_locked
          and h.BREVITY_HINT in s5.client.instructions, s5._brevity_locked)

    # ложное срабатывание (эхо): слов клиента нет 3 с — строка в журнале
    s6, cl6 = session()
    await s6._on_audio_delta(VOICE)
    await caller_talks(s6, 1.0)
    s6.barge._pending_verify_at = time.time() - 0.1
    task = asyncio.create_task(s6._speech_watch())
    await asyncio.sleep(0.15)
    s6.stop_event.set()
    await task
    check("перебивание без слов клиента — в журнале «похоже, эхо или шум»", "без слов клиента" in journal(cl6))

    # виджет (браузер гасит эхо сам) — код агента не глушит
    s7, _ = session(phone=False)
    await s7._on_audio_delta(VOICE)
    await s7._hard_barge_in("words")
    check("виджет: остановка кодом только на телефоне", not s7._barge_drop and s7.barge_total == 0)

    # диагностика
    s._log_diagnostics(120.0)
    check("итог звонка: сколько раз клиент перебивал и как", "клиент перебивал агента 1 раз (1 — голосом поверх агента)"
          in journal(cl), journal(cl)[-300:])


asyncio.run(scenario())
check("выключатель LIVE_PHONE_BARGE_IN в настройках", hasattr(__import__("backend.core.config", fromlist=["settings"]).settings,
                                                             "LIVE_PHONE_BARGE_IN"))

# ── 4. Слой промпта: коротко, по делу, с целью ───────────────────────────────────────────────────────
head, rules = lc.LIVE_VOICE_HEAD, lc.LIVE_VOICE_RULES
check("характер: коротко и по делу, не душный", "коротко, просто и по делу" in head and "Не душный" in head)
check("ответ — одна-две короткие фразы, не больше двух вариантов", "одна-две короткие фразы" in rules
      and "не больше двух на выбор" in rules)
check("у звонка есть результат — «ЦЕЛЬ ЗВОНКА», итог одной фразой и конец",
      "«ЦЕЛЬ ЗВОНКА»" in rules and "одной фразой подтверди итог" in rules)
check("названное количество, время, адрес — использовать как есть (было «три порции» → «одну»)",
      "Назвал количество, время, адрес — запомни и используй как есть" in rules)
check("рецепт OpenAI «Immediate stop»: стоп посреди фразы, без возврата к брошенному ответу",
      "stop speaking right away, even in the middle of a sentence" in rules
      and "stop right away, without finishing your sentence or adding an acknowledgment" in rules
      and "Don't resume an answer they've replaced." in rules and "договори мысль" not in rules)
check("кыргызский — коротко, по-кыргызски: бир сүйлөмдө бир ой, без поучений",
      "Кыска сүйлө: бир сүйлөмдө бир ой" in head and "акыл үйрөтпө" in head)
check("словарик понимания: «токто», «күтө туруңуз» — остановись, подожди",
      "«токто», «күтө туруңуз» — остановись, подожди" in head)
check("слой ≤ 8 500 символов, ≤ 50 фраз в кавычках", len(head + rules) <= 8500
      and len(re.findall(r"«[^»]{2,160}»", head + rules)) <= 50, len(head + rules))


# ── 5. Цель звонка ───────────────────────────────────────────────────────────────────────────────────
class _Q:
    def __init__(self, user):
        self.user = user

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.user


class FakeDB:
    def __init__(self, user):
        self.user = user

    def query(self, *a, **k):
        return _Q(self.user)


own = types.SimpleNamespace(user_id=1, voice_goal="Принять заказ: что и сколько, адрес, время, оплата.")
blk = avc.call_goal_block(FakeDB(None), own)
check("своя цель владельца → раздел «ЦЕЛЬ ЗВОНКА»", blk.startswith("# ЦЕЛЬ ЗВОНКА")
      and "результатом: Принять заказ: что и сколько, адрес, время, оплата." in blk and "Для этого" not in blk, blk)
niche_user = types.SimpleNamespace(business_niche="food_delivery")
blk2 = avc.call_goal_block(FakeDB(niche_user), types.SimpleNamespace(user_id=1, voice_goal=None))
from backend.services import niche_presets
p = niche_presets.preset("food_delivery")
check("цели нет — цель ниши + что узнать (по одному вопросу)",
      p["goal"] in blk2 and all(x in blk2 for x in p["ask"]) and "по одному вопросу" in blk2, blk2)
check("ни цели, ни ниши — раздела нет", avc.call_goal_block(FakeDB(types.SimpleNamespace(business_niche=None)),
                                                          types.SimpleNamespace(user_id=1, voice_goal="")) == "")
check("исходящий с задачей: задача звонка главнее", "своя задача этого звонка — она главнее" in blk2)
_orig = avc.build_voice_knowledge_block
avc.build_voice_knowledge_block = lambda db, agent, budget=0: ("# ЧТО ТЫ ЗНАЕШЬ НАИЗУСТЬ\nРоллы — 890 сом.", {"knowledge": 40})
try:
    card = types.SimpleNamespace(system_prompt="Ты разговариваешь с человеком по телефону от имени компании.")
    agent = types.SimpleNamespace(user_id=1, voice_goal="принять заказ", voice_additional_instructions="Ты — Айгуль.")
    prompt, stats = avc.build_call_system_prompt(FakeDB(None), card, agent, call_context="Входящий звонок", max_chars=24000)
finally:
    avc.build_voice_knowledge_block = _orig
check("промпт звонка: цель — первым разделом, потом знания, правила и контекст",
      prompt.startswith("# ЦЕЛЬ ЗВОНКА") and prompt.index("ЧТО ТЫ ЗНАЕШЬ НАИЗУСТЬ") < prompt.index("Ты — Айгуль")
      < prompt.index("[Контекст звонка]") and stats.get("goal"), prompt[:120])
card2 = types.SimpleNamespace(id="x", voice="cedar", greeting_message=None, language="ky",
                              functions={"enabled_functions": ["hangup_call"]}, system_prompt=prompt)
ins = lc.OpenAILiveClient("k", card2, "cid", telephony=True).build_session_config()["instructions"]
check("инструкции GPT-Live: голова → цель звонка → правила разговора",
      ins.index("# Кто ты и как звучишь") < ins.index("# ЦЕЛЬ ЗВОНКА") < ins.index("# Как вести разговор"))
_api = open(os.path.join(ROOT, "backend/api/agent.py"), encoding="utf-8").read()
_app = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
check("кабинет: поле «Цель звонка» сохраняется (API + колонка при старте)",
      "voice_goal: Optional[str] = Field(None, max_length=600)" in _api and "'voice_goal'," in _api
      and '"voice_goal_default": _voice_goal_default(agent)' in _api and "ADD COLUMN IF NOT EXISTS voice_goal TEXT" in _app)
_bot = open(os.path.join(ROOT, "backend/static/start/bot.js"), encoding="utf-8").read()
_prof = open(os.path.join(ROOT, "backend/static/agent.html"), encoding="utf-8").read()
check("поле «Цель звонка» есть у Старта и Profi", 'id="i-goal"' in _bot and "voice_goal:" in _bot
      and 'id="i-voice_goal"' in _prof)
_gw = open(os.path.join(ROOT, "backend/api/sip_gateway.py"), encoding="utf-8").read()
check("журнал звонка: есть ли у звонка цель", "Цель звонка: " in _gw and "Цели звонка нет" in _gw)

# ── 6. Голоса Алия / Алия 2 / Азим ───────────────────────────────────────────────────────────────────
sys.argv = ["x"]
import importlib.util
spec = importlib.util.spec_from_file_location("clv", os.path.join(ROOT, "scripts/create_live_voice.py"))
clv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clv)
check("пресеты голосов: aliya, aliya2, azim", set(clv.PRESETS) >= {"aliya", "aliya2", "azim"})
check("описания по качествам OpenAI: возраст, высота, тембр, темп; дикторская, но разговорная подача",
      all("perceived age" in v["prompt"] and "pitch" in v["prompt"] and "timbre" in v["prompt"] and "pace" in v["prompt"]
          and "broadcast-quality" in v["prompt"] and "never reading aloud" in v["prompt"]
          and "not an announcer" not in v["prompt"] for v in clv.PRESETS.values()))
check("голос держится на телефонной линии: чёткие согласные, без придыхания и хрипа",
      all("phone line" in v["prompt"] and "no breathiness" in v["prompt"] for v in clv.PRESETS.values()))
check("образец речи: оба языка, короткие фразы, ≤ 2000 символов; Азим — мужской род",
      all(re.search(r"[ңөү]", v["script"]) and "здравствуйте" in v["script"].lower() and len(v["script"]) <= 2000
          for v in clv.PRESETS.values()) and "записал:" in clv.PRESETS["azim"]["script"]
      and "записала:" in clv.PRESETS["aliya"]["script"])
check("род голосов: Алия, Алия 2 — женский, Азим — мужской",
      clv.PRESETS["aliya"]["gender"] == "f" and clv.PRESETS["aliya2"]["gender"] == "f" and clv.PRESETS["azim"]["gender"] == "m")

# ── 7. Анри: сценка до заказа, цель звонка ───────────────────────────────────────────────────────────
anri = open(os.path.join(ROOT, "docs/voice_agent_prompt_anri.md"), encoding="utf-8").read()
spec2 = importlib.util.spec_from_file_location("sas", os.path.join(ROOT, "scripts/setup_anri_sales.py"))
sas = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(sas)
goal = sas.extract_goal(anri)
check("у Анри есть цель звонка, скрипт --prompts её пишет", goal.startswith("Подключить клиента") and len(goal) <= 600, goal)
b1 = sas.extract_block1(anri)
check("сценка: меню придумывать, в базе не искать, вести к оформлению заказа (адрес, время, оплата)",
      "не ищи их в базе знаний" in b1 and "адрес, время, оплата" in b1 and "из роли не выходи" in b1)
orch = open(os.path.join(ROOT, "docs/orchestrator_prompt_anri.md"), encoding="utf-8").read()
check("инструкция переписки Анри без мест шаблона <…> (журнал звонка их ругал)", not re.findall(r"<[^<>\n]{2,80}>", orch))

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
