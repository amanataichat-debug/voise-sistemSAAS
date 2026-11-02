"""Реклама Click-to-WhatsApp в первом сообщении и звонок по просьбе клиента после недозвона
(без сети и БД: запросы к базе подменены)."""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.services.whatsapp_service as wa
import backend.services.agent_tools as at
import backend.services.agent_mode as am
import backend.services.agent_contact_phones as acp

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def item(message, **extra):
    base = {"key": {"remoteJid": "996700111222@s.whatsapp.net", "id": "A1", "fromMe": False},
            "pushName": "Айбек", "messageTimestamp": 1761900000, "message": message}
    base.update(extra)
    return base


# --- реклама ---
ad = {"title": "Голосовой агент для бизнеса", "body": "Отвечает на звонки 24/7",
      "sourceUrl": "https://www.instagram.com/p/XYZ/", "sourceType": "ad"}
p = wa.parse_incoming(item({"extendedTextMessage": {
    "text": "Здравствуйте! Хочу узнать подробнее", "contextInfo": {"externalAdReply": ad}}}))
check("ad: распознана реклама Instagram", p and p["ad"] and p["ad"]["source"] == "Instagram", p and p.get("ad"))
check("ad: строка об объявлении в начале текста",
      p and p["text"].startswith("[Клиент пришёл из рекламы Instagram: «Голосовой агент для бизнеса — Отвечает"), p and p["text"])
check("ad: текст клиента сохранён", p and p["text"].endswith("Хочу узнать подробнее"))

p = wa.parse_incoming(item({"conversation": "Привет"},
                           contextInfo={"externalAdReply": {"sourceUrl": "https://fb.me/abc", "title": "Акция"}}))
check("ad: Facebook и contextInfo на уровне item", p and p["ad"]["source"] == "Facebook", p and p.get("ad"))

p = wa.parse_incoming(item({"conversation": "Добрый день"}))
check("ad: обычное сообщение без рекламы", p and p["ad"] is None and p["text"] == "Добрый день")

p = wa.parse_incoming(item({"reactionMessage": {"text": "👍"}}))
check("ad: реакция по-прежнему отброшена", p is None)

# --- звонок по просьбе клиента после недозвона ---
from backend.core.timezone_utils import BISHKEK

now_local = datetime.now(BISHKEK).replace(hour=15, minute=0, second=0, microsecond=0)
now_utc = now_local.astimezone(timezone.utc)
earlier = (now_local - timedelta(hours=3)).astimezone(timezone.utc)
contact = SimpleNamespace(id="c1", user_id="u1", phone="996700111222", alt_phones=None, call_phone=None,
                          last_called_at=earlier.replace(tzinfo=None))
at._bishkek_now = lambda: now_local
acp.missed_call_today = lambda *a, **k: SimpleNamespace(created_at=earlier)

came_back = {"v": False}
am.client_contacted_since = lambda db, cid, since: came_back["v"]

g = at._check_call_guards(contact, now_utc, allow_shift=False, window=None, db=object(), user_id="u1")
check("callback: клиент молчит — второй звонок сегодня запрещён",
      not g.get("ok") and g.get("error") == "already_called_today", g)

came_back["v"] = True
g = at._check_call_guards(contact, now_utc, allow_shift=False, window=None, db=object(), user_id="u1")
check("callback: клиент сам написал после звонка — звонок разрешён", g.get("ok"), g)

contact.last_called_at = None
came_back["v"] = False
g = at._check_call_guards(contact, now_utc, allow_shift=False, window=None, db=object(), user_id="u1")
check("callback: недозвон по sip_calls, клиент молчит — запрещено", not g.get("ok"), g)
came_back["v"] = True
g = at._check_call_guards(contact, now_utc, allow_shift=False, window=None, db=object(), user_id="u1")
check("callback: недозвон, потом клиент написал — разрешено", g.get("ok"), g)

print(f"\n{'OK' if not failures else 'FAILED'}: {failures} провал(ов)")
sys.exit(1 if failures else 0)
