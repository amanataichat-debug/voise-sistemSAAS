"""
Голосовой помощник и AI-чат владельца платят из кошелька, когда кредиты агента кончились (28.11.2026).

Владелец Beauty пополнил кошелёк на 150 сом, а «Поговорить с агентом» отвечал «кредиты закончились»:
помощник смотрел только кредиты. Теперь:
  - доступ — кошелёк > 0 ИЛИ кредитов ≥ 100 (handler_owner_voice.access_error → owner_voice.can_pay);
  - минуты разговора — из кошелька по тарифу Voksy Live Go, кошелёк пуст — кредиты;
  - запросы к агенту (голос и текстовый чат) — кредиты, недостающее — из кошелька (CreditService.charge_owner);
  - precheck_owner пропускает нехватку кредитов, если кошелёк не пуст; пусто везде — прежний отказ.

Часть 1 — без базы. Часть 2 — PostgreSQL при OWNER_WALLET_TEST_DATABASE_URL (база БУДЕТ ОЧИЩЕНА).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
PG = os.environ.get("OWNER_WALLET_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", DATABASE_URL=PG or "postgresql://u:p@localhost/none",
                  PRODUCTION="true", OPENAI_API_KEY="k", JWT_SECRET_KEY="owner-wallet-test-secret-0123456789")

import backend.core.config  # noqa: E402,F401

failures = 0


def check(name, cond, extra=""):
    global failures
    if cond:
        print(f"PASS {name}")
    else:
        failures += 1
        print(f"FAIL {name}  [{extra}]")


from backend.services.credit_service import CreditService, InsufficientCreditsError  # noqa: E402
from backend.services import owner_voice as ov  # noqa: E402
from backend.services.wallet_service import tyiyn_per_minute  # noqa: E402

t = CreditService.credits_to_tyiyn(1900)
check("1900 кредитов (≈$1) → ≈110 сом в кошельке", 9000 < t < 13000, t)
check("кредиты → тыйыны: ноль — ноль, один — хотя бы тыйын", CreditService.credits_to_tyiyn(0) == 0
      and CreditService.credits_to_tyiyn(1) >= 1)
check("минута голосового помощника из кошелька ≈3 сома", 200 < tyiyn_per_minute("gemini_live") < 500,
      tyiyn_per_minute("gemini_live"))

if not PG:
    print("SKIP часть 2: OWNER_WALLET_TEST_DATABASE_URL не задан")
    print(f"ПРОВАЛОВ: {failures}")
    sys.exit(1 if failures else 0)

from sqlalchemy import text  # noqa: E402
from backend.db.session import engine, SessionLocal  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: E402,F401
from backend.models.user import User  # noqa: E402
from backend.models.subscription import SubscriptionPlan  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.wallet_transaction import WalletTransaction  # noqa: E402
from backend.websockets import handler_owner_voice as hov  # noqa: E402

with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
import app as app_module  # noqa: E402
app_module.ensure_plan_catalog()
db = SessionLocal()

plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == "beauty_pro").first()
check("тариф Beauty Pro заведён", plan is not None)
u = User(email="salon-wallet@kg.kg", password_hash="x", email_verified=True, first_name="Айдана",
         subscription_plan_id=plan.id, subscription_end_date=datetime.now(timezone.utc) + timedelta(days=20),
         business_niche="beauty_salon", credits_balance=0, wallet_balance=15000)
db.add(u)
db.flush()
agent = AgentConfig(user_id=u.id, name="Администратор")
db.add(agent)
db.commit()

check("Beauty Pro → режим агента full", u.agent_mode() == "full", u.agent_mode())
check("кредитов 0, в кошельке 150 сом → помощник ДОСТУПЕН", hov.access_error(u, agent) is None,
      hov.access_error(u, agent))
check("разговор при 150 сомах не обрезан до минуты", hov._affordable_seconds(u, 1200) > 600,
      hov._affordable_seconds(u, 1200))

try:
    CreditService.precheck_owner(db, u)
    ok = True
except InsufficientCreditsError:
    ok = False
check("чат владельца: кредитов нет, кошелёк есть — пускаем", ok)

charged = ov.charge_voice_minutes(db, u, agent, 120)
db.refresh(u)
check("2 минуты разговора списаны из кошелька", charged > 0 and u.wallet_balance == 15000 - charged
      and u.credits_balance == 0, (charged, u.wallet_balance))
tx = db.query(WalletTransaction).filter(WalletTransaction.user_id == u.id).order_by(WalletTransaction.created_at.desc()).first()
check("в истории кошелька: голосовой помощник, Voksy Live Go", tx is not None and tx.channel == "owner_voice"
      and tx.engine == "gemini_live" and tx.seconds == 120, tx and (tx.channel, tx.engine, tx.seconds))

before = u.wallet_balance
paid = CreditService.charge_owner(db, u, model_slug="google/gemini-3.6-flash", prompt_tokens=20000,
                                  completion_tokens=2000, ref_type="chat", ref_id=agent.id)
db.refresh(u)
check("запрос к агенту без кредитов — оплачен кошельком", paid > 0 and u.wallet_balance == before - paid
      and u.credits_balance == 0, (paid, before, u.wallet_balance))

u.credits_balance = 5000
db.commit()
before = u.wallet_balance
paid = CreditService.charge_owner(db, u, model_slug="google/gemini-3.6-flash", prompt_tokens=20000,
                                  completion_tokens=2000, ref_type="chat", ref_id=agent.id)
db.refresh(u)
check("кредиты есть — кошелёк не трогаем", paid == 0 and u.wallet_balance == before and u.credits_balance < 5000,
      (paid, u.wallet_balance, u.credits_balance))
charged = ov.charge_voice_minutes(db, u, agent, 60)
db.refresh(u)
check("минуты — из кошелька и при кредитах (кошелёк не пуст)", charged > 0 and u.wallet_balance == before - charged)

u.credits_balance = 0
u.wallet_balance = 0
db.commit()
err = hov.access_error(u, agent)
check("пусто везде — понятный отказ про кошелёк", err and err["code"] == "insufficient_credits"
      and "Кошел" in err["message"], err)
try:
    CreditService.precheck_owner(db, u)
    refused = False
except InsufficientCreditsError:
    refused = True
check("чат владельца: пусто везде — 402 как раньше", refused)

u.wallet_balance = 0
u.credits_balance = 3000
db.commit()
check("кредиты есть, кошелёк пуст — помощник доступен", hov.access_error(u, agent) is None)
charged = ov.charge_voice_minutes(db, u, agent, 60)
db.refresh(u)
check("кошелёк пуст — минуты из кредитов", charged > 0 and u.credits_balance == 3000 - charged and u.wallet_balance == 0,
      (charged, u.credits_balance))

db.close()
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
