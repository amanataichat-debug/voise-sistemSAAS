"""Демо голосового агента (ветка 0811-voice-jarvis): /ws/demo-voice, данные выдуманной компании,
Telegram-бот демо. Без сети и без базы.

Проверяем главное: демо НЕ ХОДИТ В БАЗУ (ни одной сессии SQLAlchemy за весь разговор), выдаёт карточки
той же формы, что настоящий помощник (иначе окно разговора сломается), токен `dv1.` открывает только
голос демо и истекает, лимиты не дают сжечь минуты Google, а функция действия без «да» не срабатывает.

    python scripts/voice_tests/test_demo_voice.py
"""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, json, logging, sys
from datetime import datetime

sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="https://example.com", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k",
                  JWT_SECRET_KEY="test-secret", DEMO_BOT_TOKEN="111:demo")
logging.disable(logging.CRITICAL)

import backend.core.config  # noqa: F401 — первым, как в app.py
from backend.services import owner_voice as ov
from backend.services import owner_voice_demo as demo
from backend.websockets.gemini_client import gemini_schema

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


def run(coro, timeout=5):
    return asyncio.run(asyncio.wait_for(coro, timeout))


# ---------- данные выдуманной компании ----------
check("компания и агент названы", bool(demo.COMPANY) and "Окна" in demo.COMPANY, demo.COMPANY)
_r = demo.get_report({"period": "today"})
check("отчёт: сцены в порядке рассказа",
      [s["id"] for s in _r["card"]["scenes"]][:2] == ["calls", "messages"]
      and _r["card"]["scenes"][-1]["id"] == "money", [s["id"] for s in _r["card"]["scenes"]])
check("отчёт: у каждой сцены есть подсказки речи", all(s["cue"] for s in _r["card"]["scenes"]))
check("отчёт: в script ровно по строке на сцену (речь и картинка идут в ногу)",
      len(_r["script"]) == len(_r["card"]["scenes"]) >= 5, (len(_r["script"]), len(_r["card"]["scenes"])))
check("отчёт помечен как демонстрация", _r["demo"] is True)
check("отчёт за неделю рисует столбики по дням",
      any(s["id"] == "days" and len(s["bars"]) == 7 for s in demo.get_report({"period": "week"})["card"]["scenes"]))
check("цифры дня одинаковы в течение дня", demo.day_stats(datetime(2026, 11, 5).date()) == demo.day_stats(datetime(2026, 11, 5).date()))
check("цифры разных дней отличаются", demo.day_stats(datetime(2026, 11, 5).date()) != demo.day_stats(datetime(2026, 11, 6).date()))
check("в выходные тише, чем в будни",
      demo.day_stats(datetime(2026, 11, 8).date())["msgs_in"] < 20)
_b = demo.get_balance({})
check("балансы демо: кошелёк, кредиты, тариф", [r["label"] for r in _b["card"]["rows"]] == ["Кошелёк", "Кредиты агента", "Тариф"], _b["card"]["rows"])
_c = demo.client_info({"query": "нурлан"})
check("клиент находится по имени", _c["found"] and _c["name"].startswith("Нурлан"), _c.get("name"))
check("у клиента есть переписка и этап", _c["card"]["lines"] and _c["stage"], _c.get("stage"))
check("по номеру телефона тоже находится", demo.client_info({"query": "+996 700 11 22 33"})["found"])
_no = demo.client_info({"query": "Вася Пупкин"})
check("незнакомого не выдумываем, а перечисляем кто есть", not _no["found"] and "Нурлан" in _no["message"], _no.get("message"))
check("кто ждёт ответа — только ждущие", all(d["waiting"] for d in demo.list_dialogs({"folder": "waiting"})["dialogs"]))
check("бот на паузе: в демо никого", demo.list_dialogs({"folder": "paused"})["dialogs"] == [])
check("расписание на завтра не пустое", demo.get_schedule({"days": 2})["count"] >= 2)
check("цифры за период считаются", demo.get_stats({"period": "week"})["calls"]["total"] > 0)
check("вопрос не по цифрам получает ответ по демо-данным", "реклам" in demo.ask_agent("почему мало заявок")["answer"].lower())
_unknown = demo.ask_agent("посчитай налоги за третий квартал")["answer"].lower()
check("на незнакомый вопрос — «данных нет», без слов о демонстрации",
      "данных нет" in _unknown and not any(w in _unknown for w in ("демонстрац", "выдуман", "придуман")), _unknown[:80])
_act = demo.do_action("написать Нурлану в WhatsApp")
check("действие подтверждается по-деловому, без слов о демонстрации",
      _act["ok"] and _act["card"]["title"] == "Сделано"
      and not any(w in (_act["answer"] + _act["card"]["text"]).lower() for w in ("демо", "выдуман", "не отправлено")),
      _act["answer"])

# ---------- сводка и промпт ----------
_brief = demo.demo_brief()
check("сводка: сегодня, неделя, клиенты, звонки, задачи, воронка, деньги",
      all(x in _brief for x in ("Сегодня:", "За неделю:", "Клиенты и переписка", "Последние звонки",
                                "Задачи", "Воронка:", "Деньги:")), _brief[:120])
_prompt = demo.demo_prompt("Азамат")
check("промпт: прямой запрет говорить про демонстрацию и выдуманность",
      "НИКОГДА не говори, что это демонстрация" in _prompt and "выдуманная или тестовая компания" in _prompt)
check("промпт: ни слова «демо» в самих данных и правилах речи",
      "СВОДКА ДЕЛ:" in _prompt and "придуманная компания" not in _prompt)
check("промпт: по имени", "Азамат" in _prompt)
check("промпт: отчёт по порядку script", "script ПО ПОРЯДКУ" in _prompt)
check("промпт: клиенты демо перечислены", "Нурлан Асанов" in _prompt and "Жылдыз" in _prompt)
check("промпт влезает в разумный размер", 3000 < len(_prompt) < 12000, len(_prompt))
_hi = demo.demo_greeting("Азамат")
check("приветствие обычное: здоровается по имени и слушает",
      _hi.startswith("Здравствуйте, Азамат") and "Слушаю" in _hi
      and not any(w in _hi.lower() for w in ("демонстрац", "выдуман", "придуман")), _hi)
_cards = demo.demo_cards()
check("карточки на старте: день, ждут ответа, воронка, балансы",
      [c["type"] for c in _cards] == ["stats", "list", "funnel", "balance"], [c["type"] for c in _cards])
check("в подписях карточек нет слова «демо»",
      not any("демо" in str(c.get("subtitle", "")).lower() for c in _cards), [c.get("subtitle") for c in _cards])

# ---------- функции Gemini ----------
_fn = {f["name"]: f for f in demo.demo_functions()}
check("список функций демо = список настоящего помощника", set(_fn) == set(ov.FUNCTION_NAMES), sorted(_fn))
check("в демо все функции отвечают сразу (BLOCKING)", all(f["behavior"] == "BLOCKING" for f in _fn.values()))
for name, f in _fn.items():
    check(f"схема {name} не меняется очисткой под Gemini", gemini_schema(f["parameters"]) == f["parameters"])
check("описание do_action без слов о демонстрации",
      "ответил «да»" in _fn["do_action"]["description"]
      and not any(w in _fn["do_action"]["description"].lower() for w in ("демо", "выдуман")),
      _fn["do_action"]["description"][:70])
check("исполнитель демо знает все функции, кроме телеграма (он в сессии)",
      all(demo.run_demo_function(n, {"period": "today", "query": "нурлан", "folder": "waiting", "days": 1,
                                     "question": "привет", "command": "позвонить"}).get("ok")
          for n in demo.DEMO_FUNCTION_NAMES if n != "send_to_telegram"))
check("несуществующая функция — отказ, а не падение", demo.run_demo_function("drop_database", {})["ok"] is False)

# ---------- ни одного запроса к базе ----------
import backend.db.session as dbs
_opened = []
_real = dbs.SessionLocal
dbs.SessionLocal = lambda *a, **kw: (_opened.append(1), _real(*a, **kw))[1]
try:
    demo.get_report({"period": "week"})
    demo.client_info({"query": "марат"})
    demo.demo_brief()
    demo.demo_cards()
    demo.run_demo_function("get_stats", {"period": "month"})
finally:
    dbs.SessionLocal = _real
check("демо не открывает ни одной сессии базы данных", _opened == [], len(_opened))

# ---------- короткий токен демо ----------
_tok = demo.make_demo_token(777, "Азамат")
check("токен демо начинается с dv1.", _tok.startswith("dv1."), _tok[:4])
check("токен читается обратно", demo.read_demo_token(_tok) == {"chat_id": "777", "name": "Азамат"})
check("подделанный токен не проходит", demo.read_demo_token(_tok[:-3] + "aaa") is None)
check("токен кабинета сюда не подходит", demo.read_demo_token(ov.make_voice_token("u", "a")) is None)
check("токен демо не открывает голос кабинета", ov.read_voice_token(_tok) is None)
import time as _time
check("через полчаса токен истекает", demo.read_demo_token(_tok, now=_time.time() + demo.DEMO_TOKEN_TTL + 1) is None)
check("адрес Mini App демо", demo.demo_webapp_url() == "https://example.com/static/voice-live.html?demo=1",
      demo.demo_webapp_url())

# ---------- доступ и лимиты ----------
from backend.api import demo_voice as dv

dv._starts.clear(); dv._all_starts.clear()
_acc = dv.demo_access(_tok)
check("вход по токену Telegram", _acc["ok"] and _acc["chat_id"] == "777" and _acc["name"] == "Азамат", _acc)
check("мусор вместо токена не пускает", dv.demo_access("qwerty")["code"] == "unauthorized")
dv._starts.clear(); dv._all_starts.clear()
for i in range(dv.PER_DAY):
    dv.demo_access(_tok)
_over = dv.demo_access(_tok)
check("больше лимита в сутки — вежливый отказ", _over["ok"] is False and _over["code"] == "demo_limit", _over)
check("назавтра снова можно (сам токен к тому времени, конечно, уже другой)",
      dv.quota_error("tg:777", now=_time.time() + 86401) is None)
dv._starts.clear(); dv._all_starts.clear()
for i in range(dv.PER_HOUR):
    dv.demo_access(demo.make_demo_token(1000 + i))
check("общий лимит в час бережёт минуты Google", dv.demo_access(demo.make_demo_token(9999))["code"] == "demo_busy")
dv._starts.clear(); dv._all_starts.clear()
_saved = os.environ.pop("DEMO_BOT_TOKEN")
check("без DEMO_BOT_TOKEN демо выключено", dv.demo_access(_tok)["code"] == "not_configured" and not dv.enabled())
os.environ["DEMO_BOT_TOKEN"] = _saved

# ---------- адрес вебхука: владельцу ничего придумывать не нужно ----------
_auto = dv.webhook_secret()
check("секрет вебхука считается из токена сам", len(_auto) == 32 and _auto.isalnum(), _auto)
os.environ["DEMO_BOT_TOKEN"] = "222:other"
check("другой бот — другой адрес", dv.webhook_secret() != _auto)
os.environ["DEMO_BOT_TOKEN"] = "111:demo"
check("секрет не меняется от запуска к запуску", dv.webhook_secret() == _auto)
os.environ["DEMO_BOT_WEBHOOK_SECRET"] = "ручной"
check("заданный руками секрет сильнее", dv.webhook_secret() == "ручной")
del os.environ["DEMO_BOT_WEBHOOK_SECRET"]
check("адрес вебхука с секретом внутри",
      dv.webhook_url() == f"https://example.com/api/demo/webhook/{_auto}", dv.webhook_url())

# ---------- вебхук демо-бота ----------
_sent = []


async def fake_call(token, method, payload):
    _sent.append((method, payload))
    return {"ok": True}


from backend.services.agent_telegram_service import AgentTelegramService
AgentTelegramService._call = staticmethod(fake_call)


class FakeRequest:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


dv._starts.clear(); dv._all_starts.clear()
_sent.clear()
run(dv.demo_webhook(dv.webhook_secret(), FakeRequest({"message": {"chat": {"id": 777}, "text": "/start"}})))
check("/start отвечает кнопкой Mini App",
      any(m == "sendMessage" and "web_app" in json.dumps(p.get("reply_markup") or {}) for m, p in _sent), _sent[:1])
check("/start ставит кнопку меню чата", any(m == "setChatMenuButton" for m, _p in _sent))
_sent.clear()
run(dv.demo_webhook(dv.webhook_secret(), FakeRequest({"message": {"chat": {"id": 777}, "text": "привет"}})))
check("на обычное сообщение тоже кнопка", any(m == "sendMessage" for m, _p in _sent))
from fastapi import HTTPException
try:
    run(dv.demo_webhook("не-тот-секрет", FakeRequest({})))
    check("чужой секрет вебхука отбивается", False)
except HTTPException as exc:
    check("чужой секрет вебхука отбивается", exc.status_code == 404)

# ---------- настройка бота при старте: один токен и ничего больше ----------
_api = []


async def fake_api(token, method, payload):
    _api.append((method, payload))
    if method == "getMe":
        return {"id": 111, "username": "voksy_demo_bot"}
    if method == "getWebhookInfo":
        return {"url": _hook_now[0]}
    return {"ok": True}


AgentTelegramService._call = staticmethod(fake_api)
_hook_now = [""]
dv._registered = False
dv._token_belongs_to_agent = lambda token: False
_res = run(dv.register_demo_bot())
check("при старте вебхук прописывается сам", _res["ok"] and _res["changed"] and _res["bot"] == "voksy_demo_bot", _res)
check("ставится и кнопка Mini App, и команда", {"setWebhook", "setMyCommands", "setChatMenuButton"} <= {m for m, _p in _api})
_hook_now[0] = dv.webhook_url()
_api.clear()
dv._registered = False
_res = run(dv.register_demo_bot())
check("второй запуск ничего не трогает", _res["ok"] and _res["changed"] is False and "setWebhook" not in {m for m, _p in _api}, _res)
dv._token_belongs_to_agent = lambda token: True
_api.clear()
_res = run(dv.register_demo_bot(force=True))
check("рабочий бот агента демо НЕ перехватывает", _res["ok"] is False and "ОТДЕЛЬНЫЙ бот" in _res["error"], _res.get("error"))
check("и ничего не настраивает", "setWebhook" not in {m for m, _p in _api})
dv._token_belongs_to_agent = lambda token: False
AgentTelegramService._call = staticmethod(fake_call)

# ---------- вход из Mini App ----------
import hashlib, hmac
from urllib.parse import urlencode


def init_data(bot_token, user_id=777, name="Азамат"):
    fields = {"auth_date": str(int(_time.time())),
              "user": json.dumps({"id": user_id, "first_name": name}, ensure_ascii=False)}
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


_auth = run(dv.demo_webapp_auth(dv.WebAppAuth(init_data=init_data("111:demo"))))
check("Mini App демо выдаёт токен dv1.", _auth["token"].startswith("dv1.") and _auth["name"] == "Азамат", _auth["name"])
check("в ответе — компания и предел разговора", _auth["company"] == demo.COMPANY and _auth["max_seconds"] > 60)
try:
    run(dv.demo_webapp_auth(dv.WebAppAuth(init_data=init_data("222:чужой-бот"))))
    check("подпись чужого бота не проходит", False)
except HTTPException as exc:
    check("подпись чужого бота не проходит", exc.status_code == 401)

# ---------- сессия демо ----------
from backend.websockets.handler_demo_voice import DemoVoiceSession


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data, mode="text"):
        self.sent.append(data)


class FakeClient:
    is_connected = True
    function_executor = None

    def set_assistant_speaking(self, v):
        pass


_ws, _cl = FakeWS(), FakeClient()
_sess = DemoVoiceSession(_ws, _cl, owner_name="Азамат", chat_id="777")
check("сессия демо короче разговора владельца", _sess.max_seconds == demo.MAX_SECONDS < ov.MAX_SESSION_SECONDS,
      (_sess.max_seconds, ov.MAX_SESSION_SECONDS))
check("исполнитель функций — сессия демо", _cl.function_executor == _sess.execute)
_res = run(_sess.execute("c1", "get_report", {"period": "today"}))
check("функция отчёта работает через сессию", _res["ok"] and _res["card"]["type"] == "report")
_sess.observe({"type": "transcript.delta", "role": "user", "delta": "нет, не надо"})
_res = run(_sess.execute("c2", "do_action", {"command": "позвонить Нурлану", "confirmed": True}))
check("владелец сказал «нет» — действие не показываем", _res["ok"] is False and _res["status"] == "rejected", _res)
_sess._agent_spoke = True
_sess.observe({"type": "transcript.delta", "role": "user", "delta": "да, давай"})
_res = run(_sess.execute("c3", "do_action", {"command": "позвонить Нурлану", "confirmed": True}))
check("после «да» показываем, как это выглядело бы", _res["ok"] and _res["card"]["type"] == "done", _res.get("card"))
_res = run(_sess.execute("c4", "удалить_всё", {}))
check("выдуманной функции нет", _res["ok"] is False)
_sent.clear()
_res = run(_sess.execute("c5", "send_to_telegram", {"text": "Отчёт за сегодня"}))
check("отчёт уходит в тот же чат Telegram", _res["ok"] and any(m == "sendMessage" and p["chat_id"] == "777" for m, p in _sent), _sent[:1])
_no_chat = DemoVoiceSession(FakeWS(), FakeClient(), owner_name="", chat_id=None)
check("из браузера отправлять в Telegram нечего",
      run(_no_chat.execute("c6", "send_to_telegram", {"text": "x"}))["ok"] is False)
_ws.sent.clear()
run(_sess._save_prefs({"address": "chef"}))
check("настройки в демо не сохраняются, но окно получает ответ",
      _ws.sent and _ws.sent[0]["type"] == "prefs.saved" and _ws.sent[0]["ok"] is False, _ws.sent[:1])
_sess._finish_sync("client_closed")
check("конец демо-разговора ничего не списывает и не пишет", True)

print()
print("ИТОГО провалов:", failures)
sys.exit(1 if failures else 0)
