"""
Service module for WellcomeAI application.
Contains business logic services that act as intermediaries between API endpoints and data models.
✅ ОБНОВЛЕНО v3.0: Добавлен VoximplantPartnerService для партнёрской интеграции
✅ ОБНОВЛЕНО v3.3: Добавлен R2StorageService для записей звонков
✅ ОБНОВЛЕНО v3.9: Добавлен TelegramNotificationService для уведомлений о звонках
"""

from .auth_service import AuthService
from .user_service import UserService
from .assistant_service import AssistantService
from .file_service import FileService
from .conversation_service import ConversationService
from .subscription_service import SubscriptionService
from .notification_service import NotificationService
from .email_service import EmailService
from .browser_agent_service import BrowserAgentService, browser_agent_service

# ✅ НОВОЕ v3.0: Voximplant Partner Service
from .voximplant_partner import VoximplantPartnerService, get_voximplant_partner_service

# ✅ НОВОЕ v3.3: R2 Storage Service
from .r2_storage import R2StorageService

# ✅ НОВОЕ v3.9: Telegram Notification Service
from .telegram_notification import TelegramNotificationService, send_call_notification_safe

# ✅ НОВОЕ v4.0: Webhook Notification Service
from .webhook_notification import WebhookNotificationService, send_webhook_safe

# Export services
__all__ = [
    "AuthService",
    "UserService",
    "AssistantService",
    "FileService",
    "ConversationService",
    "SubscriptionService",
    "NotificationService",
    "EmailService",
    "BrowserAgentService",
    "browser_agent_service",
    # ✅ НОВОЕ v3.0: Voximplant Partner
    "VoximplantPartnerService",
    "get_voximplant_partner_service",
    # ✅ НОВОЕ v3.3: R2 Storage
    "R2StorageService",
    # ✅ НОВОЕ v3.9: Telegram Notifications
    "TelegramNotificationService",
    "send_call_notification_safe",
    # ✅ НОВОЕ v4.0: Webhook Notifications
    "WebhookNotificationService",
    "send_webhook_safe",
]
