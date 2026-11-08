"""
Голосовой режим оркестратора (ветка 0811-voice-jarvis): владелец ↔ Gemini Live ↔ оркестратор.

Браузер (agent/voice-chat.js) открывает /ws/agent-voice?agent_id=<id>&voice=<имя>, первым сообщением шлёт
{"type": "auth", "token": <JWT>}, дальше — микрофон PCM16 16 кГц (input_audio_buffer.append), получает протокол виджета (response.audio.delta 24 кГц,
transcript.delta, conversation.interrupted, function_call.*). Голос и разговор ведёт Gemini Live
(GeminiLiveClient + handle_gemini_messages — те же, что у карточек), знания и действия — функции
services/owner_voice.py, исполняются своим исполнителем (function_executor), без реестра backend/functions.

Доступ: только владелец агента с режимом full (оплаченный Profi, legacy agent, админ) и кредитами агента.
Деньги: минуты голоса — кредиты агента (voice_live), запросы к оркестратору — кредиты как в чате (voice_chat).
В конце разговор пишется в историю текстового чата агента (chat_history), чтобы чат продолжил тему.
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import json
import time
import traceback
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.eleven_assistant import GEMINI_LIVE_VOICES
from backend.services import owner_voice as ov
from backend.websockets.gemini_client import GeminiLiveClient
from backend.websockets.handler_gemini import handle_gemini_messages

logger = get_logger(__name__)

GEMINI_IN_RATE = 16000
GEMINI_OUT_RATE = 24000
_PLACEHOLDERS = {"[no user input]", "[no response]", "[interrupted]", "[incomplete - disconnected]"}


def _log(message: str, level: str = "INFO") -> None:
    getattr(logger, level.lower(), logger.info)(f"[OWNER-VOICE] {message}")


def resolve_voice(name: Optional[str]) -> str:
    wanted = (name or "").strip().lower()
    for v in GEMINI_LIVE_VOICES:
        if v.lower() == wanted:
            return v
    for v in GEMINI_LIVE_VOICES:
        if v.lower() == ov.DEFAULT_VOICE.lower():
            return v
    return GEMINI_LIVE_VOICES[0]


def access_error(user, agent) -> Optional[Dict[str, str]]:
    """None — можно разговаривать; иначе {code, message} для клиента."""
    if user is None:
        return {"code": "unauthorized", "message": "Войдите в кабинет заново"}
    if agent is None:
        return {"code": "not_found", "message": "Агент не найден"}
    if getattr(user, "agent_mode", None) is None or user.agent_mode() != "full":
        return {"code": "profi_required",
                "message": "Голосовой разговор с агентом доступен на тарифе Profi"}
    if (user.credits_balance or 0) < 100:
        return {"code": "insufficient_credits", "message": "Недостаточно кредитов агента — пополните баланс"}
    return None


class _SessionSocket:
    """Прокладка: события Gemini уходят в браузер как есть, а сессия видит стенограмму."""

    def __init__(self, websocket: WebSocket, session: "OwnerVoiceSession") -> None:
        self._ws = websocket
        self._session = session

    async def send_json(self, data, mode: str = "text"):
        try:
            self._session.observe(data)
        except Exception as exc:
            _log(f"observe failed: {exc}", "WARNING")
        return await self._ws.send_json(data, mode=mode)

    def __getattr__(self, name):
        return getattr(self._ws, name)


class OwnerVoiceSession:
    def __init__(self, websocket: WebSocket, user_id: str, agent_id: str, client: GeminiLiveClient,
                 in_rate: int = GEMINI_IN_RATE, prefs: Optional[Dict[str, Any]] = None, owner=None) -> None:
        self.websocket = websocket
        self.user_id = user_id
        self.agent_id = agent_id
        self.client = client
        self.in_rate = in_rate if in_rate in (8000, 16000, 24000, 44100, 48000) else GEMINI_IN_RATE
        self._resample_state = None
        self.pairs: List[Dict[str, str]] = []
        self.started_at = time.time()
        self.actions_done = 0
        self.functions_called = 0
        self.typed: List[str] = []   # быстрые команды из окна — у них нет расшифровки речи, в историю пишем сами
        self.prefs: Dict[str, Any] = ov.clean_prefs(prefs)
        self.owner = owner or SimpleNamespace(first_name="")
        self._sem = asyncio.Semaphore(2)
        # Последняя реплика владельца — для проверки «да» перед действием (ov.action_refusal)
        self._owner_cur = ""
        self._agent_spoke = False
        self.interruption_state: Dict[str, Any] = {
            "is_user_speaking": False, "is_assistant_speaking": False, "last_speech_start": 0,
            "last_speech_stop": 0, "interruption_count": 0, "last_interruption_time": 0,
            "assistant_audio_ever": False, "greeting_retries": 0,
        }
        client.function_executor = self.execute

    # ------------------------------------------------------------------ стенограмма
    def observe(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict) or data.get("type") != "transcript.delta":
            return
        delta = data.get("delta") or ""
        if data.get("role") == "user":
            if self._agent_spoke:
                self._owner_cur = ""
                self._agent_spoke = False
            self._owner_cur += delta
        else:
            if delta.strip():
                self._agent_spoke = True

    def last_owner_text(self) -> str:
        return self._owner_cur.strip()

    async def save_turn(self, user: str, assistant: str) -> bool:
        user = "" if (user or "").strip() in _PLACEHOLDERS else (user or "").strip()
        assistant = "" if (assistant or "").strip() in _PLACEHOLDERS else (assistant or "").strip()
        if not user and assistant and self.typed:
            user = self.typed.pop(0)   # ответ на быструю команду из окна
        if user or assistant:
            self.pairs.append({"user": user, "assistant": assistant})
        return True

    # ------------------------------------------------------------------ функции
    async def execute(self, call_id: str, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        self.functions_called += 1
        args = args if isinstance(args, dict) else {}
        if name == "do_action":
            refusal = ov.action_refusal(args.get("confirmed"), self.last_owner_text())
            if refusal and args.get("confirmed") is True and ov.owner_confirmation(self.last_owner_text()) is None:
                # расшифровка «да» могла ещё не прийти — даём ей до 1,2 с
                for _ in range(6):
                    await asyncio.sleep(0.2)
                    refusal = ov.action_refusal(args.get("confirmed"), self.last_owner_text())
                    if not refusal:
                        break
            if refusal:
                _log(f"do_action refused: {refusal[:80]} | owner said: «{self.last_owner_text()[:80]}»")
                return {"ok": False, "status": "rejected", "message": refusal}
            if self.actions_done >= ov.MAX_ACTIONS_PER_SESSION:
                return {"ok": False, "message": "Слишком много действий за один разговор — продолжите в чате агента."}
        if name not in ov.FUNCTION_NAMES:
            return {"ok": False, "error": f"Нет такой функции: {name}"}
        if name in ov.FAST_FUNCTIONS:
            # прямой запрос к базе — в потоке: синхронный драйвер БД не останавливает звук разговора
            return await asyncio.to_thread(ov.run_fast_function, name, args, self.user_id, self.agent_id)
        async with self._sem:
            # Оркестратор делает синхронные запросы к БД внутри корутин. В основном loop'е воркера, где идёт
            # звук, это и были «запинки, как будто связь пропадает» (живой тест 08.11.2026): пока агент думал,
            # кусочки ответа Gemini не уходили в браузер. Поэтому — в фоновом loop'е (core/background_loop.py).
            from backend.core import background_loop
            result = await background_loop.run(self._execute_slow(name, args, list(self.pairs)))
        if name == "do_action" and result.get("ok"):
            self.actions_done += 1
        return result

    async def _execute_slow(self, name: str, args: Dict[str, Any], pairs: List[Dict[str, str]]) -> Dict[str, Any]:
        """Оркестратор и Telegram — выполняется в фоновом loop'е, со своей сессией БД."""
        from backend.db.session import SessionLocal
        from backend.models.agent_config import AgentConfig
        from backend.models.user import User

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == self.user_id).first()
            agent = db.query(AgentConfig).filter(AgentConfig.id == self.agent_id,
                                                 AgentConfig.user_id == self.user_id).first()
            if user is None or agent is None:
                return {"ok": False, "error": "Агент недоступен"}
            if name == "send_to_telegram":
                return await ov.send_owner_telegram(args.get("text") or "", agent, user, db)
            kind = "read" if name == "ask_agent" else "action"
            request = args.get("question") if kind == "read" else args.get("command")
            result = await ov.run_orchestrator_task(kind, request or "", agent, user, db, pairs)
            if kind == "action" and result.get("ok") and result.get("answer"):
                result["card"] = {"type": "done", "title": "Сделано", "text": result["answer"]}
            return result
        except Exception as exc:
            name_err = type(exc).__name__
            if name_err in ("InsufficientCreditsError", "SubscriptionExpiredError", "SubscriptionRequiredError"):
                return {"ok": False, "error": "Кредиты агента закончились или тариф истёк — скажи об этом владельцу."}
            _log(f"function {name} failed: {exc}\n{traceback.format_exc()}", "ERROR")
            return {"ok": False, "error": "Не получилось — попробуйте ещё раз"}
        finally:
            db.close()

    # ------------------------------------------------------------------ звук от браузера
    def _to_gemini(self, pcm: bytes) -> bytes:
        if self.in_rate == GEMINI_IN_RATE:
            return pcm
        out, self._resample_state = audioop.ratecv(pcm, 2, 1, self.in_rate, GEMINI_IN_RATE, self._resample_state)
        return out

    async def handle_client_messages(self) -> None:
        while True:
            message = await self.websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if message.get("bytes") is not None:
                if self.client.is_connected:
                    await self.client.process_audio(self._to_gemini(message["bytes"]))
                continue
            if message.get("text") is None:
                continue
            try:
                data = json.loads(message["text"])
            except json.JSONDecodeError:
                continue
            mtype = data.get("type", "")
            if mtype == "input_audio_buffer.append":
                try:
                    pcm = base64.b64decode(data.get("audio") or "")
                except Exception:
                    continue
                if pcm and self.client.is_connected:
                    await self.client.process_audio(self._to_gemini(pcm))
            elif mtype == "ping":
                await self.websocket.send_json({"type": "pong"})
            elif mtype == "audio_playback.stopped":
                self.client.set_assistant_speaking(False)
                self.interruption_state["is_assistant_speaking"] = False
            elif mtype == "session.end":
                raise WebSocketDisconnect()
            elif mtype == "prefs.save":
                await self._save_prefs(data.get("prefs"))
            elif mtype == "text.input":
                # быстрая команда из окна («Отчёт за сегодня») — как если бы владелец сказал её вслух
                text = str(data.get("text") or "").strip()[:500]
                if text and self.client.is_connected:
                    self.observe({"type": "transcript.delta", "role": "user", "delta": text})
                    self.typed.append(text)
                    await self.client.send_text_message(text)

    async def _save_prefs(self, raw: Any) -> None:
        """Настройки из окна (обращение, голос): в базу, а новое обращение — сразу в разговор."""
        new = ov.clean_prefs(raw)
        address_changed = any(new.get(k) != self.prefs.get(k) for k in ("address", "address_text", "formality"))
        self.prefs = new
        ok = await asyncio.to_thread(ov.save_prefs, self.user_id, self.agent_id, new)
        await self.websocket.send_json({"type": "prefs.saved", "ok": ok, "prefs": new,
                                        "greeting": ov.greeting_text(self.owner, new)})
        if address_changed and self.client.is_connected:
            await self.client.send_text_message(ov.address_change_text(new, self.owner))

    async def _watch_limit(self) -> None:
        try:
            await asyncio.sleep(max(60, ov.MAX_SESSION_SECONDS))
            await self.websocket.send_json({"type": "session.limit",
                                            "message": "Разговор длится слишком долго — начните новый"})
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ жизненный цикл
    async def run(self) -> str:
        self.client.turn_saver = self.save_turn
        gemini_task = asyncio.create_task(
            handle_gemini_messages(self.client, _SessionSocket(self.websocket, self), self.interruption_state))
        client_task = asyncio.create_task(self.handle_client_messages())
        limit_task = asyncio.create_task(self._watch_limit())
        reason = "unknown"
        try:
            done, _ = await asyncio.wait({gemini_task, client_task, limit_task}, return_when=asyncio.FIRST_COMPLETED)
            if client_task in done:
                exc = client_task.exception() if not client_task.cancelled() else None
                reason = "client_closed" if exc is None or isinstance(exc, (WebSocketDisconnect, ConnectionClosed)) \
                    else "client_error"
                if reason == "client_error":
                    _log(f"client loop error: {exc}", "ERROR")
            elif limit_task in done:
                reason = "time_limit"
            else:
                reason = "gemini_closed"
        except Exception as exc:
            reason = "error"
            _log(f"session error: {exc}\n{traceback.format_exc()}", "ERROR")
        finally:
            for task in (gemini_task, client_task, limit_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(gemini_task, client_task, limit_task, return_exceptions=True)
            try:
                await self.client.close()
            except Exception as exc:
                _log(f"client.close error: {exc}", "WARNING")
            await asyncio.to_thread(self._finish_sync, reason)
        return reason

    def _finish_sync(self, reason: str) -> None:
        from backend.db.session import SessionLocal
        from backend.models.agent_config import AgentConfig
        from backend.models.user import User

        elapsed = time.time() - self.started_at
        credits = 0
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == self.user_id).first()
            agent = db.query(AgentConfig).filter(AgentConfig.id == self.agent_id).first()
            if user is not None and agent is not None:
                if not getattr(user, "is_admin", False):
                    credits = ov.charge_voice_minutes(db, user, agent, elapsed)
                ov.append_voice_history(db, agent, self.pairs)
        except Exception as exc:
            _log(f"finish failed: {exc}\n{traceback.format_exc()}", "ERROR")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()
        _log(f"session finished ({reason}): {elapsed:.0f}s, pairs={len(self.pairs)}, functions={self.functions_called}, "
             f"actions={self.actions_done}, credits={credits}")


async def _fail(websocket: WebSocket, code: str, message: str, close_code: int = 1008) -> None:
    try:
        await websocket.send_json({"type": "error", "error": {"code": code, "message": message}})
        await websocket.close(code=close_code)
    except Exception:
        pass


def _load_owner(db, token: str, agent_id: Optional[str]):
    """JWT → (user, agent) — только свой агент."""
    from backend.core.security import decode_jwt_token
    from backend.models.agent_config import AgentConfig
    from backend.models.user import User

    if (token or "").startswith("ov1."):
        # короткий токен Telegram Mini App: открывает только голос этого агента
        scoped = ov.read_voice_token(token)
        if not scoped:
            return None, None
        try:
            user_uuid = uuid.UUID(scoped["user_id"])
            agent_id = scoped["agent_id"]
        except ValueError:
            return None, None
    else:
        try:
            payload = decode_jwt_token(token or "")
            user_uuid = uuid.UUID(str(payload.get("sub")))
        except Exception:
            return None, None
    user = db.query(User).filter(User.id == user_uuid).first()
    if user is None:
        return None, None
    q = db.query(AgentConfig).filter(AgentConfig.user_id == user.id)
    if agent_id:
        try:
            q = q.filter(AgentConfig.id == uuid.UUID(str(agent_id)))
        except ValueError:
            return user, None
        agent = q.first()
    else:
        agent = q.order_by(AgentConfig.created_at.asc()).first()
    return user, agent


async def handle_owner_voice_connection(websocket: WebSocket, db) -> None:
    """Точка входа /ws/agent-voice (сокет ещё не принят)."""
    await websocket.accept()
    params = websocket.query_params
    # JWT — первым сообщением {"type": "auth", "token": …}, а не в адресе: uvicorn пишет адрес сокета
    # с параметрами в журнал, токен кабинета оказался бы в логах Render.
    token = ""
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if isinstance(first, dict) and first.get("type") == "auth":
            token = str(first.get("token") or "")
    except Exception:
        token = ""
    user, agent = await asyncio.to_thread(_load_owner, db, token, params.get("agent_id"))
    err = access_error(user, agent)
    if err:
        await _fail(websocket, err["code"], err["message"])
        return
    api_key = settings.GEMINI_API_KEY or getattr(user, "gemini_api_key", None)
    if not api_key:
        await _fail(websocket, "not_configured", "Голосовой движок не настроен на сервере", 1011)
        return

    prefs = ov.clean_prefs(getattr(agent, "owner_voice_prefs", None))
    # голос: выбранный в этом окне (параметр) → сохранённый в настройках → по умолчанию
    voice = resolve_voice(params.get("voice") or prefs.get("voice"))
    try:
        in_rate = int(params.get("rate") or GEMINI_IN_RATE)
    except ValueError:
        in_rate = GEMINI_IN_RATE
    owner = SimpleNamespace(first_name=ov.owner_first_name(user))

    t0 = time.monotonic()
    ctx = await asyncio.to_thread(ov.build_session_context, db, user, agent)
    brief, cards = ctx.get("brief") or "", ctx.get("cards") or []
    prompt = ov.build_voice_prompt(user, agent, brief, agent.chat_history or [], voice=voice, prefs=prefs)
    config = SimpleNamespace(
        id=agent.id, user_id=user.id, name=f"Голосовой режим: {agent.name}", system_prompt=prompt,
        greeting_message=ov.greeting_text(owner, prefs), functions=[{"name": n} for n in ov.FUNCTION_NAMES],
        google_sheet_id=None, voice=voice,
    )
    user_id, agent_id = str(user.id), str(agent.id)
    client_id = f"owner-voice-{uuid.uuid4().hex[:8]}"
    user_agent = ""
    try:
        user_agent = websocket.headers.get("user-agent", "")
    except Exception:
        pass
    client = GeminiLiveClient(api_key, config, client_id, None, user_agent,
                              voice_override=voice, record_conversation=False, realtime_text=True,
                              await_setup=True, voice_hints=False, telephony=False,
                              blocking_tools=ov.BLOCKING_TOOLS, function_definitions=ov.GEMINI_FUNCTIONS)
    # дальше сессия БД вебсокета не нужна — каждая функция открывает свою
    try:
        db.close()
    except Exception:
        pass
    if not await client.connect():
        _log(f"Gemini did not open: {client.connect_error}", "ERROR")
        await _fail(websocket, "connection_failed", "Голосовой движок не ответил — попробуйте ещё раз", 1011)
        return
    connect_ms = int((time.monotonic() - t0) * 1000)
    await websocket.send_json({
        "type": "connection_status", "status": "connected", "provider": "owner_voice",
        "message": "Connected: Voksy Live Go (full-duplex)", "full_duplex": True,
        "audio_rate": GEMINI_OUT_RATE, "input_rate": GEMINI_IN_RATE, "voice": voice,
        "voices": GEMINI_LIVE_VOICES, "session_id": client.session_id, "client_id": client_id,
        "max_seconds": ov.MAX_SESSION_SECONDS, "prefs": prefs, "owner_name": owner.first_name,
    })
    if cards:
        # картина дня на экране, пока агент здоровается
        await websocket.send_json({"type": "owner.dashboard", "cards": cards})
    _log(f"session {client_id} started in {connect_ms}ms: user={user_id} agent={agent_id} voice={voice} "
         f"prompt={client.system_prompt_chars} chars (brief {len(brief)})")
    session = OwnerVoiceSession(websocket, user_id, agent_id, client, in_rate=in_rate, prefs=prefs, owner=owner)
    await session.run()
    try:
        await websocket.close()
    except Exception:
        pass
