# backend/models/__init__.py
"""
Database models module for Voksy AI application.
This module contains SQLAlchemy ORM models that represent database tables.
✅ ОБНОВЛЕНО: Добавлена модель EmailVerification для верификации email
✅ ОБНОВЛЕНО: Добавлены модели Gemini Assistant и Gemini Conversation
✅ ОБНОВЛЕНО: Добавлена модель Contact для CRM функциональности
✅ ОБНОВЛЕНО: Добавлена модель ContactNote для ленты заметок
✅ ОБНОВЛЕНО: Добавлены модели Partner для партнерской программы
✅ ОБНОВЛЕНО v3.0: Добавлены модели Voximplant Partner Integration
✅ ОБНОВЛЕНО v3.1: Добавлены модели Grok Assistant для xAI Voice API
"""

from .base import Base, engine, create_tables_with_full_tracking
from .user import User
from .assistant import AssistantConfig
from .conversation import Conversation
from .file import File
from .integration import Integration
from .pinecone_config import PineconeConfig

# ✅ КРИТИЧЕСКИ ВАЖНО: Импортируем новые модели для отслеживания
from .subscription import SubscriptionPlan, SubscriptionLog, PaymentTransaction

# ✅ ДОБАВЛЕНО: Импортируем ElevenLabs модели
from .elevenlabs import ElevenLabsAgent, ElevenLabsConversation

from .function_log import FunctionLog

# ✅ НОВОЕ: Импортируем модель для верификации email
from .email_verification import EmailVerification
from .embed_config import EmbedConfig

# ✅ НОВОЕ: Импортируем Gemini модели
from .gemini_assistant import GeminiAssistantConfig, GeminiConversation

# ✅ НОВОЕ v3.1: Импортируем Grok модели для xAI Voice API
from .grok_assistant import GrokAssistantConfig, GrokConversation, GrokVoice

# ✅ НОВОЕ v4.0: Импортируем Cartesia модель для Cartesia TTS
from .cartesia_assistant import CartesiaAssistantConfig

# ✅ НОВОЕ v1.0: Импортируем Translate модели (OpenAI Realtime Translation API)
from .translate_assistant import TranslateAssistantConfig, TranslateConversation

# ✅ НОВОЕ: Импортируем Yandex модели (Yandex SpeechKit Realtime API)
from .yandex_assistant import YandexAssistantConfig, YandexConversation

# ✅ НОВОЕ: Импортируем Fish модель (Fish Audio TTS + OpenAI Realtime)
from .fish_assistant import FishAssistantConfig

# ✅ НОВОЕ: Импортируем Contact и ContactNote для CRM
from .contact import Contact, ContactNote

# ✅ НОВОЕ: Импортируем Partner модели для партнерской программы
from .partner import Partner, ReferralRelationship, PartnerCommission
from .task import Task, TaskStatus
from .browser_task import BrowserTask, BrowserTaskStatus

# ✅ НОВОЕ: Agent Mode модель
from .agent_config import AgentConfig
from .agent_contact import AgentContact
from .agent_call import AgentCall
from .agent_connector import AgentConnector

# ✅ НОВОЕ v2.2: Telegram-интеграция агента
from .agent_telegram_chat_history import AgentTelegramChatHistory

# ✅ НОВОЕ: Личный Telegram-аккаунт агента (MTProto, коннектор Telegram)
from .agent_telegram_account import (
    AgentTelegramAccount,
    AgentTelegramDialog,
    AgentTelegramMessage,
)

# ✅ НОВОЕ v3.0: Импортируем Voximplant Partner модели
from .voximplant_child import (
    VoximplantChildAccount,
    VoximplantPhoneNumber,
    VoximplantVerificationStatus
)

# ✅ НОВОЕ v3.6: SMS модель для входящих SMS через Voximplant
from .sms_message import SmsMessage

# ✅ НОВОЕ: Система кредитов оркестратора Voksy AI Agent
from .credit_transaction import CreditTransaction, CreditTransactionType
from .credit_package import CreditPackage

# Export specific models
__all__ = [
    "Base", 
    "engine", 
    "create_tables_with_full_tracking",
    "User", 
    "AssistantConfig", 
    "Conversation", 
    "File",
    "Integration",
    "PineconeConfig",
    # Модели для полного отслеживания подписок
    "SubscriptionPlan",
    "SubscriptionLog", 
    "PaymentTransaction",
    # ElevenLabs модели
    "ElevenLabsAgent",
    "ElevenLabsConversation",
    "FunctionLog",
    # ✅ НОВОЕ: Email верификация
    "EmailVerification",
    "EmbedConfig",
    # ✅ НОВОЕ: Gemini модели
    "GeminiAssistantConfig",
    "GeminiConversation",
    # ✅ НОВОЕ v3.1: Grok модели для xAI Voice API
    "GrokAssistantConfig",
    "GrokConversation",
    "GrokVoice",
    # ✅ НОВОЕ v4.0: Cartesia модель
    "CartesiaAssistantConfig",
    # ✅ НОВОЕ v1.0: Translate модели (OpenAI Realtime Translation)
    "TranslateAssistantConfig",
    "TranslateConversation",
    # ✅ НОВОЕ: Yandex модели (Yandex SpeechKit Realtime API)
    "YandexAssistantConfig",
    "YandexConversation",
    # ✅ НОВОЕ: Fish модель (Fish Audio TTS)
    "FishAssistantConfig",
    # ✅ НОВОЕ: CRM модели
    "Contact",
    "ContactNote",
    # ✅ НОВОЕ: Partner модели
    "Partner",
    "ReferralRelationship",
    "PartnerCommission",
    "Task",
    "TaskStatus",
    "BrowserTask",
    "BrowserTaskStatus",
    # ✅ НОВОЕ: Agent Mode
    "AgentConfig",
    "AgentContact",
    "AgentCall",
    "AgentConnector",
    # ✅ НОВОЕ v2.2: Telegram-интеграция агента
    "AgentTelegramChatHistory",
    # ✅ НОВОЕ: Личный Telegram-аккаунт агента
    "AgentTelegramAccount",
    "AgentTelegramDialog",
    "AgentTelegramMessage",
    # ✅ НОВОЕ v3.0: Voximplant Partner модели
    "VoximplantChildAccount",
    "VoximplantPhoneNumber",
    "VoximplantVerificationStatus",
    # ✅ НОВОЕ v3.6: SMS модель
    "SmsMessage",
    # ✅ НОВОЕ: Система кредитов оркестратора
    "CreditTransaction",
    "CreditTransactionType",
    "CreditPackage",
]
