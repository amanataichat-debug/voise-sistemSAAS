"""Instagram: свой аккаунт ≠ клиент (поллер, 06.11.2026) + сторож звонка + запрет звонка на свой номер.

05.11.2026: в DM `from.id` своего аккаунта не совпал с id из INSTAGRAM_GET_USER_INFO —
ответы владельца клиентам стали «сообщениями клиента», все треды склеились в карточку
@voksy_ai, агент позвонил на номер, который владелец отправил клиенту (свой номер),
и час говорил сам с собой.
"""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, sys, uuid
from datetime import datetime, timedelta

sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")

import backend.services  # noqa: F401 — порядок импорта как у остальных проверок
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, ARRAY

@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_t, _c, **_kw):
    return "JSON"

@compiles(ARRAY, "sqlite")
def _array_sqlite(_t, _c, **_kw):
    return "JSON"

from backend.models.base import Base
from backend.models.agent_connector import AgentConnector
from backend.models.agent_contact import AgentContact
from backend.models.agent_instagram import AgentInstagramConversation, AgentInstagramMessage
from backend.models.sip_gateway import SipPhoneNumber
from backend.core import instagram_poller as P
from backend.services import instagram_service as ig

failures = 0
def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1

engine = create_engine("sqlite://")
Base.metadata.create_all(engine, tables=[
    AgentConnector.__table__, AgentContact.__table__, AgentInstagramConversation.__table__,
    AgentInstagramMessage.__table__, SipPhoneNumber.__table__,
])
db = sessionmaker(bind=engine)()

AGENT = uuid.uuid4()
USER = uuid.uuid4()
APP_ID, MSG_ID = "17841400000000001", "990000000000042"   # id из GET_USER_INFO и from.id в DM
conn = AgentConnector(id=uuid.uuid4(), agent_config_id=AGENT, user_id=USER, toolkit="instagram",
                      status="connected", composio_user_id="agent_x")
db.add(conn); db.commit()

async def fake_get_me(_uid):
    # Composio вернул только app-scoped id — как в проде
    return {"ok": True, "id": APP_ID, "ids": [APP_ID], "username": "voksy_ai"}
ig.get_me = fake_get_me

now = datetime.utcnow()
def iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%S+0000")
def msg(mid, minutes_ago, frm, frm_user, to, to_user, text):
    return {"id": mid, "created_time": iso(now - timedelta(minutes=minutes_ago)),
            "from": {"id": frm, "username": frm_user},
            "to": {"data": [{"id": to, "username": to_user}]}, "message": text}

CLIENT = "880000000000777"
messages = [  # Graph: новые первыми
    msg("m3", 2, MSG_ID, "voksy_ai", CLIENT, "shop_client", "Наш номер агента 0705701707, позвоните"),
    msg("m2", 4, CLIENT, "shop_client", MSG_ID, "voksy_ai", "Магазин одежды"),
    msg("m1", 6, MSG_ID, "voksy_ai", CLIENT, "shop_client", "Здравствуйте! Какая у вас сфера?"),
]

dispatched = []
async def fake_inbound(agent_id, contact_id, text):
    dispatched.append((contact_id, text))

async def run():
    own_ids, own_user = await P._own_identity(db, conn)
    check("username своего аккаунта известен", own_user == "voksy_ai", own_user)
    parsed = P._parse_messages(messages)
    own_ids = P._learn_own_ids(db, conn, own_ids, own_user, [("c1", None, parsed)], [])
    check("from.id своего аккаунта выучен по username", MSG_ID in own_ids, sorted(own_ids))
    check("выученный id сохранён в коннекторе", MSG_ID in (conn.external_account_id or ""), conn.external_account_id)
    ran = await P._process_conversation(db, conn, None, "c1", parsed, own_ids, own_user, fake_inbound)
    await asyncio.sleep(0)
    row = db.query(AgentInstagramConversation).filter_by(ig_conversation_id="c1").first()
    check("тред привязан к клиенту, а не к своему аккаунту", row.igsid == CLIENT and row.ig_username == "shop_client",
          f"{row.igsid} @{row.ig_username}")
    contact = db.query(AgentContact).filter_by(id=row.agent_contact_id).first()
    check("карточка клиента = @shop_client", contact is not None and contact.name == "@shop_client",
          contact and contact.name)
    rows = {m.ig_message_id: m for m in db.query(AgentInstagramMessage).all()}
    check("сообщения владельца — исходящие (sent_by=owner)",
          rows["m1"].direction == "outbound" and rows["m3"].direction == "outbound" and rows["m3"].sent_by == "owner")
    check("сообщение клиента — входящее", rows["m2"].direction == "inbound")
    check("оркестратор получил только текст клиента",
          ran and dispatched and "0705701707" not in dispatched[-1][1] and "Магазин" in dispatched[-1][1], dispatched)

    # ---- ремонт старого сломанного треда: карточка @voksy_ai с igsid своего аккаунта ----
    bad = AgentContact(id=uuid.uuid4(), agent_config_id=AGENT, user_id=USER, phone=f"ig:{MSG_ID}",
                       name="@voksy_ai", status="new")
    db.add(bad); db.flush()
    row2 = AgentInstagramConversation(agent_config_id=AGENT, ig_conversation_id="c2", igsid=MSG_ID,
                                      ig_username="voksy_ai", agent_contact_id=bad.id,
                                      last_processed_at=now)
    db.add(row2); db.flush()
    db.add(AgentInstagramMessage(agent_config_id=AGENT, agent_contact_id=bad.id, ig_conversation_id="c2",
                                 ig_message_id="x1", direction="inbound", body="Здравствуйте"))
    db.add(AgentInstagramMessage(agent_config_id=AGENT, agent_contact_id=bad.id, ig_conversation_id="c2",
                                 ig_message_id="x2", direction="inbound", body="Ответ владельца"))
    db.commit()
    C2 = "880000000000999"
    parsed2 = P._parse_messages([
        msg("x2", 30, MSG_ID, "voksy_ai", C2, "aibek", "Ответ владельца"),
        msg("x1", 31, C2, "aibek", MSG_ID, "voksy_ai", "Здравствуйте"),
    ])
    check("сломанный тред распознан", P._row_broken(row2, own_ids, own_user))
    await P._process_conversation(db, conn, row2, "c2", parsed2, own_ids, own_user, fake_inbound)
    db.refresh(row2)
    check("ремонт: тред перепривязан к клиенту", row2.igsid == C2 and row2.agent_contact_id != bad.id, row2.igsid)
    m1 = db.query(AgentInstagramMessage).filter_by(ig_message_id="x1").first()
    m2 = db.query(AgentInstagramMessage).filter_by(ig_message_id="x2").first()
    check("ремонт: сообщения переехали в карточку клиента", m1.agent_contact_id == row2.agent_contact_id)
    check("ремонт: ответ владельца стал исходящим", m2.direction == "outbound" and m2.sent_by == "owner")

    # ---- один igsid у двух тредов = свой аккаунт ----
    r_a = types_row("q1", "555"); r_b = types_row("q2", "555")
    learned = P._learn_own_ids(db, conn, set(), "", [], [r_a, r_b])
    check("igsid в двух тредах → свой", "555" in learned)

class _R:
    def __init__(self, c, i): self.ig_conversation_id, self.igsid = c, i
def types_row(c, i): return _R(c, i)

asyncio.run(run())

# ---- свой номер платформы ----
from backend.services.sip_gateway_service import SipGatewayService
db.add(SipPhoneNumber(id=uuid.uuid4(), user_id=USER, phone_number="996705701707", gateway_id="sip-gw-1"))
db.commit()
check("0705701707 — номер платформы", SipGatewayService.is_platform_number(db, "0705701707"))
check("номер клиента — не платформы", not SipGatewayService.is_platform_number(db, "0700123456"))
num = db.query(SipPhoneNumber).first()
try:
    SipGatewayService.queue_outbound_call(db, user_id=USER, to_number="+996 705 701 707", caller_number=num,
                                          assistant_type="openai", assistant_id=None)
    check("звонок на свой номер отклонён", False)
except ValueError as e:
    check("звонок на свой номер отклонён", "номер самой платформы" in str(e), str(e))

# ---- сторож тишины ----
from backend.services.call_transcript import CallTranscript
import time
t = CallTranscript()
t.last_activity_at -= 100
t.on_event({"type": "transcript.delta", "role": "user", "delta": "алло"})
check("речь обновляет время активности", time.time() - t.last_activity_at < 1)
t.last_activity_at -= 100
t.on_event({"type": "output.transcription", "text": "  "})
check("пустой текст активностью не считается", time.time() - t.last_activity_at > 50)

print(f"\n{'OK' if not failures else f'FAILURES: {failures}'}")
sys.exit(1 if failures else 0)
