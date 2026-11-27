# backend/websockets/barge_in.py
"""
Клиент перебивает агента по-настоящему — код останавливает агента сам (GPT-Live, телефон).

Звонок f9b4a269 (25.11.2026, «агент болтушка, клиент говорит — он продолжает своё»): за 4,5 минуты клиент говорил
поверх агента не меньше 44 секунд («азыр токто» — «стой сейчас», «үш порцияга» — «на три порции», «сеттерге акция
барбы» дважды), а агент ни разу не замолчал по-настоящему: «перебиваний: 0», четыре ответа длиннее 15 секунд.
У GPT-Live нет настройки чувствительности перебивания. Документация OpenAI (live-prompting, 11.2026): «Instructions
can encourage it to yield when interrupted, but they do not provide precise control over when it stops speaking» и
«The model may keep speaking briefly after an interruption»; приложению советуют «Temporarily mute or drop the
output, discard locally queued audio, and send the corrective instruction»; инструкция по ходу разговора «can
interrupt speech in progress».

Поэтому на телефоне перебивание держит код, а не только промпт:
- громкая речь клиента поверх ЗВУЧАЩЕГО агента дольше MIN_SPEECH_SEC (короткое «ага», «ооба», «м-м» — 0,2–0,5 с —
  не перебивание, агент договаривает фразу) → хендлер перестаёт пускать звук агента в линию, мост сбрасывает
  очередь, модель получает инструкцию замолчать и ответить на слова клиента;
- порог громкости — выше порога эхо-ворот (echo_gate.py) и не ниже доли STRONG_SHARE от обычной громкости этого
  клиента: эхо громкой связи заметно тише живой речи;
- самозащита: если после «перебивания» за VERIFY_SEC от клиента не пришло ни одного слова (это было эхо, телевизор,
  шум улицы), это ложное срабатывание; после MAX_FALSE таких механизм выключается до конца звонка.
"""

from typing import Optional

MIN_SPEECH_SEC = 0.65      # столько громкой речи клиента поверх агента = перебивание (поддакивание короче)
FIRST_SPEECH_SEC = 0.9     # пока клиент ещё не говорил отдельно (приветствие) — нужно дольше
GAP_SEC = 0.25             # паузы между слогами короче этого не сбрасывают счёт
MIN_RMS = 400              # ≈ −38 dBFS: тише — точно не речь в трубку
MIN_RMS_FIRST = 800        # ≈ −32 dBFS: пока голос клиента ещё не измерен (приветствие) — эхо громкой связи тише
STRONG_SHARE = 0.45        # доля обычной громкости речи клиента (эхо громкой связи заметно тише)
VERIFY_SEC = 3.0           # за это время после перебивания должны прийти слова клиента
MAX_FALSE = 2              # столько ложных срабатываний — механизм выключается до конца звонка


class BargeInDetector:
    """Считает громкую речь клиента поверх агента. feed() → True один раз на ответ агента."""

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)
        self.voiced = 0.0
        self._gap = 0.0
        self.armed = True            # одно перебивание на один ответ агента (снова — rearm() на новом ответе)
        self.count = 0               # сколько раз перебили
        self.false_count = 0         # ложных (слов клиента так и не пришло)
        self.disabled_reason = ""
        self._pending_verify_at: Optional[float] = None

    def threshold(self, gate_threshold: float, speech_rms: float) -> float:
        floor = MIN_RMS if speech_rms else MIN_RMS_FIRST
        return max(float(floor), float(gate_threshold or 0.0), STRONG_SHARE * float(speech_rms or 0.0))

    def feed(self, rms: float, dur: float, agent_audible: bool, gate_threshold: float, speech_rms: float) -> bool:
        if not self.enabled or not agent_audible or not self.armed:
            self.voiced, self._gap = 0.0, 0.0
            return False
        if rms >= self.threshold(gate_threshold, speech_rms):
            self.voiced += dur
            self._gap = 0.0
        else:
            self._gap += dur
            if self._gap > GAP_SEC:
                self.voiced = 0.0
        need = MIN_SPEECH_SEC if speech_rms else FIRST_SPEECH_SEC
        if self.voiced >= need:
            self.voiced, self._gap = 0.0, 0.0
            self.armed = False
            self.count += 1
            return True
        return False

    def rearm(self) -> None:
        self.armed = True
        self.voiced, self._gap = 0.0, 0.0

    # ── самопроверка: пришли ли слова клиента после перебивания ──────────────────────────────────
    def expect_words(self, now: float) -> None:
        self._pending_verify_at = now + VERIFY_SEC

    def words_heard(self) -> None:
        self._pending_verify_at = None

    def check_false(self, now: float) -> bool:
        """True — только что признали ложное срабатывание (слов не было VERIFY_SEC)."""
        if self._pending_verify_at is None or now < self._pending_verify_at:
            return False
        self._pending_verify_at = None
        self.false_count += 1
        if self.false_count >= MAX_FALSE and self.enabled:
            self.enabled = False
            self.disabled_reason = "дважды сработало без слов клиента (эхо или шум линии)"
        return True
