"""ИИ-управляющий салона (Voksy Beauty, шаг 1.6б).

Часть 1 (без базы): настройки по умолчанию и правка (пределы, неизвестные поля), текст «спасибо + оценка»,
инструкции сценариев без готовых обещаний скидок, push-обработчик в service worker.

Часть 2 (PostgreSQL, SALON_MANAGER_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- кандидаты: «вернуть ушедших» (не был 60+ дней, не записан вперёд), «повтор услуги» (срок по repeat_days),
  «заполнить окна» (у мастера завтра свободно, его клиенты давно не были); пауза, «не звонить», будущая
  запись и недавнее действие — пропуск; мессенджер → сообщение, только номер O! → звонок, ничего → пропуск;
- автономность: «сам» → approved, «на одобрение» → proposed; одобрить / отклонить / одобрить все;
- исполнение: только 10–20 Бишкек; сообщение — прогон оркестратора (событие salon), итог в журнале;
  звонок — задача агента; клиент успел записаться сам → пропуск; бронь одна на 2 воркера;
- лимит в день, повторный план в тот же день не дублирует;
- отзыв: «спасибо» с просьбой оценить только за визит «пришёл», save_review, низкая оценка → владельцу;
- отчёты: утренний (сегодня, вчера, неявки, выручка, агент вчера, ждут одобрения) и недельный, один на день,
  в приложении (/api/manager/reports), Telegram и push; тик в 9:00 и 10:00;
- API: доступ только владельцу, сценарии — только автономному агенту (402), push-подписка.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("SALON_MANAGER_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="salon-mgr-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import salon_manager as sm  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
d = sm.settings_of(None)
check("по умолчанию: сам, сценарии выключены, отчёты включены в 9:00",
      d["autonomy"] == "auto" and not d["winback"]["on"] and not d["fill_gaps"]["on"] and not d["repeat_review"]["on"]
      and d["morning_report"] == {"on": True, "hour": 9} and d["weekly_report"]["on"])
c = sm.clean_settings(d, {"autonomy": "approve", "winback": {"on": True, "days": 5, "per_day": 999},
                          "morning_report": {"hour": 3}, "hack": 1, "fill_gaps": {"on": "yes"}})
check("правка: пределы и неизвестные поля", c["autonomy"] == "approve" and c["winback"] == {"on": True, "days": 21, "per_day": 50}
      and c["morning_report"]["hour"] == 6 and "hack" not in c and c["fill_gaps"]["on"] is True)
check("правка: неверная автономность не меняется", sm.clean_settings(d, {"autonomy": "boss"})["autonomy"] == "auto")


class B:
    client_name = "Нурай Асанова"


check("«спасибо» с просьбой оценить 1–5", "Нурай, спасибо" in sm.review_text(B(), "Айгуль")
      and "от 1 до 5" in sm.review_text(B(), "Айгуль") and "Айгуль" in sm.review_text(B(), "Айгуль"))
check("инструкции сценариев: без обещаний скидок, запись в два шага",
      all("create_booking" in t for t in sm.INSTRUCTIONS.values()) and "Скидок" in sm.INSTRUCTIONS["winback"])
sw = open(os.path.join(ROOT, "backend/static/sw.js"), encoding="utf-8").read()
check("service worker: push и клик по уведомлению", "addEventListener('push'" in sw and "notificationclick" in sw
      and "showNotification" in sw)

if not PG:
    print("SKIP часть 2: SALON_MANAGER_TEST_DATABASE_URL не задан")
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
from backend.models.agent_whatsapp import AgentWhatsAppMessage  # noqa: E402
from backend.models.booking import Booking  # noqa: E402
from backend.models.salon_manager import ManagerAction  # noqa: E402
from backend.services import booking_service as bs, agent_tools, push_service, owner_notifications  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
app_module.ensure_booking_tables()
cl = TestClient(app_module.app, raise_server_exceptions=False)
B = bs.BISHKEK
db = SessionLocal()
NOW = bs.now_utc()
TODAY = bs.to_local(NOW).date()


def at_day(day: date, hm: str):
    return datetime.combine(day, time.fromisoformat(hm), B).astimezone(timezone.utc)


pro = SubscriptionPlan(code="beauty_pro", name="Beauty Pro", price=5990, max_assistants=10, description="p", is_active=True,
                       product="beauty", limits={"staff": 20, "agent_mode": "full", "credits_month": 10000})
start = SubscriptionPlan(code="beauty_start", name="Beauty Start", price=1990, max_assistants=3, description="s",
                         is_active=True, product="beauty", limits={"staff": 3, "agent_mode": "reactive", "credits_month": 0})
db.add_all([pro, start])
db.flush()
u = User(email="mgr@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
         subscription_plan_id=pro.id, subscription_end_date=NOW + timedelta(days=20), business_niche="beauty_salon",
         business_profile={"answers": {}})
db.add(u)
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор", is_active=True, telegram_enabled=True)
db.add(agent)
db.flush()
from backend.services.business_profile import apply_to_agent  # noqa: E402
apply_to_agent(db, u, agent)
nails = AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Маникюр", body="", price=1200,
                            duration_min=60, collection="Ногти", repeat_days=21)
cut = AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Стрижка", body="", price=1000,
                          duration_min=60, collection="Волосы")
db.add_all([nails, cut])
db.commit()
H = {"Authorization": "Bearer " + __import__("backend.core.security", fromlist=["x"]).create_jwt_token(u.id)}
ov = cl.get("/api/booking/overview?days=1", headers=H).json()
m1 = ov["resources"][0]["id"]
cl.patch(f"/api/booking/resources/{m1}", headers=H, json={"name": "Айгуль",
         "schedule": {dd: [["09:00", "19:00"]] for dd in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}})
db.expire_all()


def contact(name, phone, wa=True, status="active"):
    c = AgentContact(user_id=u.id, agent_config_id=agent.id, name=name, phone=phone, status=status)
    db.add(c)
    db.flush()
    if wa:
        db.add(AgentWhatsAppMessage(agent_config_id=agent.id, agent_contact_id=c.id, direction="inbound", body="Здравствуйте",
                                    created_at=datetime.utcnow() - timedelta(days=100)))
    db.commit()
    return c


_slot = [8]


def visit(c, days_ago, service, status="completed", price=1200):
    _slot[0] = 9 if _slot[0] >= 18 else _slot[0] + 1          # разные часы: у мастера не бывает двух визитов сразу
    st = at_day(TODAY - timedelta(days=days_ago), f"{_slot[0]:02d}:00")
    b = Booking(user_id=u.id, resource_id=uuid.UUID(m1), service_entry_id=service.id, service_title=service.title,
                contact_id=c.id, client_name=c.name, start_at=st, end_at=st + timedelta(minutes=60), status=status,
                price=price, source="owner")
    db.add(b)
    db.commit()
    return b


lost = contact("Асель", "+996555000001")              # 90 дней не была → вернуть
visit(lost, 90, cut, price=1000)
repeat = contact("Бермет", "+996555000002")           # маникюр 20 дней назад, повтор через 21 → напомнить
rb = visit(repeat, 20, nails)
caller = contact("Чолпон", "+996700111333", wa=False)  # только номер O! → звонок
visit(caller, 80, cut, price=1000)
nowhere = contact("Динара", "+996555000004", wa=False)  # ни мессенджера, ни O! → пропуск
visit(nowhere, 80, cut)
paused = contact("Эльмира", "+996555000005")
visit(paused, 80, cut)
paused.ai_paused_until = datetime(9999, 12, 31, tzinfo=timezone.utc)
dnc = contact("Жаныл", "+996555000006", status="do_not_call")
visit(dnc, 80, cut)
booked = contact("Зарина", "+996555000007")             # давно не была, но уже записана вперёд
visit(booked, 80, cut)
bs.create_booking(db, db.query(User).get(u.id), start=at_day(TODAY + timedelta(days=3), "15:00"), service_id=str(cut.id),
                  resource_id=m1, contact_id=str(booked.id), source="owner")
recent = contact("Курман", "+996555000008")             # давно, но уже писали на этой неделе
visit(recent, 80, cut)
db.add(ManagerAction(user_id=u.id, agent_config_id=agent.id, contact_id=recent.id, scenario="winback", kind="message",
                     status="done", payload={}, created_at=NOW - timedelta(days=2)))
db.commit()

agent.manager_settings = sm.clean_settings(sm.settings_of(agent), {"winback": {"on": True}, "repeat_review": {"on": True}})
db.commit()
user = db.query(User).get(u.id)
rows = sm.plan_actions(db, user, agent)
by = {(a.scenario, str(a.contact_id)): a for a in rows}
names = {str(x.id): x.name for x in db.query(AgentContact).all()}
got = sorted((a.scenario, names[str(a.contact_id)], a.kind) for a in rows)
check("план: вернуть Асель (сообщение), Чолпон (звонок O!), повтор маникюра Бермет",
      ("winback", "Асель", "message") in got and ("winback", "Чолпон", "call") in got and ("repeat", "Бермет", "message") in got, got)
skipped = {"Динара", "Эльмира", "Жаныл", "Зарина", "Курман"}
check("план: без канала, пауза, «не звонить», записан вперёд, недавно писали — пропуск",
      not ({n for _, n, _ in got} & skipped), got)
check("план: автономно — сразу одобрено", all(a.status == "approved" for a in rows))
wb = next(a for a in rows if a.scenario == "winback" and names[str(a.contact_id)] == "Асель")
sl = [bs.parse_start(x["start"]) for x in wb.payload["slots"]]
check("план: причина и свободные окна у её мастера, окна разнесены (не 09:00 и 09:30)",
      "90 дней" in wb.payload["reason"] and len(sl) == 2 and wb.payload["master"] == "Айгуль"
      and abs(sl[1] - sl[0]) >= sm.SLOT_GAP, wb.payload)
rp = next(a for a in rows if a.scenario == "repeat")
check("повтор: привязан к визиту и сроку", rp.payload["booking_id"] == str(rb.id) and rp.payload["repeat_days"] == 21)
again = sm.plan_actions(db, user, agent)
check("повторный план: тех же клиентов снова не берёт", not again, [(a.scenario, names[str(a.contact_id)]) for a in again])

# ── заполнить пустые окна: у Айгуль завтра свободно → её давние клиенты
db.query(ManagerAction).delete()
agent.manager_settings = sm.clean_settings(sm.settings_of(agent), {"winback": {"on": False}, "repeat_review": {"on": False},
                                                                  "fill_gaps": {"on": True}})
db.commit()
rows = sm.plan_actions(db, user, agent)
got = sorted((a.scenario, names[str(a.contact_id)]) for a in rows)
tomorrow = (TODAY + timedelta(days=1)).isoformat()
check("окна: клиенты мастера с окнами только на завтра, не больше двух на мастера",
      rows and all(a.scenario == "fill_gaps" and a.payload["day"] == tomorrow and a.payload["slots"] for a in rows)
      and len(rows) <= 2 and all(bs.to_local(bs.parse_start(sl["start"])).date().isoformat() == tomorrow
                                 for a in rows for sl in a.payload["slots"]), got)
agent.manager_settings = sm.clean_settings(sm.settings_of(agent), {"winback": {"on": True}, "repeat_review": {"on": True},
                                                                  "fill_gaps": {"on": False}})
db.commit()

# ── одобрение
db.query(ManagerAction).delete()
db.commit()
agent.manager_settings = sm.clean_settings(sm.settings_of(agent), {"autonomy": "approve"})
db.commit()
rows = sm.plan_actions(db, user, agent)
check("на одобрение: всё proposed", rows and all(a.status == "proposed" for a in rows), [a.status for a in rows])
r = cl.get("/api/manager/actions?status=proposed", headers=H).json()
check("API: предложения с причиной, окнами, именем", len(r["actions"]) == len(rows) and r["actions"][0]["reason"]
      and r["actions"][0]["contact_name"], str(r)[:300])
one = r["actions"][0]["id"]
r = cl.post("/api/manager/actions/decide", headers=H, json={"ids": [one], "approve": False}).json()
check("отклонить одно", r["changed"] == 1 and r["pending"] == len(rows) - 1, r)
r = cl.post("/api/manager/actions/decide", headers=H, json={"approve": True}).json()
check("одобрить все остальные", r["changed"] == len(rows) - 1 and r["pending"] == 0, r)

# ── исполнение
runs = []


async def fake_analyze(self, agent_call, agent_contact, agent_config, user, task, transcript, call_status,
                       duration_seconds, db, call_direction):
    runs.append({"contact": agent_contact.name, "transcript": transcript, "direction": call_direction,
                 "kind": (agent_call.postcall_log or {}).get("followup_kind")})
    db.add(AgentWhatsAppMessage(agent_config_id=agent_config.id, agent_contact_id=agent_contact.id, direction="outbound",
                                body=f"{agent_contact.name}, давно вас не видели! Есть время завтра", created_at=datetime.utcnow()))
    db.commit()


from backend.services.agent_orchestrator import PostCallOrchestrator  # noqa: E402
PostCallOrchestrator._analyze_v3_openrouter = fake_analyze
calls = []


async def fake_task(args, user_id, agent_config_id, db_, channel="call"):
    calls.append(args)
    return {"ok": True, "scheduled_local": "11:02"}


agent_tools.fn_create_agent_task = fake_task
night = datetime.combine(TODAY, time(22, 0), B).astimezone(timezone.utc)
check("ночью (22:00) не исполняет", asyncio.run(sm.execute_due(db, night)) == 0 and not runs)
noon = datetime.combine(TODAY, time(11, 0), B).astimezone(timezone.utc)
n = asyncio.run(sm.execute_due(db, noon)) + asyncio.run(sm.execute_due(db, noon))
db.expire_all()
done = db.query(ManagerAction).filter(ManagerAction.status == "done").all()
check("днём: одобренные выполнены", n == len(done) and len(done) >= 2, (n, [(a.scenario, a.status, a.result) for a in db.query(ManagerAction).all()]))
check("сообщение: событие salon с окнами в инструкции", runs and runs[0]["kind"] == "salon"
      and runs[0]["direction"] == "followup_outbound" and "вободное время" in runs[0]["transcript"], runs[:1])
check("звонок: задача агента с инструкцией", calls and "ИИ-управляющий" in calls[0]["title"] and calls[0]["notes"], calls)
check("журнал: что написал агент", any("Написал в WhatsApp" in (a.result or "") for a in done), [a.result for a in done])
rej = db.query(ManagerAction).filter(ManagerAction.id == uuid.UUID(one)).first()
check("отклонённое не выполнено", rej.status == "rejected" and rej.result is None)

# клиент записался сам до исполнения → пропуск
db.query(ManagerAction).delete()
db.commit()
a = ManagerAction(user_id=u.id, agent_config_id=agent.id, contact_id=lost.id, scenario="winback", kind="message",
                  status="approved", payload={"reason": "test", "slots": []}, created_at=NOW)
db.add(a)
db.commit()
bs.create_booking(db, user, start=at_day(TODAY + timedelta(days=2), "12:00"), service_id=str(cut.id), resource_id=m1,
                  contact_id=str(lost.id), source="owner")
db.commit()
asyncio.run(sm.execute_due(db, noon))
db.expire_all()
a = db.query(ManagerAction).get(a.id)
check("клиент записался сам → пропуск", a.status == "skipped" and "записался" in a.result, (a.status, a.result))

# ── отзыв
from backend.services import booking_notify  # noqa: E402
y = visit(repeat, 0, nails)
y.start_at, y.end_at = NOW - timedelta(hours=3), NOW - timedelta(hours=2)
ns = visit(paused, 0, cut, status="no_show")
ns.start_at, ns.end_at = NOW - timedelta(hours=3), NOW - timedelta(hours=2)
db.commit()
due = dict((str(b), k) for b, k in booking_notify.due(db, NOW))
check("после визита: «спасибо + оценка» (сценарий включён), за неявку — нет",
      due.get(str(y.id)) == "review" and str(ns.id) not in due, due)
sent_msgs = []


async def fake_send(db_, user_, contact_, message):
    sent_msgs.append(message)
    return True


booking_notify.send_to_client = fake_send
asyncio.run(booking_notify.run_once(NOW))
db.expire_all()
check("просьба оценить ушла, отмечено review_asked_at", any("от 1 до 5" in m for m in sent_msgs)
      and db.query(Booking).get(y.id).review_asked_at is not None, sent_msgs)
hint = sm.review_hint(db, u.id, repeat.id)
check("разбор переписки видит «ждём оценку»", "save_review" in hint, hint)
notified = []


async def fake_owner_send(agent_, db_, message):
    notified.append(message)
    return True


owner_notifications._send = fake_owner_send
pushes = []
push_service.send_to_user_sync = lambda db_, uid, title, body, url="", tag=None: pushes.append((title, body, url)) or 1


async def fake_push(uid, title, body, url="", tag=None):
    pushes.append((title, body, url))
    return 1


push_service.send_to_user = fake_push
ctx = {"agent_config_id": str(agent.id), "user_id": str(u.id), "user": user, "agent_config": agent, "surface": "postcall",
       "current_contact_id": str(repeat.id), "call_direction": "whatsapp_inbound"}
r = json.loads(asyncio.run(agent_tools.execute_tool("save_review", {"score": 2, "comment": "долго ждала"}, ctx, db)))
asyncio.run(asyncio.sleep(0.3))
db.expire_all()
check("save_review: оценка 2 сохранена в визит, ответ — извиниться", r.get("ok") and r["low"]
      and db.query(Booking).get(y.id).review_score == 2 and "извинись" in r["next"], r)
check("низкая оценка → владельцу в Telegram и push", any("Низкая оценка" in m for m in notified) and pushes, (notified, pushes))
r = json.loads(asyncio.run(agent_tools.execute_tool("save_review", {"score": 9}, ctx, db)))
check("save_review: оценка вне 1–5 — отказ", not r.get("ok"))

# ── отчёты
db.query(ManagerAction).delete()
db.add(ManagerAction(user_id=u.id, agent_config_id=agent.id, contact_id=lost.id, scenario="winback", kind="message",
                     status="done", payload={}, created_at=NOW - timedelta(days=1), done_at=at_day(TODAY - timedelta(days=1), "12:00")))
yb = visit(caller, 1, cut, status="no_show")
db.commit()
title, body, data = sm.build_morning(db, user, agent, TODAY)
check("утренний: сегодня, вчера, неявки по именам, агент вчера", "Сегодня" in body and "не пришли 1 (Чолпон)" in body
      and "ИИ-управляющий вчера: написал 1" in body and data["yesterday"]["no_show"] == 1, body)
pushes.clear()
notified.clear()
nine = datetime.combine(TODAY, time(9, 5), B).astimezone(timezone.utc)
asyncio.run(sm.tick(db, nine))
asyncio.run(sm.tick(db, nine))
cnt = db.execute(text("SELECT count(*) FROM owner_reports WHERE user_id = :u AND kind = 'morning'"), {"u": str(u.id)}).scalar()
check("тик 9:00: утренний отчёт один раз, в Telegram и push", cnt == 1 and len([m for m in notified if "Доброе утро" in m]) == 1
      and any(p[0] == "Утренний отчёт" for p in pushes), (cnt, notified[:1], pushes))
r = cl.get("/api/manager/reports", headers=H).json()
check("отчёт в приложении: /api/manager/reports", r["reports"] and r["reports"][0]["kind"] == "morning"
      and not r["reports"][0]["read"], str(r)[:200])
rid = r["reports"][0]["id"]
cl.post(f"/api/manager/reports/{rid}/read", headers=H)
ov = cl.get("/api/manager/overview", headers=H).json()
check("обзор: прочитано, сценарии доступны, последний отчёт", ov["unread_reports"] == 0 and ov["scenarios_available"]
      and ov["last_report"]["id"] == rid, str(ov)[:300])
monday = TODAY - timedelta(days=TODAY.weekday())
title, body, data = sm.build_weekly(db, user, agent, monday)
check("недельный отчёт: выручка, визиты, мастера", "Итоги недели" in body and "Выручка" in body and "Визитов" in body, body)

# тик 10:00 в режиме одобрения: план + просьба одобрить
db.query(ManagerAction).delete()
db.execute(text("UPDATE agent_configs SET jobs_ran_on = '{}'::jsonb"))
db.commit()
pushes.clear()
notified.clear()
ten = datetime.combine(TODAY, time(10, 5), B).astimezone(timezone.utc)
st = asyncio.run(sm.tick(db, ten))
check("тик 10:00: план на одобрение и просьба владельцу", st["planned"] >= 1
      and any("подготовил" in m for m in notified) and any(p[0] == "Нужно ваше одобрение" for p in pushes), (st, notified))

# ── API: права и тариф
r = cl.put("/api/manager/settings", headers=H, json={"morning_report": {"hour": 8}, "autonomy": "auto"})
check("настройки сохраняются", r.status_code == 200 and r.json()["settings"]["morning_report"]["hour"] == 8, r.text[:200])
user.subscription_plan_id = start.id
db.commit()
r = cl.put("/api/manager/settings", headers=H, json={"winback": {"on": True}})
check("Beauty Start: клиентские сценарии — 402", r.status_code == 402, r.text[:200])
r = cl.put("/api/manager/settings", headers=H, json={"winback": {"on": False}, "fill_gaps": {"on": False},
                                                     "repeat_review": {"on": True}, "morning_report": {"on": True}})
check("Beauty Start: отзывы и отчёты можно", r.status_code == 200, r.text[:200])
ov = cl.get("/api/manager/overview", headers=H).json()
check("Beauty Start: сценарии недоступны в обзоре", ov["scenarios_available"] is False)
user.subscription_plan_id = pro.id
db.commit()
r = cl.get("/api/push/key", headers=H).json()
check("push: ключ VAPID создаётся сам", len(r.get("key", "")) > 60, r)
sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BPx" + "a" * 80, "auth": "b" * 22}}
r = cl.post("/api/push/subscribe", headers=H, json={"subscription": sub}).json()
check("push: подписка сохранена", r.get("devices") == 1, r)
r = cl.post("/api/push/subscribe", headers=H, json={"subscription": {"endpoint": "http://x"}})
check("push: неполная подписка — 400", r.status_code == 400)
r = cl.post("/api/push/unsubscribe", headers=H, json={"endpoint": sub["endpoint"]}).json()
check("push: отписка", r.get("devices") == 0)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
