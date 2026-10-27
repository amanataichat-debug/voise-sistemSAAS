"""
Agent Config model for Voksy AI Agent — autonomous calling AI agent.
Stores orchestrator config, onboarding documents, and chat history per user.

✅ v3.0: multi-provider voice assistant (gemini / openai / cartesia / yandex /
         cascade / fish), hardcoded orchestrator prompts (uses_hardcoded_prompt),
         OpenRouter model.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, Integer, DateTime, ForeignKey, JSON, Float
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from backend.models.base import Base


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), default="Мой агент", nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)

    # ── Тип голосового ассистента — выбирается при создании, можно менять ──
    # gemini | openai | cartesia | yandex | cascade | fish | eleven (новые агенты — только eleven)
    assistant_type = Column(String(20), nullable=True)

    # ── FK на голосового ассистента (заполняется ровно один из шести) ──
    gemini_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gemini_assistant_configs.id", ondelete="SET NULL"),
        nullable=True
    )
    openai_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_configs.id", ondelete="SET NULL"),
        nullable=True
    )
    cartesia_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cartesia_assistant_configs.id", ondelete="SET NULL"),
        nullable=True
    )
    yandex_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("yandex_assistant_configs.id", ondelete="SET NULL"),
        nullable=True
    )
    # Каскад живёт в grok_assistant_configs (assistant_type='cascade').
    cascade_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("grok_assistant_configs.id", ondelete="SET NULL"),
        nullable=True
    )
    fish_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fish_assistant_configs.id", ondelete="SET NULL"),
        nullable=True
    )
    # ElevenLabs: OpenAI Realtime текстом + синтез ElevenLabs (handler_eleven.py), серверные ключи.
    eleven_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("eleven_assistant_configs.id", ondelete="SET NULL"),
        nullable=True
    )

    # ── Голосовые сообщения в мессенджерах (ветка 1210-chat-v2, 3.4) ──
    # Озвучка ElevenLabs (eleven_v3) на серверном ключе. NULL — как в
    # Eleven-карточке звонков (voice_id / language / stability). Колонки —
    # app.ensure_chat_v2_columns(); логика — services/agent_voice_service.py.
    voice_msg_voice_id = Column(String(255), nullable=True)
    voice_msg_voice_name = Column(String(255), nullable=True)
    voice_msg_language = Column(String(10), nullable=True)
    voice_msg_stability = Column(Float, nullable=True)
    # Движок озвучки: eleven (ElevenLabs) | openai (OpenAI Speech, голоса GPT-Live).
    voice_msg_engine = Column(String(16), nullable=True)
    voice_msg_openai_voice = Column(String(32), nullable=True)
    # Режим «агент решает сам»: доля голосовых среди ответов, % (30 / 40 / 50).
    voice_msg_auto_share = Column(Integer, nullable=True)

    # ── Оркестратор (планировщик) ──
    # Формат OpenRouter (provider/model-name) для новых агентов v3.0+
    orchestrator_model = Column(String(100), default="deepseek/deepseek-v4-pro", nullable=False)
    orchestrator_prompt = Column(Text, nullable=True)  # для старых агентов (gpt-4o-mini)

    # Агенты (выполнение шагов) — legacy поля, не используются (оставлены до миграции)
    agent_model = Column(String(100), default="gpt-4o-mini", nullable=False)
    agent_functions = Column(JSON, default=list, nullable=False)

    # Лимиты — legacy поля
    max_steps = Column(Integer, default=10, nullable=False)
    step_timeout_sec = Column(Integer, default=60, nullable=False)

    # Онбординг документы (5 шагов)
    doc_who_am_i = Column(Text, nullable=True)
    doc_who_we_call = Column(Text, nullable=True)
    doc_how_we_talk = Column(Text, nullable=True)
    doc_what_we_offer = Column(Text, nullable=True)
    doc_rules_and_goals = Column(Text, nullable=True)

    # Шаг 6 wizard — произвольный текст (инструкции для ОРКЕСТРАТОРА)
    additional_instructions = Column(Text, nullable=True)

    # Инструкции именно для ГОЛОСОВОГО агента (поведение в живом звонке).
    # Дописываются к system_prompt связанного голосового ассистента.
    voice_additional_instructions = Column(Text, nullable=True)

    # Первая фраза голосового агента при ВХОДЯЩЕМ звонке.
    # Поддерживает переменную {name} — сервер подставит имя из карточки
    # контакта (если звонящий найден в базе агента), иначе вырежет плейсхолдер.
    # Пустое значение = используется дефолтная первая фраза голосового ассистента.
    inbound_first_phrase = Column(Text, nullable=True)

    # ── База знаний (векторная БД Pinecone) ──
    # Namespace создаётся через PineconeService; пустой namespace = базы нет.
    # Обслуживает и оркестратор (tool search_knowledge_base), и голосового
    # ассистента (функция search_pinecone) во всех трёх провайдерах.
    kb_namespace = Column(String(64), nullable=True)
    kb_char_count = Column(Integer, default=0, nullable=False)
    kb_content = Column(Text, nullable=True)        # полный текст для просмотра/редактирования
    kb_name = Column(String(100), nullable=True)    # опциональное название базы
    kb_updated_at = Column(DateTime, nullable=True)

    # Флаг — TRUE для агентов созданных после v3.0
    uses_hardcoded_prompt = Column(Boolean, default=False, nullable=False)

    # Рабочие часы (Бишкек, UTC+6). Это же — окно, в которое агент ЗВОНИТ
    # (_check_call_guards, планировщик): звонок с start:00 до end:00.
    working_hours_start = Column(Integer, default=9, nullable=False)
    working_hours_end = Column(Integer, default=20, nullable=False)  # не включительно, до 24
    # Ограничивать ли время звонков этим окном. False — агент звонит в любое
    # время суток (тесты владельца, круглосуточный бизнес). Настройка «Звонки»
    # в карточке агента. Колонку добавляет app.ensure_branding_columns().
    call_window_enabled = Column(Boolean, default=True, nullable=False, server_default="true")

    # Номер для исходящих звонков (caller_id)
    default_caller_id = Column(String(50), nullable=True)

    # Чат с агентом
    chat_history = Column(JSONB, default=list, nullable=False)

    # ── Telegram-интеграция агента (v2.2) ──
    telegram_bot_token = Column(String(100), nullable=True)
    telegram_bot_username = Column(String(50), nullable=True)
    telegram_chat_ids = Column(JSONB, default=list, nullable=False)
    telegram_webhook_secret = Column(String(64), nullable=True, unique=True)
    telegram_enabled = Column(Boolean, default=False, nullable=False)

    # Оповещения владельцу в Telegram-бот агента (services/owner_notifications.py).
    # Колонки добавляет app.ensure_chat_v2_columns().
    notify_new_lead = Column(Boolean, default=True, nullable=False)
    notify_call_summary = Column(Boolean, default=True, nullable=False)
    notify_daily_digest = Column(Boolean, default=True, nullable=False)
    # {"daily_digest": "2026-10-27", ...} — дедуп ежедневных заданий между воркерами
    jobs_ran_on = Column(JSONB, nullable=True)

    # ── Публичный HTTP-канал (приём заявок «сервер-к-серверу») ──
    # Внешний бэкенд (например форма сайта) шлёт запрос с секретным ключом,
    # запрос попадает в ChatOrchestrator (stateless), агент сам решает что делать.
    public_api_key = Column(String(64), nullable=True, unique=True, index=True)
    public_enabled = Column(Boolean, default=False, nullable=False)

    # ── Вебхук оркестратора (отправка событий во внешнюю систему) ──
    # URL, на который оркестратор (чат / PostCall) шлёт событие через tool
    # send_webhook. Пустой URL = вебхук не настроен (tool вернёт ошибку, блок
    # промпта не показывается). Резолвится сервером — модель URL не передаёт.
    webhook_url = Column(String(500), nullable=True)

    # ── Фирменный стиль документов агента (ветка 1310-kp-design) ──
    # Чем заполнено — тем оформляются PDF (КП, счёт, памятка): плашка шапки
    # красится в brand_color, в ней логотип и название, в колонтитуле —
    # контакты. Пусто → нейтральное оформление в цвете Voksy AI без логотипа,
    # поэтому у клиентов платформы документы всё равно выглядят прилично.
    brand_company_name = Column(String(255), nullable=True)
    brand_color = Column(String(16), nullable=True)          # #RRGGBB
    brand_contacts = Column(String(500), nullable=True)      # «сайт · телефон»
    brand_logo_file_id = Column(UUID(as_uuid=True), nullable=True)  # agent_files.id

    # ── CRM v2 (2510-crm-v2): настраиваемые стадии воронки ─────────────
    # JSON-строка со списком [{key,label,color,kind}], kind: open|won|lost.
    # NULL → дефолт из backend/core/pipeline_stages.py (5 стадий).
    # Колонка добавляется app.ensure_crm_v2_columns() в начале startup.
    pipeline_stages = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # ── Relationships ──
    gemini_assistant = relationship(
        "GeminiAssistantConfig", foreign_keys=[gemini_assistant_id]
    )
    openai_assistant = relationship(
        "AssistantConfig", foreign_keys=[openai_assistant_id]
    )
    cartesia_assistant = relationship(
        "CartesiaAssistantConfig", foreign_keys=[cartesia_assistant_id]
    )
    yandex_assistant = relationship(
        "YandexAssistantConfig", foreign_keys=[yandex_assistant_id]
    )
    cascade_assistant = relationship(
        "GrokAssistantConfig", foreign_keys=[cascade_assistant_id]
    )
    fish_assistant = relationship(
        "FishAssistantConfig", foreign_keys=[fish_assistant_id]
    )
    eleven_assistant = relationship(
        "ElevenAssistantConfig", foreign_keys=[eleven_assistant_id]
    )

    def get_voice_assistant(self):
        """Универсальный геттер — вернёт активного голосового ассистента."""
        if self.assistant_type == "gemini":
            return self.gemini_assistant
        if self.assistant_type == "openai":
            return self.openai_assistant
        if self.assistant_type == "cartesia":
            return self.cartesia_assistant
        if self.assistant_type == "yandex":
            return self.yandex_assistant
        if self.assistant_type == "cascade":
            return self.cascade_assistant
        if self.assistant_type == "fish":
            return self.fish_assistant
        if self.assistant_type == "eleven":
            return self.eleven_assistant
        return None

    def get_voice_assistant_id(self):
        if self.assistant_type == "gemini":
            return self.gemini_assistant_id
        if self.assistant_type == "openai":
            return self.openai_assistant_id
        if self.assistant_type == "cartesia":
            return self.cartesia_assistant_id
        if self.assistant_type == "yandex":
            return self.yandex_assistant_id
        if self.assistant_type == "cascade":
            return self.cascade_assistant_id
        if self.assistant_type == "fish":
            return self.fish_assistant_id
        if self.assistant_type == "eleven":
            return self.eleven_assistant_id
        return None

    def has_knowledge_base(self) -> bool:
        """True, если у агента создана векторная база (есть namespace)."""
        return bool(self.kb_namespace)

    # ── Telegram-интеграция агента (v2.2) ──
    def has_telegram_bot(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_webhook_secret)

    def get_telegram_chat_ids_list(self) -> list:
        """Возвращает голый список chat_id из telegram_chat_ids."""
        if not self.telegram_chat_ids:
            return []
        return [c.get("chat_id") for c in self.telegram_chat_ids if c.get("chat_id")]

    # ── Публичный HTTP-канал ──
    def has_public_access(self) -> bool:
        """True, если публичный канал включён и ключ сгенерирован."""
        return bool(self.public_enabled and self.public_api_key)

    # ── Вебхук оркестратора ──
    def has_webhook(self) -> bool:
        """True, если у агента задан URL вебхука."""
        return bool(self.webhook_url)
