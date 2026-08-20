"""
API endpoints для управления настройками Voximplant и Telegram.
Позволяет пользователям сохранять и получать свои учетные данные.
✅ v2.9: БЕЗ маскирования API Key - показываем полный ключ для простоты
✅ v3.9: Добавлены эндпоинты для Telegram уведомлений о звонках
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, validator
from typing import Optional

from backend.db.session import get_db
from backend.models.user import User
from backend.services.auth_service import AuthService
from backend.services.telegram_notification import TelegramNotificationService
from backend.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ============================================================================
# VOXIMPLANT SCHEMAS
# ============================================================================

class VoximplantConfigUpdate(BaseModel):
    """Схема для обновления настроек Voximplant"""
    account_id: str = Field(..., min_length=1, max_length=100, description="Voximplant Account ID")
    api_key: str = Field(..., min_length=1, max_length=500, description="Voximplant API Key")
    rule_id: str = Field(..., min_length=1, max_length=100, description="Voximplant Rule ID")
    caller_id: str = Field(..., min_length=1, max_length=50, description="Caller ID (phone number)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "account_id": "12345678",
                "api_key": "your_api_key_here",
                "rule_id": "123456",
                "caller_id": "+1234567890"
            }
        }


class VoximplantConfigResponse(BaseModel):
    """Схема ответа с настройками Voximplant"""
    account_id: Optional[str] = None
    api_key: Optional[str] = None  # ✅ Полный API Key (БЕЗ маскирования)
    rule_id: Optional[str] = None
    caller_id: Optional[str] = None
    is_configured: bool = False
    
    class Config:
        json_schema_extra = {
            "example": {
                "account_id": "12345678",
                "api_key": "b52d81e6-862a-4882-8165-c78c3452d076",
                "rule_id": "123456",
                "caller_id": "+1234567890",
                "is_configured": True
            }
        }


class VoximplantConfigDeleteResponse(BaseModel):
    """Схема ответа при удалении настроек"""
    success: bool
    message: str


# ============================================================================
# 🆕 v3.9: TELEGRAM SCHEMAS
# ============================================================================

class TelegramConfigUpdate(BaseModel):
    """Схема для обновления настроек Telegram"""
    bot_token: str = Field(
        ..., 
        min_length=20, 
        max_length=100, 
        description="Telegram Bot Token (получить у @BotFather)"
    )
    chat_id: str = Field(
        ..., 
        min_length=1, 
        max_length=50, 
        description="Chat ID (личный чат, группа или канал)"
    )
    
    @validator('bot_token')
    def validate_bot_token(cls, v):
        """Валидация формата токена бота"""
        if not TelegramNotificationService.validate_bot_token(v):
            raise ValueError(
                'Неверный формат токена. Токен должен быть в формате: 123456789:ABCdefGHI...'
            )
        return v.strip()
    
    @validator('chat_id')
    def validate_chat_id(cls, v):
        """Валидация формата chat_id"""
        if not TelegramNotificationService.validate_chat_id(v):
            raise ValueError(
                'Неверный формат Chat ID. Должен быть числом (положительным для личного чата, '
                'отрицательным для группы/канала) или @username канала'
            )
        return v.strip()
    
    class Config:
        json_schema_extra = {
            "example": {
                "bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
                "chat_id": "-1001234567890"
            }
        }


class TelegramConfigResponse(BaseModel):
    """Схема ответа с настройками Telegram"""
    bot_token: Optional[str] = None  # Маскированный токен для безопасности
    chat_id: Optional[str] = None
    is_configured: bool = False
    
    class Config:
        json_schema_extra = {
            "example": {
                "bot_token": "123456789:ABCdef***",
                "chat_id": "-1001234567890",
                "is_configured": True
            }
        }


class TelegramTestResponse(BaseModel):
    """Схема ответа при тестировании Telegram"""
    success: bool
    message: str
    message_id: Optional[int] = None


# ============================================================================
# 🆕 v4.0: WEBHOOK SCHEMAS
# ============================================================================

class WebhookConfigUpdate(BaseModel):
    """Схема для обновления настроек webhook"""
    webhook_url: Optional[str] = Field(
        None,
        max_length=500,
        description="URL для отправки webhook-уведомлений о завершённых диалогах"
    )
    webhook_enabled: bool = Field(
        False,
        description="Включить отправку webhook-уведомлений"
    )

    @validator('webhook_url')
    def validate_webhook_url(cls, v):
        """Базовая валидация URL"""
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError('URL должен начинаться с http:// или https://')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "webhook_url": "https://your-server.com/voicyfy-webhook",
                "webhook_enabled": True
            }
        }


class WebhookConfigResponse(BaseModel):
    """Схема ответа с настройками webhook"""
    webhook_url: Optional[str] = None
    webhook_enabled: bool = False
    is_configured: bool = False

    class Config:
        json_schema_extra = {
            "example": {
                "webhook_url": "https://your-server.com/voicyfy-webhook",
                "webhook_enabled": True,
                "is_configured": True
            }
        }


# ============================================================================
# VOXIMPLANT API ENDPOINTS
# ============================================================================

@router.get("/voximplant-settings", response_model=VoximplantConfigResponse)
async def get_voximplant_settings(
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить настройки Voximplant текущего пользователя.
    ✅ API Key возвращается ПОЛНОСТЬЮ (без маскирования).
    
    **Использование:**
    - GET /api/users/voximplant-settings
    
    **Ответ:**
    - account_id: Voximplant Account ID
    - api_key: Полный API Key
    - rule_id: Rule ID для маршрутизации
    - caller_id: Номер телефона для исходящих звонков
    - is_configured: true если все поля заполнены
    """
    try:
        logger.info(f"[VOXIMPLANT-SETTINGS] Getting settings for user {current_user.id}")
        
        # Проверяем наличие настроек
        is_configured = current_user.has_voximplant_config()
        
        return VoximplantConfigResponse(
            account_id=current_user.voximplant_account_id,
            api_key=current_user.voximplant_api_key,  # ✅ Полный ключ
            rule_id=current_user.voximplant_rule_id,
            caller_id=current_user.voximplant_caller_id,
            is_configured=is_configured
        )
        
    except Exception as e:
        logger.error(f"[VOXIMPLANT-SETTINGS] Error getting settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка получения настроек: {str(e)}")


@router.put("/voximplant-settings", response_model=VoximplantConfigResponse)
async def update_voximplant_settings(
    config: VoximplantConfigUpdate,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Обновить настройки Voximplant для текущего пользователя.
    
    **Использование:**
    - PUT /api/users/voximplant-settings
    
    **Body:**
    ```json
    {
        "account_id": "12345678",
        "api_key": "your_api_key",
        "rule_id": "123456",
        "caller_id": "+1234567890"
    }
    ```
    
    **Примечание:**
    - Все поля обязательны
    - Caller ID должен быть в международном формате (+...)
    - Эти настройки используются Task Scheduler для автоматических звонков
    """
    try:
        logger.info(f"[VOXIMPLANT-SETTINGS] Updating settings for user {current_user.id}")
        
        # Обновляем настройки
        current_user.voximplant_account_id = config.account_id.strip()
        current_user.voximplant_api_key = config.api_key.strip()
        current_user.voximplant_rule_id = config.rule_id.strip()
        current_user.voximplant_caller_id = config.caller_id.strip()
        
        db.commit()
        db.refresh(current_user)
        
        logger.info(f"[VOXIMPLANT-SETTINGS] ✅ Settings updated for user {current_user.id}")
        
        return VoximplantConfigResponse(
            account_id=current_user.voximplant_account_id,
            api_key=current_user.voximplant_api_key,  # ✅ Полный ключ
            rule_id=current_user.voximplant_rule_id,
            caller_id=current_user.voximplant_caller_id,
            is_configured=True
        )
        
    except Exception as e:
        db.rollback()
        logger.error(f"[VOXIMPLANT-SETTINGS] Error updating settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка обновления настроек: {str(e)}")


@router.delete("/voximplant-settings", response_model=VoximplantConfigDeleteResponse)
async def delete_voximplant_settings(
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Удалить настройки Voximplant для текущего пользователя.
    
    **Использование:**
    - DELETE /api/users/voximplant-settings
    
    **Примечание:**
    - Удаляет все сохраненные данные Voximplant
    - После удаления автоматические звонки через Task Scheduler работать не будут
    """
    try:
        logger.info(f"[VOXIMPLANT-SETTINGS] Deleting settings for user {current_user.id}")
        
        # Удаляем настройки
        current_user.voximplant_account_id = None
        current_user.voximplant_api_key = None
        current_user.voximplant_rule_id = None
        current_user.voximplant_caller_id = None
        
        db.commit()
        
        logger.info(f"[VOXIMPLANT-SETTINGS] ✅ Settings deleted for user {current_user.id}")
        
        return VoximplantConfigDeleteResponse(
            success=True,
            message="Настройки Voximplant успешно удалены"
        )
        
    except Exception as e:
        db.rollback()
        logger.error(f"[VOXIMPLANT-SETTINGS] Error deleting settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка удаления настроек: {str(e)}")


@router.post("/voximplant-settings/test")
async def test_voximplant_settings(
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Проверить настройки Voximplant (опционально).
    
    **Использование:**
    - POST /api/users/voximplant-settings/test
    
    **Ответ:**
    - success: true/false
    - message: Результат проверки
    
    **Примечание:**
    - Пока просто проверяет наличие всех полей
    - В будущем можно добавить реальную проверку через Voximplant API
    """
    try:
        logger.info(f"[VOXIMPLANT-SETTINGS] Testing settings for user {current_user.id}")
        
        if not current_user.has_voximplant_config():
            return {
                "success": False,
                "message": "Настройки Voximplant не заполнены. Заполните все поля в разделе Настройки."
            }
        
        # TODO: Здесь можно добавить реальную проверку через Voximplant API
        # Например, попробовать получить список правил или сделать тестовый запрос
        
        logger.info(f"[VOXIMPLANT-SETTINGS] ✅ Settings test passed for user {current_user.id}")
        
        return {
            "success": True,
            "message": "Настройки Voximplant заполнены корректно",
            "config": {
                "account_id": current_user.voximplant_account_id,
                "rule_id": current_user.voximplant_rule_id,
                "caller_id": current_user.voximplant_caller_id
            }
        }
        
    except Exception as e:
        logger.error(f"[VOXIMPLANT-SETTINGS] Error testing settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка проверки настроек: {str(e)}")


# ============================================================================
# 🆕 v3.9: TELEGRAM API ENDPOINTS
# ============================================================================

@router.get("/telegram-settings", response_model=TelegramConfigResponse)
async def get_telegram_settings(
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить настройки Telegram уведомлений текущего пользователя.
    
    **Использование:**
    - GET /api/users/telegram-settings
    
    **Ответ:**
    - bot_token: Маскированный токен бота (для безопасности)
    - chat_id: ID чата/группы/канала
    - is_configured: true если настройки заполнены
    
    **Примечание:**
    - Токен бота маскируется при отображении
    - Для обновления токена используйте PUT запрос
    """
    try:
        logger.info(f"[TELEGRAM-SETTINGS] Getting settings for user {current_user.id}")
        
        is_configured = current_user.has_telegram_config()
        
        # Маскируем токен для безопасности
        masked_token = None
        if current_user.telegram_bot_token:
            token = current_user.telegram_bot_token
            # Показываем первые 10 символов и последние 4
            if len(token) > 20:
                masked_token = token[:10] + "***" + token[-4:]
            else:
                masked_token = token[:5] + "***"
        
        return TelegramConfigResponse(
            bot_token=masked_token,
            chat_id=current_user.telegram_chat_id,
            is_configured=is_configured
        )
        
    except Exception as e:
        logger.error(f"[TELEGRAM-SETTINGS] Error getting settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка получения настроек: {str(e)}")


@router.put("/telegram-settings", response_model=TelegramConfigResponse)
async def update_telegram_settings(
    config: TelegramConfigUpdate,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Обновить настройки Telegram уведомлений.
    
    **Использование:**
    - PUT /api/users/telegram-settings
    
    **Body:**
    ```json
    {
        "bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        "chat_id": "-1001234567890"
    }
    ```
    
    **Как получить данные:**
    
    1. **Bot Token:**
       - Создайте бота через @BotFather в Telegram
       - Используйте команду /newbot
       - Скопируйте полученный токен
    
    2. **Chat ID:**
       - Для личного чата: отправьте сообщение боту @userinfobot
       - Для группы: добавьте бота @RawDataBot в группу
       - Для канала: перешлите сообщение из канала боту @RawDataBot
       - Chat ID группы/канала начинается с минуса (например: -1001234567890)
    
    **Примечание:**
    - После сохранения рекомендуется проверить настройки через /test
    - Уведомления будут приходить при каждом новом звонке с записью
    """
    try:
        logger.info(f"[TELEGRAM-SETTINGS] Updating settings for user {current_user.id}")
        
        # Обновляем настройки
        current_user.telegram_bot_token = config.bot_token
        current_user.telegram_chat_id = config.chat_id
        
        db.commit()
        db.refresh(current_user)
        
        logger.info(f"[TELEGRAM-SETTINGS] ✅ Settings updated for user {current_user.id}")
        
        # Маскируем токен для ответа
        token = current_user.telegram_bot_token
        masked_token = token[:10] + "***" + token[-4:] if len(token) > 20 else token[:5] + "***"
        
        return TelegramConfigResponse(
            bot_token=masked_token,
            chat_id=current_user.telegram_chat_id,
            is_configured=True
        )
        
    except ValueError as ve:
        # Ошибки валидации Pydantic
        logger.warning(f"[TELEGRAM-SETTINGS] Validation error: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        db.rollback()
        logger.error(f"[TELEGRAM-SETTINGS] Error updating settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка обновления настроек: {str(e)}")


@router.delete("/telegram-settings")
async def delete_telegram_settings(
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Удалить настройки Telegram уведомлений.
    
    **Использование:**
    - DELETE /api/users/telegram-settings
    
    **Примечание:**
    - После удаления уведомления о звонках отправляться не будут
    - Для возобновления уведомлений заполните настройки заново
    """
    try:
        logger.info(f"[TELEGRAM-SETTINGS] Deleting settings for user {current_user.id}")
        
        # Удаляем настройки
        current_user.telegram_bot_token = None
        current_user.telegram_chat_id = None
        
        db.commit()
        
        logger.info(f"[TELEGRAM-SETTINGS] ✅ Settings deleted for user {current_user.id}")
        
        return {
            "success": True,
            "message": "Настройки Telegram успешно удалены"
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"[TELEGRAM-SETTINGS] Error deleting settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка удаления настроек: {str(e)}")


@router.post("/telegram-settings/test", response_model=TelegramTestResponse)
async def test_telegram_settings(
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Проверить настройки Telegram отправкой тестового сообщения.
    
    **Использование:**
    - POST /api/users/telegram-settings/test
    
    **Ответ:**
    - success: true/false
    - message: Результат проверки
    - message_id: ID отправленного сообщения (если успешно)
    
    **Примечание:**
    - Отправляет тестовое сообщение в указанный чат
    - Если сообщение доставлено — настройки корректны
    - Проверьте, что бот добавлен в группу/канал и имеет права на отправку
    """
    try:
        logger.info(f"[TELEGRAM-SETTINGS] Testing settings for user {current_user.id}")
        
        # Проверяем наличие настроек
        if not current_user.has_telegram_config():
            return TelegramTestResponse(
                success=False,
                message="Настройки Telegram не заполнены. Сначала укажите токен бота и Chat ID."
            )
        
        # Отправляем тестовое сообщение
        result = await TelegramNotificationService.test_connection(
            bot_token=current_user.telegram_bot_token,
            chat_id=current_user.telegram_chat_id
        )
        
        if result["success"]:
            logger.info(f"[TELEGRAM-SETTINGS] ✅ Test passed for user {current_user.id}")
            return TelegramTestResponse(
                success=True,
                message="✅ Тестовое сообщение успешно отправлено! Проверьте Telegram.",
                message_id=result.get("message_id")
            )
        else:
            error = result.get("error", "Неизвестная ошибка")
            logger.warning(f"[TELEGRAM-SETTINGS] Test failed for user {current_user.id}: {error}")
            
            # Формируем понятное сообщение об ошибке
            user_message = "Не удалось отправить сообщение. "
            
            if "chat not found" in error.lower():
                user_message += "Chat ID не найден. Проверьте правильность ID."
            elif "bot was blocked" in error.lower():
                user_message += "Бот заблокирован пользователем."
            elif "not enough rights" in error.lower():
                user_message += "У бота нет прав на отправку сообщений в этот чат."
            elif "unauthorized" in error.lower():
                user_message += "Неверный токен бота. Проверьте токен."
            else:
                user_message += f"Ошибка: {error}"
            
            return TelegramTestResponse(
                success=False,
                message=user_message
            )
        
    except Exception as e:
        logger.error(f"[TELEGRAM-SETTINGS] Error testing settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка проверки настроек: {str(e)}")


# ============================================================================
# 🆕 v4.0: WEBHOOK API ENDPOINTS
# ============================================================================

@router.get("/webhook-settings", response_model=WebhookConfigResponse)
async def get_webhook_settings(
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить настройки webhook-уведомлений текущего пользователя.

    **Использование:**
    - GET /api/users/webhook-settings

    **Ответ:**
    - webhook_url: URL для отправки уведомлений
    - webhook_enabled: Включена ли отправка
    - is_configured: true если URL заполнен и отправка включена
    """
    try:
        logger.info(f"[WEBHOOK-SETTINGS] Getting settings for user {current_user.id}")

        return WebhookConfigResponse(
            webhook_url=current_user.webhook_url,
            webhook_enabled=bool(current_user.webhook_enabled),
            is_configured=current_user.has_webhook_config()
        )

    except Exception as e:
        logger.error(f"[WEBHOOK-SETTINGS] Error getting settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка получения настроек: {str(e)}")


@router.put("/webhook-settings", response_model=WebhookConfigResponse)
async def update_webhook_settings(
    config: WebhookConfigUpdate,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Обновить настройки webhook-уведомлений.

    **Использование:**
    - PUT /api/users/webhook-settings

    **Body:**
    ```json
    {
        "webhook_url": "https://your-server.com/voicyfy-webhook",
        "webhook_enabled": true
    }
    ```

    **Примечание:**
    - По событию `conversation.completed` на указанный URL приходит POST
      с полным диалогом и метаданными (для веб-чата и телефонии).
    - Если webhook_enabled = false или URL пустой — уведомления не отправляются.
    """
    try:
        logger.info(f"[WEBHOOK-SETTINGS] Updating settings for user {current_user.id}")

        current_user.webhook_url = config.webhook_url
        current_user.webhook_enabled = bool(config.webhook_enabled)

        db.commit()
        db.refresh(current_user)

        logger.info(f"[WEBHOOK-SETTINGS] ✅ Settings updated for user {current_user.id}")

        return WebhookConfigResponse(
            webhook_url=current_user.webhook_url,
            webhook_enabled=bool(current_user.webhook_enabled),
            is_configured=current_user.has_webhook_config()
        )

    except ValueError as ve:
        logger.warning(f"[WEBHOOK-SETTINGS] Validation error: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        db.rollback()
        logger.error(f"[WEBHOOK-SETTINGS] Error updating settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка обновления настроек: {str(e)}")


@router.delete("/webhook-settings")
async def delete_webhook_settings(
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Удалить настройки webhook-уведомлений.

    **Использование:**
    - DELETE /api/users/webhook-settings
    """
    try:
        logger.info(f"[WEBHOOK-SETTINGS] Deleting settings for user {current_user.id}")

        current_user.webhook_url = None
        current_user.webhook_enabled = False

        db.commit()

        logger.info(f"[WEBHOOK-SETTINGS] ✅ Settings deleted for user {current_user.id}")

        return {
            "success": True,
            "message": "Настройки webhook успешно удалены"
        }

    except Exception as e:
        db.rollback()
        logger.error(f"[WEBHOOK-SETTINGS] Error deleting settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка удаления настроек: {str(e)}")
