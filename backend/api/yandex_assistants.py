# backend/api/yandex_assistants.py
"""
REST API endpoints for Yandex voice assistants management.
Handles CRUD operations for Yandex SpeechKit Realtime assistant configs.

Вся логика звонка живёт в сценариях Voximplant (inbound_yandex / outbound_yandex).
Бэкенд хранит конфигурацию и отдаёт её через /api/telephony/config
и /api/telephony/outbound-config.

Креды (api_key + folder_id) хранятся на пользователе — каждый клиент платит
за свои токены со своего биллинга Yandex Cloud.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Dict, Any
import uuid

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.models.user import User
from backend.models.yandex_assistant import (
    YandexAssistantConfig,
    YandexConversation,
    DEFAULT_YANDEX_MODEL,
    DEFAULT_YANDEX_TEMPERATURE,
    DEFAULT_YANDEX_MAX_TOKENS,
)
from backend.core.dependencies import get_current_user, check_assistant_limit
from backend.services.assistant_limit_service import exclude_agent_owned

logger = get_logger(__name__)

router = APIRouter()


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

from pydantic import BaseModel, Field
from datetime import datetime

# Доступные модели Yandex SpeechKit Realtime.
# Дефолт для новых ассистентов — DEFAULT_YANDEX_MODEL в models/yandex_assistant.py.
YANDEX_MODELS = [
    "speech-realtime-260528",
    "speech-realtime-250923",
]

# Доступные голоса Yandex SpeechKit
YANDEX_VOICES = [
    "marina",
    "dasha",
    "alexander",
    "julia",
    "lera",
    "masha",
    "anton",
    "kirill",
    "filipp",
    "ermil",
    "jane",
    "omazh",
    "zahar",
    "madi_ru",
    "saule_ru",
]


class YandexAssistantCreate(BaseModel):
    """Schema for creating a new Yandex assistant"""
    name: str = Field(..., min_length=1, max_length=255, description="Assistant name")
    description: Optional[str] = Field(None, max_length=500, description="Assistant description")
    system_prompt: Optional[str] = Field(None, description="System instructions for assistant")
    model: str = Field(default=DEFAULT_YANDEX_MODEL, description="Yandex Realtime model")
    voice: str = Field(default="marina", description="Yandex voice (marina, dasha, alexander, ...)")
    voice_role: Optional[str] = Field(None, description="Voice role/амплуа (optional)")
    language: str = Field(default="ru", description="Language code")
    greeting_message: Optional[str] = Field(
        default="Здравствуйте! Чем я могу вам помочь?",
        max_length=500,
        description="Greeting message (first phrase)"
    )
    temperature: float = Field(default=DEFAULT_YANDEX_TEMPERATURE, ge=0.0, le=2.0, description="Model temperature")
    max_tokens: int = Field(default=DEFAULT_YANDEX_MAX_TOKENS, ge=1, le=32768, description="Max response tokens")
    functions: Optional[Any] = Field(default=None, description="Enabled functions config")
    google_sheet_id: Optional[str] = Field(None, max_length=255, description="Google Sheet ID for logging")


class YandexAssistantUpdate(BaseModel):
    """Schema for updating a Yandex assistant"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    voice: Optional[str] = None
    voice_role: Optional[str] = None
    language: Optional[str] = None
    greeting_message: Optional[str] = Field(None, max_length=500)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=32768)
    functions: Optional[Any] = None
    google_sheet_id: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None


class YandexAssistantResponse(BaseModel):
    """Schema for Yandex assistant response"""
    id: str
    name: str
    description: Optional[str]
    system_prompt: Optional[str]
    model: str
    voice: str
    voice_role: Optional[str]
    language: str
    greeting_message: Optional[str]
    temperature: float
    max_tokens: int
    functions: Optional[Any]
    google_sheet_id: Optional[str]
    is_active: bool
    total_conversations: int
    created_at: datetime

    class Config:
        from_attributes = True


class YandexApiKeysUpdate(BaseModel):
    """Schema for updating Yandex Cloud credentials"""
    yandex_api_key: Optional[str] = None
    yandex_folder_id: Optional[str] = None


class YandexApiKeysStatus(BaseModel):
    """Schema for Yandex credentials status response"""
    has_yandex_key: bool
    has_folder_id: bool
    yandex_key_preview: Optional[str] = None
    yandex_folder_id: Optional[str] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def mask_api_key(key: Optional[str]) -> Optional[str]:
    """Return masked version of API key for display."""
    if not key or len(key) < 8:
        return None
    return f"{key[:3]}...{key[-3:]}"


def assistant_to_response(assistant: YandexAssistantConfig) -> "YandexAssistantResponse":
    """Build response schema from ORM object."""
    return YandexAssistantResponse(
        id=str(assistant.id),
        name=assistant.name,
        description=assistant.description,
        system_prompt=assistant.system_prompt,
        model=assistant.model,
        voice=assistant.voice,
        voice_role=assistant.voice_role,
        language=assistant.language,
        greeting_message=assistant.greeting_message,
        temperature=assistant.temperature,
        max_tokens=assistant.max_tokens,
        functions=assistant.functions,
        google_sheet_id=assistant.google_sheet_id,
        is_active=assistant.is_active,
        total_conversations=assistant.total_conversations or 0,
        created_at=assistant.created_at,
    )


async def verify_assistant_access(
    assistant_id: str,
    user_id: str,
    db: Session,
    require_ownership: bool = True
) -> YandexAssistantConfig:
    """Verify user has access to the assistant."""
    try:
        assistant_uuid = uuid.UUID(assistant_id)
    except ValueError:
        logger.warning(f"[YANDEX-API] Invalid UUID format: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid assistant ID format"
        )

    assistant = db.query(YandexAssistantConfig).filter(
        YandexAssistantConfig.id == assistant_uuid
    ).first()

    if not assistant:
        logger.warning(f"[YANDEX-API] Assistant not found: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Yandex assistant not found"
        )

    if require_ownership and str(assistant.user_id) != user_id:
        logger.warning(f"[YANDEX-API] Unauthorized access attempt: user {user_id} -> assistant {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this assistant"
        )

    return assistant


# ============================================================================
# API ENDPOINTS — DICTIONARIES (модели/голоса для UI)
# ============================================================================

@router.get("/options")
async def get_yandex_options(
    current_user: User = Depends(get_current_user)
):
    """Get available models and voices for Yandex assistants (for UI dropdowns)."""
    return {
        "models": YANDEX_MODELS,
        "default_model": DEFAULT_YANDEX_MODEL,
        "voices": YANDEX_VOICES,
    }


# ============================================================================
# API ENDPOINTS — API KEYS (Yandex Cloud credentials)
# ============================================================================

@router.get("/api-keys", response_model=YandexApiKeysStatus)
async def get_api_keys_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get status of Yandex Cloud credentials (api_key + folder_id)."""
    return YandexApiKeysStatus(
        has_yandex_key=bool(current_user.yandex_api_key),
        has_folder_id=bool(current_user.yandex_folder_id),
        yandex_key_preview=mask_api_key(current_user.yandex_api_key),
        yandex_folder_id=current_user.yandex_folder_id,
    )


@router.put("/api-keys", response_model=YandexApiKeysStatus)
async def update_api_keys(
    keys_data: YandexApiKeysUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update Yandex Cloud API key and/or folder ID."""
    try:
        if keys_data.yandex_api_key is not None:
            if keys_data.yandex_api_key == "":
                current_user.yandex_api_key = None
            else:
                current_user.yandex_api_key = keys_data.yandex_api_key

        if keys_data.yandex_folder_id is not None:
            if keys_data.yandex_folder_id == "":
                current_user.yandex_folder_id = None
            else:
                current_user.yandex_folder_id = keys_data.yandex_folder_id

        db.commit()
        db.refresh(current_user)

        logger.info(f"[YANDEX-API] Yandex credentials updated for user {current_user.id}")

        return YandexApiKeysStatus(
            has_yandex_key=bool(current_user.yandex_api_key),
            has_folder_id=bool(current_user.yandex_folder_id),
            yandex_key_preview=mask_api_key(current_user.yandex_api_key),
            yandex_folder_id=current_user.yandex_folder_id,
        )

    except Exception as e:
        db.rollback()
        logger.error(f"[YANDEX-API] Error updating credentials: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update Yandex credentials"
        )


# ============================================================================
# API ENDPOINTS — CRUD
# ============================================================================

@router.get("")
async def get_yandex_assistants(
    include_agent_voices: bool = Query(
        False,
        description="Показать голосовых ассистентов агентов обзвона (по умолчанию скрыты)",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all Yandex assistants for the current user.
    Returns assistants list + credentials status.
    """
    try:
        logger.info(f"[YANDEX-API] Fetching assistants for user {current_user.id}")

        query = db.query(YandexAssistantConfig).filter(
            YandexAssistantConfig.user_id == current_user.id
        )
        if not include_agent_voices:
            query = exclude_agent_owned(query, YandexAssistantConfig, db, current_user.id)
        assistants = query.order_by(YandexAssistantConfig.created_at.desc()).all()

        logger.info(f"[YANDEX-API] Found {len(assistants)} Yandex assistants")

        return {
            "assistants": [assistant_to_response(a) for a in assistants],
            "has_yandex_key": bool(current_user.yandex_api_key),
            "has_folder_id": bool(current_user.yandex_folder_id),
        }

    except Exception as e:
        logger.error(f"[YANDEX-API] Error fetching assistants: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Yandex assistants"
        )


@router.get("/{assistant_id}", response_model=YandexAssistantResponse)
async def get_yandex_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific Yandex assistant by ID."""
    try:
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        return assistant_to_response(assistant)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[YANDEX-API] Error fetching assistant {assistant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Yandex assistant"
        )


@router.post("", response_model=YandexAssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_yandex_assistant(
    assistant_data: YandexAssistantCreate,
    db: Session = Depends(get_db),
    # check_assistant_limit проверяет активность подписки + общий лимит
    # ассистентов по всем провайдерам
    current_user: User = Depends(check_assistant_limit)
):
    """Create a new Yandex assistant."""
    try:
        logger.info(f"[YANDEX-API] Creating Yandex assistant for user {current_user.id}")
        logger.info(f"[YANDEX-API] Assistant name: {assistant_data.name}")

        assistant = YandexAssistantConfig(
            user_id=current_user.id,
            name=assistant_data.name,
            description=assistant_data.description,
            system_prompt=assistant_data.system_prompt,
            model=assistant_data.model or DEFAULT_YANDEX_MODEL,
            voice=assistant_data.voice or "marina",
            voice_role=assistant_data.voice_role,
            language=assistant_data.language or "ru",
            greeting_message=assistant_data.greeting_message,
            temperature=assistant_data.temperature,
            max_tokens=assistant_data.max_tokens,
            functions=assistant_data.functions,
            google_sheet_id=assistant_data.google_sheet_id,
            is_active=True,
        )

        db.add(assistant)
        db.commit()
        db.refresh(assistant)

        logger.info(f"[YANDEX-API] Yandex assistant created: {assistant.id}")

        return assistant_to_response(assistant)

    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        logger.error(f"[YANDEX-API] Database integrity error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create assistant due to database constraint"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[YANDEX-API] Error creating assistant: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create Yandex assistant"
        )


@router.put("/{assistant_id}", response_model=YandexAssistantResponse)
async def update_yandex_assistant(
    assistant_id: str,
    assistant_data: YandexAssistantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a Yandex assistant."""
    try:
        logger.info(f"[YANDEX-API] Updating assistant {assistant_id} for user {current_user.id}")

        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        update_data = assistant_data.dict(exclude_unset=True)

        for key, value in update_data.items():
            setattr(assistant, key, value)

        db.commit()
        db.refresh(assistant)

        logger.info(f"[YANDEX-API] Yandex assistant updated: {assistant_id}")

        return assistant_to_response(assistant)

    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        logger.error(f"[YANDEX-API] Database integrity error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to update assistant due to database constraint"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[YANDEX-API] Error updating assistant {assistant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update Yandex assistant"
        )


@router.delete("/{assistant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_yandex_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a Yandex assistant."""
    try:
        logger.info(f"[YANDEX-API] Deleting assistant {assistant_id} for user {current_user.id}")

        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        db.delete(assistant)
        db.commit()

        logger.info(f"[YANDEX-API] Yandex assistant deleted: {assistant_id}")

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[YANDEX-API] Error deleting assistant {assistant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete Yandex assistant"
        )


# ============================================================================
# API ENDPOINTS — CONVERSATIONS (история диалогов для интерфейса)
# ============================================================================

@router.get("/{assistant_id}/conversations")
async def get_yandex_conversations(
    assistant_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get conversation history for a Yandex assistant."""
    try:
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        query = db.query(YandexConversation).filter(
            YandexConversation.assistant_id == assistant.id
        )
        total = query.count()
        conversations = query.order_by(
            YandexConversation.created_at.desc()
        ).offset(offset).limit(limit).all()

        return {
            "conversations": [c.to_dict() for c in conversations],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[YANDEX-API] Error fetching conversations for {assistant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch conversations"
        )
