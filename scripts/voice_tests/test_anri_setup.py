"""Настройка продаж Анри (scripts/setup_anri_sales.py, docs/anri_knowledge.json) — после звонка af6e4d6a.

Часть 1 без сети и БД: факты базы знаний совпадают с кодом и сайтом (тарифы, минуты), устаревшие цены находятся,
Блок 1 и инструкция переписки читаются. Часть 2 — на настоящем PostgreSQL при ANRI_TEST_DATABASE_URL: просмотр
ничего не пишет, --apply загружает факты, обновляет запись владельца с тем же вопросом (без дубля), выключает
устаревшие, записывает инструкции с копией прежних, повтор ничего не меняет, промпт звонка начинается с фактов.
"""
import io
import json
import logging
import os
import sys
import types
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("ANRI_TEST_DATABASE_URL")
os.environ.update(HOST_URL="http://x", DATABASE_URL=PG or "sqlite://", PRODUCTION="false", OPENAI_API_KEY="k",
                  KB_VECTOR_ENABLED="0")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import setup_anri_sales as sas

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ============================================================ часть 1: факты и разбор, без БД
kn = sas.load_knowledge()
entries = kn["entries"]
titles = [sas._norm(e["title"]) for e in entries]
check("база знаний: вопросы уникальны, ответы не пустые", len(set(titles)) == len(titles) and all(e["body"].strip() for e in entries))
check("база знаний: ответы короткие, чтобы голос сказал их сразу (до 600 симв.)",
      max(len(e["body"]) for e in entries) <= 600, max(len(e["body"]) for e in entries))

from backend.api.payments import SUBSCRIPTION_PLANS_CONFIG as PLANS
cp = kn["current_prices"]
check("цены тарифов в базе = цены в коде оплаты (payments.SUBSCRIPTION_PLANS_CONFIG)",
      cp["ai_voice"] == PLANS["ai_voice"]["base_price"] and cp["start"] == PLANS["start"]["base_price"]
      and cp["profi"] == PLANS["profi"]["base_price"], (cp, {k: v["base_price"] for k, v in PLANS.items()}))
from backend.services import wallet_service as ws
check("минута в базе = минута кошелька (gpt_live), ±0,01 сом", abs(ws.som_per_minute("gpt_live") - cp["minute_in"]) < 0.011,
      ws.som_per_minute("gpt_live"))
_site = open(os.path.join(ROOT, "frontend/src/i18n/ru.js"), encoding="utf-8").read()
check("минуты в базе = минуты на сайте (5,47 и 6,47)", "5,47" in _site and "6,47" in _site
      and any("5,47" in e["body"] and "6,47" in e["body"] for e in entries))
check("на сайте: «платите только за подписку и минуты» → в базе «разовой платы за подключение нет»",
      "Вы платите только за подписку и минуты" in _site and any("Разовой платы за подключение нет" in e["body"] for e in entries))
allowed = sas.allowed_amounts(kn)
check("в самих фактах нет устаревших цен", all(not sas.stale_amounts(f"{e['title']} {e['body']}", allowed) for e in entries),
      [e["title"] for e in entries if sas.stale_amounts(e["body"], allowed)])
check("звонок af6e4d6a: «Тариф Profi — 5 990 сом» находится как устаревшая цена",
      sas.stale_amounts("Тариф Profi\nПрофи\nЦена -5 990 сом в месяц", allowed) == [5990])
check("Profi 15 000 (до 07.11.2026) — устаревшая", sas.stale_amounts("Profi — 15 000 сом в месяц", allowed) == [15000])
check("кредиты, годы, текущие цены и текст без тарифа — не устаревшие цены",
      sas.stale_amounts("Старт 7 500 сом, 10 000 кредитов, с 2026 года", allowed) == []
      and sas.stale_amounts("Доставка 5000 сом по Бишкеку", allowed) == [])
_voice_doc = open(sas.VOICE_DOC, encoding="utf-8").read()
_b1 = sas.extract_block1(_voice_doc)
check("Блок 1 читается из шаблона, без «одну секунду» и устаревших цен",
      _b1.startswith("Ты — Анри") and "одну секунду" not in _b1 and not sas.stale_amounts(_b1, allowed), len(_b1))
# 14.11.2026 (1411-voice-lean): без нумерованного сценария, примеры — только для стиля, первая фраза с представлением
check("Блок 1 Анри: короткий (≤ 5 тыс.), без сценария «Звонок по шагам», примеры помечены «не повторяй дословно»",
      len(_b1) <= 5000 and "Звонок по шагам" not in _b1 and "Не повторяй их дословно" in _b1, len(_b1))
check("Блок 1 Анри: разговорный кыргызский без книжных слов", not any(w in _b1 for w in ("албетте", "сунуштайм", "өтүнмө")))
_fp = sas.extract_first_phrase(_voice_doc)
check("первая фраза входящего: представляется по имени и компании, без «чем могу помочь»",
      "Анри" in _fp and "Вокси Эй-Ай" in _fp and "чем могу помочь" not in _fp.lower(), _fp)
check("первая фраза: нет раздела — пустая строка", sas.extract_first_phrase("# x\n## БЛОК 1\n```\nа\n```") == "")
_orch = open(sas.ORCH_DOC, encoding="utf-8").read()
check("инструкция переписки: без непроверенного «подключим за 1–2 дня» и «зависит от объёма»",
      _orch.startswith("# Кто ты") and "1–2 дня" not in _orch and "месяц, зависит от объёма" not in _orch)
check("инструкция переписки: после звонка — ровно обещанное и время следующего контакта",
      "ровно то, что пообещал голосовой агент" in _orch and "Не шли презентацию, КП и ссылку одновременно" in _orch)

# ============================================================ часть 2: настоящий PostgreSQL
if not PG:
    print("SKIP часть 2: ANRI_TEST_DATABASE_URL не задан")
else:
    from sqlalchemy import text as _text
    from backend.db.session import SessionLocal, engine
    from backend.models.base import Base
    import backend.models  # noqa: F401 — регистрирует все модели
    from backend.models.user import User
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E
    from backend.models.eleven_assistant import ElevenAssistantConfig
    from backend.services import agent_voice_context as avc
    from backend.services.agent_prompts import build_voice_agent_prompt

    with engine.begin() as conn:
        conn.execute(_text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    db = SessionLocal()
    owner = User(email="anri-test@x.kg", password_hash="x")
    db.add(owner); db.flush()
    card = ElevenAssistantConfig(user_id=owner.id, name="Анри Тест Voice", system_prompt=build_voice_agent_prompt("старый блок"))
    db.add(card); db.flush()
    ag = AgentConfig(user_id=owner.id, name="Анри Тест", is_active=True, eleven_assistant_id=card.id,
                     additional_instructions="Старый промпт оркестратора", voice_additional_instructions="старый блок",
                     doc_what_we_offer="Тариф Profi — 15 000 сом в месяц")
    db.add(ag); db.flush()
    stale = E(user_id=owner.id, agent_config_id=ag.id, kind="section", title="Тариф Profi",
              body="Profi\nДля активных продаж\n\n Цена -5 990 сом в месяц", in_prompt=True, is_active=True)
    same_q = E(user_id=owner.id, agent_config_id=ag.id, kind="qa", title="Сколько стоит? Какие у вас тарифы?",
               body="Старт 7 500, Profi 5 990", in_prompt=True, is_active=True)
    keep = E(user_id=owner.id, agent_config_id=ag.id, kind="qa", title="Где вы находитесь?", body="Бишкек, офис в центре.",
             in_prompt=True, is_active=True)
    db.add_all([stale, same_q, keep]); db.commit()
    ag_id, stale_id, same_id, keep_id, card_id = ag.id, stale.id, same_q.id, keep.id, card.id
    db.close()

    def run(*args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = sas.main(["--name", "Анри Тест", "--no-vectors", *args])
        return code, buf.getvalue()

    code, out = run()
    db = SessionLocal()
    n_qa = db.query(E).filter(E.agent_config_id == ag_id, E.kind == "qa").count()
    check("просмотр: ничего не записал, показал устаревшие цены в базе и в поле агента",
          code == 0 and n_qa == 2 and "5990" in out and "doc_what_we_offer" in out and "добавить" in out, out[-600:])
    db.close()

    code, out = run("--apply", "--disable-stale", "--prompts")
    db = SessionLocal()
    rows = db.query(E).filter(E.agent_config_id == ag_id, E.kind == "qa").all()
    tagged = [r for r in rows if (r.attributes or {}).get("_source") == sas.SOURCE_TAG]
    check("--apply: все факты загружены, запись владельца с тем же вопросом обновлена без дубля",
          code == 0 and len(tagged) == len(entries) and len(rows) == len(entries) + 1
          and db.get(E, same_id).body == entries[0]["body"], (len(rows), len(tagged)))
    check("--disable-stale: «Profi 5 990» выключена, чужая запись без цен — нет, исправленная фактом — включена",
          db.get(E, stale_id).is_active is False and db.get(E, keep_id).is_active is True
          and db.get(E, same_id).is_active is True)
    check("факты помечены на векторизацию и стоят первыми в промпте",
          all(r.vector_status == "pending" for r in tagged) and all(r.position < 0 for r in tagged))
    a2 = db.get(AgentConfig, ag_id)
    c2 = db.get(ElevenAssistantConfig, card_id)
    check("--prompts: инструкции звонков и переписки записаны, карточка голоса пересобрана",
          a2.voice_additional_instructions == _b1 and a2.additional_instructions.startswith("# Кто ты")
          and _b1 in c2.system_prompt and c2.system_prompt.startswith("Ты — голосовой AI-агент"))
    _bk = [ln for ln in out.splitlines() if "Прежние тексты сохранены" in ln]
    _bk_data = json.load(open(_bk[0].split(": ", 1)[1].strip(), encoding="utf-8")) if _bk else {}
    check("--prompts: прежние тексты сохранены в копию", _bk_data.get("additional_instructions") == "Старый промпт оркестратора",
          _bk)
    prompt, stats = avc.build_call_system_prompt(db, c2, a2, call_context="Исходящий звонок Марату", max_chars=24000)
    check("промпт звонка: начинается с фактов «наизусть», в нём минуты и «разовой платы нет», без 5 990",
          prompt.startswith(f"# {avc.KNOWLEDGE_HEADING}") and "5,47" in prompt and "Разовой платы за подключение нет" in prompt
          and "5 990" not in prompt and prompt.index(avc.KNOWLEDGE_HEADING) < prompt.index("Ты — Анри"), stats)
    _missing = [e["title"] for e in entries if e["title"] not in prompt]
    check("промпт звонка: ВСЕ факты пакета в разделе «наизусть», ни один не ушёл в поиск (пауза)",
          not _missing, _missing[:3])
    db.close()

    code, out = run("--apply")
    check("повторный --apply ничего не меняет", code == 0 and "добавить 0, обновить 0" in out, [l for l in out.splitlines() if "обновить" in l])
    code, out = run("--apply", "--name", "Нет такого")
    check("агента нет — понятный выход с кодом 2", code == 2)

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
