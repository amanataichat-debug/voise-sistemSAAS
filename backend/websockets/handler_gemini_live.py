# backend/websockets/handler_gemini_live.py
"""
Карточка ElevenLabs с движком «Google Gemini Live» (voice_engine="gemini_live").

Тот же протокол виджета и телефона, что у GPT-Live и ElevenLabs, но говорит Gemini Live
(gemini-3.8-live, речь в речь, full-duplex): сервер Google сам ловит речь и перебивания, мы
стримим микрофон непрерывно. Вход в сессию — handler_eleven.handle_eleven_websocket_connection
(выбор движка по карточке), поэтому у движка общие с остальными: промпт, приветствие, функции,
база знаний агента, номера и звонки, «Диалоги» (eleven_conversations), журнал звонка, кошелёк.

Переиспользовано без копирования:
- GeminiLiveClient (gemini_client.py) — соединение, setup, VAD-профиль, функции NON_BLOCKING;
- handle_gemini_messages (handler_gemini.py) — разбор событий Gemini, транскрипты, перебивания,
  вызовы функций; реплики он отдаёт в gemini_client.turn_saver, мы копим пары (клиент, агент)
  и пишем диалог в конце сессии, как LiveVoiceSession у GPT-Live.

Ключ — серверный GEMINI_API_KEY, иначе gemini_api_key владельца карточки.
Голос — имя из GEMINI_LIVE_VOICES в колонке live_voice (регистр восстанавливаем).
Вход: по телефону PCM16 16 кГц (адаптер, профиль "gemini"), в виджете PCM16 24 кГц — сжимаем до 16.
Выход Gemini — PCM16 24 кГц (виджет играет как есть, адаптер сам понижает до 8 кГц).
"""

import asyncio
import audioop
import base64
import json
import math
import os
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosed

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.eleven_assistant import (
    DEFAULT_ELEVEN_GREETING,
    GEMINI_LIVE_DEFAULT_VOICE,
    GEMINI_LIVE_VOICES,
    ElevenConversation,
)
from backend.models.user import User
from backend.websockets.call_guard import CallEndGuard
from backend.websockets.call_log import CallLogRecorder
from backend.websockets.gemini_client import GEMINI_LIVE_MODEL, GeminiLiveClient
from backend.websockets.handler_gemini import handle_gemini_messages
from backend.websockets.handler_live import _save_dialog, async_save_to_google_sheets
from backend.websockets.wallet_meter import WalletMeter

logger = get_logger(__name__)

GEMINI_IN_RATE = 16000        # Gemini Live принимает PCM16 16 кГц
WIDGET_IN_RATE = 24000        # микрофон виджета в full-duplex (как у GPT-Live)
GEMINI_OUT_RATE = 24000
# Клиент заговорил, пока ответ ещё доигрывается (Google считает ход законченным) → обрываем доигрывание,
# если осталось больше этого (сек). GEMINI_PLAYBACK_BARGE_IN=0 — выключатель.
PLAYBACK_BARGE_IN = os.getenv("GEMINI_PLAYBACK_BARGE_IN", "1").strip().lower() not in ("0", "false", "no", "off")
PLAYBACK_BARGE_MIN_LEFT_S = 0.35
_PLACEHOLDERS = {"[no user input]", "[no response]", "[interrupted]", "[incomplete - disconnected]"}


def _log(message: str, level: str = "INFO") -> None:
    if level == "ERROR":
        logger.error(f"[GEMINI-LIVE] {message}")
    elif level == "WARNING":
        logger.warning(f"[GEMINI-LIVE] {message}")
    else:
        logger.info(f"[GEMINI-LIVE] {message}")


def resolve_voice(live_voice: Optional[str]) -> str:
    """Имя голоса Gemini с правильным регистром (в колонке оно в нижнем); чужой голос → по умолчанию."""
    wanted = (live_voice or "").strip().lower()
    for name in GEMINI_LIVE_VOICES:
        if name.lower() == wanted:
            return name
    return GEMINI_LIVE_DEFAULT_VOICE


def resolve_api_key(db: Session, assistant) -> Optional[str]:
    """Серверный ключ, иначе ключ владельца карточки."""
    if settings.GEMINI_API_KEY:
        return settings.GEMINI_API_KEY
    if getattr(assistant, "user_id", None):
        try:
            user = db.query(User).filter(User.id == assistant.user_id).first()
            if user is not None and user.gemini_api_key:
                return user.gemini_api_key
        except Exception as exc:
            _log(f"owner key lookup failed: {exc}", "WARNING")
    return None


class PhoneInputLevel:
    """Выравнивание громкости голоса звонящего перед Gemini (только телефон).

    Запись звонка 02.11.2026 (953a3a6c): голос клиента в линии −30…−40 dBFS, голос агента −20 — клиент на
    10–18 дБ тише. Приложение Gemini получает с микрофона телефона ровный громкий звук, а мы — тихую узкую
    полосу 8 кГц: VAD с низкой чувствительностью ловил начало фразы с опозданием, слова доходили обрывками
    («Слушай, баба», «Schwarze Panther»). Здесь медленная АРУ: усиление считается только по кадрам речи
    (выше шумового пола), вниз меняется быстро, вверх — плавно. Применяется только к кадрам речи и только
    пока агент молчит: шум пауз и эхо голоса агента не раздуваем (детектор речи Google обрывал бы агента).
    GEMINI_PHONE_AGC=0 — выключить, GEMINI_PHONE_AGC_TARGET_DBFS (−22), GEMINI_PHONE_AGC_MAX_DB (12).
    """

    def __init__(self) -> None:
        self.enabled = os.getenv("GEMINI_PHONE_AGC", "1").strip().lower() not in ("0", "false", "no", "off")
        try:
            target_db = float(os.getenv("GEMINI_PHONE_AGC_TARGET_DBFS", "-22"))
        except ValueError:
            target_db = -22.0
        try:
            max_db = float(os.getenv("GEMINI_PHONE_AGC_MAX_DB", "12"))
        except ValueError:
            max_db = 12.0
        self.target = 32768.0 * (10 ** (min(-6.0, target_db) / 20))
        self.max_gain = 10 ** (max(0.0, min(24.0, max_db)) / 20)
        self.gain = 1.0           # усиление речи, к которому идём
        self.applied = 1.0        # усиление, применённое к последнему кадру (плавный переход речь ↔ пауза)
        self.floor = 0.0          # шумовой пол линии (RMS)
        self.speech_frames = 0

    def process(self, pcm: bytes, assistant_playing: bool) -> bytes:
        if not self.enabled or not pcm:
            return pcm
        out = []
        step = 640   # 20 мс при 16 кГц
        for i in range(0, len(pcm), step):
            out.append(self._frame(pcm[i:i + step], assistant_playing))
        return b"".join(out)

    def _frame(self, pcm: bytes, assistant_playing: bool) -> bytes:
        try:
            rms = audioop.rms(pcm, 2)
        except Exception:
            return pcm
        # шумовой пол: быстро вниз; вверх — по кадрам без речи, а во время речи лишь ползёт (+2,5 %/с), чтобы
        # длинный монолог не стал «шумом», но выросший шум линии со временем всё же учёлся
        is_speech = self.floor > 0 and rms > max(self.floor * 2.5, 120)
        if self.floor <= 0 or rms < self.floor:
            self.floor = rms if self.floor <= 0 else self.floor * 0.7 + rms * 0.3
        elif is_speech:
            self.floor *= 1.0005
        else:
            self.floor = self.floor * 0.98 + rms * 0.02
        if is_speech:
            self.speech_frames += 1
            wanted = max(1.0, min(self.max_gain, self.target / max(rms, 1.0)))
            if wanted < self.gain:
                self.gain = self.gain * 0.7 + wanted * 0.3
            elif not assistant_playing:
                self.gain = self.gain * 0.97 + wanted * 0.03
        # Усиливаем только речь и только когда агент молчит: шум пауз и эхо агента остаются как были,
        # иначе детектор речи Google «услышал» бы клиента там, где его нет, и оборвал агента.
        target = self.gain if (is_speech and not assistant_playing) else 1.0
        self.applied = self.applied * 0.5 + target * 0.5
        if self.applied <= 1.01:
            return pcm
        try:
            return audioop.mul(pcm, 2, self.applied)
        except Exception:
            return pcm

    @property
    def gain_db(self) -> float:
        return round(20 * math.log10(max(self.gain, 1e-6)), 1)


class _ObserveSocket:
    """Прокладка между хендлером событий Gemini и клиентом: всё уходит как есть, а сессия видит события
    (стенограмма → прощание, звук → тишина, функции → журнал звонка)."""

    def __init__(self, websocket, session) -> None:
        self._ws = websocket
        self._session = session

    async def send_json(self, data, mode: str = "text"):
        if isinstance(data, dict) and data.get("type") == "gemini.voice_activity":
            # служебный сигнал детектора речи Google: решает сессия, клиенту (мост/виджет) не нужен
            try:
                await self._session.on_voice_activity(data.get("activity") or "")
            except Exception as exc:
                _log(f"voice activity handling failed: {exc}", "WARNING")
            return None
        try:
            self._session.observe(data)
        except Exception as exc:  # наблюдение не должно ронять звонок
            _log(f"observe failed: {exc}", "WARNING")
        if isinstance(data, dict) and data.get("type") == "conversation.interrupted" and self._session.greeting_protected():
            # перебивание на приветствии не чистит очередь звука: иначе уже готовое приветствие пропадает целиком
            self._session.note_greeting_guard("Gemini оборвал приветствие (шум линии) — очередь звука сохранена")
            return None
        return await self._ws.send_json(data, mode=mode)

    def __getattr__(self, name):
        return getattr(self._ws, name)


class GeminiLiveVoiceSession:
    """Один разговор: клиент (виджет / SIP-адаптер) ↔ Gemini Live."""

    def __init__(self, websocket: WebSocket, assistant, client: GeminiLiveClient, db: Session,
                 client_id: str, telephony: bool, call_log: Optional[CallLogRecorder]) -> None:
        self.websocket = websocket
        self.assistant = assistant
        self.client = client
        self.db = db
        self.client_id = client_id
        self.telephony = telephony
        self.call_log = call_log
        self._owns_call_log = False
        self.in_rate = GEMINI_IN_RATE if telephony else WIDGET_IN_RATE
        self._resample_state = None
        self.pairs: List[Dict[str, str]] = []
        self.audio_chunks_in = 0
        self.started_at = time.time()
        self.on_session_end = None
        # Завершение звонка как у GPT-Live: прощание + тишина → трубка; защита hangup_call от обрыва на полуслове
        self.guard = CallEndGuard(telephony, call_log)
        self.client.function_gate = self.guard.gate
        self.interruption_state: Dict[str, Any] = {
            "is_user_speaking": False,
            "is_assistant_speaking": False,
            "last_speech_start": 0,
            "last_speech_stop": 0,
            "interruption_count": 0,
            "last_interruption_time": 0,
            "assistant_audio_ever": False,
            "greeting_retries": 0,
        }
        # Защита приветствия. Звонок 02.11.2026 (9d508d88): Gemini сгенерировал приветствие за доли секунды
        # (быстрее реального времени), шум линии при снятии трубки сработал как «перебивание», адаптер очистил
        # очередь звука — клиент 10 с слышал тишину и переспрашивал «алло?». Пока приветствие не проиграно,
        # Gemini получает тишину вместо звука линии, а «перебивание» очередь не чистит.
        self._greet_audio_s = 0.0
        self._greet_first_audio: Optional[float] = None
        self._greet_generated = False
        self._greet_notes = 0
        # Доигрывание ответа у клиента: Gemini отдаёт звук быстрее реального времени, поэтому «говорит» агент
        # для собеседника до _play_until, а не до turnComplete
        self._play_until = 0.0
        self.playback_barge_ins = 0
        self.input_level = PhoneInputLevel() if telephony else None

    @property
    def dialog_session_id(self) -> str:
        # у звонка диалог пишется под id звонка (SIP-роут связывает по нему звонок и реплики)
        return getattr(self.assistant, "sip_session_id", None) or self.client.session_id

    # ------------------------------------------------------------------ наблюдение за событиями
    def observe(self, data: Dict[str, Any]) -> None:
        """События, идущие клиенту: стенограмма и звук — в страж прощания, функции — в журнал звонка."""
        mtype = data.get("type")
        if mtype == "response.audio.delta":
            try:
                secs = len(base64.b64decode(data.get("delta") or "")) / 2 / GEMINI_OUT_RATE
            except Exception:
                secs = 0.0
            now = time.time()
            self._play_until = max(now, self._play_until) + secs
        elif mtype == "conversation.interrupted" and not self.greeting_protected():
            self._play_until = 0.0
        if not self._greet_generated:
            if mtype == "response.audio.delta":
                if self._greet_first_audio is None:
                    self._greet_first_audio = time.time()
                try:
                    self._greet_audio_s += len(base64.b64decode(data.get("delta") or "")) / 2 / GEMINI_OUT_RATE
                except Exception:
                    pass
            elif mtype in ("assistant.speech.ended", "turn.saved") and self._greet_first_audio is not None:
                self._greet_generated = True
        if mtype == "transcript.delta":
            self.guard.on_transcript("user" if data.get("role") == "user" else "assistant", data.get("delta") or "")
        elif mtype == "response.audio.delta":
            self.guard.on_audio()
        elif self.call_log is not None:
            name = data.get("function")
            if mtype == "function_call.executing":
                self.call_log.functions += 1
                self.call_log.add("function", f"Вызов функции {name}",
                                  arguments=json.dumps(data.get("arguments"), ensure_ascii=False, default=str)[:500])
            elif mtype == "function_call.completed":
                result = data.get("result")
                failed = isinstance(result, dict) and bool(result.get("error"))
                if failed:
                    self.call_log.function_errors += 1
                secs = data.get("execution_time")
                self.call_log.add(
                    "function",
                    f"Функция {name} {'вернула ошибку' if failed else 'выполнена'}"
                    + (f" за {secs:.2f} с" if isinstance(secs, (int, float)) else ""),
                    level="warning" if failed else "info",
                    result=(json.dumps(result, ensure_ascii=False, default=str)[:500] if result is not None else None))
            elif mtype in ("function_call.error", "function_call.delivery_error"):
                self.call_log.function_errors += 1
                self.call_log.add("function", f"Функция {name or data.get('function_call_id')} не выполнена: "
                                              f"{data.get('error')}", level="error")

    async def _watch_end(self) -> None:
        """Раз в четверть секунды: прощание было и все молчат → просим адаптер положить трубку (после проигрыша звука)."""
        try:
            while True:
                await asyncio.sleep(0.25)
                if self.guard.should_auto_hangup(time.time()):
                    self.guard.mark_hangup_sent("прощание и тишина")
                    await self.websocket.send_json({"type": "call.hangup", "reason": "farewell", "drain": True})
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ реплики
    async def save_turn(self, user: str, assistant: str) -> bool:
        """gemini_client.turn_saver: реплика закончилась — копим пару и пишем в журнал звонка."""
        user = "" if (user or "").strip() in _PLACEHOLDERS else (user or "").strip()
        assistant = "" if (assistant or "").strip() in _PLACEHOLDERS else (assistant or "").strip()
        if not (user or assistant):
            return True
        self.pairs.append({"user": user, "assistant": assistant})
        if self.call_log:
            if user:
                self.call_log.user_turns += 1
                self.call_log.add("user", f"Клиент: «{user}»")
            if assistant:
                self.call_log.assistant_turns += 1
                self.call_log.add("assistant", f"Агент: «{assistant}»")
        return True

    # ------------------------------------------------------------------ защита приветствия
    def greeting_protected(self, now: Optional[float] = None) -> bool:
        """Идёт приветствие: ещё не началось (до 8 с от старта) или его звук не доигран (+0,4 с, не дольше 15 с)."""
        if not self.telephony:
            return False   # в виджете эхо гасит браузер, шума снятия трубки нет
        now = now or time.time()
        if self._greet_first_audio is None:
            return now - self.started_at < 8.0
        if now - self._greet_first_audio > 15.0:
            return False
        return (not self._greet_generated) or now < self._greet_first_audio + self._greet_audio_s + 0.4

    def note_greeting_guard(self, text: str) -> None:
        if self.call_log is not None and self._greet_notes < 2:
            self._greet_notes += 1
            self.call_log.add("session", text)

    def playback_left(self, now: Optional[float] = None) -> float:
        """Сколько секунд ответа агента ещё не доиграно у собеседника."""
        return max(0.0, self._play_until - (now or time.time()))

    async def on_voice_activity(self, activity: str) -> None:
        """Детектор речи Google увидел начало речи собеседника. Если ответ агента ещё доигрывается (Google уже
        закончил ход и сам `interrupted` не пришлёт) — обрываем доигрывание, как это делает живой человек:
        иначе агент договаривает 5–10 с поверх клиента, а потом отвечает на его новую фразу (звонок 02.11.2026)."""
        if activity != "start" or not PLAYBACK_BARGE_IN:
            return
        now = time.time()
        left = self.playback_left(now)
        if left < PLAYBACK_BARGE_MIN_LEFT_S or self.greeting_protected(now):
            return
        self._play_until = 0.0
        self.playback_barge_ins += 1
        self.interruption_state["interruption_count"] += 1
        self.interruption_state["last_interruption_time"] = now
        self.interruption_state["is_assistant_speaking"] = False
        try:
            self.client.set_assistant_speaking(False)
        except Exception:
            pass
        if self.call_log is not None:
            self.call_log.interruptions += 1
            self.call_log.add("barge_in", f"Клиент заговорил поверх ответа — агент замолчал (не доиграно {left:.1f} с)")
        _log(f"playback barge-in: {left:.1f}s of reply dropped")
        await self.websocket.send_json({"type": "conversation.interrupted", "timestamp": now,
                                        "reason": "caller_speech_during_playback"})

    async def _feed(self, pcm: bytes) -> None:
        """Звук линии в Gemini; на приветствии — тишина той же длины (поток не прерывается, VAD не срабатывает)."""
        if not pcm or not self.client.is_connected:
            return
        data = self._to_gemini(pcm)
        if self.input_level is not None:
            data = self.input_level.process(data, assistant_playing=self.playback_left() > 0)
        if self.greeting_protected():
            data = b"\x00" * len(data)
        await self.client.process_audio(data)

    # ------------------------------------------------------------------ вход от клиента
    def _to_gemini(self, pcm: bytes) -> bytes:
        if self.in_rate == GEMINI_IN_RATE:
            return pcm
        out, self._resample_state = audioop.ratecv(pcm, 2, 1, self.in_rate, GEMINI_IN_RATE, self._resample_state)
        return out

    async def handle_client_messages(self) -> None:
        """Цикл чтения от виджета / адаптера: звук в Gemini, служебные сообщения — как в handler_gemini."""
        while True:
            message = await self.websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if message.get("text") is not None:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                msg_type = data.get("type", "")
                if msg_type == "ping":
                    await self.websocket.send_json({"type": "pong"})
                elif msg_type == "session.update":
                    await self.websocket.send_json({
                        "type": "session.update.ack",
                        "event_id": data.get("event_id", f"ack_{int(time.time() * 1000)}"),
                    })
                elif msg_type == "input_audio_buffer.append":
                    try:
                        pcm = base64.b64decode(data.get("audio") or "")
                    except Exception:
                        continue
                    self.audio_chunks_in += 1
                    await self._feed(pcm)
                    await self.websocket.send_json({"type": "input_audio_buffer.append.ack",
                                                    "event_id": data.get("event_id")})
                elif msg_type == "response.cancel":
                    await self.websocket.send_json({"type": "response.cancel.ack", "event_id": data.get("event_id")})
                elif msg_type == "audio_playback.stopped":
                    self.client.set_assistant_speaking(False)
                    self.interruption_state["is_assistant_speaking"] = False
                elif msg_type == "screen.context":
                    pass  # Vision на этом пути не используется
                # speech.user_started/stopped: сервер Google сам определяет речь и перебивания
            elif message.get("bytes") is not None:
                self.audio_chunks_in += 1
                await self._feed(message["bytes"])

    # ------------------------------------------------------------------ жизненный цикл
    async def run(self) -> None:
        self.client.turn_saver = self.save_turn
        gemini_task = asyncio.create_task(
            handle_gemini_messages(self.client, _ObserveSocket(self.websocket, self), self.interruption_state))
        client_task = asyncio.create_task(self.handle_client_messages())
        watch_task = asyncio.create_task(self._watch_end()) if self.telephony else None
        reason = "unknown"
        try:
            done, _ = await asyncio.wait({gemini_task, client_task}, return_when=asyncio.FIRST_COMPLETED)
            if client_task in done:
                exc = client_task.exception() if not client_task.cancelled() else None
                if exc is None or isinstance(exc, (WebSocketDisconnect, ConnectionClosed)):
                    reason = "client_disconnected"
                else:
                    reason = "client_error"
                    _log(f"client loop error: {exc}", "ERROR")
            else:
                reason = "gemini_closed"
        except Exception as exc:
            reason = "error"
            _log(f"session error: {exc}\n{traceback.format_exc()}", "ERROR")
        finally:
            if self.on_session_end is not None:
                try:
                    await self.on_session_end()
                except Exception as exc:
                    _log(f"on_session_end failed: {exc}", "ERROR")
            tasks = [t for t in (gemini_task, client_task, watch_task) if t is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.client.close()
            except Exception as exc:
                _log(f"client.close error: {exc}", "WARNING")
            await self._finish(reason)

    async def _finish(self, reason: str) -> None:
        elapsed = time.time() - self.started_at
        # Реплика, которую Gemini не успел закрыть turnComplete, уже ушла в turn_saver через финальное сохранение
        saved = 0
        if self.pairs:
            saved = await _save_dialog(
                str(self.assistant.id), self.dialog_session_id, self.pairs, elapsed,
                self.client.conversation_record_id, self.client.model, conversation_model=ElevenConversation)
            sheet_id = getattr(self.assistant, "google_sheet_id", None)
            if sheet_id:
                for p in self.pairs:
                    if p["user"] or p["assistant"]:
                        asyncio.create_task(async_save_to_google_sheets(
                            sheet_id=sheet_id, user_message=p["user"], assistant_message=p["assistant"],
                            function_result=None, conversation_id=self.client.conversation_record_id,
                            context="Gemini Live dialog"))
        _log(f"session {self.client_id} finished ({reason}): {elapsed:.1f}s, audio_in={self.audio_chunks_in} chunks, "
             f"interruptions={self.interruption_state['interruption_count']}, "
             f"dialog_pairs={len(self.pairs)} saved={saved}")
        if self.call_log:
            census = self.client.census
            n_in = census.get("serverContent.inputTranscription", 0) + census.get("inputTranscription", 0)
            n_out = census.get("serverContent.outputTranscription", 0) + census.get("outputTranscription", 0)
            self.call_log.add(
                "session",
                "События Gemini за звонок: " + (", ".join(f"{k}×{v}" for k, v in sorted(census.items())) or "ничего не пришло")
                + f"; переподключений {self.client.reconnect_count}, goAway {self.client.go_away_seen}",
            )
            if self.audio_chunks_in > 50 and n_in == 0:
                self.call_log.add(
                    "session",
                    "Gemini не прислал расшифровку речи клиента (inputTranscription): диалог, память и итог звонка для "
                    "агента-оркестратора будут пустыми, звонок засчитается как «не ответил». Проверьте поле "
                    "inputAudioTranscription в setup и GEMINI_INPUT_TRANSCRIPTION_LANGUAGES", level="error")
            elif self.audio_chunks_in > 50 and self.interruption_state["assistant_audio_ever"] and n_out == 0:
                self.call_log.add("session", "Gemini не прислал расшифровку речи агента (outputTranscription): "
                                             "реплики агента не попадут в диалог", level="warning")
            self.call_log.add(
                "session",
                f"Сессия завершена ({reason}): {elapsed:.1f} с, прерываний {self.interruption_state['interruption_count']}, "
                f"реплик в диалоге {len(self.pairs)}"
                + (f", из них клиент оборвал доигрывание {self.playback_barge_ins}" if self.playback_barge_ins else "")
                + (f"; громкость линии клиента +{self.input_level.gain_db} дБ"
                   if self.input_level is not None and self.input_level.enabled and self.input_level.speech_frames else "")
                + ("" if self.interruption_state["assistant_audio_ever"] else
                   ". Gemini не прислал ни одного звука — смотрите предупреждения выше"),
                level="info" if self.interruption_state["assistant_audio_ever"] else "warning",
            )
            if self._owns_call_log:
                await self.call_log.save()
        await self._send_webhook()

    async def _send_webhook(self) -> None:
        """Итоговый webhook conversation.completed владельцу карточки (как у GPT-Live)."""
        if not getattr(self.assistant, "user_id", None):
            return
        try:
            from backend.db.session import SessionLocal
            from backend.services.webhook_notification import send_webhook_safe

            db = SessionLocal()
            try:
                user = db.query(User).get(self.assistant.user_id)
                if user and user.has_webhook_config():
                    await send_webhook_safe(
                        db=db, webhook_url=user.webhook_url, webhook_enabled=user.webhook_enabled,
                        source="sip_call" if self.telephony else "web_chat",
                        session_id=self.dialog_session_id, assistant_id=str(self.assistant.id),
                        assistant_name=self.assistant.name, assistant_type="eleven",
                        caller_number=None, call_direction=None, duration_seconds=None,
                        call_cost=None, record_url=None,
                    )
            finally:
                db.close()
        except Exception as exc:
            _log(f"webhook error: {exc}", "ERROR")


def _create_conversation_record(db: Session, assistant, session_id: str, assistant_message: str = "") -> Optional[str]:
    """Пустая запись диалога: к ней привязываются function_logs этой сессии (как у GPT-Live)."""
    try:
        conv = ElevenConversation(assistant_id=assistant.id, session_id=session_id,
                                  user_message="", assistant_message=assistant_message)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return str(conv.id)
    except Exception as exc:
        _log(f"conversation record failed: {exc}", "ERROR")
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _record_failed_dialog(assistant_id: str, session_id: str, message: str) -> Optional[str]:
    """
    Запись диалога о НЕсостоявшемся звонке — СВОЕЙ сессией БД.

    Сессию звонка (db SIP-роута) к этому моменту мог испортить предыдущий сбой (aborted transaction):
    запись в неё молча падала, и в «Диалогах» не появлялось ничего — владелец не видел ни звонка, ни
    причины (01.11.2026). Отдельная сессия не зависит от состояния звонка.
    """
    from backend.db.session import SessionLocal

    db = SessionLocal()
    try:
        conv = ElevenConversation(assistant_id=uuid.UUID(str(assistant_id)), session_id=session_id,
                                  user_message="", assistant_message=message)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return str(conv.id)
    except Exception as exc:
        _log(f"failed-dialog record failed: {exc}", "ERROR")
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        db.close()


async def run_gemini_live_session(websocket: WebSocket, assistant, db: Session, client_id: str, fail,
                                  meter: Optional[WalletMeter] = None) -> None:
    """
    Карточка ElevenLabs с движком Gemini Live. Сокет уже принят, кошелёк проверен, журнал звонка
    заведён (виджет) или унаследован от SIP-роута — см. handler_eleven.handle_eleven_websocket_connection.
    """
    from sqlalchemy.orm.attributes import set_committed_value
    from backend.websockets.handler_eleven import _check_subscription

    api_key = resolve_api_key(db, assistant)
    if not api_key:
        await fail("gemini_not_configured",
                   "Ключ Google Gemini не задан: добавьте GEMINI_API_KEY на сервере или ключ в профиле владельца", 1011)
        return
    telephony = bool(getattr(assistant, "telephony_mode", False))
    voice = resolve_voice(assistant.live_voice)
    greeting = assistant.greeting_message or DEFAULT_ELEVEN_GREETING
    if not assistant.greeting_message:
        set_committed_value(assistant, "greeting_message", greeting)  # только в памяти
    user_agent = ""
    try:
        user_agent = websocket.headers.get("user-agent", "")
    except Exception:
        pass
    t_start = time.monotonic()

    sub_task = asyncio.create_task(_check_subscription(assistant))
    client = GeminiLiveClient(api_key, assistant, client_id, db, user_agent,
                              voice_override=voice, record_conversation=False, realtime_text=True,
                              await_setup=True, voice_hints=True, telephony=telephony, blocking_tools=True)
    connect_task = asyncio.create_task(client.connect())

    sub = await sub_task
    if sub is not None and not sub.get("active"):
        connect_task.cancel()
        await asyncio.gather(connect_task, return_exceptions=True)
        try:
            await client.close()
        except Exception:
            pass
        code = "TRIAL_EXPIRED" if sub.get("is_trial") else "SUBSCRIPTION_EXPIRED"
        msg = "Ваш пробный период истек" if sub.get("is_trial") else "Ваша подписка истекла"
        try:
            await websocket.send_json({"type": "error", "error": {
                "code": code, "message": msg, "subscription_status": sub, "requires_payment": True}})
            await websocket.close(code=1008)
        except Exception:
            pass
        return
    if not await connect_task:
        # Причина от Google (неверный ключ, нет такой модели, отклонённое поле) — клиенту и в журнал звонка
        reason = client.connect_error or "нет связи с Google"
        if call_log_early := CallLogRecorder.current():
            call_log_early.add("session", f"Gemini Live не открылся: {reason}", level="error")
            # Звонок оборвётся, а диалога не будет — владелец не увидит ни звонка, ни журнала (01.11.2026: «Анри»
            # сбрасывался через 3 с без единой записи). Оставляем запись диалога с причиной, журнал привязываем к ней.
            failed_session = getattr(assistant, "sip_session_id", None) or client.session_id
            call_log_early.session_id = failed_session
            call_log_early.assistant_type = "eleven"
            call_log_early.assistant_id = str(assistant.id)
            call_log_early.meta.update({"engine": "gemini_live", "llm_model": GEMINI_LIVE_MODEL})
            await asyncio.to_thread(_record_failed_dialog, str(assistant.id), failed_session,
                                    f"⚠ Звонок не соединился: Gemini Live ({GEMINI_LIVE_MODEL}) не открылся — {reason}")
        await fail("gemini_connection_failed", f"Gemini Live ({GEMINI_LIVE_MODEL}) не открылся: {reason}", 1011)
        return
    connect_ms = int((time.monotonic() - t_start) * 1000)

    session_id = getattr(assistant, "sip_session_id", None) or client.session_id
    client.conversation_record_id = await asyncio.to_thread(_create_conversation_record, db, assistant, session_id)

    call_log = CallLogRecorder.current()
    if call_log is not None:
        call_log.session_id = session_id
        call_log.assistant_type = "eleven"
        call_log.assistant_id = str(assistant.id)
        call_log.meta.update({
            "assistant_name": assistant.name,
            "engine": "gemini_live",
            "llm_model": client.model,
            "voice": voice,
            "language": assistant.language,
            "telephony_profile": telephony,
        })
        from backend.websockets.gemini_client import thinking_level
        _tl = thinking_level(client.model)
        thinking_note = f", рассуждения {_tl}, функции без ожидания" if _tl else ""
        _langs = getattr(client, "sent_transcription_languages", None) or []
        thinking_note += (f"; текст речи клиента: {', '.join(_langs)}" if _langs
                          else "; текст речи клиента: без подсказки языков")
        call_log.add("session", f"Сессия начата: Google {client.model}{thinking_note} (речь в речь), голос {voice}; "
                                f"подключение {connect_ms} мс; промпт {client.system_prompt_chars} симв. "
                                f"(карточка + знания агента + правила манеры)",
                     functions=", ".join(client.enabled_functions) or "нет — агент не сможет искать в базе знаний")

    await websocket.send_json({
        "type": "connection_status",
        "status": "connected",
        "provider": "eleven",
        "engine": "gemini_live",
        "transport": client.model,
        "model": client.model,
        "message": f"Connected to Google {client.model} (full-duplex)",
        "full_duplex": True,
        "audio_rate": GEMINI_OUT_RATE,
        "voice": voice,
        "voices": GEMINI_LIVE_VOICES,
        "session_id": client.session_id,
        "client_id": client_id,
        "functions_enabled": len(client.enabled_functions),
        "telephony": telephony,
        "enable_vision": False,
        "greeting_message": greeting,
    })
    _log(f"session {client_id} started on Gemini Live in {connect_ms}ms: assistant={assistant.id} '{assistant.name}' "
         f"voice={voice} telephony={telephony} functions={client.enabled_functions}")

    session = GeminiLiveVoiceSession(websocket, assistant, client, db, client_id, telephony, call_log)
    if meter is not None:
        meter.start(websocket)
        session.on_session_end = lambda: meter.finish(session_id)
    await session.run()
    if meter is not None:
        await meter.finish(session_id)
