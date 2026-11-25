"""
Страница салона (Voksy Beauty, шаг 1.5).

Часть 1 (без сети и базы): чистка полей страницы (Instagram, Telegram, ссылки, телефоны, акции, цвет,
удобства, порядок галереи), часы работы салона из графиков мастеров, <head> для превью ссылки и JSON-LD
(экранирование, «</script>» в данных), адрес витрины по нише, права сотрудников, файлы страницы
(salon.html с местом под <head>, salon.js с визитом по ссылке, конструктор в booking.html, значки есть в
спрайте, старой book.html нет).

Часть 2 (PostgreSQL, SALON_TEST_DATABASE_URL): миграция колонки profile, профиль страницы (сохранение,
понятные ошибки), фото (логотип и обложка заменяются со старым файлом, галерея с пределом, удаление,
порядок и подписи), готовность страницы, публичная выдача (витрина, без email владельца и оплаты
мастеров, «не показывать цены»), /beauty, /p и /b (превью ссылки, JSON-LD, режим, noindex, 404 и
выключенная запись), слоты на 14 дней, визит из нескольких услуг по ссылке (всё или ничего, «только
через администратора» — отказ), запись по ссылке без async и с уведомлением владельцу в фоне, лимит с
одного устройства, QR страницы, права сотрудников.
"""

import io
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("SALON_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="salon-test-secret-0123456789abcdef",
                  KB_VECTOR_ENABLED="0")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import booking_service as bs  # noqa: E402
from backend.services import salon_page as sp  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def raises(fn, *a):
    try:
        fn(*a)
    except sp.PageError as e:
        return str(e)
    return None


# ───────────────────────── часть 1: без базы ─────────────────────────
check("Instagram: ссылка, @имя и имя → имя", sp.instagram_handle("https://www.instagram.com/salon.aidana/?hl=ru") == "salon.aidana"
      and sp.instagram_handle("@salon.aidana") == "salon.aidana" and sp.instagram_handle("") == "")
check("Instagram: мусор — понятная ошибка", "Instagram" in (raises(sp.instagram_handle, "не аккаунт!!") or ""))
check("Telegram: t.me/ и @", sp.telegram_handle("https://t.me/salon_aidana") == "salon_aidana" and sp.telegram_handle("@salon_aidana") == "salon_aidana")
check("Telegram: короткое имя — ошибка", raises(sp.telegram_handle, "@ab") is not None)
check("ссылка: без схемы дописывается https://", sp._url("2gis.kg/bishkek/firm/1", "Карта") == "https://2gis.kg/bishkek/firm/1")
check("ссылка: javascript: и мусор — ошибка", raises(sp._url, "javascript:alert(1)", "Сайт") is not None and raises(sp._url, "просто текст", "Сайт") is not None)
check("телефон → E.164, неверный — ошибка", sp._phone("0700 123 456", "Телефон") == "+996700123456" and raises(sp._phone, "12", "Телефон") is not None)
pr = sp._promos([{"title": "  −20%  на  маникюр ", "text": "a\n\n\n\nb", "until": "2026-12-31"}, {"title": ""}, "x"])
check("акции: пустые и битые отбрасываются, пробелы сжимаются", pr == [{"title": "−20% на маникюр", "text": "a\n\nb", "until": "2026-12-31"}], pr)
check("акции: дата только ГГГГ-ММ-ДД", raises(sp._promos, [{"title": "x", "until": "31.12.2026"}]) is not None)
check("акции: не больше 6", len(sp._promos([{"title": str(i)} for i in range(10)])) == sp.MAX_PROMOS)
g = sp.reorder_gallery([{"id": "a", "caption": ""}, {"id": "b", "caption": "B"}, {"id": "c"}],
                       [{"id": "c", "caption": " зал "}, {"id": "zzz"}, {"id": "a", "caption": "A"}])
check("галерея: порядок и подписи только из своих фото, забытые — в конце",
      [x["id"] for x in g] == ["c", "a", "b"] and g[0]["caption"] == "зал" and g[2]["caption"] == "B", g)


class _R:
    def __init__(self, sched):
        self.schedule = sched


hours = sp.salon_hours([_R({"mon": [["10:00", "14:00"], ["15:00", "19:00"]], "tue": [["09:00", "18:00"]]}),
                        _R({"mon": [["09:30", "20:00"]], "sat": [["11:00", "16:00"]]})])
check("часы салона = раннее начало и поздний конец мастеров", hours[0]["open"] == "09:30" and hours[0]["close"] == "20:00"
      and hours[1]["open"] == "09:00" and hours[6]["open"] is None and hours[5]["close"] == "16:00", hours[:2])
check("часы словами склеивают одинаковые дни", sp.hours_text([{"title": t, "open": "09:00" if i < 5 else None, "close": "19:00"}
                                                            for i, t in enumerate(bs.DAY_TITLES)]) == "пн–пт 09:00–19:00")
view = {"name": 'Салон "Айпери" <b>', "tagline": "Ногти</script><script>alert(1)</script>", "address": "Бишкек", "hours": hours,
        "cover_url": "/media/salon/x", "gallery": [], "staff_word": "Мастера", "phone": "+996700123456",
        "instagram": "salon.aiperi", "accent": "#d6336c"}
head = sp.head_html(view, "https://t.voksyai.online", "/beauty/salon")
check("<head>: название и описание экранированы", '<title>Салон &quot;Айпери&quot; &lt;b&gt; — онлайн-запись</title>' in head
      and "<script>alert" not in head.split('application/ld+json')[0], head[:160])
ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', head, re.S)
check("JSON-LD: «</script>» в данных не закрывает тег, разметка разбирается", ld is not None and "</script>" not in ld.group(1)
      and json.loads(ld.group(1).replace("<\\/", "</"))["@type"] == "BeautySalon")
check("превью: картинка абсолютной ссылкой, адрес канонический", 'og:image" content="https://t.voksyai.online/media/salon/x"' in head
      and 'rel="canonical" href="https://t.voksyai.online/beauty/salon"' in head)


class _P:
    slug = "aidana"


check("адрес витрины: салонам /beauty/, другим нишам /p/", sp.page_path(_P(), "nails") == "/beauty/aidana"
      and sp.page_path(_P(), "dentistry") == "/p/aidana" and sp.staff_word("dentistry") == "Специалисты")
check("карта: своя ссылка главнее, иначе поиск по адресу", sp.map_link({"map_url": "https://2gis.kg/x"}, "a") == "https://2gis.kg/x"
      and "google.com/maps/search" in sp.map_link({}, "Киевская 95") and sp.map_link({}, "") is None)
A = ts.staff_allowed
check("права: страницу салона меняет «Мастера и график»", A(["booking_setup"], "GET", "/api/booking/page") and A(["booking_setup"], "PUT", "/api/booking/page")
      and A(["booking_setup"], "POST", "/api/booking/page/media") and A(["booking_setup"], "DELETE", "/api/booking/page/media/1")
      and not A(["booking"], "PUT", "/api/booking/page") and not A(["booking"], "POST", "/api/booking/page/media"))
STATIC = os.path.join(ROOT, "backend", "static")
sh = open(os.path.join(STATIC, "salon.html"), encoding="utf-8").read()
sj = open(os.path.join(STATIC, "salon", "salon.js"), encoding="utf-8").read()
bh = open(os.path.join(STATIC, "booking.html"), encoding="utf-8").read()
check("salon.html: место под <head> и режим страницы", "<!--SALON_HEAD-->" in sh and 'data-mode="page"' in sh and "/static/salon/salon.js" in sh)
check("salon.js: визит по ссылке, слоты на 14 дней, виджет закрывается", "'/visit-slots'" in sj and "post('/visit'" in sj and "days: first ? '14'" in sj
      and "voksy-booking" in sj)
check("конструктор подключён к журналу, старой book.html нет", "/static/booking/page.js" in bh and not os.path.exists(os.path.join(STATIC, "book.html")))
sprite = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', open(os.path.join(STATIC, "icons", "ui.svg"), encoding="utf-8").read()))
used = set(re.findall(r"icon\('([a-z0-9-]+)'", open(os.path.join(STATIC, "booking", "page.js"), encoding="utf-8").read()))
check("значки конструктора есть в спрайте", used <= sprite, sorted(used - sprite))

if not PG:
    print("SKIP: часть 2 — SALON_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ───────────────────────── часть 2: PostgreSQL ─────────────────────────
from sqlalchemy import text  # noqa: E402
from PIL import Image  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.booking import Booking, BookingPage, SalonMedia  # noqa: E402
from backend.models.team import TeamMember  # noqa: E402
from backend.core.security import create_jwt_token, hash_password  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.api import booking as booking_api  # noqa: E402
import backend.core.background_loop as bgl  # noqa: E402

with engine.begin() as conn:   # старая база: колонки страницы ещё нет
    conn.execute(text("ALTER TABLE booking_pages DROP COLUMN profile"))
for fn in ("ensure_user_profile_columns", "ensure_booking_tables", "ensure_team_tables", "ensure_finance_tables", "ensure_branding_columns"):
    getattr(app_module, fn)()
with engine.connect() as conn:
    pcols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='booking_pages'"))}
check("миграция: booking_pages.profile", "profile" in pcols)

cl = TestClient(app_module.app, raise_server_exceptions=False)
now = datetime.now(timezone.utc)
db = SessionLocal()
submitted = []
bgl.submit = lambda coro: (submitted.append(coro), coro.close())     # уведомление владельцу — без Telegram


def plan(code):
    p = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).first()
    if not p:
        p = SubscriptionPlan(code=code, name=code.title(), price=100, max_assistants=3, description=code, is_active=True)
        db.add(p)
        db.flush()
    return p


owner = User(email="owner-secret@salon.kg", password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
             first_name="Айгерим", company_name="Салон Айгерим", business_niche="beauty_salon",
             business_profile={"modules": {"booking": True}})
owner.subscription_plan_id = plan("start").id
owner.subscription_end_date = now + timedelta(days=20)
clinic = User(email="clinic@dent.kg", password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
              first_name="Доктор", company_name="Дент", business_niche="dentistry", business_profile={"modules": {"booking": True}})
db.add_all([owner, clinic])
db.flush()
db.add(AgentConfig(user_id=owner.id, name="Бот салона"))
db.commit()
HO = {"Authorization": f"Bearer {create_jwt_token(owner.id)}"}
HC = {"Authorization": f"Bearer {create_jwt_token(clinic.id)}"}


def err(r):
    try:
        d = r.json()
    except Exception:
        return ""
    x = d.get("detail") or d.get("message")
    return x if isinstance(x, str) else str(x)


def png(w=800, h=500, color=(214, 51, 108)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


# мастера и услуги
ov = cl.get("/api/booking/overview", headers=HO).json()
aigul = ov["resources"][0]
cl.patch(f"/api/booking/resources/{aigul['id']}", headers=HO, json={"name": "Айгуль", "schedule": {d: [["09:00", "19:00"]] for d in bs.DAYS}})
meerim = cl.post("/api/booking/resources", headers=HO, json={"name": "Мээрим"}).json()["resource"]
cl.patch(f"/api/booking/resources/{meerim['id']}", headers=HO, json={"schedule": {d: [["10:00", "20:00"]] for d in bs.DAYS}})
mani = cl.post("/api/booking/services", headers=HO, json={"title": "Маникюр", "category": "Ногти", "price": 1200, "duration_min": 60,
                                                          "masters": [{"id": aigul["id"]}]}).json()["service"]
cut = cl.post("/api/booking/services", headers=HO, json={"title": "Стрижка", "category": "Волосы", "price": 1000, "duration_min": 45,
                                                         "masters": [{"id": meerim["id"]}]}).json()["service"]
hard = cl.post("/api/booking/services", headers=HO, json={"title": "Сложное окрашивание", "category": "Волосы", "price": 9000,
                                                          "duration_min": 180, "online_booking": False, "masters": [{"id": meerim["id"]}]}).json()["service"]

# ── профиль страницы ──
r = cl.get("/api/booking/page", headers=HO)
d = r.json() if r.status_code == 200 else {}
check("страница: профиль, готовность, QR и код виджета", r.status_code == 200 and 0 < d["readiness"]["percent"] < 100
      and d["page"]["page_url"].startswith("https://t.voksyai.online/beauty/") and str(d.get("qr") or "").startswith("data:image/svg")
      and "booking-widget.js" in (d.get("widget_code") or "") and d["profile"]["accent"] == sp.DEFAULT_ACCENT, d.get("readiness"))
slug = d["page"]["slug"]
r = cl.put("/api/booking/page", headers=HO, json={"title": "Салон «Айгерим»", "tagline": "Ногти и волосы <b>в центре</b>", "about": "Работаем с 2017",
                                                  "address": "Бишкек, Киевская 95", "phone": "0700 123 456", "whatsapp": "+996 555 111 222",
                                                  "instagram": "https://instagram.com/salon.aigerim/", "telegram": "@salon_aigerim",
                                                  "website": "salon.kg", "amenities": ["wifi", "card", "nope"], "accent": "#7C3AED",
                                                  "promos": [{"title": "−20% новым", "until": "2099-01-01"}, {"title": "Прошлая", "until": "2000-01-01"}]})
p = r.json().get("profile", {}) if r.status_code == 200 else {}
check("сохранение: телефоны в E.164, ссылки нормализованы, чужие удобства отброшены", r.status_code == 200 and p["phone"] == "+996700123456"
      and p["whatsapp"] == "+996555111222" and p["instagram"] == "salon.aigerim" and p["website"] == "https://salon.kg"
      and p["amenities"] == ["wifi", "card"] and p["accent"] == "#7c3aed" and p["title"] == "Салон «Айгерим»", p)
r = cl.put("/api/booking/page", headers=HO, json={"instagram": "плохо!!"})
check("неверный Instagram — 400 с понятным текстом", r.status_code == 400 and "Instagram" in err(r), err(r))
r = cl.put("/api/booking/page", headers=HO, json={"accent": "red"})
check("неверный цвет — 400", r.status_code == 400)
db.expire_all()
pg = db.query(BookingPage).filter(BookingPage.user_id == owner.id).first()
check("адрес и название ушли в настройки записи (их берут напоминания и бот)", (pg.settings or {}).get("address") == "Бишкек, Киевская 95"
      and (pg.settings or {}).get("title") == "Салон «Айгерим»")

# ── фото ──
r = cl.post("/api/booking/page/media?kind=cover", headers=HO, files={"file": ("c.png", png(), "image/png")})
cover1 = (r.json().get("profile") or {}).get("cover") if r.status_code == 200 else None
r = cl.post("/api/booking/page/media?kind=cover", headers=HO, files={"file": ("c2.png", png(color=(10, 120, 200)), "image/png")})
cover2 = (r.json().get("profile") or {}).get("cover") if r.status_code == 200 else None
db.expire_all()
check("обложка заменяется, старый файл удаляется", cover1 and cover2 and cover1["id"] != cover2["id"]
      and db.get(SalonMedia, uuid.UUID(cover1["id"])) is None and db.get(SalonMedia, uuid.UUID(cover2["id"])) is not None)
r = cl.post("/api/booking/page/media?kind=logo", headers=HO, files={"file": ("l.png", png(900, 900), "image/png")})
logo = (r.json().get("profile") or {}).get("logo") if r.status_code == 200 else None
lm = db.get(SalonMedia, uuid.UUID(logo["id"])) if logo else None
check("логотип уменьшен до 512 px", lm is not None and lm.kind == "logo" and max(lm.width, lm.height) <= 512, lm and (lm.width, lm.height))
r = cl.post("/api/booking/page/media?kind=banner", headers=HO, files={"file": ("x.png", png(), "image/png")})
check("неизвестный вид фото — 400", r.status_code == 400)
r = cl.post("/api/booking/page/media?kind=gallery", headers=HO, files={"file": ("x.txt", b"not an image", "text/plain")})
check("не картинка — 400 с понятным текстом", r.status_code == 400 and "картинка" in err(r).lower(), err(r))
old_max = sp.MAX_GALLERY
sp.MAX_GALLERY = 3
ids = []
for i in range(3):
    r = cl.post("/api/booking/page/media?kind=gallery", headers=HO, files={"file": (f"g{i}.png", png(color=(i * 60, 80, 120)), "image/png")},
                data={"caption": f"Фото {i}"})
    if r.status_code == 200:
        ids = [g["id"] for g in r.json()["profile"]["gallery"]]
r = cl.post("/api/booking/page/media?kind=gallery", headers=HO, files={"file": ("g9.png", png(), "image/png")})
check("галерея: подписи и предел", len(ids) == 3 and r.status_code == 400 and "не больше 3" in err(r), (len(ids), err(r)))
sp.MAX_GALLERY = old_max
r = cl.put("/api/booking/page", headers=HO, json={"gallery": [{"id": ids[2], "caption": "Зал"}, {"id": ids[0]}]})
gal = r.json()["profile"]["gallery"] if r.status_code == 200 else []
check("галерея: новый порядок и подпись", [g["id"] for g in gal] == [ids[2], ids[0], ids[1]] and gal[0]["caption"] == "Зал", [g["caption"] for g in gal])
r = cl.delete(f"/api/booking/page/media/{ids[1]}", headers=HO)
db.expire_all()
check("удаление фото галереи (и файла)", r.status_code == 200 and len(r.json()["profile"]["gallery"]) == 2 and db.get(SalonMedia, uuid.UUID(ids[1])) is None)
check("чужое/несуществующее фото — 404", cl.delete(f"/api/booking/page/media/{uuid.uuid4()}", headers=HO).status_code == 404)
rd = cl.get("/api/booking/page", headers=HO).json()["readiness"]
check("готовность выросла, осталось фото мастеров и галерея от 3", rd["percent"] >= 70 and {t["key"] for t in rd["todo"]} >= {"master_photos"}, rd)

# ── публично ──
r = cl.get(f"/api/public/booking/{slug}")
pub = r.json() if r.status_code == 200 else {}
s = pub.get("salon") or {}
check("публично: витрина салона", r.status_code == 200 and s.get("name") == "Салон «Айгерим»" and s.get("cover_url") and s.get("logo_url")
      and len(s.get("gallery") or []) == 2 and s.get("instagram") == "salon.aigerim" and s.get("accent") == "#7c3aed"
      and s.get("hours")[0]["open"] == "09:00" and s.get("hours")[0]["close"] == "20:00", {k: s.get(k) for k in ("name", "accent")})
check("публично: только текущие акции", [x["title"] for x in s.get("promos") or []] == ["−20% новым"])
check("публично: без email владельца и оплаты мастеров", "owner-secret@salon.kg" not in r.text and "pay_percent" not in r.text and "pay_fixed" not in r.text)
check("публично: услуги «только через админа» скрыты, есть поле фото", [x["title"] for x in pub["services"]] == ["Маникюр", "Стрижка"]
      and all("photo_url" in x for x in pub["services"]))
cl.put("/api/booking/page", headers=HO, json={"show_prices": False})
pub2 = cl.get(f"/api/public/booking/{slug}").json()
check("«не показывать цены»: цен нет нигде", pub2["salon"]["show_prices"] is False and all(x["price"] is None and not x["price_text"] for x in pub2["services"])
      and all(all(v.get("price") is None for v in (m.get("service_prices") or {}).values()) for m in pub2["resources"]))
cl.put("/api/booking/page", headers=HO, json={"show_prices": True})

r = cl.get(f"/beauty/{slug}")
check("/beauty/: превью ссылки, JSON-LD, версии файлов", r.status_code == 200 and "<title>Салон «Айгерим» — онлайн-запись</title>" in r.text
      and 'property="og:image"' in r.text and '"@type": "BeautySalon"' in r.text and 'data-mode="page"' in r.text
      and re.search(r'/static/salon/salon\.js\?v=h[0-9a-f]{10}', r.text) and "x-frame-options" not in {k.lower() for k in r.headers}
      and "noindex" not in r.text, r.status_code)
check("слоган с разметкой экранирован в превью", "&lt;b&gt;в центре&lt;/b&gt;" in r.text and "<b>в центре</b>" not in r.text)
r = cl.get(f"/b/{slug}?embed=1")
check("/b/: только запись, не индексируется, открывается во фрейме", r.status_code == 200 and 'data-mode="book"' in r.text
      and 'name="robots" content="noindex"' in r.text and "x-frame-options" not in {k.lower() for k in r.headers})
check("/p/ тоже отдаёт страницу", cl.get(f"/p/{slug}").status_code == 200)
check("нет такой страницы — 404 и noindex", (lambda x: x.status_code == 404 and "noindex" in x.text)(cl.get("/beauty/no-such-salon-x")))
check("кривой адрес — на главную", cl.get("/beauty/AB", allow_redirects=False).status_code == 302
      and cl.get("/beauty/x_y.z", allow_redirects=False).status_code == 302)
cl.put("/api/booking/settings", headers=HO, json={"enabled": False})
check("запись выключена — страница 404", cl.get(f"/beauty/{slug}").status_code == 404 and cl.get(f"/api/public/booking/{slug}").status_code == 404)
cl.put("/api/booking/settings", headers=HO, json={"enabled": True})
kit = cl.get("/api/booking/link-kit", headers=HO).json()
check("link-kit: QR и адрес витрины", str(kit.get("qr_page") or "").startswith("data:image/svg") and kit["page"]["page_url"].endswith(f"/beauty/{slug}"))
ck = cl.get("/api/booking/page", headers=HC).json()
check("другая ниша: витрина на /p/", ck["page"]["page_url"].endswith("/p/" + ck["page"]["slug"]))

# ── слоты и визит по ссылке ──
tomorrow = (bs.to_local(bs.now_utc()).date() + timedelta(days=1)).isoformat()
r = cl.get(f"/api/public/booking/{slug}/slots?service_id={mani['id']}&date={tomorrow}&days=14")
days = r.json().get("days") if r.status_code == 200 else []
check("слоты на 14 дней", r.status_code == 200 and 10 <= len(days) <= 14 and days[0]["date"] == tomorrow and days[0]["slots"], len(days or []))
check("больше 14 дней — 422", cl.get(f"/api/public/booking/{slug}/slots?days=30").status_code == 422)
booking_api._PUBLIC_HITS.clear()
items = [{"service_id": mani["id"]}, {"service_id": cut["id"]}]
r = cl.post(f"/api/public/booking/{slug}/visit-slots", json={"items": items, "date": tomorrow})
vs = r.json().get("slots") if r.status_code == 200 else []
check("визит по ссылке: время для всей цепочки", r.status_code == 200 and vs and vs[0]["end_time"], r.text[:200])
r = cl.post(f"/api/public/booking/{slug}/visit", json={"items": items, "start": vs[0]["start"], "name": "Нурай", "phone": "+996 700 999 888"})
v = r.json().get("visit") if r.status_code == 200 else {}
db.expire_all()
rows = db.query(Booking).filter(Booking.user_id == owner.id, Booking.client_name == "Нурай").order_by(Booking.start_at).all()
check("визит записан целиком: две услуги подряд, один визит, источник «онлайн»", len(rows) == 2 and rows[0].visit_id and rows[0].visit_id == rows[1].visit_id
      and rows[1].start_at == rows[0].end_at and all(x.source == "online" for x in rows) and v.get("price") == 2200.0, (len(rows), v))
check("владельцу — уведомление в фоне (обработчик синхронный)", len(submitted) == 1)
r = cl.post(f"/api/public/booking/{slug}/visit-slots", json={"items": [{"service_id": hard["id"]}, {"service_id": cut["id"]}], "date": tomorrow})
check("«только через администратора» в визите по ссылке — отказ", r.status_code == 400 and "администратор" in err(r), err(r))
r = cl.post(f"/api/public/booking/{slug}/visit", json={"items": items, "start": vs[0]["start"], "name": "Второй", "phone": "+996 700 111 000"})
check("то же время второй раз — занято", r.status_code == 400, err(r))
r = cl.post(f"/api/public/booking/{slug}", json={"start": f"{tomorrow}T16:00", "service_id": mani["id"], "name": "Асель", "phone": "+996 700 222 333"})
b = r.json().get("booking") if r.status_code == 200 else {}
check("запись по ссылке: время начала и конца в ответе (для календаря)", r.status_code == 200 and b.get("start") and b.get("end") and len(submitted) == 2, r.text[:160])
codes = [cl.post(f"/api/public/booking/{slug}", json={"start": f"{tomorrow}T17:{i:02d}", "service_id": mani["id"], "name": "Спам",
                                                     "phone": "+996 700 000 0" + str(i).zfill(2)}).status_code for i in range(10)]
check("лимит записей с одного устройства — 429", 429 in codes, codes)

# ── сотрудники ──
m = TeamMember(owner_id=owner.id, email="admin@salon.kg", name="Админ", role_title="Администратор",
               password_hash=hash_password("x12345"), permissions=["booking"], is_active=True, token_version=1)
m2 = TeamMember(owner_id=owner.id, email="setup@salon.kg", name="Управляющий", role_title="Управляющий",
                password_hash=hash_password("x12345"), permissions=["booking", "booking_setup"], is_active=True, token_version=1)
db.add_all([m, m2])
db.commit()
H1 = {"Authorization": f"Bearer {create_jwt_token(owner.id, extra={'staff': str(m.id), 'stv': 1})}"}
H2 = {"Authorization": f"Bearer {create_jwt_token(owner.id, extra={'staff': str(m2.id), 'stv': 1})}"}
check("администратор журнала страницу не меняет", cl.put("/api/booking/page", headers=H1, json={"tagline": "x"}).status_code == 403
      and cl.get("/api/booking/page", headers=H1).status_code == 403)
r = cl.put("/api/booking/page", headers=H2, json={"tagline": "От управляющего"})
check("управляющий («Мастера и график») меняет страницу", r.status_code == 200 and r.json()["profile"]["tagline"] == "От управляющего")

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
