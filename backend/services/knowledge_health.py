"""
Проверка базы знаний агента — кнопка в кабинете для любого клиента (10.11.2026).

Владелец: «проверь абсолютно, что агент реально читает базу знаний: вопросы-ответы, каталог, о компании,
документы, PDF — и находит быстро во время разговора». Раньше это делал только scripts/voice_selfcheck.py из
Render Shell и с вопросами про наш собственный бизнес («тестовая симка»). Теперь проверка общая:

  * вопросами служат СОБСТВЕННЫЕ записи агента: по заголовку каждой записи поиск должен найти именно её —
    тем же путём, что на звонке (answer_query с channel="call", гибрид слова + смысл, быстрый путь);
    позиции каталога — через answer_products, как find_products;
  * что из вопросов-ответов не влезло в «ЧТО ТЫ ЗНАЕШЬ НАИЗУСТЬ» на звонке и в промпт переписки (такие ответы
    агент достаёт поиском — это дольше);
  * файлы, внутри которых агент не может искать (текст не извлечён), позиции без цены и фото;
  * поиск по смыслу: включён ли, все ли записи проиндексированы, сколько отвечает;
  * инструкции: незаполненный шаблон, слишком длинная инструкция звонков.

Проверка НИЧЕГО не пишет в базу: поиски идут в отдельных сессиях, где commit заменён на flush, а в конце —
rollback, поэтому самопроверка не попадает ни в «Чего не хватает», ни в счётчики «пригодилось».
"""

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

MAX_QA_QUESTIONS = 30        # сколько вопросов-ответов проверяем поиском (по порядку в базе)
MAX_SECTION_QUESTIONS = 4
MAX_PRODUCT_QUESTIONS = 4
CONCURRENCY = 6              # поиски параллельно: 40 запросов ≈ 3–8 с вместо 30–40
SLOW_MS = 1500               # на звонке дольше — клиент слышит паузу


def _check(level: str, area: str, text: str, items: Optional[List[str]] = None) -> Dict[str, Any]:
    out = {"level": level, "area": area, "text": text}
    if items:
        out["items"] = items[:8]
    return out


def _dry_session(factory: Callable):
    """Сессия, которая ничего не сохранит: commit → flush, в конце rollback (см. run_health)."""
    s = factory()
    s.commit = s.flush  # type: ignore[assignment]
    return s


def _found(result: Dict[str, Any], title: str) -> bool:
    t = (title or "").strip().lower()
    return any((r.get("title") or "").strip().lower() == t for r in (result.get("results") or []))


async def _search_one(factory, agent, kind: str, title: str, sem: asyncio.Semaphore) -> Dict[str, Any]:
    from backend.services import agent_knowledge_service as kb

    async with sem:
        s = _dry_session(factory)
        started = time.monotonic()
        try:
            if kind == "product":
                res = await kb.answer_products(s, agent.id, query=title, limit=5, channel="selfcheck")
            else:
                res = await kb.answer_query(s, agent.id, getattr(agent, "kb_namespace", None), title, top_k=4,
                                            channel="call")
            ok = _found(res, title)
            error = None if res.get("ok", True) else res.get("error")
        except Exception as e:  # сбой поиска — это и есть находка проверки
            res, ok, error = {}, False, f"{type(e).__name__}: {e}"
        finally:
            ms = int((time.monotonic() - started) * 1000)
            try:
                s.rollback()
            except Exception:
                pass
            s.close()
    return {"kind": kind, "q": title, "found": ok, "ms": ms, "mode": res.get("search") if res else None,
            "top": ((res.get("results") or [{}])[0].get("title") if res else None), "error": error}


async def run_health(db, agent, session_factory: Optional[Callable] = None) -> Dict[str, Any]:
    """Полная проверка базы знаний агента. db — сессия запроса (только чтение), session_factory — для поисков."""
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E
    from backend.services import agent_knowledge_service as kb
    from backend.services import agent_knowledge_vectors as vectors
    from backend.services import agent_voice_context as avc

    if session_factory is None:
        from backend.db.session import SessionLocal as session_factory  # noqa: N813

    started = time.monotonic()
    checks: List[Dict[str, Any]] = []
    rows = (db.query(E).filter(E.agent_config_id == agent.id, E.is_active.is_(True))
            .order_by(E.position.asc(), E.created_at.asc()).all())
    by_kind: Dict[str, List[Any]] = {}
    for r in rows:
        by_kind.setdefault(r.kind, []).append(r)
    qa, sections, products, docs = (by_kind.get(k, []) for k in ("qa", "section", "product", "document"))
    counts = {"qa": len(qa), "section": len(sections), "product": len(products), "document": len(docs)}

    if not rows:
        checks.append(_check("fail", "База", "База знаний пуста: агенту нечем отвечать. Добавьте вопросы и ответы, "
                                             "раздел о компании и прайс."))
        return {"ok": False, "counts": counts, "checks": checks, "searches": [], "seconds": 0.0}
    checks.append(_check("ok", "База", f"Вопросов-ответов {len(qa)}, разделов о компании {len(sections)}, "
                                      f"позиций каталога {len(products)}, файлов-источников {len(docs)}."))
    if not qa:
        checks.append(_check("warn", "База", "Нет ни одной пары «вопрос — ответ». Это самый быстрый способ научить "
                                             "агента: частые вопросы клиентов с готовыми ответами (цена, адрес, "
                                             "график, доставка, оплата)."))

    # ── что агент знает наизусть на звонке и в переписке ──
    try:
        block, stats = avc.build_voice_knowledge_block(db, agent)
        missing_call = [r.title for r in qa if r.in_prompt and (r.title or "") not in block]
        if missing_call:
            checks.append(_check("warn", "Звонок", f"Не влезли в «наизусть» на звонке ({len(missing_call)}) — "
                                                   "агент найдёт их поиском, но с паузой. Сократите длинные ответы "
                                                   "или выключите редкие вопросы.", missing_call))
        else:
            checks.append(_check("ok", "Звонок", f"Все вопросы-ответы агент знает наизусть на звонке "
                                                 f"({stats.get('total', 0)} симв. знаний в промпте)."))
        chat_block = kb.build_prompt_block(db, agent.id)
        missing_chat = [r.title for r in qa if r.in_prompt and (r.title or "") not in chat_block]
        if missing_chat:
            checks.append(_check("warn", "Переписка", f"Не влезли в промпт переписки ({len(missing_chat)}) — "
                                                      "агент найдёт их поиском.", missing_chat))
    except Exception as e:
        db.rollback()
        checks.append(_check("warn", "Звонок", f"Не удалось собрать знания звонка: {e}"))

    # ── файлы и каталог ──
    empty_docs = [r.title for r in docs if not (r.chunk_count or 0) or r.vector_status == "failed"]
    if empty_docs:
        checks.append(_check("fail", "Файлы", "Внутри этих файлов агент искать не может: текст не извлёкся (скан без "
                                              "распознавания, пустой файл или ошибка индексации). Загрузите заново или "
                                              "текстовую версию.", empty_docs))
    elif docs:
        checks.append(_check("ok", "Файлы", f"Во всех {len(docs)} файлах агент ищет ответы."))
    if products:
        no_price = [r.title for r in products if r.price is None]
        no_photo = [r.title for r in products if not r.media_file_ids and not (r.attributes or {}).get("_photo_url")]
        if no_price:
            checks.append(_check("warn", "Каталог", f"Позиции без цены ({len(no_price)}): на «сколько стоит» агент "
                                                    "скажет «цена по запросу».", no_price))
        if no_photo and len(no_photo) == len(products):
            checks.append(_check("warn", "Каталог", "Ни у одной позиции нет фото — агенту нечего показать клиенту."))
        elif no_photo:
            checks.append(_check("warn", "Каталог", f"Позиции без фото ({len(no_photo)}): агенту нечего показать клиенту.", no_photo))
        if not no_price and len(no_photo) < len(products):
            checks.append(_check("ok", "Каталог", f"Позиций в каталоге: {len(products)}, у всех есть цена."))

    # ── поиск по смыслу ──
    if not vectors.enabled():
        checks.append(_check("warn", "Поиск по смыслу", "Выключен: агент ищет только по словам. Перефразированные "
                                                        "вопросы клиентов находятся хуже."))
    else:
        indexed = [r for r in rows if r.kind in vectors.INDEXED_KINDS]
        unsynced = [r.title for r in indexed if r.vector_status != "ready"]
        t0 = time.monotonic()
        try:
            probe = await vectors.search("проверка связи", getattr(agent, "kb_namespace", None), agent.id)
        except Exception:
            probe = None
        ms = int((time.monotonic() - t0) * 1000)
        if probe is None:
            checks.append(_check("fail", "Поиск по смыслу", "Не отвечает (ошибка или таймаут) — на звонке будет только "
                                                            "поиск по словам."))
        elif unsynced:
            checks.append(_check("warn", "Поиск по смыслу", f"Ещё не проиндексированы ({len(unsynced)}) — по смыслу "
                                                            "они пока не находятся (досинхронизируются сами за пару "
                                                            "минут).", unsynced))
        else:
            checks.append(_check("ok", "Поиск по смыслу", f"Все {len(indexed)} записей проиндексированы, отвечает за {ms} мс."))

    # ── инструкции ──
    for warning in avc.instruction_warnings(agent):
        checks.append(_check("warn", "Инструкции", warning))

    # ── самопроверка поиска: каждую запись — по её же вопросу ──
    plan = ([("qa", r.title) for r in qa[:MAX_QA_QUESTIONS]]
            + [("section", r.title) for r in sections[:MAX_SECTION_QUESTIONS]]
            + [("product", r.title) for r in products[:MAX_PRODUCT_QUESTIONS]])
    plan = [(k, t) for k, t in plan if (t or "").strip()]
    sem = asyncio.Semaphore(CONCURRENCY)
    searches = await asyncio.gather(*[_search_one(session_factory, agent, k, t, sem) for k, t in plan])
    missed = [s for s in searches if not s["found"]]
    slow = [s for s in searches if s["found"] and s["ms"] > SLOW_MS]
    if searches and not missed:
        checks.append(_check("ok", "Поиск", f"Агент нашёл все {len(searches)} проверенных записей по их вопросам."))
    if missed:
        checks.append(_check("fail", "Поиск", f"Агент не находит по их же вопросу ({len(missed)}) — добавьте в них "
                                              "ключевые слова (как спрашивают клиенты) или переформулируйте вопрос.",
                             [f"{s['q']}" + (f" → нашлось «{s['top']}»" if s.get("top") else " → ничего") for s in missed]))
    if slow:
        checks.append(_check("warn", "Поиск", f"Поиск дольше {SLOW_MS / 1000:.1f} с ({len(slow)}) — на звонке клиент "
                                              "услышит паузу. Частые вопросы лучше держать в «вопрос — ответ».",
                             [f"{s['q']} — {s['ms']} мс" for s in slow]))

    worst = "fail" if any(c["level"] == "fail" for c in checks) else (
        "warn" if any(c["level"] == "warn" for c in checks) else "ok")
    return {"ok": worst != "fail", "status": worst, "counts": counts, "checks": checks, "searches": searches,
            "seconds": round(time.monotonic() - started, 1)}
