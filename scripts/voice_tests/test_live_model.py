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
check("отказ модели делегирования не переключает голосовую модель", not ok and models == ["gpt-live-1.1"], models)

print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
sys.exit(1 if failures else 0)
