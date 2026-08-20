# backend/services/webhook_notification.py
"""
🔗 Webhook Notification Service v4.0
=====================================

Сервис для отправки webhook-уведомлений о завершённых диалогах.
По событию `conversation.completed` шлёт POST-запрос на URL клиента
с полным диалогом и метаданными. Покрывает телефонию и веб-чат.

ИСПОЛЬЗОВАНИЕ:
    from backend.services.webhook_notification import send_webhook_safe

    await send_webhook_safe(
        db=db,
        webhook_url="https://your-server.com/voicyfy-webhook",
        webhook_enabled=True,
        source="web_chat",                # "web_chat" | "telephony"
        session_id="abc-123",
        assistant_id="...",
        assistant_name="Продажи",
        assistant_type="gemini",          # "gemini" | "gemini_31" | "openai"
    )

АРХИТЕКТУРА:
- Без HMAC-подписи — простой POST с JSON body
- Fire-and-forget — без ретраев и таблицы доставок (как Telegram)
- Один URL на пользователя (поля webhook_url / webhook_enabled в БД)
- Диалог собирается из БД по session_id отдельным запросом
"""

import json
import uuid
import asyncio
import aiohttp
from typing import Optional, List, Dict, Any
from datetime import datetime

from backend.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

WEBHOOK_TIMEOUT_SECONDS = 10
WEBHOOK_USER_AGENT = "Voicyfy-Webhook/1.0"

# Системные плейсхолдеры, которые не должны попадать в диалог
_EMPTY_USER_MESSAGES = {"[no user input]"}
_EMPTY_ASSISTANT_MESSAGES = {"[no response]", "[interrupted]"}


# =============================================================================
# WEBHOOK NOTIFICATION SERVICE
# =============================================================================

class WebhookNotificationService:
    """
    Сервис для отправки webhook-уведомлений о завершённых диалогах.
    """

    @staticmethod
    def fetch_dialog_from_db(db, session_id: str, assistant_type: str) -> List[Dict[str, Any]]:
        """
        Достаёт диалог из БД и собирает массив реплик.

        Args:
            db: Сессия SQLAlchemy
            session_id: Идентификатор сессии диалога
            assistant_type: "gemini" | "gemini_31" | "openai"

        Returns:
            Массив реплик [{"role": "user/assistant", "text": "...", "ts": <ms>}]
            Может быть пустым.

        Примечание по хранилищу:
            - Gemini ВЕБ-ЧАТ пишет реплики в `gemini_conversations` (GeminiConversation).
            - OpenAI веб-чат и ЛЮБАЯ телефония (через ConversationService) пишут в
              `conversations` (Conversation).
            Поэтому выбираем основную таблицу по типу ассистента, а если она пустая —
            пробуем вторую таблицу (покрывает gemini-телефонию, чьи реплики лежат
            в `conversations`).
        """
        dialog: List[Dict[str, Any]] = []

        if not session_id:
            logger.warning("[WEBHOOK] fetch_dialog_from_db called without session_id")
            return dialog

        from backend.models.gemini_assistant import GeminiConversation
        from backend.models.conversation import Conversation

        # Определяем порядок таблиц: основная + fallback
        if assistant_type in ("gemini", "gemini_31"):
            models_to_try = [GeminiConversation, Conversation]
        elif assistant_type == "openai":
            models_to_try = [Conversation, GeminiConversation]
        else:
            logger.warning(f"[WEBHOOK] Unknown assistant_type '{assistant_type}', trying conversations table")
            models_to_try = [Conversation, GeminiConversation]

        for model in models_to_try:
            dialog = WebhookNotificationService._query_dialog_table(db, model, session_id)
            if dialog:
                logger.info(f"[WEBHOOK] Dialog found in {model.__tablename__}: {len(dialog)} messages")
                return dialog

        logger.info(f"[WEBHOOK] No dialog records found for session {session_id}")
        return dialog

    @staticmethod
    def _query_dialog_table(db, model, session_id: str) -> List[Dict[str, Any]]:
        """Запрашивает одну таблицу диалогов и разворачивает записи в массив реплик."""
        dialog: List[Dict[str, Any]] = []
        try:
            records = (
                db.query(model)
                .filter(model.session_id == session_id)
                .order_by(model.created_at.asc())
                .all()
            )

            for record in records:
                created_at = getattr(record, "created_at", None)
                ts = int(created_at.timestamp() * 1000) if created_at else int(datetime.utcnow().timestamp() * 1000)

                user_message = (record.user_message or "").strip()
                assistant_message = (record.assistant_message or "").strip()

                # Реплика пользователя
                if user_message and user_message not in _EMPTY_USER_MESSAGES:
                    dialog.append({"role": "user", "text": user_message, "ts": ts})

                # Реплика ассистента
                if assistant_message and assistant_message not in _EMPTY_ASSISTANT_MESSAGES:
                    dialog.append({"role": "assistant", "text": assistant_message, "ts": ts})

        except Exception as e:
            logger.error(f"[WEBHOOK] ❌ Error querying {getattr(model, '__tablename__', model)}: {e}")

        return dialog

    @staticmethod
    def build_payload(
        source: str,
        session_id: str,
        assistant_id: str,
        assistant_name: str,
        assistant_type: str,
        dialog: List[Dict[str, Any]],
        caller_number: Optional[str] = None,
        call_direction: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        call_cost: Optional[float] = None,
        record_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Собирает финальный payload для webhook.

        Returns:
            Dict с событием conversation.completed
        """
        return {
            "event": "conversation.completed",
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": {
                "source": source,                  # "web_chat" | "telephony"
                "session_id": session_id,
                "assistant_id": assistant_id,
                "assistant_name": assistant_name,
                "assistant_type": assistant_type,  # "gemini" | "gemini_31" | "openai"
                "caller_number": caller_number,    # None для web_chat
                "call_direction": call_direction,  # None для web_chat
                "duration_seconds": duration_seconds,  # None для web_chat
                "call_cost": call_cost,            # None для web_chat
                "record_url": record_url,          # None для web_chat
                "dialog": dialog,                  # List[{role, text, ts}]
                "messages_count": len(dialog)
            }
        }

    @staticmethod
    async def send_webhook(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Отправляет POST-запрос на webhook URL клиента.

        Args:
            url: URL клиента
            payload: Тело запроса (будет сериализовано в JSON)

        Returns:
            Dict: {"success": bool, "status_code": int, "error": str|None}
        """
        try:
            body = json.dumps(payload, ensure_ascii=False)

            headers = {
                "Content-Type": "application/json",
                "User-Agent": WEBHOOK_USER_AGENT
            }

            logger.info(f"[WEBHOOK] Sending POST to {url}")
            logger.info(f"[WEBHOOK]    Payload size: {len(body)} bytes")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=body.encode("utf-8"),
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT_SECONDS)
                ) as response:
                    status_code = response.status

                    if 200 <= status_code < 300:
                        logger.info(f"[WEBHOOK] ✅ Delivered, status: {status_code}")
                        return {
                            "success": True,
                            "status_code": status_code,
                            "error": None
                        }
                    else:
                        logger.warning(f"[WEBHOOK] ⚠️ Client returned non-2xx status: {status_code}")
                        return {
                            "success": False,
                            "status_code": status_code,
                            "error": f"HTTP {status_code}"
                        }

        except aiohttp.ClientError as e:
            logger.error(f"[WEBHOOK] ❌ Connection error: {e}")
            return {"success": False, "status_code": 0, "error": f"Connection error: {str(e)}"}
        except asyncio.TimeoutError:
            logger.error(f"[WEBHOOK] ❌ Request timeout after {WEBHOOK_TIMEOUT_SECONDS}s")
            return {"success": False, "status_code": 0, "error": "Request timeout"}
        except Exception as e:
            logger.error(f"[WEBHOOK] ❌ Unexpected error: {e}")
            return {"success": False, "status_code": 0, "error": f"Unexpected error: {str(e)}"}

    @staticmethod
    async def send_conversation_completed(
        db,
        webhook_url: str,
        source: str,                    # "web_chat" | "telephony"
        session_id: str,
        assistant_id: str,
        assistant_name: str,
        assistant_type: str,            # "gemini" | "gemini_31" | "openai"
        caller_number: Optional[str] = None,
        call_direction: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        call_cost: Optional[float] = None,
        record_url: Optional[str] = None,
    ) -> bool:
        """
        Главный публичный метод — отправляет webhook о завершённом диалоге.

        Returns:
            True если webhook успешно доставлен, False иначе
        """
        logger.info(f"[WEBHOOK] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(f"[WEBHOOK] 🔗 Sending conversation.completed")
        logger.info(f"[WEBHOOK]    Source: {source}")
        logger.info(f"[WEBHOOK]    Session: {session_id}")
        logger.info(f"[WEBHOOK]    Assistant: {assistant_name} ({assistant_type})")
        logger.info(f"[WEBHOOK] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # 1. Валидация URL
        if not webhook_url:
            logger.warning("[WEBHOOK] ⚠️ Empty webhook_url, skipping")
            return False

        # 2. Собираем диалог из БД
        dialog = WebhookNotificationService.fetch_dialog_from_db(db, session_id, assistant_type)

        # 3. Не шлём пустые вебхуки
        if len(dialog) == 0:
            logger.warning(f"[WEBHOOK] ⚠️ Empty dialog for session {session_id}, skipping webhook")
            return False

        logger.info(f"[WEBHOOK] 💬 Dialog has {len(dialog)} messages")

        # 4. Собираем payload
        payload = WebhookNotificationService.build_payload(
            source=source,
            session_id=session_id,
            assistant_id=assistant_id,
            assistant_name=assistant_name,
            assistant_type=assistant_type,
            dialog=dialog,
            caller_number=caller_number,
            call_direction=call_direction,
            duration_seconds=duration_seconds,
            call_cost=call_cost,
            record_url=record_url,
        )

        # 5. Отправляем
        result = await WebhookNotificationService.send_webhook(webhook_url, payload)

        # 6. Логируем результат
        if result["success"]:
            logger.info(f"[WEBHOOK] ✅ Notification delivered successfully")
        else:
            logger.warning(f"[WEBHOOK] ❌ Notification failed: {result.get('error')}")

        return result["success"]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

async def send_webhook_safe(
    db,
    webhook_url: Optional[str],
    webhook_enabled: bool,
    **kwargs
) -> bool:
    """
    Безопасная обёртка для отправки webhook-уведомления.
    Не выбрасывает исключения, только логирует ошибки.

    Args:
        db: Сессия SQLAlchemy
        webhook_url: URL клиента (может быть None)
        webhook_enabled: Включены ли вебхуки у пользователя
        **kwargs: Остальные параметры send_conversation_completed

    Returns:
        True если отправка успешна, False иначе
    """
    if not webhook_url or not webhook_enabled:
        # ⚠️ Закрываем сессию даже при раннем выходе (для fire-and-forget задач)
        _safe_close_db(db)
        return False

    try:
        return await WebhookNotificationService.send_conversation_completed(
            db=db, webhook_url=webhook_url, **kwargs
        )
    except Exception as e:
        logger.error(f"[WEBHOOK] Safe wrapper error: {e}")
        return False
    finally:
        # ⚠️ Закрываем сессию: для fire-and-forget задач (voximplant) это
        # единственное место, где сессия может быть закрыта. Для веб-чат
        # хендлеров повторный close() в их finally безопасен (идемпотентен).
        _safe_close_db(db)


def _safe_close_db(db) -> None:
    """Безопасно закрывает сессию БД, не выбрасывая исключений."""
    try:
        if db is not None:
            db.close()
    except Exception as e:
        logger.debug(f"[WEBHOOK] db.close() ignored error: {e}")
