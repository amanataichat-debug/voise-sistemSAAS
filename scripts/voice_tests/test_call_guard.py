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
acp.voice_call_since = lambda *a, **k: False
check("кулдаун: переписка → звонок сейчас", sched._dial_gate(contact, naive_now, None, db=object()) is None)
acp.voice_call_since = lambda *a, **k: True
check("кулдаун: был звонок → перенос", sched._dial_gate(contact, naive_now, None, db=object()) is not None)
check("кулдаун без db → прежняя логика", sched._dial_gate(contact, naive_now, None) is not None)

# --- строки сообщений помечены каналом сразу при создании ---
src = open(ao.__file__, encoding="utf-8").read()
for d in ("sms_inbound", "telegram_inbound", "instagram_inbound", "whatsapp_inbound"):
    check(f"AgentCall для {d} помечен при создании", f'postcall_log={{"call_direction": "{d}"}}' in src)
sched_src = open(sys.modules[TaskScheduler.__module__].__file__, encoding="utf-8").read()
check("запланированная отправка в Telegram помечена при создании",
      'postcall_log={"call_direction": "telegram_outbound"}' in sched_src)

print(f"\n{'OK' if not failures else 'FAILED'}: {failures} failures")
sys.exit(1 if failures else 0)
