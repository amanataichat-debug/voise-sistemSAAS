"""Воронка v3 (0311-call-guard, 04.11.2026): «Успех» не ставится сам, «Думает» и дожим,
чистка текста клиенту, продажи в подсказках голоса. Без сети и БД."""
import os
import sys
import types
import logging
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

from backend.core.pipeline_stages import stage_from_decision
from backend.services import sales_funnel as sf
from backend.services.message_style import clean_client_text as clean
from backend.websockets import live_client as lc, voice_hints as vh

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# --- «Успех» не ставится по решению разбора ---
check("SUCCESS (звонок с «подумаю») → этап не меняется", stage_from_decision("SUCCESS", "active") is None)
check("SUCCESS из «Новый» → тоже не «Успех»", stage_from_decision("SUCCESS", "new") is None)
check("REJECTED → rejected", stage_from_decision("REJECTED", "active") == "rejected")
check("FOLLOWUP из «Новый» → active", stage_from_decision("FOLLOWUP", "new") == "active")
check("FOLLOWUP из «Думает» → не откатываем", stage_from_decision("FOLLOWUP", "dumaet") is None)

# --- этапы владельца ---
import json
agent = types.SimpleNamespace(id="a1", pipeline_stages=json.dumps([
    {"key": "new", "label": "Новый", "kind": "open"},
    {"key": "active", "label": "В работе", "kind": "open"},
    {"key": "dumaet", "label": "Думает", "kind": "open"},
    {"key": "success", "label": "Успех", "kind": "won"},
    {"key": "rejected", "label": "Отказ", "kind": "lost"},
    {"key": "do_not_call", "label": "Не звонить", "kind": "lost"},
], ensure_ascii=False))
check("этап «Думает» найден по названию", sf.thinking_stage_key(agent) == "dumaet")
check("«Отказ» = rejected", sf.lost_stage_key(agent) == "rejected")
plain = types.SimpleNamespace(id="a2", pipeline_stages=None)
check("без своего «Думает» — дожим выключен", sf.thinking_stage_key(plain) is None)


# --- КП ушло → «Думает» ---
class DB:
    def commit(self): pass
    def rollback(self): pass


from backend.models.agent_contact import AgentContact
c = AgentContact(id=None, status="active", memory={}, phone="996700000001")
check("КП → «Думает»", sf.on_proposal_sent(DB(), agent, c) == "dumaet" and c.status == "dumaet")
check("дожим начат с 0 касаний", c.memory["nurture"]["touches"] == 0)
won = AgentContact(status="success", memory={}, phone="996700000002")
check("из «Успеха» КП в «Думает» не тянет", sf.on_proposal_sent(DB(), agent, won) is None and won.status == "success")

# --- расписание касаний ---
now = datetime(2026, 11, 5, 8, 0)
iso = lambda d: sf._iso(d)
state = {"out": None, "in": None, "task": False}
sf._last_message_at = lambda db, cid, direction: state["out"] if direction == "outbound" else state["in"]
sf._has_future_task = lambda db, cid: state["task"]
import backend.services.agent_pause as ap
ap.is_paused = lambda contact, now=None: False


def contact(touches, last_touch):
    return types.SimpleNamespace(id="c", memory={"nurture": {"touches": touches, "last_touch_at": iso(last_touch)}})


state.update(out=now - timedelta(hours=25), **{"in": None})
check("сутки тишины после КП → касание 1", sf.next_action(None, contact(0, now - timedelta(hours=25)), now) == ("touch", 1))
check("прошло 5 часов → ждём", sf.next_action(None, contact(0, now - timedelta(hours=5)), now) is None)
state["in"] = now - timedelta(hours=2)
check("клиент ответил последним → касание не нужно", sf.next_action(None, contact(1, now - timedelta(hours=30)), now) is None)
state["in"] = None
check("после касания 1 сутки тишины → касание 2", sf.next_action(None, contact(1, now - timedelta(hours=25)), now) == ("touch", 2))
check("после касания 3 сутки тишины → «Отказ»", sf.next_action(None, contact(3, now - timedelta(hours=25)), now) == ("close", None))
state["task"] = True
check("у агента есть своя задача → не мешаем", sf.next_action(None, contact(1, now - timedelta(hours=40)), now) is None)
state["task"] = False
check("нет дожима в памяти → ничего", sf.next_action(None, types.SimpleNamespace(id="c", memory={}), now) is None)
check("инструкции на 3 касания", set(sf.TOUCH_INSTRUCTIONS) == {1, 2, 3} and "расчёт" in sf.TOUCH_INSTRUCTIONS[2])

# --- оживление: написал, получил ответ и замолчал (07.11.2026, «в WhatsApp пишут и пропадают») ---
rs = {"last": None, "in": None, "task": False}
sf._latest_message = lambda db, cid: rs["last"]
sf._last_message_at = lambda db, cid, direction: rs["in"] if direction == "inbound" else None
sf._has_future_task = lambda db, cid: rs["task"]


def lead(mem=None):
    return types.SimpleNamespace(id="L", memory=mem or {})


def out_msg(hours_ago, channel="whatsapp", sent_by="agent"):
    return {"at": now - timedelta(hours=hours_ago), "direction": "outbound", "sent_by": sent_by, "channel": channel}


rs.update(last=out_msg(4), **{"in": now - timedelta(hours=5)})
act = sf.reengage_action(None, lead(), now)
check("оживление: клиент написал, мы ответили 4 ч назад, он молчит → касание 1",
      act is not None and act[:2] == ("touch", 1) and act[3] == "whatsapp", str(act))
anchor = act[2]
rs["last"] = out_msg(2)
check("оживление: прошло 2 ч → ждём (касание через 3 ч)", sf.reengage_action(None, lead(), now) is None)
rs["last"] = {"at": now - timedelta(hours=1), "direction": "inbound", "sent_by": None, "channel": "whatsapp"}
check("оживление: клиент написал последним → не трогаем", sf.reengage_action(None, lead(), now) is None)
rs["last"] = out_msg(23)
done1 = lead({"reengage": {"anchor": anchor, "touches": 1}})
check("оживление: после касания 1 прошли сутки → касание 2", (sf.reengage_action(None, done1, now) or ())[:2] == ("touch", 2))
check("оживление: после касания 2 — больше сами не пишем",
      sf.reengage_action(None, lead({"reengage": {"anchor": anchor, "touches": 2}}), now) is None)
rs["in"] = now - timedelta(hours=26)
check("оживление: клиент снова написал (новое сообщение) → счёт заново",
      (sf.reengage_action(None, lead({"reengage": {"anchor": anchor, "touches": 2}}), now) or ())[:2] == ("touch", 1))
rs["in"] = now - timedelta(days=5)
check("оживление: клиент писал 5 дней назад — старый лид, первыми не пишем", sf.reengage_action(None, lead(), now) is None)
rs["in"] = now - timedelta(hours=25)
rs["last"] = out_msg(23, channel="instagram")
check("оживление: Instagram позже 24 ч после клиента — нельзя", sf.reengage_action(None, lead(), now) is None)
rs.update(last=out_msg(4, sent_by="owner"), **{"in": now - timedelta(hours=5)})
check("оживление: последним писал сам владелец — не вмешиваемся", sf.reengage_action(None, lead(), now) is None)
rs["last"] = out_msg(4)
check("оживление: идёт дожим после КП — у него свои касания",
      sf.reengage_action(None, lead({"nurture": {"touches": 0}}), now) is None)
rs["task"] = True
check("оживление: у клиента есть задача агента — не мешаем", sf.reengage_action(None, lead(), now) is None)
rs["task"] = False
check("оживление: этапы — открытые кроме «Думает»", sf.reengage_stage_keys(agent) == {"new", "active"})
check("оживление: 2 инструкции, без «вы тут?» и с запретом писать отказавшимся",
      set(sf.REENGAGE_INSTRUCTIONS) == {1, 2} and "НЕ пиши" in sf.REENGAGE_INSTRUCTIONS[1])

# --- переписка: правило «не анкета» для WhatsApp / Telegram / Instagram ---
from backend.services import agent_orchestrator as _ao
_src = open(_ao.__file__, encoding="utf-8").read()
# Правила переписки и разбора звонка проверяются сборкой настоящего события: test_chat_prompt.py
check("касание оживления: своё событие «клиент замолчал», не «дожим после КП»",
      'followup_kind == "reengage"' in _src and "КЛИЕНТ ЗАМОЛЧАЛ В ПЕРЕПИСКЕ" in _src)

# --- из «Отказа (нет ответа)» клиент написал → «В работе» ---
lost = AgentContact(status="rejected", memory={"lost_by": "no_reply"}, lost_reason="нет ответа", phone="996700000003")
check("написал после «нет ответа» → «В работе»", sf.on_client_message(DB(), agent, lost) == "active" and lost.status == "active")
real_no = AgentContact(status="rejected", memory={}, lost_reason="not_target", phone="996700000004")
check("настоящий отказ не трогаем", sf.on_client_message(DB(), agent, real_no) is None and real_no.status == "rejected")

# --- чистка текста клиенту ---
t = clean("Здравствуйте! Я Анри из Voksy AI 👋🙂\n1) чем занимаетесь?\n2) **сколько** звонков — в день?\n- что отдать агенту")
check("тире, звёздочки, нумерация, второй смайл убраны",
      "—" not in t and "**" not in t and "1)" not in t and "🙂" not in t and "- что" not in t, t)
check("ссылка и номер целы", clean("Ссылка https://voksyai.online/a_b-c*d номер +996 705 701 707")
      == "Ссылка https://voksyai.online/a_b-c*d номер +996 705 701 707")
check("диапазон 10–15 → 10-15", clean("от 10–15 тыс") == "от 10-15 тыс")
check("разделитель реплик --- сохраняется", "---" in clean("Привет\n---\nКак дела?"))

# --- прогрев: имя-заглушка не используется ---
from backend.services import call_warmup as cw
check("«Клиент 0700…» → просто «Здравствуйте»", cw._greeting(types.SimpleNamespace(name="Клиент 0700630174")) == "Здравствуйте")
check("Айбек → «Айбек, здравствуйте»", cw._greeting(types.SimpleNamespace(name="Айбек")) == "Айбек, здравствуйте")
check("текст прогрева без длинного тире", "—" not in cw.WARMUP_TEMPLATE)

# --- голос: продажи и поддакивание (общие для GPT-Live и Gemini) ---
g = vh.build_gemini_voice_hints([], "Kore", True)
for name, text in (("GPT-Live", lc.VOICE_DELEGATION_HINT), ("Gemini", g)):
    check(f"{name}: ПРОДАЖИ и ГОРЯЧИЙ КЛИЕНТ", "ПРОДАЖИ." in text and "ГОРЯЧИЙ КЛИЕНТ" in text)
    check(f"{name}: поддакивание не обрывает фразу", "Поддакивание — НЕ перебивание" in text)
    check(f"{name}: ссылка на регистрацию в материалах", "ссылку на регистрацию" in text)
check("GPT-Live: нет «спроси, интересно ли дальше»", "спроси, интересно ли дальше" not in lc.VOICE_DELEGATION_HINT)

# --- оркестратор: касание дожима — сообщение, не звонок ---
from backend.services import agent_orchestrator as ao
check("followup_outbound — не звонок", not ao.is_voice_direction("followup_outbound"))
src = open(ao.__file__, encoding="utf-8").read()
from backend.core.pipeline_stages import stage_from_decision
check("«Успех» не ставится сам по итогу разбора — только явно (агент/владелец)",
      stage_from_decision("SUCCESS", "active") is None)
from backend.models.agent_call import AgentCall
ac = AgentCall(postcall_log={"call_direction": "followup_outbound", "channel": "instagram"})
check("касание видно в истории как сообщение канала", ac._resolve_channel() == "instagram")

# --- второй номер WhatsApp (звонок 04.11.2026: «отправьте на другой WhatsApp») ---
from backend.services import agent_tools as at
schema = at.ADD_CONTACT_PHONE_TOOL if hasattr(at, "ADD_CONTACT_PHONE_TOOL") else None
src_tools = open(at.__file__, encoding="utf-8").read()
check("add_contact_phone умеет use_for_whatsapp", '"use_for_whatsapp"' in src_tools and 'mem["whatsapp_phone"] = digits' in src_tools)
check("whatsapp_send_message идёт на «номер для WhatsApp» карточки, а не в старый чат",
      'pref_raw = phone_arg or mem.get("whatsapp_phone")' in src_tools)
check("промпт разбора звонка: другой номер → сначала add_contact_phone (use_for_whatsapp), потом отправка",
      "сначала add_contact_phone (use_for_whatsapp" in src)

print(f"\n{'OK' if not failures else 'FAILED'}: {failures} failures")
sys.exit(1 if failures else 0)
