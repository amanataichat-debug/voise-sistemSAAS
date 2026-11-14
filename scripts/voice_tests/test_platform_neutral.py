"""Общие правила платформы нейтральны: годятся любому бизнесу, а не только продаже самого Voksy (10.11.2026).

Подсказки голосу, базовые правила звонка и правила разбора звонков/переписки получают агенты ВСЕХ клиентов
(салон, автосервис, магазин, клиника). Туда успела просочиться логика продажи Voksy: «на «дорого» сравни с
зарплатой менеджера», «пришли ссылку на регистрацию, администратор поможет подключить», «произноси «Вокси Эй-Ай»»,
кыргызские образцы про наш тариф Старт, «позвоню, послушаете агента сами». Агент стоматологии повторял бы это.
Наша продажа живёт в инструкции Анри и его базе знаний (docs/), а не в коде. Этот тест сторожит границу.
"""
import ast
import logging
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")
logging.disable(logging.CRITICAL)

from backend.websockets import live_client as lc, voice_hints as vh
from backend.services import agent_prompts as ap

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


# То, что относится только к продаже Voksy: бренд, персонаж, наши тарифы и наша воронка подключения.
VOKSY_ONLY = re.compile(
    r"Voksy|Вокси|Анри|зарплат\w* менеджер|ссылку на регистрацию|администратор сегодня же|тариф Старт|Старт —|"
    r"айына жети миң|послушаете сами|услышать агента|Не объясняй платформу|CRMге",
    re.IGNORECASE)


def leaks(text):
    return sorted({m.group(0) for m in VOKSY_ONLY.finditer(text or "")})


fns = [{"name": "search_knowledge_base", "description": "Ищет ответ в базе знаний."},
       {"name": "hangup_call", "description": "Завершает звонок."}]
cfg = lc.OpenAILiveClient("k", type("A", (), {"id": "x", "system_prompt": "<КАРТОЧКА>", "functions": {},
                                              "voice": "cedar", "greeting_message": None})(),
                          "cid", telephony=True).build_session_config()
gpt_voice = cfg["instructions"].replace("<КАРТОЧКА>", "")
gemini_voice = vh.gemini_prompt_head("Puck", "ru", "Здравствуйте") + vh.build_gemini_voice_hints(fns, "Puck", True)

check("GPT-Live: подсказки голосу без продажи Voksy", not leaks(gpt_voice), leaks(gpt_voice))
check("GPT-Live: бэкенд делегирования без продажи Voksy", not leaks(lc.BACKEND_SYSTEM_PROMPT), leaks(lc.BACKEND_SYSTEM_PROMPT))
check("Gemini Live: подсказки голосу без продажи Voksy", not leaks(gemini_voice), leaks(gemini_voice))
check("базовые правила звонка (любой агент) без продажи Voksy",
      not leaks(ap.VOICE_AGENT_PROMPT_BASE), leaks(ap.VOICE_AGENT_PROMPT_BASE))

# Правила разбора звонков и переписки — строки внутри _build_postcall_input (комментарии не в счёт)
_src = open(os.path.join(ROOT, "backend/services/agent_orchestrator.py"), encoding="utf-8").read()
_fn = next(n for n in ast.walk(ast.parse(_src)) if isinstance(n, ast.FunctionDef) and n.name == "_build_postcall_input")
_strings = " ".join(n.value for n in ast.walk(_fn) if isinstance(n, ast.Constant) and isinstance(n.value, str))
check("правила разбора звонков и переписки (любой агент) без продажи Voksy", not leaks(_strings), leaks(_strings))

# Сами правила при этом остались: продажа, горячий клиент, цена цифрой, не эхо — для любого бизнеса
# 14.11.2026 (1411-voice-lean): у GPT-Live правила продажи — короткие, без жёсткого сценария «ТЕМП ПРОДАЖИ»
check("общие правила продаж на месте: цена цифрой, закрыть согласного, следующий шаг",
      "назови цифру сразу" in gpt_voice and "Согласился — закрывай" in gpt_voice
      and "следующий шаг" in gpt_voice and "ТЕМП ПРОДАЖИ" not in gpt_voice and "ЗВОНОК КЛИЕНТУ" in _strings and "ОДНО СООБЩЕНИЕ ЗА ОТВЕТ" in _strings)

# А продажа Voksy — у Анри: в его сценарии и базе знаний
_anri = open(os.path.join(ROOT, "docs/voice_agent_prompt_anri.md"), encoding="utf-8").read()
_orch = open(os.path.join(ROOT, "docs/orchestrator_prompt_anri.md"), encoding="utf-8").read()
check("наша продажа — в инструкциях Анри (регистрация, «Вокси Эй-Ай», сравнение с менеджером)",
      "Вокси Эй-Ай" in _anri and "регистрац" in _anri and "менеджер" in _anri and "ссылку на регистрацию" in _orch)

# Журнал звонка подсказывает владельцу, где его инструкция вредит звонку (у любого клиента)
import types
from backend.services import agent_voice_context as avc
_tpl = open(os.path.join(ROOT, "backend/static/js/instruction-templates.js"), encoding="utf-8").read()
_w = avc.instruction_warnings(types.SimpleNamespace(voice_additional_instructions="Ты — <имя>, менеджер компании «<название>».",
                                                    additional_instructions="Пиши коротко."))
check("журнал звонка: незаполненные места шаблона видны владельцу", len(_w) == 1 and "<имя>" in _w[0], _w)
_w = avc.instruction_warnings(types.SimpleNamespace(voice_additional_instructions="Говори коротко. " * 600,
                                                    additional_instructions=""))
check("журнал звонка: слишком длинная инструкция для звонков — предупреждение", len(_w) == 1 and "длинная" in _w[0], _w)
check("журнал звонка: нормальная инструкция и агент без инструкций — без предупреждений",
      avc.instruction_warnings(types.SimpleNamespace(voice_additional_instructions="Ты — Айгуль.", additional_instructions=None)) == []
      and avc.instruction_warnings(None) == [])
_sip = open(os.path.join(ROOT, "backend/api/sip_gateway.py"), encoding="utf-8").read()
check("SIP-звонок пишет эти предупреждения в журнал", "agent_voice_context.instruction_warnings(agent_cfg)" in _sip)
# сами тексты шаблонов — строки между «var voice» и объявлением объекта (имя объекта VoksyInstructionTemplates не в счёт)
_tpl_text = " ".join(re.findall(r"'((?:[^'\\]|\\.)*)'", _tpl[_tpl.find("var voice"):_tpl.find("window.VoksyInstructionTemplates = {")]))
check("шаблоны кабинета — для любого бизнеса (без Voksy) и с местами для заполнения",
      len(_tpl_text) > 2000 and not leaks(_tpl_text) and "<название>" in _tpl_text and "confirmFilled" in _tpl,
      (len(_tpl_text), leaks(_tpl_text)))

# Кыргызский везде один — разговорный бишкекский (10.11.2026, владелец: «не литературный, как говорит молодёжь
# в Бишкеке»). Раньше звонок говорил по-бишкекски, а голосовые сообщения, перевод в инбоксе и инструмент
# голосового — «чистым литературным, без русских слов».
_files = {p: open(os.path.join(ROOT, p), encoding="utf-8").read() for p in (
    "backend/services/agent_voice_service.py", "backend/services/agent_prompts.py", "backend/services/agent_tools.py",
    "backend/services/inbox_service.py", "backend/services/agent_orchestrator.py", "backend/websockets/fish_llm_client.py",
    "backend/websockets/live_client.py")}
_strings_all = {p: " ".join(n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, str))
                for p, src in _files.items()}
_book = {p: re.findall(r"чистый литературный|адабий|литературная бишкекская|не вставляй русские слова|без русских слов",
                       t, re.IGNORECASE) for p, t in _strings_all.items()}
check("нигде не требуется книжный кыргызский «без русских слов»", not any(_book.values()),
      {p: v for p, v in _book.items() if v})
from backend.services.agent_voice_service import SPEECH_NORMS
check("кыргызский — разговорный бишкекский: звонок, каскад, переписка, голосовые, перевод в инбоксе",
      "как говорят в Бишкеке" in lc.LIVE_VOICE_HEAD and "разговорном бишкекском" in SPEECH_NORMS["ky"]
      and all("разговорный бишкекский" in _strings_all[p] for p in (
          "backend/services/agent_prompts.py", "backend/services/agent_tools.py", "backend/services/inbox_service.py",
          "backend/services/agent_orchestrator.py", "backend/websockets/fish_llm_client.py")))

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
