#!/usr/bin/env python3
"""
Самопроверка голосового агента ПЕРЕД звонком (ветка 2510-voice-live).

Зачем. Знания, речь и подсказки голоса делят один бюджет промпта, и правка в одном месте
ломала другое (знания вытеснялись, поиск отвечал пусто). Этот скрипт за несколько секунд на
РЕАЛЬНЫХ данных агента показывает, что сломано, до того как вы позвоните.

Запуск на Render (Shell сервиса, там лежат ключи и база):
    cd /opt/render/project/src
    python scripts/voice_selfcheck.py                         # единственный агент или список
    python scripts/voice_selfcheck.py --agent Анри            # по части имени или uuid
    python scripts/voice_selfcheck.py --questions scripts/voice_selfcheck_questions.json
    python scripts/voice_selfcheck.py --caller 996558200862   # худший случай: звонит знакомый клиент
    python scripts/voice_selfcheck.py --reindex               # заново проиндексировать записи для поиска по смыслу
    python scripts/voice_selfcheck.py --json                  # то же машинным форматом

Что проверяет:
  1. состав базы знаний и синхронизацию векторов (записи без вектора не находятся по смыслу);
  2. БЮДЖЕТ ПРОМПТА ЗВОНКА: сколько занимает промпт карточки, контекст, знания, влезло ли всё
     в лимит голосового слоя (знания выброшены = FAIL);
  3. какие функции получит голос (поиск по базе, каталог) и что подсказка про материалы на месте;
  4. ПОИСК: каждый вопрос из списка проходит тем же кодом, что и на звонке (гибрид слова + смысл),
     с замером задержки;
  5. векторный слой: включён ли, отвечает ли Pinecone и за сколько.

Вопросы: файл JSON вида [{"q": "тестовая симка", "expect": "тестов"}, ...]. `expect` —
подстрока, которая должна быть в найденном (без неё достаточно любого непустого результата).
Положите сюда то, что клиенты спрашивают чаще всего: это ваш набор «не должно ломаться».

Код возврата: 0 — провалов нет, 1 — есть FAIL (подходит для проверки перед деплоем).
"""

import argparse
import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_QUESTIONS = [
    {"q": "тестовая симка"},
    {"q": "какие документы нужны для подключения"},
    {"q": "сколько стоит"},
    {"q": "пришлите коммерческое предложение"},
    {"q": "умеет ли агент звонить клиентам"},
    {"q": "какой у вас режим работы"},
]

SLOW_SEARCH_MS = 1500
CHARS_PER_TOKEN = 2.6   # русский текст ≈ 2,5–3 символа на токен (оценка)
OPENAI_TOKEN_LIMIT = 16384


class Report:
    """Копит результаты проверок: PASS / WARN / FAIL."""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def add(self, level: str, area: str, message: str, **data: Any) -> None:
        self.items.append({"level": level, "area": area, "message": message, **({"data": data} if data else {})})

    def ok(self, area: str, message: str, **data: Any) -> None:
        self.add("PASS", area, message, **data)

    def warn(self, area: str, message: str, **data: Any) -> None:
        self.add("WARN", area, message, **data)

    def fail(self, area: str, message: str, **data: Any) -> None:
        self.add("FAIL", area, message, **data)

    def count(self, level: str) -> int:
        return sum(1 for i in self.items if i["level"] == level)


# ---------------------------------------------------------------------------
# Выбор агента
# ---------------------------------------------------------------------------
def pick_agent(db, selector: Optional[str]):
    from backend.models.agent_config import AgentConfig

    agents = db.query(AgentConfig).order_by(AgentConfig.created_at.asc()).all() \
        if hasattr(AgentConfig, "created_at") else db.query(AgentConfig).all()
    if selector:
        sel = selector.strip().lower()
        found = [a for a in agents if str(a.id).lower() == sel or sel in (a.name or "").lower()]
    else:
        found = agents if len(agents) == 1 else []
    return found[0] if len(found) == 1 else None, agents


# ---------------------------------------------------------------------------
# 1. База знаний и вектора
# ---------------------------------------------------------------------------
def check_knowledge(db, agent, rep: Report) -> None:
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E
    from backend.services import agent_knowledge_vectors as vectors

    rows = db.query(E).filter(E.agent_config_id == agent.id).all()
    active = [r for r in rows if r.is_active]
    by_kind: Dict[str, int] = {}
    for r in active:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    if not active and not getattr(agent, "kb_namespace", None):
        rep.fail("база", "База знаний пуста: нет ни записей, ни файлов — агенту нечем отвечать")
        return
    rep.ok("база", "Активных записей: " + (", ".join(f"{k} — {v}" for k, v in sorted(by_kind.items())) or "нет")
           + ("; есть файлы в поиске (Pinecone)" if getattr(agent, "kb_namespace", None) else ""))

    indexed = [r for r in active if r.kind in vectors.INDEXED_KINDS]
    if not vectors.enabled():
        rep.warn("вектора", "Векторный поиск выключен (KB_VECTOR_ENABLED=0 или нет ключа Pinecone/OpenAI): "
                            "поиск идёт только по словам")
        return
    unsynced = [r for r in indexed if r.vector_status not in ("ready",)]
    if unsynced:
        rep.warn("вектора", f"Записей без вектора: {len(unsynced)} из {len(indexed)} — по смыслу они не находятся. "
                            f"Запустите с --reindex (или подождите: фоновая проверка досинхронизирует сама)",
                 titles=[(r.title or "")[:40] for r in unsynced[:5]])
    else:
        rep.ok("вектора", f"Все {len(indexed)} записей проиндексированы для поиска по смыслу")


async def check_pinecone(agent, rep: Report) -> None:
    from backend.services import agent_knowledge_vectors as vectors

    if not vectors.enabled():
        return
    started = time.time()
    res = await vectors.search("проверка связи", getattr(agent, "kb_namespace", None), agent.id)
    ms = int((time.time() - started) * 1000)
    if res is None:
        rep.fail("вектора", "Векторный слой не ответил (ошибка или таймаут) — на звонке будет только поиск по словам")
    elif ms > 1500:
        rep.warn("вектора", f"Векторный поиск отвечает медленно: {ms} мс (таймаут {vectors.VECTOR_TIMEOUT} с)")
    else:
        rep.ok("вектора", f"Pinecone и эмбеддинги отвечают за {ms} мс")


# ---------------------------------------------------------------------------
# 2–3. Бюджет промпта, функции
# ---------------------------------------------------------------------------
def check_prompt(db, agent, caller: Optional[str], rep: Report) -> None:
    from backend.api.agent import _resolve_voice_assistant
    from backend.services import agent_voice_context
    from backend.services.sip_gateway_service import SipGatewayService
    from backend.websockets import live_client

    voice = _resolve_voice_assistant(db, agent)
    if voice is None:
        rep.fail("промпт", "У агента нет голосовой карточки — звонить нечем")
        return
    limit = live_client.LIVE_VOICE_INSTRUCTIONS_MAX_CHARS
    base = (getattr(voice, "system_prompt", None) or "").strip()

    call = SimpleNamespace(direction="inbound", caller=caller or "996000000000", to_number=None,
                           call_metadata={}, id="selfcheck")
    ctx = SipGatewayService.call_context_text(call)
    followup = SipGatewayService.messenger_followup_hint(agent, db)
    if caller:
        try:
            known = SipGatewayService.inbound_known_contact_block(call, agent, db)
        except Exception as e:
            known = ""
            rep.warn("промпт", f"История знакомого клиента не собралась: {e}")
        if known:
            ctx = f"{ctx}\n\n{known}"
        else:
            rep.warn("промпт", f"Номер {caller} в базе контактов не найден — историю клиента не проверить")
    ctx = "\n\n".join(p for p in (ctx, followup) if p)

    prompt, stats = agent_voice_context.build_call_system_prompt(db, voice, agent, call_context=ctx, max_chars=limit)
    constant_hint = len(live_client.VOICE_DELEGATION_HINT) + len(live_client.VOICE_TELEPHONY_HINT)
    total_chars = len(prompt) + constant_hint
    tokens = int(total_chars / CHARS_PER_TOKEN)
    knowledge = stats.get("total", 0)

    rep.ok("промпт", f"Бюджет: промпт карточки {len(base)} + контекст звонка {len(ctx)} + знания {knowledge} "
                     f"+ постоянные правила голоса {constant_hint} = {total_chars} симв. "
                     f"(≈{tokens} токенов из {OPENAI_TOKEN_LIMIT}); лимит промпта {limit}")
    if stats.get("dropped"):
        rep.fail("промпт", f"ЗНАНИЯ НЕ ВЛЕЗЛИ: промпт карточки {len(base)} симв. почти занял лимит {limit}. Агент будет "
                           f"искать каждый ответ функцией. Сократите «дополнительные инструкции» агента")
    elif stats.get("trimmed") and knowledge < agent_voice_context.MIN_CALL_KNOWLEDGE:
        rep.fail("промпт", f"Знания урезаны до {knowledge} симв. (нужно хотя бы {agent_voice_context.MIN_CALL_KNOWLEDGE}): "
                           f"промпт карточки {len(base)} симв. занимает почти весь лимит {limit}. Практически всё "
                           f"агент будет искать функцией. Сократите «дополнительные инструкции» агента")
    elif stats.get("trimmed"):
        rep.warn("промпт", f"Знания обрезаны до {knowledge} симв. (бюджет {agent_voice_context.VOICE_KNOWLEDGE_BUDGET}): "
                           f"часть наизусть не помещается, остальное агент достаёт поиском")
    elif knowledge:
        rep.ok("промпт", "Знания целиком в промпте звонка: "
               + ", ".join(f"{n} {stats[k]}" for k, n in (("company", "о компании"), ("knowledge", "вопрос-ответ"),
                                                           ("price", "прайс"), ("files", "материалы")) if stats.get(k)))
    else:
        rep.warn("промпт", "В промпт звонка знания не попали (база пуста или нечего вшивать) — всё через поиск")
    if limit < 20000:
        rep.warn("промпт", f"Лимит промпта голоса {limit} мал (норма 24000) — удалите переменную "
                           f"LIVE_VOICE_INSTRUCTIONS_MAX_CHARS в Render")
    if tokens > OPENAI_TOKEN_LIMIT * 0.9:
        rep.fail("промпт", f"Промпт близок к лимиту OpenAI ({tokens} из {OPENAI_TOKEN_LIMIT} токенов)")
    if "МАТЕРИАЛЫ" not in live_client.VOICE_DELEGATION_HINT:
        rep.fail("промпт", "В постоянной подсказке голоса нет правила про отправку материалов")

    wanted = agent_voice_context.kb_function_names(db, agent)
    have = set()
    functions = getattr(voice, "functions", None)
    if isinstance(functions, dict):
        have = set(functions.get("enabled_functions", []))
    elif isinstance(functions, list):
        have = {(f.get("name") if isinstance(f, dict) else f) for f in functions}
    rep.ok("функции", f"Функции знаний на звонке (считаются живьём перед звонком): {', '.join(sorted(wanted)) or 'нет'}")
    if "search_knowledge_base" not in wanted:
        rep.warn("функции", "Поиск по базе знаний на звонке не включится: в базе ничего нет")
    stale = {"search_pinecone"} & have
    if stale:
        rep.warn("функции", "На карточке остался устаревший search_pinecone — на звонке он заменится живым пересчётом")


# ---------------------------------------------------------------------------
# 4. Поиск
# ---------------------------------------------------------------------------
async def check_search(db, agent, questions: List[Dict[str, str]], rep: Report) -> None:
    from backend.services import agent_knowledge_service as kb

    for item in questions:
        q = item["q"]
        expect = (item.get("expect") or "").lower()
        started = time.time()
        try:
            res = await kb.answer_query(db, agent.id, getattr(agent, "kb_namespace", None), q, top_k=4)
        except Exception as e:
            rep.fail("поиск", f"«{q}» — поиск упал: {type(e).__name__}: {e}")
            continue
        ms = int((time.time() - started) * 1000)
        results = res.get("results") or []
        head = [(r.get("title") or (r.get("text") or "")[:40]).strip() for r in results[:3]]
        mode = res.get("search") or "слова"
        blob = json.dumps(results, ensure_ascii=False).lower()
        if not results:
            rep.fail("поиск", f"«{q}» — НИЧЕГО не нашлось ({ms} мс)")
        elif expect and expect not in blob:
            rep.fail("поиск", f"«{q}» — нашлось, но без «{expect}»: {head} ({ms} мс)")
        elif ms > SLOW_SEARCH_MS:
            rep.warn("поиск", f"«{q}» — {head} за {ms} мс (медленно, порог {SLOW_SEARCH_MS})", mode=mode)
        else:
            rep.ok("поиск", f"«{q}» → {head} [{mode}, {ms} мс]")


# ---------------------------------------------------------------------------
# Вывод и запуск
# ---------------------------------------------------------------------------
ICON = {"PASS": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]"}


def print_report(rep: Report, agent_name: str) -> None:
    print(f"\nСамопроверка голоса — агент «{agent_name}»\n" + "=" * 70)
    area = None
    for item in rep.items:
        if item["area"] != area:
            area = item["area"]
            print(f"\n{area.upper()}")
        print(f"  {ICON[item['level']]} {item['message']}")
        titles = (item.get("data") or {}).get("titles")
        if titles:
            print(f"         например: {titles}")
    print("\n" + "=" * 70)
    verdict = "ГОТОВО к звонку" if not rep.count("FAIL") else "ЕСТЬ ПРОВАЛЫ — звонить рано"
    print(f"{verdict}: OK {rep.count('PASS')}, предупреждений {rep.count('WARN')}, провалов {rep.count('FAIL')}")


def load_questions(path: Optional[str]) -> List[Dict[str, str]]:
    if not path:
        return DEFAULT_QUESTIONS
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for item in data:
        out.append({"q": item, "expect": ""} if isinstance(item, str) else
                   {"q": item["q"], "expect": item.get("expect", "")})
    return out


async def run(args) -> int:
    from backend.db.session import SessionLocal
    from backend.services import agent_knowledge_vectors as vectors

    rep = Report()
    db = SessionLocal()
    try:
        agent, agents = pick_agent(db, args.agent)
        if agent is None:
            print("Укажите агента: --agent <часть имени или uuid>. Найдено:")
            for a in agents:
                print(f"  {a.id}  {a.name}")
            return 2
        if args.reindex:
            if not vectors.enabled():
                rep.warn("вектора", "Переиндексация пропущена: векторный поиск выключен")
            else:
                stats = await vectors.sync_agent(agent.id, force=True)
                db.expire_all()
                rep.ok("вектора", f"Переиндексация: проиндексировано {stats['indexed']}, убрано лишних "
                                  f"{stats['dropped']}, ошибок {stats['failed']}")
        check_knowledge(db, agent, rep)
        check_prompt(db, agent, args.caller, rep)
        await check_pinecone(agent, rep)
        await check_search(db, agent, load_questions(args.questions), rep)
    finally:
        db.close()

    if args.json:
        print(json.dumps({"agent": getattr(agent, "name", None), "items": rep.items}, ensure_ascii=False, indent=2))
    else:
        print_report(rep, agent.name)
    return 1 if rep.count("FAIL") else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Самопроверка голосового агента перед звонком")
    ap.add_argument("--agent", help="часть имени агента или его uuid")
    ap.add_argument("--questions", help="JSON-файл с вопросами [{q, expect}]")
    ap.add_argument("--caller", help="номер знакомого клиента: проверить худший случай (длинная история в промпте)")
    ap.add_argument("--reindex", action="store_true", help="заново проиндексировать записи для поиска по смыслу")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
