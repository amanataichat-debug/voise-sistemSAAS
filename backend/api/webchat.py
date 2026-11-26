"""
Чат на сайте — публичный API для посетителя (Voksy Beauty, шаг 1.6в). Без входа.

  GET  /api/public/chat/{slug}            — показывать ли чат, как зовут, приветствие, цвет и логотип
  POST /api/public/chat/{slug}/start      — начать или продолжить разговор (токен в X-Chat-Token)
  POST /api/public/chat/{slug}/messages   — сообщение посетителя {text}
  GET  /api/public/chat/{slug}/messages   — новые сообщения после ?after= (+ «печатает»)
  POST /api/public/chat/{slug}/identity   — «оставить номер» {name, phone}

Токен посетителя — заголовок X-Chat-Token (не в адресе: не оседает в логах). Обработчики синхронные
(FastAPI выполняет их в потоках, цикл звонков не замирает), разбор ботом уходит в фоновый loop.
Логика — services/webchat_service.py.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.models.booking import BookingPage
from backend.models.user import User
from backend.services import webchat_service as wc

logger = get_logger(__name__)

public_router = APIRouter()


def _ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")


def _page(db: Session, slug: str):
    page = db.query(BookingPage).filter(BookingPage.slug == (slug or "").lower()).first()
    if page is None or not page.enabled:
        raise HTTPException(status_code=404, detail="Страница не найдена")
    owner = db.query(User).filter(User.id == page.user_id).first()
    if owner is None or not owner.is_active:
        raise HTTPException(status_code=404, detail="Страница не найдена")
    return page, owner


def _session(db: Session, page, token: Optional[str]):
    s = wc.find_session(db, page, token)
    if s is None:
        raise HTTPException(status_code=401, detail="Разговор не найден — откройте чат заново")
    return s


def _fail(e: wc.ChatError):
    raise HTTPException(status_code=e.status, detail=e.message)


@public_router.get("/{slug}")
def chat_info(slug: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import salon_page as sp
    from backend.services import salon_media as sm
    page, owner = _page(db, slug)
    prof = sp.profile_of(page)
    state = wc.public_state(db, page, owner)
    from backend.services import booking_service as bs
    return {**state, "name": bs.settings_of(page).get("title") or owner.company_name or owner.first_name or "",
            "accent": prof.get("accent") or sp.DEFAULT_ACCENT, "logo_url": sm.url_of(prof.get("logo_id")),
            "page_path": sp.page_path(page, owner.business_niche), "book_path": f"/b/{page.slug}"}


class StartIn(BaseModel):
    origin: str = Field("salon", max_length=10)


@public_router.post("/{slug}/start")
def chat_start(slug: str, request: Request, body: StartIn = StartIn(),
               x_chat_token: Optional[str] = Header(None), db: Session = Depends(get_db)) -> Dict[str, Any]:
    page, owner = _page(db, slug)
    try:
        s, token, is_new = wc.start_session(db, page, owner, token=x_chat_token, origin=body.origin, ip=_ip(request),
                                            user_agent=request.headers.get("user-agent", ""))
        db.commit()
    except wc.ChatError as e:
        db.rollback()
        _fail(e)
    out = wc.visitor_messages(db, s)
    if wc.due_for_dispatch(db, s):
        wc.schedule_dispatch(s.id, 0)
    return {"ok": True, "token": token, "new": is_new, **out, **wc.public_state(db, page, owner)}


class MessageIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=wc.MAX_TEXT)


@public_router.post("/{slug}/messages")
def chat_send(slug: str, body: MessageIn, request: Request, x_chat_token: Optional[str] = Header(None),
              db: Session = Depends(get_db)) -> Dict[str, Any]:
    page, owner = _page(db, slug)
    s = _session(db, page, x_chat_token)
    # Владелец выключил чат — не принимаем. Бот выключен / тариф / кредиты — принимаем в уже открытый
    # разговор: посетитель получит служебный ответ, владелец — оповещение и ответит из инбокса.
    if not wc.chat_settings(page)["enabled"]:
        raise HTTPException(status_code=404, detail="Чат сейчас недоступен")
    try:
        m = wc.post_message(db, s, body.text, ip=_ip(request))
        db.commit()
    except wc.ChatError as e:
        db.rollback()
        _fail(e)
    wc.schedule_dispatch(s.id)
    return {"ok": True, "message": {"id": str(m.id), "who": "client", "text": m.body,
                                    "ts": wc.iso_utc(m.created_at), "attachment": None}}


@public_router.get("/{slug}/messages")
def chat_poll(slug: str, after: Optional[str] = None, x_chat_token: Optional[str] = Header(None),
              db: Session = Depends(get_db)) -> Dict[str, Any]:
    page, _owner = _page(db, slug)
    s = _session(db, page, x_chat_token)
    out = wc.visitor_messages(db, s, after=after)
    if wc.due_for_dispatch(db, s):
        wc.schedule_dispatch(s.id, 0)
    return {"ok": True, **out}


class IdentityIn(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    phone: Optional[str] = Field(None, max_length=40)


@public_router.post("/{slug}/identity")
def chat_identity(slug: str, body: IdentityIn, x_chat_token: Optional[str] = Header(None),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    page, _owner = _page(db, slug)
    s = _session(db, page, x_chat_token)
    if not (body.name or body.phone):
        raise HTTPException(status_code=400, detail="Укажите имя или телефон")
    try:
        res = wc.set_identity(db, s, name=body.name, phone=body.phone)
        db.commit()
    except wc.ChatError as e:
        db.rollback()
        _fail(e)
    return {"ok": True, **res}
