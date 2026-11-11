"""Переписка как у живого продавца (ветка 1111-chat-live): разбор переписки, проверка текста
перед отправкой, чистка «(Бишкек)» и прайса, звонок на сегодня после недозвона, разминувшегося
с сообщением клиента. Без сети и БД: запросы к базе подменены."""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

from backend.services import chat_conscience as cc
from backend.services.message_style import clean_client_text
import backend.services.agent_tools as at
import backend.services.agent_mode as am
import backend.services.agent_contact_phones as acp
from backend.services.agent_prompts import build_time_block

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


NOW = datetime.utcnow()


def msg(direction, body, minutes_ago, sent_by=None):
    return NS(direction=direction, body=body, sent_by=sent_by,
              sent_at=NOW - timedelta(minutes=minutes_ago), created_at=None)


# ── Джанара: «После 18:00 позвоните пожалуйста» ─────────────────────────────
janara = [
    msg("outbound", "Janara, здравствуйте! Через полчаса созвонимся, как договаривались. Вам удобно? "
                    "Если нет, напишите, когда лучше, перенесу.", 40),
    msg("inbound", "Здравствуйте!", 14),
    msg("outbound", "Здравствуйте, Janara. Давайте созвонимся на 2 минуты, удобно сейчас?", 14),
    msg("inbound", "После 18:00 позвоните пожалуйста", 3),
]
s = cc.situation(janara, NOW)
check("janara: согласие на звонок с временем", s.call_settled and s.time_named, vars(s))
block = cc.situation_block(janara, NOW)
check("janara: разбор говорит «согласился, не переспрашивай»",
      "СОГЛАСИЛСЯ" in block and "Не переспрашивай" in block, block)
r = cc.send_refusal(janara, "Хорошо, позвоню сегодня после 18:00, чтобы связь была лучше. "
                            "Будет удобно в 18:00 (Бишкек)?", NOW)
check("janara: «будет удобно в 18:00?» после согласия — отказ", r and "согласился" in r, r)
check("janara: «Хорошо, позвоню сегодня в 18:00.» — можно",
      cc.send_refusal(janara, "Хорошо, позвоню сегодня в 18:00.", NOW) is None)

# ── массаж: «я на работе / как установить? / цена какая» ─────────────────────
massage = [
    msg("outbound", "Понял. Для такого потока агент может принимать обращения и записывать клиентов на массаж.", 31),
    msg("outbound", "Давайте позвоню вам на 2 минуты, чтобы вы сами послушали, как он работает. Удобно сейчас?", 31),
    msg("inbound", "Я на работе", 30),
    msg("inbound", "Как это установить?", 30),
    msg("inbound", "Цена какая", 30),
]
s = cc.situation(massage, NOW)
check("massage: клиент занят, но спросил — отвечаем, без созвона", s.deflected and s.asked_question, vars(s))
r = cc.send_refusal(massage, "Понял. Когда будет удобно поговорить 2 минуты: сегодня после работы или завтра?", NOW)
check("massage: снова зовёт на созвон занятого — отказ", r is not None, r)
ok_text = "Установка простая: регистрируетесь, админ помогает подключить.\nСтарт: 7 500 сом в месяц"
check("massage: ответ на вопросы без созвона — можно", cc.send_refusal(massage, ok_text, NOW) is None)

after = massage + [
    msg("outbound", "Демо на 2 минуты удобнее сегодня после работы или завтра?", 20),
    msg("inbound", "Хорошо,понятно. Я поговорю с менеджером", 19),
    msg("inbound", "Спасибо", 19),
]
s = cc.situation(after, NOW)
check("manager: «поговорю с менеджером» — не согласие, а «не сейчас»", s.deflected and not s.call_settled, vars(s))
r = cc.send_refusal(after, "Когда вам удобнее поговорить: сегодня после работы или завтра?", NOW)
check("manager: тот же вопрос второй раз — отказ", r is not None, r)
check("manager: «Хорошо, будем на связи.» — можно",
      cc.send_refusal(after, "Хорошо, будем на связи.", NOW) is None)

# ── «Да» → не переспрашивать «точно сейчас?» ─────────────────────────────────
yes = [msg("outbound", "Позвоню на 2 минуты, удобно сейчас?", 2), msg("inbound", "Да", 1)]
check("yes: согласие", cc.situation(yes, NOW).call_settled)
check("yes: «Точно сейчас позвонить?» — отказ", cc.send_refusal(yes, "Точно сейчас позвонить?", NOW) is not None)
check("yes: «Звоню, ожидайте.» — можно", cc.send_refusal(yes, "Звоню, ожидайте.", NOW) is None)

# «Нет» — не согласие
no = [msg("outbound", "Позвоню на 2 минуты, удобно сейчас?", 2), msg("inbound", "Нет, не надо звонить", 1)]
check("no: «нет, не надо звонить» — не согласие", not cc.situation(no, NOW).call_settled)

# Согласие на НЕзвонок («пришлю пример?» → «да») не делает его звонком
ex = [msg("outbound", "Пришлю пример, как это выглядит у салона?", 2), msg("inbound", "Да", 1)]
check("example: «да» на пример — не звонок", not cc.situation(ex, NOW).call_settled)

# Вопросы владельца (sent_by=owner) — не вопросы агента
own = [msg("outbound", "Когда удобно созвониться?", 5, sent_by="owner"), msg("inbound", "Добрый день", 1)]
check("owner: вопрос владельца агенту не засчитан",
      cc.send_refusal(own, "Когда удобно созвониться?", NOW) is None)

# Новое сообщение без вопроса — всегда можно
check("no question: без вопроса — можно", cc.send_refusal(after, "Понял вас, хорошего дня.", NOW) is None)
check("empty: пустой тред — можно", cc.send_refusal([], "Когда удобно?", NOW) is None)

# ── чистка текста ───────────────────────────────────────────────────────────
t = clean_client_text("Хорошо, позвоню сегодня после 18:00. Будет удобно в 18:00 (Бишкек)?")
check("clean: «(Бишкек)» убран", "Бишкек" not in t and "(" not in t, t)
t = clean_client_text("Позвоню в 18:00 по бишкекскому времени.")
check("clean: «по бишкекскому времени» убрано", t == "Позвоню в 18:00.", t)
t = clean_client_text("Позвоню в 10:00 по Бишкеку, хорошо?")
check("clean: «по Бишкеку» убрано", t == "Позвоню в 10:00, хорошо?", t)
t = clean_client_text("AI Voice — 1 990 сом/мес\nСтарт — 7 500 сом/мес\n- Profi - 12 500 сом в месяц")
check("clean: прайс столбцом «Название: цена»",
      t == "AI Voice: 1 990 сом/мес\nСтарт: 7 500 сом/мес\nProfi: 12 500 сом в месяц", t)
check("clean: числа через тире по-прежнему", clean_client_text("Цена 10–15 тысяч") == "Цена 10-15 тысяч")
check("clean: обычное тире между словами", clean_client_text("Это быстро — за 5 минут") == "Это быстро, за 5 минут")

# ── блок времени: клиенту без часового пояса ────────────────────────────────
tb = build_time_block(round_to_minutes=0)
check("time block: клиенту без «(Бишкек)»", "КЛИЕНТУ" in tb and "Никогда не" in tb and "СЕГОДНЯ" in tb)
check("time block: «ВСЕГДА … (Бишкек)» для пользователя убрано", "Пользователю ВСЕГДА" not in tb)

# ── недозвон, разминувшийся с сообщением клиента ────────────────────────────
from backend.core.timezone_utils import BISHKEK

now_local = datetime.now(BISHKEK).replace(hour=14, minute=5, second=0, microsecond=0)
missed_at = (now_local - timedelta(minutes=4)).astimezone(timezone.utc)          # 14:01 недозвон
client_msg_at = (now_local - timedelta(minutes=5)).astimezone(timezone.utc)      # 14:00 «после 18»
at._bishkek_now = lambda: now_local
acp.missed_call_today = lambda *a, **k: NS(created_at=missed_at.replace(tzinfo=None), id="m1")
acp.today_call_stats = lambda *a, **k: {"answered": False, "dialed_out": 1, "last_answered": False}
acp.voice_call_today = lambda *a, **k: True


def contacted_since(db, cid, since):
    since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    return client_msg_at > since


am.client_contacted_since = contacted_since
contact = NS(id="c1", user_id="u1", phone="996700111222", alt_phones=None, call_phone=None,
             last_called_at=missed_at.replace(tzinfo=None))
six_pm = now_local.replace(hour=18, minute=0).astimezone(timezone.utc)
g = at._check_call_guards(contact, six_pm, allow_shift=False, window=None, db=object(), user_id="u1")
check("race: клиент написал «после 18» перед недозвоном — звонок на 18:00 сегодня разрешён", g.get("ok"), g)
soon = now_local.replace(hour=15, minute=0).astimezone(timezone.utc)
g = at._check_call_guards(contact, soon, allow_shift=False, window=None, db=object(), user_id="u1")
check("race: через час после недозвона — всё ещё нельзя (2 ч минимум)", not g.get("ok"), g)

client_msg_at = (now_local - timedelta(hours=3)).astimezone(timezone.utc)        # писал давно
g = at._check_call_guards(contact, six_pm, allow_shift=False, window=None, db=object(), user_id="u1")
check("race: клиент писал давно и молчит после недозвона — запрещено", not g.get("ok"), g)

# планировщик: та же проверка при исполнении задачи
from backend.core.task_scheduler import TaskScheduler
import backend.services.agent_tools as _at
client_msg_at = (now_local - timedelta(minutes=5)).astimezone(timezone.utc)
missed_at_gate = datetime.now(timezone.utc) - timedelta(hours=4)
acp.missed_call_today = lambda *a, **k: NS(created_at=missed_at_gate.replace(tzinfo=None), id="m1")
client_msg_at = missed_at_gate - timedelta(minutes=5)
ts = TaskScheduler.__new__(TaskScheduler)
gate = ts._missed_today_gate(contact, NS(user_id="u1", id="t1"), NS(working_hours_start=None, working_hours_end=None), object())
check("scheduler: звонок по просьбе клиента не переносится на завтра", gate is None, gate)
client_msg_at = missed_at_gate - timedelta(hours=3)
gate = ts._missed_today_gate(contact, NS(user_id="u1", id="t1"), NS(working_hours_start=None, working_hours_end=None), object())
check("scheduler: клиент молчит — по-прежнему переносим", gate is not None, gate)

# ── execute_tool: отказ только в клиентском контуре и не больше двух раз ────
import backend.services.whatsapp_service as wa
wa.get_thread = lambda db, cid, limit=20: after
am.check_tool = lambda *a, **k: None
at._paused_send_refusal = lambda *a, **k: None
sent = []


async def fake_send(args, user_id, agent_config, db, **kw):
    sent.append(args.get("text"))
    return {"ok": True}


at.fn_whatsapp_send_message = fake_send
args = {"agent_contact_id": "c1", "text": "Когда вам удобнее поговорить: сегодня после работы или завтра?"}
ctx = {"surface": "postcall", "user_id": "u1", "agent_config_id": "a1", "agent_config": NS(id="a1")}
out = json.loads(asyncio.run(at.execute_tool("whatsapp_send_message", dict(args), ctx, NS(rollback=lambda: None))))
check("execute_tool: повтор вопроса в разборе — не отправлен", out.get("ok") is False and not sent, out)
owner_ctx = {"surface": "owner_chat", "user_id": "u1", "agent_config_id": "a1", "agent_config": NS(id="a1")}
asyncio.run(at.execute_tool("whatsapp_send_message", dict(args), owner_ctx, NS(rollback=lambda: None)))
check("execute_tool: команда владельца из его чата — не блокируется", len(sent) == 1, sent)
asyncio.run(at.execute_tool("whatsapp_send_message", dict(args), ctx, NS(rollback=lambda: None)))
asyncio.run(at.execute_tool("whatsapp_send_message", dict(args), ctx, NS(rollback=lambda: None)))
check("execute_tool: после двух отказов за разбор — отправляем", len(sent) == 2, sent)

print(f"\n{'OK' if not failures else 'FAILED'}: {failures} провал(ов)")
sys.exit(1 if failures else 0)
