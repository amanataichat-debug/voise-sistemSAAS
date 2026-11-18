"""Аналитика кабинета (ветка 1811-niche-admin-speed): analytics_service + /api/analytics.

Часть 1 (без БД): периоды (сегодня … год, свой, «этот месяц» ↔ те же дни прошлого месяца), шаги графика
(час / день / неделя с понедельника / месяц), раскладка дат по столбцам, выводы словами.
Часть 2 (PostgreSQL, ANALYTICS_TEST_DATABASE_URL или ADMIN_TEST_DATABASE_URL): известные данные → точные
цифры: сообщения по каналам и прошлый период, уникальные клиенты, «отвечал только бот», реклама,
время ответа (медиана бота и владельца, без ответа), звонки (пропущенные, минуты, итоги разбора без
переписки), новые клиенты и откуда пришли (первое касание: мессенджер / звонок / запись по ссылке / вручную),
когорта (купили / отказ / в работе), воронка, причины отказа, теги, тепловая карта, записи (неявки,
выручка, повторные, загрузка мастеров), склад, кошелёк и кредиты, пробелы базы знаний; суммы столбцов
= итогам при любом шаге; чужие данные не попадают; API, CSV, кабинет без бота.
"""
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────── часть 1: без БД ─────────────────────────
PG = os.environ.get("ANALYTICS_TEST_DATABASE_URL") or os.environ.get("ADMIN_TEST_DATABASE_URL")
# настройки читаются при первом импорте backend.*: адрес БД задаём сразу (часть 1 к ней не подключается)
os.environ.update(HOST_URL="https://t.voksyai.online", JWT_SECRET_KEY="analytics-test-secret-0123456789abcdef",
                  DATABASE_URL=PG or "postgresql://none:none@127.0.0.1:1/none", PRODUCTION="true", OPENAI_API_KEY="k")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
from backend.services import analytics_service as an  # noqa: E402

NOW = datetime(2026, 11, 18, 9, 30)          # UTC → 15:30 в Бишкеке, среда
r = an.resolve_period("7d", now=NOW)
check("7 дней: 12–18 ноября, прошлые 5–11", (r.a, r.b, r.prev_a, r.prev_b) ==
      (date(2026, 11, 12), date(2026, 11, 18), date(2026, 11, 5), date(2026, 11, 11)), str((r.a, r.b, r.prev_a, r.prev_b)))
check("границы в UTC: полночь Бишкека = 18:00 UTC накануне", r.since == datetime(2026, 11, 11, 18) and
      r.until == datetime(2026, 11, 18, 18), str((r.since, r.until)))
r = an.resolve_period("today", now=datetime(2026, 11, 18, 19, 0))     # 01:00 19 ноября в Бишкеке
check("сегодня — по дате Бишкека (UTC ещё 18-е)", r.a == date(2026, 11, 19) and r.days == 1, str(r.a))
r = an.resolve_period("mtd", now=NOW)
check("этот месяц ↔ те же дни прошлого месяца", (r.a, r.b, r.prev_a, r.prev_b) ==
      (date(2026, 11, 1), date(2026, 11, 18), date(2026, 10, 1), date(2026, 10, 18)) and r.prev_label == "чем в прошлом месяце")
r = an.resolve_period("mtd", now=datetime(2026, 3, 30, 12))
check("этот месяц 30 марта ↔ весь февраль, не дальше его конца", (r.prev_a, r.prev_b) == (date(2026, 2, 1), date(2026, 2, 28)))
r = an.resolve_period("custom", "2026-11-20", "2026-10-01", now=NOW)
check("свой период: даты переставлены, будущее обрезано", (r.a, r.b) == (date(2026, 10, 1), date(2026, 11, 18)))
r = an.resolve_period("custom", "2020-01-01", "2026-11-18", now=NOW)
check("свой период не длиннее двух лет", r.days == an.MAX_DAYS)
r = an.resolve_period("мусор", now=NOW)
check("неизвестный период → 30 дней", r.key == "30d" and r.days == 30)
check("псевдонимы: year → 365d, quarter → 90d", an.resolve_period("year", now=NOW).days == 365 and
      an.resolve_period("quarter", now=NOW).days == 90)
check("подписи сравнения", an.resolve_period("7d", now=NOW).prev_label == "чем неделей раньше" and
      an.resolve_period("365d", now=NOW).prev_label == "чем годом раньше" and
      an.resolve_period("yesterday", now=NOW).prev_label == "чем накануне")
check("шаги: день → час; 7 дней → день; 30 → день/неделя; 90 → неделя по умолчанию; год → месяц",
      an.granularities(1) == ["hour"] and an.granularities(7) == ["day"] and an.granularities(30) == ["day", "week"]
      and an.default_granularity(90) == "week" and an.default_granularity(365) == "month"
      and an.pick_granularity(365, "day") == "month" and an.pick_granularity(30, "week") == "week")
wk = an.make_buckets(date(2026, 11, 1), date(2026, 11, 18), "week")
check("недели с понедельника: 1 (вс), 2–8, 9–15, 16–18", [(b.start.day, b.end.day) for b in wk] ==
      [(1, 1), (2, 8), (9, 15), (16, 18)], str([(b.start, b.end) for b in wk]))
mo = an.make_buckets(date(2025, 11, 19), date(2026, 11, 18), "month")
check("год по месяцам: 13 столбцов, первый и последний неполные", len(mo) == 13 and mo[0].start == date(2025, 11, 19)
      and mo[-1].end == date(2026, 11, 18))
d0 = mo[0].to_dict("month", date(2026, 11, 18), 15)
check("подпись месяца прошлого года с годом и неполные дни", d0["label"] == "ноя 25" and "(19–30)" in d0["title"], str(d0))
idx = an.BucketIndex(wk, "week")
check("дата → столбец недели", idx.index(date(2026, 11, 10)) == 2 and idx.index(date(2026, 10, 31)) is None)
hb = an.make_buckets(date(2026, 11, 18), date(2026, 11, 18), "hour")
hidx = an.BucketIndex(hb, "hour")
check("часы: 24 столбца, будущие часы помечены", len(hb) == 24 and hidx.index(date(2026, 11, 18), 9) == 9
      and hb[16].to_dict("hour", date(2026, 11, 18), 15)["future"] and not hb[15].to_dict("hour", date(2026, 11, 18), 15)["future"])
ins = an.build_insights({
    "heatmap": {"grids": {"all": [[0] * 24 for _ in range(1)] + [[0] * 10 + [5, 6] + [0] * 12] + [[0] * 24 for _ in range(5)]}},
    "channels": [{"title": "WhatsApp", "new_clients": 8}, {"title": "Telegram", "new_clients": 2}],
    "replies": {"all": {"median_bot_sec": 18, "median_owner_sec": 1500, "unanswered": 3}},
    "calls": {"missed": 4, "in": 10}, "bookings": {"no_show_pct": 20, "arrived": 8, "no_show": 2, "load_pct": 30, "total": 10},
    "clients": {"cohort": {"new": 10, "won": 2, "open": 5}}, "modules": {"crm": True}, "bot": {"gaps": 2},
})
texts = " | ".join(i["text"] for i in ins)
check("выводы: максимум 5, сначала то, что требует внимания", len(ins) == 5 and ins[0]["tone"] == "warn", texts)
check("выводы: без ответа, пропущенные звонки, неявки — словами", "3 обращения остались без ответа" in texts and
      "Пропущено 40% входящих" in texts, texts)
few = an.build_insights({"calls": {"missed": 1, "in": 2}})
check("выводы: два звонка — ещё не повод говорить о пропущенных", not few, str(few))
ins2 = an.build_insights({"heatmap": {"grids": {"all": [[0] * 24 for _ in range(1)] + [[0] * 10 + [5, 6] + [0] * 12] +
                                                [[0] * 24 for _ in range(5)]}},
                          "channels": [{"title": "WhatsApp", "new_clients": 8}, {"title": "Telegram", "new_clients": 2}],
                          "replies": {"all": {"median_bot_sec": 18}}})
t2 = " | ".join(i["text"] for i in ins2)
check("выводы: час пик, главный канал, скорость бота", "во вторник с 10:00 до 12:00" in t2 and
      "WhatsApp приносит 80% новых клиентов" in t2 and "Бот отвечает в среднем за 18 с" in t2, t2)

# ───────────────────────── часть 2: PostgreSQL ─────────────────────────
if not PG:
    print("SKIP: часть 2 — ANALYTICS_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

import warnings  # noqa: E402
warnings.filterwarnings("ignore")
import backend.core.config  # noqa: E402,F401
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_contact import AgentContact  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app_module.ensure_booking_tables()
app_module.ensure_dialog_indexes()
cl = TestClient(app_module.app, raise_server_exceptions=False)
db = SessionLocal()
BK = timezone(timedelta(hours=6))
TODAY = datetime.now(BK).date()


def bk(days_ago, hm, sec=0):
    """Время Бишкека N дней назад → (naive UTC, aware UTC)."""
    d = TODAY - timedelta(days=days_ago)
    t = datetime.combine(d, time.fromisoformat(hm), BK) + timedelta(seconds=sec)
    return t.astimezone(timezone.utc).replace(tzinfo=None), t.astimezone(timezone.utc)


plan = SubscriptionPlan(code="start", name="Start", price=7500, max_assistants=3, description="s", is_active=True)
db.add(plan)
db.flush()
u = User(email="shop@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", subscription_plan_id=plan.id,
         subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20), business_niche="beauty_salon",
         business_profile={"answers": {"crm": "no"}}, wallet_balance=12345, credits_balance=900)
other = User(email="other@kg.kg", password_hash="x", email_verified=True, first_name="Чужой", subscription_plan_id=plan.id,
             subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20))
db.add_all([u, other])
db.flush()
agent = AgentConfig(user_id=u.id, name="Бот салона")
agent_o = AgentConfig(user_id=other.id, name="Чужой бот")
db.add_all([agent, agent_o])
db.flush()


def contact(name, phone, days_ago, status="new", **kw):
    c = AgentContact(user_id=u.id, agent_config_id=agent.id, name=name, phone=phone, status=status,
                     created_at=bk(days_ago, "09:00")[0], updated_at=bk(days_ago, "09:00")[0], **kw)
    db.add(c)
    db.flush()
    return c


c1 = contact("Нурай", "+996555000001", 2, "success", deal_amount=5000)
c2 = contact("Бакыт", "+996555000002", 3, "rejected", lost_reason="Дорого", tags="vip,повтор")
c2.updated_at = bk(1, "12:00")[0]
c3 = contact("Аида", "+996555000003", 1)
c4 = contact("Старый", "+996555000004", 40)
c5 = contact("Звонил", "+996555000005", 5, "active", deal_amount=3000)
c6 = contact("По ссылке", "+996555000006", 6, "active")
c7 = contact("Вручную", "+996555000007", 4)
c8 = contact("Из рекламы", "+996555000008", 2)
co = AgentContact(user_id=other.id, agent_config_id=agent_o.id, name="Чужой клиент", phone="+996700000009", status="new",
                  created_at=bk(2, "09:00")[0])
db.add(co)
db.flush()


from backend.models.agent_whatsapp import AgentWhatsAppMessage  # noqa: E402
from backend.models.agent_telegram_account import AgentTelegramMessage  # noqa: E402
from backend.models.agent_instagram import AgentInstagramMessage  # noqa: E402


def msg(table, c, days_ago, hm, direction, sec=0, sent_by=None, body="Здравствуйте"):
    t = bk(days_ago, hm, sec)[0]
    aid = agent_o.id if c is co else agent.id
    if table == "wa":
        db.add(AgentWhatsAppMessage(agent_config_id=aid, agent_contact_id=c.id, direction=direction, body=body,
                                    sent_by=sent_by, created_at=t))
    elif table == "tg":
        db.add(AgentTelegramMessage(agent_contact_id=c.id, direction=direction, body=body, sent_by=sent_by, created_at=t))
    else:
        db.add(AgentInstagramMessage(agent_config_id=aid, agent_contact_id=c.id, direction=direction, body=body,
                                     sent_by=sent_by, created_at=t))


msg("wa", c1, 2, "10:00", "inbound")
msg("wa", c1, 2, "10:00", "outbound", sec=20)
msg("tg", c2, 3, "15:00", "inbound")
msg("tg", c2, 3, "15:10", "outbound", sent_by="owner")
msg("ig", c3, 1, "12:00", "inbound")
msg("ig", c3, 1, "12:01", "inbound")
msg("wa", c4, 40, "11:00", "inbound")
msg("wa", c8, 2, "16:00", "inbound", body="[Клиент пришёл из рекламы Instagram: «Окна»] Здравствуйте")
msg("wa", c8, 2, "16:00", "outbound", sec=30)
msg("wa", co, 2, "10:00", "inbound")           # чужой — не считается

# звонки по телефонии
from backend.models.sip_gateway import SipCall  # noqa: E402


def call(direction, days_ago, hm, answered, dur, number, owner=u):
    t = bk(days_ago, hm)[1]
    db.add(SipCall(direction=direction, status="completed" if answered else "no_answer", user_id=owner.id,
                   caller=number if direction == "inbound" else None, to_number=number if direction == "outbound" else None,
                   created_at=t, answered_at=t + timedelta(seconds=5) if answered else None,
                   duration_sec=dur if answered else None))


call("inbound", 5, "11:00", True, 120, "+996555000005")
call("inbound", 5, "11:30", False, 0, "+996555000010")
call("outbound", 1, "10:00", True, 60, "+996555000002")
call("outbound", 1, "10:30", False, 0, "+996555000003")
call("inbound", 35, "11:00", True, 30, "+996555000004")
call("outbound", 33, "10:00", True, 20, "+996555000011")     # исходящий прошлого периода — не «обращение»
call("inbound", 2, "11:00", True, 90, "+996700000009", owner=other)
db.flush()

from backend.models.agent_call import AgentCall  # noqa: E402
db.add_all([
    AgentCall(agent_contact_id=c5.id, agent_config_id=agent.id, user_id=u.id, direction="inbound", status="answered",
              post_call_decision="SUCCESS", created_at=bk(5, "11:00")[0], duration_seconds=120),
    AgentCall(agent_contact_id=c2.id, agent_config_id=agent.id, user_id=u.id, direction="outbound", status="answered",
              post_call_decision="FOLLOWUP", created_at=bk(1, "10:00")[0], duration_seconds=60),
    AgentCall(agent_contact_id=c1.id, agent_config_id=agent.id, user_id=u.id, direction="whatsapp_inbound", status="answered",
              post_call_decision="SUCCESS", created_at=bk(2, "10:01")[0]),
])

# записи
from backend.models.booking import Booking, BookingResource  # noqa: E402
res = BookingResource(user_id=u.id, name="Айдана", schedule={d: [["09:00", "19:00"]] for d in
                                                             ("mon", "tue", "wed", "thu", "fri", "sat")})
db.add(res)
db.flush()


def booking(c, days_ago, hm, status, price, source="owner", created_days_ago=None, minutes=60):
    s = bk(days_ago, hm)[1]
    created = bk(created_days_ago if created_days_ago is not None else days_ago + 1, "08:00")[1]
    db.add(Booking(user_id=u.id, resource_id=res.id, contact_id=c.id if c else None, service_title="Стрижка",
                   client_name=c.name if c else "Гость", start_at=s, end_at=s + timedelta(minutes=minutes), status=status,
                   price=price, source=source, created_at=created))


booking(c1, 50, "12:00", "arrived", 1000, created_days_ago=51)
booking(c1, 2, "12:00", "arrived", 1200)
booking(c2, 3, "12:00", "no_show", 900)
booking(c3, 1, "12:00", "cancelled", 700)
booking(c6, 6, "12:00", "arrived", 1500, source="online", created_days_ago=6)
db.flush()
# запись по ссылке — раньше всех прочих касаний клиента c6
db.execute(text("UPDATE bookings SET created_at = :t WHERE contact_id = :c"), {"t": bk(6, "08:30")[1], "c": c6.id})

# кошелёк, кредиты, пробел базы знаний, склад
from backend.models.wallet_transaction import WalletTransaction  # noqa: E402
db.add_all([
    WalletTransaction(user_id=u.id, type="charge", amount=-500, balance_after=0, seconds=60, created_at=bk(2, "10:00")[1]),
    WalletTransaction(user_id=u.id, type="topup", amount=10000, balance_after=0, created_at=bk(3, "10:00")[1]),
    WalletTransaction(user_id=u.id, type="charge", amount=-700, balance_after=0, seconds=80, created_at=bk(45, "10:00")[1]),
])
db.execute(text("INSERT INTO credit_transactions (id, user_id, product, type, amount, balance_after, created_at) "
                "VALUES (:id, :u, 'orchestrator', 'spend', -100, 800, :t)"), {"id": uuid.uuid4(), "u": u.id, "t": bk(2, "10:00")[1]})
from backend.models.agent_knowledge import AgentKnowledgeGap  # noqa: E402
db.add(AgentKnowledgeGap(agent_config_id=agent.id, query="Есть парковка?", query_norm="есть парковка", channel="whatsapp",
                         first_asked_at=bk(1, "10:00")[0], last_asked_at=bk(1, "10:00")[0]))
db.add_all([
    AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Шуба", body="", price=90000,
                        stock_qty=2, in_stock=True, media_file_ids=["f1"], collection="Женская"),
    AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Шапка", body="", price=5000,
                        in_stock=False, attributes={"_photo_url": "https://x/y.jpg"}, collection="Женская"),
    AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Варежки", body="", price=None,
                        collection="Детская"),
    AgentKnowledgeEntry(user_id=u.id, agent_config_id=agent.id, kind="product", title="Снято", body="", price=10,
                        is_active=False),
])
db.commit()

rep = an.build_report(db, u, period="30d")
ch = {c["key"]: c for c in rep["channels"]}
s = rep["series"]
check("сообщения по каналам за 30 дней (чужие не считаются)", (ch["whatsapp"]["messages_in"], ch["telegram"]["messages_in"],
      ch["instagram"]["messages_in"]) == (2, 1, 2), str({k: v["messages_in"] for k, v in ch.items()}))
check("прошлый период: сообщение 40 дней назад", ch["whatsapp"]["prev_in"] == 1 and sum(rep["prev"]["messages_in"]) == 1)
check("сумма столбцов = итогам по каналам", sum(s["whatsapp_in"]) == 2 and sum(s["instagram_in"]) == 2
      and sum(s["messages_in"]) == 5 and sum(s["inbound"]) == 7, str((sum(s["messages_in"]), sum(s["inbound"]))))
check("уникальные написавшие: 4, по каналам 2/1/1", rep["bot"]["writers"] == 4 and ch["whatsapp"]["writers"] == 2
      and ch["telegram"]["writers"] == 1 and ch["instagram"]["writers"] == 1, str(rep["bot"]))
check("ответы бота 2, ваши 1, доля бота 67%", rep["bot"]["replies"] == 2 and rep["bot"]["owner_replies"] == 1
      and rep["bot"]["bot_share"] == 67, str(rep["bot"]))
check("клиенты, которым отвечал только бот: 2", rep["bot"]["bot_only_clients"] == 2, str(rep["bot"]["bot_only_clients"]))
check("из рекламы Click-to-WhatsApp: 1 клиент", rep["ads"] == {"clients": 1, "messages": 1}, str(rep["ads"]))
ra = rep["replies"]["all"]
check("время ответа: медиана 30 с, бот 25 с, вы 10 мин", (ra["median_sec"], ra["median_bot_sec"], ra["median_owner_sec"])
      == (30, 25, 600), str(ra))
check("без ответа 1 (Instagram, две реплики подряд — одно обращение), 4 обращения", ra["unanswered"] == 1
      and ra["threads"] == 4 and rep["replies"]["instagram"]["unanswered"] == 1, str(rep["replies"]))
c = rep["calls"]
check("звонки: 2 входящих, 2 исходящих, 2 с разговором, 1 пропущенный, 3 мин", (c["in"], c["out"], c["answered"],
      c["missed"], c["talk_min"]) == (2, 2, 2, 1, 3.0), str(c))
check("звонки: принято 50% входящих, прошлый период 2, разных звонивших 2", c["answer_rate"] == 50 and c["prev_total"] == 2
      and c["unique_callers"] == 2, str(c))
check("итоги разбора: успех 1, перезвон 1 (разбор переписки не считается звонком)",
      {o["key"]: o["count"] for o in c["outcomes"]} == {"SUCCESS": 1, "FOLLOWUP": 1}, str(c["outcomes"]))
cli = rep["clients"]
fc = {x["key"]: x["count"] for x in cli["first_channel"]}
check("новые клиенты: 7 за 30 дней, 1 в прошлом периоде", cli["total"] == 7 and cli["prev_total"] == 1
      and sum(s["new_clients"]) == 7, str((cli["total"], cli["prev_total"])))
check("откуда пришли: WhatsApp 2, Telegram 1, Instagram 1, звонок 1, запись по ссылке 1, вручную 1",
      fc == {"whatsapp": 2, "telegram": 1, "instagram": 1, "call": 1, "booking": 1, "manual": 1}, str(fc))
co_ = cli["cohort"]
check("когорта: купили 1 (5 000), отказ 1, в работе 5", (co_["won"], co_["lost"], co_["open"], co_["won_amount"])
      == (1, 1, 5, 5000.0), str(co_))
f = rep["funnel"]
st = {x["key"]: x["count"] for x in f["stages"]}
check("воронка сейчас: новые 4, в работе 2, успех 1, отказ 1", (st["new"], st["active"], st["success"], st["rejected"])
      == (4, 2, 1, 1) and f["pipeline_amount"] == 3000 and f["won_amount"] == 5000, str(st))
check("причина отказа и теги", f["lost_reasons"] == [{"text": "дорого", "count": 1}] and
      {t["tag"] for t in f["tags"]} == {"vip", "повтор"}, str((f["lost_reasons"], f["tags"])))
hm = rep["heatmap"]
check("тепловая карта: 5 сообщений + 2 входящих звонка", sum(sum(r) for r in hm["grids"]["all"]) == 7
      and sum(sum(r) for r in hm["grids"]["call"]) == 2 and sum(sum(r) for r in hm["grids"]["instagram"]) == 2)
d_wa = (TODAY - timedelta(days=2)).weekday()
check("тепловая карта: WhatsApp в 10:00 и 16:00 по Бишкеку", hm["grids"]["whatsapp"][d_wa][10] == 1
      and hm["grids"]["whatsapp"][d_wa][16] == 1)
b = rep["bookings"]
check("записи: 3 визита (без отменённой), пришли 2, не пришли 1, отмена 1", (b["total"], b["arrived"], b["no_show"],
      b["cancelled"]) == (3, 2, 1, 1), str(b))
check("выручка 2 700, неявки 33%, средний чек 1 350", b["revenue"] == 2700 and b["no_show_pct"] == 33
      and b["avg_check"] == 1350, str((b["revenue"], b["no_show_pct"], b["avg_check"])))
check("прошлый период записей: 1 визит на 1 000", b["prev_total"] == 1 and b["prev_revenue"] == 1000)
check("повторные клиенты: 1 (Нурай была 50 дней назад)", b["repeat_clients"] == 1 and b["clients"] == 3, str(b["repeat_clients"]))
check("источники записей за период: по ссылке 1", b["sources"].get("online") == 1, str(b["sources"]))
m = b["masters"][0]
check("загрузка мастера: 180 мин из графика 9–19", m["booked_min"] == 180 and m["capacity_min"] > 0
      and m["load_pct"] == round(180 * 100 / m["capacity_min"]), str(m))
check("столбцы записей = итогу", sum(s["visits"]) == 3 and sum(s["arrived"]) == 2)
cat = rep["catalog"]
check("склад: 3 позиции, нет в наличии 1, мало 1, без фото 1, без цены 1", (cat["active"], cat["out_of_stock"],
      cat["low_stock"], cat["no_photo"], cat["no_price"]) == (3, 1, 1, 1, 1), str(cat))
mo_ = rep["money"]
check("кошелёк за период: потрачено 5 сом (1 мин), пополнено 100, кредитов 100", (mo_["wallet_spent"], mo_["paid_minutes"],
      mo_["wallet_topup"], mo_["credits_spent"]) == (5.0, 1.0, 100.0, 100), str(mo_))
check("пробелы базы знаний: 1", rep["bot"]["gaps"] == 1)
k = {x["id"]: x for x in rep["kpis"]}
check("KPI: обращения 7 (было 2: исходящий звонок не обращение), новые 7 (было 1), звонки 4 (было 2), время ответа 30 с",
      k["inbound"]["value"] == 7 and k["inbound"]["prev"] == 2 and sum(rep["prev"]["inbound"]) == 2
      and k["new_clients"]["value"] == 7 and k["new_clients"]["prev"] == 1 and k["calls"]["prev"] == 2
      and k["calls"]["value"] == 4 and k["calls"]["delta"]["dir"] == "up" and k["reply"]["value"] == 30
      and k["reply"]["good"] == "down", str({x: (y["value"], y.get("prev")) for x, y in k.items()}))
check("KPI ниши с записью: записи и выручка", "visits" in k and "revenue" in k and k["revenue"]["fmt"] == "som")
check("выводы посчитаны", isinstance(rep["insights"], list) and len(rep["insights"]) >= 2,
      " | ".join(i["text"] for i in rep["insights"]))

wk_rep = an.build_report(db, u, period="30d", granularity="week")
check("по неделям: те же итоги", wk_rep["period"]["granularity"] == "week" and sum(wk_rep["series"]["messages_in"]) == 5
      and sum(wk_rep["series"]["new_clients"]) == 7 and sum(wk_rep["series"]["calls"]) == 4 and len(wk_rep["buckets"]) <= 6)
yr = an.build_report(db, u, period="365d")
check("год по месяцам: старые данные тоже в столбцах", yr["period"]["granularity"] == "month"
      and sum(yr["series"]["messages_in"]) == 6 and sum(yr["series"]["new_clients"]) == 8
      and sum(yr["series"]["calls"]) == 6 and sum(yr["series"]["visits"]) == 4, str(sum(yr["series"]["messages_in"])))
td = an.build_report(db, u, period="today")
check("сегодня по часам: 24 столбца", td["period"]["granularity"] == "hour" and len(td["buckets"]) == 24)
ys = an.build_report(db, u, period="custom", date_from=(TODAY - timedelta(days=2)).isoformat(),
                     date_to=(TODAY - timedelta(days=2)).isoformat())
check("один день в прошлом по часам: WhatsApp в 10 и 16", ys["series"]["whatsapp_in"][10] == 1 and ys["series"]["whatsapp_in"][16] == 1
      and sum(ys["series"]["whatsapp_in"]) == 2)
sec = an.build_report(db, u, period="7d", sections=["kpis", "series"])
check("только KPI и ряды: тяжёлые блоки не считаются", sec["heatmap"] is None and sec["catalog"] is None
      and sec["money"] is None and sec["kpis"])

# API
H = {"Authorization": f"Bearer {create_jwt_token(u.id)}"}
r1 = cl.get("/api/analytics/report?period=30d", headers=H)
check("API: отчёт за 30 дней", r1.status_code == 200 and r1.json()["period"]["days"] == 30, r1.text[:200])
r2 = cl.get("/api/analytics/report?period=7d&sections=kpis,series,мусор", headers=H)
j2 = r2.json() if r2.status_code == 200 else {}
check("API: секции и защита от мусора (воронка и тепловая карта не считаются)", r2.status_code == 200
      and j2.get("funnel") is None and j2.get("heatmap") is None and j2.get("kpis"), r2.text[:200])
r3 = cl.get("/api/analytics/report?period=custom&from=2026-13-40&to=xx", headers=H)
check("API: кривые даты → 30 дней, не 500", r3.status_code == 200 and r3.json()["period"]["days"] == 30, r3.text[:200])
r4 = cl.get("/api/analytics/export.csv?period=30d", headers=H)
check("CSV: заголовок, итоговая строка, BOM для Excel", r4.status_code == 200 and r4.content.startswith(b"\xef\xbb\xbf")
      and "Обращения" in r4.text and "Итого" in r4.text and "attachment" in r4.headers.get("content-disposition", ""))
check("API без токена → 401/403", cl.get("/api/analytics/report").status_code in (401, 403))

# кабинет без чат-бота (только голосовой ассистент)
solo = User(email="voice@kg.kg", password_hash="x", email_verified=True, first_name="Голос", subscription_plan_id=plan.id,
            subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20))
db.add(solo)
db.flush()
call("inbound", 1, "13:00", True, 45, "+996555000077", owner=solo)
db.commit()
sr = an.build_report(db, solo, period="7d")
sk = {x["id"] for x in sr["kpis"]}
check("без бота: звонки и разговоры ассистентов, без CRM и мессенджеров", not sr["has_bot"] and "calls" in sk
      and "dialogs" in sk and "inbound" not in sk and sr["funnel"] is None and sr["calls"]["in"] == 1, str(sk))

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
