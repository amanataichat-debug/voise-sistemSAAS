"""
Автозавершение звонка после прощания, без сети.
Запуск: HOST_URL=https://voksyai.online DATABASE_URL=sqlite:///:memory: python scripts/test_live_auto_hangup.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.websockets import handler_live as h  # noqa: E402
from backend.websockets.call_log import CallLogRecorder  # noqa: E402


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)


# --- распознавание прощания
for t in ["До свидания", "Все хорошо, давай пока", "Пока.", "Всего доброго!", "Рахмат, жакшы калыңыз",
          "Кош болуңуз", "ну всё, до встречи"]:
    check(f"прощание: «{t}»", h.is_farewell(t))
for t in ["Пока не знаю, что выбрать", "А пока что подскажите цену", "Сколько стоит подключение", "Рахмат, түшүнүктүү",
          "Давайте я пока пришлю предложение, а вы подумаете"]:
    check(f"не прощание: «{t}»", not h.is_farewell(t))


def make(phone=True):
    s = h.LiveVoiceSession.__new__(h.LiveVoiceSession)
    s.telephony = phone
    s.call_log = CallLogRecorder("phone" if phone else "widget")
    s._log_buf = {"user": "", "assistant": ""}
    s._log_role = None
    s._eos = h.EndOfSpeechTracker()
    s._out_det = h.OutputSpeechDetector()
    s._last_voice_at = 0.0
    s._farewell_at = None
    s._farewell_user = False
    s._hangup_sent = False
    return s


now = time.time()

# 1. клиент попрощался, ассистент ответил «Пока», обе стороны молчат 3 с → кладём трубку
s = make()
s._log_transcript("user", "Все хорошо, давай пока")
t0 = s._farewell_at
check("прощание клиента замечено", t0 is not None and s._farewell_user)
s._last_voice_at = t0 + 1.0                       # ассистент ответил
check("сразу после ответа трубку не кладём", not s._should_auto_hangup(t0 + 1.5))
check("после 2,5 с общей тишины — кладём", s._should_auto_hangup(t0 + 1.0 + 2.6))

# 2. ассистент ещё не ответил на прощание клиента: ждём до 8 с
s = make()
s._log_transcript("user", "До свидания")
t0 = s._farewell_at
check("ответа ассистента нет — не торопимся", not s._should_auto_hangup(t0 + 4.0))
check("ответа нет 8 с — кладём (клиент ушёл)", s._should_auto_hangup(t0 + 8.1))

# 3. попрощался только ассистент: нужно 5 с тишины
s = make()
s._log_transcript("assistant", "Супер, договорились. Всего доброго!")
t0 = s._farewell_at
s._last_voice_at = t0
check("ассистент попрощался — 3 с мало", not s._should_auto_hangup(t0 + 3.0))
check("ассистент попрощался — 5,1 с достаточно", s._should_auto_hangup(t0 + 5.1))

# 4. клиент заговорил после прощания — разговор продолжается
s = make()
s._log_transcript("assistant", "Всего доброго!")
t0 = s._farewell_at
s._last_voice_at = t0
s._eos._vad.last_voice_at = t0 + 3.0              # клиент снова говорил
check("клиент заговорил после прощания — не кладём", not s._should_auto_hangup(t0 + 12.0))
check("и прощание сброшено", s._farewell_at is None)

# 5. ассистент говорит — не кладём
s = make()
s._log_transcript("assistant", "Всего доброго!")
s._last_voice_at = s._farewell_at
s._out_det.voiced = True
check("пока ассистент говорит — не кладём", not s._should_auto_hangup(s._farewell_at + 30))

# 6. виджет (не телефон) — никогда
s = make(phone=False)
s._log_transcript("user", "До свидания")
check("в виджете автозавершения нет", s._farewell_at is None and not s._should_auto_hangup(time.time() + 60))

# 7. после отправки команды повторно не шлём
s = make()
s._log_transcript("assistant", "Всего доброго!")
s._last_voice_at = s._farewell_at
s._hangup_sent = True
check("команда уже отправлена — повторов нет", not s._should_auto_hangup(s._farewell_at + 30))
print("все проверки пройдены")
