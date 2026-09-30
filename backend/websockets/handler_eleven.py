# backend/websockets/handler_eleven.py
"""
Хендлер Eleven-ассистента: OpenAI Realtime (текст) + ElevenLabs (озвучка).

Тот же «половинный каскад», что у Fish (handler_fish.py): вся логика диалога —
FishVoiceSession, диалоговая модель — FishLLMClient, отличается только клиент
синтеза (ElevenTTSClient, Text-to-Dialogue WebSocket, Eleven v3) и таблицы
(eleven_assistant_configs / eleven_conversations).

Роут /ws/eleven/{assistant_id} (api/eleven_ws.py) и телефон через
SIP_HANDLERS["eleven"]. Протокол клиента — виджета (widget.js с
data-ws-path="/ws/eleven/"). Ключи серверные: OPENAI_API_KEY и ELEVENLABS_API_KEY.
Язык ответа берётся из карточки (по умолчанию кыргызский): ElevenLabs получает
language_code, диалоговой модели дописывается инструкция отвечать на этом языке.

По умолчанию (settings.ELEVEN_ASR_ENABLED) работает режим «ASR → текст»:
    звук абонента → Scribe Realtime (ASR + VAD, пауза ELEVEN_ASR_SILENCE_MS)
    → готовая фраза текстом → OpenAI Realtime (текст → текст) → ElevenLabs TTS.
Если Scribe не подключился, звонок идёт по прежней схеме (звук напрямую в OpenAI).
"""

import traceback
import uuid

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.eleven_assistant import (
    DEFAULT_ELEVEN_GREETING,
    ELEVEN_SAMPLE_RATE,
    ElevenAssistantConfig,
    ElevenConversation,
)
from backend.models.user import User
from backend.websockets.eleven_tts_client import ElevenTTSClient
from backend.websockets.scribe_stt_client import ScribeSTTClient
from backend.websockets.fish_llm_client import INPUT_RATE as LLM_INPUT_RATE, FishLLMClient
from backend.websockets.handler_fish import LOG_TAG, FishVoiceSession

logger = get_logger(__name__)


def _log(message: str, level: str = "INFO") -> None:
    if level == "ERROR":
        logger.error(f"[ELEVEN] {message}")
    elif level == "WARNING":
        logger.warning(f"[ELEVEN] {message}")
    else:
        logger.info(f"[ELEVEN] {message}")


_tables_ready = False


def _ensure_tables() -> None:
    """Создать eleven_* таблицы, если их нет (страховка, как у Fish и SIP)."""
    global _tables_ready
    if _tables_ready:
        return
    try:
        from backend.models.base import Base, engine
        Base.metadata.create_all(
            engine, tables=[ElevenAssistantConfig.__table__, ElevenConversation.__table__], checkfirst=True,
        )
        _tables_ready = True
    except Exception as exc:
        _log(f"ensure tables failed: {exc}", "ERROR")


async def handle_eleven_websocket_connection(websocket: WebSocket, assistant_id: str, db: Session) -> None:
    """Точка входа для /ws/eleven/{assistant_id} и для SIP-адаптера."""
    LOG_TAG.set("ELEVEN")  # логи FishVoiceSession в этой задаче помечаются как Eleven
    client_id = str(uuid.uuid4())
    await websocket.accept()
    _ensure_tables()

    async def fail(code: str, message: str, ws_code: int = 1008) -> None:
        _log(f"{code}: {message} (assistant {assistant_id})", "WARNING")
        try:
            await websocket.send_json({"type": "error", "error": {"code": code, "message": message}})
            await websocket.close(code=ws_code)
        except Exception:
            pass

    try:
        try:
            assistant_uuid = uuid.UUID(str(assistant_id))
        except ValueError:
            await fail("assistant_not_found", "Assistant not found")
            return
        assistant = db.query(ElevenAssistantConfig).filter(ElevenAssistantConfig.id == assistant_uuid).first()
        if not assistant:
            await fail("assistant_not_found", "Assistant not found")
            return
        if not assistant.is_active:
            await fail("assistant_inactive", "Assistant is inactive")
            return
        if not (assistant.voice_id or "").strip():
            await fail("voice_not_set", "У агента не выбран голос ElevenLabs")
            return

        user = db.query(User).filter(User.id == assistant.user_id).first() if assistant.user_id else None
        if user and not user.is_admin and user.email != "amanat.aichat@gmail.com":
            from backend.services.user_service import UserService
            sub = await UserService.check_subscription_status(db, str(user.id))
            if not sub.get("active"):
                code = "TRIAL_EXPIRED" if sub.get("is_trial") else "SUBSCRIPTION_EXPIRED"
                msg = "Ваш пробный период истек" if sub.get("is_trial") else "Ваша подписка истекла"
                try:
                    await websocket.send_json({"type": "error", "error": {
                        "code": code, "message": msg, "subscription_status": sub, "requires_payment": True}})
                    await websocket.close(code=1008)
                except Exception:
                    pass
                return

        if not settings.OPENAI_API_KEY:
            await fail("openai_not_configured", "OPENAI_API_KEY is not configured on the server", 1011)
            return
        if not settings.ELEVENLABS_API_KEY:
            await fail("elevenlabs_not_configured", "ELEVENLABS_API_KEY is not configured on the server", 1011)
            return

        telephony = bool(getattr(assistant, "telephony_mode", False))
        user_agent = ""
        try:
            user_agent = websocket.headers.get("user-agent", "")
        except Exception:
            pass

        # ASR → текст: Scribe поднимаем первым — от него зависит, в каком режиме открыть OpenAI.
        # Виджет и SIP-адаптер (HANDLER_IN_RATE["eleven"]) шлют PCM16 24 кГц.
        stt = None
        if settings.ELEVEN_ASR_ENABLED:
            stt = ScribeSTTClient(
                settings.ELEVENLABS_API_KEY,
                language=(assistant.language or "").strip().lower(),
                sample_rate=LLM_INPUT_RATE,
                silence_ms=settings.ELEVEN_ASR_SILENCE_MS,
                label=client_id[:8],
            )
            if not await stt.connect():
                _log(f"Scribe unavailable ({stt.fatal_error or 'connect failed'}), "
                     f"session {client_id} uses audio input in OpenAI", "WARNING")
                stt = None

        llm = FishLLMClient(settings.OPENAI_API_KEY, assistant, client_id, db, user_agent, telephony=telephony,
                            conversation_model=ElevenConversation, label="ELEVEN-LLM",
                            text_input=stt is not None)
        if not await llm.connect():
            if stt is not None:
                await stt.close()
            await fail("openai_connection_failed", "Failed to connect to OpenAI", 1011)
            return

        session = FishVoiceSession(websocket, assistant, llm, None, db, client_id, provider="eleven")
        if stt is not None:
            session.attach_stt(stt)
        tts = ElevenTTSClient(
            settings.ELEVENLABS_API_KEY, assistant, ELEVEN_SAMPLE_RATE,
            on_audio=session.on_tts_audio,
            on_speech_started=session.on_tts_speech_started,
            on_speech_ended=session.on_tts_speech_ended,
            label=client_id[:8],
        )
        session.tts = tts
        try:
            await tts.connect()
        except Exception as exc:
            _log(f"ElevenLabs connect failed: {exc}", "ERROR")
            if stt is not None:
                await stt.close()
            await llm.close()
            await fail("elevenlabs_connection_failed", f"Failed to connect to ElevenLabs: {exc}", 1011)
            return

        await websocket.send_json({
            "type": "connection_status",
            "status": "connected",
            "provider": "eleven",
            "message": f"Connected: {'Scribe ASR + ' if stt else ''}OpenAI {llm.model} (text) + ElevenLabs {tts.model}",
            "asr": "scribe" if stt else None,
            "model": llm.model,
            "tts_model": tts.model,
            "voice_id": tts.voice_id,
            "language": tts.language,
            "functions_enabled": len(llm.enabled_functions),
            "client_id": client_id,
            "telephony": telephony,
            "greeting_message": assistant.greeting_message or DEFAULT_ELEVEN_GREETING,
        })
        _log(f"session {client_id} started: assistant={assistant.id} '{assistant.name}' voice={tts.voice_id} "
             f"model={tts.model} lang={tts.language} telephony={telephony} "
             f"input={'scribe asr' if stt else 'openai audio'}")

        await session.run()

    except WebSocketDisconnect:
        _log(f"client disconnected before start: {client_id}")
    except Exception as exc:
        _log(f"fatal: {exc}\n{traceback.format_exc()}", "ERROR")
        try:
            await websocket.send_json({"type": "error", "error": {"code": "server_error", "message": "Internal server error"}})
        except Exception:
            pass
