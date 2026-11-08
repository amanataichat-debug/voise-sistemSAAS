"""
Демо голосового режима: /ws/demo-voice (ветка 0811-voice-jarvis).

То же окно и тот же Gemini Live, что у владельца на /ws/agent-voice, но БЕЗ БАЗЫ: ни одного запроса
к PostgreSQL за весь разговор. Данные — придуманная компания из services/owner_voice_demo.py, функции
исполняются в памяти процесса. Поэтому демонстрацию можно показывать кому угодно: чужих клиентов,
денег и кабинета здесь нет и достать их отсюда нечем.

Деньги тратит только Google (минуты Gemini Live), поэтому вход — по короткому токену `dv1.` из
Telegram-бота демо (или JWT кабинета для своих), длина разговора ограничена DEMO_VOICE_MAX_SECONDS,
а число разговоров — лимитами в api/demo_voice.py.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from types import SimpleNamespace
from typing import Any, Dict, Optional

from fastapi import WebSocket

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.eleven_assistant import GEMINI_LIVE_VOICES
from backend.services import owner_voice as ov
from backend.services import owner_voice_demo as demo
from backend.websockets.gemini_client import GeminiLiveClient
from backend.websockets.handler_owner_voice import (GEMINI_IN_RATE, GEMINI_OUT_RATE, OwnerVoiceSession,
                                                      _fail, resolve_voice)

logger = get_logger(__name__)
DEFAULT_VOICE = (os.getenv("DEMO_VOICE_GEMINI_VOICE", "") or ov.DEFAULT_VOICE).strip()


def _log(message: str, level: str = "INFO") -> None:
    getattr(logger, level.lower(), logger.info)(f"[DEMO-VOICE] {message}")


class DemoVoiceSession(OwnerVoiceSession):
    """Сессия демо: функции — в памяти, настройки не сохраняются, в конце никаких списаний и записей."""

    def __init__(self, websocket: WebSocket, client: GeminiLiveClient, in_rate: int = GEMINI_IN_RATE,
                 owner_name: str = "", chat_id: Optional[str] = None) -> None:
        super().__init__(websocket, "demo", "demo", client, in_rate=in_rate,
                         owner=SimpleNamespace(first_name=owner_name))
        self.chat_id = chat_id
        self.owner_name = owner_name
        self.max_seconds = demo.MAX_SECONDS

    async def execute(self, call_id: str, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        self.functions_called += 1
        args = args if isinstance(args, dict) else {}
        if name == "do_action":
            # то же правило, что у настоящего помощника: без «да» владельца действие не показываем
            refusal = ov.action_refusal(args.get("confirmed"), self.last_owner_text())
            if refusal:
                return {"ok": False, "status": "rejected", "message": refusal}
        if name == "send_to_telegram":
            return await self._send_telegram(str(args.get("text") or ""))
        if name not in demo.DEMO_FUNCTION_NAMES:
            return {"ok": False, "error": f"Нет такой функции: {name}"}
        # данные лежат в памяти процесса, но считаются в потоке: звук разговора не должен ждать
        return await asyncio.to_thread(demo.run_demo_function, name, args)

    async def _send_telegram(self, text: str) -> Dict[str, Any]:
        from backend.api.demo_voice import send_demo_message

        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Пустое сообщение"}
        if not self.chat_id:
            return {"ok": False, "error": "Этот разговор открыт не из Telegram — отправить туда нечего"}
        ok = await send_demo_message(self.chat_id, "🎙 Из демо-разговора:\n\n" + ov._clip(text, 3000))
        return {"ok": bool(ok), "message": "Отправил вам в этот чат" if ok else "Не получилось отправить"}

    async def _save_prefs(self, raw: Any) -> None:
        """В демо настройки никуда не сохраняются — только отвечаем окну, чтобы оно не ждало."""
        self.prefs = ov.clean_prefs(raw)
        await self.websocket.send_json({"type": "prefs.saved", "ok": False, "prefs": self.prefs,
                                        "greeting": demo.demo_greeting(self.owner_name),
                                        "note": "Настройки здесь не сохраняются"})

    def _finish_sync(self, reason: str) -> None:
        _log(f"demo finished ({reason}): {time.time() - self.started_at:.0f}s, pairs={len(self.pairs)}, "
             f"functions={self.functions_called}")


async def handle_demo_voice_connection(websocket: WebSocket) -> None:
    """Точка входа /ws/demo-voice (сокет ещё не принят). Базы данных здесь нет вообще."""
    from backend.api.demo_voice import demo_access

    await websocket.accept()
    params = websocket.query_params
    token = ""
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if isinstance(first, dict) and first.get("type") == "auth":
            token = str(first.get("token") or "")
    except Exception:
        token = ""
    access = demo_access(token)
    if not access.get("ok"):
        await _fail(websocket, access.get("code", "forbidden"), access.get("message", "Демо недоступно"))
        return

    api_key = settings.GEMINI_API_KEY
    if not api_key:
        await _fail(websocket, "not_configured", "Демо временно недоступно", 1011)
        return
    voice = resolve_voice(params.get("voice") or DEFAULT_VOICE)
    try:
        in_rate = int(params.get("rate") or GEMINI_IN_RATE)
    except ValueError:
        in_rate = GEMINI_IN_RATE

    owner_name = str(access.get("name") or "")
    prompt = demo.demo_prompt(owner_name)
    config = SimpleNamespace(id="demo", user_id="demo", name=f"Демо: {demo.COMPANY}", system_prompt=prompt,
                             greeting_message=demo.demo_greeting(owner_name),
                             functions=[{"name": n} for n in demo.DEMO_FUNCTION_NAMES],
                             google_sheet_id=None, voice=voice)
    client_id = f"demo-voice-{uuid.uuid4().hex[:8]}"
    user_agent = ""
    try:
        user_agent = websocket.headers.get("user-agent", "")
    except Exception:
        pass
    t0 = time.monotonic()
    client = GeminiLiveClient(api_key, config, client_id, None, user_agent,
                              voice_override=voice, record_conversation=False, realtime_text=True,
                              await_setup=True, voice_hints=False, telephony=False,
                              blocking_tools=True, function_definitions=demo.demo_functions())
    if not await client.connect():
        _log(f"Gemini did not open: {client.connect_error}", "ERROR")
        await _fail(websocket, "connection_failed", "Голосовой движок не ответил — попробуйте ещё раз", 1011)
        return
    await websocket.send_json({
        "type": "connection_status", "status": "connected", "provider": "demo_voice",
        "message": "Connected: Voksy Live Go (demo)", "full_duplex": True,
        "audio_rate": GEMINI_OUT_RATE, "input_rate": GEMINI_IN_RATE, "voice": voice,
        "voices": GEMINI_LIVE_VOICES, "session_id": client.session_id, "client_id": client_id,
        "max_seconds": demo.MAX_SECONDS, "demo": True, "company": demo.COMPANY,
        "owner_name": owner_name, "prefs": ov.clean_prefs(None),
    })
    await websocket.send_json({"type": "owner.dashboard", "cards": demo.demo_cards()})
    _log(f"session {client_id} started in {int((time.monotonic() - t0) * 1000)}ms: {access.get('who')} "
         f"voice={voice} prompt={client.system_prompt_chars} chars")
    session = DemoVoiceSession(websocket, client, in_rate=in_rate, owner_name=owner_name,
                               chat_id=access.get("chat_id"))
    await session.run()
    try:
        await websocket.close()
    except Exception:
        pass
