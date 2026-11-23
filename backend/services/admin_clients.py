"""
Админка «Клиенты» — клиенты платформы глазами владельца (ветка 1811-niche-admin-speed).

Старый список /api/admin/users делал на каждого пользователя четыре отдельных запроса (три
подсчёта ассистентов и проверку подписки): 100 строк — 400 запросов, страница грузилась секунды.
Здесь одна страница списка = два запроса: пользователи с тарифом (фильтры, поиск, сортировка,
пагинация в SQL) и агрегаты «что настроил клиент» только для id этой страницы.

Что видит владелец:
  • имя, компания, телефон (позвонить / WhatsApp), почта, ниша, тариф и дни до конца, вход;
  • что клиент настроил: бот/агент, WhatsApp, Telegram, Instagram, номер телефонии, ассистенты,
    каталог, база знаний, клиенты в CRM, звонки за 30 дней, оплаты;
  • свой статус по клиенту (Новый → Позвонил → Настраиваю → Платит → Ушёл), заметку и дату
    следующего звонка — мини-CRM владельца по клиентам платформы;
  • сводку: кто зарегистрировался сегодня / за неделю, кому позвонить, кто ничего не настроил.

Всё — чтение и правка своих полей users; деньги и тарифы правят прежние эндпоинты admin.py.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

BISHKEK = timezone(timedelta(hours=6))

# Статусы мини-CRM владельца по клиентам платформы
ADMIN_STATUSES = [
    {"key": "new", "label": "Новый", "color": "#64748b"},
    {"key": "contacted", "label": "Позвонил", "color": "#2a5ce8"},
    {"key": "setup", "label": "Настраиваю", "color": "#d97706"},
    {"key": "paying", "label": "Платит", "color": "#059669"},
    {"key": "churned", "label": "Ушёл", "color": "#dc2626"},
]
ADMIN_STATUS_KEYS = {s["key"] for s in ADMIN_STATUSES}

PAID_PLANS = ("start", "profi", "ai_voice", "agent", "pro", "business")
TRIAL_PLANS = ("free", "referral_trial")

SEGMENTS = (
    "all", "new_today", "new_week", "call_today", "no_phone", "no_setup",
    "trial", "paying", "expiring", "expired", "admins",
)
SORTS = {
    "created_desc": "u.created_at DESC NULLS LAST",
    "created_asc": "u.created_at ASC NULLS LAST",
    "login_desc": "u.last_login DESC NULLS LAST",
    "end_asc": "u.subscription_end_date ASC NULLS LAST",
    "name": "lower(coalesce(nullif(u.first_name, ''), u.email)) ASC",
    "next_contact": "u.admin_next_contact_at ASC NULLS LAST",
}


# ───────────────────────────── время ─────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def bishkek_day_start_utc(now: Optional[datetime] = None) -> datetime:
    """Начало сегодняшних суток по Бишкеку в UTC."""
    local = (now or _now()).astimezone(BISHKEK)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime) and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _aware(dt) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ───────────────────────────── фильтры ─────────────────────────────

def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _phone_query_digits(q: str) -> str:
    """
    Цифры номера из поиска без кода страны и ведущего нуля: «0700 12 34» и «+996 700 12 34»
    ищут одно и то же «7001234» (в базе номер хранится как +996700123456).
    """
    d = _digits(q)
    if d.startswith("996") and len(d) > 6:
        d = d[3:]
    elif d.startswith("0") and len(d) > 4:
        d = d[1:]
    return d


def _segment_sql(segment: str, params: Dict[str, Any]) -> Optional[str]:
    now = _now()
    params.setdefault("now", now)
    if segment == "new_today":
        params["day_start"] = bishkek_day_start_utc(now)
        return "u.created_at >= :day_start"
    if segment == "new_week":
        params["week_ago"] = now - timedelta(days=7)
        return "u.created_at >= :week_ago"
    if segment == "call_today":
        # кому звонить: новые без статуса/«Новый» за 14 дней или наступила дата следующего звонка
        params["fortnight"] = now - timedelta(days=14)
        params["day_end"] = bishkek_day_start_utc(now) + timedelta(days=1)
        return ("(u.admin_next_contact_at IS NOT NULL AND u.admin_next_contact_at < :day_end) OR "
                "(coalesce(u.admin_status, 'new') = 'new' AND u.created_at >= :fortnight AND NOT coalesce(u.is_admin, false))")
    if segment == "no_phone":
        return "(u.phone IS NULL OR u.phone = '')"
    if segment == "no_setup":
        return ("NOT EXISTS (SELECT 1 FROM agent_configs a WHERE a.user_id = u.id) "
                "AND NOT EXISTS (SELECT 1 FROM eleven_assistant_configs e WHERE e.user_id = u.id)")
    if segment == "trial":
        return "p.code IN ('free', 'referral_trial') AND u.subscription_end_date > :now"
    if segment == "paying":
        params["paid_plans"] = list(PAID_PLANS)
        return "p.code = ANY(:paid_plans) AND u.subscription_end_date > :now"
    if segment == "expiring":
        params["in3"] = now + timedelta(days=3)
        return "u.subscription_end_date > :now AND u.subscription_end_date <= :in3"
    if segment == "expired":
        return "(u.subscription_end_date IS NULL OR u.subscription_end_date <= :now) AND NOT coalesce(u.is_admin, false)"
    if segment == "admins":
        return "coalesce(u.is_admin, false)"
    return None


def _where(q: Optional[str], segment: Optional[str], status: Optional[str], niche: Optional[str],
           plan: Optional[str], params: Dict[str, Any]) -> str:
    conds: List[str] = []
    q = (q or "").strip()
    if q:
        params["q"] = f"%{q.lower()}%"
        parts = [
            "lower(u.email) LIKE :q", "lower(coalesce(u.first_name, '')) LIKE :q",
            "lower(coalesce(u.last_name, '')) LIKE :q", "lower(coalesce(u.company_name, '')) LIKE :q",
            "lower(coalesce(u.business_niche_other, '')) LIKE :q",
        ]
        digits = _phone_query_digits(q)
        if len(digits) >= 4:
            # номер в любом написании: «0700 12 34», «+996700…», «700123456»
            params["qd"] = f"%{digits}%"
            parts.append("regexp_replace(coalesce(u.phone, ''), '\\D', '', 'g') LIKE :qd")
        conds.append("(" + " OR ".join(parts) + ")")
    seg = _segment_sql(segment or "all", params) if segment and segment != "all" else None
    if seg:
        conds.append(f"({seg})")
    if segment != "admins":
        # рабочие списки — только клиенты; админы — отдельным сегментом «Админы»
        conds.append("NOT coalesce(u.is_admin, false)")
    if status:
        if status == "new":
            conds.append("coalesce(u.admin_status, 'new') = 'new'")
        else:
            params["status"] = status
            conds.append("u.admin_status = :status")
    if niche:
        if niche == "none":
            conds.append("(u.business_niche IS NULL OR u.business_niche = '')")
        else:
            params["niche"] = niche
            conds.append("u.business_niche = :niche")
    if plan:
        params["plan"] = plan
        conds.append("p.code = :plan")
    return ("WHERE " + " AND ".join(conds)) if conds else ""


# ───────────────────────────── «что настроил» ─────────────────────────────

def _setup_for(db: Session, ids: List[uuid.UUID]) -> Dict[str, Dict[str, Any]]:
    """Агрегаты по списку пользователей одним заходом (по запросу на таблицу, только эти id)."""
    out: Dict[str, Dict[str, Any]] = {str(i): {} for i in ids}
    if not ids:
        return out
    params = {"ids": [str(i) for i in ids], "month_ago": _now() - timedelta(days=30)}

    def run(sql: str, apply):
        try:
            for row in db.execute(text(sql), params).mappings():
                rec = out.get(str(row["user_id"]))
                if rec is not None:
                    apply(rec, row)
        except Exception as e:  # таблицы может не быть в старой схеме — блок просто пустой
            db.rollback()
            logger.warning(f"[ADMIN-CLIENTS] aggregate skipped: {e}")

    ids_cond = "user_id = ANY(CAST(:ids AS uuid[]))"
    run(f"""SELECT user_id, count(*) AS n, bool_or(telegram_bot_token IS NOT NULL AND telegram_bot_token <> '') AS tg_bot,
                   max(name) AS agent_name
            FROM agent_configs WHERE {ids_cond} GROUP BY user_id""",
        lambda r, row: r.update(agents=row["n"], tg_bot=bool(row["tg_bot"]), agent_name=row["agent_name"]))
    run(f"SELECT user_id, count(*) AS n FROM eleven_assistant_configs WHERE {ids_cond} GROUP BY user_id",
        lambda r, row: r.update(assistants=row["n"]))
    run(f"""SELECT user_id, bool_or(status = 'connected') AS ok FROM agent_whatsapp_accounts
            WHERE {ids_cond} GROUP BY user_id""",
        lambda r, row: r.update(whatsapp=bool(row["ok"])))
    run(f"""SELECT user_id, bool_or(status = 'connected') AS ok FROM agent_telegram_accounts
            WHERE {ids_cond} GROUP BY user_id""",
        lambda r, row: r.update(telegram=bool(row["ok"])))
    run(f"""SELECT user_id, bool_or(status = 'connected') AS ok FROM agent_connectors
            WHERE {ids_cond} AND toolkit = 'instagram' GROUP BY user_id""",
        lambda r, row: r.update(instagram=bool(row["ok"])))
    run(f"""SELECT user_id, count(*) AS n FROM sip_phone_numbers WHERE {ids_cond} AND is_active
            GROUP BY user_id""",
        lambda r, row: r.update(numbers=row["n"]))
    run(f"""SELECT user_id, count(*) FILTER (WHERE kind = 'product') AS products,
                   count(*) FILTER (WHERE kind <> 'product') AS knowledge
            FROM agent_knowledge_entries WHERE {ids_cond} GROUP BY user_id""",
        lambda r, row: r.update(products=row["products"], knowledge=row["knowledge"]))
    run(f"SELECT user_id, count(*) AS n FROM agent_contacts WHERE {ids_cond} GROUP BY user_id",
        lambda r, row: r.update(contacts=row["n"]))
    run(f"""SELECT user_id, count(*) AS n, max(created_at) AS last_at FROM sip_calls
            WHERE {ids_cond} AND created_at >= :month_ago GROUP BY user_id""",
        lambda r, row: r.update(calls_30d=row["n"], last_call_at=_iso(row["last_at"])))
    run(f"""SELECT user_id, coalesce(sum(amount), 0) AS paid, count(*) AS n, max(created_at) AS last_at
            FROM payment_transactions WHERE {ids_cond} AND status = 'success' GROUP BY user_id""",
        lambda r, row: r.update(paid_total=float(row["paid"] or 0), payments=row["n"],
                                last_payment_at=_iso(row["last_at"])))
    return out


def setup_score(s: Dict[str, Any]) -> Dict[str, Any]:
    """Сколько шагов запуска клиент сделал: бот/ассистент, канал, знания или каталог, номер."""
    steps = [
        ("assistant_or_agent", bool(s.get("agents") or s.get("assistants"))),
        ("channel", bool(s.get("whatsapp") or s.get("telegram") or s.get("instagram") or s.get("tg_bot"))),
        ("knowledge", bool(s.get("products") or s.get("knowledge"))),
        ("number", bool(s.get("numbers"))),
    ]
    done = sum(1 for _, ok in steps if ok)
    return {"done": done, "total": len(steps), "steps": {k: ok for k, ok in steps}}


# ───────────────────────────── список ─────────────────────────────

_BASE_COLS = """
    u.id, u.email, u.first_name, u.last_name, u.company_name, u.phone, u.business_niche,
    u.business_niche_other, u.onboarded_at, u.created_at, u.last_login, u.email_verified, u.is_active,
    u.is_admin, u.is_trial, u.subscription_end_date, u.subscription_start_date, u.wallet_balance,
    u.credits_balance, u.admin_status, u.admin_note, u.admin_next_contact_at, u.agent_subscription_blocked,
    u.agent_trial_started_at, p.code AS plan_code, p.name AS plan_name
"""


def _plan_state(row) -> Dict[str, Any]:
    now = _now()
    end = _aware(row["subscription_end_date"])
    active = bool(end and end > now)
    days_left = max(0, (end - now).days) if active else 0
    code = row["plan_code"] or None
    if row["is_admin"]:
        kind = "admin"
    elif code in TRIAL_PLANS:
        kind = "trial" if active else "trial_over"
    elif code in PAID_PLANS:
        kind = "paid" if active else "expired"
    else:
        kind = "none"
    return {"code": code, "name": row["plan_name"], "end": _iso(end), "active": active,
            "days_left": days_left, "kind": kind}


def _row_to_client(row, setup: Dict[str, Any]) -> Dict[str, Any]:
    from backend.services.niche_catalog import niche_title
    name = " ".join(x for x in [row["first_name"], row["last_name"]] if x) or None
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": name,
        "first_name": row["first_name"],
        "company_name": row["company_name"],
        "phone": row["phone"],
        "niche": row["business_niche"],
        "niche_title": niche_title(row["business_niche"], row["business_niche_other"]),
        "niche_other": row["business_niche_other"],
        "onboarded_at": _iso(row["onboarded_at"]),
        "created_at": _iso(row["created_at"]),
        "last_login": _iso(row["last_login"]),
        "email_verified": bool(row["email_verified"]),
        "is_active": bool(row["is_active"]) if row["is_active"] is not None else True,
        "is_admin": bool(row["is_admin"]),
        "blocked": bool(row["agent_subscription_blocked"]),
        "plan": _plan_state(row),
        "wallet": round((row["wallet_balance"] or 0) / 100, 2),
        "credits": int(row["credits_balance"] or 0),
        "admin_status": row["admin_status"] or "new",
        "admin_note": row["admin_note"],
        "next_contact_at": _iso(row["admin_next_contact_at"]),
        "setup": setup,
        "setup_score": setup_score(setup),
    }


def list_clients(db: Session, *, q: Optional[str] = None, segment: Optional[str] = None,
                 status: Optional[str] = None, niche: Optional[str] = None, plan: Optional[str] = None,
                 sort: str = "created_desc", offset: int = 0, limit: int = 50) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    where = _where(q, segment, status, niche, plan, params)
    order = SORTS.get(sort, SORTS["created_desc"])
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    params.update(limit=limit, offset=offset)
    rows = db.execute(text(f"""
        SELECT {_BASE_COLS}, count(*) OVER () AS total
        FROM users u LEFT JOIN subscription_plans p ON p.id = u.subscription_plan_id
        {where}
        ORDER BY {order}, u.id
        LIMIT :limit OFFSET :offset
    """), params).mappings().all()
    total = int(rows[0]["total"]) if rows else 0
    if not rows and offset:
        total = int(db.execute(text(f"""SELECT count(*) FROM users u
            LEFT JOIN subscription_plans p ON p.id = u.subscription_plan_id {where}"""),
            {k: v for k, v in params.items() if k not in ("limit", "offset")}).scalar() or 0)
    setups = _setup_for(db, [r["id"] for r in rows])
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [_row_to_client(r, setups.get(str(r["id"]), {})) for r in rows],
    }


# ───────────────────────────── сводка ─────────────────────────────

def summary(db: Session) -> Dict[str, Any]:
    now = _now()
    day_start = bishkek_day_start_utc(now)
    params = {
        "now": now, "day_start": day_start, "week_ago": now - timedelta(days=7),
        "month_ago": now - timedelta(days=30), "in3": now + timedelta(days=3),
        "day_end": day_start + timedelta(days=1), "fortnight": now - timedelta(days=14),
        "paid_plans": list(PAID_PLANS), "trial_plans": list(TRIAL_PLANS),
    }
    row = db.execute(text("""
        SELECT
          count(*) FILTER (WHERE NOT coalesce(u.is_admin, false)) AS total,
          count(*) FILTER (WHERE u.created_at >= :day_start) AS new_today,
          count(*) FILTER (WHERE u.created_at >= :week_ago) AS new_week,
          count(*) FILTER (WHERE u.created_at >= :month_ago) AS new_month,
          count(*) FILTER (WHERE (u.phone IS NULL OR u.phone = '') AND NOT coalesce(u.is_admin, false)) AS no_phone,
          count(*) FILTER (WHERE p.code = ANY(:trial_plans) AND u.subscription_end_date > :now) AS trial,
          count(*) FILTER (WHERE p.code = ANY(:paid_plans) AND u.subscription_end_date > :now) AS paying,
          count(*) FILTER (WHERE u.subscription_end_date > :now AND u.subscription_end_date <= :in3
                           AND NOT coalesce(u.is_admin, false)) AS expiring,
          count(*) FILTER (WHERE (u.admin_next_contact_at IS NOT NULL AND u.admin_next_contact_at < :day_end) OR
                           (coalesce(u.admin_status, 'new') = 'new' AND u.created_at >= :fortnight
                            AND NOT coalesce(u.is_admin, false))) AS call_today,
          count(*) FILTER (WHERE u.created_at >= :week_ago
                           AND NOT EXISTS (SELECT 1 FROM agent_configs a WHERE a.user_id = u.id)
                           AND NOT EXISTS (SELECT 1 FROM eleven_assistant_configs e WHERE e.user_id = u.id)) AS week_no_setup
        FROM users u LEFT JOIN subscription_plans p ON p.id = u.subscription_plan_id
    """), params).mappings().first() or {}

    by_status = {s["key"]: 0 for s in ADMIN_STATUSES}
    for r in db.execute(text("""SELECT coalesce(admin_status, 'new') AS s, count(*) AS n FROM users
                                WHERE NOT coalesce(is_admin, false) GROUP BY 1""")).mappings():
        by_status[r["s"]] = by_status.get(r["s"], 0) + int(r["n"])
    by_niche = [
        {"niche": r["niche"], "count": int(r["n"])}
        for r in db.execute(text("""SELECT business_niche AS niche, count(*) AS n FROM users
                                    WHERE business_niche IS NOT NULL AND business_niche <> ''
                                    GROUP BY 1 ORDER BY 2 DESC LIMIT 30""")).mappings()
    ]
    # регистрации по дням за 14 дней (по Бишкеку) — маленький график
    days = []
    try:
        for r in db.execute(text("""
            SELECT (u.created_at AT TIME ZONE 'Asia/Bishkek')::date AS d, count(*) AS n
            FROM users u WHERE u.created_at >= :since GROUP BY 1 ORDER BY 1
        """), {"since": day_start - timedelta(days=13)}).mappings():
            days.append({"date": r["d"].isoformat(), "count": int(r["n"])})
    except Exception as e:
        db.rollback()
        logger.warning(f"[ADMIN-CLIENTS] signups by day skipped: {e}")
    filled = {d["date"]: d["count"] for d in days}
    series = []
    for i in range(13, -1, -1):
        d = (day_start.astimezone(BISHKEK) - timedelta(days=i)).date().isoformat()
        series.append({"date": d, "count": filled.get(d, 0)})

    return {
        "counts": {k: int(v or 0) for k, v in dict(row).items()},
        "by_status": by_status,
        "by_niche": by_niche,
        "signups_by_day": series,
        "statuses": ADMIN_STATUSES,
    }


# ───────────────────────────── карточка ─────────────────────────────

def _messages_30d(db: Session, user_id: str) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    since = _now() - timedelta(days=30)
    queries = {
        "whatsapp": """SELECT count(*) FILTER (WHERE m.direction = 'inbound') AS inbound,
                              count(*) FILTER (WHERE m.direction <> 'inbound') AS outbound
                       FROM agent_whatsapp_messages m JOIN agent_configs a ON a.id = m.agent_config_id
                       WHERE a.user_id = CAST(:uid AS uuid) AND m.created_at >= :since""",
        "telegram": """SELECT count(*) FILTER (WHERE m.direction = 'inbound') AS inbound,
                              count(*) FILTER (WHERE m.direction <> 'inbound') AS outbound
                       FROM agent_telegram_messages m JOIN agent_contacts c ON c.id = m.agent_contact_id
                       WHERE c.user_id = CAST(:uid AS uuid) AND m.created_at >= :since""",
        "instagram": """SELECT count(*) FILTER (WHERE m.direction = 'inbound') AS inbound,
                               count(*) FILTER (WHERE m.direction <> 'inbound') AS outbound
                        FROM agent_instagram_messages m JOIN agent_configs a ON a.id = m.agent_config_id
                        WHERE a.user_id = CAST(:uid AS uuid) AND m.created_at >= :since""",
    }
    for key, sql in queries.items():
        try:
            r = db.execute(text(sql), {"uid": user_id, "since": since}).mappings().first() or {}
            out[key] = {"inbound": int(r.get("inbound") or 0), "outbound": int(r.get("outbound") or 0)}
        except Exception as e:
            db.rollback()
            logger.warning(f"[ADMIN-CLIENTS] messages {key} skipped: {e}")
    return out


def client_details(db: Session, user_id: str) -> Optional[Dict[str, Any]]:
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        return None
    row = db.execute(text(f"""
        SELECT {_BASE_COLS}
        FROM users u LEFT JOIN subscription_plans p ON p.id = u.subscription_plan_id
        WHERE u.id = :uid
    """), {"uid": uid}).mappings().first()
    if not row:
        return None
    setup = _setup_for(db, [uid]).get(str(uid), {})
    client = _row_to_client(row, setup)

    agents = []
    try:
        for a in db.execute(text("""SELECT id, name, is_active, created_at, niche_key,
                                           (telegram_bot_token IS NOT NULL AND telegram_bot_token <> '') AS tg_bot
                                    FROM agent_configs WHERE user_id = :uid ORDER BY created_at"""),
                            {"uid": uid}).mappings():
            agents.append({"id": str(a["id"]), "name": a["name"], "active": bool(a["is_active"]),
                           "created_at": _iso(a["created_at"]), "niche_key": a["niche_key"],
                           "tg_bot": bool(a["tg_bot"])})
    except Exception as e:
        db.rollback()
        logger.warning(f"[ADMIN-CLIENTS] agents skipped: {e}")

    payments = []
    try:
        for p in db.execute(text("""SELECT t.amount, t.status, t.created_at, t.paid_at, t.payment_details,
                                           sp.code AS plan_code, sp.name AS plan_name
                                    FROM payment_transactions t
                                    LEFT JOIN subscription_plans sp ON sp.id = t.plan_id
                                    WHERE t.user_id = :uid
                                    ORDER BY t.created_at DESC LIMIT 10"""), {"uid": uid}).mappings():
            kind = None
            try:
                import json as _json
                kind = (_json.loads(p["payment_details"] or "{}") or {}).get("type")
            except Exception:
                kind = None
            payments.append({"amount": float(p["amount"] or 0), "status": p["status"],
                             "plan": p["plan_code"], "plan_name": p["plan_name"],
                             "kind": "wallet" if kind == "wallet_topup" else ("plan" if p["plan_code"] else kind),
                             "created_at": _iso(p["created_at"]), "paid_at": _iso(p["paid_at"])})
    except Exception as e:
        db.rollback()
        logger.warning(f"[ADMIN-CLIENTS] payments skipped: {e}")

    timeline = []
    try:
        for t in db.execute(text("""
            SELECT l.action, l.text, l.details, l.created_at, l.ip,
                   coalesce(nullif(a.first_name, ''), a.email) AS admin_name
            FROM admin_audit_log l LEFT JOIN users a ON a.id = l.admin_user_id
            WHERE l.target_user_id = :uid ORDER BY l.created_at DESC LIMIT 60
        """), {"uid": uid}).mappings():
            timeline.append({"action": t["action"], "text": t["text"], "details": t["details"],
                             "created_at": _iso(t["created_at"]), "admin": t["admin_name"]})
    except Exception as e:
        db.rollback()
        logger.warning(f"[ADMIN-CLIENTS] timeline skipped: {e}")
    try:
        for s in db.execute(text("""SELECT action, plan_code, details, created_at FROM subscription_logs
                                    WHERE user_id = :uid ORDER BY created_at DESC LIMIT 15"""),
                            {"uid": uid}).mappings():
            timeline.append({"action": "subscription", "text": s["action"], "details": {
                "plan": s["plan_code"], "info": s["details"]}, "created_at": _iso(s["created_at"]), "admin": None})
    except Exception as e:
        db.rollback()
        logger.warning(f"[ADMIN-CLIENTS] subscription logs skipped: {e}")
    timeline.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    client.update({
        "agents": agents,
        "payments": payments,
        "messages_30d": _messages_30d(db, str(uid)),
        "timeline": timeline[:80],
    })
    return client


# ───────────────────────────── правка ─────────────────────────────

def log_action(db: Session, admin, target_id, action: str, text_value: Optional[str] = None,
               details: Optional[dict] = None, ip: Optional[str] = None) -> None:
    from backend.models.admin_audit import AdminAuditLog
    db.add(AdminAuditLog(
        admin_user_id=getattr(admin, "id", None), target_user_id=target_id, action=action,
        text=(text_value or None) and str(text_value)[:4000], details=details or None,
        ip=(ip or None) and str(ip)[:64],
    ))


def update_client(db: Session, admin, user, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Правка своих полей владельцем: статус, дата следующего звонка, заметка (в ленту),
    телефон / имя / компания / email / ниша клиента. Каждое изменение — строка в журнале.
    """
    from backend.utils.phone import normalize_phone_e164
    changed: Dict[str, Any] = {}

    if "admin_status" in data:
        st = (data.get("admin_status") or "new").strip()
        if st not in ADMIN_STATUS_KEYS:
            raise ValueError("Неизвестный статус")
        if (user.admin_status or "new") != st:
            old = user.admin_status or "new"
            user.admin_status = st
            changed["admin_status"] = st
            log_action(db, admin, user.id, "status", None, {"from": old, "to": st})

    if "next_contact_at" in data:
        raw = data.get("next_contact_at")
        if raw in (None, ""):
            value = None
        else:
            try:
                value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("Неверная дата следующего звонка")
            if value.tzinfo is None:
                value = value.replace(tzinfo=BISHKEK)
        user.admin_next_contact_at = value
        changed["next_contact_at"] = _iso(value)
        log_action(db, admin, user.id, "next_contact", None, {"at": _iso(value)})

    note = (data.get("note") or "").strip()
    if note:
        user.admin_note = note[:2000]
        changed["admin_note"] = user.admin_note
        log_action(db, admin, user.id, "note", note)

    profile_changes = {}
    if "phone" in data:
        raw = (data.get("phone") or "").strip()
        if raw:
            phone = normalize_phone_e164(raw)
            if not phone:
                raise ValueError("Неверный номер телефона")
        else:
            phone = None
        if phone != user.phone:
            profile_changes["phone"] = [user.phone, phone]
            user.phone = phone
    for field in ("first_name", "company_name"):
        if field in data:
            value = " ".join(str(data.get(field) or "").split())[:100] or None
            if value != getattr(user, field):
                profile_changes[field] = [getattr(user, field), value]
                setattr(user, field, value)
    if "email" in data:
        email = (data.get("email") or "").strip().lower()
        if email and email != (user.email or "").lower():
            import re
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,24}", email):
                raise ValueError("Неверный email")
            other = db.query(type(user)).filter(type(user).email.ilike(email), type(user).id != user.id).first()
            if other:
                raise ValueError("Этот email уже занят другим кабинетом")
            try:
                from backend.models.team import TeamMember
                if db.query(TeamMember).filter(TeamMember.email == email).first():
                    raise ValueError("Этот email занят сотрудником другого кабинета")
            except ImportError:
                pass
            profile_changes["email"] = [user.email, email]
            user.email = email
            # Почту исправил админ — считаем её подтверждённой, чтобы клиент сразу вошёл.
            user.email_verified = True
    if "niche" in data:
        from backend.services.niche_catalog import is_known_niche
        niche = (data.get("niche") or "").strip() or None
        if niche and not is_known_niche(niche):
            raise ValueError("Неизвестная ниша")
        if niche != user.business_niche:
            profile_changes["business_niche"] = [user.business_niche, niche]
            user.business_niche = niche
    if profile_changes:
        changed.update({k: v[1] for k, v in profile_changes.items()})
        log_action(db, admin, user.id, "profile", None, profile_changes)

    db.commit()
    return changed
