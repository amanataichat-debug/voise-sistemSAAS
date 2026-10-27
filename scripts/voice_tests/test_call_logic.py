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


async def farewell():
    # звонок 27.10.2026 (f50d0b4e): «Да, всё, спасибо, хорошо» → «Тогда на этом всё, спасибо за звонок!»,
    # hangup_call не вызван, линия висела 35 с
    check("прощание: «Тогда на этом всё, спасибо за звонок!» — закрывающая фраза ассистента",
          h.is_assistant_closing("Тогда на этом всё, спасибо за звонок!"))
    check("прощание: «спасибо за звонок в …! Чем помочь?» — не прощание",
          not h.is_assistant_closing("Спасибо за звонок в Voksy! Чем могу помочь?"))
    check("прощание: кыргызское «Чалганыңыз үчүн рахмат, жакшы калыңыз!»",
          h.is_assistant_closing("Чалганыңыз үчүн рахмат, жакшы калыңыз!"))
    check("прощание: клиент «Да, всё, спасибо, хорошо» закончил разговор", h.is_caller_closing("Да, всё, спасибо, хорошо"))
    check("прощание: «спасибо, а сколько стоит доставка?» — не конец", not h.is_caller_closing("спасибо, а сколько стоит доставка?"))

    now = time.time()
    s, out, _ = session()
    s._last_user_voice_at = now - 12
    s._log_transcript("assistant", "Тогда на этом всё, спасибо за звонок!")
    check("автозавершение: прощание ассистента зафиксировано", s._farewell_at is not None)
    s._farewell_at = now - 6; s._last_audio_at = now - 3
    check("автозавершение: прощание + 4 с общей тишины → кладём трубку", s._should_auto_hangup(now))
    s._last_audio_at = now - 0.5
    check("автозавершение: ассистент ещё говорит → ждём", not s._should_auto_hangup(now))

    s, out, _ = session()
    s._log_transcript("assistant", "Спасибо за звонок в нашу компанию!")
    check("автозавершение: «спасибо за звонок» в приветствии (клиент ещё молчал) не считается", s._farewell_at is None)

    s, out, _ = session()
    s._last_user_voice_at = now - 1
    s._log_transcript("user", "Хорошо, спасибо")
    check("автозавершение: «хорошо, спасибо» клиента — ждём тишины", s._farewell_at is not None)
    s._log_transcript("assistant", "А вам удобно, если завтра в десять я пришлю договор?")
    check("автозавершение: ассистент продолжил разговор вопросом → прощание снято", s._farewell_at is None)


def phone_voice():
    import numpy as np
    import backend.websockets.sip_media_adapter as sma
    t = np.arange(8000) / 8000.0
    def level(freq, fx):
        x = (np.sin(2 * np.pi * freq * t) * 8000).astype(np.int16).tobytes()
        y = b"".join(fx(x[i:i + 320]) for i in range(0, len(x), 320))
        a = np.frombuffer(y, dtype=np.int16)[2000:].astype(float)
        return 20 * np.log10(np.sqrt((a ** 2).mean()) / (8000 / np.sqrt(2)))
    low, mid = level(90, sma.PhoneVoice(highpass_hz=200, gain_db=0)), level(1000, sma.PhoneVoice(highpass_hz=200, gain_db=0))
    check("звук в линию: бас 90 Гц срезан (≥ 15 дБ), 1 кГц не тронут (±1 дБ)", low <= -15 and abs(mid) <= 1.0,
          f"90 Гц {low:.1f} дБ, 1 кГц {mid:.1f} дБ")
    g = level(1000, sma.PhoneVoice(highpass_hz=0, gain_db=-3))
    check("звук в линию: усиление −3 дБ", abs(g + 3) <= 0.3, f"{g:.1f} дБ")
    full = (np.sin(2 * np.pi * 400 * t) * 32000).astype(np.int16).tobytes()
    peak = np.abs(np.frombuffer(sma.PhoneVoice(highpass_hz=0, gain_db=6)(full), dtype=np.int16)).max()
    check("звук в линию: ограничитель не даёт клиппинга при +6 дБ", peak < 32767, str(peak))
    check("звук в линию: по умолчанию включено (срез НЧ + тише)",
          sma.PhoneVoice().enabled and sma.settings.SIP_OUT_GAIN_DB < 0 and sma.settings.SIP_OUT_HIGHPASS_HZ > 0)


def voice_hints():
    check("подсказка: мужской голос cedar → мужской род", "мужском роде" in lc.voice_gender_hint("cedar"))
    check("подсказка: marin → женский род", "женском роде" in lc.voice_gender_hint("marin"))
    check("подсказка: alloy — без подсказки рода", lc.voice_gender_hint("alloy") == "")
    hint = lc.VOICE_DELEGATION_HINT
    for needle, label in [("Delegation policy", "метка делегирования OpenAI"), ("Interruption policy", "метка перебивания OpenAI"),
                          ("ЗАВЕРШЕНИЕ", "прощание + hangup_call в том же ходе"), ("Чалганыңыз үчүн рахмат", "кыргызские образцы"),
                          ("давайте в Телеграм", "выбор канала не делегировать")]:
        check(f"подсказка голосу: {label}", needle in hint)
    check("подсказка звонка: не предлагать «созвониться» на звонке и не выдумывать «помню»",
          "созвониться" in lc.VOICE_TELEPHONY_HINT and "помню" in lc.VOICE_TELEPHONY_HINT)
    a = types.SimpleNamespace(id="x", system_prompt="Ты Анри.", functions={"enabled_functions": ["hangup_call"]},
                              voice="cedar", greeting_message=None)
    cfg = lc.OpenAILiveClient("k", a, "cid", telephony=True).build_session_config()
    check("конфиг: Backend tools и род голоса — в инструкции голосового слоя",
          "Backend tools" in cfg["instructions"] and "hangup_call" in cfg["instructions"] and "мужском роде" in cfg["instructions"])


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
    await farewell()
    phone_voice()
    voice_hints()
    prompt_budget()
    session_config()


asyncio.run(main())
print("\nПРОВАЛОВ:", failures)
