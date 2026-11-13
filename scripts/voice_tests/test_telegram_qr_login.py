"""Вход в личный Telegram по QR-коду (13.11.2026: код из приложения у части владельцев «не проходит»).

Telegram подменён фальшивым клиентом (без сети):
  часть 1 — telegram_user_service.qr_login: скан → вход, другой DC (MigrateTo), облачный пароль,
            QR обновляется по сроку, отмена, «QR устарел»; картинка QR (segno);
  часть 2 — API /api/agent/telegram-account/qr/start на PostgreSQL (TG_QR_TEST_DATABASE_URL
            или CATALOG_TEST_DATABASE_URL): pending_qr + картинка, подключение, пароль,
            новая попытка гасит старую, вход по номеру гасит QR.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from cryptography.fernet import Fernet  # noqa: E402

PG = os.environ.get("TG_QR_TEST_DATABASE_URL") or os.environ.get("CATALOG_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://t.voksyai.online", OPENAI_API_KEY="k", PRODUCTION="true",
                  JWT_SECRET_KEY="tgqr-test-secret-0123456789", TELEGRAM_API_ID="12345",
                  TELEGRAM_API_HASH="hash", TELEGRAM_SESSION_KEY=Fernet.generate_key().decode())
os.environ["DATABASE_URL"] = PG or "postgresql://u:p@localhost:1/none"
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: E402,F401
from telethon import errors, types  # noqa: E402
from backend.services import telegram_user_service as tg  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


class FakeSession:
    def save(self):
        return "SESSION-OK"


class FakeClient:
    """Сценарий: список ответов на ExportLoginToken/ImportLoginToken; scan_after — через сколько с «сканируют»."""

    def __init__(self, script, scan_after=None, token_ttl=30):
        self.script = list(script)
        self.scan_after = scan_after
        self.ttl = token_ttl
        self.handlers = []
        self.session = FakeSession()
        self.switched = None
        self.logged_in = None
        self.calls = []
        self.disconnected = False

    async def connect(self):
        if self.scan_after is not None:
            asyncio.get_running_loop().call_later(self.scan_after, self._fire)

    def _fire(self):
        for h, _ev in list(self.handlers):
            asyncio.ensure_future(h(types.UpdateLoginToken()))

    def add_event_handler(self, cb, ev):
        self.handlers.append((cb, ev))

    def remove_event_handler(self, cb):
        self.handlers = [x for x in self.handlers if x[0] is not cb]

    async def __call__(self, req):
        self.calls.append(type(req).__name__)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if item == "token":
            return types.auth.LoginToken(expires=datetime.now(timezone.utc) + timedelta(seconds=self.ttl), token=os.urandom(32))
        return item

    async def _switch_dc(self, dc):
        self.switched = dc

    async def _on_login(self, user):
        self.logged_in = user

    async def disconnect(self):
        self.disconnected = True


USER = types.User(id=777, username="norka_kg", first_name="Айгерим")
SUCCESS = types.auth.LoginTokenSuccess(authorization=types.auth.Authorization(user=USER))


def run(client, stop_after=None, max_seconds=60):
    tg._new_client = lambda session_str="": client
    qrs = []
    t0 = asyncio.get_event_loop_policy().new_event_loop()

    async def go():
        start = asyncio.get_running_loop().time()

        async def on_qr(url, exp):
            qrs.append(url)

        async def should_stop():
            return stop_after is not None and asyncio.get_running_loop().time() - start >= stop_after
        return await tg.qr_login(on_qr, should_stop, max_seconds=max_seconds)
    try:
        return t0.run_until_complete(go()), qrs
    finally:
        t0.close()


tg.QR_STOP_CHECK_SECONDS = 0.05

# ── Часть 1 ───────────────────────────────────────────────────────────────
c = FakeClient(["token", SUCCESS], scan_after=0.2)
res, qrs = run(c)
check("скан → вход: ok, сессия, кто вошёл", res.get("ok") and not res.get("needs_password") and res.get("session") == "SESSION-OK"
      and res.get("tg_username") == "norka_kg" and res.get("tg_user_id") == 777, res)
check("QR — ссылка tg://login?token=", len(qrs) == 1 and qrs[0].startswith("tg://login?token=") and "=" not in qrs[0].split("token=")[1], qrs)
check("после входа соединение закрыто и обработчик снят", c.disconnected and not c.handlers)
check("Telethon узнал о входе (_on_login)", c.logged_in is USER)

c = FakeClient(["token", types.auth.LoginTokenMigrateTo(dc_id=4, token=b"t" * 32), SUCCESS], scan_after=0.2)
res, _ = run(c)
check("аккаунт на другом сервере Telegram: переключение DC и вход", res.get("ok") and c.switched == 4 and c.calls[-1] == "ImportLoginTokenRequest", c.calls)

c = FakeClient(["token", errors.SessionPasswordNeededError(None)], scan_after=0.2)
res, _ = run(c)
check("облачный пароль: needs_password + сессия для шага пароля", res.get("ok") and res.get("needs_password") and res.get("session") == "SESSION-OK", res)

c = FakeClient(["token", "token", SUCCESS], scan_after=0.9, token_ttl=2.5)
res, qrs = run(c)
check("QR истёк до скана → новый QR, потом вход", res.get("ok") and len(qrs) == 2 and qrs[0] != qrs[1], (len(qrs), res))

c = FakeClient(["token"] * 5)
res, _ = run(c, stop_after=0.2)
check("владелец начал заново → попытка выходит (cancelled)", res == {"ok": False, "error": "cancelled"}, res)

c = FakeClient(["token"] * 5)
res, _ = run(c, max_seconds=0.3)
check("никто не отсканировал → qr_expired", res == {"ok": False, "error": "qr_expired"}, res)

c = FakeClient([errors.FloodWaitError(None, capture=30)])
res, _ = run(c)
check("ошибка Telegram → понятный код", res.get("ok") is False and str(res.get("error", "")).startswith("flood_wait"), res)

uri = tg.qr_svg_data_uri("tg://login?token=abcDEF123")
check("картинка QR — SVG data URI", uri.startswith("data:image/svg+xml;base64,") and len(uri) > 500)

# ── Часть 2: API ──────────────────────────────────────────────────────────
if not PG:
    print("SKIP часть 2: TG_QR_TEST_DATABASE_URL не задан")
else:
    from sqlalchemy import text
    from backend.db.session import engine, SessionLocal
    from backend.models.base import Base
    import backend.models  # noqa: F401
    from backend.models.user import User
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_telegram_account import AgentTelegramAccount
    from backend.core.security import create_jwt_token
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    db = SessionLocal()
    u = User(email="qr@x.kg", password_hash="x", email_verified=True, is_active=True)
    db.add(u); db.flush()
    ag = AgentConfig(user_id=u.id, name="Бот"); db.add(ag); db.commit()
    tok = create_jwt_token(u.id)
    db.close()

    from fastapi.testclient import TestClient
    from app import app
    from backend.api import agent_telegram_account as api

    async def no_baseline(db, row, session):
        return None
    api._baseline_dialogs = no_baseline
    H = {"Authorization": "Bearer " + tok}

    def scripted(outcome, delay=0.6):
        async def fake(on_qr, should_stop, max_seconds=180):
            await on_qr("tg://login?token=AAAA" + os.urandom(4).hex(), datetime.now(timezone.utc) + timedelta(seconds=30))
            loop = asyncio.get_running_loop(); end = loop.time() + delay
            while loop.time() < end:
                if await should_stop():
                    return {"ok": False, "error": "cancelled"}
                await asyncio.sleep(0.05)
            return outcome
        return fake

    def row():
        s = SessionLocal()
        try:
            return s.query(AgentTelegramAccount).first()
        finally:
            s.close()

    def wait_status(cl, want, secs=4.0):
        import time
        end = time.time() + secs
        last = None
        while time.time() < end:
            last = cl.get("/api/agent/telegram-account", headers=H).json()
            if last.get("status") == want:
                return last
            time.sleep(0.1)
        return last

    with TestClient(app) as cl:
        tg.qr_login = scripted({"ok": True, "needs_password": False, "session": "S1", "tg_user_id": 9, "tg_username": "shop", "tg_first_name": "Шоп"})
        r = cl.post("/api/agent/telegram-account/qr/start", headers=H)
        j = r.json()
        check("qr/start: pending_qr и картинка сразу", r.status_code == 200 and j.get("status") == "pending_qr" and str(j.get("qr", "")).startswith("data:image/svg+xml"), (r.status_code, j.get("status")))
        check("qr/start: ссылка для телефона (открыть в Telegram)", str(j.get("qr_link", "")).startswith("tg://login?token="))
        j = wait_status(cl, "connected")
        check("скан → connected, кто подключён", j.get("status") == "connected" and j.get("tg_username") == "shop", j.get("status"))
        rw = row()
        check("сессия зашифрована и расшифровывается", rw.session_encrypted and tg.decrypt_session(rw.session_encrypted) == "S1" and rw.qr_url is None)
        r = cl.post("/api/agent/telegram-account/qr/start", headers=H)
        check("уже подключён → 400 already_connected", r.status_code == 400 and "already_connected" in r.text)
        cl.delete("/api/agent/telegram-account", headers=H)

        tg.qr_login = scripted({"ok": True, "needs_password": True, "session": "S2"})
        cl.post("/api/agent/telegram-account/qr/start", headers=H)
        j = wait_status(cl, "pending_password")
        check("аккаунт с облачным паролем → pending_password", j.get("status") == "pending_password")
        tg.confirm_password = lambda s, p: asyncio.sleep(0, {"ok": True, "session": s + "+pw", "tg_user_id": 9, "tg_username": "shop2"})
        r = cl.post("/api/agent/telegram-account/verify-password", headers=H, json={"password": "secret"})
        check("после пароля → connected (та же сессия QR)", r.status_code == 200 and r.json().get("status") == "connected"
              and tg.decrypt_session(row().session_encrypted) == "S2+pw", r.text[:200])
        cl.delete("/api/agent/telegram-account", headers=H)

        tg.qr_login = scripted({"ok": False, "error": "qr_expired"}, delay=0.3)
        cl.post("/api/agent/telegram-account/qr/start", headers=H)
        j = wait_status(cl, "qr_expired")
        check("не отсканировали → qr_expired без ошибки", j.get("status") == "qr_expired" and not j.get("last_error"))

        # новая попытка гасит старую: первая «успела бы» подключить, но её метку уже сменили
        tg.qr_login = scripted({"ok": True, "needs_password": False, "session": "OLD", "tg_username": "old"}, delay=1.0)
        cl.post("/api/agent/telegram-account/qr/start", headers=H)
        tg.qr_login = scripted({"ok": True, "needs_password": False, "session": "NEW", "tg_username": "new"}, delay=0.3)
        cl.post("/api/agent/telegram-account/qr/start", headers=H)
        j = wait_status(cl, "connected")
        import time
        time.sleep(1.2)
        j = cl.get("/api/agent/telegram-account", headers=H).json()
        check("новая попытка гасит старую: подключён по новой", j.get("tg_username") == "new" and tg.decrypt_session(row().session_encrypted) == "NEW", j.get("tg_username"))
        cl.delete("/api/agent/telegram-account", headers=H)

        # вход по номеру посреди QR гасит QR
        tg.qr_login = scripted({"ok": True, "needs_password": False, "session": "QR", "tg_username": "qr"}, delay=1.0)
        cl.post("/api/agent/telegram-account/qr/start", headers=H)
        tg.start_login = lambda phone: asyncio.sleep(0, {"ok": True, "session": "CODE", "phone_code_hash": "h"})
        r = cl.post("/api/agent/telegram-account/start", headers=H, json={"phone": "0700123456"})
        time.sleep(1.4)
        j = cl.get("/api/agent/telegram-account", headers=H).json()
        check("вход по номеру посреди QR: QR больше не подключит", r.status_code == 200 and j.get("status") == "pending_code", j.get("status"))

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
