"""
Язык разговора GPT-Live держит КОД, а не только промпт (ветка 2211-live-kyrgyz, 22.11.2026).

Звонок a128fccf (gpt-live-1.1, линия «ky»): клиент сказал «Бизге кыргызча сүйлөй турган адам керек» (нам нужен
тот, кто говорит по-кыргызски). Native Decisions пометил это как wants_human, хендлер подсказал «передай менеджеру»,
агент одной кыргызской фразой пообещал перезвон менеджера, дальше съехал на русский («Кстати, вы сейчас говорите…»
— строка из промпта) и положил трубку. Правило «попросил язык — держи до конца» лежало в середине 13 тыс. символов
русского промпта, и модель его не удержала.

Гайд OpenAI (live-prompting, «Language and pronunciation»): «Write the prompt and examples in the language you want
the assistant to speak». Поэтому, как только клиент ЯВНО попросил язык (или дважды целой фразой говорит
по-кыргызски), хендлер шлёт session.instructions.append — постоянную инструкцию на ЭТОМ языке. Она действует до
конца сессии; явная просьба о другом языке её заменяет.

Только детекция и тексты; без сети — проверяется тестом scripts/voice_tests/test_live_language.py.
"""
from __future__ import annotations

import re
from typing import Optional

# Явная просьба о кыргызском. Линия 8 кГц коверкает слово: «кабельский», «кыргыский» (звонок a72651fc).
_KY_REQUEST = re.compile(
    r"кыргызча|кыргыз\s*тил|по[\s-]*кыргыз|на\s+кыргыз|кыргызск|кыргыск|кабельск|kyrgyzcha|кыргызыча",
    re.IGNORECASE,
)
# Явная просьба о русском.
_RU_REQUEST = re.compile(r"орусча|орус\s*тил|по[\s-]*русски|на\s+русском|русский\s+язык|russian", re.IGNORECASE)
# «Не понимаю / не знаю кыргызский» — это просьба о РУССКОМ, а не о кыргызском.
_NEGATION = re.compile(
    r"түшүнбө|тушунбо|билбейм|билбейт|не\s+понима|не\s+говорю|не\s+знаю|плохо\s+(?:понима|говор|знаю)",
    re.IGNORECASE,
)
# Признаки кыргызской речи в расшифровке: буквы ң ө ү и частые слова (в русской речи их нет).
_KY_LETTERS = re.compile(r"[ңөүҢӨҮ]")
_KY_WORDS = re.compile(
    r"\b(?:ооба|жок|макул|канча|кандай|эмне|бизде|сиз|сизге|сизде|мен|менин|керек|азыр|кийин|жакшы|рахмат|"
    r"болот|болбойт|түшүндүм|салам|саламатсызбы|кечиресиз|айтып|бар|экен|деп|үчүн|менен)\b",
    re.IGNORECASE,
)


def requested_language(text: str) -> Optional[str]:
    """Явная просьба клиента о языке в реплике: «ky», «ru» или None. Решает последнее упоминание."""
    t = " ".join((text or "").split())
    if not t:
        return None
    ky = [m.start() for m in _KY_REQUEST.finditer(t)]
    ru = [m.start() for m in _RU_REQUEST.finditer(t)]
    if ky and _NEGATION.search(t) and not ru:
        return "ru"  # «кыргызча түшүнбөйм» — не понимаю по-кыргызски
    if not ky and not ru:
        return None
    if ky and (not ru or ky[-1] > ru[-1]):
        return "ky"
    return "ru"


def is_kyrgyz_speech(text: str) -> bool:
    """Целая реплика похожа на кыргызскую речь (не одно «ооба» и не одно русское слово)."""
    t = " ".join((text or "").split())
    words = re.findall(r"\w+", t)
    if len(words) < 3:
        return False
    letters = len(_KY_LETTERS.findall(t))
    ky_words = len(_KY_WORDS.findall(t))
    return letters >= 2 or (letters >= 1 and ky_words >= 1) or ky_words >= 2


def mentions_language(text: str) -> bool:
    """В реплике есть разговор о языке — значит просьба «нужен человек, говорящий по-кыргызски» не о менеджере."""
    t = text or ""
    return bool(_KY_REQUEST.search(t) or _RU_REQUEST.search(t))


# Инструкции на том языке, на котором агент должен говорить (совет OpenAI). Без готовых реплик: модель подбирает
# слова сама; бизнес-слова по-русски — норма бишкекской речи.
LOCK_INSTRUCTIONS = {
    "ky": (
        "# Тил: кыргызча (бул эң маанилүү эреже)\n"
        "Кардар кыргызча сүйлөшкүсү келет. Мындан ары ушул чалуунун аягына чейин ар бир жообуңду кыргызча гана айт, "
        "бир да сүйлөмдү орусча кошпо. Бишкекте кадимки адамдар кандай сүйлөсө, ошондой сүйлө: жөнөкөй, жылуу, "
        "кыска сүйлөмдөр менен, китептик сөздөрсүз. Тариф, заявка, ссылка, ватсап, сайт сыяктуу сөздөр орусча калса "
        "болот. Инструкциядагы орусча сүйлөмдөрдү сөзмө-сөз которбо — оюңду өз сөзүң менен айт. Тил жөнүндө "
        "түшүндүрбө жана кечирим сураба, менеджерге өткөрбө — жөн эле кыргызча уланта бер, кардардын суроосуна жооп "
        "бер. Кардар өзү «орусча сүйлөйлү» десе гана орусчага өт."
    ),
    "ru": (
        "# Язык: русский (это самое важное правило)\n"
        "Собеседник хочет говорить по-русски. До конца звонка отвечай только по-русски: живой разговорный язык, "
        "короткие фразы, без канцелярита. О языке не рассуждай и не извиняйся — просто продолжай разговор. На "
        "кыргызский переходи, только если человек сам попросит."
    ),
}

# Смена языка — тот же разговор (владелец 22.11.2026: «на русском у него одна память, на кыргызском другая — переспросил
# сколько заявок, хотя клиент уже сказал по-русски»). К инструкции добавляем то, что клиент уже рассказал.
_EARLIER = {
    "ky": "\nКардар буга чейин айткандары (бул маалымат сенде бар, кайра сураба, ошол жерден уланта бер): ",
    "ru": "\nЧто собеседник уже рассказал (это ты знаешь, не переспрашивай, продолжай с того же места): ",
}


def lock_instruction(lang: str, earlier_turns=None, limit: int = 600) -> str:
    """Постоянная инструкция языка + то, что клиент уже сказал (последние реплики, не длиннее limit)."""
    text = LOCK_INSTRUCTIONS[lang]
    turns = [" ".join(str(t).split()) for t in (earlier_turns or []) if str(t).strip()]
    tail, size = [], 0
    for t in reversed(turns):
        if len(re.findall(r"\w+", t)) < 3:
            continue  # «алло», «ооба» — не факты
        if size + len(t) > limit:
            break
        tail.insert(0, t)
        size += len(t)
    if tail:
        text += _EARLIER[lang] + " / ".join(tail)
    return text[:1990]


LOCK_LOG = {
    "ky": "Язык закреплён: кыргызский — постоянная инструкция голосовой модели на кыргызском",
    "ru": "Язык закреплён: русский — постоянная инструкция голосовой модели",
}


class LanguageLock:
    """
    Решает, когда закрепить язык. feed_partial — на каждом фрагменте реплики клиента (явная просьба срабатывает
    сразу, до ответа модели); feed_turn — на законченной реплике (две кыргызские реплики подряд → кыргызский).
    Возвращает язык, который надо закрепить сейчас, или None.
    """

    KY_TURNS_TO_LOCK = 2

    def __init__(self) -> None:
        self.locked: Optional[str] = None
        self.reason = ""
        self.ky_turns = 0
        self.last_request_at = 0.0

    def _set(self, lang: str, reason: str) -> Optional[str]:
        if lang == self.locked:
            return None
        self.locked = lang
        self.reason = reason
        return lang

    def feed_partial(self, text: str, now: float = 0.0) -> Optional[str]:
        lang = requested_language(text)
        if not lang:
            return None
        self.last_request_at = now
        return self._set(lang, "request")

    def feed_turn(self, text: str) -> Optional[str]:
        if is_kyrgyz_speech(text):
            self.ky_turns += 1
        elif len(re.findall(r"\w+", text or "")) >= 4:
            self.ky_turns = 0  # целая русская фраза обнуляет счёт
        # Кыргызская речь не перебивает явную просьбу о русском.
        if self.locked is None and self.ky_turns >= self.KY_TURNS_TO_LOCK:
            return self._set("ky", "speech")
        return None


# ── Метрики для журнала звонка (22.11.2026): после тест-звонка — цифры, а не «агент тупит» ────────────────────
def reply_in_language(lang: str, text: str) -> bool:
    """Ответ агента на закреплённом языке? Короткие ответы («ооба», «да») не считаем."""
    t = " ".join((text or "").split())
    if len(re.findall(r"\w+", t)) < 4:
        return True
    letters = len(_KY_LETTERS.findall(t))
    if lang == "ky":
        return is_kyrgyz_speech(t)
    return letters < 2


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_PHRASE_MEMORY: dict = {}  # ключ ассистента → список множеств фраз последних звонков (в памяти воркера)
PHRASE_MEMORY_CALLS = 20


def _norm_phrases(texts) -> set:
    out = set()
    for t in texts or []:
        for s in _SENT_SPLIT.split(" ".join(str(t).split())):
            words = re.findall(r"\w+", s.lower())
            if len(words) >= 5:
                out.add(" ".join(words))
    return out


def repeated_from_past_calls(key, assistant_texts) -> list:
    """
    Фразы этого звонка, которые агент уже говорил в прошлых звонках того же ассистента (шаблонная речь).
    Помнит последние PHRASE_MEMORY_CALLS звонков в памяти воркера; сам звонок запоминает.
    Приветствие (первую реплику) вызывающий не передаёт — оно одинаковое по определению.
    """
    if not key:
        return []
    phrases = _norm_phrases(assistant_texts)
    past = _PHRASE_MEMORY.setdefault(str(key), [])
    seen = set().union(*past) if past else set()
    repeated = sorted(p for p in phrases if p in seen)
    past.append(phrases)
    del past[:-PHRASE_MEMORY_CALLS]
    return repeated
