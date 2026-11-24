"""Прослушать голоса GPT-Live: одни и те же сцены разными голосами → mp3 в R2 → ссылки (ветка 2411-live-voices).

Запуск в Render Shell (тот же OPENAI_API_KEY, что у звонков, — так проверяется и то, что свой голос
открывается на ключе сервера):

    python scripts/voice_samples.py                       # cedar, marin + все свои голоса из базы
    python scripts/voice_samples.py azim azim2 cedar      # только эти
    python scripts/voice_samples.py --scenes read_ky pain_ky

Для каждого голоса — одна живая сессия GPT-Live с настоящим слоем платформы (LIVE_VOICE_HEAD /
LIVE_VOICE_RULES) и нейтральной карточкой магазина. Сцены:
  read_ru, read_ky — один и тот же живой текст со смехом (чёткость, акцент, интонация, смех);
  joke_ru  — собеседник шутит «вы не робот?» (юмор, находчивость);
  pain_ky  — собеседник по-кыргызски жалуется на потерянных клиентов (сочувствие + решение);
  news_ky  — собеседник радуется (радость, живая реакция).
Сцены read_* — модель читает текст, live-сцены — отвечает своими словами (видно «ум» и характер).

На выходе по каждому голосу два mp3: «как в виджете» (24 кГц) и «как по телефону» (полоса 300–3400 Гц,
8 кГц) — клиент слышит второй. Ссылки живут 7 дней. Если свой голос не открылся на ключе, это пишется
строкой «НЕ ОТКРЫЛСЯ» (звучит голос по умолчанию) — тогда создайте голос этим ключом:
python scripts/create_live_voice.py --preset <ключ>.

Стоимость — обычные минуты GPT-Live (≈1–1,5 мин на голос).
"""
import argparse
import asyncio
import base64
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backend.core.config  # noqa: F401  (первым: иначе циклический импорт backend.db.session)
from backend.core.config import settings

RATE = 24000
SILENCE = b"\x00" * (RATE // 10 * 2)  # 0,1 с тишины на вход (Live — full-duplex)

CARD = ("Ты — консультант интернет-магазина детской одежды «Балапан» в Бишкеке: продаёте оптом и в розницу, "
        "доставка по всему Кыргызстану. Помогаешь людям выбрать и оформить заказ, отвечаешь живо и по делу.")

READ_RU = ("Ой, здравствуйте! Да, слышу вас прекрасно. Ха, ну это вы точно подметили! Смотрите, как это работает: "
           "клиент пишет вам в ватсап хоть в два часа ночи, а ответ приходит через пару секунд — с ценой, размерами "
           "и фото. Утром вы просто открываете и видите готовые заказы. Ну классно же?")
READ_KY = ("Саламатсызбы! Ооба, жакшы угуп жатам. Ха-ха, туура байкадыңыз! Карасаңыз, мындай иштейт: кардар "
           "ватсапка түнкү саат экиде жазса деле, эки секундда жооп барат — баасы, размери, сүрөтү менен. Эртең "
           "менен ачып эле даяр заказдарды көрөсүз. Жакшы да?")

SCENES = {
    "read_ru": ("say", READ_RU),
    "read_ky": ("say", READ_KY),
    "joke_ru": ("live", "Сейчас собеседник по телефону сказал тебе по-русски: «Слушайте, а вы случайно не робот? "
                        "А то я с роботами разговаривать не люблю, ха-ха». Ответь ему вслух прямо сейчас — живо, "
                        "с юмором, коротко, как в настоящем разговоре."),
    "pain_ky": ("live", "Сейчас собеседник сказал тебе по-кыргызски: «Кечээ эки кардар жазып, жооп ала албай кетип "
                        "калышты, жиним келип жатат». Ответь ему вслух по-кыргызски: коротко и тепло посочувствуй "
                        "и предложи решение."),
    "news_ky": ("live", "Сейчас собеседник радостно сказал тебе по-кыргызски: «Биз кечээ экинчи дүкөн ачтык!». "
                        "Ответь ему вслух по-кыргызски — искренне порадуйся вместе с ним и задай один живой вопрос."),
}


def ffmpeg_path():
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def to_mp3(pcm: bytes, phone: bool) -> bytes:
    ff = ffmpeg_path()
    if not ff:
        raise SystemExit("Нет ffmpeg (imageio-ffmpeg в requirements)")
    af = ["-af", "highpass=f=300,lowpass=f=3400", "-ar", "8000"] if phone else ["-ar", str(RATE)]
    with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as f:
        f.write(pcm)
        raw = f.name
    try:
        out = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1",
                              "-i", raw, *af, "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "pipe:1"],
                             capture_output=True, timeout=120)
    finally:
        os.unlink(raw)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode(errors="ignore")[:300])
    return out.stdout


async def record_voice(voice: str, scenes: list) -> dict:
    from backend.websockets.live_client import OpenAILiveClient
    card = types.SimpleNamespace(id=f"sample-{voice}", system_prompt=CARD, functions=None, voice=voice,
                                 greeting_message=None, language="ky")
    cl = OpenAILiveClient(settings.OPENAI_API_KEY, card, client_id=f"voice-sample-{voice}-{int(time.time())}",
                          db_session=None, audio_rate=RATE)
    result = {"voice": voice, "opened": False, "sounds_as": None, "scenes": {}, "pcm": b"", "error": ""}
    if not await cl.connect():
        result["error"] = "сессия GPT-Live не открылась (смотрите WARNING выше)"
        return result
    custom = getattr(cl, "custom_voice", None)
    result["sounds_as"] = (custom or {}).get("title") or cl.voice
    result["opened"] = cl.voice == voice

    queue: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()

    async def pump():
        try:
            async for ev in cl.receive_events():
                await queue.put(ev)
        finally:
            await queue.put(None)

    async def feed():
        while not stop.is_set():
            await cl.send_audio(SILENCE)
            await asyncio.sleep(0.1)

    pumper, feeder = asyncio.create_task(pump()), asyncio.create_task(feed())

    async def collect(max_wait_start: float, quiet: float = 1.8, max_total: float = 50.0):
        pcm, said, t0, last = bytearray(), [], time.monotonic(), None
        while True:
            now = time.monotonic()
            if now - t0 > max_total or (last is None and now - t0 > max_wait_start) or (last and now - last > quiet):
                break
            try:
                ev = await asyncio.wait_for(queue.get(), 0.3)
            except asyncio.TimeoutError:
                continue
            if ev is None:
                break
            et = ev.get("type")
            if et == "session.output_audio.delta" and ev.get("delta"):
                pcm.extend(base64.b64decode(ev["delta"]))
                last = time.monotonic()
            elif et == "session.output_transcript.delta":
                said.append(ev.get("delta") or ev.get("text") or "")
            elif et == "error":
                said.append(f" [ошибка: {(ev.get('error') or {}).get('message', ev)}]")
        return bytes(pcm), "".join(said).strip()

    gap = b"\x00" * int(RATE * 0.8) * 2
    try:
        await collect(max_wait_start=1.5)  # модель могла заговорить сама — это не сцена
        for name in scenes:
            mode, text = SCENES[name]
            if mode == "say":
                await cl.append_instructions("Read the following exactly, without an introduction or explanation: "
                                             f"<say>{text}</say>")
            else:
                await cl.append_instructions(text)
            pcm, said = await collect(max_wait_start=12)
            result["scenes"][name] = {"seconds": round(len(pcm) / 2 / RATE, 1), "said": said}
            result["pcm"] += pcm + gap
    finally:
        stop.set()
        feeder.cancel()
        try:
            await cl.close(timeout=5)
        except Exception:
            pass
        pumper.cancel()
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("voices", nargs="*", help="ключи голосов; по умолчанию cedar, marin и все свои из базы")
    ap.add_argument("--scenes", nargs="+", choices=sorted(SCENES), default=list(SCENES))
    a = ap.parse_args()
    if not settings.OPENAI_API_KEY:
        raise SystemExit("Нет OPENAI_API_KEY")

    from backend.services import live_voices
    voices = [v.strip().lower() for v in a.voices] or (["cedar", "marin"] + live_voices.keys())
    from backend.services.r2_storage import R2StorageService as R2
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M")
    print(f"Голоса: {', '.join(voices)}; сцены: {', '.join(a.scenes)}\n")

    for voice in voices:
        res = await record_voice(voice, a.scenes)
        title = res["sounds_as"] or voice
        print("=" * 72)
        if res["error"]:
            print(f"{voice}: {res['error']}")
            continue
        if not res["opened"]:
            print(f"{voice}: НЕ ОТКРЫЛСЯ на ключе сервера — в записи звучит «{title}». Создайте голос этим ключом: "
                  f"python scripts/create_live_voice.py --preset {voice}")
        else:
            print(f"{voice}: {title}")
        for name, sc in res["scenes"].items():
            print(f"  {name:8} {sc['seconds']:5.1f} с  {sc['said'][:220]}")
        if not res["pcm"].strip(b"\x00"):
            print("  звука нет")
            continue
        for phone in (False, True):
            data = to_mp3(res["pcm"], phone)
            suffix = "phone" if phone else "wide"
            key = f"voice-samples/{stamp}/{voice}-{suffix}.mp3"
            label = "по телефону" if phone else "как в виджете"
            if R2.objects_available() and await R2.put_object(key, data, "audio/mpeg"):
                url = R2.presigned_url(key, expires_seconds=7 * 24 * 3600, filename=f"{voice}-{suffix}.mp3")
                print(f"  ▶ {label}: {url}")
            else:
                path = os.path.join(tempfile.gettempdir(), f"{voice}-{suffix}.mp3")
                with open(path, "wb") as f:
                    f.write(data)
                print(f"  ▶ {label}: {path} (R2 недоступен — файл на сервере)")
    print("\nСравнивайте «по телефону»: так голос слышит клиент.")


if __name__ == "__main__":
    asyncio.run(main())
