#!/usr/bin/env python3
"""
Звонки WhatsApp голосового агента — управление аккаунтом шлюза из Render Shell (этап 1, проба).
ТЗ: docs/tz_whatsapp_calls.md, шлюз: infra/wa-call-gateway/.

  python scripts/wa_call_account.py agents --email you@mail.com   # id агентов и владельца (для WA_CALLS_USERS)
  python scripts/wa_call_account.py list
  python scripts/wa_call_account.py create --agent <id агента>      # аккаунт + QR в терминале
  python scripts/wa_call_account.py qr <id аккаунта>                 # новый QR (номер отвязали / QR истёк)
  python scripts/wa_call_account.py set <id> --inbound on --outbound on --limit 20 --first-phrase "…"
  python scripts/wa_call_account.py call <id> 996555123456 [--any]   # исходящий через WhatsApp
  python scripts/wa_call_account.py calls [--limit 20]               # последние звонки WhatsApp
  python scripts/wa_call_account.py logout <id>                      # отвязать номер от шлюза
  python scripts/wa_call_account.py delete <id>

Нужны переменные Render: WA_CALL_GATEWAY_TOKEN (= GATEWAY_TOKEN шлюза), WA_CALLS_ENABLED=1,
WA_CALLS_USERS=<id владельца>. QR показывается, когда шлюз подключён к бэкенду и прислал код.
"""

import argparse
import os
import sys
import time
import uuid
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backend.core.config  # noqa: E402,F401  — первым: иначе циклический импорт backend.db.session

from backend.core.config import settings  # noqa: E402
from backend.db.session import SessionLocal  # noqa: E402
from backend.models.agent_config import AgentConfig  # noqa: E402
from backend.models.sip_gateway import SipCall  # noqa: E402
from backend.models.wa_call import AgentWaCallAccount, WaCallStatus  # noqa: E402
from backend.services import wa_call_service  # noqa: E402


def _ensure_table() -> None:
    from backend.models.base import Base, engine
    Base.metadata.create_all(engine, tables=[AgentWaCallAccount.__table__], checkfirst=True)


def _account(db, ident: str) -> AgentWaCallAccount:
    try:
        acc = db.get(AgentWaCallAccount, uuid.UUID(ident))
    except ValueError:
        acc = None
    if acc is None:
        sys.exit(f"Аккаунт {ident} не найден (python scripts/wa_call_account.py list)")
    return acc


def _onoff(value):
    if value is None:
        return None
    return value.lower() in ("1", "on", "yes", "true", "да")


def _print_qr(code: str) -> None:
    try:
        import segno
        segno.make(code, error="l").terminal(compact=True)
    except Exception as exc:
        print(f"(не удалось нарисовать QR: {exc}) Код QR: {code}")


def _checks(acc: AgentWaCallAccount) -> None:
    if not settings.WA_CALL_GATEWAY_TOKEN:
        print("⚠ WA_CALL_GATEWAY_TOKEN не задан в Render — шлюз не сможет подключиться")
    if not wa_call_service.feature_enabled_for(acc.user_id):
        print(f"⚠ Звонки WhatsApp выключены для владельца {acc.user_id}: WA_CALLS_ENABLED=1 и "
              f"WA_CALLS_USERS={acc.user_id} (через запятую)")


def show(acc: AgentWaCallAccount) -> None:
    print(f"{acc.id}  агент {acc.agent_config_id}  {acc.status:<12} +{acc.phone or '—':<14} "
          f"шлюз {acc.gateway_id}  входящие {'да' if acc.inbound_enabled else 'нет'}  "
          f"исходящие {'да' if acc.outbound_enabled else 'нет'} (до {acc.daily_outbound_limit}/сутки)"
          + (f"  ошибка: {acc.last_error}" if acc.last_error else ""))


def wait_qr(acc_id, timeout: int = 240) -> None:
    """Ждать QR от шлюза и рисовать его, пока номер не подключится."""
    print("Жду QR-код от шлюза… (WhatsApp на телефоне → Связанные устройства → Привязать устройство)")
    shown = None
    started = time.time()
    while time.time() - started < timeout:
        db = SessionLocal()
        try:
            acc = db.get(AgentWaCallAccount, acc_id)
            if acc is None:
                return
            if acc.status == WaCallStatus.CONNECTED:
                print(f"\n✅ Подключено: +{acc.phone} ({acc.push_name or '—'}). Позвоните на этот номер в WhatsApp.")
                return
            if acc.qr_code and acc.qr_code != shown:
                shown = acc.qr_code
                print(f"\nQR (обновляется сам, действует до {acc.qr_expires_at:%H:%M:%S} UTC):")
                _print_qr(acc.qr_code)
            if acc.status in (WaCallStatus.ERROR, WaCallStatus.BANNED) and acc.last_error:
                print(f"Шлюз сообщил: {acc.status} — {acc.last_error}")
        finally:
            db.close()
        time.sleep(2)
    print("Время ожидания вышло. Проверьте журнал шлюза: journalctl -u voksy-wa-call -f "
          "и повторите: python scripts/wa_call_account.py qr <id>")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    ag = sub.add_parser("agents")
    ag.add_argument("--email", help="email владельца (часть адреса тоже подходит)")
    c = sub.add_parser("create")
    c.add_argument("--agent", required=True)
    c.add_argument("--gateway", default=settings.WA_CALL_GATEWAY_DEFAULT_ID)
    c.add_argument("--no-wait", action="store_true")
    q = sub.add_parser("qr")
    q.add_argument("account")
    s = sub.add_parser("set")
    s.add_argument("account")
    s.add_argument("--inbound")
    s.add_argument("--outbound")
    s.add_argument("--limit", type=int)
    s.add_argument("--first-phrase")
    k = sub.add_parser("call")
    k.add_argument("account")
    k.add_argument("to")
    k.add_argument("--any", action="store_true", help="звонить и тем, кто ни разу не писал (только проба)")
    k.add_argument("--name", help="имя собеседника для приветствия")
    ls = sub.add_parser("calls")
    ls.add_argument("--limit", type=int, default=20)
    lo = sub.add_parser("logout")
    lo.add_argument("account")
    d = sub.add_parser("delete")
    d.add_argument("account")
    args = p.parse_args()

    _ensure_table()
    db = SessionLocal()
    try:
        if args.cmd == "agents":
            from backend.models.user import User
            q = db.query(AgentConfig, User).join(User, User.id == AgentConfig.user_id)
            if args.email:
                q = q.filter(User.email.ilike(f"%{args.email.strip()}%"))
            rows = q.order_by(User.email, AgentConfig.created_at).limit(50).all()
            if not rows:
                print("Агентов не найдено")
            for agent, user in rows:
                print(f"агент {agent.id}  «{agent.name}»  голос: {agent.assistant_type or '—'}  "
                      f"{'включён' if agent.is_active else 'выключен'}  |  владелец {user.email}  id {user.id}")
            print("\nWA_CALLS_USERS = id владельца, create --agent = id агента")
            return
        if args.cmd == "list":
            rows = db.query(AgentWaCallAccount).order_by(AgentWaCallAccount.created_at.asc()).all()
            if not rows:
                print("Аккаунтов нет: python scripts/wa_call_account.py create --agent <id агента>")
            for acc in rows:
                show(acc)
            return
        if args.cmd == "create":
            try:
                agent = db.get(AgentConfig, uuid.UUID(args.agent))
            except ValueError:
                agent = None
            if agent is None:
                sys.exit(f"Агент {args.agent} не найден")
            acc = AgentWaCallAccount(
                user_id=agent.user_id, agent_config_id=agent.id, gateway_id=args.gateway,
                status=WaCallStatus.PENDING_QR, inbound_enabled=True, outbound_enabled=False,
                daily_outbound_limit=settings.WA_CALLS_DAILY_OUTBOUND,
                login_requested_at=datetime.utcnow(),
            )
            db.add(acc)
            db.commit()
            print(f"Аккаунт {acc.id} для агента «{agent.name}» создан, шлюзу отправлена команда входа.")
            _checks(acc)
            if not args.no_wait:
                wait_qr(acc.id)
            return
        acc = _account(db, args.account) if hasattr(args, "account") else None
        if args.cmd == "qr":
            acc.login_requested_at = datetime.utcnow()
            acc.status = WaCallStatus.PENDING_QR
            acc.qr_code = None
            db.commit()
            _checks(acc)
            wait_qr(acc.id)
        elif args.cmd == "set":
            if _onoff(args.inbound) is not None:
                acc.inbound_enabled = _onoff(args.inbound)
            if _onoff(args.outbound) is not None:
                acc.outbound_enabled = _onoff(args.outbound)
            if args.limit:
                acc.daily_outbound_limit = max(1, args.limit)
            if args.first_phrase is not None:
                acc.first_phrase = args.first_phrase.strip() or None
            db.commit()
            show(acc)
        elif args.cmd == "call":
            agent = db.get(AgentConfig, acc.agent_config_id)
            atype, aid = agent.assistant_type, agent.get_voice_assistant_id()
            meta = {"contact_name": args.name} if args.name else {}
            try:
                call = wa_call_service.queue_outbound_call(
                    db, account=acc, to_number=args.to, assistant_type=atype, assistant_id=aid, metadata=meta,
                    require_contact_history=not args.any)
            except ValueError as exc:
                sys.exit(f"Не звоним: {exc}")
            print(f"Звонок {call.id} поставлен в очередь шлюза {call.gateway_id}: +{call.did} → +{call.to_number}")
        elif args.cmd == "calls":
            rows = (db.query(SipCall).filter(SipCall.gateway_id.like("wa-call%"))
                    .order_by(SipCall.created_at.desc()).limit(args.limit).all())
            for r in rows:
                peer = r.caller if r.direction == "inbound" else r.to_number
                print(f"{r.created_at:%m-%d %H:%M} {r.id} {r.direction:<8} {r.status:<9} +{peer or '—':<14} "
                      f"{(r.duration_sec or 0):>6.0f} с  {r.end_reason or ''}  {'запись' if r.recording_url else ''}")
        elif args.cmd == "logout":
            acc.logout_requested_at = datetime.utcnow()
            db.commit()
            print("Шлюзу отправлена команда отвязать номер.")
        elif args.cmd == "delete":
            if acc.status == WaCallStatus.CONNECTED:
                acc.logout_requested_at = datetime.utcnow()
                db.commit()
                print("Номер на связи: сначала отвязываю (подождите 5 с)…")
                time.sleep(5)
            db.delete(acc)
            db.commit()
            print("Аккаунт удалён.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
