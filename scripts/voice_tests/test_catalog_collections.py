"""Каталог как у магазина (13.11.2026): коллекции, размеры, остаток в штуках, старая цена.

Клиенты Старта (норка, детская одежда) не могли нормально заполнить каталог: всё одной кучей, без коллекций,
размеров и остатков. Теперь у позиции есть collection / sizes / stock_qty / old_price.

Часть 1 без БД: нормализация полей API, размер из характеристик, ответ агенту, импорт колонок из таблицы,
прайс в промпте по коллекциям.
Часть 2 — на настоящем PostgreSQL при CATALOG_TEST_DATABASE_URL: поиск с коллекцией и размером, «только в
наличии», переименование коллекции через API.
"""
import asyncio
import logging
import os
import sys
import types
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("CATALOG_TEST_DATABASE_URL")
os.environ.update(HOST_URL="http://x", DATABASE_URL=PG or "sqlite://", PRODUCTION="false", OPENAI_API_KEY="k",
                  KB_VECTOR_ENABLED="0")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401
from backend.services import agent_knowledge_service as kb
from backend.services import sheets_leads as sl

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ============================================================ часть 1
from backend.api import agent as api  # noqa: E402

check("коллекция: пробелы схлопнуты", api._kb_collection("  Женская   коллекция ") == "Женская коллекция")
check("коллекция: пусто → None", api._kb_collection("   ") is None)
check("размеры: из строки, без повторов", api._kb_sizes(["42, 44 / 46", "44", "s;M"]) == ["42", "44", "46", "s", "M"],
      api._kb_sizes(["42, 44 / 46", "44", "s;M"]))
check("размеры: пусто → None", api._kb_sizes([]) is None and api._kb_sizes(["  "]) is None)
check("остаток: число, не меньше нуля", api._kb_stock("7") == 7 and api._kb_stock(-3) == 0 and api._kb_stock("") is None)

R = types.SimpleNamespace
row = R(kind="product", title="Шапка-ушанка норка", body="Тёплая", price=9500.0, currency="KGS", in_stock=True,
        attributes={"Цвет": "чёрный", "_key": "x"}, media_file_ids=["f1"], collection="Мужская",
        sizes=["56", "58"], stock_qty=2, old_price=12000.0, keywords=None)
out = kb.entry_for_agent(row)
check("агенту: коллекция, размеры, остаток, старая цена", out.get("collection") == "Мужская" and out.get("sizes") == ["56", "58"]
      and out.get("stock_left") == 2 and "12" in str(out.get("old_price")), out)
check("агенту: служебные ключи скрыты", "_key" not in (out.get("attributes") or {}))
row2 = R(**{**row.__dict__, "old_price": 5000.0})
check("агенту: старая цена ниже текущей — не скидка", "old_price" not in kb.entry_for_agent(row2))

check("размер: из списка", kb._has_size(row, "58") and not kb._has_size(row, "60"))
check("размер: из характеристики «Размер: 42–48»",
      kb._has_size(R(sizes=None, attributes={"Размер": "42-48"}), "46")
      and not kb._has_size(R(sizes=None, attributes={"Размер": "42-48"}), "50"))
check("размер: латиница без регистра", kb._has_size(R(sizes=["XL", "M"], attributes={}), "xl"))
check("поиск видит коллекцию и размеры", "мужская" in kb.catalog_tags_text(row).lower()
      and "58" in kb.catalog_tags_text(row))

# Импорт из таблицы
vals = [["Название", "Коллекция", "Цена", "Цена до скидки", "Размеры", "Количество", "Цвет"],
        ["Пуховик детский", "Детская / Зима", "4 500", "5 900", "110, 116; 122", "0", "синий"],
        ["Шапка норка", "Мужская", "9500", "", "56/58", "3", "чёрный"]]
prods = sl.rows_to_products(vals)
p1, p2 = prods
check("таблица: коллекция", p1["collection"] == "Детская / Зима" and p2["collection"] == "Мужская", [p1["collection"], p2["collection"]])
check("таблица: цена не спутана со «ценой до скидки»", p1["price"] == 4500 and p1["old_price"] == 5900, [p1["price"], p1["old_price"]])
check("таблица: размеры списком", p1["sizes"] == ["110", "116", "122"] and p2["sizes"] == ["56", "58"], [p1["sizes"], p2["sizes"]])
check("таблица: количество", p1["stock_qty"] == 0 and p2["stock_qty"] == 3)
check("таблица: прочие колонки — характеристики", p1["attributes"].get("Цвет") == "синий" and "Коллекция" not in p1["attributes"],
      p1["attributes"])

# Прайс в промпте по коллекциям
from backend.services import agent_voice_context as avc  # noqa: E402


class _Q:
    def __init__(self, rows): self.rows = rows
    def filter(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def all(self): return self.rows
    def count(self): return len(self.rows)


_rows = [R(title="Шапка норка", price=9500.0, currency="KGS", in_stock=True, body="", collection="Мужская", sizes=["56"]),
         R(title="Берет норка", price=8000.0, currency="KGS", in_stock=True, body="", collection="Женская", sizes=None),
         R(title="Шарф", price=1500.0, currency="KGS", in_stock=False, body="", collection=None, sizes=None)]
_db = types.SimpleNamespace(query=lambda *a: _Q(_rows))
block = avc.build_price_block(_db, "a1")
check("прайс: заголовки коллекций", "## Женская" in block and "## Мужская" in block and "## Без коллекции" in block, block)
check("прайс: размеры у позиции в наличии", "размеры 56" in block, block)
check("прайс: позиции под своей коллекцией",
      block.index("## Женская") < block.index("Берет") < block.index("## Мужская") < block.index("Шапка"), block)

# ============================================================ часть 2: PostgreSQL
if not PG:
    print("SKIP часть 2: CATALOG_TEST_DATABASE_URL не задан")
else:
    from sqlalchemy import text as _text
    from backend.db.session import SessionLocal, engine
    from backend.models.base import Base
    import backend.models  # noqa: F401
    from backend.models.user import User
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E

    with engine.begin() as conn:
        conn.execute(_text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    db = SessionLocal()
    owner = User(email="shop@x.kg", password_hash="x")
    db.add(owner); db.flush()
    ag = AgentConfig(user_id=owner.id, name="Норка", is_active=True)
    db.add(ag); db.flush()

    def add(title, collection, price, sizes=None, stock=None, in_stock=True):
        db.add(E(user_id=owner.id, agent_config_id=ag.id, kind="product", title=title, body="", price=price, currency="KGS",
                 in_stock=in_stock, in_prompt=False, is_active=True, collection=collection, sizes=sizes, stock_qty=stock))

    add("Шуба норка длинная", "Женская", 85000, ["44", "46", "48"], 2)
    add("Шуба норка короткая", "Женская", 60000, ["42", "44"], 0, in_stock=False)
    add("Шапка норка ушанка", "Мужская", 9500, ["56", "58"], 5)
    add("Шапка норка кубанка", "Мужская", 11000, ["58", "60"], 1)
    add("Берет норка", "Женская", 8000, None, None)
    add("Комбинезон детский", "Детская / Зима", 4500, ["86", "92", "98"], 10)
    db.commit()

    def titles(rows):
        return [r.title for r in rows]

    r = kb.search_products(db, ag.id, query="шуба", collection="женская", size="46")
    check("PG: шуба, женская, 46", titles(r) == ["Шуба норка длинная"], titles(r))
    r = kb.search_products(db, ag.id, query="шапка", collection="мужской", size="60")
    check("PG: шапка, мужская (другое окончание), 60", titles(r) == ["Шапка норка кубанка"], titles(r))
    r = kb.search_products(db, ag.id, query="", collection="Женская")
    check("PG: вся коллекция, в наличии выше", titles(r)[0] != "Шуба норка короткая" and len(r) == 3, titles(r))
    r = kb.search_products(db, ag.id, query="шуба", in_stock_only=True)
    check("PG: только в наличии", titles(r) == ["Шуба норка длинная"], titles(r))
    r = kb.search_products(db, ag.id, query="женская норка")
    check("PG: коллекция словом в запросе", set(titles(r)) >= {"Шуба норка длинная", "Берет норка"}, titles(r))
    r = kb.search_products(db, ag.id, query="комбинезон 92")
    check("PG: размер словом в запросе", titles(r)[:1] == ["Комбинезон детский"], titles(r))

    res = asyncio.run(kb.answer_products(db, ag.id, query="шапка", collection="детская"))
    check("PG: неверная коллекция → поиск без неё, не пусто", res["total"] >= 1
          and res["results"][0]["title"].startswith("Шапка"), res)
    res = asyncio.run(kb.answer_products(db, ag.id, query="шуба", size="46"))
    check("PG: ответ агенту — остаток и размеры", res["results"][0].get("stock_left") == 2
          and "46" in res["results"][0].get("sizes", []), res["results"][0])

    # Переименование коллекции через API
    from fastapi.testclient import TestClient
    from app import app
    from backend.core.dependencies import get_current_user
    from backend.db.session import get_db

    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_db] = lambda: db
    api.kb_vectors.schedule_sync = lambda *a, **k: None
    cl = TestClient(app)
    rr = cl.post("/api/agent/knowledge/collections/rename", json={"old": "Мужская", "new": "Для мужчин"})
    check("API: переименование", rr.status_code == 200 and rr.json()["changed"] == 2, rr.text[:200])
    left = db.query(E).filter(E.agent_config_id == ag.id, E.collection == "Для мужчин").count()
    check("API: обе позиции в новой коллекции", left == 2, left)
    rr = cl.post("/api/agent/knowledge", json={"kind": "product", "title": "Варежки", "collection": " Детская / Зима ",
                                               "sizes": ["S, M"], "stock_qty": 0, "price": 900, "old_price": 1200})
    e = rr.json().get("entry", {})
    check("API: новая позиция с полями", rr.status_code == 200 and e.get("collection") == "Детская / Зима"
          and e.get("sizes") == ["S", "M"] and e.get("stock_qty") == 0 and e.get("in_stock") is False
          and e.get("old_price") == 1200, e)
    rr = cl.put(f"/api/agent/knowledge/{e.get('id')}", json={"stock_qty": 4})
    check("API: остаток > 0 → снова в наличии", rr.json()["entry"]["in_stock"] is True, rr.text[:200])
    rr = cl.post("/api/agent/knowledge/collections/rename", json={"old": "Детская / Зима", "new": ""})
    check("API: удалить коллекцию — позиции остаются", rr.json()["changed"] == 2
          and db.query(E).filter(E.agent_config_id == ag.id).count() == 7, rr.text[:200])
    # Справочник коллекций: пустые коллекции живут, порядок владельца, товар их пополняет
    rr = cl.post("/api/agent/knowledge/collections/set", json={"collections": ["Женская", " Мужская ", "женская", "Детская", ""]})
    check("справочник: сохранён без повторов и пустых", rr.status_code == 200
          and rr.json()["collections"][:3] == ["Женская", "Мужская", "Детская"], rr.text[:300])
    lst = cl.get("/api/agent/knowledge").json()["collections"]
    check("справочник: пустая «Детская» видна в /knowledge", "Детская" in lst and lst.index("Женская") < lst.index("Детская"), lst)
    check("справочник: коллекции товаров дописаны в конец", "Для мужчин" in lst, lst)
    rr = cl.post("/api/agent/knowledge", json={"kind": "product", "title": "Шарф", "collection": "Осень"})
    lst = cl.get("/api/agent/knowledge").json()["collections"]
    check("справочник: новая коллекция из товара запомнилась", "Осень" in lst, lst)
    rr = cl.post("/api/agent/knowledge/collections/rename", json={"old": "Детская", "new": "Детям"})
    lst = rr.json().get("collections") or []
    check("справочник: переименование пустой коллекции на месте", "Детям" in lst and "Детская" not in lst
          and lst.index("Мужская") < lst.index("Детям"), lst)
    rr = cl.post("/api/agent/knowledge/collections/rename", json={"old": "Детям", "new": ""})
    check("справочник: удаление пустой коллекции", "Детям" not in (rr.json().get("collections") or []), rr.text[:200])
    rr = cl.put("/api/agent/knowledge/collections", json={"collections": []})
    check("справочник: старый PUT /knowledge/collections не ломает сервер", rr.status_code < 500, rr.status_code)
    app.dependency_overrides.clear()
    db.close()

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
