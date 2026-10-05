#!/usr/bin/env python3
"""
Проверка распознавания Google Chirp 3 (GoogleSTTClient) — тем же клиентом, что в звонках.

Запуск на Render (Shell сервиса, там лежат ключи):
    cd /opt/render/project/src
    python scripts/test_google_stt.py                      # кыргызская + русская фраза, 8 кГц как в телефоне
    python scripts/test_google_stt.py --rate 16000         # как в виджете
    python scripts/test_google_stt.py --text "Саламатсызбы, баасы канча?" --text "Сколько стоит доставка?"
    python scripts/test_google_stt.py --wav call.wav       # свой файл (WAV PCM16 моно)
    python scripts/test_google_stt.py --debug              # каждый ответ Google (события речи, черновики)
    python scripts/test_google_stt.py --no-denoise --languages ky-KG   # сравнить настройки

Что делает:
  1. показывает, какой ключ Google найден (сервисный аккаунт / API-ключ, проект, регион);
  2. синтезирует фразы голосом ElevenLabs (eleven_v3, ELEVENLABS_API_KEY) или берёт --wav;
  3. стримит звук в Chirp 3 в реальном темпе (кусками по 20 мс, между фразами тишина)
     и печатает черновики, готовые реплики и задержку от конца речи до реплики
     (конец речи — последний громкий кусок звука перед репликой).

Ключи из окружения: GOOGLE_SPEECH_CREDENTIALS_JSON (или GOOGLE_SERVICE_ACCOUNT_JSON) либо
GOOGLE_SPEECH_API_KEY + GOOGLE_SPEECH_PROJECT_ID; ELEVENLABS_API_KEY для синтеза фраз.
"""

import argparse
import asyncio
import audioop
import base64
import importlib.util
import logging
import os
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.core.config import settings  # noqa: E402

# Клиент грузим напрямую из файла: пакет backend.websockets тянет за собой все хендлеры
_spec = importlib.util.spec_from_file_location(
    "google_stt_client", os.path.join(ROOT, "backend", "websockets", "google_stt_client.py"))
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)

DEFAULT_TEXTS = [
    ("ky", "Саламатсызбы! Мен эртең саат үчтө жазылгым келет. Баасы канча болот?"),
    ("ru", "Здравствуйте, подскажите, пожалуйста, сколько стоит доставка в Бишкеке?"),
]
CHUNK_MS = 20
PAUSE_SEC = 2.5


def synth(text: str, lang: str, voice: str, rate: int) -> bytes:
    import httpx
    key = settings.ELEVENLABS_API_KEY
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY не задан — передайте свой файл через --wav")
    headers = {"xi-api-key": key}
    if not voice:
        r = httpx.get("https://api.elevenlabs.io/v1/voices", headers=headers, timeout=20)
        r.raise_for_status()
        voices = r.json().get("voices") or []
        if not voices:
            raise SystemExit("В аккаунте ElevenLabs нет голосов — укажите --voice")
        voice = voices[0]["voice_id"]
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                   params={"output_format": f"pcm_{rate}"}, headers=headers, timeout=60,
                   json={"text": text, "model_id": "eleven_v3", "language_code": lang})
    if r.status_code != 200:
        raise SystemExit(f"ElevenLabs TTS {r.status_code}: {r.text[:300]}")
    return r.content


def read_wav(path: str, rate: int) -> bytes:
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise SystemExit("Нужен WAV PCM16")
        pcm = w.readframes(w.getnframes())
        if w.getnchannels() == 2:
            pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
        if w.getframerate() != rate:
            pcm, _ = audioop.ratecv(pcm, 2, 1, w.getframerate(), rate, None)
    return pcm


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=int, default=8000, help="8000 (телефон) или 16000 (виджет)")
    ap.add_argument("--text", action="append", help="фраза для синтеза (можно несколько)")
    ap.add_argument("--lang", default="ky", help="язык синтеза для --text (ky/ru)")
    ap.add_argument("--voice", default="", help="voice_id ElevenLabs (по умолчанию первый в аккаунте)")
    ap.add_argument("--wav", help="свой WAV вместо синтеза")
    ap.add_argument("--endpointing", default=settings.GOOGLE_STT_ENDPOINTING)
    ap.add_argument("--location", default=settings.GOOGLE_SPEECH_LOCATION)
    ap.add_argument("--languages", default=settings.GOOGLE_STT_LANGUAGES)
    ap.add_argument("--hold", type=int, default=settings.GOOGLE_STT_COMMIT_HOLD_MS, help="мс ожидания после финала")
    ap.add_argument("--no-denoise", action="store_true")
    ap.add_argument("--debug", action="store_true", help="печатать каждый ответ Google")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    creds = g.parse_credentials_json(settings.GOOGLE_SPEECH_CREDENTIALS_JSON)
    print("— Ключ Google —")
    if settings.GOOGLE_SPEECH_API_KEY:
        print(f"  API-ключ …{settings.GOOGLE_SPEECH_API_KEY[-4:]}, проект {settings.GOOGLE_SPEECH_PROJECT_ID}")
    elif creds:
        print(f"  сервисный аккаунт {creds.get('client_email')}, проект "
              f"{settings.GOOGLE_SPEECH_PROJECT_ID or creds.get('project_id')}")
    else:
        raise SystemExit("  не найден: задайте GOOGLE_SPEECH_CREDENTIALS_JSON (весь JSON) "
                         "или GOOGLE_SPEECH_API_KEY + GOOGLE_SPEECH_PROJECT_ID")

    print("— Звук —")
    clips = []
    if args.wav:
        clips.append(("файл", read_wav(args.wav, args.rate)))
    else:
        texts = [(args.lang, t) for t in args.text] if args.text else DEFAULT_TEXTS
        for lang, text in texts:
            t0 = time.monotonic()
            pcm = synth(text, lang, args.voice, args.rate)
            print(f"  [{lang}] {len(pcm) / (args.rate * 2):.1f} с ({(time.monotonic() - t0):.1f} с синтез): {text}")
            clips.append((text, pcm))

    t_start = time.monotonic()
    speech_end = {"at": 0.0}   # время отправки последнего громкого куска
    results = []

    def ts() -> str:
        return f"{time.monotonic() - t_start:6.2f}s"

    async def on_partial(text: str) -> None:
        print(f"  {ts()}  … {text}")

    async def on_committed(text: str) -> None:
        lag = int((time.monotonic() - speech_end["at"]) * 1000) if speech_end["at"] else None
        speech_end["at"] = 0.0
        results.append((text, lag, client.detected_language))
        print(f"  {ts()}  ✔ [{client.detected_language or '?'}] {text}   (от конца речи {lag} мс)")


    async def on_closed(fatal: bool) -> None:
        print(f"  {ts()}  поток закрыт (fatal={fatal}, {client.fatal_error})")

    client = g.GoogleSTTClient(
        credentials_info=creds, api_key=settings.GOOGLE_SPEECH_API_KEY,
        project_id=settings.GOOGLE_SPEECH_PROJECT_ID, location=args.location,
        languages=[x.strip() for x in args.languages.split(",") if x.strip()],
        sample_rate=args.rate, model=settings.GOOGLE_STT_MODEL, endpointing=args.endpointing,
        denoise=settings.GOOGLE_STT_DENOISE and not args.no_denoise,
        commit_hold_ms=args.hold, debug=args.debug,
        phrases=[x.strip() for x in (settings.GOOGLE_STT_PHRASES or "").split(",") if x.strip()],
        on_partial=on_partial, on_committed=on_committed, on_closed=on_closed, label="test",
    )
    print("— Подключение —")
    t0 = time.monotonic()
    if not await client.connect():
        raise SystemExit(f"  НЕ подключились: {client.fatal_error} — {client._last_error[0]}: {client._last_error[1]}")
    print(f"  ок за {int((time.monotonic() - t0) * 1000)} мс: {client.provider_label}")

    print("— Распознавание (реальный темп) —")
    t_start = time.monotonic()
    step = int(args.rate * 2 * CHUNK_MS / 1000)
    silence = b"\x00" * step
    for _, pcm in clips:
        for i in range(0, len(pcm), step):
            chunk = pcm[i:i + step]
            await client.send_audio(base64.b64encode(chunk).decode())
            if audioop.rms(chunk, 2) > 300:
                speech_end["at"] = time.monotonic()
            await asyncio.sleep(CHUNK_MS / 1000)
        for _ in range(int(PAUSE_SEC * 1000 / CHUNK_MS)):
            await client.send_audio(base64.b64encode(silence).decode())
            await asyncio.sleep(CHUNK_MS / 1000)
    await client.close()

    print("— Итог —")
    print(f"  {client.provider_label}")
    print(f"  фраз отправлено: {len(clips)}, реплик получено: {len(results)} "
          f"(черновиков: {client.partials}, событий начала/конца речи: {client.vad_events})")
    for text, lag, lang in results:
        print(f"  [{lang or '?'}] {lag} мс  {text}")
    if not results:
        print("  ничего не распознано — смотрите ошибки выше")


if __name__ == "__main__":
    asyncio.run(main())
