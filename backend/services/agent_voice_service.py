"""
Голосовые сообщения агента в мессенджерах (ветка 1210-chat-v2, задача 3.4).

Агент отвечает клиенту голосовым в личном Telegram и WhatsApp. Озвучка —
ElevenLabs Text-to-Speech на серверном ELEVENLABS_API_KEY (модель
AGENT_VOICE_TTS_MODEL, по умолчанию eleven_v3: единственное семейство с
кыргызским). Голос / язык / подача — из настроек агента «Голосовые сообщения»
(agent_configs.voice_msg_*), пустые поля берутся из Eleven-карточки звонков
(agent_configs.eleven_assistant_id) — даже если звонки идут через GPT-Live.
Нет voice_id ни там, ни там → голосовых нет.

Формат: просим у ElevenLabs Opus (opus_48000_64). Если пришёл OGG — это
готовое голосовое Telegram (voice_note, длительность из последней страницы
OGG). Иначе берём MP3 и, если на сервере есть ffmpeg, перекодируем в OGG/Opus;
без ffmpeg Telegram получит аудиофайл. WhatsApp-шлюз (Evolution,
sendWhatsAppAudio) перекодирует сам — ему подходит любой из форматов.
Instagram (Composio) аудио отправлять не умеет — там голосовых нет.

Режим — настройка коннектора voice_reply_mode: never | on_voice (только в
ответ на голосовое клиента) | auto (агент решает сам). Стоимость озвучки —
кредиты агента (AGENT_VOICE_USD_PER_1K_CHARS по курсу оркестратора).
"""

import asyncio
import math
import shutil
from typing import Any, Dict, Optional

import httpx

from backend.core.config import settings
from backend.core.logging import get_logger

logger = get_logger(__name__)

ELEVEN_API = "https://api.elevenlabs.io/v1"

VOICE_REPLY_MODES = ("never", "on_voice", "auto")
DEFAULT_VOICE_REPLY_MODE = "on_voice"

# Метка исходящего голосового в треде (агент и владелец видят содержание).
VOICE_BODY_PREFIX = "🎤 Голосовое: "


def is_configured() -> bool:
    return bool(settings.ELEVENLABS_API_KEY)


def voice_reply_mode(row) -> str:
    mode = getattr(row, "voice_reply_mode", None) or DEFAULT_VOICE_REPLY_MODE
    return mode if mode in VOICE_REPLY_MODES else DEFAULT_VOICE_REPLY_MODE


# ============================================================================
# ГОЛОС АГЕНТА
# ============================================================================

def resolve_voice(db, agent_config) -> Optional[Dict[str, Any]]:
    """
    Голос для голосовых сообщений: {voice_id, voice_name, language, stability,
    model, source} или None (голос не выбран нигде).
    """
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
        stability = card.stability if (card and card.stability is not None) else 0.5
    return {
        "voice_id": voice_id,
        "voice_name": (agent_config.voice_msg_voice_name if own else None) or None,
        "language": language,
        "stability": float(stability),
        "model": settings.AGENT_VOICE_TTS_MODEL,
        "source": "own" if own else "call_card",
    }


# ============================================================================
# СИНТЕЗ
# ============================================================================

def ogg_duration_seconds(data: bytes) -> int:
    """Длительность OGG/Opus: granule последней страницы / 48000 (минус pre-skip не важен)."""
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


async def _tts(text: str, voice: Dict[str, Any], output_format: str) -> Dict[str, Any]:
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


async def synthesize(text: str, voice: Dict[str, Any]) -> Dict[str, Any]:
    """
    Текст → {ok, data, mime, filename, is_ogg, duration} или {ok: False, error}.
    is_ogg=True — можно слать голосовым (voice note) в Telegram.
    """
    if not is_configured():
        return {"ok": False, "error": "tts_not_configured"}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty_text"}

    res = await _tts(text, voice, "opus_48000_64")
    if res.get("ok") and res["data"].startswith(b"OggS"):
        data = res["data"]
        return {"ok": True, "data": data, "mime": "audio/ogg", "filename": "voice.ogg",
                "is_ogg": True, "duration": ogg_duration_seconds(data)}
    if res.get("ok"):
        logger.info("[AGENT-VOICE] opus output is not OGG — falling back to mp3")

    res = await _tts(text, voice, "mp3_44100_128")
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
    """Пример голоса для настроек (MP3 — играет в любом браузере)."""
    if not is_configured():
        return {"ok": False, "error": "tts_not_configured"}
    return await _tts(text, voice, "mp3_44100_128")


# ============================================================================
# УЧЁТ
# ============================================================================

def credits_for(text: str) -> int:
    from backend.services.agent_models import _CREDITS_PER_USD
    usd = len(text or "") / 1000.0 * max(0.0, settings.AGENT_VOICE_USD_PER_1K_CHARS)
    return max(1, math.ceil(usd * _CREDITS_PER_USD)) if text else 0


def charge(db, user_id, text: str, ref_id=None, notes: str = "") -> int:
    """Списать озвучку с кредитов агента (в ноль, не в минус). Возвращает сумму."""
    from backend.services.credit_service import CreditService
    amount = credits_for(text)
    if amount <= 0:
        return 0
    try:
        CreditService.charge_amount(
            db, user_id, amount, ref_type="agent_voice_tts", ref_id=ref_id,
            model_slug=f"elevenlabs/{settings.AGENT_VOICE_TTS_MODEL}",
            notes=notes or f"Голосовое сообщение, {len(text)} симв.",
        )
    except Exception as e:
        logger.error(f"[AGENT-VOICE] charge failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    return amount


def last_inbound_is_voice(rows) -> bool:
    """Последнее входящее клиента в треде — голосовое (распознанное или нет)."""
    from backend.services.agent_media_service import KIND_LABELS
    for m in reversed(list(rows or [])):
        if (m.direction or "inbound") == "inbound":
            return (m.body or "").lstrip().startswith(KIND_LABELS["voice"])
    return False


def prompt_hint(db, agent_config, channel: str, contact_id) -> str:
    """Строка для события входящего сообщения: можно ли сейчас ответить голосом."""
    try:
        if not is_configured() or channel not in ("telegram", "whatsapp"):
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
        if mode == "auto":
            return ("\n\nГОЛОСОВЫЕ ОТВЕТЫ: на твоё усмотрение — можешь ответить голосовым "
                    "(send_voice_message) или текстом.")
        if last_inbound_is_voice(svc.get_thread(db, contact_id, limit=10)):
            return ("\n\nГОЛОСОВЫЕ ОТВЕТЫ: клиент прислал голосовое — ответь ему голосовым "
                    "(send_voice_message); цены, адреса и ссылки продублируй текстом.")
        return "\n\nГОЛОСОВЫЕ ОТВЕТЫ: клиент писал текстом — отвечай текстом."
    except Exception as e:
        logger.warning(f"[AGENT-VOICE] prompt_hint failed: {e}")
        return ""
