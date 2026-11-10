"""
Google Таблица агента (Старт и Profi): запись заявок и импорт прайса в каталог.

Владелец вставляет ссылку на свою таблицу и даёт доступ «Редактор» служебному
адресу платформы (service account из GOOGLE_SERVICE_ACCOUNT_JSON).

  Заявки: новый клиент → строка на листе «Заявки» (создаётся сам):
          дата, канал, имя, телефон, стадия, первое сообщение.
  Каталог: первый лист таблицы читается как прайс. Колонки ищутся по названиям
          в первой строке (Название / Цена / Описание / Наличие / Ключевые слова),
          каждая строка становится позицией каталога базы знаний. Повторный импорт
          обновляет позиции из таблицы и не трогает добавленные вручную.

Все вызовы Google API синхронные — снаружи их зовут через asyncio.to_thread.
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LEADS_SHEET_TITLE = "Заявки"
LEADS_HEADER = ["Дата (Бишкек)", "Канал", "Имя", "Телефон", "Стадия", "Сообщение"]
CATALOG_MAX_ROWS = 1000
CATALOG_SOURCE = "google_sheet"  # attributes["_source"] у импортированных позиций

_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]{20,})")

COLUMN_ALIASES = {
    "title": ("название", "наименование", "товар", "услуга", "позиция", "name", "title", "аталышы"),
    "price": ("цена", "стоимость", "price", "баасы", "баа", "сом"),
    "body": ("описание", "характеристики", "description", "сүрөттөмө"),
    "in_stock": ("наличие", "в наличии", "остаток", "stock", "бар"),
    "keywords": ("ключевые слова", "синонимы", "keywords", "теги"),
}


class SheetError(Exception):
    """Понятная владельцу ошибка (текст — для интерфейса)."""


def parse_sheet_id(value: str) -> Optional[str]:
    value = (value or "").strip()
    m = _ID_RE.search(value)
    if m:
        return m.group(1)
    return value if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", value) else None


def service_account_email() -> Optional[str]:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    try:
        return json.loads(raw).get("client_email")
    except Exception:
        return None


def _service():
    from backend.services.google_sheets_service import GoogleSheetsService
    try:
        service = GoogleSheetsService._get_sheets_service()
    except Exception:
        service = None
    if service is None:
        raise SheetError("Google Таблицы не настроены на сервере. Напишите в поддержку.")
    return service


def _http_error(e) -> SheetError:
    status = getattr(getattr(e, "resp", None), "status", None)
    email = service_account_email() or "служебному адресу платформы"
    if status == 403:
        return SheetError(f"Нет доступа. Откройте таблицу для {email} с правом «Редактор».")
    if status == 404:
        return SheetError("Таблица не найдена. Проверьте ссылку.")
    return SheetError("Google не ответил. Попробуйте ещё раз через минуту.")


def check_access(sheet_id: str) -> Dict[str, Any]:
    """Название таблицы и её листы; SheetError, если доступа нет."""
    from googleapiclient.errors import HttpError
    try:
        meta = _service().spreadsheets().get(
            spreadsheetId=sheet_id, fields="properties.title,sheets.properties.title"
        ).execute()
    except HttpError as e:
        raise _http_error(e)
    return {
        "title": meta.get("properties", {}).get("title") or "Без названия",
        "sheets": [s["properties"]["title"] for s in meta.get("sheets", [])],
    }


# ---------------------------------------------------------------- заявки

def _ensure_leads_sheet(service, sheet_id: str) -> None:
    meta = service.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if LEADS_SHEET_TITLE in titles:
        return
    service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={
        "requests": [{"addSheet": {"properties": {"title": LEADS_SHEET_TITLE, "gridProperties": {"frozenRowCount": 1}}}}]
    }).execute()
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"'{LEADS_SHEET_TITLE}'!A1",
        valueInputOption="RAW", body={"values": [LEADS_HEADER]},
    ).execute()


def append_lead(sheet_id: str, channel: str, name: str, phone: str, stage: str, message: str) -> None:
    from googleapiclient.errors import HttpError
    from backend.core.timezone_utils import now_bishkek
    service = _service()
    try:
        _ensure_leads_sheet(service, sheet_id)
        row = [now_bishkek().strftime("%d.%m.%Y %H:%M"), channel, name, phone, stage, (message or "")[:1000]]
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=f"'{LEADS_SHEET_TITLE}'!A:F",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [row]},
        ).execute()
    except HttpError as e:
        raise _http_error(e)


# ---------------------------------------------------------------- каталог

def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("ё", "е").split())


def map_columns(header: List[Any]) -> Dict[str, int]:
    """Номера колонок по названиям из первой строки. Без «Название» импорт невозможен."""
    found: Dict[str, int] = {}
    for idx, cell in enumerate(header):
        name = _norm(cell)
        if not name:
            continue
        for field, aliases in COLUMN_ALIASES.items():
            if field in found:
                continue
            if any(name == a or name.startswith(a + " ") or name.startswith(a + ",") or name.startswith(a + "(") for a in aliases):
                found[field] = idx
                break
    return found


def parse_price(value: Any) -> Optional[float]:
    text = str(value or "").replace(" ", " ")
    digits = re.sub(r"[^\d,.]", "", text).replace(",", ".")
    if digits.count(".") > 1:  # 1.500.000 → тысячи
        digits = digits.replace(".", "")
    try:
        price = float(digits) if digits else None
    except ValueError:
        return None
    return price if price is not None and price >= 0 else None


def parse_in_stock(value: Any) -> bool:
    text = _norm(value)
    if not text:
        return True
    if text in ("нет", "no", "0", "false", "закончился", "под заказ", "жок"):
        return False
    try:
        return float(text.replace(",", ".")) > 0
    except ValueError:
        return True


def rows_to_products(values: List[List[Any]]) -> List[Dict[str, Any]]:
    if not values:
        raise SheetError("Таблица пустая.")
    cols = map_columns(values[0])
    if "title" not in cols:
        raise SheetError("В первой строке нет колонки «Название». Подпишите колонки: Название, Цена, Описание, Наличие.")
    get = lambda row, field: row[cols[field]] if field in cols and cols[field] < len(row) else ""  # noqa: E731
    products, seen = [], set()
    for row in values[1:CATALOG_MAX_ROWS + 1]:
        title = " ".join(str(get(row, "title")).split())[:500]
        if not title or _norm(title) in seen:
            continue
        seen.add(_norm(title))
        products.append({
            "title": title,
            "price": parse_price(get(row, "price")),
            "body": str(get(row, "body") or "").strip(),
            "in_stock": parse_in_stock(get(row, "in_stock")),
            "keywords": str(get(row, "keywords") or "").strip() or None,
        })
    if not products:
        raise SheetError("В таблице нет строк с названием товара или услуги.")
    return products


def read_catalog(sheet_id: str) -> List[Dict[str, Any]]:
    from googleapiclient.errors import HttpError
    service = _service()
    try:
        meta = service.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
        titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
        first = next((t for t in titles if t != LEADS_SHEET_TITLE), None)
        if not first:
            raise SheetError("В таблице нет листа с прайсом.")
        values = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{first}'!A1:Z{CATALOG_MAX_ROWS + 1}",
            valueRenderOption="FORMATTED_VALUE",
        ).execute().get("values", [])
    except HttpError as e:
        raise _http_error(e)
    return rows_to_products(values)


def import_catalog(db, agent, user_id, products: List[Dict[str, Any]]) -> Dict[str, int]:
    """Создать или обновить позиции каталога из таблицы. Ручные позиции не трогаются."""
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E

    existing = {
        _norm(row.title): row
        for row in db.query(E).filter(E.agent_config_id == agent.id, E.kind == "product").all()
        if isinstance(row.attributes, dict) and row.attributes.get("_source") == CATALOG_SOURCE
    }
    created = updated = 0
    for p in products:
        row = existing.pop(_norm(p["title"]), None)
        if row is None:
            row = E(user_id=user_id, agent_config_id=agent.id, kind="product", currency="KGS",
                    in_prompt=False, is_active=True, attributes={"_source": CATALOG_SOURCE})
            db.add(row)
            created += 1
        else:
            updated += 1
        row.title = p["title"]
        row.body = p["body"]
        row.price = p["price"]
        row.in_stock = p["in_stock"]
        row.keywords = p["keywords"]
        row.char_count = len(p["body"])
        row.is_active = True
        row.vector_status = "pending"
    # Позиции, которых больше нет в таблице, выключаем (не удаляем — вдруг вернутся)
    hidden = 0
    for row in existing.values():
        if row.is_active:
            row.is_active = False
            hidden += 1
    db.commit()
    return {"created": created, "updated": updated, "hidden": hidden}
