"""
Agent Telegram API v2.2 — подключение и управление Telegram-ботом агента.

Префикс: /api/agent/telegram

Бот агента: фронтенд для AGENT_CHAT + канал доставки уведомлений тулзы
send_telegram_notification из PostCall-оркестратора.

Старые users.telegram_* (общесистемные уведомления о звонках) не трогаются.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.core.dependencies import get_current_user
from backend.models.user import User
from backend.models.agent_config import AgentConfig
from backend.services.agent_telegram_service import (
    AgentTelegramService,
    process_telegram_message,
    generate_webhook_secret,
    build_webhook_url,
)

logger = get_logger(__name__)

router = APIRouter()

VALID_CHAT_TYPES = {"private", "group", "supergroup", "channel"}


# ============================================================================
# SCHEMAS
# ============================================================================

class TelegramConnectRequest(BaseModel):
    bot_token: str = Field(..., min_length=10, max_length=100)


class TelegramEnabledRequest(BaseModel):
    enabled: bool


class TelegramChatAddRequest(BaseModel):
    chat_id: str = Field(..., min_length=1, max_length=50)
    title: Optional[str] = None
    type: Optional[str] = None


# ============================================================================
# HELPERS
# ============================================================================

def _get_agent(current_user: User, db: Session, agent_id: Optional[str] = None) -> AgentConfig:
    """
    Резолв конкретного агента пользователя для настроек Telegram (v3.1).
    При наличии agent_id ищем именно его (в пределах user_id); иначе — первого
    по дате создания (обратная совместимость с одним агентом).

    Каждый агент имеет собственный telegram_bot_token + уникальный
    telegram_webhook_secret, поэтому у каждого агента — свой бот и свой webhook.
    """
    q = db.query(AgentConfig).filter(AgentConfig.user_id == current_user.id)
    if agent_id:
        agent = q.filter(AgentConfig.id == agent_id).first()
    else:
        agent = q.order_by(AgentConfig.created_at.asc()).first()
    if not agent:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return agent


def _settings_dict(agent: AgentConfig) -> dict:
    """Сериализация настроек Telegram (без раскрытия токена/секрета целиком)."""
    is_configured = agent.has_telegram_bot()
    token_masked = None
    if agent.telegram_bot_token:
        token_masked = "***" + agent.telegram_bot_token[-4:]

    return {
        "enabled": bool(agent.telegram_enabled),
        "is_configured": is_configured,
        "bot_username": agent.telegram_bot_username,
        "bot_token_masked": token_masked,
        "chat_ids": agent.telegram_chat_ids or [],
    }


# ============================================================================
# ENDPOINTS — SETTINGS
# ============================================================================

@router.get("")
@router.get("/")
async def get_telegram_settings(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Текущие настройки Telegram-бота агента."""
    agent = _get_agent(current_user, db, agent_id)

    # Авто-миграция webhook: если у Telegram зарегистрирован старый URL
    # (например, через Selectel-прокси), тихо переустанавливаем на актуальный.
    if agent.has_telegram_bot() and agent.telegram_webhook_secret:
        try:
            expected_url = build_webhook_url(agent.telegram_webhook_secret)
            info = await AgentTelegramService.get_webhook_info(agent.telegram_bot_token)
            if info and info.get("url") != expected_url:
                logger.info(
                    f"[AGENT-TG] Webhook URL drift detected for user {current_user.id}: "
                    f"{info.get('url')} -> {expected_url}, re-registering..."
                )
                await AgentTelegramService.setup_webhook(
                    agent.telegram_bot_token, expected_url, agent.telegram_webhook_secret
                )
        except Exception as e:
            logger.warning(f"[AGENT-TG] Webhook drift check failed for user {current_user.id}: {e}")

    return _settings_dict(agent)


@router.put("")
@router.put("/")
async def connect_telegram_bot(
    body: TelegramConnectRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Подключить / обновить Telegram-бота агента."""
    agent = _get_agent(current_user, db, agent_id)

    token = body.bot_token.strip()

    # 1. Валидируем токен
    bot_info = await AgentTelegramService.validate_token(token)
    if not bot_info:
        raise HTTPException(status_code=400, detail="invalid_token")

    # 1b. Один бот = один webhook. Запрещаем переиспользовать токен другим
    #     агентом, иначе setup_webhook перезапишет webhook первого агента.
    token_owner = db.query(AgentConfig).filter(
        AgentConfig.telegram_bot_token == token,
        AgentConfig.id != agent.id,
    ).first()
    if token_owner:
        raise HTTPException(status_code=400, detail="token_already_used")

    # 2. Снимаем webhook со старого токена, если он отличается
    old_token = agent.telegram_bot_token
    if old_token and old_token != token:
        await AgentTelegramService.delete_webhook(old_token)

    # 3. Сохраняем токен и username
    agent.telegram_bot_token = token
    agent.telegram_bot_username = bot_info.get("username")

    # 4. Генерируем secret если его нет
    if not agent.telegram_webhook_secret:
        agent.telegram_webhook_secret = generate_webhook_secret()

    # 5. Регистрируем webhook
    webhook_url = build_webhook_url(agent.telegram_webhook_secret)
    ok = await AgentTelegramService.setup_webhook(token, webhook_url, agent.telegram_webhook_secret)
    if not ok:
        logger.warning(f"[AGENT-TG] setup_webhook failed for user {current_user.id} (token saved anyway)")

    # 6. telegram_enabled не трогаем (FALSE при первом подключении — пусть юзер включит сам)
    db.commit()
    db.refresh(agent)

    logger.info(f"[AGENT-TG] Bot @{agent.telegram_bot_username} connected for user {current_user.id}")
    return _settings_dict(agent)


@router.delete("")
@router.delete("/")
async def disconnect_telegram_bot(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отключить и удалить Telegram-бота агента (истории чатов остаются)."""
    agent = _get_agent(current_user, db, agent_id)

    old_token = agent.telegram_bot_token
    if old_token:
        await AgentTelegramService.delete_webhook(old_token)

    agent.telegram_bot_token = None
    agent.telegram_bot_username = None
    agent.telegram_webhook_secret = None
    agent.telegram_chat_ids = []
    agent.telegram_enabled = False
    flag_modified(agent, "telegram_chat_ids")
    db.commit()

    logger.info(f"[AGENT-TG] Bot disconnected for user {current_user.id}")
    return _settings_dict(agent)


@router.patch("/enabled")
async def set_telegram_enabled(
    body: TelegramEnabledRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Рубильник enabled/disabled."""
    agent = _get_agent(current_user, db, agent_id)
    agent.telegram_enabled = bool(body.enabled)
    db.commit()
    db.refresh(agent)
    return _settings_dict(agent)


@router.post("/regenerate-secret")
async def regenerate_telegram_secret(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Перевыпустить webhook secret и переустановить webhook."""
    agent = _get_agent(current_user, db, agent_id)
    if not agent.telegram_bot_token:
        raise HTTPException(status_code=400, detail="telegram_bot_not_configured")

    agent.telegram_webhook_secret = generate_webhook_secret()
    webhook_url = build_webhook_url(agent.telegram_webhook_secret)
    ok = await AgentTelegramService.setup_webhook(
        agent.telegram_bot_token, webhook_url, agent.telegram_webhook_secret
    )
    if not ok:
        logger.warning(f"[AGENT-TG] regenerate-secret: setup_webhook failed for user {current_user.id}")

    db.commit()
    db.refresh(agent)
    return _settings_dict(agent)


# ============================================================================
# ENDPOINTS — CHATS
# ============================================================================

@router.post("/chats")
async def add_telegram_chat(
    body: TelegramChatAddRequest,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Добавить chat_id вручную."""
    agent = _get_agent(current_user, db, agent_id)

    chat_id = body.chat_id.strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id_required")

    chat_type = body.type
    if chat_type and chat_type not in VALID_CHAT_TYPES:
        raise HTTPException(status_code=400, detail="invalid_chat_type")

    chats = list(agent.telegram_chat_ids or [])
    if any(c.get("chat_id") == chat_id for c in chats):
        raise HTTPException(status_code=400, detail="already_exists")

    chats.append({
        "chat_id": chat_id,
        "title": body.title,
        "type": chat_type,
        "added_at": datetime.utcnow().isoformat(),
    })
    agent.telegram_chat_ids = chats
    flag_modified(agent, "telegram_chat_ids")
    db.commit()
    db.refresh(agent)

    logger.info(f"[AGENT-TG] Added chat {chat_id} for user {current_user.id}")
    return _settings_dict(agent)


@router.delete("/chats/{chat_id}")
async def delete_telegram_chat(
    chat_id: str,
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Удалить chat_id из массива."""
    agent = _get_agent(current_user, db, agent_id)

    chats = list(agent.telegram_chat_ids or [])
    new_chats = [c for c in chats if c.get("chat_id") != chat_id]
    agent.telegram_chat_ids = new_chats
    flag_modified(agent, "telegram_chat_ids")
    db.commit()
    db.refresh(agent)

    logger.info(f"[AGENT-TG] Removed chat {chat_id} for user {current_user.id}")
    return _settings_dict(agent)


# ============================================================================
# ENDPOINTS — TEST
# ============================================================================

@router.post("/test")
async def test_telegram(
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отправить тестовое сообщение во все добавленные чаты."""
    agent = _get_agent(current_user, db, agent_id)

    if not agent.has_telegram_bot():
        raise HTTPException(status_code=400, detail="telegram_bot_not_configured")
    if not agent.get_telegram_chat_ids_list():
        raise HTTPException(status_code=400, detail="no_chat_ids_configured")

    text = "🧪 Тестовое сообщение от Voksy AI Agent. Если ты это видишь — связь работает."

    # send_to_all_chats требует telegram_enabled — для теста шлём напрямую,
    # чтобы можно было проверить связь до включения рубильника.
    import asyncio
    chat_ids = agent.get_telegram_chat_ids_list()
    token = agent.telegram_bot_token
    results = await asyncio.gather(
        *[AgentTelegramService.send_message(token, cid, text) for cid in chat_ids],
        return_exceptions=True,
    )
    sent = sum(1 for r in results if r is True)
    total = len(chat_ids)
    return {"sent": sent, "failed": total - sent, "total": total}


# ============================================================================
# ENDPOINT — WEBHOOK (public, no auth)
# ============================================================================

@router.post("/webhook/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Публичный webhook от Telegram. БЕЗ авторизации.
    Всегда возвращает 200 OK, чтобы Telegram не ретраил.
    """
    try:
        # 1. Найти агента по secret
        agent = db.query(AgentConfig).filter(
            AgentConfig.telegram_webhook_secret == secret
        ).first()
        if not agent:
            return {"ok": True}

        # 2. Проверить заголовок секрет-токена
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header_secret != secret:
            logger.warning(f"[AGENT-TG] Webhook secret header mismatch for agent {agent.id}")
            return {"ok": True}

        # 3. Включён ли бот
        if not agent.telegram_enabled:
            logger.info(f"[AGENT-TG] Webhook ignored: bot disabled (agent {agent.id})")
            return {"ok": True}

        # 4. Распарсить update
        try:
            update = await request.json()
        except Exception:
            return {"ok": True}

        message = update.get("message")
        if not message or not isinstance(message, dict):
            return {"ok": True}

        # 5. chat_id в списке разрешённых?
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        if not chat_id or chat_id not in agent.get_telegram_chat_ids_list():
            logger.info(
                f"[AGENT-TG] Webhook ignored: chat {chat_id} not in allow-list "
                f"{agent.get_telegram_chat_ids_list()} (agent {agent.id})"
            )
            return {"ok": True}

        # 6. Текст или голосовое (voice → распознаём в текст через STT)
        text = message.get("text")
        if not text:
            voice = message.get("voice")
            if voice:
                await AgentTelegramService._call(
                    agent.telegram_bot_token, "sendChatAction",
                    {"chat_id": chat_id, "action": "typing"},
                )
                text = await AgentTelegramService.transcribe_voice(
                    agent.telegram_bot_token, voice,
                )
                if not text:
                    await AgentTelegramService.send_message(
                        token=agent.telegram_bot_token,
                        chat_id=chat_id,
                        text="Не удалось распознать голосовое. Попробуйте ещё раз или напишите текстом.",
                    )
                    return {"ok": True}
            else:
                await AgentTelegramService.send_message(
                    token=agent.telegram_bot_token,
                    chat_id=chat_id,
                    text="Я понимаю только текстовые и голосовые сообщения.",
                )
                return {"ok": True}

        # 7. Отправитель
        from_user = message.get("from", {}) or {}

        # 8. Обработать
        logger.info(f"[AGENT-TG] Incoming message from chat {chat_id} (agent {agent.id}): {text[:80]!r}")
        await process_telegram_message(
            agent=agent,
            chat_id=chat_id,
            text=text,
            from_user=from_user,
            message=message,
            db=db,
        )

    except Exception as e:
        logger.error(f"[AGENT-TG] Webhook error: {e}", exc_info=True)

    # 9. Всегда 200 OK
    return {"ok": True}
