"""Запрос к модели переписки повторяется на временных сбоях (ветка 1111-clean-system), без сети."""
import asyncio
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)
import backend.core.config  # noqa: F401
import httpx
from backend.services import openrouter_client as orc

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


orc.RETRY_DELAYS = (0.0, 0.0)
script = []


def handler(request):
    code, body = script.pop(0)
    return httpx.Response(code, json=body, request=request)

real_client = httpx.AsyncClient
orc.httpx.AsyncClient = lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
ok_body = {"choices": [{"message": {"content": "ok"}}], "usage": {}}


async def call():
    c = orc.OpenRouterClient.__new__(orc.OpenRouterClient)
    c.api_key = "k"
    return await c.chat_completion(model="m", messages=[{"role": "user", "content": "x"}])

script[:] = [(503, {}), (200, ok_body)]
r = asyncio.run(call())
check("503 → повтор → ответ", r["choices"][0]["message"]["content"] == "ok" and not script)
script[:] = [(200, {"error": {"message": "upstream"}}), (429, {}), (200, ok_body)]
r = asyncio.run(call())
check("пустой ответ и 429 → ещё повторы → ответ", r["choices"] and not script)
script[:] = [(400, {"error": "bad"})]
try:
    asyncio.run(call())
    check("400 (ошибка запроса) — без повторов, исключение", False)
except httpx.HTTPStatusError:
    check("400 (ошибка запроса) — без повторов, исключение", not script)
script[:] = [(503, {}), (503, {}), (503, {})]
try:
    asyncio.run(call())
    check("три сбоя подряд — исключение (разбор запишет ошибку)", False)
except Exception:
    check("три сбоя подряд — исключение (разбор запишет ошибку)", not script)
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
