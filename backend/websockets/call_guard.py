# backend/websockets/call_guard.py
"""
Завершение телефонного звонка для движков без своей логики прощания (Gemini Live у карточек ElevenLabs).

У GPT-Live (handler_live.LiveVoiceSession) есть две страховки, без которых звонок «висит» или рвётся:
1. Автозавершение: клиент или ассистент попрощался → клиент молчит несколько секунд → хендлер сам просит
   положить трубку (модель hangup_call вызывает не всегда; живой звонок с Gemini 01.11.2026: прощание было,
   трубку пришлось класть самому).
2. Защита hangup_call: модель вызывала завершение, пока клиент ещё называл время и номер, и связь рвалась
   на полуслове — такой вызов отклоняется, пока клиент не попрощался или не замолчал.

Здесь та же логика (те же регулярки прощаний и пороги — импортируются из handler_live, чтобы правила не
расходились), оформленная отдельным объектом: хендлер кормит его фрагментами стенограммы и событиями звука,
а он говорит, пора ли класть трубку.

Отличие от GPT-Live: звук Gemini приходит быстрее реального времени, поэтому «ассистент закончил говорить»
по приходу аудио не значит «клиент уже услышал прощание». Команда на завершение идёт с drain=True — адаптер
сначала дожидается, пока мост проиграет очередь, и только потом кладёт трубку.
"""

import json
import time
from typing import Any, Dict, Optional

from backend.websockets.handler_live import (
    AUTO_HANGUP,
    HANGUP_ASSISTANT_QUIET_SEC,
    HANGUP_GUARD_SILENCE_SEC,
    HANGUP_SILENCE_SEC,
    hangup_refusal_text,
    is_assistant_closing,
    is_caller_closing,
)


class CallEndGuard:
    """Прощание → тишина → «пора класть трубку»; и отказ преждевременному hangup_call."""

    def __init__(self, phone: bool, call_log=None) -> None:
        self.phone = phone
        self.call_log = call_log
        self.last_user_voice_at = 0.0     # последняя речь клиента (по стенограмме)
        self.last_audio_at = 0.0          # последний кусок звука ассистента
        self.farewell_at: Optional[float] = None
        self.caller_farewell_at: Optional[float] = None   # прощание именно клиента (для hangup_call)
        self.last_user_text = ""                           # последняя реплика клиента целиком
        self.hangup_sent = False
        self.rejections = 0
        self._buf = {"user": "", "assistant": ""}
        self._role: Optional[str] = None

    # ------------------------------------------------------------------ вход
    def on_audio(self) -> None:
        self.last_audio_at = time.time()

    def on_transcript(self, role: str, delta: str) -> None:
        """Фрагмент стенограммы. Реплика роли копится, пока не заговорит другая сторона."""
        if not delta:
            return
        if self._role and self._role != role:
            self._buf[self._role] = ""          # реплика прежнего говорящего закончилась
        self._role = role
        self._buf[role] += delta
        if not delta.strip():
            return
        if role == "user":
            self.last_user_voice_at = time.time()
            self.last_user_text = self._buf["user"]
        if AUTO_HANGUP:
            self._scan(role, self._buf[role])

    def _note(self, text: str, level: str = "info") -> None:
        if self.call_log is not None:
            self.call_log.add("session", text, level=level)

    def _scan(self, role: str, text: str) -> None:
        if not self.phone or self.hangup_sent:
            return
        if role == "user":
            closing = is_caller_closing(text)
        else:
            # прощание ассистента до первой речи клиента — это приветствие («спасибо за звонок в …»)
            closing = self.last_user_voice_at > 0 and is_assistant_closing(text)
            if not closing:
                if self.farewell_at is not None and len(text.split()) >= 4:
                    self.farewell_at = None
                    self._note("Прощание отменено: ассистент продолжил разговор")
                return
        if closing:
            if self.farewell_at is None:
                self._note(f"Прощание ({'клиент' if role == 'user' else 'ассистент'}): "
                           f"ждём тишины, чтобы завершить звонок")
            self.farewell_at = time.time()
            if role == "user":
                self.caller_farewell_at = self.farewell_at

    # ------------------------------------------------------------------ решение
    def should_auto_hangup(self, now: float) -> bool:
        """Прощание было, клиент и ассистент после него молчат достаточно долго."""
        if not AUTO_HANGUP or self.hangup_sent or self.farewell_at is None or not self.phone:
            return False
        if self.last_user_voice_at > self.farewell_at + 1.5:   # клиент заговорил после прощания
            self.farewell_at = None
            return False
        if now - self.last_user_voice_at < 1.0:
            return False
        if now - self.last_audio_at < HANGUP_ASSISTANT_QUIET_SEC:
            return False
        return now - max(self.last_user_voice_at, self.farewell_at) >= HANGUP_SILENCE_SEC

    def mark_hangup_sent(self, reason: str) -> None:
        self.hangup_sent = True
        self._note(f"Автозавершение звонка: {reason} (модель hangup_call не вызвала)")

    def gate(self, name: str, arguments: Any, agent_quiet_since: Optional[float] = None) -> Optional[str]:
        """
        Перед выполнением функции: None — можно; строка — почему нельзя (её прочтёт модель).
        Сейчас защищается только hangup_call (см. docstring модуля и handler_live.hangup_refusal_text).
        agent_quiet_since — когда агент замолчал ДЛЯ КЛИЕНТА (у Gemini — конец доигрывания, а не приход звука).
        """
        if name != "hangup_call" or not self.phone:
            return None
        args: Dict[str, Any] = arguments if isinstance(arguments, dict) else {}
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                args = {}
        quiet = self.last_audio_at if agent_quiet_since is None else max(self.last_audio_at, agent_quiet_since)
        refusal = hangup_refusal_text(str(args.get("reason") or ""), self.last_user_text, self.caller_farewell_at,
                                      self.last_user_voice_at, quiet)
        if refusal:
            self.rejections += 1
            self._note("Завершение звонка отклонено: клиент не прощался и не просил закончить — звонок продолжается",
                       "warning")
        return refusal