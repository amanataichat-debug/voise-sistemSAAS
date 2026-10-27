"""Скрипт самопроверки voice_selfcheck.py на подменённых данных."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, importlib.util, os, sys, types

sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false",
                  PINECONE_API_KEY="k", OPENAI_API_KEY="k", KB_VECTOR_ENABLED="1")

spec = importlib.util.spec_from_file_location("selfcheck", os.path.join(ROOT, "scripts", "voice_selfcheck.py"))
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

import backend.api.agent as agent_api
from backend.services import agent_voice_context as avc
from backend.services import agent_knowledge_service as kb
from backend.services import agent_knowledge_vectors as vec
from backend.websockets import live_client

E = lambda kind, title, status, active=True: types.SimpleNamespace(
    kind=kind, title=title, vector_status=status, is_active=active)


class FakeQuery:
    def __init__(self, rows): self.rows = rows
    def filter(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def all(self): return self.rows


def fake_db(rows): return types.SimpleNamespace(query=lambda *a, **k: FakeQuery(rows))


agent = types.SimpleNamespace(id="a1", name="Анри", kb_namespace="kb-1")
failures = 0
def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1

# --- общее: подмена функций, которые ходят в БД ---
agent_api._resolve_voice_assistant = lambda db, a: types.SimpleNamespace(system_prompt="П" * 14616, functions={"enabled_functions": ["hangup_call", "search_pinecone"]})
blk = "# ЗНАНИЯ\n" + "В: цена? О: 5000 сом.\n" * 400
avc.build_voice_knowledge_block = lambda db, a, budget=0: (blk[:9000], {"knowledge": 9000, "total": 9000})
avc.kb_function_names = lambda db, a: {"search_knowledge_base", "find_products"}

# 1. ваш случай при СТАРОМ лимите 16000 → FAIL «знания не влезли»
live_client.LIVE_VOICE_INSTRUCTIONS_MAX_CHARS = 16000
rep = sc.Report()
sc.check_prompt(fake_db([]), agent, None, rep)
lv = {i["level"] for i in rep.items if i["area"] == "промпт"}
check("1. лимит 16000 и промпт карточки 14616 → FAIL «ЗНАНИЯ НЕ ВЛЕЗЛИ»", any(i["level"] == "FAIL" and ("НЕ ВЛЕЗЛИ" in i["message"] or "урезаны" in i["message"]) for i in rep.items), str([i["message"][:80] for i in rep.items if i["level"]=="FAIL"]))
check("1. предупреждение про малый лимит", any("мал" in i["message"] for i in rep.items if i["level"] == "WARN"))

# 2. тот же агент при новом лимите 24000 → знания в промпте (обрезаны — WARN), без FAIL
live_client.LIVE_VOICE_INSTRUCTIONS_MAX_CHARS = 24000
rep = sc.Report()
sc.check_prompt(fake_db([]), agent, None, rep)
check("2. лимит 24000 → провалов нет", rep.count("FAIL") == 0, str([i["message"][:70] for i in rep.items if i["level"] == "FAIL"]))
check("2. сообщение о бюджете со всеми слагаемыми", any("Бюджет:" in i["message"] and "токенов" in i["message"] for i in rep.items))
check("2. при 24000 знания целиком в промпте", any("Знания целиком в промпте" in i["message"] for i in rep.items), str([i["message"][:60] for i in rep.items]))
check("2. функции знаний показаны, устаревший search_pinecone замечен", any("find_products" in i["message"] for i in rep.items) and any("search_pinecone" in i["message"] for i in rep.items))

# 3. база: пустая → FAIL; записи без вектора → WARN; всё синхронно → PASS
rep = sc.Report(); sc.check_knowledge(fake_db([]), types.SimpleNamespace(id="a", kb_namespace=None), rep)
check("3. пустая база → FAIL", rep.count("FAIL") == 1)
rep = sc.Report(); sc.check_knowledge(fake_db([E("qa", "Q1", "ready"), E("product", "Тестовые номера", None), E("qa", "Q2", "failed")]), agent, rep)
check("3. записи без вектора → WARN со списком", any(i["level"] == "WARN" and "Записей без вектора: 2 из 3" in i["message"] for i in rep.items))
rep = sc.Report(); sc.check_knowledge(fake_db([E("qa", "Q1", "ready"), E("product", "T", "ready")]), agent, rep)
check("3. всё проиндексировано → PASS", any("Все 2 записей проиндексированы" in i["message"] for i in rep.items) and rep.count("WARN") == 0)
rep = sc.Report(); os.environ["KB_VECTOR_ENABLED"] = "0"; sc.check_knowledge(fake_db([E("qa", "Q1", None)]), agent, rep)
check("3. вектора выключены → WARN", any("выключен" in i["message"] for i in rep.items)); os.environ["KB_VECTOR_ENABLED"] = "1"

# 4. поиск: найдено / не найдено / не то / медленно / упало
async def search_case():
    async def fake_answer(db, agent_id, ns, q, top_k=4):
        if q == "пусто": return {"results": []}
        if q == "упало": raise RuntimeError("boom")
        if q == "медленно":
            await asyncio.sleep(0.05); return {"results": [{"title": "X"}], "search": "hybrid"}
        return {"results": [{"title": "Тестовые номера", "text": "SIM"}], "search": "hybrid"}
    kb.answer_query = fake_answer
    sc.SLOW_SEARCH_MS = 10
    rep = sc.Report()
    await sc.check_search(None, agent, [{"q": "найдётся", "expect": "тестов"}, {"q": "пусто"}, {"q": "найдётся", "expect": "гарантия"},
                                        {"q": "упало"}, {"q": "медленно"}], rep)
    lv = [i["level"] for i in rep.items]
    check("4. поиск: PASS / FAIL / FAIL / FAIL / WARN", lv == ["PASS", "FAIL", "FAIL", "FAIL", "WARN"], str(lv))
asyncio.run(search_case())

# 5. вывод и коды
rep = sc.Report(); rep.fail("x", "плохо"); rep.ok("x", "хорошо")
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf): sc.print_report(rep, "Анри")
check("5. отчёт печатается, вердикт «звонить рано»", "звонить рано" in buf.getvalue())
check("6. файл вопросов читается", len(sc.load_questions(os.path.join(ROOT, "scripts", "voice_selfcheck_questions.json"))) == 7)
check("6. вопросы по умолчанию без файла", len(sc.load_questions(None)) == 6)
print("\nПРОВАЛОВ:", failures)
