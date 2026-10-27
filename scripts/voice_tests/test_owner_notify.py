"""Оповещения владельцу в Telegram (services/owner_notifications.py): тексты и разбор."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys, types
from datetime import date

sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")

from backend.services import owner_notifications as on
from backend.services.agent_orchestrator import _strip_channel_prefix

failures = 0
def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1

agent = types.SimpleNamespace(id="a1", pipeline_stages=None)
contact = types.SimpleNamespace(name="Айбек", phone="+996700123456", status="new",
                                memory={"summary": "Хочет подключить бота для салона, просит КП."})
lead = on.format_new_lead(agent, contact, "whatsapp_inbound", "Здравствуйте, сколько стоит?")
check("Новый клиент: канал, имя, номер, текст",
      "WhatsApp" in lead and "Айбек · +996700123456" in lead and "сколько стоит" in lead, lead)

call = types.SimpleNamespace(status="answered", duration_seconds=135)
summ = on.format_call_summary(agent, contact, call, "inbound")
check("Итог звонка: длительность и итог", "2:15" in summ and "Входящий" in summ and "КП" in summ, summ)
missed = on.format_call_summary(agent, contact, types.SimpleNamespace(status="no_answer", duration_seconds=0), "outbound")
check("Недозвон: без итога", "не дозвонились" in missed and "Итог" not in missed, missed)

tg = types.SimpleNamespace(name=None, phone="tg:12345", status="active", memory={})
check("Служебный номер мессенджера не показываем", "tg:" not in on.format_new_lead(agent, tg, "telegram_inbound", ""))

check("Префикс канала снимается",
      _strip_channel_prefix('Клиент написал в WhatsApp: "Привет, есть доставка?"') == "Привет, есть доставка?")
check("SMS тоже", _strip_channel_prefix('Клиент прислал SMS: "да"') == "да")

dig = on.format_daily_digest(agent, date(2026, 10, 27), {"new_contacts": 3, "chats": 12, "calls": 5,
                                                          "calls_answered": 4, "calls_missed": 1, "minutes": 9, "success": 2})
check("Итоги дня", "27.10" in dig and "Новых клиентов: 3" in dig and "ответили 4" in dig, dig)

long = on._clip("а" * 500, 220)
check("Длинный текст обрезается", len(long) == 220 and long.endswith("…"))

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
