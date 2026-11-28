"""
Звонки WhatsApp голосового агента (ТЗ docs/tz_whatsapp_calls.md): логика бэкенда.

Звонок держит свой шлюз wa-call-1 (infra/wa-call-gateway/). Он присылает события по
управляющему сокету /ws/wa-call-gateway/control (api/wa_call_gateway.py), а звук — по тому же
медиа-сокету /ws/sip/{call_id}, что и SIP-мост, поэтому разговор ведут ТЕ ЖЕ голосовые агенты.
Здесь — всё, что звонок WhatsApp отличает от телефонного:

  * выключатель WA_CALLS_ENABLED + список владельцев WA_CALLS_USERS (проба);
  * решение по входящему: принять (accept с ассистентом агента) или отклонить
    (номер не привязан, агент выключен, нет тарифа, кошелёк пуст) — с записью в sip_calls
    и сообщением владельцу о пропущенном звонке;
  * состояние аккаунтов (QR, на связи, отвязан, блокировка) → agent_wa_call_accounts;
  * очередь исходящих через WhatsApp с теми же защитами, что у SIP (свой номер, анти-долбёж,
    кошелёк), плюс лимиты против блокировки номера: в сутки, в час и «первым — только тем,
    кто сам писал или звонил».

Звонок WhatsApp — строка sip_calls с call_metadata.channel = "whatsapp" (новых таблиц для
звонков нет): журнал, «Диалоги», запись, стенограмма и PostCall работают как есть.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.agent_config import AgentConfig
from backend.models.sip_gateway import SIP_SUPPORTED_ASSISTANT_TYPES, SipCall, SipCallStatus, normalize_sip_number
from backend.models.wa_call import GATEWAY_STATE_TO_STATUS, AgentWaCallAccount, WaCallStatus

logger = get_logger(__name__)

CHANNEL = "whatsapp"

# Почему отклонили входящий → нужно ли сообщить владельцу о пропущенном звонке
REJECT_NOTIFY = {"agent_inactive", "assistant_inactive", "wallet_empty", "no_access", "inbound_disabled",
                 "gateway_busy", "decision_timeout"}
REJECT_TEXT = {
    "agent_inactive": "агент выключен",
    "assistant_inactive": "голосовой ассистент агента выключен",
    "assistant_missing": "у агента нет голосового ассистента",
    "wallet_empty": "кошелёк пуст",
    "no_access": "тариф не включает агента",
    "inbound_disabled": "приём звонков WhatsApp выключен",
    "gateway_busy": "все линии шлюза заняты",
    "decision_timeout": "бэкенд не успел ответить шлюзу",
    "backend_unavailable": "бэкенд был недоступен (деплой)",
    "feature_off": "звонки WhatsApp выключены",
    "unknown_account": "номер не привязан к агенту",
}


# Агент на звонок не отвечает, но номер владельца на связи: звонок продолжает звонить на его телефоне,
# а не сбрасывается (иначе клиент слышит «отклонено» и уходит).
PASS_TO_PHONE = {"agent_inactive", "assistant_inactive", "assistant_missing", "wallet_empty", "no_access",
                 "inbound_disabled", "feature_off"}
# Выключил сам владельца — о каждом звонке в Telegram не пишем
SILENT_REASONS = {"inbound_disabled"}


def _utcnow() -> datetime:
    return datetime.utcnow()  # как в sip_gateway_service: naive UTC


def _uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


# ============================================================================ выключатели

def allowed_users() -> set:
    return {x.strip() for x in (settings.WA_CALLS_USERS or "").split(",") if x.strip()}


def feature_enabled_for(user_id: Any) -> bool:
    """Звонки WhatsApp включены для владельца: WA_CALLS_ENABLED и (список пуст или он в списке)."""
    if not settings.WA_CALLS_ENABLED:
        return False
    users = allowed_users()
    return not users or str(user_id) in users


PLAN_TITLES = {"start": "Старт", "profi": "Profi", "agent": "Агент", "beauty_ai": "Beauty AI",
               "beauty_pro": "Beauty Pro"}


def allowed_plans() -> set:
    return {x.strip() for x in (settings.WA_CALLS_PLANS or "").split(",") if x.strip()}


def plans_text() -> str:
    codes = [c for c in ("start", "profi", "beauty_ai", "beauty_pro") if c in allowed_plans()]
    return ", ".join(PLAN_TITLES.get(c, c) for c in codes) or "—"


def plan_allows(owner) -> bool:
    """Тариф владельца включает звонки WhatsApp: активный тариф из WA_CALLS_PLANS (пробный — нет), админ — да."""
    if owner is None or getattr(owner, "agent_subscription_blocked", False):
        return False
    if getattr(owner, "is_admin", False):
        return True
    try:
        plan = owner.subscription_plan_rel
        end = owner.subscription_end_date
        if plan is None or not end or plan.code not in allowed_plans():
            return False
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end > datetime.now(timezone.utc)
    except Exception:
        return False


def can_call_out(owner) -> bool:
    """Звонить клиентам самому через WhatsApp — инициатива, только режим full (Profi, Beauty Pro)."""
    try:
        return plan_allows(owner) and owner.agent_mode() == "full"
    except Exception:
        return False


def is_whatsapp_call(call: Optional[SipCall]) -> bool:
    return bool(call is not None and (call.call_metadata or {}).get("channel") == CHANNEL)


class WaLine:
    """
    «Номер» звонка WhatsApp для кода, написанного под SIP-номер (sip_phone_numbers): первая фраза,
    привязка к агенту (resolve_greeting, AgentCallFinalizer). Не строка базы — id нет.
    """

    def __init__(self, account: AgentWaCallAccount) -> None:
        self.id = None
        self.user_id = account.user_id
        self.agent_config_id = account.agent_config_id
        self.first_phrase = account.first_phrase
        self.phone_number = account.phone
        self.gateway_id = account.gateway_id
        self.allow_outbound = bool(account.outbound_enabled)
        self.is_active = True


def line_for_call(db: Session, call: SipCall) -> Optional[WaLine]:
    account = account_for_call(db, call)
    return WaLine(account) if account is not None else None


def account_for_call(db: Session, call: SipCall) -> Optional[AgentWaCallAccount]:
    aid = _uuid((call.call_metadata or {}).get("wa_account_id"))
    return db.get(AgentWaCallAccount, aid) if aid else None


# ============================================================================ агент и ассистент

def _resolve_voice(db: Session, account: AgentWaCallAccount) -> Tuple[Optional[AgentConfig], Optional[str], Optional[Any], Optional[str]]:
    """(агент, тип ассистента, id ассистента, причина отказа)."""
    agent = db.get(AgentConfig, account.agent_config_id)
    if agent is None:
        return None, None, None, "unknown_account"
    atype = agent.assistant_type
    aid = agent.get_voice_assistant_id()
    if atype not in SIP_SUPPORTED_ASSISTANT_TYPES or not aid:
        return agent, None, None, "assistant_missing"
    return agent, atype, aid, None


def _owner(db: Session, user_id):
    from backend.models.user import User
    return db.query(User).filter(User.id == user_id).first()


def _phone_from_lid(db: Session, agent: AgentConfig, jid: str) -> str:
    """WhatsApp прислал только LID (скрытый id): номер — из переписки Evolution с тем же JID."""
    if not jid or agent is None:
        return ""
    try:
        from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppChat
        row = (
            db.query(AgentWhatsAppChat.phone)
            .join(AgentWhatsAppAccount, AgentWhatsAppAccount.id == AgentWhatsAppChat.account_id)
            .filter(AgentWhatsAppAccount.agent_config_id == agent.id, AgentWhatsAppChat.remote_jid == jid)
            .first()
        )
        return normalize_sip_number(row[0]) if row and row[0] else ""
    except Exception as exc:
        logger.warning(f"[WA-CALL] LID lookup failed: {exc}")
        db.rollback()
        return ""


# ============================================================================ входящий

def decide_inbound(db: Session, gateway_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Событие started входящего звонка → команда шлюзу accept / reject.
    Строка sip_calls заводится в обоих случаях: принятый звонок дальше ведёт медиа-сокет,
    отклонённый остаётся в журнале как пропущенный.
    """
    call_id = _uuid(event.get("call_id"))
    if call_id is None:
        return {"type": "reject", "call_id": str(event.get("call_id") or ""), "reason": "bad_call_id"}
    account = db.get(AgentWaCallAccount, _uuid(event.get("account_id"))) if _uuid(event.get("account_id")) else None
    caller = normalize_sip_number(str(event.get("caller") or ""))
    caller_jid = str(event.get("caller_jid") or "")[:128]
    caller_name = str(event.get("caller_name") or "")[:120]

    def reply(reason: Optional[str], agent=None, atype=None, aid=None) -> Dict[str, Any]:
        meta = {
            "channel": CHANNEL,
            "wa_call_id": str(event.get("wa_call_id") or "")[:64],
            "caller_jid": caller_jid,
            "caller_name": caller_name,
        }
        if account is not None:
            meta["wa_account_id"] = str(account.id)
            meta["agent_config_id"] = str(account.agent_config_id)
        if caller_name:
            meta["contact_name"] = caller_name
        if reason:
            meta["rejected"] = reason
        call = SipCall(
            id=call_id,
            gateway_id=gateway_id,
            direction="inbound",
            status=SipCallStatus.FAILED if reason else SipCallStatus.RINGING,
            user_id=account.user_id if account is not None else None,
            assistant_type=atype,
            assistant_id=_uuid(aid),
            did=account.phone if account is not None else str(event.get("did") or "")[:30],
            caller=caller or None,
            call_metadata=meta,
            ended_at=_utcnow() if reason else None,
            end_reason=reason,
        )
        db.add(call)
        try:
            db.commit()
        except IntegrityError:  # повтор события после переподключения шлюза
            db.rollback()
        if reason:
            logger.info(f"[WA-CALL] inbound {call_id} from {caller or caller_jid or '—'} rejected: {reason}")
            if reason in REJECT_NOTIFY and reason not in SILENT_REASONS and agent is not None:
                notify_missed_call(agent, caller, caller_name, reason)
            out = {"type": "reject", "call_id": str(call_id), "reason": reason}
            if account is not None and reason in PASS_TO_PHONE:
                # Агент не отвечает — звонок не сбрасываем: он звонит на телефоне владельца как обычно
                # (шлюз со старой версией не знает поля и отклонит, как раньше)
                out["ignore"] = True
            return out
        logger.info(f"[WA-CALL] inbound {call_id} from {caller or caller_jid or '—'} accepted → {atype}/{aid}")
        return {"type": "accept", "call_id": str(call_id), "assistant_id": str(aid), "assistant_type": atype,
                "metadata": {"agent_config_id": str(account.agent_config_id)}}

    if account is None:
        return reply("unknown_account")
    if not feature_enabled_for(account.user_id):
        return reply("feature_off")
    agent, atype, aid, problem = _resolve_voice(db, account)
    if problem:
        return reply(problem, agent)
    if not caller:
        caller = _phone_from_lid(db, agent, caller_jid)
    if not account.inbound_enabled:
        return reply("inbound_disabled", agent)
    if not agent.is_active:
        return reply("agent_inactive", agent)
    owner = _owner(db, account.user_id)
    if owner is None or not owner.has_agent_chat_access() or not plan_allows(owner):
        return reply("no_access", agent)
    from backend.services.sip_gateway_service import SipGatewayService
    assistant = SipGatewayService.load_assistant(db, atype, aid)
    if assistant is None:
        return reply("assistant_missing", agent)
    if not getattr(assistant, "is_active", True):
        return reply("assistant_inactive", agent)
    if atype == "eleven":
        from backend.services.wallet_service import WalletService
        if not WalletService.can_start_call(db, account.user_id):
            return reply("wallet_empty", agent)
    return reply(None, agent, atype, aid)


def notify_missed_call(agent: AgentConfig, caller: str, caller_name: str, reason: str) -> None:
    """Владельцу в Telegram-бот агента: пропущенный звонок WhatsApp и почему."""
    who = caller_name or ""
    if caller:
        who = f"{who} (+{caller})" if who else f"+{caller}"
    line = (f"📞 Пропущенный звонок WhatsApp от {who or 'неизвестного номера'}: {REJECT_TEXT.get(reason, reason)}. "
            f"Агент не ответил — звонок шёл на ваш телефон.")
    if reason == "wallet_empty":
        line += " Пополните кошелёк в кабинете."
    _notify(agent, line)


def _notify(agent: AgentConfig, line: str) -> None:
    try:
        from backend.core.background_loop import submit
        from backend.services.agent_contact_merge import _BotTarget
        from backend.services.agent_tools import fn_send_telegram_notification
        target = _BotTarget(agent)
        if not (target.telegram_enabled and target.has_telegram_bot()):
            return
        submit(fn_send_telegram_notification({"message": line}, target, None))
    except Exception as exc:
        logger.warning(f"[WA-CALL] owner notification failed: {exc}")


# ============================================================================ события шлюза

def apply_call_event(db: Session, gateway_id: str, event: Dict[str, Any]) -> Optional[SipCall]:
    """call.event шлюза WhatsApp → sip_calls (через общий обработчик событий моста)."""
    from backend.services.sip_gateway_service import SipGatewayService
    call_id = _uuid(event.get("call_id"))
    if call_id is None:
        return None
    kind = event.get("event")
    call = db.get(SipCall, call_id)
    if call is None and event.get("direction") == "inbound":
        # Шлюз отклонил сам (все линии заняты / бэкенд недоступен), не спросив бэкенд
        account = db.get(AgentWaCallAccount, _uuid(event.get("account_id"))) if _uuid(event.get("account_id")) else None
        call = SipCall(
            id=call_id, gateway_id=gateway_id, direction="inbound", status=SipCallStatus.RINGING,
            user_id=account.user_id if account is not None else None,
            did=account.phone if account is not None else None,
            caller=normalize_sip_number(str(event.get("caller") or "")) or None,
            call_metadata={"channel": CHANNEL, "wa_account_id": str(account.id) if account else None,
                           "agent_config_id": str(account.agent_config_id) if account else None,
                           "caller_jid": str(event.get("caller_jid") or "")[:128],
                           "caller_name": str(event.get("caller_name") or "")[:120]},
        )
        db.add(call)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            call = db.get(SipCall, call_id)
        if account is not None and kind == "failed" and event.get("reason") in REJECT_NOTIFY:
            agent = db.get(AgentConfig, account.agent_config_id)
            if agent is not None:
                notify_missed_call(agent, call.caller or "", str(event.get("caller_name") or ""),
                                   str(event.get("reason")))
    if call is None:
        logger.warning(f"[WA-CALL] event {kind} for unknown call {call_id}")
        return None
    meta = dict(call.call_metadata or {})
    meta.setdefault("channel", CHANNEL)
    if event.get("wa_call_id"):
        meta["wa_call_id"] = str(event["wa_call_id"])[:64]
    if kind in ("ended", "failed") and event.get("underruns"):
        meta["gateway_underruns"] = int(event.get("underruns") or 0)
    if event.get("error"):
        meta["gateway_error"] = str(event["error"])[:300]
    call.call_metadata = meta
    if kind == "ringing":
        if call.direction == "outbound" and call.status in (SipCallStatus.QUEUED, SipCallStatus.DIALING):
            call.status = SipCallStatus.RINGING
        db.commit()
        return call
    if kind == "failed" and call.status == SipCallStatus.FAILED and call.end_reason:
        db.commit()  # уже отклонён бэкендом — причина остаётся своя
        return call
    data = dict(event)
    data["gateway_id"] = gateway_id
    return SipGatewayService.apply_bridge_event(db, data)


def apply_account_state(db: Session, gateway_id: str, data: Dict[str, Any]) -> Optional[AgentWaCallAccount]:
    """account.state шлюза → строка аккаунта; блокировка и отвязка номера — владельцу в Telegram."""
    account = db.get(AgentWaCallAccount, _uuid(data.get("account_id"))) if _uuid(data.get("account_id")) else None
    if account is None:
        logger.warning(f"[WA-CALL] state for unknown account {data.get('account_id')}")
        return None
    state = str(data.get("state") or "")
    new_status = GATEWAY_STATE_TO_STATUS.get(state, WaCallStatus.ERROR)
    old_status = account.status
    now = _utcnow()
    account.gateway_id = gateway_id or account.gateway_id
    account.status = new_status
    account.last_state_at = now
    account.last_error = (str(data.get("error"))[:500] if data.get("error") else None)
    if state == "qr_expired":
        account.last_error = account.last_error or "QR не отсканирован вовремя — запросите новый"
    phone = normalize_sip_number(str(data.get("phone") or ""))
    if phone:
        account.phone = phone
    if data.get("push_name"):
        account.push_name = str(data["push_name"])[:255]
    if state == "qr" and data.get("qr"):
        account.qr_code = str(data["qr"])[:2000]
        account.qr_expires_at = now + timedelta(seconds=int(data.get("expires_in") or 60))
    elif new_status != WaCallStatus.PENDING_QR:
        account.qr_code = None
        account.qr_expires_at = None
    if new_status == WaCallStatus.CONNECTED and old_status != WaCallStatus.CONNECTED:
        account.connected_at = now
    db.commit()
    if old_status != new_status and new_status in (WaCallStatus.BANNED, WaCallStatus.LOGGED_OUT):
        agent = db.get(AgentConfig, account.agent_config_id)
        if agent is not None and old_status == WaCallStatus.CONNECTED:
            text = ("WhatsApp временно заблокировал номер звонков агента" if new_status == WaCallStatus.BANNED
                    else "Номер WhatsApp для звонков агента отключён с телефона")
            _notify(agent, f"⚠️ {text} (+{account.phone or '—'}). Звонки через WhatsApp остановлены — "
                           f"подключите номер заново в кабинете.")
    return account


def sync_gateway_accounts(db: Session, gateway_id: str, accounts: list) -> None:
    """hello шлюза: известные ему аккаунты и их состояния."""
    for item in accounts or []:
        if isinstance(item, dict):
            try:
                apply_account_state(db, gateway_id, item)
            except Exception as exc:
                db.rollback()
                logger.warning(f"[WA-CALL] hello account sync failed: {exc}")


def pending_account_commands(db: Session, gateway_id: str) -> list:
    """Команды login/logout, которые ждут отправки на шлюз (поставил кабинет или скрипт)."""
    out = []
    rows = (
        db.query(AgentWaCallAccount)
        .filter(AgentWaCallAccount.gateway_id == gateway_id)
        .filter((AgentWaCallAccount.login_requested_at.isnot(None))
                | (AgentWaCallAccount.logout_requested_at.isnot(None)))
        .with_for_update(skip_locked=True)
        .all()
    )
    for acc in rows:
        if acc.logout_requested_at is not None:
            out.append({"type": "logout", "account_id": str(acc.id)})
            acc.logout_requested_at = None
            acc.login_requested_at = None
        elif acc.login_requested_at is not None:
            out.append({"type": "login", "account_id": str(acc.id), "force": "true"})
            acc.login_requested_at = None
    db.commit()
    return out


# ============================================================================ исходящий

def outbound_refusal(db: Session, account: Optional[AgentWaCallAccount], to_digits: str,
                     require_contact_history: bool = True) -> Optional[str]:
    """Причина, по которой звонить через WhatsApp нельзя (None — можно). Текст — для модели и владельца."""
    from backend.services.sip_gateway_service import (
        BISHKEK_OFFSET, OUTBOUND_MIN_GAP_PER_NUMBER, SipGatewayService, bishkek_now,
    )

    if account is None:
        return "У агента нет подключённого номера для звонков WhatsApp."
    if not feature_enabled_for(account.user_id):
        return "Звонки WhatsApp выключены."
    if not account.is_connected():
        return "Номер для звонков WhatsApp не на связи — подключите его заново в кабинете."
    if not account.outbound_enabled:
        return "Исходящие звонки через WhatsApp выключены в настройках агента."
    if len(to_digits) < 8 or not to_digits.isdigit():
        return "Неверный номер."
    if to_digits == normalize_sip_number(account.phone or "") or SipGatewayService.is_platform_number(db, to_digits) \
            or is_wa_call_number(db, to_digits):
        return "Это номер самой платформы (номер агента), а не номер клиента — звонить на него нельзя."
    now = _utcnow()
    recent = (
        db.query(SipCall.id)
        .filter(SipCall.user_id == account.user_id, SipCall.direction == "outbound",
                SipCall.to_number == to_digits, SipCall.created_at >= now - OUTBOUND_MIN_GAP_PER_NUMBER)
        .first()
    )
    if recent is not None:
        return ("Этому номеру уже звонили только что — повторный звонок будет позже, "
                "чтобы не беспокоить клиента подряд.")
    base = (db.query(SipCall.id)
            .filter(SipCall.gateway_id == account.gateway_id, SipCall.direction == "outbound",
                    SipCall.did == account.phone))
    hourly = base.filter(SipCall.created_at >= now - timedelta(hours=1)).count()
    if hourly >= max(1, settings.WA_CALLS_HOURLY_OUTBOUND):
        return (f"Лимит звонков через WhatsApp в час ({settings.WA_CALLS_HOURLY_OUTBOUND}) исчерпан — "
                f"защита номера от блокировки. Позвоните позже.")
    day_start = bishkek_now().replace(hour=0, minute=0, second=0, microsecond=0) - BISHKEK_OFFSET  # naive UTC
    daily = base.filter(SipCall.created_at >= day_start).count()
    limit = account.daily_outbound_limit or settings.WA_CALLS_DAILY_OUTBOUND
    if daily >= limit:
        return (f"Дневной лимит звонков через WhatsApp ({limit}) исчерпан — защита номера от блокировки. "
                f"Звонок можно поставить на завтра.")
    if require_contact_history and not client_reached_us(db, account, to_digits):
        return ("Через WhatsApp первым звоним только тем, кто сам писал или звонил агенту: "
                "сначала напишите клиенту и дождитесь ответа.")
    from backend.services.wallet_service import WalletService
    if not WalletService.can_start_call(db, account.user_id):
        return "Баланс кошелька исчерпан — пополните кошелёк, чтобы звонить."
    return None


def is_wa_call_number(db: Session, digits: str) -> bool:
    """Номер — один из наших номеров звонков WhatsApp (любого владельца)."""
    try:
        return db.query(AgentWaCallAccount.id).filter(AgentWaCallAccount.phone == digits).first() is not None
    except Exception:
        db.rollback()
        return False


def client_reached_us(db: Session, account: AgentWaCallAccount, digits: str) -> bool:
    """Клиент сам выходил на связь с агентом: писал в любом мессенджере или звонил."""
    try:
        from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppChat, AgentWhatsAppMessage
        suffix = digits[-9:]
        wrote = (
            db.query(AgentWhatsAppMessage.id)
            .join(AgentWhatsAppChat, AgentWhatsAppChat.id == AgentWhatsAppMessage.chat_id)
            .join(AgentWhatsAppAccount, AgentWhatsAppAccount.id == AgentWhatsAppChat.account_id)
            .filter(AgentWhatsAppAccount.agent_config_id == account.agent_config_id,
                    AgentWhatsAppChat.phone.like(f"%{suffix}"),
                    AgentWhatsAppMessage.direction == "inbound")
            .first()
        )
        if wrote is not None:
            return True
        called = (db.query(SipCall.id)
                  .filter(SipCall.user_id == account.user_id, SipCall.direction == "inbound",
                          SipCall.caller.like(f"%{suffix}"))
                  .first())
        if called is not None:
            return True
        from backend.services.agent_contact_phones import find_contact_by_phone
        from backend.services.agent_mode import client_contacted_since
        contact = find_contact_by_phone(db, account.agent_config_id, digits)
        return bool(contact is not None and client_contacted_since(db, contact.id, datetime(2000, 1, 1)))
    except Exception as exc:
        logger.warning(f"[WA-CALL] contact history check failed: {exc}")
        db.rollback()
        return False


def account_for_agent(db: Session, agent_config_id: Any) -> Optional[AgentWaCallAccount]:
    aid = _uuid(agent_config_id)
    if aid is None:
        return None
    return (db.query(AgentWaCallAccount)
            .filter(AgentWaCallAccount.agent_config_id == aid)
            .order_by(AgentWaCallAccount.created_at.asc())
            .first())


def queue_outbound_call(db: Session, *, account: AgentWaCallAccount, to_number: str, assistant_type: str,
                        assistant_id: Any, metadata: Optional[Dict[str, Any]] = None,
                        task_id: Optional[uuid.UUID] = None, require_contact_history: bool = True) -> SipCall:
    """Поставить исходящий через WhatsApp в очередь шлюза (его заберёт управляющий сокет). ValueError — нельзя."""
    to_digits = normalize_sip_number(to_number)
    refusal = outbound_refusal(db, account, to_digits, require_contact_history=require_contact_history)
    if refusal:
        raise ValueError(refusal)
    meta = dict(metadata or {})
    meta.update({"channel": CHANNEL, "wa_account_id": str(account.id), "agent_config_id": str(account.agent_config_id)})
    call = SipCall(
        id=uuid.uuid4(),
        gateway_id=account.gateway_id,
        direction="outbound",
        status=SipCallStatus.QUEUED,
        user_id=account.user_id,
        phone_number_id=None,
        task_id=task_id,
        assistant_type=assistant_type,
        assistant_id=_uuid(assistant_id),
        did=account.phone,
        to_number=to_digits,
        call_metadata=meta,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    logger.info(f"[WA-CALL] queued outbound WhatsApp call {call.id}: {call.did} -> {call.to_number} "
                f"via {call.gateway_id}")
    return call


def originate_payload(call: SipCall) -> Dict[str, Any]:
    from backend.services.sip_gateway_service import SipGatewayService
    payload = SipGatewayService.originate_payload(call)
    payload["account_id"] = str((call.call_metadata or {}).get("wa_account_id") or "")
    return payload


# ============================================================================ выбор канала (ТЗ 5.3)

def usable_outbound_account(db: Session, agent_config_id: Any) -> Optional[AgentWaCallAccount]:
    """Аккаунт, через который агент может звонить сам: на связи, исходящие включены, тариф Profi."""
    account = account_for_agent(db, agent_config_id)
    if account is None or not account.is_connected() or not account.outbound_enabled:
        return None
    if not feature_enabled_for(account.user_id):
        return None
    owner = _owner(db, account.user_id)
    if not can_call_out(owner):  # Старт и Beauty AI — только входящие (исходящий = инициатива)
        return None
    return account


def whatsapp_dial_number(contact) -> str:
    """Номер WhatsApp клиента: продиктованный «для WhatsApp» (memory.whatsapp_phone), иначе основной."""
    mem = getattr(contact, "memory", None) or {}
    for raw in (mem.get("whatsapp_phone") if isinstance(mem, dict) else None, getattr(contact, "phone", None)):
        digits = normalize_sip_number(str(raw or ""))
        if len(digits) >= 8:
            return digits
    return ""


def whatsapp_route(db: Session, agent_config_id: Any, phone: str,
                   require_history: bool = True) -> Tuple[Optional[AgentWaCallAccount], Optional[str]]:
    """
    Можно ли позвонить на этот номер через WhatsApp: (аккаунт, None) или (None, причина для модели).
    Только постоянные условия; лимиты в сутки и в час проверяет постановка в очередь.
    """
    account = usable_outbound_account(db, agent_config_id)
    if account is None:
        return None, None
    digits = normalize_sip_number(phone or "")
    if len(digits) < 8:
        return None, "Неверный номер для звонка через WhatsApp."
    if require_history and not client_reached_us(db, account, digits):
        return None, ("Через WhatsApp первым звоним только тем, кто сам писал или звонил агенту: "
                      "сначала напиши клиенту и дождись ответа.")
    return account, None


def choose_outbound_channel(db: Session, agent_config_id: Any, contact) -> Tuple[Optional[str], Optional[AgentWaCallAccount], str]:
    """
    Канал исходящего звонка агента — решает код, не модель:
      номер O! → "sip" (оператор надёжнее), иначе → "whatsapp", если у агента есть аккаунт звонков
      WhatsApp с исходящими и клиент сам выходил на связь; иначе (None, None, номер).
    """
    from backend.models.sip_gateway import is_o_number
    from backend.services.agent_contact_phones import dial_number
    sip_digits = normalize_sip_number(dial_number(contact) if contact is not None else "")
    if is_o_number(sip_digits):
        return "sip", None, sip_digits
    wa_digits = whatsapp_dial_number(contact)
    account, _ = whatsapp_route(db, agent_config_id, wa_digits)
    if account is not None:
        return "whatsapp", account, wa_digits
    return None, None, sip_digits
