"""
Ответ агента в мессенджере (Telegram личный / WhatsApp / Instagram) —
несколькими короткими сообщениями, как пишет человек, а не одной простынёй.

split_reply() режет текст: строки «---» от модели → абзацы → предложения,
упаковывая по 1–2 предложения в сообщение. send_in_parts() отправляет части
по очереди через тулзу канала. Лимит проверяется перед первой частью, чтобы
начатый ответ не обрывался на середине; каждая часть в лимит засчитывается.
"""

import asyncio
import random
import re
from typing import Any, Awaitable, Callable, Dict, List

SINGLE_MESSAGE_MAX = 90
PART_TARGET = 120
LONG_SENTENCE = 260
MIN_PART = 35
MAX_PARTS = 6

_MARKER = re.compile(r"(?m)^[ \t]*-{3,}[ \t]*$")
_PARAGRAPH = re.compile(r"\n[ \t]*\n+")
_SENTENCE_END = re.compile(r"[.!?…]+[\"»”')\]]*\s+")
_CLAUSE_END = re.compile(r"(?:[;:]|\s—|,)\s+")
_LIST_LINE = re.compile(r"(?m)^[ \t]*(?:[-•*]|\d+[.)])\s+")
# Сокращения, после которых точка — не конец предложения.
_ABBREV = {
    "т.е", "т.к", "т.д", "т.п", "т.н", "др", "пр", "см", "напр", "руб", "коп",
    "тыс", "млн", "млрд", "г", "гг", "ул", "д", "кв", "стр", "им", "мин", "сек",
    "ч", "шт", "ок", "etc", "e.g", "i.e", "vs",
}


def _cut_at(text: str, pattern: re.Pattern) -> List[str]:
    out, start = [], 0
    for m in pattern.finditer(text):
        if pattern is _SENTENCE_END:
            word = text[start:m.start()].split()[-1:] or [""]
            last = word[0].rstrip(".").lower()
            if last in _ABBREV or (len(last) == 1 and last.isalpha()):
                continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def _join(a: str, b: str, sep: str) -> str:
    if sep == "auto":
        sep = "\n\n" if ("\n" in a or "\n" in b) else " "
    return a + sep + b


def _pack(pieces: List[str], target: int, sep: str = " ") -> List[str]:
    out: List[str] = []
    for p in pieces:
        if out and len(_join(out[-1], p, sep)) <= target:
            out[-1] = _join(out[-1], p, sep)
        else:
            out.append(p)
    return out


def _split_paragraph(par: str) -> List[str]:
    if len(par) <= PART_TARGET:
        return [par]
    if len(_LIST_LINE.findall(par)) >= 2:
        # Список держим целым сообщением, длинный — режем по строкам.
        return _pack([ln.strip() for ln in par.split("\n") if ln.strip()], LONG_SENTENCE, sep="\n")
    pieces: List[str] = []
    for sentence in _cut_at(par, _SENTENCE_END):
        if len(sentence) > LONG_SENTENCE:
            pieces.extend(_pack(_cut_at(sentence, _CLAUSE_END), PART_TARGET))
        else:
            pieces.append(sentence)
    return _merge_short(_pack(pieces, PART_TARGET))


def _merge_short(parts: List[str]) -> List[str]:
    # Обрывок предложения в 3–4 слова отдельным сообщением выглядит как сбой.
    out: List[str] = []
    for p in parts:
        if out and (len(p) < MIN_PART or len(out[-1]) < MIN_PART) \
                and len(out[-1]) + 1 + len(p) <= PART_TARGET + MIN_PART:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def _cap(parts: List[str]) -> List[str]:
    # Лишнее не обрезаем: перепаковываем части крупнее, пока не уложимся.
    parts = list(parts)
    while len(parts) > MAX_PARTS:
        i = min(range(len(parts) - 1), key=lambda k: len(parts[k]) + len(parts[k + 1]))
        parts[i:i + 2] = [_join(parts[i], parts[i + 1], "auto")]
    return parts


def split_reply(text: str) -> List[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    if _MARKER.search(raw):
        blocks = [b.strip() for b in _MARKER.split(raw) if b.strip()]
        parts: List[str] = []
        for b in blocks:
            parts.extend([b] if len(b) <= LONG_SENTENCE else _split_paragraph(b))
        return _cap(parts)
    if len(raw) <= SINGLE_MESSAGE_MAX and "\n\n" not in raw:
        return [raw]
    parts = []
    for par in _PARAGRAPH.split(raw):
        par = par.strip()
        if par:
            parts.extend(_split_paragraph(par))
    return _cap(parts)


def typing_seconds(text: str) -> float:
    """Пауза «печатает…» перед очередной частью: 1–2.5 с, длиннее текст — дольше."""
    return round(min(2.5, max(1.0, 0.8 + 0.01 * len(text or ""))) + random.uniform(-0.2, 0.2), 2)


SendOne = Callable[[Dict[str, Any], int, bool], Awaitable[Dict[str, Any]]]


async def send_in_parts(send_one: SendOne, args: Dict[str, Any], *, pause_between: bool) -> Dict[str, Any]:
    """
    send_one(args, part_index, skip_rate_limit) отправляет одно сообщение.
    pause_between=True — канал сам не показывает «печатает…», ждём здесь.
    """
    parts = split_reply(args.get("text") or "")
    if len(parts) <= 1:
        return await send_one(args, 0, False)

    call_args = dict(args)
    first: Dict[str, Any] = {}
    for i, part in enumerate(parts):
        if i > 0 and pause_between:
            await asyncio.sleep(typing_seconds(part))
        call_args = dict(call_args, text=part)
        res = await send_one(call_args, i, i > 0)
        if not res.get("ok"):
            if i == 0:
                return res
            return dict(first, parts_sent=i, parts_total=len(parts),
                        warning=f"Отправлено {i} из {len(parts)} сообщений: {res.get('error')}")
        if i == 0:
            first = res
            # Контакт мог быть создан по номеру — продолжения шлём уже ему.
            if res.get("agent_contact_id") and not call_args.get("agent_contact_id"):
                call_args["agent_contact_id"] = res["agent_contact_id"]
                call_args.pop("phone", None)
    return dict(first, parts_sent=len(parts))
