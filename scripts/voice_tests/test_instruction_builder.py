"""«Сгенерировать по описанию» и «Добавить правку» (services/instruction_builder.py, 10.11.2026) — без сети и БД.

Модель подменена: проверяем, что генератор получает профиль агента (этапы воронки, база знаний, может ли звонить),
знает, что платформа делает сама, разбирает ответ (инструкция + факты для базы знаний), не отдаёт обрезанную
правку, и что кабинеты Старта и Profi подключили окно.
"""
import asyncio
import json
import logging
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")
logging.disable(logging.CRITICAL)

from backend.services import instruction_builder as ib

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def user(mode):
    return types.SimpleNamespace(id="u1", agent_mode=lambda: mode)


stages = json.dumps([{"key": "new", "label": "Новый", "kind": "open"},
                     {"key": "podbor", "label": "Подбор запчасти", "kind": "open"},
                     {"key": "won", "label": "Заказ оформлен", "kind": "won"},
                     {"key": "lost", "label": "Отказ", "kind": "lost"}])
agent = types.SimpleNamespace(id=None, name="Азамат", brand_company_name="АвтоДеталь KG", pipeline_stages=stages,
                              doc_who_am_i="Магазин автозапчастей для японских и корейских авто в Бишкеке.",
                              doc_what_we_offer="Оригинал и аналоги, доставка по городу.",
                              additional_instructions="", voice_additional_instructions="Ты — Азамат. Скидки не обещай.")

# ── профиль и запрос ──
prof = ib.agent_profile(None, agent, user("reactive"))
check("профиль: имя, компания, о бизнесе, ТОЧНЫЕ этапы воронки владельца",
      "Азамат" in prof and "АвтоДеталь KG" in prof and "японских и корейских" in prof
      and "Подбор запчасти" in prof and "Заказ оформлен (успех)" in prof, prof)
check("профиль: Старт — агент сам не звонит", "Агент может сам звонить клиентам: нет" in prof)
check("профиль: Profi — может звонить", "звонить клиентам: да" in ib.agent_profile(None, agent, user("full")))

msgs = ib.generate_messages("chat", "Продаём автозапчасти, клиенты пишут в Instagram.", prof, call_first=False)
sysm = msgs[0]["content"]
check("переписка: генератор знает, что платформа делает сама, и не повторяет это",
      "ЧТО ПЛАТФОРМА УЖЕ ДЕЛАЕТ САМА" in sysm and "дожим в три касания" in sysm and "без кавычек, тире" in sysm)
check("переписка: этапы — точными названиями, каталог с фото, звать владельца",
      "ТОЧНЫЕ названия этапов" in sysm and "find_products" in sysm and "notify_owner_handoff" in sysm)
check("переписка: Старт — правило «сам не звонит», Profi — «может позвонить сам»",
      ib.CALL_RULE_REACTIVE in sysm and ib.CALL_RULE_FULL in ib.generate_messages("chat", "x" * 30, prof, call_first=True)[0]["content"]
      and "{call_rule}" not in sysm)
vsys = ib.generate_messages("voice", "Звонят по объявлениям.", prof)[0]["content"]
check("звонок: шаги, сомнения, «оформить», разговорный кыргызский; факты — в базу знаний, не в инструкцию",
      all(k in vsys for k in ("Звонок по шагам", "Сомнения", "оформить", "бишкекском кыргызском", ib.KB_MARK)))
check("оба генератора: не выдумывать скидки и сроки, без markdown, без Voksy у чужой компании",
      all(k in sysm and k in vsys for k in ("Не выдумывай", "Без markdown", "Не упоминай платформу Voksy AI")))
cur = ib.generate_messages("voice", "x" * 30, prof, current="Скидки не обещай.")[1]["content"]
check("генерация учитывает текущую инструкцию владельца (его запреты сохраняются)", "Скидки не обещай." in cur)

# ── разбор ответа ──
text, facts = ib.split_generated("```\n## Кто ты\n**Ты — Азамат**, менеджер.\n\n"
                                 f"{ib.KB_MARK}\n- Доставка по Бишкеку? — 200 сом.\n- Работаете в воскресенье? — Да, до 15:00.\n```")
check("ответ: инструкция без markdown и обёртки, факты отдельно", text == "Кто ты\nТы — Азамат, менеджер."
      and facts == ["Доставка по Бишкеку? — 200 сом.", "Работаете в воскресенье? — Да, до 15:00."], (text, facts))
t2, notes = ib.split_refined(f"Новая инструкция целиком.\n{ib.CHANGES_MARK}\n1. Добавлен этап Замер.")
check("правка: текст и «что изменено»", t2 == "Новая инструкция целиком." and notes == ["Добавлен этап Замер."])


# ── generate / refine целиком (модель подменена) ──
async def flows():
    long_text = "Ты — Азамат, менеджер АвтоДеталь KG.\n" + ("Спроси марку, модель и год авто. " * 20)
    calls = []

    async def fake_complete(db, u, messages, *, temperature, note):
        calls.append((note, temperature, messages))
        if note.startswith("generate"):
            return long_text + f"\n{ib.KB_MARK}\nДоставка? — 200 сом."
        if "огрызок" in messages[1]["content"]:
            return "Коротко."
        return long_text + "Скидки не предлагай.\n" + f"{ib.CHANGES_MARK}\nДобавлен запрет скидок."

    ib._complete = fake_complete
    r = await ib.generate(None, user("reactive"), agent, kind="chat", brief="Продаём автозапчасти японских авто в Бишкеке.")
    check("generate: инструкция и факты для базы, модель указана", r["text"].startswith("Ты — Азамат") and r["kb_facts"] == ["Доставка? — 200 сом."]
          and r["model"] == ib.MODEL and calls[-1][0] == "generate_chat")
    for bad, code in ((("chat", "коротко"), "brief_too_short"), (("sms", "x" * 40), "invalid_kind")):
        try:
            await ib.generate(None, user("reactive"), agent, kind=bad[0], brief=bad[1])
            check(f"generate: отказ {code}", False)
        except ib.BuilderError as e:
            check(f"generate: отказ {code}", e.code == code, e.code)
    r = await ib.refine(None, user("reactive"), agent, kind="voice", change="не предлагай скидки", current=long_text)
    check("refine: новая инструкция целиком, «что изменено», changed", r["changed"] and r["changes"] == ["Добавлен запрет скидок."]
          and "Скидки не предлагай." in r["text"] and calls[-1][1] == 0.2)
    try:
        await ib.refine(None, user("reactive"), agent, kind="voice", change="огрызок", current=long_text)
        check("refine: обрезанный ответ модели не отдаётся владельцу", False)
    except ib.BuilderError as e:
        check("refine: обрезанный ответ модели не отдаётся владельцу", e.code == "refine_truncated", e.code)
    try:
        await ib.refine(None, user("reactive"), types.SimpleNamespace(additional_instructions=""), kind="chat", change="добавь этап")
        check("refine: пустая инструкция — подсказка сначала сгенерировать", False)
    except ib.BuilderError as e:
        check("refine: пустая инструкция — подсказка сначала сгенерировать", e.code == "nothing_to_refine")

asyncio.run(flows())

# ── API и кабинеты ──
api = open(os.path.join(ROOT, "backend/api/agent.py"), encoding="utf-8").read()
check("API: /instructions/generate и /instructions/refine, доступ Старт/Profi, ничего не сохраняют сами",
      '@router.post("/instructions/generate")' in api and '@router.post("/instructions/refine")' in api
      and api.count("current_user.has_agent_chat_access()") >= 2)
_js = open(os.path.join(ROOT, "backend/static/js/instruction-builder.js"), encoding="utf-8").read()
_bot = open(os.path.join(ROOT, "backend/static/start/bot.js"), encoding="utf-8").read()
_iv = open(os.path.join(ROOT, "backend/static/agent/instructions-voice.js"), encoding="utf-8").read()
_ah = open(os.path.join(ROOT, "backend/static/agent.html"), encoding="utf-8").read()
_sh = open(os.path.join(ROOT, "backend/static/start-bot.html"), encoding="utf-8").read()
check("окно: «Готово, поставить», «Добавить правку», видно добавленное и убранное, факты для базы",
      "Готово, поставить" in _js and "vib-add" in _js and "vib-del" in _js and "kb_facts" in _js)
check("Старт: кнопки у переписки и звонков, скрипт подключён",
      _bot.count("builder('generate'") == 2 and _bot.count("builder('refine'") == 2 and "instruction-builder.js" in _sh)
check("Profi: кнопки у переписки и звонков, сохраняется только своё поле, скрипт подключён",
      _ah.count("openInstructionBuilder('generate'") == 2 and _ah.count("openInstructionBuilder('refine'") == 2
      and "instruction-builder.js" in _ah and "JSON.stringify({ [field]: text })" in _iv)

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
