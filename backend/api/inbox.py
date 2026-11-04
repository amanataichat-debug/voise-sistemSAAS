"""
Инбокс (ветка 0411-inbox): /api/inbox/* — общий чат с клиентами по WhatsApp,
личному Telegram и Instagram для тарифов Старт и Profi.

Логика — services/inbox_service.py. Здесь только HTTP: доступ, разбор
формы с файлом/голосовым, коды ошибок.

Доступ: User.has_agent_chat_access() (Старт и Profi, триал, админ). Карточки
и сообщения — только свои (agent_contacts.user_id).
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.core.logging import get_logger
from backend.core.pipeline_stages import get_agent_stages, is_valid_stage_for_agent
from backend.core.timezone_utils import iso_utc
from backend.db.session import get_db
from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.user import User
from backend.services import agent_pause
from backend.services import inbox_service as inbox

logger = get_logger(__name__)
router = APIRouter()


# ============================================================================
# HELPERS
# ============================================================================

def _require_access(user: User) -> None:
    if not (user.is_admin or user.has_agent_chat_access()):
        raise HTTPException(status_code=402, detail="no_access")


def _contact(db: Session, user: User, contact_id: str) -> AgentContact:
    try:
        contact = db.query(AgentContact).filter(
            AgentContact.id == contact_id, AgentContact.user_id == user.id,
        ).first()
    except Exception:
        db.rollback()
        contact = None
    if contact is None:
        raise HTTPException(status_code=404, detail="not_found")
    return contact


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """ISO (UTC) → naive UTC, как в таблицах сообщений."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="invalid_datetime")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _err(e: "inbox.SendError", status: int = 400):
    raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


def _stages(agent) -> list:
    try:
        return get_agent_stages(agent)
    except Exception:
        return get_agent_stages(None)


# ============================================================================
# ОБЗОР, НАСТРОЙКИ
# ============================================================================

@router.get("/overview")
async def overview(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Всё для первой отрисовки: режим тарифа, боты, каналы, стадии, настройки, языки."""
    _require_access(current_user)
    agents = inbox.user_agents(db, current_user)
    settings = inbox.get_settings(db, current_user.id)
    return {
        "agent_mode": current_user.agent_mode(),
        "agents": [{
            "id": str(a.id),
            "name": a.name,
            "is_active": bool(a.is_active),
            "channels": inbox.channel_status(db, a),
            "stages": _stages(a),
        } for a in agents],
        "settings": settings,
        "languages": [{"code": c, "label": l} for c, l in inbox.LANGUAGES],
        "pause_options": list(inbox.PAUSE_OPTIONS),
    }


class SettingsBody(BaseModel):
    channels: Optional[Dict[str, bool]] = None
    my_language: Optional[str] = None
    languages: Optional[list] = None
    auto_translate_incoming: Optional[bool] = None
    default_pause: Optional[Any] = None
    tone: Optional[str] = None
    enter_to_send: Optional[bool] = None
    sound: Optional[bool] = None
    desktop_notify: Optional[bool] = None


@router.get("/settings")
async def get_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_access(current_user)
    return inbox.get_settings(db, current_user.id)


@router.put("/settings")
async def put_settings(body: SettingsBody, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    _require_access(current_user)
    try:
        return inbox.save_settings(db, current_user.id, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# СПИСОК ДИАЛОГОВ
# ============================================================================

@router.get("/dialogs")
async def list_dialogs(
    folder: str = Query("all"),
    channel: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    before: Optional[str] = Query(None),
    limit: int = Query(40, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_access(current_user)
    if folder not in inbox.FOLDERS:
        raise HTTPException(status_code=400, detail="invalid_folder")
    if channel and channel not in inbox.CHANNELS:
        raise HTTPException(status_code=400, detail="invalid_channel")
    result = inbox.list_dialogs(
        db, current_user, folder=folder, channel=channel, stage=stage or None,
        q=(q or "").strip() or None, agent_id=agent_id or None,
        before=_parse_dt(before), limit=limit,
    )
    latest = inbox.latest_activity(db, current_user)
    result["latest_at"] = iso_utc(latest)
    return result


@router.get("/updates")
async def updates(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Дешёвый опрос раз в несколько секунд: время последнего сообщения и непрочитанные."""
    _require_access(current_user)
    latest = inbox.latest_activity(db, current_user)
    counts = inbox.list_dialogs(db, current_user, folder="all", limit=1)["counts"]
    return {"latest_at": iso_utc(latest), "counts": counts}


# ============================================================================
# ДИАЛОГ
# ============================================================================

@router.get("/dialogs/{contact_id}")
async def open_dialog(
    contact_id: str,
    mark_read: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_access(current_user)
    contact = _contact(db, current_user, contact_id)
    settings = inbox.get_settings(db, current_user.id)
    card = inbox.contact_card(db, current_user, contact)
    tl = inbox.timeline(db, contact, settings=settings)
    agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first() if contact.agent_config_id else None
    if mark_read:
        inbox.mark_read(db, current_user.id, contact.id)
    return {
        "contact": card,
        "timeline": tl,
        "default_channel": inbox.default_channel(db, contact, card["send_channels"]),
        "stages": _stages(agent),
    }


@router.get("/dialogs/{contact_id}/timeline")
async def dialog_timeline(
    contact_id: str,
    before: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_access(current_user)
    contact = _contact(db, current_user, contact_id)
    return inbox.timeline(db, contact, before=_parse_dt(before), settings=inbox.get_settings(db, current_user.id))


@router.post("/dialogs/{contact_id}/read")
async def read_dialog(contact_id: str, current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    _require_access(current_user)
    contact = _contact(db, current_user, contact_id)
    inbox.mark_read(db, current_user.id, contact.id)
    return {"ok": True}


@router.post("/dialogs/{contact_id}/send")
async def send(
    contact_id: str,
    channel: str = Form(...),
    text: str = Form(""),
    pause: Optional[str] = Form(None),
    library_file_id: Optional[str] = Form(None),
    original_text: Optional[str] = Form(None),
    original_language: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    voice: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Сообщение клиенту от владельца: текст, файл с ноутбука (file), запись с
    микрофона (voice) или файл библиотеки бота (library_file_id). pause —
    "channel" | минуты | "-1" (до включения) | "0" (бот продолжает отвечать).
    """
    _require_access(current_user)
    contact = _contact(db, current_user, contact_id)
    if pause not in (None, "", "channel", "0"):
        try:
            pause_val: Any = int(pause)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_pause")
        if pause_val != -1 and not (1 <= pause_val <= 60 * 24 * 30):
            raise HTTPException(status_code=400, detail="invalid_pause")
    else:
        pause_val = pause or "channel"
    upload = None
    voice_data = None
    if file is not None and file.filename:
        upload = {"data": await file.read(), "filename": file.filename, "mime": file.content_type}
    elif voice is not None:
        voice_data = {"data": await voice.read(), "mime": voice.content_type}
    original = ({"text": original_text, "language": original_language}
                if (original_text or "").strip() else None)
    try:
        result = await inbox.send_message(
            db, current_user, contact, channel=channel, text=text, upload=upload, voice=voice_data,
            library_file_id=(library_file_id or None), pause_minutes=pause_val, original=original,
        )
    except inbox.SendError as e:
        db.rollback()
        _err(e, 413 if e.code == "file_too_large" else 400)
    except HTTPException:
        raise
    except Exception as e:  # сбой канала (сессия Telegram, шлюз WhatsApp) — понятный ответ вместо 500
        db.rollback()
        logger.error(f"[INBOX] send failed for contact {contact.id}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail={
            "code": "channel_error",
            "message": f"Канал {inbox.CHANNEL_LABELS.get(channel, channel)} сейчас не отвечает — сообщение не отправлено. "
                       "Проверьте подключение канала или выберите другой.",
        })
    return result


class PauseBody(BaseModel):
    minutes: Optional[int] = None
    forever: bool = False


@router.post("/dialogs/{contact_id}/pause")
async def pause(contact_id: str, body: PauseBody, current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Остановить бота с клиентом (все мессенджеры) на N минут или навсегда."""
    _require_access(current_user)
    contact = _contact(db, current_user, contact_id)
    if not body.forever and not (body.minutes and 1 <= body.minutes <= 60 * 24 * 365):
        raise HTTPException(status_code=400, detail="minutes_or_forever_required")
    agent_pause.set_pause(contact, -1 if body.forever else body.minutes, "manual")
    db.commit()
    db.refresh(contact)
    return contact.to_dict()


@router.delete("/dialogs/{contact_id}/pause")
async def resume(contact_id: str, current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    _require_access(current_user)
    contact = _contact(db, current_user, contact_id)
    agent_pause.clear_pause(contact)
    db.commit()
    db.refresh(contact)
    return contact.to_dict()


class StageBody(BaseModel):
    stage: str


@router.post("/dialogs/{contact_id}/stage")
async def move_stage(contact_id: str, body: StageBody, current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    _require_access(current_user)
    contact = _contact(db, current_user, contact_id)
    agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first() if contact.agent_config_id else None
    if not is_valid_stage_for_agent(agent, body.stage):
        raise HTTPException(status_code=400, detail="invalid_stage")
    old = contact.status
    contact.status = body.stage
    db.commit()
    logger.info(f"[INBOX] contact {contact.id} stage {old} -> {body.stage} (inbox)")
    return {"ok": True, "stage": contact.status}


@router.post("/dialogs/{contact_id}/call")
async def call_now(contact_id: str, current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """«Позвонить»: бот звонит клиенту по команде владельца (Старт и Profi)."""
    _require_access(current_user)
    contact = _contact(db, current_user, contact_id)
    try:
        return inbox.queue_owner_call(db, current_user, contact)
    except inbox.SendError as e:
        db.rollback()
        _err(e)


class ReminderBody(BaseModel):
    title: str
    scheduled_time: str
    note: Optional[str] = None


@router.post("/dialogs/{contact_id}/reminder")
async def reminder(contact_id: str, body: ReminderBody, current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    _require_access(current_user)
    contact = _contact(db, current_user, contact_id)
    when = _parse_dt(body.scheduled_time)
    try:
        return inbox.create_reminder(db, current_user, contact, body.title, when, body.note or "")
    except inbox.SendError as e:
        db.rollback()
        _err(e)


# ============================================================================
# ПОМОЩНИК И ПЕРЕВОДЫ
# ============================================================================

class AssistBody(BaseModel):
    action: str                      # fix | translate | draft
    text: str = ""
    language: Optional[str] = None
    contact_id: Optional[str] = None


@router.post("/assist")
async def assist(body: AssistBody, current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    _require_access(current_user)
    contact = _contact(db, current_user, body.contact_id) if body.contact_id else None
    if body.action == "draft" and contact is None:
        raise HTTPException(status_code=400, detail="contact_required")
    try:
        return await inbox.assist(db, current_user, action=body.action, text=body.text,
                                  language=body.language, contact=contact)
    except inbox.SendError as e:
        _err(e, 402 if e.code == "insufficient_credits" else 400)


class TranslateBody(BaseModel):
    language: Optional[str] = None


@router.post("/messages/{channel}/{message_id}/translate")
async def translate_message(channel: str, message_id: str, body: TranslateBody,
                            current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Перевести входящее на язык владельца (или выбранный). Перевод сохраняется — второй раз бесплатно."""
    from backend.models.inbox import InboxTranslation
    _require_access(current_user)
    msg = inbox.find_message(db, current_user, channel, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="not_found")
    settings = inbox.get_settings(db, current_user.id)
    language = body.language or settings.get("my_language") or "ru"
    cached = db.query(InboxTranslation).filter(
        InboxTranslation.channel == channel, InboxTranslation.message_id == msg.id,
        InboxTranslation.kind == "translation",
    ).first()
    if cached is not None and cached.language == language:
        return {"text": cached.text, "language": language, "cached": True}
    text = inbox._clean_body(msg.body or "", False).replace("🎤 Голосовое: ", "")
    try:
        res = await inbox.assist(db, current_user, action="translate_incoming", text=text,
                                 language=language, settings=settings)
    except inbox.SendError as e:
        _err(e, 402 if e.code == "insufficient_credits" else 400)
    inbox._save_translation(db, channel, msg.id, "translation", res["text"], language)
    return {"text": res["text"], "language": language, "cached": False}


# ============================================================================
# БЕЗ КАРТОЧКИ (WhatsApp)
# ============================================================================

@router.get("/unlinked/{chat_id}")
async def unlinked(chat_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_access(current_user)
    data = inbox.unlinked_timeline(db, current_user, chat_id)
    if not data:
        raise HTTPException(status_code=404, detail="not_found")
    return data


class LinkBody(BaseModel):
    contact_id: Optional[str] = None
    name: Optional[str] = None


@router.post("/unlinked/{chat_id}/link")
async def link(chat_id: str, body: LinkBody, current_user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Создать карточку клиента для чата (или привязать к существующей) — дальше это обычный диалог."""
    _require_access(current_user)
    try:
        contact = inbox.link_unlinked(db, current_user, chat_id, contact_id=body.contact_id, name=body.name)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, "contact_id": str(contact.id)}
