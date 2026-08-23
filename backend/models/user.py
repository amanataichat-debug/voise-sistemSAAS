# backend/models/user.py
"""
Модель пользователя для приложения WellcomeAI.
Представляет пользователя с данными аутентификации и профиля.
✅ ОБНОВЛЕНО: Добавлено поле email_verified для верификации email
✅ ОБНОВЛЕНО: Добавлено поле gemini_api_key для Google Gemini API
✅ ОБНОВЛЕНО v2.8: Добавлены поля Voximplant для автоматических звонков
✅ ОБНОВЛЕНО v2.9: Добавлено поле grok_api_key для xAI Grok Voice API
✅ ОБНОВЛЕНО v3.9: Добавлены поля Telegram для уведомлений о звонках
"""

import uuid
from sqlalchemy import Column, String, Boolean, JSON, DateTime, Integer, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .pinecone_config import PineconeConfig
from .base import Base, BaseModel


class User(Base, BaseModel):
    """
    Модель пользователя, представляющая пользователей приложения.
    """
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    openai_api_key = Column(String, nullable=True)
    elevenlabs_api_key = Column(String, nullable=True)
    gemini_api_key = Column(String, nullable=True)  # Google Gemini API key
    grok_api_key = Column(String, nullable=True)    # ✅ v2.9: xAI Grok API key
    cartesia_api_key = Column(String, nullable=True)  # ✅ v4.0: Cartesia TTS API key
    fish_api_key = Column(String, nullable=True)      # ✅ Fish Audio TTS API key
    openrouter_api_key = Column(String(255), nullable=True)  # ✅ Cascade: OpenRouter API key
    yandex_api_key = Column(String, nullable=True)    # ✅ Yandex Cloud API key (SpeechKit Realtime)
    yandex_folder_id = Column(String(100), nullable=True)  # ✅ Yandex Cloud folder ID

    # ✅ v2.8: Voximplant настройки для автоматических звонков
    voximplant_account_id = Column(String(100), nullable=True)
    voximplant_api_key = Column(String(500), nullable=True)
    voximplant_rule_id = Column(String(100), nullable=True)
    voximplant_caller_id = Column(String(50), nullable=True)
    
    # ✅ НОВОЕ v3.9: Telegram настройки для уведомлений о звонках
    telegram_bot_token = Column(String(100), nullable=True)
    telegram_chat_id = Column(String(50), nullable=True)  # Может быть отрицательным для групп/каналов

    # ✅ НОВОЕ v4.0: Webhook настройки для уведомлений о завершённых диалогах
    webhook_url = Column(String(500), nullable=True)
    webhook_enabled = Column(Boolean, default=False, nullable=False, index=True)

    subscription_plan = Column(String, default="free")
    usage_tokens = Column(Integer, default=0)
    last_login = Column(DateTime(timezone=True), nullable=True)
    google_sheets_token = Column(JSON, nullable=True)
    google_sheets_authorized = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    email_verified = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Новые поля для системы тарификации
    subscription_plan_id = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id"), nullable=True)
    subscription_start_date = Column(DateTime(timezone=True), nullable=True)
    subscription_end_date = Column(DateTime(timezone=True), nullable=True)
    is_trial = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    payment_status = Column(String(50), nullable=True)

    # ✅ Персональный API-ключ Voksy AI (внешние интеграции: Claude Code и т.п.).
    # Сам ключ (vfy_...) НЕ хранится — только SHA-256-хэш; префикс показываем в UI.
    api_key_hash = Column(String(64), nullable=True, unique=True, index=True)
    api_key_prefix = Column(String(20), nullable=True)
    api_key_created_at = Column(DateTime(timezone=True), nullable=True)

    # ✅ Система кредитов оркестратора Voksy AI Agent (тариф `agent`)
    # Поля добавлены в БД вручную через SQL (см. ТЗ раздел 2.1).
    credits_balance = Column(Integer, default=0, nullable=False)
    agent_trial_used = Column(Boolean, default=False, nullable=False)
    agent_trial_started_at = Column(DateTime(timezone=True), nullable=True)
    agent_subscription_blocked = Column(Boolean, default=False, nullable=False, index=True)

    # ✅ Кредиты каскад-ассистентов (LLM gpt-realtime-2.1-mini на серверном ключе).
    # Отдельный кошелёк, независимый от кредитов оркестратора (credits_balance)
    # и от подписки agent. Доступен на всех тарифах (включая free).
    # Колонки добавляются в БД startup-ALTER (см. app.py fix_missing_columns).
    cascade_credits_balance = Column(Integer, default=0, nullable=False)
    cascade_trial_granted = Column(Boolean, default=False, nullable=False)

    # Отношения
    assistants = relationship("AssistantConfig", back_populates="user", cascade="all, delete-orphan")
    gemini_assistants = relationship("GeminiAssistantConfig", back_populates="user", cascade="all, delete-orphan")
    grok_assistants = relationship("GrokAssistantConfig", back_populates="user", cascade="all, delete-orphan")  # ✅ v2.9
    cartesia_assistants = relationship("CartesiaAssistantConfig", back_populates="user", cascade="all, delete-orphan")  # ✅ v4.0
    fish_assistants = relationship("FishAssistantConfig", back_populates="user", cascade="all, delete-orphan")  # ✅ Fish Audio TTS
    yandex_assistants = relationship("YandexAssistantConfig", back_populates="user", cascade="all, delete-orphan")  # ✅ Yandex SpeechKit Realtime
    translate_assistants = relationship("TranslateAssistantConfig", back_populates="user", cascade="all, delete-orphan")  # ✅ v1.0
    files = relationship("File", back_populates="user", cascade="all, delete-orphan")
    subscription_plan_rel = relationship("SubscriptionPlan", foreign_keys=[subscription_plan_id])
    elevenlabs_agents = relationship("ElevenLabsAgent", back_populates="user", cascade="all, delete-orphan")
   
    def __repr__(self):
        """Строковое представление пользователя"""
        return f"<User {self.email}>"
    
    @property
    def full_name(self):
        """Получить полное имя пользователя"""
        parts = []
        if self.first_name:
            parts.append(self.first_name)
        if self.last_name:
            parts.append(self.last_name)
        return " ".join(parts) if parts else None
    
    def to_dict(self):
        """Преобразовать в словарь, исключая конфиденциальные поля"""
        data = super().to_dict()
        # Удаляем конфиденциальные данные
        data.pop("password_hash", None)
        data.pop("openai_api_key", None)
        data.pop("elevenlabs_api_key", None)
        data.pop("gemini_api_key", None)
        data.pop("grok_api_key", None)           # ✅ v2.9: Скрываем Grok API key
        data.pop("cartesia_api_key", None)       # ✅ v4.0: Скрываем Cartesia API key
        data.pop("fish_api_key", None)           # ✅ Fish: Скрываем Fish Audio API key
        data.pop("openrouter_api_key", None)     # ✅ Cascade: Скрываем OpenRouter API key
        data.pop("yandex_api_key", None)         # ✅ Yandex: Скрываем Yandex Cloud API key
        data.pop("voximplant_api_key", None)
        data.pop("telegram_bot_token", None)     # ✅ v3.9: Скрываем Telegram токен
        data.pop("webhook_url", None)            # ✅ v4.0: Скрываем webhook URL из публичных ответов
        data.pop("google_sheets_token", None)
        data.pop("api_key_hash", None)           # ✅ Персональный API-ключ: скрываем хэш

        # Преобразуем UUID в строку для сериализации JSON
        if isinstance(data.get("id"), uuid.UUID):
            data["id"] = str(data["id"])
        if isinstance(data.get("subscription_plan_id"), uuid.UUID):
            data["subscription_plan_id"] = str(data["subscription_plan_id"])
            
        return data
    
    def has_api_key(self):
        """Проверить, настроен ли ключ OpenAI API у пользователя"""
        return bool(self.openai_api_key)
    
    def has_elevenlabs_api_key(self):
        """Проверить, настроен ли ключ ElevenLabs API у пользователя"""
        return bool(self.elevenlabs_api_key)
    
    def has_gemini_api_key(self):
        """Проверить, настроен ли ключ Gemini API у пользователя"""
        return bool(self.gemini_api_key)
    
    def has_grok_api_key(self):
        """✅ v2.9: Проверить, настроен ли ключ xAI Grok API у пользователя"""
        return bool(self.grok_api_key)

    def has_cartesia_api_key(self):
        """✅ v4.0: Проверить, настроен ли ключ Cartesia API у пользователя"""
        return bool(self.cartesia_api_key)
    
    def has_yandex_api_key(self):
        """✅ Yandex: Проверить, настроен ли ключ Yandex Cloud API у пользователя"""
        return bool(self.yandex_api_key)

    def has_fish_api_key(self):
        """✅ Fish: Проверить, настроен ли ключ Fish Audio API у пользователя"""
        return bool(self.fish_api_key)

    def has_voximplant_config(self):
        """✅ v2.8: Проверить, настроены ли данные Voximplant"""
        return bool(
            self.voximplant_account_id and 
            self.voximplant_api_key and 
            self.voximplant_rule_id and 
            self.voximplant_caller_id
        )
    
    def get_voximplant_config(self):
        """✅ v2.8: Получить настройки Voximplant"""
        if not self.has_voximplant_config():
            return None
        
        return {
            "account_id": self.voximplant_account_id,
            "api_key": self.voximplant_api_key,
            "rule_id": self.voximplant_rule_id,
            "caller_id": self.voximplant_caller_id
        }
    
    def has_telegram_config(self):
        """✅ НОВОЕ v3.9: Проверить, настроены ли данные Telegram"""
        return bool(self.telegram_bot_token and self.telegram_chat_id)
    
    def get_telegram_config(self):
        """✅ НОВОЕ v3.9: Получить настройки Telegram"""
        if not self.has_telegram_config():
            return None
        
        return {
            "bot_token": self.telegram_bot_token,
            "chat_id": self.telegram_chat_id
        }

    def has_webhook_config(self):
        """✅ НОВОЕ v4.0: Проверить, настроен ли webhook URL"""
        return bool(self.webhook_url and self.webhook_enabled)

    def get_webhook_config(self):
        """✅ НОВОЕ v4.0: Получить настройки webhook"""
        if not self.has_webhook_config():
            return None
        return {
            "url": self.webhook_url,
            "enabled": self.webhook_enabled
        }

    def has_active_subscription(self):
        """Проверить, активна ли подписка пользователя"""
        if self.is_admin:
            return True
            
        from datetime import datetime
        if self.subscription_end_date and self.subscription_end_date > datetime.now():
            return True
            
        return False
    
    # ========================================================================
    # ✅ Хелперы подписки `agent` (система кредитов оркестратора)
    # ========================================================================

    # Доступ к агенту-оркестратору (модель v3.1).
    # Источники доступа:
    #   • 3-дневный тестовый период — для любого тарифа;
    #   • тариф `profi`;
    #   • legacy-тариф `agent` (обратная совместимость);
    #   • админ.
    # Тарифы `ai_voice` и `start` после окончания триала доступа НЕ дают.
    AGENT_TRIAL_DAYS = 3

    def is_agent_plan(self) -> bool:
        """True если текущий тариф пользователя — legacy-тариф `agent`."""
        try:
            plan = self.subscription_plan_rel
            return bool(plan and plan.code == "agent")
        except Exception:
            return False

    def is_profi_plan(self) -> bool:
        """True если активный (не истёкший) тариф — `profi`."""
        try:
            plan = self.subscription_plan_rel
            if not (plan and plan.code == "profi"):
                return False
            if not self.subscription_end_date:
                return False
            from datetime import datetime, timezone
            end = self.subscription_end_date
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            return end > datetime.now(timezone.utc)
        except Exception:
            return False

    def agent_trial_active(self) -> bool:
        """True пока идёт 3-дневный тестовый период агента."""
        if not self.agent_trial_started_at:
            return False
        from datetime import datetime, timezone, timedelta
        started = self.agent_trial_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < started + timedelta(days=self.AGENT_TRIAL_DAYS)

    def _legacy_agent_plan_active(self) -> bool:
        """Legacy: активная (не истёкшая) подписка на старый тариф `agent`."""
        if not self.is_agent_plan() or not self.subscription_end_date:
            return False
        from datetime import datetime, timezone
        end = self.subscription_end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end > datetime.now(timezone.utc)

    def has_agent_access(self) -> bool:
        """
        Доступ к агенту-оркестратору: тестовый период ИЛИ profi ИЛИ
        legacy-тариф agent ИЛИ админ. Ручная блокировка перекрывает всё.
        """
        if self.agent_subscription_blocked:
            return False
        if self.is_admin:
            return True
        if self.agent_trial_active():
            return True
        if self.is_profi_plan():
            return True
        return self._legacy_agent_plan_active()

    def has_active_agent_subscription(self) -> bool:
        """Алиас has_agent_access (имя сохранено для обратной совместимости)."""
        return self.has_agent_access()

    def agent_days_remaining(self) -> int:
        """Сколько дней осталось доступа к агенту (триал или profi/agent)."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        candidates = []
        if self.agent_trial_active() and self.agent_trial_started_at:
            started = self.agent_trial_started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            candidates.append(started + timedelta(days=self.AGENT_TRIAL_DAYS))
        if (self.is_profi_plan() or self._legacy_agent_plan_active()) and self.subscription_end_date:
            end = self.subscription_end_date
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            candidates.append(end)
        if not candidates:
            return 0
        return max(0, (max(candidates) - now).days)

    def agent_subscription_status(self) -> str:
        """active | trial | expired | none"""
        if self.has_agent_access():
            # «trial» — только если доступ держится исключительно на триале
            if self.agent_trial_active() and not (
                self.is_admin or self.is_profi_plan() or self._legacy_agent_plan_active()
            ):
                return "trial"
            return "active"
        if self.agent_trial_used:
            return "expired"
        return "none"

    def days_until_subscription_end(self) -> int:
        if not self.subscription_end_date:
            return 0
        from datetime import datetime, timezone
        end = self.subscription_end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        delta = end - datetime.now(timezone.utc)
        return max(0, delta.days)

    # ========================================================================
    # ✅ Хелперы кредитов каскад-ассистентов (независимы от подписки agent)
    # ========================================================================

    def has_cascade_credits(self) -> bool:
        """True если на балансе каскада есть кредиты (>0)."""
        return (self.cascade_credits_balance or 0) > 0

    def is_email_verified(self):
        """Проверить, подтверждён ли email пользователя"""
        return self.email_verified
    
    def needs_email_verification(self):
        """Проверить, требуется ли верификация email"""
        return not self.email_verified
    
    def get_available_providers(self):
        """✅ v2.9: Получить список доступных AI провайдеров"""
        providers = []
        
        if self.openai_api_key:
            providers.append("openai")
        if self.gemini_api_key:
            providers.append("gemini")
        if self.grok_api_key:
            providers.append("grok")
        if self.elevenlabs_api_key:
            providers.append("elevenlabs")
        if self.cartesia_api_key:
            providers.append("cartesia")
        if self.openrouter_api_key:
            providers.append("openrouter")
        if self.yandex_api_key:
            providers.append("yandex")
        if self.fish_api_key:
            providers.append("fish")

        return providers
