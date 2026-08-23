# backend/core/config.py

"""
Configuration settings for the WellcomeAI application.
ОБНОВЛЕНО: Добавлены настройки Email для верификации
✅ ОБНОВЛЕНО v3.0: Добавлены настройки Voximplant Partner Integration
✅ ОБНОВЛЕНО v3.3: Добавлены настройки Cloudflare R2 Storage
"""

import os
from pydantic_settings import BaseSettings
from pydantic import SecretStr, validator
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Settings(BaseSettings):
    """Application settings class using Pydantic for validation"""
    
    # Application info
    APP_NAME: str = "WellcomeAI"
    VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "False") == "True"
    PRODUCTION: bool = os.getenv("PRODUCTION", "False") == "True"
    
    # Server settings
    PORT: int = int(os.getenv("PORT", "5050"))
    
    # ✅ ИСПРАВЛЕНО: HOST_URL должен быть публично доступным
    HOST_URL: Optional[str] = os.getenv("HOST_URL")

    # ✅ v2.2: Публичный базовый URL (для Telegram webhook и т.п.).
    # По умолчанию совпадает с HOST_URL. Вид: https://voksyai.online
    PUBLIC_BASE_URL: Optional[str] = os.getenv("PUBLIC_BASE_URL") or os.getenv("HOST_URL")

    # ✅ Базовый URL для регистрации Telegram-webhook напрямую на Render,
    # минуя Selectel-прокси (Telegram-серверам прокси не нужен и мешает).
    # Вид: https://new-struktura-voiceai.onrender.com
    TELEGRAM_WEBHOOK_BASE_URL: Optional[str] = os.getenv("TELEGRAM_WEBHOOK_BASE_URL")
    
    # Database settings
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    
    # Authentication and security
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-this-in-production")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day
    
    # OpenAI settings
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")

    # ✅ v3.0: OpenRouter — системный ключ для оркестратора Voksy AI Agent
    OPENROUTER_API_KEY: Optional[str] = os.getenv("OPENROUTER_API_KEY")

    # STT (speech-to-text) — распознавание голосовых в веб-чате и Telegram.
    # Основной провайдер — OpenAI, fallback — OpenRouter (если задан ключ).
    STT_OPENAI_MODEL: str = os.getenv("STT_OPENAI_MODEL", "gpt-4o-mini-transcribe")
    STT_OPENROUTER_MODEL: str = os.getenv("STT_OPENROUTER_MODEL", "openai/gpt-4o-mini-transcribe")
    STT_LANGUAGE: str = os.getenv("STT_LANGUAGE", "ru")
    REALTIME_WS_URL: str = os.getenv(
        "REALTIME_WS_URL", 
        "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
    )
    
    # WebSocket settings
    WS_PING_INTERVAL: int = 20  # seconds
    WS_PING_TIMEOUT: int = 60   # seconds
    WS_CLOSE_TIMEOUT: int = 30  # seconds
    WS_MAX_MSG_SIZE: int = 15 * 1024 * 1024  # 15MB
    MAX_RECONNECT_ATTEMPTS: int = 5
    
    # Audio settings
    DEFAULT_VOICE: str = "alloy"
    AVAILABLE_VOICES: list = ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "onyx", "nova", "fable", "marin", "cedar"]
    
    # Path settings
    STATIC_DIR: str = os.path.join(os.getcwd(), "static")
    TEMPLATE_DIR: str = os.path.join(os.getcwd(), "templates")
    
    # CORS Settings
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")
    
    # =========================================================================
    # ✅ Finik (finik.kg) — QR-эквайринг, валюта KGS
    # =========================================================================
    FINIK_API_KEY: str = os.getenv("FINIK_API_KEY", "")
    # Прод: https://api.acquiring.averspay.kg, бета: https://beta.api.acquiring.averspay.kg
    FINIK_API_URL: str = os.getenv("FINIK_API_URL", "https://api.acquiring.averspay.kg")
    # Приватный RSA-ключ (содержимое .pem целиком, многострочное)
    FINIK_PRIVATE_PEM: str = os.getenv("FINIK_PRIVATE_PEM", "")
    # ID счёта Finik, куда зачисляются средства
    FINIK_ACCOUNT_ID: str = os.getenv("FINIK_ACCOUNT_ID", "")
    # Публичный ключ Finik для проверки подписи входящих webhook'ов (PEM).
    # Пока не выдан — проверка подписи пропускается с предупреждением в логах.
    FINIK_PUBLIC_KEY: str = os.getenv("FINIK_PUBLIC_KEY", "")
    FINIK_VERIFY_WEBHOOK_SIGNATURE: bool = os.getenv("FINIK_VERIFY_WEBHOOK_SIGNATURE", "True") == "True"

    # Payment settings
    SUBSCRIPTION_DURATION_DAYS: int = 30  # Длительность подписки в днях
    
    # ✅ НОВОЕ: Email settings для верификации
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "voicyfy@mail.ru")
    EMAIL_HOST: str = os.getenv("EMAIL_HOST", "smtp.mail.ru")
    EMAIL_PORT: int = int(os.getenv("EMAIL_PORT", "465"))
    EMAIL_USERNAME: str = os.getenv("EMAIL_USERNAME", "voicyfy@mail.ru")
    EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")
    EMAIL_USE_SSL: bool = os.getenv("EMAIL_USE_SSL", "True") == "True"
    EMAIL_USE_TLS: bool = os.getenv("EMAIL_USE_TLS", "False") == "True"
    
    # ✅ НОВОЕ: Email verification settings
    VERIFICATION_CODE_LENGTH: int = 6
    VERIFICATION_CODE_EXPIRY_MINUTES: int = 10
    VERIFICATION_MAX_ATTEMPTS: int = 3
    VERIFICATION_RESEND_COOLDOWN_SECONDS: int = 60
    
    # =========================================================================
    # ✅ НОВОЕ v3.0: Voximplant Partner Integration
    # =========================================================================
    
    # Credentials родительского аккаунта (для создания дочерних)
    VOXIMPLANT_PARENT_ACCOUNT_ID: str = os.getenv("VOXIMPLANT_PARENT_ACCOUNT_ID", "")
    VOXIMPLANT_PARENT_API_KEY: str = os.getenv("VOXIMPLANT_PARENT_API_KEY", "")
    
    # ID эталонного аккаунта для клонирования (опционально)
    # Создай его вручную в Voximplant, настрой приложение и сценарии,
    # затем укажи здесь ID для автоматического клонирования
    VOXIMPLANT_TEMPLATE_ACCOUNT_ID: Optional[str] = os.getenv("VOXIMPLANT_TEMPLATE_ACCOUNT_ID")
    
    # =========================================================================
    # ✅ НОВОЕ v3.3: Cloudflare R2 Storage для записей звонков
    # =========================================================================
    
    R2_ACCESS_KEY: str = os.getenv("R2_ACCESS_KEY", "")
    R2_SECRET_KEY: str = os.getenv("R2_SECRET_KEY", "")
    R2_ENDPOINT: str = os.getenv("R2_ENDPOINT", "")
    R2_BUCKET: str = os.getenv("R2_BUCKET", "voicyfy")
    R2_PUBLIC_URL: str = os.getenv("R2_PUBLIC_URL", "")

    # =========================================================================
    # ✅ НОВОЕ: Composio — внешние коннекторы агента (Google Calendar, Gmail)
    # =========================================================================
    # Серверный API-ключ Composio (dashboard.composio.dev). Без него коннекторы
    # отключены — пользователь просто не увидит кнопок подключения.
    COMPOSIO_API_KEY: Optional[str] = os.getenv("COMPOSIO_API_KEY")

    # Auth Config ID на каждый toolkit (создаётся в дашборде Composio).
    # Нужен для link() — старта OAuth-флоу подключения аккаунта пользователя.
    COMPOSIO_AUTH_CONFIG_GOOGLECALENDAR: Optional[str] = os.getenv("COMPOSIO_AUTH_CONFIG_GOOGLECALENDAR")
    COMPOSIO_AUTH_CONFIG_GMAIL: Optional[str] = os.getenv("COMPOSIO_AUTH_CONFIG_GMAIL")

    # =========================================================================
    # ✅ НОВОЕ: Личный Telegram-аккаунт агента (MTProto через Telethon)
    # =========================================================================
    # Креды MTProto-приложения с https://my.telegram.org/apps. Без них коннектор
    # Telegram отключён — пользователь не увидит кнопку подключения.
    TELEGRAM_API_ID: Optional[str] = os.getenv("TELEGRAM_API_ID")
    TELEGRAM_API_HASH: Optional[str] = os.getenv("TELEGRAM_API_HASH")
    # Ключ Fernet для шифрования StringSession в БД (сессия = полный доступ к
    # аккаунту, в открытом виде не храним). Генерация:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    TELEGRAM_SESSION_KEY: Optional[str] = os.getenv("TELEGRAM_SESSION_KEY")

    # =========================================================================

    # ✅ ИСПРАВЛЕНО: Улучшенные validators с детальными проверками
    @validator("HOST_URL")
    def validate_host_url(cls, v):
        if not v:
            raise ValueError("HOST_URL must be set - payment callbacks require a public URL!")

        if not v.startswith(("http://", "https://")):
            raise ValueError("HOST_URL must start with http:// or https://")

        # ✅ СТРОГАЯ проверка на localhost (webhook Finik требует публичный https)
        localhost_indicators = ["localhost", "127.0.0.1", "0.0.0.0", ".local"]
        if any(indicator in v.lower() for indicator in localhost_indicators):
            raise ValueError(
                "HOST_URL cannot be localhost or local domain - Finik webhooks require public access! "
                "Use public domain like https://yourdomain.com"
            )

        return v
    
    @validator("DATABASE_URL")
    def validate_database_url(cls, v):
        if not v and not cls.__dict__.get('DEBUG', False):
            raise ValueError("DATABASE_URL must be set in production mode")
        return v
    
    @validator("EMAIL_PASSWORD")
    def validate_email_password(cls, v):
        """Проверяем, что EMAIL_PASSWORD задан"""
        if not v:
            print("⚠️ WARNING: EMAIL_PASSWORD is not set - email verification will not work!")
        return v
    
    @validator("FINIK_ACCOUNT_ID")
    def validate_finik_config(cls, v, values):
        """Мягкая проверка конфигурации Finik — не роняем приложение, только предупреждаем."""
        missing = []
        if not values.get("FINIK_API_KEY"):
            missing.append("FINIK_API_KEY")
        if not values.get("FINIK_PRIVATE_PEM"):
            missing.append("FINIK_PRIVATE_PEM")
        if not v:
            missing.append("FINIK_ACCOUNT_ID")
        if missing:
            print(f"⚠️ WARNING: Finik payments not fully configured, missing: {', '.join(missing)}")
        return v

    # ✅ НОВЫЙ validator для Voximplant Partner Integration
    @validator("VOXIMPLANT_PARENT_API_KEY")
    def validate_voximplant_config(cls, v, values):
        """Проверяем конфигурацию Voximplant Partner"""
        account_id = values.get('VOXIMPLANT_PARENT_ACCOUNT_ID')
        
        if account_id and not v:
            print("⚠️ WARNING: VOXIMPLANT_PARENT_ACCOUNT_ID is set but VOXIMPLANT_PARENT_API_KEY is missing!")
        
        if v and not account_id:
            print("⚠️ WARNING: VOXIMPLANT_PARENT_API_KEY is set but VOXIMPLANT_PARENT_ACCOUNT_ID is missing!")
        
        if account_id and v:
            print(f"✅ Voximplant Partner configured: Account ID {account_id}")
        
        return v
    
    # ✅ НОВОЕ v3.3: Validator для Cloudflare R2
    @validator("R2_PUBLIC_URL")
    def validate_r2_config(cls, v, values):
        """Проверяем конфигурацию R2"""
        access_key = values.get('R2_ACCESS_KEY')
        secret_key = values.get('R2_SECRET_KEY')
        endpoint = values.get('R2_ENDPOINT')
        bucket = values.get('R2_BUCKET')
        
        if access_key and secret_key and endpoint:
            if v:
                print(f"✅ Cloudflare R2 configured: {bucket}")
            else:
                print("⚠️ WARNING: R2 credentials set but R2_PUBLIC_URL is missing!")
        else:
            if any([access_key, secret_key, endpoint, v]):
                print("⚠️ WARNING: Partial R2 configuration - recordings will not be saved!")
            # Не выводим предупреждение если R2 полностью не настроен - это опционально
        
        return v
    
    class Config:
        """Pydantic settings configuration"""
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

# Create a global settings instance
try:
    settings = Settings()
    print("✅ Configuration loaded successfully")
    
    # Проверяем Email настройки
    if settings.EMAIL_PASSWORD:
        print(f"📧 Email configured: {settings.EMAIL_FROM} via {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
    else:
        print("⚠️ Email not configured - verification emails will not work")
    
    # ✅ Проверяем Voximplant Partner настройки
    if settings.VOXIMPLANT_PARENT_ACCOUNT_ID and settings.VOXIMPLANT_PARENT_API_KEY:
        print(f"📞 Voximplant Partner configured: Account {settings.VOXIMPLANT_PARENT_ACCOUNT_ID}")
        if settings.VOXIMPLANT_TEMPLATE_ACCOUNT_ID:
            print(f"   Template account: {settings.VOXIMPLANT_TEMPLATE_ACCOUNT_ID}")
        else:
            print("   ⚠️ No template account - will create empty child accounts")
    else:
        print("ℹ️  Voximplant Partner not configured - telephony features disabled")
    
    # ✅ НОВОЕ v3.3: Проверяем R2 настройки
    if settings.R2_ACCESS_KEY and settings.R2_SECRET_KEY and settings.R2_ENDPOINT:
        print(f"💾 R2 Storage configured: {settings.R2_BUCKET}")
        if settings.R2_PUBLIC_URL:
            print(f"   Public URL: {settings.R2_PUBLIC_URL}")
        else:
            print("   ⚠️ R2_PUBLIC_URL not set - recordings won't be publicly accessible")
    else:
        print("ℹ️  R2 Storage not configured - call recordings will use temporary Voximplant URLs")

    # ✅ Проверяем Composio (коннекторы агента)
    if settings.COMPOSIO_API_KEY:
        _cfgd = [
            name for name, val in (
                ("GoogleCalendar", settings.COMPOSIO_AUTH_CONFIG_GOOGLECALENDAR),
                ("Gmail", settings.COMPOSIO_AUTH_CONFIG_GMAIL),
            ) if val
        ]
        print(f"🔌 Composio configured. Auth configs: {', '.join(_cfgd) if _cfgd else 'none (set COMPOSIO_AUTH_CONFIG_*)'}")
    else:
        print("ℹ️  Composio not configured - agent connectors (Calendar/Gmail) disabled")

except Exception as e:
    print(f"❌ Configuration error: {str(e)}")
    print("Please check your .env file and fix the configuration issues.")
    raise
