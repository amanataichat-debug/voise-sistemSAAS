"""
Голосовые сообщения агента в мессенджерах (ветка 1210-chat-v2, задача 3.4).

Агент отвечает клиенту голосовым в личном Telegram и WhatsApp. Два движка
озвучки на серверных ключах (agent_configs.voice_msg_engine):

  eleven — ElevenLabs Text-to-Speech (AGENT_VOICE_TTS_MODEL, eleven_v3 —
           единственное семейство с кыргызским). Голос / язык / подача — из
           agent_configs.voice_msg_*, пустые поля — из Eleven-карточки звонков.
           Подача по умолчанию Creative. «Очеловечивание»: агент пишет текст
           разговорно, со звуковыми метками v3 ([softly], [short pause],
           [exhales] …, белый список VOICE_TAGS) и многоточиями; неизвестные
           метки вырезаются, спокойная манера добавляется сама.
  openai — GPT-Live (gpt-live-1, как голосовой режим ChatGPT): сессия Live
           без собеседника дословно наговаривает текст (_gpt_live_speak), PCM →
           OGG/Opus через ffmpeg (imageio-ffmpeg). Не вышло — запасной вариант
           OpenAI Speech (AGENT_VOICE_OPENAI_TTS_MODEL) голосами
           (OPENAI_TTS_VOICES — набор модели Speech, AGENT_VOICE_OPENAI_VOICES)
           с инструкцией манеры (openai_instructions: язык агента с родным
           произношением, спокойно, тихо, с паузами). Метки
           превращаются в паузы «…».

Формат: просим Opus. Если пришёл OGG — это голосовое Telegram (voice_note,
длительность из последней страницы OGG). Иначе берём MP3 и, если на сервере
есть ffmpeg, перекодируем в OGG/Opus; без ffmpeg Telegram получит аудиофайл.
WhatsApp-шлюз (Evolution, sendWhatsAppAudio) перекодирует сам. Instagram
(Composio) аудио не умеет — там голосовых нет.

Когда отвечать голосом — настройка коннектора voice_reply_mode: never |
on_voice (только в ответ на голосовое клиента) | auto (агент решает сам; доля
голосовых среди ответов — agent_configs.voice_msg_auto_share, подсказка
prompt_hint говорит, каким видом отвечать сейчас). Текст после голосового не
должен повторять его (repeats_recent_voice). Стоимость — кредиты агента.
"""

import asyncio
import math
import re
import shutil
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

import httpx

from backend.core.config import settings
from backend.core.logging import get_logger

logger = get_logger(__name__)

ELEVEN_API = "https://api.elevenlabs.io/v1"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"

VOICE_REPLY_MODES = ("never", "on_voice", "auto")
DEFAULT_VOICE_REPLY_MODE = "on_voice"

ENGINE_ELEVEN = "eleven"
ENGINE_OPENAI = "openai"
VOICE_ENGINES = (ENGINE_ELEVEN, ENGINE_OPENAI)

# Подача ElevenLabs по умолчанию для голосовых: Creative (живее, чем Natural в звонках).
DEFAULT_MESSAGE_STABILITY = 0.0
DEFAULT_OPENAI_VOICE = "marin"

AUTO_SHARE_CHOICES = (30, 40, 50)
DEFAULT_AUTO_SHARE = 40

# Метка исходящего голосового в треде (агент и владелец видят содержание).
VOICE_BODY_PREFIX = "🎤 Голосовое: "

# Звуковые метки Eleven v3, которые разрешено ставить агенту (остальные вырезаются).
VOICE_TAGS = (
    "softly", "calm", "thoughtful", "warmly", "short pause", "long pause",
    "exhales", "sighs", "chuckles", "hesitates",
)
_TAG_RE = re.compile(r"\[([^\[\]]{1,30})\]")

# Голоса модели OpenAI Speech (у неё свой набор, не весь список GPT-Live звонков).
# Можно переопределить переменной AGENT_VOICE_OPENAI_VOICES (через запятую).
OPENAI_TTS_VOICES = [v.strip().lower() for v in (settings.AGENT_VOICE_OPENAI_VOICES or "").split(",") if v.strip()]

# (на каком языке, с каким произношением)
LANGUAGE_NAMES = {"ru": ("русском", "русским"), "ky": ("кыргызском", "кыргызским"),
                  "kk": ("казахском", "казахским"), "uz": ("узбекском", "узбекским"),
                  "en": ("английском", "английским"), "tr": ("турецком", "турецким")}


# Чистая речь без акцента и диалекта: для каждого языка — какой нормой говорить.
SPEECH_NORMS = {
    "ru": ("Говори на чистом, правильном русском языке — литературная норма, как у "
           "носителя из большого города: без акцента (английского, кыргызского или любого "
           "другого), без диалекта, без жаргона и просторечий. Чёткая, понятная дикция, "
           "правильные ударения."),
    "ky": ("Сүйлө таза, туура кыргыз тилинде — адабий кыргыз тили, Бишкектеги эне тилдүү "
           "адамдай. Говори на чистом литературном кыргызском, как носитель из Бишкека: без "
           "русского и английского акцента, без местного диалекта, не вставляй русские слова, "
           "где есть кыргызские. Чёткое, понятное произношение кыргызских звуков (ң, ө, ү)."),
}


def openai_instructions(language: Optional[str]) -> str:
    lang = LANGUAGE_NAMES.get((language or "").lower())
    norm = SPEECH_NORMS.get((language or "").lower())
    if norm:
        accent = norm + " "
    elif lang:
        accent = (f"Текст на {lang[0]} языке: произноси его как носитель, с родным {lang[1]} "
                  f"произношением, без иностранного акцента и без диалекта. ")
    else:
        accent = "Произноси текст как носитель его языка, без иностранного акцента и без диалекта. "
    return (
        accent
        + "Это обычное голосовое сообщение в мессенджере, записанное на телефон. Говори спокойно и "
        "негромко, неторопливо, тёплым разговорным тоном, как живой человек знакомому клиенту. "
        "Делай естественные паузы, иногда чуть задумывайся перед словом. Никакой дикторской или "
        "рекламной интонации, не спеши и не повышай голос."
    )


# Кыргызский от русского по тексту: буквы ң ө ү и частые кыргызские слова.
_KY_LETTERS = set("ңөүҢӨҮ")
_KY_WORDS = {
    "саламатсызбы", "салам", "рахмат", "ооба", "жок", "жакшы", "макул", "сиз", "сизге", "сизди",
    "менен", "керек", "бар", "эмне", "кандай", "азыр", "эртең", "бүгүн", "кийин", "үчүн", "болот",
    "болсо", "биз", "бизде", "мен", "сен", "анан", "дагы", "абдан", "баасы", "канча", "жазып",
    "жиберем", "айтып", "берем", "алам", "келет", "кутуп", "калам", "туура", "эмес",
}


def detect_language(text: str, default: Optional[str] = None) -> Optional[str]:
    """
    Язык текста голосового: ky (кыргызские буквы/слова), ru (кириллица), en (латиница);
    иначе default. Озвучка берёт язык из самого текста, а не только из настроек агента:
    агент отвечает клиенту на его языке.
    """
    clean = strip_tags(text or "")
    if any(ch in _KY_LETTERS for ch in clean):
        return "ky"
    words = re.findall(r"[а-яёңөүa-z]+", clean.lower())
    if not words:
        return default
    if sum(1 for w in words if w in _KY_WORDS) >= 1 and default == "ky":
        return "ky"
    if sum(1 for w in words if w in _KY_WORDS) >= 2:
        return "ky"
    cyr = sum(1 for w in words if re.search(r"[а-яё]", w))
    lat = len(words) - cyr
    if cyr > lat:
        return "ru"
    if lat > cyr and default not in ("ky", "ru", "kk", "uz"):
        return "en"
    return default


def is_configured(engine: str = ENGINE_ELEVEN) -> bool:
    if engine == ENGINE_OPENAI:
        return bool(settings.OPENAI_API_KEY)
    return bool(settings.ELEVENLABS_API_KEY)


def any_engine_configured() -> bool:
    return is_configured(ENGINE_ELEVEN) or is_configured(ENGINE_OPENAI)


def voice_reply_mode(row) -> str:
    mode = getattr(row, "voice_reply_mode", None) or DEFAULT_VOICE_REPLY_MODE
    return mode if mode in VOICE_REPLY_MODES else DEFAULT_VOICE_REPLY_MODE


def auto_share(agent_config) -> int:
    v = getattr(agent_config, "voice_msg_auto_share", None)
    return int(v) if v in AUTO_SHARE_CHOICES else DEFAULT_AUTO_SHARE


# ============================================================================
# ГОЛОС АГЕНТА
# ============================================================================

def resolve_voice(db, agent_config) -> Optional[Dict[str, Any]]:
    """
    Голос для голосовых сообщений: {engine, voice_id, voice_name, language,
    stability, model, source} или None (голос не выбран / движок не настроен).
    """
    engine = getattr(agent_config, "voice_msg_engine", None) or ENGINE_ELEVEN
    if engine not in VOICE_ENGINES:
        engine = ENGINE_ELEVEN
    if not is_configured(engine):
        return None
    if engine == ENGINE_OPENAI:
        from backend.websockets.live_client import LIVE_VOICE_SET
        voice = (getattr(agent_config, "voice_msg_openai_voice", None) or DEFAULT_OPENAI_VOICE).lower()
        if voice not in LIVE_VOICE_SET:
            voice = DEFAULT_OPENAI_VOICE
        return {
            "engine": ENGINE_OPENAI, "voice_id": voice, "voice_name": voice,
            "language": _agent_language(db, agent_config), "stability": None,
            "model": settings.AGENT_VOICE_OPENAI_TTS_MODEL, "source": "own",
        }

    card = None
    if getattr(agent_config, "eleven_assistant_id", None):
        from backend.models.eleven_assistant import ElevenAssistantConfig
        card = db.query(ElevenAssistantConfig).filter(
            ElevenAssistantConfig.id == agent_config.eleven_assistant_id
        ).first()
    voice_id = agent_config.voice_msg_voice_id or (card.voice_id if card else None)
    if not voice_id:
        return None
    own = bool(agent_config.voice_msg_voice_id)
    language = agent_config.voice_msg_language or (card.language if card else None) or "ky"
    stability = agent_config.voice_msg_stability
    if stability is None:
        stability = DEFAULT_MESSAGE_STABILITY
    return {
        "engine": ENGINE_ELEVEN,
        "voice_id": voice_id,
        "voice_name": (agent_config.voice_msg_voice_name if own else None) or None,
        "language": language,
        "stability": float(stability),
        "model": settings.AGENT_VOICE_TTS_MODEL,
        "source": "own" if own else "call_card",
    }


def _agent_language(db, agent_config) -> Optional[str]:
    """Язык голосовых: из настроек голосовых, иначе из Eleven-карточки звонков."""
    if getattr(agent_config, "voice_msg_language", None):
        return agent_config.voice_msg_language
    if getattr(agent_config, "eleven_assistant_id", None):
        from backend.models.eleven_assistant import ElevenAssistantConfig
        card = db.query(ElevenAssistantConfig).filter(
            ElevenAssistantConfig.id == agent_config.eleven_assistant_id
        ).first()
        if card and card.language:
            return card.language
    return None


# ============================================================================
# ТЕКСТ: МЕТКИ И «ОЧЕЛОВЕЧИВАНИЕ»
# ============================================================================

def strip_tags(text: str) -> str:
    """Текст без звуковых меток — для треда, лимитов и сравнения с текстом."""
    out = _TAG_RE.sub(" ", text or "")
    return re.sub(r"\s{2,}", " ", out).strip()


def prepare_text(text: str, engine: str) -> str:
    """
    Текст для озвучки. ElevenLabs v3: оставить только разрешённые метки и
    добавить спокойную манеру в начало, если агент её не задал. OpenAI: метки
    пауз → «…», остальные метки убрать (манеру задаёт инструкция).
    """
    text = (text or "").strip()
    if engine == ENGINE_OPENAI:
        text = re.sub(r"\[(short pause|long pause|hesitates)\]", "…", text, flags=re.I)
        return strip_tags(text)

    def keep(m):
        tag = m.group(1).strip().lower()
        return f"[{tag}]" if tag in VOICE_TAGS else " "
    text = re.sub(r"\s{2,}", " ", _TAG_RE.sub(keep, text)).strip()
    if not text.startswith("["):
        text = "[softly] " + text
    return text


# ============================================================================
# СИНТЕЗ
# ============================================================================

def ogg_duration_seconds(data: bytes) -> int:
    """Длительность OGG/Opus: granule последней страницы / 48000."""
    try:
        idx = data.rfind(b"OggS")
        if idx < 0:
            return 0
        granule = int.from_bytes(data[idx + 6:idx + 14], "little")
        return max(1, round(granule / 48000))
    except Exception:
        return 0


def _ffmpeg_path() -> Optional[str]:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


async def _to_ogg_opus(data: bytes) -> Optional[bytes]:
    """MP3 → OGG/Opus через ffmpeg (если он есть на сервере)."""
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "-ac", "1", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(data), timeout=60)
        if proc.returncode == 0 and out.startswith(b"OggS"):
            return out
        logger.warning(f"[AGENT-VOICE] ffmpeg failed ({proc.returncode}): {err[:200]!r}")
    except Exception as e:
        logger.warning(f"[AGENT-VOICE] ffmpeg error: {e}")
    return None


async def _tts_eleven(text: str, voice: Dict[str, Any], fmt: str) -> Dict[str, Any]:
    output_format = "opus_48000_64" if fmt == "opus" else "mp3_44100_128"
    body = {
        "text": text,
        "model_id": voice["model"],
        "language_code": voice["language"],
        "voice_settings": {"stability": voice["stability"]},
    }
    url = f"{ELEVEN_API}/text-to-speech/{voice['voice_id']}"
    headers = {"xi-api-key": settings.ELEVENLABS_API_KEY or "", "Accept": "audio/*"}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, params={"output_format": output_format}, json=body, headers=headers)
            if resp.status_code >= 400 and "language_code" in resp.text:
                # Модель не приняла язык — без language_code (текст и так на нужном языке).
                body.pop("language_code", None)
                resp = await client.post(url, params={"output_format": output_format}, json=body, headers=headers)
    except Exception as e:
        logger.warning(f"[AGENT-VOICE] ElevenLabs request failed: {type(e).__name__}: {e}")
        return {"ok": False, "error": "tts_unavailable"}
    if resp.status_code >= 400:
        logger.warning(f"[AGENT-VOICE] ElevenLabs {output_format} → {resp.status_code}: {resp.text[:300]}")
        return {"ok": False, "error": f"tts_error_{resp.status_code}", "detail": resp.text[:200]}
    return {"ok": True, "data": resp.content}


async def _tts_openai(text: str, voice: Dict[str, Any], fmt: str) -> Dict[str, Any]:
    body = {
        "model": voice.get("model") or settings.AGENT_VOICE_OPENAI_TTS_MODEL,
        "voice": voice["voice_id"] if voice["voice_id"] in OPENAI_TTS_VOICES else DEFAULT_OPENAI_VOICE,
        "input": text,
        "instructions": openai_instructions(voice.get("language")),
        "response_format": "opus" if fmt == "opus" else "mp3",
    }
    headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY or ''}"}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(OPENAI_SPEECH_URL, json=body, headers=headers)
            if resp.status_code >= 400 and "instructions" in resp.text:
                body.pop("instructions", None)  # модель без управления манерой
                resp = await client.post(OPENAI_SPEECH_URL, json=body, headers=headers)
            if resp.status_code >= 400 and "voice" in resp.text.lower() and body["voice"] != DEFAULT_OPENAI_VOICE:
                # Модель не знает этот голос — озвучиваем голосом по умолчанию, а не падаем.
                logger.warning(f"[AGENT-VOICE] OpenAI voice {body['voice']!r} rejected: {resp.text[:200]} — using {DEFAULT_OPENAI_VOICE}")
                body["voice"] = DEFAULT_OPENAI_VOICE
                resp = await client.post(OPENAI_SPEECH_URL, json=body, headers=headers)
    except Exception as e:
        logger.warning(f"[AGENT-VOICE] OpenAI speech request failed: {type(e).__name__}: {e}")
        return {"ok": False, "error": "tts_unavailable"}
    if resp.status_code >= 400:
        logger.warning(f"[AGENT-VOICE] OpenAI speech {fmt} → {resp.status_code}: {resp.text[:300]}")
        return {"ok": False, "error": f"tts_error_{resp.status_code}", "detail": resp.text[:200]}
    return {"ok": True, "data": resp.content}


# ---------------------------------------------------------------------------
# GPT-Live (gpt-live-1): та же модель, что в звонках и в голосовом режиме ChatGPT.
# Сессия Live без собеседника: просим дословно произнести текст и собираем
# выходной PCM16 24 кГц, пока звук не закончится. Модель говорит в реальном
# темпе, поэтому 30-секундное голосовое готовится ~30 с.
# ---------------------------------------------------------------------------

LIVE_RATE = 24000
_LIVE_SILENCE = b"\x00\x00" * (LIVE_RATE // 10)   # 100 мс тишины на вход


def _live_prompt(language: Optional[str]) -> str:
    return (
        "Ты записываешь голосовое сообщение клиенту в мессенджере, как живой менеджер на "
        "телефон. Тебе пришлют текст — произнеси его ДОСЛОВНО: не добавляй приветствий, "
        "вопросов и комментариев, ничего не пропускай и не пересказывай. После текста сразу "
        "замолчи. " + openai_instructions(language)
    )


async def _gpt_live_speak(text: str, voice: Dict[str, Any], timeout: float = 120.0) -> Dict[str, Any]:
    """Текст → {ok, pcm (PCM16 24 кГц mono), transcript, seconds} через GPT-Live."""
    import base64
    import time
    import types
    from backend.websockets.live_client import OpenAILiveClient

    cfg = types.SimpleNamespace(system_prompt=_live_prompt(voice.get("language")), functions=None,
                                voice=voice["voice_id"])
    client = OpenAILiveClient(settings.OPENAI_API_KEY, cfg, client_id=f"voice-msg-{int(time.time())}",
                              db_session=None, audio_rate=LIVE_RATE, voice_override=voice["voice_id"])
    if not await client.connect():
        return {"ok": False, "error": "live_unavailable"}

    pcm = bytearray()
    transcript = []
    stop = asyncio.Event()

    async def feed_silence():
        # Live — full-duplex: держим входной поток живым тишиной, как в звонке.
        while not stop.is_set():
            await client.send_audio(_LIVE_SILENCE)
            await asyncio.sleep(0.1)

    feeder = asyncio.create_task(feed_silence())
    started = time.monotonic()
    last_audio = None
    try:
        await client.append_instructions(
            "Произнеси дословно этот текст голосового сообщения и замолчи:\n«" + text + "»"
        )
        events = client.receive_events()
        while True:
            now = time.monotonic()
            if now - started > timeout:
                break
            if last_audio is None and now - started > 20:
                break  # модель так и не заговорила
            if last_audio is not None and now - last_audio > 1.6:
                break  # звук закончился
            try:
                event = await asyncio.wait_for(events.__anext__(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            except StopAsyncIteration:
                break
            etype = event.get("type")
            if etype == "session.output_audio.delta":
                chunk = event.get("delta") or ""
                if chunk:
                    pcm.extend(base64.b64decode(chunk))
                    last_audio = time.monotonic()
            elif etype == "session.output_transcript.delta":
                transcript.append(event.get("delta") or event.get("text") or "")
    except Exception as e:
        logger.warning(f"[AGENT-VOICE] GPT-Live synthesis error: {type(e).__name__}: {e}")
    finally:
        stop.set()
        feeder.cancel()
        try:
            await client.close(timeout=5)
        except Exception:
            pass

    if len(pcm) < LIVE_RATE:  # меньше 0.5 с звука
        return {"ok": False, "error": "live_no_audio"}
    said = "".join(transcript).strip()
    logger.info(f"[AGENT-VOICE] GPT-Live voice={voice['voice_id']} {len(pcm) / 2 / LIVE_RATE:.1f}s, said={said[:120]!r}")
    return {"ok": True, "pcm": bytes(pcm), "transcript": said, "seconds": len(pcm) / 2 / LIVE_RATE}


def _pcm_to_wav(pcm: bytes, rate: int = LIVE_RATE) -> bytes:
    import io
    import wave
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return bio.getvalue()


async def _pcm_to_ogg(pcm: bytes, rate: int = LIVE_RATE) -> Optional[bytes]:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1",
            "-i", "pipe:0", "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(pcm), timeout=60)
        if proc.returncode == 0 and out.startswith(b"OggS"):
            return out
        logger.warning(f"[AGENT-VOICE] ffmpeg pcm→ogg failed ({proc.returncode}): {err[:200]!r}")
    except Exception as e:
        logger.warning(f"[AGENT-VOICE] ffmpeg pcm→ogg error: {e}")
    return None


async def _synthesize_live(text: str, voice: Dict[str, Any]) -> Dict[str, Any]:
    res = await _gpt_live_speak(text, voice)
    if not res.get("ok"):
        return res
    pcm, secs = res["pcm"], res["seconds"]
    ogg = await _pcm_to_ogg(pcm)
    base = {"ok": True, "engine_used": "gpt_live", "seconds": secs, "spoken": res.get("transcript")}
    if ogg:
        return dict(base, data=ogg, mime="audio/ogg", filename="voice.ogg", is_ogg=True,
                    duration=max(1, round(secs)))
    return dict(base, data=_pcm_to_wav(pcm), mime="audio/wav", filename="voice.wav", is_ogg=False,
                duration=max(1, round(secs)))


async def _tts(text: str, voice: Dict[str, Any], fmt: str) -> Dict[str, Any]:
    """fmt: 'opus' | 'mp3'."""
    if voice.get("engine") == ENGINE_OPENAI:
        return await _tts_openai(text, voice, fmt)
    return await _tts_eleven(text, voice, fmt)


async def synthesize(text: str, voice: Dict[str, Any]) -> Dict[str, Any]:
    """
    Текст → {ok, data, mime, filename, is_ogg, duration} или {ok: False, error}.
    is_ogg=True — можно слать голосовым (voice note) в Telegram.
    """
    engine = voice.get("engine") or ENGINE_ELEVEN
    if not is_configured(engine):
        return {"ok": False, "error": "tts_not_configured"}
    text = prepare_text(text, engine)
    if not strip_tags(text):
        return {"ok": False, "error": "empty_text"}
    voice = dict(voice, language=detect_language(text, voice.get("language")))

    if engine == ENGINE_OPENAI and settings.AGENT_VOICE_OPENAI_USE_LIVE:
        res = await _synthesize_live(text, voice)
        if res.get("ok"):
            return res
        logger.warning(f"[AGENT-VOICE] GPT-Live failed ({res.get('error')}) — falling back to OpenAI speech")

    res = await _tts(text, voice, "opus")
    if res.get("ok") and res["data"].startswith(b"OggS"):
        data = res["data"]
        return {"ok": True, "data": data, "mime": "audio/ogg", "filename": "voice.ogg",
                "is_ogg": True, "duration": ogg_duration_seconds(data)}
    if res.get("ok"):
        logger.info(f"[AGENT-VOICE] {engine} opus output is not OGG — falling back to mp3")

    res = await _tts(text, voice, "mp3")
    if not res.get("ok"):
        return res
    mp3 = res["data"]
    ogg = await _to_ogg_opus(mp3)
    if ogg:
        return {"ok": True, "data": ogg, "mime": "audio/ogg", "filename": "voice.ogg",
                "is_ogg": True, "duration": ogg_duration_seconds(ogg)}
    return {"ok": True, "data": mp3, "mime": "audio/mpeg", "filename": "voice.mp3",
            "is_ogg": False, "duration": 0}


async def synthesize_preview(text: str, voice: Dict[str, Any]) -> Dict[str, Any]:
    """Пример голоса для настроек: {ok, data, mime[, seconds, engine_used]} (MP3 или WAV)."""
    engine = voice.get("engine") or ENGINE_ELEVEN
    if not is_configured(engine):
        return {"ok": False, "error": "tts_not_configured"}
    text = prepare_text(text, engine)
    voice = dict(voice, language=detect_language(text, voice.get("language")))
    if engine == ENGINE_OPENAI and settings.AGENT_VOICE_OPENAI_USE_LIVE:
        res = await _gpt_live_speak(text, voice)
        if res.get("ok"):
            return {"ok": True, "data": _pcm_to_wav(res["pcm"]), "mime": "audio/wav",
                    "seconds": res["seconds"], "engine_used": "gpt_live"}
        logger.warning(f"[AGENT-VOICE] GPT-Live preview failed ({res.get('error')}) — falling back to OpenAI speech")
    res = await _tts(text, voice, "mp3")
    return dict(res, mime="audio/mpeg") if res.get("ok") else res


# ============================================================================
# УЧЁТ
# ============================================================================

def credits_for(text: str, engine: str = ENGINE_ELEVEN, seconds: Optional[float] = None) -> int:
    """
    Кредиты за озвучку. GPT-Live — по секундам звука (VOICE_COST_USD_PER_MIN_GPT_LIVE;
    без длительности — оценка ≈ 14 символов в секунду), OpenAI Speech и ElevenLabs — по символам.
    """
    from backend.services.agent_models import _CREDITS_PER_USD
    text = strip_tags(text)
    if not text:
        return 0
    if engine == ENGINE_OPENAI and settings.AGENT_VOICE_OPENAI_USE_LIVE:
        secs = seconds if seconds else len(text) / 14.0
        usd = secs / 60.0 * max(0.0, settings.VOICE_COST_USD_PER_MIN_GPT_LIVE)
    else:
        rate = (settings.AGENT_VOICE_OPENAI_USD_PER_1K_CHARS if engine == ENGINE_OPENAI
                else settings.AGENT_VOICE_USD_PER_1K_CHARS)
        usd = len(text) / 1000.0 * max(0.0, rate)
    return max(1, math.ceil(usd * _CREDITS_PER_USD))


def charge(db, user_id, text: str, ref_id=None, notes: str = "", engine: str = ENGINE_ELEVEN,
           seconds: Optional[float] = None) -> int:
    """Списать озвучку с кредитов агента (в ноль, не в минус). Возвращает сумму."""
    from backend.services.credit_service import CreditService
    amount = credits_for(text, engine, seconds)
    if amount <= 0:
        return 0
    if engine == ENGINE_OPENAI:
        from backend.websockets.live_client import LIVE_MODEL
        model = LIVE_MODEL if (seconds and settings.AGENT_VOICE_OPENAI_USE_LIVE) else settings.AGENT_VOICE_OPENAI_TTS_MODEL
    else:
        model = settings.AGENT_VOICE_TTS_MODEL
    try:
        CreditService.charge_amount(
            db, user_id, amount, ref_type="agent_voice_tts", ref_id=ref_id,
            model_slug=f"{'openai' if engine == ENGINE_OPENAI else 'elevenlabs'}/{model}",
            notes=notes or f"Голосовое сообщение, {len(strip_tags(text))} симв.",
        )
    except Exception as e:
        logger.error(f"[AGENT-VOICE] charge failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    return amount


# ============================================================================
# ПЕРЕПИСКА: КОГДА ГОЛОСОМ И БЕЗ ПОВТОРОВ
# ============================================================================

def last_inbound_is_voice(rows) -> bool:
    """Последнее входящее клиента в треде — голосовое (распознанное или нет)."""
    from backend.services.agent_media_service import KIND_LABELS
    for m in reversed(list(rows or [])):
        if (m.direction or "inbound") == "inbound":
            return (m.body or "").lstrip().startswith(KIND_LABELS["voice"])
    return False


def _agent_turns(rows, limit: int = 8) -> list:
    """
    Последние ответы агента в треде: серия его исходящих подряд = один ответ
    (ответ режется на несколько сообщений). True — в ответе было голосовое.
    Сообщения владельца не считаются.
    """
    turns, current = [], None
    for m in rows or []:
        if (m.direction or "inbound") != "outbound":
            if current is not None:
                turns.append(current)
                current = None
            continue
        if getattr(m, "sent_by", None) == "owner":
            continue
        is_voice = (m.body or "").startswith(VOICE_BODY_PREFIX)
        current = (current or False) or is_voice
    if current is not None:
        turns.append(current)
    return turns[-limit:]


def should_reply_with_voice(rows, share_percent: int) -> bool:
    """
    Режим «агент решает сам»: держим долю голосовых ответов около share_percent
    (по последним 10 ответам агента). Голосом — пока доля ниже заданной; два
    голосовых подряд — только при доле от 50%. Первый ответ в чате — текстом.
    """
    turns = _agent_turns(rows, limit=10)
    if not turns:
        return False
    if turns[-1] and share_percent < 50:
        return False
    return sum(1 for t in turns if t) / len(turns) * 100 < share_percent


def _norm_words(text: str) -> list:
    return re.findall(r"\w+", strip_tags(text).lower())


def repeats_recent_voice(rows, text: str, window_seconds: int = 600) -> bool:
    """
    Текст почти повторяет последнее голосовое агента в этом треде (за 10 минут)?
    Дополнение (адрес, список вопросов, номер) — не повтор: проверяем, что
    большая часть слов текста уже прозвучала в голосовом.
    """
    from datetime import datetime, timedelta
    words = _norm_words(text)
    if len(words) < 4:
        return False
    since = datetime.utcnow() - timedelta(seconds=window_seconds)
    for m in reversed(list(rows or [])):
        if (m.direction or "inbound") != "outbound" or getattr(m, "sent_by", None) == "owner":
            continue
        body = m.body or ""
        if not body.startswith(VOICE_BODY_PREFIX):
            continue
        ts = getattr(m, "sent_at", None) or m.created_at
        if ts is not None and ts < since:
            return False
        spoken = _norm_words(body[len(VOICE_BODY_PREFIX):])
        if not spoken:
            return False
        spoken_set = set(spoken)
        covered = sum(1 for w in words if w in spoken_set) / len(words)
        ratio = SequenceMatcher(None, " ".join(words), " ".join(spoken)).ratio()
        return covered >= 0.7 or ratio >= 0.6
    return False


def prompt_hint(db, agent_config, channel: str, contact_id) -> str:
    """Строка для события входящего сообщения: каким видом отвечать сейчас."""
    try:
        if channel not in ("telegram", "whatsapp"):
            return ""
        if channel == "telegram":
            from backend.services import telegram_user_service as svc
        else:
            from backend.services import whatsapp_service as svc
        account = svc.get_account_for_agent(db, agent_config.id)
        if account is None or resolve_voice(db, agent_config) is None:
            return ""
        mode = voice_reply_mode(account)
        if mode == "never":
            return ""
        rows = svc.get_thread(db, contact_id, limit=30)
        if last_inbound_is_voice(rows):
            return ("\n\nГОЛОСОВЫЕ ОТВЕТЫ: клиент прислал голосовое — ответь ему голосовым "
                    "(send_voice_message). Данные (адрес, цены, ссылки, номера, список вопросов) — "
                    "отдельным текстом, не пересказывая голосовое.")
        if mode == "auto":
            if should_reply_with_voice(rows, auto_share(agent_config)):
                return ("\n\nГОЛОСОВЫЕ ОТВЕТЫ: сейчас ответь голосовым (send_voice_message), если "
                        "ответ не сводится к короткому «да / хорошо / минуту». Данные — отдельным "
                        "текстом, не пересказывая голосовое.")
            return "\n\nГОЛОСОВЫЕ ОТВЕТЫ: сейчас ответь текстом."
        return "\n\nГОЛОСОВЫЕ ОТВЕТЫ: клиент писал текстом — отвечай текстом."
    except Exception as e:
        logger.warning(f"[AGENT-VOICE] prompt_hint failed: {e}")
        return ""
