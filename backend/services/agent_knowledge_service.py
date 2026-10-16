"""
База знаний агента v2 (ветка 1410-kb-v2): сборка знаний в промпт и поиск.

Главное правило — **самый быстрый поиск тот, которого нет**. По логу звонка
владельца один вызов функции стоит ~4 секунды (голосовая модель идёт в
делегирующий бэкенд), а средняя задержка ответа и без функций 6,6 с. Поэтому
знания доходят до агента тремя слоями, от бесплатного к дорогому:

  1. ПРОМПТ (0 мс). Пары «вопрос — ответ» и короткие секции вшиваются в
     system prompt при старте сессии. Агент отвечает сразу, функция не
     вызывается вообще. Сюда попадает то, что спрашивают постоянно: цены,
     адрес, часы работы, условия.
  2. POSTGRES (единицы мс). Если в промпте ответа нет — ищем по заголовкам,
     ключевым словам и тексту записей обычным SQL. Без эмбеддингов, без
     сети. Сюда же каталог товаров: по цене и модели точное совпадение
     работает лучше векторов.
  3. PINECONE (сотни мс + эмбеддинг). Только документы (PDF/DOCX/XLSX), где
     ответ лежит внутри длинного текста. Единственный слой, который платит
     задержкой, — и он же самый редкий.

Нижние слои вызываются, только если верхние молчат, поэтому обычный вопрос
клиента до Pinecone вообще не доходит.
"""

import asyncio
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import or_

from backend.core.logging import get_logger
from backend.models.agent_knowledge import (
    KB_PROMPT_CHAR_BUDGET, KB_PROMPT_MAX_QA, AgentKnowledgeEntry, AgentKnowledgeGap,
)

logger = get_logger(__name__)

# Короткая секция целиком влезает в промпт; длинная идёт туда одним
# заголовком, а тело достаётся поиском.
SECTION_PROMPT_MAX_CHARS = 900

# Ниже этой близости кусок документа считаем мусором и не отдаём агенту.
# Раньше Pinecone всегда возвращал три куска, даже нерелевантных, и агент
# придумывал ответ по ним.
KB_MIN_SCORE = 0.3

# Слова, которые ничего не дают поиску (встречаются в любом вопросе).
_STOP_WORDS = {
    "и", "а", "в", "на", "с", "по", "за", "у", "к", "о", "об", "от", "до", "из",
    "для", "же", "ли", "не", "но", "что", "как", "это", "вы", "мы", "вас", "нам",
    "есть", "быть", "мне", "там", "тут", "ещё", "еще", "или", "да", "нет",
    "скажите", "подскажите", "пожалуйста", "хочу", "можно", "какой", "какая",
    "какие", "сколько", "где", "когда", "почему",
}

_WORD_RE = re.compile(r"[\w\-]{2,}", re.UNICODE)


# Как произносить валюту. Код «KGS» голосовая модель читает по буквам
# («кей-джи-эс»), поэтому в прайс и в ответы инструментов идёт слово.
_CURRENCY_WORDS = {"KGS": "сом", "RUB": "руб.", "USD": "долл.", "EUR": "евро", "KZT": "тенге"}


def normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def format_price(price, currency: Optional[str] = None) -> str:
    """
    Цена в виде, который модель произнесёт правильно: «84 990 сом».

    Разряды разделяем пробелом — иначе «84990» читается как «восемьдесят
    четыре тысячи девятьсот девяносто» через запинку, а то и по цифрам.
    """
    if price is None:
        return "цена по запросу"
    code = (currency or "KGS").upper()
    word = _CURRENCY_WORDS.get(code, code)
    whole = f"{price:,.0f}".replace(",", " ") if abs(price - round(price)) < 0.005 else f"{price:,.2f}".replace(",", " ")
    return f"{whole} {word}"


def keywords_of(query: str, limit: int = 6) -> List[str]:
    """Значимые слова запроса: по ним ищем в Postgres."""
    words = [w.lower() for w in _WORD_RE.findall(query or "")]
    out = [w for w in words if w not in _STOP_WORDS]
    # Длинные слова информативнее — их вперёд.
    out.sort(key=len, reverse=True)
    return out[:limit]


# ============================================================================
# СЛОЙ 1: ЗНАНИЯ В ПРОМПТЕ
# ============================================================================

def build_prompt_block(db, agent_config_id, budget: int = KB_PROMPT_CHAR_BUDGET) -> str:
    """
    Блок знаний для system prompt: пары «вопрос — ответ» и короткие секции.

    Укладывается в бюджет символов: сначала вопрос-ответ (их спрашивают чаще
    всего), потом секции по порядку. Что не влезло — упоминается заголовком,
    чтобы агент знал, что это у него есть, и мог достать поиском.
    """
    rows = (
        db.query(AgentKnowledgeEntry)
        .filter(
            AgentKnowledgeEntry.agent_config_id == agent_config_id,
            AgentKnowledgeEntry.is_active.is_(True),
            AgentKnowledgeEntry.in_prompt.is_(True),
            AgentKnowledgeEntry.kind.in_(("qa", "section")),
        )
        .order_by(AgentKnowledgeEntry.kind.desc(),  # qa раньше section
                  AgentKnowledgeEntry.position.asc(),
                  AgentKnowledgeEntry.created_at.asc())
        .all()
    )
    if not rows:
        return ""

    qa_lines: List[str] = []
    section_lines: List[str] = []
    deferred: List[str] = []          # заголовки того, что не влезло
    used = 0
    qa_count = 0

    for row in rows:
        title = (row.title or "").strip()
        body = (row.body or "").strip()
        if not title and not body:
            continue

        if row.kind == "qa":
            if qa_count >= KB_PROMPT_MAX_QA:
                deferred.append(title or body[:60])
                continue
            piece = f"В: {title}\nО: {body}"
        else:
            if len(body) > SECTION_PROMPT_MAX_CHARS:
                deferred.append(title)
                continue
            piece = f"## {title}\n{body}" if title else body

        if used + len(piece) > budget:
            deferred.append(title or body[:60])
            continue

        used += len(piece)
        if row.kind == "qa":
            qa_lines.append(piece)
            qa_count += 1
        else:
            section_lines.append(piece)

    if not qa_lines and not section_lines and not deferred:
        return ""

    parts = ["\n\n# ЗНАНИЯ О КОМПАНИИ (отвечай сразу, не вызывая поиск)"]
    if qa_lines:
        parts.append("\n## Частые вопросы и готовые ответы\n" + "\n\n".join(qa_lines))
    if section_lines:
        parts.append("\n" + "\n\n".join(section_lines))
    if deferred:
        titles = ", ".join(f"«{t}»" for t in deferred[:15] if t)
        if titles:
            parts.append(
                "\n## Есть ещё в базе (достань инструментом search_knowledge_base)\n"
                + titles
            )
    parts.append(
        "\nОтвечай по этим данным сразу — это самый быстрый путь. Поиск вызывай "
        "только если ответа здесь нет. Если его нет и в поиске — честно скажи, "
        "что уточнишь у специалиста, и не выдумывай."
    )
    return "\n".join(parts)


# ============================================================================
# СЛОЙ 2: БЫСТРЫЙ ПОИСК В POSTGRES
# ============================================================================

def _score(row: AgentKnowledgeEntry, words: List[str], query_norm: str) -> float:
    """
    Насколько запись подходит запросу. Простая и предсказуемая оценка:
    совпадение в заголовке весит больше, чем в тексте, точная фраза — больше
    отдельных слов. Для базы в сотни записей этого достаточно, зато считается
    мгновенно и объяснимо для владельца.
    """
    title = (row.title or "").lower()
    keywords = (row.keywords or "").lower()
    body = (row.body or "").lower()

    score = 0.0
    if query_norm and query_norm in title:
        score += 10.0
    if query_norm and query_norm in keywords:
        score += 6.0
    for w in words:
        if w in title:
            score += 3.0
        if w in keywords:
            score += 2.0
        if w in body:
            score += 1.0
    # Вопрос-ответ — готовая формулировка, её выдаём охотнее.
    if row.kind == "qa":
        score *= 1.2
    return score


def search_entries(
    db,
    agent_config_id,
    query: str,
    kinds: Optional[tuple] = None,
    limit: int = 4,
) -> List[AgentKnowledgeEntry]:
    """Быстрый поиск по записям базы (без эмбеддингов)."""
    words = keywords_of(query)
    query_norm = normalize(query)
    if not words and not query_norm:
        return []

    q = db.query(AgentKnowledgeEntry).filter(
        AgentKnowledgeEntry.agent_config_id == agent_config_id,
        AgentKnowledgeEntry.is_active.is_(True),
    )
    if kinds:
        q = q.filter(AgentKnowledgeEntry.kind.in_(kinds))

    # Отбираем кандидатов по любому значимому слову, ранжируем уже в Python:
    # база одного агента — сотни строк, это дешевле и гибче, чем SQL-рейтинг.
    patterns = []
    for w in words[:4]:
        like = f"%{w}%"
        patterns += [
            AgentKnowledgeEntry.title.ilike(like),
            AgentKnowledgeEntry.keywords.ilike(like),
            AgentKnowledgeEntry.body.ilike(like),
        ]
    if patterns:
        q = q.filter(or_(*patterns))

    rows = q.limit(60).all()
    scored = [(r, _score(r, words, query_norm)) for r in rows]
    scored = [(r, s) for r, s in scored if s > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [r for r, _ in scored[:limit]]


def search_products(
    db,
    agent_config_id,
    query: str = "",
    max_price: Optional[float] = None,
    min_price: Optional[float] = None,
    limit: int = 5,
) -> List[AgentKnowledgeEntry]:
    """
    Каталог: подбор позиций. Цена фильтруется в SQL — по числам векторный
    поиск врёт («до 20 000» он не понимает), а SQL отвечает точно.
    """
    q = db.query(AgentKnowledgeEntry).filter(
        AgentKnowledgeEntry.agent_config_id == agent_config_id,
        AgentKnowledgeEntry.kind == "product",
        AgentKnowledgeEntry.is_active.is_(True),
    )
    if max_price is not None:
        q = q.filter(AgentKnowledgeEntry.price <= max_price)
    if min_price is not None:
        q = q.filter(AgentKnowledgeEntry.price >= min_price)

    words = keywords_of(query)
    if words:
        patterns = []
        for w in words[:4]:
            like = f"%{w}%"
            patterns += [
                AgentKnowledgeEntry.title.ilike(like),
                AgentKnowledgeEntry.keywords.ilike(like),
                AgentKnowledgeEntry.body.ilike(like),
            ]
        q = q.filter(or_(*patterns))

    rows = q.limit(60).all()
    if not words:
        rows.sort(key=lambda r: (r.price if r.price is not None else 0))
        return rows[:limit]
    query_norm = normalize(query)
    scored = [(r, _score(r, words, query_norm)) for r in rows]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [r for r, _ in scored[:limit]]


def mark_hits(db, rows: List[AgentKnowledgeEntry]) -> None:
    """Отметить, что знание пригодилось — владелец видит, что реально работает."""
    now = datetime.utcnow()
    for row in rows:
        row.hit_count = (row.hit_count or 0) + 1
        row.last_hit_at = now


def record_gap(db, agent_config_id, query: str, channel: Optional[str] = None) -> None:
    """
    Запомнить вопрос, на который ответа не нашлось. Повторы складываем в одну
    строку: владельцу нужен список «что чаще всего спрашивают мимо базы».
    """
    query = (query or "").strip()[:500]
    if not query:
        return
    norm = normalize(query)[:500]
    try:
        row = (
            db.query(AgentKnowledgeGap)
            .filter(AgentKnowledgeGap.agent_config_id == agent_config_id,
                    AgentKnowledgeGap.query_norm == norm)
            .first()
        )
        if row is None:
            db.add(AgentKnowledgeGap(
                agent_config_id=agent_config_id, query=query, query_norm=norm,
                channel=channel,
            ))
        else:
            row.ask_count = (row.ask_count or 0) + 1
            row.last_asked_at = datetime.utcnow()
            row.resolved = False
            if channel:
                row.channel = channel
    except Exception as e:
        # Статистика не должна ронять ответ клиенту.
        logger.warning(f"[KB] пробел не записан: {e}")


# ============================================================================
# ОТВЕТ ПО БАЗЕ: СЛОИ ЦЕЛИКОМ (одна реализация на чат и на звонок)
# ============================================================================

def _answer_from_entries(db, agent_config_id, query: str, top_k: int) -> Optional[Dict[str, Any]]:
    """
    Слой Postgres: готовый ответ по записям базы или None, если не нашлось.
    Синхронный — вызывается через asyncio.to_thread, чтобы на звонке не
    задерживать звук (сетевой задержки здесь нет, но commit может ждать диск).
    """
    rows = search_entries(db, agent_config_id, query, limit=top_k)
    if not rows:
        return None
    mark_hits(db, rows)
    try:
        db.commit()
    except Exception:
        db.rollback()
    return {
        "ok": True, "query": query, "source": "knowledge_base",
        "total": len(rows),
        "results": [entry_for_agent(r) for r in rows],
    }


async def _answer_from_documents(namespace: str, query: str, top_k: int) -> Optional[Dict[str, Any]]:
    """
    Слой Pinecone: ответ по документам или None. Единственный слой, который
    платит сетью и эмбеддингом, поэтому зовётся последним.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not namespace or not api_key:
        return None
    from backend.services.pinecone_service import PineconeService
    try:
        matches = await PineconeService.search(
            query=query, namespace=namespace, api_key=api_key, top_k=top_k,
        )
    except Exception as e:
        logger.error(f"[KB] поиск по документам не удался: {e}", exc_info=True)
        return {"ok": False, "error": f"Ошибка поиска: {e}"}
    results = [
        {"text": m.get("text", ""), "score": m.get("score")}
        for m in matches if (m.get("score") or 0) >= KB_MIN_SCORE
    ]
    if not results:
        return None
    return {"ok": True, "query": query, "source": "documents",
            "total": len(results), "results": results}


async def answer_query(db, agent_config_id, kb_namespace: Optional[str], query: str,
                       top_k: int = 4, channel: Optional[str] = None) -> Dict[str, Any]:
    """
    Ответ из базы знаний слоями: записи (Postgres) → документы (Pinecone).

    Одна реализация для всех каналов: оркестратор в чате и мессенджерах
    (services/agent_tools.py) и голосовой агент на звонке
    (backend/functions/search_knowledge_base.py). Раньше на звонке работал
    только search_pinecone — он искал по документам и не видел ни пар
    «вопрос — ответ», ни секций, ни каталога, из-за чего агент на звонке
    отправлял клиента «к специалисту» при заполненной базе.

    Не нашлось нигде — пишем вопрос в пробелы базы: владелец увидит, чего
    клиентам не хватает, и дополнит базу по фактам, а не по догадкам.
    """
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "Пустой поисковый запрос"}

    if agent_config_id:
        try:
            fast = await asyncio.to_thread(_answer_from_entries, db, agent_config_id, query, top_k)
        except Exception as e:
            logger.warning(f"[KB] быстрый поиск не сработал: {e}")
            fast = None
        if fast:
            return fast

    docs = await _answer_from_documents(kb_namespace or "", query, top_k)
    if docs is not None:
        return docs

    if agent_config_id:
        def _gap():
            record_gap(db, agent_config_id, query, channel)
            try:
                db.commit()
            except Exception:
                db.rollback()
        try:
            await asyncio.to_thread(_gap)
        except Exception as e:
            logger.warning(f"[KB] пробел не записан: {e}")
    return {
        "ok": True, "query": query, "total": 0, "results": [],
        "note": "В базе знаний ответа нет. Не выдумывай: скажи клиенту, что "
                "уточнишь у специалиста, и предложи записать его вопрос.",
    }


async def answer_products(db, agent_config_id, query: str = "",
                          max_price: Optional[float] = None,
                          min_price: Optional[float] = None,
                          limit: int = 5, channel: Optional[str] = None) -> Dict[str, Any]:
    """
    Подбор позиций каталога. Общая реализация для чата и звонка.

    Цена фильтруется в SQL: «наушники до 3000» — это число, а по числам
    векторный поиск врёт.
    """
    if not agent_config_id:
        return {"ok": False, "error": "Агент не определён"}

    def _num(value):
        # Модель присылает цену и строкой («30000»), и числом — SQL нужно число.
        try:
            return float(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None

    lo, hi = _num(min_price), _num(max_price)

    def _work():
        rows = search_products(db, agent_config_id, query=query,
                               max_price=hi, min_price=lo,
                               limit=min(int(limit or 5), 10))
        if not rows:
            record_gap(db, agent_config_id, query, channel)
            try:
                db.commit()
            except Exception:
                db.rollback()
            return {"ok": True, "total": 0, "results": [],
                    "note": "Подходящих позиций в каталоге нет. Не придумывай товар — "
                            "предложи похожее из каталога или уточни запрос у клиента."}
        mark_hits(db, rows)
        try:
            db.commit()
        except Exception:
            db.rollback()
        return {"ok": True, "total": len(rows),
                "results": [entry_for_agent(r) for r in rows]}

    try:
        return await asyncio.to_thread(_work)
    except Exception as e:
        logger.error(f"[KB] подбор по каталогу не удался: {e}", exc_info=True)
        return {"ok": False, "error": f"Ошибка поиска: {e}"}


# ============================================================================
# ФОРМАТ ОТВЕТА ДЛЯ АГЕНТА
# ============================================================================

def entry_for_agent(row: AgentKnowledgeEntry) -> Dict[str, Any]:
    """
    Компактное представление записи. Короче — лучше: агент быстрее
    формулирует ответ, а на звонке это прямо влияет на задержку.
    """
    out: Dict[str, Any] = {"title": row.title, "text": row.body}
    if row.kind == "product":
        if row.price is not None:
            out["price"] = format_price(row.price, row.currency)
        if row.attributes:
            out["attributes"] = row.attributes
        out["in_stock"] = bool(row.in_stock)
        media = row.media_file_ids or []
        if media:
            # Агент отправит это клиенту: whatsapp_send_file / telegram_send_file.
            out["media_file_ids"] = media
    return out
