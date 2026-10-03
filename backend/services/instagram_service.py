"""
Instagram Service — обмен DM через коннектор Composio (toolkit INSTAGRAM).

Тонкая обёртка над composio_service.execute() + работа с локальной историей
переписки (agent_instagram_conversations / agent_instagram_messages).

Ограничения Instagram Messaging API, которые здесь учитываются:
  • писать первым нельзя — только отвечать в уже существующем DM-треде;
  • действует 24-часовое окно: вне его отправка падает (error_subcode 2534022);
  • только Business/Creator-аккаунты.

Входящие забирает поллер backend/core/instagram_poller.py (у тулкита нет
триггеров — только polling через LIST_ALL_CONVERSATIONS / LIST_ALL_MESSAGES).
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.core.logging import get_logger
from backend.services import composio_service

logger = get_logger(__name__)

# Анти-спам: максимум исходящих Instagram-сообщений с одного агента в час.
IG_SEND_HOURLY_LIMIT = 30


def is_configured() -> bool:
    """True, если Composio настроен и у Instagram задан auth_config."""
    return composio_service.is_configured() and composio_service.toolkit_available("instagram")


def connector_for_agent(db, agent_config_id):
    """Строка подключённого Instagram-коннектора агента (или None)."""
    from backend.models.agent_connector import AgentConnector
    return db.query(AgentConnector).filter(
        AgentConnector.agent_config_id == agent_config_id,
        AgentConnector.toolkit == "instagram",
        AgentConnector.status == "connected",
    ).first()


def connected(db, agent_config_id) -> bool:
    return connector_for_agent(db, agent_config_id) is not None


# ============================================================================
# GRAPH API ЧЕРЕЗ COMPOSIO
# ============================================================================

def _data_dict(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Достать полезные данные из ответа composio_service.execute().
    Поле data по контракту Composio — строка/объект; нормализуем к dict.
    """
    data = result.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return {}
    return data if isinstance(data, dict) else {}


def _inner_list(data: Dict[str, Any]) -> List[dict]:
    """
    Развернуть двойную обёртку списковых ответов Graph/Composio:
    {'data': {'data': [...], 'paging': ...}} либо сразу {'data': [...]}.
    """
    inner = data.get("data")
    if isinstance(inner, dict):
        inner = inner.get("data")
    return inner if isinstance(inner, list) else []


def parse_ig_time(value) -> Optional[datetime]:
    """created_time/updated_time Graph API (ISO, '+0000') → naive UTC datetime."""
    if not value:
        return None
    s = str(value).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        elif len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
            s = s[:-2] + ":" + s[-2:]  # +0000 → +00:00
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


async def get_me(composio_user_id: str) -> Dict[str, Any]:
    """Свой IG Business аккаунт: {ok, id, username}."""
    res = await composio_service.execute(
        "INSTAGRAM_GET_USER_INFO", {"ig_user_id": "me"}, composio_user_id
    )
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}
    d = _data_dict(res)
    body = d.get("data") if isinstance(d.get("data"), dict) else d
    return {
        "ok": True,
        "id": str(body.get("id") or body.get("user_id") or ""),
        "username": body.get("username"),
    }


async def list_conversations(composio_user_id: str, limit: int = 20) -> Dict[str, Any]:
    """DM-треды аккаунта: {ok, conversations: [{id, updated_time}]}."""
    res = await composio_service.execute(
        "INSTAGRAM_LIST_ALL_CONVERSATIONS",
        {"platform": "instagram", "limit": limit},
        composio_user_id,
    )
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "conversations": []}
    return {"ok": True, "conversations": _inner_list(_data_dict(res))}


async def list_messages(composio_user_id: str, conversation_id: str, limit: int = 15) -> Dict[str, Any]:
    """
    Сообщения треда (новые первыми у Graph): {ok, messages: [...]}.
    Каждый элемент best-effort: {id, created_time, from:{id,username}, message}.
    """
    args = {"conversation_id": conversation_id, "limit": limit, "fields": MESSAGE_FIELDS}
    res = await composio_service.execute("INSTAGRAM_LIST_ALL_MESSAGES", args, composio_user_id)
    if not res.get("ok"):
        # Старые версии тулза могут не принимать fields — повтор без вложений.
        args.pop("fields", None)
        res = await composio_service.execute("INSTAGRAM_LIST_ALL_MESSAGES", args, composio_user_id)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "messages": []}
    return {"ok": True, "messages": _inner_list(_data_dict(res))}


# Поля сообщения Graph API: attachments нужны для голосовых/фото/файлов клиента.
MESSAGE_FIELDS = "id,created_time,from,to,message,attachments,is_unsupported"


def parse_attachments(message: dict) -> List[Dict[str, Any]]:
    """
    Вложения сообщения Graph API → [{url, mime, filename, kind_hint}].
    Instagram отдаёт их как attachments.data[] с image_data / video_data /
    audio_data / file_url (CDN-ссылки, живут ограниченное время — качаем сразу).
    """
    att = message.get("attachments")
    if isinstance(att, dict):
        att = att.get("data")
    out = []
    for a in att or []:
        if not isinstance(a, dict):
            continue
        mime = a.get("mime_type")
        name = a.get("name")
        url, hint = None, None
        for key, kind in (("audio_data", "voice"), ("video_data", "video"), ("image_data", "image")):
            block = a.get(key)
            if isinstance(block, dict) and block.get("url"):
                url, hint = block["url"], kind
                break
        if not url:
            payload = a.get("payload") if isinstance(a.get("payload"), dict) else {}
            url = a.get("file_url") or payload.get("url")
            t = (a.get("type") or "").lower()
            hint = {"audio": "voice", "image": "image", "video": "video", "file": None}.get(t)
        if not url:
            continue
        if hint == "voice" and mime and mime.startswith("video/"):
            mime = "audio/mp4"  # голосовые IG приходят mp4-контейнером
        out.append({"url": url, "mime": mime, "filename": name, "kind_hint": hint})
    return out


async def download_attachment(url: str, max_bytes: int) -> Dict[str, Any]:
    """Скачать вложение по CDN-ссылке: {ok, data, mime} или {ok: False, error}."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return {"ok": False, "error": "download_failed"}
                length = int(resp.headers.get("content-length") or 0)
                if length and length > max_bytes:
                    return {"ok": False, "error": "file_too_large"}
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        return {"ok": False, "error": "file_too_large"}
                return {"ok": True, "data": bytes(buf), "mime": resp.headers.get("content-type")}
    except Exception as e:
        logger.warning(f"[IG] attachment download failed: {type(e).__name__}: {e}")
        return {"ok": False, "error": "download_failed"}


async def send_text(composio_user_id: str, recipient_igsid: str, text: str) -> Dict[str, Any]:
    """
    Отправить текстовое DM. Возвращает {ok, message_id?} либо {ok:False, error}.
    Отправка возможна только в существующий тред и в 24-часовом окне.
    """
    res = await composio_service.execute(
        "INSTAGRAM_SEND_TEXT_MESSAGE",
        {"recipient_id": str(recipient_igsid), "text": text},
        composio_user_id,
    )
    if not res.get("ok"):
        return {"ok": False, "error": _human_send_error(res.get("error"))}
    d = _data_dict(res)
    body = d.get("data") if isinstance(d.get("data"), dict) else d
    return {"ok": True, "message_id": body.get("message_id") or body.get("mid")}


async def send_image(composio_user_id: str, recipient_igsid: str, image_url: str) -> Dict[str, Any]:
    """
    Отправить картинку DM по публичной ссылке (Instagram сам её скачивает).
    Документы и аудио Composio-тулкит отправлять не умеет — для них агент
    шлёт ссылку текстом (agent_tools.fn_instagram_send_file).
    """
    res = await composio_service.execute(
        "INSTAGRAM_SEND_IMAGE",
        {"recipient_id": str(recipient_igsid), "image_url": image_url},
        composio_user_id,
    )
    if not res.get("ok"):
        return {"ok": False, "error": _human_send_error(res.get("error"))}
    d = _data_dict(res)
    body = d.get("data") if isinstance(d.get("data"), dict) else d
    return {"ok": True, "message_id": body.get("message_id") or body.get("mid")}


def _human_send_error(error) -> str:
    """Человекочитаемая причина отказа отправки для оркестратора/UI."""
    s = str(error or "")
    if "2534022" in s or "window" in s.lower():
        return ("Окно ответа Instagram (24 часа) закрыто — написать клиенту "
                "можно только после его нового сообщения.")
    if "not_configured" in s:
        return "Коннектор Instagram не настроен на сервере."
    return s or "Не удалось отправить сообщение в Instagram."


# ============================================================================
# ЛОКАЛЬНАЯ ИСТОРИЯ ПЕРЕПИСКИ
# ============================================================================

def store_message(db, agent_config_id, direction: str, body: str,
                  agent_contact_id=None, ig_conversation_id: Optional[str] = None,
                  ig_message_id: Optional[str] = None, sent_at: Optional[datetime] = None,
                  attachment_id=None):
    """Сохранить сообщение в тред (без commit — коммитит вызывающий)."""
    from backend.models.agent_instagram import AgentInstagramMessage
    msg = AgentInstagramMessage(
        agent_config_id=agent_config_id,
        agent_contact_id=agent_contact_id,
        ig_conversation_id=ig_conversation_id,
        ig_message_id=(str(ig_message_id) if ig_message_id else None),
        direction=direction,
        body=body or "",
        sent_at=sent_at,
        attachment_id=attachment_id,
    )
    db.add(msg)
    return msg


def message_exists(db, agent_config_id, ig_message_id) -> bool:
    """Дедупликация поллера по mid Graph API."""
    from backend.models.agent_instagram import AgentInstagramMessage
    if not ig_message_id:
        return False
    return db.query(AgentInstagramMessage.id).filter(
        AgentInstagramMessage.agent_config_id == agent_config_id,
        AgentInstagramMessage.ig_message_id == str(ig_message_id),
    ).first() is not None


def get_thread(db, agent_contact_id, limit: int = 30) -> list:
    """Переписка с контактом (старые → новые) для карточки/контекста."""
    from backend.models.agent_instagram import AgentInstagramMessage
    rows = (
        db.query(AgentInstagramMessage)
        .filter(AgentInstagramMessage.agent_contact_id == agent_contact_id)
        .order_by(AgentInstagramMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def conversation_for_contact(db, agent_config_id, agent_contact_id):
    """DM-тред контакта у этого агента (или None) — для отправки ответа."""
    from backend.models.agent_instagram import AgentInstagramConversation
    return db.query(AgentInstagramConversation).filter(
        AgentInstagramConversation.agent_config_id == agent_config_id,
        AgentInstagramConversation.agent_contact_id == agent_contact_id,
    ).order_by(AgentInstagramConversation.updated_at.desc()).first()
