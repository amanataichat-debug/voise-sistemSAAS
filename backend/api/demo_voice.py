"""
Демо-бот Voksy AI (ветка 0811-voice-jarvis): /ws/demo-voice, Mini App и Telegram-вебхук.

Зачем: показать голосового агента человеку, у которого ещё нет кабинета, не пуская его ни в чью базу.
Разговор ведёт handler_demo_voice на придуманных данных (services/owner_voice_demo.py) — запросов
к PostgreSQL за весь разговор нет ни одного.

Как заходят:
  1) человек открывает бота демо → /start → кнопка Mini App «🎙 Поговорить с агентом»;
  2) страница static/voice-live.html?demo=1 шлёт initData в POST /api/demo/webapp-auth;
  3) подпись проверяется ключом бота демо → короткий токен `dv1.` (только для /ws/demo-voice, 30 мин);
  4) окно разговора открывает сокет и присылает токен первым сообщением.
Для своих (админ кабинета) токеном может быть обычный JWT — чтобы проверять демо из браузера.

Деньги: минуты Gemini Live платит платформа, поэтому разговор ограничен по длине
(DEMO_VOICE_MAX_SECONDS) и по числу в сутки на одного человека (DEMO_VOICE_PER_DAY) и на всех
(DEMO_VOICE_PER_HOUR). Счётчики — в памяти воркера: демо, точности до единицы не требуется.

Переменные окружения: DEMO_BOT_TOKEN (бот демо), DEMO_BOT_WEBHOOK_SECRET (адрес вебхука),
DEMO_VOICE_MAX_SECONDS, DEMO_VOICE_PER_DAY, DEMO_VOICE_PER_HOUR. Нет DEMO_BOT_TOKEN — демо выключено.
"""
from __future__ import annotations

import os
import time
import traceback
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.core.config import settings
from backend.core.dependencies import check_admin_access
from backend.core.logging import get_logger
from backend.models.user import User
from backend.services import owner_voice as ov
from backend.services import owner_voice_demo as demo
from backend.services.agent_telegram_service import AgentTelegramService

logger = get_logger(__name__)
router = APIRouter()

PER_DAY = int(os.getenv("DEMO_VOICE_PER_DAY", "5") or 5)
PER_HOUR = int(os.getenv("DEMO_VOICE_PER_HOUR", "60") or 60)
_starts: Dict[str, Deque[float]] = defaultdict(deque)
_all_starts: Deque[float] = deque()


def bot_token() -> str:
    return (os.getenv("DEMO_BOT_TOKEN", "") or "").strip()


def webhook_secret() -> str:
    return (os.getenv("DEMO_BOT_WEBHOOK_SECRET", "") or "").strip()


def enabled() -> bool:
    return bool(bot_token())


async def send_demo_message(chat_id: str, text: str, reply_markup: Optional[dict] = None) -> bool:
    """Сообщение в чат демо-бота. Зовётся и из сессии (send_to_telegram)."""
    if not enabled() or not chat_id:
        return False
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await AgentTelegramService._call(bot_token(), "sendMessage", payload) is not None


# ---------------------------------------------------------------- доступ и лимиты
def _trim(dq: Deque[float], window: float, now: float) -> None:
    while dq and now - dq[0] > window:
        dq.popleft()


def quota_error(who: str, now: Optional[float] = None) -> Optional[Dict[str, str]]:
    """None — можно начинать разговор; иначе {code, message}. Счётчики в памяти воркера."""
    now = now or time.time()
    _trim(_all_starts, 3600, now)
    if len(_all_starts) >= PER_HOUR:
        return {"code": "demo_busy", "message": "Демо сейчас нарасхват — попробуйте через несколько минут"}
    dq = _starts[who]
    _trim(dq, 86400, now)
    if len(dq) >= PER_DAY:
        return {"code": "demo_limit",
                "message": "На сегодня демонстраций хватит. Напишите нам — покажем на вашей компании"}
    return None


def note_start(who: str, now: Optional[float] = None) -> None:
    now = now or time.time()
    _starts[who].append(now)
    _all_starts.append(now)


def demo_access(token: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Пускать ли в демо. Токен: `dv1.` из Telegram или JWT кабинета (для своих). Базу не трогаем."""
    if not enabled():
        return {"ok": False, "code": "not_configured", "message": "Демо сейчас выключено"}
    who, name, chat_id = "", "", None
    if (token or "").startswith("dv1."):
        data = demo.read_demo_token(token, now=now)
        if not data:
            return {"ok": False, "code": "unauthorized",
                    "message": "Ссылка устарела — откройте кнопку «🎙 Поговорить с агентом» в боте ещё раз"}
        chat_id, name = data["chat_id"], data["name"]
        who = f"tg:{chat_id}"
    else:
        try:
            from backend.core.security import decode_jwt_token
            payload = decode_jwt_token(token or "")
            who = f"user:{payload.get('sub')}"
        except Exception:
            return {"ok": False, "code": "unauthorized", "message": "Откройте демо через бота Voksy AI"}
    err = quota_error(who, now=now)
    if err:
        return {"ok": False, **err}
    note_start(who, now=now)
    return {"ok": True, "who": who, "name": name, "chat_id": chat_id}


# ---------------------------------------------------------------- вход из Mini App
class WebAppAuth(BaseModel):
    init_data: str


@router.post("/api/demo/webapp-auth")
async def demo_webapp_auth(body: WebAppAuth):
    """initData Telegram Mini App → короткий токен только для голосового сокета демо."""
    if not enabled():
        raise HTTPException(status_code=404, detail="Демо сейчас выключено")
    fields = ov.verify_webapp_init_data(body.init_data, bot_token())
    if not fields:
        raise HTTPException(status_code=401, detail="Не удалось проверить вход через Telegram")
    user = fields.get("user") or {}
    chat_id = str(user.get("id") or "")
    if not chat_id:
        raise HTTPException(status_code=401, detail="Не удалось проверить вход через Telegram")
    name = str(user.get("first_name") or "").strip()
    return {"token": demo.make_demo_token(chat_id, name), "name": name,
            "company": demo.COMPANY, "max_seconds": demo.MAX_SECONDS}


# ---------------------------------------------------------------- Telegram-вебхук демо-бота
_WELCOME = (
    "Привет! Это демонстрация голосового агента Voksy AI.\n\n"
    "Нажмите «🎙 Поговорить с агентом» — и поговорите голосом с агентом выдуманной компании "
    "«{company}» (пластиковые окна, Бишкек). Спросите его:\n"
    "• «Что нового?» — он покажет отчёт за день\n"
    "• «Кто ждёт ответа?»\n"
    "• «Что писал Нурлан?»\n"
    "• «Сколько денег на счету?»\n\n"
    "Данные выдуманные, звонить и писать по-настоящему он в демо не будет.\n"
    "Такой же агент работает на вашей базе: voksyai.online"
)


def _voice_button() -> dict:
    return {"inline_keyboard": [[{"text": "🎙 Поговорить с агентом",
                                  "web_app": {"url": demo.demo_webapp_url()}}]]}


@router.post("/api/demo/webhook/{secret}")
async def demo_webhook(secret: str, request: Request):
    """Вебхук демо-бота: на любое сообщение отвечаем кнопкой Mini App."""
    if not enabled() or not webhook_secret() or secret != webhook_secret():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        update = await request.json()
    except Exception:
        return {"ok": True}
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return {"ok": True}
    text = str(message.get("text") or "")
    try:
        if text.startswith("/start") or text.startswith("/voice") or text.startswith("/demo"):
            await send_demo_message(chat_id, _WELCOME.format(company=demo.COMPANY), _voice_button())
            await AgentTelegramService._call(bot_token(), "setChatMenuButton", {
                "chat_id": int(chat_id), "menu_button": {"type": "web_app", "text": "🎙 Демо",
                                                         "web_app": {"url": demo.demo_webapp_url()}}})
        else:
            await send_demo_message(chat_id, "Нажмите кнопку — и поговорим голосом.", _voice_button())
    except Exception as exc:
        logger.error(f"[DEMO-BOT] webhook failed: {exc}")
    return {"ok": True}


@router.post("/api/demo/setup")
async def demo_setup(current_user: User = Depends(check_admin_access)):
    """Админ: один раз зарегистрировать вебхук демо-бота, команды и кнопку меню.
    Токен бота и секрет адреса берутся из окружения (DEMO_BOT_TOKEN, DEMO_BOT_WEBHOOK_SECRET)."""
    if not enabled():
        raise HTTPException(status_code=400, detail="DEMO_BOT_TOKEN не задан")
    if not webhook_secret():
        raise HTTPException(status_code=400, detail="DEMO_BOT_WEBHOOK_SECRET не задан")
    me = await AgentTelegramService._call(bot_token(), "getMe", {})
    if not me:
        raise HTTPException(status_code=400, detail="Telegram не принял DEMO_BOT_TOKEN")
    base = (settings.HOST_URL or "https://voksyai.online").rstrip("/")
    hook = f"{base}/api/demo/webhook/{webhook_secret()}"
    ok = await AgentTelegramService._call(bot_token(), "setWebhook", {
        "url": hook, "allowed_updates": ["message"], "drop_pending_updates": True}) is not None
    await AgentTelegramService._call(bot_token(), "setMyCommands", {
        "commands": [{"command": "start", "description": "Поговорить с демо-агентом"}]})
    await AgentTelegramService._call(bot_token(), "setChatMenuButton", {
        "menu_button": {"type": "web_app", "text": "🎙 Демо", "web_app": {"url": demo.demo_webapp_url()}}})
    logger.info(f"[DEMO-BOT] setup by {current_user.email}: @{me.get('username')} webhook={ok}")
    return {"ok": ok, "bot": me.get("username"), "webhook": hook, "mini_app": demo.demo_webapp_url(),
            "limits": {"per_day": PER_DAY, "per_hour": PER_HOUR, "max_seconds": demo.MAX_SECONDS}}


# ---------------------------------------------------------------- сокет демо
@router.websocket("/ws/demo-voice")
async def demo_voice_endpoint(websocket: WebSocket):
    from backend.websockets.handler_demo_voice import handle_demo_voice_connection

    try:
        await handle_demo_voice_connection(websocket)
    except WebSocketDisconnect:
        logger.info("[DEMO-VOICE] client disconnected")
    except Exception as exc:
        logger.error(f"[DEMO-VOICE] websocket error: {exc}\n{traceback.format_exc()}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass
