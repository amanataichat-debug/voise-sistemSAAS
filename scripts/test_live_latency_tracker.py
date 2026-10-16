"""
Проверка замера задержки GPT-Live без сети: синтетическое аудио → EndOfSpeechTracker
и LiveVoiceSession._record_reply_latency. Запуск: python scripts/test_live_latency_tracker.py
"""
import array
import base64
import math
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.websockets.call_log import CallLogRecorder  # noqa: E402
from backend.websockets.handler_live import EndOfSpeechTracker, LiveVoiceSession, OutputSpeechDetector  # noqa: E402

RATE = 24000
FRAME = RATE // 50  # 20 мс


def frame(loud: bool) -> bytes:
    amp = 6000 if loud else 8
    return array.array("h", [int(amp * math.sin(i * 0.3)) for i in range(FRAME)]).tobytes()


def feed(tracker, t0, seconds, loud, results):
    t = t0
    for _ in range(int(seconds * 50)):
        t += 0.02
        ended = tracker.feed(frame(loud), t)
        if ended is not None:
            results.append(ended)
    return t


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)


# 1. конец речи = последний громкий кадр, а не момент решения
tr, ends = EndOfSpeechTracker(), []
t = feed(tr, 100.0, 1.0, False, ends)
t = feed(tr, t, 1.0, True, ends)
speech_end = t
t = feed(tr, t, 1.0, False, ends)
check("конец реплики найден один раз", len(ends) == 1)
check("конец речи ≈ последний громкий кадр (±40 мс)", abs(ends[0] - speech_end) < 0.04)

# 2. тишина без речи ничего не даёт
tr, ends = EndOfSpeechTracker(), []
feed(tr, 0.0, 3.0, False, ends)
check("тишина — нет реплик", not ends)


def make_session():
    s = LiveVoiceSession.__new__(LiveVoiceSession)
    s.call_log = CallLogRecorder("phone")
    s._eos = EndOfSpeechTracker()
    s._out_det = OutputSpeechDetector()
    s._user_done_at = None
    s._reply_with_function = False
    s.assistant_speaking = False
    return s


# 3. ответ после паузы: задержка считается от реального конца речи
import time  # noqa: E402

s = make_session()
s._on_user_speech_end(time.time() - 1.2)
s._record_reply_latency()
lat = s.call_log.latencies_plain_ms
check("ответ без функции записан", len(lat) == 1 and 1150 <= lat[0] <= 1350)

# 4. ответ с функцией разложен отдельно
s = make_session()
s._on_user_speech_end(time.time() - 4.0)
s._reply_with_function = True
s._record_reply_latency()
check("ответ с функцией записан отдельно", not s.call_log.latencies_plain_ms and len(s.call_log.latencies_fn_ms) == 1)
check(">=3 с — предупреждение в журнале", any(e["level"] == "warning" and e["kind"] == "latency" for e in s.call_log.events))

# 5. быстрый ответ раньше паузы детектора: модель заговорила через 0,3 с после конца речи
s = make_session()
base = time.time() - 1.0
t = base
for _ in range(50):
    t += 0.02
    s._eos.feed(frame(True), t)
# «сейчас» = t + 0.3 с тишины; подменим время через прямой вызов take_pending_end
done = s._eos.take_pending_end(t + 0.3)
check("быстрый ответ: конец речи забран до таймера детектора", done is not None and abs(done - t) < 0.001)

# 6. модель заговорила поверх клиента — замера нет
s = make_session()
t = time.time()
for _ in range(30):
    t += 0.02
    s._eos.feed(frame(True), t)
check("перебивание: клиент ещё говорит — замер не берём", s._eos.take_pending_end(t + 0.02) is None)

# 7. слишком старый конец речи (клиент ушёл) в замер не попадает
s = make_session()
s._on_user_speech_end(time.time() - 60)
s._record_reply_latency()
check("задержка > 30 с отброшена", not s.call_log.latencies_ms)

# 8. выходной поток GPT-Live непрерывный: тишина — не речь ассистента, громкий звук — речь
det = OutputSpeechDetector()
res = [det.feed(frame(False)) for _ in range(100)]
check("тишина в выходном потоке — ассистент не говорит", not det.voiced and not any(res))
res = [det.feed(frame(True)) for _ in range(10)]
check("громкий звук — начало речи ровно один раз", det.voiced and res.count("start") == 1)
res = [det.feed(frame(False)) for _ in range(30)]
check("0,6 с тишины — конец речи", not det.voiced and res.count("end") == 1)

# 9. задержка фиксируется по началу голоса, а не по приходу первых данных
s = make_session()
s._on_user_speech_end(time.time() - 1.5)
for _ in range(40):  # 0,8 с тишины от модели: данные идут, но ответа ещё нет
    if s._out_det.feed(frame(False)) == "start":
        s._record_reply_latency()
check("тихие данные не считаются ответом", not s.call_log.latencies_ms)
for _ in range(5):
    if s._out_det.feed(frame(True)) == "start":
        s._record_reply_latency()
check("первый голос — задержка записана", len(s.call_log.latencies_ms) == 1 and s.call_log.latencies_ms[0] >= 1450)

print("summary:", {k: v for k, v in make_session().call_log.summary().items() if k.startswith("latency")})
print("все проверки пройдены")
