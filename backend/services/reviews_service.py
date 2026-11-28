"""
Отзывы клиентов салона (Voksy Beauty, шаг 2.3).

Откуда приходит оценка:
  • переписка — после визита клиенту ушла просьба оценить (правило «спасибо» с оценкой или сценарий
    ИИ-управляющего), он ответил цифрой или словами, бот вызвал save_review (save_from_chat);
  • ссылка — в той же просьбе ссылка /r/<токен> (вставка {оценить}): звёзды, текст, как подписать (save_from_link).
Один отзыв на визит: повторная оценка того же визита меняет его, а не добавляет новый.

Публикация (решение владельца 26.11.2026): 4–5 — сразу на странице салона, 1–3 — ждут проверки владельца
(status=pending), он отвечает от салона и решает, показать или скрыть. Клиент изменил оценку — статус
считается заново (кроме скрытого владельцем). Рейтинг салона и мастеров — только по опубликованным.
Ответ салона виден на странице под отзывом; галочкой его можно ещё и отправить клиенту в мессенджер
(через очередь отправок, правило review_reply).

Оценка дублируется в bookings.review_score / review_text / review_at — по ним уже считают карточка клиента
(средняя оценка), отчёты ИИ-управляющего и «Требует внимания» на главной.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

LOW = 3                         # 1–3 — на проверку владельцу
VISITED = ("arrived", "completed")
LINK_DAYS = 60                  # ссылка «Оцените визит» живёт столько дней после визита
CHAT_DAYS = 14                  # ответ в переписке относится к визиту не старше
MAX_TEXT = 1000
MAX_REPLY = 1000
MAX_NAME = 40
STATUS_TITLES = {"published": "На странице", "pending": "Ждёт проверки", "hidden": "Скрыт"}
SOURCE_TITLES = {"chat": "Переписка", "link": "По ссылке"}


class ReviewError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(v) -> Optional[uuid.UUID]:
    if v is None or v == "":
        return None
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


def status_for(score: int) -> str:
    return "published" if int(score) > LOW else "pending"


def clean_score(v) -> int:
    try:
        s = int(v)
    except (TypeError, ValueError):
        raise ReviewError("Оценка — число от 1 до 5")
    if not 1 <= s <= 5:
        raise ReviewError("Оценка — число от 1 до 5")
    return s


def clean_text(v, n: int = MAX_TEXT) -> Optional[str]:
    s = re.sub(r"[ \t]+", " ", str(v or "")).strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s[:n] or None


def display_name(full: Optional[str]) -> str:
    """Подпись на странице из имени в карточке: «Айгерим Касымова» → «Айгерим К.»; служебные имена — пусто."""
    s = re.sub(r"\s+", " ", str(full or "")).strip()
    if not s or re.search(r"\d", s) or re.match(r"(?i)^(клиент|гость|client|guest)\b", s):
        return ""
    parts = s.split(" ")
    first = parts[0][:1].upper() + parts[0][1:]
    if len(parts) > 1 and parts[1][:1].isalpha():
        return f"{first} {parts[1][:1].upper()}."[:MAX_NAME]
    return first[:MAX_NAME]


# ───────────────────────────── ссылка «Оцените визит» ─────────────────────────────

def _secret() -> bytes:
    from backend.core.config import settings
    return ("voksy-review:" + str(getattr(settings, "JWT_SECRET_KEY", "") or "")).encode()


def review_token(booking_id) -> str:
    """Подписанный адрес визита: 32 hex записи + 12 hex подписи (без входа, угадать нельзя)."""
    hx = _uuid(booking_id).hex
    sig = hmac.new(_secret(), hx.encode(), hashlib.sha256).hexdigest()[:12]
    return hx + sig


def booking_id_from_token(token: str) -> Optional[uuid.UUID]:
    t = (token or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{44}", t):
        return None
    hx, sig = t[:32], t[32:]
    good = hmac.new(_secret(), hx.encode(), hashlib.sha256).hexdigest()[:12]
    if not hmac.compare_digest(sig, good):
        return None
    return uuid.UUID(hx)


def review_link(booking_id, host: Optional[str] = None) -> str:
    if host is None:
        from backend.core.config import settings
        host = (getattr(settings, "HOST_URL", "") or "https://voksyai.online").rstrip("/")
    return f"{host}/r/{review_token(booking_id)}"


# ───────────────────────────── запись оценки ─────────────────────────────

def _visit_first(db: Session, b):
    """Визит из нескольких услуг — один отзыв, на первую его запись."""
    from backend.models.booking import Booking
    if not getattr(b, "visit_id", None):
        return b
    first = (db.query(Booking).filter(Booking.visit_id == b.visit_id, Booking.status.in_(VISITED))
             .order_by(Booking.start_at).first())
    return first or b


def _services_of(db: Session, b) -> str:
    from backend.models.booking import Booking
    if not getattr(b, "visit_id", None):
        return b.service_title or ""
    titles = [t for (t,) in db.query(Booking.service_title).filter(
        Booking.visit_id == b.visit_id, Booking.status.in_(VISITED)).order_by(Booking.start_at).all() if t]
    return ", ".join(dict.fromkeys(titles)) or (b.service_title or "")


def find_review(db: Session, b):
    from backend.models.review import SalonReview
    q = db.query(SalonReview).filter(SalonReview.user_id == b.user_id)
    if getattr(b, "visit_id", None):
        r = q.filter(SalonReview.visit_id == b.visit_id).first()
        if r is not None:
            return r
    return q.filter(SalonReview.booking_id == b.id).first()


def record(db: Session, b, score, review_text: Optional[str], source: str,
           author_name: Optional[str] = None) -> Tuple[Any, bool, bool]:
    """Сохранить оценку визита (без коммита). → (отзыв, новый ли, стала ли низкой — сообщить владельцу)."""
    from backend.models.review import SalonReview
    score = clean_score(score)
    b = _visit_first(db, b)
    now = _now()
    review_text = clean_text(review_text)
    r = find_review(db, b)
    created = r is None
    was_low = (r is not None and r.score <= LOW)
    if r is None:
        r = SalonReview(id=uuid.uuid4(), user_id=b.user_id, booking_id=b.id, visit_id=b.visit_id,
                        contact_id=b.contact_id, resource_id=b.resource_id, service_title=_services_of(db, b)[:300],
                        visit_at=b.start_at, created_at=now,
                        author_name=(author_name if author_name is not None else display_name(b.client_name))[:MAX_NAME])
        db.add(r)
    elif author_name is not None:
        r.author_name = author_name[:MAX_NAME]
    r.score, r.source, r.updated_at = score, source if source in ("chat", "link") else "chat", now
    if review_text or source == "link":
        r.text = review_text
    if not (r.status == "hidden" and r.checked_at is not None):
        r.status = status_for(score)
        r.checked_at = None
    if r.status == "published" and r.published_at is None:
        r.published_at = now
    db.flush()
    b.review_score, b.review_text, b.review_at = score, r.text, now
    return r, created, score <= LOW and not was_low


def _notify_low_submit(owner_id, review_id) -> None:
    try:
        from backend.core import background_loop
        background_loop.submit(notify_low(owner_id, review_id))
    except Exception as e:
        logger.warning(f"[REVIEWS] low notify not scheduled: {e}")


NOTIFY_LOW = _notify_low_submit     # тесты подменяют


async def notify_low(owner_id, review_id) -> None:
    """Оценка 1–3 — владельцу в Telegram-бот агента и push: связаться с клиентом, решить по отзыву."""
    from backend.db.session import SessionLocal
    from backend.models.review import SalonReview
    from backend.services import owner_notifications, push_service
    db = SessionLocal()
    try:
        r = db.query(SalonReview).filter(SalonReview.id == review_id).first()
        if r is None:
            return
        names = {str(m.id): m.name for m in bs.resources(db, owner_id, active_only=False)}
        client = ""
        if r.booking_id:
            from backend.models.booking import Booking
            b = db.query(Booking).filter(Booking.id == r.booking_id).first()
            client = (b.client_name if b else "") or ""
        msg = (f"⚠️ Низкая оценка визита: {r.score} из 5 — {client or r.author_name or 'клиент'}"
               + (f", «{r.service_title}»" if r.service_title else "")
               + (f", мастер {names.get(str(r.resource_id))}" if r.resource_id and names.get(str(r.resource_id)) else "")
               + (f"\nОтзыв: {r.text}" if r.text else "")
               + "\nНа странице салона он не виден, пока вы не решите. Свяжитесь с клиентом и ответьте в разделе «Отзывы».")
        agent = bs._first_agent(db, owner_id)
        if agent is not None:
            await owner_notifications._send(agent, db, msg)
        push_service.send_to_user_sync(db, owner_id, f"Оценка {r.score} из 5", msg.split("\n")[0][2:],
                                       "/static/reviews.html?status=pending", tag="review")
    except Exception as e:
        logger.warning(f"[REVIEWS] low review notify: {e}")
    finally:
        db.close()


def chat_visit(db: Session, user_id, contact_id):
    """Визит, к которому относится ответ клиента в переписке: тот, где просили оценку и её ещё нет, иначе последний."""
    from backend.models.booking import Booking
    now = bs.now_utc()
    q = db.query(Booking).filter(Booking.user_id == user_id, Booking.contact_id == contact_id,
                                 Booking.status.in_(VISITED), Booking.start_at >= now - timedelta(days=CHAT_DAYS))
    return (q.filter(Booking.review_asked_at.isnot(None), Booking.review_score.is_(None))
            .order_by(Booking.start_at.desc()).first()) or q.order_by(Booking.start_at.desc()).first()


def save_from_chat(db: Session, user, contact_id, score, comment: Optional[str] = None) -> Dict[str, Any]:
    """Инструмент бота save_review: оценка из переписки → отзыв, ответ модели — что сказать клиенту (поле next)."""
    try:
        score = clean_score(score)
    except ReviewError as e:
        return {"ok": False, "error": str(e)}
    b = chat_visit(db, user.id, contact_id)
    if b is None:
        return {"ok": False, "error": "Не нашёл недавний визит клиента — оценку некуда сохранить"}
    r, _created, became_low = record(db, b, score, comment, "chat")
    db.commit()
    names = {str(m.id): m.name for m in bs.resources(db, user.id, active_only=False)}
    out = {"ok": True, "score": score, "service": r.service_title or b.service_title,
           "master": names.get(str(r.resource_id)), "published": r.status == "published"}
    if score <= LOW:
        out["low"] = True
        out["next"] = ("Оценка низкая: коротко извинись, спроси, что было не так, и скажи, что администратор "
                       "свяжется. Не спорь и не оправдывайся. Владельцу уже сообщили.")
        if became_low:
            NOTIFY_LOW(user.id, r.id)
    else:
        out["next"] = ("Коротко и тепло поблагодари. Если клиент написал пару слов о визите — их увидят на странице "
                       "салона. Не проси ничего ещё.")
    return out


# ───────────────────────────── страница «Оцените визит» ─────────────────────────────

def link_state(db: Session, token: str) -> Dict[str, Any]:
    """Что показать по ссылке: салон, визит, уже оставленный отзыв. Ошибка — ReviewError с текстом для клиента."""
    from backend.models.booking import Booking, BookingPage
    from backend.models.user import User
    from backend.services import salon_media as sm
    from backend.services import salon_page as sp
    bid = booking_id_from_token(token)
    if bid is None:
        raise ReviewError("Ссылка неверная. Попросите салон прислать её ещё раз.")
    b = db.query(Booking).filter(Booking.id == bid).first()
    if b is None:
        raise ReviewError("Запись не найдена — возможно, её удалили.")
    owner = db.query(User).filter(User.id == b.user_id).first()
    page = db.query(BookingPage).filter(BookingPage.user_id == b.user_id).first()
    if owner is None or not owner.is_active or page is None:
        raise ReviewError("Салон сейчас не принимает отзывы.")
    if b.status not in VISITED:
        raise ReviewError("Оценить можно визит, который уже состоялся.")
    if bs.now_utc() - b.start_at > timedelta(days=LINK_DAYS):
        raise ReviewError("Срок ссылки истёк — оценить визит можно в течение двух месяцев.")
    b = _visit_first(db, b)
    prof = sp.profile_of(page)
    cfg = bs.settings_of(page)
    master = None
    if b.resource_id:
        from backend.models.booking import BookingResource
        m = db.query(BookingResource).filter(BookingResource.id == b.resource_id).first()
        if m is not None:
            master = {"name": m.name, "photo_url": sm.url_of(m.photo_id) if getattr(m, "photo_id", None) else None,
                      "initials": "".join(x[:1] for x in (m.name or "?").split()[:2]).upper(), "color": m.color}
    r = find_review(db, b)
    local = bs.to_local(b.start_at)
    return {
        "salon": {"name": cfg.get("title") or owner.company_name or "Салон", "logo_url": sm.url_of(prof.get("logo_id")),
                  "accent": prof.get("accent") or sp.DEFAULT_ACCENT, "page_url": sp.page_path(page, owner.business_niche),
                  "book_url": f"/b/{page.slug}" if page.enabled else None},
        "visit": {"date": local.date().isoformat(), "time": local.strftime("%H:%M"), "service": _services_of(db, b),
                  "master": master},
        "suggest_name": display_name(b.client_name),
        "review": ({"score": r.score, "text": r.text or "", "author_name": r.author_name or "",
                    "status": r.status, "reply": r.reply_text or ""} if r is not None else None),
        "can_edit": r is None or not r.reply_text,
        "_booking": b,
    }


def save_from_link(db: Session, token: str, score, review_text: Optional[str], name: Optional[str],
                   anonymous: bool = False) -> Dict[str, Any]:
    st = link_state(db, token)
    if not st["can_edit"]:
        raise ReviewError("Салон уже ответил на ваш отзыв — изменить его нельзя.")
    b = st["_booking"]
    author = "" if anonymous else (clean_text(name, MAX_NAME) or st["suggest_name"] or "")
    r, created, became_low = record(db, b, score, review_text, "link", author_name=author)
    db.commit()
    if became_low:
        NOTIFY_LOW(b.user_id, r.id)
    return {"ok": True, "status": r.status, "created": created, "score": r.score}


# ───────────────────────────── рейтинг и лента на странице ─────────────────────────────

def rating(db: Session, user_id) -> Dict[str, Any]:
    """Рейтинг салона по опубликованным: средняя, число оценок и отзывов с текстом, по звёздам."""
    row = db.execute(text("""
        SELECT count(*), avg(score), count(*) FILTER (WHERE COALESCE(text, '') <> ''),
               count(*) FILTER (WHERE score = 5), count(*) FILTER (WHERE score = 4), count(*) FILTER (WHERE score = 3),
               count(*) FILTER (WHERE score = 2), count(*) FILTER (WHERE score = 1)
        FROM salon_reviews WHERE user_id = :u AND status = 'published'
    """), {"u": str(user_id)}).first()
    n = int(row[0] or 0)
    return {"count": n, "avg": round(float(row[1]), 1) if n else None, "texts": int(row[2] or 0),
            "dist": {str(s): int(row[3 + i] or 0) for i, s in enumerate((5, 4, 3, 2, 1))}}


def master_ratings(db: Session, user_id) -> Dict[str, Dict[str, Any]]:
    rows = db.execute(text("""
        SELECT resource_id, count(*), avg(score) FROM salon_reviews
        WHERE user_id = :u AND status = 'published' AND resource_id IS NOT NULL GROUP BY resource_id
    """), {"u": str(user_id)}).fetchall()
    return {str(r[0]): {"count": int(r[1]), "avg": round(float(r[2]), 1)} for r in rows}


def _public_item(r, masters: Dict[str, str]) -> Dict[str, Any]:
    when = r.visit_at or r.created_at
    return {"id": str(r.id), "score": r.score, "text": r.text or "", "author": r.author_name or "",
            "date": bs.to_local(when).date().isoformat() if when else None,
            "master": masters.get(str(r.resource_id)) if r.resource_id else None,
            "master_id": str(r.resource_id) if r.resource_id else None,
            "service": r.service_title or "",
            "reply": r.reply_text or "", "reply_date": bs.to_local(r.reply_at).date().isoformat() if r.reply_at else None}


def public_list(db: Session, user_id, master_id=None, offset: int = 0, limit: int = 10) -> Dict[str, Any]:
    """Опубликованные отзывы с текстом (оценки без слов — только в рейтинге), новые сверху."""
    from backend.models.review import SalonReview
    q = db.query(SalonReview).filter(SalonReview.user_id == user_id, SalonReview.status == "published",
                                     SalonReview.text.isnot(None), SalonReview.text != "")
    mid = _uuid(master_id)
    if master_id and mid is None:
        return {"items": [], "total": 0, "next_offset": None}
    if mid is not None:
        q = q.filter(SalonReview.resource_id == mid)
    total = q.count()
    offset, limit = max(0, int(offset or 0)), max(1, min(int(limit or 10), 30))
    rows = q.order_by(SalonReview.created_at.desc(), SalonReview.id.desc()).offset(offset).limit(limit).all()
    masters = {str(m.id): m.name for m in bs.resources(db, user_id, active_only=False)}
    nxt = offset + len(rows)
    return {"items": [_public_item(r, masters) for r in rows], "total": total,
            "next_offset": nxt if nxt < total else None}


def public_block(db: Session, user_id, show: bool = True) -> Dict[str, Any]:
    """Для витрины: рейтинг + первые отзывы. Выключено владельцем или пусто — {enabled: False}."""
    if not show:
        return {"enabled": False}
    try:
        rt = rating(db, user_id)
        if not rt["count"]:
            return {"enabled": False}
        first = public_list(db, user_id, limit=6)
        return {"enabled": True, **rt, "items": first["items"], "next_offset": first["next_offset"]}
    except Exception as e:
        db.rollback()
        logger.warning(f"[REVIEWS] public block: {e}")
        return {"enabled": False}


# ───────────────────────────── кабинет: проверка и ответы ─────────────────────────────

def counts(db: Session, user_id) -> Dict[str, int]:
    rows = db.execute(text("SELECT status, count(*) FROM salon_reviews WHERE user_id = :u GROUP BY status"),
                      {"u": str(user_id)}).fetchall()
    out = {s: 0 for s in ("published", "pending", "hidden")}
    for s, n in rows:
        out[s] = int(n)
    out["all"] = sum(out.values())
    return out


def pending_count(db: Session, user_id) -> int:
    return int(db.execute(text("SELECT count(*) FROM salon_reviews WHERE user_id = :u AND status = 'pending'"),
                          {"u": str(user_id)}).scalar() or 0)


def _owner_item(r, masters: Dict[str, str], clients: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    d = _public_item(r, masters)
    c = clients.get(str(r.contact_id)) or {}
    d.update(status=r.status, status_title=STATUS_TITLES.get(r.status, r.status), source=r.source,
             source_title=SOURCE_TITLES.get(r.source, r.source), contact_id=str(r.contact_id) if r.contact_id else None,
             client_name=c.get("name") or "", client_phone=c.get("phone") or "",
             created_at=r.created_at.isoformat() if r.created_at else None,
             checked=r.checked_at is not None, reply_by=r.reply_by or "")
    return d


def owner_list(db: Session, user_id, status: Optional[str] = None, master_id=None, score: Optional[int] = None,
               offset: int = 0, limit: int = 30) -> Dict[str, Any]:
    from backend.models.agent_contact import AgentContact
    from backend.models.review import SalonReview
    from backend.services.salon_clients import real_phone
    q = db.query(SalonReview).filter(SalonReview.user_id == user_id)
    if status in ("published", "pending", "hidden"):
        q = q.filter(SalonReview.status == status)
    mid = _uuid(master_id)
    if mid is not None:
        q = q.filter(SalonReview.resource_id == mid)
    if score:
        s = int(score)
        q = q.filter(SalonReview.score <= LOW) if s == -1 else q.filter(SalonReview.score == s)
    total = q.count()
    offset, limit = max(0, int(offset or 0)), max(1, min(int(limit or 30), 100))
    rows = q.order_by(SalonReview.created_at.desc(), SalonReview.id.desc()).offset(offset).limit(limit).all()
    masters = {str(m.id): m.name for m in bs.resources(db, user_id, active_only=False)}
    cids = [r.contact_id for r in rows if r.contact_id]
    clients = {}
    if cids:
        for c in db.query(AgentContact).filter(AgentContact.id.in_(cids)).all():
            clients[str(c.id)] = {"name": c.name, "phone": real_phone(c.phone)}
    nxt = offset + len(rows)
    return {"items": [_owner_item(r, masters, clients) for r in rows], "total": total,
            "next_offset": nxt if nxt < total else None}


def overview(db: Session, user) -> Dict[str, Any]:
    """Шапка раздела «Отзывы»: рейтинг, счётчики, мастера, просят ли оценку, показываются ли на странице."""
    from backend.services import automations
    from backend.services import salon_page as sp
    from backend.models.booking import BookingPage
    page = db.query(BookingPage).filter(BookingPage.user_id == user.id).first()
    show = page is not None and sp.profile_of(page).get("show_reviews", True) is not False
    st = automations.states(db, user.id)
    asking = bool(st["thanks"]["params"].get("ask_review"))
    manager = False
    try:
        if user.agent_mode() == "full":
            from backend.models.agent_config import AgentConfig
            from backend.services import salon_manager
            agent = db.query(AgentConfig).filter(AgentConfig.user_id == user.id).order_by(AgentConfig.created_at).first()
            manager = agent is not None and salon_manager.review_on(agent)
    except Exception:
        db.rollback()
    mr = master_ratings(db, user.id)
    masters = []
    for m in bs.resources(db, user.id, active_only=False):
        d = bs.resource_to_dict(m)
        masters.append({"id": str(m.id), "name": m.name, "photo_url": d.get("photo_url"), "initials": d.get("initials"),
                        "color": m.color, **(mr.get(str(m.id)) or {"count": 0, "avg": None})})
    masters.sort(key=lambda m: (-(m["count"] > 0), -(m["avg"] or 0), m["name"] or ""))
    return {"rating": rating(db, user.id), "counts": counts(db, user.id), "masters": masters,
            "asking": asking or manager, "asking_by": "manager" if manager and not asking else ("rule" if asking else None),
            "show_on_page": show, "page_path": sp.page_path(page, user.business_niche) if page else None}


def get_owned(db: Session, user_id, review_id):
    from backend.models.review import SalonReview
    rid = _uuid(review_id)
    r = db.query(SalonReview).filter(SalonReview.id == rid, SalonReview.user_id == user_id).first() if rid else None
    if r is None:
        raise ReviewError("Отзыв не найден")
    return r


def _short(r) -> str:
    return f"{r.score}★" + (f" «{(r.text or '')[:60]}»" if r.text else "") + (f" — {r.author_name}" if r.author_name else "")


def moderate(db: Session, user, review_id, action: str) -> Dict[str, Any]:
    """Показать на странице / скрыть (без коммита — коммитит API вместе с журналом действий)."""
    from backend.services import activity
    r = get_owned(db, user.id, review_id)
    if action not in ("publish", "hide"):
        raise ReviewError("Неизвестное действие")
    new = "published" if action == "publish" else "hidden"
    old = r.status
    r.status, r.checked_at, r.updated_at = new, _now(), _now()
    if new == "published" and r.published_at is None:
        r.published_at = _now()
    if old != new:
        activity.log(db, user.id, "review." + action, "review",
                     ("Показал отзыв на странице: " if new == "published" else "Скрыл отзыв: ") + _short(r),
                     object_id=r.id, changes={"status": [STATUS_TITLES.get(old), STATUS_TITLES.get(new)]}, user=user)
    return {"id": str(r.id), "status": r.status}


def reply(db: Session, user, review_id, reply_text: Optional[str], send: bool = False,
          publish: Optional[bool] = None) -> Dict[str, Any]:
    """Ответ салона (пусто — убрать). send — ещё и клиенту в мессенджер через очередь. publish — заодно решить по показу."""
    from backend.services import activity
    from backend.services import notify_queue as nq
    r = get_owned(db, user.id, review_id)
    body = clean_text(reply_text, MAX_REPLY)
    old = r.reply_text
    r.reply_text = body
    r.reply_at = _now() if body else None
    r.reply_by = activity.actor_for(user).get("name") if body else None
    r.updated_at = _now()
    queued = False
    if body and send and r.contact_id:
        digest = hashlib.sha1(body.encode()).hexdigest()[:10]
        queued = bool(nq.enqueue(db, user_id=user.id, contact_id=r.contact_id, rule="review_reply", text_=body,
                                 idem_key=f"review_reply:{r.id}:{digest}", kind="service", booking_id=r.booking_id,
                                 expires_at=_now() + timedelta(days=3)))
    if (old or "") != (body or ""):
        activity.log(db, user.id, "review.reply", "review",
                     ("Ответил на отзыв " if body else "Убрал ответ на отзыв ") + _short(r)
                     + (" (и отправил клиенту)" if queued else ""),
                     object_id=r.id, changes={"reply": [old or "", body or ""]}, user=user)
    out = {"id": str(r.id), "reply": r.reply_text or "", "queued": queued}
    if publish is not None:
        out.update(moderate(db, user, review_id, "publish" if publish else "hide"))
    return out


def set_show_on_page(db: Session, user, show: bool) -> bool:
    from backend.models.booking import BookingPage
    from backend.services import activity
    page = db.query(BookingPage).filter(BookingPage.user_id == user.id).first()
    if page is None:
        raise ReviewError("Сначала включите онлайн-запись — у салона ещё нет страницы")
    prof = dict(page.profile or {})
    old = prof.get("show_reviews", True) is not False
    prof["show_reviews"] = bool(show)
    page.profile = prof
    if old != bool(show):
        activity.log(db, user.id, "review.settings", "review",
                     "Отзывы на странице салона: " + ("показывать" if show else "не показывать"), user=user)
    return bool(show)


# ───────────────────────────── перенос старых оценок ─────────────────────────────

def backfill_from_bookings(conn) -> int:
    """Оценки до шага 2.3 (bookings.review_score) → salon_reviews, по правилам публикации. Повтор безопасен."""
    rows = conn.execute(text("""
        SELECT b.id, b.user_id, b.visit_id, b.contact_id, b.resource_id, b.service_title, b.start_at, b.client_name,
               b.review_score, b.review_text, COALESCE(b.review_at, b.end_at)
        FROM bookings b
        WHERE b.review_score BETWEEN 1 AND 5
          AND NOT EXISTS (SELECT 1 FROM salon_reviews r WHERE r.booking_id = b.id
                          OR (b.visit_id IS NOT NULL AND r.visit_id = b.visit_id))
    """)).fetchall()
    n, seen_visits = 0, set()
    for (bid, uid, vid, cid, rid, svc, start, cname, score, txt, at) in rows:
        if vid is not None:
            if vid in seen_visits:
                continue
            seen_visits.add(vid)
        st = status_for(score)
        conn.execute(text("""
            INSERT INTO salon_reviews (id, user_id, booking_id, visit_id, contact_id, resource_id, service_title, visit_at,
                                       score, text, author_name, source, status, published_at, created_at, updated_at)
            VALUES (:id, :u, :b, :v, :c, :r, :svc, :st_at, :score, :txt, :author, 'chat', :status, :pub, :at, :at)
            ON CONFLICT (booking_id) DO NOTHING
        """), {"id": uuid.uuid4(), "u": uid, "b": bid, "v": vid, "c": cid, "r": rid, "svc": (svc or "")[:300] or None,
               "st_at": start, "score": int(score), "txt": txt, "author": display_name(cname) or None, "status": st,
               "pub": at if st == "published" else None, "at": at or _now()})
        n += 1
    return n
