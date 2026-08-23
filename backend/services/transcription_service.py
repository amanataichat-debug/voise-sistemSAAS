"""
TranscriptionService — распознавание речи (STT) для веб-чата и Telegram-бота агента.

Единый сервис для обоих каналов. Провайдер по умолчанию — OpenAI
(/v1/audio/transcriptions, модель gpt-4o-mini-transcribe). Если OpenAI недоступен
или не настроен — fallback на OpenRouter (/api/v1/audio/transcriptions).

Оба эндпоинта принимают multipart-файл и совместимы по формату ответа ({"text": ...}).
OpenAI принимает OGG/Opus (голосовые Telegram) и webm/mp4 (запись из браузера)
без перекодирования.
"""

from typing import Optional

import httpx

from backend.core.config import settings
from backend.core.logging import get_logger

logger = get_logger(__name__)

OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENROUTER_TRANSCRIBE_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
REQUEST_TIMEOUT = 60.0


async def _post_transcription(
    url: str,
    api_key: str,
    model: str,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    language: Optional[str] = None,
    extra_headers: Optional[dict] = None,
) -> str:
    """Низкоуровневый multipart-вызов /audio/transcriptions. Бросает при не-200."""
    headers = {"Authorization": f"Bearer {api_key}"}
    if extra_headers:
        headers.update(extra_headers)
    data = {"model": model}
    if language:
        data["language"] = language
    files = {"file": (filename, audio_bytes, content_type)}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, data=data, files=files)
    if resp.status_code != 200:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:300]}")
    out = resp.json()
    return (out.get("text") or "").strip()


class TranscriptionService:
    """STT с провайдером OpenAI и fallback на OpenRouter."""

    @staticmethod
    async def transcribe(
        audio_bytes: bytes,
        filename: str = "audio.ogg",
        content_type: str = "audio/ogg",
        language: Optional[str] = None,
    ) -> Optional[str]:
        """
        Распознаёт речь из аудио-байтов. Пробует OpenAI, затем OpenRouter.
        Возвращает распознанный текст (может быть пустой строкой) или None, если
        оба провайдера недоступны/упали.
        """
        if not audio_bytes:
            return None
        lang = language or settings.STT_LANGUAGE or None

        # 1. OpenAI — основной провайдер
        if settings.OPENAI_API_KEY:
            try:
                return await _post_transcription(
                    OPENAI_TRANSCRIBE_URL,
                    settings.OPENAI_API_KEY,
                    settings.STT_OPENAI_MODEL,
                    audio_bytes, filename, content_type, lang,
                )
            except Exception as e:
                logger.warning(f"[STT] OpenAI transcription failed, trying fallback: {e}")

        # 2. OpenRouter — fallback
        if settings.OPENROUTER_API_KEY:
            try:
                return await _post_transcription(
                    OPENROUTER_TRANSCRIBE_URL,
                    settings.OPENROUTER_API_KEY,
                    settings.STT_OPENROUTER_MODEL,
                    audio_bytes, filename, content_type, lang,
                    extra_headers={
                        "HTTP-Referer": settings.HOST_URL or "",
                        "X-Title": "Voksy AI",
                    },
                )
            except Exception as e:
                logger.error(f"[STT] OpenRouter transcription failed: {e}")

        return None
