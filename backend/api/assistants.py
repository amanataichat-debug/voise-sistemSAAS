"""
Assistant API endpoints for WellcomeAI application.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, Path
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy.sql import func

from backend.core.logging import get_logger
from backend.core.dependencies import (
    get_current_user, 
    check_subscription_active_for_assistants, 
    check_assistant_limit
)
from backend.db.session import get_db
from backend.models.user import User
from backend.models.assistant import AssistantConfig
from backend.models.pinecone_config import PineconeConfig
from backend.schemas.assistant import AssistantCreate, AssistantUpdate, AssistantResponse, EmbedCodeResponse
from backend.schemas.conversation import ConversationResponse, ConversationStats
from backend.services.assistant_service import AssistantService
from backend.services.conversation_service import ConversationService
from backend.services.user_service import UserService

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()

@router.get("/", response_model=List[AssistantResponse])
async def get_assistants(
    include_agent_voices: bool = Query(
        False,
        description="Показать голосовых ассистентов агентов обзвона (по умолчанию скрыты)",
    ),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Get all assistants for the current user.

    Args:
        include_agent_voices: включить голосовых ассистентов агентов обзвона
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency

    Returns:
        List of AssistantResponse objects
    """
    try:
        return await AssistantService.get_assistants(
            db, str(current_user.id), include_agent_voices=include_agent_voices
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_assistants: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve assistants"
        )

@router.post("/", response_model=AssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_assistant(
    assistant_data: AssistantCreate,
    current_user: User = Depends(check_assistant_limit),
    db: Session = Depends(get_db)
):
    """
    Create a new assistant.
    
    Args:
        assistant_data: Assistant creation data
        current_user: Current authenticated user (with active subscription and within limits)
        db: Database session dependency
    
    Returns:
        AssistantResponse with the new assistant information
    """
    try:
        # Дополнительная проверка подписки (check_assistant_limit уже включает её)
        subscription_status = await UserService.check_subscription_status(db, str(current_user.id))
        
        if not subscription_status["active"] and not current_user.is_admin and current_user.email != "well96well@gmail.com":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "subscription_expired",
                    "message": "Your subscription has expired. Please upgrade your subscription to create assistants.",
                    "code": "SUBSCRIPTION_EXPIRED",
                    "subscription_status": subscription_status
                }
            )
        
        return await AssistantService.create_assistant(db, str(current_user.id), assistant_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in create_assistant: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create assistant"
        )

@router.get("/{assistant_id}", response_model=AssistantResponse)
async def get_assistant(
    assistant_id: str = Path(..., description="The ID of the assistant to retrieve"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Get assistant by ID.
    
    Args:
        assistant_id: Assistant ID
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        AssistantResponse with the assistant information
    """
    try:
        assistant = await AssistantService.get_assistant_by_id(db, assistant_id, str(current_user.id))
        
        return AssistantResponse(
            id=str(assistant.id),
            user_id=str(assistant.user_id),
            name=assistant.name,
            description=assistant.description,
            system_prompt=assistant.system_prompt,
            voice=assistant.voice,
            language=assistant.language,
            google_sheet_id=assistant.google_sheet_id,
            greeting_message=assistant.greeting_message,  # ✅ ИСПРАВЛЕНО: Добавлено поле
            enable_vision=assistant.enable_vision or False,
            functions=assistant.functions,
            is_active=assistant.is_active,
            is_public=assistant.is_public,
            created_at=assistant.created_at,
            updated_at=assistant.updated_at,
            total_conversations=assistant.total_conversations,
            temperature=assistant.temperature,
            max_tokens=assistant.max_tokens
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_assistant: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve assistant"
        )

@router.put("/{assistant_id}", response_model=AssistantResponse)
async def update_assistant(
    assistant_data: AssistantUpdate,
    assistant_id: str = Path(..., description="The ID of the assistant to update"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Update an assistant.
    
    Args:
        assistant_data: Assistant update data
        assistant_id: Assistant ID
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        AssistantResponse with the updated assistant information
    """
    try:
        return await AssistantService.update_assistant(db, assistant_id, str(current_user.id), assistant_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in update_assistant: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update assistant"
        )

@router.delete("/{assistant_id}", response_model=dict)
async def delete_assistant(
    assistant_id: str = Path(..., description="The ID of the assistant to delete"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Delete an assistant.
    
    Args:
        assistant_id: Assistant ID
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        Confirmation message
    """
    try:
        await AssistantService.delete_assistant(db, assistant_id, str(current_user.id))
        return {"success": True, "message": "Assistant deleted successfully", "id": assistant_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in delete_assistant: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete assistant"
        )

@router.get("/{assistant_id}/embed-code", response_model=EmbedCodeResponse)
async def get_embed_code(
    assistant_id: str = Path(..., description="The ID of the assistant"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Get embed code for an assistant.
    
    Args:
        assistant_id: Assistant ID
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        EmbedCodeResponse with the embed code
    """
    try:
        return await AssistantService.get_embed_code(db, assistant_id, str(current_user.id))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_embed_code: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate embed code"
        )

@router.get("/{assistant_id}/conversations", response_model=List[ConversationResponse])
async def get_conversations(
    assistant_id: str = Path(..., description="The ID of the assistant"),
    skip: int = Query(0, ge=0, description="Number of conversations to skip"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of conversations to return"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Get conversations for an assistant.
    
    Args:
        assistant_id: Assistant ID
        skip: Number of conversations to skip
        limit: Maximum number of conversations to return
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        List of ConversationResponse objects
    """
    try:
        # Verify assistant belongs to user
        await AssistantService.get_assistant_by_id(db, assistant_id, str(current_user.id))
        
        return await ConversationService.get_conversations(db, assistant_id, skip, limit)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_conversations: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve conversations"
        )

@router.get("/{assistant_id}/stats", response_model=ConversationStats)
async def get_conversation_stats(
    assistant_id: str = Path(..., description="The ID of the assistant"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Get conversation statistics for an assistant.
    
    Args:
        assistant_id: Assistant ID
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        ConversationStats with statistics information
    """
    try:
        # Verify assistant belongs to user
        await AssistantService.get_assistant_by_id(db, assistant_id, str(current_user.id))
        
        return await ConversationService.get_conversation_stats(db, assistant_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_conversation_stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve conversation statistics"
        )

@router.post("/{assistant_id}/verify-sheet", response_model=Dict[str, Any])
async def verify_google_sheet(
    sheet_data: Dict[str, str],
    assistant_id: str = Path(..., description="The ID of the assistant"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Verify access to Google Sheet for an assistant.
    
    Args:
        sheet_data: Dictionary with sheet_id
        assistant_id: Assistant ID
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        Verification result
    """
    try:
        # Verify assistant belongs to user
        await AssistantService.get_assistant_by_id(db, assistant_id, str(current_user.id))
        
        sheet_id = sheet_data.get("sheet_id")
        if not sheet_id:
            return {"success": False, "message": "No sheet ID provided"}
        
        # Import service here to avoid circular imports
        from backend.services.google_sheets_service import GoogleSheetsService
        
        # Verify sheet access
        result = await GoogleSheetsService.verify_sheet_access(sheet_id)
        
        # If successful, try to set up sheet with headers
        if result.get("success"):
            await GoogleSheetsService.setup_sheet(sheet_id)
            
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in verify_google_sheet: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to verify Google Sheet: {str(e)}"
        )

# Knowledge base endpoints also require active subscription

@router.post("/{assistant_id}/knowledge-base", response_model=Dict[str, Any])
async def create_or_update_knowledge_base(
    content_data: Dict[str, str],
    assistant_id: str = Path(..., description="Assistant ID"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Create or update knowledge base for an assistant
    
    Args:
        content_data: Dictionary with content for knowledge base
        assistant_id: Assistant ID
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        Status information including namespace
    """
    try:
        # Get assistant and verify ownership
        assistant = await AssistantService.get_assistant_by_id(db, assistant_id, str(current_user.id))
        
        # Check for API key
        api_key = current_user.openai_api_key
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OpenAI API key is required for knowledge base creation"
            )
        
        content = content_data.get("content", "")
        
        # Get existing config if any
        existing_config = db.query(PineconeConfig).filter(
            PineconeConfig.assistant_id == assistant.id
        ).first()
        
        existing_namespace = existing_config.namespace if existing_config else None
        
        # Create or update knowledge base in Pinecone
        from backend.services.pinecone_service import PineconeService
        namespace, char_count = await PineconeService.create_or_update_knowledge_base(
            content=content, 
            api_key=api_key,
            namespace=existing_namespace
        )
        
        # Create or update PineconeConfig
        if existing_config:
            existing_config.namespace = namespace
            existing_config.char_count = char_count
            existing_config.content_preview = content[:200] + "..." if len(content) > 200 else content
            existing_config.updated_at = func.now()
        else:
            new_config = PineconeConfig(
                assistant_id=assistant.id,
                namespace=namespace,
                char_count=char_count,
                content_preview=content[:200] + "..." if len(content) > 200 else content
            )
            db.add(new_config)
        
        # Update system prompt to include knowledge base usage instruction
        system_prompt = assistant.system_prompt or ""
        kb_instruction = f"\nYou have access to a knowledge base with relevant information. When responding to questions, please utilize this additional context from the knowledge base (namespace: {namespace})."
        
        if "knowledge base" not in system_prompt.lower():
            system_prompt += kb_instruction
            assistant.system_prompt = system_prompt
        
        db.commit()
        
        return {
            "success": True,
            "namespace": namespace,
            "char_count": char_count,
            "message": "Knowledge base created/updated successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating/updating knowledge base: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create/update knowledge base: {str(e)}"
        )

@router.get("/{assistant_id}/knowledge-base", response_model=Dict[str, Any])
async def get_knowledge_base_status(
    assistant_id: str = Path(..., description="Assistant ID"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Get knowledge base status for an assistant
    
    Args:
        assistant_id: Assistant ID
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        Status information about the knowledge base
    """
    try:
        # Get assistant and verify ownership
        assistant = await AssistantService.get_assistant_by_id(db, assistant_id, str(current_user.id))
        
        # Get knowledge base config
        config = db.query(PineconeConfig).filter(
            PineconeConfig.assistant_id == assistant.id
        ).first()
        
        if not config:
            return {
                "has_knowledge_base": False,
                "namespace": None,
                "char_count": 0,
                "updated_at": None,
                "content_preview": None
            }
            
        return {
            "has_knowledge_base": True,
            "namespace": config.namespace,
            "char_count": config.char_count,
            "updated_at": config.updated_at,
            "content_preview": config.content_preview
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting knowledge base status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get knowledge base status: {str(e)}"
        )

@router.delete("/{assistant_id}/knowledge-base", response_model=Dict[str, Any])
async def delete_knowledge_base(
    assistant_id: str = Path(..., description="Assistant ID"),
    current_user: User = Depends(check_subscription_active_for_assistants),
    db: Session = Depends(get_db)
):
    """
    Delete knowledge base for an assistant
    
    Args:
        assistant_id: Assistant ID
        current_user: Current authenticated user (with active subscription)
        db: Database session dependency
    
    Returns:
        Status information about the deletion
    """
    try:
        # Get assistant and verify ownership
        assistant = await AssistantService.get_assistant_by_id(db, assistant_id, str(current_user.id))
        
        # Get knowledge base config
        config = db.query(PineconeConfig).filter(
            PineconeConfig.assistant_id == assistant.id
        ).first()
        
        if not config:
            return {
                "success": True,
                "message": "No knowledge base found for this assistant"
            }
        
        # Delete from Pinecone
        from backend.services.pinecone_service import PineconeService
        await PineconeService.delete_knowledge_base(config.namespace)
        
        # Delete config from database
        db.delete(config)
        db.commit()
        
        return {
            "success": True,
            "message": "Knowledge base deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting knowledge base: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete knowledge base: {str(e)}"
        )
