# backend/websockets/handler_translate.py
"""
WebSocket прокси для OpenAI Realtime Translation API (gpt-realtime-translate).

✅ v1.0: Initial Translate WebSocket handler

Логика проще, чем у обычного Realtime, потому что нет conversation lifecycle:
  - Только session.update для установки target language
  - session.input_audio_buffer.append для отправки аудио
  - session.output_audio.delta / session.*_transcript.* в ответ

Браузер ↔ handler ↔ OpenAI Translation API.
"""

import asyncio
import json
import uuid
from datetime import datetime

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from backend.core.logging import get_logger
from backend.models.translate_assistant import TranslateAssistantConfig, TranslateConversation

logger = get_logger(__name__)

# Endpoint OpenAI Realtime Translation
OPENAI_TRANSLATE_URL = "wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate"
# Стоимость перевода (фикс, не токены)
TRANSLATE_COST_PER_MINUTE = 0.034


async def handle_translate_connection(client_ws: WebSocket, assistant_id: str, db):
    """Главная точка входа: мостим клиента в OpenAI Translation API."""
    await client_ws.accept()
    logger.info(f"[TRANSLATE] New connection: assistant_id={assistant_id}")

    # 1. Загрузить ассистента
    try:
        assistant_uuid = uuid.UUID(assistant_id)
    except ValueError:
        logger.warning(f"[TRANSLATE] Invalid assistant_id: {assistant_id}")
        await client_ws.close(code=1008, reason="invalid_assistant_id")
        return

    assistant = db.query(TranslateAssistantConfig).filter_by(id=assistant_uuid).first()
    if not assistant or not assistant.is_active:
        logger.warning(f"[TRANSLATE] Assistant not found or inactive: {assistant_id}")
        await client_ws.close(code=1008, reason="assistant_not_found")
        return

    # 2. Проверить наличие OpenAI ключа у владельца
    user = assistant.user
    if not user or not user.openai_api_key:
        logger.warning(f"[TRANSLATE] No OpenAI key for assistant {assistant_id}")
        try:
            await client_ws.send_json({
                "type": "error",
                "code": "openai_key_required",
                "message": "Укажите OpenAI API ключ в разделе OpenAI Агенты"
            })
        except Exception:
            pass
        await client_ws.close(code=1008)
        return

    # 3. Подключение к OpenAI translation endpoint
    headers = [("Authorization", f"Bearer {user.openai_api_key}")]

    session_state = {
        "session_id": str(uuid.uuid4()),
        "source_text": [],
        "translated_text": [],
        "source_language": None,
        "started_at": datetime.utcnow(),
    }

    try:
        async with websockets.connect(
            OPENAI_TRANSLATE_URL,
            extra_headers=headers,
            max_size=15 * 1024 * 1024,
            ping_interval=30,
            ping_timeout=120,
            close_timeout=15,
        ) as openai_ws:
            logger.info(f"[TRANSLATE] ✅ Connected to OpenAI translation endpoint")

            # 3.1 Конфиг target language
            await openai_ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "audio": {
                        "output": {"language": assistant.output_language}
                    }
                }
            }))
            logger.info(f"[TRANSLATE] session.update sent, target language={assistant.output_language}")

            # 3.2 Приветствие клиенту (если задано)
            if assistant.greeting_message:
                try:
                    await client_ws.send_json({
                        "type": "translate.greeting",
                        "message": assistant.greeting_message,
                        "output_language": assistant.output_language,
                    })
                except Exception:
                    pass

            # 3.3 Два таска параллельно
            await asyncio.gather(
                _client_to_openai(client_ws, openai_ws),
                _openai_to_client(openai_ws, client_ws, assistant, session_state),
                return_exceptions=True
            )

    except WebSocketDisconnect:
        logger.info(f"[TRANSLATE] Client disconnected: {assistant_id}")
    except Exception as e:
        logger.error(f"[TRANSLATE] Error: {e}", exc_info=True)
        try:
            await client_ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
        try:
            await client_ws.close()
        except Exception:
            pass
    finally:
        # 4. Залогировать диалог
        try:
            _save_conversation(db, assistant, session_state)
        except Exception as save_err:
            logger.error(f"[TRANSLATE] Failed to save conversation: {save_err}", exc_info=True)


async def _client_to_openai(client_ws: WebSocket, openai_ws):
    """Принимаем от клиента → нормализуем в формат translation API → шлём в OpenAI."""
    try:
        while True:
            msg = await client_ws.receive_text()
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue

            mtype = data.get("type", "")

            # Поддерживаем оба формата событий — клиент может слать как realtime-style,
            # так и translation-style. Нормализуем.
            if mtype == "input_audio_buffer.append":
                await openai_ws.send(json.dumps({
                    "type": "session.input_audio_buffer.append",
                    "audio": data.get("audio", "")
                }))
            elif mtype == "ping":
                try:
                    await client_ws.send_json({"type": "pong"})
                except Exception:
                    pass
            elif mtype.startswith("session."):
                # Уже в нужном формате — прокидываем как есть
                await openai_ws.send(msg)
            # Прочие типы игнорируем (нет conversation lifecycle)
    except WebSocketDisconnect:
        logger.info("[TRANSLATE] client_to_openai: client disconnected")
    except Exception as e:
        logger.warning(f"[TRANSLATE] client_to_openai error: {e}")


async def _openai_to_client(openai_ws, client_ws: WebSocket, assistant, state):
    """Принимаем от OpenAI → копим транскрипты → прозрачно прокидываем клиенту."""
    try:
        async for raw in openai_ws:
            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                continue

            et = evt.get("type")

            # Накапливаем тексты для логирования
            if et == "session.input_transcript.delta":
                state["source_text"].append(evt.get("delta", ""))
            elif et == "session.output_transcript.delta":
                state["translated_text"].append(evt.get("delta", ""))
            elif et == "session.input_transcript.done":
                if evt.get("language"):
                    state["source_language"] = evt.get("language")
            elif et == "error":
                logger.warning(f"[TRANSLATE] OpenAI error event: {evt}")

            # Прозрачно прокидываем клиенту
            try:
                await client_ws.send_text(raw)
            except Exception:
                break
    except websockets.exceptions.ConnectionClosed:
        logger.info("[TRANSLATE] openai_to_client: OpenAI connection closed")
    except Exception as e:
        logger.warning(f"[TRANSLATE] openai_to_client error: {e}")


def _save_conversation(db, assistant, state):
    """Сохранить лог разговора по окончании сессии."""
    source_text = "".join(state["source_text"]).strip()
    translated_text = "".join(state["translated_text"]).strip()

    # Не сохраняем пустые сессии
    if not source_text and not translated_text:
        logger.info("[TRANSLATE] Empty session — skip saving conversation")
        return

    duration = (datetime.utcnow() - state["started_at"]).total_seconds()
    cost = (duration / 60) * TRANSLATE_COST_PER_MINUTE

    conv = TranslateConversation(
        assistant_id=assistant.id,
        session_id=state["session_id"],
        source_text=source_text or None,
        translated_text=translated_text or None,
        source_language=state.get("source_language"),
        target_language=assistant.output_language,
        duration_seconds=duration,
        call_cost=cost,
    )
    db.add(conv)

    assistant.total_conversations = (assistant.total_conversations or 0) + 1
    db.commit()
    logger.info(f"[TRANSLATE] ✅ Conversation saved: session={state['session_id']}, "
                f"duration={duration:.1f}s, cost=${cost:.4f}")
