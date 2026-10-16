"""
Автозавершение по оценке модели («разговор закончен?»), без сети.
Запуск: HOST_URL=https://voksyai.online DATABASE_URL=sqlite:///:memory: python scripts/test_call_end_judge.py
"""
import asyncio
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.websockets import call_end_judge as j  # noqa: E402
from backend.websockets import handler_live as h  # noqa: E402
from backend.websockets.call_log import CallLogRecorder  # noqa: E402


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)


# ---- разбор ответа модели
for text, want in [("END", True), ("end.", True), ("CONTINUE", False), ("continue", False), ("  END\n", True),
                   ("CONTINUE (клиент думает)", False), ("", None), ("не знаю", None), ("Я думаю END", True)]:
    check(f"ответ «{text.strip()}» → {want}", j.parse_verdict(text) == want)

turns = [{"role": "assistant", "text": "Жакшы калыңыз!"}, {"role": "user", "text": "Рахмат, сау болуңуз"}]
check("транскрипт форматируется", j.format_turns(turns) == "АГЕНТ: Жакшы калыңыз!\nКЛИЕНТ: Рахмат, сау болуңуз")


async def http_case(handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        return await j.call_is_finished("k", turns, "m", client=c)


def reply(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


async def main():
    check("модель сказала END → True", await http_case(lambda r: reply("END")) is True)
    check("модель сказала CONTINUE → False", await http_case(lambda r: reply("CONTINUE")) is False)
    check("HTTP 500 → None", await http_case(lambda r: httpx.Response(500, text="boom")) is None)
    check("непонятный ответ → None", await http_case(lambda r: reply("мм")) is None)

    seen = []

    def picky(request):
        body = json.loads(request.content)
        seen.append({k: v for k, v in body.items() if k.startswith("reasoning")})
        if "reasoning_effort" in body:
            return httpx.Response(400, text="Unsupported parameter: 'reasoning_effort'")
        return reply("END")
    check("модель не принимает reasoning_effort — запрос повторён без него", await http_case(picky) is True and len(seen) == 3)

    # ---- слежение в сессии
    calls = []
    verdicts = []

    async def fake_judge(api_key, t, model, timeout=6.0, client=None):
        calls.append(len(t))
        return verdicts.pop(0) if verdicts else None
    j.call_is_finished = fake_judge

    class Ws:
        def __init__(self): self.sent = []
        async def send_json(self, d): self.sent.append(d)

    class Cl:
        api_key = "k"

    def make():
        s = h.LiveVoiceSession.__new__(h.LiveVoiceSession)
        s.telephony = True
        s.ws = Ws(); s.closed = False
        s.client = Cl()
        s.call_log = CallLogRecorder("phone")
        s.transcript = h.TranscriptCollector()
        s.transcript.add("assistant", "Здравствуйте", 0, 1000)
        s.transcript.add("user", "Хорошо, спасибо, до свидания", 2000, 3000)
        s.started_at = time.time() - 60
        s._tasks = []
        s._log_buf = {"user": "", "assistant": ""}; s._log_role = None
        s.stop_event = asyncio.Event()
        s._eos = h.EndOfSpeechTracker()
        s._out_det = h.OutputSpeechDetector()
        s._last_voice_at = 0.0
        s._hangup_sent = False
        s._farewell_at = None; s._farewell_user = False
        s._judge_running = False; s._judge_calls = 0; s._judge_errors = 0
        s._judged_voice_mark = -1.0; s._judged_sig = -1; s._judged_at = 0.0
        return s

    async def run_tick(s, now):
        s._maybe_judge(now)
        await asyncio.gather(*list(s._tasks))

    h.AUTO_HANGUP_MODE = "judge"
    h.JUDGE_SILENCE_SEC = 3.0
    base = time.time()

    s = make(); s._last_voice_at = base - 1.0
    await run_tick(s, base)
    check("тишина короче 3 с — модель не зовём", not calls)

    s = make(); s._last_voice_at = base - 4.0; verdicts[:] = [True]
    await run_tick(s, base)
    check("тишина 4 с — модель позвана один раз", len(calls) == 1)
    check("модель сказала «закончен» — команда положить трубку", any(m.get("type") == "call.hangup" for m in s.ws.sent))

    calls.clear(); s = make(); s._last_voice_at = base - 4.0; verdicts[:] = [False]
    await run_tick(s, base)
    check("модель сказала «продолжается» — звонок не трогаем", not any(m.get("type") == "call.hangup" for m in s.ws.sent))
    await run_tick(s, base + 1)
    check("тот же отрезок тишины без нового текста — повторно не спрашиваем", len(calls) == 1)
    s.transcript.add("user", "Подождите, ещё вопрос", 4000, 5000)
    verdicts[:] = [False]
    await run_tick(s, base + 2)
    check("в транскрипте появился новый текст — спрашиваем заново", len(calls) == 2)
    await run_tick(s, base + 14)
    check("ещё 10+ с тишины — спрашиваем снова", len(calls) == 3)

    calls.clear(); s = make(); s._last_voice_at = base - 4.0; verdicts[:] = [True]
    s._out_det.voiced = True
    await run_tick(s, base)
    check("ассистент говорит — не зовём", not calls)

    calls.clear(); s = make(); s._last_voice_at = base - 4.0
    s.started_at = time.time() - 3
    await run_tick(s, base)
    check("в первые 10 с разговор «закончиться» не может", not calls)

    # клиент заговорил, пока шёл запрос
    s = make(); s._last_voice_at = base - 4.0

    async def slow_judge(api_key, t, model, timeout=6.0, client=None):
        s._eos._vad.last_voice_at = time.time()   # клиент заговорил за время запроса
        return True
    j.call_is_finished = slow_judge
    await run_tick(s, base)
    check("клиент заговорил во время запроса — трубку не кладём", not any(m.get("type") == "call.hangup" for m in s.ws.sent))

    # две ошибки подряд → запасной путь по словам
    j.call_is_finished = fake_judge
    s = make(); s._last_voice_at = base - 4.0; verdicts[:] = [None, None]
    await run_tick(s, base)
    s._judged_sig = -1
    await run_tick(s, base + 1)
    check("две ошибки подряд — судья считается сломанным", s._judge_errors >= h.JUDGE_BROKEN_AFTER)
    s._log_transcript("user", "ну всё, до свидания")
    s._last_voice_at = s._farewell_at + 1
    check("запасной путь по словам прощания включился", s._should_auto_hangup(s._farewell_at + 4.0))

    # режим выключен
    h.AUTO_HANGUP_MODE = "off"; h.AUTO_HANGUP = False
    calls.clear(); s = make(); s._last_voice_at = base - 4.0
    await run_tick(s, base)
    check("режим off — модель не зовём", not calls)
    print("все проверки пройдены")


asyncio.run(main())
