"""
Автоматизации — автоматические сообщения клиентам (Voksy Beauty, шаг 2.2).

Владелец включает правила и правит тексты (RU и KY) на экране «Автоматизации»; планировщик (plan) раз в минуту
ставит сообщения в очередь (services/notify_queue.py), очередь отправляет. Тексты — шаблоны без ИИ (кредиты не
тратятся): {имя}, {когда}, {дата}, {время}, {услуга}, {мастер}, {адрес}, {салон}, {ссылка}, {подарок}, {прошло}.
Кусок в [квадратных скобках] выпадает целиком, если хоть одного значения внутри нет («[ Адрес: {адрес}.]»).
Язык — по последнему сообщению клиента (кыргызский → KY, иначе RU) или как задал владелец.

Правила (RULES):
  по записи (service, 08:00–21:00): подтверждение записи, напоминание накануне, напоминание в день визита,
    «спасибо» после визита (с просьбой оценить 1–5 — галочка или сценарий ИИ-управляющего), «не пришёл»;
  возврат клиентов (marketing, 10:00–20:00, с лимитами и отказом от рассылок): поздравление с ДР, «пора повторить
    услугу» (у услуги «напомнить о повторе через N дней»), «давно не были».
«Повтор» и «давно не были» пропускаются, если тот же сценарий включён у ИИ-управляющего (Beauty Pro) — он пишет
сам, текстом под клиента, и два сообщения об одном не уйдут.

Совместимость: напоминания и «спасибо» до шага 2.2 включались галочками reminders / thanks в правилах записи —
пока владелец не тронул правило, его состояние берётся оттуда. Метки reminder_24h_at / reminder_2h_at /
followup_at у записи по-прежнему ставятся (теперь — когда сообщение встало в очередь).
"""

from __future__ import annotations

import logging
import re
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services import booking_service as bs
from backend.services import notify_queue as nq

logger = logging.getLogger(__name__)

PLACEHOLDERS = [
    ("имя", "Имя клиента"), ("когда", "Когда: «завтра в 15:00»"), ("дата", "Дата: 28.11"), ("время", "Время: 15:00"),
    ("услуга", "Услуга"), ("мастер", "Мастер"), ("адрес", "Адрес салона"), ("салон", "Название салона"),
    ("ссылка", "Ссылка на запись"), ("подарок", "Подарок (поздравление с ДР)"), ("прошло", "Сколько прошло с визита"),
]

RULES: List[Dict[str, Any]] = [
    {"key": "confirm", "group": "booking", "kind": "service", "title": "Подтверждение записи", "default_on": True,
     "desc": "Сразу после записи: когда, услуга, мастер, адрес. Если записал бот в переписке — не дублируем, он уже "
             "подтвердил.", "params": {}, "options": {},
     "ru": "Здравствуйте[, {имя}]! Вы записаны {когда}[ на {услуга}][ к мастеру {мастер}].[ Адрес: {адрес}.] "
           "Если планы изменятся, просто напишите сюда.",
     "ky": "Саламатсызбы[, {имя}]! Сиз {когда}[ {услуга} кызматына][ {мастер} чеберге] жазылдыңыз.[ Дарегибиз: {адрес}.] "
           "Пландар өзгөрсө, ушул жерге жазыңыз."},
    {"key": "reminder_24h", "group": "booking", "kind": "service", "title": "Напоминание накануне", "default_on": True,
     "legacy": "reminders",
     "desc": "Если клиент записался заранее. Просим написать, если планы изменились.",
     "params": {"hours": 24}, "options": {"hours": [12, 24, 48]},
     "ru": "Здравствуйте[, {имя}]! Напоминаем: {когда} вы записаны[ на {услуга}][ к мастеру {мастер}].[ Адрес: {адрес}.] "
           "Если планы изменились, напишите, перенесём на удобное время.",
     "ky": "Саламатсызбы[, {имя}]! Эскертип коебуз: {когда} сиз[ {услуга} кызматына][ {мастер} чеберге] жазылгансыз."
           "[ Дарегибиз: {адрес}.] Пландар өзгөрсө, жазыңыз, ыңгайлуу убакытка которобуз."},
    {"key": "reminder_2h", "group": "booking", "kind": "service", "title": "Напоминание в день визита",
     "default_on": True, "legacy": "reminders",
     "desc": "За пару часов до визита — короткое «ждём вас».",
     "params": {"hours": 2}, "options": {"hours": [1, 2, 3]},
     "ru": "Здравствуйте[, {имя}]! Ждём вас {когда}[ на {услуга}].[ Адрес: {адрес}.]",
     "ky": "Саламатсызбы[, {имя}]! Сизди {когда} күтөбүз.[ Дарегибиз: {адрес}.]"},
    {"key": "thanks", "group": "booking", "kind": "service", "title": "Спасибо после визита", "default_on": True,
     "legacy": "thanks",
     "desc": "Через час после визита. С галочкой «просить оценку» — просим оценить визит от 1 до 5 (оценки 1–3 сразу "
             "приходят вам).",
     "params": {"hours": 1, "ask_review": False}, "options": {"hours": [1, 2, 3, 24]},
     "ru": "Спасибо, что пришли к нам[, {имя}]! Будем рады видеть вас снова. Если что-то понравилось или нет, напишите, "
           "нам важно.",
     "ky": "Келгениңиз үчүн рахмат[, {имя}]! Сизди дагы күтөбүз. Бир нерсе жакса же жакпаса, жазып коюңуз, биз үчүн "
           "маанилүү."},
    {"key": "review", "group": "booking", "kind": "service", "title": "Просьба оценить визит", "default_on": False,
     "hidden": True, "desc": "Вместо «спасибо», если включена оценка визита.", "params": {}, "options": {},
     "ru": "Спасибо, что пришли к нам[, {имя}]! Оцените, пожалуйста, визит[ у мастера {мастер}] от 1 до 5, просто "
           "ответьте цифрой. Нам очень важно ваше мнение.",
     "ky": "Келгениңиз үчүн рахмат[, {имя}]![ {мастер} чебердин] кызматын 1ден 5ке чейин баалап коюңузчу, жөн гана "
           "цифра менен жооп бериңиз. Сиздин пикириңиз биз үчүн маанилүү."},
    {"key": "no_show", "group": "booking", "kind": "service", "title": "Клиент не пришёл", "default_on": False,
     "desc": "Через пару часов после пропущенной записи — без упрёков предложить другое время.",
     "params": {"hours": 2}, "options": {"hours": [1, 2, 4, 24]},
     "ru": "Здравствуйте[, {имя}]! Жаль, что не получилось прийти[ на {услуга}]. Записать вас на другое время? "
           "Напишите, когда вам удобно.",
     "ky": "Саламатсызбы[, {имя}]! Келе албай калганыңызга өкүнүчтүү. Башка убакытка жазып коёлубу? Качан ыңгайлуу "
           "экенин жазыңыз."},
    {"key": "birthday", "group": "return", "kind": "marketing", "title": "Поздравление с днём рождения",
     "default_on": False, "desc": "Утром в день рождения (или заранее). Подарок — по желанию: скидка, бонус, уход.",
     "params": {"days_before": 0, "hour": 10, "gift": ""},
     "options": {"days_before": [0, 1, 3, 7], "hour": list(range(10, 20))},
     "ru": "С днём рождения[, {имя}]! Желаем красоты, радости и отличного настроения.[ В подарок от нас: {подарок}.] "
           "Будем рады видеть вас!",
     "ky": "Туулган күнүңүз менен[, {имя}]! Сулуулук, кубаныч жана жакшы маанай каалайбыз.[ Бизден белек: {подарок}.] "
           "Сизди күтөбүз!"},
    {"key": "repeat", "group": "return", "kind": "marketing", "title": "Пора повторить услугу", "default_on": False,
     "manager": "repeat_review",
     "desc": "Когда прошло столько дней, сколько указано у услуги («напомнить о повторе через N дней»), а клиент ещё "
             "не записан.", "params": {"hour": 11}, "options": {"hour": list(range(10, 20))},
     "ru": "Здравствуйте[, {имя}]![ С прошлого визита на {услуга} прошло {прошло}.] Пора освежить! Записать вас"
           "[ к мастеру {мастер}]? Напишите удобный день.[ Или запишитесь сами: {ссылка}]",
     "ky": "Саламатсызбы[, {имя}]![ Акыркы {услуга} кызматынан бери {прошло} өттү.] Жаңыртуучу убак келди![ {мастер} "
           "чеберге] жазып коёлубу? Ыңгайлуу күндү жазыңыз.[ Же өзүңүз жазылыңыз: {ссылка}]"},
    {"key": "winback", "group": "return", "kind": "marketing", "title": "Давно не были", "default_on": False,
     "manager": "winback",
     "desc": "Клиенту, который давно не приходил и не записан. Одно сообщение на каждый такой перерыв.",
     "params": {"days": 60, "hour": 12}, "options": {"days": [30, 45, 60, 90, 120], "hour": list(range(10, 20))},
     "ru": "Здравствуйте[, {имя}]! Давно вас не видели, соскучились. Записать вас на удобное время?"
           "[ Или выберите время сами: {ссылка}]",
     "ky": "Саламатсызбы[, {имя}]! Сизди көптөн бери көрө элекпиз, сагындык. Ыңгайлуу убакытка жазып коёлубу?"
           "[ Же убакытты өзүңүз тандаңыз: {ссылка}]"},
]
RULE_BY_KEY = {r["key"]: r for r in RULES}
GROUP_TITLES = {"booking": "Запись", "return": "Возврат клиентов"}
LIMITS_KEY = "limits"
DEFAULT_LIMITS = {"max_per_day": nq.DEFAULT_MAX_PER_DAY, "gap_days": nq.DEFAULT_GAP_DAYS, "lang": "auto"}
MAX_TEMPLATE = 1000
CONFIRM_LOOKBACK = timedelta(minutes=30)
MARKETING_EVERY = 10 * 60
_last_marketing = 0.0


class AutomationError(ValueError):
    pass


# ───────────────────────────── настройки ─────────────────────────────

def _rows(db: Session, user_id) -> Dict[str, Any]:
    from backend.models.automation import AutomationRule
    return {r.key: r for r in db.query(AutomationRule).filter(AutomationRule.user_id == nq._uuid(user_id)).all()}


def _legacy_cfg(db: Session, user_id) -> Dict[str, Any]:
    from backend.models.booking import BookingPage
    page = db.query(BookingPage).filter(BookingPage.user_id == nq._uuid(user_id)).first()
    return bs.settings_of(page)


def rule_state(rule: Dict[str, Any], row, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Действующее состояние правила: строка владельца поверх значений по умолчанию (и старых галочек записи)."""
    enabled = row.enabled if row is not None and row.enabled is not None else (
        bool(cfg.get(rule["legacy"])) if rule.get("legacy") else rule["default_on"])
    params = dict(rule["params"])
    for k, v in ((row.params if row is not None else None) or {}).items():
        if k in params:
            params[k] = v
    return {"enabled": bool(enabled), "params": params,
            "template_ru": (row.template_ru if row is not None and row.template_ru else None) or rule["ru"],
            "template_ky": (row.template_ky if row is not None and row.template_ky else None) or rule["ky"],
            "custom_ru": bool(row is not None and row.template_ru), "custom_ky": bool(row is not None and row.template_ky)}


def states(db: Session, user_id) -> Dict[str, Dict[str, Any]]:
    rows = _rows(db, user_id)
    cfg = _legacy_cfg(db, user_id)
    return {r["key"]: rule_state(r, rows.get(r["key"]), cfg) for r in RULES}


def limits_for(db: Session, user_id) -> Dict[str, Any]:
    from backend.models.automation import AutomationRule
    row = db.query(AutomationRule).filter(AutomationRule.user_id == nq._uuid(user_id),
                                          AutomationRule.key == LIMITS_KEY).first()
    out = dict(DEFAULT_LIMITS)
    for k, v in ((row.params if row is not None else None) or {}).items():
        if k in out:
            out[k] = v
    return out


def _clamp_int(v, lo: int, hi: int, name: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise AutomationError(f"{name}: нужно число")
    if not lo <= n <= hi:
        raise AutomationError(f"{name}: от {lo} до {hi}")
    return n


def clean_template(raw: Any) -> Optional[str]:
    s = str(raw or "").strip()
    if not s:
        return None
    if len(s) > MAX_TEMPLATE:
        raise AutomationError(f"Текст длиннее {MAX_TEMPLATE} символов")
    unknown = sorted(set(re.findall(r"\{([^{}]+)\}", s)) - {k for k, _ in PLACEHOLDERS})
    if unknown:
        raise AutomationError("Неизвестная вставка: {" + unknown[0] + "}. Можно: " +
                              ", ".join("{" + k + "}" for k, _ in PLACEHOLDERS))
    if s.count("[") != s.count("]"):
        raise AutomationError("Квадратные скобки не закрыты")
    return s


def save_rule(db: Session, user, key: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Изменить правило (вкл/выкл, параметры, тексты). Пустой текст — вернуть текст по умолчанию."""
    from backend.models.automation import AutomationRule
    from backend.services import activity
    rule = RULE_BY_KEY.get(key)
    if rule is None or rule.get("hidden") and key != "review":
        raise AutomationError("Нет такого правила")
    row = db.query(AutomationRule).filter(AutomationRule.user_id == user.id, AutomationRule.key == key).first()
    cfg = _legacy_cfg(db, user.id)
    before = rule_state(rule, row, cfg)
    if row is None:
        row = AutomationRule(user_id=user.id, key=key)
        db.add(row)
    if "enabled" in data:
        row.enabled = bool(data["enabled"])
    if "params" in data and isinstance(data["params"], dict):
        params = dict(row.params or {})
        for k, v in data["params"].items():
            if k not in rule["params"]:
                continue
            if k == "gift":
                params[k] = str(v or "").strip()[:200]
            elif k == "ask_review":
                params[k] = bool(v)
            else:
                opts = rule["options"].get(k)
                n = _clamp_int(v, 0, 1000, k)
                if opts and n not in opts:
                    raise AutomationError("Выберите значение из списка")
                params[k] = n
        row.params = params
    for lang in ("ru", "ky"):
        f = "template_" + lang
        if f in data:
            setattr(row, f, clean_template(data[f]))
    db.flush()
    after = rule_state(rule, row, cfg)
    changes = []
    if before["enabled"] != after["enabled"]:
        changes.append("включено" if after["enabled"] else "выключено")
    if before["params"] != after["params"]:
        changes.append("время и параметры")
    if before["template_ru"] != after["template_ru"] or before["template_ky"] != after["template_ky"]:
        changes.append("текст")
    if changes:
        activity.log(db, user.id, "automation.update", "automation", f"Автоматизация «{rule['title']}»: " +
                     ", ".join(changes), object_id=key, user=user,
                     changes={"enabled": [before["enabled"], after["enabled"]]} if before["enabled"] != after["enabled"] else None)
    return after


def save_limits(db: Session, user, data: Dict[str, Any]) -> Dict[str, Any]:
    from backend.models.automation import AutomationRule
    row = db.query(AutomationRule).filter(AutomationRule.user_id == user.id, AutomationRule.key == LIMITS_KEY).first()
    if row is None:
        row = AutomationRule(user_id=user.id, key=LIMITS_KEY, params={})
        db.add(row)
    params = dict(row.params or {})
    if "max_per_day" in data:
        params["max_per_day"] = _clamp_int(data["max_per_day"], 1, 500, "Сообщений в день")
    if "gap_days" in data:
        params["gap_days"] = _clamp_int(data["gap_days"], 1, 30, "Одному клиенту не чаще, дней")
    if "lang" in data:
        if data["lang"] not in ("auto", "ru", "ky"):
            raise AutomationError("Язык: auto, ru или ky")
        params["lang"] = data["lang"]
    row.params = params
    db.flush()
    return limits_for(db, user.id)


# ───────────────────────────── текст ─────────────────────────────

_OPT = re.compile(r"\[([^\[\]]*)\]")
_PH = re.compile(r"\{([^{}]+)\}")


def render(template: str, values: Dict[str, Any]) -> str:
    def opt(m):
        part = m.group(1)
        keys = _PH.findall(part)
        if any(not str(values.get(k) or "").strip() for k in keys):
            return ""
        return _PH.sub(lambda x: str(values.get(x.group(1)) or ""), part)
    s = _OPT.sub(opt, template or "")
    s = _PH.sub(lambda x: str(values.get(x.group(1)) or ""), s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r" +([.,!?:;])", r"\1", s)
    s = re.sub(r"\(\s*\)", "", s)
    return s.strip()


_WD_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def when_words(start: datetime, now: datetime, lang: str = "ru") -> str:
    st = bs.to_local(start)
    today = bs.to_local(now).date()
    hm = st.strftime("%H:%M")
    if lang == "ky":
        if st.date() == today:
            return f"бүгүн саат {hm}"
        if st.date() == today + timedelta(days=1):
            return f"эртең саат {hm}"
        return f"{st.day:02d}.{st.month:02d} саат {hm}"
    if st.date() == today:
        return f"сегодня в {hm}"
    if st.date() == today + timedelta(days=1):
        return f"завтра в {hm}"
    return f"{_WD_RU[st.weekday()]} {st.day:02d}.{st.month:02d} в {hm}"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n100, n10 = abs(n) % 100, abs(n) % 10
    if 11 <= n100 <= 14:
        return many
    return one if n10 == 1 else few if 2 <= n10 <= 4 else many


def ago_words(days: int, lang: str = "ru") -> str:
    if lang == "ky":
        if days >= 60:
            return f"{round(days / 30)} ай"
        if days >= 14:
            return f"{round(days / 7)} жума"
        return f"{days} күн"
    if days >= 60:
        m = round(days / 30)
        return f"{m} {_plural(m, 'месяц', 'месяца', 'месяцев')}"
    if days >= 14:
        w = round(days / 7)
        return f"{w} {_plural(w, 'неделя', 'недели', 'недель')}"
    return f"{days} {_plural(days, 'день', 'дня', 'дней')}"


def client_lang(db: Session, contact_id, mode: str = "auto") -> str:
    """Язык клиента: по последнему его сообщению (кыргызский — KY), иначе RU. mode ru/ky — как задал владелец."""
    if mode in ("ru", "ky"):
        return mode
    try:
        row = db.execute(text("""
            SELECT body FROM (
              SELECT body, created_at FROM agent_whatsapp_messages WHERE agent_contact_id = :c AND direction = 'inbound'
              UNION ALL SELECT body, created_at FROM agent_telegram_messages WHERE agent_contact_id = :c AND direction = 'inbound'
              UNION ALL SELECT body, created_at FROM agent_instagram_messages WHERE agent_contact_id = :c AND direction = 'inbound'
              UNION ALL SELECT body, created_at FROM agent_webchat_messages WHERE agent_contact_id = :c AND direction = 'inbound'
            ) m WHERE body IS NOT NULL AND body <> '' ORDER BY created_at DESC LIMIT 3
        """), {"c": str(contact_id)}).fetchall()
    except Exception:
        db.rollback()
        return "ru"
    from backend.services.agent_voice_service import detect_language
    for (body,) in row:
        lang = detect_language(body or "", None)
        if lang == "ky":
            return "ky"
        if lang == "ru":
            return "ru"
    return "ru"


def _first_name(name: Optional[str]) -> str:
    from backend.services.sip_gateway_service import first_name
    try:
        return first_name(name or "") or ""
    except Exception:
        parts = (name or "").strip().split()
        return parts[0] if parts else ""


def _salon_values(db: Session, user) -> Dict[str, Any]:
    from backend.core.config import settings
    page = bs.get_page(db, user, create=False)
    cfg = bs.settings_of(page)
    host = (getattr(settings, "HOST_URL", "") or "").rstrip("/")
    link = ""
    if page is not None and getattr(page, "enabled", True) and page.slug and host:
        link = f"{host}/b/{page.slug}"
    return {"адрес": cfg.get("address") or "", "салон": cfg.get("title") or user.company_name or "", "ссылка": link}


def values_for_booking(db: Session, user, b, now: datetime, lang: str, salon: Dict[str, Any],
                       masters: Dict[str, str]) -> Dict[str, Any]:
    st = bs.to_local(b.start_at)
    services = b.service_title or ""
    if b.visit_id:
        from backend.models.booking import Booking
        parts = (db.query(Booking.service_title).filter(Booking.visit_id == b.visit_id, Booking.status != "cancelled")
                 .order_by(Booking.start_at).all())
        titles = [t for (t,) in parts if t]
        if len(titles) > 1:
            services = ", ".join(titles[:-1]) + (" жана " if lang == "ky" else " и ") + titles[-1]
    return {**salon, "имя": _first_name(b.client_name), "когда": when_words(b.start_at, now, lang),
            "дата": f"{st.day:02d}.{st.month:02d}", "время": st.strftime("%H:%M"), "услуга": services,
            "мастер": masters.get(str(b.resource_id), "") if b.resource_id else ""}


def preview(db: Session, user, key: str, template: Optional[str], lang: str = "ru") -> str:
    """Как будет выглядеть сообщение на примере (без отправки)."""
    from backend.services.message_style import clean_client_text
    rule = RULE_BY_KEY.get(key)
    if rule is None:
        raise AutomationError("Нет такого правила")
    tpl = clean_template(template) or (rule["ky"] if lang == "ky" else rule["ru"])
    now = bs.now_utc()
    sample_start = datetime.combine(bs.to_local(now).date() + timedelta(days=1), time(15, 0), bs.BISHKEK)
    st = states(db, user.id).get(key) or {"params": {}}
    vals = {**_salon_values(db, user), "имя": "Айжан", "когда": when_words(sample_start, now, lang),
            "дата": f"{sample_start.day:02d}.{sample_start.month:02d}", "время": "15:00",
            "услуга": "Маникюр" if lang == "ru" else "Маникюр", "мастер": "Айгуль", "прошло": ago_words(35, lang),
            "подарок": (st.get("params") or {}).get("gift") or ""}
    if not vals["ссылка"]:
        vals["ссылка"] = "https://voksyai.online/b/salon"
    return clean_client_text(render(tpl, vals))


# ───────────────────────────── планировщик ─────────────────────────────

def _users_with(db: Session, user_ids) -> Dict[str, Any]:
    from backend.models.user import User
    out = {}
    if not user_ids:
        return out
    for u in db.query(User).filter(User.id.in_(list(user_ids))).all():
        out[str(u.id)] = u
    return out


def due_bookings(db: Session, now: datetime) -> List[Tuple[Any, str]]:
    """[(id записи, правило)] — кому пора: подтверждение, напоминания, «спасибо»/оценка, «не пришёл».
    Состояние правил — по владельцу записи (с учётом старых галочек «напоминания» и «спасибо»)."""
    rows = db.execute(text("""
        SELECT b.id, b.user_id, b.start_at, b.end_at, b.created_at, b.status, b.source, b.visit_id,
               b.reminder_24h_at, b.reminder_2h_at, b.followup_at, a.manager_settings
        FROM bookings b JOIN booking_pages p ON p.user_id = b.user_id
        LEFT JOIN agent_contacts c ON c.id = b.contact_id
        LEFT JOIN agent_configs a ON a.id = c.agent_config_id
        WHERE b.contact_id IS NOT NULL AND b.status IN ('booked', 'confirmed', 'arrived', 'completed', 'no_show')
          AND b.start_at < :max_start AND b.end_at > :min_end
        ORDER BY b.start_at
    """), {"max_start": now + timedelta(hours=49), "min_end": now - timedelta(hours=50)}).mappings().all()
    cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
    out = []
    for r in rows:
        uid = str(r["user_id"])
        if uid not in cache:
            cache[uid] = states(db, uid)
        stt = cache[uid]
        st, en, cr, status = r["start_at"], r["end_at"], r["created_at"], r["status"]
        active = status in ("booked", "confirmed")
        if stt["confirm"]["enabled"] and active and r["source"] not in ("bot", None) and now - cr <= CONFIRM_LOOKBACK \
                and st - now > timedelta(minutes=30):
            out.append((r["id"], "confirm"))
        h24 = int(stt["reminder_24h"]["params"].get("hours") or 24)
        h2 = int(stt["reminder_2h"]["params"].get("hours") or 2)
        if active and stt["reminder_24h"]["enabled"] and r["reminder_24h_at"] is None \
                and st - now <= timedelta(hours=h24) and st - now > timedelta(hours=3) and st - cr >= timedelta(hours=h24 + 2):
            out.append((r["id"], "reminder_24h"))
        elif active and stt["reminder_2h"]["enabled"] and r["reminder_2h_at"] is None \
                and st - now <= timedelta(hours=h2) and st - now > timedelta(minutes=20) and st - cr >= timedelta(hours=h2 + 1):
            out.append((r["id"], "reminder_2h"))
        review = bool(stt["thanks"]["params"].get("ask_review")) or bool(
            ((r["manager_settings"] or {}).get("repeat_review") or {}).get("on"))
        th = int(stt["thanks"]["params"].get("hours") or 1)
        if (stt["thanks"]["enabled"] or review) and r["followup_at"] is None and status != "no_show" \
                and timedelta(hours=th) <= now - en <= timedelta(hours=th + 24):
            if not review or status in ("arrived", "completed"):
                out.append((r["id"], "review" if review else "thanks"))
        hn = int(stt["no_show"]["params"].get("hours") or 2)
        if status == "no_show" and stt["no_show"]["enabled"] and timedelta(hours=hn) <= now - st <= timedelta(hours=hn + 24):
            out.append((r["id"], "no_show"))
    return out


_MARKERS = {"reminder_24h": "reminder_24h_at", "reminder_2h": "reminder_2h_at", "thanks": "followup_at",
            "review": "followup_at"}


def plan_bookings(db: Session, now: Optional[datetime] = None) -> int:
    """Записи → очередь. Метка у записи ставится атомарно (несколько воркеров), сообщение — с idem_key."""
    from backend.models.agent_contact import AgentContact
    from backend.models.booking import Booking
    now = now or bs.now_utc()
    n = 0
    users: Dict[str, Any] = {}
    salons: Dict[str, Dict[str, Any]] = {}
    masters: Dict[str, Dict[str, str]] = {}
    lims: Dict[str, Dict[str, Any]] = {}
    stcache: Dict[str, Dict[str, Any]] = {}
    for booking_id, rule in due_bookings(db, now):
        col = _MARKERS.get(rule)
        if col:
            got = db.execute(text(f"UPDATE bookings SET {col} = now() WHERE id = :id AND {col} IS NULL RETURNING id"),
                             {"id": booking_id}).first()
            db.commit()
            if got is None:
                continue
        b = db.query(Booking).filter(Booking.id == booking_id).first()
        if b is None:
            continue
        uid = str(b.user_id)
        if uid not in users:
            users.update(_users_with(db, [b.user_id]))
            from backend.models.booking import BookingResource
            masters[uid] = {str(m.id): m.name for m in db.query(BookingResource).filter(BookingResource.user_id == b.user_id).all()}
            lims[uid] = limits_for(db, uid)
        user = users.get(uid)
        if user is None or not user.has_agent_chat_access():
            continue
        if uid not in salons:
            salons[uid] = _salon_values(db, user)
        contact = db.query(AgentContact).filter(AgentContact.id == b.contact_id).first()
        if contact is None:
            continue
        lang = client_lang(db, contact.id, lims[uid]["lang"])
        if uid not in stcache:
            stcache[uid] = states(db, uid)
        stt = stcache[uid][rule]
        tpl = stt["template_ky"] if lang == "ky" else stt["template_ru"]
        vals = values_for_booking(db, user, b, now, lang, salons[uid], masters[uid])
        msg = render(tpl, vals)
        key_id = str(b.visit_id) if b.visit_id and rule in ("confirm", "no_show") else str(b.id)
        expires = {"confirm": b.start_at, "reminder_24h": b.start_at - timedelta(hours=1),
                   "reminder_2h": b.start_at, "thanks": b.end_at + timedelta(days=2),
                   "review": b.end_at + timedelta(days=2), "no_show": b.start_at + timedelta(days=2)}[rule]
        if nq.enqueue(db, user_id=b.user_id, contact_id=contact.id, rule=rule, text_=msg, idem_key=f"{rule}:{key_id}",
                      kind="service", lang=lang, booking_id=b.id, expires_at=expires, send_after=now):
            n += 1
        db.commit()
    return n


def _today_local(now: datetime) -> date:
    return bs.to_local(now).date()


def _at_hour(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(int(hour), 0), bs.BISHKEK).astimezone(timezone.utc)


def _enabled_users(db: Session, key: str) -> List[str]:
    rows = db.execute(text("SELECT user_id FROM automation_rules WHERE key = :k AND enabled IS TRUE"), {"k": key}).fetchall()
    return [str(r[0]) for r in rows]


def _manager_covers(db: Session, user, key: str) -> bool:
    """Тот же сценарий у ИИ-управляющего (Beauty Pro) — он пишет сам, правило молчит."""
    rule = RULE_BY_KEY[key]
    if not rule.get("manager") or user.agent_mode() != "full":
        return False
    from backend.models.agent_config import AgentConfig
    from backend.services import salon_manager
    agent = db.query(AgentConfig).filter(AgentConfig.user_id == user.id).order_by(AgentConfig.created_at).first()
    if agent is None:
        return False
    return bool(salon_manager.settings_of(agent)[rule["manager"]]["on"])


def manager_covers(db: Session, user, key: str) -> bool:
    try:
        return _manager_covers(db, user, key)
    except Exception:
        db.rollback()
        return False


def plan_marketing(db: Session, now: Optional[datetime] = None, force: bool = False) -> int:
    """Поздравления с ДР, «пора повторить», «давно не были» → очередь (не чаще раза в 10 минут на воркер)."""
    global _last_marketing
    if not force and _time.monotonic() - _last_marketing < MARKETING_EVERY:
        return 0
    _last_marketing = _time.monotonic()
    now = now or bs.now_utc()
    n = 0
    for key, fn in (("birthday", _plan_birthday), ("repeat", _plan_repeat), ("winback", _plan_winback)):
        uids = _enabled_users(db, key)
        for uid, user in _users_with(db, uids).items():
            try:
                if not user.has_agent_chat_access() or manager_covers(db, user, key):
                    continue
                n += fn(db, user, states(db, uid)[key], now)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning(f"[AUTOMATIONS] {key} for {uid} failed: {e}")
    return n


def _marketing_enqueue(db: Session, user, contact_id, key: str, st: Dict[str, Any], vals: Dict[str, Any],
                       idem: str, send_at: datetime, lang_mode: str, expires: datetime, meta=None) -> bool:
    lang = client_lang(db, contact_id, lang_mode)
    tpl = st["template_ky"] if lang == "ky" else st["template_ru"]
    msg = render(tpl, {**vals, "прошло": vals.get("прошло_" + lang) or vals.get("прошло") or ""})
    return bool(nq.enqueue(db, user_id=user.id, contact_id=contact_id, rule=key, text_=msg, idem_key=idem,
                           kind="marketing", lang=lang, send_after=send_at, expires_at=expires, meta=meta))


_ELIGIBLE = ("c.marketing_opt_out_at IS NULL AND c.status <> 'do_not_call' "
             "AND (c.ai_paused_until IS NULL OR c.ai_paused_until < '9999-01-01')")


def _plan_birthday(db: Session, user, st: Dict[str, Any], now: datetime) -> int:
    p = st["params"]
    target = _today_local(now) + timedelta(days=int(p.get("days_before") or 0))
    mmdd = [target.strftime("%m-%d")]
    if target.month == 3 and target.day == 1 and not (target.year % 4 == 0 and (target.year % 100 or target.year % 400 == 0)):
        mmdd.append("02-29")
    rows = db.execute(text(f"SELECT c.id, c.name FROM agent_contacts c WHERE c.user_id = :u AND c.birthday IS NOT NULL "
                           f"AND to_char(c.birthday, 'MM-DD') = ANY(:d) AND {_ELIGIBLE}"),
                      {"u": str(user.id), "d": mmdd}).fetchall()
    if not rows:
        return 0
    salon = _salon_values(db, user)
    lim = limits_for(db, user.id)
    today = _today_local(now)
    send_at = max(_at_hour(today, int(p.get("hour") or 10)), now)
    expires = _at_hour(today, nq.MARKETING_WINDOW[1])
    n = 0
    for cid, name in rows:
        vals = {**salon, "имя": _first_name(name), "подарок": p.get("gift") or ""}
        n += _marketing_enqueue(db, user, cid, "birthday", st, vals, f"birthday:{cid}:{target.year}", send_at,
                                lim["lang"], expires)
    return n


def _plan_repeat(db: Session, user, st: Dict[str, Any], now: datetime) -> int:
    from backend.models.booking import VISITED_SQL
    rows = db.execute(text(f"""
        WITH last AS (
          SELECT DISTINCT ON (b.contact_id, b.service_entry_id) b.id, b.contact_id, b.service_entry_id, b.service_title,
                 b.resource_id, b.start_at, e.repeat_days
          FROM bookings b JOIN agent_knowledge_entries e ON e.id = b.service_entry_id
          WHERE b.user_id = :u AND b.contact_id IS NOT NULL AND b.status IN {VISITED_SQL}
            AND e.repeat_days IS NOT NULL AND e.repeat_days > 0
          ORDER BY b.contact_id, b.service_entry_id, b.start_at DESC
        )
        SELECT last.id, last.contact_id, last.service_title, last.resource_id, last.start_at, last.repeat_days, c.name
        FROM last JOIN agent_contacts c ON c.id = last.contact_id
        WHERE last.start_at + make_interval(days => last.repeat_days) <= :now
          AND last.start_at + make_interval(days => last.repeat_days + 14) > :now
          AND NOT EXISTS (SELECT 1 FROM bookings f WHERE f.contact_id = last.contact_id AND f.user_id = :u
                          AND f.status IN ('booked', 'confirmed') AND f.start_at > :now)
          AND {_ELIGIBLE}
    """), {"u": str(user.id), "now": now}).fetchall()
    if not rows:
        return 0
    salon = _salon_values(db, user)
    lim = limits_for(db, user.id)
    from backend.models.booking import BookingResource
    names = {str(m.id): m.name for m in db.query(BookingResource).filter(BookingResource.user_id == user.id).all()}
    today = _today_local(now)
    send_at = max(_at_hour(today, int(st["params"].get("hour") or 11)), now)
    n = 0
    for bid, cid, title, rid, start, days, name in rows:
        ago = (now - bs._aw(start)).days
        vals = {**salon, "имя": _first_name(name), "услуга": title or "", "мастер": names.get(str(rid), "") if rid else "",
                "прошло_ru": ago_words(ago, "ru"), "прошло_ky": ago_words(ago, "ky")}
        n += _marketing_enqueue(db, user, cid, "repeat", st, vals, f"repeat:{cid}:{bid}", send_at, lim["lang"],
                                send_at + timedelta(days=7), meta={"booking_id": str(bid)})
    return n


def _plan_winback(db: Session, user, st: Dict[str, Any], now: datetime) -> int:
    from backend.models.booking import VISITED_SQL
    days = int(st["params"].get("days") or 60)
    rows = db.execute(text(f"""
        SELECT b.contact_id, max(b.start_at) AS last_at, c.name
        FROM bookings b JOIN agent_contacts c ON c.id = b.contact_id
        WHERE b.user_id = :u AND b.status IN {VISITED_SQL} AND {_ELIGIBLE}
          AND NOT EXISTS (SELECT 1 FROM bookings f WHERE f.contact_id = b.contact_id AND f.user_id = :u
                          AND f.status IN ('booked', 'confirmed') AND f.start_at > :now)
        GROUP BY b.contact_id, c.name
        HAVING max(b.start_at) <= :now - make_interval(days => :d) AND max(b.start_at) > :now - make_interval(days => :d2)
    """), {"u": str(user.id), "now": now, "d": days, "d2": days + 30}).fetchall()
    if not rows:
        return 0
    salon = _salon_values(db, user)
    lim = limits_for(db, user.id)
    today = _today_local(now)
    send_at = max(_at_hour(today, int(st["params"].get("hour") or 12)), now)
    n = 0
    for cid, last_at, name in rows:
        vals = {**salon, "имя": _first_name(name)}
        n += _marketing_enqueue(db, user, cid, "winback", st, vals,
                                f"winback:{cid}:{bs.to_local(bs._aw(last_at)).date().isoformat()}", send_at,
                                lim["lang"], send_at + timedelta(days=7))
    return n


def plan(db: Session, now: Optional[datetime] = None) -> int:
    n = 0
    try:
        n += plan_bookings(db, now)
    except Exception as e:
        db.rollback()
        logger.error(f"[AUTOMATIONS] plan_bookings failed: {e}", exc_info=True)
    try:
        n += plan_marketing(db, now)
    except Exception as e:
        db.rollback()
        logger.error(f"[AUTOMATIONS] plan_marketing failed: {e}", exc_info=True)
    return n


# ───────────────────────────── для экрана ─────────────────────────────

def overview(db: Session, user) -> Dict[str, Any]:
    stt = states(db, user.id)
    stats = nq.stats(db, user.id)
    out = []
    for r in RULES:
        if r.get("hidden"):
            continue
        s = stt[r["key"]]
        st_rule = stats.get(r["key"], {})
        if r["key"] == "thanks":
            for k, v in stats.get("review", {}).items():
                st_rule[k] = st_rule.get(k, 0) + v
        item = {
            "key": r["key"], "group": r["group"], "group_title": GROUP_TITLES[r["group"]], "kind": r["kind"],
            "title": r["title"], "desc": r["desc"], "enabled": s["enabled"], "params": s["params"], "options": r["options"],
            "template_ru": s["template_ru"], "template_ky": s["template_ky"], "default_ru": r["ru"], "default_ky": r["ky"],
            "custom_ru": s["custom_ru"], "custom_ky": s["custom_ky"],
            "stats": {"sent": st_rule.get("sent", 0), "failed": st_rule.get("failed", 0),
                      "skipped": st_rule.get("skipped", 0), "pending": st_rule.get("pending", 0) + st_rule.get("sending", 0)},
        }
        if r.get("manager") and manager_covers(db, user, r["key"]):
            item["covered_by_manager"] = True
        if r["key"] == "thanks":
            item["review_text_ru"] = stt["review"]["template_ru"]
            item["review_text_ky"] = stt["review"]["template_ky"]
        out.append(item)
    return {"rules": out, "limits": limits_for(db, user.id), "placeholders": [{"key": k, "title": t} for k, t in PLACEHOLDERS],
            "windows": {"service": list(nq.SERVICE_WINDOW), "marketing": list(nq.MARKETING_WINDOW)}}
