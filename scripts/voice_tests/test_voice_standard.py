"""
Страж эталона голоса GPT-Live — docs/voice_live_standard.md (22.11.2026, подтверждён тест-звонками 22–23.11).

Падает, если правка нарушила стандарт: вернула готовые фразы или пары «так, а не так» в слой платформы,
раздула его, написала подсказки по ходу звонка по-русски, вернула примеры речи и язык в инструкцию Анри или
сценарий по шагам в шаблон клиентов. Хотите осознанно поменять правило — сначала документ (с записью в журнал
изменений и причиной), потом этот тест. Без сети и БД.
"""
import logging
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.websockets.handler_live as h
import backend.websockets.live_client as lc
import backend.websockets.live_language as ll
from backend.services import agent_voice_context as avc
from backend.services import instruction_builder as ib
from backend.services.agent_prompts import build_voice_agent_prompt

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


QUOTE = re.compile(r"«[^»]{2,160}»")
CYR = re.compile(r"[А-Яа-яЁёҢңӨөҮү]")
KY_LETTERS = re.compile(r"[ңөүҢӨҮ]")

head, rules = lc.LIVE_VOICE_HEAD, lc.LIVE_VOICE_RULES
layer = head + rules

# ── 1. Документ на месте ─────────────────────────────────────────────────────────────────────────────
doc_path = os.path.join(ROOT, "docs", "voice_live_standard.md")
doc = open(doc_path, encoding="utf-8").read() if os.path.exists(doc_path) else ""
check("стандарт docs/voice_live_standard.md существует и ссылается на этот тест",
      "test_voice_standard.py" in doc and "НЕЛЬЗЯ" in doc and "Журнал изменений" in doc)

# ── 2. Слой платформы: поведение вместо фраз ─────────────────────────────────────────────────────────
check("слой платформы ≤ 8 500 символов (раздел 6)", len(layer) <= 8500, len(layer))
check("в слое платформы нет пар «так, а не так» (НЕЛЬЗЯ 2)", "», а не «" not in layer)
check("фраз в кавычках в слое платформы ≤ 50 — только словарик понимания, латиница, книжные слова (раздел 6)",
      len(QUOTE.findall(layer)) <= 50, len(QUOTE.findall(layer)))
check("модель подбирает слова сама: «ни одну фразу из этих инструкций не произноси дословно»",
      "ни одну фразу из этих инструкций не произноси дословно" in head)
check("нет «стабильного голоса / ровного тона» (НЕЛЬЗЯ 7)",
      "СТАБИЛЬНЫЙ ГОЛОС" not in layer and "ровн" not in layer.lower())
check("эмоции по-человечески: смех, сочувствие, радость, удивление, лёгкий вдох, в меру (МОЖНО 5)",
      all(w in head for w in ("рассмеяться", "посочувствуй", "порадуйся", "удивись", "вдох", "в меру", "без театра"))
      and "шёпотом — не больше пары слов" in head)
check("кыргызское правило написано по-кыргызски и про бишкекский аралаш (МОЖНО 2)",
      "Бишкекте кадимки адамдар" in head and "орусча сөздөр кадимкидей кошулат" in head
      and len(KY_LETTERS.findall(head)) >= 20)
check("смена языка — тот же разговор, не переспрашивать (раздел 5)",
      "Смена языка — тот же разговор" in head and "не переспрашивай" in head)
check("словарик для понимания клиента на месте («баасы канча», «өзүм жооп берем»)",
      "«баасы канча?»" in head and "«өзүм жооп берем» — отвечаю сам" in head)
check("заголовки шаблона OpenAI на английском (МОЖНО 6)",
      all(x in rules for x in ("Backchannel policy:", "Interruption policy:", "Delegation policy:",
                               "Delegate to the backend when:", "Do not delegate to the backend when:",
                               "Delegate to the backend before giving an answer that depends on backend work.")))
check("в слое платформы нет нашего (Voksy, Анри) (НЕЛЬЗЯ 11)",
      not re.search(r"(?i)voksy|вокси|анри", layer))

# ── 3. Язык держит код ───────────────────────────────────────────────────────────────────────────────
check("закрепление кыргызского написано по-кыргызски и без готовых реплик",
      len(KY_LETTERS.findall(ll.LOCK_INSTRUCTIONS["ky"])) >= 5
      and not QUOTE.sub("", ll.LOCK_INSTRUCTIONS["ky"]).count("«"))
check("закрепление языка несёт уже сказанное клиентом (не переспрашивать)",
      "кайра сураба" in ll.lock_instruction("ky", ["Бизде кафе бар, күнүнө жыйырма заявка келет"]))
check("кыргызская линия: заметка с кыргызской строкой «не менеджер»",
      "бул менеджер эмес" in lc.KYRGYZ_LINE_NOTE)
q0 = lc.CALL_DECISION_TRIGGER["decisions"]["questions"][0]
check("Decisions: просьба о языке — отдельный ответ wants_language", {"value": "wants_language"} in q0["choices"])
check("детекция: «кыргызча сүйлөй турган адам керек» → кыргызский",
      ll.requested_language("Бизге кыргызча сүйлөй турган адам керек") == "ky")
check("кыргызских реплик до закрепления — 2", ll.LanguageLock.KY_TURNS_TO_LOCK == 2)

# ── 4. Подсказки по ходу звонка — на английском, язык ответа = язык разговора (НЕЛЬЗЯ 5) ─────────────
hints = list(h.MOOD_HINTS.values()) + list(h.CUE_HINTS.values()) + list(h.INTENT_HINTS.values())
check("подсказки Decisions без кириллицы и без готовых фраз",
      all(not CYR.search(x) and "«" not in x for x in hints))
check("каждая подсказка говорит: отвечай на языке разговора, не по-английски",
      all(h._HINT_LANG in x for x in hints))
check("лимиты эмоций: ≤3 за звонок, 40 с / 30 с (раздел 6)",
      h.CUE_MAX_PER_CALL == 3 and h.CUE_HINT_GAP_SEC == 40.0 and h.CUE_ANY_GAP_SEC == 30.0)

# ── 5. Модели и лимиты ───────────────────────────────────────────────────────────────────────────────
_lc_src = open(os.path.join(ROOT, "backend/websockets/live_client.py"), encoding="utf-8").read()
check("модель голоса по умолчанию gpt-live-1.1, запасная gpt-live-1",
      lc.STABLE_LIVE_MODEL == "gpt-live-1" and 'or "gpt-live-1.1"' in _lc_src)
check("предупреждение владельцу о готовых фразах — от 4", avc.QUOTED_PHRASES_WARN == 4)

# ── 6. Слой владельца: Анри, шаблон, генератор ──────────────────────────────────────────────────────
anri = open(os.path.join(ROOT, "docs", "voice_agent_prompt_anri.md"), encoding="utf-8").read()
i = anri.find("## БЛОК 1"); a = anri.find("```", i) + 3; block1 = anri[a:anri.find("```", a)]
check("Блок 1 Анри ≤ 3 500 символов", len(block1) <= 3500, len(block1))
check("Блок 1 Анри без примеров речи, без раздела о языке, ≤ 3 фраз в кавычках (НЕЛЬЗЯ 1, 4)",
      "Примеры живой речи" not in block1 and "Язык — самое важное" not in block1
      and "Кыргызча сүйлөгөндө" not in block1 and len(QUOTE.findall(block1)) <= 3, len(QUOTE.findall(block1)))
check("Блок 1 Анри без цен цифрами (факты — в базе знаний, НЕЛЬЗЯ 6)",
      not re.search(r"\d[\d\s]{2,}\s*сом", block1))
tpl = open(os.path.join(ROOT, "backend/static/js/instruction-templates.js"), encoding="utf-8").read()
voice_tpl = tpl[tpl.find("var voice ="):tpl.find("var chat =")]
check("шаблон звонка для клиентов без сценария по шагам (НЕЛЬЗЯ 3)",
      "Как вести звонок" not in voice_tpl and not re.search(r"'\d\. ", voice_tpl))
check("генератор инструкций звонков: «Никаких готовых реплик», без кыргызских образцов",
      "Никаких готовых реплик" in ib.VOICE_SYSTEM and "реплик-образцов под этот бизнес" not in ib.VOICE_SYSTEM)

# ── 7. Сборка промпта: порядок блоков (раздел 2) ─────────────────────────────────────────────────────
card = types.SimpleNamespace(id="x", voice="cedar", greeting_message=None, language="ky",
                             functions={"enabled_functions": ["hangup_call"]},
                             system_prompt=build_voice_agent_prompt(block1))
ins = lc.OpenAILiveClient("k", card, "cid", telephony=True).build_session_config()["instructions"]
check("порядок: голова платформы → кыргызская линия → карточка → правила платформы",
      ins.startswith(head) and ins.index("# Линия") < ins.index("Ты — Анри") < ins.index("# Шесть твёрдых правил"))

# ── 8. Разговор как у человека (23.11.2026, звонок c3847245) ──────────────────────────────────────────
check("согласие — решение, а не новый вопрос; не проверять «правильно ли понял»",
      "Согласие — это решение, а не повод спросить ещё раз" in rules and "Не проверяй, правильно ли понял" in rules)
check("вопрос — не в конце каждой реплики; ответ на обычный вопрос — одна-две фразы, без перечня вариантов",
      "а не в конце каждой реплики" in rules and "одна-две короткие фразы" in rules and "без перечня всех вариантов" in rules)
check("рассуждать вслух вместо слов ожидания; кыргызские «бир карап айтайын», «тактап айтайын» запрещены",
      "подумай вслух" in rules and "«бир карап айтайын»" in rules and "«тактап айтайын»" in rules)
check("рецепт OpenAI: No recap, без повторных подтверждений и дежурных предложений в конце",
      "No recap." in rules and "Avoid repeated acknowledgments and generic closing offers." in rules)
check("смех и вдох — голосом, без ремарок (OpenAI: perform the sounds, never read stage directions)",
      "Смех и вдох делай голосом" in head and "не читай ремарки" in head.replace('"\n    "', ""))
check("не делегировать «как работает / подойдёт ли / сколько стоит» и согласие прислать материалы",
      "отвечай сам из того, что знаешь" in rules and "просит прислать материалы" in rules and "просит менеджера или перезвонить" in rules)

# ── 9. Механика звука (раздел 7) ─────────────────────────────────────────────────────────────────────
_app = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
_mon = _app[_app.find("async def monitor_resources"):_app.find("psutil not available")]
check("нет принудительного gc.collect() в middleware запросов (замораживал звук до 1,6 с)",
      "gc.collect()" not in _mon.replace("# gc.collect()", "") and "gc.freeze()" in _app)
_bad = []
for d, _, fs in os.walk(os.path.join(ROOT, "backend")):
    for f in fs:
        if f.endswith(".py"):
            t = open(os.path.join(d, f), encoding="utf-8", errors="ignore").read()
            if re.search(r"^logging\.basicConfig\(", t, re.M):
                _bad.append(f)
check("никто не перенастраивает корневой логгер basicConfig (стирал очередь логов)", not _bad, _bad)
_logsrc = open(os.path.join(ROOT, "backend/core/logging.py"), encoding="utf-8").read()
check("логи пишет отдельный поток (QueueHandler + QueueListener), после fork очередь пересоздаётся",
      "QueueListener" in _logsrc and "register_at_fork(after_in_child=_after_fork_in_child)" in _logsrc)
from backend.services.sip_gateway_service import spoken_name
check("латинское имя в приветствии — кириллицей (Nurlan → Нурлан, Aibek → Айбек, Meerim → Мээрим)",
      spoken_name("Nurlan") == "Нурлан" and spoken_name("Aibek") == "Айбек" and spoken_name("Meerim") == "Мээрим"
      and spoken_name("Айгуль") == "Айгуль", [spoken_name(x) for x in ("Nurlan", "Aibek", "Meerim")])

# ── 10. Коротко, слушает, ведёт к цели (27.11.2026, звонок f9b4a269) ─────────────────────────────────────
check("краткость первым пунктом: одна-две фразы, не больше двух вариантов; характер — «не душный»",
      rules.index("Коротко — это вежливо") < rules.index("# Шесть твёрдых правил") and "не больше двух на выбор" in rules
      and "Не душный" in head)
check("Interruption policy — рецепт OpenAI «Immediate stop», без «договори мысль»",
      "stop speaking right away, even in the middle of a sentence" in rules
      and "Don't resume an answer they've replaced." in rules and "договори мысль" not in layer)
check("у звонка есть результат: раздел «ЦЕЛЬ ЗВОНКА» (цель владельца или ниши)",
      "«ЦЕЛЬ ЗВОНКА»" in rules and avc.GOAL_HEADING == "ЦЕЛЬ ЗВОНКА")
check("кыргызский — коротко, правило по-кыргызски", "Кыска сүйлө: бир сүйлөмдө бир ой" in head)
check("просьба замолчать — не прощание (Decisions stop_talking) и стоп кодом (barge_in.py)",
      {"value": "stop_talking"} in q0["choices"] and h.HARD_BARGE_IN and h._STOP_TALK_RE.search("азыр токто")
      and os.path.exists(os.path.join(ROOT, "backend/websockets/barge_in.py")))

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
