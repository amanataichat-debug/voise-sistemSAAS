"""
Фото салона и мастеров (Voksy Beauty, шаг 1.2): аватар мастера, портфолио, сертификаты, фото помещения.

  • Загрузка: картинка проверяется Pillow (JPEG / PNG / WEBP / GIF; разворот по EXIF, без метаданных —
    в EXIF телефона бывают координаты), уменьшается по виду (аватар 720 px, портфолио 1600, сертификат
    1800, салон 1920, логотип 512) и сохраняется JPEG ≈ 80–300 КБ. Прозрачность — на белом фоне.
    Фото салона (обложка, галерея — вид «salon») и логотип показывает страница салона /beauty/<slug>.
  • Хранение: R2 (salon/<user>/<id>.jpg), если он настроен; иначе байты прямо в salon_media.data —
    кабинет и тесты работают и без R2, а фото уже маленькие.
  • Отдача: GET /media/salon/<id> без входа — фото показывает страница салона и онлайн-запись.
    Содержимое по id никогда не меняется (новое фото = новый id), поэтому кэш браузера на год.
    Байты из R2 держатся в памяти воркера (LRU, CACHE_BYTES) — вторая страница салона не ходит в R2.

Всё синхронное: зовётся из обработчиков-def или через asyncio.to_thread (Pillow — это CPU).
"""

from __future__ import annotations

import io
import logging
import threading
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

KINDS = ("avatar", "portfolio", "certificate", "salon", "logo")
MAX_SIDE = {"avatar": 720, "portfolio": 1600, "certificate": 1800, "salon": 1920, "logo": 512}
QUALITY = {"avatar": 84, "portfolio": 82, "certificate": 85, "salon": 82, "logo": 90}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 60_000_000                  # защита от «картинки-бомбы»
LIMITS = {"portfolio": 30, "certificate": 12, "salon": 20}   # на мастера (портфолио, сертификаты) / на салон
CACHE_BYTES = 48 * 1024 * 1024


class MediaError(ValueError):
    """Понятная причина отказа (уходит в кабинет как есть)."""


# ───────────────────────────── картинка ─────────────────────────────

def prepare_image(data: bytes, kind: str) -> Tuple[bytes, int, int]:
    """Проверить и уменьшить картинку → (jpeg, ширина, высота)."""
    if kind not in KINDS:
        raise MediaError("Неизвестный вид фото")
    if not data:
        raise MediaError("Пустой файл")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaError("Фото больше 15 МБ — выберите поменьше")
    try:
        from PIL import Image, ImageOps
    except ImportError:          # Pillow приходит вместе с reportlab; без него — понятная ошибка
        raise MediaError("Обработка фото недоступна на сервере")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        img = Image.open(io.BytesIO(data))
        fmt = (img.format or "").upper()
        if fmt not in ("JPEG", "PNG", "WEBP", "GIF", "MPO"):
            raise MediaError("Нужна картинка JPG, PNG или WEBP")
        img.seek(0)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        side = MAX_SIDE[kind]
        if max(img.size) > side:
            img.thumbnail((side, side), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=QUALITY[kind], optimize=True, progressive=True)
        return out.getvalue(), img.size[0], img.size[1]
    except MediaError:
        raise
    except Image.DecompressionBombError:
        raise MediaError("Слишком большое изображение")
    except Exception as e:
        logger.info(f"[SALON-MEDIA] not an image: {e}")
        raise MediaError("Не получилось открыть фото — нужна картинка JPG, PNG или WEBP")


# ───────────────────────────── R2 (синхронно, мы уже в потоке) ─────────────────────────────

def _r2():
    from backend.services.r2_storage import R2StorageService
    if not R2StorageService.objects_available():
        return None
    return R2StorageService._get_client()


def _bucket() -> str:
    from backend.core.config import settings
    return settings.R2_BUCKET


def _r2_put(key: str, data: bytes) -> bool:
    client = _r2()
    if client is None:
        return False
    try:
        client.put_object(Bucket=_bucket(), Key=key, Body=data, ContentType="image/jpeg",
                          CacheControl="public, max-age=31536000, immutable")
        return True
    except Exception as e:
        logger.error(f"[SALON-MEDIA] R2 put {key}: {e}")
        return False


def _r2_get(key: str) -> Optional[bytes]:
    client = _r2()
    if client is None:
        return None
    try:
        return client.get_object(Bucket=_bucket(), Key=key)["Body"].read()
    except Exception as e:
        logger.error(f"[SALON-MEDIA] R2 get {key}: {e}")
        return None


def _r2_delete(key: str) -> None:
    client = _r2()
    if client is None:
        return
    try:
        client.delete_object(Bucket=_bucket(), Key=key)
    except Exception as e:
        logger.warning(f"[SALON-MEDIA] R2 delete {key}: {e}")


# ───────────────────────────── кэш байтов из R2 ─────────────────────────────

_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_CACHE_SIZE = 0
_LOCK = threading.Lock()


def _cache_get(key: str) -> Optional[bytes]:
    with _LOCK:
        b = _CACHE.get(key)
        if b is not None:
            _CACHE.move_to_end(key)
        return b


def _cache_put(key: str, b: bytes) -> None:
    global _CACHE_SIZE
    if len(b) > CACHE_BYTES // 8:
        return
    with _LOCK:
        if key in _CACHE:
            return
        _CACHE[key] = b
        _CACHE_SIZE += len(b)
        while _CACHE_SIZE > CACHE_BYTES and _CACHE:
            _, old = _CACHE.popitem(last=False)
            _CACHE_SIZE -= len(old)


def _cache_drop(key: str) -> None:
    global _CACHE_SIZE
    with _LOCK:
        old = _CACHE.pop(key, None)
        if old is not None:
            _CACHE_SIZE -= len(old)


# ───────────────────────────── строки salon_media ─────────────────────────────

def url_of(media_id) -> Optional[str]:
    """Адрес фото для страниц (относительный — работает и на сайте салона, и в кабинете)."""
    return f"/media/salon/{media_id}" if media_id else None


def store(db: Session, user_id, kind: str, data: bytes, resource_id=None):
    """Уменьшить и сохранить фото. Без commit — его делает вызывающий."""
    from backend.models.booking import SalonMedia
    jpeg, w, h = prepare_image(data, kind)
    mid = uuid.uuid4()
    row = SalonMedia(id=mid, user_id=user_id, kind=kind, resource_id=resource_id, mime="image/jpeg",
                     width=w, height=h, size_bytes=len(jpeg))
    key = f"salon/{user_id}/{mid}.jpg"
    if _r2_put(key, jpeg):
        row.storage_key = key
    else:
        row.data = jpeg
    db.add(row)
    db.flush()
    return row


def get_row(db: Session, media_id, user_id=None):
    from backend.models.booking import SalonMedia
    try:
        mid = uuid.UUID(str(media_id))
    except (ValueError, TypeError):
        return None
    q = db.query(SalonMedia).filter(SalonMedia.id == mid)
    if user_id is not None:
        q = q.filter(SalonMedia.user_id == user_id)
    return q.first()


def read_bytes(db: Session, media_id) -> Optional[bytes]:
    """Байты фото для /media/salon/<id> (None — нет такого)."""
    key = str(media_id)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    row = get_row(db, media_id)
    if row is None:
        return None
    if row.data:
        return bytes(row.data)
    if row.storage_key:
        b = _r2_get(row.storage_key)
        if b:
            _cache_put(key, b)
        return b
    return None


def delete(db: Session, user_id, media_id) -> bool:
    """Удалить фото владельца (строку и объект R2). Без commit."""
    row = get_row(db, media_id, user_id)
    if row is None:
        return False
    if row.storage_key:
        _r2_delete(row.storage_key)
    _cache_drop(str(row.id))
    db.delete(row)
    db.flush()
    return True


def delete_many(db: Session, user_id, ids: List[Any]) -> int:
    n = 0
    for mid in ids or []:
        if mid and delete(db, user_id, mid):
            n += 1
    return n


def media_items(raw: Any, title_key: str = "caption") -> List[Dict[str, Any]]:
    """Список портфолио/сертификатов из JSONB → [{id, url, caption|title}] (битые элементы пропускаются)."""
    out = []
    for it in (raw or []):
        if isinstance(it, dict) and it.get("id"):
            out.append({"id": str(it["id"]), "url": url_of(it["id"]),
                        title_key: str(it.get(title_key) or "")[:200]})
    return out
