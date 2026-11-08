"""
Демо голосового режима (ветка 0811-voice-jarvis): любой желающий говорит с «агентом» компании,
которой не существует. Нужен, чтобы показывать Voksy AI, не пуская человека в чужой кабинет.

Данных из базы здесь НЕТ ВООБЩЕ: все цифры, клиенты, переписка и звонки — придуманная компания
«Окна Комфорт» (пластиковые окна, Бишкек). Модуль отдаёт то же, что services/owner_voice.py отдаёт
настоящему владельцу — сводку дел, карточки на экран и результаты быстрых функций той же формы, —
поэтому окно разговора (static/agent/voice-chat.js) и Gemini работают без единой правки.

Числа «живые»: привязаны к сегодняшней дате по Бишкеку и меняются день ото дня (_wave), но внутри
одного дня одинаковы — демонстрация должна выглядеть одинаково у всех, кто её сейчас смотрит.

Вход — Telegram-бот демо (api/demo_voice.py), сокет — websockets/handler_demo_voice.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.core.logging import get_logger
from backend.services import owner_voice as ov

logger = get_logger(__name__)

COMPANY = "Окна Комфорт"
AGENT_NAME = "Айна"
BISHKEK_OFFSET = ov.BISHKEK_OFFSET
WEEKDAYS, WEEKDAYS_SHORT = ov.WEEKDAYS, ov.WEEKDAYS_SHORT

MAX_SECONDS = int(__import__("os").getenv("DEMO_VOICE_MAX_SECONDS", "420") or 420)


def now_bk(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.utcnow()) + BISHKEK_OFFSET


def _wave(day: Any, seed: int, lo: int, hi: int) -> int:
    """Одно и то же число весь день, разное в разные дни: демонстрация живая, но не прыгает."""
    base = day.toordinal() * 131 + seed * 977
    return lo + (base * 2654435761 % 4294967296) % max(1, hi - lo + 1)


# ---------------------------------------------------------------- придуманная компания
def _clients(today) -> List[Dict[str, Any]]:
    """Клиенты демо-компании. mins — сколько минут назад последнее сообщение/звонок."""
    return [
        {"name": "Нурлан Асанов", "company": "Кафе «Ала-Тоо»", "phone": "+996 700 11 22 33",
         "channel": "whatsapp", "stage": "В работе", "waiting": True, "mins": 14,
         "need": "шесть окон в зал, нужен замер и цена",
         "memory": "Кафе на Чуй, ремонт зала. Нужны шесть окон 1,5 на 1,8, профиль потеплее. Торопится — открытие через месяц.",
         "feed": [("клиент", "Здравствуйте! Сколько будет шесть окон в зал, размер примерно полтора на метр восемьдесят?", 40),
                  ("агент", "Здравствуйте, Нурлан! Шесть окон такого размера — от 92 тысяч сом с установкой. Точную цену скажу после замера, он бесплатный.", 39),
                  ("клиент", "А замер когда можно?", 15),
                  ("клиент", "И рассрочка есть?", 14)]},
        {"name": "Гүлнара Осмонова", "company": "", "phone": "+996 555 44 55 66",
         "channel": "telegram", "stage": "Думает", "waiting": False, "mins": 190,
         "need": "кп отправлено, думает над профилем",
         "memory": "Частный дом в Джале, восемь окон и балконный блок. Отправили КП на 148 тысяч. Сравнивает с конкурентом.",
         "feed": [("агент", "Гүлнара, отправила расчёт на восемь окон и балконный блок — 148 тысяч сом с установкой и вывозом старых рам.", 200),
                  ("клиент", "Спасибо, посмотрю вечером с мужем", 190)]},
        {"name": "Марат Жумабеков", "company": "ОсОО «Темир»", "phone": "+996 702 77 88 99",
         "channel": "whatsapp", "stage": "В работе", "waiting": True, "mins": 55,
         "need": "замер назначен на завтра, просит перенести на утро",
         "memory": "Офис на Советской, 14 окон. Замер завтра в 14:00, просит перенести на утро.",
         "feed": [("агент", "Марат, замерщик приедет завтра в два часа дня, адрес Советская 112.", 70),
                  ("клиент", "А можно утром? После обеда меня не будет", 55)]},
        {"name": "Асель Турсунова", "company": "", "phone": "+996 707 12 34 56",
         "channel": "telegram", "stage": "Успех", "waiting": False, "mins": 320,
         "need": "оплатила, монтаж в четверг",
         "memory": "Квартира на Асанбай, три окна. Оплатила 54 тысячи, монтаж в четверг утром.",
         "feed": [("клиент", "Оплатила, чек отправила", 330), ("агент", "Получили оплату, спасибо! Монтаж в четверг с девяти утра.", 320)]},
        {"name": "Жылдыз", "company": "", "phone": "+996 550 90 80 70",
         "channel": "whatsapp", "stage": "Новый", "waiting": True, "mins": 95,
         "need": "написала с рекламы, не дозвонились",
         "memory": "Пришла с рекламы в Instagram. Два окна на кухню. Звонили — не взяла трубку.",
         "feed": [("клиент", "Здравствуйте, видела рекламу. Сколько стоит окно на кухню?", 95)]},
        {"name": "Тимур", "company": "", "phone": "+996 777 65 43 21",
         "channel": "instagram", "stage": "Отказ", "waiting": False, "mins": 1500,
         "need": "дорого, ушёл думать",
         "memory": "Сказал «дорого», сравнивал с бэушными рамами. Вернуться через месяц.",
         "feed": [("клиент", "Дороговато выходит, я подумаю", 1500)]},
    ]


def _calls(today) -> List[Dict[str, Any]]:
    return [
        {"client": "Нурлан Асанов", "dir": "входящий", "mins": 42, "sec": 214, "status": "answered",
         "result": "Спрашивал цену на шесть окон, записались на замер — ждёт время"},
        {"client": "Жылдыз", "dir": "исходящий", "mins": 96, "sec": 0, "status": "no_answer",
         "result": "Не взяла трубку, перезвонить вечером"},
        {"client": "Марат Жумабеков", "dir": "исходящий", "mins": 180, "sec": 141, "status": "answered",
         "result": "Подтвердил замер на завтра, просит перенести на утро"},
        {"client": "Асель Турсунова", "dir": "входящий", "mins": 330, "sec": 96, "status": "answered",
         "result": "Сообщила об оплате, монтаж в четверг"},
    ]


def _tasks(today) -> List[Dict[str, Any]]:
    return [
        {"in_hours": 2, "what": "звонок", "client": "Жылдыз", "title": "Перезвонить по окнам на кухню", "by": "агент"},
        {"in_hours": 17, "what": "сообщение в Telegram", "client": "Гүлнара Осмонова",
         "title": "Спросить решение по расчёту", "by": "агент"},
        {"in_hours": 20, "what": "звонок", "client": "Нурлан Асанов", "title": "Согласовать время замера", "by": "вы"},
        {"in_hours": 26, "what": "напоминание вам", "client": "Марат Жумабеков",
         "title": "Замерщик к Марату, перенести на утро", "by": "вы"},
    ]


FUNNEL = [("Новый", 12), ("В работе", 8), ("Думает", 5), ("Успех", 6), ("Отказ", 3), ("Не звонить", 1)]


def day_stats(day) -> Dict[str, int]:
    """Цифры одного дня придуманной компании (суббота и воскресенье — тише)."""
    quiet = day.weekday() >= 5
    k = 0.45 if quiet else 1.0
    calls_in = max(0, int(_wave(day, 1, 2, 7) * k))
    calls_out = max(0, int(_wave(day, 2, 4, 11) * k))
    answered = max(0, int((calls_in + calls_out) * 0.72))
    st = ov._empty_day()
    st.update(calls_in=calls_in, calls_out=calls_out, answered=answered,
              talk_sec=answered * _wave(day, 3, 90, 240),
              msgs_in=max(0, int(_wave(day, 4, 9, 28) * k)),
              bot_out=max(0, int(_wave(day, 5, 12, 34) * k)),
              owner_out=max(0, int(_wave(day, 6, 0, 5) * k)),
              new_clients=max(0, int(_wave(day, 7, 1, 6) * k)))
    st["writers"] = max(1, int(st["msgs_in"] * 0.45)) if st["msgs_in"] else 0
    return st


def _days_map(a, b) -> Dict[Any, Dict[str, int]]:
    days, d = {}, a
    while d <= b:
        days[d] = day_stats(d)
        d += timedelta(days=1)
    return days


def _when(minutes_ago: float, today_bk: datetime) -> str:
    return ov._day_label(datetime.utcnow() - timedelta(minutes=minutes_ago), today_bk)


# ---------------------------------------------------------------- балансы демо
def demo_balances() -> Dict[str, Any]:
    return {"wallet_som": 1860.0, "wallet_minutes": 340, "som_per_minute": 5.47, "wallet_free": False,
            "credits": 16400, "credits_voice_minutes": 280, "plan": "Profi", "plan_days_left": 23}


# ---------------------------------------------------------------- быстрые функции (та же форма ответа)
def get_stats(args: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    since, until, label, a, b = ov.period_range(args.get("period"), args.get("date_from"), args.get("date_to"), now=now)
    days = _days_map(a, b)
    total = ov._sum_days(days)
    total["writers"] = max(1, int(total["msgs_in"] * 0.4)) if total["msgs_in"] else 0
    calls = total["calls_in"] + total["calls_out"]
    result: Dict[str, Any] = {
        "ok": True, "demo": True, "period": label, "summary": ov._stats_sentence(total),
        "calls": {"total": calls, "incoming": total["calls_in"], "outgoing": total["calls_out"],
                  "with_talk": total["answered"], "talk_minutes": round(total["talk_sec"] / 60)},
        "messages": {"from_clients": total["msgs_in"], "clients_wrote": total["writers"],
                     "agent_replies": total["bot_out"], "your_replies": total["owner_out"]},
        "new_clients": total["new_clients"],
    }
    bars = []
    if 1 < len(days) <= 31:
        for d in sorted(days):
            st = days[d]
            bars.append({"label": WEEKDAYS_SHORT[d.weekday()] if len(days) <= 8 else f"{d:%d.%m}",
                         "full": f"{WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m}",
                         "value": st["calls_in"] + st["calls_out"] + st["msgs_in"]})
    result["card"] = {
        "type": "stats", "title": label[:1].upper() + label[1:],
        "tiles": [
            {"label": "Звонки", "value": calls, "sub": f"с разговором {total['answered']}"},
            {"label": "Сообщения", "value": total["msgs_in"], "sub": f"от {total['writers']} клиентов"},
            {"label": "Новые клиенты", "value": total["new_clients"], "sub": "в CRM"},
            {"label": "Разговоры", "value": f"{round(total['talk_sec'] / 60)} мин", "sub": f"входящих {total['calls_in']}"},
        ],
        "bars": bars,
    }
    return result


def get_report(args: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    today = now_bk(now)
    since, until, label, a, b = ov.period_range(args.get("period"), args.get("date_from"), args.get("date_to"), now=now)
    days = _days_map(a, b)
    total = ov._sum_days(days)
    total["writers"] = max(1, int(total["msgs_in"] * 0.4)) if total["msgs_in"] else 0
    calls = total["calls_in"] + total["calls_out"]
    talk_min = round(total["talk_sec"] / 60)

    length = (b - a).days + 1
    prev_b = a - timedelta(days=1)
    prev = ov._sum_days(_days_map(prev_b - timedelta(days=length - 1), prev_b))
    _ps, _pu, prev_label, prev_short = ov._prev_range(a, b)
    d_calls = ov._delta(calls, prev["calls_in"] + prev["calls_out"])
    d_msgs = ov._delta(total["msgs_in"], prev["msgs_in"])
    no_answer = max(0, calls - total["answered"])

    clients = _clients(today)
    waiting = [c for c in clients if c["waiting"]]
    scenes = [
        ov._report_scene("calls", "Звонки", "tiles", tiles=[
            {"label": "Всего", "value": calls, "sub": f"{d_calls['text']} {prev_label}" if d_calls["text"] else "",
             "dir": d_calls["dir"]},
            {"label": "С разговором", "value": total["answered"], "sub": f"{talk_min} мин всего"},
            {"label": "Не ответили", "value": no_answer, "sub": "перезвонить", "accent": bool(no_answer)},
            {"label": "Входящих", "value": total["calls_in"], "sub": f"исходящих {total['calls_out']}"},
        ]),
        ov._report_scene("messages", "Переписка", "tiles", tiles=[
            {"label": "От клиентов", "value": total["msgs_in"],
             "sub": f"{d_msgs['text']} {prev_label}" if d_msgs["text"] else "", "dir": d_msgs["dir"]},
            {"label": "Писало клиентов", "value": total["writers"], "sub": "разных"},
            {"label": "Ответил агент", "value": total["bot_out"], "sub": "сообщений"},
            {"label": "Ответили вы", "value": total["owner_out"], "sub": "сами"},
        ]),
    ]
    if len(days) > 1:
        week_view = len(days) <= 8
        scenes.append(ov._report_scene("days", "По дням", "bars", bars=[
            {"label": WEEKDAYS_SHORT[d.weekday()] if week_view else f"{d:%d.%m}",
             "full": f"{WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m}",
             "value": days[d]["calls_in"] + days[d]["calls_out"] + days[d]["msgs_in"]} for d in sorted(days)]))
    if waiting:
        scenes.append(ov._report_scene("waiting", f"Ждут ответа ({len(waiting)})", "list", items=[
            {"title": c["name"], "meta": f"{ov.CHANNEL_NAMES.get(c['channel'], c['channel'])} · {_when(c['mins'], today)}",
             "text": c["feed"][-1][1], "channel": c["channel"]} for c in waiting]))
    scenes.append(ov._report_scene("funnel", "Воронка CRM", "funnel",
                                   stages=[{"label": k, "value": v} for k, v in FUNNEL],
                                   note=f"новых за период {total['new_clients']}"))
    tasks = _tasks(today)
    scenes.append(ov._report_scene("tasks", f"Запланировано ({len(tasks)})", "list", items=[
        {"title": f"{_when(-t['in_hours'] * 60, today)} · {t['what']}", "meta": t["client"], "text": t["title"]}
        for t in tasks[:5]]))
    money = demo_balances()
    scenes.append(ov._report_scene("money", "Балансы", "balance", rows=ov._money_rows(money)))

    script = [
        f"Звонков {calls}" + (f", {d_calls['text']} {prev_label}" if d_calls["text"] else "") +
        f"; с разговором {total['answered']}" + (f", не ответили {no_answer}" if no_answer else ""),
        f"Сообщений от клиентов {total['msgs_in']} от {total['writers']} клиентов" +
        (f", {d_msgs['text']} {prev_label}" if d_msgs["text"] else ""),
    ]
    if len(days) > 1:
        best = max(days, key=lambda d: days[d]["calls_in"] + days[d]["calls_out"] + days[d]["msgs_in"])
        script.append(f"По дням самый плотный — {WEEKDAYS[best.weekday()]}")
    if waiting:
        script.append(f"Ждут ответа {len(waiting)}: " + ", ".join(c["name"] for c in waiting))
    script.append(f"Новых клиентов за период {total['new_clients']}, в воронке "
                  + ", ".join(f"{k} {v}" for k, v in FUNNEL[:3]))
    script.append(f"Запланировано {len(tasks)}: ближайшее — {tasks[0]['what']}, {tasks[0]['client']}")
    script.append(ov._money_sentence(money))
    return {
        "ok": True, "demo": True, "period": label, "summary": ov._stats_sentence(total),
        "compare_with": prev_label, "calls_delta": d_calls["text"], "messages_delta": d_msgs["text"],
        "calls": {"total": calls, "incoming": total["calls_in"], "outgoing": total["calls_out"],
                  "answered": total["answered"], "no_answer": no_answer, "talk_minutes": talk_min},
        "messages": {"from_clients": total["msgs_in"], "clients_wrote": total["writers"],
                     "agent_replies": total["bot_out"], "your_replies": total["owner_out"]},
        "new_clients": total["new_clients"], "waiting": len(waiting), "scheduled": len(tasks),
        "balances": money, "script": script, "speak_order": [s["id"] for s in scenes],
        "card": {"type": "report", "title": "Отчёт · " + ov._PERIOD_SHORT.get(str(args.get("period") or "today"), label),
                 "subtitle": prev_short, "scenes": scenes},
    }


def get_balance(args: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    b = demo_balances()
    return {"ok": True, "demo": True, "summary": ov._money_sentence(b), "warnings": [], **b,
            "card": {"type": "balance", "title": "Балансы", "subtitle": "кошелёк, кредиты, тариф",
                     "rows": ov._money_rows(b)}}


def client_info(args: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    today = now_bk(now)
    query = " ".join(str(args.get("query") or "").lower().split())
    clients = _clients(today)
    found = None
    if query:
        digits = "".join(ch for ch in query if ch.isdigit())
        for c in clients:
            hay = f"{c['name']} {c['company']} {c['phone']}".lower()
            if query in hay or (len(digits) >= 4 and digits[-4:] in "".join(ch for ch in c["phone"] if ch.isdigit())):
                found = c
                break
        if found is None:
            for c in clients:
                if any(w and w in c["name"].lower() for w in query.split()):
                    found = c
                    break
    if found is None:
        return {"ok": True, "demo": True, "found": False,
                "message": f"В демо-базе «{COMPANY}» такого клиента нет. Есть: "
                           + ", ".join(c["name"] for c in clients) + ". Назовите кого-нибудь из них."}
    lines = [f"{'Клиент' if who == 'клиент' else 'Агент'} ({_when(m, today)}): {text}"
             for who, text, m in found["feed"]]
    calls = [c for c in _calls(today) if c["client"] == found["name"]]
    return {
        "ok": True, "demo": True, "found": True, "name": found["name"], "company": found["company"],
        "phone": found["phone"], "stage": found["stage"],
        "channel": ov.CHANNEL_NAMES.get(found["channel"], found["channel"]),
        "agent_knows": found["memory"], "need": found["need"],
        "last_contact": _when(found["mins"], today), "waiting_reply": found["waiting"],
        "messages": lines,
        "calls": [{"when": _when(c["mins"], today), "direction": c["dir"],
                   "outcome": "был разговор" if c["status"] == "answered" else "не ответил",
                   "result": c["result"]} for c in calls],
        "card": {"type": "client", "title": found["name"],
                 "subtitle": f"{found['stage']} · {ov.CHANNEL_NAMES.get(found['channel'], found['channel'])} · "
                             f"{_when(found['mins'], today)}",
                 "lines": lines + [f"Звонок ({_when(c['mins'], today)}): {c['result']}" for c in calls]},
    }


def list_dialogs(args: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    today = now_bk(now)
    folder = args.get("folder") if args.get("folder") in ("waiting", "unread", "paused", "all") else "waiting"
    channel = args.get("channel") if args.get("channel") in ("whatsapp", "telegram", "instagram") else None
    clients = _clients(today)
    if folder in ("waiting", "unread"):
        clients = [c for c in clients if c["waiting"]]
    elif folder == "paused":
        clients = []
    if channel:
        clients = [c for c in clients if c["channel"] == channel]
    titles = {"waiting": "Ждут ответа", "unread": "Непрочитанные", "paused": "Бот на паузе", "all": "Последние диалоги"}
    items = [{"name": c["name"], "channels": [ov.CHANNEL_NAMES.get(c["channel"], c["channel"])],
              "when": _when(c["mins"], today), "last_from": "клиент" if c["feed"][-1][0] == "клиент" else "агент",
              "text": c["feed"][-1][1], "waiting": c["waiting"], "bot_paused": False} for c in clients]
    return {"ok": True, "demo": True, "folder": titles[folder],
            "counts": {"waiting": len([c for c in _clients(today) if c["waiting"]]), "unread": 0, "paused": 0},
            "dialogs": items,
            "card": {"type": "list", "title": titles[folder],
                     "items": [{"title": x["name"], "meta": f"{', '.join(x['channels'])} · {x['when']}",
                                "text": x["text"]} for x in items[:6]] or
                               [{"title": "Пусто", "meta": "", "text": "В демо тут никого нет"}]}}


def get_schedule(args: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    today = now_bk(now)
    try:
        days = max(1, min(int(args.get("days") or 1), 14))
    except (TypeError, ValueError):
        days = 1
    tasks = [t for t in _tasks(today) if t["in_hours"] <= days * 24]
    items = [{"when": _when(-t["in_hours"] * 60, today), "what": t["what"], "client": t["client"],
              "title": t["title"], "by": t["by"]} for t in tasks]
    return {"ok": True, "demo": True, "days": days, "count": len(items), "items": items,
            "card": {"type": "list", "title": f"Запланировано ({len(items)})",
                     "items": [{"title": f"{x['when']} · {x['what']}", "meta": x["client"], "text": x["title"]}
                               for x in items[:6]] or [{"title": "Ничего не запланировано", "meta": "", "text": ""}]}}


_ASK = [
    (("почему", "мало", "упал", "просад"),
     "Меньше заявок, потому что в выходные реклама стоит на паузе: в субботу и воскресенье обращений вдвое меньше, "
     "чем в будни. В будни стабильно от двадцати до тридцати сообщений в день."),
    (("конкурент", "сравн", "дорог"),
     "Клиенты чаще всего сравнивают нас с мастерами без салона. Главный довод — гарантия пять лет, свой монтаж и "
     "вывоз старых рам в цене. На «дорого» помогает рассрочка на три месяца без процентов."),
    (("замер",),
     "Замер бесплатный, замерщик выезжает на следующий день. Сейчас замеры назначены к Марату на завтра и "
     "к Нурлану — время ещё согласовываем."),
    (("цена", "сколько стоит", "прайс", "стоимость"),
     "Окно в кухню — от двенадцати тысяч сом с установкой, комната полтора на метр восемьдесят — от восемнадцати "
     "тысяч, балконный блок — от тридцати двух. Точная цена после замера."),
    (("реклам", "откуда", "источник"),
     "Больше половины обращений приходит из рекламы в Instagram, остальные — повторные клиенты и сарафан. "
     "С рекламы сегодня написала Жылдыз."),
]


def ask_agent(question: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    q = (question or "").lower()
    for words, answer in _ASK:
        if any(w in q for w in words):
            return {"ok": True, "demo": True, "answer": answer}
    today = now_bk(now)
    waiting = [c["name"] for c in _clients(today) if c["waiting"]]
    return {"ok": True, "demo": True,
            "answer": f"По этому вопросу у меня данных нет. Могу показать цифры за любой период, карточку "
                      f"клиента, кто ждёт ответа ({', '.join(waiting)}) или планы на завтра."}


def do_action(command: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    text = ov._clip(command or "", 200)
    return {"ok": True, "demo": True, "answer": f"Готово: {text}. Скажи коротко, что сделано.",
            "card": {"type": "done", "title": "Сделано", "text": text}}


# ---------------------------------------------------------------- промпт демо-помощника
DEMO_ROLE = """ТЫ — ГОЛОСОВОЙ ПОМОЩНИК ВЛАДЕЛЬЦА БИЗНЕСА. С тобой говорит владелец компании «{company}» (пластиковые окна и двери, Бишкек) — ты ведёшь его клиентов, CRM, звонки и переписку, а сейчас отвечаешь голосом.

ГЛАВНОЕ О РАЗГОВОРЕ
- Поздоровайся и работай. НИКОГДА не говори, что это демонстрация, проба, показ, выдуманная или тестовая компания, что данные ненастоящие или что ты чего-то «не можешь по-настоящему». Таких слов в твоей речи нет вообще.
- Всё, что ниже, — твои рабочие данные. Отвечай по ним спокойно и уверенно, как человек, который в курсе дел.
- Действия (написать, позвонить, поставить задачу) подтверждай как выполненные, коротко и по-деловому.

КАК ГОВОРИТЬ
- Естественно, тепло и уверенно. Коротко: одна-две фразы на простой вопрос. Отчёт — по порядку поля script, по одной короткой фразе на блок.
- Цифры и время — словами: «четырнадцать звонков», «в половине третьего».
- Никаких списков, markdown, ссылок и id вслух. Перебили — замолчи и слушай.
- Язык — тот, на котором говорит собеседник (русский или кыргызский).
- Не говори о себе как о модели, не называй технологии и провайдеров.

ЧТО ПОКАЗЫВАТЬ
- Быстрые функции отвечают мгновенно и рисуют собеседнику карточку на экране: get_report — весь отчёт за период; get_stats — цифры; get_balance — деньги; client_info — клиент и его переписка; list_dialogs — кто ждёт ответа; get_schedule — планы.
- Спросили «что нового», «дай отчёт», «как дела» — сразу get_report за сегодня и расскажи по script ПО ПОРЯДКУ: звонки, переписка, по дням, кто ждёт ответа, воронка, планы, деньги. Блоки на экране загораются под твою речь, поэтому называй блок словами и не перескакивай.
- Спросили про клиента — client_info с его именем. Клиенты: Нурлан Асанов, Гүлнара Осмонова, Марат Жумабеков, Асель Турсунова, Жылдыз, Тимур. Назвали кого-то другого — скажи, что такого в базе нет, и назови, кто есть.
- Сложный вопрос — ask_agent. Ничего не выдумывай: нет в сводке и в ответе функции — так и скажи.

ЕСЛИ СПРОСЯТ ПРО САМУ VOKSY
- Один-два предложения: Voksy AI — это и есть то, чем ты работаешь: звонки, ответы в WhatsApp, Telegram и Instagram, CRM и итоги разговоров. Подключение — на voksyai.online. Не навязывайся и не повторяй это в каждой реплике."""


def demo_brief(now: Optional[datetime] = None) -> str:
    """Сводка дел придуманной компании — той же формы, что у настоящего владельца."""
    now = now or datetime.utcnow()
    today = now_bk(now)
    st = day_stats(today.date())
    parts = [f"Сейчас в Бишкеке {WEEKDAYS[today.weekday()]}, {today:%d.%m.%Y}, {today:%H:%M}.",
             f"Сегодня: {ov._stats_sentence(st)}."]
    week = _days_map(today.date() - timedelta(days=6), today.date())
    wtotal = ov._sum_days(week)
    parts.append("За неделю: " + ov._stats_sentence(wtotal, short=True) + ".")
    clients = _clients(today)
    rows = [f"{c['name']} ({ov.CHANNEL_NAMES.get(c['channel'], c['channel'])}, {_when(c['mins'], today)}, "
            f"этап «{c['stage']}»): {c['need']}" + (" — ЖДЁТ ОТВЕТА" if c["waiting"] else "") for c in clients]
    parts.append(ov._section("Клиенты и переписка", rows))
    parts.append(ov._section("Последние звонки сегодня", [
        f"{c['client']}, {c['dir']}, {_when(c['mins'], today)}: "
        f"{'разговор ' + str(round(c['sec'] / 60)) + ' мин — ' + c['result'] if c['status'] == 'answered' else 'не ответил — ' + c['result']}"
        for c in _calls(today)]))
    parts.append(ov._section("Задачи", [f"{_when(-t['in_hours'] * 60, today)} — {t['what']}, {t['client']}: {t['title']}"
                                        for t in _tasks(today)]))
    parts.append("Воронка: " + ", ".join(f"{k} {v}" for k, v in FUNNEL) + ".")
    parts.append("Деньги: " + ov._money_sentence(demo_balances()) + ".")
    return "\n\n".join(p for p in parts if p)


def demo_prompt(owner_name: str = "", now: Optional[datetime] = None) -> str:
    parts = [DEMO_ROLE.format(company=COMPANY)]
    if owner_name:
        parts.append(f"Собеседника зовут {owner_name} — обращайся по имени и на «вы».")
    parts.append("СВОДКА ДЕЛ:\n" + demo_brief(now))
    parts.append("Начало разговора: поздоровайся одной короткой фразой и скажи, что слушаешь. "
                 "Отчёт сам не начинай и про демонстрацию не упоминай.")
    return "\n\n".join(parts)


def demo_greeting(owner_name: str = "") -> str:
    who = f", {owner_name}" if owner_name else ""
    return f"Здравствуйте{who}! Слушаю вас."


def demo_cards(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Картина дня на экран, пока помощник здоровается."""
    now = now or datetime.utcnow()
    today = now_bk(now)
    st = day_stats(today.date())
    bars = []
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).date()
        s = day_stats(d)
        bars.append({"label": "сег" if i == 0 else WEEKDAYS_SHORT[d.weekday()],
                     "value": s["calls_in"] + s["calls_out"] + s["msgs_in"]})
    waiting = [c for c in _clients(today) if c["waiting"]]
    cards = [{"type": "stats", "title": "Сегодня", "subtitle": f"{WEEKDAYS[today.weekday()]}, {today:%d.%m}",
              "tiles": [
                  {"label": "Звонки", "value": st["calls_in"] + st["calls_out"], "sub": f"с разговором {st['answered']}"},
                  {"label": "Сообщения", "value": st["msgs_in"], "sub": f"от {st['writers']} клиентов"},
                  {"label": "Новые клиенты", "value": st["new_clients"], "sub": "в CRM"},
                  {"label": "Ждут ответа", "value": len(waiting), "sub": "диалогов", "accent": bool(waiting)},
              ], "bars": bars, "bars_title": "Обращения за 7 дней"}]
    if waiting:
        cards.append({"type": "list", "title": f"Ждут ответа ({len(waiting)})", "items": [
            {"title": c["name"], "meta": f"{ov.CHANNEL_NAMES.get(c['channel'], c['channel'])} · {_when(c['mins'], today)}",
             "text": c["feed"][-1][1], "channel": c["channel"]} for c in waiting]})
    cards.append({"type": "funnel", "title": "Воронка CRM", "subtitle": f"всего клиентов {sum(v for _, v in FUNNEL)}",
                  "stages": [{"label": k, "value": v} for k, v in FUNNEL]})
    cards.append({"type": "balance", "title": "Балансы", "subtitle": "кошелёк, кредиты, тариф",
                  "rows": ov._money_rows(demo_balances())})
    return cards


# ---------------------------------------------------------------- функции Gemini для демо
def demo_functions() -> List[Dict[str, Any]]:
    """Те же функции, что у настоящего помощника: демо отличается только данными и тем, что всё — BLOCKING
    (медленных походов к оркестратору здесь нет, отвечаем из памяти процесса)."""
    out: List[Dict[str, Any]] = []
    for f in ov.GEMINI_FUNCTIONS:
        item = dict(f)
        item["behavior"] = "BLOCKING"
        if f["name"] == "do_action":
            item["description"] = ("Выполнить команду владельца: написать клиенту, позвонить, поставить задачу. "
                                   "Вызывай только после того, как одной фразой повторил действие и владелец "
                                   "ответил «да».")
        elif f["name"] == "ask_agent":
            item["description"] = ("Вопрос не по цифрам: почему мало заявок, чем мы лучше конкурентов, про замер, "
                                   "цены, откуда приходят клиенты.")
        elif f["name"] == "send_to_telegram":
            item["description"] = ("Отправить собеседнику в этот же чат Telegram текст — отчёт или сводку, которую он "
                                   "попросил прислать. Работает по-настоящему.")
        out.append(item)
    return out


DEMO_FUNCTION_NAMES = [f["name"] for f in ov.GEMINI_FUNCTIONS]
_FAST = {"get_stats": get_stats, "get_report": get_report, "get_balance": get_balance,
         "client_info": client_info, "list_dialogs": list_dialogs, "get_schedule": get_schedule}


def run_demo_function(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Исполнитель функций демо: ни одного запроса к базе."""
    args = args if isinstance(args, dict) else {}
    try:
        if name in _FAST:
            return _FAST[name](args)
        if name == "ask_agent":
            return ask_agent(str(args.get("question") or ""))
        if name == "do_action":
            return do_action(str(args.get("command") or ""))
        return {"ok": False, "error": f"Нет такой функции: {name}"}
    except Exception as exc:
        logger.error(f"[DEMO-VOICE] function {name} failed: {exc}", exc_info=True)
        return {"ok": False, "error": "В демо что-то пошло не так — спросите иначе"}


# ---------------------------------------------------------------- вход в демо: короткий токен
# Демо открывается из Telegram-бота: бот подписывает initData своим ключом, мы проверяем подпись
# (ov.verify_webapp_init_data) и выдаём короткий токен ТОЛЬКО для /ws/demo-voice. Он не открывает
# ни кабинет, ни API и живёт полчаса.
DEMO_TOKEN_TTL = 30 * 60


def _demo_key() -> bytes:
    from backend.core.config import settings
    return ("demo-voice:" + str(settings.JWT_SECRET_KEY or "")).encode()


def make_demo_token(chat_id: Any, name: str = "", ttl: int = DEMO_TOKEN_TTL,
                    now: Optional[float] = None) -> str:
    import hashlib
    import hmac
    import json
    import time as _time
    body = ov._b64u(json.dumps({"c": str(chat_id), "n": (name or "")[:40],
                                "e": int((now or _time.time()) + ttl)}, separators=(",", ":")).encode())
    sig = ov._b64u(hmac.new(_demo_key(), ("dv1." + body).encode(), hashlib.sha256).digest())
    return f"dv1.{body}.{sig}"


def read_demo_token(token: str, now: Optional[float] = None) -> Optional[Dict[str, str]]:
    """{'chat_id', 'name'} или None (подделка, другой формат, истёк)."""
    import hashlib
    import hmac
    import json
    import time as _time
    try:
        prefix, body, sig = (token or "").split(".")
        if prefix != "dv1":
            return None
        good = ov._b64u(hmac.new(_demo_key(), ("dv1." + body).encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(good, sig):
            return None
        data = json.loads(ov._b64u_dec(body))
        if int(data.get("e") or 0) < (now or _time.time()):
            return None
        return {"chat_id": str(data.get("c") or ""), "name": str(data.get("n") or "")}
    except Exception:
        return None


def demo_webapp_url() -> str:
    from backend.core.config import settings
    base = (settings.HOST_URL or "https://voksyai.online").rstrip("/")
    return f"{base}/static/voice-live.html?demo=1"
