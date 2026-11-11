# backend/websockets/echo_gate.py
"""
Подавление эха и шума телефонной линии, пока агент говорит (GPT-Live и Gemini Live, только телефон).

07.11.2026, тест-звонки: «шумный, перебивает, сбивается и начинает фразу заново». Обе голосовые модели слышат
линию всё время, в том числе пока говорят сами: эхо собственного голоса на громкой связи, шум улицы, кашель
они принимали за речь клиента — сбивались, начинали заново («Да-… да, я вас слушаю»), отвечали на шум.
Браузер (виджет) гасит эхо сам, поэтому ворота работают только на телефоне.

Пока агент говорит (и ещё ECHO_TAIL_SEC после — звук идёт до телефона и обратно с задержкой), кусок входа
тише порога приглушается в GATE_GAIN раз (≈ −24 дБ; поток в модель не прерывается). 11.11.2026: раньше он
заменялся абсолютной цифровой тишиной — модель слышала рывки «тишина ↔ шум линии», а тихое начало слова
клиента (первый слог перебивания) пропадало целиком. Приглушённое эхо для модели уже не речь, а переходы мягкие. Порог — не ниже MIN_RMS
(≈ −42 dBFS), не ниже SPEECH_SHARE от обычной громкости речи ЭТОГО клиента (меряется, пока агент молчит)
и в FLOOR_FACTOR раз выше шума линии. Громкая речь клиента проходит сразу и держит ворота открытыми
HOLD_SEC (хвосты слов), так что перебить агента можно — в том числе тихому клиенту: у части клиентов голос
в линии на 10–18 дБ тише агента, поэтому главный порог относительный.
"""

import time
from typing import Optional

try:
    import audioop  # Python ≤3.12; на 3.13+ — пакет audioop-lts
except ImportError:  # pragma: no cover
    audioop = None

ECHO_TAIL_SEC = 0.8
HOLD_SEC = 0.6
MIN_RMS = 250
SPEECH_SHARE = 0.3
FLOOR_FACTOR = 6.0
SPEECH_RMS = 300          # громче этого (и в 4 раза выше шума) — речь клиента, пока агент молчит
SPEECH_FLOOR_FACTOR = 4.0
GATE_GAIN = 0.063         # ≈ −24 дБ: эхо/шум неразборчивы, но не «обрыв в ноль»


def update_floor(floor: Optional[float], rms: float, is_speech: bool) -> float:
    """
    Шум линии (как у PhoneInputLevel в handler_gemini_live): вниз сразу; по кадрам без речи — плавно вверх;
    во время речи почти не растёт (+2,5 %/с), иначе за пару секунд разговора «пол» дорастал бы до громкости
    голоса и порог глушил бы самого клиента. Первый кадр не поднимает пол выше MIN_RMS (это может быть «алло»).
    """
    if floor is None:
        return min(float(rms), float(MIN_RMS))
    if rms < floor:
        return float(rms)
    if is_speech:
        return floor * 1.0005
    return floor * 0.98 + rms * 0.02


class LineEchoGate:
    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled) and audioop is not None
        self.speech_rms = 0.0             # обычная громкость речи клиента (EMA), пока агент молчит
        self.floor: Optional[float] = None  # шум линии: вниз сразу, вверх медленно
        self.open_until = 0.0
        self.gated_seconds = 0.0          # сколько звука заменено тишиной
        self.passed_seconds = 0.0         # сколько речи клиента прошло поверх агента

    def process(self, pcm: bytes, agent_active: bool, dur: float, now: Optional[float] = None) -> bytes:
        """pcm — PCM16 mono. agent_active — агент говорит или договорил меньше ECHO_TAIL_SEC назад."""
        if not self.enabled or not pcm:
            return pcm
        try:
            rms = audioop.rms(pcm, 2)
        except Exception:
            return pcm
        now = now or time.time()
        floor = self.floor
        if not agent_active:
            is_speech = rms >= max(SPEECH_RMS, (floor or 0.0) * SPEECH_FLOOR_FACTOR)
            self.floor = update_floor(floor, rms, is_speech)
            if is_speech:
                self.speech_rms = rms if self.speech_rms == 0.0 else self.speech_rms * 0.9 + rms * 0.1
            return pcm
        threshold = max(MIN_RMS, self.speech_rms * SPEECH_SHARE, (floor or 0.0) * FLOOR_FACTOR)
        if rms >= threshold:
            self.open_until = now + HOLD_SEC
        if now < self.open_until:
            self.passed_seconds += dur
            return pcm
        self.gated_seconds += dur
        try:
            return audioop.mul(pcm, 2, GATE_GAIN)
        except Exception:
            return b"\x00" * len(pcm)

    def summary(self) -> str:
        """Строка для журнала звонка (пусто, если подавлять почти ничего не пришлось)."""
        if self.gated_seconds < 1.0:
            return ""
        return (f"шум и эхо линии во время речи агента приглушены для модели: {int(self.gated_seconds)} с "
                f"(речь клиента поверх агента прошла: {self.passed_seconds:.1f} с)")
