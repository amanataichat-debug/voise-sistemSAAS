"""
ИИ-управляющий салона (Voksy Beauty, шаг 1.6б; решения владельца 26.11.2026 — docs/voksy_beauty_plan.md).

Агент сам находит, кому написать или позвонить, и отчитывается владельцу. Каждый сценарий включается
переключателем (agent_configs.manager_settings):

  winback       — вернуть ушедших: клиент бывал, но не приходит дольше N дней (по умолчанию 60) и не
                  записан вперёд → тёплое сообщение с окнами у его мастера;
  fill_gaps     — заполнить пустые окна: у мастера завтра 3+ свободных часа → предложить их его клиентам,
                  которые давно не были;
  repeat        — повтор услуги и отзыв: у услуги «повторять через N дней» (маникюр — 21) → напомнить
                  к сроку и предложить окна; после визита «спасибо» + просьба оценить 1–5 (оценку
                  сохраняет save_review, 1–3 — сразу владельцу);
  morning_report / weekly_report — отчёт владельцу в 9:00 (недельный — по понедельникам): в Telegram,
                  в приложение (owner_reports → главная и «ИИ-управляющий») и push на телефон.

Автономность (autonomy): «auto» — агент делает сам и отчитывается; «approve» — действия создаются
со статусом proposed, владелец одобряет их в приложении (или все сразу), только потом агент пишет/звонит.

Каналы: клиент писал в мессенджер → сообщение (пишет оркестратор под клиента, событие followup_outbound
c followup_kind="salon", пауза бота и правила переписки действуют как обычно); мессенджера нет, но номер
O! и тариф со звонками → звонок (задача агента, обычные проверки звонка). Никакого — клиента пропускаем.
Клиентские сценарии — только автономный агент (Beauty Pro / Profi: agent_mode == "full"); отчёты —
всем с онлайн-записью. Защита от спама: писать 10:00–20:00 Бишкек, одному клиенту не чаще раза в
CONTACT_COOLDOWN_DAYS, «вернуть» — не чаще раза в WINBACK_COOLDOWN_DAYS, общий лимит в день, клиентов
на паузе, «не звонить» и с будущей записью не трогаем.

Тик — weekly_jobs (раз в 5 минут, фоновый loop): отчёты по часам, планирование в 10:00 (claim_agent_job —
один воркер), исполнение одобренных — каждым тиком под атомарной бронью строки.
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

WORK_HOURS = (10, 20)                 # пишем и звоним клиентам только в это время (Бишкек)
PLAN_HOUR = 10                        # когда составляем действия на день
CONTACT_COOLDOWN_DAYS = 7             # одному клиенту от сценариев — не чаще
WINBACK_COOLDOWN_DAYS = 30
EXEC_PER_TICK = 5
PROPOSAL_TTL_DAYS = 3                 # неодобренное предложение старше — снимается
FILL_GAPS_MIN_FREE = 3                # свободных часов у мастера завтра, чтобы звать клиентов
REVIEW_LOW = 3                        # оценка 1–3 — сразу владельцу

SCENARIOS = {
    "winback": "Вернуть ушедших клиентов",
    "fill_gaps": "Заполнить пустые окна",
    "repeat": "Повтор услуги",
}
DEFAULTS: Dict[str, Any] = {
    "autonomy": "auto",               # auto | approve
    "winback": {"on": False, "days": 60, "per_day": 8},
    "fill_gaps": {"on": False, "per_day": 5},
    "repeat_review": {"on": False},
    "morning_report": {"on": True, "hour": 9},
    "weekly_report": {"on": True},
    "max_per_day": 20,
}
STATUS_TITLES = {"proposed": "Ждёт одобрения", "approved": "Одобрено", "running": "Выполняется",
                 "done": "Сделано", "failed": "Не получилось", "rejected": "Отклонено", "skipped": "Пропущено"}


# ───────────────────────────── настройки ─────────────────────────────

def settings_of(agent) -> Dict[str, Any]:
    out = copy.deepcopy(DEFAULTS)
    raw = getattr(agent, "manager_settings", None) if agent is not None else None
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k].update({kk: vv for kk, vv in v.items() if kk in out[k]})
            elif k in out and not isinstance(out[k], dict):
                out[k] = v
    if out["autonomy"] not in ("auto", "approve"):
        out["autonomy"] = "auto"
    return out


def _int(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(int(v), hi))
    except (TypeError, ValueError):
        return default


def clean_settings(current: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """Правка настроек из приложения: только известные поля, числа в разумных пределах."""
    out = copy.deepcopy(current)
    data = data or {}
    if data.get("autonomy") in ("auto", "approve"):
        out["autonomy"] = data["autonomy"]
    for key in ("winback", "fill_gaps", "repeat_review", "morning_report", "weekly_report"):
        v = data.get(key)
        if isinstance(v, dict) and "on" in v:
            out[key]["on"] = bool(v["on"])
    wb = data.get("winback") or {}
    if "days" in wb:
        out["winback"]["days"] = _int(wb["days"], 21, 365, 60)
    if "per_day" in wb:
        out["winback"]["per_day"] = _int(wb["per_day"], 1, 50, 8)
    fg = data.get("fill_gaps") or {}
    if "per_day" in fg:
        out["fill_gaps"]["per_day"] = _int(fg["per_day"], 1, 30, 5)
    mr = data.get("morning_report") or {}
    if "hour" in mr:
        out["morning_report"]["hour"] = _int(mr["hour"], 6, 12, 9)
    if "max_per_day" in data:
        out["max_per_day"] = _int(data["max_per_day"], 1, 100, 20)
    return out


def can_run_scenarios(user) -> bool:
    """Клиентские сценарии — только автономный агент (Beauty Pro / Profi)."""
    try:
        return user is not None and user.agent_mode() == "full"
    except Exception:
        return False


def booking_on(db: Session, user) -> bool:
    from backend.services.booking_tools import booking_enabled
    return booking_enabled(db, user)


# ───────────────────────────── время ─────────────────────────────

def now_local(now: Optional[datetime] = None) -> datetime:
    return bs.to_local(now or bs.now_utc())


def in_work_hours(now: Optional[datetime] = None) -> bool:
    return WORK_HOURS[0] <= now_local(now).hour < WORK_HOURS[1]


def _day_bounds(day: date) -> Tuple[datetime, datetime]:
    return bs.day_bounds(day)


# ───────────────────────────── кто подходит ─────────────────────────────

def _recent_action_contacts(db: Session, user_id, days: int, scenario: Optional[str] = None) -> set:
    sql = ("SELECT DISTINCT contact_id FROM manager_actions WHERE user_id = :uid AND contact_id IS NOT NULL "
           "AND created_at > now() - make_interval(days => :d) AND status NOT IN ('rejected')")
    params: Dict[str, Any] = {"uid": str(user_id), "d": int(days)}
    if scenario:
        sql += " AND scenario = :sc"
        params["sc"] = scenario
    return {str(r[0]) for r in db.execute(text(sql), params).fetchall()}


def _has_upcoming(db: Session, user_id, contact_id) -> bool:
    return bool(db.execute(text(
        "SELECT 1 FROM bookings WHERE user_id = :uid AND contact_id = :cid AND status IN ('booked','confirmed') "
        "AND start_at > now() LIMIT 1"), {"uid": str(user_id), "cid": str(contact_id)}).first())


def _contact_ok(db: Session, contact) -> bool:
    from backend.services import agent_pause
    if contact is None:
        return False
    if (contact.status or "") in ("do_not_call", "rejected", "lost"):
        return False
    try:
        return not agent_pause.is_paused(contact)
    except Exception:
        return True


def channel_for(db: Session, user, agent, contact) -> Optional[str]:
    """'message' — клиент есть в мессенджере, 'call' — можно позвонить (O!, тариф), None — некуда."""
    from backend.services.agent_tools import _detect_recent_channel, agent_can_call_contact
    try:
        if _detect_recent_channel(db, str(agent.id), str(contact.id)):
            return "message"
    except Exception:
        db.rollback()
    try:
        ok, _ = agent_can_call_contact(db, user, contact)
        return "call" if ok else None
    except Exception:
        db.rollback()
        return None


def _resource_by_name(db: Session, user_id, name: Optional[str]):
    if not name:
        return None
    for r in bs.resources(db, user_id):
        if r.name == name:
            return r
    return None


SLOT_GAP = timedelta(hours=3)      # окна клиенту — разные (утро и вечер), а не 09:00 и 09:30


def _slots(db: Session, user, service, resource, day: Optional[date] = None, count: int = 2,
           only_day: bool = False) -> List[Dict[str, Any]]:
    try:
        rows = bs.nearest_slots(db, user, service, resource.id if resource else None,
                                day.isoformat() if day else None, count=40)
    except Exception as e:
        logger.warning(f"[MANAGER] slots: {e}")
        return []
    if only_day and day is not None:
        rows = [s for s in rows if bs.to_local(bs.parse_start(s["start"])).date() == day]
    picked: List[Dict[str, Any]] = []
    for s in rows:
        t = bs.parse_start(s["start"])
        if all(abs(t - bs.parse_start(p["start"])) >= SLOT_GAP for p in picked):
            picked.append(s)
        if len(picked) >= count:
            break
    if len(picked) < count:            # широких окон нет — берём ближайшие, какие есть
        picked += [s for s in rows if s not in picked][:count - len(picked)]
    picked.sort(key=lambda s: s["start"])
    return [{"start": s["start"], "label": s["label"], "master": s.get("resource")} for s in picked[:count]]


# ───────────────────────────── кандидаты ─────────────────────────────

def winback_candidates(db: Session, user, agent, cfg: Dict[str, Any], skip: set) -> List[Dict[str, Any]]:
    from backend.models.agent_contact import AgentContact
    from backend.services.salon_ai_tools import lapsed_clients
    days, limit = cfg["winback"]["days"], cfg["winback"]["per_day"]
    skip = skip | _recent_action_contacts(db, user.id, WINBACK_COOLDOWN_DAYS, "winback")
    out = []
    tomorrow = now_local().date() + timedelta(days=1)
    for row in lapsed_clients(db, user, days, limit=limit * 4):
        cid = row["agent_contact_id"]
        if cid in skip:
            continue
        contact = db.query(AgentContact).filter(AgentContact.id == cid).first()
        if not _contact_ok(db, contact):
            continue
        kind = channel_for(db, user, agent, contact)
        if not kind:
            continue
        service = bs.find_service(db, user.id, None, row["last_service"]) if row.get("last_service") else None
        res = _resource_by_name(db, user.id, row.get("last_master"))
        slots = _slots(db, user, service, res, tomorrow) or _slots(db, user, service, None, tomorrow)
        out.append({"contact": contact, "scenario": "winback", "kind": kind, "payload": {
            "reason": f"не приходит {row['days_since']} {_plural(row['days_since'], 'день', 'дня', 'дней')}"
                      + (f"; последний раз — «{row['last_service']}»" if row.get("last_service") else "")
                      + (f" у мастера {row['last_master']}" if row.get("last_master") else ""),
            "days_since": row["days_since"], "visits": row["visits"], "service": row.get("last_service"),
            "service_id": str(service.id) if service else None, "master": row.get("last_master"),
            "resource_id": str(res.id) if res else None, "slots": slots}})
        skip.add(cid)
        if len(out) >= limit:
            break
    return out


def repeat_candidates(db: Session, user, agent, cfg: Dict[str, Any], skip: set, limit: int = 20) -> List[Dict[str, Any]]:
    from backend.models.agent_contact import AgentContact
    rows = db.execute(text("""
        SELECT DISTINCT ON (b.contact_id, b.service_entry_id) b.id, b.contact_id, b.service_entry_id, b.resource_id,
               b.start_at, e.repeat_days, e.title
        FROM bookings b JOIN agent_knowledge_entries e ON e.id = b.service_entry_id
        WHERE b.user_id = :uid AND b.contact_id IS NOT NULL AND b.status IN ('arrived', 'completed')
          AND e.repeat_days > 0 AND b.start_at > now() - interval '400 days'
        ORDER BY b.contact_id, b.service_entry_id, b.start_at DESC
    """), {"uid": str(user.id)}).fetchall()
    done = {str(r[0]) for r in db.execute(text(
        "SELECT payload->>'booking_id' FROM manager_actions WHERE user_id = :uid AND scenario = 'repeat'"),
        {"uid": str(user.id)}).fetchall() if r[0]}
    now = bs.now_utc()
    names = {str(r.id): r.name for r in bs.resources(db, user.id, active_only=False)}
    out = []
    for bid, cid, sid, rid, start, rdays, title in rows:
        if str(bid) in done or str(cid) in skip:
            continue
        due = bs._aw(start) + timedelta(days=int(rdays))
        if not (due - timedelta(days=2) <= now <= due + timedelta(days=21)):
            continue
        if _has_upcoming(db, user.id, cid):
            continue
        contact = db.query(AgentContact).filter(AgentContact.id == cid).first()
        if not _contact_ok(db, contact):
            continue
        kind = channel_for(db, user, agent, contact)
        if not kind:
            continue
        service = bs.find_service(db, user.id, sid)
        res = next((r for r in bs.resources(db, user.id) if str(r.id) == str(rid)), None)
        day = max(now_local().date() + timedelta(days=1), bs.to_local(due).date())
        slots = _slots(db, user, service, res, day) or _slots(db, user, service, None, day)
        out.append({"contact": contact, "scenario": "repeat", "kind": kind, "payload": {
            "reason": f"«{title}» — {(now - bs._aw(start)).days} дн. назад, обычно повторяют через {rdays} дн.",
            "booking_id": str(bid), "service": title, "service_id": str(sid), "repeat_days": int(rdays),
            "master": names.get(str(rid)), "resource_id": str(rid) if rid else None, "slots": slots}})
        skip.add(str(cid))
        if len(out) >= limit:
            break
    return out


def fill_gaps_candidates(db: Session, user, agent, cfg: Dict[str, Any], skip: set, *, day: Optional[date] = None,
                         resource=None, min_free: int = FILL_GAPS_MIN_FREE,
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Давние клиенты мастеров, у которых в день day (по умолчанию завтра) свободно min_free+ часов.
    resource — только этот мастер (кнопка «Предложить клиентам» на главной салона)."""
    from backend.models.agent_contact import AgentContact
    limit = limit or cfg["fill_gaps"]["per_day"]
    day = day or (now_local().date() + timedelta(days=1))
    per_master_max = limit if resource is not None else 2
    out: List[Dict[str, Any]] = []
    for r in ([resource] if resource is not None else bs.resources(db, user.id)):
        if not bs.bookable_online(r):
            continue
        try:
            free = bs.free_slots(db, user, None, r.id, day.isoformat(), 1, online_only=True)
        except Exception:
            continue
        starts = (free[0]["slots"] if free else [])
        hours = len({s["time"][:2] for s in starts})
        if hours < min_free:
            continue
        rows = db.execute(text("""
            SELECT b.contact_id, max(b.start_at) AS last_at, count(*) AS visits,
                   (array_agg(b.service_entry_id ORDER BY b.start_at DESC))[1] AS last_service
            FROM bookings b
            WHERE b.user_id = :uid AND b.resource_id = :rid AND b.contact_id IS NOT NULL
              AND b.status IN ('arrived', 'completed')
            GROUP BY b.contact_id
            HAVING max(b.start_at) < now() - interval '14 days'
            ORDER BY count(*) DESC, max(b.start_at) ASC LIMIT 20
        """), {"uid": str(user.id), "rid": str(r.id)}).fetchall()
        per_master = 0
        for cid, last_at, visits, last_sid in rows:
            if str(cid) in skip or _has_upcoming(db, user.id, cid):
                continue
            contact = db.query(AgentContact).filter(AgentContact.id == cid).first()
            if not _contact_ok(db, contact):
                continue
            kind = channel_for(db, user, agent, contact)
            if not kind:
                continue
            service = bs.find_service(db, user.id, last_sid) if last_sid else None
            slots = _slots(db, user, service, r, day, only_day=True)
            if not slots:
                continue
            when_word = "сегодня" if day == now_local().date() else "завтра" if day == now_local().date() + timedelta(days=1) \
                else day.strftime("%d.%m")
            out.append({"contact": contact, "scenario": "fill_gaps", "kind": kind, "payload": {
                "reason": f"у мастера {r.name} {when_word} свободно {hours} ч, клиент был у него {_times(int(visits))}",
                "day": day.isoformat(), "service": service.title if service else None,
                "service_id": str(service.id) if service else None, "master": r.name, "resource_id": str(r.id),
                "slots": slots}})
            skip.add(str(cid))
            per_master += 1
            if per_master >= per_master_max or len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return out


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def _times(n: int) -> str:
    if 11 <= n % 100 <= 14 or n % 10 in (0, 1, 5, 6, 7, 8, 9):
        return f"{n} раз"
    return f"{n} раза"


# ───────────────────────────── планирование ─────────────────────────────

def _today_count(db: Session, user_id) -> int:
    start, _ = _day_bounds(now_local().date())
    return int(db.execute(text("SELECT count(*) FROM manager_actions WHERE user_id = :uid AND created_at >= :s"),
                          {"uid": str(user_id), "s": start}).scalar() or 0)


def plan_actions(db: Session, user, agent) -> List[Any]:
    """Найти кандидатов по включённым сценариям и завести действия (auto → approved, approve → proposed)."""
    from backend.models.salon_manager import ManagerAction
    cfg = settings_of(agent)
    if not can_run_scenarios(user) or not booking_on(db, user):
        return []
    room = cfg["max_per_day"] - _today_count(db, user.id)
    if room <= 0:
        return []
    skip = _recent_action_contacts(db, user.id, CONTACT_COOLDOWN_DAYS)
    found: List[Dict[str, Any]] = []
    if cfg["repeat_review"]["on"]:
        found += repeat_candidates(db, user, agent, cfg, skip)
    if cfg["fill_gaps"]["on"]:
        found += fill_gaps_candidates(db, user, agent, cfg, skip)
    if cfg["winback"]["on"]:
        found += winback_candidates(db, user, agent, cfg, skip)
    status = "approved" if cfg["autonomy"] == "auto" else "proposed"
    now = bs.now_utc()
    rows = []
    for c in found[:room]:
        a = ManagerAction(id=uuid.uuid4(), user_id=user.id, agent_config_id=agent.id, contact_id=c["contact"].id,
                          scenario=c["scenario"], kind=c["kind"], status=status, payload=c["payload"],
                          created_at=now, decided_at=now if status == "approved" else None)
        db.add(a)
        rows.append(a)
    # Неодобренные старые предложения больше не актуальны
    db.execute(text("UPDATE manager_actions SET status = 'skipped', result = 'Не одобрено вовремя', done_at = now() "
                    "WHERE user_id = :uid AND status = 'proposed' AND created_at < now() - make_interval(days => :d)"),
               {"uid": str(user.id), "d": PROPOSAL_TTL_DAYS})
    db.commit()
    return rows


def action_to_dict(a, contact=None) -> Dict[str, Any]:
    p = a.payload or {}
    return {"id": str(a.id), "scenario": a.scenario, "scenario_title": SCENARIOS.get(a.scenario, a.scenario),
            "kind": a.kind, "status": a.status, "status_title": STATUS_TITLES.get(a.status, a.status),
            "contact_id": str(a.contact_id) if a.contact_id else None,
            "contact_name": (contact.name if contact is not None else None) or "Клиент",
            "contact_phone": contact.phone if contact is not None and not str(contact.phone or "").startswith(("web:", "tg:", "ig:", "wa:")) else None,
            "reason": p.get("reason"), "service": p.get("service"), "master": p.get("master"),
            "slots": p.get("slots") or [], "result": a.result,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "done_at": a.done_at.isoformat() if a.done_at else None}


def list_actions(db: Session, user_id, statuses: Optional[List[str]] = None, days: int = 14,
                 limit: int = 200) -> List[Dict[str, Any]]:
    from backend.models.agent_contact import AgentContact
    from backend.models.salon_manager import ManagerAction
    q = db.query(ManagerAction).filter(ManagerAction.user_id == user_id,
                                       ManagerAction.created_at >= bs.now_utc() - timedelta(days=days))
    if statuses:
        q = q.filter(ManagerAction.status.in_(statuses))
    rows = q.order_by(ManagerAction.created_at.desc()).limit(limit).all()
    ids = [r.contact_id for r in rows if r.contact_id]
    contacts = {c.id: c for c in db.query(AgentContact).filter(AgentContact.id.in_(ids)).all()} if ids else {}
    return [action_to_dict(r, contacts.get(r.contact_id)) for r in rows]


def decide(db: Session, user_id, ids: Optional[List[str]], approve: bool, user=None) -> int:
    """Одобрить / отклонить предложения владельца (ids=None — все, что ждут). user — для журнала действий."""
    params: Dict[str, Any] = {"uid": str(user_id), "st": "approved" if approve else "rejected"}
    sql = "UPDATE manager_actions SET status = :st, decided_at = now() WHERE user_id = :uid AND status = 'proposed'"
    if ids is not None:
        clean = []
        for x in ids:
            try:
                clean.append(str(uuid.UUID(str(x))))
            except ValueError:
                pass
        if not clean:
            return 0
        sql += " AND id = ANY(CAST(:ids AS uuid[]))"
        params["ids"] = clean
    rows = db.execute(text(sql + " RETURNING id, contact_id, scenario"), params).fetchall()
    if rows:
        from backend.services import activity
        from backend.models.agent_contact import AgentContact
        cids = [r[1] for r in rows if r[1]]
        names = {c.id: c.name for c in db.query(AgentContact).filter(AgentContact.id.in_(cids)).all()} if cids else {}
        who = [f"{names.get(r[1]) or 'клиент'} ({SCENARIOS.get(r[2], r[2]).lower()})" for r in rows[:6]]
        activity.log(db, user_id, "manager.approve" if approve else "manager.reject", "manager",
                     f"{len(rows)} {_plural(len(rows), 'предложение', 'предложения', 'предложений')}: " + ", ".join(who)
                     + (" …" if len(rows) > 6 else ""), meta={"ids": [str(r[0]) for r in rows]}, user=user)
    db.commit()
    return len(rows)


# ───────────────────────────── исполнение ─────────────────────────────

def _claim_batch(db: Session, limit: int = EXEC_PER_TICK) -> List[str]:
    rows = db.execute(text("""
        UPDATE manager_actions SET status = 'running'
        WHERE id IN (SELECT id FROM manager_actions WHERE status = 'approved'
                     ORDER BY created_at LIMIT :lim FOR UPDATE SKIP LOCKED)
        RETURNING id
    """), {"lim": int(limit)}).fetchall()
    db.commit()
    return [str(r[0]) for r in rows]


def _slots_text(slots: List[Dict[str, Any]]) -> str:
    parts = []
    for s in slots or []:
        parts.append(s["label"] + (f" ({s['master']})" if s.get("master") else ""))
    return ", ".join(parts)


INSTRUCTIONS = {
    "winback": ("Клиент давно не был у нас: {reason}. Напиши ему тепло и коротко, как знакомому: без давления и "
                "без шаблонного «мы скучаем». Предложи записаться снова{master_part}. Свободное время: {slots}. "
                "Скидок и подарков, которых нет в базе знаний, не обещай. Согласится — запиши через "
                "create_booking (в два шага: итог → его «да»)."),
    "fill_gaps": ("У мастера {master} завтра есть свободное время: {slots}. Клиент раньше ходил к нему. "
                  "Коротко предложи это время на «{service}», одним сообщением, без давления. "
                  "Согласится — запиши через create_booking (итог → его «да»)."),
    "repeat": ("Пора повторить услугу: {reason}. Напомни клиенту одной-двумя фразами и предложи записаться"
               "{master_part}. Свободное время: {slots}. Согласится — запиши через create_booking (итог → его «да»)."),
}


def build_instruction(a, fresh_slots: Optional[List[Dict[str, Any]]] = None) -> str:
    p = a.payload or {}
    slots = fresh_slots if fresh_slots is not None else (p.get("slots") or [])
    master = p.get("master")
    tpl = INSTRUCTIONS.get(a.scenario, INSTRUCTIONS["winback"])
    return tpl.format(reason=p.get("reason") or "", master=master or "мастер",
                      master_part=(f" к мастеру {master}" if master else ""),
                      service=p.get("service") or "услугу",
                      slots=_slots_text(slots) or "уточни у клиента удобный день и найди время через find_booking_slots")


async def _run_message(db: Session, a, agent, contact, user, instruction: str) -> str:
    from backend.models.agent_call import AgentCall
    from backend.services.agent_orchestrator import PostCallOrchestrator
    from backend.services.agent_tools import _detect_recent_channel
    from backend.services.booking_confirm import messages_since
    channel = _detect_recent_channel(db, str(agent.id), str(contact.id)) or "whatsapp"
    started = bs.now_utc()
    call = AgentCall(agent_contact_id=contact.id, agent_config_id=agent.id, user_id=user.id, status="calling",
                     direction="outbound", started_at=started,
                     postcall_log={"call_direction": "followup_outbound", "channel": channel, "followup_kind": "salon",
                                   "salon_scenario": a.scenario, "manager_action_id": str(a.id)})
    db.add(call)
    db.commit()
    await PostCallOrchestrator()._analyze_v3_openrouter(
        agent_call=call, agent_contact=contact, agent_config=agent, user=user, task=None,
        transcript=f"[Канал переписки: {channel}] [Сценарий: {SCENARIOS.get(a.scenario)}] " + instruction,
        call_status="answered", duration_seconds=0, db=db, call_direction="followup_outbound")
    sent = [m for m in messages_since(db, contact.id, started) if m["direction"] == "outbound"]
    names = {"whatsapp": "WhatsApp", "telegram": "Telegram", "instagram": "Instagram"}
    if not sent:
        raise RuntimeError("агент решил не писать")
    return f"Написал в {names.get(channel, channel)}: «{(sent[-1]['body'] or '')[:160]}»"


async def _run_call(db: Session, a, agent, contact, user, instruction: str) -> str:
    from backend.services.agent_tools import fn_create_agent_task
    res = await fn_create_agent_task({"agent_contact_id": str(contact.id), "delay_minutes": 2,
                                      "title": f"ИИ-управляющий: {SCENARIOS.get(a.scenario)}",
                                      "notes": instruction}, str(user.id), str(agent.id), db)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "звонок не поставлен")
    return "Поставил звонок клиенту" + (f" на {res['scheduled_local']}" if res.get("scheduled_local") else "")


async def execute_action(db: Session, action_id: str) -> Optional[str]:
    """Выполнить одно одобренное действие. Возвращает итоговый статус."""
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact
    from backend.models.salon_manager import ManagerAction
    from backend.models.user import User
    a = db.query(ManagerAction).filter(ManagerAction.id == uuid.UUID(action_id)).first()
    if a is None:
        return None

    def finish(status: str, result: str) -> str:
        a.status, a.result, a.done_at = status, result[:500], bs.now_utc()
        db.commit()
        return status

    user = db.query(User).filter(User.id == a.user_id).first()
    agent = db.query(AgentConfig).filter(AgentConfig.id == a.agent_config_id).first()
    contact = db.query(AgentContact).filter(AgentContact.id == a.contact_id).first() if a.contact_id else None
    if user is None or agent is None or contact is None:
        return finish("skipped", "Клиент или агент удалён")
    if not can_run_scenarios(user) or not agent.is_active:
        return finish("skipped", "Агент выключен или тариф без автономного агента")
    if not _contact_ok(db, contact):
        return finish("skipped", "Клиент на паузе или в «не звонить»")
    if _has_upcoming(db, user.id, contact.id):
        return finish("skipped", "Клиент уже записался сам")
    p = a.payload or {}
    fresh = None
    if p.get("slots"):
        service = bs.find_service(db, user.id, p["service_id"]) if p.get("service_id") else None
        res = next((r for r in bs.resources(db, user.id) if str(r.id) == str(p.get("resource_id"))), None)
        day = date.fromisoformat(p["day"]) if p.get("day") else now_local().date() + timedelta(days=1)
        if p.get("day") and day < now_local().date():
            return finish("skipped", "Окно уже прошло")
        fresh = _slots(db, user, service, res, day, only_day=bool(p.get("day")))
        if not fresh and a.scenario == "fill_gaps":
            return finish("skipped", "Свободное время уже заняли")
    instruction = build_instruction(a, fresh)
    try:
        result = await (_run_message if a.kind == "message" else _run_call)(db, a, agent, contact, user, instruction)
    except Exception as e:
        db.rollback()
        logger.warning(f"[MANAGER] action {action_id} failed: {e}")
        a = db.query(ManagerAction).filter(ManagerAction.id == uuid.UUID(action_id)).first()
        return finish("failed", str(e)[:300])
    return finish("done", result)


async def execute_due(db: Session, now: Optional[datetime] = None) -> int:
    if not in_work_hours(now):
        return 0
    n = 0
    for aid in _claim_batch(db):
        try:
            if await execute_action(db, aid) == "done":
                n += 1
        except Exception as e:
            db.rollback()
            logger.error(f"[MANAGER] execute {aid}: {e}", exc_info=True)
    return n


# ───────────────────────────── отзывы ─────────────────────────────

def review_on(agent) -> bool:
    return bool(settings_of(agent)["repeat_review"]["on"])


def review_text(b, resource_name: Optional[str]) -> str:
    name = (b.client_name or "").strip().split()
    first = name[0] if name else ""
    who = f" у мастера {resource_name}" if resource_name else ""
    return (f"{first + ', с' if first else 'С'}пасибо, что пришли к нам! Оцените, пожалуйста, визит{who} "
            "от 1 до 5 — просто ответьте цифрой. Нам очень важно ваше мнение.")


def save_review(db: Session, user, contact_id, score, comment: Optional[str] = None) -> Dict[str, Any]:
    """Оценка визита из переписки → отзыв (шаг 2.3: reviews_service — публикация 4–5, проверка 1–3, владельцу)."""
    from backend.services import reviews_service
    return reviews_service.save_from_chat(db, user, contact_id, score, comment)


def review_hint(db: Session, user_id, contact_id) -> str:
    """Строка для разбора переписки: клиента просили оценить визит — ответ цифрой сохранить."""
    try:
        row = db.execute(text(
            "SELECT service_title FROM bookings WHERE user_id = :uid AND contact_id = :cid AND review_asked_at IS NOT NULL "
            "AND review_score IS NULL AND review_asked_at > now() - interval '3 days' ORDER BY review_asked_at DESC LIMIT 1"),
            {"uid": str(user_id), "cid": str(contact_id)}).first()
        if row is None:
            return ""
        return ("\n\nЖДЁМ ОЦЕНКУ ВИЗИТА" + (f" («{row[0]}»)" if row[0] else "") + ": клиента попросили оценить визит "
                "от 1 до 5. Ответил цифрой или словами вроде «всё отлично» — сохрани через save_review (оценку и "
                "комментарий), потом ответь по полю next.")
    except Exception as e:
        logger.warning(f"[MANAGER] review hint: {e}")
        db.rollback()
        return ""


# ───────────────────────────── отчёты ─────────────────────────────

def _money(v) -> str:
    return bs.price_text(round(float(v or 0)))


def _visited_stats(db: Session, user_id, a: datetime, b: datetime) -> Dict[str, Any]:
    r = db.execute(text("""
        SELECT count(*) FILTER (WHERE status IN ('arrived','completed')),
               count(*) FILTER (WHERE status = 'no_show'),
               COALESCE(sum(price) FILTER (WHERE status IN ('arrived','completed')), 0),
               avg(review_score) FILTER (WHERE review_at >= :a AND review_at < :b),
               count(review_score) FILTER (WHERE review_at >= :a AND review_at < :b)
        FROM bookings WHERE user_id = :uid AND start_at >= :a AND start_at < :b
    """), {"uid": str(user_id), "a": a, "b": b}).first()
    came, no_show, revenue, avg_review, n_review = r
    names = [x[0] for x in db.execute(text(
        "SELECT COALESCE(client_name, 'клиент') FROM bookings WHERE user_id = :uid AND status = 'no_show' "
        "AND start_at >= :a AND start_at < :b ORDER BY start_at LIMIT 5"),
        {"uid": str(user_id), "a": a, "b": b}).fetchall()]
    return {"came": int(came or 0), "no_show": int(no_show or 0), "no_show_names": names,
            "revenue": float(revenue or 0), "avg_check": (float(revenue) / came) if came else None,
            "review_avg": round(float(avg_review), 1) if avg_review else None, "review_n": int(n_review or 0)}


def _agent_stats(db: Session, user_id, a: datetime, b: datetime) -> Dict[str, Any]:
    r = db.execute(text("""
        SELECT count(*) FILTER (WHERE status = 'done' AND kind = 'message'),
               count(*) FILTER (WHERE status = 'done' AND kind = 'call'),
               count(*) FILTER (WHERE status = 'proposed')
        FROM manager_actions WHERE user_id = :uid AND COALESCE(done_at, created_at) >= :a AND COALESCE(done_at, created_at) < :b
    """), {"uid": str(user_id), "a": a, "b": b}).first()
    booked = db.execute(text(
        "SELECT count(*) FROM bookings WHERE user_id = :uid AND source IN ('bot', 'call') AND created_at >= :a "
        "AND created_at < :b AND status <> 'cancelled'"), {"uid": str(user_id), "a": a, "b": b}).scalar()
    returned = db.execute(text("""
        SELECT count(DISTINCT b.contact_id) FROM bookings b JOIN manager_actions m ON m.contact_id = b.contact_id
        WHERE b.user_id = :uid AND b.created_at >= :a AND b.created_at < :b AND b.status <> 'cancelled'
          AND m.status = 'done' AND m.done_at <= b.created_at AND m.done_at > b.created_at - interval '7 days'
    """), {"uid": str(user_id), "a": a, "b": b}).scalar()
    return {"messages": int(r[0] or 0), "calls": int(r[1] or 0), "booked_by_bot": int(booked or 0),
            "booked_after_manager": int(returned or 0)}


def _pending(db: Session, user_id) -> int:
    return int(db.execute(text("SELECT count(*) FROM manager_actions WHERE user_id = :uid AND status = 'proposed'"),
                          {"uid": str(user_id)}).scalar() or 0)


def _today_block(db: Session, user, day: date) -> Dict[str, Any]:
    a, b = _day_bounds(day)
    names = {str(r.id): r.name for r in bs.resources(db, user.id, active_only=False)}
    rows = [x for x in bs.list_bookings(db, user.id, a, b, include_cancelled=False)]
    by_master: Dict[str, Dict[str, Any]] = {}
    for x in rows:
        key = names.get(str(x.resource_id)) or "Без мастера"
        m = by_master.setdefault(key, {"name": key, "count": 0, "first": None})
        m["count"] += 1
        hm = bs.to_local(x.start_at).strftime("%H:%M")
        m["first"] = m["first"] or hm
    free = []
    for r in bs.resources(db, user.id):
        try:
            days = bs.free_slots(db, user, None, r.id, day.isoformat(), 1, online_only=False, ignore_lead=False)
        except Exception:
            continue
        times = [s["time"] for s in (days[0]["slots"] if days else [])]
        hours = sorted({t[:2] for t in times})
        if hours:
            free.append({"master": r.name, "hours": len(hours), "times": [t for t in times if t.endswith(":00")][:4]})
    return {"count": len(rows), "first": bs.to_local(rows[0].start_at).strftime("%H:%M") if rows else None,
            "by_master": sorted(by_master.values(), key=lambda m: -m["count"]),
            "expected": sum(float(x.price or 0) for x in rows), "free": free}


def build_morning(db: Session, user, agent, day: date) -> Tuple[str, str, Dict[str, Any]]:
    today = _today_block(db, user, day)
    y0, y1 = _day_bounds(day - timedelta(days=1))
    yest = _visited_stats(db, user.id, y0, y1)
    ag = _agent_stats(db, user.id, y0, y1)
    pending = _pending(db, user.id)
    data = {"today": today, "yesterday": yest, "agent": ag, "pending": pending}
    lines = [f"☀️ Доброе утро! Отчёт на {day.day:02d}.{day.month:02d}", ""]
    if today["count"]:
        lines.append(f"Сегодня записей: {today['count']}, первая в {today['first']}"
                     + (f", ожидаем {_money(today['expected'])}" if today["expected"] else ""))
        for m in today["by_master"][:6]:
            lines.append(f"  • {m['name']}: {m['count']} (с {m['first']})")
    else:
        lines.append("Сегодня записей пока нет.")
    if today["free"]:
        lines.append("Свободно: " + "; ".join(f"{f['master']} — {f['hours']} ч" for f in today["free"][:5]))
    lines += ["", f"Вчера: пришли {yest['came']}, не пришли {yest['no_show']}"
              + (f" ({', '.join(yest['no_show_names'])})" if yest["no_show_names"] else "")
              + f", выручка {_money(yest['revenue'])}"
              + (f", средний чек {_money(yest['avg_check'])}" if yest["avg_check"] else "")]
    if yest["review_n"]:
        lines.append(f"Оценки клиентов: {yest['review_avg']} из 5 ({yest['review_n']})")
    if ag["messages"] or ag["calls"] or ag["booked_by_bot"]:
        lines.append(f"ИИ-управляющий вчера: написал {ag['messages']}, позвонил {ag['calls']}; "
                     f"бот записал {ag['booked_by_bot']}"
                     + (f", вернулись после его сообщений {ag['booked_after_manager']}" if ag["booked_after_manager"] else ""))
    if pending:
        lines.append(f"\nЖдут вашего одобрения: {pending} — откройте «ИИ-управляющий» в приложении.")
    title = (f"Сегодня {today['count']} {_plural(today['count'], 'запись', 'записи', 'записей')}"
             + (f", вчера {_money(yest['revenue'])}" if yest["revenue"] else ""))
    return title, "\n".join(lines), data


def build_weekly(db: Session, user, agent, day: date) -> Tuple[str, str, Dict[str, Any]]:
    from backend.services.salon_ai_tools import lapsed_clients
    a, _ = _day_bounds(day - timedelta(days=7))
    b, _ = _day_bounds(day)
    pa, _ = _day_bounds(day - timedelta(days=14))
    cur, prev = _visited_stats(db, user.id, a, b), _visited_stats(db, user.id, pa, a)
    ag = _agent_stats(db, user.id, a, b)
    masters = db.execute(text("""
        SELECT r.name, count(*), COALESCE(sum(b.price), 0) FROM bookings b JOIN booking_resources r ON r.id = b.resource_id
        WHERE b.user_id = :uid AND b.status IN ('arrived','completed') AND b.start_at >= :a AND b.start_at < :b
        GROUP BY r.name ORDER BY 3 DESC LIMIT 5"""), {"uid": str(user.id), "a": a, "b": b}).fetchall()
    services = db.execute(text("""
        SELECT COALESCE(service_title, 'без услуги'), count(*) FROM bookings
        WHERE user_id = :uid AND status IN ('arrived','completed') AND start_at >= :a AND start_at < :b
        GROUP BY 1 ORDER BY 2 DESC LIMIT 3"""), {"uid": str(user.id), "a": a, "b": b}).fetchall()
    new_clients = int(db.execute(text("""
        SELECT count(*) FROM (SELECT contact_id, min(start_at) AS f FROM bookings
        WHERE user_id = :uid AND contact_id IS NOT NULL AND status IN ('arrived','completed') GROUP BY contact_id) x
        WHERE x.f >= :a AND x.f < :b"""), {"uid": str(user.id), "a": a, "b": b}).scalar() or 0)
    lapsed = len(lapsed_clients(db, user, 60, limit=500))
    delta = cur["revenue"] - prev["revenue"]
    data = {"week": cur, "prev": prev, "agent": ag, "masters": [{"name": m[0], "visits": int(m[1]), "revenue": float(m[2])} for m in masters],
            "services": [{"title": s[0], "count": int(s[1])} for s in services], "new_clients": new_clients,
            "lapsed": lapsed}
    lines = [f"📊 Итоги недели {(day - timedelta(days=7)).strftime('%d.%m')}–{(day - timedelta(days=1)).strftime('%d.%m')}", "",
             f"Выручка {_money(cur['revenue'])}" + (f" ({'+' if delta >= 0 else '−'}{_money(abs(delta))} к прошлой неделе)" if prev["revenue"] else ""),
             f"Визитов {cur['came']}, не пришли {cur['no_show']}, новых клиентов {new_clients}"
             + (f", средний чек {_money(cur['avg_check'])}" if cur["avg_check"] else "")]
    if data["masters"]:
        lines.append("Мастера: " + "; ".join(f"{m['name']} — {m['visits']} визитов, {_money(m['revenue'])}" for m in data["masters"]))
    if data["services"]:
        lines.append("Чаще всего: " + ", ".join(f"{s['title']} ({s['count']})" for s in data["services"]))
    if cur["review_n"]:
        lines.append(f"Оценки клиентов: {cur['review_avg']} из 5 ({cur['review_n']})")
    lines.append(f"ИИ-управляющий: написал {ag['messages']}, позвонил {ag['calls']}"
                 + (f", вернулись {ag['booked_after_manager']}" if ag["booked_after_manager"] else ""))
    if lapsed:
        lines.append(f"Давно не приходили (60+ дней): {lapsed} — сценарий «Вернуть ушедших» напишет им сам.")
    title = f"Неделя: {_money(cur['revenue'])}, визитов {cur['came']}"
    return title, "\n".join(lines), data


def save_report(db: Session, user_id, kind: str, day: date, title: str, body: str, data: Dict[str, Any]):
    """Сохранить отчёт (один на день и вид). None — уже был."""
    row = db.execute(text("""
        INSERT INTO owner_reports (id, user_id, kind, day, title, text, data, created_at)
        VALUES (gen_random_uuid(), :uid, :k, :d, :t, :x, CAST(:j AS JSONB), now())
        ON CONFLICT (user_id, kind, day) DO NOTHING RETURNING id
    """), {"uid": str(user_id), "k": kind, "d": day.isoformat(), "t": title[:200], "x": body,
           "j": __import__("json").dumps(data, ensure_ascii=False, default=str)}).first()
    db.commit()
    return row[0] if row else None


async def deliver_report(db: Session, user, agent, kind: str, title: str, body: str) -> None:
    """Отчёт уже в приложении; копия — в Telegram-бот (если подключён) и push на телефон."""
    from backend.services import owner_notifications, push_service
    try:
        if getattr(agent, "telegram_enabled", False):
            await owner_notifications._send(agent, db, body)
    except Exception as e:
        logger.warning(f"[MANAGER] report telegram: {e}")
    head = "Утренний отчёт" if kind == "morning" else "Итоги недели"
    await push_service.send_to_user(user.id, head, title, "/static/manager.html#reports", tag=f"report-{kind}")


async def make_report(db: Session, user, agent, kind: str, day: date, deliver: bool = True) -> Optional[Dict[str, Any]]:
    build = build_morning if kind == "morning" else build_weekly
    title, body, data = build(db, user, agent, day)
    rid = save_report(db, user.id, kind, day, title, body, data)
    if rid is None:
        return None
    if deliver:
        await deliver_report(db, user, agent, kind, title, body)
    return {"id": str(rid), "title": title, "text": body}


def list_reports(db: Session, user_id, limit: int = 30) -> List[Dict[str, Any]]:
    rows = db.execute(text("SELECT id, kind, day, title, text, data, created_at, read_at FROM owner_reports "
                           "WHERE user_id = :uid ORDER BY created_at DESC LIMIT :lim"),
                      {"uid": str(user_id), "lim": int(limit)}).fetchall()
    return [{"id": str(r[0]), "kind": r[1], "day": r[2], "title": r[3], "text": r[4], "data": r[5] or {},
             "created_at": r[6].isoformat() if r[6] else None, "read": r[7] is not None} for r in rows]


# ───────────────────────────── тик ─────────────────────────────

def _owner_agents(db: Session) -> List[Any]:
    """Первый агент каждого владельца с онлайн-записью (сценарии и отчёты — один на кабинет)."""
    from backend.models.agent_config import AgentConfig
    rows = db.execute(text("""
        SELECT DISTINCT ON (a.user_id) a.id FROM agent_configs a
        JOIN booking_pages p ON p.user_id = a.user_id
        ORDER BY a.user_id, a.created_at
    """)).fetchall()
    ids = [r[0] for r in rows]
    return db.query(AgentConfig).filter(AgentConfig.id.in_(ids)).all() if ids else []


async def tick(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    from backend.models.user import User
    from backend.services.owner_notifications import claim_agent_job
    loc = now_local(now)
    day = loc.date()
    stat = {"reports": 0, "planned": 0, "executed": 0}
    for agent in _owner_agents(db):
        try:
            user = db.query(User).filter(User.id == agent.user_id).first()
            if user is None or not booking_on(db, user) or user.agent_mode() is None:
                continue
            cfg = settings_of(agent)
            if cfg["morning_report"]["on"] and loc.hour >= cfg["morning_report"]["hour"] and loc.hour < 13:
                if claim_agent_job(db, agent.id, "salon_morning", day):
                    if await make_report(db, user, agent, "morning", day):
                        stat["reports"] += 1
            if cfg["weekly_report"]["on"] and loc.weekday() == 0 and loc.hour >= cfg["morning_report"]["hour"] and loc.hour < 13:
                if claim_agent_job(db, agent.id, "salon_weekly", day):
                    if await make_report(db, user, agent, "weekly", day):
                        stat["reports"] += 1
            if can_run_scenarios(user) and loc.hour >= PLAN_HOUR and loc.hour < WORK_HOURS[1] - 2 and agent.is_active:
                if any(cfg[k]["on"] for k in ("winback", "fill_gaps", "repeat_review")) and \
                        claim_agent_job(db, agent.id, "salon_plan", day):
                    rows = plan_actions(db, user, agent)
                    stat["planned"] += len(rows)
                    if rows and cfg["autonomy"] == "approve":
                        await _ask_approval(db, user, agent, len(rows))
        except Exception as e:
            db.rollback()
            logger.error(f"[MANAGER] tick agent {agent.id}: {e}", exc_info=True)
    try:
        stat["executed"] = await execute_due(db, now)
    except Exception as e:
        db.rollback()
        logger.error(f"[MANAGER] execute_due: {e}", exc_info=True)
    return stat


async def _ask_approval(db: Session, user, agent, n: int) -> None:
    from backend.services import owner_notifications, push_service
    import os
    host = (os.getenv("HOST_URL") or "https://voksyai.online").rstrip("/")
    msg = (f"🤖 ИИ-управляющий подготовил {n} действий на сегодня (вернуть клиентов, заполнить окна, напомнить "
           f"о повторе). Ничего не отправлено, пока вы не одобрите: {host}/static/manager.html#approve")
    try:
        if getattr(agent, "telegram_enabled", False):
            await owner_notifications._send(agent, db, msg)
    except Exception as e:
        logger.warning(f"[MANAGER] approval telegram: {e}")
    await push_service.send_to_user(user.id, "Нужно ваше одобрение", f"ИИ-управляющий подготовил {n} действий",
                                    "/static/manager.html#approve", tag="approve")
