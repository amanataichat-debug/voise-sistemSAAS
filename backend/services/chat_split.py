"""
Ответ агента в мессенджере (Telegram личный / WhatsApp / Instagram) — одним
сообщением или двумя-тремя, как решила модель.

Ветка 1111-clean-system. Раньше текст длиннее 90 символов резался кодом на куски
до 6 сообщений по предложениям: терялись переносы строк, прайс «столбцом»
разваливался, фразы отрывались от мысли. Теперь делит ответ только модель —
строкой «---» между частями; код лишь соблюдает предел MAX_PARTS (лишние части
склеивает через пустую строку) и ничего не трогает внутри части.

send_in_parts() отправляет части по очереди через тулзу канала. Лимит проверяется
перед первой частью, чтобы начатый ответ не обрывался на середине.
"""

import asyncio
import random
import re
from typing import Any, Awaitable, Callable, Dict, List

MAX_PARTS = 3

_MARKER = re.compile(r"(?m)^[ \t]*-{3,}[ \t]*$")


def _cap(parts: List[str]) -> List[str]:
    # Лишнее не обрезаем: склеиваем хвост с последней разрешённой частью.
    if len(parts) <= MAX_PARTS:
        return parts
    return parts[:MAX_PARTS - 1] + ["\n\n".join(parts[MAX_PARTS - 1:])]


def split_reply(text: str) -> List[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    if not _MARKER.search(raw):
        return [raw]
    parts = [b.strip() for b in _MARKER.split(raw) if b.strip()]
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
