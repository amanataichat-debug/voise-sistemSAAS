"""Звонки WhatsApp голосового агента (ТЗ docs/tz_whatsapp_calls.md, шлюз infra/wa-call-gateway/).

Часть 1 (без базы): настоящий HandlerSocket на линии WhatsApp — вход 16 кГц → хендлеру 24 кГц, выход
24 → 16 кГц без телефонной обработки (срез 200 Гц, −3 дБ) и с широкой полосой (5 кГц проходит), SIP-линия
8 кГц — как раньше; перебивание (clear) и метка hangup; выключатели WA_CALLS_ENABLED / WA_CALLS_USERS;
install.sh перечисляет каждый файл шлюза; белый лейбл в текстах для клиента.

Часть 2 (PostgreSQL, WA_CALLS_TEST_DATABASE_URL): решение по входящему (accept с ассистентом агента,
reject: выключено, номер не привязан, агент выключен, приём выключен, кошелёк пуст — с записью в sip_calls),
номер по LID из переписки, состояния аккаунта (QR → на связи → блокировка), события звонка, исходящий:
лимиты (в сутки, в час, «первым — только тем, кто писал»), свой номер, очередь шлюза и originate с
account_id; выбор канала (O! → SIP, иначе WhatsApp, Старт — без исходящих), инструменты агента
(check_phone_operator, agent_can_call_contact, проверка звонка), управляющий сокет и медиа-сокет
/ws/sip/{id} целиком через TestClient (токен шлюза, start 16 кГц, профиль без G.711, «Звонок WhatsApp»
в журнале), приём записи по токену шлюза, PostCall для исходящего из скрипта.
"""
import asyncio
import base64
import json
import math
import os
import re
import struct
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("WA_CALLS_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="wa-calls-test-secret-0123456789abcdef",
                  WA_CALL_GATEWAY_TOKEN="wa-secret", SIP_GATEWAY_TOKEN="sip-secret")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.core.config import settings  # noqa: E402
from backend.websockets import sip_media_adapter as sma  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def tone(rate, ms, freq, amp=8000):
    n = int(rate * ms / 1000)
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate))) for i in range(n))


def rms(pcm):
    n = len(pcm) // 2
    if not n:
        return 0.0
    vals = struct.unpack(f"<{n}h", pcm[: n * 2])
    return math.sqrt(sum(v * v for v in vals) / n)


class FakeBridge:
    """Сокет шлюза/моста со стороны бэкенда: то, что видит HandlerSocket."""

    def __init__(self):
        self.inbox = asyncio.Queue()
        self.bytes_out = []
        self.text_out = []

    async def receive(self):
        return await self.inbox.get()

    async def send_bytes(self, data):
        self.bytes_out.append(data)

    async def send_text(self, text):
        self.text_out.append(json.loads(text))

    async def close(self, *a, **k):
        pass

    headers = {}
    query_params = {}
    client = None


async def adapter_checks():
    # ---- линия WhatsApp: 16 кГц, без телефонной обработки
    br = FakeBridge()
    sock = sma.HandlerSocket(br, "eleven", "t1", line_rate=16000, channel="whatsapp")
    sock.start()
    for _ in range(5):
        await br.inbox.put({"type": "websocket.receive", "bytes": tone(16000, 20, 300)})
    got = 0
    while True:
        try:
            msg = await asyncio.wait_for(sock.receive(), 0.3)
        except asyncio.TimeoutError:
            break
        data = json.loads(msg["text"])
        got += len(base64.b64decode(data["audio"]))
    check("WhatsApp: 5 кадров по 20 мс (640 байт, 16 кГц) → хендлеру 24 кГц (~960 байт на кадр)",
          abs(got - 5 * 960) <= 960, got)
    low = tone(24000, 100, 150)
    await sock.send_json({"type": "response.audio.delta", "delta": base64.b64encode(low).decode()})
    out = b"".join(br.bytes_out)
    check("WhatsApp: ответ 24 кГц → в линию 16 кГц (100 мс = 3200 байт)", len(out) == 3200, len(out))
    check("WhatsApp: без среза басов и −3 дБ (PhoneVoice только для оператора)", rms(out) > 0.8 * rms(low),
          f"{rms(out):.0f} vs {rms(low):.0f}")
    br.bytes_out.clear()
    hi = tone(24000, 100, 5000)
    await sock.send_json({"type": "response.audio.delta", "delta": base64.b64encode(hi).decode()})
    out_hi = b"".join(br.bytes_out)
    check("WhatsApp: широкая полоса — 5 кГц проходит (телефон срезал бы до 3,4 кГц)",
          rms(out_hi[400:]) > 0.6 * rms(hi), f"{rms(out_hi[400:]):.0f} vs {rms(hi):.0f}")
    await sock.send_json({"type": "speech.started"})
    check("WhatsApp: перебивание → шлюзу clear", {"type": "clear"} in br.text_out, br.text_out)
    stopped = json.loads((await asyncio.wait_for(sock.receive(), 2))["text"])
    check("WhatsApp: хендлеру audio_playback.stopped, как от браузера", stopped.get("type") == "audio_playback.stopped")
    await sock.send_json({"type": "call.hangup", "reason": "bye", "drain": True})
    await asyncio.sleep(0.05)
    check("WhatsApp: прощание с доигрыванием → метка hangup шлюзу", {"type": "mark", "name": "hangup"} in br.text_out)
    await br.inbox.put({"type": "websocket.receive", "text": json.dumps({"type": "mark", "name": "hangup"})})
    await asyncio.sleep(0.1)
    check("WhatsApp: метка вернулась → hangup шлюзу",
          any(m.get("type") == "hangup" for m in br.text_out), br.text_out)
    check("WhatsApp: секунд звука — по 16 кГц", sock.bytes_per_second == 32000)
    await sock.finish()

    # ---- линия оператора: как раньше
    br2 = FakeBridge()
    sip = sma.HandlerSocket(br2, "eleven", "t2")
    sip.start()
    await sip.send_json({"type": "response.audio.delta", "delta": base64.b64encode(low).decode()})
    out2 = b"".join(br2.bytes_out)
    check("SIP: по-прежнему 8 кГц (100 мс = 1600 байт)", len(out2) == 1600, len(out2))
    check("SIP: телефонная обработка на месте (басы срезаны)", rms(out2) < 0.6 * rms(low),
          f"{rms(out2):.0f} vs {rms(low):.0f}")
    await sip.finish()
    bare = sma.HandlerSocket.__new__(sma.HandlerSocket)
    check("адаптер без конструктора — линия оператора 8 кГц", bare.line_rate == 8000 and bare.channel == "phone")
    check("срез фильтра: 8 кГц → 3400 Гц, 16 кГц → широкая полоса",
          sma.antialias_cutoff(8000) == 3400 and sma.antialias_cutoff(16000) > 7000)


asyncio.run(adapter_checks())

# ---- выключатели
from backend.services import wa_call_service as wcs  # noqa: E402

settings.WA_CALLS_ENABLED, settings.WA_CALLS_USERS = False, ""
check("выключатель: по умолчанию выключено", not wcs.feature_enabled_for(uuid.uuid4()))
u1, u2 = uuid.uuid4(), uuid.uuid4()
settings.WA_CALLS_ENABLED, settings.WA_CALLS_USERS = True, f"{u1}, {uuid.uuid4()}"
check("выключатель: проба — только владельцы из WA_CALLS_USERS",
      wcs.feature_enabled_for(u1) and not wcs.feature_enabled_for(u2))
settings.WA_CALLS_USERS = ""
check("выключатель: WA_CALLS_USERS пусто — всем (этап 3)", wcs.feature_enabled_for(u2))

# ---- install.sh перечисляет все файлы шлюза
gw = os.path.join(ROOT, "infra", "wa-call-gateway")
inst = open(os.path.join(gw, "install.sh"), encoding="utf-8").read()
listed = set(re.findall(r"^\s+(gateway/\S+|\S+\.service|\S+\.md|licenses/\S+)\s*$", inst, re.M))
need = {f"gateway/{f}" for f in os.listdir(os.path.join(gw, "gateway"))
        if (f.endswith(".go") and not f.endswith("_test.go")) or f in ("go.mod", "go.sum")}
check("install.sh скачивает каждый файл шлюза (go.mod, go.sum, *.go)", need <= listed, sorted(need - listed))
check("install.sh: лицензии библиотек ставятся на сервер", {"licenses/meowcaller-MIT.txt",
                                                            "licenses/hypermeow-MPL-2.0.txt"} <= listed)
unit = open(os.path.join(gw, "voksy-wa-call.service"), encoding="utf-8").read()
check("systemd: каталоги данных совпадают с install.sh",
      "/opt/voksy-wa-call/data" in unit and "/var/spool/voksy-wa-call-rec" in unit
      and "DATA_DIR=$APP_DIR/data" in inst and "REC_DIR=/var/spool/voksy-wa-call-rec" in inst)
check("install.sh: входящие порты закрыты, кроме SSH", "ufw default deny incoming" in inst and "ufw allow OpenSSH" in inst)
check("install.sh: сессии WhatsApp — права 700/600", "install -d -m 700" in inst and "chmod 600" in inst)

# ---- белый лейбл: клиент не видит названий библиотек и провайдеров
_BRANDS = re.compile(r"meowcaller|whatsmeow|hypermeow|green\s*api|evolution|asterisk", re.I)
texts = list(wcs.REJECT_TEXT.values())
src = open(os.path.join(ROOT, "backend", "services", "wa_call_service.py"), encoding="utf-8").read()
texts += re.findall(r'return \(?"([^"]+)"', src)
texts += re.findall(r'line = f"([^"]+)"', src)
bad = [t for t in texts if _BRANDS.search(t)]
check("белый лейбл: в отказах и сообщениях владельцу нет названий библиотек/провайдеров", not bad and len(texts) > 10,
      bad)

if not PG:
    print("SKIP часть 2: задайте WA_CALLS_TEST_DATABASE_URL (тестовая PostgreSQL, БУДЕТ ОЧИЩЕНА)")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ═══════════════════════════ часть 2: PostgreSQL ═══════════════════════════
from sqlalchemy import text  # noqa: E402
from backend.db.session import SessionLocal, engine  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_contact import AgentContact  # noqa: E402
from backend.models.agent_call import AgentCall  # noqa: E402
from backend.models.eleven_assistant import ElevenAssistantConfig  # noqa: E402
from backend.models.sip_gateway import SipCall, SipCallStatus, SipPhoneNumber  # noqa: E402
from backend.models.wa_call import AgentWaCallAccount, WaCallStatus  # noqa: E402
from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppChat, AgentWhatsAppMessage  # noqa: E402
from backend.core.security import hash_password  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)

import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app_module.ensure_wallet_columns()
app_module.ensure_user_profile_columns()
app_module.ensure_chat_v2_columns()
app_module.ensure_wa_call_tables()
app_module.ensure_wa_call_tables()  # идемпотентно

# PostCall не запускаем (LLM): только фиксируем, что звонок ушёл на разбор
import backend.services.agent_call_finalizer as acf  # noqa: E402
scheduled = []
acf._schedule = lambda *a, **k: scheduled.append(a)
# сообщения владельцу — без Telegram
notes = []
wcs._notify = lambda agent, line: notes.append(line)

now = datetime.now(timezone.utc)
db = SessionLocal()
profi = SubscriptionPlan(code="profi", name="Profi", price=12500, max_assistants=5, description="p", is_active=True)
start = SubscriptionPlan(code="start", name="Start", price=7500, max_assistants=5, description="s", is_active=True)
db.add_all([profi, start])
db.flush()
owner = User(email="wa@voksy.kg", password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
             first_name="Айбек", wallet_balance=50000, subscription_plan_id=profi.id,
             subscription_end_date=now + timedelta(days=20))
db.add(owner)
db.flush()
card = ElevenAssistantConfig(user_id=owner.id, name="Голос агента", voice_engine="gpt_live", live_voice="marin",
                             is_active=True, greeting_message="Саламатсызбы! Слушаю вас.")
db.add(card)
db.flush()
agent = AgentConfig(user_id=owner.id, name="Агент", is_active=True, assistant_type="eleven",
                    eleven_assistant_id=card.id)
db.add(agent)
db.flush()
acc = AgentWaCallAccount(user_id=owner.id, agent_config_id=agent.id, gateway_id="wa-call-1", phone="996700999888",
                         status=WaCallStatus.CONNECTED, inbound_enabled=True, outbound_enabled=False,
                         daily_outbound_limit=3)
db.add(acc)
wa_acc = AgentWhatsAppAccount(agent_config_id=agent.id, user_id=owner.id, instance_name="t1", status="connected",
                              webhook_secret="s",
                              wa_phone="996700999888")
db.add(wa_acc)
db.commit()

settings.WA_CALLS_ENABLED, settings.WA_CALLS_USERS = False, ""


def started(caller="996555123456", **kw):
    return dict({"type": "call.event", "event": "started", "direction": "inbound", "call_id": str(uuid.uuid4()),
                 "wa_call_id": "ABCDEF0123", "account_id": str(acc.id), "caller": caller,
                 "caller_jid": caller + "@s.whatsapp.net", "caller_name": "Нурлан"}, **kw)


ev = started()
r = wcs.decide_inbound(db, "wa-call-1", ev)
row = db.get(SipCall, uuid.UUID(ev["call_id"]))
check("входящий: выключатель выключен → reject feature_off и строка в журнале",
      r["type"] == "reject" and r["reason"] == "feature_off" and row is not None and row.status == SipCallStatus.FAILED
      and (row.call_metadata or {}).get("channel") == "whatsapp", r)
settings.WA_CALLS_ENABLED, settings.WA_CALLS_USERS = True, str(owner.id)

ev = started()
r = wcs.decide_inbound(db, "wa-call-1", ev)
row = db.get(SipCall, uuid.UUID(ev["call_id"]))
check("входящий: accept с ассистентом агента", r["type"] == "accept" and r["assistant_type"] == "eleven"
      and r["assistant_id"] == str(card.id), r)
check("входящий: строка sip_calls — звонит, канал whatsapp, аккаунт и агент в метаданных",
      row.status == SipCallStatus.RINGING and row.direction == "inbound" and row.did == "996700999888"
      and row.caller == "996555123456" and row.call_metadata.get("wa_account_id") == str(acc.id)
      and row.call_metadata.get("agent_config_id") == str(agent.id) and row.user_id == owner.id)
accepted_call_id = ev["call_id"]

r = wcs.decide_inbound(db, "wa-call-1", started(account_id=str(uuid.uuid4())))
check("входящий: номер не привязан → reject unknown_account", r["reason"] == "unknown_account", r)
agent.is_active = False
db.commit()
notes.clear()
r = wcs.decide_inbound(db, "wa-call-1", started())
check("входящий: агент выключен → reject + владельцу «пропущенный звонок WhatsApp»",
      r["reason"] == "agent_inactive" and notes and "Пропущенный звонок WhatsApp" in notes[0] and "Нурлан" in notes[0],
      notes)
check("входящий: агент выключен → ignore (звонок идёт на телефон владельца)", r.get("ignore") is True, r)
r = wcs.decide_inbound(db, "wa-call-1", started(account_id=str(uuid.uuid4())))
check("входящий: чужой аккаунт — без ignore (просто отказ)", not r.get("ignore"), r)
agent.is_active = True
acc.inbound_enabled = False
db.commit()
notes.clear()
r = wcs.decide_inbound(db, "wa-call-1", started())
check("входящий: приём выключен → reject inbound_disabled", r["reason"] == "inbound_disabled", r)
check("входящий: агент выключен владельцем → звонок не сбрасываем (ignore — звонит на телефоне), без сообщения",
      r.get("ignore") is True and not notes, (r, notes))
acc.inbound_enabled = True
owner.wallet_balance = 0
db.commit()
r = wcs.decide_inbound(db, "wa-call-1", started())
check("входящий: кошелёк пуст → reject wallet_empty", r["reason"] == "wallet_empty", r)
owner.wallet_balance = 50000
db.commit()

# ---- тарифы: Старт, Profi, Beauty AI, Beauty Pro — да; пробный и Beauty Start — нет
trial = SubscriptionPlan(code="free", name="Trial", price=0, max_assistants=1, description="t", is_active=True)
b_ai = SubscriptionPlan(code="beauty_ai", name="Beauty AI", price=3990, max_assistants=1, description="b",
                        is_active=True, product="beauty", limits={"agent_mode": "reactive"})
b_start = SubscriptionPlan(code="beauty_start", name="Beauty Start", price=1990, max_assistants=1, description="b",
                           is_active=True, product="beauty", limits={"agent_mode": "reactive"})
db.add_all([trial, b_ai, b_start])
db.commit()
for plan, want_in, want_out in ((trial, False, False), (b_start, False, False), (b_ai, True, False),
                                (start, True, False), (profi, True, True)):
    owner.subscription_plan_id = plan.id
    db.commit()
    db.expire_all()
    owner = db.get(User, owner.id)
    r = wcs.decide_inbound(db, "wa-call-1", started())
    check(f"тариф {plan.code}: входящий {'принимаем' if want_in else 'нет'}, исходящий {'да' if want_out else 'нет'}",
          (r["type"] == "accept") == want_in and wcs.can_call_out(owner) == want_out
          and (want_in or (r.get("reason") == "no_access" and r.get("ignore") is True)), (r, wcs.can_call_out(owner)))
acc = db.get(AgentWaCallAccount, acc.id)
agent = db.get(AgentConfig, agent.id)

# LID: номер — из переписки с тем же JID
chat = AgentWhatsAppChat(account_id=wa_acc.id, remote_jid="12345678901234@lid", phone="996777123123", push_name="Асель")
db.add(chat)
db.commit()
ev = started(caller="", caller_jid="12345678901234@lid")
r = wcs.decide_inbound(db, "wa-call-1", ev)
check("входящий с LID: номер найден по переписке с тем же JID",
      r["type"] == "accept" and db.get(SipCall, uuid.UUID(ev["call_id"])).caller == "996777123123")

# ---- состояния аккаунта
wcs.apply_account_state(db, "wa-call-1", {"account_id": str(acc.id), "state": "qr", "qr": "2@abc,def", "expires_in": 60})
db.refresh(acc)
check("аккаунт: QR → ждём сканирования, код сохранён", acc.status == WaCallStatus.PENDING_QR and acc.qr_code == "2@abc,def")
wcs.apply_account_state(db, "wa-call-1", {"account_id": str(acc.id), "state": "connected", "phone": "996700999888",
                                          "push_name": "Voksy"})
db.refresh(acc)
check("аккаунт: на связи — номер, имя, QR очищен", acc.status == WaCallStatus.CONNECTED and acc.qr_code is None
      and acc.connected_at is not None and acc.push_name == "Voksy")
notes.clear()
wcs.apply_account_state(db, "wa-call-1", {"account_id": str(acc.id), "state": "banned", "error": "temp ban"})
db.refresh(acc)
check("аккаунт: блокировка → статус banned и сообщение владельцу", acc.status == WaCallStatus.BANNED and notes, notes)
wcs.apply_account_state(db, "wa-call-1", {"account_id": str(acc.id), "state": "connected", "phone": "996700999888"})

# ---- события звонка
wcs.apply_call_event(db, "wa-call-1", {"event": "answered", "call_id": accepted_call_id, "direction": "inbound"})
wcs.apply_call_event(db, "wa-call-1", {"event": "ended", "call_id": accepted_call_id, "direction": "inbound",
                                       "duration_sec": 42.5, "reason": "wa_hangup", "underruns": 2})
row = db.get(SipCall, uuid.UUID(accepted_call_id))
db.refresh(row)
check("событие: ended → завершён, длительность и причина", row.status == SipCallStatus.COMPLETED
      and row.duration_sec == 42.5 and row.end_reason == "wa_hangup" and row.call_metadata.get("gateway_underruns") == 2)
gid = str(uuid.uuid4())
notes.clear()
wcs.apply_call_event(db, "wa-call-1", {"event": "failed", "call_id": gid, "direction": "inbound", "account_id": str(acc.id),
                                       "caller": "996555000111", "reason": "gateway_busy"})
row = db.get(SipCall, uuid.UUID(gid))
check("событие: шлюз отклонил сам (линии заняты) → строка в журнале и владельцу", row is not None
      and row.status == SipCallStatus.FAILED and row.end_reason == "gateway_busy" and notes, notes)

# ---- исходящий
contact_wa = AgentContact(user_id=owner.id, agent_config_id=agent.id, name="Нурлан", phone="+996555123456", status="new")
contact_o = AgentContact(user_id=owner.id, agent_config_id=agent.id, name="Бакыт", phone="+996700111222", status="new")
contact_quiet = AgentContact(user_id=owner.id, agent_config_id=agent.id, name="Молчун", phone="+996555777888",
                             status="new")
db.add_all([contact_wa, contact_o, contact_quiet])
db.commit()
check("исходящий: выключен в аккаунте → отказ", "выключены" in (wcs.outbound_refusal(db, acc, "996555123456") or ""))
acc.outbound_enabled = True
db.commit()
check("исходящий: свой номер → отказ", "номер самой платформы" in (wcs.outbound_refusal(db, acc, "996700999888") or ""))
check("исходящий: клиент ни разу не писал и не звонил → первым не звоним",
      "первым звоним только" in (wcs.outbound_refusal(db, acc, "996555777888") or ""))
check("исходящий: звонил сам (входящий WhatsApp) → можно", wcs.outbound_refusal(db, acc, "996555123456") is None,
      wcs.outbound_refusal(db, acc, "996555123456"))
call = wcs.queue_outbound_call(db, account=acc, to_number="+996555123456", assistant_type="eleven",
                               assistant_id=card.id, metadata={"contact_name": "Нурлан"})
check("исходящий: в очереди шлюза wa-call-1 с нашего номера", call.status == SipCallStatus.QUEUED
      and call.gateway_id == "wa-call-1" and call.did == "996700999888" and call.to_number == "996555123456"
      and call.phone_number_id is None and call.call_metadata.get("channel") == "whatsapp")
payload = wcs.originate_payload(call)
check("originate: account_id аккаунта и номер", payload["type"] == "originate" and payload["account_id"] == str(acc.id)
      and payload["to"] == "996555123456", payload)
check("исходящий: повтор на тот же номер сразу — анти-долбёж",
      "уже звонили" in (wcs.outbound_refusal(db, acc, "996555123456") or ""))
for i in range(2):
    db.add(SipCall(id=uuid.uuid4(), gateway_id="wa-call-1", direction="outbound", status=SipCallStatus.COMPLETED,
                   user_id=owner.id, did="996700999888", to_number=f"99655500000{i}",
                   call_metadata={"channel": "whatsapp"}))
db.commit()
check("исходящий: дневной лимит аккаунта (3) исчерпан", "Дневной лимит" in (wcs.outbound_refusal(
    db, acc, "996777123123", require_contact_history=False) or ""))
acc.daily_outbound_limit = 50
settings.WA_CALLS_HOURLY_OUTBOUND = 3
db.commit()
check("исходящий: лимит в час исчерпан", "в час" in (wcs.outbound_refusal(db, acc, "996777123123",
                                                                          require_contact_history=False) or ""))
settings.WA_CALLS_HOURLY_OUTBOUND = 6
db.query(SipCall).filter(SipCall.direction == "outbound").delete()
db.commit()

# ---- выбор канала: O! → SIP, иначе WhatsApp
ch, a, num = wcs.choose_outbound_channel(db, agent.id, contact_o)
check("канал: номер O! → SIP (оператор надёжнее)", ch == "sip" and a is None and num == "996700111222", ch)
ch, a, num = wcs.choose_outbound_channel(db, agent.id, contact_wa)
check("канал: не O!, клиент звонил в WhatsApp → WhatsApp", ch == "whatsapp" and a is not None and num == "996555123456",
      ch)
ch, a, num = wcs.choose_outbound_channel(db, agent.id, contact_quiet)
check("канал: не O!, клиент не писал → звонить нельзя", ch is None, ch)
contact_wa.memory = {"whatsapp_phone": "996555123499"}
check("канал: номер для WhatsApp из карточки главнее основного", wcs.whatsapp_dial_number(contact_wa) == "996555123499")
contact_wa.memory = {}
db.commit()
owner.subscription_plan_id = start.id
db.commit()
db.expire_all()
ch, _, _ = wcs.choose_outbound_channel(db, agent.id, db.get(AgentContact, contact_wa.id))
check("канал: Старт — только входящие, исходящий через WhatsApp нельзя", ch is None, ch)
owner = db.get(User, owner.id)
owner.subscription_plan_id = profi.id
db.commit()
db.expire_all()

# ---- инструменты агента
from backend.services import agent_tools  # noqa: E402

res = asyncio.run(agent_tools.fn_check_phone_operator({"phone": "+996555123456"}, str(owner.id), str(agent.id), db))
check("check_phone_operator: не O!, есть WhatsApp и клиент писал → можно позвонить через WhatsApp",
      res.get("can_call") is True and res.get("operator") == "whatsapp", res)
res = asyncio.run(agent_tools.fn_check_phone_operator({"phone": "+996555777888"}, str(owner.id), str(agent.id), db))
check("check_phone_operator: клиент не писал → нельзя, с объяснением", res.get("can_call") is False
      and "писал" in res.get("note", ""), res)
res = asyncio.run(agent_tools.fn_check_phone_operator({"phone": "+996700111222"}, str(owner.id), str(agent.id), db))
check("check_phone_operator: O! — как раньше", res.get("can_call") is True and res.get("operator") == "O!", res)
owner = db.get(User, owner.id)
ok, why = agent_tools.agent_can_call_contact(db, owner, db.get(AgentContact, contact_wa.id))
check("agent_can_call_contact: клиент WhatsApp — агент может позвонить сам", ok, why)
ok, why = agent_tools.agent_can_call_contact(db, owner, db.get(AgentContact, contact_quiet.id))
check("agent_can_call_contact: не O! и не писал — нельзя", not ok, why)
g = agent_tools._check_call_guards(db.get(AgentContact, contact_wa.id), now, allow_shift=False, window=None, db=db,
                                   user_id=owner.id)
check("проверка звонка: не O!, но есть WhatsApp → разрешено", g.get("ok") is True, g)
g = agent_tools._check_call_guards(db.get(AgentContact, contact_quiet.id), now, allow_shift=False, window=None, db=db,
                                   user_id=owner.id)
check("проверка звонка: не O! и без WhatsApp → прежний отказ other_operator", g.get("error") == "other_operator", g)

# ---- управляющий сокет и медиа-сокет целиком
import backend.api.sip_gateway as sg  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

cl = TestClient(app_module.app, raise_server_exceptions=False)
try:
    with cl.websocket_connect("/ws/wa-call-gateway/control?token=wrong&gateway=wa-call-1") as ws:
        ws.receive_text()
    rejected = False
except Exception:
    rejected = True
check("управляющий сокет: чужой токен не пускает", rejected)

with cl.websocket_connect("/ws/wa-call-gateway/control?token=wa-secret&gateway=wa-call-1") as ws:
    ws.send_text(json.dumps({"type": "hello", "gateway_id": "wa-call-1", "version": "0.1.0", "max_calls": 10,
                             "accounts": [{"account_id": str(acc.id), "state": "connected", "phone": "996700999888"}]}))
    ev = started()
    ws.send_text(json.dumps(ev))
    reply = None
    for _ in range(20):
        m = json.loads(ws.receive_text())
        if m.get("call_id") == ev["call_id"]:
            reply = m
            break
    check("управляющий сокет: started → accept с ассистентом", reply and reply["type"] == "accept"
          and reply["assistant_id"] == str(card.id), reply)
    db.expire_all()
    acc = db.get(AgentWaCallAccount, acc.id)
    acc.login_requested_at = datetime.utcnow()
    db.commit()
    q = wcs.queue_outbound_call(db, account=acc, to_number="996555123456", assistant_type="eleven", assistant_id=card.id)
    seen = {}
    for _ in range(40):
        m = json.loads(ws.receive_text())
        seen[m.get("type")] = m
        if "originate" in seen and "login" in seen:
            break
    check("управляющий сокет: исходящий из очереди → originate с account_id",
          seen.get("originate", {}).get("call_id") == str(q.id)
          and seen.get("originate", {}).get("account_id") == str(acc.id), list(seen))
    check("управляющий сокет: запрошенный вход → login аккаунту", seen.get("login", {}).get("account_id") == str(acc.id))
    ws.send_text(json.dumps({"type": "call.event", "event": "ringing", "call_id": str(q.id), "direction": "outbound"}))
    ws.send_text(json.dumps({"type": "account.state", "account_id": str(acc.id), "state": "qr", "qr": "2@x",
                             "expires_in": 30}))
    import time as _t
    _t.sleep(0.5)
db.expire_all()
check("управляющий сокет: ringing → статус звонка, account.state → QR в базе",
      db.get(SipCall, q.id).status == SipCallStatus.RINGING
      and db.get(AgentWaCallAccount, acc.id).qr_code == "2@x")
acc = db.get(AgentWaCallAccount, acc.id)
wcs.apply_account_state(db, "wa-call-1", {"account_id": str(acc.id), "state": "connected", "phone": "996700999888"})

seen_h = {}


async def fake_handler(ws, assistant_id, hdb):
    a = hdb.get(ElevenAssistantConfig, uuid.UUID(assistant_id))
    seen_h.update(wideband=getattr(a, "line_wideband", None), channel=getattr(a, "line_channel", None),
                  g711=getattr(a, "live_phone_g711", False), telephony=getattr(a, "telephony_mode", False),
                  rate=ws.line_rate, provider=ws.provider, prompt=(a.system_prompt or ""))
    got = 0
    while got < 4 * 960:
        msg = await ws.receive()
        data = json.loads(msg["text"])
        if data.get("type") == "input_audio_buffer.append":
            got += len(base64.b64decode(data["audio"]))
    seen_h["in_bytes"] = got
    await ws.send_json({"type": "response.audio.delta",
                        "delta": base64.b64encode(tone(24000, 100, 300)).decode()})
    await asyncio.sleep(0.5)  # GPT-Live: адаптер придерживает начало реплики (запас 200 мс)
    await ws.send_json({"type": "call.hangup", "reason": "done"})
    try:
        while True:
            await ws.receive()
    except WebSocketDisconnect:
        return


sg.SIP_HANDLERS["eleven"] = fake_handler
ev = started()
r = wcs.decide_inbound(db, "wa-call-1", ev)
call_id = ev["call_id"]
try:
    with cl.websocket_connect(f"/ws/sip/{call_id}?token=sip-secret&gateway=wa-call-1") as ws:
        ws.send_text(json.dumps({"type": "start", "call_id": call_id, "channel": "whatsapp", "direction": "inbound",
                                 "format": {"encoding": "pcm16", "sample_rate": 16000, "channels": 1, "frame_ms": 20}}))
        m = json.loads(ws.receive_text())
    sip_token_reply = m
except Exception as exc:
    sip_token_reply = {"error": str(exc)}
check("медиа-сокет: токен SIP-моста не открывает звонок WhatsApp", sip_token_reply.get("type") == "hangup",
      sip_token_reply)

ev = started()
r = wcs.decide_inbound(db, "wa-call-1", ev)
call_id = ev["call_id"]
audio_back = b""
with cl.websocket_connect(f"/ws/sip/{call_id}?token=wa-secret&gateway=wa-call-1") as ws:
    ws.send_text(json.dumps({"type": "start", "call_id": call_id, "channel": "whatsapp", "direction": "inbound",
                             "caller": "996555123456", "account_id": str(acc.id), "assistant_type": "eleven",
                             "format": {"encoding": "pcm16", "sample_rate": 16000, "channels": 1, "frame_ms": 20}}))
    for _ in range(5):
        ws.send_bytes(tone(16000, 20, 300))
    for _ in range(30):
        m = ws.receive()
        if m.get("bytes"):
            audio_back += m["bytes"]
        elif m.get("text"):
            if json.loads(m["text"]).get("type") == "hangup":
                break
    ws.send_text(json.dumps({"type": "ended", "reason": "backend_hangup"}))
check("медиа-сокет: карточка на линии WhatsApp — широкая полоса, без G.711, канал whatsapp",
      seen_h.get("wideband") is True and seen_h.get("channel") == "whatsapp" and not seen_h.get("g711")
      and seen_h.get("telephony") is True and seen_h.get("rate") == 16000 and seen_h.get("provider") == "openai",
      seen_h)
check("медиа-сокет: кадры 16 кГц → хендлеру 24 кГц", 4 * 960 <= (seen_h.get("in_bytes") or 0) <= 6 * 960,
      seen_h.get("in_bytes"))
check("медиа-сокет: ответ хендлера → шлюзу PCM16 16 кГц (100 мс = 3200 байт)", len(audio_back) == 3200,
      len(audio_back))
check("медиа-сокет: промпт звонка — тот же, что на телефоне (контекст входящего звонка)",
      "[Контекст звонка]" in seen_h.get("prompt", "") or "входящий" in seen_h.get("prompt", ""))
import time as _t2  # noqa: E402
_t2.sleep(1.0)
db.expire_all()
row = db.get(SipCall, uuid.UUID(call_id))
check("медиа-сокет: звонок отвечен", row.status in (SipCallStatus.ANSWERED, SipCallStatus.COMPLETED), row.status)
log_text = " ".join(str(e.get("text", "")) for ev_list in db.execute(text("SELECT events FROM call_logs")).scalars().all()
                    for e in (ev_list or []))
check("журнал звонка: первая строка «Звонок WhatsApp»", "Звонок WhatsApp входящий" in log_text, log_text[:300])
# Голос клиента во входящем звуке (звонок c4b0c0fd: звук шёл, а модель не услышала ни слова)
_hs = sma.HandlerSocket(None, "openai", "x", line_rate=16000, channel="whatsapp")
for _ in range(100):  # 2 с голоса
    _f = tone(16000, 20, 300)
    _hs._note_inbound(len(_f)); _hs._note_voice(_f)
_hs.gateway_stats = {"frames_in": 100, "silence_frames": 400, "dropped_in": 0}
_v = _hs.voice_summary()
check("журнал: «Голос клиента» — речь посчитана, счётчики шлюза в строке, без тревоги",
      _v and "речь ≈2.0 с" in _v[0] and "получено 2.0 с" in _v[0] and not _v[1], _v)
_hs = sma.HandlerSocket(None, "openai", "x", line_rate=16000, channel="whatsapp")
for _ in range(600):  # 12 с нулей
    _f = b"\x00" * 640
    _hs._note_inbound(len(_f)); _hs._note_voice(_f)
_v = _hs.voice_summary()
check("журнал: 12 с одной тишины → предупреждение «голос теряется до сервера»", _v and _v[1] and "одна тишина" in _v[0], _v)

# Приветствие GPT-Live своими словами (28.11.2026): ориентир владельца — по смыслу, знакомому — без представления
import types as _types  # noqa: E402
from backend.websockets.live_client import OpenAILiveClient  # noqa: E402
_c = OpenAILiveClient.__new__(OpenAILiveClient)
_c.assistant_config = _types.SimpleNamespace(greeting_known_client=True)
_txt = _c.greeting_instruction("Саламатсызбы! Бул Анри.")
check("приветствие: не «дословно», ориентир по смыслу, знакомому — по имени и без представления",
      "дословно" not in _txt and "смысл, не текст" in _txt and "не представляйся заново" in _txt, _txt)
_c.assistant_config = _types.SimpleNamespace()
check("приветствие новому: без блока «уже общался»", "уже общался" not in _c.greeting_instruction("Здравствуйте!"))
os.environ["LIVE_GREETING_FREE"] = "0"
check("приветствие: LIVE_GREETING_FREE=0 — прежнее дословное", "дословно" in _c.greeting_instruction("Здравствуйте!"))
os.environ.pop("LIVE_GREETING_FREE")
check("PostCall: входящий WhatsApp на номер агента ушёл на разбор", any(a and a[4] == "inbound" for a in scheduled),
      scheduled)
ac = db.query(AgentCall).filter(AgentCall.call_session_id == call_id).first()
check("PostCall: звонок записан агенту (AgentCall)", ac is not None and ac.direction == "inbound")

# ---- запись звонка: токен шлюза WhatsApp принимается
resp = cl.post(f"/api/sip/recordings/{call_id}", content=b"ID3fake", headers={"X-Gateway-Token": "bad"})
check("запись: чужой токен → 401", resp.status_code == 401, resp.status_code)
resp = cl.post(f"/api/sip/recordings/{call_id}", content=b"ID3fake", headers={"X-Gateway-Token": "wa-secret",
                                                                               "Content-Type": "audio/mpeg"})
check("запись: токен шлюза WhatsApp принят (дальше — R2)", resp.status_code in (200, 502, 503), resp.status_code)

# ---- PostCall для исходящего из скрипта (без задачи)
out = SipCall(id=uuid.uuid4(), gateway_id="wa-call-1", direction="outbound", status=SipCallStatus.ANSWERED,
              user_id=owner.id, did="996700999888", to_number="996555444333", answered_at=datetime.utcnow(),
              call_metadata={"channel": "whatsapp", "wa_account_id": str(acc.id), "agent_config_id": str(agent.id),
                             "contact_name": "Тест"})
db.add(out)
db.commit()
scheduled.clear()
acf.AgentCallFinalizer.on_media_finished(db, out, wcs.line_for_call(db, out), True)
ac = db.query(AgentCall).filter(AgentCall.call_session_id == str(out.id)).first()
check("PostCall: исходящий WhatsApp из скрипта → карточка клиента и разбор",
      ac is not None and ac.direction == "outbound" and scheduled and scheduled[0][4] == "outbound")

# ---- кабинет: /api/agent/wa-calls (подключение по QR, выключатель, отвязать)
from backend.core.security import create_jwt_token  # noqa: E402
owner = db.get(User, owner.id)
H = {"Authorization": f"Bearer {create_jwt_token(owner.id)}"}
agent2 = AgentConfig(user_id=owner.id, name="Второй", is_active=True, assistant_type="eleven",
                     eleven_assistant_id=card.id)
db.add(agent2)
db.commit()
A2 = f"?agent_id={agent2.id}"
st = cl.get("/api/agent/wa-calls" + A2, headers=H).json()
check("кабинет: GET — доступно, тариф подходит, номера нет", st.get("available") and st.get("plan_ok")
      and st.get("status") == "not_connected" and st.get("can_outbound") is True and st["agent"]["has_voice"], st)
r = cl.post("/api/agent/wa-calls/connect" + A2, headers=H)
st = r.json()
check("кабинет: «Подключить» → аккаунт ждёт QR, шлюзу уйдёт login", r.status_code == 200
      and st.get("status") == "pending_qr" and st["account"]["waiting_gateway"] is True, st)
acc2 = wcs.account_for_agent(db, agent2.id)
cmds = wcs.pending_account_commands(db, "wa-call-1")
check("кабинет: команда login для нового аккаунта", any(c["type"] == "login" and c["account_id"] == str(acc2.id)
                                                       for c in cmds), cmds)
wcs.apply_account_state(db, "wa-call-1", {"account_id": str(acc2.id), "state": "qr", "qr": "2@xyz,abc,def",
                                          "expires_in": 60})
st = cl.get("/api/agent/wa-calls" + A2, headers=H).json()
check("кабинет: QR от шлюза → картинка SVG в ответе", (st.get("qr") or "").startswith("data:image/svg+xml;base64,"),
      st.get("qr_error"))
wcs.apply_account_state(db, "wa-call-1", {"account_id": str(acc2.id), "state": "connected", "phone": "996700555444"})
st = cl.get("/api/agent/wa-calls" + A2, headers=H).json()
check("кабинет: на связи, номер скрыт наполовину", st["status"] == "connected"
      and st["account"]["phone_masked"].startswith("+996") and "555444" not in st["account"]["phone_masked"], st)
st = cl.patch("/api/agent/wa-calls/settings" + A2, headers=H, json={"inbound_enabled": False}).json()
check("кабинет: переключатель «агент выключен»", st["account"]["inbound_enabled"] is False, st)
st = cl.patch("/api/agent/wa-calls/settings" + A2, headers=H,
              json={"inbound_enabled": True, "outbound_enabled": True, "daily_outbound_limit": 999}).json()
check("кабинет: Profi включает исходящие, лимит не выше 50", st["account"]["outbound_enabled"] is True
      and st["account"]["daily_outbound_limit"] == 50, st)
owner.subscription_plan_id = b_ai.id
db.commit()
r = cl.patch("/api/agent/wa-calls/settings" + A2, headers=H, json={"outbound_enabled": True})
check("кабинет: Beauty AI — исходящие нельзя (402)", r.status_code == 402, r.status_code)
r = cl.patch("/api/agent/wa-calls/settings" + A2, headers=H, json={"inbound_enabled": True})
check("кабинет: Beauty AI — выключатель входящих работает", r.status_code == 200, r.status_code)
owner.subscription_plan_id = trial.id
db.commit()
r = cl.post("/api/agent/wa-calls/connect" + A2, headers=H)
st = cl.get("/api/agent/wa-calls" + A2, headers=H).json()
check("кабинет: пробный период — подключить нельзя (402), строка зовёт к тарифам",
      r.status_code == 402 and st.get("plan_ok") is False, (r.status_code, st.get("plan_ok")))
owner.subscription_plan_id = profi.id
db.commit()
st = cl.delete("/api/agent/wa-calls" + A2, headers=H).json()
cmds = wcs.pending_account_commands(db, "wa-call-1")
check("кабинет: «Отвязать» → logout шлюзу, статус logged_out, исходящие выключены",
      st["status"] == "logged_out" and st["account"]["outbound_enabled"] is False
      and any(c["type"] == "logout" and c["account_id"] == str(acc2.id) for c in cmds), (st, cmds))
settings.WA_CALLS_USERS = "someone-else"
st = cl.get("/api/agent/wa-calls" + A2, headers=H).json()
check("кабинет: владелец не в списке пробы → строки нет (available=false)", st.get("available") is False, st)
settings.WA_CALLS_USERS = ""
check("кабинет: WA_CALLS_ENABLED=1 и пустой список → всем", cl.get("/api/agent/wa-calls" + A2, headers=H).json()
      .get("available") is True)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
