"""
Мастера и роли (Voksy Beauty, шаг 1.2).

Часть 1 (без сети и базы): правила доступа сотрудника (мастер — только /api/booking/me*, бухгалтер —
финансы), разделы и домашняя страница мастера, своя цена/длительность мастера, опыт, инициалы,
обработка фото (уменьшение, EXIF, прозрачность, не картинка).

Часть 2 (PostgreSQL, MASTERS_TEST_DATABASE_URL): миграция колонок карточки мастера, фото мастера и
портфолио (загрузка, порядок, удаление, публичная отдача с кэшем на год), цены и длительность мастера в
свободном времени и в записи, «только через администратора» (ссылка, бот, звонок), публичная страница
без телефонов и графика, вход мастера одной кнопкой и привязка, кабинет мастера: видит только своё,
не отменяет, не трогает чужие записи, переносит свою, записывает к себе, телефоны клиентов — по
настройке владельца, заработок (% + оклад), статистика, клиенты, лента; бухгалтер — финансы, мастер —
нет; удаление сотрудника отвязывает мастера, удаление мастера удаляет его фото.
"""

import io
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("MASTERS_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="masters-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import team_service as ts  # noqa: E402
from backend.services import booking_service as bs  # noqa: E402
from backend.services import salon_media as sm  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────── часть 1: правила и чистые функции ─────────────────────────
A = ts.staff_allowed
check("мастер: свой кабинет открыт", all(A(["booking_own"], m, p) for m, p in [
    ("GET", "/api/booking/me"), ("GET", "/api/booking/me/clients"), ("GET", "/api/booking/me/slots"),
    ("POST", "/api/booking/me/bookings"), ("PATCH", "/api/booking/me/bookings/1"), ("PATCH", "/api/booking/me/profile"),
    ("POST", "/api/booking/me/media"), ("DELETE", "/api/booking/me/media/1")]))
check("мастер: общий журнал, мастера и правила закрыты", not any(A(["booking_own"], m, p) for m, p in [
    ("GET", "/api/booking/overview"), ("GET", "/api/booking/slots"), ("POST", "/api/booking/bookings"),
    ("PATCH", "/api/booking/bookings/1"), ("DELETE", "/api/booking/bookings/1"), ("GET", "/api/booking/resources"),
    ("PATCH", "/api/booking/resources/1"), ("PUT", "/api/booking/settings"), ("POST", "/api/booking/resources/1/media")]))
check("мастер: не удаляет запись и не видит клиентов CRM", not A(["booking_own"], "DELETE", "/api/booking/me/bookings/1")
      and not A(["booking_own"], "GET", "/api/agent/contacts") and not A(["booking_own"], "GET", "/api/finance/summary"))
check("кабинет мастера — только с «Свой журнал»", not A(["booking"], "GET", "/api/booking/me")
      and not A(["booking_setup", "crm"], "POST", "/api/booking/me/bookings"))
check("фото мастеров — у «Мастера и график»", A(["booking_setup"], "POST", "/api/booking/resources/1/media")
      and A(["booking_setup"], "DELETE", "/api/booking/resources/1/media/2") and not A(["booking"], "POST", "/api/booking/resources/1/media"))
check("вход мастера создаёт только владелец", not A(ts.PERM_KEYS, "POST", "/api/booking/resources/1/login")
      and not A(ts.PERM_KEYS, "PUT", "/api/booking/resources/1/login"))
check("бухгалтер: финансы целиком", all(A(["finance"], m, p) for m, p in [
    ("GET", "/api/finance/summary"), ("GET", "/api/finance/expenses"), ("POST", "/api/finance/expenses"),
    ("PATCH", "/api/finance/expenses/1"), ("DELETE", "/api/finance/expenses/1"), ("PATCH", "/api/finance/payroll/1")]))
check("финансы закрыты без раздела", not any(A([k for k in ts.PERM_KEYS if k != "finance"], "GET", "/api/finance/summary") for _ in [0]))
check("бухгалтер: кошелёк и тарифы по-прежнему закрыты", not A(["finance"], "GET", "/api/wallet/balance")
      and not A(["finance"], "POST", "/api/payments/create-payment"))
check("разделы: мастер → «Мой день»", [s["id"] for s in ts.sections(["booking_own"])] == ["booking_own"]
      and ts.home_page(["booking_own"]) == "/static/master.html")
check("разделы: управляющий видит кассу и финансы последними",
      [s["id"] for s in ts.sections(["finance", "booking", "booking_setup", "analytics", "inbox"])] == ["inbox", "booking", "analytics", "cash", "finance"])
check("права: новые разделы известны и упорядочены",
      ts.clean_permissions(["finance", "booking_own", "booking"]) == ["booking", "booking_own", "finance"])

sid = str(uuid.uuid4())
check("свои цены: мусор отбрасывается", bs.clean_service_prices({sid: {"price": "1500", "duration_min": "90"}, "x": {"price": 1},
                                                                  str(uuid.uuid4()): {"price": -5, "duration_min": 3}})
      == {sid: {"price": 1500.0, "duration_min": 90}})


class _Svc:
    def __init__(self, id, price, dur):
        self.id, self.price, self.duration_min = id, price, dur


class _Res:
    def __init__(self, sp):
        self.service_prices = sp


svc = _Svc(uuid.UUID(sid), 1200, 60)
check("цена и время мастера", bs.service_terms(_Res({sid: {"price": 1800, "duration_min": 90}}), svc) == (90, 1800.0))
check("без своих — из каталога", bs.service_terms(_Res(None), svc) == (60, 1200)
      and bs.service_terms(_Res({sid: {"price": 2000}}), svc) == (60, 2000.0))
check("без услуги — 60 минут", bs.service_terms(_Res(None), None) == (60, None))
year = bs.to_local(bs.now_utc()).year
check("опыт: с 2018 года", bs.experience_years(2018) == year - 2018 and bs.experience_years(year + 1) is None
      and bs.experience_years("abc") is None and bs.experience_years(1900) is None)
check("инициалы", bs.initials("Айгуль Садыкова") == "АС" and bs.initials("мария") == "М" and bs.initials("") == "?")

try:
    from PIL import Image
    big = io.BytesIO()
    Image.new("RGB", (4000, 3000), (200, 120, 90)).save(big, "JPEG", quality=95)
    jpeg, w, h = sm.prepare_image(big.getvalue(), "avatar")
    check("аватар уменьшен до 720 px", max(w, h) == 720 and Image.open(io.BytesIO(jpeg)).format == "JPEG", (w, h))
    jpeg, w, h = sm.prepare_image(big.getvalue(), "portfolio")
    check("работа — до 1600 px", max(w, h) == 1600, (w, h))
    # EXIF: снято «боком» (Orientation=6) — после обработки картинка стоит
    tall = io.BytesIO()
    im = Image.new("RGB", (300, 200), (10, 20, 30))
    exif = im.getexif()
    exif[0x0112] = 6
    im.save(tall, "JPEG", exif=exif)
    _, w2, h2 = sm.prepare_image(tall.getvalue(), "portfolio")
    check("поворот по EXIF", (w2, h2) == (200, 300), (w2, h2))
    png = io.BytesIO()
    Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(png, "PNG")
    jp, _, _ = sm.prepare_image(png.getvalue(), "certificate")
    px = Image.open(io.BytesIO(jp)).convert("RGB").getpixel((5, 5))
    check("прозрачность — на белом", min(px) > 240, px)
    try:
        sm.prepare_image(b"%PDF-1.4 not an image", "portfolio")
        check("не картинка — отказ", False)
    except sm.MediaError:
        check("не картинка — отказ", True)
    try:
        sm.prepare_image(b"x" * (sm.MAX_UPLOAD_BYTES + 1), "portfolio")
        check("больше 15 МБ — отказ", False)
    except sm.MediaError as e:
        check("больше 15 МБ — отказ", "15" in str(e))
except ImportError:
    check("Pillow установлен", False)

if not PG:
    print("SKIP: часть 2 — MASTERS_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ───────────────────────── часть 2: PostgreSQL ─────────────────────────
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.models.booking import Booking, BookingResource, SalonMedia  # noqa: E402
from backend.models.team import TeamMember  # noqa: E402
from backend.core.security import create_jwt_token, hash_password  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)

import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# миграция: у старой базы нет колонок карточки — их добавит ensure_master_card_columns
with engine.begin() as conn:
    for col in ("photo_id", "bio", "experience_since", "portfolio", "certificates", "service_prices", "team_member_id", "online_booking"):
        conn.execute(text(f"ALTER TABLE booking_resources DROP COLUMN {col}"))
app_module.ensure_master_card_columns()
app_module.ensure_master_card_columns()   # идемпотентно
with engine.connect() as conn:
    cols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='booking_resources'"))}
check("миграция: колонки карточки мастера добавлены", {"photo_id", "bio", "portfolio", "service_prices", "team_member_id",
                                                       "online_booking"} <= cols, sorted(cols))
app_module.ensure_user_profile_columns()
app_module.ensure_team_tables()
app_module.ensure_finance_tables()

cl = TestClient(app_module.app, raise_server_exceptions=False)
now = datetime.now(timezone.utc)
db = SessionLocal()


def plan(code):
    p = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).first()
    if not p:
        p = SubscriptionPlan(code=code, name=code.title(), price=100, max_assistants=3, description=code, is_active=True)
        db.add(p)
        db.flush()
    return p


owner = User(email="owner@salon.kg", password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
             wallet_balance=100000, first_name="Айгерим", company_name="Салон Айгерим", business_niche="beauty_salon",
             business_profile={"modules": {"booking": True}})
owner.subscription_plan_id = plan("start").id
owner.subscription_end_date = now + timedelta(days=20)
db.add(owner)
db.flush()
other = User(email="other@salon.kg", password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
             first_name="Чужой", business_niche="beauty_salon")
db.add(other)
agent = AgentConfig(user_id=owner.id, name="Бот салона")
db.add(agent)
db.flush()
mani = AgentKnowledgeEntry(user_id=owner.id, agent_config_id=agent.id, kind="product", title="Маникюр с покрытием",
                           price=1200, duration_min=60, is_active=True, collection="Ногти")
cut = AgentKnowledgeEntry(user_id=owner.id, agent_config_id=agent.id, kind="product", title="Стрижка", price=800,
                          duration_min=45, is_active=True, collection="Волосы")
db.add_all([mani, cut])
db.commit()

HO = {"Authorization": f"Bearer {create_jwt_token(owner.id)}"}
HX = {"Authorization": f"Bearer {create_jwt_token(other.id)}"}


def err(r):
    try:
        d = r.json()
    except Exception:
        return {}
    x = d.get("detail") or d.get("message")
    return x if isinstance(x, dict) else {"message": x}


def img(w=900, h=700, color=(220, 150, 170)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


# ── мастера и карточка
r = cl.get("/api/booking/overview", headers=HO)
check("журнал открылся: первый мастер создан", r.status_code == 200 and len(r.json()["resources"]) == 1, r.text[:200])
aigul = r.json()["resources"][0]
check("карточка: новые поля по умолчанию", aigul.get("online_booking") is True and aigul.get("portfolio") == []
      and aigul.get("photo_url") is None and aigul.get("initials") and "pay_percent" in aigul, aigul)
r = cl.post("/api/booking/resources", headers=HO, json={"name": "Мээрим", "role": "Барбер"})
meerim = r.json()["resource"]
r = cl.patch(f"/api/booking/resources/{aigul['id']}", headers=HO, json={
    "name": "Айгуль", "role": "Мастер маникюра", "bio": "Аккуратный маникюр", "experience_since": 2018,
    "service_ids": [str(mani.id)], "service_prices": {str(mani.id): {"price": 1800, "duration_min": 90}},
    "schedule": {d: [["09:00", "19:00"]] for d in bs.DAYS}})
aigul = r.json().get("resource") or {}
check("карточка сохранена: о себе, опыт, свои цены", r.status_code == 200 and aigul.get("bio") == "Аккуратный маникюр"
      and aigul.get("experience_years") == year - 2018
      and aigul.get("service_prices") == {str(mani.id): {"price": 1800.0, "duration_min": 90}}, r.text[:300])
r = cl.patch(f"/api/booking/resources/{aigul['id']}", headers=HO, json={"experience_since": 1700})
check("неверный год — 400", r.status_code == 400, r.text[:120])
cl.patch(f"/api/booking/resources/{meerim['id']}", headers=HO, json={"schedule": {d: [["10:00", "20:00"]] for d in bs.DAYS}})

# ── фото
r = cl.post(f"/api/booking/resources/{aigul['id']}/media?kind=avatar", headers=HO, files={"file": ("a.jpg", img(1600, 1600), "image/jpeg")})
j = r.json() if r.status_code == 200 else {}
check("аватар загружен", r.status_code == 200 and (j.get("resource") or {}).get("photo_url", "").startswith("/media/salon/"), r.text[:200])
first_photo = (j.get("resource") or {}).get("photo_id")
r = cl.get(f"/media/salon/{first_photo}")
check("фото отдаётся без входа, кэш на год", r.status_code == 200 and r.headers.get("content-type") == "image/jpeg"
      and "immutable" in r.headers.get("cache-control", "") and r.content[:2] == b"\xff\xd8", (r.status_code, r.headers.get("cache-control")))
r = cl.post(f"/api/booking/resources/{aigul['id']}/media?kind=avatar", headers=HO, files={"file": ("b.jpg", img(), "image/jpeg")})
new_photo = (r.json().get("resource") or {}).get("photo_id")
check("новый аватар заменил старый, старый файл удалён", new_photo and new_photo != first_photo
      and cl.get(f"/media/salon/{first_photo}").status_code == 404, (first_photo, new_photo))
pids = []
for i in range(3):
    r = cl.post(f"/api/booking/resources/{aigul['id']}/media?kind=portfolio", headers=HO,
                files={"file": (f"w{i}.jpg", img(color=(i * 60, 100, 150)), "image/jpeg")}, data={"title": f"Работа {i}"})
    pids.append((r.json().get("media") or {}).get("id"))
r = cl.post(f"/api/booking/resources/{aigul['id']}/media?kind=certificate", headers=HO,
            files={"file": ("c.png", img(600, 400), "image/png")}, data={"title": "Kodi 2023"})
res = r.json().get("resource") or {}
check("портфолио и сертификат", [p["id"] for p in res.get("portfolio", [])] == pids and res["portfolio"][0]["caption"] == "Работа 0"
      and res.get("certificates", [{}])[0].get("title") == "Kodi 2023", res.get("portfolio"))
r = cl.post(f"/api/booking/resources/{aigul['id']}/media?kind=portfolio", headers=HO, files={"file": ("x.txt", b"hello", "text/plain")})
check("не картинка — 400 с понятной причиной", r.status_code == 400 and "картин" in (err(r).get("message") or ""), r.text[:160])
r = cl.post(f"/api/booking/resources/{aigul['id']}/media?kind=selfie", headers=HO, files={"file": ("a.jpg", img(), "image/jpeg")})
check("неизвестный вид фото — 400", r.status_code == 400, r.text[:120])
fake = str(uuid.uuid4())
r = cl.patch(f"/api/booking/resources/{aigul['id']}", headers=HO, json={
    "portfolio": [{"id": pids[2], "caption": "Френч"}, {"id": fake, "caption": "чужое"}, {"id": pids[0], "caption": "  Нюд  "}]})
pf = (r.json().get("resource") or {}).get("portfolio") or []
check("порядок и подписи; чужие id не принимаются, забытые не теряются",
      [p["id"] for p in pf] == [pids[2], pids[0], pids[1]] and pf[0]["caption"] == "Френч" and pf[1]["caption"] == "Нюд", pf)
r = cl.delete(f"/api/booking/resources/{aigul['id']}/media/{pids[1]}", headers=HO)
check("удалить работу", r.status_code == 200 and len(r.json()["resource"]["portfolio"]) == 2
      and cl.get(f"/media/salon/{pids[1]}").status_code == 404, r.text[:160])
r = cl.post(f"/api/booking/resources/{aigul['id']}/media?kind=portfolio", headers=HX, files={"file": ("a.jpg", img(), "image/jpeg")})
check("чужой кабинет не загрузит фото чужому мастеру", r.status_code == 404, r.status_code)
r = cl.get("/media/salon/not-a-uuid")
check("кривой id фото — 404", r.status_code == 404)

# ── цены и время мастера в записи
tomorrow = (bs.to_local(bs.now_utc()) + timedelta(days=1)).date().isoformat()
r = cl.get(f"/api/booking/slots?service_id={mani.id}&resource_id={aigul['id']}&date={tomorrow}", headers=HO)
slots = r.json()["days"][0]["slots"]
check("свободное время по длительности мастера (90 мин): последнее окно 17:30",
      slots and slots[-1]["time"] == "17:30", slots[-2:] if slots else r.text[:200])
r = cl.post("/api/booking/bookings", headers=HO, json={"start": f"{tomorrow}T10:00", "service_id": str(mani.id),
                                                       "resource_id": aigul["id"], "client_name": "Жылдыз", "client_phone": "+996700111222"})
b1 = r.json().get("booking") or {}
check("запись к мастеру: 90 минут и цена мастера 1800", b1.get("duration_min") == 90 and b1.get("price") == 1800.0, b1)

# ── «только через администратора»
cl.patch(f"/api/booking/resources/{meerim['id']}", headers=HO, json={"online_booking": False})
page = bs.get_page(db, owner)
db.commit()
slug = page.slug
r = cl.get(f"/api/public/booking/{slug}")
pub = r.json()
names = [m["name"] for m in pub.get("resources", [])]
check("публичная страница: мастер «только через админа» скрыт", r.status_code == 200 and names == ["Айгуль"], names)
m0 = (pub.get("resources") or [{}])[0]
check("публичная карточка: фото, опыт, работы, цены — без телефона и графика",
      m0.get("photo_url") and m0.get("experience_years") and len(m0.get("portfolio") or []) == 2 and m0.get("service_prices")
      and "phone" not in m0 and "schedule" not in m0 and "team_member_id" not in m0 and "pay_percent" not in m0, sorted(m0))
r = cl.get(f"/api/public/booking/{slug}/slots?resource_id={meerim['id']}&date={tomorrow}")
check("по ссылке к нему не записаться", r.status_code == 200 and r.json()["days"] == [], r.text[:160])
db.expire_all()
try:
    bs.create_booking(db, owner, start=f"{tomorrow}T12:00", resource_id=meerim["id"], client_name="Бот", source="bot")
    check("бот к нему не запишет", False)
except bs.BookingError as e:
    db.rollback()
    check("бот к нему не запишет", "администратор" in str(e), str(e))
r = cl.post("/api/booking/bookings", headers=HO, json={"start": f"{tomorrow}T12:00", "resource_id": meerim["id"], "client_name": "Асан"})
check("администратор записывает к нему из кабинета", r.status_code == 200, r.text[:160])
near = bs.nearest_slots(db, owner, None, None, tomorrow, count=30)
check("бот и голос предлагают только онлайн-мастеров", near and all(s["resource_id"] == aigul["id"] for s in near))
near_svc = bs.nearest_slots(db, owner, db.get(AgentKnowledgeEntry, mani.id), None, tomorrow, count=2)
check("бот видит цену и длительность мастера", near_svc and near_svc[0].get("price") == 1800.0 and near_svc[0].get("duration_min") == 90, near_svc[:1])
db.commit()

# ── вход мастера одной кнопкой
r = cl.post(f"/api/booking/resources/{aigul['id']}/login", headers=HO, json={"email": "aigul@salon.kg"})
j = r.json() if r.status_code == 200 else {}
aigul_pw = j.get("password")
aigul_member = (j.get("member") or {}).get("id")
check("вход мастеру создан и привязан", r.status_code == 200 and aigul_pw and j["resource"]["team_member_id"] == aigul_member
      and (j.get("member") or {}).get("permissions") == ["booking_own"] and j["member"]["role_title"] == "Мастер", r.text[:300])
r = cl.post("/api/auth/login", json={"email": "aigul@salon.kg", "password": aigul_pw})
lj = r.json() if r.status_code == 200 else {}
check("мастер входит и попадает в «Мой день»", r.status_code == 200 and lj.get("redirect_to") == "/static/master.html", r.text[:200])
HM = {"Authorization": f"Bearer {lj.get('token')}"}
r = cl.get("/api/users/me", headers=HM)
me = r.json() if r.status_code == 200 else {}
check("/users/me мастера: меню «Мой день»", [x["id"] for x in (me.get("staff") or {}).get("menu", [])] == ["booking_own"], me.get("staff"))

# второй мастер с входом для проверки изоляции
r = cl.post("/api/team", headers=HO, json={"name": "Мээрим", "email": "meerim@salon.kg", "permissions": ["booking_own"]})
meerim_member = (r.json().get("member") or {}).get("id")
r = cl.put(f"/api/booking/resources/{meerim['id']}/login", headers=HO, json={"team_member_id": meerim_member})
check("привязка существующего сотрудника", r.status_code == 200 and r.json()["resource"]["team_member_id"] == meerim_member, r.text[:200])
m2 = db.query(TeamMember).filter(TeamMember.email == "meerim@salon.kg").first()
HM2 = {"Authorization": f"Bearer {create_jwt_token(owner.id, extra={'staff': str(m2.id), 'stv': m2.token_version})}"}
r = cl.put(f"/api/booking/resources/{meerim['id']}/login", headers=HM2, json={"team_member_id": None})
check("мастер не отвяжет вход сам", r.status_code == 403, r.status_code)

# ── кабинет мастера
r = cl.get(f"/api/booking/me?date={tomorrow}&days=2", headers=HM)
me = r.json() if r.status_code == 200 else {}
check("кабинет: только свои записи", r.status_code == 200 and me.get("master", {}).get("id") == aigul["id"]
      and [b["client_name"] for b in me.get("bookings", [])] == ["Жылдыз"], r.text[:300])
check("кабинет: телефоны клиентов скрыты по умолчанию", me.get("show_phones") is False
      and all(b.get("client_phone") is None for b in me.get("bookings", [])), me.get("bookings"))
check("кабинет: услуги мастера со своей ценой", [(s["title"], s["price"], s["duration_min"]) for s in me.get("services", [])]
      == [("Маникюр с покрытием", 1800.0, 90)], me.get("services"))
check("кабинет: нет оплаты мастера и чужих", "pay_percent" not in me.get("master", {}) and "logins" not in me, sorted(me.get("master", {})))
check("кабинет: лента — новая запись", any(f["kind"] == "new" and f["client_name"] == "Жылдыз" for f in me.get("feed", [])), me.get("feed"))
r = cl.get("/api/booking/overview", headers=HM)
check("мастеру общий журнал закрыт", r.status_code == 403, r.status_code)
r = cl.get(f"/api/booking/me?resource_id={meerim['id']}", headers=HM)
check("чужой кабинет по resource_id не открыть", r.status_code == 200 and r.json()["master"]["id"] == aigul["id"], r.text[:160])
other_booking = db.query(Booking).filter(Booking.resource_id == uuid.UUID(meerim["id"])).first()
r = cl.patch(f"/api/booking/me/bookings/{other_booking.id}", headers=HM, json={"status": "arrived"})
check("чужую запись не отметить", r.status_code == 400 and "другого мастера" in (err(r).get("message") or ""), r.text[:160])
r = cl.patch(f"/api/booking/me/bookings/{b1['id']}", headers=HM, json={"status": "cancelled"})
check("отменить свою запись мастер не может", r.status_code == 403, r.text[:160])
r = cl.patch(f"/api/booking/me/bookings/{b1['id']}", headers=HM, json={"start": f"{tomorrow}T13:00", "note": "оттенок 105"})
bj = r.json().get("booking") or {}
check("перенести свою запись и оставить заметку", r.status_code == 200 and bj.get("note") == "оттенок 105"
      and bj["start"].startswith(f"{tomorrow}T07:00"), r.text[:200])
r = cl.patch(f"/api/booking/me/bookings/{b1['id']}", headers=HM, json={"start": f"{tomorrow}T13:00", "resource_id": meerim["id"]})
db.expire_all()
check("к другому мастеру не перенести", str(db.get(Booking, uuid.UUID(b1["id"])).resource_id) == aigul["id"])
r = cl.get(f"/api/booking/me/slots?service_id={mani.id}&date={tomorrow}", headers=HM)
check("своё свободное время", r.status_code == 200 and all(True for _ in r.json()["days"]), r.text[:120])
r = cl.post("/api/booking/me/bookings", headers=HM, json={"start": f"{tomorrow}T15:00", "service_id": str(mani.id), "client_name": "Нурай"})
nb = r.json().get("booking") or {}
check("мастер записал клиента к себе", r.status_code == 200 and nb.get("duration_min") == 90, r.text[:200])
db.expire_all()
check("запись мастера — к нему, источник staff", str(db.get(Booking, uuid.UUID(nb["id"])).resource_id) == aigul["id"]
      and db.get(Booking, uuid.UUID(nb["id"])).source == "staff")
r = cl.post("/api/booking/me/bookings", headers=HM, json={"start": f"{tomorrow}T15:30", "client_name": "Двойная"})
check("занятое время — отказ", r.status_code == 400, r.text[:120])

# телефоны — по настройке владельца
cl.put("/api/booking/settings", headers=HO, json={"settings": {"master_phones": True}})
r = cl.get(f"/api/booking/me?date={tomorrow}&days=2", headers=HM)
check("владелец разрешил — мастер видит телефоны", any(b.get("client_phone") == "+996700111222" for b in r.json().get("bookings", [])))

# заработок и статистика: визит «пришёл» сегодня
today_local = bs.to_local(bs.now_utc()).date()
past = datetime.combine(today_local, datetime.min.time()).replace(tzinfo=bs.BISHKEK) + timedelta(hours=7)
done = Booking(user_id=owner.id, resource_id=uuid.UUID(aigul["id"]), service_entry_id=mani.id, service_title="Маникюр с покрытием",
               client_name="Жылдыз", client_phone="+996700111222", start_at=past, end_at=past + timedelta(minutes=90),
               status="arrived", price=2000, source="owner")   # без карточки — тот же клиент по телефону
db.add(done)
db.commit()
cl.patch(f"/api/finance/payroll/{aigul['id']}", headers=HO, json={"pay_percent": 40, "pay_fixed": 0})
r = cl.get("/api/booking/me", headers=HM)
e = r.json().get("earnings") or {}
check("заработок сегодня: 40% с 2000 = 800", (e.get("today") or {}).get("salary") == 800 and (e.get("today") or {}).get("visits") == 1
      and e.get("configured") is True, e)
s = r.json().get("stats") or {}
check("статистика за 30 дней", s.get("visits") == 1 and s.get("avg_check") == 2000 and s.get("top_services", [{}])[0].get("title") == "Маникюр с покрытием", s)
r = cl.get("/api/booking/me/clients", headers=HM)
cs = r.json().get("clients") or []
zh = [c for c in cs if c["name"] == "Жылдыз"]
check("клиенты мастера с историей (визит без карточки — к ней же по телефону)", r.status_code == 200 and len(zh) == 1
      and zh[0]["visits"] == 1 and zh[0]["next_visit"] and zh[0]["contact_id"] and len(zh[0]["history"]) == 2
      and all(c["name"] != "Асан" for c in cs), [(c["name"], c["visits"]) for c in cs])
check("клиенты: ближайший визит первым", cs and cs[0].get("next_visit") is not None, [c.get("next_visit") for c in cs])

# профиль и работы мастера
r = cl.patch("/api/booking/me/profile", headers=HM, json={"bio": "Люблю нюд"})
check("мастер правит «о себе»", r.status_code == 200 and r.json()["master"]["bio"] == "Люблю нюд", r.text[:120])
r = cl.post("/api/booking/me/media", headers=HM, files={"file": ("m.jpg", img(), "image/jpeg")})
mine = (r.json().get("media") or {}).get("id")
check("мастер добавил свою работу", r.status_code == 200 and len(r.json()["master"]["portfolio"]) == 3, r.text[:160])
cert_id = res.get("certificates", [{}])[0].get("id")
r = cl.delete(f"/api/booking/me/media/{cert_id}", headers=HM)
check("сертификат мастер не удалит (только работы)", r.status_code == 404, r.status_code)
r = cl.delete(f"/api/booking/me/media/{mine}", headers=HM)
check("мастер удалил свою работу", r.status_code == 200, r.text[:120])

# владелец смотрит «как видит мастер»
r = cl.get(f"/api/booking/me?resource_id={meerim['id']}", headers=HO)
check("владелец открывает кабинет любого мастера", r.status_code == 200 and r.json()["master"]["id"] == meerim["id"]
      and r.json().get("preview") is True, r.text[:160])

# ── бухгалтер
r = cl.post("/api/team", headers=HO, json={"name": "Гульнара", "email": "buh@salon.kg", "role_title": "Бухгалтер",
                                           "permissions": ["finance", "analytics"]})
buh = db.query(TeamMember).filter(TeamMember.email == "buh@salon.kg").first()
HB = {"Authorization": f"Bearer {create_jwt_token(owner.id, extra={'staff': str(buh.id), 'stv': buh.token_version})}"}
r = cl.get("/api/finance/summary?period=30d", headers=HB)
check("бухгалтер видит финансы", r.status_code == 200 and "revenue" in r.text, r.text[:160])
r = cl.post("/api/finance/expenses", headers=HB, json={"category": "rent", "title": "Аренда", "amount": 30000, "repeat": "month"})
check("бухгалтер вносит расход", r.status_code == 200, r.text[:160])
r = cl.get("/api/finance/summary", headers=HM)
check("мастеру финансы закрыты", r.status_code == 403, r.status_code)
r = cl.get("/api/users/me", headers=HB)
check("меню бухгалтера: аналитика, касса (смены) и финансы", [x["id"] for x in (r.json().get("staff") or {}).get("menu", [])] == ["analytics", "cash", "finance"])

# ── удаление: сотрудник → мастер без входа; мастер → фото удаляются
r = cl.delete(f"/api/team/{meerim_member}", headers=HO)
db.expire_all()
check("удалили сотрудника — мастер без входа", r.status_code == 200
      and db.get(BookingResource, uuid.UUID(meerim["id"])).team_member_id is None, r.text[:120])
r = cl.post("/api/booking/resources", headers=HO, json={"name": "Временный"})
tmp = r.json()["resource"]
cl.post(f"/api/booking/resources/{tmp['id']}/media?kind=avatar", headers=HO, files={"file": ("t.jpg", img(), "image/jpeg")})
n_before = db.query(SalonMedia).filter(SalonMedia.resource_id == uuid.UUID(tmp["id"])).count()
r = cl.delete(f"/api/booking/resources/{tmp['id']}", headers=HO)
db.expire_all()
check("удалили мастера без записей — его фото удалены", r.status_code == 200 and r.json().get("deleted") and n_before == 1
      and db.query(SalonMedia).filter(SalonMedia.resource_id == uuid.UUID(tmp["id"])).count() == 0, (n_before, r.text[:120]))

# ── сотрудник без раздела записи и чужой владелец
r = cl.get("/api/booking/me", headers=HX)
check("кабинет без мастеров — понятная причина", r.status_code in (200, 404), r.status_code)

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
