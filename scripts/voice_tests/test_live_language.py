"""Язык разговора GPT-Live держит код (live_language.py) — звонок a128fccf, 22.11.2026. Без сети и БД."""
import asyncio
import logging
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)

import backend.websockets.handler_live as h
import backend.websockets.live_client as lc
import backend.websockets.live_language as ll
from backend.websockets.call_log import CallLogRecorder

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# ── детектор ────────────────────────────────────────────────────────────────────────────────────────────
rq = ll.requested_language
check("«Бизге кыргызча сүйлөй турган адам керек» → кыргызский", rq("Саламатсызбы. Бизге кыргызча сүйлөй турган адам керек") == "ky")
check("«давайте по-кыргызски» → кыргызский", rq("давайте по-кыргызски") == "ky")
check("искажение линии «кабельский» → кыргызский", rq("можно на кабельском, кабельский") == "ky")
check("«говорите по-русски» → русский", rq("говорите, пожалуйста, по-русски") == "ru")
check("«орусча сүйлөңүз» → русский", rq("орусча сүйлөңүз") == "ru")
check("«кыргызча түшүнбөйм» → русский (не понимает по-кыргызски)", rq("мен кыргызча түшүнбөйм") == "ru")
check("решает последнее упоминание", rq("не по-русски, а кыргызча") == "ky")
check("обычная фраза — без просьбы", rq("сколько стоит тариф?") is None)
check("кыргызская речь узнаётся", ll.is_kyrgyz_speech("Бизде кафе бар, канча турат?"))
check("одно «ооба» — не речь", not ll.is_kyrgyz_speech("ооба"))
check("русская фраза — не кыргызская", not ll.is_kyrgyz_speech("Скажите, а сколько это стоит в месяц?"))

lock = ll.LanguageLock()
check("явная просьба закрепляет язык сразу", lock.feed_partial("кыргызча сүйлөңүз", 1.0) == "ky" and lock.locked == "ky")
check("повтор просьбы — без второй инструкции", lock.feed_partial("кыргызча сүйлөңүз деп жатам", 2.0) is None)
check("просьба о русском заменяет кыргызский", lock.feed_partial("лучше по-русски", 3.0) == "ru")
check("кыргызская речь не перебивает явную просьбу о русском",
      lock.feed_turn("Бизде кафе бар, канча турат?") is None and lock.feed_turn("Мен азыр иштеп жатам, кийин") is None
      and lock.locked == "ru")
l2 = ll.LanguageLock()
check("одна кыргызская реплика — ещё не закрепляем", l2.feed_turn("Бизде кафе бар, канча турат?") is None)
check("две кыргызские реплики подряд — кыргызский", l2.feed_turn("Мен өзүм жооп берем, убактым жок") == "ky")
l3 = ll.LanguageLock()
l3.feed_turn("Бизде кафе бар, канча турат?")
l3.feed_turn("Нет, скажите лучше, как это работает у вас")
check("русская фраза между ними обнуляет счёт", l3.feed_turn("Мен өзүм жооп берем") is None and l3.locked is None)

check("инструкция кыргызского — на кыргызском и без готовых реплик",
      "кыргызча гана" in ll.LOCK_INSTRUCTIONS["ky"] and "менеджерге өткөрбө" in ll.LOCK_INSTRUCTIONS["ky"]
      and "«" not in ll.LOCK_INSTRUCTIONS["ky"].replace("«орусча сүйлөйлү»", ""))
check("инструкция короче лимита session.instructions.append (2000)", max(len(v) for v in ll.LOCK_INSTRUCTIONS.values()) < 2000)
check("в тексте нет латинских букв внутри кириллицы",
      not any(c.isascii() and c.isalpha() for c in ll.LOCK_INSTRUCTIONS["ky"].split("\n", 1)[1]))

# ── Decisions: просьба о языке — отдельный ответ, не wants_human ──────────────────────────────────────────
q = lc.CALL_DECISION_TRIGGER["decisions"]["questions"][0]
check("Decisions знает wants_language", {"value": "wants_language"} in q["choices"] and "NOT wants_human" in q["instructions"])
check("заметка кыргызской линии: по-кыргызски, просьба о кыргызском — не менеджер",
      "бул менеджер эмес" in lc.KYRGYZ_LINE_NOTE)
check("подсказка «нужен менеджер»: просьба о языке — перейти самому",
      "that is a language request" in h.INTENT_HINTS["wants_human"])
check("подсказка «запутался»: не представляться заново, кыргызский — отвечать по-кыргызски",
      "introduce yourself again" in h.MOOD_HINTS["confused"] and "answer in Kyrgyz" in h.MOOD_HINTS["confused"])


# ── реплей звонка a128fccf через настоящий хендлер ─────────────────────────────────────────────────────
class FC:
    session_id = "s"
    io_rate = 8000
    enabled_functions = []
    delegation_model = "gpt-6-luna"

    def __init__(self):
        self.thinking, self.instructions = [], []

    async def append_thinking(self, text, delegation_id=None):
        self.thinking.append(text)
        return True

    async def append_instructions(self, text, delegation_id=None):
        self.instructions.append(text)
        return True

    def decode_output_b64(self, x):
        return x


def session():
    cl = CallLogRecorder(channel="phone")
    s = h.LiveVoiceSession(websocket=None, assistant=types.SimpleNamespace(), client=FC(), db=None,
                           client_id="c", telephony=True, call_log=cl)

    async def em(d):
        return None
    s.emit = em
    return s, cl


def decision(intent="none", mood="neutral"):
    return {"event": {"type": "decision.completed", "decision": {"answers": [
        {"name": "caller_intent", "choice": intent}, {"name": "caller_mood", "choice": mood},
        {"name": "caller_cue", "choice": "none"}]}}}


async def say(s, role, text):
    await s._on_live_event({"type": f"session.{'input' if role == 'user' else 'output'}_transcript.delta",
                                "delta": text, "start_ms": 0, "end_ms": 1})
    await asyncio.sleep(0)


async def replay():
    s, cl = session()
    await say(s, "assistant", "Добрый день! Это Анри из Вокси.")
    await say(s, "user", "Скажите, а с Юля у вас номер был")
    # первые реплики: «confused» дважды подряд — подсказку «объясни проще» не даём (агент заново представлялся)
    await s._on_decision(decision(mood="confused"))
    await s._on_decision(decision(mood="confused"))
    check("начало звонка: «confused» не подсказывает «объясни заново»", not s.client.thinking, s.client.thinking)
    await say(s, "assistant", "Расскажите, чем занимаетесь?")
    await say(s, "user", "Саламатсызбы. Бизге кыргызча сүйлөй турган адам керек")
    await asyncio.sleep(0.01)
    check("просьба о кыргызском → постоянная инструкция на кыргызском, сразу, до ответа",
          len(s.client.instructions) == 1 and s.client.instructions[0].startswith(ll.LOCK_INSTRUCTIONS["ky"]),
          len(s.client.instructions))
    check("смена языка — тот же разговор: в инструкции то, что клиент уже сказал (не переспрашивать)",
          "кайра сураба" in s.client.instructions[0] and "Скажите, а с Юля у вас номер был" in s.client.instructions[0])
    await s._on_decision(decision(intent="wants_human"))
    check("Decisions wants_human на просьбу о языке → подсказки «передай менеджеру» нет",
          not any("менеджер" in t for t in s.client.thinking), s.client.thinking)
    journal = " | ".join(e.get("text", "") if isinstance(e, dict) else str(e) for e in cl.events)
    check("журнал: язык закреплён и просьба о языке не принята за менеджера",
          "Язык закреплён: кыргызский" in journal and "не менеджера" in journal, journal[-300:])
    await say(s, "assistant", "Ооба, түшүндүм.")
    await say(s, "user", "Бизге кыргызча сүйлөгөн менеджер керек")
    await asyncio.sleep(0.01)
    check("повтор просьбы — вторая инструкция не уходит", len(s.client.instructions) == 1)

    # настоящая просьба о менеджере без языка — подсказка остаётся
    s2, _ = session()
    await say(s2, "user", "Соедините меня с вашим директором, пожалуйста")
    await s2._on_decision(decision(intent="wants_human"))
    check("просьба о живом человеке без языка — прежняя подсказка менеджера",
          s2.client.thinking == [h.INTENT_HINTS["wants_human"]])

    # Decisions wants_language на кыргызской речи без слова «кыргызча»
    s3, _ = session()
    await say(s3, "user", "Мен өзүм жооп берем, бизде кафе бар")
    await say(s3, "assistant", "Понял.")
    await say(s3, "user", "сиз эмне деп жатасыз")
    await s3._on_decision(decision(intent="wants_language"))
    check("wants_language на кыргызской речи → кыргызский закреплён", s3.lang_lock.locked == "ky"
          and s3.client.instructions and s3.client.instructions[-1].startswith(ll.LOCK_INSTRUCTIONS["ky"]))


asyncio.run(replay())

# ── метрики журнала: язык и шаблонная речь ─────────────────────────────────────────────────────────────
check("ответ по-русски на закреплённом кыргызском — несовпадение",
      not ll.reply_in_language("ky", "Кстати, вы сейчас говорите как раз с таким голосовым агентом."))
check("кыргызский ответ с русскими словами (аралаш) — совпадение",
      ll.reply_in_language("ky", "Ооба, түшүндүм, тариф боюнча азыр айтып берем, заявкаңызды жазып коёбуз."))
check("короткое «ооба» не считается", ll.reply_in_language("ky", "Ооба.") and ll.reply_in_language("ru", "Да, конечно."))
ll._PHRASE_MEMORY.clear()
first = ll.repeated_from_past_calls("a1", ["Добрый день!", "Смотрите, агент отвечает всем клиентам сразу и днём и ночью."])
second = ll.repeated_from_past_calls("a1", ["Смотрите, агент отвечает всем клиентам сразу и днём и ночью. А у вас как?"])
check("фраза из прошлого звонка того же ассистента — найдена; первый звонок — без повторов",
      first == [] and second == ["смотрите агент отвечает всем клиентам сразу и днём и ночью"], second)
check("другой ассистент — своя память", ll.repeated_from_past_calls("a2", ["Смотрите, агент отвечает всем клиентам сразу и днём и ночью."]) == [])


async def diag():
    s, cl = session()
    s.started_at = time.time() - 60
    await say(s, "assistant", "Добрый день! Это Анри.")
    await say(s, "user", "Кыргызча сүйлөңүзчү")
    await asyncio.sleep(0.01)
    await say(s, "assistant", "Кстати, вы сейчас говорите как раз с таким голосовым агентом, он отвечает всем.")
    await say(s, "user", "кыргызча деп жатам го")
    await say(s, "assistant", "Ооба, кечиресиз, азыр кыргызча сүйлөйм, сиздин бизнес кандай?")
    await say(s, "user", "")
    s._flush_log("assistant")
    s._log_diagnostics(60.0)
    text = " | ".join(e.get("text", "") for e in cl.events)
    check("журнал: агент перешёл на язык не сразу — видно, через сколько ответов",
          "перешёл не сразу: через 1 ответ" in text, text[-400:])
    check("журнал: ответов не на языке клиента — цифрой", "ответов не на языке клиента (кыргызский): 1" in text)


asyncio.run(diag())

# ── предупреждения владельцу (журнал звонка + «Проверить базу знаний») ───────────────────────────────
import backend.services.agent_voice_context as avc
card = types.SimpleNamespace(language="ky", greeting_message="Здравствуйте! Чем могу помочь?")
ag = types.SimpleNamespace(voice_additional_instructions="Ты — Анри.", additional_instructions="",
                           inbound_first_phrase="Добрый день! Это Анри из Вокси.", eleven_assistant=card)
w = avc.instruction_warnings(ag)
check("кыргызская линия, а приветствие только по-русски — предупреждение владельцу",
      any("Линия кыргызская" in x for x in w), w)
ag.inbound_first_phrase = "Саламатсызбы! Здравствуйте! Это Анри."
check("двуязычное приветствие — без предупреждения", not any("Линия кыргызская" in x for x in avc.instruction_warnings(ag)))
ag.voice_additional_instructions = ("Скажи «Саат бештерде чалсам болобу?». Говори «Ой, автосалон — жакшы экен!». "
                                    "Спроси «Күнүнө канча заявка келет?». Ответ «Вы что, робот? — Робот, да».")
check("готовые фразы в инструкции звонков — предупреждение «агент повторит дословно»",
      any("готовых фраз" in x for x in avc.instruction_warnings(ag)))

# ── входящий звонок: «я вам звоню» не говорить ──────────────────────────────────────────────────────────
from backend.services import sip_gateway_service as sgs
call = types.SimpleNamespace(direction="inbound", caller="996700000000", call_metadata={})
ctx = sgs.SipGatewayService.call_context_text(call)
check("контекст входящего: позвонил клиент, не представляться снова", "не представляйся снова" in ctx and "Это входящий" in ctx)

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
