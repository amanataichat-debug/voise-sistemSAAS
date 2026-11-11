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
from backend.websockets.voice_rules import build_voice_rules
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
    s._caller_farewell_at = s._farewell_at
    check("hangup: клиент попрощался → разрешено", s._hangup_refusal(hangup()["arguments"]) is None)
    # 07.11.2026 (d08c06f4): агент сам сказал «до свидания» и положил трубку жалующемуся клиенту
    s, out, _ = session(); s._last_user_voice_at = time.time() - 1; s._farewell_at = time.time() - 0.5
    check("hangup: попрощался только агент, клиент говорил секунду назад → отклонено",
          s._hangup_refusal(hangup()["arguments"]) is not None)
    s, out, _ = session(); s._farewell_at = time.time() - 5; s._last_user_voice_at = time.time() - 1
    check("hangup: прощание было, но клиент заговорил снова → отклонено", s._hangup_refusal(hangup()["arguments"]) is not None)
    s, out, _ = session(); s._last_user_voice_at = time.time() - 1
    s.transcript.add("user", "Нет, спасибо, мне не интересно, не звоните больше", 0, 1)
    check("hangup: user_request и клиент правда просит закончить → разрешено",
          s._hangup_refusal(hangup("user_request")["arguments"]) is None)
    s, out, _ = session(); s._last_user_voice_at = time.time() - 1
    s.transcript.add("user", "Сен соны айтпасың, тастаймын деп айтпадың, айтпайсың", 0, 1)
    check("hangup: user_request, а клиент жалуется «ты не объясняешь» → отклонено (звонок d08c06f4)",
          s._hangup_refusal(hangup("user_request")["arguments"]) is not None)
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
    check("decisions: «занят» не велит прощаться, а коротко принять договорённость без пересказа (07.11.2026)",
          "попрощайся" not in s.client.thinking[0].lower().replace("сам попрощается", "")
          and "договорились" in s.client.thinking[0] and "без пересказа" in s.client.thinking[0])
    s, out, _ = session()
    await s._on_live_event(ev("goodbye", "positive"))
    check("decisions: прощание → автозавершение ждёт тишины", s._farewell_at is not None)

    # живые эмоции на лету (владелец 27.10.2026): шутка / проблема / хорошая новость клиента
    cue = lambda c: {"type": "decision.event", "event": {"type": "decision.completed", "decision": {"answers": [
        {"name": "caller_intent", "choice": "none"}, {"name": "caller_cue", "choice": c},
        {"name": "caller_mood", "choice": "neutral"}]}}}
    s, out, cl = session()
    await s._on_live_event(cue("joke"))
    check("эмоции: клиент пошутил → подсказка посмеяться вместе", len(s.client.thinking) == 1 and "посмейся" in s.client.thinking[0])
    await s._on_live_event(cue("joke"))
    check("эмоции: вторая шутка сразу — без повторной подсказки", len(s.client.thinking) == 1)
    s._cue_hint_at["joke"] -= 35
    await s._on_live_event(cue("problem"))
    check("эмоции: проблема клиента → сочувствие, без шуток", len(s.client.thinking) == 2 and "посочувствуй" in s.client.thinking[1]
          and "без шуток" in s.client.thinking[1])
    check("эмоции: реакция записана в журнал звонка", any("Decisions) — подсказка живой реакции" in e["text"] for e in cl.events))
    s, out, _ = session(); s._farewell_at = time.time()
    await s._on_live_event(cue("good_news"))
    check("эмоции: после прощания подсказок нет", len(s.client.thinking) == 0)
    # звонок 27.10.2026 (f7cc4b4c): «тише/медленнее» в подсказках проседало голос, скачки тона
    RULES = build_voice_rules("gpt_live", telephony=True)
    hints_all = " ".join(h.CUE_HINTS.values()) + " " + RULES
    check("стабильный голос: нигде не просим говорить тише/медленнее/бодрее",
          not any(w in hints_all for w in ("медленнее и тише", "говори тише", "бодрее", "слышимый вдох")))
    check("стабильный голос: правило одной громкости и темпа на весь звонок",
          "одним и тем же голосом" in RULES and "не шепчешь" in RULES)
    check("эмоции: реакция — после того, как клиент договорит", all("договорит" in v for v in h.CUE_HINTS.values()))
    s, out, _ = session()
    for i, c in enumerate(["joke", "problem", "good_news", "joke"]):
        s._cue_hint_at = {k: v - 100 for k, v in s._cue_hint_at.items()}
        await s._on_live_event(cue(c))
    check("эмоции: не больше 3 подсказок за звонок", len(s.client.thinking) == 3, str(len(s.client.thinking)))
    check("материалы: не говорить «уже отправил» (голос и бэкенд)",
          "«уже отправил»" in RULES and "никогда «уже отправил»" in lc.BACKEND_SYSTEM_PROMPT)
    names = [q["name"] for q in lc.CALL_DECISION_TRIGGER["decisions"]["questions"]]
    check("эмоции: Decisions спрашивает caller_cue (joke/problem/good_news)", "caller_cue" in names)
    check("эмоции: словами, а не громкостью; смех и вздохи вслух не изображать (шум по линии 8 кГц)",
          "Эмоции — словами" in RULES and "вслух не смеёшься" in RULES)


async def diagnostics():
    cl = CallLogRecorder(channel="phone"); cl.latencies_ms.extend([900, 4200, 5100])
    s = h.LiveVoiceSession(websocket=None, assistant=types.SimpleNamespace(), client=FC(), db=None, client_id="c", call_log=cl)
    s.voiced_bytes = int(130 * 8000 * 2); s.function_times = [("search_knowledge_base", 3.4)] * 3
    s.repeats = {"понял": 10}; s.errors = 1; s.close_seconds = 15.0
    s._log_diagnostics(200)
    texts = " | ".join(e["text"] for e in cl.events)
    for needle, label in [("медленнее 2,5 с", "медленные ответы"), ("монолог", "монолог"), ("вызвана 3 раза", "повторные функции"),
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

    # 07.11.2026 (звонок 5bf12c2d): журнал считал задержку от прихода транскрипта (≈0,8 с), а клиент ждал 2–3 с.
    # Теперь — от последнего громкого куска входящего звука, пока ассистент молчит.
    s, out, cl = session(); s.client.io_rate = 24000
    for _ in range(5):
        s._track_input_level(silence)
    s._track_input_level(voice)
    check("задержка по звуку: голос клиента над шумом линии отмечен", s._last_user_audio_at > 0)
    s._last_user_audio_at = time.time() - 2.0
    s._user_spoke_at = time.time() - 0.6
    await s._on_live_event({"type": "session.output_audio.delta", "delta": voice})
    snd = cl.latencies_ms[-1] if cl.latencies_ms else 0
    check("задержка по звуку: в журнал идёт пауза от конца речи (~2 с), а не от транскрипта (~0,6 с)",
          1900 <= snd <= 2600 and any("по звуку линии" in e["text"] for e in cl.events), str(cl.latencies_ms))
    before = s._last_user_audio_at
    s._track_input_level(voice)
    check("задержка по звуку: пока ассистент говорит, вход (эхо) не считается речью клиента",
          s._last_user_audio_at == before)
    s.assistant_speaking = False; s._user_spoke_at = time.time() - 0.5
    s._reply_started_at = time.time()
    check("задержка по звуку: звук до прошлого ответа не годится — запасной замер по транскрипту",
          s._sound_gap_ms(time.time() + 0.1) is None)

    # 07.11.2026: «шумный, перебивает, сбивается и начинает заново» — пока агент говорит, тихий звук линии
    # (эхо громкой связи, шум улицы) в модель не идёт; громкая речь клиента проходит.
    def pcm(amp, n=160):
        return base64.b64encode(b"".join(struct.pack("<h", int(amp * math.sin(i / 3))) for i in range(n))).decode()
    loud, echo = pcm(9000), pcm(500)
    zero = base64.b64encode(b"\x00\x00" * 160).decode()
    g, _, gcl = session(); g.client.io_rate = 8000
    for _ in range(10):
        g._track_input_level(pcm(40)); g._gate_input(pcm(40), 0.02)   # тишина линии, агент молчит
    for _ in range(5):
        g._gate_input(loud, 0.02)                                       # клиент говорит
    check("эхо-ворота: пока агент молчит, звук идёт как есть", g._gate_input(echo, 0.02) == echo)
    g.assistant_speaking = True; g._last_audio_at = time.time(); g.echo_gate.open_until = 0.0
    check("эхо-ворота: агент говорит — тихое эхо/шум заменяются тишиной", g._gate_input(echo, 0.02) == zero)
    check("эхо-ворота: громкая речь клиента поверх агента проходит (можно перебить)", g._gate_input(loud, 0.02) == loud)
    check("эхо-ворота: хвост слова после громкого куска проходит", g._gate_input(echo, 0.02) == echo)
    g.echo_gate.open_until = 0.0; g.assistant_speaking = False; g._last_audio_at = time.time() - 0.3
    check("эхо-ворота: ещё 0,8 с после речи агента эхо глушится", g._gate_input(echo, 0.02) == zero)
    g._last_audio_at = time.time() - 2.0
    check("эхо-ворота: потом снова всё проходит", g._gate_input(echo, 0.02) == echo)
    w, _, _ = session(phone=False); w.assistant_speaking = True; w._last_audio_at = time.time()
    check("эхо-ворота: в виджете не работают (там браузерное шумоподавление)", w._gate_input(echo, 0.02) == echo)
    q, _, _ = session(); q.client.io_rate = 8000
    for _ in range(10):
        q._track_input_level(pcm(20)); q._gate_input(pcm(20), 0.02)
    for _ in range(5):
        q._gate_input(pcm(560), 0.02)                                   # тихий клиент: речь ≈ −42 dBFS
    q.assistant_speaking = True; q._last_audio_at = time.time()
    check("эхо-ворота: тихий клиент (−42 dBFS) всё равно может перебить агента", q._gate_input(pcm(560), 0.02) == pcm(560))
    q.echo_gate.open_until = 0.0
    check("эхо-ворота: у тихого клиента эхо втрое тише его речи глушится", q._gate_input(pcm(120), 0.02) != pcm(120))
    from backend.websockets.echo_gate import LineEchoGate
    eg = LineEchoGate()
    raw = lambda amp: base64.b64decode(pcm(amp))
    for _ in range(10):
        eg.process(raw(40), False, 0.02)
    for _ in range(250):                                            # клиент говорит 5 секунд подряд
        eg.process(raw(4000), False, 0.02)
    check("эхо-ворота: долгая речь клиента не поднимает «пол» шума — его речь поверх агента проходит",
          eg.process(raw(4000), True, 0.02) == raw(4000) and (eg.floor or 0) < 100, f"floor={eg.floor}")
    eg2 = LineEchoGate()
    eg2.process(raw(9000), False, 0.02)
    check("эхо-ворота: громкое «алло» первым кадром не задирает «пол»", (eg2.floor or 0) <= 250, f"floor={eg2.floor}")
    g.echo_gate.gated_seconds = 3.0; g.reply_durations = [4.0, 18.5]; g.call_log = gcl
    g._log_diagnostics(60)
    texts = " | ".join(e["text"] for e in gcl.events)
    check("диагностика: монолог дольше 15 с и подавленный шум линии — в журнале",
          "длиннее 15 с" in texts and "шум и эхо линии" in texts, texts[-300:])


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
    rules = build_voice_rules("gpt_live", telephony=True, functions=[{"name": "hangup_call", "description": "Завершить звонок."}])
    for needle, label in [("Delegation policy", "метка делегирования OpenAI"), ("Interruption policy", "метка перебивания OpenAI"),
                          ("hangup_call", "прощание + hangup_call в том же ходе"), ("Жакшы калыңыз", "кыргызский пример"),
                          ("выбрал время или канал", "выбор канала не делегировать"), ("Backend tools", "список функций бэкенда")]:
        check(f"правила голоса: {label}", needle in rules)
    check("телефон: короче и переспрашивать; на виджете телефонного правила нет",
          "телефонный звонок" in rules and "телефонный звонок" not in build_voice_rules("gpt_live"))
    a = types.SimpleNamespace(id="x", system_prompt="Ты Анри.", functions={"enabled_functions": ["hangup_call"]},
                              voice="cedar", greeting_message=None)
    cfg = lc.OpenAILiveClient("k", a, "cid", telephony=True).build_session_config()
    check("конфиг: Backend tools и род голоса — в инструкции голосового слоя",
          "Backend tools" in cfg["instructions"] and "hangup_call" in cfg["instructions"] and "мужском роде" in cfg["instructions"])


def greeting_personal():
    from datetime import datetime
    import backend.services.sip_gateway_service as sgs
    ev, day, night = datetime(2026, 10, 27, 19, 40), datetime(2026, 10, 27, 13, 0), datetime(2026, 10, 27, 2, 0)
    p = sgs.personalize_greeting
    check("приветствие: вечер + имя", p("Здравствуйте. Чем могу помочь?", "Айбек", ev) == "Добрый вечер, Айбек. Чем могу помочь?",
          p("Здравствуйте. Чем могу помочь?", "Айбек", ev))
    check("приветствие: «Добрый день» владельца днём без имени — как было",
          p("Добрый день! Это Анри из Voksy.", "", day) == "Добрый день! Это Анри из Voksy.")
    check("приветствие: «Добрый день» вечером → «Добрый вечер»", p("Добрый день! Это Анри.", "", ev) == "Добрый вечер! Это Анри.")
    check("приветствие: ночью — «Здравствуйте»", p("Добрый вечер! Слушаю вас.", "", night).startswith("Здравствуйте!"))
    check("приветствие: имя уже во фразе — не дублируем",
          p("Здравствуйте, Айгуль! Это Анри.", "Айгуль", ev) == "Добрый вечер, Айгуль! Это Анри.")
    check("приветствие: кыргызское «Саламатсызбы» не меняем, добавляем имя",
          p("Саламатсызбы! Сизге кантип жардам берсем болот?", "Айбек", ev) == "Саламатсызбы, Айбек! Сизге кантип жардам берсем болот?")
    check("приветствие: фраза без приветствия в начале — как есть", p("Компания Voksy, слушаю вас.", "Айбек", ev) == "Компания Voksy, слушаю вас.")
    check("имя: «Клиент 0700…» и компания — не имя, «Айбек Осмонов» → «Айбек»",
          sgs.first_name("Клиент 0700123456") == "" and sgs.first_name("ООО Ромашка") == "" and sgs.first_name("Айбек Осмонов") == "Айбек")
    check("имя: ник из мессенджера («VeTeR», «AIBEK») — не имя, «Анна-Мария» и «Aibek» — имя (07.11.2026)",
          sgs.first_name("VeTeR") == "" and sgs.first_name("AIBEK") == "" and sgs.first_name("Анна-Мария") == "Анна-Мария"
          and sgs.first_name("Aibek") == "Aibek")
    call = types.SimpleNamespace(direction="inbound", caller="996700000000", call_metadata={})
    check("контекст звонка: время Бишкека и время суток", "Сейчас в Бишкеке" in sgs.SipGatewayService.call_context_text(call))
    rules = build_voice_rules("gpt_live", telephony=True)
    check("правила голоса: диалог — одна-две фразы за ход, объяснение кусками, без слышимого дыхания",
          "одна-две короткие фразы" in rules and "три-четыре коротких фразы" in rules and "не вздыхаешь" in rules)
    check("правила голоса: перебили — замолчи, поддакивание — договорить, не вступать на паузе",
          "замолчи и выслушай" in rules and "Backchannel policy" in rules and "посреди его фразы" in rules)
    check("правила голоса: обращение по имени два-три раза", "по имени два-три раза" in rules)


def prompt_budget():
    blk = "# ЗНАНИЯ\n" + "В: цена? О: 5000 сом.\n" * 400
    avc.build_voice_knowledge_block = lambda db, a, budget=0: (blk[:9000], {"knowledge": 9000, "total": 9000})
    real_limit = lc.LIVE_VOICE_INSTRUCTIONS_MAX_CHARS
    check("бюджет: настроенный лимит промпта голоса не меньше 20000 (в Render не должна стоять старая "
          "переменная LIVE_VOICE_INSTRUCTIONS_MAX_CHARS)", real_limit >= 20000, f"сейчас {real_limit}")
    # Реальный случай звонка 25.10: промпт карточки 14616 + контекст 1101 при НАСТРОЕННОМ лимите.
    # Промпт карточки агента собирается из кода: база + «Инструкции для звонков» владельца.
    a = types.SimpleNamespace(system_prompt="устаревшая копия в БД")
    ag = types.SimpleNamespace(voice_additional_instructions="П" * 14000)
    p, st = avc.build_call_system_prompt(None, a, ag, call_context="К" * 1101, max_chars=real_limit)
    check("промпт звонка агента собран из кода, а не из копии в БД",
          "устаревшая копия" not in p and "голосовой агент компании" in p)
    check("бюджет: звонок 25.10 (карточка 14616 + контекст 1101) при настроенном лимите — знания в промпте",
          "5000 сом" in p and st.get("total", 0) >= avc.MIN_CALL_KNOWLEDGE and not st.get("dropped"),
          f"лимит {real_limit}, знания {st.get('total', 0)}, {st}")
    for base_n, ctx_n in [(10000, 6000), (15000, 5000), (20000, 5000)]:
        ag = types.SimpleNamespace(voice_additional_instructions="П" * (base_n - 600))
        p, st = avc.build_call_system_prompt(None, types.SimpleNamespace(), ag, call_context="К" * ctx_n, max_chars=24000)
        check(f"бюджет: карточка {base_n} + контекст {ctx_n} при лимите 24000 → знания в промпте и всё влезло",
              "5000 сом" in p and len(p) <= 24000 and not st.get("dropped"), f"len={len(p)} {st}")
    # 07.11.2026: считаем и головы инструкций (произношение + «Живой разговор»), они тоже идут голосовому слою
    total = 24000 + len(build_voice_rules("gpt_live", telephony=True, voice="marin"))
    check("бюджет: худший случай укладывается в 16 384 токена OpenAI (≈2,6 симв./токен, запас 5%)",
          total / 2.6 < 16384 * 0.95, f"{total} симв. ≈ {int(total / 2.6)} токенов")


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
    ins = cfg["instructions"]
    check("конфиг: карточка первой, за ней общие правила голоса и кыргызский блок",
          ins.startswith("Ты Алия.") and "КАК РАЗГОВАРИВАТЬ" in ins and "КЫРГЫЗЧА СҮЙЛӨӨ" in ins)
    check("конфиг: язык начала — по приветствию («Салам!» → кыргызский)", "на кыргызском" in ins)
    check("конфиг: правила не дублируются (одна копия завершения и материалов)",
          ins.count("hangup_call") <= 3 and ins.count("Материалы:") == 1)
    vm = lc.OpenAILiveClient("k", types.SimpleNamespace(system_prompt="Произнеси текст.", functions=None, voice="marin"),
                             "cid", conversation_rules=False).build_session_config()
    check("озвучка голосового сообщения: без правил звонка", vm["instructions"] == "Произнеси текст.")


async def main():
    await hangup_guard()
    await decisions()
    await diagnostics()
    await voiced()
    await farewell()
    phone_voice()
    voice_hints()
    greeting_personal()
    prompt_budget()
    session_config()


asyncio.run(main())
print("\nПРОВАЛОВ:", failures)
