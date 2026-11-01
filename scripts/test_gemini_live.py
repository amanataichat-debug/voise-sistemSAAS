#!/usr/bin/env python3
"""
Проверка Gemini Live из Render Shell: ключ, список Live-моделей, настройка сессии, звук в ответ.

    python scripts/test_gemini_live.py                  # ключ GEMINI_API_KEY, модель GEMINI_LIVE_MODEL
    python scripts/test_gemini_live.py --model NAME     # другая модель (можно несколько раз)
    python scripts/test_gemini_live.py --key AIza...    # ключ из аргумента

Что делает, по шагам (каждый шаг печатает ОК / ПРОВАЛ и причину от Google):
  1. REST ListModels: ключ принят? какие модели умеют bidiGenerateContent (это и есть Live)?
     Если нашей модели в списке нет — имя в GEMINI_LIVE_MODEL неверное, берите имя из списка.
  2. Для каждой модели три настройки сессии по нарастающей: минимум → + транскрипция → как в приложении
     (VAD-профиль). Видно, какое поле Google отклоняет, и код/текст закрытия сессии.
  3. Текстовая реплика (realtimeInput.text) и ожидание звука: сколько байт аудио пришло и за сколько.

Код возврата 0 — хотя бы одна модель отдала звук; 1 — нет.
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import websockets

WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
REST_MODELS = "https://generativelanguage.googleapis.com/v1beta/models"


def ok(msg):
    print(f"  ОК     {msg}")


def bad(msg):
    print(f"  ПРОВАЛ {msg}")


def list_models(key):
    """Шаг 1. Возвращает множество имён моделей с bidiGenerateContent (без префикса models/) или None."""
    print("\n[1] Ключ и список Live-моделей (ListModels)")
    names, token = [], None
    try:
        while True:
            params = {"key": key, "pageSize": 200}
            if token:
                params["pageToken"] = token
            r = httpx.get(REST_MODELS, params=params, timeout=20)
            if r.status_code != 200:
                err = ""
                try:
                    err = r.json().get("error", {}).get("message", "")
                except Exception:
                    err = r.text[:300]
                bad(f"HTTP {r.status_code}: {err}")
                if r.status_code in (400, 401, 403):
                    print("         → ключ не принят Google (опечатка, ключ отозван или у проекта выключен Generative Language API)")
                return None
            data = r.json()
            names += data.get("models", [])
            token = data.get("nextPageToken")
            if not token:
                break
    except Exception as exc:
        bad(f"нет связи с Google: {exc}")
        return None
    ok(f"ключ принят, моделей всего: {len(names)}")
    live = sorted(m["name"].replace("models/", "") for m in names if "bidiGenerateContent" in (m.get("supportedGenerationMethods") or []))
    if live:
        ok("Live-модели (bidiGenerateContent) для этого ключа:")
        for n in live:
            print(f"           - {n}")
    else:
        bad("ни одной модели с bidiGenerateContent: Live API этому ключу/проекту недоступен")
    return set(live)


def setups(model, voice):
    base = {
        "model": f"models/{model}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    with_tr = {**base, "outputAudioTranscription": {}, "inputAudioTranscription": {}}
    app_like = {**with_tr, "systemInstruction": {"parts": [{"text": "Ты вежливый телефонный ассистент. Говори коротко."}]},
                "realtimeInputConfig": {"automaticActivityDetection": {
                    "disabled": False, "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
                    "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH", "prefixPaddingMs": 100, "silenceDurationMs": 500}}}
    # Поля для долгих звонков (лимит сессии без них ~15 мин аудио / ~10 мин соединения). В приложение они НЕ
    # включены, пока этот вариант не пройдёт на вашем ключе: принято → можно включать, отклонено → видна причина.
    long_calls = {**app_like, "sessionResumption": {}, "contextWindowCompression": {"slidingWindow": {}}}
    return [("минимальная", base), ("+ транскрипция", with_tr), ("как в приложении (VAD + промпт)", app_like),
            ("долгие звонки (sessionResumption + contextWindowCompression)", long_calls)]


async def connect(key):
    try:
        return await websockets.connect(f"{WS_URL}?key={key}", max_size=15 * 1024 * 1024, open_timeout=20)
    except TypeError:
        raise


async def try_setup(key, model, label, setup):
    """Открыть сессию, отправить setup, дождаться setupComplete. Возвращает (ws|None, причина)."""
    try:
        ws = await connect(key)
    except Exception as exc:
        return None, f"не открылось соединение: {type(exc).__name__}: {exc}"
    await ws.send(json.dumps({"setup": setup}))
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
    except asyncio.TimeoutError:
        await ws.close()
        return None, "15 с тишины: Google не прислал ни setupComplete, ни ошибки"
    except websockets.exceptions.ConnectionClosed as exc:
        return None, f"Google закрыл сессию: код {exc.code}, причина «{exc.reason}»"
    msg = json.loads(raw)
    if "setupComplete" in msg:
        return ws, "setupComplete"
    await ws.close()
    return None, f"ответ без setupComplete: {json.dumps(msg, ensure_ascii=False)[:300]}"


async def hear_audio(ws, seconds=20):
    """Отправить текст и посчитать звук. Возвращает (байт, секунд до первого звука, транскрипт, служебные)."""
    await ws.send(json.dumps({"realtimeInput": {"text": "Скажи одной короткой фразой по-кыргызски: Саламатсызбы, мен Voksy AI жардамчысымын."}}))
    started = time.time()
    first, total, said, extra = None, 0, "", []
    deadline = started + seconds
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
        except asyncio.TimeoutError:
            break
        except websockets.exceptions.ConnectionClosed as exc:
            extra.append(f"сессию закрыл Google: код {exc.code}, причина «{exc.reason}»")
            break
        msg = json.loads(raw)
        sc = msg.get("serverContent") or {}
        for part in (sc.get("modelTurn") or {}).get("parts", []):
            data = (part.get("inlineData") or {}).get("data")
            if data:
                import base64
                total += len(base64.b64decode(data))
                first = first if first is not None else time.time() - started
        if "outputTranscription" in sc:
            said += sc["outputTranscription"].get("text", "")
        for k in ("error", "goAway"):
            if k in msg:
                extra.append(f"{k}: {json.dumps(msg[k], ensure_ascii=False)[:300]}")
        if sc.get("turnComplete"):
            break
    return total, first, said.strip(), extra


async def check_model(key, model, voice):
    print(f"\n[2] Модель {model}")
    got_audio = False
    for label, setup in setups(model, voice):
        ws, why = await try_setup(key, model, label, setup)
        if ws is None:
            bad(f"настройка «{label}»: {why}")
            continue
        ok(f"настройка «{label}»: сессия открыта")
        if label.startswith("как в приложении"):
            print(f"\n[3] Звук от {model} (до 20 с)")
            total, first, said, extra = await hear_audio(ws)
            for e in extra:
                bad(e)
            if total:
                ok(f"получено {total} байт звука PCM, первый через {first:.1f} с; сказал: «{said}»")
                got_audio = True
            else:
                bad("звука нет: модель молчит на текстовую реплику (realtimeInput.text)")
        await ws.close()
    return got_audio


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.getenv("GEMINI_API_KEY", ""))
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--voice", default="Aoede")
    args = ap.parse_args()
    if not args.key:
        print("Нет ключа: задайте GEMINI_API_KEY или --key")
        return 1
    models = args.model or [os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")]
    print(f"Ключ {args.key[:6]}…{args.key[-4:]}, проверяем модели: {', '.join(models)}")
    live = list_models(args.key)
    if live is not None:
        for m in models:
            if m not in live:
                bad(f"модели «{m}» нет среди Live-моделей ключа. Подходящие из списка выше впишите в GEMINI_LIVE_MODEL")
    any_audio = False
    for m in models:
        any_audio = await check_model(args.key, m, args.voice) or any_audio
    print("\nИТОГ:", "звук получен" if any_audio else "звука нет — см. ПРОВАЛ выше")
    return 0 if any_audio else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
