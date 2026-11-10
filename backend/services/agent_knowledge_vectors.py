"""
Векторный слой записей базы знаний агента (ветка 2510-voice-live).

ЗАЧЕМ. До этого в Pinecone лежали только файлы-документы. Вопросы-ответы, секции «о компании»
и каталог ИСКАЛИСЬ ТОЛЬКО ПО СЛОВАМ в Postgres. На звонке 25.10.2026 клиент спросил про «тестовые
симки», а в каталоге это записано как «тестовые номера»: по буквам разное, по смыслу то же самое.
Поиск по словам вернул 0 записей, агент сказал «администратор подскажет». Векторный поиск находит
такие вещи по смыслу.

КАК УСТРОЕНО.
- Вектора записей лежат в ОТДЕЛЬНОМ namespace агента (`entries_namespace`), а не рядом с файлами:
  старые эндпоинты `POST/DELETE /knowledge-base` стирают весь namespace файлов целиком, и вектора
  записей пропали бы при любой правке старой базы.
- В вектор уходит заголовок + ключевые слова + текст (+ цена и характеристики у товара). Ответ
  агенту собирается из СТРОКИ БАЗЫ по entry_id, а не из текста вектора: правка записи сразу видна,
  даже если вектор ещё не обновился (устаревший вектор влияет только на порядок).
- Поиск: ОДИН эмбеддинг запроса → два запроса к индексу параллельно (записи + файлы). Результат
  сливается с поиском по словам (agent_knowledge_service._hybrid_answer).
- Любая ошибка или таймаут (`KB_VECTOR_TIMEOUT`) → `search` возвращает None, и вызывающий код
  работает по-старому (слова → файлы). Выключатель: `KB_VECTOR_ENABLED=0`.
- Состояние записи — `AgentKnowledgeEntry.vector_status` (pending | ready | failed | skipped).
  Правка записи ставит `pending` и запускает фоновую синхронизацию; редкая проверка при поиске
  (`maybe_backfill`) досинхронизирует то, что не успело (в том числе все старые записи).

Все синхронные вызовы SDK (OpenAI, Pinecone, БД) идут в потоках: синхронный HTTP прямо в async-коде
останавливает звук всех звонков воркера (так было на звонке 16.10.2026).
"""

import asyncio
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from backend.core.logging import get_logger

logger = get_logger(__name__)

INDEXED_KINDS = ("qa", "section", "product")
EMBED_MODEL = "text-embedding-3-small"   # тот же, что у файлов: индекс 1536, cosine
EMBED_BATCH = 64
MAX_TEXT_CHARS = 6000

# Ниже этой близости запись считаем нерелевантной (text-embedding-3-small: связанные тексты
# ≈ 0.40–0.65, несвязанные ≈ 0.10–0.25)
ENTRY_MIN_SCORE = float(os.environ.get("KB_ENTRY_MIN_SCORE", "0.32"))
VECTOR_TIMEOUT = float(os.environ.get("KB_VECTOR_TIMEOUT", "2.0"))
BACKFILL_EVERY_SEC = 120.0

_lock = threading.Lock()
_index_handle: Dict[str, Any] = {}
_openai_handle: Dict[str, Any] = {}
_last_backfill_check: Dict[str, float] = {}
_syncing: set = set()
_resync: set = set()      # правка пришла во время синхронизации — после неё запустить ещё одну
_bg_tasks: set = set()    # ссылки на фоновые задачи: без них сборщик мусора может убить задачу на лету


def spawn(coro) -> Optional["asyncio.Task"]:
    """Запустить фоновую задачу, удерживая ссылку на неё. Вне event loop — тихо ничего не делает."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return None
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def _uuids(values: Sequence[Any]) -> List[uuid.UUID]:
    """Строки/UUID → список UUID; некорректные пропускаем (метаданные вектора могли испортиться)."""
    out = []
    for v in values:
        try:
            out.append(v if isinstance(v, uuid.UUID) else uuid.UUID(str(v)))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


# ---------------------------------------------------------------------------
# Включено ли, имена, тексты
# ---------------------------------------------------------------------------
def enabled() -> bool:
    flag = os.environ.get("KB_VECTOR_ENABLED", "1").strip().lower() in ("1", "true", "yes")
    return bool(flag and os.environ.get("PINECONE_API_KEY") and os.environ.get("OPENAI_API_KEY"))


def entries_namespace(agent_config_id) -> str:
    """Namespace записей агента. Производный от id агента: не зависит от kb_namespace файлов."""
    return f"agent-{str(agent_config_id).replace('-', '')}-e"


def entry_text(row) -> str:
    """Текст записи для эмбеддинга: чем полнее, тем лучше находит по смыслу."""
    parts = [(row.title or "").strip()]
    keywords = (getattr(row, "keywords", None) or "").strip()
    if keywords:
        parts.append(f"Ключевые слова: {keywords}")
    body = (row.body or "").strip()
    if body:
        parts.append(body)
    if getattr(row, "kind", None) == "product":
        attrs = getattr(row, "attributes", None)
        if isinstance(attrs, dict) and attrs:
            parts.append("; ".join(f"{k}: {v}" for k, v in attrs.items()))
        if getattr(row, "price", None) is not None:
            parts.append(f"Цена: {row.price:g} {getattr(row, 'currency', None) or 'KGS'}")
    return "\n".join(p for p in parts if p)[:MAX_TEXT_CHARS]


# ---------------------------------------------------------------------------
# Клиенты (кэшируем: новое соединение на каждый запрос = +100–300 мс на звонке)
# ---------------------------------------------------------------------------
def _get_index():
    """Дескриптор индекса Pinecone. Создание делает лишний запрос describe_index — держим один."""
    with _lock:
        idx = _index_handle.get("index")
        if idx is None:
            import pinecone
            from backend.services.pinecone_service import PINECONE_INDEX_NAME
            pc = pinecone.Pinecone(api_key=os.environ.get("PINECONE_API_KEY"))
            idx = pc.Index(PINECONE_INDEX_NAME)
            _index_handle["index"] = idx
        return idx


def _reset_index() -> None:
    with _lock:
        _index_handle.pop("index", None)


def _openai():
    with _lock:
        cli = _openai_handle.get("client")
        if cli is None:
            import openai
            cli = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), timeout=3.0, max_retries=0)
            _openai_handle["client"] = cli
        return cli


def _embed_sync(texts: Sequence[str], timeout: float = 3.0) -> List[List[float]]:
    """Эмбеддинги пачкой (OpenAI принимает список) — для переиндексации это один запрос вместо N."""
    cli = _openai().with_options(timeout=timeout)
    out: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        chunk = [t or " " for t in texts[i:i + EMBED_BATCH]]
        resp = cli.embeddings.create(model=EMBED_MODEL, input=chunk)
        out.extend(d.embedding for d in resp.data)
    return out


def _norm_matches(response) -> List[Dict[str, Any]]:
    items = response.get("matches", []) if isinstance(response, dict) else (response.matches or [])
    out = []
    for m in items:
        get = m.get if isinstance(m, dict) else (lambda k, d=None, _m=m: getattr(_m, k, d))
        md = get("metadata", None) or {}
        out.append({"id": get("id"), "score": float(get("score", 0.0) or 0.0), "metadata": dict(md)})
    return out


async def warmup() -> None:
    """Создать клиентов заранее (при старте), чтобы первый поиск на звонке не платил за describe_index и TLS."""
    if not enabled():
        return
    try:
        await asyncio.to_thread(_get_index)
        await asyncio.to_thread(_openai)
        logger.info("[KB-VEC] клиенты Pinecone и OpenAI прогреты")
    except Exception as e:
        logger.warning(f"[KB-VEC] прогрев не удался (поиск создаст клиентов сам): {e}")


# ---------------------------------------------------------------------------
# Поиск
# ---------------------------------------------------------------------------
async def search(query: str, docs_namespace: Optional[str], agent_config_id,
                 top_k_entries: int = 8, top_k_docs: int = 3) -> Optional[Dict[str, Any]]:
    """
    Один эмбеддинг запроса → два запроса к индексу параллельно: записи агента и его файлы.

    Возвращает {"entries": [{entry_id, score}], "docs": [{text, score}], "ms": int} или None, если
    векторный слой недоступен (выключен, ошибка, таймаут) — тогда вызывающий код идёт по-старому.
    """
    if not query or not enabled():
        return None
    started = time.time()
    ens = entries_namespace(agent_config_id)

    async def _run() -> Dict[str, Any]:
        vec = (await asyncio.to_thread(_embed_sync, [query], 2.5))[0]

        def _q(namespace: str, k: int) -> List[Dict[str, Any]]:
            return _norm_matches(_get_index().query(vector=vec, namespace=namespace, top_k=k,
                                                    include_metadata=True))

        jobs = [asyncio.to_thread(_q, ens, top_k_entries)]
        if docs_namespace:
            jobs.append(asyncio.to_thread(_q, docs_namespace, top_k_docs))
        res = await asyncio.gather(*jobs, return_exceptions=True)

        failures = [r for r in res if isinstance(r, Exception)]
        if len(failures) == len(res):
            raise failures[0]
        entries_raw = res[0] if not isinstance(res[0], Exception) else []
        docs_raw = (res[1] if len(res) > 1 and not isinstance(res[1], Exception) else [])
        if failures:
            logger.warning(f"[KB-VEC] часть поиска не удалась: {failures[0]}")
        entries = [{"entry_id": m["metadata"].get("entry_id"), "score": m["score"]}
                   for m in entries_raw if m["metadata"].get("entry_id")]
        docs = [{"text": m["metadata"].get("text", ""), "score": m["score"]}
                for m in docs_raw if m["metadata"].get("text")]
        return {"entries": entries, "docs": docs}

    try:
        result = await asyncio.wait_for(_run(), VECTOR_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"[KB-VEC] векторный поиск не уложился в {VECTOR_TIMEOUT}с — идём по словам")
        return None
    except Exception as e:
        logger.warning(f"[KB-VEC] векторный поиск не удался ({type(e).__name__}: {e}) — идём по словам")
        _reset_index()  # дескриптор мог протухнуть
        return None
    result["ms"] = int((time.time() - started) * 1000)
    return result


def fuse_ranked(lists: Sequence[Sequence[Any]], k: int = 60) -> List[Any]:
    """
    Reciprocal Rank Fusion: слияние нескольких ранжированных списков в один. Запись, которую нашли
    и по словам, и по смыслу, поднимается наверх; одиночное попадание остаётся в выдаче.
    """
    scores: Dict[Any, float] = {}
    order: Dict[Any, int] = {}
    for lst in lists:
        for rank, key in enumerate(lst):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            order.setdefault(key, len(order))
    return sorted(scores, key=lambda key: (-scores[key], order[key]))


# ---------------------------------------------------------------------------
# Индексация
# ---------------------------------------------------------------------------
async def index_rows(agent_config_id, rows: Sequence[Dict[str, Any]]) -> int:
    """Залить записи (dict: id, kind, title, text) в namespace агента. Возвращает число векторов."""
    if not rows:
        return 0
    ens = entries_namespace(agent_config_id)
    vectors = await asyncio.to_thread(_embed_sync, [r["text"] for r in rows], 20.0)

    def _upsert():
        index = _get_index()
        batch = []
        for r, values in zip(rows, vectors):
            batch.append({
                "id": f"entry-{r['id']}",
                "values": values,
                "metadata": {"entry_id": str(r["id"]), "kind": r["kind"], "title": (r["title"] or "")[:200]},
            })
            if len(batch) >= 100:
                index.upsert(vectors=batch, namespace=ens)
                batch = []
        if batch:
            index.upsert(vectors=batch, namespace=ens)

    await asyncio.to_thread(_upsert)
    return len(rows)


async def delete_ids(agent_config_id, entry_ids: Sequence[str]) -> None:
    """Убрать вектора записей из поиска."""
    ids = [f"entry-{i}" for i in entry_ids]
    if not ids or not enabled():
        return
    ens = entries_namespace(agent_config_id)
    try:
        await asyncio.to_thread(lambda: _get_index().delete(ids=ids, namespace=ens))
    except Exception as e:
        logger.warning(f"[KB-VEC] вектора {len(ids)} шт. не удалены: {e}")


def _load_pending(agent_config_id, force: bool) -> Dict[str, Any]:
    """БД (в потоке, своя сессия): что индексировать, что убрать."""
    from backend.db.session import SessionLocal
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E

    snapshot_at = datetime.utcnow()  # правки позже этого момента не должны затираться статусом «готово»
    db = SessionLocal()
    try:
        rows = db.query(E).filter(E.agent_config_id == agent_config_id, E.kind.in_(INDEXED_KINDS)).all()
        todo, drop, skip = [], [], []
        for row in rows:
            text = entry_text(row)
            if not row.is_active or not text.strip():
                if row.vector_status != "skipped" or force:
                    drop.append(str(row.id))
                    skip.append(row.id)
                continue
            if force or row.vector_status in (None, "pending", "failed"):
                todo.append({"id": str(row.id), "kind": row.kind, "title": row.title, "text": text})
        return {"todo": todo, "drop": drop, "skip": skip, "all_ids": {str(r.id) for r in rows},
                "snapshot_at": snapshot_at}
    finally:
        db.close()


def _mark_status(ids: Sequence[Any], status: str, error: Optional[str] = None,
                 snapshot_at: Optional[datetime] = None) -> None:
    """
    Записать статус. snapshot_at — момент, на который была снята выборка: записи, изменённые ПОСЛЕ него
    (updated_at позже), не трогаем — они остаются «pending» и уйдут в следующий проход. Без этого
    правка во время синхронизации затиралась статусом «ready» и вектор оставался устаревшим.
    """
    from backend.db.session import SessionLocal
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E

    uids = _uuids(ids)
    if not uids:
        return
    db = SessionLocal()
    try:
        q = db.query(E).filter(E.id.in_(uids))
        if snapshot_at is not None:
            q = q.filter(E.updated_at <= snapshot_at)
        q.update({"vector_status": status, "vector_error": (error or "")[:500] or None}, synchronize_session=False)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[KB-VEC] статус векторизации не записан: {e}")
    finally:
        db.close()


async def sync_agent(agent_config_id, force: bool = False) -> Dict[str, int]:
    """
    Синхронизировать вектора записей агента с базой. force=True — переиндексировать всё и убрать
    вектора, которых в базе уже нет. Безопасно звать повторно и параллельно: второй вызов во время работы первого лишь помечает «нужен ещё проход».
    """
    key = str(agent_config_id)
    stats = {"indexed": 0, "dropped": 0, "failed": 0}
    if not enabled():
        return stats
    if key in _syncing:
        _resync.add(key)  # идёт синхронизация — после неё запустим ещё одну, чтобы свежая правка не ждала
        return stats
    _syncing.add(key)
    try:
        data = await asyncio.to_thread(_load_pending, agent_config_id, force)
        snap = data.get("snapshot_at")
        todo = data["todo"]
        for i in range(0, len(todo), 200):
            part = todo[i:i + 200]
            try:
                n = await index_rows(agent_config_id, part)
                await asyncio.to_thread(_mark_status, [r["id"] for r in part], "ready", None, snap)
                stats["indexed"] += n
            except Exception as e:
                stats["failed"] += len(part)
                logger.error(f"[KB-VEC] пачка из {len(part)} записей не проиндексирована: {e}")
                await asyncio.to_thread(_mark_status, [r["id"] for r in part], "failed",
                                        f"{type(e).__name__}: {e}", snap)
        if data["drop"]:
            await delete_ids(agent_config_id, data["drop"])
            await asyncio.to_thread(_mark_status, data["skip"], "skipped", None, snap)
            stats["dropped"] += len(data["drop"])
        if force:
            stats["dropped"] += await _drop_orphans(agent_config_id, data["all_ids"])
        if stats["indexed"] or stats["dropped"] or stats["failed"]:
            logger.info(f"[KB-VEC] агент {key}: проиндексировано {stats['indexed']}, убрано {stats['dropped']}, "
                        f"ошибок {stats['failed']}")
        return stats
    finally:
        _syncing.discard(key)
        if key in _resync:
            _resync.discard(key)
            spawn(sync_agent(agent_config_id))


async def _drop_orphans(agent_config_id, db_ids: set) -> int:
    """Убрать вектора записей, которых в базе уже нет (переиндексация с force)."""
    ens = entries_namespace(agent_config_id)

    def _run() -> List[str]:
        index = _get_index()
        orphans: List[str] = []
        for page in index.list(namespace=ens):
            for vid in page:
                if str(vid).startswith("entry-") and str(vid)[6:] not in db_ids:
                    orphans.append(str(vid))
        if orphans:
            index.delete(ids=orphans, namespace=ens)
        return orphans

    try:
        return len(await asyncio.to_thread(_run))
    except Exception as e:
        logger.info(f"[KB-VEC] чистка лишних векторов пропущена: {e}")
        return 0


def schedule_sync(agent_config_id, force: bool = False) -> None:
    """Запустить синхронизацию в фоне (после правки записи). Ошибки уходят в журнал, не клиенту."""
    if not enabled():
        return

    async def _guard():
        try:
            await sync_agent(agent_config_id, force=force)
        except Exception as e:
            logger.error(f"[KB-VEC] фоновая синхронизация не удалась: {e}", exc_info=True)

    spawn(_guard())  # вне event loop (скрипт) — ничего не делает: вызывающий сам ждёт sync_agent


def schedule_delete(agent_config_id, entry_ids: Sequence[str]) -> None:
    if not enabled() or not entry_ids:
        return
    spawn(delete_ids(agent_config_id, list(entry_ids)))


def _count_unsynced(agent_config_id) -> int:
    from backend.db.session import SessionLocal
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E

    db = SessionLocal()
    try:
        return (db.query(E.id)
                .filter(E.agent_config_id == agent_config_id, E.kind.in_(INDEXED_KINDS), E.is_active.is_(True),
                        (E.vector_status.is_(None)) | (E.vector_status.in_(("pending", "failed"))))
                .count())
    finally:
        db.close()


async def maybe_backfill(agent_config_id) -> None:
    """
    Редкая проверка при поиске (не чаще раза в BACKFILL_EVERY_SEC на агента): есть ли записи без
    вектора? Если да — досинхронизировать в фоне. Так старые записи и пропущенные правки
    подтягиваются сами, без ручных скриптов.
    """
    if not enabled():
        return
    key = str(agent_config_id)
    now = time.time()
    if now - _last_backfill_check.get(key, 0.0) < BACKFILL_EVERY_SEC:
        return
    _last_backfill_check[key] = now
    try:
        if await asyncio.to_thread(_count_unsynced, agent_config_id):
            schedule_sync(agent_config_id)
    except Exception as e:
        logger.warning(f"[KB-VEC] проверка синхронизации не удалась: {e}")
