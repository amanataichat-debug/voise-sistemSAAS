"""
Настройка продаж Анри одной командой (Render Shell).

Что делает (10.11.2026, после звонка af6e4d6a — клиент Марат ушёл):
  1. Находит агента (по имени, id или почте владельца) и показывает, что сейчас стоит: длину инструкций
     переписки и звонков, состав базы знаний, сколько знаний уходит в промпт звонка.
  2. Ищет УСТАРЕВШИЕ цены в базе знаний и инструкциях (на звонке поиск вернул «Тариф Profi — 5 990 сом» при
     настоящих 12 500): всё, где рядом с тарифом стоит сумма не из текущего прайса.
  3. --apply: загружает проверенные факты из docs/anri_knowledge.json как пары «вопрос — ответ» (они идут в
     раздел «ЧТО ТЫ ЗНАЕШЬ НАИЗУСТЬ» промпта звонка и в промпт переписки). Совпадает вопрос — обновляет,
     нет — добавляет. Затем векторизует их для поиска.
  4. --disable-stale (вместе с --apply): выключает записи базы с устаревшими ценами (не удаляет — включить
     обратно можно в кабинете).
  5. --prompts (вместе с --apply): записывает инструкцию звонков (Блок 1 из docs/voice_agent_prompt_anri.md) и
     инструкцию переписки (docs/orchestrator_prompt_anri.md). Старые тексты сохраняются в файл-копию.

Запуск:
  python scripts/setup_anri_sales.py --name Анри                        # только показать
  python scripts/setup_anri_sales.py --name Анри --apply                # загрузить знания
  python scripts/setup_anri_sales.py --name Анри --apply --disable-stale --prompts   # всё сразу
  Вместо --name можно --agent <uuid> или --email <почта владельца>.
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

KNOWLEDGE_PATH = os.path.join(ROOT, "docs", "anri_knowledge.json")
VOICE_DOC = os.path.join(ROOT, "docs", "voice_agent_prompt_anri.md")
ORCH_DOC = os.path.join(ROOT, "docs", "orchestrator_prompt_anri.md")
SOURCE_TAG = "anri_sales_kb"
# Наши записи — первыми в промпте (build_prompt_block сортирует вопрос-ответ по position)
POSITION_BASE = -1000

_TARIFF_RX = re.compile(r"profi|профи|старт|\bstart\b|ai\s*voice|тариф", re.IGNORECASE)
_AMOUNT_RX = re.compile(r"(?<![\d.,])(\d{1,2}[ \u00a0]\d{3}|\d{4,5})(?![\d])(\s*(?:кредит|\w*кредит))?", re.IGNORECASE)


def load_knowledge(path: str = KNOWLEDGE_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def allowed_amounts(knowledge: dict) -> set:
    prices = knowledge.get("current_prices") or {}
    return {int(v) for k, v in prices.items() if isinstance(v, (int, float)) and v >= 1000}


def stale_amounts(text: str, allowed: set) -> list:
    """Суммы рядом с упоминанием тарифа, которых нет в текущем прайсе (кредиты, годы и скидки — не цены)."""
    if not text or not _TARIFF_RX.search(text):
        return []
    found = []
    for m in _AMOUNT_RX.finditer(text):
        if m.group(2):          # «10 000 кредитов» — не цена
            continue
        start = max(text.rfind(c, 0, m.start()) for c in ".!?\n") + 1
        ends = [i for i in (text.find(c, m.end() + 1) for c in ".!?\n") if i >= 0]
        says_som = text[m.end():m.end() + 6].lower().lstrip().startswith(("сом", "с.", "kgs"))
        if not says_som and "кредит" in text[start:min(ends) if ends else len(text)].lower():
            continue            # «В Старте 10 000 кредитов, в Profi — 20 000.» — тоже кредиты
        n = int(re.sub(r"\D", "", m.group(1)))
        if 2020 <= n <= 2035 or n < 1000 or n > 60000:
            continue
        tail = text[m.end():m.end() + 12].lower()
        if "%" in tail or "сек" in tail or "мин" in tail or "символ" in tail:
            continue
        if n not in allowed:
            found.append(n)
    return sorted(set(found))


def extract_block1(doc_text: str) -> str:
    i = doc_text.find("## БЛОК 1")
    if i < 0:
        raise ValueError("в docs/voice_agent_prompt_anri.md нет раздела «## БЛОК 1»")
    a = doc_text.find("```", i)
    b = doc_text.find("```", a + 3)
    if a < 0 or b < 0:
        raise ValueError("Блок 1 должен быть в ``` ```")
    return doc_text[a + 3:b].strip()


def extract_first_phrase(doc_text: str) -> str:
    """Первая фраза входящего звонка — блок ``` под «## ПЕРВАЯ ФРАЗА ВХОДЯЩЕГО ЗВОНКА» (14.11.2026)."""
    i = doc_text.find("## ПЕРВАЯ ФРАЗА ВХОДЯЩЕГО ЗВОНКА")
    if i < 0:
        return ""
    a = doc_text.find("```", i)
    b = doc_text.find("```", a + 3)
    next_section = doc_text.find("\n## ", i + 3)
    if a < 0 or b < 0 or (0 <= next_section < a):
        return ""
    return " ".join(doc_text[a + 3:b].split())


def _norm(title: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (title or "").lower().replace("ё", "е")).split())


def find_agent(db, args):
    from backend.models.agent_config import AgentConfig
    from backend.models.user import User

    q = db.query(AgentConfig)
    if args.agent:
        q = q.filter(AgentConfig.id == args.agent)
    if args.email:
        q = q.join(User, User.id == AgentConfig.user_id).filter(User.email.ilike(args.email.strip()))
    if args.name:
        q = q.filter(AgentConfig.name.ilike(f"%{args.name.strip()}%"))
    rows = q.order_by(AgentConfig.created_at.asc()).all()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        print("Агент не найден. Укажите --name, --agent или --email.")
    else:
        print("Подходит несколько агентов — уточните --agent <id>:")
        for r in rows[:20]:
            print(f"  {r.id}  «{r.name}»")
    return None


def report(db, agent, knowledge: dict) -> dict:
    """Что сейчас стоит у агента и что поменяет --apply. Возвращает план для apply()."""
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E
    from backend.services import agent_voice_context as avc

    allowed = allowed_amounts(knowledge)
    print(f"\nАгент «{agent.name}» ({agent.id})")
    print(f"  Инструкция переписки (additional_instructions): {len(agent.additional_instructions or '')} симв.")
    print(f"  Инструкция звонков (voice_additional_instructions): {len(agent.voice_additional_instructions or '')} симв.")

    rows = db.query(E).filter(E.agent_config_id == agent.id).all()
    active = [r for r in rows if r.is_active]
    by_kind = {}
    for r in active:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    print(f"  База знаний: активных записей {len(active)} ({', '.join(f'{k} {v}' for k, v in sorted(by_kind.items())) or 'пусто'}), "
          f"выключенных {len(rows) - len(active)}")

    # Запись с тем же вопросом, что у факта из пакета, будет ИСПРАВЛЕНА им — устаревшей её не считаем
    # (иначе --disable-stale выключал бы только что исправленный ответ про цены).
    pack_titles = {_norm(item["title"]) for item in knowledge["entries"]}
    stale = []
    for r in active:
        if (r.attributes or {}).get("_source") == SOURCE_TAG:
            continue
        if r.kind == "qa" and _norm(r.title) in pack_titles:
            continue
        bad = stale_amounts(f"{r.title}\n{r.body}", allowed)
        if bad:
            stale.append((r, bad))
    if stale:
        print(f"\n  ⚠ Устаревшие цены в базе знаний ({len(stale)}): текущий прайс {sorted(allowed)}")
        for r, bad in stale:
            print(f"    - [{r.kind}] «{(r.title or '')[:70]}»: {', '.join(str(x) for x in bad)}")
    else:
        print("\n  Устаревших цен в базе знаний не найдено.")

    for field in ("additional_instructions", "voice_additional_instructions", "doc_who_am_i", "doc_what_we_offer",
                  "doc_rules_and_goals", "doc_how_we_talk", "doc_who_we_call"):
        bad = stale_amounts(getattr(agent, field, None) or "", allowed)
        if bad:
            print(f"  ⚠ Устаревшие цены в поле {field}: {', '.join(str(x) for x in bad)} — поправьте текст в кабинете")

    by_title = {}
    for r in rows:
        if r.kind == "qa":
            by_title.setdefault(_norm(r.title), r)
    plan = {"insert": [], "update": [], "same": [], "stale": stale}
    for i, item in enumerate(knowledge["entries"]):
        row = by_title.get(_norm(item["title"]))
        if row is None:
            plan["insert"].append((i, item))
        elif (row.body or "").strip() == item["body"].strip() and row.is_active \
                and (row.keywords or "") == item.get("keywords", ""):
            plan["same"].append((i, item, row))
        else:
            plan["update"].append((i, item, row))
    print(f"\n  Факты из {os.path.relpath(KNOWLEDGE_PATH, ROOT)}: добавить {len(plan['insert'])}, "
          f"обновить {len(plan['update'])}, уже актуальны {len(plan['same'])}")
    for _, item, row in plan["update"]:
        mine = (row.attributes or {}).get("_source") == SOURCE_TAG
        print(f"    обновить{'' if mine else ' (запись владельца)'}: «{item['title']}»")

    try:
        _, stats = avc.build_voice_knowledge_block(db, agent)
        print(f"\n  Сейчас в промпт звонка уходит знаний: {stats.get('total', 0)} симв. "
              f"(о компании {stats.get('company', 0)}, вопрос-ответ {stats.get('knowledge', 0)}, "
              f"прайс {stats.get('price', 0)}, файлы {stats.get('files', 0)})")
    except Exception as e:
        db.rollback()
        print(f"\n  Состав знаний звонка посчитать не удалось: {e}")
    return plan


def apply(db, agent, knowledge: dict, plan: dict, disable_stale: bool) -> list:
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E

    touched = []
    for i, item in plan["insert"]:
        row = E(user_id=agent.user_id, agent_config_id=agent.id, kind="qa", title=item["title"],
                body=item["body"], keywords=item.get("keywords") or None, in_prompt=True, is_active=True,
                position=POSITION_BASE + i, attributes={"_source": SOURCE_TAG, "version": knowledge.get("version")},
                vector_status="pending")
        db.add(row)
        touched.append(row)
    for i, item, row in plan["update"]:
        row.body = item["body"]
        row.keywords = item.get("keywords") or None
        row.is_active = True
        row.in_prompt = True
        row.position = POSITION_BASE + i
        row.attributes = {**(row.attributes or {}), "_source": SOURCE_TAG, "version": knowledge.get("version")}
        row.vector_status = "pending"
        touched.append(row)
    disabled = 0
    if disable_stale:
        for r, _ in plan["stale"]:
            r.is_active = False
            disabled += 1
    db.commit()
    print(f"\n✓ Знания: добавлено {len(plan['insert'])}, обновлено {len(plan['update'])}"
          + (f", выключено устаревших {disabled}" if disable_stale else ""))
    if plan["stale"] and not disable_stale:
        print("  Устаревшие записи оставлены как есть: выключите их в кабинете или запустите с --disable-stale.")
    return touched


def apply_prompts(db, agent) -> str:
    """Записать инструкции звонков и переписки; старые тексты — в файл-копию. Возвращает путь копии."""
    from backend.models.eleven_assistant import ElevenAssistantConfig
    from backend.services.agent_prompts import build_voice_agent_prompt

    with open(VOICE_DOC, encoding="utf-8") as f:
        voice_doc = f.read()
    voice = extract_block1(voice_doc)
    first_phrase = extract_first_phrase(voice_doc)
    with open(ORCH_DOC, encoding="utf-8") as f:
        orch = f.read().strip()

    backup = {
        "agent_id": str(agent.id),
        "saved_at": datetime.utcnow().isoformat(),
        "additional_instructions": agent.additional_instructions,
        "voice_additional_instructions": agent.voice_additional_instructions,
        "inbound_first_phrase": getattr(agent, "inbound_first_phrase", None),
        "number_first_phrases": {},
    }
    numbers = []
    if first_phrase:
        from backend.models.sip_gateway import SipPhoneNumber
        numbers = db.query(SipPhoneNumber).filter(SipPhoneNumber.agent_config_id == agent.id).all()
        backup["number_first_phrases"] = {str(n.id): n.first_phrase for n in numbers if n.first_phrase}
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"anri_prompts_backup_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)

    agent.voice_additional_instructions = voice
    agent.additional_instructions = orch
    if first_phrase:
        # Фраза номера главнее фразы агента (SipGatewayService.resolve_greeting) — очищаем её у номеров агента
        agent.inbound_first_phrase = first_phrase
        for n in numbers:
            n.first_phrase = None
    card = None
    if getattr(agent, "eleven_assistant_id", None):
        card = db.query(ElevenAssistantConfig).filter(ElevenAssistantConfig.id == agent.eleven_assistant_id).first()
        if card is not None:
            card.system_prompt = build_voice_agent_prompt(voice)
    db.commit()
    print(f"\n✓ Инструкция звонков: {len(voice)} симв.; инструкция переписки: {len(orch)} симв."
          + ("; карточка голоса пересобрана" if card is not None else ""))
    if first_phrase:
        print(f"✓ Первая фраза входящего: «{first_phrase}» (у номеров агента своя фраза очищена: {len(numbers)})")
    print(f"  Прежние тексты сохранены: {path}")
    return path


async def vectorize(agent_id) -> None:
    try:
        from backend.services import agent_knowledge_vectors as vec
        if not vec.enabled():
            print("  Векторный поиск выключен (KB_VECTOR_ENABLED=0 или нет ключей) — поиск по словам работает и так.")
            return
        await vec.sync_agent(agent_id)
        print("✓ Векторы для поиска обновлены")
    except Exception as e:
        print(f"  Векторы не обновились ({e}) — обновятся сами при первом поиске.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Настройка продаж Анри: знания, устаревшие цены, инструкции")
    ap.add_argument("--name", help="часть имени агента, например «Анри»")
    ap.add_argument("--agent", help="id агента")
    ap.add_argument("--email", help="почта владельца")
    ap.add_argument("--apply", action="store_true", help="записать изменения (без него — только показать)")
    ap.add_argument("--disable-stale", action="store_true", help="выключить записи базы с устаревшими ценами")
    ap.add_argument("--prompts", action="store_true", help="записать инструкции звонков и переписки из docs/")
    ap.add_argument("--no-vectors", action="store_true", help="не векторизовать (тесты, нет сети)")
    args = ap.parse_args(argv)
    if not (args.name or args.agent or args.email):
        ap.error("укажите агента: --name, --agent или --email")

    import backend.core.config  # noqa: F401 — первым, иначе циклический импорт (см. voice_selfcheck.py)
    from backend.db.session import SessionLocal

    knowledge = load_knowledge()
    db = SessionLocal()
    try:
        agent = find_agent(db, args)
        if agent is None:
            return 2
        plan = report(db, agent, knowledge)
        if not args.apply:
            print("\nЭто был просмотр. Записать: добавьте --apply (и при желании --disable-stale --prompts).")
            return 0
        touched = apply(db, agent, knowledge, plan, args.disable_stale)
        if args.prompts:
            apply_prompts(db, agent)
        agent_id = agent.id
    finally:
        db.close()
    if touched and not args.no_vectors:
        asyncio.run(vectorize(agent_id))
    print("\nГотово. Проверка знаний на звонке: python scripts/voice_selfcheck.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
