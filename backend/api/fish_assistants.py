# backend/api/fish_assistants.py
"""
REST API endpoints for Fish Audio voice assistants management.

Fish Audio — TTS-провайдер. Диалог ведёт OpenAI Realtime
(gpt-realtime-2.1-mini, output_modalities=["text"]) прямо в сценарии
Voximplant, озвучивает Fish через наш прокси /ws/fish/tts/{assistant_id}.
Оба ключа пользовательские: openai_api_key (LLM) и fish_api_key (TTS).

Конфиг ассистента отдаётся сценарию через существующие
/api/telephony/config и /api/telephony/outbound-config.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
import uuid

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.models.user import User
from backend.models.fish_assistant import (
    FishAssistantConfig,
    DEFAULT_FISH_MODEL,
    DEFAULT_FISH_LLM_MODEL,
    DEFAULT_FISH_SAMPLE_RATE,
    DEFAULT_FISH_LATENCY,
    FISH_MODELS,
    FISH_SELECTABLE_MODELS,
    FISH_LATENCY_MODES,
    FISH_SPEED_MIN,
    FISH_SPEED_MAX,
    FISH_TEMPERATURE_MIN,
    FISH_TEMPERATURE_MAX,
)
from backend.core.dependencies import get_current_user, check_assistant_limit
from backend.services.assistant_limit_service import exclude_agent_owned

logger = get_logger(__name__)

router = APIRouter()


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

class FishAssistantCreate(BaseModel):
    """Schema for creating a new Fish assistant"""
    name: str = Field(..., min_length=1, max_length=255, description="Assistant name")
    description: Optional[str] = Field(None, max_length=500, description="Assistant description")
    system_prompt: Optional[str] = Field(None, description="System instructions for assistant")
    fish_voice_id: Optional[str] = Field(None, description="Fish Audio reference_id (голос из библиотеки fish.audio)")
    fish_model: str = Field(default=DEFAULT_FISH_MODEL, description=f"Fish TTS model: {FISH_MODELS}")
    fish_latency: str = Field(default=DEFAULT_FISH_LATENCY, description=f"Latency mode: {FISH_LATENCY_MODES}")
    sample_rate: int = Field(default=DEFAULT_FISH_SAMPLE_RATE, ge=8000, le=48000, description="PCM sample rate, Hz")
    voice_speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Скорость речи Fish (prosody.speed)")
    llm_model: str = Field(default=DEFAULT_FISH_LLM_MODEL, max_length=100, description="OpenAI Realtime model")
    language: str = Field(default="ru", max_length=10, description="Dialog language")
    temperature: float = Field(default=0.7, ge=0.0, le=1.0, description="Живость интонации Fish (0–1)")
    greeting_message: Optional[str] = Field(
        default="Здравствуйте! Чем я могу вам помочь?",
        max_length=500,
        description="Greeting message",
    )
    google_sheet_id: Optional[str] = Field(None, max_length=255, description="Google Sheet ID for logging")
    functions: Optional[List[Dict]] = Field(default=None, description="Enabled functions config")


class FishAssistantUpdate(BaseModel):
    """Schema for updating a Fish assistant"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    system_prompt: Optional[str] = None
    fish_voice_id: Optional[str] = None
    fish_model: Optional[str] = None
    fish_latency: Optional[str] = None
    sample_rate: Optional[int] = Field(None, ge=8000, le=48000)
    voice_speed: Optional[float] = Field(None, ge=0.5, le=2.0)
    llm_model: Optional[str] = Field(None, max_length=100)
    language: Optional[str] = Field(None, max_length=10)
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0)
    greeting_message: Optional[str] = Field(None, max_length=500)
    google_sheet_id: Optional[str] = Field(None, max_length=255)
    functions: Optional[List[Dict]] = None
    is_active: Optional[bool] = None


class FishAssistantResponse(BaseModel):
    """Schema for Fish assistant response"""
    id: str
    name: str
    description: Optional[str]
    system_prompt: Optional[str]
    fish_voice_id: Optional[str]
    fish_model: str
    fish_latency: str
    sample_rate: int
    voice_speed: Optional[float]
    llm_model: str
    language: str
    temperature: Optional[float]
    greeting_message: Optional[str]
    google_sheet_id: Optional[str]
    functions: Optional[Any]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class FishApiKeysUpdate(BaseModel):
    """Schema for updating Fish-related API keys"""
    openai_api_key: Optional[str] = None
    fish_api_key: Optional[str] = None


class FishApiKeysStatus(BaseModel):
    """Schema for API keys status response"""
    has_openai_key: bool
    has_fish_key: bool
    openai_key_preview: Optional[str] = None
    fish_key_preview: Optional[str] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def mask_api_key(key: Optional[str]) -> Optional[str]:
    """Return masked version of API key for display."""
    if not key or len(key) < 8:
        return None
    return f"{key[:3]}...{key[-3:]}"


def validate_fish_model(model: Optional[str]) -> None:
    if model is not None and model not in FISH_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown Fish model. Available: {FISH_MODELS}",
        )


def validate_fish_latency(latency: Optional[str]) -> None:
    if latency is not None and latency not in FISH_LATENCY_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown latency mode. Available: {FISH_LATENCY_MODES}",
        )


def to_response(assistant: FishAssistantConfig) -> FishAssistantResponse:
    return FishAssistantResponse(
        id=str(assistant.id),
        name=assistant.name,
        description=assistant.description,
        system_prompt=assistant.system_prompt,
        fish_voice_id=assistant.fish_voice_id,
        fish_model=assistant.fish_model or DEFAULT_FISH_MODEL,
        fish_latency=assistant.fish_latency or DEFAULT_FISH_LATENCY,
        sample_rate=assistant.sample_rate or DEFAULT_FISH_SAMPLE_RATE,
        voice_speed=assistant.voice_speed,
        llm_model=assistant.llm_model or DEFAULT_FISH_LLM_MODEL,
        language=assistant.language or "ru",
        temperature=assistant.temperature,
        greeting_message=assistant.greeting_message,
        google_sheet_id=assistant.google_sheet_id,
        functions=assistant.functions,
        is_active=assistant.is_active,
        created_at=assistant.created_at,
    )


async def verify_assistant_access(
    assistant_id: str,
    user_id: str,
    db: Session,
    require_ownership: bool = True,
) -> FishAssistantConfig:
    """Verify user has access to the assistant."""
    try:
        assistant_uuid = uuid.UUID(assistant_id)
    except ValueError:
        logger.warning(f"[FISH-API] Invalid UUID format: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid assistant ID format",
        )

    assistant = db.query(FishAssistantConfig).filter(
        FishAssistantConfig.id == assistant_uuid
    ).first()

    if not assistant:
        logger.warning(f"[FISH-API] Assistant not found: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fish assistant not found",
        )

    if require_ownership and str(assistant.user_id) != user_id:
        logger.warning(f"[FISH-API] Unauthorized access: user {user_id} -> assistant {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this assistant",
        )

    return assistant


# ============================================================================
# API ENDPOINTS — META (фиксированные пути идут до /{assistant_id})
# ============================================================================

@router.get("/options")
async def get_fish_options():
    """Справочник моделей синтеза и режимов латентности Fish Audio."""
    return {
        # В селекторе — только обкатываемая модель; API при этом принимает
        # весь FISH_MODELS, чтобы старые агенты продолжали сохраняться.
        "models": FISH_SELECTABLE_MODELS,
        "default_model": DEFAULT_FISH_MODEL,
        "latency_modes": FISH_LATENCY_MODES,
        "default_latency": DEFAULT_FISH_LATENCY,
        "llm_models": [DEFAULT_FISH_LLM_MODEL, "gpt-realtime-1.5"],
        "default_llm_model": DEFAULT_FISH_LLM_MODEL,
        "sample_rate": DEFAULT_FISH_SAMPLE_RATE,
        "speed": {"min": FISH_SPEED_MIN, "max": FISH_SPEED_MAX, "default": 1.0},
        "temperature": {
            "min": FISH_TEMPERATURE_MIN, "max": FISH_TEMPERATURE_MAX, "default": 0.7
        },
    }


@router.get("/api-keys", response_model=FishApiKeysStatus)
async def get_api_keys_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get status of API keys needed for Fish agents."""
    return FishApiKeysStatus(
        has_openai_key=bool(current_user.openai_api_key),
        has_fish_key=bool(current_user.fish_api_key),
        openai_key_preview=mask_api_key(current_user.openai_api_key),
        fish_key_preview=mask_api_key(current_user.fish_api_key),
    )


@router.put("/api-keys", response_model=FishApiKeysStatus)
async def update_api_keys(
    keys_data: FishApiKeysUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update OpenAI and/or Fish Audio API keys."""
    try:
        if keys_data.openai_api_key is not None:
            current_user.openai_api_key = keys_data.openai_api_key or None

        if keys_data.fish_api_key is not None:
            current_user.fish_api_key = keys_data.fish_api_key or None

        db.commit()
        db.refresh(current_user)

        logger.info(f"[FISH-API] API keys updated for user {current_user.id}")

        return FishApiKeysStatus(
            has_openai_key=bool(current_user.openai_api_key),
            has_fish_key=bool(current_user.fish_api_key),
            openai_key_preview=mask_api_key(current_user.openai_api_key),
            fish_key_preview=mask_api_key(current_user.fish_api_key),
        )

    except Exception as e:
        db.rollback()
        logger.error(f"[FISH-API] Error updating API keys: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update API keys",
        )


# ============================================================================
# API ENDPOINTS — CRUD
# ============================================================================

@router.get("")
async def get_fish_assistants(
    include_agent_voices: bool = Query(
        False,
        description="Показать голосовых ассистентов агентов обзвона (по умолчанию скрыты)",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all Fish assistants for the current user + API key status."""
    try:
        query = db.query(FishAssistantConfig).filter(
            FishAssistantConfig.user_id == current_user.id
        )
        if not include_agent_voices:
            query = exclude_agent_owned(query, FishAssistantConfig, db, current_user.id)
        assistants = query.order_by(FishAssistantConfig.created_at.desc()).all()

        logger.info(f"[FISH-API] Found {len(assistants)} Fish assistants for user {current_user.id}")

        return {
            "assistants": [to_response(a) for a in assistants],
            "has_openai_key": bool(current_user.openai_api_key),
            "has_fish_key": bool(current_user.fish_api_key),
        }

    except Exception as e:
        logger.error(f"[FISH-API] Error fetching assistants: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Fish assistants",
        )


@router.post("", response_model=FishAssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_fish_assistant(
    assistant_data: FishAssistantCreate,
    db: Session = Depends(get_db),
    # check_assistant_limit проверяет активность подписки + общий лимит
    # ассистентов по всем провайдерам
    current_user: User = Depends(check_assistant_limit),
):
    """Create a new Fish assistant."""
    try:
        validate_fish_model(assistant_data.fish_model)
        validate_fish_latency(assistant_data.fish_latency)

        assistant = FishAssistantConfig(
            user_id=current_user.id,
            name=assistant_data.name,
            description=assistant_data.description,
            system_prompt=assistant_data.system_prompt,
            fish_voice_id=assistant_data.fish_voice_id,
            fish_model=assistant_data.fish_model,
            fish_latency=assistant_data.fish_latency,
            sample_rate=assistant_data.sample_rate,
            voice_speed=assistant_data.voice_speed,
            llm_model=assistant_data.llm_model,
            language=assistant_data.language,
            temperature=assistant_data.temperature,
            greeting_message=assistant_data.greeting_message,
            google_sheet_id=assistant_data.google_sheet_id,
            functions=assistant_data.functions,
            is_active=True,
        )

        db.add(assistant)
        db.commit()
        db.refresh(assistant)

        logger.info(f"[FISH-API] Fish assistant created: {assistant.id} for user {current_user.id}")

        return to_response(assistant)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[FISH-API] Error creating assistant: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create Fish assistant",
        )


@router.get("/{assistant_id}", response_model=FishAssistantResponse)
async def get_fish_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific Fish assistant by ID."""
    assistant = await verify_assistant_access(
        assistant_id=assistant_id,
        user_id=str(current_user.id),
        db=db,
        require_ownership=True,
    )
    return to_response(assistant)


@router.put("/{assistant_id}", response_model=FishAssistantResponse)
async def update_fish_assistant(
    assistant_id: str,
    assistant_data: FishAssistantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a Fish assistant."""
    try:
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True,
        )

        validate_fish_model(assistant_data.fish_model)
        validate_fish_latency(assistant_data.fish_latency)

        update_data = assistant_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(assistant, field, value)

        db.commit()
        db.refresh(assistant)

        logger.info(f"[FISH-API] Fish assistant updated: {assistant_id}")

        return to_response(assistant)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[FISH-API] Error updating assistant {assistant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update Fish assistant",
        )


@router.delete("/{assistant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fish_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a Fish assistant."""
    try:
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True,
        )

        db.delete(assistant)
        db.commit()

        logger.info(f"[FISH-API] Fish assistant deleted: {assistant_id}")

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[FISH-API] Error deleting assistant {assistant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete Fish assistant",
        )
