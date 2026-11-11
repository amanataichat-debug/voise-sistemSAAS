"""
Звук агента в телефонной линии (ветка 1111-clean-system, жалоба «запинки, звук пропадает»), без сети:
  * GPT-Live отдаёт речь в реальном темпе; сетевая пауза посреди фразы опустошает очередь моста —
    адаптер замечает провал, наращивает запас и добирает немного звука перед продолжением;
  * ровный поток провалов не даёт и запас не растёт;
  * перебивание сбрасывает очередь, но выученный запас остаётся.
"""
import asyncio
import base64
import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401
from backend.websockets import sip_media_adapter as sma

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_bytes(self, b):
        self.sent.append(b)

    async def send_text(self, t):
        self.sent.append(json.loads(t))


FRAME = b"\x01\x00" * 160            # 20 мс PCM16 8 кГц
DELTA = base64.b64encode(FRAME * 2).decode()   # 40 мс за дельту (выход GPT-Live на G.711 = 8 кГц)


async def stream(sock, seconds, pause_at=None, pause=0.0):
    n = int(seconds / 0.04)
    for i in range(n):
        if pause_at is not None and i == int(pause_at / 0.04):
            await asyncio.sleep(pause)
        await sock.send_json({"type": "response.audio.delta", "delta": DELTA})
        await asyncio.sleep(0.04)


async def main():
    ws = FakeWS()
    s = sma.HandlerSocket(ws, "openai_g711", "call-1")
    s._voice.enabled = False
    base = s._cushion_bytes
    await stream(s, 1.2)
    check("ровный поток: провалов нет, запас прежний", s.underruns == 0 and s._cushion_bytes == base,
          (s.underruns, s._cushion_bytes))
    await stream(s, 1.0, pause_at=0.4, pause=0.45)
    check("сетевая пауза посреди фразы → провал замечен", s.underruns >= 1, s.underruns)
    check("запас на следующие реплики вырос", s._cushion_bytes > base, s._cushion_bytes)
    check("запас не растёт бесконечно", s._cushion_bytes <= s._cushion_max)
    learned = s._cushion_bytes
    await s.send_json({"type": "conversation.interrupted"})
    check("перебивание: очередь моста сброшена, выученный запас остаётся",
          {"type": "clear"} in ws.sent and s._cushion_bytes == learned and not s._out_hold)
    sent_audio = sum(len(b) for b in ws.sent if isinstance(b, (bytes, bytearray)))
    check("весь звук дошёл до моста (ничего не потеряно при добое буфера)",
          sent_audio + len(s._out_hold) == int((1.2 + 1.0) / 0.04) * len(FRAME) * 2, sent_audio)


asyncio.run(main())


# --- функции поиска на звонке не останавливают звук: выполняются в фоновом потоке ---
import threading
from backend.websockets import function_calls as fc
from backend.websockets.call_log import CallLogRecorder, CURRENT_CALL_LOG

seen = {}


async def fake_execute(name, arguments, context):
    seen[name] = (threading.get_ident(), CURRENT_CALL_LOG.get())
    return {"ok": True}

fc.execute_function = fake_execute


async def offload_check():
    log = CallLogRecorder(channel="phone")
    log.activate()
    await fc.run_function("search_knowledge_base", {"query": "цена"}, {})
    await fc.run_function("hangup_call", {}, {})
    main_thread = threading.get_ident()
    check("поиск по базе на звонке — в фоновом потоке (звук основного потока не стоит)",
          seen["search_knowledge_base"][0] != main_thread)
    check("журнал звонка доступен функции и в фоновом потоке", seen["search_knowledge_base"][1] is log)
    check("hangup_call остаётся в основном потоке (трогает сам звонок)", seen["hangup_call"][0] == main_thread)

asyncio.run(offload_check())
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
