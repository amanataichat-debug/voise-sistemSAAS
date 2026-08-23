"""
AgentTelegramService — весь Telegram Bot API агента в одном месте.

v2.2 Telegram bot integration

Бот агента выполняет две роли:
1. Фронтенд для AGENT_CHAT — общение с агентом прямо из Telegram.
2. Канал доставки уведомлений от тулзы send_telegram_notification (PostCall).

Используется только REST к https://api.telegram.org/bot{token}/{method}
через httpx.AsyncClient. Никакого python-telegram-bot.
"""

import asyncio
import re
import secrets
from datetime import datetime
from typing import Optional, List

import httpx
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.models.agent_config import AgentConfig
from backend.models.agent_telegram_chat_history import AgentTelegramChatHistory
from backend.models.user import User

logger = get_logger(__name__)

# Маркер анти-спама для уведомлений об истёкшей подписке (раздел 9, edge case 9).
# Храним последний показ внутри history JSONB как системную запись.
_SUB_NOTICE_MARKER = "__sub_expired_notice__"
_SUB_NOTICE_COOLDOWN_SEC = 24 * 3600


def _should_send_sub_notice(history_row) -> bool:
    """True если уведомление о подписке можно отправить (прошло > 24ч)."""
    history = history_row.history or []
    last_ts = None
    for entry in reversed(history):
        if isinstance(entry, dict) and entry.get("marker") == _SUB_NOTICE_MARKER:
            last_ts = entry.get("ts")
            break
    if not last_ts:
        return True
    try:
        last_dt = datetime.fromisoformat(last_ts)
    except (ValueError, TypeError):
        return True
    return (datetime.utcnow() - last_dt).total_seconds() >= _SUB_NOTICE_COOLDOWN_SEC


def _mark_sub_notice_sent(history_row, db: Session) -> None:
    """Записать маркер времени последнего уведомления о подписке."""
    history = list(history_row.history or [])
    # Удаляем старые маркеры, чтобы не разрастались
    history = [e for e in history if not (isinstance(e, dict) and e.get("marker") == _SUB_NOTICE_MARKER)]
    history.append({"marker": _SUB_NOTICE_MARKER, "ts": datetime.utcnow().isoformat()})
    history_row.history = history
    flag_modified(history_row, "history")
    db.commit()

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
REQUEST_TIMEOUT = 20.0
TELEGRAM_MAX_LEN = 4096
# Лимит rich-сообщений (sendRichMessage) — 32768 UTF-8 символов.
TELEGRAM_RICH_MAX_LEN = 32768


# ============================================================================
# MARKDOWN → TELEGRAM HTML
# Модели-оркестраторы отвечают в Markdown. Telegram Bot API понимает только
# ограниченный HTML и НЕ умеет таблицы / заголовки / markdown. Если послать
# сырой markdown с parse_mode=HTML — Telegram падает с 400 (а юзер не видит
# ответа). Поэтому конвертируем в безопасный Telegram-HTML.
# ============================================================================

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def strip_html_tags(s: str) -> str:
    """Грубое удаление HTML-тегов + раскодирование базовых сущностей (для fallback)."""
    s = _HTML_TAG_RE.sub("", s or "")
    return (s.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&"))


def _md_inline(text: str) -> str:
    """Инлайновый markdown (текст уже HTML-экранирован) → Telegram HTML."""
    # Защищаем inline-код, чтобы внутри не сработали bold/italic
    codes: List[str] = []

    def _stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)

    # Ссылки [text](url)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', text)
    # Жирный **x** / __x__
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![A-Za-z0-9])__([^_\n]+)__(?![A-Za-z0-9])", r"<b>\1</b>", text)
    # Курсив *x* / _x_
    text = re.sub(r"(?<![\*\w])\*([^*\n]+)\*(?![\*\w])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])", r"<i>\1</i>", text)
    # Зачёркнутый ~~x~~
    text = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", text)
    # Спойлер ||x|| → <tg-spoiler> (rich-конструкция, деградируем для старых клиентов)
    text = re.sub(r"\|\|([^\n]+?)\|\|", r"<tg-spoiler>\1</tg-spoiler>", text)
    # Маркер ==x== → жирный (прямого аналога в Telegram HTML нет)
    text = re.sub(r"==([^\n=]+?)==", r"<b>\1</b>", text)

    # Восстанавливаем код
    text = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)
    return text


def _split_table_row(row: str) -> List[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in row.split("|")]


def _strip_md_markers(s: str) -> str:
    """Убирает markdown-маркеры (для ячеек таблицы — там голый текст в моноширинном блоке)."""
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"~~([^~]+)~~", r"\1", s)
    return s.strip()


def _render_table(header: List[str], body: List[List[str]]) -> str:
    """Markdown-таблица → выровненный моноширинный <pre> блок."""
    cols = len(header)
    rows = [header] + body
    norm: List[List[str]] = []
    for r in rows:
        r = [_strip_md_markers(c) for c in r]
        if len(r) < cols:
            r = r + [""] * (cols - len(r))
        norm.append(r[:cols])

    widths = [max(len(norm[ri][ci]) for ri in range(len(norm))) for ci in range(cols)]

    def fmt(r: List[str]) -> str:
        return " | ".join(r[ci].ljust(widths[ci]) for ci in range(cols))

    lines = [fmt(norm[0]), "-+-".join("-" * widths[ci] for ci in range(cols))]
    for r in norm[1:]:
        lines.append(fmt(r))
    return "<pre>" + _esc("\n".join(lines)) + "</pre>"


def markdown_to_telegram_html(md: str) -> str:
    """
    Конвертирует Markdown-ответ модели в безопасный Telegram-HTML.
    Поддержка: заголовки, жирный/курсив/код/зачёркнутый, ссылки, списки,
    блоки кода ```...```, таблицы (→ моноширинный выровненный блок), цитаты.
    """
    md = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    # Сворачиваемые блоки (rich) не понимает старый Telegram-HTML — разворачиваем:
    # <summary> → жирная строка-заголовок, сами теги <details> убираем.
    md = re.sub(r"<summary[^>]*>(.*?)</summary>",
                lambda m: "\n**" + m.group(1).strip() + "**\n", md,
                flags=re.IGNORECASE | re.DOTALL)
    md = re.sub(r"</?details[^>]*>", "", md, flags=re.IGNORECASE)
    lines = md.split("\n")
    n = len(lines)
    out: List[str] = []
    i = 0
    in_code = False
    code_buf: List[str] = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Блок кода ```
        if stripped.startswith("```"):
            if not in_code:
                in_code = True
                code_buf = []
            else:
                in_code = False
                out.append("<pre>" + _esc("\n".join(code_buf)) + "</pre>")
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # Таблица: текущая строка с '|' + следующая строка-разделитель |---|
        if ("|" in line and i + 1 < n and "-" in lines[i + 1]
                and re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i + 1])
                and lines[i + 1].count("|") >= 1):
            header = _split_table_row(line)
            j = i + 2
            body_rows = []
            while j < n and "|" in lines[j] and lines[j].strip():
                body_rows.append(_split_table_row(lines[j]))
                j += 1
            out.append(_render_table(header, body_rows))
            i = j
            continue

        # Заголовок # .. ######
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            out.append("<b>" + _md_inline(_esc(m.group(2).strip())) + "</b>")
            i += 1
            continue

        # Горизонтальная линия --- *** ___
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            out.append("──────────")
            i += 1
            continue

        # Цитата >
        if stripped.startswith(">"):
            out.append("<i>" + _md_inline(_esc(stripped.lstrip(">").strip())) + "</i>")
            i += 1
            continue

        # Маркированный список
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if m:
            indent = " " * len(m.group(1))
            out.append(indent + "• " + _md_inline(_esc(m.group(2))))
            i += 1
            continue

        # Нумерованный список
        m = re.match(r"^(\s*)(\d+)[.)]\s+(.*)$", line)
        if m:
            out.append(m.group(1) + m.group(2) + ". " + _md_inline(_esc(m.group(3))))
            i += 1
            continue

        # Обычная строка
        out.append(_md_inline(_esc(line)))
        i += 1

    if in_code and code_buf:
        out.append("<pre>" + _esc("\n".join(code_buf)) + "</pre>")

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_for_telegram(text: str, limit: int = TELEGRAM_MAX_LEN) -> List[str]:
    """Режет длинный текст на части ≤ limit по границам строк (для лимита Telegram 4096)."""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    buf = ""
    for line in text.split("\n"):
        # одиночная строка длиннее лимита — жёстко режем
        while len(line) > limit:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) + 1 > limit:
            if buf:
                chunks.append(buf)
            buf = line
        else:
            buf = line if not buf else buf + "\n" + line
    if buf:
        chunks.append(buf)
    return chunks


def generate_webhook_secret() -> str:
    """~43 символа, влезает в VARCHAR(64)."""
    return secrets.token_urlsafe(32)


def build_webhook_url(secret: str) -> str:
    base = (
        settings.TELEGRAM_WEBHOOK_BASE_URL
        or settings.PUBLIC_BASE_URL
        or settings.HOST_URL
        or ""
    ).rstrip("/")
    return f"{base}/api/agent/telegram/webhook/{secret}"


class AgentTelegramService:
    """Тонкая обёртка над Telegram Bot API для бота агента."""

    @staticmethod
    async def _call(token: str, method: str, payload: dict) -> Optional[dict]:
        """Низкоуровневый вызов метода Telegram Bot API. Возвращает result или None."""
        url = TELEGRAM_API.format(token=token, method=method)
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if resp.status_code == 200 and data.get("ok"):
                    return data.get("result")
                logger.error(
                    f"[AGENT-TG] {method} failed: {data.get('error_code')} - {data.get('description')}"
                )
                return None
        except Exception as e:
            logger.error(f"[AGENT-TG] {method} request error: {e}")
            return None

    @staticmethod
    async def validate_token(token: str) -> Optional[dict]:
        """getMe → {"id", "username", "first_name"} или None при ошибке."""
        if not token:
            return None
        result = await AgentTelegramService._call(token, "getMe", {})
        if not result:
            return None
        return {
            "id": result.get("id"),
            "username": result.get("username"),
            "first_name": result.get("first_name"),
        }

    @staticmethod
    async def get_webhook_info(token: str) -> Optional[dict]:
        """getWebhookInfo → текущая регистрация webhook у Telegram."""
        if not token:
            return None
        return await AgentTelegramService._call(token, "getWebhookInfo", {})

    @staticmethod
    async def setup_webhook(token: str, webhook_url: str, secret: str) -> bool:
        """setWebhook с secret_token, allowed_updates=['message'], drop_pending_updates."""
        result = await AgentTelegramService._call(token, "setWebhook", {
            "url": webhook_url,
            "secret_token": secret,
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        })
        return result is True or bool(result)

    @staticmethod
    async def delete_webhook(token: str) -> bool:
        """deleteWebhook."""
        if not token:
            return False
        result = await AgentTelegramService._call(token, "deleteWebhook", {
            "drop_pending_updates": False,
        })
        return result is True or bool(result)

    @staticmethod
    async def _send_chunk(token: str, chat_id: str, text: str, parse_mode: Optional[str]) -> bool:
        """Отправляет один кусок. При сбое HTML-парсинга — повтор без parse_mode (plain)."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        result = await AgentTelegramService._call(token, "sendMessage", payload)
        if result is not None:
            return True
        # Fallback: Telegram отклонил разметку — шлём как обычный текст
        if parse_mode:
            plain = strip_html_tags(text)
            result = await AgentTelegramService._call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": plain,
                "disable_web_page_preview": True,
            })
            return result is not None
        return False

    @staticmethod
    async def send_message(token: str, chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
        """
        sendMessage. Безопасная обёртка — не бросает наружу, только логирует.
        Режет длинные сообщения на части (лимит Telegram 4096) и при ошибке
        HTML-разметки откатывается на обычный текст, чтобы ответ всё равно дошёл.
        """
        if not token or not chat_id:
            return False
        if not text:
            return False
        chunks = _split_for_telegram(text)
        ok_any = False
        for chunk in chunks:
            ok = await AgentTelegramService._send_chunk(token, chat_id, chunk, parse_mode)
            ok_any = ok_any or ok
        return ok_any

    @staticmethod
    async def send_rich_message(
        token: str,
        chat_id: str,
        markdown: Optional[str] = None,
        html: Optional[str] = None,
        **kwargs,
    ) -> Optional[dict]:
        """
        sendRichMessage (Bot API rich messages). InputRichMessage принимает РОВНО
        одно из полей markdown/html — строку (не дерево блоков). Возвращает
        отправленный Message (dict) или None при ошибке.
        """
        if not token or not chat_id:
            return None
        rich: dict = {}
        if markdown is not None:
            rich["markdown"] = markdown[:TELEGRAM_RICH_MAX_LEN]
        elif html is not None:
            rich["html"] = html[:TELEGRAM_RICH_MAX_LEN]
        else:
            return None
        payload = {"chat_id": chat_id, "rich_message": rich, **kwargs}
        return await AgentTelegramService._call(token, "sendRichMessage", payload)

    @staticmethod
    async def send_rich_message_draft(
        token: str,
        chat_id: str,
        draft_id: int,
        markdown: str,
        **kwargs,
    ) -> bool:
        """
        sendRichMessageDraft — стриминг частичного rich-сообщения (только личные
        чаты). draft_id ненулевой; правки с тем же id анимируются. Draft эфемерен
        (~30 сек), финал обязательно слать через sendRichMessage.
        """
        if not token or not chat_id or not draft_id:
            return False
        try:
            chat_int = int(chat_id)
        except (TypeError, ValueError):
            return False
        payload = {
            "chat_id": chat_int,
            "draft_id": int(draft_id),
            "rich_message": {"markdown": (markdown or "")[:TELEGRAM_RICH_MAX_LEN]},
            **kwargs,
        }
        result = await AgentTelegramService._call(token, "sendRichMessageDraft", payload)
        return result is True or bool(result)

    @staticmethod
    async def send_rich_or_fallback(token: str, chat_id: str, markdown: str) -> bool:
        """
        Пробует отправить ответ как rich (sendRichMessage, лимит 32768, сырой
        Markdown модели). При ошибке (старый клиент / 400) откатывается на
        legacy-путь: markdown_to_telegram_html + sendMessage (чанк 4096).
        """
        if not token or not chat_id:
            return False
        if not markdown:
            markdown = "Готово."
        result = await AgentTelegramService.send_rich_message(token, chat_id, markdown=markdown)
        if result is not None:
            return True
        # Fallback: безопасный Telegram-HTML обычным sendMessage
        html = markdown_to_telegram_html(markdown)
        return await AgentTelegramService.send_message(token, chat_id, html, parse_mode="HTML")

    @staticmethod
    async def transcribe_voice(token: str, voice: dict) -> Optional[str]:
        """
        Голосовое сообщение Telegram (message.voice) → текст.
        getFile → скачиваем OGG/Opus с file-сервера Telegram → STT.
        Возвращает распознанный текст или None.
        """
        if not token or not isinstance(voice, dict):
            return None
        file_id = voice.get("file_id")
        if not file_id:
            return None

        info = await AgentTelegramService._call(token, "getFile", {"file_id": file_id})
        file_path = (info or {}).get("file_path")
        if not file_path:
            return None

        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.error(f"[AGENT-TG] voice download failed: {resp.status_code}")
                    return None
                audio = resp.content
        except Exception as e:
            logger.error(f"[AGENT-TG] voice download error: {e}")
            return None

        from backend.services.transcription_service import TranscriptionService
        return await TranscriptionService.transcribe(
            audio, filename="voice.ogg", content_type="audio/ogg",
        )

    @staticmethod
    async def send_to_all_chats(agent_config: AgentConfig, text: str) -> dict:
        """
        Шлёт text во все chat_id из agent_config.telegram_chat_ids параллельно.
        Возвращает {"sent": int, "failed": int, "total": int}.
        """
        if not agent_config.telegram_enabled or not agent_config.has_telegram_bot():
            return {"sent": 0, "failed": 0, "total": 0}

        chat_ids = agent_config.get_telegram_chat_ids_list()
        if not chat_ids:
            return {"sent": 0, "failed": 0, "total": 0}

        token = agent_config.telegram_bot_token
        results = await asyncio.gather(
            *[AgentTelegramService.send_message(token, cid, text) for cid in chat_ids],
            return_exceptions=True,
        )

        sent = sum(1 for r in results if r is True)
        total = len(chat_ids)
        return {"sent": sent, "failed": total - sent, "total": total}


async def process_telegram_message(
    agent: AgentConfig,
    chat_id: str,
    text: str,
    from_user: dict,
    message: dict,
    db: Session,
) -> None:
    """
    Обрабатывает входящее текстовое сообщение в чат-режиме:
    1. Находит/создаёт AgentTelegramChatHistory для чата.
    2. Обновляет метаданные отправителя.
    3. Вызывает ChatOrchestrator.run_telegram.
    4. Отправляет ответ обратно в Telegram.
    """
    # 1. Найти или создать историю чата
    history_row = db.query(AgentTelegramChatHistory).filter(
        AgentTelegramChatHistory.agent_config_id == agent.id,
        AgentTelegramChatHistory.chat_id == chat_id,
    ).first()
    if not history_row:
        chat = message.get("chat", {}) if isinstance(message, dict) else {}
        history_row = AgentTelegramChatHistory(
            agent_config_id=agent.id,
            chat_id=chat_id,
            chat_type=chat.get("type"),
            chat_title=chat.get("title") or chat.get("first_name"),
            history=[],
        )
        db.add(history_row)
        db.flush()

    # 2. Метаданные отправителя
    history_row.last_sender_user_id = str(from_user.get("id", "")) if from_user else None
    history_row.last_sender_username = from_user.get("username") if from_user else None
    history_row.last_message_at = datetime.utcnow()

    # 3. ChatOrchestrator в Telegram-режиме (показываем "печатает…")
    from backend.services.agent_orchestrator import ChatOrchestrator
    from backend.services.credit_service import (
        CreditService,
        InsufficientCreditsError,
        SubscriptionExpiredError,
        SubscriptionRequiredError,
    )
    user = db.query(User).filter(User.id == agent.user_id).first()

    # 3a. Гейтинг подписки/кредитов. Если подписка истекла или кредитов нет —
    #     отвечаем текстом, без вызова оркестратора. Анти-спам: не чаще раза
    #     в сутки на чат (маркер в history JSONB).
    if user is not None:
        try:
            CreditService.precheck(db, user)
        except (SubscriptionExpiredError, SubscriptionRequiredError, InsufficientCreditsError) as gate_err:
            if isinstance(gate_err, InsufficientCreditsError):
                notice = "На балансе закончились кредиты. Пополните в личном кабинете Voksy AI."
            else:
                notice = "Подписка истекла, продлите в личном кабинете Voksy AI."

            if _should_send_sub_notice(history_row):
                await AgentTelegramService.send_message(
                    token=agent.telegram_bot_token,
                    chat_id=chat_id,
                    text=notice,
                )
                _mark_sub_notice_sent(history_row, db)
            else:
                logger.info(f"[AGENT-TG] Subscription notice suppressed (anti-spam) for chat {chat_id}")
            return

    orchestrator = ChatOrchestrator()

    # 4. Стриминг через sendRichMessageDraft доступен только в личных чатах
    #    с v3-агентами (uses_hardcoded_prompt). В группах/каналах и для legacy
    #    v2-агентов — обычный rich-ответ одним сообщением (с fallback).
    is_private = (history_row.chat_type or "") == "private"
    use_stream = is_private and getattr(agent, "uses_hardcoded_prompt", False)

    if use_stream:
        await _stream_telegram_reply(
            agent, chat_id, text, user, db, history_row, message, orchestrator,
        )
        return

    await AgentTelegramService._call(
        agent.telegram_bot_token, "sendChatAction",
        {"chat_id": chat_id, "action": "typing"},
    )
    result = await orchestrator.run_telegram(
        message=text,
        agent_config=agent,
        user=user,
        db=db,
        telegram_history_row=history_row,
    )
    # Ответ в Telegram — сырой Markdown модели через rich (fallback на HTML)
    await AgentTelegramService.send_rich_or_fallback(
        token=agent.telegram_bot_token,
        chat_id=chat_id,
        markdown=result.get("reply") or "Готово.",
    )


async def _stream_telegram_reply(
    agent: AgentConfig,
    chat_id: str,
    text: str,
    user,
    db: Session,
    history_row,
    message: dict,
    orchestrator,
) -> None:
    """
    Драйвер стриминга для личных чатов: гоняет ChatOrchestrator.run_telegram_stream,
    по мере токенов шлёт анимированный draft (sendRichMessageDraft), на финале —
    постоянное rich-сообщение (sendRichMessage с fallback). Списание кредитов и
    запись истории делает сам генератор.
    """
    token = agent.telegram_bot_token

    # draft_id: ненулевой int, стабильный в пределах хода. Берём message_id входящего.
    draft_id = 0
    if isinstance(message, dict):
        try:
            draft_id = int(message.get("message_id") or 0)
        except (TypeError, ValueError):
            draft_id = 0
    if not draft_id:
        draft_id = secrets.randbelow(2_000_000_000) + 1

    buffer = ""
    final_text = ""
    last_sent = ""
    last_ts = 0.0
    MIN_INTERVAL = 0.7   # сек между draft-апдейтами (бережём rate-limit)
    MIN_DELTA = 24       # минимум новых символов для апдейта

    async def flush(force: bool = False) -> None:
        nonlocal last_sent, last_ts
        if not buffer:
            return
        now = asyncio.get_event_loop().time()
        if not force and (now - last_ts < MIN_INTERVAL or len(buffer) - len(last_sent) < MIN_DELTA):
            return
        if await AgentTelegramService.send_rich_message_draft(token, chat_id, draft_id, buffer):
            last_sent = buffer
            last_ts = now

    try:
        async for ev in orchestrator.run_telegram_stream(
            message=text, agent_config=agent, user=user, db=db, telegram_history_row=history_row,
        ):
            etype = ev.get("type")
            if etype == "token":
                buffer = ev.get("buffer") or (buffer + (ev.get("text") or ""))
                await flush()
            elif etype == "clear_partial":
                buffer = ""
            elif etype == "tool":
                # Лёгкий индикатор работы во время вызова инструмента.
                note = (buffer + "\n\n_⏳ работаю…_") if buffer.strip() else "_⏳ работаю…_"
                await AgentTelegramService.send_rich_message_draft(token, chat_id, draft_id, note)
            elif etype == "done":
                final_text = ev.get("reply") or buffer or "Готово."
                break
            elif etype == "error":
                final_text = ev.get("reply") or buffer
                break
    except Exception as e:
        logger.error(f"[AGENT-TG] stream driver error: {e}", exc_info=True)
        final_text = final_text or buffer

    # Финал: постоянное сообщение заменяет эфемерный draft.
    await AgentTelegramService.send_rich_or_fallback(
        token=token, chat_id=chat_id, markdown=final_text or buffer or "Готово.",
    )
