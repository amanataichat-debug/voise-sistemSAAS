"""Быстрый ответ базы знаний на звонке (10.11.2026) — без сети и БД.

Звонок af6e4d6a: поиск по базе шёл 2,5 и 1,3 с — гибридный поиск ждал векторную часть, даже когда по словам уже
нашёлся точный ответ. Теперь на звонке при УВЕРЕННОМ совпадении по словам векторы ждём не дольше FAST_VECTOR_GRACE.
Проверяем: уверенность — только на однозначных вопросах и всегда на верной записи; на звонке ответ приходит сразу;
в переписке и при неуверенном совпадении поиск ждёт векторы, как раньше.
"""
import asyncio
import json
import logging
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")
logging.disable(logging.CRITICAL)

from backend.services import agent_knowledge_service as kb
from backend.services import agent_knowledge_vectors as vectors

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


facts = json.load(open(os.path.join(ROOT, "docs/anri_knowledge.json"), encoding="utf-8"))["entries"]
ROWS = [types.SimpleNamespace(id=f"e{i}", title=e["title"], body=e["body"], keywords=e.get("keywords"), kind="qa",
                              attributes=None, price=None, currency=None, in_stock=True, media_file_ids=None)
        for i, e in enumerate(facts)]


def confident(q):
    rows, quality = kb.rank_entries(ROWS, q, 4)
    return quality == "ok" and kb.keyword_confident(rows, q), (rows[0].title if rows else None)


# ── уверенность: только однозначно и только верная запись ──
for q, title in (("сколько стоит минута звонка", "Сколько стоит минута звонка?"),
                 ("Агент говорит по-кыргызски?", "Агент говорит по-кыргызски?"),
                 ("Это компьютерная программа, что нужно установить?", "Это компьютерная программа? Что нужно установить?"),
                 ("Есть ли разовый платёж за подключение или настройку, кроме тарифа Start 7500 сом в месяц? "
                  "Дополнительные расходы на звонки, стоимость минут и списание",
                  "Есть ли дополнительные расходы кроме тарифа? Плата за подключение?")):
    ok, top = confident(q)
    check(f"уверенно и верно: «{q[:40]}»", ok and top == title, top)
for q in ("какие тарифы и цены",                                   # две похожие записи — пусть рассудят векторы
          "Для подключения голосового ИИ-агента что должен предоставить клиент: пароль от телефона, Wi-Fi, доступы, "
          "какие данные нужны для настройки?",                       # длинный пересказ — мало совпавших слов
          "есть ли доставка"):                                       # ничего похожего
    check(f"не уверенно — ждём векторы: «{q[:40]}»", not confident(q)[0])


# ── гибридный поиск целиком: звонок отвечает сразу, переписка и неуверенный вопрос ждут векторы ──
class FakeDB:
    def commit(self): pass
    def rollback(self): pass


VECTOR_SEC = 1.5


async def slow_vectors(query, ns, agent_id, top_k_entries=8):
    await asyncio.sleep(VECTOR_SEC)
    return {"entries": [], "docs": [], "ms": int(VECTOR_SEC * 1000)}


async def fake_not_found(db, agent_id, query, channel):
    return {"ok": True, "total": 0, "results": [], "search": "not_found"}

kb.search_entries_ranked = lambda db, aid, q, kinds=None, limit=4: kb.rank_entries(ROWS, q, limit)
kb._load_entries_by_ids = lambda db, aid, ids: {}
kb.mark_hits = lambda db, rows: None
kb._not_found = fake_not_found
vectors.search = slow_vectors
vectors.spawn = lambda coro: coro.close()


async def timed(query, channel):
    t = time.monotonic()
    out = await kb._hybrid_answer(FakeDB(), "a1", "ns", query, 4, channel)
    return out, time.monotonic() - t


async def main():
    out, sec = await timed("сколько стоит минута звонка", "call")
    check("звонок, уверенный вопрос: ответ сразу из базы, не ждёт 1,5 с векторов",
          sec < 0.6 and out["search"] == "keywords_fast" and out["results"][0]["title"] == "Сколько стоит минута звонка?",
          f"{sec:.2f} с")
    check("быстрый ответ — в том же формате, что гибридный (агент читает его так же)",
          out["ok"] and out["source"] == "knowledge_base" and "5,47" in out["results"][0]["text"])
    out, sec = await timed("сколько стоит минута звонка", "whatsapp")
    check("переписка: поиск ждёт векторы, как раньше (пауза там не слышна, полный поиск точнее)",
          sec >= VECTOR_SEC - 0.05 and out.get("search") == "hybrid", f"{sec:.2f} с")
    out, sec = await timed("какие тарифы и цены", "call")
    check("звонок, неуверенный вопрос: ждём векторы, точность важнее", sec >= VECTOR_SEC - 0.05, f"{sec:.2f} с")

    async def fast_vectors(query, ns, agent_id, top_k_entries=8):
        await asyncio.sleep(0.05)
        return {"entries": [], "docs": [], "ms": 50}
    vectors.search = fast_vectors
    out, sec = await timed("сколько стоит минута звонка", "call")
    check("векторы успели за 0,25 с — используется полный гибридный ответ", out.get("search") == "hybrid", out.get("search"))

asyncio.run(main())
check("ожидание векторов при уверенном совпадении — четверть секунды", kb.FAST_VECTOR_GRACE == 0.25)
check("голосовая функция поиска передаёт канал «call»",
      'channel="call"' in open(os.path.join(ROOT, "backend/functions/search_knowledge_base.py"), encoding="utf-8").read())

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
