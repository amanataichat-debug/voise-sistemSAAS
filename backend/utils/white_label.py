"""
White-label голосовых движков: клиент видит только наши названия.

Под капотом звонок ведут сторонние провайдеры (каскад ElevenLabs + ASR + текстовая
модель, OpenAI GPT-Live, Gemini Live), но в кабинете, на лендинге и в журнале звонка
клиенту показываются названия Voksy AI:

    eleven    (ASR → текстовая модель → синтез ElevenLabs)  → «Voksy Voice»
    gpt_live  (OpenAI GPT-Live, речь в речь)                → «Voksy Live»
    gemini    (Gemini Live)                                 → «Voksy Live Go»

Внутренние id (`eleven`, `gpt_live`, `assistant_type`) не меняются. Админ видит
журнал звонка как есть (`brand_call_log` вызывается только для не-админов).
Оркестратор агента (модели переписки) сюда не относится и не переименовывается.
"""
from __future__ import annotations

import re
from typing import Any

VOICE_BRAND = "Voksy Voice"
LIVE_BRAND = "Voksy Live"
LIVE_GO_BRAND = "Voksy Live Go"

ENGINE_BRANDS = {
    "eleven": VOICE_BRAND,
    "gpt_live": LIVE_BRAND,
    "gemini": LIVE_GO_BRAND,
}


def engine_brand(engine: str | None) -> str:
    return ENGINE_BRANDS.get(engine or "eleven", VOICE_BRAND)


# Имена функций агента, в которых есть «google» и т.п. — это интеграции, их не трогаем.
_KEEP_TOKENS = ("sheet", "calendar", "gmail", "google_doc", "_doc", "drive")

# Токен (слово, id модели, URL, метка лога) с названием провайдера внутри.
_PROVIDER_TOKEN = re.compile(
    r"[\w./@+-]*?(?:eleven|scribe|gpt|openai|open-ai|chatgpt|whisper|gemini|google|chirp|"
    r"yandex|speechkit|deepseek|openrouter|anthropic|claude|grok|xai|fish|cartesia|"
    r"realtime|v3_conversational)[\w./@+-]*",
    re.IGNORECASE,
)
_PROVIDER_WORDS = re.compile(r"\b(?:Яндекс|Yandex SpeechKit|Yandex Cloud|Fish Audio|Eleven Labs|Gemini Live|"
                             r"Google Speech|Scribe Realtime|OpenAI Realtime)\b", re.IGNORECASE)
_URL = re.compile(r"\b(?:wss?|https?)://\S+")
_LIVE_HINT = re.compile(r"gpt-live|gpt_live|\blive\b", re.IGNORECASE)
_GO_HINT = re.compile(r"gemini", re.IGNORECASE)
_DUP = re.compile(r"\b(Voksy(?: Live Go| Live| Voice)?)(?:[\s,+/·→-]+\1\b)+")


def _token_brand(token: str) -> str:
    if _GO_HINT.search(token):
        return LIVE_GO_BRAND
    if _LIVE_HINT.search(token):
        return LIVE_BRAND
    return "Voksy"


def brand_text(text: Any) -> Any:
    """Строка → та же строка с названиями провайдеров и моделей, заменёнными на наши."""
    if not isinstance(text, str) or not text:
        return text

    def repl(m: re.Match) -> str:
        tok = m.group(0)
        low = tok.lower()
        if any(k in low for k in _KEEP_TOKENS):
            return tok
        return _token_brand(tok)

    out = _URL.sub(lambda m: m.group(0) if any(k in m.group(0) for k in ("voksyai", "localhost")) else "Voksy", text)
    out = _PROVIDER_WORDS.sub(lambda m: _token_brand(m.group(0)), out)
    out = _PROVIDER_TOKEN.sub(repl, out)
    out = _DUP.sub(r"\1", out)
    return out


def brand_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return brand_text(obj)
    if isinstance(obj, list):
        return [brand_obj(v) for v in obj]
    if isinstance(obj, dict):
        return {k: brand_obj(v) for k, v in obj.items()}
    return obj


# Поля meta журнала, которые раскрывают состав каскада — клиенту не показываем.
_HIDDEN_META = ("asr", "llm_model", "tts_model", "voice_id", "telephony_profile", "gateway")


# Реплики клиента и ассистента — это речь, в ней «Google» или «gpt» могут быть по делу.
_SPEECH_KINDS = ("user", "assistant", "transcript")


def brand_event(e: Any) -> Any:
    if not isinstance(e, dict):
        return e
    out = dict(e)
    if (out.get("kind") or "") not in _SPEECH_KINDS:
        out["text"] = brand_text(out.get("text"))
    if out.get("data"):
        out["data"] = brand_obj(out["data"])
    return out


def brand_call_log(row: Any) -> tuple[dict, list, str]:
    """
    Журнал звонка (строка CallLog) для клиента: без моделей и провайдеров,
    движок — нашим названием. Возвращает (meta, events, text для .txt).
    """
    from types import SimpleNamespace
    from backend.websockets.call_log import render_text

    meta = dict(row.meta or {})
    engine = meta.get("engine") or "eleven"
    for key in _HIDDEN_META:
        meta.pop(key, None)
    meta = brand_obj(meta)
    meta["engine"] = meta["engine_title"] = engine_brand(engine)
    events = [brand_event(e) for e in (row.events or [])]
    fake = SimpleNamespace(**{k: getattr(row, k, None) for k in (
        "session_id", "assistant_type", "assistant_id", "channel", "sip_call_id",
        "started_at", "duration_sec", "summary")})
    fake.assistant_type = engine_brand(engine)
    fake.meta = meta
    fake.events = events
    return meta, events, render_text(fake)
