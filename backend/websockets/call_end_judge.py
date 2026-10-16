# backend/websockets/call_end_judge.py
"""
Закончился ли телефонный разговор — решает быстрая текстовая модель по концу диалога, а не список слов.

Зачем: голосовая модель GPT-Live сама почти не вызывает hangup_call, и линия висит после «Пока.»; фразы
прощания по ключевым словам хрупки (особенно кыргызский, который распознаётся с ошибками). Модель читает
последние реплики и отвечает END / CONTINUE на любом языке.

Вызывается только когда в разговоре уже несколько секунд тишина (см. LiveVoiceSession._maybe_judge), поэтому
стоит копейки: короткий запрос, ответ в одно слово. Любая ошибка → None (вызывающий переходит на запасной путь).
"""

import re
from typing import Any, Dict, List, Optional

import httpx

from backend.core.logging import get_logger

logger = get_logger(__name__)

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MAX_TURNS = 8
MAX_TURN_CHARS = 300

SYSTEM_PROMPT = (
    "Ты определяешь, закончился ли телефонный разговор. Дан конец транскрипта (КЛИЕНТ и АГЕНТ). "
    "Транскрипция бывает с ошибками, особенно на кыргызском; разговор идёт на русском или кыргызском. "
    "Сейчас обе стороны уже несколько секунд молчат.\n"
    "Ответь ОДНИМ словом:\n"
    "END — если разговор явно завершён: стороны попрощались, или клиент ясно дал понять, что говорить больше не о чем, "
    "а агент ответил на это завершающей фразой (или сам закончил разговор прощанием), и продолжения не ждут.\n"
    "CONTINUE — если разговор ещё идёт или есть малейшее сомнение: клиент мог задуматься, агент задал вопрос и "
    "ответа нет, клиент попрощался, а агент ещё не ответил, обсуждение не доведено до договорённости.\n"
    "Оценивай смысл всего конца диалога, а не отдельные слова. При сомнении — CONTINUE."
)


def format_turns(turns: List[Dict[str, Any]]) -> str:
    lines = []
    for t in turns[-MAX_TURNS:]:
        who = "КЛИЕНТ" if t.get("role") == "user" else "АГЕНТ"
        text = " ".join(str(t.get("text") or "").split())[:MAX_TURN_CHARS]
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines)


def parse_verdict(text: str) -> Optional[bool]:
    """True = разговор закончен, False = продолжается, None = ответ непонятен."""
    t = (text or "").strip().upper()
    if not t:
        return None
    if re.match(r"^\W*CONTINUE\b", t):
        return False
    if re.match(r"^\W*END\b", t):
        return True
    if "CONTINUE" in t:
        return False
    if re.search(r"\bEND\b", t):
        return True
    return None


async def call_is_finished(api_key: str, turns: List[Dict[str, Any]], model: str, timeout: float = 6.0,
                           client: Optional[httpx.AsyncClient] = None) -> Optional[bool]:
    """True — завершён, False — продолжается, None — не удалось определить (ошибка сети/ответа)."""
    dialog = format_turns(turns)
    if not dialog or not api_key:
        return None
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        # reasoning_effort подбираем как в chat_llm_client: пробуем выключить, принимаем то, что примет API
        for extra in ({"reasoning_effort": "none"}, {"reasoning_effort": "minimal"}, {}):
            body = {
                "model": model,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": f"Конец разговора:\n{dialog}\n\nОтвет (END или CONTINUE):"}],
                "max_completion_tokens": 64,
                **extra,
            }
            resp = await http.post(CHAT_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
            if resp.status_code == 400 and extra and "reasoning" in resp.text:
                continue  # параметр не поддерживается — следующий вариант
            if resp.status_code != 200:
                logger.warning(f"[CALL-END-JUDGE] HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            content = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            verdict = parse_verdict(content)
            logger.info(f"[CALL-END-JUDGE] {model}: {content.strip()[:40]!r} -> {verdict}")
            return verdict
        return None
    except Exception as exc:
        logger.warning(f"[CALL-END-JUDGE] failed: {exc}")
        return None
    finally:
        if own:
            await http.aclose()
