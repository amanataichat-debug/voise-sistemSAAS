"""Каталог на тысячи позиций (автозапчасти, 10.11.2026): импорт из таблицы и поиск find_products.

Часть 1 без БД: колонки «Артикул», «Марка», «Модель» не выбрасываются, одинаковые названия с разными артикулами —
разные позиции, артикул в любом написании, марка кириллицей и латиницей.
Часть 2 — на настоящем PostgreSQL при CATALOG_TEST_DATABASE_URL: 3000 позиций, поиск находит нужную запчасть,
даже когда под широкое слово («фильтр», «тойота») подходят тысячи строк.
"""
import asyncio
import logging
import os
import random
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


# ============================================================ часть 1: без БД
rows = [["Наименование", "Цена", "Артикул", "Марка", "Модель", "Год", "Фото", "Наличие"],
        ["Колодки тормозные передние", "2 800", "04465-33450", "Toyota", "Camry 70", "2018-2023", "https://x.kg/1.jpg", "есть"],
        ["Колодки тормозные передние", "2 400", "04465-12610", "Toyota", "Corolla", "2013-2018", "", "есть"],
        ["Колодки тормозные передние", "2 800", "04465-33450", "Toyota", "Camry 70", "2018-2023", "", "есть"],
        ["Фильтр масляный", "450", "90915-YZZE1", "Toyota", "Camry", "", "не ссылка", "0"]]
prods = sl.rows_to_products(rows)
check("импорт: одинаковые названия с разными артикулами — разные позиции, точный повтор — один",
      len(prods) == 3 and [p["attributes"]["Артикул"] for p in prods] == ["04465-33450", "04465-12610", "90915-YZZE1"], prods)
check("импорт: Артикул, Марка, Модель, Год — характеристики; Фото — ссылка; не-ссылка отброшена",
      prods[0]["attributes"] == {"Артикул": "04465-33450", "Марка": "Toyota", "Модель": "Camry 70", "Год": "2018-2023"}
      and prods[0]["photo_url"] == "https://x.kg/1.jpg" and prods[2]["photo_url"] is None and prods[2]["in_stock"] is False)
check("импорт: прежняя таблица без доп. колонок — как раньше",
      [p["title"] for p in sl.rows_to_products([["Название", "Цена"], ["Стрижка", "800"], ["стрижка", "900"]])] == ["Стрижка"])
check("предел строк каталога — 10 000", sl.CATALOG_MAX_ROWS == 10000)

check("артикул в любом написании", kb.article_tokens("90915 yzze1")[0] == "90915yzze1" and kb.article_tokens("90915-YZZE1") == ["90915yzze1"]
      and kb.compact("90915 YZZE1") == kb.compact("90915-yzze1") == "90915yzze1")
check("артикул: обычные слова и короткие числа — не артикул", kb.article_tokens("колодки на камри 70") == [])
_t = kb.query_terms("колодки камри")
check("марка/модель кириллицей = латиницей (камри ↔ camry, тойота ↔ toyota)",
      any("camr" in t for t in _t) and any("toyo" in t for t in kb.query_terms("тойота")))
row = types.SimpleNamespace(title="Колодки тормозные передние", keywords=None, body="", kind="product",
                            attributes={"_source": "google_sheet", "Артикул": "04465-33450", "Модель": "Camry 70"})
_rx = kb._term_regexes(kb.query_terms("колодки камри"))
check("ранжирование видит характеристики (модель в attributes)", kb._rank_row(row, _rx, "колодки камри")[1] == 2)
check("точный артикул — сильное совпадение", kb._rank_row(row, kb._term_regexes(kb.query_terms("0446533450")), "0446533450",
                                                          kb.article_tokens("0446533450"))[2] is True)
_out = kb.entry_for_agent(types.SimpleNamespace(kind="product", title="Колодки", body="", price=2800.0, currency="KGS",
                                                in_stock=True, media_file_ids=None,
                                                attributes={"_source": "google_sheet", "_key": "k", "Модель": "Camry 70",
                                                            "_photo_url": "https://x.kg/1.jpg"}))
check("агенту — характеристики без служебных ключей и ссылка на фото",
      _out["attributes"] == {"Модель": "Camry 70"} and _out["photo_url"] == "https://x.kg/1.jpg" and _out["price"] == "2 800 сом")

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
    owner = User(email="parts@x.kg", password_hash="x")
    db.add(owner); db.flush()
    ag = AgentConfig(user_id=owner.id, name="Азамат", is_active=True)
    db.add(ag); db.flush()
    AG = ag.id

    random.seed(7)
    cars = [("Toyota", "Camry 70"), ("Toyota", "Camry 50"), ("Toyota", "Corolla"), ("Toyota", "RAV4"), ("Toyota", "Prius"),
            ("Lexus", "RX"), ("Honda", "Fit"), ("Honda", "CR-V"), ("Hyundai", "Sonata"), ("Hyundai", "Elantra"),
            ("Kia", "Rio"), ("Nissan", "X-Trail"), ("Mazda", "6"), ("Mercedes", "W212"), ("Daewoo", "Nexia")]
    parts = ["Фильтр масляный", "Фильтр воздушный", "Фильтр салонный", "Колодки тормозные передние",
             "Колодки тормозные задние", "Амортизатор передний", "Свеча зажигания", "Ремень ГРМ", "Помпа", "Рычаг передний"]
    rows = []
    n = 0
    for _ in range(20):
        for brand, model in cars:
            for part in parts:
                n += 1
                art = f"{random.randint(10000, 99999)}-{random.choice('ABCDEFGHJK')}{random.randint(100, 999)}"
                rows.append(dict(id=uuid.uuid4(), user_id=owner.id, agent_config_id=AG, kind="product", title=part, body="",
                                 price=float(random.randint(3, 90) * 100), currency="KGS", in_stock=True, in_prompt=False,
                                 is_active=True, position=0, hit_count=0, chunk_count=0, char_count=0,
                                 attributes={"_source": "google_sheet", "Артикул": art, "Марка": brand, "Модель": model}))
    # искомые позиции — где-то в середине
    target_pads = dict(id=uuid.uuid4(), user_id=owner.id, agent_config_id=AG, kind="product", title="Колодки тормозные передние",
                       body="Оригинал", price=2800.0, currency="KGS", in_stock=True, in_prompt=False, is_active=True, position=0,
                       hit_count=0, chunk_count=0, char_count=0,
                       attributes={"_source": "google_sheet", "Артикул": "04465-33450", "Марка": "Toyota", "Модель": "Camry 70"},
                       media_file_ids=[str(uuid.uuid4())])
    target_oil = dict(id=uuid.uuid4(), user_id=owner.id, agent_config_id=AG, kind="product", title="Фильтр масляный оригинал",
                      body="", price=450.0, currency="KGS", in_stock=True, in_prompt=False, is_active=True, position=0,
                      hit_count=0, chunk_count=0, char_count=0,
                      attributes={"_source": "google_sheet", "Артикул": "90915-YZZE1", "Марка": "Toyota", "Модель": "Corolla"})
    rows.insert(1500, target_pads)
    rows.insert(2100, target_oil)
    db.bulk_insert_mappings(E, rows)
    db.commit()
    total = db.query(E).filter(E.agent_config_id == AG).count()
    check("каталог: 3000+ позиций загружено", total >= 3000, total)

    def ids(res):
        return [r.id for r in res]

    import time as _time
    t0 = _time.time()
    r = kb.search_products(db, AG, "90915YZZE1")
    check("артикул без дефиса находит позицию первой", r and r[0].id == target_oil["id"], [x.title for x in r])
    r = kb.search_products(db, AG, "90915 yzze1")
    check("артикул с пробелом и маленькими буквами — тоже", r and r[0].id == target_oil["id"])
    r = kb.search_products(db, AG, "04465-33450")
    check("артикул с дефисом — колодки Camry 70", r and r[0].id == target_pads["id"])
    r = kb.search_products(db, AG, "колодки передние камри 70", limit=10)
    check("«колодки передние камри 70» (кириллицей, модель в характеристиках) — только Camry 70",
          r and all((x.attributes or {}).get("Модель") == "Camry 70" and "передние" in x.title for x in r), [x.attributes.get("Модель") for x in r])
    r = kb.search_products(db, AG, "амортизатор хендай соната", limit=10)
    check("«амортизатор хендай соната» — Hyundai Sonata", r and all((x.attributes or {}).get("Модель") == "Sonata"
                                                                    and "Амортизатор" in x.title for x in r))
    # в каталоге 20 одинаковых масляных фильтров Corolla + «оригинал»: все 21 — наверху, ниже — менее подходящие
    r = kb.search_products(db, AG, "фильтр масляный тойота королла", limit=25)
    check("«фильтр масляный тойота королла» среди 900 фильтров и 1000 Toyota — Corolla и оригинал в выдаче",
          r and all((x.attributes or {}).get("Модель") == "Corolla" and "масляный" in x.title for x in r[:21])
          and target_oil["id"] in ids(r[:21]),
          [(x.title, x.attributes.get("Модель")) for x in r][:4])
    r = kb.search_products(db, AG, "помпа нексия")
    check("«помпа нексия» — Daewoo Nexia", r and all((x.attributes or {}).get("Модель") == "Nexia" for x in r))
    r = kb.search_products(db, AG, "колодки", max_price=500)
    check("фильтр цены в SQL работает вместе с поиском", all(x.price <= 500 for x in r))
    took = _time.time() - t0
    check("поиск быстрый: 8 запросов на 3000 позиций за ~секунду", took < 3.0, f"{took:.2f} с")

    res = asyncio.run(kb.answer_products(db, AG, query="колодки камри 70 передние"))
    first = res["results"][0] if res.get("results") else {}
    check("find_products агенту: характеристики без служебных ключей, id фото для отправки",
          first.get("attributes", {}).get("Модель") == "Camry 70" and "_source" not in first.get("attributes", {})
          and first.get("media_file_ids"), first)

    # импорт из таблицы: повторный — без дублей; старые позиции (до «_key») сопоставляются по названию
    db.query(E).filter(E.agent_config_id == AG).delete()
    legacy = E(user_id=owner.id, agent_config_id=AG, kind="product", title="Фильтр масляный", body="", is_active=True,
               in_prompt=False, attributes={"_source": "google_sheet"})
    db.add(legacy); db.commit()
    st1 = sl.import_catalog(db, ag, owner.id, prods)
    st2 = sl.import_catalog(db, ag, owner.id, prods)
    rows_now = db.query(E).filter(E.agent_config_id == AG, E.is_active.is_(True)).all()
    check("импорт: старая позиция сопоставлена по названию, повтор без дублей",
          st1 == {"created": 2, "updated": 1, "hidden": 0} and st2 == {"created": 0, "updated": 3, "hidden": 0}
          and len(rows_now) == 3, (st1, st2, len(rows_now)))
    pads = [x for x in rows_now if x.attributes.get("Артикул") == "04465-33450"][0]
    check("импорт: характеристики и ссылка на фото сохранены", pads.attributes.get("Модель") == "Camry 70"
          and pads.attributes.get("_photo_url") == "https://x.kg/1.jpg" and pads.attributes.get("_key"))
    db.close()

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
