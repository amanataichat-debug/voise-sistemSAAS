"""
Ручная авторизация личного Telegram агента из Render Shell — с подробным
выводом, куда и как Telegram отправил код.

Зачем: в кабинете код «не приходит», а сайт показывает только итог. Скрипт
делает те же шаги, что /api/agent/telegram-account (тот же TELEGRAM_API_ID /
TELEGRAM_API_HASH, тот же Telethon), но печатает ответ Telegram целиком:
  • type      — куда отправлен код: App (в приложение Telegram на другом
                устройстве — чат «Telegram»), Sms, Call, FlashCall, MissedCall,
                Email, FirebaseSms, SetUpEmailRequired, Fragment…;
  • next_type — каким способом можно запросить код повторно (команда resend);
  • timeout   — через сколько секунд можно запросить повтор.
Если вход прошёл и указан --agent-id, сессия сохраняется в агента так же,
как это делает кабинет (зашифрованная StringSession + снимок диалогов), —
после этого в кабинете аккаунт виден как подключённый.

Запуск (Render → сервис → Shell, из корня проекта):
    python scripts/tg_login_debug.py +996554128222
    python scripts/tg_login_debug.py +996554128222 --agent-id <UUID агента>
    python scripts/tg_login_debug.py +996554128222 --agent-id <UUID> --debug   # + сетевой лог Telethon

Во время работы скрипт спрашивает код. Вместо кода можно ввести:
    resend — запросить код следующим способом (next_type, обычно SMS/звонок);
    q      — выйти (Telegram-сессия не сохраняется).
"""

import argparse
import asyncio
import getpass
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.core  # noqa: E402,F401 — порядок импорта (иначе циклический импорт моделей)
from backend.core.config import settings  # noqa: E402


def _type_name(obj) -> str:
    return type(obj).__name__.replace("SentCodeType", "").replace("CodeType", "") if obj is not None else "—"


HINTS = {
    "App": "код отправлен В ПРИЛОЖЕНИЕ Telegram (чат «Telegram» на телефоне/десктопе, где этот номер уже залогинен), не SMS",
    "Sms": "код отправлен SMS",
    "SmsWord": "в SMS придёт слово, а не цифры",
    "SmsPhrase": "в SMS придёт фраза, а не цифры",
    "Call": "код продиктуют звонком",
    "FlashCall": "код = номер входящего звонка (сброс)",
    "MissedCall": "код = последние цифры номера пропущенного звонка",
    "FragmentSms": "номер с Fragment — код придёт на fragment.com",
    "FirebaseSms": "Telegram хочет SMS через Firebase (только официальные приложения) — сторонний клиент код может не получить; пробуйте resend",
    "Email": "код отправлен на почту, привязанную к аккаунту",
    "EmailCode": "код отправлен на почту, привязанную к аккаунту",
    "SetUpEmailRequired": "Telegram требует сначала привязать email к аккаунту (сделайте это в официальном приложении и повторите)",
}


def _print_sent(sent, phone: str) -> None:
    t = _type_name(getattr(sent, "type", None))
    print("\n──────── Ответ Telegram на запрос кода ────────")
    print(f"  номер:      {phone}")
    print(f"  type:       {t}  → {HINTS.get(t, 'см. документацию Telegram auth.SentCode')}")
    print(f"  length:     {getattr(getattr(sent, 'type', None), 'length', '—')}")
    print(f"  next_type:  {_type_name(getattr(sent, 'next_type', None))}")
    print(f"  timeout:    {getattr(sent, 'timeout', None) or '—'} с")
    print(f"  hash:       {(getattr(sent, 'phone_code_hash', '') or '')[:6]}…")
    print("───────────────────────────────────────────────\n")
    try:
        print("Полный ответ:\n" + sent.stringify())
    except Exception:
        print(repr(sent))
    print()


async def main() -> int:
    ap = argparse.ArgumentParser(description="Авторизация личного Telegram агента с диагностикой")
    ap.add_argument("phone", help="номер, например +996554128222 или 0554 128 222")
    ap.add_argument("--agent-id", help="UUID агента: сохранить сессию в него после входа")
    ap.add_argument("--debug", action="store_true", help="подробный лог Telethon (сеть, DC, запросы)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not args.debug:
        logging.getLogger("telethon").setLevel(logging.INFO)

    from backend.utils.phone import normalize_phone_e164
    from backend.services import telegram_user_service as tg_user

    phone = normalize_phone_e164(args.phone)
    if not phone:
        print(f"❌ Номер «{args.phone}» не распознан")
        return 1
    print(f"Номер после нормализации: {phone}")

    if not tg_user.is_configured():
        print("❌ Не заданы TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION_KEY")
        return 1
    print(f"TELEGRAM_API_ID = {settings.TELEGRAM_API_ID}, API_HASH = {str(settings.TELEGRAM_API_HASH)[:4]}…")

    agent = None
    if args.agent_id:
        from backend.db.session import SessionLocal
        from backend.models.agent_config import AgentConfig
        db = SessionLocal()
        agent = db.query(AgentConfig).filter(AgentConfig.id == args.agent_id).first()
        db.close()
        if agent is None:
            print(f"❌ Агент {args.agent_id} не найден")
            return 1
        print(f"Агент: {agent.name} ({agent.id}), владелец {agent.user_id}")

    from telethon import errors
    from telethon.tl.functions.auth import ResendCodeRequest

    client = tg_user._new_client()
    await client.connect()
    try:
        print(f"Подключено к Telegram, DC {client.session.dc_id} ({client.session.server_address})")
        try:
            sent = await client.send_code_request(phone)
        except Exception as e:
            print(f"❌ send_code_request: {type(e).__name__}: {e}")
            print(f"   код ошибки для кабинета: {tg_user._error_code(e)}")
            return 1
        _print_sent(sent, phone)
        code_hash = sent.phone_code_hash

        while True:
            code = input("Введите код (или resend / q): ").strip()
            if code.lower() == "q":
                print("Выход без сохранения.")
                return 0
            if code.lower() == "resend":
                try:
                    sent = await client(ResendCodeRequest(phone, code_hash))
                    code_hash = sent.phone_code_hash
                    _print_sent(sent, phone)
                except Exception as e:
                    print(f"❌ resend: {type(e).__name__}: {e}")
                continue
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
                break
            except errors.SessionPasswordNeededError:
                pwd = getpass.getpass("Включена двухэтапная аутентификация. Облачный пароль: ")
                try:
                    await client.sign_in(password=pwd)
                    break
                except Exception as e:
                    print(f"❌ пароль: {type(e).__name__}: {e}")
                    return 1
            except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError) as e:
                print(f"❌ неверный код ({type(e).__name__}) — попробуйте ещё раз")
            except Exception as e:
                print(f"❌ sign_in: {type(e).__name__}: {e}")
                return 1

        me = await client.get_me()
        print(f"\n✅ Вход выполнен: id={me.id} @{me.username or '—'} {me.first_name or ''}")
        session_str = client.session.save()
    finally:
        await client.disconnect()

    if agent is None:
        print("Сессия НЕ сохранена (не указан --agent-id). Telegram покажет вход в «Устройствах» — можно завершить его там.")
        return 0

    # Сохранение — как в кабинете (_finalize_connected + _baseline_dialogs).
    from backend.db.session import SessionLocal
    from backend.models.agent_telegram_account import AgentTelegramAccount, AgentTelegramDialog
    from backend.api.agent_telegram_account import _baseline_dialogs

    db = SessionLocal()
    try:
        row = db.query(AgentTelegramAccount).filter(
            AgentTelegramAccount.agent_config_id == agent.id
        ).first()
        if row is None:
            row = AgentTelegramAccount(agent_config_id=agent.id, user_id=agent.user_id)
            db.add(row)
        else:
            # Новый аккаунт — старые диалоги/маркеры поллера не годятся.
            db.query(AgentTelegramDialog).filter(AgentTelegramDialog.account_id == row.id).delete()
        row.phone = phone
        row.session_encrypted = tg_user.encrypt_session(session_str)
        row.status = "connected"
        row.phone_code_hash = None
        row.tg_user_id = me.id
        row.tg_username = me.username
        row.tg_first_name = me.first_name
        row.last_error = None
        db.commit()
        db.refresh(row)
        await _baseline_dialogs(db, row, session_str)
        print(f"✅ Сессия сохранена в агента {agent.id}. Включите автоответ в кабинете (Коннекторы → Telegram).")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
