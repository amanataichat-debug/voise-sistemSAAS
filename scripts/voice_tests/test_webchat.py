"""Чат на сайте (Voksy Beauty, шаг 1.6в).

Часть 1 (без базы): направление webchat_inbound — переписка, а не звонок; ответ ожидается; инструменты
ответа в наборе отправок (пауза бота их останавливает); канал «Сайт» в инбоксе, аналитике, CRM; настройки
чата по умолчанию; что посетитель видит вместо служебной строки файла; виджет на сайт и окно чата на месте.

Часть 2 (PostgreSQL, WEBCHAT_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- публичный API: состояние, сессия по токену, сообщение, опрос, чужой токен — 401, длина, лимит;
- первое сообщение заводит карточку «Гость сайта» (web:…); серия из двух сообщений уходит в разбор
  одним текстом и только один раз (claim_batch);
- разбор: настоящий handle_inbound_webchat → бот отвечает инструментом webchat_send_message через
  execute_tool → посетитель видит ответ, «печатает» гаснет; хронология оркестратора видит чат;
- бот на паузе (владелец ответил) — разбор не запускается; бот выключен — посетителю служебный ответ
  ОДИН раз, владельцу оповещение;
- «оставить номер»: номер уходит в карточку; номер уже есть у клиента — карточка гостя склеивается
  с ним, переписка переезжает;
- инбокс: диалог в списке и в канале «Сайт», лента, ответ владельца (sent_by=owner, пауза бота);
- запись в два шага прямо в чате: итог → «да» клиента в чате → запись;
- инструменты чата у бота только когда чат есть; кабинет: настройки чата и код кнопки, выключение;
- аналитика считает сообщения чата отдельным каналом.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("WEBCHAT_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="webchat-test-secret-0123456789abcdef",
                  WEBCHAT_DEBOUNCE_SECONDS="0")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import webchat_service as wc  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
from backend.services import agent_orchestrator as ao, agent_mode, inbox_service, analytics_service  # noqa: E402
from backend.models.agent_call import MESSAGE_EVENT_DIRECTIONS  # noqa: E402
check("чат на сайте — переписка, не звонок", "webchat_inbound" in ao.MESSAGE_DIRECTIONS
      and not ao.is_voice_direction("webchat_inbound") and "webchat_inbound" in MESSAGE_EVENT_DIRECTIONS)
check("на сообщение в чате ждём ответ (напоминалка, если бот промолчал)", "webchat_inbound" in ao.REPLY_EXPECTED_DIRECTIONS)
check("ответ в чат — отправка клиенту: пауза и правила тарифа действуют",
      {"webchat_send_message", "webchat_send_file"} <= agent_mode.SEND_TOOLS
      and {"webchat_send_message", "webchat_send_file"} <= ao.CLIENT_REPLY_TOOLS)
check("канал «Сайт» в инбоксе и аналитике", "webchat" in inbox_service.CHANNELS
      and inbox_service.CHANNEL_LABELS["webchat"] == "Сайт" and "webchat" in analytics_service.CHANNELS
      and "webchat_inbound" in analytics_service.MESSAGE_DIRECTIONS)
check("гость без номера: web:… не показывается как телефон", inbox_service.real_phone("web:abc") is None)
cfg = wc.chat_settings(None)
check("чат по умолчанию включён, подпись и приветствие есть", cfg["enabled"] and cfg["title"] and cfg["greeting"])
check("посетитель не видит служебную строку файла и id",
      wc._visitor_text("📎 Отправлен файл «прайс.pdf» [file_id=0a1b2c3d-1111-2222-3333-444455556666]\nДержите прайс", True)
      == "Держите прайс")
check("токен: только безопасные символы", wc.TOKEN_RE.match("a" * 32) and not wc.TOKEN_RE.match("a b") and not wc.TOKEN_RE.match("x" * 10))
names = {t["name"] for t in wc.TOOLS}
check("инструменты бота: ответ и файл в чат", names == {"webchat_send_message", "webchat_send_file"})
st = os.path.join(ROOT, "backend", "static")
salon_html = open(os.path.join(st, "salon.html"), encoding="utf-8").read()
widget = open(os.path.join(st, "booking-widget.js"), encoding="utf-8").read()
check("страница салона подключает окно чата", "/static/salon/chat.js" in salon_html and "/static/salon/chat.css" in salon_html)
check("виджет на сайт: режим чата (data-chat) и окно /b/<slug>?chat=1",
      "data-chat" in widget and "&chat=1" in widget and "VoksyChat" in widget)
inbox_js = open(os.path.join(st, "inbox", "inbox.js"), encoding="utf-8").read()
check("инбокс: канал «Сайт» и ответ в него", "webchat: { label: 'Сайт'" in inbox_js and "'webchat'" in inbox_js)

if not PG:
    print("SKIP часть 2: WEBCHAT_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ───────────────────────────── часть 2 ─────────────────────────────
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_contact import AgentContact  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.models.agent_call import AgentCall  # noqa: E402
from backend.models.booking import Booking  # noqa: E402
from backend.models.webchat import WebChatSession, AgentWebChatMessage  # noqa: E402
from backend.services import booking_service as bs, agent_tools, agent_pause  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
app_module.ensure_booking_tables()
cl = TestClient(app_module.app, raise_server_exceptions=False)
B = bs.BISHKEK
db = SessionLocal()

# разбор в фоне не запускаем — тест сам зовёт claim_batch / handle_inbound_webchat
scheduled = []
wc.schedule_dispatch = lambda sid, delay=0: scheduled.append(str(sid))
alerts = []
from backend.services import owner_notifications  # noqa: E402


async def _fake_send(agent, db_, message):
    alerts.append(message)
    return True
owner_notifications._send = _fake_send

plan = SubscriptionPlan(code="beauty_ai", name="Beauty AI", price=3990, max_assistants=10, description="p",
                        is_active=True, product="beauty", limits={"staff": 10, "agent_mode": "reactive"})
db.add(plan)
db.flush()
u = User(email="webchat@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
         subscription_plan_id=plan.id, subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20),
         business_niche="beauty_salon", business_profile={"answers": {}}, openai_api_key="k")
u2 = User(email="other@kg.kg", password_hash="x", email_verified=True, first_name="Другой", company_name="Другой салон",
          subscription_plan_id=plan.id, subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20),
          business_niche="beauty_salon", business_profile={"answers": {}}, openai_api_key="k")
db.add_all([u, u2])
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор", is_active=True)
agent2 = AgentConfig(user_id=u2.id, name="Другой", is_active=True)
db.add_all([agent, agent2])
db.flush()
from backend.services.business_profile import apply_to_agent  # noqa: E402
apply_to_agent(db, u, agent)
nails = AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Маникюр с покрытием",
                            body="Снятие, форма, покрытие", price=1200, duration_min=90, collection="Ногти")
known = AgentContact(user_id=u.id, agent_config_id=agent.id, name="Нурай", phone="+996555111222", status="active")
db.add_all([nails, known])
page = bs.get_page(db, u)
page2 = bs.get_page(db, u2)
db.commit()
SLUG, SLUG2 = page.slug, page2.slug
H = {"Authorization": "Bearer " + __import__("backend.core.security", fromlist=["x"]).create_jwt_token(u.id)}

# ── состояние и сессия
r = cl.get(f"/api/public/chat/{SLUG}")
check("чат виден на странице салона (бот есть и включён, тариф оплачен)", r.status_code == 200 and r.json()["enabled"], r.text[:200])
info = cl.get(f"/api/public/booking/{SLUG}").json()
check("витрина салона знает про чат", (info.get("salon") or {}).get("chat", {}).get("enabled") is True)
r = cl.post(f"/api/public/chat/{SLUG}/start", json={})
tok = r.json().get("token")
check("старт: токен и пустой разговор", r.status_code == 200 and tok and r.json()["items"] == [], r.text[:200])
T = {"X-Chat-Token": tok}
r = cl.post(f"/api/public/chat/{SLUG}/start", json={}, headers=T)
check("тот же токен — та же сессия", r.json().get("new") is False and r.json()["token"] == tok)
check("чужой салон не принимает токен", cl.get(f"/api/public/chat/{SLUG2}/messages", headers=T).status_code == 401)
check("без токена — 401", cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": "x"}).status_code == 401)

# ── сообщения посетителя
r1 = cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": "Здравствуйте"}, headers=T)
r2 = cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": "Сколько стоит маникюр?"}, headers=T)
check("сообщения приняты", r1.status_code == 200 and r2.status_code == 200, r2.text[:200])
db.expire_all()
sess = db.query(WebChatSession).filter(WebChatSession.page_slug == SLUG).first()
guest = db.query(AgentContact).filter(AgentContact.id == sess.agent_contact_id).first()
check("первое сообщение завело карточку «Гость сайта» без номера", guest is not None and guest.name == wc.GUEST_NAME
      and guest.phone.startswith("web:") and guest.agent_config_id == agent.id)
check("разбор поставлен в очередь после каждого сообщения", scheduled.count(str(sess.id)) == 2)
poll = cl.get(f"/api/public/chat/{SLUG}/messages", headers=T).json()
check("посетитель видит свои сообщения и «печатает»", len(poll["items"]) == 2 and poll["typing"] is True
      and all(m["who"] == "client" for m in poll["items"]))
batch = wc.claim_batch(db, sess.id)
check("серия из двух сообщений уходит в разбор одним текстом", batch is not None and batch[0] == str(guest.id)
      and batch[1] == "Здравствуйте\nСколько стоит маникюр?", batch)
check("повторно та же серия не отдаётся", wc.claim_batch(db, sess.id) is None)

# ── разбор: бот отвечает через настоящий execute_tool
seen = {}


async def fake_run(self, agent_call, contact, agent_config, user, body, db_):
    seen["body"] = body
    seen["direction"] = (agent_call.postcall_log or {}).get("call_direction")
    seen["tools"] = [t["function"]["name"] for t in await agent_tools.build_postcall_tools(agent_config, db_)]
    ctx = {"agent_config_id": str(agent_config.id), "user_id": str(user.id), "user": user, "agent_config": agent_config,
           "surface": "postcall", "current_contact_id": str(contact.id), "call_direction": "webchat_inbound"}
    seen["send"] = json.loads(await agent_tools.execute_tool(
        "webchat_send_message", {"agent_contact_id": str(contact.id), "text": "Маникюр с покрытием — **1 200** сом. Записать вас?"}, ctx, db_))
    seen["timeline"] = ao.build_conversation_timeline(db_, contact)
    agent_call.status = "answered"
    db_.commit()
ao.PostCallOrchestrator.run_for_webchat = fake_run
asyncio.run(ao.handle_inbound_webchat(str(sess.id), batch[0], batch[1]))
check("разбор получил склеенный текст и направление webchat_inbound", seen.get("body") == batch[1]
      and seen.get("direction") == "webchat_inbound", seen.get("direction"))
check("у бота есть инструменты ответа в чат", "webchat_send_message" in (seen.get("tools") or []))
check("ответ бота ушёл в чат (текст почищен от markdown)", (seen.get("send") or {}).get("ok")
      and "**" not in db.query(AgentWebChatMessage).filter(AgentWebChatMessage.direction == "outbound").first().body, seen.get("send"))
check("хронология оркестратора видит чат на сайте", "Чат на сайте, клиент → агент" in (seen.get("timeline") or ""),
      (seen.get("timeline") or "")[:300])
poll = cl.get(f"/api/public/chat/{SLUG}/messages", headers=T).json()
bot = [m for m in poll["items"] if m["who"] == "bot"]
check("посетитель видит ответ бота, «печатает» погасло", len(bot) == 1 and "1 200" in bot[0]["text"] and poll["typing"] is False,
      poll)
after = poll["cursor"]
check("опрос после курсора — пусто", cl.get(f"/api/public/chat/{SLUG}/messages", params={"after": after}, headers=T).json()["items"] == [])
check("курсор с «+», ставшим пробелом, тоже понимается", cl.get(f"/api/public/chat/{SLUG}/messages?after={after}", headers=T).json()["items"] == [])

# ── пауза бота: разбор не запускается
seen.clear()
db.expire_all()
guest = db.query(AgentContact).get(guest.id)
agent_pause.set_pause(guest, 30, "owner_message")
db.commit()
cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": "а в субботу можно?"}, headers=T)
b = wc.claim_batch(db, sess.id)
asyncio.run(ao.handle_inbound_webchat(str(sess.id), b[0], b[1]))
check("бот на паузе (владелец ведёт чат) — бот молчит", "body" not in seen)
db.expire_all()
guest = db.query(AgentContact).get(guest.id)
agent_pause.clear_pause(guest) if hasattr(agent_pause, "clear_pause") else setattr(guest, "ai_paused_until", None)
guest.ai_paused_until = None
db.commit()

# ── бот выключен: служебный ответ один раз + оповещение
agent_row = db.query(AgentConfig).get(agent.id)
agent_row.is_active = False
db.commit()
for txt in ("алло?", "есть кто?"):
    cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": txt}, headers=T)
    b = wc.claim_batch(db, sess.id)
    asyncio.run(ao.handle_inbound_webchat(str(sess.id), b[0], b[1]))
db.expire_all()
autos = db.query(AgentWebChatMessage).filter(AgentWebChatMessage.sent_by == "auto").all()
check("бот выключен → посетителю служебный ответ, но один", len(autos) == 1 and "Администратор ответит" in autos[0].body, len(autos))
check("владельцу оповещение «бот не ответил» (одно)", len(alerts) == 1 and "бот выключен" in alerts[0], alerts)
check("выключенный бот — чат не показывается новым посетителям", cl.get(f"/api/public/chat/{SLUG}").json()["enabled"] is False)
agent_row = db.query(AgentConfig).get(agent.id)
agent_row.is_active = True
db.commit()

# ── «оставить номер»: склейка с существующим клиентом
msgs_before = db.query(AgentWebChatMessage).filter(AgentWebChatMessage.agent_contact_id == guest.id).count()
guest_id = guest.id
r = cl.post(f"/api/public/chat/{SLUG}/identity", json={"name": "Нурай", "phone": "0555 111 222"}, headers=T)
db.expire_all()
sess = db.query(WebChatSession).get(sess.id)
check("номер уже есть у клиента → гость склеен с ним", r.status_code == 200 and r.json().get("merged")
      and str(sess.agent_contact_id) == str(known.id), r.text[:200])
check("переписка переехала в карточку клиента, гостя больше нет",
      db.query(AgentWebChatMessage).filter(AgentWebChatMessage.agent_contact_id == known.id).count() == msgs_before
      and db.query(AgentContact).filter(AgentContact.id == guest_id).first() is None)
check("неверный номер — понятный отказ", cl.post(f"/api/public/chat/{SLUG}/identity", json={"phone": "12"}, headers=T).status_code == 400)

# новый посетитель оставляет новый номер — он записывается в его карточку
T2 = {"X-Chat-Token": cl.post(f"/api/public/chat/{SLUG}/start", json={}).json()["token"]}
cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": "Добрый день"}, headers=T2)
r = cl.post(f"/api/public/chat/{SLUG}/identity", json={"name": "Айсулуу", "phone": "+996 700 123 456"}, headers=T2)
db.expire_all()
s2 = db.query(WebChatSession).filter(WebChatSession.id != sess.id, WebChatSession.page_slug == SLUG).first()
c2 = db.query(AgentContact).get(s2.agent_contact_id)
check("новый номер — в карточку гостя, имя тоже", not r.json().get("merged") and c2.phone == "+996700123456" and c2.name == "Айсулуу",
      (c2.phone, c2.name))

# ── инбокс
dl = cl.get("/api/inbox/dialogs?folder=all&channel=webchat", headers=H)
items = dl.json().get("items") or dl.json().get("dialogs") or []
ids = {str(it.get("contact_id") or it.get("id")) for it in items}
check("инбокс: диалоги чата в канале «Сайт»", dl.status_code == 200 and str(known.id) in ids and str(c2.id) in ids,
      dl.text[:300])
tl = cl.get(f"/api/inbox/dialogs/{known.id}", headers=H)
tj = tl.json()
tl_items = (tj.get("timeline") or {}).get("items") or tj.get("items") or []
check("инбокс: лента чата и канал отправки", tl.status_code == 200 and any(i.get("channel") == "webchat" for i in tl_items)
      and ((tj.get("contact") or {}).get("send_channels") or {}).get("webchat", {}).get("can_send"), tl.text[:300])
r = cl.post(f"/api/inbox/dialogs/{known.id}/send", headers=H,
            data={"channel": "webchat", "text": "Нурай, это Айдана, сейчас подскажу 🙂"})
db.expire_all()
own = db.query(AgentWebChatMessage).filter(AgentWebChatMessage.sent_by == "owner").first()
kc = db.query(AgentContact).get(known.id)
check("ответ владельца из инбокса уходит в чат как есть и ставит паузу бота",
      r.status_code == 200 and own is not None and own.body.endswith("🙂") and agent_pause.is_paused(kc), r.text[:300])
poll = cl.get(f"/api/public/chat/{SLUG}/messages", headers=T).json()
check("посетитель видит ответ администратора", any(m["who"] == "owner" for m in poll["items"]))
kc.ai_paused_until = None
db.commit()

# ── запись в два шага прямо в чате
d0 = datetime.now(B).date() + timedelta(days=2)
while d0.weekday() != 2:
    d0 += timedelta(days=1)
start = datetime.combine(d0, time(11, 0), B).isoformat()
ctxw = {"agent_config_id": str(agent.id), "user_id": str(u.id), "user": db.query(User).get(u.id), "agent_config": agent,
        "surface": "postcall", "current_contact_id": str(known.id), "call_direction": "webchat_inbound"}


def run(tool, args):
    return json.loads(asyncio.run(agent_tools.execute_tool(tool, args, ctxw, db)))


first = run("create_booking", {"start": start, "service": "маникюр"})
check("чат: первый шаг записи — итог и confirmation_id", first.get("needs_confirmation") and first.get("confirmation_id"), str(first)[:200])
run("webchat_send_message", {"agent_contact_id": str(known.id), "text": first.get("summary") or "Итог записи. Записываю?"})
cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": "Да, записывайте"}, headers=T)
r = run("create_booking", {"confirmation_id": first["confirmation_id"]})
db.expire_all()
check("«да» в чате → запись создана (и у салона, где мастера ещё не заводили: мастер по умолчанию не теряется)", r.get("ok") and db.query(Booking).filter(Booking.contact_id == known.id,
      Booking.status != "cancelled").count() == 1, str(r)[:300])

# ── набор инструментов и кабинет
pg = cl.get("/api/booking/page", headers=H).json()
check("кабинет: состояние чата и код кнопки на сайт", pg.get("chat", {}).get("live") is True
      and 'data-chat="1"' in (pg.get("chat_widget_code") or ""), str(pg.get("chat"))[:200])
r = cl.put("/api/booking/page", headers=H, json={"chat_enabled": False, "chat_title": "Администратор Алия",
                                                 "chat_greeting": "Салам! Чем помочь?"})
check("кабинет: выключить чат и поменять подпись", r.status_code == 200 and r.json()["chat"]["enabled"] is False
      and r.json()["chat"]["title"] == "Администратор Алия")
check("выключенный чат: на странице нет, новая сессия — 404", cl.get(f"/api/public/chat/{SLUG}").json()["enabled"] is False
      and cl.post(f"/api/public/chat/{SLUG}/start", json={}).status_code == 404)
check("выключенный чат: сообщение не принимается", cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": "x"}, headers=T).status_code == 404)
db.expire_all()
tools_off = [t["function"]["name"] for t in asyncio.run(agent_tools.build_postcall_tools(db.query(AgentConfig).get(agent.id), db))]
check("чат выключен, но переписка была — ответить в неё бот всё ещё может", "webchat_send_message" in tools_off)
db.query(WebChatSession).filter(WebChatSession.agent_config_id == agent2.id).delete()
p2 = bs.get_page(db, db.query(User).get(u2.id))
p2.profile = {"chat_enabled": False}
db.commit()
tools_none = [t["function"]["name"] for t in asyncio.run(agent_tools.build_postcall_tools(db.query(AgentConfig).get(agent2.id), db))]
check("чата нет и не было — инструментов чата у бота нет", "webchat_send_message" not in tools_none)
cl.put("/api/booking/page", headers=H, json={"chat_enabled": True})

# ── лимиты и длина
wc._reset_limits()
too_long = cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": "я" * 2100}, headers=T2)
check("слишком длинное сообщение — отказ", too_long.status_code in (400, 422))
codes = [cl.post(f"/api/public/chat/{SLUG}/messages", json={"text": f"спам {i}"}, headers=T2).status_code for i in range(30)]
check("лимит сообщений с одной сессии — 429", codes.count(200) == wc.MESSAGES_PER_SESSION_10MIN and 429 in codes,
      (codes.count(200), codes[-1]))

# ── аналитика
rep = cl.get("/api/analytics/report?period=7d", headers=H).json()
chs = {c["key"]: c for c in rep.get("channels") or []}
check("аналитика: «Чат на сайте» отдельным каналом с сообщениями", "webchat" in chs and chs["webchat"]["messages_in"] >= 5,
      str(chs.get("webchat"))[:200])

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
