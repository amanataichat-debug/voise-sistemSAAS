"""Антиспам «один звонок в день»: переписка звонком не считается (ветка 0311-call-guard).
Без сети и БД: факты о звонках (sip_calls) подменены."""
import os
import sys
import types
import logging
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.services.agent_contact_phones as acp
import backend.services.agent_tools as at
import backend.services.agent_orchestrator as ao
from backend.core.task_scheduler import TaskScheduler

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# --- направления PostCall ---
for d in ("whatsapp_inbound", "telegram_inbound", "instagram_inbound", "sms_inbound", "telegram_outbound"):
    check(f"{d} — не звонок", not ao.is_voice_direction(d))
for d in ("outbound", "inbound", None, ""):
    check(f"{d!r} — звонок", ao.is_voice_direction(d))

# --- _check_call_guards: last_called_at сегодня, но звонков не было ---
now_utc = datetime.now(timezone.utc)
contact = types.SimpleNamespace(
    id="c1", user_id="u1", phone="996700123456", alt_phones=None, call_phone=None,
    last_called_at=(now_utc - timedelta(minutes=5)).replace(tzinfo=None),
)
when = now_utc + timedelta(minutes=10)
acp.missed_call_today = lambda *a, **k: None


def guard(fact, db=object()):
    acp.voice_call_today = lambda *a, **k: fact
    return at._check_call_guards(contact, when, window=None, db=db)


r = guard(False)
check("WhatsApp поставил last_called_at, звонков нет → звонить можно", r.get("ok"), r)
r = guard(True)
check("реальный звонок сегодня → already_called_today", r.get("error") == "already_called_today", r)
r = guard(None)
check("факт не проверить → прежняя логика (отказ)", r.get("error") == "already_called_today", r)
r = guard(False, db=None)
check("без db → прежняя логика (отказ)", r.get("error") == "already_called_today", r)

# --- планировщик: кулдаун 3 ч не держит звонок после сообщения ---
sched = TaskScheduler.__new__(TaskScheduler)
naive_now = now_utc.replace(tzinfo=None)
acp.voice_calls_since = lambda *a, **k: []
check("кулдаун: переписка → звонок сейчас", sched._dial_gate(contact, naive_now, None, db=object()) is None)
acp.voice_calls_since = lambda *a, **k: [types.SimpleNamespace(answered_at=None, direction="outbound")]
check("кулдаун: был звонок → перенос", sched._dial_gate(contact, naive_now, None, db=object()) is not None)
check("кулдаун без db → прежняя логика", sched._dial_gate(contact, naive_now, None) is not None)

# --- перезвон по договорённости в тот же день ---
real_called = contact.last_called_at
acp.voice_call_today = lambda *a, **k: True
acp.today_call_stats = lambda *a, **k: {"answered": True, "dialed_out": 1, "last_answered": True}
r = at._check_call_guards(contact, when, window=None, db=object())
check("клиент взял трубку и попросил «через 2 часа» → звонок сегодня разрешён", r.get("ok"), r)
acp.today_call_stats = lambda *a, **k: {"answered": False, "dialed_out": 1, "last_answered": False}
r = at._check_call_guards(contact, when, window=None, db=object())
check("сегодня только недозвон → already_called_today", r.get("error") == "already_called_today", r)
acp.today_call_stats = lambda *a, **k: {"answered": True, "dialed_out": 3, "last_answered": True}
r = at._check_call_guards(contact, when, window=None, db=object())
check("3 набора за день → daily_call_limit", r.get("error") == "daily_call_limit", r)
acp.today_call_stats = lambda *a, **k: None
r = at._check_call_guards(contact, when, window=None, db=object())
check("статистику не прочитать → прежний отказ", r.get("error") == "already_called_today", r)

# --- планировщик: после разговора пауза 15 мин, а не 3 часа ---
from backend.core import task_scheduler as ts
answered = types.SimpleNamespace(answered_at=naive_now, direction="outbound")
missed_row = types.SimpleNamespace(answered_at=None, direction="outbound")
contact.last_called_at = (now_utc - timedelta(minutes=20)).replace(tzinfo=None)
acp.voice_calls_since = lambda *a, **k: [answered]
check("после разговора 20 мин назад → звонок сейчас", sched._dial_gate(contact, naive_now, None, db=object()) is None)
acp.voice_calls_since = lambda *a, **k: [missed_row]
check("после недозвона 20 мин назад → перенос", sched._dial_gate(contact, naive_now, None, db=object()) is not None)
acp.voice_calls_since = lambda *a, **k: []
check("звонков не было (переписка) → звонок сейчас", sched._dial_gate(contact, naive_now, None, db=object()) is None)
contact.last_called_at = real_called

src_prompt = open(ao.__file__, encoding="utf-8").read()
check("промпт PostCall: просьба клиента о времени → РОВНО на это время, в том числе сегодня",
      "РОВНО на это время, в том числе СЕГОДНЯ" in src_prompt)

# --- строки сообщений помечены каналом сразу при создании ---
src = open(ao.__file__, encoding="utf-8").read()
for d in ("sms_inbound", "telegram_inbound", "instagram_inbound", "whatsapp_inbound"):
    check(f"AgentCall для {d} помечен при создании", f'postcall_log={{"call_direction": "{d}"}}' in src)
sched_src = open(sys.modules[TaskScheduler.__module__].__file__, encoding="utf-8").read()
check("запланированная отправка в Telegram помечена при создании",
      'postcall_log={"call_direction": "telegram_outbound"}' in sched_src)

print(f"\n{'OK' if not failures else 'FAILED'}: {failures} failures")
sys.exit(1 if failures else 0)
