"""
Страница салона (Voksy Beauty, шаг 1.5): публичная витрина `/beauty/<slug>` с записью по шагам.

Данные
  • `booking_pages.settings` — правила записи (как раньше) + название `title` и адрес `address`
    (их же берут напоминания клиенту и бот).
  • `booking_pages.profile` (JSONB) — то, что видит клиент на странице: слоган, «о салоне», логотип,
    обложка, галерея, телефон, WhatsApp, Instagram, Telegram, сайт, ссылка на карту, удобства, акции,
    фирменный цвет, показывать ли цены. Чистится здесь (`clean_profile`), ничего лишнего не хранится.
  • Фото — `salon_media` (виды `logo` и `salon`), как у мастеров: `/media/salon/<id>` без входа.

Публично (`public_view`) отдаётся только то, что салон сам выставил: без email владельца, графиков
мастеров по датам, оплаты мастеров и телефонов клиентов. Часы работы салона = объединение рабочих
часов активных мастеров по дням недели (отдельно их вводить не нужно).

Превью ссылки (WhatsApp, Telegram, Instagram — они не выполняют JS) и разметка для Google строятся
на сервере: `head_html` → <title>, description, Open Graph, JSON-LD (BeautySalon / LocalBusiness).

Всё синхронное — зовётся из обработчиков-def.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from sqlalchemy.orm import Session

from backend.services import booking_service as bs
from backend.services.salon_media import url_of

ACCENTS = ("#d6336c", "#be185d", "#9d174d", "#7c3aed", "#2a5ce8", "#0d9488", "#b45309", "#111827")
DEFAULT_ACCENT = "#d6336c"
AMENITIES = {
    "wifi": "Wi-Fi", "parking": "Парковка", "card": "Оплата картой", "qr": "Оплата по QR",
    "coffee": "Кофе и чай", "kids": "Можно с детьми", "sterile": "Стерилизация инструментов",
    "aircon": "Кондиционер", "women": "Только для женщин", "men": "Мужской зал",
    "access": "Доступно для колясок", "gift": "Подарочные сертификаты",
}
MAX_GALLERY = 20
MAX_PROMOS = 6
BEAUTY_NICHES = ("beauty_salon", "barbershop", "nails", "massage_spa", "cosmetology")

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_IG = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_TG = re.compile(r"^[A-Za-z0-9_]{4,32}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PageError(ValueError):
    """Понятная причина отказа (уходит в кабинет как есть)."""


# ───────────────────────────── чистка полей ─────────────────────────────

def _text(v: Any, n: int) -> str:
    s = str(v or "").replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(" ".join(line.split()) for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s[:n]


def _line(v: Any, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _phone(v: Any, field: str) -> str:
    s = _line(v, 40)
    if not s:
        return ""
    from backend.utils.phone import normalize_phone_e164
    p = normalize_phone_e164(s)
    if not p:
        raise PageError(f"{field}: неверный номер, например +996 700 123 456")
    return p


def _url(v: Any, field: str, n: int = 500) -> str:
    s = _line(v, n)
    if not s:
        return ""
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    u = urlparse(s)
    if u.scheme not in ("http", "https") or not u.netloc or "." not in u.netloc or " " in s:
        raise PageError(f"{field}: нужна ссылка вида https://…")
    return s


def instagram_handle(v: Any) -> str:
    s = _line(v, 200)
    if not s:
        return ""
    s = re.sub(r"^(https?://)?(www\.)?(instagram\.com|instagr\.am)/", "", s, flags=re.I)
    s = s.lstrip("@").split("/")[0].split("?")[0]
    if not _IG.match(s):
        raise PageError("Instagram: укажите имя аккаунта, например @salon.aidana")
    return s


def telegram_handle(v: Any) -> str:
    s = _line(v, 200)
    if not s:
        return ""
    s = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/", "", s, flags=re.I)
    s = s.lstrip("@").split("/")[0].split("?")[0]
    if not _TG.match(s):
        raise PageError("Telegram: укажите имя, например @salon_aidana")
    return s


def _promos(raw: Any) -> List[Dict[str, Any]]:
    out = []
    for it in (raw or [])[:MAX_PROMOS]:
        if not isinstance(it, dict):
            continue
        title = _line(it.get("title"), 80)
        if not title:
            continue
        until = str(it.get("until") or "").strip()
        if until and not _DATE.match(until):
            raise PageError("Акция: дата окончания в формате ГГГГ-ММ-ДД")
        out.append({"title": title, "text": _text(it.get("text"), 300), "until": until or None})
    return out


def profile_of(page) -> Dict[str, Any]:
    p = dict((getattr(page, "profile", None) or {}) if page is not None else {})
    p.setdefault("accent", DEFAULT_ACCENT)
    p.setdefault("show_prices", True)
    p.setdefault("amenities", [])
    p.setdefault("promos", [])
    p.setdefault("gallery", [])
    return p


def save_profile(db: Session, page, data: Dict[str, Any]) -> Dict[str, Any]:
    """Записать поля страницы (только присланные). title/address уходят в настройки записи."""
    prof = profile_of(page)
    cfg = dict(page.settings or {})
    if "title" in data:
        cfg["title"] = _line(data.get("title"), 120)
    if "address" in data:
        cfg["address"] = _line(data.get("address"), 300)
    if "tagline" in data:
        prof["tagline"] = _line(data.get("tagline"), 140)
    if "about" in data:
        prof["about"] = _text(data.get("about"), 2000)
    if "phone" in data:
        prof["phone"] = _phone(data.get("phone"), "Телефон")
    if "whatsapp" in data:
        prof["whatsapp"] = _phone(data.get("whatsapp"), "WhatsApp")
    if "instagram" in data:
        prof["instagram"] = instagram_handle(data.get("instagram"))
    if "telegram" in data:
        prof["telegram"] = telegram_handle(data.get("telegram"))
    if "website" in data:
        prof["website"] = _url(data.get("website"), "Сайт", 200)
    if "map_url" in data:
        prof["map_url"] = _url(data.get("map_url"), "Ссылка на карту")
    if "hours_note" in data:
        prof["hours_note"] = _line(data.get("hours_note"), 160)
    if "amenities" in data:
        prof["amenities"] = [k for k in dict.fromkeys(data.get("amenities") or []) if k in AMENITIES]
    if "promos" in data:
        prof["promos"] = _promos(data.get("promos"))
    if "accent" in data:
        a = str(data.get("accent") or "").strip()
        if not _HEX.match(a):
            raise PageError("Цвет: выберите из списка или укажите #rrggbb")
        prof["accent"] = a.lower()
    if "show_prices" in data:
        prof["show_prices"] = bool(data.get("show_prices"))
    if "show_reviews" in data:
        prof["show_reviews"] = bool(data.get("show_reviews"))
    if "gallery" in data:
        prof["gallery"] = reorder_gallery(prof.get("gallery"), data.get("gallery"))
    page.profile = prof
    page.settings = cfg
    db.flush()
    bs.notify_changed(db, page.user_id)
    return prof


def reorder_gallery(current: Any, wanted: Any) -> List[Dict[str, Any]]:
    """Новый порядок и подписи галереи — только из тех фото, что уже загружены."""
    have = {str(it.get("id")): it for it in (current or []) if isinstance(it, dict) and it.get("id")}
    out, seen = [], set()
    for it in (wanted or []):
        mid = str((it or {}).get("id") or "") if isinstance(it, dict) else ""
        if mid in have and mid not in seen:
            seen.add(mid)
            out.append({"id": mid, "caption": _line(it.get("caption"), 120)})
    for mid, it in have.items():         # не прислали — остаётся в конце (удаляется только кнопкой)
        if mid not in seen:
            out.append({"id": mid, "caption": _line(it.get("caption"), 120)})
    return out


# ───────────────────────────── часы работы ─────────────────────────────

def salon_hours(resources: List[Any]) -> List[Dict[str, Any]]:
    """Часы салона по дням недели = от раннего начала до позднего конца смен активных мастеров."""
    out = []
    for i, d in enumerate(bs.DAYS):
        lo, hi = None, None
        for r in resources:
            for a, b in bs.clean_schedule(r.schedule or bs.DEFAULT_SCHEDULE).get(d, []):
                lo = a if lo is None or a < lo else lo
                hi = b if hi is None or b > hi else hi
        out.append({"day": d, "title": bs.DAY_TITLES[i], "open": lo, "close": hi})
    return out


def hours_text(hours: List[Dict[str, Any]]) -> str:
    """«пн–пт 09:00–20:00, сб 10:00–18:00» — для описания и разметки."""
    parts, i = [], 0
    while i < len(hours):
        h = hours[i]
        j = i
        while j + 1 < len(hours) and hours[j + 1]["open"] == h["open"] and hours[j + 1]["close"] == h["close"]:
            j += 1
        if h["open"]:
            days = h["title"] if i == j else f'{h["title"]}–{hours[j]["title"]}'
            parts.append(f'{days} {h["open"]}–{h["close"]}')
        i = j + 1
    return ", ".join(parts)


# ───────────────────────────── публичная выдача ─────────────────────────────

def map_link(prof: Dict[str, Any], address: str) -> Optional[str]:
    if prof.get("map_url"):
        return prof["map_url"]
    if address:
        return "https://www.google.com/maps/search/?api=1&query=" + quote(address)
    return None


def staff_word(niche: Optional[str]) -> str:
    return "Мастера" if (niche or "") in BEAUTY_NICHES or not niche else "Специалисты"


def page_path(page, niche: Optional[str] = None) -> str:
    """Адрес витрины: салонам — /beauty/<slug>, другим нишам с записью — /p/<slug> (страница та же)."""
    return f"/beauty/{page.slug}" if (niche or "") in BEAUTY_NICHES or not niche else f"/p/{page.slug}"


def service_photos(db: Session, owner_id, services: List[Any]) -> Dict[str, str]:
    """Первое фото услуги из каталога бота → подписанная ссылка R2 на сутки (страница берёт её при открытии)."""
    want = {}
    for e in services:
        ids = [str(x) for x in (getattr(e, "media_file_ids", None) or [])]
        if ids:
            want[str(e.id)] = ids
    if not want:
        return {}
    try:
        import uuid as _uuid
        from backend.models.agent_file import AgentFile
        from backend.services.agent_media_service import file_link
        flat = []
        for ids in want.values():
            for x in ids:
                try:
                    flat.append(_uuid.UUID(x))
                except ValueError:
                    pass
        rows = {str(f.id): f for f in db.query(AgentFile).filter(AgentFile.user_id == owner_id, AgentFile.id.in_(flat)).all()}
        out = {}
        for sid, ids in want.items():
            for x in ids:
                f = rows.get(x)
                if f is not None and str(getattr(f, "mime_type", "") or "").startswith("image/"):
                    link = file_link(f, 24 * 3600)
                    if link:
                        out[sid] = link
                        break
        return out
    except Exception:
        return {}


def public_view(db: Session, page, owner) -> Dict[str, Any]:
    """Всё для витрины салона (без кабинетных данных)."""
    from backend.services.niche_catalog import niche_title
    prof = profile_of(page)
    cfg = bs.settings_of(page)
    active = bs.resources(db, owner.id)
    hours = salon_hours(active)
    today = bs.to_local(bs.now_utc()).date().isoformat()
    promos = [p for p in prof.get("promos") or [] if not p.get("until") or p["until"] >= today]
    gallery = [{"id": it["id"], "url": url_of(it["id"]), "caption": it.get("caption") or ""}
               for it in prof.get("gallery") or [] if isinstance(it, dict) and it.get("id")]
    address = cfg.get("address") or ""
    return {
        "name": cfg.get("title") or owner.company_name or owner.first_name or "Онлайн-запись",
        "niche": niche_title(owner.business_niche, owner.business_niche_other),
        "staff_word": staff_word(owner.business_niche),
        "tagline": prof.get("tagline") or "",
        "about": prof.get("about") or "",
        "logo_url": url_of(prof.get("logo_id")),
        "cover_url": url_of(prof.get("cover_id")),
        "gallery": gallery,
        "address": address,
        "map_url": map_link(prof, address),
        "phone": prof.get("phone") or "",
        "whatsapp": prof.get("whatsapp") or "",
        "instagram": prof.get("instagram") or "",
        "telegram": prof.get("telegram") or "",
        "website": prof.get("website") or "",
        "amenities": [{"key": k, "title": AMENITIES[k]} for k in prof.get("amenities") or [] if k in AMENITIES],
        "promos": promos,
        "accent": prof.get("accent") or DEFAULT_ACCENT,
        "show_prices": prof.get("show_prices", True) is not False,
        "hours": hours,
        "hours_note": prof.get("hours_note") or "",
        "booking_enabled": bool(page.enabled),
        "page_path": page_path(page, owner.business_niche),
        "book_path": f"/b/{page.slug}",
        "chat": _chat_state(db, page, owner),
        "reviews": _reviews_block(db, owner, prof),
    }


def _reviews_block(db: Session, owner, prof: Dict[str, Any]) -> Dict[str, Any]:
    """Отзывы на витрине (шаг 2.3): рейтинг по опубликованным и первые отзывы с текстом."""
    from backend.services import reviews_service
    return reviews_service.public_block(db, owner.id, prof.get("show_reviews", True) is not False)


def _chat_state(db: Session, page, owner) -> Dict[str, Any]:
    from backend.services import webchat_service
    return webchat_service.public_state(db, page, owner)


# ───────────────────────────── кабинет: готовность ─────────────────────────────

def readiness(db: Session, page, owner) -> Dict[str, Any]:
    """Чего не хватает странице — по пунктам, с весом (для полосы «Страница готова на N%»)."""
    prof = profile_of(page)
    cfg = bs.settings_of(page)
    masters = bs.resources(db, owner.id)
    items = [
        ("title", "Название салона", bool(cfg.get("title") or owner.company_name), 10),
        ("cover", "Обложка — фото зала или работ", bool(prof.get("cover_id")), 15),
        ("logo", "Логотип", bool(prof.get("logo_id")), 5),
        ("tagline", "Короткая фраза о салоне", bool(prof.get("tagline")), 10),
        ("address", "Адрес", bool(cfg.get("address")), 15),
        ("contacts", "Телефон или WhatsApp", bool(prof.get("phone") or prof.get("whatsapp")), 10),
        ("gallery", "Фото салона (от 3)", len(prof.get("gallery") or []) >= 3, 10),
        ("master_photos", "Фото мастеров", bool(masters) and all(r.photo_id for r in masters), 15),
        ("about", "Пара слов о салоне", bool(prof.get("about")), 10),
    ]
    done = sum(w for _, _, ok, w in items if ok)
    return {"percent": int(round(done * 100 / sum(w for *_, w in items))),
            "todo": [{"key": k, "title": t} for k, t, ok, _ in items if not ok]}


# ───────────────────────────── <head> для превью ссылки и Google ─────────────────────────────

def _abs(host: str, path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return path if path.startswith("http") else host + path


def head_html(view: Dict[str, Any], host: str, canonical_path: str) -> str:
    """<title>, description, Open Graph и JSON-LD — строятся на сервере, всё экранировано."""
    name = view["name"]
    desc_parts = [view.get("tagline") or view.get("niche") or "Онлайн-запись"]
    if view.get("address"):
        desc_parts.append(view["address"])
    ht = hours_text(view.get("hours") or [])
    if ht:
        desc_parts.append(ht)
    desc = ". ".join(p.rstrip(".") for p in desc_parts if p) + ". Запись онлайн за минуту."
    url = host + canonical_path
    image = _abs(host, view.get("cover_url") or view.get("logo_url") or (view["gallery"][0]["url"] if view.get("gallery") else None))
    e = lambda s: html.escape(str(s or ""), quote=True)   # noqa: E731
    out = [
        f"<title>{e(name)} — онлайн-запись</title>",
        f'<meta name="description" content="{e(desc[:300])}">',
        f'<link rel="canonical" href="{e(url)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{e(name)}">',
        f'<meta property="og:description" content="{e(desc[:300])}">',
        f'<meta property="og:url" content="{e(url)}">',
        '<meta property="og:locale" content="ru_RU">',
        f'<meta name="theme-color" content="{e(view.get("accent") or DEFAULT_ACCENT)}">',
    ]
    if image:
        out += [f'<meta property="og:image" content="{e(image)}">', '<meta name="twitter:card" content="summary_large_image">']
    ld: Dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "BeautySalon" if view.get("staff_word") == "Мастера" else "LocalBusiness",
        "name": name, "url": url, "description": desc[:300],
    }
    if image:
        ld["image"] = image
    if view.get("address"):
        ld["address"] = {"@type": "PostalAddress", "streetAddress": view["address"], "addressCountry": "KG"}
    if view.get("phone"):
        ld["telephone"] = view["phone"]
    same = []
    if view.get("instagram"):
        same.append(f"https://instagram.com/{view['instagram']}")
    if view.get("telegram"):
        same.append(f"https://t.me/{view['telegram']}")
    if view.get("website"):
        same.append(view["website"])
    if same:
        ld["sameAs"] = same
    days = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
            "sat": "Saturday", "sun": "Sunday"}
    spec = [{"@type": "OpeningHoursSpecification", "dayOfWeek": days[h["day"]], "opens": h["open"], "closes": h["close"]}
            for h in view.get("hours") or [] if h.get("open")]
    if spec:
        ld["openingHoursSpecification"] = spec
    rv = view.get("reviews") or {}
    if rv.get("enabled") and rv.get("count") and rv.get("avg"):
        ld["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": rv["avg"], "bestRating": 5,
                                 "worstRating": 1, "ratingCount": rv["count"]}
        if rv.get("texts"):
            ld["aggregateRating"]["reviewCount"] = rv["texts"]
        items = [it for it in rv.get("items") or [] if it.get("text")][:5]
        if items:
            ld["review"] = [{"@type": "Review", "reviewRating": {"@type": "Rating", "ratingValue": it["score"],
                                                                 "bestRating": 5},
                             "author": {"@type": "Person", "name": it.get("author") or "Клиент салона"},
                             "datePublished": it.get("date"), "reviewBody": it["text"][:500]} for it in items]
    # </script> внутри строк JSON не должен закрыть тег
    blob = json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
    out.append(f'<script type="application/ld+json">{blob}</script>')
    return "\n  ".join(out)


def is_valid_slug(slug: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9-]{3,64}", slug or ""))

