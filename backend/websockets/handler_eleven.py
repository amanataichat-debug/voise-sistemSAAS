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

import asyncio
import time
import traceback
import uuid
from collections import OrderedDict
from typing import Optional

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
from backend.websockets.call_log import CallLogRecorder
from backend.websockets.eleven_tts_client import ElevenTTSClient
from backend.websockets.scribe_stt_client import ScribeSTTClient
from backend.websockets.openai_stt_client import OpenAISTTClient
from backend.websockets.yandex_stt_client import YandexSTTClient
from backend.websockets.google_stt_client import GoogleSTTClient, parse_credentials_json
from backend.websockets.chat_llm_client import ChatLLMClient
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


# Кэш синтезированного приветствия (PCM 24 кГц) по голосу, модели, языку, стабильности и тексту:
# со второго звонка приветствие звучит сразу при подъёме трубки, без ожидания ElevenLabs.
# Свой в каждом воркере Gunicorn; при смене текста/голоса ключ другой — запись пересоздаётся.
_GREETING_CACHE: "OrderedDict[tuple, bytes]" = OrderedDict()
_GREETING_CACHE_MAX = 200


def _greeting_cache_key(assistant, tts, text: str) -> tuple:
    return (tts.voice_id, tts.model, tts.language, round(float(tts.stability or 0), 2), text)


def _greeting_cache_put(key: tuple, pcm: bytes) -> None:
    if len(pcm) < ELEVEN_SAMPLE_RATE:  # меньше 0,5 с — что-то пошло не так, не кэшируем
        return
    _GREETING_CACHE[key] = pcm
    _GREETING_CACHE.move_to_end(key)
    while len(_GREETING_CACHE) > _GREETING_CACHE_MAX:
        _GREETING_CACHE.popitem(last=False)


async def _check_subscription(assistant) -> Optional[dict]:
    """Статус подписки владельца (None — проверка не нужна). Синхронная БД — в отдельном потоке,
    своей сессией, параллельно с подключениями к провайдерам."""
    if not assistant.user_id:
        return None

    def run() -> Optional[dict]:
        from backend.db.session import SessionLocal
        from backend.services.user_service import UserService
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == assistant.user_id).first()
            if not user or user.is_admin or user.email == "amanat.aichat@gmail.com":
                return None
            return asyncio.run(UserService.check_subscription_status(db, str(user.id)))
        finally:
            db.close()

    try:
        return await asyncio.to_thread(run)
    except Exception as exc:
        _log(f"subscription check failed: {exc}", "WARNING")
        return None  # звонок важнее: при сбое проверки не обрываем


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
    if CallLogRecorder.current() is None:  # виджет; у звонка журнал заводит SIP-роут
        CallLogRecorder(channel="widget").activate()
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
        t_start = time.monotonic()

        # Клиенты создаются без сети, подключаются параллельно (раньше — по очереди, ~3,5 с до
        # приветствия). Приветствие звучит, как только готов ElevenLabs (или сразу — из кэша),
        # не дожидаясь Scribe и OpenAI.
        use_asr = settings.ELEVEN_ASR_ENABLED
        use_chat = use_asr and "realtime" not in (settings.ELEVEN_TEXT_LLM_MODEL or "")
        if use_chat:
            if settings.ELEVEN_TEXT_LLM_PROVIDER == "openrouter" and settings.OPENROUTER_API_KEY:
                llm = ChatLLMClient(settings.OPENROUTER_API_KEY, assistant, client_id, db, user_agent,
                                    telephony=telephony, conversation_model=ElevenConversation, label="ELEVEN-LLM",
                                    provider="openrouter", model=settings.ELEVEN_TEXT_LLM_MODEL,
                                    route=[x.strip() for x in (settings.ELEVEN_TEXT_LLM_ROUTE or "").split(",")])
            else:
                model = settings.ELEVEN_TEXT_LLM_MODEL if settings.ELEVEN_TEXT_LLM_PROVIDER == "openai" else "gpt-5.6-luna"
                llm = ChatLLMClient(settings.OPENAI_API_KEY, assistant, client_id, db, user_agent, telephony=telephony,
                                    conversation_model=ElevenConversation, label="ELEVEN-LLM", model=model)
        else:
            llm = FishLLMClient(settings.OPENAI_API_KEY, assistant, client_id, db, user_agent, telephony=telephony,
                                conversation_model=ElevenConversation, label="ELEVEN-LLM", text_input=use_asr)
        session = FishVoiceSession(websocket, assistant, llm, None, db, client_id, provider="eleven")
        tts = ElevenTTSClient(
            settings.ELEVENLABS_API_KEY, assistant, ELEVEN_SAMPLE_RATE,
            on_audio=session.on_tts_audio,
            on_speech_started=session.on_tts_speech_started,
            on_speech_ended=session.on_tts_speech_ended,
            label=client_id[:8],
        )
        session.tts = tts
        asr_language = (assistant.language or "").strip().lower()
        # «none» — без второго языка (в Render пустое значение переменной задать нельзя)
        asr_secondary = [x.strip().lower() for x in (settings.ELEVEN_ASR_SECONDARY_LANGUAGES or "").split(",")
                         if x.strip() and x.strip().lower() not in ("none", "-", "off")]

        def make_scribe():
            # Телефон: Scribe получает родные 8 кГц (SIP-адаптер отдаёт 24 кГц — сессия пересчитает обратно)
            return ScribeSTTClient(
                settings.ELEVENLABS_API_KEY, language=asr_language,
                sample_rate=8000 if (telephony and settings.ELEVEN_ASR_PHONE_8K) else LLM_INPUT_RATE,
                silence_ms=settings.ELEVEN_ASR_SILENCE_MS, label=client_id[:8], secondary_languages=asr_secondary,
            )

        def make_openai_stt():
            return OpenAISTTClient(
                settings.OPENAI_API_KEY, language=asr_language, silence_ms=settings.ELEVEN_ASR_SILENCE_MS,
                label=client_id[:8], secondary_languages=asr_secondary,
                model=settings.ELEVEN_ASR_OPENAI_MODEL, delay=settings.ELEVEN_ASR_OPENAI_DELAY,
            )

        def make_yandex():
            return YandexSTTClient(
                settings.YANDEX_SPEECHKIT_API_KEY, settings.YANDEX_FOLDER_ID,
                languages=[x.strip() for x in (settings.YANDEX_STT_LANGUAGES or "").split(",") if x.strip()],
                sample_rate=8000 if telephony else 16000, silence_ms=settings.ELEVEN_ASR_SILENCE_MS,
                model=settings.YANDEX_STT_MODEL, label=client_id[:8],
            )

        def make_google():
            return GoogleSTTClient(
                credentials_info=parse_credentials_json(settings.GOOGLE_SPEECH_CREDENTIALS_JSON),
                api_key=settings.GOOGLE_SPEECH_API_KEY, project_id=settings.GOOGLE_SPEECH_PROJECT_ID,
                location=settings.GOOGLE_SPEECH_LOCATION,
                languages=[x.strip() for x in (settings.GOOGLE_STT_LANGUAGES or "").split(",") if x.strip()],
                sample_rate=8000 if telephony else 16000, silence_ms=settings.ELEVEN_ASR_SILENCE_MS,
                model=settings.GOOGLE_STT_MODEL, endpointing=settings.GOOGLE_STT_ENDPOINTING,
                denoise=settings.GOOGLE_STT_DENOISE,
                phrases=[x.strip() for x in (settings.GOOGLE_STT_PHRASES or "").split(",") if x.strip()],
                label=client_id[:8],
            )

        async def connect_stt():
            """Основной движок распознавания, при сбое — следующий. None — ни один не подключился."""
            order = {
                "yandex": [make_yandex, make_scribe],
                "google": [make_google, make_yandex, make_scribe],
                "openai": [make_openai_stt, make_scribe],
            }.get(settings.ELEVEN_ASR_PROVIDER, [make_scribe, make_openai_stt])
            for make in order:
                client = make()
                if await client.connect():
                    return client
                _log(f"ASR {type(client).__name__} unavailable ({client.fatal_error or 'connect failed'})", "WARNING")
            return None

        timings: dict = {}

        async def timed(name, coro):
            t0 = time.monotonic()
            try:
                return await coro
            finally:
                timings[name] = int((time.monotonic() - t0) * 1000)

        async def tts_connect() -> bool:
            try:
                await tts.connect()
                return True
            except Exception as exc:
                _log(f"ElevenLabs connect failed: {exc}", "ERROR")
                return False

        tts_task = asyncio.create_task(timed("ElevenLabs", tts_connect()))
        stt_task = asyncio.create_task(timed("распознавание", connect_stt())) if use_asr else None
        llm_task = asyncio.create_task(timed("LLM", llm.connect())) if use_chat else None
        sub_task = asyncio.create_task(timed("подписка", _check_subscription(assistant)))

        async def abort(code: str, message: str, ws_code: int = 1011, payload: Optional[dict] = None) -> None:
            for t in (tts_task, stt_task, llm_task, sub_task):
                if t and not t.done():
                    t.cancel()
            if stt_task is not None and stt_task.done() and not stt_task.cancelled() and stt_task.result():
                try:
                    await stt_task.result().close()
                except Exception:
                    pass
            for c in (tts, llm):
                if c is not None:
                    try:
                        await c.close()
                    except Exception:
                        pass
            if payload:
                try:
                    await websocket.send_json(payload)
                    await websocket.close(code=1008)
                except Exception:
                    pass
            else:
                await fail(code, message, ws_code)

        # Приветствие: готовая запись из кэша звучит сразу (подписка проверяется параллельно и
        # оборвёт звонок, если истекла); иначе — как только подключён ElevenLabs
        greeting_text = (assistant.greeting_message or DEFAULT_ELEVEN_GREETING).strip()
        cache_key = _greeting_cache_key(assistant, tts, greeting_text)
        session.on_greeting_audio = lambda pcm: _greeting_cache_put(cache_key, pcm)
        cached = _GREETING_CACHE.get(cache_key)
        greet_task = None
        if cached and greeting_text:
            greet_task = asyncio.create_task(session.play_cached_greeting(cached))

        sub = await sub_task
        if sub is not None and not sub.get("active"):
            if greet_task is not None:
                greet_task.cancel()
            code = "TRIAL_EXPIRED" if sub.get("is_trial") else "SUBSCRIPTION_EXPIRED"
            msg = "Ваш пробный период истек" if sub.get("is_trial") else "Ваша подписка истекла"
            await abort(code, msg, payload={"type": "error", "error": {
                "code": code, "message": msg, "subscription_status": sub, "requires_payment": True}})
            return

        if not await tts_task:
            await abort("elevenlabs_connection_failed", "Failed to connect to ElevenLabs")
            return
        if greet_task is None and use_chat and greeting_text:
            await session.greet()  # история чат-модели локальная — приветствие можно положить до подключения
        greeting_at = int((time.monotonic() - t_start) * 1000)

        stt = await stt_task if stt_task is not None else None
        if use_asr and stt is None:
            _log(f"ASR unavailable, session {client_id} uses audio input in OpenAI Realtime", "WARNING")
        if use_chat and stt is None:
            # Без распознавания текстовой модели нечего слушать — прежняя схема: звук в OpenAI Realtime
            if llm_task is not None:
                llm_task.cancel()
            await llm.close()
            llm = FishLLMClient(settings.OPENAI_API_KEY, assistant, client_id, db, user_agent, telephony=telephony,
                                conversation_model=ElevenConversation, label="ELEVEN-LLM", text_input=False)
            session.llm = llm
            session.call_log.session_id = llm.session_id
            llm_task = None
            use_chat = False
        if llm_task is not None:
            ok = await llm_task
        else:
            ok = await timed("LLM", llm.connect())
            if ok and session.greeting_started:
                await llm.add_assistant_message(greeting_text)  # приветствие уже прозвучало — в контекст
        if not ok:
            await abort("openai_connection_failed", "Failed to connect to OpenAI")
            return
        if stt is not None:
            if not use_chat:
                llm.text_input = True
            session.attach_stt(stt)

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
        session.call_log.meta.update({
            "llm_model": llm.model,
            "asr": (getattr(stt, "provider_label", None)
                    or f"Scribe Realtime ({stt.language or 'auto'}"
                       f"{'+' + ','.join(stt.secondary_languages) if stt.secondary_languages else ''}, "
                       f"{stt.sample_rate // 1000} кГц, пауза {stt.silence_ms} мс)") if stt
                   else "OpenAI audio (VAD + whisper)",
            "tts_model": tts.model,
            "voice_id": tts.voice_id,
            "language": tts.language,
            "telephony_profile": telephony,
        })
        session.call_log.add(
            "session",
            f"Сессия начата: {(type(stt).__name__.replace('STTClient', '') + ' → ') if stt else ''}OpenAI {llm.model} → ElevenLabs {tts.model}, язык {tts.language}. "
            f"Приветствие через {greeting_at} мс от начала сессии{' (из кэша)' if greet_task else ''}; подключения: "
            + ", ".join(f"{k} {v} мс" for k, v in timings.items()),
        )
        _log(f"session {client_id} started: assistant={assistant.id} '{assistant.name}' voice={tts.voice_id} "
             f"model={tts.model} lang={tts.language} telephony={telephony} "
             f"input={(getattr(stt, 'provider_label', None) or 'scribe asr') if stt else 'openai audio'} greeting_at={greeting_at}ms timings={timings}")

        await session.run()

    except WebSocketDisconnect:
        _log(f"client disconnected before start: {client_id}")
    except Exception as exc:
        _log(f"fatal: {exc}\n{traceback.format_exc()}", "ERROR")
        try:
            await websocket.send_json({"type": "error", "error": {"code": "server_error", "message": "Internal server error"}})
        except Exception:
            pass
