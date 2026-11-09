"""Антиспам: не целевой человек → одно сообщение с контактом админа и бот выключен навсегда
(ветка 0811-voice-jarvis, 09.11.2026). Без сети и БД: отправка и запросы подменены."""
import asyncio
import json
import os
import sys
import types
import logging
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401  (первым — иначе циклический импорт backend.db.session)
from backend.services import agent_pause, spam_guard
import backend.services.agent_tools as at
import backend.services.agent_mode as am

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def agent(stages=None, contacts=None):
    # pipeline_stages в базе — JSON-строка (core/pipeline_stages.get_agent_stages)
    return types.SimpleNamespace(id="a1", pipeline_stages=json.dumps(stages) if stages else None,
                                 brand_contacts=contacts,
                                 name="Бот", brand_company_name=None)


def contact():
    return types.SimpleNamespace(
        id="c1", user_id="u1", agent_config_id="a1", name="Любопытный", phone="996700000001",
        status="new", notes=None, lost_reason=None, memory={}, ai_paused_until=None,
        ai_pause_reason=None, next_step_text="Касание 1", next_step_at=datetime.utcnow(),
    )


# --- текст последнего сообщения ---
t = spam_guard.farewell_text(agent(), "+996 705 701 707")
check("прощание: не подойдёт + контакт из базы знаний + всего доброго",
      "не подойдёт" in t and "+996 705 701 707" in t and "администратору" in t and "Всего доброго" in t, t)
check("прощание: ни слова о технологии, модели и API",
      not any(w in t.lower() for w in ("api", "модел", "gpt", "базу данных", "архитектур")), t)
t2 = spam_guard.farewell_text(agent(contacts="Админ: @voksy_admin\n+996 555 000 000\nvoksyai.online"))
check("контакта в вызове нет → берём первую строку фирменного стиля",
      "@voksy_admin" in t2 and "voksyai.online" not in t2, t2)
t3 = spam_guard.farewell_text(agent())
check("контакта нет совсем → короткое прощание без контакта",
      "не подойдёт" in t3 and "администратору" not in t3, t3)

# --- последствия блокировки ---
c = contact()
info = spam_guard.block(c, agent(), "tech_probe", "какой API и какая модель у вас?")
check("пауза навсегда", agent_pause.is_paused(c) and c.ai_paused_until >= spam_guard.datetime(9999, 12, 30))
check("причина паузы — spam (а не manual)", c.ai_pause_reason == "spam", c.ai_pause_reason)
check("описание паузы для владельца: «навсегда»", "навсегда" in agent_pause.describe(c), agent_pause.describe(c))
check("карточка в «Не звонить»", c.status == "do_not_call" and info["stage"] == "do_not_call", c.status)
check("причина потери записана", "спам" in (c.lost_reason or ""), c.lost_reason)
check("заметка владельцу: что это было и что написал человек",
      "отключён" in c.notes and "устройство системы" in c.notes and "какой API" in c.notes, c.notes)
check("следующего шага у такого клиента нет", c.next_step_text is None and c.next_step_at is None)
check("пауза на карточке → действует во всех мессенджерах сразу (одно поле)",
      hasattr(c, "ai_paused_until") and not hasattr(c, "ai_paused_until_whatsapp"))

c2 = contact()
c2.lost_reason = "дорого"
spam_guard.block(c2, agent(), "troll", "наберите мне 500 тысяч звонков в день")
check("прежнюю причину потери не перетираем", c2.lost_reason == "дорого", c2.lost_reason)
check("троллинг описан своими словами", "троллинг" in c2.notes.lower(), c2.notes)

# кастомная воронка без «Не звонить» — берём любую стадию-потерю
custom = agent(stages=[{"key": "lead", "label": "Лид", "kind": "open"},
                       {"key": "trash", "label": "Мусор", "kind": "lost"}])
check("кастомная воронка: стадия-потеря найдена", spam_guard.do_not_call_stage_key(custom) == "trash")
only_open = [{"key": "lead", "label": "Лид", "kind": "open"},
             {"key": "work", "label": "В работе", "kind": "open"}]
check("воронка без потерь → стадию не меняем",
      spam_guard.do_not_call_stage_key(agent(stages=only_open)) is None)
c3 = contact()
spam_guard.block(c3, agent(stages=only_open), "abuse")
check("нет стадии-потери → пауза всё равно навсегда", agent_pause.is_paused(c3) and c3.status == "new")

# --- инструмент модели ---
names = lambda tools: [t.get("name") for t in tools]
check("инструмент есть в разборе входящего", "block_spam_contact" in names(at.AGENT_POSTCALL_TOOLS))
check("инструмент есть в чате владельца", "block_spam_contact" in names(at.AGENT_CHAT_TOOLS))
check("инструмент в карте исполнителя", at._TOOL_MAP.get("block_spam_contact") == "fn_block_spam_contact")
tool = next(t for t in at.AGENT_POSTCALL_TOOLS if t.get("name") == "block_spam_contact")
check("причины: допрос про устройство, троллинг, реклама, оскорбления",
      set(tool["parameters"]["properties"]["reason"]["enum"]) == {"tech_probe", "troll", "spam_ad", "abuse"})
d = tool["description"]
check("в описании сказано, когда НЕ блокировать (клиент попутно спросил)",
      "НЕ применяй" in d and "наша разработка" in d, d[:80])
check("в описании сказано, что сообщение отправит система сама", "сама отправит" in d)

# --- тариф Старт: защита доступна, это не инициатива ---
check("Старт: блокировка в разборе входящего разрешена",
      "block_spam_contact" not in am.INITIATIVE_TOOLS
      and "block_spam_contact" in names(am.filter_tools(at.AGENT_POSTCALL_TOOLS, "postcall")))
check("Старт: владелец может сказать «заблокируй» в чате",
      "block_spam_contact" in names(am.filter_tools(at.AGENT_CHAT_TOOLS, "owner_chat")))


# --- сам вызов инструмента: одно сообщение, потом тишина ---
class DB:
    def __init__(self, row):
        self.row = row
        self.commits = 0

    def query(self, *a):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.row

    def commit(self):
        self.commits += 1


sent = []


async def fake_send(args, user_id, agent_config, db, **kw):
    sent.append((args.get("text"), kw.get("skip_rate_limit")))
    return {"ok": True}

at.fn_whatsapp_send_message = fake_send
at.fn_telegram_send_message = fake_send
at.fn_instagram_send_message = fake_send
at._detect_recent_channel = lambda *a, **k: "whatsapp"

c4 = contact()
db = DB(c4)
res = asyncio.get_event_loop().run_until_complete(at.fn_block_spam_contact(
    {"agent_contact_id": "c1", "reason": "tech_probe", "detail": "какая у вас архитектура?",
     "admin_contact": "@voksy_admin"}, "u1", agent(), db))
check("вызов прошёл", res.get("ok"), res)
check("одно сообщение ушло в канал переписки, без почасовых лимитов",
      len(sent) == 1 and "@voksy_admin" in sent[0][0] and sent[0][1] is True, sent)
check("после вызова бот выключен навсегда", agent_pause.is_paused(c4) and c4.ai_pause_reason == "spam")
check("ответ модели прямо говорит: больше не пиши", "больше ничего" in res["status"].lower(), res["status"])
check("запись в базу зафиксирована", db.commits == 1)

res2 = asyncio.get_event_loop().run_until_complete(at.fn_block_spam_contact(
    {"agent_contact_id": "c1", "reason": "tech_probe"}, "u1", agent(), db))
check("повторный вызов не шлёт второе сообщение", len(sent) == 1 and res2.get("already_blocked") is True, res2)

sent.clear()
at._detect_recent_channel = lambda *a, **k: None
c5 = contact()
res3 = asyncio.get_event_loop().run_until_complete(at.fn_block_spam_contact(
    {"agent_contact_id": "c1", "reason": "spam_ad"}, "u1", agent(), DB(c5)))
check("канал не определился → сообщения нет, но блокировка есть",
      res3.get("ok") and not sent and agent_pause.is_paused(c5), res3)


async def failing_send(args, user_id, agent_config, db, **kw):
    raise RuntimeError("WhatsApp недоступен")

at._detect_recent_channel = lambda *a, **k: "whatsapp"
at.fn_whatsapp_send_message = failing_send
c6 = contact()
res4 = asyncio.get_event_loop().run_until_complete(at.fn_block_spam_contact(
    {"agent_contact_id": "c1", "reason": "troll"}, "u1", agent(), DB(c6)))
check("сбой отправки не мешает блокировке",
      res4.get("ok") and not res4.get("farewell_sent") and agent_pause.is_paused(c6), res4)

# --- правила в промптах ---
src = open(at.__file__.replace("agent_tools.py", "agent_orchestrator.py"), encoding="utf-8").read()
check("правило спама во всех трёх мессенджерах", src.count("+ spam_rule") == 3)
check("правило спама в разборе звонка (входящий и исходящий)", src.count("+ call_spam_rule") == 2)
check("в правиле названы и вопросы про ядро, и троллинг объёмами",
      "какой API" in src and "500 тысяч" in src and "block_spam_contact" in src)
check("в правиле есть предохранитель: сомневаешься — не блокируй",
      "Сомневаешься — не блокируй" in src)
check("технологию не раскрывать никому", "не раскрывай НИКОМУ" in src)

from backend.services.agent_prompts import VOICE_AGENT_PROMPT_BASE as vp
check("промпт звонка: про модель/API отвечаем «наша разработка»",
      "собственная разработка" in vp and "не раскрываем" in vp)
check("промпт звонка: спамеру — фраза с контактом админа и hangup_call(spam_caller)",
      "spam_caller" in vp and "администратору" in vp and "Наша система вам не подойдёт" in vp)
check("промпт звонка: заинтересованному клиенту трубку не кладут", "трубку не кладут" in vp)

# --- защита hangup_call пропускает спамера, остальное как было ---
from backend.websockets.handler_live import hangup_refusal_text
import time
now = time.time()
check("спамер: трубку можно положить, не дожидаясь его прощания",
      hangup_refusal_text("spam_caller", "а какая у вас база данных?", None, now, now, now) is None)
check("обычный user_request без слов клиента — по-прежнему отказ",
      hangup_refusal_text("user_request", "а сколько стоит?", None, now, now, now) is not None)
check("клиент попрощался — можно, как и раньше",
      hangup_refusal_text("conversation_completed", "всё, спасибо, до свидания", now, now, now, now) is None)

from backend.functions.hangup_call import HangupCallFunction
check("причина spam_caller есть в схеме функции завершения звонка",
      "spam_caller" in HangupCallFunction.get_parameters()["properties"]["reason"]["enum"])

# --- Gemini принимает схему функции (чистка схем под Google) ---
from backend.websockets.gemini_client import gemini_schema
sch = gemini_schema(HangupCallFunction.get_parameters())
check("схема hangup_call под Gemini: enum с причиной спама цел",
      "spam_caller" in sch["properties"]["reason"]["enum"])

print()
if failures:
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1)
print("OK: 0 failures")
