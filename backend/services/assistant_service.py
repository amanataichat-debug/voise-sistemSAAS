"""
Assistant service for WellcomeAI application.
Handles assistant management operations.
"""

import uuid
import os
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Dict, Any

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.models.assistant import AssistantConfig
from backend.models.user import User
from backend.schemas.assistant import AssistantCreate, AssistantUpdate, AssistantResponse, EmbedCodeResponse

logger = get_logger(__name__)

class AssistantService:
    """Service for assistant operations"""
    
    @staticmethod
    async def get_assistants(
        db: Session, user_id: str, include_agent_voices: bool = False
    ) -> List[AssistantResponse]:
        """
        Get all assistants for a user

        Args:
            db: Database session
            user_id: User ID
            include_agent_voices: включить голосовых ассистентов агентов обзвона
                (по умолчанию скрыты — они управляются со страницы agent.html)

        Returns:
            List of assistant responses
        """
        from backend.services.assistant_limit_service import exclude_agent_owned

        query = db.query(AssistantConfig).filter(AssistantConfig.user_id == user_id)
        if not include_agent_voices:
            query = exclude_agent_owned(query, AssistantConfig, db, user_id)
        assistants = query.all()
        
        result = []
        for assistant in assistants:
            # Преобразуем функции в ожидаемый формат (список) если они представлены в виде словаря
            functions = assistant.functions
            if isinstance(functions, dict) and 'enabled_functions' in functions:
                # Превращаем {'enabled_functions': ['func1', 'func2']} в [{'name': 'func1'}, {'name': 'func2'}]
                functions = [{'name': func_name, 'description': f'Function {func_name}', 'parameters': {}} 
                           for func_name in functions.get('enabled_functions', [])]
            
            result.append(AssistantResponse(
                id=str(assistant.id),
                user_id=str(assistant.user_id),
                name=assistant.name,
                description=assistant.description,
                system_prompt=assistant.system_prompt,
                voice=assistant.voice,
                language=assistant.language,
                google_sheet_id=assistant.google_sheet_id,
                greeting_message=assistant.greeting_message,
                enable_vision=assistant.enable_vision or False,
                functions=functions,
                is_active=assistant.is_active,
                is_public=assistant.is_public,
                created_at=assistant.created_at,
                updated_at=assistant.updated_at,
                total_conversations=assistant.total_conversations,
                temperature=assistant.temperature,
                max_tokens=assistant.max_tokens
            ))
        
        return result
    
    @staticmethod
    async def get_assistant_by_id(db: Session, assistant_id: str, user_id: Optional[str] = None) -> AssistantConfig:
        """
        Get assistant by ID
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            user_id: Optional User ID for authorization check
            
        Returns:
            AssistantConfig object
            
        Raises:
            HTTPException: If assistant not found or doesn't belong to user
        """
        assistant = db.query(AssistantConfig).filter(AssistantConfig.id == assistant_id).first()
        
        if not assistant:
            logger.warning(f"Assistant not found: {assistant_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant not found"
            )
        
        # Check if assistant belongs to user if user_id is provided
        if user_id and str(assistant.user_id) != user_id:
            logger.warning(f"Unauthorized access to assistant {assistant_id} by user {user_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this assistant"
            )
        
        return assistant
    
    @staticmethod
    async def create_assistant(db: Session, user_id: str, assistant_data: AssistantCreate) -> AssistantResponse:
        """
        Create a new assistant
        
        Args:
            db: Database session
            user_id: User ID
            assistant_data: Assistant creation data
            
        Returns:
            AssistantResponse for the new assistant
            
        Raises:
            HTTPException: If creation fails
        """
        try:
            # Create assistant instance
            assistant = AssistantConfig(
                user_id=user_id,
                name=assistant_data.name,
                description=assistant_data.description,
                system_prompt=assistant_data.system_prompt,
                voice=assistant_data.voice,
                language=assistant_data.language,
                google_sheet_id=assistant_data.google_sheet_id,
                greeting_message=assistant_data.greeting_message,
                enable_vision=assistant_data.enable_vision,
                functions=assistant_data.functions,
                is_active=True,
                is_public=False
            )
            
            # Generate API access token for public assistants
            if assistant.is_public:
                assistant.api_access_token = str(uuid.uuid4())
            
            db.add(assistant)
            db.commit()
            db.refresh(assistant)
            
            logger.info(f"Assistant created: {assistant.id} for user {user_id}")
            
            # Преобразуем функции в ожидаемый формат (список) если они представлены в виде словаря
            functions = assistant.functions
            if isinstance(functions, dict) and 'enabled_functions' in functions:
                # Превращаем {'enabled_functions': ['func1', 'func2']} в [{'name': 'func1'}, {'name': 'func2'}]
                functions = [{'name': func_name, 'description': f'Function {func_name}', 'parameters': {}} 
                           for func_name in functions.get('enabled_functions', [])]
            
            return AssistantResponse(
                id=str(assistant.id),
                user_id=str(assistant.user_id),
                name=assistant.name,
                description=assistant.description,
                system_prompt=assistant.system_prompt,
                voice=assistant.voice,
                language=assistant.language,
                google_sheet_id=assistant.google_sheet_id,
                greeting_message=assistant.greeting_message,
                enable_vision=assistant.enable_vision or False,
                functions=functions,
                is_active=assistant.is_active,
                is_public=assistant.is_public,
                created_at=assistant.created_at,
                updated_at=assistant.updated_at,
                total_conversations=assistant.total_conversations,
                temperature=assistant.temperature,
                max_tokens=assistant.max_tokens
            )

        except IntegrityError as e:
            db.rollback()
            logger.error(f"Database integrity error during assistant creation: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Creation failed due to database constraint"
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Unexpected error during assistant creation: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Creation failed due to server error"
            )
    
    @staticmethod
    async def update_assistant(
        db: Session, 
        assistant_id: str, 
        user_id: str, 
        assistant_data: AssistantUpdate
    ) -> AssistantResponse:
        """
        Update an assistant
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            user_id: User ID
            assistant_data: Assistant update data
            
        Returns:
            Updated AssistantResponse
            
        Raises:
            HTTPException: If update fails
        """
        try:
            # Get assistant and verify ownership
            assistant = await AssistantService.get_assistant_by_id(db, assistant_id, user_id)
            
            # Update only provided fields
            update_data = assistant_data.dict(exclude_unset=True)
            for key, value in update_data.items():
                setattr(assistant, key, value)
            
            # Generate API access token for public assistants if becoming public
            if update_data.get('is_public') and not assistant.api_access_token:
                assistant.api_access_token = str(uuid.uuid4())
            
            db.commit()
            db.refresh(assistant)
            
            logger.info(f"Assistant updated: {assistant_id}")
            
            # Преобразуем функции в ожидаемый формат (список) если они представлены в виде словаря
            functions = assistant.functions
            if isinstance(functions, dict) and 'enabled_functions' in functions:
                # Превращаем {'enabled_functions': ['func1', 'func2']} в [{'name': 'func1'}, {'name': 'func2'}]
                functions = [{'name': func_name, 'description': f'Function {func_name}', 'parameters': {}} 
                           for func_name in functions.get('enabled_functions', [])]
            
            return AssistantResponse(
                id=str(assistant.id),
                user_id=str(assistant.user_id),
                name=assistant.name,
                description=assistant.description,
                system_prompt=assistant.system_prompt,
                voice=assistant.voice,
                language=assistant.language,
                google_sheet_id=assistant.google_sheet_id,
                greeting_message=assistant.greeting_message,
                enable_vision=assistant.enable_vision or False,
                functions=functions,
                is_active=assistant.is_active,
                is_public=assistant.is_public,
                created_at=assistant.created_at,
                updated_at=assistant.updated_at,
                total_conversations=assistant.total_conversations,
                temperature=assistant.temperature,
                max_tokens=assistant.max_tokens
            )

        except IntegrityError as e:
            db.rollback()
            logger.error(f"Database integrity error during assistant update: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Update failed due to database constraint"
            )
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Unexpected error during assistant update: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Update failed due to server error"
            )
    
    @staticmethod
    async def delete_assistant(db: Session, assistant_id: str, user_id: str) -> bool:
        """
        Delete an assistant
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            user_id: User ID
            
        Returns:
            True if deletion was successful
            
        Raises:
            HTTPException: If deletion fails
        """
        try:
            # Get assistant and verify ownership
            assistant = await AssistantService.get_assistant_by_id(db, assistant_id, user_id)

            # ✅ FIX: Запрещаем удаление, если ассистент является голосом агента обзвона.
            # Иначе агент молча остаётся без голоса. Просим сначала отвязать через
            # карточку агента (сменить голос или удалить агента).
            from backend.models.agent_config import AgentConfig
            bound_agent = db.query(AgentConfig).filter(
                AgentConfig.openai_assistant_id == assistant.id
            ).first()
            if bound_agent:
                logger.warning(
                    f"Blocked deletion of assistant {assistant_id}: "
                    f"used by agent {bound_agent.id} ({bound_agent.name})"
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Этот ассистент используется агентом «{bound_agent.name}». "
                        f"Сначала смените голос агента или удалите агента."
                    )
                )

            # Delete assistant (related rows: conversations/embeds/files/etc. — ON DELETE
            # CASCADE; tasks.assistant_id — ON DELETE SET NULL, не блокирует удаление)
            db.delete(assistant)
            db.commit()

            logger.info(f"Assistant deleted: {assistant_id}")
            return True

        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error deleting assistant: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete assistant"
            )
    
    @staticmethod
    async def get_embed_code(db: Session, assistant_id: str, user_id: str) -> EmbedCodeResponse:
        """
        Get embed code for an assistant
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            user_id: User ID
            
        Returns:
            EmbedCodeResponse with the embed code
            
        Raises:
            HTTPException: If assistant isn't active
        """
        # Get assistant and verify ownership
        assistant = await AssistantService.get_assistant_by_id(db, assistant_id, user_id)
        
        # Check if assistant is active
        if not assistant.is_active:
            logger.warning(f"Attempt to get embed code for inactive assistant: {assistant_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This assistant is not active. Please activate it first."
            )
        
        # Generate embed code
        host_url = settings.HOST_URL
        embed_code = f"""<!-- WellcomeAI Voice Assistant -->
<script>
    (function() {{
        var script = document.createElement('script');
        script.src = '{host_url}/static/widget.js';
        script.dataset.assistantId = '{assistant_id}';
        script.dataset.server = '{host_url}'; // Explicitly specify the server
        script.dataset.position = 'bottom-right'; // Widget position
        script.async = true;
        document.head.appendChild(script);
    }})();
</script>
<!-- End WellcomeAI -->"""
        
        return EmbedCodeResponse(
            embed_code=embed_code,
            assistant_id=assistant_id
        )
    
    @staticmethod
    async def increment_conversation_count(db: Session, assistant_id: str) -> None:
        """
        Increment the conversation count for an assistant
        
        Args:
            db: Database session
            assistant_id: Assistant ID
        """
        try:
            assistant = db.query(AssistantConfig).filter(AssistantConfig.id == assistant_id).first()
            
            if assistant:
                assistant.total_conversations += 1
                db.commit()
                logger.debug(f"Incremented conversation count for assistant {assistant_id}")
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error incrementing conversation count: {str(e)}")
