"""Отзывы и рейтинг салона (Voksy Beauty, шаг 2.3).

Часть 1 (без базы): подписанная ссылка «Оцените визит» (подделка и обрезка — отказ), подпись автора из имени карточки,
правило публикации (4–5 сразу, 1–3 — на проверку), оценка вне 1–5, права сотрудников, вставка {оценить} в просьбе
оценить (RU и KY), разметка Google (aggregateRating, «</» не закрывает тег), страницы, меню, значки, словари, журнал.

Часть 2 (PostgreSQL, REVIEWS_TEST_DATABASE_URL; база БУДЕТ ОЧИЩЕНА):
- бот: save_review 5 с текстом → на странице; 2 → ждёт проверки, владельцу сообщение; тот же визит — один отзыв
  (оценку меняют, а не добавляют); визит из двух услуг — один отзыв на первую запись с обеими услугами;
- ссылка: салон, визит, мастер; не тот визит / не состоялся / старше двух месяцев — понятный отказ; отзыв по ссылке,
  «без имени», изменение; после ответа салона изменить нельзя;
- кабинет: ждут проверки → показать / скрыть (журнал действий), ответ салона + отправка клиенту через очередь,
  скрытый владельцем остаётся скрытым после правки клиента, чужой отзыв — 404;
- витрина: рейтинг салона и мастеров только по опубликованным, распределение по звёздам, «Показать ещё», отзывы
  мастера, JSON-LD aggregateRating на /beauty/<slug>; «не показывать на странице» — ни отзывов, ни разметки;
- автоматизации: просьба оценить со ссылкой на этот визит; визит из двух услуг — одно «спасибо» после последней;
- сотрудник «Мастера и график» — да, «Клиенты» и мастер — 403; главная салона — «Отзывы ждут проверки»; кабинет
  мастера и бот видят рейтинг; перенос старых оценок из записей — один раз.
"""
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("REVIEWS_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="reviews-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL if not os.environ.get("TEST_LOGS") else logging.NOTSET)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import automations as au  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services import notify_queue as nq  # noqa: E402
from backend.services import reviews_service as rv  # noqa: E402
from backend.services import salon_page as sp  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────────── часть 1 ─────────────────────────────
bid = uuid.uuid4()
tok = rv.review_token(bid)
check("ссылка: токен 44 знака, обратно — та же запись", len(tok) == 44 and rv.booking_id_from_token(tok) == bid)
bad = tok[:-1] + ("0" if tok[-1] != "0" else "1")
check("ссылка: подделанная подпись / обрезанная / чужая запись — отказ", rv.booking_id_from_token(bad) is None
      and rv.booking_id_from_token(tok[:40]) is None and rv.booking_id_from_token(uuid.uuid4().hex + tok[32:]) is None
      and rv.booking_id_from_token("") is None)
check("ссылка: адрес /r/<токен> на HOST_URL", rv.review_link(bid) == "https://t.voksyai.online/r/" + tok)
check("подпись: «Айгерим Касымова» → «Айгерим К.», одно имя — как есть",
      rv.display_name("айгерим касымова") == "Айгерим К." and rv.display_name("Нурай") == "Нурай")
check("подпись: служебные имена («Клиент 0700…», «Гость сайта», цифры) — без имени",
      rv.display_name("Клиент 0700123456") == "" and rv.display_name("Гость сайта") == "" and rv.display_name("") == "")
check("публикация: 4–5 сразу, 1–3 — на проверку", [rv.status_for(s) for s in (5, 4, 3, 2, 1)]
      == ["published", "published", "pending", "pending", "pending"])
for v in (0, 6, "x", None):
    try:
        rv.clean_score(v)
        check(f"оценка {v!r} — отказ", False)
    except rv.ReviewError as e:
        check(f"оценка {v!r} — понятный отказ", "от 1 до 5" in str(e))
check("права: отзывы — «Мастера и график»", ts.staff_allowed(["booking_setup"], "GET", "/api/beauty/reviews")
      and ts.staff_allowed(["booking_setup"], "POST", f"/api/beauty/reviews/{uuid.uuid4()}/reply")
      and ts.staff_allowed(["booking_setup"], "PUT", "/api/beauty/reviews/settings")
      and not ts.staff_allowed(["crm", "booking", "inbox"], "GET", "/api/beauty/reviews")
      and not ts.staff_allowed(["booking_own"], "POST", f"/api/beauty/reviews/{uuid.uuid4()}/publish"))
rule = au.RULE_BY_KEY["review"]
for lang in ("ru", "ky"):
    with_link = au.render(rule[lang], {"имя": "Айжан", "мастер": "Айгуль", "оценить": "https://t.voksyai.online/r/abc"})
    without = au.render(rule[lang], {"имя": "Айжан", "мастер": "Айгуль", "оценить": ""})
    check(f"просьба оценить ({lang}): ссылка в тексте, без ссылки — кусок выпадает",
          "https://t.voksyai.online/r/abc" in with_link and "{" not in without and "r/" not in without and "[" not in without, without)
check("вставка {оценить} есть в списке вставок", "оценить" in {k for k, _ in au.PLACEHOLDERS})
view = {"name": "Салон </script> Айдана", "staff_word": "Мастера", "address": "Бишкек", "hours": [], "gallery": [],
        "reviews": {"enabled": True, "avg": 4.8, "count": 12, "texts": 5,
                    "items": [{"score": 5, "text": "Всё </script> супер", "author": "Айжан К.", "date": "2026-11-20"}]}}
head = sp.head_html(view, "https://t.voksyai.online", "/beauty/aidana")
ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', head, re.S).group(1).replace("<\\/", "</"))
check("Google: aggregateRating (средняя, оценок, отзывов с текстом) и отзыв", ld["aggregateRating"]["ratingValue"] == 4.8
      and ld["aggregateRating"]["ratingCount"] == 12 and ld["aggregateRating"]["reviewCount"] == 5
      and ld["review"][0]["reviewRating"]["ratingValue"] == 5, ld.get("aggregateRating"))
check("Google: «</script>» в тексте отзыва не закрывает тег", head.count("</script>") == 1)
view["reviews"] = {"enabled": False}
check("Google: отзывы выключены — без рейтинга", "aggregateRating" not in sp.head_html(view, "https://t.voksyai.online", "/beauty/x"))
html = open(os.path.join(ROOT, "backend/static/reviews.html"), encoding="utf-8").read()
check("кабинет: страница со своими js и css", "/static/reviews/reviews.js" in html and "/static/reviews/reviews.css" in html
      and "<title>Отзывы" in html)
rhtml = open(os.path.join(ROOT, "backend/static/review.html"), encoding="utf-8").read()
check("«Оцените визит»: место для заголовка с названием салона", "<!--REVIEW_HEAD-->" in rhtml and "/static/review/review.js" in rhtml)
side = open(os.path.join(ROOT, "backend/static/js/sidebar.js"), encoding="utf-8").read()
check("меню: «Отзывы» у ниш с записью (оба меню)", side.count("reviews-nav-item") == 2)
sprite = open(os.path.join(ROOT, "backend/static/icons/ui.svg"), encoding="utf-8").read()
have = set(re.findall(r'id="i-([a-z0-9-]+)"', sprite))
src = open(os.path.join(ROOT, "backend/static/reviews/reviews.js"), encoding="utf-8").read()
used = set(re.findall(r"icon\('([a-z0-9-]+)'", src))
check("значки экрана есть в спрайте", not (used - have), sorted(used - have))
for lang in ("ru", "ky"):
    d = json.load(open(os.path.join(ROOT, f"backend/static/i18n/{lang}.json"), encoding="utf-8"))
    check(f"словарь {lang}: sidebar.reviews", bool(d["sidebar"].get("reviews")))
from backend.services import activity as act  # noqa: E402
check("журнал действий: группа «Отзывы»", act.GROUPS.get("reviews", ("", ()))[1] == ("review",))

if not PG:
    print("SKIP часть 2: REVIEWS_TEST_DATABASE_URL не задан")
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
from backend.models.automation import NotificationQueue  # noqa: E402
from backend.models.booking import Booking, BookingResource  # noqa: E402
from backend.models.review import SalonReview  # noqa: E402
from backend.models.activity import ActivityLog  # noqa: E402
from backend.core.security import create_jwt_token  # noqa: E402
from backend.services import agent_tools, beauty_services, master_cabinet, salon_ai_tools  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
app_module.ensure_booking_tables()
app_module.ensure_user_profile_columns()
cl = TestClient(app_module.app, raise_server_exceptions=False)
B = bs.BISHKEK
db = SessionLocal()
NOW = bs.now_utc()
TODAY = bs.to_local(NOW).date()
LOWS = []
rv.NOTIFY_LOW = lambda owner_id, review_id: LOWS.append(str(review_id))


def at(day: date, hm: str):
    return datetime.combine(day, time.fromisoformat(hm), B).astimezone(timezone.utc)


plan = SubscriptionPlan(code="beauty_start", name="Beauty Start", price=1990, max_assistants=3, description="s",
                        is_active=True, product="beauty", limits={"staff": 3, "agent_mode": "reactive", "credits_month": 0})
db.add(plan)
db.flush()
u = User(email="rev@kg.kg", password_hash="x", email_verified=True, first_name="Айдана", company_name="Салон Айдана",
         subscription_plan_id=plan.id, subscription_end_date=NOW + timedelta(days=20), business_niche="beauty_salon",
         business_profile={"answers": {}})
other = User(email="other@kg.kg", password_hash="x", email_verified=True, first_name="Другой", business_niche="beauty_salon",
             subscription_plan_id=plan.id, subscription_end_date=NOW + timedelta(days=20))
db.add_all([u, other])
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор", is_active=True)
agent_o = AgentConfig(user_id=other.id, name="Бот", is_active=True)
db.add_all([agent, agent_o])
db.commit()
HO = {"Authorization": "Bearer " + create_jwt_token(u.id)}
HX = {"Authorization": "Bearer " + create_jwt_token(other.id)}
user = db.query(User).get(u.id)
page = bs.get_page(db, user, create=True)
page.settings = dict(page.settings or {}, address="Бишкек, Киевская 1", title="Салон Айдана")
page.enabled = True
db.commit()
SLUG = page.slug
svc = beauty_services.save(db, user, None, {"title": "Маникюр", "price": 1400, "duration_min": 60, "category": "Ногти"})
svc2 = beauty_services.save(db, user, None, {"title": "Брови", "price": 700, "duration_min": 30, "category": "Брови"})
db.commit()
fullday = {dd: [["00:00", "23:59"]] for dd in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
m1 = BookingResource(user_id=u.id, name="Айгуль", schedule=fullday, is_active=True)
m2 = BookingResource(user_id=u.id, name="Бермет", schedule=fullday, is_active=True)
mo = BookingResource(user_id=other.id, name="Чужой", schedule=fullday, is_active=True)
db.add_all([m1, m2, mo])
db.commit()
seq = [0]


def contact(name, phone, owner=None, ag=None):
    c = AgentContact(user_id=(owner or u).id, agent_config_id=(ag or agent).id, name=name, phone=phone, status="new", memory={})
    db.add(c)
    db.commit()
    return c


def booking(c, start, status="completed", master=None, service=None, visit_id=None, owner=None, minutes=50):
    s = service or svc
    b = Booking(user_id=(owner or u).id, resource_id=(master or m1).id, service_entry_id=s.id if s else None,
                service_title=s.title if s else None, contact_id=c.id, client_name=c.name, start_at=start,
                end_at=start + timedelta(minutes=minutes), status=status, price=1400, source="owner", visit_id=visit_id)
    db.add(b)
    db.commit()
    return b


def past(days, hm="12:00"):
    seq[0] += 1
    return at(TODAY - timedelta(days=days), hm) + timedelta(minutes=seq[0] % 50)


def reviews(**flt):
    db.expire_all()
    q = db.query(SalonReview)
    for k, v in flt.items():
        q = q.filter(getattr(SalonReview, k) == v)
    return q.all()


# ── бот: оценка из переписки ──
aizhan = contact("Айжан Токтогулова", "+996555100100")
b1 = booking(aizhan, past(1, "11:00"))
db.query(Booking).get(b1.id).review_asked_at = NOW - timedelta(hours=20)
db.commit()
ctx = {"agent_config_id": str(agent.id), "user_id": str(u.id), "user": user, "agent_config": agent, "surface": "postcall",
       "current_contact_id": str(aizhan.id), "call_direction": "whatsapp_inbound"}
r = json.loads(asyncio.run(agent_tools.execute_tool("save_review", {"score": 5, "comment": "Айгуль — золотые руки!"}, ctx, db)))
rr = reviews(booking_id=b1.id)
check("бот: 5 с текстом → отзыв на странице, подпись «Айжан Т.», оценка и в записи",
      r.get("ok") and r.get("published") and len(rr) == 1 and rr[0].status == "published" and rr[0].author_name == "Айжан Т."
      and rr[0].source == "chat" and db.query(Booking).get(b1.id).review_score == 5 and not LOWS, (r, [x.status for x in rr]))
r = json.loads(asyncio.run(agent_tools.execute_tool("save_review", {"score": 2, "comment": "долго ждала"}, ctx, db)))
rr = reviews(booking_id=b1.id)
check("бот: тот же визит — оценку меняют, а не добавляют; 2 → ждёт проверки, владельцу сообщение, ответ — извиниться",
      r.get("low") and "извинись" in r["next"] and len(reviews(contact_id=aizhan.id)) == 1 and rr[0].status == "pending"
      and rr[0].score == 2 and len(LOWS) == 1, (r, len(LOWS)))
r = json.loads(asyncio.run(agent_tools.execute_tool("save_review", {"score": 1}, ctx, db)))
check("бот: снова низкая (2 → 1) — повторно не дёргаем владельца; текст прежний", len(LOWS) == 1 and reviews(booking_id=b1.id)[0].text == "долго ждала")
r = json.loads(asyncio.run(agent_tools.execute_tool("save_review", {"score": 9}, ctx, db)))
check("бот: оценка вне 1–5 — отказ", not r.get("ok"))
ctx2 = dict(ctx, current_contact_id=str(contact("Без визитов", "+996555000111").id))
r = json.loads(asyncio.run(agent_tools.execute_tool("save_review", {"score": 5}, ctx2, db)))
check("бот: нет недавнего визита — понятный отказ", not r.get("ok") and "визит" in r.get("error", ""))

# визит из двух услуг
meer = contact("Мээрим", "+996550300300")
vid = uuid.uuid4()
v1 = booking(meer, past(2, "10:00"), visit_id=vid, master=m1)
v2 = booking(meer, v1.end_at + timedelta(minutes=5), visit_id=vid, master=m2, service=svc2, minutes=30)
rec, created, low = rv.record(db, db.query(Booking).get(v2.id), 4, "Всё понравилось", "chat")
db.commit()
rec2, created2, _ = rv.record(db, db.query(Booking).get(v1.id), 5, None, "chat")
db.commit()
vr = reviews(visit_id=vid)
check("визит из двух услуг: один отзыв на первую запись, обе услуги, мастер первой",
      created and not created2 and len(vr) == 1 and vr[0].booking_id == v1.id and vr[0].service_title == "Маникюр, Брови"
      and vr[0].resource_id == m1.id and vr[0].score == 5 and vr[0].text == "Всё понравилось", [(x.service_title, x.score) for x in vr])

# ── ссылка «Оцените визит» ──
nur = contact("Нурай Абдыкадырова", "+996700200200")
b2 = booking(nur, past(3, "15:00"), master=m2)
tok2 = rv.review_token(b2.id)
g = cl.get(f"/api/public/review/{tok2}")
G = g.json() if g.status_code == 200 else {}
check("ссылка: салон, визит, мастер, подпись по умолчанию, отзыва ещё нет",
      g.status_code == 200 and G["salon"]["name"] == "Салон Айдана" and G["visit"]["service"] == "Маникюр"
      and G["visit"]["master"]["name"] == "Бермет" and G["suggest_name"] == "Нурай А." and G["review"] is None
      and G["can_edit"] and "_booking" not in G, g.text[:300])
r = cl.get(f"/r/{tok2}")
check("страница /r/<токен>: заголовок с названием салона, noindex", r.status_code == 200 and "Оцените визит — Салон Айдана" in r.text
      and 'name="robots" content="noindex"' in r.text and "<!--REVIEW_HEAD-->" not in r.text)
check("ссылка: подделанная — 404 с понятным текстом", cl.get(f"/api/public/review/{tok2[:-2]}00").status_code == 404
      and "неверная" in cl.get(f"/api/public/review/{tok2[:-2]}00").json()["message"])
b_future = booking(nur, at(TODAY + timedelta(days=2), "12:00"), status="booked")
r = cl.get(f"/api/public/review/{rv.review_token(b_future.id)}")
check("ссылка: визит ещё не состоялся — отказ", r.status_code == 404 and "состоялся" in r.json()["message"])
b_old = booking(nur, past(70))
r = cl.get(f"/api/public/review/{rv.review_token(b_old.id)}")
check("ссылка: старше двух месяцев — отказ", r.status_code == 404 and "истёк" in r.json()["message"])
r = cl.post(f"/api/public/review/{tok2}", json={"score": 4, "text": "  Очень аккуратно,\n\n\n\nспасибо!  ", "name": "Нурай"})
nr = reviews(booking_id=b2.id)
check("ссылка: 4 → сразу на странице, текст почищен, своя подпись", r.status_code == 200 and r.json()["status"] == "published"
      and len(nr) == 1 and nr[0].source == "link" and nr[0].author_name == "Нурай" and nr[0].text == "Очень аккуратно,\n\nспасибо!", r.text)
r = cl.post(f"/api/public/review/{tok2}", json={"score": 3, "text": "Подумав — так себе", "anonymous": True})
nr = reviews(booking_id=b2.id)
check("ссылка: изменить отзыв (3, без имени) → ждёт проверки, владельцу сообщение", r.status_code == 200
      and r.json()["status"] == "pending" and len(nr) == 1 and nr[0].author_name == "" and len(LOWS) == 2, r.text)
check("ссылка: оценка 0 — 400", cl.post(f"/api/public/review/{tok2}", json={"score": 0}).status_code == 400)

# ── кабинет ──
r = cl.get("/api/beauty/reviews?status=pending", headers=HO)
D = r.json() if r.status_code == 200 else {}
pend_ids = {x["id"] for x in D.get("items", [])}
check("кабинет: ждут проверки — обе низкие оценки, имя и телефон клиента владельцу",
      r.status_code == 200 and pend_ids == {str(reviews(booking_id=b1.id)[0].id), str(nr[0].id)}
      and D["overview"]["counts"]["pending"] == 2 and any(x["client_phone"] == "+996700200200" for x in D["items"]), r.text[:300])
check("кабинет: клиентов не просят оценить (правило выключено) — подсказка", D["overview"]["asking"] is False)
nid = str(nr[0].id)
r = cl.post(f"/api/beauty/reviews/{nid}/reply", headers=HO,
            json={"text": "Нурай, простите! Мы поговорили с мастером.", "send": True})
qrow = db.query(NotificationQueue).filter(NotificationQueue.rule == "review_reply").all()
check("ответ салона: сохранён и встал в очередь клиенту", r.status_code == 200 and r.json()["queued"]
      and len(qrow) == 1 and "простите" in qrow[0].text and qrow[0].contact_id == nur.id, r.text)
r = cl.post(f"/api/beauty/reviews/{nid}/reply", headers=HO, json={"text": "Нурай, простите! Мы поговорили с мастером.", "send": True})
check("ответ салона: тот же текст ещё раз — второй строки в очереди нет", r.status_code == 200
      and db.query(NotificationQueue).filter(NotificationQueue.rule == "review_reply").count() == 1)
r = cl.post(f"/api/public/review/{tok2}", json={"score": 5, "text": "передумала"})
check("после ответа салона клиент отзыв не меняет", r.status_code == 400 and "ответил" in r.json()["message"]
      and cl.get(f"/api/public/review/{tok2}").json()["can_edit"] is False)
r = cl.post(f"/api/beauty/reviews/{nid}/publish", headers=HO)
check("показать на странице (оценка 3 после проверки) + журнал действий", r.status_code == 200 and r.json()["status"] == "published"
      and db.query(ActivityLog).filter(ActivityLog.action == "review.publish", ActivityLog.object_id == nid).count() == 1)
aid = str(reviews(booking_id=b1.id)[0].id)
r = cl.post(f"/api/beauty/reviews/{aid}/hide", headers=HO)
check("скрыть со страницы", r.status_code == 200 and reviews(booking_id=b1.id)[0].status == "hidden")
rv.record(db, db.query(Booking).get(b1.id), 5, "Всё же хорошо", "chat")
db.commit()
check("скрытый владельцем остаётся скрытым после правки клиента", reviews(booking_id=b1.id)[0].status == "hidden")
check("чужой отзыв / неверный id — 404", cl.post(f"/api/beauty/reviews/{aid}/publish", headers=HX).status_code == 404
      and cl.post(f"/api/beauty/reviews/{uuid.uuid4()}/hide", headers=HO).status_code == 404
      and cl.post("/api/beauty/reviews/xyz/hide", headers=HO).status_code in (404, 422))
r = cl.get("/api/beauty/reviews?score=-1", headers=HO)
check("фильтр «1–3 звезды»", r.status_code == 200 and {x["score"] for x in r.json()["items"]} <= {1, 2, 3}
      and r.json()["items"])

# ── витрина ──
for i in range(14):
    c = contact(f"Клиентка{i} Тест", f"+99655570{i:04d}")
    b = booking(c, past(5 + i), master=m1 if i % 2 else m2)
    rv.record(db, b, 5 if i % 3 else 4, f"Отзыв номер {i}" if i < 12 else None, "link", author_name=f"Клиентка{i}")
db.commit()
pub = cl.get(f"/api/public/booking/{SLUG}").json()
R = pub["salon"]["reviews"]
n_pub = db.query(SalonReview).filter(SalonReview.user_id == u.id, SalonReview.status == "published").all()
avg = round(sum(x.score for x in n_pub) / len(n_pub), 1)
check("витрина: рейтинг только по опубликованным, распределение, первые 6 с текстом и «ещё»",
      R["enabled"] and R["count"] == len(n_pub) and R["avg"] == avg and sum(R["dist"].values()) == R["count"]
      and len(R["items"]) == 6 and all(x["text"] for x in R["items"]) and R["next_offset"] == 6
      and "client_phone" not in R["items"][0], (R.get("count"), R.get("avg"), avg))
mr = {m["id"]: m.get("rating") for m in pub["resources"]}
check("витрина: рейтинг у каждого мастера", mr[str(m1.id)] and mr[str(m2.id)] and mr[str(m1.id)]["count"] >= 7, mr)
more = cl.get(f"/api/public/booking/{SLUG}/reviews?offset=6&limit=10").json()
allr = cl.get(f"/api/public/booking/{SLUG}/reviews?limit=30").json()
check("витрина: «Показать ещё» без повторов, оценки без слов в ленту не попадают",
      len(more["items"]) == allr["total"] - 6 and not ({x["id"] for x in more["items"]} & {x["id"] for x in R["items"]})
      and all(x["text"] for x in allr["items"]) and more["next_offset"] is None, (len(more["items"]), allr["total"]))
mm = cl.get(f"/api/public/booking/{SLUG}/reviews?master={m2.id}").json()
check("витрина: отзывы мастера", mm["items"] and all(x["master_id"] == str(m2.id) for x in mm["items"]))
check("витрина: ждущие проверки и скрытые не видны", not any("долго ждала" in x["text"] or "Всё же хорошо" in x["text"]
                                                             for x in allr["items"]))
h = cl.get(f"/beauty/{SLUG}").text
ldp = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', h, re.S).group(1).replace("<\\/", "</"))
check("страница салона: JSON-LD aggregateRating", ldp.get("aggregateRating", {}).get("ratingValue") == avg
      and ldp["aggregateRating"]["ratingCount"] == len(n_pub), ldp.get("aggregateRating"))
r = cl.put("/api/beauty/reviews/settings", headers=HO, json={"show_on_page": False})
pub2 = cl.get(f"/api/public/booking/{SLUG}").json()
check("«не показывать на странице»: ни отзывов, ни рейтинга мастеров, ни разметки",
      r.status_code == 200 and not pub2["salon"]["reviews"]["enabled"] and not any(m.get("rating") for m in pub2["resources"])
      and cl.get(f"/api/public/booking/{SLUG}/reviews").json()["items"] == []
      and "aggregateRating" not in cl.get(f"/beauty/{SLUG}").text)
cl.put("/api/beauty/reviews/settings", headers=HO, json={"show_on_page": True})
r = cl.put("/api/booking/page", headers=HO, json={"tagline": "Красота рядом"})
check("сохранение страницы салона не сбрасывает «показывать отзывы»", r.status_code == 200
      and cl.get(f"/api/public/booking/{SLUG}").json()["salon"]["reviews"]["enabled"])

# ── автоматизации: просьба оценить со ссылкой ──
sent = []


async def fake_send(db_, user_, contact_, message):
    sent.append((str(contact_.id), message))
    return True, "whatsapp", None


nq.SENDER = fake_send
nq.SERVICE_WINDOW = (0, 24)
r = cl.put("/api/beauty/automations/thanks", headers=HO, json={"enabled": True, "params": {"hours": 1, "ask_review": True}})
check("включили «просить оценку»", r.status_code == 200)
m3 = BookingResource(user_id=u.id, name="Чолпон", schedule=fullday, is_active=True)
m4 = BookingResource(user_id=u.id, name="Динара", schedule=fullday, is_active=True)
db.add_all([m3, m4])
db.commit()
aru = contact("Айпери", "+996770111222")
end_ago = NOW - timedelta(hours=2)
b3 = booking(aru, end_ago - timedelta(minutes=50), master=m3)
vc = contact("Бегимай", "+996770333444")
vid2 = uuid.uuid4()
w1 = booking(vc, NOW - timedelta(hours=4), visit_id=vid2, master=m1)
w2 = booking(vc, w1.end_at + timedelta(minutes=5), visit_id=vid2, master=m2, service=svc2, minutes=30)
vc_late = contact("Самара", "+996770555666")
vid3 = uuid.uuid4()
x1 = booking(vc_late, NOW - timedelta(hours=3), visit_id=vid3, master=m4)
x2 = booking(vc_late, NOW - timedelta(minutes=20), visit_id=vid3, master=m3, service=svc2, minutes=60)   # ещё идёт
au.plan_bookings(db, NOW)
rq = db.query(NotificationQueue).filter(NotificationQueue.rule == "review").all()
mine = [x for x in rq if x.contact_id == aru.id]
check("просьба оценить: ссылка именно на этот визит", len(mine) == 1 and rv.review_link(b3.id) in mine[0].text, mine[0].text if mine else rq)
vcq = [x for x in rq if x.contact_id == vc.id]
check("визит из двух услуг: одна просьба, после последней услуги, ссылка ведёт к визиту",
      len(vcq) == 1 and rv.review_link(w2.id) in vcq[0].text, [x.text for x in vcq])
check("визит ещё идёт (вторая услуга не кончилась) — «спасибо» ждёт", not [x for x in rq if x.contact_id == vc_late.id])
asyncio.run(nq.process(db, NOW + timedelta(seconds=1)))
db.expire_all()
check("после отправки просьбы — у записи отмечено «попросили оценку»", db.query(Booking).get(b3.id).review_asked_at is not None
      and any(c == str(aru.id) and "/r/" in m for c, m in sent))
link = re.search(r"/r/([0-9a-f]{44})", mine[0].text).group(1)
r = cl.post(f"/api/public/review/{link}", json={"score": 5, "text": "Спасибо за маникюр!"})
check("клиент оценил по ссылке из сообщения", r.status_code == 200 and reviews(booking_id=b3.id)[0].status == "published")
ov = cl.get("/api/beauty/reviews", headers=HO).json()["overview"]
check("кабинет: теперь клиентов просят оценить", ov["asking"] is True and ov["asking_by"] == "rule")

# ── сотрудники ──
mgr, _ = ts.create_member(db, user, name="Управляющий", email="mgr@kg.kg", password="secret12", role_title="Управляющий",
                          permissions=["booking", "booking_setup", "crm"])
crm_only, _ = ts.create_member(db, user, name="Админ", email="crm@kg.kg", password="secret12", role_title="Администратор",
                               permissions=["booking", "crm", "inbox"])
master_m, _ = ts.create_member(db, user, name="Мастер", email="m@kg.kg", password="secret12", role_title="Мастер",
                               permissions=["booking_own"])
db.commit()


def sh(m):
    return {"Authorization": "Bearer " + create_jwt_token(u.id, extra={"staff": str(m.id), "stv": m.token_version})}


check("сотрудник «Мастера и график»: видит и отвечает", cl.get("/api/beauty/reviews", headers=sh(mgr)).status_code == 200
      and cl.post(f"/api/beauty/reviews/{nid}/reply", headers=sh(mgr), json={"text": "Ждём вас снова"}).status_code == 200)
check("ответ сотрудника подписан его именем", reviews(booking_id=b2.id)[0].reply_by == "Управляющий")
check("сотрудник «Клиенты» и мастер — 403", cl.get("/api/beauty/reviews", headers=sh(crm_only)).status_code == 403
      and cl.post(f"/api/beauty/reviews/{nid}/hide", headers=sh(master_m)).status_code == 403)

# ── главная, кабинет мастера, бот ──
sad = contact("Грустная", "+996555123123")
rv.record(db, booking(sad, past(4), master=m3), 2, "Опоздали на полчаса", "link")
db.commit()
home = cl.get("/api/beauty/home", headers=HO).json()
att = {a["key"]: a for a in home.get("attention") or []}
n_pend = len(reviews(status="pending"))
check("главная: «Отзывы ждут проверки» со ссылкой на раздел", n_pend and att.get("low_reviews", {}).get("count") == n_pend
      and "reviews.html?status=pending" in att["low_reviews"]["href"], att.get("low_reviews"))
stt = master_cabinet.stats(db, db.query(BookingResource).get(m1.id))
check("кабинет мастера: рейтинг и последние отзывы (только опубликованные)", stt["rating"] and stt["rating"]["count"] >= 7
      and 0 < len(stt["reviews"]) <= 3 and all(x["master_id"] == str(m1.id) for x in stt["reviews"]), stt.get("rating"))
staff = salon_ai_tools.get_staff(db, user, "Айгуль")
info = salon_ai_tools.get_salon_information(db, user)
check("бот видит рейтинг мастера и салона", "из 5" in staff["master"].get("rating", "") and "из 5" in info.get("rating", ""),
      (staff["master"].get("rating"), info.get("rating")))

# ── перенос старых оценок ──
old_c = contact("Старая Оценка", "+996555999000")
ob = booking(old_c, past(20))
ob2 = booking(old_c, past(21))
db.execute(text("UPDATE bookings SET review_score = 5, review_text = 'Было отлично', review_at = now() WHERE id = :i"), {"i": ob.id})
db.execute(text("UPDATE bookings SET review_score = 2, review_at = now() WHERE id = :i"), {"i": ob2.id})
db.commit()
with engine.begin() as conn:
    n1 = rv.backfill_from_bookings(conn)
with engine.begin() as conn:
    n2 = rv.backfill_from_bookings(conn)
o1, o2 = reviews(booking_id=ob.id), reviews(booking_id=ob2.id)
check("перенос старых оценок: 5 — на странице, 2 — на проверку, повтор ничего не добавляет",
      n1 == 2 and n2 == 0 and o1[0].status == "published" and o1[0].text == "Было отлично" and o1[0].author_name == "Старая О."
      and o2[0].status == "pending", (n1, n2))
db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
