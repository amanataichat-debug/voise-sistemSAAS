"""
Предел звонка 5 минут и запрет мата (18.11.2026, звонок 17478409: 6 минут, клиент «охуенно говорите, не стыдно?»).

  1. Словарь мата: ловит русский/кыргызский/английский мат и латиницу-двойники, не трогает «застрахуем», «себе», «небо»…
  2. Переписка: clean_client_text вырезает слово.
  3. Каскад Voksy Voice: мат вырезается до синтеза.
  4. Телефон (GPT-Live / Gemini Live): мат в стенограмме ответа → сброс очереди моста, глушение звука, указание извиниться.
  5. Сторож звонка: на 5-й минуте агенту уходит wrap_up, через минуту — трубка; hangup_call после wrap_up разрешён.
Запуск: python scripts/voice_tests/test_call_limits_profanity.py (без сети и БД).
"""
import asyncio
import base64
import json
import logging
import os
import sys
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)
import backend.core.config  # noqa: E402,F401

from backend.utils.profanity import find_profanity, has_profanity, strip_profanity  # noqa: E402
from backend.services.message_style import clean_client_text  # noqa: E402

failures = 0


def check(name, ok, extra=""):
    global failures
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        failures += 1


# ── 1. словарь ───────────────────────────────────────────────────────────────
BAD = ("охуенно Охуенно ОХУЕННО нахуй похуй хуйня хуёво пиздец пизда заебал заебись уебок ебать ёбаный ебанутый "
       "выебываться отъебись долбоёб блядь бля блять мудак пидор пидорас сука сучка залупа гандон шлюха говно жопа "
       "дрочить сигейин fuck fucking shit bitch 0хуенно xуй oхуeнно").split()
GOOD = ("застрахуем страхуйте тихую сухую глухую психую себе себя небо хлеб сукно мудрый сиз сиздин сигнал хуже "
        "худой хутор хурма хулиган учеба убежать подъезд объезд отъезд съезд поехали наехать уехал приехать сразу "
        "страна посрамить блин бляшка мандарин мандат команда драчун ебонит отлично классно круто здорово "
        "жакшы рахмат").split()
check("мат: всё из списка ловится", [w for w in BAD if not has_profanity(w)] == [],
      [w for w in BAD if not has_profanity(w)])
check("мат: обычные слова не задеты", [w for w in GOOD if has_profanity(w)] == [],
      [w for w in GOOD if has_profanity(w)])
check("мат: в предложении находит слово", find_profanity("Да вы охуенно говорите!") == ["охуенно"])
check("вырезание: «Это охуенно, правда! Сука, круто.»",
      strip_profanity("Это охуенно, правда! Сука, круто.") == "Это правда! Круто.",
      strip_profanity("Это охуенно, правда! Сука, круто."))
check("вырезание: чистый текст не меняется", strip_profanity("Цена: 7 500 сом, да.") == "Цена: 7 500 сом, да.")

# ── 2. переписка ─────────────────────────────────────────────────────────────
out = clean_client_text("Это просто охуенно! Ссылка https://example.kg/a_b?x=1")
check("переписка: мат вырезан, ссылка цела", "охуе" not in out and "https://example.kg/a_b?x=1" in out, out)

# ── 3. каскад: вырезается до синтеза ─────────────────────────────────────────
from backend.websockets import handler_fish as hf  # noqa: E402


class _Log:
    def __init__(self):
        self.lines = []

    def add(self, kind, text, level="info", **kw):
        self.lines.append((kind, text, level))


spoken = []
fs = hf.FishVoiceSession.__new__(hf.FishVoiceSession)
fs.call_log = _Log()


async def _say(text):
    spoken.append(text)
fs.tts = NS(say=_say)
asyncio.run(fs._say("Это охуенно, давайте начнём."))
asyncio.run(fs._say("Хорошо."))
check("каскад: в синтез ушло без мата", spoken == ["Это давайте начнём.", "Хорошо."] or
      (all("охуе" not in s for s in spoken) and len(spoken) == 2), spoken)
check("каскад: строка в журнале звонка", any(l[2] == "warning" and "нецензурное" in l[1] for l in fs.call_log.lines))

# ── 4. адаптер звонка: обрыв звука и указание извиниться ────────────────────
from backend.websockets import sip_media_adapter as sma  # noqa: E402


class _WS:
    def __init__(self):
        self.texts, self.bytes = [], []
        self.headers, self.query_params, self.client = {}, {}, None

    async def send_text(self, t):
        self.texts.append(json.loads(t))

    async def send_bytes(self, b):
        self.bytes.append(b)


async def adapter_case():
    ws = _WS()
    sock = sma.HandlerSocket(ws, "openai_g711", "test-call")
    audio = base64.b64encode(b"\x10\x00" * 800).decode()
    await sock.send_json({"type": "transcript.delta", "role": "assistant", "delta": "Хорошего вам дня, "})
    check("адаптер: чистая речь — без сброса", not any(t.get("type") == "clear" for t in ws.texts))
    await sock.send_json({"type": "transcript.delta", "role": "assistant", "delta": "это о"})
    await sock.send_json({"type": "transcript.delta", "role": "assistant", "delta": "хуенно"})
    check("адаптер: мат по кускам стенограммы → сброс очереди моста",
          any(t.get("type") == "clear" for t in ws.texts), ws.texts)
    check("адаптер: счётчик", sock.profanity_hits == 1)
    msg = sock._queue.get_nowait()
    d = json.loads(msg["text"])
    check("адаптер: хендлеру ушло session.directive profanity",
          d.get("type") == "session.directive" and d.get("kind") == "profanity" and "извинись" in d.get("text", ""), d)
    before = sock.audio_bytes_out
    await sock.send_json({"type": "response.audio.delta", "delta": audio})
    check("адаптер: остаток фразы в линию не уходит", sock.audio_bytes_out == before)
    sock._mute_until = 0
    await sock.send_json({"type": "response.audio.delta", "delta": audio})
    check("адаптер: после паузы звук снова идёт", sock.audio_bytes_out > before)
    await sock.send_json({"type": "transcript.delta", "role": "assistant", "delta": "бля"})
    check("адаптер: недописанный короткий обрывок («бля…шка») не срабатывает", sock.profanity_hits == 1)
    await sock.send_json({"type": "transcript.delta", "role": "assistant", "delta": "шка на трубе."})
    check("адаптер: «бляшка» не мат", sock.profanity_hits == 1)
    await sock.send_json({"type": "transcript.delta", "role": "user", "delta": "охуенно говорите"})
    check("адаптер: мат КЛИЕНТА звук агента не обрывает", sock.profanity_hits == 1)
    await sock.send_directive("wrap_up", "итог")
    d = json.loads(sock._queue.get_nowait()["text"])
    check("адаптер: send_directive кладёт указание во входную очередь", d == {
        "type": "session.directive", "kind": "wrap_up", "text": "итог"}, d)

asyncio.run(adapter_case())

# ── 5. hangup_call после предела разрешён ────────────────────────────────────
from backend.websockets.call_guard import CallEndGuard  # noqa: E402
from backend.websockets.handler_live import LiveVoiceSession  # noqa: E402

g = CallEndGuard(phone=True)
g.last_user_voice_at = __import__("time").time()
check("Gemini: до предела hangup_call без прощания отклоняется",
      g.gate("hangup_call", {"reason": "conversation_completed"}) is not None)
g.time_limit_at = __import__("time").time()
check("Gemini: после wrap_up hangup_call разрешён", g.gate("hangup_call", {"reason": "conversation_completed"}) is None)

lv = LiveVoiceSession.__new__(LiveVoiceSession)
lv.telephony, lv.call_log = True, None
lv._time_limit_at = __import__("time").time()
check("GPT-Live: после wrap_up hangup_call разрешён", lv._hangup_refusal({"reason": "conversation_completed"}) is None)

# ── 6. сторож звонка: wrap_up на 5-й минуте, трубка через минуту ─────────────
from backend.api import sip_gateway as sg  # noqa: E402


async def watchdog_case():
    clock = [1000.0]
    yield_ = asyncio.sleep

    async def fast_sleep(sec):
        clock[0] += sec
        await yield_(0)

    sent, hung = [], []

    class FakeSock:
        closed = False
        ended_by_bridge = False
        _queue = asyncio.Queue()

        async def send_directive(self, kind, text):
            sent.append((clock[0] - 1000.0, kind, text))

        async def hangup(self, reason):
            hung.append((clock[0] - 1000.0, reason))
            self.closed = True

    transcript = NS(last_activity_at=0.0)
    real_sleep, real_time = sg.asyncio.sleep, sg.time.time
    sg.asyncio.sleep, sg.time.time = fast_sleep, lambda: clock[0]
    try:
        async def talk():  # кто-то всё время говорит: тишина не срабатывает
            while not hung:
                transcript.last_activity_at = clock[0]
                await real_sleep(0)
        talker = asyncio.ensure_future(talk())
        await asyncio.wait_for(sg._call_watchdog(FakeSock(), transcript, "c1"), 5)
        talker.cancel()
    finally:
        sg.asyncio.sleep, sg.time.time = real_sleep, real_time
    check("сторож: предел по умолчанию 5 мин", sg.SIP_SOFT_CALL_SECONDS == 300)
    check("сторож: wrap_up ушёл один раз сразу после 5 мин",
          len(sent) == 1 and sent[0][1] == "wrap_up" and 300 < sent[0][0] <= 310 and "другие звонки" in sent[0][2], sent)
    check("сторож: не уложился — трубка через минуту", len(hung) == 1 and hung[0][1] == "time_limit"
          and 360 < hung[0][0] <= 375, hung)

asyncio.run(watchdog_case())

print(f"\n{'OK' if not failures else 'FAILED'}: {failures} провал(ов)")
sys.exit(1 if failures else 0)
