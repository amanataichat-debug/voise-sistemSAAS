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

# Жёсткий потолок на слой Pinecone (эмбеддинг + запрос к индексу). Без него
# повисший/медленный OpenAI или Pinecone висит неограниченно: на звонке
# 16.10.2026 это выглядело как «агент замолчал и не ответил за 2+ минуты» —
# function_call от GPT-Live не возвращался вообще, потому что исполнение
# функции само не возвращалось. Таймаут не чинит причину (см. pinecone_service),
# но гарантирует, что при сбое агент услышит «не нашлось» за разумное время,
# а не замолчит навсегда.
DOCS_SEARCH_TIMEOUT = 4.0

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


# ----------------------------------------------------------------------------
# ПОИСК, УСТОЙЧИВЫЙ К ОКОНЧАНИЯМ И СИНОНИМАМ (ветка 2510-voice-live)
# ----------------------------------------------------------------------------
# Звонок 21.10.2026: клиент спрашивал про «тестовую сим-карту», в базе это есть, а агент
# отвечал «нет». Прежний поиск искал подстроку целого слова: «тестовая» не находила
# «тестовый», «подключения» — «подключение», «симка» — «сим-карту». К тому же в
# Postgres уходили только 4 самых длинных слова запроса — а длинные слова как раз
# чаще всего склоняются. Теперь: основа слова = его начало (подходит и русскому, и
# кыргызскому — оба меняют слово с конца), совпадение только с начала слова, синонимы
# из словаря ниже, учитываются ВСЕ значимые слова запроса.

def stem(word: str) -> str:
    """Основа слова: его начало. «подключения» / «подключить» → «подкл», «цена» / «цены» → «цен»."""
    w = (word or "").lower().replace("ё", "е").strip("-")
    n = len(w)
    if n <= 3:
        return w
    if n == 4:
        return w[:3]
    if n <= 6:
        return w[:4]
    return w[:5]


_SYNONYM_GROUPS = [
    ["сим", "симка", "симки", "сим-карта", "сим-карты", "симкарта", "sim", "simka", "sim-карта", "esim"],
    ["кп", "коммерческое", "коммерческий", "предложение", "предложения", "kp"],
    ["цена", "цены", "цену", "стоимость", "стоимости", "тариф", "тарифы", "прайс", "расценки", "стоит",
     "оплата", "платить"],
    ["тест", "тестовый", "тестовая", "тестирование", "пробный", "пробная", "демо", "демонстрация", "trial",
     "бесплатный", "бесплатно"],
    ["каталог", "ассортимент", "товар", "товары", "продукция", "номенклатура", "позиции"],
    ["договор", "контракт", "оферта", "соглашение"],
    ["whatsapp", "ватсап", "вотсап", "ватсапп", "вацап", "воцап", "вотсапп"],
    ["telegram", "телеграм", "телеграмм", "телега", "тг"],
    ["instagram", "инстаграм", "инста", "инстаграмм"],
    ["подключение", "подключить", "подключиться", "подключаться", "регистрация", "зарегистрироваться",
     "запуск", "запустить", "начать", "старт"],
    ["документ", "документы", "бумаги", "справки", "реквизиты"],
    ["менеджер", "специалист", "оператор", "человек", "живой"],
    ["звонок", "звонки", "звонить", "позвонить", "обзвон", "телефония"],
]

_SYN_INDEX: Dict[str, frozenset] = {}
for _group in _SYNONYM_GROUPS:
    _stems = frozenset(stem(_w) for _w in _group)
    for _st in _stems:
        _SYN_INDEX[_st] = _SYN_INDEX.get(_st, frozenset()) | _stems

MAX_QUERY_TERMS = 10
MAX_SQL_ALTS = 40


def query_terms(query: str, limit: int = MAX_QUERY_TERMS) -> List[frozenset]:
    """
    Значимые слова запроса, каждое — набор допустимых основ (само слово + синонимы).
    Длинный запрос модели («Условия подключения к Voksy AI: какие документы нужны…»)
    даёт до 10 слов, а не 4: поиск не должен терять слова из конца фразы.
    """
    seen = set()
    terms: List[frozenset] = []
    words = [w.lower() for w in _WORD_RE.findall(query or "")]
    for w in sorted((x for x in words if x not in _STOP_WORDS), key=len, reverse=True):
        st = stem(w)
        if not st or st in seen:
            continue
        seen.add(st)
        terms.append(_SYN_INDEX.get(st, frozenset()) | {st})
        if len(terms) >= limit:
            break
    return terms


def _term_regexes(terms: List[frozenset]) -> list:
    return [re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in sorted(alts, key=len, reverse=True)) + ")")
            for alts in terms]


def _rank_row(row, rxs: list, query_norm: str):
    """(оценка, сколько слов запроса нашлось, было ли попадание в заголовок/ключевые слова)."""
    title = (row.title or "").lower()
    keywords = (row.keywords or "").lower()
    body = (row.body or "").lower()
    score, matched, strong = 0.0, 0, False
    for rx in rxs:
        hit = False
        if rx.search(title):
            score += 3.0
            hit = strong = True
        if rx.search(keywords):
            score += 2.0
            hit = strong = True
        n = len(rx.findall(body))
        if n:
            score += 1.0 + min(n - 1, 2) * 0.25
            hit = True
        if hit:
            matched += 1
    if query_norm and query_norm in title:
        score += 10.0
        strong = True
    if query_norm and query_norm in keywords:
        score += 6.0
        strong = True
    if rxs:
        # Запись, в которой нашлись почти все слова запроса, важнее той, где одно
        score *= 0.6 + matched / len(rxs)
    if getattr(row, "kind", None) == "qa":
        score *= 1.2  # вопрос-ответ — готовая формулировка, её выдаём охотнее
    return score, matched, strong


def rank_entries(rows: list, query: str, limit: int = 4):
    """
    Ранжирование записей под запрос. Возвращает (записи, качество):
    «ok» — есть уверенные совпадения (слово в заголовке/ключевых словах или ≥2 слов запроса),
    «weak» — только одиночное упоминание в тексте (идём дальше, в документы),
    «none» — не нашлось.
    """
    terms = query_terms(query)
    query_norm = normalize(query)
    if not terms:
        return [], "none"
    rxs = _term_regexes(terms)
    ranked = []
    for row in rows:
        score, matched, strong = _rank_row(row, rxs, query_norm)
        if score > 0:
            ranked.append((score, matched, strong, row))
    ranked.sort(key=lambda x: x[0], reverse=True)
    good = [r for sc, m, st, r in ranked if st or m >= 2]
    if good:
        return good[:limit], "ok"
    if ranked:
        return [r for _, _, _, r in ranked[:2]], "weak"
    return [], "none"


def _candidate_filter(terms: List[frozenset]):
    """SQL-условие «в записи есть хоть одна основа любого слова запроса» (подстрока; точнее отберёт rank)."""
    alts = sorted({a for t in terms for a in t if len(a) >= 2}, key=len, reverse=True)[:MAX_SQL_ALTS]
    patterns = []
    for a in alts:
        like = f"%{a}%"
        patterns += [
            AgentKnowledgeEntry.title.ilike(like),
            AgentKnowledgeEntry.keywords.ilike(like),
            AgentKnowledgeEntry.body.ilike(like),
        ]
    return or_(*patterns) if patterns else None


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

def search_entries_ranked(
    db,
    agent_config_id,
    query: str,
    kinds: Optional[tuple] = None,
    limit: int = 4,
):
    """Быстрый поиск по записям базы (без эмбеддингов). Возвращает (записи, качество: ok/weak/none)."""
    terms = query_terms(query)
    if not terms:
        return [], "none"

    q = db.query(AgentKnowledgeEntry).filter(
        AgentKnowledgeEntry.agent_config_id == agent_config_id,
        AgentKnowledgeEntry.is_active.is_(True),
    )
    if kinds:
        q = q.filter(AgentKnowledgeEntry.kind.in_(kinds))
    cond = _candidate_filter(terms)
    if cond is not None:
        q = q.filter(cond)

    # Кандидатов отбирает SQL по подстроке основы, точно ранжирует Python: база одного
    # агента — сотни строк, это дешевле и гибче, чем SQL-рейтинг.
    return rank_entries(q.limit(200).all(), query, limit)


def search_entries(
    db,
    agent_config_id,
    query: str,
    kinds: Optional[tuple] = None,
    limit: int = 4,
) -> List[AgentKnowledgeEntry]:
    """Записи базы под запрос (совместимая обёртка: только список)."""
    rows, _ = search_entries_ranked(db, agent_config_id, query, kinds, limit)
    return rows


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

    terms = query_terms(query)
    if terms:
        cond = _candidate_filter(terms)
        if cond is not None:
            q = q.filter(cond)

    rows = q.limit(200).all()
    if not terms:
        rows.sort(key=lambda r: (r.price if r.price is not None else 0))
        return rows[:limit]
    rxs = _term_regexes(terms)
    query_norm = normalize(query)
    scored = [(r, _rank_row(r, rxs, query_norm)[0]) for r in rows]
    scored = [(r, sc) for r, sc in scored if sc > 0]
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
    rows, quality = search_entries_ranked(db, agent_config_id, query, limit=top_k)
    if not rows:
        return None
    mark_hits(db, rows)
    try:
        db.commit()
    except Exception:
        db.rollback()
    out = {
        "ok": True, "query": query, "source": "knowledge_base",
        "total": len(rows),
        "results": [entry_for_agent(r) for r in rows],
    }
    if quality == "weak":
        out["weak"] = True
        out["note"] = ("Совпадение неточное — слова запроса встретились только вскользь. Проверь, что это "
                       "действительно про вопрос клиента, иначе скажи, что уточнишь.")
    return out


async def _answer_from_documents(namespace: str, query: str, top_k: int) -> Optional[Dict[str, Any]]:
    """
    Слой Pinecone: ответ по документам или None. Единственный слой, который
    платит сетью и эмбеддингом, поэтому зовётся последним.

    Обёрнут в asyncio.wait_for(DOCS_SEARCH_TIMEOUT): PineconeService.search
    теперь сам уходит в поток (см. pinecone_service.py — раньше эмбеддинг и
    запрос к индексу были синхронными HTTP-вызовами ПРЯМО в event loop и
    останавливали звук у всех звонков на этом воркере, а не только у этого),
    но сеть всё равно может зависнуть или отвечать очень долго — таймаут не
    даёт этому растянуться на минуты.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not namespace or not api_key:
        return None
    from backend.services.pinecone_service import PineconeService
    try:
        matches = await asyncio.wait_for(
            PineconeService.search(
                query=query, namespace=namespace, api_key=api_key, top_k=top_k,
            ),
            timeout=DOCS_SEARCH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[KB] поиск по документам не ответил за {DOCS_SEARCH_TIMEOUT}с "
                       f"(namespace={namespace}) — считаем, что не нашлось")
        return None
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
        # Уверенное совпадение отдаём сразу; слабое (слово встретилось вскользь) не должно
        # закрывать дорогу документам — сначала пробуем их.
        if fast and not fast.get("weak"):
            return fast

    docs = await _answer_from_documents(kb_namespace or "", query, top_k)
    if docs is not None:
        if fast:
            docs["related"] = fast["results"]
        return docs
    if fast:
        return fast

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
