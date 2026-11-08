"""
Голосовые ответы владельцу в Telegram-боте агента (ветка 0811-voice-jarvis).

Владелец спросил голосовым сообщением → бот, как и раньше, распознаёт его и отвечает текстом (оркестратор),
а следом присылает ГОЛОСОВОЕ: короткий живой пересказ ответа (2–5 предложений, цифры и имена, «подробности —
в сообщении выше»), озвученный голосом агента для голосовых сообщений (agent_voice_service: ElevenLabs или
GPT-Live; голос не выбран → голос GPT-Live по умолчанию). Работает в фоновом loop'е со своей сессией БД:
синтез идёт до ~20 с, а webhook Telegram должен ответить быстро.

Живой разговор (как телефон) в Telegram — это Mini App (кнопка «🎙 Голос», /static/voice-live.html), см.
api/agent_telegram.py::telegram_webapp_auth. Бот сам по себе звонить не умеет: Bot API не поддерживает звонки.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from backend.core.logging import get_logger

logger = get_logger(__name__)

SPEECH_MODEL = (os.getenv("OWNER_VOICE_TG_SPEECH_MODEL", "google/gemini-3.6-flash") or "").strip()
MAX_SPOKEN = 700

SPOKEN_PROMPT = (
    "Ты — голос делового помощника владельца бизнеса. Перескажи ответ ниже для голосового сообщения: живо, тепло, "
    "уверенно, с лёгкой харизмой — как толковый помощник, который рад рассказать новости. 2–5 коротких предложений, "
    "не длиннее 600 символов. Главное — цифры и имена; длинный список или таблицу не зачитывай — назови итог и самое "
    "важное и в конце скажи, что подробности в сообщении выше. Без markdown, списков, ссылок, id и эмодзи. Язык — тот "
    "же, что у ответа. Верни только текст для озвучки."
)

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")


def plain_text(markdown: str, limit: int = MAX_SPOKEN) -> str:
    """Запасной путь без модели: markdown → простой текст, обрезка по предложению."""
    text = re.sub(r"```.*?```", " ", markdown or "", flags=re.S)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or set(line) <= set("|-: "):
            continue
        line = line.strip("|").replace("|", ", ")
        line = re.sub(r"^[#>*\-•\d.)\s]+", "", line)
        lines.append(line)
    text = re.sub(r"[*_`#~]+", "", " ".join(lines))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > limit * 0.5 else cut.rstrip() + "…").strip()


async def spoken_version(text: str) -> Tuple[str, Tuple[int, int], Optional[str]]:
    """(текст для озвучки, (prompt_tokens, completion_tokens), модель) — модель недоступна → plain_text."""
    base = plain_text(text, limit=4000)
    if not base:
        return "", (0, 0), None
    if not SPEECH_MODEL:
        return plain_text(text), (0, 0), None
    try:
        from backend.services.agent_orchestrator import _extract_usage
        from backend.services.openrouter_client import get_openrouter_client

        resp = await get_openrouter_client().chat_completion(
            model=SPEECH_MODEL,
            messages=[{"role": "system", "content": SPOKEN_PROMPT}, {"role": "user", "content": base}],
            temperature=0.6,
        )
        spoken = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        spoken = plain_text(spoken)
        if spoken:
            return spoken, _extract_usage(resp), SPEECH_MODEL
    except Exception as exc:
        logger.warning(f"[OWNER-TG-VOICE] spoken rewrite failed: {exc}")
    return plain_text(text), (0, 0), None


def owner_voice_for(db, agent) -> Optional[Dict[str, Any]]:
    """Голос агента для голосовых сообщений; не выбран — голос GPT-Live по умолчанию (если OpenAI настроен)."""
    from backend.services import agent_voice_service as avs

    voice = avs.resolve_voice(db, agent)
    if voice:
        return voice
    if avs.is_configured(avs.ENGINE_OPENAI):
        from backend.core.config import settings
        return {"engine": avs.ENGINE_OPENAI, "voice_id": avs.DEFAULT_OPENAI_VOICE, "voice_name": avs.DEFAULT_OPENAI_VOICE,
                "language": None, "stability": None, "model": settings.AGENT_VOICE_OPENAI_TTS_MODEL, "source": "default"}
    return None


async def send_voice(token: str, chat_id: str, audio: Dict[str, Any]) -> bool:
    """OGG/Opus → sendVoice (голосовое с волной), иначе sendAudio."""
    from backend.services.agent_telegram_service import TELEGRAM_API

    method = "sendVoice" if audio.get("is_ogg") else "sendAudio"
    field = "voice" if audio.get("is_ogg") else "audio"
    data = {"chat_id": chat_id}
    if audio.get("duration"):
        data["duration"] = str(int(audio["duration"]))
    files = {field: (audio.get("filename") or "voice.ogg", audio["data"], audio.get("mime") or "audio/ogg")}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(TELEGRAM_API.format(token=token, method=method), data=data, files=files)
        body = resp.json()
        if resp.status_code == 200 and body.get("ok"):
            return True
        logger.error(f"[OWNER-TG-VOICE] {method} failed: {body.get('error_code')} {body.get('description')}")
    except Exception as exc:
        logger.error(f"[OWNER-TG-VOICE] {method} error: {exc}")
    return False


async def reply_with_voice(agent_id: str, chat_id: str, reply_markdown: str) -> bool:
    """Голосовой ответ владельцу после текстового. True — голосовое ушло."""
    from backend.db.session import SessionLocal
    from backend.models.agent_config import AgentConfig
    from backend.models.user import User
    from backend.services import agent_voice_service as avs
    from backend.services.agent_telegram_service import AgentTelegramService

    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.id == agent_id).first()
        if agent is None or not agent.telegram_bot_token or not agent.telegram_enabled:
            return False
        user = db.query(User).filter(User.id == agent.user_id).first()
        if user is None or (user.credits_balance or 0) <= 0:
            return False
        voice = owner_voice_for(db, agent)
        if not voice:
            logger.info(f"[OWNER-TG-VOICE] no voice configured for agent {agent_id} — text only")
            return False
        token = agent.telegram_bot_token
        await AgentTelegramService._call(token, "sendChatAction", {"chat_id": chat_id, "action": "record_voice"})
        spoken, (p_tok, c_tok), model = await spoken_version(reply_markdown)
        if not spoken:
            return False
        audio = await avs.synthesize(spoken, voice)
        if not audio.get("ok"):
            logger.warning(f"[OWNER-TG-VOICE] synthesis failed: {audio.get('error')}")
            return False
        sent = await send_voice(token, chat_id, audio)
        if sent:
            avs.charge(db, user.id, spoken, ref_id=agent.id, notes="Голосовой ответ владельцу в Telegram",
                       engine=voice.get("engine") or avs.ENGINE_ELEVEN, seconds=audio.get("duration") or None)
            if model and (p_tok or c_tok):
                try:
                    from backend.services.credit_service import CreditService
                    CreditService.charge(db=db, user_id=user.id, model_slug=model, prompt_tokens=p_tok,
                                         completion_tokens=c_tok, ref_type="voice_chat", ref_id=agent.id,
                                         notes="пересказ ответа для голосового")
                except Exception as exc:
                    logger.error(f"[OWNER-TG-VOICE] rewrite charge failed: {exc}")
        return sent
    except Exception as exc:
        logger.error(f"[OWNER-TG-VOICE] reply failed: {exc}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()
