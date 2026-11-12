"""
Инбокс (ветка 0411-inbox): services/inbox_service.py, api/inbox.py и правки,
которые он затронул (пауза бота при отправке, эхо отправок, задачи владельца).

Часть 1 — без базы и сети (всегда).
Часть 2 — на настоящем PostgreSQL, если задан INBOX_TEST_DATABASE_URL
(отдельная пустая база, таблицы создаются и удаляются тестом):

    INBOX_TEST_DATABASE_URL=postgresql://postgres:pg@localhost/voksy_test \
        python scripts/voice_tests/test_inbox.py
"""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, json, sys, types, uuid
from datetime import datetime, timedelta

sys.path.insert(0, ROOT)
PG = os.environ.get("INBOX_TEST_DATABASE_URL")
os.environ.update(HOST_URL="http://x", DATABASE_URL=PG or "sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")

from backend.services import inbox_service as inbox
from backend.services import agent_tools, agent_pause

failures = 0
def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ============================================================ часть 1
cur = inbox.get_settings.__defaults__  # noqa — функция есть
base = dict(inbox.DEFAULT_SETTINGS, channels=dict(inbox.DEFAULT_SETTINGS["channels"]))
s = inbox.clean_settings({"languages": ["en", "zh", "en"], "default_pause": 120, "channels": {"telegram": False}}, base)
check("настройки: языки без повторов", s["languages"] == ["en", "zh"], s["languages"])
check("настройки: пауза 2 часа", s["default_pause"] == 120)
check("настройки: Telegram выключен, остальные каналы как были",
      s["channels"] == {"whatsapp": True, "instagram": True, "telegram": False}, s["channels"])
for bad in ({"languages": ["xx"]}, {"default_pause": 17}, {"tone": "rude"}, {"my_language": "klingon"}, {"languages": []}):
    try:
        inbox.clean_settings(bad, base)
        check(f"настройки: отказ {bad}", False)
    except ValueError:
        check(f"настройки: отказ {bad}", True)
check("настройки: неизвестный ключ игнорируется", "evil" not in inbox.clean_settings({"evil": 1}, base))

check("кто пишет: входящее = клиент", inbox._who("inbound", None) == "client")
check("кто пишет: исходящее агента = бот", inbox._who("outbound", None) == "bot")
check("кто пишет: исходящее владельца = вы", inbox._who("outbound", "owner") == "owner")
check("телефон: служебный tg: скрыт", inbox.real_phone("tg:123") is None and inbox.real_phone("+996700") == "+996700")
check("превью: без file_id", "file_id" not in inbox._preview("📎 Отправлен файл «КП.pdf» [file_id=abc]\nвот"))
check("тело: подпись файла убрана, если есть карточка файла",
      inbox._clean_body("📎 Отправлен файл «КП.pdf» [file_id=abc]\nвот КП", True) == "вот КП")
check("тело: заглушка вложения убрана", inbox._clean_body("🖼 Изображение (обрабатывается…)", True) == "")

msgs = inbox._assist_messages("translate", "Здравствуйте", language="zh", tone="formal")
check("помощник: перевод называет язык", "Китайский" in msgs[0]["content"])
msgs = inbox._assist_messages("fix", "превет", language=None, tone="friendly")
check("помощник: исправление не меняет язык и факты", "не меняй" in msgs[0]["content"])
msgs = inbox._assist_messages("draft", "", language=None, tone="neutral", context="Знания: доставка 200 сом")
check("помощник: черновик опирается на знания", "доставка" in msgs[1]["content"])


# Пауза бота: ответ, начатый до паузы, не уходит (execute_tool, клиентский контур)
class _Q:
    def __init__(self, row): self.row = row
    def filter(self, *a, **k): return self
    def first(self): return self.row
class _DB:
    def __init__(self, row): self.row = row
    def query(self, *a): return _Q(self.row)
    def rollback(self): pass
paused_contact = types.SimpleNamespace(ai_paused_until=datetime.utcnow() + timedelta(minutes=30))
free_contact = types.SimpleNamespace(ai_paused_until=None)
cid = str(uuid.uuid4())
ctx = {"surface": "postcall", "current_contact_id": cid, "user": None, "user_id": None, "agent_config_id": None}
out = asyncio.run(agent_tools.execute_tool("whatsapp_send_message", {"agent_contact_id": cid, "text": "hi"}, ctx, _DB(paused_contact)))
check("бот на паузе: отправка клиенту в PostCall заблокирована", "сам ведёт переписку" in json.loads(out).get("error", ""), out)
check("бот не на паузе: проверка паузы пропускает", agent_tools._paused_send_refusal(_DB(free_contact), cid) is None)
check("чат владельца с агентом: пауза не мешает команде", agent_tools._paused_send_refusal(_DB(paused_contact), None) is None)

# Планировщик и блокировщик знают задачи владельца
import inspect as _insp
from backend.core import task_scheduler
from backend.services import subscription_blocker
src = _insp.getsource(task_scheduler.TaskScheduler.execute_agent_task)
check("планировщик: звонок по команде владельца на Старте разрешён",
      "owner_command and user.has_agent_chat_access()" in src)
check("планировщик: окно звонков агента не переносит команду владельца", "and not owner_command" in src)
check("блокировщик: задачи владельца Старта не отменяются",
      'Task.created_by != "owner"' in _insp.getsource(subscription_blocker.check_expired_agent_subscriptions))


# ---- «Почему бот молчит» (bot_issues): раньше все причины были молчаливыми
from types import SimpleNamespace as _N
from unittest.mock import MagicMock as _MM


def _issues(user, agent, calls, has_chats=False):
    db = _MM()
    q = db.query.return_value.filter.return_value
    q.order_by.return_value.limit.return_value.all.return_value = calls
    q.first.return_value = ("x",) if has_chats else None
    return inbox.bot_issues(db, user, _N(id="c1"), agent)


_ok_user = _N(agent_mode=lambda: "reactive", credits_balance=5000)
_agent = _N(id="a1", is_active=True)
check("bot_issues: всё в порядке → пусто", _issues(_ok_user, _agent, []) == [])
codes = [i["code"] for i in _issues(_N(agent_mode=lambda: "reactive", credits_balance=0), _agent, [])]
check("bot_issues: нет кредитов", codes == ["no_credits"], codes)
codes = [i["code"] for i in _issues(_N(agent_mode=lambda: None, credits_balance=5000), _N(id="a1", is_active=False), [])]
check("bot_issues: бот выключен и нет доступа", codes == ["agent_off", "no_access"], codes)
_iss = _issues(_N(agent_mode=lambda: "reactive", credits_balance=0), _agent, [])
check("bot_issues: Старт ведёт во вкладку кредитов", _iss[0]["url"] == "/static/start-bot.html#s-credits", _iss[0]["url"])
_iss = _issues(_N(agent_mode=lambda: "full", credits_balance=0), _agent, [])
check("bot_issues: Profi ведёт на страницу агента", _iss[0]["url"] == "/static/agent.html", _iss[0]["url"])
_call = _N(created_at=datetime(2026, 11, 12, 13, 46), postcall_log={
    "call_direction": "telegram_inbound", "warning": "no_reply_to_client",
    "tool_calls": [{"tool": "telegram_send_message", "result": {"ok": False, "error": "FloodWait"}}]})
_iss = _issues(_ok_user, _agent, [_call])
check("bot_issues: ответ не ушёл + причина отправки",
      len(_iss) == 1 and _iss[0]["code"] == "no_reply" and "FloodWait" in _iss[0]["text"] and _iss[0].get("at"), _iss)
_iss = _issues(_ok_user, _agent, [_N(created_at=None, postcall_log={"call_direction": "whatsapp_inbound", "error": "timeout"})])
check("bot_issues: сбой модели", [i["code"] for i in _iss] == ["postcall_error"], _iss)
_iss = _issues(_ok_user, _agent, [_N(created_at=None, postcall_log={"call_direction": "outbound", "error": "x"})])
check("bot_issues: ошибки звонков не путаются с перепиской", _iss == [], _iss)
check("bot_issues: нет бота", [i["code"] for i in _issues(_ok_user, None, [])] == ["no_agent"])

# ============================================================ часть 2 (PostgreSQL)
if not PG:
    print("SKIP часть 2: INBOX_TEST_DATABASE_URL не задан")
else:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from backend.models.base import Base
    import backend.models  # noqa: регистрирует все модели
    from backend.models.user import User
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact
    from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppChat, AgentWhatsAppMessage
    from backend.models.agent_telegram_account import AgentTelegramAccount, AgentTelegramMessage
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.task import Task, TaskStatus
    from backend.services import whatsapp_service

    engine = create_engine(PG)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    now = datetime.utcnow()
    u = User(email=f"o{uuid.uuid4().hex[:6]}@x.kg", password_hash="x", credits_balance=0)
    db.add(u); db.flush()
    agent = AgentConfig(user_id=u.id, name="Бот", is_active=True)
    db.add(agent); db.flush()
    other = User(email=f"z{uuid.uuid4().hex[:6]}@x.kg", password_hash="x")
    db.add(other); db.flush()

    def contact(name, phone, **kw):
        c = AgentContact(agent_config_id=agent.id, user_id=u.id, name=name, phone=phone, status="new", memory={}, **kw)
        db.add(c); db.flush(); return c
    aibek = contact("Айбек", "+996700111222", memory_x=None) if False else contact("Айбек", "+996700111222")
    mira = contact("Мира", "ig:555")
    old = contact("Старый", "+996555000000")
    wa = AgentWhatsAppAccount(agent_config_id=agent.id, user_id=u.id, instance_name="i1", webhook_secret="s",
                              status="connected", wa_phone="996700000001")
    db.add(wa); db.flush()
    chat = AgentWhatsAppChat(account_id=wa.id, remote_jid="996700111222@s.whatsapp.net", phone="996700111222",
                             agent_contact_id=aibek.id)
    stranger = AgentWhatsAppChat(account_id=wa.id, remote_jid="996777@s.whatsapp.net", phone="996777", push_name="Незнакомец")
    db.add_all([chat, stranger]); db.flush()
    tg = AgentTelegramAccount(agent_config_id=agent.id, user_id=u.id, status="connected", session_encrypted="x")
    db.add(tg); db.flush()

    def wam(c, d, body, mins, by=None, ch=chat):
        m = AgentWhatsAppMessage(agent_config_id=agent.id, account_id=wa.id, chat_id=ch.id,
                                 agent_contact_id=(c.id if c else None), direction=d, body=body, sent_by=by,
                                 created_at=now - timedelta(minutes=mins))
        db.add(m); return m
    wam(aibek, "inbound", "Сколько стоит меню на 30 человек?", 50)
    wam(aibek, "outbound", "45 000 сом", 49)
    db.add(AgentTelegramMessage(account_id=tg.id, agent_contact_id=aibek.id, direction="inbound",
                                body="А доставка есть?", created_at=now - timedelta(minutes=5)))
    db.add(AgentInstagramMessage(agent_config_id=agent.id, agent_contact_id=mira.id, direction="inbound",
                                 body="Спасибо!", created_at=now - timedelta(minutes=20)))
    db.add(AgentInstagramMessage(agent_config_id=agent.id, agent_contact_id=mira.id, direction="outbound",
                                 sent_by="owner", body="Ждём вас", created_at=now - timedelta(minutes=19)))
    wam(old, "inbound", "давнее", 60 * 24 * 5)
    wam(None, "inbound", "Здравствуйте, это кафе?", 3, ch=stranger)
    mira.ai_paused_until = now + timedelta(hours=1)
    db.add(Task(is_agent_task=True, agent_contact_id=aibek.id, user_id=u.id, status=TaskStatus.SCHEDULED,
                scheduled_time=now + timedelta(days=1), title="Перезвонить", channel="call"))
    db.add(Task(is_agent_task=True, agent_contact_id=aibek.id, user_id=u.id, status=TaskStatus.SCHEDULED,
                scheduled_time=now + timedelta(hours=2), title="Напомнить про КП", channel="owner", created_by="owner"))
    db.commit()

    res = inbox.list_dialogs(db, u)
    names = [i["name"] for i in res["items"]]
    check("список: свежие сверху, один клиент = одна строка", names == ["Айбек", "Мира", "Старый"], names)
    a = res["items"][0]
    check("список: каналы клиента объединены", a["channels"] == ["telegram", "whatsapp"], a["channels"])
    check("список: последнее сообщение из Telegram", a["last_channel"] == "telegram" and a["preview"] == "А доставка есть?")
    check("список: ждёт ответа", a["waiting"] is True)
    check("список: непрочитанные за сутки (не открывал)", a["unread"] == 2, a["unread"])
    check("список: давнее сообщение не считается непрочитанным", res["items"][2]["unread"] == 0)
    check("список: ближайшая задача и её автор", a["tasks_count"] == 2 and a["next_task"]["created_by"] == "owner", a["next_task"])
    m = res["items"][1]
    check("список: последнее от вас", m["last_by"] == "owner" and not m["waiting"])
    check("список: бот на паузе", m["paused"] and not m["paused_forever"])
    check("список: вы ответили клиенту — его сообщение не «непрочитанное»", m["unread"] == 0, m["unread"])
    c = res["counts"]
    check("папки: счётчики", (c["all"], c["waiting"], c["unread"], c["paused"], c["no_card"]) == (3, 2, 1, 1, 1), c)

    check("папка «Ждут ответа»", [i["name"] for i in inbox.list_dialogs(db, u, folder="waiting")["items"]] == ["Айбек", "Старый"])
    check("папка «Бот на паузе»", [i["name"] for i in inbox.list_dialogs(db, u, folder="paused")["items"]] == ["Мира"])
    check("фильтр по каналу Instagram", [i["name"] for i in inbox.list_dialogs(db, u, channel="instagram")["items"]] == ["Мира"])
    check("поиск по тексту сообщения", [i["name"] for i in inbox.list_dialogs(db, u, q="доставка")["items"]] == ["Айбек"])
    check("поиск по цифрам номера", [i["name"] for i in inbox.list_dialogs(db, u, q="0111")["items"]] == ["Айбек"])
    check("фильтр по стадии", inbox.list_dialogs(db, u, stage="active")["items"] == [])
    st = dict(inbox.DEFAULT_SETTINGS, channels={"whatsapp": True, "instagram": False, "telegram": True})
    check("настройка: выключенный канал не показывается",
          [i["name"] for i in inbox.list_dialogs(db, u, settings=st)["items"]] == ["Айбек", "Старый"])
    page = inbox.list_dialogs(db, u, limit=1)
    check("страницы: есть продолжение", page["has_more"] and len(page["items"]) == 1)
    before = datetime.fromisoformat(page["items"][0]["last_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    check("страницы: следующая", [i["name"] for i in inbox.list_dialogs(db, u, before=before)["items"]] == ["Мира", "Старый"])
    check("чужой владелец ничего не видит", inbox.list_dialogs(db, other)["items"] == [])

    inbox.mark_read(db, u.id, aibek.id)
    check("прочитал → непрочитанных нет", inbox.list_dialogs(db, u)["items"][0]["unread"] == 0)

    nc = inbox.list_dialogs(db, u, folder="no_card")["items"]
    check("без карточки: незнакомый из WhatsApp", len(nc) == 1 and nc[0]["name"] == "Незнакомец")
    tl = inbox.unlinked_timeline(db, u, nc[0]["chat_id"])
    check("без карточки: лента чата", [i["text"] for i in tl["items"]] == ["Здравствуйте, это кафе?"])
    newc = inbox.link_unlinked(db, u, nc[0]["chat_id"], name="Асель")
    check("без карточки → карточка, сообщения перешли",
          db.query(AgentWhatsAppMessage).filter_by(agent_contact_id=newc.id).count() == 1 and newc.name == "Асель")
    check("после привязки папка пуста", inbox.list_dialogs(db, u, folder="no_card")["items"] == [])
    try:
        inbox.link_unlinked(db, other, stranger.id)
        check("чужой чат не привязать", False)
    except LookupError:
        check("чужой чат не привязать", True)

    t = inbox.timeline(db, aibek)
    check("лента: все каналы по времени", [(i["channel"], i["who"]) for i in t["items"]] ==
          [("whatsapp", "client"), ("whatsapp", "bot"), ("telegram", "client")], [(i["channel"], i["who"]) for i in t["items"]])
    t2 = inbox.timeline(db, aibek, limit=2)
    check("лента: «показать раньше»", t2["has_more"] and len(t2["items"]) == 2)

    # Отправка владельца: WhatsApp без сети (шлюз подменён)
    sent = []
    async def fake_send_text(instance, to, text, delay_ms=None):
        sent.append((to, text)); return {"ok": True, "message_id": "WAID-1"}
    whatsapp_service.send_text = fake_send_text
    whatsapp_service.is_configured = lambda: True
    card = inbox.contact_card(db, u, aibek)
    check("карточка: каналы ответа", card["send_channels"]["whatsapp"]["can_send"]
          and not card["send_channels"]["instagram"]["can_send"], card["send_channels"])
    check("карточка: задачи с автором", {x["created_by"] for x in card["tasks"]} == {"agent", "owner"})
    # Последним клиент писал в Telegram, но Telegram на сервере не настроен → туда нельзя, берём WhatsApp
    check("канал по умолчанию — последний доступный, где писал клиент",
          inbox.default_channel(db, aibek, card["send_channels"]) == "whatsapp")
    av = {"whatsapp": {"can_send": True}, "telegram": {"can_send": True}}
    check("канал по умолчанию — где клиент писал последним", inbox.default_channel(db, aibek, av) == "telegram")
    # 3 исходящих подряд: у агента сработал бы «не больше 2 подряд», владельцу можно
    for i in range(3):
        r = asyncio.run(inbox.send_message(db, u, aibek, channel="whatsapp", text=f"Да, доставка есть {i}"))
    check("отправка: ушло в WhatsApp, лимит агента владельца не держит", len(sent) == 3 and r["ok"], sent)
    row = db.query(AgentWhatsAppMessage).filter_by(wa_message_id="WAID-1").first()
    check("отправка: сохранено как «вы» с id WhatsApp", row is not None and row.sent_by == "owner")
    check("эхо webhook'а узнаётся по id — дубля не будет", whatsapp_service.message_exists(db, agent.id, "WAID-1"))
    db.refresh(aibek)
    left = (aibek.ai_paused_until - datetime.utcnow()).total_seconds() / 60
    check("отправка: бот молчит 30 мин (настройка канала)", 28 <= left <= 31, left)
    r = asyncio.run(inbox.send_message(db, u, aibek, channel="whatsapp", text="ок", pause_minutes=-1,
                                       original={"text": "ок", "language": "ru"}))
    db.refresh(aibek)
    check("отправка: «бот молчит до включения»", aibek.ai_paused_until.year == 9999 and r["paused_forever"])
    check("отправка: исходный текст перевода сохранён", inbox.timeline(db, aibek)["items"][-1]["original"]["text"] == "ок")
    try:
        asyncio.run(inbox.send_message(db, u, mira, channel="instagram", text="x"))
        check("отправка: Instagram без подключения — понятный отказ", False)
    except inbox.SendError as e:
        check("отправка: Instagram без подключения — понятный отказ", "Instagram" in e.message, e.message)

    # Звонок по команде владельца: тариф Старт, нет номера → понятная ошибка
    u.agent_mode = lambda: "reactive"            # тариф Старт
    u.has_agent_chat_access = lambda: True
    try:
        inbox.queue_owner_call(db, u, aibek)
        check("звонок: без голоса/номера — понятная ошибка", False)
    except inbox.SendError as e:
        check("звонок: без голоса/номера — понятная ошибка", e.code in ("no_voice", "no_number"), e.code)
    rem = inbox.create_reminder(db, u, aibek, "Позвонить самому", datetime.utcnow() + timedelta(hours=3))
    check("напоминание владельцу: channel=owner, автор вы", rem["channel"] == "owner" and rem["created_by"] == "owner")

    # ---------- HTTP: /api/inbox/* поверх той же базы ----------
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import inbox as inbox_api
    from backend.core.dependencies import get_current_user
    from backend.db.session import get_db
    from backend.services import agent_media_service
    app = FastAPI()
    app.include_router(inbox_api.router, prefix="/api/inbox")
    u.is_admin = False
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: db
    cl = TestClient(app)
    r = cl.get("/api/inbox/overview")
    check("API overview: режим, бот, каналы, языки", r.status_code == 200 and r.json()["agent_mode"] == "reactive"
          and r.json()["agents"][0]["channels"]["whatsapp"]["connected"] and len(r.json()["languages"]) >= 10, r.text[:200])
    r = cl.get("/api/inbox/dialogs", params={"folder": "waiting"})
    check("API dialogs: папка + latest_at", r.status_code == 200 and r.json()["latest_at"], r.text[:200])
    check("API dialogs: неверная папка → 400", cl.get("/api/inbox/dialogs", params={"folder": "x"}).status_code == 400)
    r = cl.get(f"/api/inbox/dialogs/{aibek.id}")
    check("API открыть диалог: карточка + лента + канал", r.status_code == 200 and r.json()["default_channel"] == "whatsapp"
          and r.json()["contact"]["display_name"] == "Айбек" and r.json()["stages"], r.text[:200])
    check("API чужой/кривой id → 404", cl.get(f"/api/inbox/dialogs/{uuid.uuid4()}").status_code == 404
          and cl.get("/api/inbox/dialogs/not-a-uuid").status_code == 404)
    r = cl.post(f"/api/inbox/dialogs/{aibek.id}/send", data={"channel": "whatsapp", "text": "Привет из API", "pause": "120"})
    db.refresh(aibek)
    check("API send текст + пауза 2 ч", r.status_code == 200 and r.json()["ok"], r.text[:200])
    check("API send: пауза «до включения» не укорачивается на 2 ч", aibek.ai_paused_until.year == 9999)
    check("API send: пустое → 400", cl.post(f"/api/inbox/dialogs/{aibek.id}/send", data={"channel": "whatsapp"}).status_code == 400)
    check("API send: кривая пауза → 400",
          cl.post(f"/api/inbox/dialogs/{aibek.id}/send", data={"channel": "whatsapp", "text": "x", "pause": "abc"}).status_code == 400)

    media = []
    async def fake_store(db_, **kw):
        from backend.models.agent_file import AgentFile
        row = AgentFile(user_id=kw["user_id"], agent_config_id=kw["agent_config_id"], source=kw.get("source"),
                        kind=kw.get("kind") or agent_media_service.classify(kw.get("mime") or "", kw["filename"]),
                        filename=kw["filename"], mime_type=kw.get("mime") or "application/octet-stream",
                        size_bytes=len(kw["data"]), status="ready", storage_key="k/" + kw["filename"],
                        agent_contact_id=kw.get("agent_contact_id"), channel=kw.get("channel"))
        db_.add(row); db_.commit(); db_.refresh(row); return row
    async def fake_load(row): return b"PNGDATA"
    async def fake_media(instance, to, **kw):
        media.append(kw["kind"]); return {"ok": True, "message_id": "WAID-M" + str(len(media))}
    async def fake_audio(instance, to, data_b64):
        media.append("voice"); return {"ok": True, "message_id": "WAID-V"}
    agent_media_service.store_file = fake_store
    agent_media_service.load_bytes = fake_load
    agent_media_service.file_link = lambda row, expires_seconds=0: "https://r2/" + row.filename
    whatsapp_service.send_media = fake_media
    whatsapp_service.send_audio = fake_audio
    r = cl.post(f"/api/inbox/dialogs/{aibek.id}/send", data={"channel": "whatsapp", "text": "Меню"},
                files={"file": ("menu.png", b"\x89PNG" + b"0" * 500, "image/png")})
    check("API send: фото с ноутбука", r.status_code == 200 and media == ["image"], r.text[:200])
    r = cl.post(f"/api/inbox/dialogs/{aibek.id}/send", data={"channel": "whatsapp"},
                files={"voice": ("rec.webm", b"\x1aE\xdf\xa3" + b"0" * 4000, "audio/webm")})
    check("API send: голосовое с микрофона", r.status_code == 200 and media[-1] == "voice", r.text[:200])
    items = inbox.timeline(db, aibek)["items"]
    vm = [i for i in items if i.get("voice")]
    img = [i for i in items if (i.get("attachment") or {}).get("kind") == "image"]
    check("лента: голосовое владельца с записью для прослушивания",
          vm and vm[-1]["who"] == "owner" and vm[-1]["attachment"]["url"].startswith("https://r2/"), vm[-1:] )
    check("лента: фото с подписью, без служебного текста", img and img[-1]["text"] == "Меню", img[-1:])
    big = b"0" * (21 * 1024 * 1024)
    check("API send: файл > 20 МБ → 413",
          cl.post(f"/api/inbox/dialogs/{aibek.id}/send", data={"channel": "whatsapp"},
                  files={"file": ("big.mp4", big, "video/mp4")}).status_code == 413)

    r = cl.post(f"/api/inbox/dialogs/{aibek.id}/pause", json={"minutes": 120})
    check("API пауза 2 ч", r.status_code == 200 and r.json()["ai_paused_until"] and not r.json()["ai_paused_forever"])
    check("API пауза без срока → 400", cl.post(f"/api/inbox/dialogs/{aibek.id}/pause", json={}).status_code == 400)
    r = cl.delete(f"/api/inbox/dialogs/{aibek.id}/pause")
    check("API вернуть бота", r.status_code == 200 and r.json()["ai_paused_until"] is None)
    r = cl.post(f"/api/inbox/dialogs/{aibek.id}/stage", json={"stage": "active"})
    check("API стадия", r.status_code == 200 and r.json()["stage"] == "active")
    check("API неизвестная стадия → 400", cl.post(f"/api/inbox/dialogs/{aibek.id}/stage", json={"stage": "zzz"}).status_code == 400)
    r = cl.post(f"/api/inbox/dialogs/{aibek.id}/call")
    check("API позвонить: понятная причина отказа", r.status_code == 400 and r.json()["detail"]["message"], r.text[:200])
    r = cl.post(f"/api/inbox/dialogs/{aibek.id}/reminder",
                json={"title": "Счёт", "scheduled_time": (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z"})
    check("API напоминание", r.status_code == 200 and r.json()["created_by"] == "owner", r.text[:200])
    r = cl.post("/api/inbox/assist", json={"action": "fix", "text": "превет"})
    check("API помощник без кредитов → 402", r.status_code == 402, r.text[:200])
    r = cl.put("/api/inbox/settings", json={"tone": "rude"})
    check("API настройки: неверное → 400", r.status_code == 400)
    r = cl.put("/api/inbox/settings", json={"tone": "friendly", "channels": {"telegram": False}})
    check("API настройки сохраняются", r.status_code == 200 and r.json()["tone"] == "friendly"
          and r.json()["channels"]["telegram"] is False)
    check("API updates", cl.get("/api/inbox/updates").json()["counts"]["all"] >= 3)
    u.has_agent_chat_access = lambda: False
    check("API без тарифа → 402", cl.get("/api/inbox/overview").status_code == 402)
    u.has_agent_chat_access = lambda: True

    s2 = inbox.save_settings(db, u.id, {"languages": ["ru", "en"], "default_pause": -1})
    check("настройки сохраняются", inbox.get_settings(db, u.id)["languages"] == ["ru", "en"] and s2["default_pause"] == -1)
    db.close()

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
