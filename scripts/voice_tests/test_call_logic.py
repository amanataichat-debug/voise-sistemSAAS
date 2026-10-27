"""Логика звонка GPT-Live: защита hangup_call, диагностика, повторы, бюджет промпта, конфиг сессии
(без сети и БД; вызовы функций и клиента подменены)."""
import asyncio
import base64
import logging
import math
import os
import struct
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.websockets.handler_live as h
import backend.websockets.live_client as lc
import backend.services.agent_voice_context as avc
from backend.websockets.call_log import CallLogRecorder

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


class FC:
    session_id = "s"
    io_rate = 8000
    enabled_functions = ["hangup_call"]

    def __init__(self):
        self.results, self.thinking = [], []

    async def send_function_result(self, cid, res):
        self.results.append((cid, res))
        return {"success": True}

    async def append_thinking(self, text):
        self.thinking.append(text)
        return True

    def decode_output_b64(self, x):
        return x


def session(phone=True, **kw):
    cl = CallLogRecorder(channel="phone" if phone else "widget")
    s = h.LiveVoiceSession(websocket=None, assistant=types.SimpleNamespace(**kw), client=FC(), db=None,
                           client_id="c", telephony=phone, call_log=cl)
    out = []

    async def em(d):
        out.append(d["type"])
    s.emit = em
    return s, out, cl


def hangup(reason="conversation_completed"):
    return {"name": "hangup_call", "call_id": "c1", "arguments": {"reason": reason, "farewell_message": "Хорошо."}}


async def hangup_guard():
    # звонок 25.10: клиент говорил 3 с назад, прощания нет → отклонить
    s, out, _ = session(); s._last_user_voice_at = time.time() - 3
    await s._on_function_call(hangup())
    check("hangup: клиент говорил 3 с назад → отклонено и модель продолжает",
          s.hangup_rejections == 1 and "function_call.started" not in out and s.client.results[0][1]["status"] == "rejected")
    s, out, _ = session(); s._last_user_voice_at = time.time() - 8
    check("hangup: клиент молчит 8 с → разрешено", s._hangup_refusal(hangup()["arguments"]) is None)
    s, out, _ = session(); s._last_user_voice_at = time.time() - 1; s._farewell_at = time.time() - 1.2
    check("hangup: клиент попрощался → разрешено", s._hangup_refusal(hangup()["arguments"]) is None)
    s, out, _ = session(); s._farewell_at = time.time() - 5; s._last_user_voice_at = time.time() - 1
    check("hangup: прощание было, но клиент заговорил снова → отклонено", s._hangup_refusal(hangup()["arguments"]) is not None)
    s, out, _ = session(); s._last_user_voice_at = time.time() - 1
    check("hangup: просьба клиента (user_request) → разрешено", s._hangup_refusal(hangup("user_request")["arguments"]) is None)
    s, out, _ = session()
    check("hangup: клиент не говорил вовсе → разрешено", s._hangup_refusal(hangup()["arguments"]) is None)
    s, out, _ = session(phone=False); s._last_user_voice_at = time.time() - 1
    check("hangup: виджет не затронут", s._hangup_refusal(hangup()["arguments"]) is None)


async def decisions():
    ev = lambda intent, mood: {"type": "decision.event", "event": {"type": "decision.completed", "decision": {"answers": [
        {"name": "caller_intent", "choice": intent}, {"name": "caller_mood", "choice": mood}]}}}
    s, out, _ = session()
    await s._on_live_event(ev("none", "confused"))
    check("decisions: «запутался» с первого раза — без подсказки", len(s.client.thinking) == 0)
    await s._on_live_event(ev("none", "confused"))
    check("decisions: два подряд — подсказка, и она не велит переспрашивать", len(s.client.thinking) == 1 and "Не переспрашивай" in s.client.thinking[0])
    s, out, _ = session()
    await s._on_live_event(ev("none", "positive"))
    check("decisions: «доволен» подсказки не даёт", len(s.client.thinking) == 0)
    s, out, _ = session()
    await s._on_live_event(ev("call_back_later", "neutral"))
    check("decisions: «занят» не велит прощаться, а подтверждать договорённость",
          "попрощайся" not in s.client.thinking[0].lower().replace("сам попрощается", "") and "подтверди договорённость" in s.client.thinking[0])
    s, out, _ = session()
    await s._on_live_event(ev("goodbye", "positive"))
    check("decisions: прощание → автозавершение ждёт тишины", s._farewell_at is not None)


async def diagnostics():
    cl = CallLogRecorder(channel="phone"); cl.latencies_ms.extend([900, 4200, 5100])
    s = h.LiveVoiceSession(websocket=None, assistant=types.SimpleNamespace(), client=FC(), db=None, client_id="c", call_log=cl)
    s.voiced_bytes = int(130 * 8000 * 2); s.function_times = [("search_knowledge_base", 3.4)] * 3
    s.repeats = {"понял": 10}; s.errors = 1; s.close_seconds = 15.0
    s._log_diagnostics(200)
    texts = " | ".join(e["text"] for e in cl.events)
    for needle, label in [("медленнее 3 с", "медленные ответы"), ("монолог", "монолог"), ("вызвана 3 раза", "повторные функции"),
                          ("«понял» повторено 10", "слово-паразит"), ("ошибок GPT-Live", "ошибки"), ("закрытие сессии", "закрытие")]:
        check(f"диагностика: {label}", needle in texts)
    cl2 = CallLogRecorder(channel="phone"); cl2.latencies_ms.extend([900, 1200])
    s2 = h.LiveVoiceSession(websocket=None, assistant=types.SimpleNamespace(), client=FC(), db=None, client_id="c", call_log=cl2)
    s2.voiced_bytes = int(60 * 8000 * 2); s2._log_diagnostics(200)
    check("диагностика: здоровый звонок → «замечаний нет»", any("замечаний нет" in e["text"] for e in cl2.events))

    s3, _, cl3 = session()
    for t in ["Түшүндүм.", "Ага, понял. Сейчас прикинем. Понял.", "Дайте проверю."]:
        s3._log_buf["assistant"] = t; s3._flush_log("assistant")
    check("повторы: русское и кыргызское «понял», «дайте проверю»",
          s3.repeats.get("понял") == 2 and s3.repeats.get("түшүндүм/түшүнүктүү") == 1 and s3.repeats.get("сейчас уточню/посмотрю", 0) >= 2, str(s3.repeats))


async def voiced():
    silence = base64.b64encode(b"\x00\x00" * 480).decode()
    voice = base64.b64encode(b"".join(struct.pack("<h", int(3000 * math.sin(i / 5))) for i in range(480))).decode()
    s, out, _ = session(); s.client.io_rate = 24000
    await s._on_live_event({"type": "session.output_audio.delta", "delta": silence})
    check("речь по громкости: тишина не делает агента «говорящим»", not s.assistant_speaking)
    await s._on_live_event({"type": "session.output_audio.delta", "delta": voice})
    check("речь по громкости: голос → assistant.speech.started", s.assistant_speaking and "assistant.speech.started" in out)
    s.stop_event.set(); out.clear()
    await s._on_live_event({"type": "session.output_audio.delta", "delta": voice})
    check("после остановки звонка звук игнорируется", out == [])


def prompt_budget():
    blk = "# ЗНАНИЯ\n" + "В: цена? О: 5000 сом.\n" * 400
    avc.build_voice_knowledge_block = lambda db, a, budget=0: (blk[:9000], {"knowledge": 9000, "total": 9000})
    real_limit = lc.LIVE_VOICE_INSTRUCTIONS_MAX_CHARS
    check("бюджет: настроенный лимит промпта голоса не меньше 20000 (в Render не должна стоять старая "
          "переменная LIVE_VOICE_INSTRUCTIONS_MAX_CHARS)", real_limit >= 20000, f"сейчас {real_limit}")
    # Реальный случай звонка 25.10: промпт карточки 14616 + контекст 1101 при НАСТРОЕННОМ лимите
    a = types.SimpleNamespace(system_prompt="П" * 14616)
    p, st = avc.build_call_system_prompt(None, a, object(), call_context="К" * 1101, max_chars=real_limit)
    check("бюджет: звонок 25.10 (карточка 14616 + контекст 1101) при настроенном лимите — знания в промпте",
          "5000 сом" in p and st.get("total", 0) >= avc.MIN_CALL_KNOWLEDGE and not st.get("dropped"),
          f"лимит {real_limit}, знания {st.get('total', 0)}, {st}")
    for base_n, ctx_n in [(10000, 6000), (15000, 5000), (20000, 5000)]:
        a = types.SimpleNamespace(system_prompt="П" * base_n)
        p, st = avc.build_call_system_prompt(None, a, object(), call_context="К" * ctx_n, max_chars=24000)
        check(f"бюджет: карточка {base_n} + контекст {ctx_n} при лимите 24000 → знания в промпте и всё влезло",
              "5000 сом" in p and len(p) <= 24000 and not st.get("dropped"), f"len={len(p)} {st}")
    total = 24000 + len(lc.VOICE_DELEGATION_HINT) + len(lc.VOICE_TELEPHONY_HINT)
    check("бюджет: худший случай укладывается в 16 384 токена OpenAI (≈2,6 симв./токен)", total / 2.6 < 16384 * 0.9, f"{total} симв. ≈ {int(total / 2.6)} токенов")


def session_config():
    a = types.SimpleNamespace(id="x", system_prompt="Ты Алия.", functions={"enabled_functions": ["hangup_call"]},
                              voice="marin", greeting_message=None, live_phone_g711=True)
    opts = h.live_media_options(a)
    c = lc.OpenAILiveClient("k", a, "cid", telephony=True, greeting="  Салам!  ", **opts)
    cfg = c.build_session_config()
    check("конфиг: телефон → G.711 μ-law 8 кГц", cfg["audio"]["format"] == {"type": "audio/pcmu", "rate": 8000})
    check("конфиг: триггеры Decisions переданы", [t["event_type"] for t in cfg.get("triggers", [])] == ["session.input_transcript.delta"])
    check("конфиг: приветствие — developer-сообщение в session.input", cfg["input"][0]["role"] == "developer" and "Салам!" in cfg["input"][0]["content"][0]["text"])
    check("конфиг: параллельные функции включены", cfg["delegation"]["responses"].get("parallel_tool_calls") is True)
    w = lc.OpenAILiveClient("k", types.SimpleNamespace(**{**a.__dict__, "live_phone_g711": False}), "cid", **h.live_media_options(types.SimpleNamespace()))
    check("конфиг: виджет остаётся на PCM 24 кГц", w.build_session_config()["audio"]["format"]["rate"] == 24000)
    hint = lc.VOICE_DELEGATION_HINT
    for needle, label in [("МАТЕРИАЛЫ", "правило про отправку КП после звонка"), ("НЕ ПЕРЕСПРАШИВАЙ", "не переспрашивать"),
                          ("ДОГОВОРЁННОСТЬ", "подтверждать договорённость"), ("НЕ говори «понял»", "запрет «понял»"),
                          ("Түшүндүм", "кыргызское «понял» в списке запрещённого")]:
        check(f"подсказка голосу: {label}", needle in hint)


async def main():
    await hangup_guard()
    await decisions()
    await diagnostics()
    await voiced()
    prompt_budget()
    session_config()


asyncio.run(main())
print("\nПРОВАЛОВ:", failures)
