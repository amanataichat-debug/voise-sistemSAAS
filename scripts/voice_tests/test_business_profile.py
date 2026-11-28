"""Профиль бизнеса: ниша → модули, воронка, каталог, бот (ветка 1811-niche-admin-speed).

Часть 1 — без БД: у КАЖДОЙ ниши корректный пресет (воронка с системными ключами проходит
нормализацию CRM, вопросы с вариантами и значением по умолчанию, каталог, что спрашивать,
частые вопросы), модули по модели работы, вкладки телефона.
Часть 2 — PostgreSQL (BUSINESS_TEST_DATABASE_URL или ADMIN_TEST_DATABASE_URL):
- /api/business/setup сохраняет нишу, ответы, телефон и настраивает уже созданного бота:
  воронка, разделы каталога, инструкции переписки и звонков, частые вопросы в «Чего не хватает»;
- то, что владелец заполнил сам (инструкции, своя воронка), не перезаписывается;
- новый бот через /api/agent/create создаётся сразу настроенным;
- /api/users/me отдаёт business (модули, каталог, вкладки), оркестратор знает сферу;
- смена ниши меняет стандартную воронку, кривые значения → 400; модули включаются вручную.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
os.environ.update(HOST_URL="https://t.voksyai.online", PRODUCTION="true", OPENAI_API_KEY="k",
                  JWT_SECRET_KEY="business-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


import backend.core.config  # noqa: E402,F401
from backend.services import niche_presets as np_  # noqa: E402
from backend.services import business_profile as bp  # noqa: E402
from backend.services.niche_catalog import NICHES  # noqa: E402
from backend.core.pipeline_stages import normalize_stages_list  # noqa: E402

# ── Часть 1: все ниши ─────────────────────────────────────────────
bad = []
for n in NICHES:
    p = np_.preset(n["key"])
    st = normalize_stages_list(p["pipeline"])
    keys = [s["key"] for s in (st or [])]
    if not st or len(st) != len(p["pipeline"]):
        bad.append((n["key"], "воронка не проходит нормализацию"))
    for sys_key in ("new", "success", "rejected", "do_not_call"):
        if sys_key not in keys:
            bad.append((n["key"], f"нет системной стадии {sys_key}"))
    if keys and keys[0] != "new":
        bad.append((n["key"], "первая стадия не new"))
    for q in p["questions"]:
        if not q.get("options") or q.get("default") not in [o[0] for o in q["options"]]:
            bad.append((n["key"], f"вопрос {q.get('key')} без верного значения по умолчанию"))
    if not p["catalog"].get("title") or not p["ask"] or not p["kb"]:
        bad.append((n["key"], "нет каталога / что спрашивать / частых вопросов"))
check(f"все {len(NICHES)} ниш: корректные пресеты", not bad, bad[:5])

m = np_.modules_for("massage_spa", {})
check("массаж: запись есть, CRM по умолчанию нет, заказов нет", m["booking"] and not m["crm"] and not m["orders"], m)
check("стоматология: CRM по умолчанию нужна", np_.modules_for("dentistry", {})["crm"] is True)
m = np_.modules_for("clothing", {})
check("одежда: заказы, без записи, без CRM", m["orders"] and not m["booking"] and not m["crm"], m)
m = np_.modules_for("real_estate", {"meetings": "no"})
check("недвижимость: CRM всегда, встречи по ответу", m["crm"] and not m["booking"], m)
m = np_.modules_for("other", {"model": "booking", "crm": "no"})
check("другое → «по записи»: запись, без CRM", m["booking"] and not m["crm"], m)
_page_ready = bp.page_ready
bp.page_ready = lambda name: True      # страницы «Записи» и «Заказы» есть в сборке
tabs = [t["id"] for t in bp.tabs_for("full", np_.modules_for("real_estate", {}), "Объекты")]
check("Profi, недвижимость: Главная · Записи · AI · Клиенты · Ещё", tabs == ["home", "booking", "ai", "clients", "more"], tabs)
tabs = [t["id"] for t in bp.tabs_for("full", np_.modules_for("clothing", {}), "Товары")]
check("Profi без записи: Главная · Звонки · AI · Клиенты · Ещё", tabs == ["home", "calls", "ai", "clients", "more"], tabs)
tabs = [t["id"] for t in bp.tabs_for("reactive", np_.modules_for("massage_spa", {}), "Услуги", beauty=True)]
check("салон на Старте / Beauty AI: Главная · Записи · AI · Клиенты · Ещё",
      tabs == (["home", "booking", "ai", "clients", "more"] if bp.page_ready("booking.html") else ["home", "inbox", "catalog", "clients", "more"]), tabs)
tabs = [t["id"] for t in bp.tabs_for("reactive", np_.modules_for("clothing", {}), "Товары")]
check("Старт, магазин: Главная · Чаты · Заказы · Клиенты · Ещё", tabs == ["home", "inbox", "orders", "clients", "more"], tabs)
pg = bp.module_pages(np_.modules_for("clothing", {}))
check("меню: у магазина «Заказы» есть и у Profi (вкладок телефона на неё нет)",
      pg["orders"] == "/static/orders.html" and pg["booking"] is None, pg)
pg = bp.module_pages(np_.modules_for("auto_service", {"booking": "yes", "parts": "yes"}))
check("меню: СТО с записью и запчастями — и «Записи», и «Заказы»", pg["orders"] and pg["booking"], pg)
bp.page_ready = lambda name: False
check("меню: страницы модуля нет — пункта нет", bp.module_pages({"booking": True, "orders": True}) == {"booking": None, "orders": None})     # страницы модуля ещё нет — вкладка не ведёт в пустоту
tabs = [t["id"] for t in bp.tabs_for("reactive", np_.modules_for("clothing", {}), "Товары")]
check("страницы «Заказы» нет — вместо неё каталог", tabs == ["home", "inbox", "catalog", "clients", "more"], tabs)
tabs = [t["id"] for t in bp.tabs_for("full", np_.modules_for("massage_spa", {}), "Услуги")]
check("страницы «Записи» нет — у Profi остаются «Звонки»", tabs[1] == "calls", tabs)
bp.page_ready = _page_ready
check("цветы: каталог «Букеты» с размерами букета", np_.preset("flowers")["catalog"]["title"] == "Букеты"
      and np_.preset("flowers")["catalog"]["size_presets"][0][0] == "Размер букета")
txt = bp.chat_instructions("massage_spa", np_.modules_for("massage_spa", {}), {"reminders": "yes"})
check("инструкция массажа: цель, что спросить, запись", "Массаж и СПА" in txt and "записаться" in txt and "• " in txt, txt[:200])
check("в инструкции нет незаполненных <…>", "<" not in txt and ">" not in txt)

PG = os.environ.get("BUSINESS_TEST_DATABASE_URL") or os.environ.get("ADMIN_TEST_DATABASE_URL")
if not PG:
    print("SKIP часть 2: BUSINESS_TEST_DATABASE_URL не задан")
    print("ПРОВАЛОВ:", failures)
    sys.exit(1 if failures else 0)

# ── Часть 2: PostgreSQL ─────────────────────────────────────────────
os.environ["DATABASE_URL"] = PG
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
import backend.db.session as dbs  # noqa: E402
engine = create_engine(PG)
dbs.engine = engine
dbs.SessionLocal = sessionmaker(bind=engine)
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeGap  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.db.session import get_db  # noqa: E402

S = sessionmaker(bind=engine)


def _get_db():
    d = S()
    try:
        yield d
    finally:
        d.close()


app_module.app.dependency_overrides[get_db] = _get_db
cl = TestClient(app_module.app, raise_server_exceptions=False)
db = S()
plan = SubscriptionPlan(code="start", name="Start", price=7500, max_assistants=3, description="s", is_active=True)
db.add(plan)
db.flush()
u = User(email="massage@kg.kg", password_hash="x", email_verified=True, first_name="Айгуль",
         subscription_plan_id=plan.id, subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20))
db.add(u)
db.flush()
bot = AgentConfig(user_id=u.id, name="Бот салона")
own = AgentConfig(user_id=u.id, name="Второй бот", additional_instructions="Мои правила", pipeline_stages=None)
db.add_all([bot, own])
db.commit()
H = {"Authorization": f"Bearer {create_jwt_token(u.id)}"}

r = cl.get("/api/business/niches", headers=H)
check("список ниш с вопросами", r.status_code == 200 and len(r.json().get("niches", [])) == len(NICHES)
      and r.json()["niches"][0].get("questions"), r.status_code)
r = cl.get("/api/users/me", headers=H)
check("до выбора: business.needs_setup", r.status_code == 200 and r.json().get("business", {}).get("needs_setup") is True,
      r.text[:200])

r = cl.post("/api/business/setup", headers=H, json={"niche": "massage_spa", "answers": {"staff": "1", "crm": "no", "reminders": "yes"},
                                                    "phone": "0700 11 22 33", "company_name": "Relax SPA"})
check("setup: 200 и сводка", r.status_code == 200 and r.json().get("summary"), r.text[:300])
summary = " | ".join(r.json().get("summary", []))
check("сводка говорит, что настроено", "Массаж и СПА" in summary and "Онлайн-запись" in summary and "Воронка" in summary, summary)
db.expire_all()
u2 = db.get(User, u.id)
check("пользователь: ниша, ответы, телефон, компания, onboarded", u2.business_niche == "massage_spa"
      and u2.business_profile.get("answers", {}).get("staff") == "1" and u2.phone == "+996700112233"
      and u2.company_name == "Relax SPA" and u2.onboarded_at is not None)
b1 = db.get(AgentConfig, bot.id)
b2 = db.get(AgentConfig, own.id)
import json  # noqa: E402
stages = json.loads(b1.pipeline_stages or "[]")
check("бот: воронка массажа", [s["label"] for s in stages][:3] == ["Новый клиент", "Записан", "Был на визите"], [s["label"] for s in stages])
check("бот: разделы каталога", (b1.catalog_collections or [])[:2] == ["Классический массаж", "Лечебный"], b1.catalog_collections)
check("бот: инструкции переписки и звонков", "Массаж и СПА" in (b1.additional_instructions or "")
      and "Массаж и СПА" in (b1.voice_additional_instructions or ""), (b1.additional_instructions or "")[:80])
check("бот: niche_key", b1.niche_key == "massage_spa")
gaps = db.query(AgentKnowledgeGap).filter(AgentKnowledgeGap.agent_config_id == bot.id).all()
check("бот: частые вопросы клиентов в «Чего не хватает»", len(gaps) >= 7 and any("противопоказания" in g.query.lower() for g in gaps), len(gaps))
check("свои инструкции владельца не перезаписаны", b2.additional_instructions == "Мои правила")

r = cl.post("/api/business/setup", headers=H, json={"niche": "massage_spa", "answers": {"staff": "2-5"}})
db.expire_all()
check("повторный setup: вопросы без дублей", db.query(AgentKnowledgeGap).filter(AgentKnowledgeGap.agent_config_id == bot.id).count() == len(gaps))

r = cl.get("/api/users/me", headers=H)
biz = r.json().get("business") or {}
third = "booking" if bp.page_ready("booking.html") else "catalog"
check("/users/me → business: модули, каталог «Услуги», вкладки", biz.get("modules", {}).get("booking") is True
      and biz.get("catalog", {}).get("title") == "Услуги" and [t["id"] for t in biz.get("tabs", [])][:3] in (["home", "inbox", third], ["home", "booking", "ai"])
      and r.json().get("phone") == "+996700112233", biz)

from backend.services.agent_prompts import build_orchestrator_prompt  # noqa: E402
prompt = build_orchestrator_prompt(db.get(AgentConfig, bot.id), include_time_block=False, db=db)
check("оркестратор знает сферу и модули", "## Сфера бизнеса" in prompt and "Массаж и СПА" in prompt and "онлайн-запись" in prompt)

u3 = User(email="flowers@kg.kg", password_hash="x", email_verified=True, first_name="Нурай",
          subscription_plan_id=plan.id, subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20))
db.add(u3)
db.commit()
H3 = {"Authorization": f"Bearer {create_jwt_token(u3.id)}"}
r = cl.post("/api/business/setup", headers=H3, json={"niche": "flowers", "answers": {"delivery": "yes"}})
check("клиент без бота: setup проходит и обещает настроить бота", r.status_code == 200
      and any("сразу после создания" in x for x in r.json().get("summary", [])), r.text[:200])
r = cl.post("/api/agent/create", headers=H3, json={
    "name": "Новый бот", "assistant_type": "eleven", "doc_who_am_i": "Салон", "doc_who_we_call": "клиенты",
    "doc_how_we_talk": "вежливо", "doc_what_we_offer": "массаж", "doc_rules_and_goals": "записывать"})
check("новый бот создан", r.status_code == 200, r.text[:300])
if r.status_code == 200:
    db.expire_all()
    nb = db.get(AgentConfig, uuid.UUID(r.json()["id"]))
    check("новый бот сразу настроен под нишу", nb.niche_key == "flowers" and "Цветы" in (nb.additional_instructions or "")
          and json.loads(nb.pipeline_stages or "[]")[1]["label"] == "Подбор букета" and (nb.catalog_collections or [])[0] == "Букеты",
          (nb.niche_key, (nb.additional_instructions or "")[:60]))

r = cl.post("/api/business/setup", headers=H, json={"niche": "dentistry"})
db.expire_all()
b1 = db.get(AgentConfig, bot.id)
check("смена ниши: стандартная воронка заменена на стоматологию", json.loads(b1.pipeline_stages)[1]["label"] == "Консультация",
      [s["label"] for s in json.loads(b1.pipeline_stages)])
check("смена ниши: инструкции, которые уже есть, не тронуты", "Массаж и СПА" in (b1.additional_instructions or ""))
check("стоматология: CRM включилась", cl.get("/api/business/profile", headers=H).json()["modules"]["crm"] is True)

r = cl.put("/api/business/modules", headers=H, json={"modules": {"crm": False, "orders": True, "hack": True}})
check("модули вручную: CRM выкл, заказы вкл, лишнее игнор", r.status_code == 200 and r.json()["modules"]["crm"] is False
      and r.json()["modules"]["orders"] is True and "hack" not in r.json()["modules"], r.text[:200])
r = cl.post("/api/business/setup", headers=H, json={"niche": "spaceships"})
check("неизвестная ниша: 400", r.status_code == 400, r.status_code)
r = cl.post("/api/business/setup", headers=H, json={"niche": "flowers", "phone": "12"})
check("кривой телефон: 400 с понятным текстом", r.status_code == 400 and "телефон" in r.text.lower(), r.text[:150])
r = cl.post("/api/business/setup", headers=H, json={"niche": "other", "niche_other": "  Ремонт   часов ", "answers": {"model": "sales"}})
check("«Другое» своими словами", r.status_code == 200 and r.json()["business"]["niche_title"] == "Другое: Ремонт часов"
      and r.json()["business"]["modules"]["orders"] is True, r.text[:200])

db.close()
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
