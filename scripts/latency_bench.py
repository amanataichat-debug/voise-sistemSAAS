#!/usr/bin/env python3
"""
Замер задержки каскада Eleven-ассистента: диалоговая модель и озвучка.

Запуск на Render (Shell сервиса, там лежат ключи):
    cd /opt/render/project/src
    python scripts/latency_bench.py --assistant <uuid Eleven-ассистента>

Что меряет:
  * LLM — время до первого текста ответа (и до конца ответа) на промпте и функциях
    ассистента, фразы — последние реплики клиентов из eleven_conversations:
      realtime:<model>    OpenAI Realtime в текстовом режиме (как сейчас в проде)
      openai:<model>      OpenAI Chat Completions, потоковый ответ
      openrouter:<model>  OpenRouter, провайдер с наименьшей задержкой (provider.sort=latency)
  * TTS — время от отправки текста до первого звука:
      eleven_v3_conversational через Text-to-Dialogue WebSocket (как сейчас в проде)
      eleven_v4_turbo / eleven_v4 через обычный stream-input WebSocket и HTTP-стрим

Первый запрос каждой модели — прогрев (не считается): в проде соединение уже открыто.

Результат: таблицы в консоли + отчёт (report.txt, index.html с плеерами озвучки и
ответами моделей) в R2: bench/<время>/..., ссылки печатаются в конце.

Ключи из окружения: OPENAI_API_KEY, OPENROUTER_API_KEY, ELEVENLABS_API_KEY (+ R2_* для отчёта).
"""

import argparse
import asyncio
import base64
import html
import io
import json
import os
import statistics
import sys
import time
import uuid
import wave
from datetime import datetime
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import httpx  # noqa: E402
import websockets  # noqa: E402

DEFAULT_LLMS = [
    "realtime:gpt-realtime-2",
    "openai:gpt-4o-mini",
    "openai:gpt-4.1-nano",
    "openai:gpt-4.1-mini",
    "openai:gpt-5.4-nano",
    "openai:gpt-5.4-mini",
    "openrouter:openai/gpt-oss-120b",
    "openrouter:openai/gpt-oss-20b",
    "openrouter:google/gemini-3.8-flash",
]
DEFAULT_TTS = ["eleven_v3_conversational", "eleven_v4_turbo", "eleven_v4"]
FALLBACK_PHRASES = [
    "Алло, саламатсызбы?",
    "Бүгүнкү күнү пайыз канча болот?",
    "Voksy AI туташтыруу канча турат?",
    "Эртең саат алтыда чалып коёсуздарбы?",
    "Может на русском? Сколько стоит подключение?",
]
RUSSIAN_TTS_TEXT = "Да, могу по-русски. Подскажите, чем могу помочь?"
MAX_TOKENS = 300
PCM_RATE = 24000


def log(msg: str) -> None:
    print(msg, flush=True)


# ============================================================================ контекст ассистента
def load_context(assistant_id: Optional[str], n_phrases: int) -> Dict[str, Any]:
    # Пакет backend.core первым: иначе цикл импорта config → core/__init__ → scheduler → db.session
    # (в приложении этот порядок задаёт app.py).
    import backend.core  # noqa: F401
    from backend.db.session import SessionLocal
    from backend.models.eleven_assistant import (
        DEFAULT_ELEVEN_GREETING, ElevenAssistantConfig, ElevenConversation,
    )
    from backend.websockets.fish_llm_client import LANGUAGE_INSTRUCTIONS
    from backend.websockets.openai_client_new import normalize_functions

    db = SessionLocal()
    try:
        q = db.query(ElevenAssistantConfig)
        if assistant_id:
            a = q.filter(ElevenAssistantConfig.id == uuid.UUID(assistant_id)).first()
        else:
            last = (db.query(ElevenConversation.assistant_id)
                    .order_by(ElevenConversation.created_at.desc()).first())
            a = q.filter(ElevenAssistantConfig.id == last.assistant_id).first() if last else q.first()
        if a is None:
            raise SystemExit("Eleven-ассистент не найден (--assistant <uuid>)")

        instructions = a.system_prompt or "Ты вежливый голосовой помощник."
        lang = (a.language or "").strip().lower()[:2]
        if lang in LANGUAGE_INSTRUCTIONS:
            instructions += "\n\n" + LANGUAGE_INSTRUCTIONS[lang]
        tools = [{"name": f["name"], "description": f["description"], "parameters": f["parameters"]}
                 for f in normalize_functions(a.functions)]

        rows = (db.query(ElevenConversation.user_message, ElevenConversation.assistant_message)
                .filter(ElevenConversation.assistant_id == a.id)
                .order_by(ElevenConversation.created_at.desc()).limit(200).all())
        phrases, replies = [], []
        for r in rows:
            u = (r.user_message or "").strip()
            if 6 <= len(u) <= 200 and u not in phrases and not u.startswith("["):
                phrases.append(u)
            s = (r.assistant_message or "").strip()
            if s:
                first = s.split("\n")[0][:160]
                if len(first) >= 20 and first not in replies:
                    replies.append(first)
        phrases = phrases[:n_phrases] or FALLBACK_PHRASES[:n_phrases]
        return {
            "assistant_id": str(a.id), "name": a.name, "language": a.language or "ky",
            "voice_id": a.voice_id, "stability": a.stability if a.stability is not None else 0.5,
            "instructions": instructions, "tools": tools,
            "greeting": a.greeting_message or DEFAULT_ELEVEN_GREETING,
            "phrases": phrases,
            "tts_texts": (replies[:3] or [a.greeting_message or DEFAULT_ELEVEN_GREETING]) + [RUSSIAN_TTS_TEXT],
        }
    finally:
        db.close()


# ============================================================================ LLM: chat completions (OpenAI / OpenRouter)
async def chat_stream(client: httpx.AsyncClient, provider: str, model: str, ctx: Dict[str, Any],
                      phrase: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    if provider == "openai":
        url, key = "https://api.openai.com/v1/chat/completions", os.getenv("OPENAI_API_KEY")
    else:
        url, key = "https://openrouter.ai/api/v1/chat/completions", os.getenv("OPENROUTER_API_KEY")
    if not key:
        return {"error": f"нет ключа для {provider}"}
    body: Dict[str, Any] = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": ctx["instructions"]},
            {"role": "assistant", "content": ctx["greeting"]},
            {"role": "user", "content": phrase},
        ],
    }
    if ctx["tools"]:
        body["tools"] = [{"type": "function", "function": t} for t in ctx["tools"]]
        body["tool_choice"] = "auto"
    body.update(extra)
    headers = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        headers.update({"HTTP-Referer": "https://voksyai.online", "X-Title": "VoksiAI latency bench"})

    t0 = time.perf_counter()
    first_any = first_text = None
    text, tool_calls, served_by = "", [], None
    try:
        async with client.stream("POST", url, json=body, headers=headers, timeout=60) as resp:
            if resp.status_code != 200:
                err = (await resp.aread()).decode("utf-8", "replace")[:300]
                return {"error": f"HTTP {resp.status_code}: {err}", "status": resp.status_code}
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                served_by = served_by or chunk.get("provider")
                for ch in chunk.get("choices") or []:
                    d = ch.get("delta") or {}
                    now = time.perf_counter()
                    if (d.get("reasoning") or d.get("reasoning_content") or d.get("content")
                            or d.get("tool_calls")) and first_any is None:
                        first_any = now
                    if d.get("content"):
                        if first_text is None:
                            first_text = now
                        text += d["content"]
                    for tc in d.get("tool_calls") or []:
                        name = (tc.get("function") or {}).get("name")
                        if name:
                            tool_calls.append(name)
                            if first_text is None:
                                first_text = now  # ответ — вызов функции
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    end = time.perf_counter()
    return {
        "ttft_ms": int((first_text - t0) * 1000) if first_text else None,
        "first_token_ms": int((first_any - t0) * 1000) if first_any else None,
        "total_ms": int((end - t0) * 1000),
        "text": text.strip(), "tool_calls": tool_calls, "provider": served_by,
    }


def chat_variants(provider: str, model: str) -> List[Dict[str, Any]]:
    """Варианты параметров: пробуем по очереди, пока API не примет (400 — следующий)."""
    if provider == "openrouter":
        base = {"max_tokens": MAX_TOKENS, "provider": {"sort": "latency"}}
        if "gpt-oss" in model or "gpt-5" in model:
            return [{**base, "reasoning": {"effort": "low"}}, base]
        if "gemini" in model:
            # Flash по умолчанию «думает» — для голоса выключаем или ставим минимум
            # {"enabled": false} OpenRouter принял, но модель всё равно думала и съела лимит токенов
            return [{**base, "max_tokens": 1200, "reasoning": {"effort": "minimal"}},
                    {**base, "max_tokens": 1200, "reasoning": {"max_tokens": 0}}, {**base, "max_tokens": 1200}]
        return [base]
    if model.startswith(("gpt-5", "gpt-6", "gpt-7", "o")):
        base = {"max_completion_tokens": MAX_TOKENS}
        return [{**base, "reasoning_effort": e} for e in ("none", "minimal", "low")] + [base]
    return [{"max_completion_tokens": MAX_TOKENS}, {"max_tokens": MAX_TOKENS}]


# ============================================================================ LLM: OpenAI Realtime (как FishLLMClient)
async def realtime_turn(model: str, ctx: Dict[str, Any], phrase: str) -> Dict[str, Any]:
    key = os.getenv("OPENAI_API_KEY")
    try:
        ws = await websockets.connect(
            f"wss://api.openai.com/v1/realtime?model={model}",
            extra_headers=[("Authorization", f"Bearer {key}")], max_size=None,
        )
    except Exception as exc:
        return {"error": f"connect: {exc}"}
    try:
        tools = [{"type": "function", **t} for t in ctx["tools"]]
        await ws.send(json.dumps({"type": "session.update", "session": {
            "type": "realtime", "model": model, "output_modalities": ["text"],
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000}, "turn_detection": None}},
            "instructions": ctx["instructions"], "tools": tools,
            "tool_choice": "auto" if tools else "none", "max_output_tokens": MAX_TOKENS,
        }}))
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": ctx["greeting"]}]}}))
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": phrase}]}}))
        # дождаться, пока сессия настроена, чтобы не мерить её применение
        deadline = time.perf_counter() + 10
        while time.perf_counter() < deadline:
            ev = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if ev.get("type") == "session.updated":
                break
            if ev.get("type") == "error":
                return {"error": json.dumps(ev.get("error"), ensure_ascii=False)[:300]}
        t0 = time.perf_counter()
        await ws.send(json.dumps({"type": "response.create",
                                  "response": {"output_modalities": ["text"], "max_output_tokens": MAX_TOKENS}}))
        first = None
        text, tool_calls, usage = "", [], {}
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), 60))
            et = ev.get("type")
            if et == "response.output_text.delta":
                first = first or time.perf_counter()
                text += ev.get("delta") or ""
            elif et == "response.output_item.added" and (ev.get("item") or {}).get("type") == "function_call":
                first = first or time.perf_counter()
                tool_calls.append(ev["item"].get("name"))
            elif et == "response.done":
                usage = (ev.get("response") or {}).get("usage") or {}
                break
            elif et == "error":
                return {"error": json.dumps(ev.get("error"), ensure_ascii=False)[:300]}
        end = time.perf_counter()
        return {
            "ttft_ms": int((first - t0) * 1000) if first else None, "first_token_ms": None,
            "total_ms": int((end - t0) * 1000), "text": text.strip(), "tool_calls": tool_calls,
            "provider": "openai-realtime", "output_tokens": usage.get("output_tokens"),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        await ws.close()


async def discover(client: httpx.AsyncClient) -> List[str]:
    """Добавить свежие быстрые модели, если они есть в аккаунтах: OpenAI *luna*, Gemini Flash на OpenRouter."""
    found = []
    try:
        r = await client.get("https://api.openai.com/v1/models",
                             headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}, timeout=20)
        ids = sorted(m["id"] for m in r.json().get("data", []))
        luna = [i for i in ids if "luna" in i and "realtime" not in i and "audio" not in i]
        found += [f"openai:{i}" for i in luna[-2:]]
    except Exception as exc:
        log(f"  (список моделей OpenAI недоступен: {exc})")
    if os.getenv("OPENROUTER_API_KEY"):
        try:
            r = await client.get("https://openrouter.ai/api/v1/models", timeout=20)
            models = r.json().get("data", [])
            skip = ("image", "tts", "audio", "live", "transcribe", "embedding", ":")  # ":" — :free, :batch
            flash = sorted((m for m in models if "gemini" in m["id"] and "flash" in m["id"]
                            and not any(x in m["id"] for x in skip)),
                           key=lambda m: m.get("created") or 0, reverse=True)
            # самая свежая Flash и самая свежая Flash-Lite
            picked = [next((m for m in flash if "lite" not in m["id"]), None),
                      next((m for m in flash if "lite" in m["id"]), None)]
            found += [f"openrouter:{m['id']}" for m in picked if m]
        except Exception as exc:
            log(f"  (список моделей OpenRouter недоступен: {exc})")
    return found


async def bench_llm(spec: str, ctx: Dict[str, Any], rounds: int, client: httpx.AsyncClient) -> Dict[str, Any]:
    provider, model = spec.split(":", 1)
    runs: List[Dict[str, Any]] = []

    if provider == "realtime":
        async def one(phrase):
            return await realtime_turn(model, ctx, phrase)
    else:
        variants = chat_variants(provider, model)
        chosen: Dict[str, Any] = {}

        async def one(phrase):
            if "v" in chosen:
                return await chat_stream(client, provider, model, ctx, phrase, chosen["v"])
            last = None
            for v in variants:
                last = await chat_stream(client, provider, model, ctx, phrase, v)
                if last.get("status") == 400:
                    continue
                chosen["v"] = v
                return last
            return last

    warm = await one(ctx["phrases"][0])  # прогрев: соединение, выбор параметров
    if warm.get("error"):
        log(f"  {spec:45s} ОШИБКА: {warm['error']}")
        return {"spec": spec, "error": warm["error"], "runs": []}
    for _ in range(rounds):
        for phrase in ctx["phrases"]:
            r = await one(phrase)
            r["phrase"] = phrase
            runs.append(r)
    ok = [r["ttft_ms"] for r in runs if r.get("ttft_ms") is not None]
    res = {"spec": spec, "runs": runs,
           "ttft_med": int(statistics.median(ok)) if ok else None,
           "ttft_min": min(ok) if ok else None, "ttft_max": max(ok) if ok else None,
           "total_med": int(statistics.median([r["total_ms"] for r in runs if r.get("total_ms") is not None])) if ok else None,
           "errors": sum(1 for r in runs if r.get("error")),
           "provider": next((r.get("provider") for r in runs if r.get("provider")), None)}
    log(f"  {spec:45s} первый текст: медиана {res['ttft_med']} мс (мин {res['ttft_min']}, макс {res['ttft_max']}), "
        f"весь ответ {res['total_med']} мс, ошибок {res['errors']}"
        + (f", провайдер {res['provider']}" if res["provider"] and provider == "openrouter" else ""))
    return res


# ============================================================================ TTS
async def tts_ttd_v3(ctx: Dict[str, Any], text: str, model: str) -> Dict[str, Any]:
    """Как в проде: Text-to-Dialogue multi-stream-input, контекст → inputs + flush → close_context."""
    url = (f"wss://api.elevenlabs.io/v1/text-to-dialogue/multi-stream-input?model_id={model}"
           f"&output_format=pcm_{PCM_RATE}&language_code={ctx['language']}")
    ws = await websockets.connect(url, extra_headers={"xi-api-key": os.getenv("ELEVENLABS_API_KEY")}, max_size=None)
    try:
        cid = f"ctx_{uuid.uuid4().hex[:8]}"
        await ws.send(json.dumps({"context_id": cid, "voices": [ctx["voice_id"]],
                                  "voice_settings": {"stability": ctx["stability"]}}))
        t0 = time.perf_counter()
        await ws.send(json.dumps({"context_id": cid, "inputs": [{"text": text, "voice_id": ctx["voice_id"],
                                                                  "new_turn": True}]}))
        await ws.send(json.dumps({"context_id": cid, "flush": True}))
        await ws.send(json.dumps({"context_id": cid, "close_context": True}))
        return await _collect(ws, t0, final_keys=("is_final",))
    finally:
        await ws.close()


async def tts_stream_input(ctx: Dict[str, Any], text: str, model: str) -> Dict[str, Any]:
    """Обычный TTS WebSocket: {"text":" "} → текст + flush → {"text":""}."""
    url = (f"wss://api.elevenlabs.io/v1/text-to-speech/{ctx['voice_id']}/stream-input?model_id={model}"
           f"&output_format=pcm_{PCM_RATE}&language_code={ctx['language']}")
    ws = await websockets.connect(url, extra_headers={"xi-api-key": os.getenv("ELEVENLABS_API_KEY")}, max_size=None)
    try:
        await ws.send(json.dumps({"text": " ", "voice_settings": {"stability": ctx["stability"],
                                                                  "similarity_boost": 0.8}}))
        t0 = time.perf_counter()
        await ws.send(json.dumps({"text": text + " ", "flush": True}))
        await ws.send(json.dumps({"text": ""}))
        return await _collect(ws, t0, final_keys=("isFinal", "is_final"))
    finally:
        await ws.close()


async def _collect(ws, t0: float, final_keys) -> Dict[str, Any]:
    first, pcm = None, bytearray()
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), 30)
            msg = json.loads(raw)
            if msg.get("audio"):
                first = first or time.perf_counter()
                pcm.extend(base64.b64decode(msg["audio"]))
            if any(msg.get(k) for k in final_keys):
                break
            if msg.get("error") or (msg.get("message") and msg.get("code")):
                return {"error": json.dumps(msg, ensure_ascii=False)[:300]}
    except (asyncio.TimeoutError, websockets.ConnectionClosed):
        pass
    if first is None:
        return {"error": "звука нет"}
    return {"ttfa_ms": int((first - t0) * 1000), "total_ms": int((time.perf_counter() - t0) * 1000),
            "audio_sec": round(len(pcm) / (PCM_RATE * 2), 1), "pcm": bytes(pcm)}


async def tts_http(client: httpx.AsyncClient, ctx: Dict[str, Any], text: str, model: str) -> Dict[str, Any]:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ctx['voice_id']}/stream?output_format=pcm_{PCM_RATE}"
    body = {"text": text, "model_id": model, "language_code": ctx["language"],
            "voice_settings": {"stability": ctx["stability"], "similarity_boost": 0.8}}
    t0 = time.perf_counter()
    first, pcm = None, bytearray()
    async with client.stream("POST", url, json=body, headers={"xi-api-key": os.getenv("ELEVENLABS_API_KEY")},
                             timeout=60) as resp:
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {(await resp.aread()).decode('utf-8', 'replace')[:300]}"}
        async for chunk in resp.aiter_bytes():
            if chunk:
                first = first or time.perf_counter()
                pcm.extend(chunk)
    if first is None:
        return {"error": "звука нет"}
    return {"ttfa_ms": int((first - t0) * 1000), "total_ms": int((time.perf_counter() - t0) * 1000),
            "audio_sec": round(len(pcm) / (PCM_RATE * 2), 1), "pcm": bytes(pcm)}


async def bench_tts(label: str, fn, ctx: Dict[str, Any], rounds: int) -> Dict[str, Any]:
    runs = []
    try:
        await fn(ctx["tts_texts"][0])  # прогрев
    except Exception as exc:
        log(f"  {label:45s} ОШИБКА: {exc}")
        return {"label": label, "error": str(exc), "runs": []}
    for _ in range(rounds):
        for i, text in enumerate(ctx["tts_texts"]):
            try:
                r = await fn(text)
            except Exception as exc:
                r = {"error": f"{type(exc).__name__}: {exc}"}
            r.update({"text": text, "i": i})
            runs.append(r)
    ok = [r["ttfa_ms"] for r in runs if r.get("ttfa_ms") is not None]
    res = {"label": label, "runs": runs,
           "ttfa_med": int(statistics.median(ok)) if ok else None,
           "ttfa_min": min(ok) if ok else None, "ttfa_max": max(ok) if ok else None,
           "errors": sum(1 for r in runs if r.get("error"))}
    first_err = next((r["error"] for r in runs if r.get("error")), "")
    log(f"  {label:45s} первый звук: медиана {res['ttfa_med']} мс (мин {res['ttfa_min']}, макс {res['ttfa_max']}), "
        f"ошибок {res['errors']}" + (f" — {first_err[:120]}" if first_err else ""))
    return res


# ============================================================================ отчёт
def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(PCM_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def build_report(ctx, llm_res, tts_res, audio_links) -> (str, str):
    lines = [f"Замер задержки — {datetime.utcnow():%Y-%m-%d %H:%M} UTC, ассистент {ctx['name']} ({ctx['assistant_id']}), "
             f"язык {ctx['language']}", "", "LLM — время до первого текста (медиана / мин / макс), весь ответ:"]
    for r in sorted(llm_res, key=lambda r: r.get("ttft_med") or 10 ** 9):
        lines.append(f"  {r['spec']:45s} " + (f"ОШИБКА {r['error'][:150]}" if r.get("error") else
                     f"{r['ttft_med']} / {r['ttft_min']} / {r['ttft_max']} мс, весь ответ {r['total_med']} мс, "
                     f"ошибок {r['errors']}"))
    lines += ["", "TTS — время до первого звука (медиана / мин / макс):"]
    for r in sorted(tts_res, key=lambda r: r.get("ttfa_med") or 10 ** 9):
        lines.append(f"  {r['label']:45s} " + (f"ОШИБКА {r['error'][:150]}" if r.get("error") else
                     f"{r['ttfa_med']} / {r['ttfa_min']} / {r['ttfa_max']} мс, ошибок {r['errors']}"))
    lines += ["", "Ответы моделей (первый раунд):"]
    for i, phrase in enumerate(ctx["phrases"]):
        lines.append(f"\n— Клиент: «{phrase}»")
        for r in llm_res:
            if r.get("error") or i >= len(r["runs"]):
                continue
            run = r["runs"][i]
            ans = run.get("text") or (f"[вызов функции: {', '.join(run['tool_calls'])}]" if run.get("tool_calls")
                                      else f"[{run.get('error', 'пусто')}]")
            lines.append(f"   {r['spec']} ({run.get('ttft_ms')} мс): {ans}")
    text = "\n".join(lines) + "\n"

    parts = ["<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
             "<title>Замер задержки</title><style>body{font:14px/1.5 system-ui;margin:16px;max-width:980px}"
             "pre{white-space:pre-wrap;background:#f6f7f9;padding:12px;border-radius:8px}"
             "audio{width:100%;max-width:420px}td{padding:4px 8px;vertical-align:top}</style>",
             "<h2>Озвучка — послушать</h2><table>"]
    for label, items in audio_links.items():
        parts.append(f"<tr><td colspan=2><b>{html.escape(label)}</b></td></tr>")
        for text_, url, ms in items:
            parts.append(f"<tr><td>{ms} мс<br><audio controls preload=none src='{html.escape(url)}'></audio></td>"
                         f"<td>{html.escape(text_)}</td></tr>")
    parts.append("</table><h2>Цифры и ответы моделей</h2><pre>" + html.escape(text) + "</pre>")
    return text, "".join(parts)


async def upload(key: str, data: bytes, content_type: str) -> Optional[str]:
    from backend.core.config import settings
    from backend.services.r2_storage import R2StorageService
    client = R2StorageService._get_client()
    if client is None or not R2StorageService.is_configured():
        return None
    await asyncio.to_thread(client.put_object, Bucket=settings.R2_BUCKET, Key=key, Body=data,
                            ContentType=content_type)
    return f"{settings.R2_PUBLIC_URL.rstrip('/')}/{key}"


# ============================================================================ main
async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assistant", help="uuid Eleven-ассистента (по умолчанию — последний с диалогами)")
    ap.add_argument("--phrases", type=int, default=5, help="сколько реплик клиентов взять (5)")
    ap.add_argument("--rounds", type=int, default=2, help="повторов каждой фразы (2)")
    ap.add_argument("--llm", nargs="*", help="модели provider:model (realtime|openai|openrouter)")
    ap.add_argument("--tts", nargs="*", help=f"модели озвучки (по умолчанию {' '.join(DEFAULT_TTS)})")
    ap.add_argument("--no-discover", action="store_true", help="не искать Luna / Gemini Flash автоматически")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--skip-tts", action="store_true")
    args = ap.parse_args()

    ctx = load_context(args.assistant, args.phrases)
    log(f"Ассистент: {ctx['name']} ({ctx['assistant_id']}), язык {ctx['language']}, голос {ctx['voice_id']}, "
        f"функций {len(ctx['tools'])}, промпт {len(ctx['instructions'])} символов")
    log("Фразы: " + " | ".join(ctx["phrases"]))

    llm_res, tts_res, audio_links = [], [], {}
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    async with httpx.AsyncClient(http2=False) as client:
        if not args.skip_llm:
            specs = list(args.llm or DEFAULT_LLMS)
            if not args.llm and not args.no_discover:
                specs += [s for s in await discover(client) if s not in specs]
            if not os.getenv("OPENROUTER_API_KEY"):
                log("  OPENROUTER_API_KEY не задан — модели OpenRouter пропущены")
                specs = [s for s in specs if not s.startswith("openrouter:")]
            log(f"\nLLM ({args.rounds}×{len(ctx['phrases'])} запросов на модель, после прогрева):")
            for spec in specs:
                llm_res.append(await bench_llm(spec, ctx, args.rounds, client))

        if not args.skip_tts:
            if not ctx["voice_id"]:
                log("У ассистента нет voice_id — TTS пропущен")
            else:
                log(f"\nTTS (голос {ctx['voice_id']}, {len(ctx['tts_texts'])} текстов × {args.rounds}):")
                for model in (args.tts or DEFAULT_TTS):
                    if "v3" in model:
                        tests = [(f"{model} / text-to-dialogue WS", lambda t, m=model: tts_ttd_v3(ctx, t, m))]
                    else:
                        tests = [(f"{model} / stream-input WS", lambda t, m=model: tts_stream_input(ctx, t, m)),
                                 (f"{model} / HTTP stream", lambda t, m=model: tts_http(client, ctx, t, m))]
                    for label, fn in tests:
                        res = await bench_tts(label, fn, ctx, args.rounds)
                        tts_res.append(res)
                        items = []
                        for run in res["runs"][:len(ctx["tts_texts"])]:
                            if run.get("pcm"):
                                slug = label.split(" ")[0] + ("_http" if "HTTP" in label else "")
                                url = await upload(f"bench/{stamp}/{slug}_{run['i']}.wav", wav_bytes(run["pcm"]),
                                                   "audio/wav")
                                if url:
                                    items.append((run["text"], url, run["ttfa_ms"]))
                        if items:
                            audio_links[label] = items

    text, page = build_report(ctx, llm_res, tts_res, audio_links)
    log("\n" + "=" * 70 + "\n" + text)
    report_url = await upload(f"bench/{stamp}/report.txt", text.encode("utf-8"), "text/plain; charset=utf-8")
    page_url = await upload(f"bench/{stamp}/index.html", page.encode("utf-8"), "text/html; charset=utf-8")
    if page_url:
        log(f"Отчёт с плеерами озвучки: {page_url}\nТекст отчёта: {report_url}")
    else:
        path = f"/tmp/bench-{stamp}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
        log(f"R2 не настроен — отчёт сохранён в {path}")


if __name__ == "__main__":
    asyncio.run(main())
