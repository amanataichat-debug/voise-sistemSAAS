"""
Проверка заполнителя и «бэкенд не ответил» в LiveVoiceSession без сети.
Запуск: HOST_URL=https://voksyai.online DATABASE_URL=sqlite:///:memory: python scripts/test_live_delegation_watch.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.websockets import handler_live as h  # noqa: E402
from backend.websockets.call_log import CallLogRecorder  # noqa: E402

h.FILLER_DELAY_SEC = 0.3
h.FILLER_REPEAT_SEC = 0.5
h.DELEGATION_TIMEOUT_SEC = 1.6
h.DELEGATION_WATCH = True


class FakeClient:
    delegation_model = "fake"
    enabled_functions = []

    def __init__(self):
        self.commentary, self.instructions = [], []
        self._delegations = {}

    async def append_commentary(self, text, delegation_id=None):
        self.commentary.append(text); return True

    async def append_instructions(self, text, delegation_id=None):
        self.instructions.append(text); return True

    async def send_function_result(self, call_id, result):
        return {"success": True}


def make():
    s = h.LiveVoiceSession.__new__(h.LiveVoiceSession)
    s.client = FakeClient(); s.call_log = CallLogRecorder("phone")
    s.stop_event = asyncio.Event(); s._tasks = []; s.closed = True
    s.assistant_speaking = False; s._last_audio_at = 0.0; s._last_voice_at = 0.0
    s._out_det = h.OutputSpeechDetector()
    s._user_done_at = None; s._reply_with_function = False
    s._delegation_since = None; s._delegation_gen = 0; s._round_has_calls = {}
    s.delegations = 0; s.backend_text = ""
    return s


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)


async def ev(s, **e):
    await s._on_live_event(e)


async def main():
    # 1. тишина → заполнитель, потом сдаёмся с честным «не вышло»
    s = make()
    await ev(s, type="session.delegation.created", delegation={"id": "d1"})
    await asyncio.sleep(0.5)
    check("заполнитель отправлен после тишины", len(s.client.commentary) == 1 and not s.client.instructions)
    await asyncio.sleep(1.5)
    check("повторный заполнитель не чаще лимита, всего ≤ 2", len(s.client.commentary) == 2)
    check("по таймауту — сообщение «не получилось»", len(s.client.instructions) == 1)
    check("в журнале ошибка про бэкенд", any(e["level"] == "error" and "Бэкенд" in e["text"] for e in s.call_log.events))
    await asyncio.sleep(0.6)
    check("после сдачи повторов нет", len(s.client.instructions) == 1)

    # 2. финальный ответ без функций → слежение остановлено, заполнителя нет
    s = make()
    await ev(s, type="session.delegation.created", delegation={"id": "d2"})
    await ev(s, type="response.event", delegation_id="d2", event={"type": "response.completed"})
    await asyncio.sleep(0.6)
    check("ответ пришёл быстро — заполнителя нет", not s.client.commentary and not s.client.instructions)

    # 3. раунд с функцией: первый completed — промежуточный, слежение продолжается
    s = make()
    await ev(s, type="session.delegation.created", delegation={"id": "d3"})
    await ev(s, type="live.function_call", delegation_id="d3", call_id="c1", name="x", arguments={})
    await ev(s, type="response.event", delegation_id="d3", event={"type": "response.completed"})
    check("после раунда с функцией бэкенд ещё считается занятым", s._delegation_since is not None)
    await ev(s, type="response.event", delegation_id="d3", event={"type": "response.completed"})
    check("финальный ответ закрывает слежение", s._delegation_since is None)

    # 4. response.failed → «не получилось» сразу
    s = make()
    await ev(s, type="session.delegation.created", delegation={"id": "d4"})
    await ev(s, type="response.event", delegation_id="d4", event={"type": "response.failed",
             "response": {"error": {"code": "insufficient_quota", "message": "You have no credits remaining"}}})
    check("падение бэкенда — сообщение клиенту сразу", len(s.client.instructions) == 1)

    # 5. ассистент говорит → заполнитель не нужен
    s = make()
    await ev(s, type="session.delegation.created", delegation={"id": "d5"})
    s._out_det.voiced = True
    await asyncio.sleep(0.6)
    check("пока ассистент говорит — молчим", not s.client.commentary)
    s.stop_event.set()

    # 6. по умолчанию слежение выключено: ничего не отправляем
    h.DELEGATION_WATCH = False
    s = make()
    await ev(s, type="session.delegation.created", delegation={"id": "d6"})
    await asyncio.sleep(0.6)
    check("слежение выключено — заполнителей и сообщений нет", not s.client.commentary and not s.client.instructions)
    print("все проверки пройдены")


asyncio.run(main())
