# backend/api/cartesia_assistants.py
"""
REST API endpoints for Cartesia voice assistants management.
Handles CRUD operations for Cartesia TTS agent configs.

Cartesia is a TTS provider — all call logic lives in Voximplant scripts.
We store the agent config and serve it via existing /config and /outbound-config endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Dict, Any
import uuid

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.models.user import User
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.core.dependencies import get_current_user, check_assistant_limit
from backend.services.assistant_limit_service import exclude_agent_owned

logger = get_logger(__name__)

router = APIRouter()


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

from pydantic import BaseModel, Field
from datetime import datetime


class CartesiaAssistantCreate(BaseModel):
    """Schema for creating a new Cartesia assistant"""
    name: str = Field(..., min_length=1, max_length=255, description="Assistant name")
    description: Optional[str] = Field(None, max_length=500, description="Assistant description")
    system_prompt: Optional[str] = Field(None, description="System instructions for assistant")
    cartesia_voice_id: Optional[str] = Field(None, description="Cartesia Voice ID from play.cartesia.ai")
    voice_speed: float = Field(default=1.0, ge=0.5, le=1.5, description="Voice speed (0.5-1.5)")
    greeting_message: Optional[str] = Field(
        default="Здравствуйте! Чем я могу вам помочь?",
        max_length=500,
        description="Greeting message"
    )
    functions: Optional[List[Dict]] = Field(default=None, description="Enabled functions config")


class CartesiaAssistantUpdate(BaseModel):
    """Schema for updating a Cartesia assistant"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    system_prompt: Optional[str] = None
    cartesia_voice_id: Optional[str] = None
    voice_speed: Optional[float] = Field(None, ge=0.5, le=1.5)
    greeting_message: Optional[str] = Field(None, max_length=500)
    functions: Optional[List[Dict]] = None
    is_active: Optional[bool] = None


class CartesiaAssistantResponse(BaseModel):
    """Schema for Cartesia assistant response"""
    id: str
    name: str
    description: Optional[str]
    system_prompt: Optional[str]
    cartesia_voice_id: Optional[str]
    voice_speed: float
    greeting_message: Optional[str]
    functions: Optional[Any]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class CartesiaApiKeysUpdate(BaseModel):
    """Schema for updating Cartesia-related API keys"""
    openai_api_key: Optional[str] = None
    cartesia_api_key: Optional[str] = None


class CartesiaApiKeysStatus(BaseModel):
    """Schema for API keys status response"""
    has_openai_key: bool
    has_cartesia_key: bool
    openai_key_preview: Optional[str] = None
    cartesia_key_preview: Optional[str] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def mask_api_key(key: Optional[str]) -> Optional[str]:
    """Return masked version of API key for display."""
    if not key or len(key) < 8:
        return None
    return f"{key[:3]}...{key[-3:]}"


async def verify_assistant_access(
    assistant_id: str,
    user_id: str,
    db: Session,
    require_ownership: bool = True
) -> CartesiaAssistantConfig:
    """Verify user has access to the assistant."""
    try:
        assistant_uuid = uuid.UUID(assistant_id)
    except ValueError:
        logger.warning(f"[CARTESIA-API] Invalid UUID format: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid assistant ID format"
        )

    assistant = db.query(CartesiaAssistantConfig).filter(
        CartesiaAssistantConfig.id == assistant_uuid
    ).first()

    if not assistant:
        logger.warning(f"[CARTESIA-API] Assistant not found: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cartesia assistant not found"
        )

    if require_ownership and str(assistant.user_id) != user_id:
        logger.warning(f"[CARTESIA-API] Unauthorized access attempt: user {user_id} -> assistant {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this assistant"
        )

    return assistant


# ============================================================================
# API ENDPOINTS — API KEYS
# ============================================================================

@router.get("/api-keys", response_model=CartesiaApiKeysStatus)
async def get_api_keys_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get status of API keys needed for Cartesia agents."""
    return CartesiaApiKeysStatus(
        has_openai_key=bool(current_user.openai_api_key),
        has_cartesia_key=bool(current_user.cartesia_api_key),
        openai_key_preview=mask_api_key(current_user.openai_api_key),
        cartesia_key_preview=mask_api_key(current_user.cartesia_api_key),
    )


@router.put("/api-keys", response_model=CartesiaApiKeysStatus)
async def update_api_keys(
    keys_data: CartesiaApiKeysUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update OpenAI and/or Cartesia API keys."""
    try:
        if keys_data.openai_api_key is not None:
            if keys_data.openai_api_key == "":
                current_user.openai_api_key = None
            else:
                current_user.openai_api_key = keys_data.openai_api_key

        if keys_data.cartesia_api_key is not None:
            if keys_data.cartesia_api_key == "":
                current_user.cartesia_api_key = None
            else:
                current_user.cartesia_api_key = keys_data.cartesia_api_key

        db.commit()
        db.refresh(current_user)

        logger.info(f"[CARTESIA-API] API keys updated for user {current_user.id}")

        return CartesiaApiKeysStatus(
            has_openai_key=bool(current_user.openai_api_key),
            has_cartesia_key=bool(current_user.cartesia_api_key),
            openai_key_preview=mask_api_key(current_user.openai_api_key),
            cartesia_key_preview=mask_api_key(current_user.cartesia_api_key),
        )

    except Exception as e:
        db.rollback()
        logger.error(f"[CARTESIA-API] Error updating API keys: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update API keys"
        )


# ============================================================================
# API ENDPOINTS — CRUD
# ============================================================================

@router.get("")
async def get_cartesia_assistants(
    include_agent_voices: bool = Query(
        False,
        description="Показать голосовых ассистентов агентов обзвона (по умолчанию скрыты)",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all Cartesia assistants for the current user.
    Returns assistants list + API key status.
    """
    try:
        logger.info(f"[CARTESIA-API] Fetching assistants for user {current_user.id}")

        query = db.query(CartesiaAssistantConfig).filter(
            CartesiaAssistantConfig.user_id == current_user.id
        )
        if not include_agent_voices:
            query = exclude_agent_owned(query, CartesiaAssistantConfig, db, current_user.id)
        assistants = query.order_by(CartesiaAssistantConfig.created_at.desc()).all()

        logger.info(f"[CARTESIA-API] Found {len(assistants)} Cartesia assistants")

        result = []
        for assistant in assistants:
            result.append(CartesiaAssistantResponse(
                id=str(assistant.id),
                name=assistant.name,
                description=assistant.description,
                system_prompt=assistant.system_prompt,
                cartesia_voice_id=assistant.cartesia_voice_id,
                voice_speed=assistant.voice_speed,
                greeting_message=assistant.greeting_message,
                functions=assistant.functions,
                is_active=assistant.is_active,
                created_at=assistant.created_at,
            ))

        return {
            "assistants": result,
            "has_openai_key": bool(current_user.openai_api_key),
            "has_cartesia_key": bool(current_user.cartesia_api_key),
        }

    except Exception as e:
        logger.error(f"[CARTESIA-API] Error fetching assistants: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Cartesia assistants"
        )


@router.get("/{assistant_id}", response_model=CartesiaAssistantResponse)
async def get_cartesia_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific Cartesia assistant by ID."""
    try:
        logger.info(f"[CARTESIA-API] Fetching assistant {assistant_id} for user {current_user.id}")

        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        return CartesiaAssistantResponse(
            id=str(assistant.id),
            name=assistant.name,
            description=assistant.description,
            system_prompt=assistant.system_prompt,
            cartesia_voice_id=assistant.cartesia_voice_id,
            voice_speed=assistant.voice_speed,
            greeting_message=assistant.greeting_message,
            functions=assistant.functions,
            is_active=assistant.is_active,
            created_at=assistant.created_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CARTESIA-API] Error fetching assistant {assistant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Cartesia assistant"
        )


@router.post("", response_model=CartesiaAssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_cartesia_assistant(
    assistant_data: CartesiaAssistantCreate,
    db: Session = Depends(get_db),
    # check_assistant_limit проверяет активность подписки + общий лимит
    # ассистентов по всем провайдерам
    current_user: User = Depends(check_assistant_limit)
):
    """Create a new Cartesia assistant."""
    try:
        logger.info(f"[CARTESIA-API] Creating Cartesia assistant for user {current_user.id}")
        logger.info(f"[CARTESIA-API] Assistant name: {assistant_data.name}")

        # Create assistant instance
        assistant = CartesiaAssistantConfig(
            user_id=current_user.id,
            name=assistant_data.name,
            description=assistant_data.description,
            system_prompt=assistant_data.system_prompt,
            cartesia_voice_id=assistant_data.cartesia_voice_id,
            voice_speed=assistant_data.voice_speed,
            greeting_message=assistant_data.greeting_message,
            functions=assistant_data.functions,
            is_active=True,
        )

        db.add(assistant)
        db.commit()
        db.refresh(assistant)

        logger.info(f"[CARTESIA-API] Cartesia assistant created: {assistant.id}")

        return CartesiaAssistantResponse(
            id=str(assistant.id),
            name=assistant.name,
            description=assistant.description,
            system_prompt=assistant.system_prompt,
            cartesia_voice_id=assistant.cartesia_voice_id,
            voice_speed=assistant.voice_speed,
            greeting_message=assistant.greeting_message,
            functions=assistant.functions,
            is_active=assistant.is_active,
            created_at=assistant.created_at,
        )

    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        logger.error(f"[CARTESIA-API] Database integrity error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create assistant due to database constraint"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[CARTESIA-API] Error creating assistant: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create Cartesia assistant"
        )


@router.put("/{assistant_id}", response_model=CartesiaAssistantResponse)
async def update_cartesia_assistant(
    assistant_id: str,
    assistant_data: CartesiaAssistantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a Cartesia assistant."""
    try:
        logger.info(f"[CARTESIA-API] Updating assistant {assistant_id} for user {current_user.id}")

        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        # Update only provided fields
        update_data = assistant_data.dict(exclude_unset=True)

        for key, value in update_data.items():
            setattr(assistant, key, value)

        db.commit()
        db.refresh(assistant)

        logger.info(f"[CARTESIA-API] Cartesia assistant updated: {assistant_id}")

        return CartesiaAssistantResponse(
            id=str(assistant.id),
            name=assistant.name,
            description=assistant.description,
            system_prompt=assistant.system_prompt,
            cartesia_voice_id=assistant.cartesia_voice_id,
            voice_speed=assistant.voice_speed,
            greeting_message=assistant.greeting_message,
            functions=assistant.functions,
            is_active=assistant.is_active,
            created_at=assistant.created_at,
        )

    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        logger.error(f"[CARTESIA-API] Database integrity error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to update assistant due to database constraint"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[CARTESIA-API] Error updating assistant {assistant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update Cartesia assistant"
        )


@router.delete("/{assistant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cartesia_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a Cartesia assistant."""
    try:
        logger.info(f"[CARTESIA-API] Deleting assistant {assistant_id} for user {current_user.id}")

        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        db.delete(assistant)
        db.commit()

        logger.info(f"[CARTESIA-API] Cartesia assistant deleted: {assistant_id}")

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[CARTESIA-API] Error deleting assistant {assistant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete Cartesia assistant"
        )
