# backend/api/gemini_assistants.py
"""
REST API endpoints for Google Gemini assistants management.
Handles CRUD operations for Gemini Live API voice assistants.

🚀 PRODUCTION VERSION 1.2
✅ Complete CRUD operations
✅ Authorization validation
✅ Subscription validation
✅ Error handling
✅ Fixed routing (removed duplicate prefix)
✅ FIX: Explicit delete of related records before assistant deletion
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
import uuid

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.db.session import get_db
from backend.models.user import User
from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
from backend.core.dependencies import get_current_user, check_assistant_limit
from backend.services.assistant_limit_service import exclude_agent_owned

logger = get_logger(__name__)

router = APIRouter()


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

from pydantic import BaseModel, Field
from datetime import datetime


class GeminiAssistantCreate(BaseModel):
    """Schema for creating a new Gemini assistant"""
    name: str = Field(..., min_length=1, max_length=100, description="Assistant name")
    description: Optional[str] = Field(None, max_length=500, description="Assistant description")
    system_prompt: str = Field(..., min_length=1, description="System instructions for assistant")
    voice: str = Field(default="Aoede", description="Gemini voice name")
    language: str = Field(default="ru", description="Language code")
    greeting_message: Optional[str] = Field(
        default="Здравствуйте! Чем я могу вам помочь?",
        max_length=200,
        description="Greeting message"
    )
    google_sheet_id: Optional[str] = Field(None, description="Google Sheets ID for logging")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Model temperature")
    max_tokens: int = Field(default=4000, ge=1, le=8192, description="Maximum tokens")
    functions: Optional[list] = Field(default=None, description="Enabled functions")
    enable_thinking: bool = Field(default=False, description="Enable thinking mode")
    thinking_budget: Optional[int] = Field(default=1024, ge=128, le=4096, description="Thinking token budget")
    enable_screen_context: bool = Field(default=False, description="Enable screen context")
    model: str = Field(default="models/gemini-2.5-flash-native-audio-preview-12-2025", description="Gemini model name")


class GeminiAssistantUpdate(BaseModel):
    """Schema for updating a Gemini assistant"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    system_prompt: Optional[str] = Field(None, min_length=1)
    voice: Optional[str] = None
    language: Optional[str] = None
    greeting_message: Optional[str] = Field(None, max_length=200)
    google_sheet_id: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=8192)
    functions: Optional[list] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    enable_thinking: Optional[bool] = None
    thinking_budget: Optional[int] = Field(None, ge=128, le=4096)
    enable_screen_context: Optional[bool] = None
    model: Optional[str] = None


class GeminiAssistantResponse(BaseModel):
    """Schema for Gemini assistant response"""
    id: str
    user_id: str
    name: str
    description: Optional[str]
    system_prompt: str
    voice: str
    language: str
    greeting_message: Optional[str]
    google_sheet_id: Optional[str]
    functions: Optional[list]
    is_active: bool
    is_public: bool
    created_at: datetime
    updated_at: datetime
    total_conversations: int
    temperature: float
    max_tokens: int
    enable_thinking: bool
    thinking_budget: Optional[int]
    enable_screen_context: bool
    model: str

    class Config:
        from_attributes = True


class EmbedCodeResponse(BaseModel):
    """Schema for embed code response"""
    embed_code: str
    assistant_id: str


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def verify_assistant_access(
    assistant_id: str,
    user_id: str,
    db: Session,
    require_ownership: bool = True
) -> GeminiAssistantConfig:
    """
    Verify user has access to the assistant.
    
    Args:
        assistant_id: Assistant UUID
        user_id: User UUID
        db: Database session
        require_ownership: If True, requires user to be owner
        
    Returns:
        GeminiAssistantConfig object
        
    Raises:
        HTTPException: If assistant not found or access denied
    """
    try:
        assistant_uuid = uuid.UUID(assistant_id)
    except ValueError:
        logger.warning(f"[GEMINI-API] Invalid UUID format: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid assistant ID format"
        )
    
    assistant = db.query(GeminiAssistantConfig).filter(
        GeminiAssistantConfig.id == assistant_uuid
    ).first()
    
    if not assistant:
        logger.warning(f"[GEMINI-API] Assistant not found: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gemini assistant not found"
        )
    
    if require_ownership and str(assistant.user_id) != user_id:
        logger.warning(f"[GEMINI-API] Unauthorized access attempt: user {user_id} -> assistant {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this assistant"
        )
    
    return assistant


def build_assistant_response(assistant: GeminiAssistantConfig) -> GeminiAssistantResponse:
    """Build response object from assistant model."""
    return GeminiAssistantResponse(
        id=str(assistant.id),
        user_id=str(assistant.user_id),
        name=assistant.name,
        description=assistant.description,
        system_prompt=assistant.system_prompt,
        voice=assistant.voice,
        language=assistant.language,
        greeting_message=assistant.greeting_message,
        google_sheet_id=assistant.google_sheet_id,
        functions=assistant.functions,
        is_active=assistant.is_active,
        is_public=assistant.is_public,
        created_at=assistant.created_at,
        updated_at=assistant.updated_at,
        total_conversations=assistant.total_conversations,
        temperature=assistant.temperature,
        max_tokens=assistant.max_tokens,
        enable_thinking=assistant.enable_thinking,
        thinking_budget=assistant.thinking_budget,
        enable_screen_context=assistant.enable_screen_context,
        model=assistant.model
    )


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.get("", response_model=List[GeminiAssistantResponse])
async def get_gemini_assistants(
    include_agent_voices: bool = Query(
        False,
        description="Показать голосовых ассистентов агентов обзвона (по умолчанию скрыты)",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all Gemini assistants for the current user.

    Returns:
        List of Gemini assistant configurations
    """
    try:
        logger.info(f"[GEMINI-API] Fetching assistants for user {current_user.id}")

        query = db.query(GeminiAssistantConfig).filter(
            GeminiAssistantConfig.user_id == current_user.id
        )
        if not include_agent_voices:
            query = exclude_agent_owned(query, GeminiAssistantConfig, db, current_user.id)
        assistants = query.order_by(GeminiAssistantConfig.created_at.desc()).all()
        
        logger.info(f"[GEMINI-API] Found {len(assistants)} Gemini assistants")
        
        return [build_assistant_response(a) for a in assistants]
        
    except Exception as e:
        logger.error(f"[GEMINI-API] Error fetching assistants: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Gemini assistants"
        )


@router.get("/{assistant_id}", response_model=GeminiAssistantResponse)
async def get_gemini_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific Gemini assistant by ID.
    
    Args:
        assistant_id: Assistant UUID
        
    Returns:
        Gemini assistant configuration
    """
    try:
        logger.info(f"[GEMINI-API] Fetching assistant {assistant_id} for user {current_user.id}")
        
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )
        
        return build_assistant_response(assistant)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GEMINI-API] Error fetching assistant {assistant_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Gemini assistant"
        )


@router.post("", response_model=GeminiAssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_gemini_assistant(
    assistant_data: GeminiAssistantCreate,
    db: Session = Depends(get_db),
    # check_assistant_limit проверяет активность подписки + общий лимит
    # ассистентов по всем провайдерам
    current_user: User = Depends(check_assistant_limit)
):
    """
    Create a new Gemini assistant.
    
    Args:
        assistant_data: Assistant creation data
        
    Returns:
        Created Gemini assistant configuration
    """
    try:
        logger.info(f"[GEMINI-API] Creating Gemini assistant for user {current_user.id}")
        logger.info(f"[GEMINI-API] Assistant name: {assistant_data.name}")
        logger.info(f"[GEMINI-API] Voice: {assistant_data.voice}")
        logger.info(f"[GEMINI-API] Thinking enabled: {assistant_data.enable_thinking}")
        
        # Create assistant instance
        assistant = GeminiAssistantConfig(
            user_id=current_user.id,
            name=assistant_data.name,
            description=assistant_data.description,
            system_prompt=assistant_data.system_prompt,
            voice=assistant_data.voice,
            language=assistant_data.language,
            greeting_message=assistant_data.greeting_message,
            google_sheet_id=assistant_data.google_sheet_id,
            temperature=assistant_data.temperature,
            max_tokens=assistant_data.max_tokens,
            functions=assistant_data.functions,
            is_active=True,
            is_public=False,
            enable_thinking=assistant_data.enable_thinking,
            thinking_budget=assistant_data.thinking_budget,
            enable_screen_context=assistant_data.enable_screen_context,
            model=assistant_data.model
        )
        
        db.add(assistant)
        db.commit()
        db.refresh(assistant)
        
        logger.info(f"[GEMINI-API] ✅ Gemini assistant created: {assistant.id}")
        
        return build_assistant_response(assistant)
        
    except IntegrityError as e:
        db.rollback()
        logger.error(f"[GEMINI-API] Database integrity error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create assistant due to database constraint"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[GEMINI-API] Error creating assistant: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create Gemini assistant"
        )


@router.put("/{assistant_id}", response_model=GeminiAssistantResponse)
async def update_gemini_assistant(
    assistant_id: str,
    assistant_data: GeminiAssistantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update a Gemini assistant.
    
    Args:
        assistant_id: Assistant UUID
        assistant_data: Assistant update data
        
    Returns:
        Updated Gemini assistant configuration
    """
    try:
        logger.info(f"[GEMINI-API] Updating assistant {assistant_id} for user {current_user.id}")
        
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
        
        logger.info(f"[GEMINI-API] ✅ Gemini assistant updated: {assistant_id}")
        
        return build_assistant_response(assistant)
        
    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        logger.error(f"[GEMINI-API] Database integrity error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to update assistant due to database constraint"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[GEMINI-API] Error updating assistant {assistant_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update Gemini assistant"
        )


@router.delete("/{assistant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gemini_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a Gemini assistant and all related records.
    
    Args:
        assistant_id: Assistant UUID
        
    Returns:
        No content (204)
    """
    try:
        logger.info(f"[GEMINI-API] Deleting assistant {assistant_id} for user {current_user.id}")

        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        # ✅ FIX: Запрещаем удаление, если ассистент является голосом агента обзвона.
        # Иначе пользователь молча ломает агента (или, при старой схеме FK,
        # каскадом сносит его целиком). Просим сначала отвязать через карточку агента.
        from backend.models.agent_config import AgentConfig
        bound_agent = db.query(AgentConfig).filter(
            AgentConfig.gemini_assistant_id == assistant.id
        ).first()
        if bound_agent:
            logger.warning(
                f"[GEMINI-API] Blocked deletion of {assistant_id}: "
                f"used by agent {bound_agent.id} ({bound_agent.name})"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Этот ассистент используется агентом «{bound_agent.name}». "
                    f"Сначала смените голос агента или удалите агента."
                )
            )

        # Явно (bulk) удаляем связанные conversations — быстрее, чем ORM-каскад
        # по одной записи. Остальное (FK gemini_conversations) подстрахует БД.
        deleted_convs = db.query(GeminiConversation).filter(
            GeminiConversation.assistant_id == assistant.id
        ).delete(synchronize_session=False)
        logger.info(f"[GEMINI-API] Deleted {deleted_convs} related conversations")

        # Обнуляем ссылки в tasks (на уровне БД FK тоже стоит ON DELETE SET NULL,
        # это явное действие для наглядности и совместимости со старой схемой).
        from backend.models.task import Task
        updated_tasks = db.query(Task).filter(
            Task.gemini_assistant_id == assistant.id
        ).update(
            {Task.gemini_assistant_id: None},
            synchronize_session=False
        )
        if updated_tasks:
            logger.info(f"[GEMINI-API] Cleared {updated_tasks} task references")

        # Удаляем сам ассистент
        db.delete(assistant)
        db.commit()

        logger.info(f"[GEMINI-API] ✅ Gemini assistant deleted: {assistant_id}")

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[GEMINI-API] Error deleting assistant {assistant_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete Gemini assistant: {str(e)}"
        )


@router.get("/{assistant_id}/embed-code", response_model=EmbedCodeResponse)
async def get_gemini_embed_code(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get embed code for a Gemini assistant widget.
    
    Args:
        assistant_id: Assistant UUID
        
    Returns:
        Embed code snippet
    """
    try:
        logger.info(f"[GEMINI-API] Getting embed code for assistant {assistant_id}")
        
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )
        
        if not assistant.is_active:
            logger.warning(f"[GEMINI-API] Assistant {assistant_id} is not active")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assistant must be active to generate embed code"
            )
        
        # Generate embed code
        host_url = settings.HOST_URL
        embed_code = f"""<!-- WellcomeAI Gemini Voice Assistant -->
<script>
    (function() {{
        var script = document.createElement('script');
        script.src = '{host_url}/static/gemini-widget.js';
        script.dataset.assistantId = '{assistant_id}';
        script.dataset.server = '{host_url}';
        script.dataset.position = 'bottom-right';
        script.async = true;
        document.head.appendChild(script);
    }})();
</script>
<!-- End WellcomeAI Gemini -->"""
        
        logger.info(f"[GEMINI-API] ✅ Embed code generated for assistant {assistant_id}")
        
        return EmbedCodeResponse(
            embed_code=embed_code,
            assistant_id=assistant_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GEMINI-API] Error generating embed code: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate embed code"
        )


@router.post("/{assistant_id}/verify-sheet")
async def verify_gemini_google_sheet(
    assistant_id: str,
    sheet_data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Verify Google Sheets connection for a Gemini assistant.
    
    Args:
        assistant_id: Assistant UUID
        sheet_data: Dict with sheet_id
        
    Returns:
        Verification result
    """
    try:
        logger.info(f"[GEMINI-API] Verifying Google Sheet for assistant {assistant_id}")
        
        sheet_id = sheet_data.get("sheet_id")
        if not sheet_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sheet_id is required"
            )
        
        # Verify assistant access
        if assistant_id != "new":
            await verify_assistant_access(
                assistant_id=assistant_id,
                user_id=str(current_user.id),
                db=db,
                require_ownership=True
            )
        
        # Try to verify sheet access
        from backend.services.google_sheets_service import GoogleSheetsService
        
        result = await GoogleSheetsService.verify_sheet_access(sheet_id)
        
        if result:
            logger.info(f"[GEMINI-API] ✅ Google Sheet verified: {sheet_id[:20]}...")
            return {
                "success": True,
                "message": "Google Sheet подключена успешно"
            }
        else:
            logger.warning(f"[GEMINI-API] ❌ Google Sheet verification failed: {sheet_id[:20]}...")
            return {
                "success": False,
                "message": "Не удалось подключиться к таблице. Проверьте права доступа."
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GEMINI-API] Error verifying Google Sheet: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Ошибка проверки: {str(e)}"
        }
