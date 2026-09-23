"""
Knowledge Base API endpoints for WellcomeAI application.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Path
from sqlalchemy.orm import Session
import uuid
from typing import Dict, Any, Optional, List

from backend.core.logging import get_logger
from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.models.assistant import AssistantConfig
from backend.models.pinecone_config import PineconeConfig
from sqlalchemy.sql import func

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter(tags=["Knowledge Base"])

@router.get("/", response_model=Dict[str, Any])
async def get_knowledge_base_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get knowledge base status for the current user
    
    Args:
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Status information about the knowledge base
    """
    try:
        # Получаем все ID ассистентов пользователя
        assistant_ids = db.query(AssistantConfig.id).filter(
            AssistantConfig.user_id == current_user.id
        ).all()
        assistant_ids = [aid[0] for aid in assistant_ids]
        
        # Ищем конфигурацию для любого ассистента пользователя
        config = None
        if assistant_ids:
            config = db.query(PineconeConfig).filter(
                PineconeConfig.assistant_id.in_(assistant_ids)
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
            "content_preview": config.content_preview,
            "name": config.name,
            "id": str(config.id)
        }
        
    except Exception as e:
        logger.error(f"Error getting knowledge base status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get knowledge base status: {str(e)}"
        )

@router.get("/all", response_model=List[Dict[str, Any]])
async def get_all_knowledge_bases(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all knowledge bases for the current user
    
    Args:
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        List of knowledge bases
    """
    try:
        # Get all assistant IDs belonging to the user
        assistant_ids = db.query(AssistantConfig.id).filter(
            AssistantConfig.user_id == current_user.id
        ).all()
        assistant_ids = [aid[0] for aid in assistant_ids]
        
        # Get all knowledge bases linked to user's assistants
        configs = []
        if assistant_ids:
            configs = db.query(PineconeConfig).filter(
                PineconeConfig.assistant_id.in_(assistant_ids)
            ).order_by(PineconeConfig.updated_at.desc()).all()
        
        result = []
        for config in configs:
            result.append({
                "id": str(config.id),
                "namespace": config.namespace,
                "name": config.name or f"KB-{config.namespace[-6:]}",
                "char_count": config.char_count,
                "updated_at": config.updated_at,
                "content_preview": config.content_preview,
                "assistant_id": str(config.assistant_id) if config.assistant_id else None
            })
            
        return result
    except Exception as e:
        logger.error(f"Error getting all knowledge bases: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get knowledge bases: {str(e)}"
        )

@router.post("/", response_model=Dict[str, Any])
async def create_or_update_knowledge_base(
    content_data: Dict[str, str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create or update knowledge base for the current user
    
    Args:
        content_data: Dictionary with content for knowledge base
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Status information including namespace
    """
    try:
        # Check for API key
        api_key = current_user.openai_api_key
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OpenAI API key is required for knowledge base creation"
            )
        
        content = content_data.get("content", "")
        name = content_data.get("name", "Knowledge Base")
        
        # Получаем все ID ассистентов пользователя
        assistant_ids = db.query(AssistantConfig.id).filter(
            AssistantConfig.user_id == current_user.id
        ).all()
        assistant_ids = [aid[0] for aid in assistant_ids]
        
        # Проверяем, есть ли у пользователя ассистенты
        if not assistant_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Необходимо сначала создать ассистента"
            )
        
        # Ищем существующую конфигурацию
        existing_config = None
        if assistant_ids:
            existing_config = db.query(PineconeConfig).filter(
                PineconeConfig.assistant_id.in_(assistant_ids)
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
            existing_config.full_content = content
            existing_config.name = name
            existing_config.updated_at = func.now()
        else:
            # Берем первого ассистента пользователя для привязки
            assistant_id = assistant_ids[0]
            
            new_config = PineconeConfig(
                assistant_id=assistant_id,
                namespace=namespace,
                char_count=char_count,
                content_preview=content[:200] + "..." if len(content) > 200 else content,
                full_content=content,
                name=name
            )
            db.add(new_config)
        
        db.commit()
        
        return {
            "success": True,
            "namespace": namespace,
            "char_count": char_count,
            "message": "Knowledge base created/updated successfully",
            "id": str(existing_config.id) if existing_config else str(new_config.id),
            "name": name
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

@router.post("/new", response_model=Dict[str, Any])
async def create_new_knowledge_base(
    content_data: Dict[str, str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new knowledge base for the current user
    
    Args:
        content_data: Dictionary with content for knowledge base
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Status information including namespace
    """
    try:
        # Check for API key
        api_key = current_user.openai_api_key
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OpenAI API key is required for knowledge base creation"
            )
        
        content = content_data.get("content", "")
        name = content_data.get("name", "Knowledge Base")
        
        # Получаем все ID ассистентов пользователя
        assistant_ids = db.query(AssistantConfig.id).filter(
            AssistantConfig.user_id == current_user.id
        ).all()
        assistant_ids = [aid[0] for aid in assistant_ids]
        
        # Проверяем, есть ли у пользователя ассистенты
        if not assistant_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Необходимо сначала создать ассистента"
            )
        
        # Create new knowledge base in Pinecone with fresh namespace
        from backend.services.pinecone_service import PineconeService
        namespace, char_count = await PineconeService.create_or_update_knowledge_base(
            content=content, 
            api_key=api_key,
            namespace=None  # Всегда передаем None, чтобы создать новую БЗ
        )
        
        # Берем первого ассистента пользователя для привязки
        assistant_id = assistant_ids[0]
        
        # Create new PineconeConfig
        new_config = PineconeConfig(
            assistant_id=assistant_id,
            namespace=namespace,
            char_count=char_count,
            content_preview=content[:200] + "..." if len(content) > 200 else content,
            full_content=content,
            name=name
        )
        db.add(new_config)
        db.commit()
        
        return {
            "success": True,
            "namespace": namespace,
            "char_count": char_count,
            "message": "New knowledge base created successfully",
            "id": str(new_config.id),
            "name": name
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating new knowledge base: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create new knowledge base: {str(e)}"
        )

@router.delete("/", response_model=Dict[str, Any])
async def delete_knowledge_base(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete knowledge base for the current user
    
    Args:
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Status information about the deletion
    """
    try:
        # Получаем все ID ассистентов пользователя
        assistant_ids = db.query(AssistantConfig.id).filter(
            AssistantConfig.user_id == current_user.id
        ).all()
        assistant_ids = [aid[0] for aid in assistant_ids]
        
        # Ищем конфигурацию для любого ассистента пользователя
        config = None
        if assistant_ids:
            config = db.query(PineconeConfig).filter(
                PineconeConfig.assistant_id.in_(assistant_ids)
            ).first()
        
        if not config:
            return {
                "success": True,
                "message": "No knowledge base found for this user"
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
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting knowledge base: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete knowledge base: {str(e)}"
        )

@router.get("/{kb_id}/content", response_model=Dict[str, Any])
async def get_knowledge_base_content(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get full content of a knowledge base
    
    Args:
        kb_id: Knowledge base ID
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Knowledge base content
    """
    try:
        config = db.query(PineconeConfig).filter(PineconeConfig.id == kb_id).first()
        
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Knowledge base not found"
            )
            
        if not _owns_config(config, current_user, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this knowledge base"
            )
            
        return {
            "id": str(config.id),
            "full_content": config.full_content or "",
            "namespace": config.namespace,
            "name": config.name or f"KB-{config.namespace[-6:]}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting knowledge base content: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get knowledge base content: {str(e)}"
        )

@router.put("/{kb_id}", response_model=Dict[str, Any])
async def update_knowledge_base(
    kb_id: str,
    content_data: Dict[str, str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update an existing knowledge base
    
    Args:
        kb_id: Knowledge base ID
        content_data: Dictionary with content for knowledge base
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Status information
    """
    try:
        config = db.query(PineconeConfig).filter(PineconeConfig.id == kb_id).first()
        
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Knowledge base not found"
            )
            
        if not _owns_config(config, current_user, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this knowledge base"
            )
            
        content = content_data.get("content", "")
        name = content_data.get("name", "Knowledge Base")
        
        # Check for API key
        api_key = current_user.openai_api_key
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OpenAI API key is required for knowledge base creation"
            )
        
        # Update embeddings in Pinecone
        from backend.services.pinecone_service import PineconeService
        namespace, char_count = await PineconeService.create_or_update_knowledge_base(
            content=content, 
            api_key=api_key,
            namespace=config.namespace
        )
        
        # Update database record
        config.namespace = namespace
        config.char_count = char_count
        config.content_preview = content[:200] + "..." if len(content) > 200 else content
        config.full_content = content
        config.name = name
        config.updated_at = func.now()
        
        db.commit()
        
        return {
            "success": True,
            "namespace": namespace,
            "char_count": char_count,
            "message": "Knowledge base updated successfully",
            "id": str(config.id),
            "name": name
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating knowledge base: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update knowledge base: {str(e)}"
        )

@router.delete("/{kb_id}", response_model=Dict[str, Any])
async def delete_specific_knowledge_base(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a specific knowledge base
    
    Args:
        kb_id: Knowledge base ID
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Status information
    """
    try:
        config = db.query(PineconeConfig).filter(PineconeConfig.id == kb_id).first()
        
        if not config:
            return {
                "success": True,
                "message": "Knowledge base not found or already deleted"
            }
            
        if not _owns_config(config, current_user, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to delete this knowledge base"
            )
            
        # Delete from Pinecone
        from backend.services.pinecone_service import PineconeService
        await PineconeService.delete_knowledge_base(config.namespace)
        
        # Delete from database
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


# ============================================================================
# Индивидуальная база знаний ассистента
# ----------------------------------------------------------------------------
# Одна база на ассистента: PineconeConfig.owner_type + owner_id (UUID
# ассистента), владелец — PineconeConfig.user_id. Функция search_pinecone
# находит базу по id ассистента, который ведёт разговор (виджет и телефония),
# namespace в промпт вписывать не нужно. База агента обзвона живёт отдельно —
# AgentConfig.kb_namespace (backend/api/agent.py).
# ============================================================================

def _assistant_models() -> Dict[str, Any]:
    from backend.models.eleven_assistant import ElevenAssistantConfig
    from backend.models.fish_assistant import FishAssistantConfig
    from backend.models.gemini_assistant import GeminiAssistantConfig
    return {
        "eleven": ElevenAssistantConfig,
        "fish": FishAssistantConfig,
        "gemini": GeminiAssistantConfig,
        "openai": AssistantConfig,
    }


def _owns_config(config: PineconeConfig, current_user: User, db: Session) -> bool:
    """Новые базы принадлежат пользователю (user_id), легаси — через его OpenAI-ассистента."""
    if config.user_id is not None:
        return str(config.user_id) == str(current_user.id)
    if config.assistant_id is None:
        return False
    return db.query(AssistantConfig.id).filter(
        AssistantConfig.id == config.assistant_id,
        AssistantConfig.user_id == current_user.id,
    ).first() is not None


def _get_owned_assistant(owner_type: str, owner_id: str, current_user: User, db: Session):
    model = _assistant_models().get(owner_type)
    if model is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неизвестный тип ассистента")
    try:
        assistant_uuid = uuid.UUID(owner_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID ассистента")
    assistant = db.query(model).filter(model.id == assistant_uuid).first()
    if not assistant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ассистент не найден")
    if str(assistant.user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к ассистенту")
    return assistant


def _assistant_kb(owner_type: str, owner_id: str, db: Session) -> Optional[PineconeConfig]:
    return db.query(PineconeConfig).filter(
        PineconeConfig.owner_type == owner_type,
        PineconeConfig.owner_id == owner_id,
    ).first()


def _embedding_key(current_user: User) -> str:
    """Эмбеддинги: ключ пользователя, иначе серверный (ElevenLabs/Fish работают на серверных ключах)."""
    from backend.core.config import settings
    api_key = current_user.openai_api_key or settings.OPENAI_API_KEY
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не настроен ключ OpenAI для построения базы знаний",
        )
    return api_key


def _kb_payload(config: Optional[PineconeConfig]) -> Dict[str, Any]:
    if not config:
        return {"has_knowledge_base": False}
    return {
        "has_knowledge_base": True,
        "id": str(config.id),
        "name": config.name or f"KB-{config.namespace[-6:]}",
        "namespace": config.namespace,
        "char_count": config.char_count,
        "updated_at": config.updated_at,
        "content_preview": config.content_preview,
    }


@router.get("/assistant/{owner_type}/{owner_id}", response_model=Dict[str, Any])
async def get_assistant_knowledge_base(
    owner_type: str,
    owner_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """База знаний конкретного ассистента (или has_knowledge_base=false)."""
    _get_owned_assistant(owner_type, owner_id, current_user, db)
    config = _assistant_kb(owner_type, owner_id, db)
    payload = _kb_payload(config)
    if config:
        payload["full_content"] = config.full_content or ""
    return payload


@router.put("/assistant/{owner_type}/{owner_id}", response_model=Dict[str, Any])
async def save_assistant_knowledge_base(
    owner_type: str,
    owner_id: str,
    content_data: Dict[str, str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создать или обновить базу знаний ассистента (одна база на ассистента)."""
    _get_owned_assistant(owner_type, owner_id, current_user, db)
    content = (content_data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Добавьте содержимое базы")
    name = (content_data.get("name") or "").strip()[:100] or "База знаний"
    api_key = _embedding_key(current_user)

    config = _assistant_kb(owner_type, owner_id, db)
    try:
        from backend.services.pinecone_service import PineconeService
        namespace, char_count = await PineconeService.create_or_update_knowledge_base(
            content=content,
            api_key=api_key,
            namespace=config.namespace if config else None,
        )
        preview = content[:200] + "..." if len(content) > 200 else content
        if config:
            config.namespace = namespace
            config.char_count = char_count
            config.content_preview = preview
            config.full_content = content
            config.name = name
            config.updated_at = func.now()
        else:
            config = PineconeConfig(
                user_id=current_user.id,
                owner_type=owner_type,
                owner_id=owner_id,
                namespace=namespace,
                char_count=char_count,
                content_preview=preview,
                full_content=content,
                name=name,
            )
            db.add(config)
        db.commit()
        db.refresh(config)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving assistant knowledge base {owner_type}/{owner_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось сохранить базу знаний: {e}",
        )
    return _kb_payload(config)


@router.delete("/assistant/{owner_type}/{owner_id}", response_model=Dict[str, Any])
async def delete_assistant_knowledge_base(
    owner_type: str,
    owner_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Удалить базу знаний ассистента (namespace в Pinecone и запись)."""
    _get_owned_assistant(owner_type, owner_id, current_user, db)
    config = _assistant_kb(owner_type, owner_id, db)
    if not config:
        return {"success": True}
    await delete_assistant_kb_record(config, db)
    return {"success": True}


async def delete_assistant_kb_record(config: PineconeConfig, db: Session) -> None:
    """Удаляет namespace из Pinecone и запись базы. Используется и при удалении ассистента."""
    from backend.services.pinecone_service import PineconeService
    try:
        await PineconeService.delete_knowledge_base(config.namespace)
    except Exception as e:
        logger.warning(f"Pinecone namespace {config.namespace} not deleted: {e}")
    db.delete(config)
    db.commit()
