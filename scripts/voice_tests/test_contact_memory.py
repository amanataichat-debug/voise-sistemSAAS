"""
Память карточки клиента (ветка 1111-clean-system) — без сети и БД.
  * переписка не увеличивает «попытки» и не ставит «звонили сегодня»;
  * факты: порядок, без дублей, устаревшие удаляются, предел;
  * итог звонка не затирается итогом переписки, история коротких итогов;
  * update_contact_info: заметки дописываются, пустые поля ничего не стирают.
"""
import asyncio
import logging
import os
import sys
from datetime import datetime
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401
from backend.services import agent_tools as at

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


m = at.apply_memory_update({"attempts": 3, "last_call": "2026-11-10 10:00"},
                           {"summary": "Спросил цену стрижки"}, "whatsapp_inbound")
check("переписка: нет «попыток» и «последнего звонка» в памяти", "attempts" not in m and "last_call" not in m, m)
check("переписка: итог записан, итога звонка нет", m["summary"] == "Спросил цену стрижки" and "call_summary" not in m)

m = at.apply_memory_update({}, {"summary": "Договорились на завтра 10:00", "key_facts": ["Салон в Джале"]}, "outbound")
m = at.apply_memory_update(m, {"summary": "ок, спасибо"}, "whatsapp_inbound")
check("итог звонка не затирается итогом переписки",
      m["call_summary"] == "Договорились на завтра 10:00" and m["summary"] == "ок, спасибо", m)
check("история итогов: канал и текст по порядку",
      [h["channel"] for h in m["history"]] == ["звонок", "whatsapp"], m["history"])

facts = at.merge_facts(["Салон в Джале", "Недозвон 1 из 2"], ["салон в Джале!", "3 мастера"], ["Недозвон 1 из 2"])
check("факты: дубль без учёта регистра один, устаревший удалён, порядок сохранён",
      facts == ["салон в Джале!", "3 мастера"], facts)
many = at.merge_facts([f"факт {i}" for i in range(30)], ["новый"])
check("факты: предел, уходят самые старые", len(many) == at.MEMORY_MAX_FACTS and many[-1] == "новый", len(many))
m = {}
for i in range(12):
    m = at.apply_memory_update(m, {"summary": f"итог {i}"}, "telegram_inbound")
check("история итогов ограничена", len(m["history"]) == at.MEMORY_MAX_HISTORY and m["history"][-1]["text"] == "итог 11")


class Q:
    def __init__(self, obj):
        self.obj = obj

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.obj


class DB:
    def __init__(self, obj):
        self.obj = obj

    def query(self, *a):
        return Q(self.obj)

    def commit(self):
        pass


c = NS(name="VeTeR", company=None, position=None, notes="[2026-11-01 10:00] Владелец: VIP-клиент")
r = asyncio.run(at.fn_update_contact_info(
    {"agent_contact_id": "c1", "name": "Айбек", "company": "", "notes": "Хочет запись по субботам"}, "u", "a", DB(c)))
check("update_contact_info: имя записано, пустая компания ничего не стёрла", c.name == "Айбек" and c.company is None, r)
check("update_contact_info: заметка владельца сохранена, новая дописана",
      c.notes.startswith("[2026-11-01 10:00] Владелец: VIP-клиент") and "Хочет запись по субботам" in c.notes, c.notes)
asyncio.run(at.fn_update_contact_info({"agent_contact_id": "c1", "notes": "Хочет запись по субботам"}, "u", "a", DB(c)))
check("update_contact_info: та же заметка второй раз не дублируется", c.notes.count("Хочет запись по субботам") == 1)

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
