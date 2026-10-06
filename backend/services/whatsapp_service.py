"""
WhatsApp агента обзвона — клиент Evolution API + локальная история переписки.

Шлюз: Evolution API v2.3.7 на VPS wa-gateway-1 (https://wa.voksyai.online,
infra/whatsapp-gateway/). Неофициальный протокол WhatsApp Web (Baileys):
только переписка, звонков нет; номер можно забанить за спам, поэтому отправка
ограничена лимитами (почасовой + новые чаты в сутки) и идёт с задержкой
«печатает…».

Один агент = один instance Evolution = один номер WhatsApp (подключение по
QR). Бэкенд → шлюз: HTTPS, заголовок apikey (WHATSAPP_GATEWAY_API_KEY).
Шлюз → бэкенд: webhook instance'а на /api/whatsapp/webhook/{account_id}
с заголовком X-Voksy-Token = account.webhook_secret (обработка —
whatsapp_inbound.py).
"""

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.core.config import settings
from backend.core.logging import get_logger

logger = get_logger(__name__)

WA_SEND_HOURLY_LIMIT = 40          # исходящих сообщений с номера в час
WEBHOOK_EVENTS = ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]
WEBHOOK_TOKEN_HEADER = "X-Voksy-Token"

USER_JID_SUFFIX = "@s.whatsapp.net"


def is_configured() -> bool:
    return bool(settings.WHATSAPP_GATEWAY_URL and settings.WHATSAPP_GATEWAY_API_KEY)


def _base_url() -> str:
    return (settings.WHATSAPP_GATEWAY_URL or "").rstrip("/")


async def _request(method: str, path: str, json: Optional[dict] = None,
                   params: Optional[dict] = None, timeout: float = 30.0) -> Tuple[bool, int, Any]:
    """Запрос к Evolution API → (ok, http_status, json|text)."""
    if not is_configured():
        return False, 0, {"error": "not_configured"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(
                method, _base_url() + path, json=json, params=params,
                headers={"apikey": settings.WHATSAPP_GATEWAY_API_KEY},
            )
        try:
            data = r.json()
        except Exception:
            data = r.text
        if r.status_code >= 400:
            logger.warning(f"[WA] {method} {path} → {r.status_code}: {str(data)[:300]}")
        return r.status_code < 400, r.status_code, data
    except Exception as e:
        logger.warning(f"[WA] {method} {path} failed: {type(e).__name__}: {e}")
        return False, 0, {"error": "gateway_unreachable"}


# ============================================================================
# INSTANCE (подключение номера)
# ============================================================================

def new_instance_name(agent_config_id) -> str:
    return f"voksy-{str(agent_config_id).replace('-', '')[:24]}-{secrets.token_hex(3)}"


def new_webhook_secret() -> str:
    return secrets.token_hex(24)


def webhook_url(account_id) -> str:
    base = (settings.PUBLIC_BASE_URL or settings.HOST_URL or "").rstrip("/")
    return f"{base}/api/whatsapp/webhook/{account_id}"


def _webhook_config(account_id, secret: str) -> dict:
    return {
        "enabled": True,
        "url": webhook_url(account_id),
        "byEvents": False,
        "base64": True,  # медиа входящих приходят прямо в webhook
        "headers": {WEBHOOK_TOKEN_HEADER: secret},
        "events": WEBHOOK_EVENTS,
    }


async def create_instance(instance_name: str, account_id, secret: str) -> Dict[str, Any]:
    """Создать instance (или перенастроить webhook, если он уже есть)."""
    body = {
        "instanceName": instance_name,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
        "groupsIgnore": True,
        "rejectCall": False,
        "alwaysOnline": False,
        "readMessages": False,
        "syncFullHistory": False,
        "webhook": _webhook_config(account_id, secret),
    }
    ok, status, data = await _request("POST", "/instance/create", json=body)
    if ok:
        return {"ok": True}
    # Имя уже занято (повторное подключение после сбоя) — обновляем webhook.
    if status in (403, 409):
        ok2, _, data2 = await _request(
            "POST", f"/webhook/set/{instance_name}",
            json={"webhook": _webhook_config(account_id, secret)},
        )
        if ok2:
            return {"ok": True}
        data = data2
    return {"ok": False, "error": _error_code(data)}


async def get_qr(instance_name: str) -> Dict[str, Any]:
    """
    QR для сканирования. {state:"open"} — уже подключено;
    {qr: "data:image/png;base64,...", pairing_code} — ждём сканирования.
    """
    ok, status, data = await _request("GET", f"/instance/connect/{instance_name}")
    if not ok:
        return {"ok": False, "error": "instance_not_found" if status == 404 else _error_code(data)}
    data = data if isinstance(data, dict) else {}
    inst = data.get("instance") or {}
    if (inst.get("state") or inst.get("status")) == "open":
        return {"ok": True, "state": "open"}
    qr = data.get("base64")
    if qr and not str(qr).startswith("data:"):
        qr = "data:image/png;base64," + qr
    return {"ok": True, "state": "connecting", "qr": qr, "pairing_code": data.get("pairingCode")}


async def connection_state(instance_name: str) -> Optional[str]:
    ok, _, data = await _request("GET", f"/instance/connectionState/{instance_name}")
    if not ok or not isinstance(data, dict):
        return None
    return (data.get("instance") or {}).get("state")


async def fetch_owner(instance_name: str) -> Dict[str, Any]:
    """Кто подключён: {jid, phone, name} (после state=open)."""
    ok, _, data = await _request("GET", "/instance/fetchInstances", params={"instanceName": instance_name})
    if not ok:
        return {}
    rows = data if isinstance(data, list) else [data]
    for row in rows:
        if not isinstance(row, dict):
            continue
        jid = row.get("ownerJid") or ""
        if jid:
            return {"jid": jid, "phone": jid_phone(jid), "name": row.get("profileName")}
    return {}


async def logout_and_delete(instance_name: str) -> None:
    """Разлогинить номер и удалить instance (best-effort)."""
    await _request("DELETE", f"/instance/logout/{instance_name}")
    await _request("DELETE", f"/instance/delete/{instance_name}")


# ============================================================================
# ОТПРАВКА
# ============================================================================

def typing_delay_ms(text: str) -> int:
    """Пауза «печатает…» перед отправкой — как у живого человека."""
    return max(1200, min(1200 + 35 * len(text or ""), 6000))


def _sent_result(data) -> Dict[str, Any]:
    key = (data or {}).get("key") if isinstance(data, dict) else None
    key = key or {}
    return {"ok": True, "message_id": key.get("id"), "remote_jid": key.get("remoteJid")}


async def check_number(instance_name: str, phone_digits: str) -> Dict[str, Any]:
    """Есть ли номер в WhatsApp → {ok, exists, jid}."""
    ok, _, data = await _request(
        "POST", f"/chat/whatsappNumbers/{instance_name}", json={"numbers": [phone_digits]},
    )
    if not ok:
        return {"ok": False, "error": _error_code(data)}
    rows = data if isinstance(data, list) else []
    for row in rows:
        if isinstance(row, dict):
            return {"ok": True, "exists": bool(row.get("exists")), "jid": row.get("jid")}
    return {"ok": True, "exists": False, "jid": None}


async def send_text(instance_name: str, to: str, text: str) -> Dict[str, Any]:
    ok, _, data = await _request(
        "POST", f"/message/sendText/{instance_name}",
        json={"number": to, "text": text, "delay": typing_delay_ms(text), "linkPreview": True},
        timeout=45.0,
    )
    if not ok:
        return {"ok": False, "error": _error_code(data)}
    return _sent_result(data)


async def send_media(instance_name: str, to: str, *, data_b64: str, mime: str,
                     filename: str, kind: str, caption: str = "") -> Dict[str, Any]:
    """Файл: image/video — медиа, audio — аудио, остальное — документ."""
    mediatype = kind if kind in ("image", "video") else "document"
    body = {
        "number": to,
        "mediatype": mediatype,
        "mimetype": mime or "application/octet-stream",
        "media": data_b64,
        "fileName": filename or "file",
        "caption": caption or "",
        "delay": 1500,
    }
    ok, _, data = await _request("POST", f"/message/sendMedia/{instance_name}", json=body, timeout=120.0)
    if not ok:
        return {"ok": False, "error": _error_code(data)}
    return _sent_result(data)


async def get_media_base64(instance_name: str, message_id: str) -> Optional[Dict[str, Any]]:
    """Скачать медиа входящего, если base64 не пришёл в webhook."""
    ok, _, data = await _request(
        "POST", f"/chat/getBase64FromMediaMessage/{instance_name}",
        json={"message": {"key": {"id": message_id}}, "convertToMp4": False},
        timeout=90.0,
    )
    if not ok or not isinstance(data, dict) or not data.get("base64"):
        return None
    return data


def _error_code(data) -> str:
    s = str(data).lower() if data is not None else ""
    if "not_configured" in s:
        return "not_configured"
    if "gateway_unreachable" in s:
        return "gateway_unreachable"
    if '"exists": false' in s or "'exists': false" in s or "exists\":false" in s:
        return "not_on_whatsapp"
    if "connection closed" in s or "not connected" in s or "does not exist" in s:
        return "not_connected"
    return "whatsapp_error"


def error_human(code: str) -> str:
    return {
        "not_configured": "WhatsApp-шлюз не настроен на сервере.",
        "gateway_unreachable": "WhatsApp-шлюз недоступен, попробуйте позже.",
        "not_connected": "WhatsApp не подключён (отсканируйте QR в карточке агента).",
        "not_on_whatsapp": "Этого номера нет в WhatsApp.",
        "no_phone": "У контакта нет номера телефона для WhatsApp.",
        "send_limit_reached": "Достигнут почасовой лимит исходящих WhatsApp-сообщений.",
        "new_chat_limit_reached": ("Достигнут суточный лимит новых чатов WhatsApp (защита "
                                   "номера от бана). Напиши этому клиенту завтра."),
        "empty_text": "Пустой текст сообщения.",
        "instance_not_found": "Подключение WhatsApp не найдено на шлюзе — подключите номер заново.",
    }.get(code, "Не удалось отправить сообщение в WhatsApp.")


# ============================================================================
# JID / ВХОДЯЩИЕ
# ============================================================================

def jid_phone(jid: str) -> Optional[str]:
    """Цифры номера из jid вида 996700123456@s.whatsapp.net (или :device)."""
    if not jid or not jid.endswith(USER_JID_SUFFIX):
        return None
    num = jid.split("@")[0].split(":")[0]
    return num if num.isdigit() else None


def phone_digits(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def is_personal_jid(jid: str) -> bool:
    return bool(jid) and (jid.endswith(USER_JID_SUFFIX) or jid.endswith("@lid"))


def _unwrap(message: dict) -> dict:
    """Снять обёртки ephemeral/viewOnce."""
    for _ in range(3):
        for wrapper in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2",
                        "viewOnceMessageV2Extension", "documentWithCaptionMessage"):
            inner = message.get(wrapper)
            if isinstance(inner, dict) and isinstance(inner.get("message"), dict):
                base64 = message.get("base64")
                message = dict(inner["message"])
                if base64 and "base64" not in message:
                    message["base64"] = base64
                break
        else:
            break
    return message


def _ts(value) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def parse_incoming(item: dict) -> Optional[Dict[str, Any]]:
    """
    Элемент messages.upsert → {id, remote_jid, phone, from_me, push_name, ts,
    text, media|None}. None — служебное/групповое/неподдерживаемое.
    """
    if not isinstance(item, dict):
        return None
    key = item.get("key") or {}
    jid = key.get("remoteJid") or ""
    if not is_personal_jid(jid):
        return None  # группы, статусы, рассылки, каналы
    # Если Evolution не подменил @lid на номер — номер может лежать в alt-полях
    phone = jid_phone(jid)
    if not phone:
        for alt in (key.get("remoteJidAlt"), key.get("senderPn"), item.get("senderPn")):
            phone = jid_phone(alt or "")
            if phone:
                break

    message = item.get("message") or {}
    if not isinstance(message, dict):
        return None
    message = _unwrap(message)

    text = message.get("conversation") or (message.get("extendedTextMessage") or {}).get("text") or ""
    media = None
    for mtype, kind in (("audioMessage", "audio"), ("imageMessage", "image"),
                        ("videoMessage", "video"), ("documentMessage", "document"),
                        ("ptvMessage", "video")):
        m = message.get(mtype)
        if not isinstance(m, dict):
            continue
        if kind == "audio" and m.get("ptt"):
            kind = "voice"
        text = text or m.get("caption") or ""
        media = {
            "kind": kind,
            "mime": (m.get("mimetype") or "").split(";")[0].strip() or None,
            "filename": m.get("fileName") or m.get("title") or None,
            "duration": m.get("seconds"),
            "size": m.get("fileLength"),
            "base64": message.get("base64"),
        }
        break

    if media is None and not text:
        if isinstance(message.get("locationMessage"), dict):
            loc = message["locationMessage"]
            text = (f"📍 Геолокация: {loc.get('degreesLatitude')}, {loc.get('degreesLongitude')}"
                    + (f" ({loc.get('name') or loc.get('address')})" if (loc.get('name') or loc.get('address')) else ""))
        elif isinstance(message.get("contactMessage"), dict):
            text = f"👤 Контакт: {message['contactMessage'].get('displayName') or ''}".strip()
        elif isinstance(message.get("stickerMessage"), dict):
            text = "[стикер]"
        else:
            return None  # реакции, удаления, правки, системные

    return {
        "id": key.get("id"),
        "remote_jid": jid,
        "phone": phone,
        "from_me": bool(key.get("fromMe")),
        "push_name": item.get("pushName"),
        "ts": _ts(item.get("messageTimestamp")),
        "text": (text or "").strip(),
        "media": media,
    }


# ============================================================================
# ЛОКАЛЬНАЯ ИСТОРИЯ ПЕРЕПИСКИ
# ============================================================================

def get_account_for_agent(db, agent_config_id, require_connected: bool = True):
    from backend.models.agent_whatsapp import AgentWhatsAppAccount
    row = db.query(AgentWhatsAppAccount).filter(
        AgentWhatsAppAccount.agent_config_id == agent_config_id
    ).first()
    if row is None or (require_connected and not row.is_connected()):
        return None
    return row


def connected(db, agent_config_id) -> bool:
    return is_configured() and get_account_for_agent(db, agent_config_id) is not None


def store_message(db, account, direction: str, body: str, *, chat=None, agent_contact_id=None,
                  remote_jid: Optional[str] = None, wa_message_id: Optional[str] = None,
                  sent_at: Optional[datetime] = None, attachment_id=None):
    """Сохранить сообщение в тред (без commit — коммитит вызывающий)."""
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    msg = AgentWhatsAppMessage(
        agent_config_id=account.agent_config_id,
        account_id=account.id,
        chat_id=(chat.id if chat is not None else None),
        agent_contact_id=agent_contact_id,
        remote_jid=remote_jid or (chat.remote_jid if chat is not None else None),
        wa_message_id=(str(wa_message_id) if wa_message_id else None),
        direction=direction,
        body=body or "",
        sent_at=sent_at,
        attachment_id=attachment_id,
    )
    db.add(msg)
    return msg


def message_exists(db, agent_config_id, wa_message_id) -> bool:
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    if not wa_message_id:
        return False
    return db.query(AgentWhatsAppMessage.id).filter(
        AgentWhatsAppMessage.agent_config_id == agent_config_id,
        AgentWhatsAppMessage.wa_message_id == str(wa_message_id),
    ).first() is not None


def get_thread(db, agent_contact_id, limit: int = 30) -> list:
    """Переписка с контактом (старые → новые) для карточки/контекста."""
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    rows = (
        db.query(AgentWhatsAppMessage)
        .filter(AgentWhatsAppMessage.agent_contact_id == agent_contact_id)
        .order_by(AgentWhatsAppMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def chat_for_contact(db, account_id, agent_contact_id):
    from backend.models.agent_whatsapp import AgentWhatsAppChat
    return db.query(AgentWhatsAppChat).filter(
        AgentWhatsAppChat.account_id == account_id,
        AgentWhatsAppChat.agent_contact_id == agent_contact_id,
    ).order_by(AgentWhatsAppChat.updated_at.desc()).first()


def match_contact_by_phone(db, agent_config_id, phone: Optional[str]):
    """AgentContact агента по номеру (последние 10 цифр) или None."""
    from backend.models.agent_contact import AgentContact
    from backend.services.sms_history import phone_suffix
    suf = phone_suffix(phone or "")
    if not suf:
        return None
    return db.query(AgentContact).filter(
        AgentContact.agent_config_id == agent_config_id,
        AgentContact.phone.like(f"%{suf}"),
    ).order_by(AgentContact.created_at.desc()).first()
