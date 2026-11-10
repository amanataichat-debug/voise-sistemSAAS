"""«Проверить базу знаний» (services/knowledge_health.py, 10.11.2026).

Часть 1 без БД: кабинеты подключили кнопку, адрес API на месте. Часть 2 — PostgreSQL при KB_HEALTH_TEST_DATABASE_URL:
факты Анри + файл без текста + каталог без фото + запись, которую по её вопросу не найти; проверка находит каждую
проблему и НИЧЕГО не пишет в базу (ни «пробелы», ни счётчики «пригодилось»).
"""
import asyncio
import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("KB_HEALTH_TEST_DATABASE_URL")
os.environ.update(HOST_URL="http://x", DATABASE_URL=PG or "sqlite://", PRODUCTION="false", OPENAI_API_KEY="k",
                  KB_VECTOR_ENABLED="0")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401
from backend.services import knowledge_health as kh

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ── часть 1 ──
check("совпадение записи — по заголовку без учёта регистра",
      kh._found({"results": [{"title": "Сколько стоит?"}]}, "сколько стоит?") and not kh._found({"results": []}, "x"))
api = open(os.path.join(ROOT, "backend/api/agent.py"), encoding="utf-8").read()
check("API: GET /knowledge/health, Старт и Profi, не чаще раза в 15 с",
      '@router.get("/knowledge/health")' in api and "KB_HEALTH_MIN_INTERVAL_SEC = 15.0" in api)
_js = open(os.path.join(ROOT, "backend/static/js/knowledge-health.js"), encoding="utf-8").read()
_bot = open(os.path.join(ROOT, "backend/static/start/bot.js"), encoding="utf-8").read()
_akb = open(os.path.join(ROOT, "backend/static/js/agent-knowledge.js"), encoding="utf-8").read()
check("окно проверки: итог, проблемы с подсказками, список проверенных поисков",
      "VoksyKnowledgeHealth" in _js and "/knowledge/health" in _js and "searches" in _js)
check("кнопка «Проверить базу знаний» у Старта и Profi",
      "VoksyKnowledgeHealth.open" in _bot and "VoksyKnowledgeHealth.open" in _akb
      and "knowledge-health.js" in open(os.path.join(ROOT, "backend/static/start-bot.html"), encoding="utf-8").read()
      and "knowledge-health.js" in open(os.path.join(ROOT, "backend/static/agent-knowledge.html"), encoding="utf-8").read())

# ── часть 2: PostgreSQL ──
if not PG:
    print("SKIP часть 2: KB_HEALTH_TEST_DATABASE_URL не задан")
else:
    from sqlalchemy import text as _text
    from backend.db.session import SessionLocal, engine
    from backend.models.base import Base
    import backend.models  # noqa: F401
    from backend.models.user import User
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E, AgentKnowledgeGap

    with engine.begin() as conn:
        conn.execute(_text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    db = SessionLocal()
    owner = User(email="kbh@x.kg", password_hash="x")
    db.add(owner); db.flush()
    ag = AgentConfig(user_id=owner.id, name="Анри", is_active=True, voice_additional_instructions="Ты — <имя>, менеджер.")
    db.add(ag); db.flush()
    facts = json.load(open(os.path.join(ROOT, "docs/anri_knowledge.json"), encoding="utf-8"))["entries"]
    for i, f in enumerate(facts):
        db.add(E(user_id=owner.id, agent_config_id=ag.id, kind="qa", title=f["title"], body=f["body"],
                 keywords=f.get("keywords"), in_prompt=True, is_active=True, position=i))
    # запись, которую по её вопросу не найти: заголовок из «стоп-слов»
    db.add(E(user_id=owner.id, agent_config_id=ag.id, kind="qa", title="А как?", body="Вот так.", in_prompt=True,
             is_active=True, position=-1))
    db.add(E(user_id=owner.id, agent_config_id=ag.id, kind="document", title="Договор-скан.pdf", body="", chunk_count=0,
             in_prompt=False, is_active=True))
    db.add(E(user_id=owner.id, agent_config_id=ag.id, kind="product", title="Колодки тормозные передние Camry 70",
             body="", price=2800.0, currency="KGS", in_prompt=False, is_active=True))
    db.add(E(user_id=owner.id, agent_config_id=ag.id, kind="product", title="Фильтр масляный Corolla", body="",
             price=None, in_prompt=False, is_active=True))
    db.commit()
    AG = ag.id
    hits_before = sum(r.hit_count or 0 for r in db.query(E).all())
    gaps_before = db.query(AgentKnowledgeGap).count()

    rep = asyncio.run(kh.run_health(db, db.get(AgentConfig, AG)))
    by_area = {}
    for c in rep["checks"]:
        by_area.setdefault(c["area"], []).append(c)
    text = json.dumps(rep, ensure_ascii=False)
    check("итог: есть провалы (файл без текста, запись не находится)", rep["status"] == "fail" and not rep["ok"])
    check("база: посчитаны вопросы, каталог, файлы", rep["counts"] == {"qa": len(facts) + 1, "section": 0, "product": 2,
                                                                      "document": 1}, rep["counts"])
    check("файл без текста найден", any(c["level"] == "fail" and "Договор-скан.pdf" in c.get("items", [])
                                        for c in by_area.get("Файлы", [])))
    check("каталог: позиция без цены и позиции без фото найдены",
          any("без цены" in c["text"] and "Фильтр масляный Corolla" in c.get("items", []) for c in by_area.get("Каталог", []))
          and any("фото" in c["text"] for c in by_area.get("Каталог", [])))
    missed = [s for s in rep["searches"] if not s["found"]]
    check("поиск: все факты Анри находятся по своему вопросу, «А как?» — нет",
          [s["q"] for s in missed] == ["А как?"] and len(rep["searches"]) == len(facts) + 1 + 2,
          ([s["q"] for s in missed], len(rep["searches"])))
    check("поиск: проверен тем же путём, что звонок (быстрый ответ по словам)",
          any(s["mode"] in ("keywords_fast", "hybrid", None) for s in rep["searches"])
          and all(s["ms"] < kh.SLOW_MS for s in rep["searches"] if s["found"]), [(s["q"][:20], s["ms"]) for s in rep["searches"][:3]])
    check("поиск по смыслу выключен — предупреждение", any("Выключен" in c["text"] for c in by_area.get("Поиск по смыслу", [])))
    check("инструкции: незаполненный шаблон «<имя>»", any("<имя>" in c["text"] for c in by_area.get("Инструкции", [])))
    db.expire_all()
    check("проверка ничего не записала: ни «пробелы», ни счётчики «пригодилось»",
          sum(r.hit_count or 0 for r in db.query(E).all()) == hits_before
          and db.query(AgentKnowledgeGap).count() == gaps_before)
    check("проверка укладывается в несколько секунд", rep["seconds"] < 10, rep["seconds"])
    db.close()

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
