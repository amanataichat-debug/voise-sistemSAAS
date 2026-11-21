"""
Свои голоса GPT-Live (prompt-to-voice), ветка 1811-niche-admin-speed, 21.11.2026.

OpenAI (docs «Custom voices»): POST /v1/audio/voices {type: "prompt", name, prompt, script_hint, model: "auto"}
сохраняет голос по описанию и возвращает id `voice_…`; в сессии GPT-Live он задаётся объектом
`audio.output.voice = {"id": "voice_…"}`, а исходное описание голоса OpenAI советует класть в instructions.
Создание бесплатное, до 100 голосов на организацию; работают только с GPT-Live.

Голос платформы: `key` — короткое имя латиницей (например `aigul`), его хранит колонка `live_voice` карточки
и агента, как имя встроенного голоса. Создаёт голос скрипт `scripts/create_live_voice.py` (Render Shell),
реестр в памяти — `backend/services/live_voices.py`. Таблицу создаёт app.ensure_live_voice_table().
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from .base import Base


class LiveCustomVoice(Base):
    __tablename__ = "live_custom_voices"

    id = Column(Integer, primary_key=True)
    key = Column(String(40), unique=True, nullable=False, index=True)  # имя в карточке: латиница, нижний регистр
    voice_id = Column(String(120), nullable=False)                     # id OpenAI: voice_…
    title = Column(String(120), nullable=False)                        # подпись для владельца
    gender = Column(String(1), nullable=True)                          # m | f — грамматический род в промпте
    description = Column(Text, nullable=False)                         # описание голоса (prompt) — уходит в instructions
    script_hint = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
