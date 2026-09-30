"""
Стенограмма телефонного звонка, собранная по событиям голосового хендлера.

Хендлеры говорят на протоколе виджета, но транскрипты у провайдеров приходят
разными событиями:
  - Fish / Eleven:  input.transcription {transcript} (абонент),
                    response.text.delta / response.text.done {text} (ассистент);
  - Gemini:         input.transcription.complete / output.transcription.complete {text};
  - GPT-Live:       transcript.delta {role, delta} (фрагменты, склеиваются по роли).

HandlerSocket (sip_media_adapter) отдаёт каждое событие в on_event → CallTranscript.on_event.
Результат — список ходов [{"role": "user"|"assistant", "text": str, "t": секунды от начала}],
он пишется в sip_calls.transcript и отдаётся оркестратору агента текстом (as_text()).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

ROLE_LABELS = {"assistant": "Агент", "user": "Клиент"}


class CallTranscript:
    def __init__(self) -> None:
        self.started = time.time()
        self.turns: List[Dict[str, Any]] = []
        self._pending_assistant = ""  # дельты ответа, для которого ещё не пришёл *.done

    # ------------------------------------------------------------------ input
    def add(self, role: str, text: Optional[str], merge: bool = False) -> None:
        """Добавить ход. merge=True — дописать фрагмент к последнему ходу той же роли (GPT-Live)."""
        text = text or ""
        if merge and self.turns and self.turns[-1]["role"] == role:
            self.turns[-1]["text"] += text
            return
        if not text.strip():
            return
        self.turns.append({"role": role, "text": text if merge else text.strip(),
                           "t": round(time.time() - self.started, 1)})

    def add_greeting(self, text: Optional[str]) -> None:
        """Приветствие Fish/Eleven озвучивается напрямую, без текстовых событий хендлера."""
        if text and text.strip():
            self.turns.append({"role": "assistant", "text": text.strip(), "t": 0.0})

    def _flush_assistant(self) -> None:
        if self._pending_assistant.strip():
            self.add("assistant", self._pending_assistant)
        self._pending_assistant = ""

    def on_event(self, data: Dict[str, Any]) -> None:
        mtype = data.get("type") or ""
        if mtype == "input.transcription":
            # Fish/Eleven шлют готовую фразу; Gemini — промежуточные куски (у него есть *.complete)
            if "transcript" in data:
                self._flush_assistant()
                self.add("user", data.get("transcript"))
        elif mtype == "input.transcription.complete":
            self._flush_assistant()
            self.add("user", data.get("text"))
        elif mtype == "output.transcription.complete":
            self.add("assistant", data.get("text"))
        elif mtype == "response.text.delta":
            self._pending_assistant += data.get("delta") or ""
        elif mtype == "response.text.done":
            text = data.get("text")
            self._pending_assistant = ""
            self.add("assistant", text)
        elif mtype == "transcript.delta":
            role = "user" if data.get("role") == "user" else "assistant"
            self.add(role, data.get("delta"), merge=True)

    # ----------------------------------------------------------------- output
    def finish(self) -> List[Dict[str, Any]]:
        """Дописать недосказанный ответ (обрыв/перебивание) и вернуть ходы."""
        self._flush_assistant()
        for turn in self.turns:
            turn["text"] = " ".join(turn["text"].split())
        self.turns = [t for t in self.turns if t["text"]]
        return self.turns

    @property
    def has_user_speech(self) -> bool:
        return any(t["role"] == "user" for t in self.turns)


def transcript_as_text(turns: Optional[List[Dict[str, Any]]]) -> str:
    """Ходы → текст для оркестратора: «Агент: …\\nКлиент: …»."""
    lines = []
    for turn in turns or []:
        text = (turn.get("text") or "").strip()
        if text:
            lines.append(f"{ROLE_LABELS.get(turn.get('role'), 'Агент')}: {text}")
    return "\n".join(lines)
