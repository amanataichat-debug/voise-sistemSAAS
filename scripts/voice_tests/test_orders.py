"""Заказы магазинов и доставки (ветка 1811-niche-admin-speed).

Часть 1 (без базы): суммы и доставка, следующий шаг заказа, текст клиенту о статусе (регистр названий
не ломается), правила сотрудника для заказов.

Часть 2 (PostgreSQL, ORDERS_TEST_DATABASE_URL или AUTH_TEST_DATABASE_URL): номер по порядку с 1001; позиции из
каталога с размером/цветом; остаток резервируется при оформлении и возвращается при отмене (и снова
резервируется при возврате в работу); бот — строго по каталогу и наличию, без адреса доставки — отказ;
стадии CRM (новый → в работе → успех); API (список со счётчиками, оформление, правка состава, статус,
настройки — только владелец); сотрудник с разделом «Заказы»; инструменты бота (create/list/update,
в переписке — только текущий клиент, статус бот не меняет); аналитика (блок заказов, KPI); голосовой
помощник (get_orders, сводка).
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PG = os.environ.get("ORDERS_TEST_DATABASE_URL") or os.environ.get("AUTH_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="orders-test-secret-0123456789abcdef")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import backend.core.config  # noqa: E402,F401
from backend.services import order_service as osvc  # noqa: E402
from backend.services import team_service as ts  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ───────────────────────── часть 1 ─────────────────────────
items = [{"title": "Шуба", "price": 45000, "qty": 1}, {"title": "Шапка", "price": 2500, "qty": 2}]
cfg = dict(osvc.DEFAULT_SETTINGS, delivery_fee=300, free_delivery_from=50000)
check("сумма + доставка", osvc.totals(items, "delivery", cfg) == (50000.0, 0.0, 50000.0), osvc.totals(items, "delivery", cfg))
check("доставка до порога бесплатной", osvc.totals(items[:1], "delivery", cfg) == (45000.0, 300.0, 45300.0))
check("самовывоз без доставки", osvc.totals(items, "pickup", cfg)[1] == 0)
check("скидка вычитается", osvc.totals(items, "pickup", cfg, discount=1000)[2] == 49000.0)


class _O:
    def __init__(self, **kw):
        self.__dict__.update(dict(number=1024, client_name="Асель Токтогулова", status="new", delivery_type="delivery",
                                  items=[{"title": "Шуба норковая", "size": "L", "color": "чёрный", "qty": 1, "price": 45000}],
                                  total=45300, delivery_fee=300, address="ул. Киевская 95", delivery_when="сегодня после 18:00",
                                  paid=False), **kw)


o = _O()
check("следующий шаг доставки: новый → подтверждён", osvc.next_status(o) == "confirmed")
o.status = "packed"
check("доставка: собран → в пути", osvc.next_status(o) == "delivering")
o.delivery_type = "pickup"
check("самовывоз: собран → выдан (без «в пути»)", osvc.next_status(o) == "done")
o.status = "done"
check("выдан — дальше шагов нет", osvc.next_status(o) is None)
msg = osvc.client_message(_O(), "confirmed", dict(osvc.DEFAULT_SETTINGS, payment_note="Mbank +996 700 000 000"))
check("клиенту: «принят», номер, состав с регистром, сумма, адрес, оплата",
      msg.startswith("Асель, ваш заказ № 1024 принят: Шуба норковая (L, чёрный)") and "45 300 сом" in msg
      and "ул. Киевская 95" in msg and "Mbank" in msg, msg)
msg = osvc.client_message(_O(client_name=None, delivery_type="pickup"), "packed", dict(osvc.DEFAULT_SETTINGS, pickup_address="ТЦ Ала-Тоо"))
check("клиенту: самовывоз готов, с адресом", msg == "Заказ № 1024 готов, можно забирать: ТЦ Ала-Тоо.", msg)
msg = osvc.client_message(_O(), "delivering", osvc.DEFAULT_SETTINGS)
check("клиенту: передан курьеру, ждать к сроку", "передан курьеру, ждите сегодня после 18:00" in msg, msg)
check("о выдаче и отмене клиенту не пишем автоматически", osvc.client_message(_O(), "done", {}) == ""
      and osvc.client_message(_O(), "cancelled", {}) == "")
A = ts.staff_allowed
check("сотрудник «Заказы»: список, оформление, статус, правка", all(A(["orders"], m, p) for m, p in [
    ("GET", "/api/orders"), ("POST", "/api/orders"), ("GET", "/api/orders/products"), ("GET", "/api/orders/x"),
    ("PATCH", "/api/orders/x"), ("POST", "/api/orders/x/status"), ("GET", "/api/agent/contacts")]))
check("без раздела «Заказы» — закрыто", not A(["inbox", "crm"], "GET", "/api/orders"))
check("настройки заказов сотруднику не открыты на запись", not A(["orders"], "PUT", "/api/orders/settings"))

if not PG:
    print("SKIP: часть 2 — ORDERS_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

# ───────────────────────── часть 2 ─────────────────────────
from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.agent_contact import AgentContact  # noqa: E402
from backend.models.agent_knowledge import AgentKnowledgeEntry  # noqa: E402
from backend.models.order import Order  # noqa: E402
from backend.core.security import create_jwt_token, hash_password  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)

import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app_module.ensure_user_profile_columns()
app_module.ensure_team_tables()
app_module.ensure_order_tables()
app_module.ensure_order_tables()
cl = TestClient(app_module.app, raise_server_exceptions=False)
now = datetime.now(timezone.utc)
db = SessionLocal()

plan = SubscriptionPlan(code="start", name="Start", price=7500, max_assistants=5, description="s", is_active=True)
db.add(plan)
db.flush()
owner = User(email="shop@norka.kg", password_hash=hash_password("Passw0rd!"), email_verified=True, is_active=True,
             first_name="Айгуль", company_name="Норка KG", business_niche="clothing", wallet_balance=100000,
             subscription_plan_id=plan.id, subscription_end_date=now + timedelta(days=20))
db.add(owner)
db.flush()
agent = AgentConfig(user_id=owner.id, name="Бот Норки")
db.add(agent)
db.flush()
contact = AgentContact(user_id=owner.id, agent_config_id=agent.id, name="Жылдыз", phone="+996700111222", status="new")
db.add(contact)
shuba = AgentKnowledgeEntry(user_id=owner.id, agent_config_id=agent.id, kind="product", title="Шуба норковая", body="",
                            price=45000, currency="KGS", in_stock=True,
                            variants=[{"size": "M", "color": "чёрный", "qty": 2}, {"size": "L", "color": "чёрный", "qty": 1}])
from backend.services import catalog_service  # noqa: E402
catalog_service.apply_variants(shuba)
hat = AgentKnowledgeEntry(user_id=owner.id, agent_config_id=agent.id, kind="product", title="Шапка-кубанка", body="",
                          price=2500, currency="KGS", in_stock=True, stock_qty=5)
scarf = AgentKnowledgeEntry(user_id=owner.id, agent_config_id=agent.id, kind="product", title="Платок", body="",
                            price=900, currency="KGS", in_stock=False, stock_qty=0)
db.add_all([shuba, hat, scarf])
db.commit()
HO = {"Authorization": f"Bearer {create_jwt_token(owner.id)}"}


def reload(row):
    db.expire_all()
    return db.query(AgentKnowledgeEntry).filter(AgentKnowledgeEntry.id == row.id).first()


def err(r):
    try:
        d = r.json()
    except Exception:
        return ""
    x = d.get("detail") or d.get("message")
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)


# ── владелец оформляет заказ
r = cl.post("/api/orders", headers=HO, json={"contact_id": str(contact.id), "delivery_type": "delivery", "address": "мкр Джал 23",
                                             "delivery_when": "сегодня после 18", "payment_method": "cash",
                                             "items": [{"entry_id": str(shuba.id), "size": "L", "color": "чёрный", "qty": 1},
                                                       {"title": "кубанка", "qty": 2},
                                                       {"title": "Подарочная упаковка", "price": 300, "qty": 1}]})
j = r.json() if r.status_code == 200 else {}
o1 = j.get("order") or {}
check("заказ оформлен с номером 1001", r.status_code == 200 and o1.get("number") == 1001 and o1.get("label") == "№ 1001", r.text[:300])
check("позиции: каталог по id и по названию, своя строка", [i["title"] for i in o1.get("items", [])]
      == ["Шуба норковая", "Шапка-кубанка", "Подарочная упаковка"], o1.get("items"))
check("сумма 45 000 + 2×2 500 + 300 = 50 300", o1.get("total") == 50300 and o1.get("subtotal") == 50300, (o1.get("subtotal"), o1.get("total")))
check("следующий шаг — подтвердить", o1.get("next_status") == "confirmed")
sh, ht = reload(shuba), reload(hat)
l_left = [v for v in sh.variants if v["size"] == "L"][0]["qty"]
check("остаток зарезервирован: L чёрный 1 → 0, кубанок 5 → 3", l_left == 0 and ht.stock_qty == 3 and sh.stock_qty == 2,
      (sh.variants, ht.stock_qty))
c = db.query(AgentContact).filter(AgentContact.id == contact.id).first()
check("клиент: «Новый» → «В работе»", c.status == "active", c.status)
r = cl.post("/api/orders", headers=HO, json={"client_name": "Новая клиентка", "client_phone": "0555 123 456",
                                             "delivery_type": "pickup", "items": [{"entry_id": str(hat.id), "qty": 1}]})
o2 = r.json().get("order") if r.status_code == 200 else {}
check("второй заказ — № 1002, новый клиент заведён в CRM", r.status_code == 200 and o2.get("number") == 1002
      and o2.get("contact_id") and o2.get("client_phone") == "+996555123456", r.text[:200])
check("самовывоз без доставки", o2.get("delivery_fee") == 0 and o2.get("next_status") == "confirmed")

# ── статусы: подтвердить → собран → в пути → выдан; отмена возвращает остаток
r = cl.post(f"/api/orders/{o1['id']}/status", headers=HO, json={"status": "confirmed"})
check("подтверждён, клиенту сообщение", r.status_code == 200 and r.json()["order"]["status"] == "confirmed"
      and r.json().get("client_notified") is True, r.text[:200])
r = cl.post(f"/api/orders/{o1['id']}/status", headers=HO, json={"status": "bogus"})
check("неизвестный статус — 400", r.status_code == 400, r.status_code)
for stt in ("packed", "delivering", "done"):
    r = cl.post(f"/api/orders/{o1['id']}/status", headers=HO, json={"status": stt})
check("заказ выдан", r.status_code == 200 and r.json()["order"]["status"] == "done", r.text[:200])
c = db.query(AgentContact).filter(AgentContact.id == contact.id).first()
db.refresh(c)
check("клиент после выдачи — «Успех»", c.status == "success", c.status)
r = cl.patch(f"/api/orders/{o1['id']}", headers=HO, json={"items": [{"title": "Шуба норковая", "size": "M", "color": "чёрный"}]})
check("выданный заказ не переписывают", r.status_code == 400, r.status_code)

r = cl.post(f"/api/orders/{o2['id']}/status", headers=HO, json={"status": "cancelled", "reason": "передумала"})
check("отмена заказа", r.status_code == 200 and r.json()["order"]["status"] == "cancelled"
      and r.json()["order"]["cancel_reason"] == "передумала", r.text[:200])
check("отмена вернула кубанку на склад: 2 → 3", reload(hat).stock_qty == 3, reload(hat).stock_qty)
r = cl.post(f"/api/orders/{o2['id']}/status", headers=HO, json={"status": "new"})
check("вернуть в работу — снова резерв: 3 → 2", r.status_code == 200 and reload(hat).stock_qty == 2, reload(hat).stock_qty)

# ── правка состава пересчитывает склад и сумму
r = cl.patch(f"/api/orders/{o2['id']}", headers=HO, json={"items": [{"entry_id": str(hat.id), "qty": 3}], "paid": True,
                                                           "payment_method": "qr"})
j = r.json().get("order") if r.status_code == 200 else {}
check("правка состава: 3 кубанки, сумма 7 500, оплачен", r.status_code == 200 and j.get("total") == 7500 and j.get("paid") is True
      and j.get("payment_method") == "qr", r.text[:200])
check("склад после правки: 2 + 1 − 3 = 0, кубанок больше нет", reload(hat).stock_qty == 0 and reload(hat).in_stock is False,
      reload(hat).stock_qty)

# ── список, поиск, счётчики, товары для заказа
r = cl.get("/api/orders?q=1002", headers=HO)
check("поиск по номеру", r.status_code == 200 and [o["number"] for o in r.json()["orders"]] == [1002], r.text[:200])
r = cl.get("/api/orders?q=кубанк", headers=HO)
check("поиск по товару", r.status_code == 200 and len(r.json()["orders"]) == 2)
r = cl.get("/api/orders?q=555 123", headers=HO)
check("поиск по телефону", r.status_code == 200 and [o["number"] for o in r.json()["orders"]] == [1002], r.text[:200])
r = cl.get("/api/orders", headers=HO)
j = r.json()
check("счётчики: новый 1, выдано сегодня 1, сегодня 2", j["counts"]["new"] == 1 and j["counts"]["done_today"] == 1
      and j["counts"]["today"] == 2, j["counts"])
check("заказы включены для магазина одежды", j.get("enabled") is True and j["titles"]["packed"] == "Собран")
r = cl.get("/api/orders/products?q=шуба", headers=HO)
p = (r.json().get("products") or [{}])[0] if r.status_code == 200 else {}
check("товар для заказа: размеры и остаток по вариантам", p.get("title") == "Шуба норковая" and p.get("sizes") == ["M"]
      and {(v["size"], v["qty"]) for v in p.get("variants", [])} == {("M", 2), ("L", 0)}, p)
r = cl.get("/api/orders/summary?period=today", headers=HO)
s = r.json().get("stats") if r.status_code == 200 else {}
check("сводка дня: 2 заказа, выручка 57 800, топ — кубанка", s.get("orders") == 2 and s.get("revenue") == 57800
      and s["top"][0]["title"] == "Шапка-кубанка", s)

# ── настройки
r = cl.put("/api/orders/settings", headers=HO, json={"delivery_fee": 250, "free_delivery_from": 20000, "pickup_address": "ЦУМ, 3 этаж"})
check("настройки сохранены", r.status_code == 200 and r.json()["delivery_fee"] == 250 and r.json()["pickup_address"] == "ЦУМ, 3 этаж")
r = cl.post("/api/orders", headers=HO, json={"client_name": "Курьерская", "delivery_type": "delivery", "address": "Ахунбаева 1",
                                             "items": [{"entry_id": str(hat.id), "qty": 1}]})
check("доставка по настройке: 2 500 + 250", r.status_code == 200 and r.json()["order"]["total"] == 2750, r.text[:200])

# ── бот: строго по каталогу и наличию
from backend.services import order_tools  # noqa: E402


def tool(name, args, surface="postcall", cid=None):
    ctx = {"user_id": str(owner.id), "surface": surface, "current_contact_id": str(cid or contact.id),
           "call_direction": "whatsapp_inbound", "agent_config_id": str(agent.id)}
    return json.loads(asyncio.get_event_loop().run_until_complete(order_tools.execute(name, args, ctx, db)))


asyncio.set_event_loop(asyncio.new_event_loop())
res = tool("create_order", {"items": [{"product": "Платок"}], "delivery_type": "pickup"})
check("бот: нет в наличии — отказ", res.get("ok") is False and "нет в наличии" in res.get("error", ""), res)
res = tool("create_order", {"items": [{"product": "шуба норковая"}], "delivery_type": "pickup"})
check("бот: без размера — спросить размер", res.get("ok") is False and "размер" in res.get("error", ""), res)
res = tool("create_order", {"items": [{"product": "шуба норковая", "size": "L"}], "delivery_type": "pickup"})
check("бот: раскупленный размер — отказ", res.get("ok") is False and "нет в наличии" in res.get("error", ""), res)
res = tool("create_order", {"items": [{"product": "Сумка Gucci"}], "delivery_type": "pickup"})
check("бот: чего нет в каталоге — не выдумывает", res.get("ok") is False and "каталоге" in res.get("error", ""), res)
res = tool("create_order", {"items": [{"product": "шуба норковая", "size": "M"}], "delivery_type": "delivery"})
check("бот: доставка без адреса — спросить адрес", res.get("ok") is False and "адрес" in res.get("error", ""), res)
res = tool("create_order", {"items": [{"product": "шуба норковая", "size": "M", "qty": 1}], "delivery_type": "delivery",
                            "address": "Кок-Жар 5", "delivery_when": "завтра утром", "agent_contact_id": str(uuid.uuid4())})
o3 = res.get("order") or {}
check("бот оформил заказ и назвал номер и сумму", res.get("ok") is True and o3.get("number") == 1004
      and "№ 1004" in res.get("say", "") and "45 000 сом" in res.get("say", ""), res)   # от 20 000 доставка бесплатно
row = db.query(Order).filter(Order.number == 1004).first()
check("заказ бота — на текущего клиента, источник bot, канал whatsapp", str(row.contact_id) == str(contact.id)
      and row.source == "bot" and row.channel == "whatsapp", (row.contact_id, row.source, row.channel))
res = tool("list_orders", {})
check("бот: «где мой заказ?» — заказы текущего клиента", res.get("ok") and {x["number"] for x in res["orders"]} == {1001, 1004}, res)
res = tool("update_order", {"address": "Кок-Жар 7, кв 12"})
check("бот меняет адрес последнего открытого заказа", res.get("ok") and res["order"]["address"] == "Кок-Жар 7, кв 12", res)
res = tool("update_order", {"order_number": "1002", "cancel": True})
check("бот не трогает заказ другого клиента", res.get("ok") is False and "другого клиента" in res.get("error", ""), res)
res = tool("update_order", {"status": "done"})
check("бот не меняет статус", res.get("ok") is False and "магазин" in res.get("error", ""), res)
res = tool("update_order", {"cancel": True, "reason": "передумала"})
check("клиент отменил через бота — склад вернулся", res.get("ok") and res["order"]["status"] == "cancelled"
      and [v for v in reload(shuba).variants if v["size"] == "M"][0]["qty"] == 2, res)
res = tool("update_order", {"order_number": "1003", "status": "confirmed"}, surface="owner_chat")
check("владелец в чате меняет статус заказа", res.get("ok") and res["order"]["status"] == "confirmed", res)

# ── набор инструментов агента: заказы есть у магазина, нет у салона
from backend.services.agent_tools import build_chat_tools  # noqa: E402
names = {t["function"]["name"] for t in asyncio.get_event_loop().run_until_complete(build_chat_tools(agent, db))}
check("магазину — инструменты заказов в чате владельца (Старт)", {"create_order", "update_order", "list_orders"} <= names,
      sorted(n for n in names if "order" in n))
from backend.services.agent_prompts import build_business_block  # noqa: E402
check("в промпте бота — блок «Заказы»", "## Заказы" in build_business_block(agent, db))

# ── сотрудник с разделом «Заказы»
r = cl.post("/api/team", headers=HO, json={"name": "Курьер Бек", "email": "bek.courier@gmail.com", "password": "courier7",
                                           "permissions": ["orders"]})
check("сотрудник-курьер добавлен", r.status_code == 200, r.text[:200])
HS = {"Authorization": f"Bearer {cl.post('/api/auth/login', json={'email': 'bek.courier@gmail.com', 'password': 'courier7'}).json().get('token')}"}
r = cl.get("/api/orders", headers=HS)
check("курьер видит заказы", r.status_code == 200 and r.json()["total"] >= 3, r.status_code)
r = cl.post(f"/api/orders/{o2['id']}/status", headers=HS, json={"status": "confirmed"})
check("курьер меняет статус", r.status_code == 200, r.text[:160])
r = cl.put("/api/orders/settings", headers=HS, json={"delivery_fee": 0})
check("курьеру настройки заказов закрыты", r.status_code == 403, r.status_code)
r = cl.get("/api/agent/knowledge", headers=HS)
check("курьеру каталог не открыт", r.status_code == 403, r.status_code)
r = cl.post("/api/orders", headers=HS, json={"client_name": "От курьера", "delivery_type": "pickup",
                                             "items": [{"title": "Ремонт подкладки", "price": 500}]})
check("заказ сотрудника подписан его именем", r.status_code == 200 and r.json()["order"]["source"] == "staff"
      and r.json()["order"]["created_by"] == "Курьер Бек", r.text[:200])
me = cl.get("/api/users/me", headers=HS).json()
check("меню курьера — «Заказы»", [m["id"] for m in me["staff"]["menu"]] == ["orders"], me.get("staff"))

# ── аналитика и голос
from backend.services import analytics_service as an  # noqa: E402
rep = an.build_report(db, owner, period="7d")
od = rep.get("orders") or {}
check("аналитика: блок заказов (без отменённых)", od.get("total") == 4 and od.get("cancelled") == 1 and od.get("by_bot") == 0
      and od.get("done") == 1, {k: od.get(k) for k in ("total", "cancelled", "by_bot", "done", "revenue")})
check("аналитика: KPI «Заказы» и «Выручка по заказам»", {"orders", "order_revenue"} <= {k["id"] for k in rep["kpis"]},
      [k["id"] for k in rep["kpis"]])
check("аналитика: ряд заказов на графике", sum(rep["series"]["orders"]) == 4 and "orders" in rep["prev"])
from backend.services import owner_voice as ov  # noqa: E402
res = ov.fast_get_orders(db, owner, agent, {"period": "today"})
check("голос: заказы за сегодня с выручкой и списком", res.get("ok") and res["stats"]["orders"] == 4
      and res["card"]["items"] and "Заказы · сегодня" in res["card"]["title"], res.get("summary"))
brief = ov._brief_orders(db, owner, now)
check("голос: сводка заказов в промпте (отменённые не считаются)", "Заказов сегодня: 4" in brief, brief[:200])

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
