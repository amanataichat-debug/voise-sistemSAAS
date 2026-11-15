"""Личный Telegram и Instagram отвечают через «Отвечать через N секунд», а не раз в 1–2 минуты.

15.11.2026: Telegram опрашивался раз в 30 с и откладывал диалог ещё на тик, Instagram —
раз в 45 с (+ ещё тик). Теперь Telegram слушает событиями на постоянном соединении
(_Listener), Instagram переопрашивает коннектор ровно к сроку (_DUE). Без сети и БД.
"""
import asyncio, logging, os, sys, types
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("MECH_TEST_DATABASE_URL")
os.environ.update(HOST_URL="http://x", DATABASE_URL=PG or "sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)
import backend.core.config  # noqa: F401
from backend.core import telegram_user_poller as tgp, instagram_poller as igp
from backend.services import telegram_user_service as tgs

failures = 0
def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ---------- Telegram: слушатель разбирает через reply_delay после сообщения ----------
class FakeClient:
    def __init__(self):
        self.handlers = []
        self.connected = False
    async def connect(self): self.connected = True
    async def is_user_authorized(self): return True
    def add_event_handler(self, fn, ev): self.handlers.append(fn)
    async def get_me(self): return types.SimpleNamespace(id=1)
    def is_connected(self): return self.connected
    async def disconnect(self): self.connected = False


async def tg_case():
    fake = FakeClient()
    tgs._new_client = lambda s="": fake
    tgs.decrypt_session = lambda t: "SESSION"
    acc = types.SimpleNamespace(id="a1", session_encrypted="x", reply_delay_seconds=1,
                                status="connected", auto_reply_enabled=True)

    class Q:
        def filter(self, *a, **k): return self
        def first(self): return acc
    class DB:
        def query(self, *a): return Q()
        def close(self): pass
        def rollback(self): pass
    tgp.SessionLocal = lambda: DB()
    tgp.POLL_SECONDS = 600
    tgp.DUE_MARGIN_SECONDS = 0.2
    polls = []
    loop = asyncio.get_running_loop()

    async def fake_poll(db, account):
        polls.append(loop.time())
        return 0
    tgp._poll_account = fake_poll

    lst = tgp._Listener("a1", token=None)
    task = asyncio.create_task(lst.run())
    await asyncio.sleep(0.15)
    check("при старте слушателя — один разбор, чтобы догнать пропущенное", len(polls) == 1, polls)
    check("отправка и опрос берут живое соединение слушателя, а не открывают второе",
          tgs._live_client("SESSION") is fake)

    class Ev:
        is_private = True
    t0 = loop.time()
    await fake.handlers[0](Ev())
    await asyncio.sleep(0.6)
    await fake.handlers[0](Ev())     # клиент дописал — срок сдвигается
    await asyncio.sleep(0.9)
    check("пока клиент дописывает — не отвечаем", len(polls) == 1, len(polls))
    await asyncio.sleep(0.6)
    check("через «Отвечать через 1 с» после ПОСЛЕДНЕГО сообщения — разбор", len(polls) == 2, len(polls))
    took = polls[-1] - (t0 + 0.6)
    check("задержка ≈ настройка + запас, а не минута", 1.0 <= took <= 1.6, round(took, 2))

    # группа/канал не будит
    class Grp:
        is_private = False
    await fake.handlers[0](Grp())
    await asyncio.sleep(1.5)
    check("сообщения групп слушатель не разбирает", len(polls) == 2)

    lst.stopped = True
    lst._wake.set()
    await asyncio.wait_for(task, 2)
    check("остановка слушателя отпускает соединение", tgs._live_client("SESSION") is None and not fake.connected)

asyncio.run(tg_case())


# ---------- Telegram: события не дошли (обновления ушли другому соединению) — ловим по состоянию ящика ----------
async def tg_state_case():
    class StateClient(FakeClient):
        pts = 100
        async def __call__(self, req):
            return types.SimpleNamespace(pts=self.pts, qts=0, seq=0)
    fake = StateClient()
    tgs._new_client = lambda s="": fake
    tgp.STATE_CHECK_SECONDS = 0.2
    polls = []
    loop = asyncio.get_running_loop()

    async def fake_poll(db, account):
        polls.append(loop.time())
        return 0
    tgp._poll_account = fake_poll
    lst = tgp._Listener("a2", token=None)
    task = asyncio.create_task(lst.run())
    await asyncio.sleep(0.6)
    n0 = len(polls)
    fake.pts = 101                     # новое сообщение, а событие не пришло
    t0 = loop.time()
    await asyncio.sleep(2.3)
    check("без события: состояние ящика изменилось → разбор через «Отвечать через»", len(polls) == n0 + 1,
          (n0, len(polls)))
    took = polls[-1] - t0 if len(polls) > n0 else 99
    check("…и тоже быстро, а не через 30 с", took <= 1.8, round(took, 2))
    lst.stopped = True
    lst._wake.set()
    await asyncio.wait_for(task, 2)

asyncio.run(tg_state_case())

# ---------- Аренда клиента: короткая, продлевается, пока разбор жив ----------
from backend.services import contact_lease as cl
check("аренда клиента короткая (оборванный разбор не держит клиента минутами)", cl.LEASE.total_seconds() <= 90)
check("аренда продлевается чаще, чем истекает", cl.RENEW_SECONDS * 2 < cl.LEASE.total_seconds())
check("продление ограничено (забытый release не держит клиента вечно)", cl.RENEW_MAX_SECONDS <= 900)

check("опрос раз в 30 с остался страховкой", tgp.POLL_SECONDS <= 600)
src = open(os.path.join(ROOT, "backend/core/telegram_user_poller.py"), encoding="utf-8").read()
check("опрос пропускает аккаунты своего слушателя", "in _LISTENERS" in src)
check("аренда продлевается только по своему токену", "last_poll_at == token" in src)

# ---------- Instagram: переопрос к сроку ----------
igp._DUE.clear()
now = datetime.utcnow()
igp.schedule_due("c1", now + timedelta(seconds=6))
igp.schedule_due("c1", now + timedelta(seconds=9))
check("срок отложенного треда — самый ранний", igp._DUE["c1"] == now + timedelta(seconds=6))
check("Instagram: обычный опрос не реже раза в 20 с", igp.CHECK_INTERVAL_SECONDS <= 20)
check("ранний переопрос не чаще раза в 2 с", igp.EARLY_MIN_GAP_SECONDS >= 2)
isrc = open(os.path.join(ROOT, "backend/core/instagram_poller.py"), encoding="utf-8").read()
check("отложенный тред ставит срок, а не ждёт целый интервал", "schedule_due(connector.id, inbound[-1][\"ts\"]" in isrc)
check("причина сбоя опроса Instagram пишется в коннектор (видно владельцу)", "_set_error(db, connector" in isrc)

from backend.api import agent as agent_api
h = agent_api._instagram_error_human
check("нет доступа к сообщениям → подсказка «Разрешить доступ к сообщениям»",
      "Разрешить доступ" in h("list_conversations: (#10) Application does not have permission"))
check("всё хорошо → без предупреждения", h(None) is None)


# ---------- PostgreSQL: аренда аккаунта Telegram и опроса Instagram ----------
if PG:
    import uuid
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from backend.models.base import Base
    import backend.models  # noqa: F401
    from backend.models.user import User
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_telegram_account import AgentTelegramAccount
    from backend.models.agent_connector import AgentConnector
    import importlib
    tgp2 = importlib.reload(tgp)
    engine = create_engine(PG)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    u = User(email=f"o{uuid.uuid4().hex[:6]}@x.kg", password_hash="x")
    db.add(u); db.flush()
    ag = AgentConfig(user_id=u.id, name="Бот", is_active=True)
    db.add(ag); db.flush()
    acc = AgentTelegramAccount(agent_config_id=ag.id, user_id=u.id, status="connected", auto_reply_enabled=True)
    con = AgentConnector(agent_config_id=ag.id, user_id=u.id, toolkit="instagram", status="connected")
    db.add_all([acc, con]); db.commit()

    t1 = tgp2._lease_acquire(db, acc.id)
    check("PG: слушатель взял аренду аккаунта", t1 is not None)
    check("PG: второй воркер аренду не берёт", tgp2._lease_acquire(db, acc.id) is None)
    check("PG: обычный опрос арендованный аккаунт не трогает", not tgp2._claim(db, acc.id))
    t2 = tgp2._lease_renew(db, acc.id, t1)
    check("PG: продление по своему токену", t2 is not None)
    check("PG: продление по чужому/старому токену не проходит", tgp2._lease_renew(db, acc.id, t1) is None)
    tgp2._lease_release(db, acc.id, t2)
    check("PG: после отпускания аккаунт берётся сразу", tgp2._lease_acquire(db, acc.id) is not None)

    tok = igp._claim(db, con.id)
    check("PG: Instagram — опрос занял коннектор", tok is not None)
    check("PG: пока опрос идёт, даже ранний переопрос не пересекается", igp._claim(db, con.id, 0) is None)
    igp._release(db, con.id, tok)
    check("PG: сразу после опроса обычный тик ждёт интервал", igp._claim(db, con.id) is None)
    import time as _t; _t.sleep(2.1)
    check("PG: ранний переопрос к сроку проходит через 2 с", igp._claim(db, con.id, igp.EARLY_MIN_GAP_SECONDS) is not None)
    con2 = db.query(AgentConnector).get(con.id)
    igp._set_error(db, con2, "list_conversations: (#10) no permission")
    db.expire_all()
    check("PG: причина сбоя Instagram сохранена в коннекторе", "(#10)" in (db.query(AgentConnector).get(con.id).last_error or ""))
else:
    print("SKIP PG-часть: MECH_TEST_DATABASE_URL не задан")

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
