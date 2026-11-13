"""Каталог как у магазина, часть 2 (13.11.2026): остаток по размерам и цветам, массовые действия,
карточка товара клиенту, склад в Telegram владельцу, вопросы владельца о складе.

Часть 1 без БД: catalog_service (варианты, события склада, карточка, массовые действия, set_stock),
размер с остатком в поиске, ответ агенту.
Часть 2 — на настоящем PostgreSQL при CATALOG_TEST_DATABASE_URL: API (варианты, массовые действия,
пакетное создание), оповещение о складе, инструменты send_product_card / update_product_stock /
get_catalog_overview, правила тарифа Старт.
"""
import asyncio
import logging
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("CATALOG_TEST_DATABASE_URL")
os.environ.update(HOST_URL="http://x", DATABASE_URL=PG or "sqlite://", PRODUCTION="false", OPENAI_API_KEY="k",
                  KB_VECTOR_ENABLED="0")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401
from backend.services import catalog_service as cs
from backend.services import agent_knowledge_service as kb

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def P(**kw):
    base = dict(id="11111111-1111-1111-1111-111111111111", kind="product", title="Шуба норка", body="", price=85000.0,
                currency="KGS", in_stock=True, attributes={}, media_file_ids=[], collection="Женская", sizes=None,
                stock_qty=None, old_price=None, variants=None, is_active=True, keywords=None, vector_status=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


# ============================================================ часть 1
v = cs.normalize_variants([{"size": " 46 ", "color": "чёрный", "qty": "2"}, {"size": "46", "color": "Чёрный", "qty": 5},
                           {"size": "48", "color": "", "qty": -3}, {"size": "", "color": ""}, "мусор"])
check("варианты: чистка, повтор без регистра, остаток ≥ 0", v == [{"size": "46", "color": "чёрный", "qty": 2},
                                                                 {"size": "48", "color": "", "qty": 0}], v)
r = P(variants=[{"size": "44", "color": "чёрный", "qty": 0}, {"size": "46", "color": "чёрный", "qty": 2},
                {"size": "46", "color": "коричневый", "qty": 1}])
cs.apply_variants(r)
check("варианты → остаток, наличие, размеры", r.stock_qty == 3 and r.in_stock and r.sizes == ["44", "46"], (r.stock_qty, r.sizes))
check("размеры в наличии — без раскупленных", cs.sizes_in_stock(r) == ["46"], cs.sizes_in_stock(r))
check("цвета в наличии", cs.colors_in_stock(r) == ["чёрный", "коричневый"])
check("строки остатка для агента", cs.variant_lines(r)[0] == "44, чёрный: нет" and "46, чёрный: 2 шт." in cs.variant_lines(r))
check("поиск: размер 46 есть, 44 раскуплен", kb._has_size(r, "46") and not kb._has_size(r, "44"))
r0 = P(variants=[{"size": "46", "color": "", "qty": 0}])
cs.apply_variants(r0)
check("все варианты по нулям → нет в наличии", r0.in_stock is False and r0.stock_qty == 0)

out = kb.entry_for_agent(r)
check("агенту: product_id, размеры в наличии, остаток по вариантам",
      out.get("product_id") and out.get("sizes_in_stock") == ["46"] and len(out.get("stock_by_variant") or []) == 3, out)

# события склада
row = P(stock_qty=5, in_stock=True)
before = cs.stock_state(row)
row.stock_qty, row.in_stock = 0, False
check("событие: закончился", cs.stock_event(before, row) == "out")
row2 = P(stock_qty=5); b2 = cs.stock_state(row2); row2.stock_qty = 2
check("событие: стало мало (через порог)", cs.stock_event(b2, row2) == "low")
row3 = P(stock_qty=3); b3 = cs.stock_state(row3); row3.stock_qty = 2
check("событие: 3 → 2 — без повторного «мало»", cs.stock_event(b3, row3) is None)
row4 = P(in_stock=False, stock_qty=0); b4 = cs.stock_state(row4); row4.in_stock, row4.stock_qty = True, 4
check("событие: снова в наличии", cs.stock_event(b4, row4) == "back")
row5 = P(stock_qty=5, is_active=False); b5 = cs.stock_state(row5); row5.stock_qty, row5.in_stock = 0, False
check("событие: выключенная позиция — без шума", cs.stock_event(b5, row5) is None)
msg = cs.stock_message([(row, "out"), (row2, "low")])
check("сообщение склада: обе строки", "Закончился" in msg and "Осталось мало" in msg and "2 шт." in msg, msg)

# карточка товара
card = cs.product_card_text(P(title="Шуба норка длинная", price=85000.0, old_price=98000.0, body="Натуральный мех. " * 40,
                               variants=r.variants, sizes=r.sizes, stock_qty=3, attributes={"Цвет": "чёрный", "_key": "x", "Артикул": "N1"}))
lines = card.split("\n")
check("карточка: название первой строкой", lines[0] == "Шуба норка длинная", card)
check("карточка: цена и скидка", "85 000 сом вместо 98 000 сом, скидка 13%" in card, card)
check("карточка: размеры и цвета в наличии", "Размеры в наличии: 46" in card and "Цвета: чёрный, коричневый" in card, card)
check("карточка: осталось мало", "Осталось 3 шт." in card)
check("карточка: служебное и артикул скрыты", "_key" not in card and "N1" not in card and "Цвет: чёрный" in card)
check("карточка: описание обрезано по слову", card.endswith("…") and len(card) < 650, len(card))
check("карточка: без тире и звёздочек", "—" not in card and "*" not in card)
gone = cs.product_card_text(P(title="Берет", price=8000.0, in_stock=False, stock_qty=0))
check("карточка: нет в наличии", "Сейчас нет в наличии" in gone, gone)

# массовые действия
rows = [P(title="A", price=10000.0), P(title="B", price=999.0), P(title="C", price=None)]
n, _ = cs.apply_bulk(rows, "price_percent", 10)
check("массово: +10% с округлением, без цены пропущено", n == 2 and rows[0].price == 11000.0 and rows[1].price == 1100.0, [x.price for x in rows])
rows = [P(title="A", price=10000.0)]
cs.apply_bulk(rows, "discount", 20)
check("массово: скидка 20% — старая цена сохранена", rows[0].old_price == 10000.0 and rows[0].price == 8000.0)
cs.apply_bulk(rows, "discount", 30)
check("массово: новая скидка считается от старой цены", rows[0].old_price == 10000.0 and rows[0].price == 7000.0)
cs.apply_bulk(rows, "discount_off")
check("массово: убрать скидку", rows[0].price == 10000.0 and rows[0].old_price is None)
rows = [P(title="A", stock_qty=4), P(title="B", variants=[{"size": "46", "color": "", "qty": 2}])]
n, ev = cs.apply_bulk(rows, "out_of_stock")
check("массово: нет в наличии + события", n == 2 and all(not x.in_stock for x in rows) and rows[1].variants[0]["qty"] == 0
      and len(ev) == 2, ev)
cs.apply_bulk(rows, "collection", "  Распродажа ")
check("массово: в коллекцию", rows[0].collection == "Распродажа")

# остаток по слову владельца
r6 = P(variants=[{"size": "58", "color": "", "qty": 4}])
cs.apply_variants(r6)
done = cs.set_stock(r6, 2, size="58")
check("остаток по размеру", r6.stock_qty == 2 and "58" in done, done)
cs.set_stock(r6, 3, size="60")
check("новый размер заводится", r6.stock_qty == 5 and cs.sizes_in_stock(r6) == ["58", "60"])
try:
    cs.set_stock(r6, 1)
    check("общий остаток при вариантах — просьба уточнить размер", False)
except ValueError:
    check("общий остаток при вариантах — просьба уточнить размер", True)
r7 = P(stock_qty=None)
cs.set_stock(r7, 0)
check("общий остаток 0 → нет в наличии", r7.in_stock is False and r7.stock_qty == 0)

ov_text = cs.overview_text({"total": 10, "in_stock": 8, "units": 40, "collections": {"Женская": {"items": 5, "in_stock": 4, "units": 20},
                                                                                       "Мужская": {"items": 5, "in_stock": 4, "units": 20}},
                            "out_of_stock": [{"title": "Берет"}], "out_of_stock_count": 1,
                            "low_stock": [{"title": "Шуба", "stock_left": 2}], "low_stock_count": 1,
                            "no_photo_count": 3, "no_price_count": 0})
check("сводка склада текстом", "10 позиций" in ov_text and "Берет" in ov_text and "Шуба (2 шт.)" in ov_text
      and "без фото: 3" in ov_text and "Женская: 4/5" in ov_text, ov_text)

from backend.services import agent_mode  # noqa: E402
check("Старт: карточка товара — отправка клиенту", "send_product_card" in agent_mode.SEND_TOOLS)
check("Старт: владелец может менять остаток из чата", agent_mode.OWNER_CHAT_READONLY_ALLOWED.__contains__("update_product_stock"))
from backend.services import agent_tools as at  # noqa: E402
names_chat = [t["name"] for t in at.AGENT_CHAT_TOOLS]
names_post = [t["name"] for t in at.AGENT_POSTCALL_TOOLS]
check("инструменты: карточка в чате и в разборе", "send_product_card" in names_chat and "send_product_card" in names_post)
check("инструменты: склад только в чате владельца", "update_product_stock" in names_chat and "update_product_stock" not in names_post
      and "get_catalog_overview" in names_chat)

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
    from backend.models.agent_contact import AgentContact
    from backend.models.agent_file import AgentFile
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E

    with engine.begin() as conn:
        conn.execute(_text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    db = SessionLocal()
    owner = User(email="shop2@x.kg", password_hash="x")
    db.add(owner); db.flush()
    ag = AgentConfig(user_id=owner.id, name="Норка", is_active=True)
    db.add(ag); db.flush()

    from fastapi.testclient import TestClient
    from app import app
    from backend.core.dependencies import get_current_user
    from backend.db.session import get_db
    from backend.api import agent as api

    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_db] = lambda: db
    api.kb_vectors.schedule_sync = lambda *a, **k: None
    api.kb_vectors.schedule_delete = lambda *a, **k: None
    notified = []
    cs.schedule_stock_notify = lambda agent_id, events: notified.append(cs.stock_message(events))
    cl = TestClient(app)

    rr = cl.post("/api/agent/knowledge", json={"kind": "product", "title": "Шапка кубанка норка", "collection": "Мужская",
                                               "price": 11000, "variants": [{"size": "58", "qty": 2}, {"size": "60", "qty": 0}]})
    e = rr.json().get("entry", {})
    check("API: позиция с вариантами", rr.status_code == 200 and e.get("stock_qty") == 2 and e.get("in_stock") is True
          and e.get("sizes") == ["58", "60"] and len(e.get("variants")) == 2, rr.text[:300])
    rr = cl.put(f"/api/agent/knowledge/{e['id']}", json={"variants": [{"size": "58", "qty": 0}, {"size": "60", "qty": 0}]})
    check("API: раскупили всё → нет в наличии", rr.json()["entry"]["in_stock"] is False and rr.json()["entry"]["stock_qty"] == 0)
    check("API: владельцу ушло «закончился»", notified and "Закончился" in notified[-1] and "кубанка" in notified[-1], notified)
    rr = cl.put(f"/api/agent/knowledge/{e['id']}", json={"variants": [{"size": "58", "qty": 3}]})
    check("API: снова в наличии + оповещение", rr.json()["entry"]["in_stock"] is True and "Снова в наличии" in notified[-1], notified[-1:])

    rr = cl.post("/api/agent/knowledge/products/batch", json={"items": [
        {"title": "Шуба норка длинная", "collection": "Женская", "price": 85000, "stock_qty": 4, "sizes": ["44, 46"]},
        {"title": "Берет норка", "collection": "Женская", "price": 8000},
        {"title": "  "},
    ]})
    check("API: пакетное создание, пустые строки пропущены", rr.status_code == 200 and rr.json()["created"] == 2, rr.text[:200])
    ids = rr.json()["ids"]
    rr = cl.post("/api/agent/knowledge/bulk", json={"ids": ids, "action": "discount", "value": 10})
    shuba = db.query(E).filter(E.title == "Шуба норка длинная").first(); db.refresh(shuba)
    check("API: массовая скидка 10%", rr.json()["changed"] == 2 and shuba.price == 76500 and shuba.old_price == 85000, (shuba.price, shuba.old_price))
    rr = cl.post("/api/agent/knowledge/bulk", json={"ids": ids, "action": "price_percent", "value": "abc"})
    check("API: неверное число → 400", rr.status_code == 400)
    rr = cl.post("/api/agent/knowledge/bulk", json={"ids": ids, "action": "hack"})
    check("API: неизвестное действие → 400", rr.status_code == 400)
    n0 = len(notified)
    rr = cl.post("/api/agent/knowledge/bulk", json={"ids": ids, "action": "out_of_stock"})
    check("API: массово нет в наличии → одно сообщение владельцу", len(notified) == n0 + 1 and notified[-1].count("Закончился") == 2, notified[-1:])
    other = User(email="evil@x.kg", password_hash="x"); db.add(other); db.flush()
    oag = AgentConfig(user_id=other.id, name="Чужой", is_active=True); db.add(oag); db.flush()
    foreign = E(user_id=other.id, agent_config_id=oag.id, kind="product", title="Чужой товар", body="", price=1.0,
                currency="KGS", in_stock=True, in_prompt=False, is_active=True)
    db.add(foreign); db.commit()
    rr = cl.post("/api/agent/knowledge/bulk", json={"ids": [str(foreign.id)], "action": "delete"})
    check("API: чужие позиции не трогаются", rr.status_code == 404 and db.query(E).filter(E.id == foreign.id).count() == 1)
    rr = cl.post("/api/agent/knowledge/bulk", json={"ids": [ids[1]], "action": "delete"})
    check("API: массовое удаление", rr.json()["changed"] == 1 and db.query(E).filter(E.title == "Берет норка").count() == 0)
    app.dependency_overrides.clear()

    # Инструменты агента
    contact = AgentContact(agent_config_id=ag.id, user_id=owner.id, phone="+996700000001", name="Айжан", status="new")
    db.add(contact); db.flush()
    photo = AgentFile(user_id=owner.id, agent_config_id=ag.id, source="library", kind="image", filename="kub.jpg",
                      mime_type="image/jpeg", storage_key="k1", size_bytes=10, status="done")
    video = AgentFile(user_id=owner.id, agent_config_id=ag.id, source="library", kind="video", filename="kub.mp4",
                      mime_type="video/mp4", storage_key="k2", size_bytes=10, status="done")
    db.add_all([photo, video]); db.flush()
    kub = db.query(E).filter(E.title == "Шапка кубанка норка").first()
    kub.media_file_ids = [str(video.id), str(photo.id)]
    db.commit()

    sent = []

    async def fake_file(args, user_id, agent_config, db_, **kw):
        sent.append(("file", args.get("file_id"), args.get("caption")))
        return {"ok": True}

    async def fake_text(args, user_id, agent_config, db_, **kw):
        sent.append(("text", None, args.get("text")))
        return {"ok": True}

    at.fn_whatsapp_send_file = fake_file
    at.fn_whatsapp_send_message = fake_text
    res = asyncio.run(at.fn_send_product_card({"agent_contact_id": str(contact.id), "product_id": str(kub.id),
                                               "channel": "whatsapp", "more_photos": True}, owner.id, ag.id, db))
    check("карточка: ушла фото с подписью, потом остальное без подписи",
          res.get("ok") and sent[0] == ("file", str(photo.id), res["card"]) and sent[1][0] == "file" and sent[1][2] is None, sent)
    check("карточка: подпись — цена и размеры в наличии", "Цена: 11 000 сом" in res["card"] and "Размеры в наличии: 58" in res["card"], res["card"])
    sent.clear()
    shuba = db.query(E).filter(E.title == "Шуба норка длинная").first()
    res = asyncio.run(at.fn_send_product_card({"agent_contact_id": str(contact.id), "product_id": str(shuba.id),
                                               "channel": "whatsapp"}, owner.id, ag.id, db))
    check("карточка без фото — текстом", res.get("ok") and sent and sent[0][0] == "text", sent)
    res = asyncio.run(at.fn_send_product_card({"agent_contact_id": str(contact.id), "product_id": str(foreign.id),
                                               "channel": "whatsapp"}, owner.id, ag.id, db))
    check("карточка: чужой товар не отправляется", res.get("ok") is False, res)

    res = asyncio.run(at.fn_update_product_stock({"query": "кубанка", "qty": 1, "size": "58"}, ag.id, db))
    db.refresh(kub)
    # до правки было 3 шт. — уже «мало», повторного оповещения нет
    check("владелец: «кубанка 58 — 1 шт.»", res.get("ok") and kub.stock_qty == 1 and res.get("event") is None, res)
    res = asyncio.run(at.fn_update_product_stock({"query": "норка", "qty": 3}, ag.id, db))
    check("владелец: неоднозначно → кандидаты, ничего не менялось", res.get("ok") is False and len(res.get("candidates") or []) >= 2, res)
    res = asyncio.run(at.fn_update_product_stock({"query": "Шуба норка длинная", "available": True}, ag.id, db))
    db.refresh(shuba)
    check("владелец: «шуба снова есть»", res.get("ok") and shuba.in_stock is True, res)
    ov = asyncio.run(at.fn_get_catalog_overview({}, ag.id, db))
    check("владелец: сводка склада", ov["total"] == 2 and ov["low_stock_count"] == 1 and "Каталог" in ov["summary"], ov.get("summary"))

    rows = kb.search_products(db, ag.id, query="шапка", size="60")
    check("поиск: размер 60 раскуплен — не предлагаем", rows == [], [r.title for r in rows])
    rows = kb.search_products(db, ag.id, query="шапка", size="58")
    check("поиск: размер 58 есть", [r.title for r in rows] == ["Шапка кубанка норка"])
    db.close()

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
