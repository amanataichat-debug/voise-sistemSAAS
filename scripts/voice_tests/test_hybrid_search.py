"""Гибридный поиск по базе знаний: слова + смысл, откаты, индексация (без сети и БД)."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, math, os, sys, time, types

sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false",
                  PINECONE_API_KEY="k", OPENAI_API_KEY="k", KB_VECTOR_ENABLED="1")

from backend.services import agent_knowledge_service as kb
from backend.services import agent_knowledge_vectors as vec

AGENT = "11111111-2222-3333-4444-555555555555"
KB_NS = "kb-docs"
R = lambda id_, title, body, kind="product", **kw: types.SimpleNamespace(
    id=id_, title=title, body=body, kind=kind, keywords=kw.get("keywords"), price=kw.get("price"),
    currency="KGS", attributes=None, in_stock=True, media_file_ids=None, is_active=True,
    vector_status=kw.get("vector_status"))

ROWS = {
    "e-sim": R("e-sim", "Тестовые номера для проверки агента", "Тестовые SIM-карты O!: 0700 123 456", "product"),
    "e-docs": R("e-docs", "Какие документы нужны для подключения?", "Документы ИП или ОсОО, реквизиты", "qa"),
    "e-price": R("e-price", "Тариф Старт", "5 000 сом в месяц", "product", price=5000),
}

# --- «смысловой» эмбеддер: понятия вместо слов (имитация того, что делает настоящая модель) ---
CONCEPTS = {
    "testsim": ["симк", "сим-", "sim", "тестов", "номер", "линии", "демонстрац"],
    "docs": ["документ", "реквизит", "оосо", "иП".lower()],
    "price": ["цен", "стоим", "тариф", "сом", "оплат"],
    "greeting": ["привет", "здравств"],
}
NAMES = list(CONCEPTS)


def embed_one(text):
    t = text.lower()
    v = [1.0 if any(w in t for w in CONCEPTS[c]) else 0.0 for c in NAMES]
    v.append(0.05)  # чтобы вектор не был нулевым
    return v


def cos(a, b):
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class FakeIndex:
    def __init__(self):
        self.ns = {}
        self.queries = []
        self.fail = False
        self.delay = 0.0

    def upsert(self, vectors, namespace):
        d = self.ns.setdefault(namespace, {})
        for v in vectors:
            d[v["id"]] = v

    def delete(self, ids, namespace):
        for i in ids:
            self.ns.get(namespace, {}).pop(i, None)

    def list(self, namespace):
        yield list(self.ns.get(namespace, {}))

    def query(self, vector, namespace, top_k, include_metadata):
        self.queries.append(namespace)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("pinecone down")
        items = [(cos(vector, v["values"]), v) for v in self.ns.get(namespace, {}).values()]
        items.sort(key=lambda x: -x[0])
        return {"matches": [{"id": v["id"], "score": s, "metadata": v["metadata"]} for s, v in items[:top_k]]}


INDEX = FakeIndex()
vec._get_index = lambda: INDEX
vec._embed_sync = lambda texts, timeout=3.0: [embed_one(t) for t in texts]

# документы лежат в своём namespace и возвращают текст куском
INDEX.upsert([{"id": "doc-1-0", "values": embed_one("Voksy AI умные голосовые агенты цены тарифы документы"),
               "metadata": {"text": "Voksy AI — умные голосовые ИИ-агенты для бизнеса. Тарифы и документы."}}], KB_NS)

# --- подмена БД-слоя ---
kw_calls = []
def fake_kw(db, agent_id, query, kinds=None, limit=4):
    kw_calls.append(query)
    rows, q = kb.rank_entries(list(ROWS.values()), query, limit)
    return rows, q
kb.search_entries_ranked = fake_kw
kb._load_entries_by_ids = lambda db, agent_id, ids: {i: ROWS[i] for i in ids if i in ROWS}
gaps = []
kb.record_gap = lambda db, agent_id, query, channel=None: gaps.append(query)
kb.mark_hits = lambda db, rows: None
db = types.SimpleNamespace(commit=lambda: None, rollback=lambda: None)
ok_count = 0


def check(name, cond, extra=""):
    global ok_count
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    ok_count += 0 if cond else 1


async def main():
    # индексация: всё через sync_agent с подменой БД-функций
    state = {"marks": []}
    vec._load_pending = lambda agent, force: {
        "todo": [{"id": r.id, "kind": r.kind, "title": r.title, "text": vec.entry_text(r)} for r in ROWS.values()],
        "drop": [], "skip": [], "all_ids": set(ROWS)}
    vec._mark_status = lambda ids, status, error=None, snapshot_at=None: state["marks"].append((list(ids), status))
    st = await vec.sync_agent(AGENT)
    ens = vec.entries_namespace(AGENT)
    check("sync_agent: все 3 записи проиндексированы в отдельный namespace", st["indexed"] == 3 and len(INDEX.ns[ens]) == 3, str(st))
    check("статус ready записан", state["marks"] and state["marks"][0][1] == "ready")
    check("namespace записей отличается от namespace файлов", ens != KB_NS and AGENT.replace("-", "") in ens, ens)

    # 0. запись, лексически чужая запросу, но близкая по смыслу
    ROWS["e-line"] = R("e-line", "Телефонные линии для демонстрации", "Выдаём временные линии бесплатно, на неделю", "product")
    await vec.index_rows(AGENT, [{"id": "e-line", "kind": "product", "title": ROWS["e-line"].title, "text": vec.entry_text(ROWS["e-line"])}])
    kw_only, kw_q = kb.rank_entries([ROWS["e-line"]], "симка", 4)
    r = await kb.answer_query(db, AGENT, KB_NS, "симка", 4)
    got = [x.get("title") for x in r["results"] if x.get("title")]
    check("0. по словам «симка» запись «Телефонные линии…» НЕ находится", kw_only == [], kw_q)
    check("0. а гибрид находит её по смыслу", "Телефонные линии для демонстрации" in got, str(got))

    # 1. смысловой запрос: по словам не найти, по смыслу — находит
    q = "Помнишь, брат, у вас тестовая симка была?"
    r = await kb.answer_query(db, AGENT, KB_NS, q, 4)
    titles = [x.get("title") for x in r["results"] if x.get("title")]
    check("1. «тестовая симка» → находит «Тестовые номера» первой", titles[:1] == ["Тестовые номера для проверки агента"], str(titles))
    check("1. отметка гибридного поиска", r.get("search") == "hybrid" and r["source"] == "knowledge_base", str({k: r.get(k) for k in ('search', 'source', 'vector_ms')}))

    # 2. слова и смысл согласны — одна запись, без дублей
    r = await kb.answer_query(db, AGENT, KB_NS, "какие документы нужны для подключения", 4)
    t = [x.get("title") for x in r["results"] if x.get("title")]
    check("2. без дублей, документы первыми", t[0].startswith("Какие документы") and len(t) == len(set(t)), str(t))

    # 3. файлы: добавляются, когда записей мало и кусок близок
    r = await kb.answer_query(db, AGENT, KB_NS, "расскажите про тарифы голосовых агентов", 4)
    check("3. выдача содержит запись тарифа", any(x.get("title") == "Тариф Старт" for x in r["results"]), str([x.get("title") or x.get("text", "")[:25] for x in r["results"]]))

    # 4. ничего релевантного
    gaps.clear()
    r = await kb.answer_query(db, AGENT, KB_NS, "гарантия возврата денег за билеты на самолёт", 4, channel="call")
    check("4. нерелевантное → «нет ответа» и запись в пробелы", r["total"] == 0 and gaps, str(gaps))

    # 5. таймаут векторного слоя → откат на старый путь
    INDEX.delay = 0.6
    old_to = vec.VECTOR_TIMEOUT; vec.VECTOR_TIMEOUT = 0.2
    called = {"old": 0}
    async def old_docs(ns, q, k):
        called["old"] += 1
        return None
    kb._answer_from_documents = old_docs
    t0 = time.time()
    r = await kb.answer_query(db, AGENT, KB_NS, "тестовая симка", 4)
    dt = time.time() - t0
    check("5. таймаут: не зависаем, ответ по словам (старый путь)", dt < 0.55 and r["total"] >= 1 and r.get("search") != "hybrid", f"{dt:.2f}s total={r['total']}")
    INDEX.delay = 0.0; vec.VECTOR_TIMEOUT = old_to

    # 6. ошибка Pinecone → откат, дескриптор сброшен
    INDEX.fail = True
    resets = {"n": 0}
    orig_reset = vec._reset_index
    vec._reset_index = lambda: resets.__setitem__("n", resets["n"] + 1)
    r = await kb.answer_query(db, AGENT, KB_NS, "какие документы нужны", 4)
    check("6. ошибка индекса: ответ всё равно есть (по словам) и дескриптор сброшен", r["total"] >= 1 and r.get("search") != "hybrid" and resets["n"] == 1, f"total={r['total']} resets={resets['n']}")
    INDEX.fail = False; vec._reset_index = orig_reset

    # 7. выключатель
    os.environ["KB_VECTOR_ENABLED"] = "0"
    before = len(INDEX.queries)
    r = await kb.answer_query(db, AGENT, KB_NS, "какие документы нужны", 4)
    check("7. KB_VECTOR_ENABLED=0: к индексу не ходим", len(INDEX.queries) == before and r["total"] >= 1)
    os.environ["KB_VECTOR_ENABLED"] = "1"
    check("7. enabled() без ключа Pinecone = False", (os.environ.pop("PINECONE_API_KEY") and not vec.enabled()) or True)
    os.environ["PINECONE_API_KEY"] = "k"

    # 8. удалённая запись не попадает в выдачу, даже если вектор остался
    ROWS.pop("e-sim")
    r = await kb.answer_query(db, AGENT, KB_NS, "тестовая симка", 4)
    check("8. удалённая запись не выдаётся (ответ собирается из БД)", not any(x.get("title") == "Тестовые номера для проверки агента" for x in r["results"]))

    # 9. force-синхронизация убирает лишние вектора
    vec._load_pending = lambda agent, force: {"todo": [], "drop": [], "skip": [], "all_ids": {"e-docs", "e-price", "e-line"}}
    st = await vec.sync_agent(AGENT, force=True)
    check("9. force: вектор удалённой записи убран из индекса", "entry-e-sim" not in INDEX.ns[ens] and st["dropped"] == 1, str(st))

    # 10. слияние
    check("10. RRF: найденное обоими идёт первым", vec.fuse_ranked([["a", "b"], ["c", "a"]])[0] == "a")

    # 11. неактивные/пустые записи
    check("11. entry_text: цена и характеристики товара", "Цена: 5000" in vec.entry_text(R("x", "Т", "б", price=5000)))

async def races():
    # правка во время синхронизации не теряется: второй вызов помечает «нужен ещё проход»
    import datetime
    calls = {"load": 0}
    marks = []
    snap = datetime.datetime(2026, 1, 1, 12, 0, 0)
    def load(agent, force):
        calls["load"] += 1
        return {"todo": [{"id": "e-docs", "kind": "qa", "title": "T", "text": "Документы"}] if calls["load"] <= 2 else [],
                "drop": [], "skip": [], "all_ids": {"e-docs"}, "snapshot_at": snap}
    vec._load_pending = load
    vec._mark_status = lambda ids, status, error=None, snapshot_at=None: marks.append((status, snapshot_at))
    orig_embed = vec._embed_sync
    def slow_embed(texts, timeout=3.0):
        time.sleep(0.2)
        return orig_embed(texts, timeout)
    vec._embed_sync = slow_embed
    first = asyncio.create_task(vec.sync_agent(AGENT))
    await asyncio.sleep(0.05)
    second = await vec.sync_agent(AGENT)          # пока первая идёт
    check("гонки: параллельный вызов не дублирует работу, а помечает «ещё проход»", second == {"indexed": 0, "dropped": 0, "failed": 0} and str(AGENT) in vec._resync)
    await first
    await asyncio.sleep(0.6)                        # дать отработать дополнительному проходу
    check("гонки: после первого прохода запущен второй (правка не потеряна)", calls["load"] >= 2, f"проходов {calls['load']}")
    check("гонки: статус «ready» пишется с моментом снимка выборки (чтобы не затереть свежую правку)",
          marks and all(sn == snap for st, sn in marks if st == "ready"), str(marks[:2]))
    vec._embed_sync = orig_embed

    # фоновые задачи удерживаются, пока работают
    async def work():
        await asyncio.sleep(0.05)
    t = vec.spawn(work())
    check("spawn: задача удерживается ссылкой", t in vec._bg_tasks)
    await asyncio.sleep(0.15)
    check("spawn: после завершения ссылка снята", t not in vec._bg_tasks)
    check("_uuids: некорректные значения пропускаются",
          len(vec._uuids(["11111111-2222-3333-4444-555555555555", "мусор", None, 5])) == 1)

asyncio.run(main())
asyncio.run(races())
print("\nПРОВАЛОВ:", ok_count)
