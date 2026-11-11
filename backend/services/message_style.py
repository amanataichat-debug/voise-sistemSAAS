"""
Текст клиенту в мессенджер — как пишет живой менеджер (ветка 0311-call-guard).

Владелец 04.11.2026: «не надо длинные черточки, спецсимволы, звёздочки».
Модели ставят их, сколько ни запрещай в промпте, поэтому чистим в коде перед
отправкой в WhatsApp / Telegram / Instagram:
  * длинное и среднее тире → запятая (между словами) или дефис (в числах);
  * **жирный**, __подчёркнутый__, `код`, заголовки «# …» → просто текст;
  * маркеры списков «- », «• », «* », «1) », «1. » в начале строки → убираются;
  * смайлов не больше одного на сообщение;
  * лишние пробелы и пустые строки.
Ссылки, номера телефонов, цены и переносы строк не трогаем.
"""

import re

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF]️?"
)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")


# Часовой пояс клиенту не пишем: все клиенты в Кыргызстане (ветка 1111-chat-live).
_TZ_PAREN_RE = re.compile(
    r"\s*\(\s*(?:по\s+)?(?:времени\s+)?(?:бишкек\w*|bishkek|utc\s*\+?\s*6?|gmt\s*\+?\s*6?)"
    r"(?:\s+времени)?\s*(?:,\s*utc\s*\+?\s*6)?\s*\)",
    re.IGNORECASE,
)
_TZ_WORDS_RE = re.compile(
    r",?\s+(?:по\s+бишкек\w*(?:\s+времени)?|по\s+времени\s+бишкека|по\s+местному\s+времени)(?=[\s.,!?]|$)",
    re.IGNORECASE,
)
# Строка прайса «Название — цена»: двоеточие вместо тире (тире ниже превращается в запятую).
_PRICE_LINE_RE = re.compile(r"^([^,:\n—–]{1,40}?)\s+[—–-]\s+(?=(?:от\s+)?\d)", re.IGNORECASE)


def _clean_line(line: str) -> str:
    s = line
    s = re.sub(r"^\s{0,3}#{1,6}\s+", "", s)                    # заголовки
    s = re.sub(r"^\s*(?:[-•*·▪►✓✔]|\d{1,2}[.)])\s+", "", s)     # маркеры списков
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)                       # **жирный**
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"(?<!\w)\*(\S.*?\S|\S)\*(?!\w)", r"\1", s)        # *курсив*
    s = s.replace("`", "")
    s = _TZ_PAREN_RE.sub("", s)                                  # «в 18:00 (Бишкек)» → «в 18:00»
    s = _TZ_WORDS_RE.sub("", s)                                  # «по бишкекскому времени»
    s = re.sub(r"\(\s*\)", "", s)
    s = _PRICE_LINE_RE.sub(r"\1: ", s)                            # «Profi — 12 500» → «Profi: 12 500»
    s = re.sub(r"(\d)\s*[—–]\s*(\d)", r"\1-\2", s)               # 10–15 → 10-15
    s = re.sub(r"\s+[—–]\s+", ", ", s)                           # слово — слово → слово, слово
    s = re.sub(r"^[—–]\s*", "", s)
    s = re.sub(r"[—–]", "-", s)
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r"([.!?:;]),\s", r"\1 ", s)                       # «привет!, как» → «привет! как»
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def clean_client_text(text: str) -> str:
    """Почистить текст клиенту. Пустой/None возвращает как есть."""
    if not text:
        return text
    # Ссылки прячем, чтобы не испортить в них «_», «*», «-».
    urls = []

    def _keep(m):
        urls.append(m.group(0))
        return f"\x00{len(urls) - 1}\x00"

    body = _URL_RE.sub(_keep, text)
    lines = [_clean_line(l) for l in body.splitlines()]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    # Смайлы: оставляем только первый.
    seen = [0]

    def _emoji(m):
        seen[0] += 1
        return m.group(0) if seen[0] == 1 else ""

    out = _EMOJI_RE.sub(_emoji, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.!?])", r"\1", out)
    for i, u in enumerate(urls):
        out = out.replace(f"\x00{i}\x00", u)
    return out.strip()
