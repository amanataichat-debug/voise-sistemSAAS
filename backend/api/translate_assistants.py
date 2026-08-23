# backend/api/translate_assistants.py
"""
REST API эндпоинты для управления ассистентами-переводчиками.
OpenAI Realtime Translation API (gpt-realtime-translate).

✅ v1.0: Initial Translate Assistant CRUD
✅ Полный CRUD
✅ Проверка владения
✅ Проверка лимита ассистентов (вместе с openai/gemini/grok/cartesia)
✅ Проверка наличия OpenAI ключа (400 openai_key_required)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List
import uuid

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.db.session import get_db
from backend.models.user import User
from backend.models.translate_assistant import TranslateAssistantConfig, TranslateConversation
from backend.core.dependencies import get_current_user, check_assistant_limit
from backend.schemas.translate_assistant import (
    TranslateAssistantCreate,
    TranslateAssistantUpdate,
    TranslateAssistantResponse,
    TranslateEmbedCodeResponse,
)

logger = get_logger(__name__)

router = APIRouter()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def verify_assistant_access(
    assistant_id: str,
    user_id: str,
    db: Session,
    require_ownership: bool = True
) -> TranslateAssistantConfig:
    """Проверка доступа пользователя к переводчику."""
    try:
        assistant_uuid = uuid.UUID(assistant_id)
    except ValueError:
        logger.warning(f"[TRANSLATE-API] Invalid UUID format: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid assistant ID format"
        )

    assistant = db.query(TranslateAssistantConfig).filter(
        TranslateAssistantConfig.id == assistant_uuid
    ).first()

    if not assistant:
        logger.warning(f"[TRANSLATE-API] Assistant not found: {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Translate assistant not found"
        )

    if require_ownership and str(assistant.user_id) != user_id:
        logger.warning(f"[TRANSLATE-API] Unauthorized access: user {user_id} -> assistant {assistant_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this assistant"
        )

    return assistant


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.get("", response_model=List[TranslateAssistantResponse])
async def get_translate_assistants(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Список переводчиков текущего пользователя."""
    try:
        logger.info(f"[TRANSLATE-API] Fetching assistants for user {current_user.id}")

        assistants = db.query(TranslateAssistantConfig).filter(
            TranslateAssistantConfig.user_id == current_user.id
        ).order_by(TranslateAssistantConfig.created_at.desc()).all()

        logger.info(f"[TRANSLATE-API] Found {len(assistants)} translate assistants")

        return [TranslateAssistantResponse.model_validate(a.to_dict()) for a in assistants]

    except Exception as e:
        logger.error(f"[TRANSLATE-API] Error fetching assistants: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch translate assistants"
        )


@router.get("/{assistant_id}", response_model=TranslateAssistantResponse)
async def get_translate_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получить переводчик по ID."""
    try:
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )
        return TranslateAssistantResponse.model_validate(assistant.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TRANSLATE-API] Error fetching assistant {assistant_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch translate assistant"
        )


@router.post("", response_model=TranslateAssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_translate_assistant(
    assistant_data: TranslateAssistantCreate,
    db: Session = Depends(get_db),
    # check_assistant_limit проверяет активность подписки + лимит ассистентов
    current_user: User = Depends(check_assistant_limit)
):
    """
    Создать переводчик.

    Переводчик использует тот же OpenAI ключ, что и Realtime-2.
    Если ключа нет — возвращаем 400 {"detail": "openai_key_required"},
    фронт по этому коду перенаправит на вкладку OpenAI Агенты.
    """
    try:
        # 🔑 Проверка наличия OpenAI ключа
        if not current_user.openai_api_key:
            logger.warning(f"[TRANSLATE-API] User {current_user.id} has no OpenAI key")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="openai_key_required"
            )

        logger.info(f"[TRANSLATE-API] Creating translate assistant for user {current_user.id}")
        logger.info(f"[TRANSLATE-API] Name: {assistant_data.name}, output_language: {assistant_data.output_language}")

        assistant = TranslateAssistantConfig(
            user_id=current_user.id,
            name=assistant_data.name,
            description=assistant_data.description,
            output_language=assistant_data.output_language,
            source_language_hint=assistant_data.source_language_hint,
            enable_source_transcript=assistant_data.enable_source_transcript,
            greeting_message=assistant_data.greeting_message,
            is_active=True,
            is_public=False,
        )

        db.add(assistant)
        db.commit()
        db.refresh(assistant)

        logger.info(f"[TRANSLATE-API] ✅ Translate assistant created: {assistant.id}")

        return TranslateAssistantResponse.model_validate(assistant.to_dict())

    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        logger.error(f"[TRANSLATE-API] Database integrity error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create assistant due to database constraint"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[TRANSLATE-API] Error creating assistant: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create translate assistant"
        )


@router.put("/{assistant_id}", response_model=TranslateAssistantResponse)
async def update_translate_assistant(
    assistant_id: str,
    assistant_data: TranslateAssistantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Обновить переводчик."""
    try:
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        update_data = assistant_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(assistant, key, value)

        db.commit()
        db.refresh(assistant)

        logger.info(f"[TRANSLATE-API] ✅ Translate assistant updated: {assistant_id}")

        return TranslateAssistantResponse.model_validate(assistant.to_dict())

    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        logger.error(f"[TRANSLATE-API] Database integrity error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to update assistant due to database constraint"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[TRANSLATE-API] Error updating assistant {assistant_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update translate assistant"
        )


@router.delete("/{assistant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_translate_assistant(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Удалить переводчик и связанные записи."""
    try:
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        # Явно удаляем связанные conversations перед удалением ассистента
        deleted_convs = db.query(TranslateConversation).filter(
            TranslateConversation.assistant_id == assistant.id
        ).delete(synchronize_session=False)
        logger.info(f"[TRANSLATE-API] Deleted {deleted_convs} related conversations")

        db.delete(assistant)
        db.commit()

        logger.info(f"[TRANSLATE-API] ✅ Translate assistant deleted: {assistant_id}")

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[TRANSLATE-API] Error deleting assistant {assistant_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete translate assistant: {str(e)}"
        )


@router.get("/{assistant_id}/embed-code", response_model=TranslateEmbedCodeResponse)
async def get_translate_embed_code(
    assistant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Готовый embed-сниппет для виджета переводчика."""
    try:
        assistant = await verify_assistant_access(
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            db=db,
            require_ownership=True
        )

        if not assistant.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assistant must be active to generate embed code"
            )

        host_url = settings.HOST_URL
        embed_code = (
            f'<!-- Voksy AI Translate Widget -->\n'
            f'<script src="{host_url}/static/widget-translate.js"\n'
            f'        data-assistant-id="{assistant_id}"\n'
            f'        data-server="{host_url}"></script>\n'
            f'<!-- End Voksy AI Translate -->'
        )

        logger.info(f"[TRANSLATE-API] ✅ Embed code generated for assistant {assistant_id}")

        return TranslateEmbedCodeResponse(
            embed_code=embed_code,
            assistant_id=assistant_id
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TRANSLATE-API] Error generating embed code: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate embed code"
        )
