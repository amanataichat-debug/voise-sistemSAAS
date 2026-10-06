# backend/api/contacts.py
"""
Contacts API endpoints для CRM функциональности.
Управление контактами (клиентами) и их связью с диалогами.
Version: 4.0 - Production Ready + Full Task Delete
✅ v4.0: Полное удаление задач из БД (не просто смена статуса)
✅ v3.4: Добавлен endpoint для обновления задач PUT /contacts/tasks/{task_id}
✅ v3.3: Добавлена нормализация номеров телефонов для предотвращения дублей
✅ v3.2: Добавлена поддержка custom_greeting для персонализированных приветствий
✅ v3.1: OpenAI + Gemini ассистенты
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_, distinct, literal_column
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from uuid import UUID
from pydantic import BaseModel, Field
import re

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.services.auth_service import AuthService
from backend.services.conversation_service import ConversationService
from backend.models.user import User
from backend.models.contact import Contact, ContactNote
from backend.models.conversation import Conversation
from backend.models.assistant import AssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.models.gemini_assistant import GeminiConversation
from backend.models.fish_assistant import FishAssistantConfig, FishConversation
from backend.models.eleven_assistant import ElevenAssistantConfig, ElevenConversation
from backend.models.task import Task, TaskStatus

logger = get_logger(__name__)

# Create router
router = APIRouter()


# ==================== Pydantic схемы ====================

class ContactCreate(BaseModel):
    """Схема для создания контакта"""
    phone: str = Field(..., description="Номер телефона в формате +79123456789")
    name: Optional[str] = Field(None, description="Имя контакта")
    status: str = Field(default="new", description="Статус: new, active, client, archived")
    notes: Optional[str] = Field(None, description="Заметки")


class ContactUpdate(BaseModel):
    """Схема для обновления контакта"""
    name: Optional[str] = Field(None, description="Имя контакта")
    status: Optional[str] = Field(None, description="Статус: new, active, client, archived")
    notes: Optional[str] = Field(None, description="Заметки")


class ContactStatusUpdate(BaseModel):
    """Схема для обновления только статуса"""
    status: str = Field(..., description="Новый статус: new, active, client, archived")


class ContactNoteCreate(BaseModel):
    """Схема для создания заметки"""
    note_text: str = Field(..., description="Текст заметки", min_length=1)


class TaskCreate(BaseModel):
    """
    Схема для создания задачи
    ✅ v3.2: Добавлено поле custom_greeting
    ✅ v3.3: Добавлено поле caller_id
    """
    assistant_id: str = Field(..., description="UUID ассистента для звонка (OpenAI или Gemini)")
    scheduled_time: str = Field(..., description="Время звонка в ISO формате или текстом")
    title: str = Field(..., description="Название задачи", min_length=1, max_length=255)
    description: Optional[str] = Field(None, description="Описание задачи")
    custom_greeting: Optional[str] = Field(None, description="Персонализированное приветствие для звонка")
    caller_id: Optional[str] = Field(None, description="Номер телефона для исходящего звонка. Если не указан — первый активный номер")


class TaskUpdate(BaseModel):
    """
    Схема для обновления задачи
    ✅ v3.4: Новая схема для редактирования задач
    ✅ v3.5: Добавлено поле caller_id
    """
    assistant_id: Optional[str] = Field(None, description="UUID ассистента для звонка (OpenAI или Gemini)")
    scheduled_time: Optional[str] = Field(None, description="Время звонка в ISO формате или текстом")
    title: Optional[str] = Field(None, description="Название задачи", min_length=1, max_length=255)
    description: Optional[str] = Field(None, description="Описание задачи")
    custom_greeting: Optional[str] = Field(None, description="Персонализированное приветствие для звонка")
    caller_id: Optional[str] = Field(None, description="Номер телефона для исходящего звонка")


# ==================== Вспомогательные функции ====================

def get_or_create_contact(db: Session, user_id: UUID, phone: str) -> Contact:
    """
    🔧 DEPRECATED: Используйте ConversationService._get_or_create_contact()
    
    Получить существующий контакт или создать новый (автоматически).
    Используется при сохранении диалога.
    
    ✅ v3.3: Использует нормализацию номера
    """
    # Нормализуем номер
    normalized_phone = ConversationService._normalize_phone(phone)
    
    contact = db.query(Contact).filter(
        Contact.user_id == user_id,
        Contact.phone == normalized_phone
    ).first()
    
    if not contact:
        contact = Contact(
            user_id=user_id,
            phone=normalized_phone,
            status="new",
            last_interaction=datetime.utcnow()
        )
        db.add(contact)
        db.flush()
        logger.info(f"[CRM] Auto-created contact for phone {normalized_phone}, user {user_id}")
    else:
        # Обновляем время последнего контакта
        contact.last_interaction = datetime.utcnow()
    
    return contact


def parse_time_string(time_str: str) -> datetime:
    """
    Умный парсинг времени из строки.
    Поддерживает: "через X часов", "завтра в 15:00", "сегодня в 18:00"
    """
    time_str = time_str.lower().strip()
    now = datetime.utcnow()
    
    # "через X часов/минут"
    if "через" in time_str or "cherez" in time_str:
        if "час" in time_str or "hour" in time_str:
            match = re.search(r'(\d+)\s*(час|hour)', time_str)
            if match:
                hours = int(match.group(1))
                return now + timedelta(hours=hours)
        if "минут" in time_str or "minut" in time_str or "min" in time_str:
            match = re.search(r'(\d+)\s*(минут|minut|min)', time_str)
            if match:
                minutes = int(match.group(1))
                return now + timedelta(minutes=minutes)
    
    # "завтра в ЧЧ:ММ"
    if "завтра" in time_str or "tomorrow" in time_str:
        match = re.search(r'(\d{1,2}):(\d{2})', time_str)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            tomorrow = now + timedelta(days=1)
            return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            # Завтра в 10:00 по умолчанию
            tomorrow = now + timedelta(days=1)
            return tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
    
    # "сегодня в ЧЧ:ММ"
    if "сегодня" in time_str or "today" in time_str:
        match = re.search(r'(\d{1,2}):(\d{2})', time_str)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            result = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Если время уже прошло сегодня - ставим на завтра
            if result <= now:
                result = result + timedelta(days=1)
            return result
    
    # Fallback - через 1 час
    logger.warning(f"Could not parse time string '{time_str}', using default: +1 hour")
    return now + timedelta(hours=1)


# ==================== Диалоги провайдеров без contact_id ====================
# conversations.contact_id есть только у OpenAI-диалогов. Gemini / Fish / Eleven пишут
# в свои таблицы без contact_id — их связываем с контактом по номеру (caller_number
# нормализован, как и contacts.phone) и по ассистентам владельца контакта.
PHONE_CONVERSATION_SOURCES = (
    (GeminiConversation, GeminiAssistantConfig, "gemini"),
    (FishConversation, FishAssistantConfig, "fish"),
    (ElevenConversation, ElevenAssistantConfig, "eleven"),
)


def _phone_sessions_count(db: Session, user_id, phones: List[str]) -> dict:
    """{phone: число сессий (звонков)} по таблицам Gemini / Fish / Eleven."""
    counts: dict = {}
    phones = [p for p in phones if p and p != "unknown"]
    if not phones:
        return counts
    for conv_model, assistant_model, _ in PHONE_CONVERSATION_SOURCES:
        try:
            rows = (
                db.query(conv_model.caller_number, func.count(distinct(conv_model.session_id)))
                .join(assistant_model, assistant_model.id == conv_model.assistant_id)
                .filter(assistant_model.user_id == user_id, conv_model.caller_number.in_(phones))
                .group_by(conv_model.caller_number)
                .all()
            )
        except Exception as e:
            logger.warning(f"[CRM-API] phone sessions count failed for {conv_model.__tablename__}: {e}")
            db.rollback()
            continue
        for phone, cnt in rows:
            counts[phone] = counts.get(phone, 0) + int(cnt or 0)
    return counts


def _phone_sessions(db: Session, user_id, phone: str) -> list:
    """Сессии (звонки) контакта из таблиц Gemini / Fish / Eleven в формате карточки CRM."""
    result = []
    if not phone or phone == "unknown":
        return result
    for conv_model, assistant_model, assistant_type in PHONE_CONVERSATION_SOURCES:
        direction_col = getattr(conv_model, "call_direction", None)
        cols = [
            conv_model.session_id,
            conv_model.assistant_id,
            assistant_model.name.label("assistant_name"),
            (func.max(direction_col) if direction_col is not None else literal_column("NULL")).label("call_direction"),
            func.count(conv_model.id).label("messages_count"),
            func.min(conv_model.created_at).label("created_at"),
            func.max(conv_model.created_at).label("updated_at"),
            func.sum(conv_model.tokens_used).label("total_tokens"),
        ]
        try:
            rows = (
                db.query(*cols)
                .join(assistant_model, assistant_model.id == conv_model.assistant_id)
                .filter(assistant_model.user_id == user_id, conv_model.caller_number == phone)
                .group_by(conv_model.session_id, conv_model.assistant_id, assistant_model.name)
                .all()
            )
        except Exception as e:
            logger.warning(f"[CRM-API] phone sessions failed for {conv_model.__tablename__}: {e}")
            db.rollback()
            continue
        for r in rows:
            result.append({
                "session_id": r.session_id,
                "assistant_id": str(r.assistant_id),
                "assistant_name": r.assistant_name or "Unknown",
                "assistant_type": assistant_type,
                "call_direction": r.call_direction,
                "messages_count": r.messages_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "total_tokens": r.total_tokens or 0,
                "total_duration": 0,
            })
    return result


# ==================== CONTACTS API Endpoints ====================

@router.get("/")
async def get_contacts(
    status: Optional[str] = Query(None, description="Фильтр по статусу"),
    search: Optional[str] = Query(None, description="Поиск по имени или номеру"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить список контактов (CRM).
    
    Возвращает контакты пользователя с количеством диалогов и датой последнего контакта.
    
    **Фильтры:**
    - status: Фильтр по статусу (new, active, client, archived)
    - search: Поиск по имени или номеру телефона
    
    **Пагинация:**
    - limit: Количество записей (1-100)
    - offset: Смещение
    
    **Возвращает:**
    - contacts: Список контактов с метриками
    - total: Общее количество
    """
    try:
        logger.info(f"[CRM-API] Get contacts for user {current_user.id}")
        logger.info(f"   Filters: status={status}, search={search}")
        logger.info(f"   Pagination: limit={limit}, offset={offset}")
        
        # Подзапрос для подсчета диалогов
        conversations_count_subquery = (
            db.query(
                Conversation.contact_id,
                func.count(Conversation.id).label('total_conversations')
            )
            .group_by(Conversation.contact_id)
            .subquery()
        )
        
        # Основной запрос
        query = (
            db.query(
                Contact,
                func.coalesce(conversations_count_subquery.c.total_conversations, 0).label('conversations_count')
            )
            .outerjoin(
                conversations_count_subquery,
                Contact.id == conversations_count_subquery.c.contact_id
            )
            .filter(Contact.user_id == current_user.id)
        )
        
        # Фильтр по статусу
        if status:
            query = query.filter(Contact.status == status)
        
        # Поиск
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Contact.name.ilike(search_pattern),
                    Contact.phone.ilike(search_pattern)
                )
            )
        
        # Подсчет общего количества
        total = query.count()
        
        # Сортировка и пагинация
        contacts_with_counts = (
            query.order_by(desc(Contact.last_interaction))
            .limit(limit)
            .offset(offset)
            .all()
        )
        
        logger.info(f"✅ Found {len(contacts_with_counts)} contacts (total: {total})")
        
        # Звонки Gemini / Fish / Eleven (связаны по номеру, без contact_id)
        phone_counts = _phone_sessions_count(
            db, current_user.id, [c.phone for c, _ in contacts_with_counts]
        )

        # Формируем результат
        result = []
        for contact, conversations_count in contacts_with_counts:
            contact_dict = contact.to_dict()
            contact_dict['total_conversations'] = conversations_count + phone_counts.get(contact.phone, 0)
            result.append(contact_dict)
        
        return {
            "contacts": result,
            "total": total,
            "page": offset // limit if limit > 0 else 0,
            "page_size": limit
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting contacts: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get contacts: {str(e)}"
        )


@router.get("/{contact_id}")
async def get_contact_detail(
    contact_id: str,
    include_conversations: bool = Query(True, description="Включить список всех диалогов"),
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить детальную информацию о контакте.
    
    Возвращает информацию о контакте + все его диалоги.
    
    **Параметры:**
    - contact_id: UUID контакта
    - include_conversations: Включить список диалогов (по умолчанию true)
    
    **Возвращает:**
    - Полная информация о контакте
    - Список всех диалогов (сессий)
    - Статистика
    """
    try:
        logger.info(f"[CRM-API] Get contact detail: {contact_id}")
        logger.info(f"   User: {current_user.id}")
        
        # Проверяем UUID
        try:
            contact_uuid = UUID(contact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid contact ID format"
            )
        
        # Получаем контакт
        contact = db.query(Contact).filter(
            Contact.id == contact_uuid,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            logger.warning(f"Contact not found or access denied: {contact_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found"
            )
        
        result = contact.to_dict()
        
        # Статистика по диалогам
        stats = db.query(
            func.count(Conversation.id).label('total_conversations'),
            func.sum(Conversation.tokens_used).label('total_tokens'),
            func.sum(Conversation.duration_seconds).label('total_duration')
        ).filter(Conversation.contact_id == contact.id).first()
        
        phone_sessions = _phone_sessions(db, current_user.id, contact.phone)

        result['stats'] = {
            'total_conversations': (stats.total_conversations or 0) + len(phone_sessions),
            'total_tokens': (stats.total_tokens or 0) + sum(p['total_tokens'] for p in phone_sessions),
            'total_duration': stats.total_duration or 0
        }
        
        # Список диалогов (группированных по session_id)
        if include_conversations:
            # Группируем по session_id
            sessions = db.query(
                Conversation.session_id,
                Conversation.assistant_id,
                Conversation.call_direction,
                func.count(Conversation.id).label('messages_count'),
                func.min(Conversation.created_at).label('created_at'),
                func.max(Conversation.created_at).label('updated_at'),
                func.sum(Conversation.tokens_used).label('total_tokens'),
                func.sum(Conversation.duration_seconds).label('total_duration')
            ).filter(
                Conversation.contact_id == contact.id
            ).group_by(
                Conversation.session_id,
                Conversation.assistant_id,
                Conversation.call_direction
            ).order_by(desc(func.max(Conversation.created_at))).all()
            
            conversations = []
            for s in sessions:
                # Получаем имя ассистента
                assistant = db.query(AssistantConfig).filter(
                    AssistantConfig.id == s.assistant_id
                ).first()
                
                conversations.append({
                    "session_id": s.session_id,
                    "assistant_id": str(s.assistant_id),
                    "assistant_name": assistant.name if assistant else "Unknown",
                    "call_direction": s.call_direction,
                    "messages_count": s.messages_count,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    "total_tokens": s.total_tokens or 0,
                    "total_duration": s.total_duration or 0
                })
            
            conversations.extend(phone_sessions)
            conversations.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
            result['conversations'] = conversations
            logger.info(f"   Found {len(conversations)} conversation sessions")
        
        logger.info(f"✅ Contact details returned")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting contact detail: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get contact detail: {str(e)}"
        )


@router.post("/")
async def create_or_update_contact(
    contact_data: ContactCreate,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Создать новый контакт или обновить существующий.
    ✅ v3.3: Использует нормализацию номера для предотвращения дублей
    
    Если контакт с таким номером уже существует - обновляет его данные.
    
    **Body:**
    - phone: Номер телефона (обязательно)
    - name: Имя контакта (опционально)
    - status: Статус (опционально, по умолчанию "new")
    - notes: Заметки (опционально)
    
    **Возвращает:**
    - Созданный или обновленный контакт
    """
    try:
        logger.info(f"[CRM-API-v3.3] Create/update contact for user {current_user.id}")
        logger.info(f"   Phone (raw): {contact_data.phone}")
        
        # ✅ v3.3: НОРМАЛИЗУЕМ номер телефона
        normalized_phone = ConversationService._normalize_phone(contact_data.phone)
        logger.info(f"   Phone (normalized): {normalized_phone}")
        
        # Валидация статуса
        valid_statuses = ["new", "active", "client", "archived"]
        if contact_data.status not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            )
        
        # Проверяем существует ли контакт с нормализованным номером
        contact = db.query(Contact).filter(
            Contact.user_id == current_user.id,
            Contact.phone == normalized_phone
        ).first()
        
        if contact:
            # Обновляем существующий
            if contact_data.name is not None:
                contact.name = contact_data.name
            if contact_data.status is not None:
                contact.status = contact_data.status
            if contact_data.notes is not None:
                contact.notes = contact_data.notes
            
            contact.updated_at = datetime.utcnow()
            
            db.commit()
            db.refresh(contact)
            
            logger.info(f"✅ Contact updated: {contact.id}")
            
            return {
                "message": "Contact updated successfully",
                "contact": contact.to_dict()
            }
        else:
            # Создаем новый с нормализованным номером
            new_contact = Contact(
                user_id=current_user.id,
                phone=normalized_phone,
                name=contact_data.name,
                status=contact_data.status,
                notes=contact_data.notes
            )
            
            db.add(new_contact)
            db.commit()
            db.refresh(new_contact)
            
            logger.info(f"✅ Contact created: {new_contact.id} with normalized phone: {normalized_phone}")
            
            return {
                "message": "Contact created successfully",
                "contact": new_contact.to_dict()
            }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error creating/updating contact: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create/update contact: {str(e)}"
        )


@router.put("/{contact_id}")
async def update_contact(
    contact_id: str,
    contact_data: ContactUpdate,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Обновить существующий контакт.
    
    **Параметры:**
    - contact_id: UUID контакта
    
    **Body:**
    - name: Имя контакта (опционально)
    - status: Статус (опционально)
    - notes: Заметки (опционально)
    
    **Возвращает:**
    - Обновленный контакт
    """
    try:
        logger.info(f"[CRM-API] Update contact: {contact_id}")
        
        # Проверяем UUID
        try:
            contact_uuid = UUID(contact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid contact ID format"
            )
        
        # Получаем контакт
        contact = db.query(Contact).filter(
            Contact.id == contact_uuid,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            logger.warning(f"Contact not found: {contact_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found"
            )
        
        # Валидация статуса
        if contact_data.status:
            valid_statuses = ["new", "active", "client", "archived"]
            if contact_data.status not in valid_statuses:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
                )
        
        # Обновляем поля
        if contact_data.name is not None:
            contact.name = contact_data.name
        if contact_data.status is not None:
            contact.status = contact_data.status
        if contact_data.notes is not None:
            contact.notes = contact_data.notes
        
        contact.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(contact)
        
        logger.info(f"✅ Contact updated: {contact.id}")
        
        return {
            "message": "Contact updated successfully",
            "contact": contact.to_dict()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error updating contact: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update contact: {str(e)}"
        )


@router.patch("/{contact_id}/status")
async def update_contact_status(
    contact_id: str,
    status_data: ContactStatusUpdate,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Обновить только статус контакта.
    
    Быстрый endpoint для изменения статуса без изменения других полей.
    
    **Параметры:**
    - contact_id: UUID контакта
    
    **Body:**
    - status: Новый статус (new, active, client, archived)
    
    **Возвращает:**
    - Обновленный контакт
    """
    try:
        logger.info(f"[CRM-API] Update contact status: {contact_id} -> {status_data.status}")
        
        # Проверяем UUID
        try:
            contact_uuid = UUID(contact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid contact ID format"
            )
        
        # Валидация статуса
        valid_statuses = ["new", "active", "client", "archived"]
        if status_data.status not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            )
        
        # Получаем контакт
        contact = db.query(Contact).filter(
            Contact.id == contact_uuid,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            logger.warning(f"Contact not found: {contact_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found"
            )
        
        # Обновляем статус
        contact.status = status_data.status
        contact.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(contact)
        
        logger.info(f"✅ Contact status updated: {contact.id}")
        
        return {
            "message": "Contact status updated successfully",
            "contact": contact.to_dict()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error updating contact status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update contact status: {str(e)}"
        )


@router.delete("/{contact_id}")
async def delete_contact(
    contact_id: str,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Удалить контакт.
    
    ⚠️ ВНИМАНИЕ: Это также удалит все диалоги, заметки и задачи связанные с контактом!
    
    **Параметры:**
    - contact_id: UUID контакта
    
    **Возвращает:**
    - Сообщение об успешном удалении
    """
    try:
        logger.info(f"[CRM-API] Delete contact: {contact_id}")
        
        # Проверяем UUID
        try:
            contact_uuid = UUID(contact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid contact ID format"
            )
        
        # Получаем контакт
        contact = db.query(Contact).filter(
            Contact.id == contact_uuid,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            logger.warning(f"Contact not found: {contact_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found"
            )
        
        # Подсчитываем количество связанных данных
        conversations_count = db.query(Conversation).filter(
            Conversation.contact_id == contact.id
        ).count()
        
        notes_count = db.query(ContactNote).filter(
            ContactNote.contact_id == contact.id
        ).count()
        
        tasks_count = db.query(Task).filter(
            Task.contact_id == contact.id
        ).count()
        
        # Удаляем контакт (все связанное удалится автоматически благодаря cascade)
        db.delete(contact)
        db.commit()
        
        logger.info(f"✅ Contact deleted: {contact_id}")
        logger.info(f"   Also deleted {conversations_count} conversations, {notes_count} notes, {tasks_count} tasks")
        
        return {
            "message": "Contact deleted successfully",
            "deleted_conversations": conversations_count,
            "deleted_notes": notes_count,
            "deleted_tasks": tasks_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error deleting contact: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete contact: {str(e)}"
        )


# ==================== CONTACT NOTES API Endpoints ====================

@router.get("/{contact_id}/notes")
async def get_contact_notes(
    contact_id: str,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить все заметки по контакту (лента заметок).
    
    Возвращает список всех заметок отсортированных по дате (новые сверху).
    
    **Параметры:**
    - contact_id: UUID контакта
    
    **Возвращает:**
    - notes: Список заметок с датами и авторами
    - total: Количество заметок
    """
    try:
        logger.info(f"[CRM-API] Get notes for contact {contact_id}")
        
        # Проверяем UUID
        try:
            contact_uuid = UUID(contact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid contact ID format"
            )
        
        # Проверяем что контакт принадлежит пользователю
        contact = db.query(Contact).filter(
            Contact.id == contact_uuid,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found"
            )
        
        # Получаем заметки (новые сверху)
        notes = db.query(ContactNote).filter(
            ContactNote.contact_id == contact_uuid
        ).order_by(desc(ContactNote.created_at)).all()
        
        result = []
        for note in notes:
            note_dict = note.to_dict()
            # Добавляем информацию об авторе
            author = db.query(User).filter(User.id == note.user_id).first()
            note_dict['author_email'] = author.email if author else "Unknown"
            result.append(note_dict)
        
        logger.info(f"✅ Returned {len(result)} notes")
        
        return {
            "notes": result,
            "total": len(result)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting notes: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get notes: {str(e)}"
        )


@router.post("/{contact_id}/notes")
async def create_contact_note(
    contact_id: str,
    note_data: ContactNoteCreate,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Создать новую заметку для контакта.
    
    Добавляет заметку в ленту заметок контакта.
    
    **Параметры:**
    - contact_id: UUID контакта
    
    **Body:**
    - note_text: Текст заметки (обязательно, минимум 1 символ)
    
    **Возвращает:**
    - note: Созданная заметка с ID и датой создания
    """
    try:
        logger.info(f"[CRM-API] Create note for contact {contact_id}")
        
        # Проверяем UUID
        try:
            contact_uuid = UUID(contact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid contact ID format"
            )
        
        # Проверяем что контакт принадлежит пользователю
        contact = db.query(Contact).filter(
            Contact.id == contact_uuid,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found"
            )
        
        # Валидация
        note_text = note_data.note_text.strip()
        if not note_text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Note text cannot be empty"
            )
        
        # Создаем заметку
        new_note = ContactNote(
            contact_id=contact_uuid,
            user_id=current_user.id,
            note_text=note_text
        )
        
        db.add(new_note)
        db.commit()
        db.refresh(new_note)
        
        result = new_note.to_dict()
        result['author_email'] = current_user.email
        
        logger.info(f"✅ Note created: {new_note.id}")
        
        return {
            "message": "Note created successfully",
            "note": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error creating note: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create note: {str(e)}"
        )


@router.delete("/notes/{note_id}")
async def delete_contact_note(
    note_id: str,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Удалить заметку из ленты заметок.
    
    Можно удалить только заметки контактов, которые принадлежат пользователю.
    
    **Параметры:**
    - note_id: UUID заметки
    
    **Возвращает:**
    - Сообщение об успешном удалении
    """
    try:
        logger.info(f"[CRM-API] Delete note {note_id}")
        
        # Проверяем UUID
        try:
            note_uuid = UUID(note_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid note ID format"
            )
        
        # Получаем заметку
        note = db.query(ContactNote).filter(ContactNote.id == note_uuid).first()
        
        if not note:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Note not found"
            )
        
        # Проверяем что контакт принадлежит пользователю
        contact = db.query(Contact).filter(
            Contact.id == note.contact_id,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Удаляем
        db.delete(note)
        db.commit()
        
        logger.info(f"✅ Note deleted: {note_id}")
        
        return {"message": "Note deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error deleting note: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete note: {str(e)}"
        )


# ==================== TASKS API Endpoints (OpenAI + Gemini + Custom Greeting) ====================

@router.get("/{contact_id}/tasks")
async def get_contact_tasks(
    contact_id: str,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить все задачи контакта.
    ✅ Поддерживает OpenAI и Gemini ассистентов
    ✅ v3.2: Возвращает custom_greeting
    
    **Параметры:**
    - contact_id: UUID контакта
    
    **Возвращает:**
    - tasks: Список всех задач (запланированных и выполненных)
    - pending_count: Количество ожидающих задач
    - completed_count: Количество выполненных
    """
    try:
        logger.info(f"[TASKS-API] Get tasks for contact {contact_id}")
        
        # Проверяем UUID
        try:
            contact_uuid = UUID(contact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid contact ID format"
            )
        
        # Проверяем доступ к контакту
        contact = db.query(Contact).filter(
            Contact.id == contact_uuid,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found"
            )
        
        # Получаем все задачи
        tasks = db.query(Task).filter(
            Task.contact_id == contact_uuid
        ).order_by(desc(Task.scheduled_time)).all()
        
        # Статистика
        pending_count = sum(1 for t in tasks if t.status in [TaskStatus.SCHEDULED, TaskStatus.PENDING])
        completed_count = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        
        result = []
        for task in tasks:
            task_dict = task.to_dict()
            
            # Определяем тип и получаем имя ассистента
            if task.assistant_id:
                assistant = db.query(AssistantConfig).filter(
                    AssistantConfig.id == task.assistant_id
                ).first()
                task_dict['assistant_name'] = assistant.name if assistant else "Unknown OpenAI"
                task_dict['assistant_type'] = 'openai'
            elif task.gemini_assistant_id:
                gemini_assistant = db.query(GeminiAssistantConfig).filter(
                    GeminiAssistantConfig.id == task.gemini_assistant_id
                ).first()
                task_dict['assistant_name'] = gemini_assistant.name if gemini_assistant else "Unknown Gemini"
                task_dict['assistant_type'] = 'gemini'
            elif task.cartesia_assistant_id:
                cartesia_assistant = db.query(CartesiaAssistantConfig).filter(
                    CartesiaAssistantConfig.id == task.cartesia_assistant_id
                ).first()
                task_dict['assistant_name'] = cartesia_assistant.name if cartesia_assistant else "Unknown Cartesia"
                task_dict['assistant_type'] = 'cartesia'
            else:
                task_dict['assistant_name'] = "Unknown"
                task_dict['assistant_type'] = 'unknown'

            result.append(task_dict)

        logger.info(f"✅ Returned {len(result)} tasks (pending: {pending_count}, completed: {completed_count})")
        
        return {
            "tasks": result,
            "total": len(result),
            "pending_count": pending_count,
            "completed_count": completed_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting tasks: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get tasks: {str(e)}"
        )


@router.get("/tasks/{task_id}")
async def get_task_detail(
    task_id: str,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить детальную информацию о задаче.
    ✅ v3.4: Новый endpoint для получения деталей задачи
    
    **Параметры:**
    - task_id: UUID задачи
    
    **Возвращает:**
    - Полная информация о задаче с именем ассистента и типом
    """
    try:
        logger.info(f"[TASKS-API] Get task detail: {task_id}")
        
        # Проверяем UUID
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid task ID format"
            )
        
        # Получаем задачу
        task = db.query(Task).filter(Task.id == task_uuid).first()
        
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found"
            )
        
        # Проверяем доступ через контакт
        contact = db.query(Contact).filter(
            Contact.id == task.contact_id,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Формируем результат
        result = task.to_dict()
        
        # Определяем тип и получаем имя ассистента
        if task.assistant_id:
            assistant = db.query(AssistantConfig).filter(
                AssistantConfig.id == task.assistant_id
            ).first()
            result['assistant_name'] = assistant.name if assistant else "Unknown OpenAI"
            result['assistant_type'] = 'openai'
        elif task.gemini_assistant_id:
            gemini_assistant = db.query(GeminiAssistantConfig).filter(
                GeminiAssistantConfig.id == task.gemini_assistant_id
            ).first()
            result['assistant_name'] = gemini_assistant.name if gemini_assistant else "Unknown Gemini"
            result['assistant_type'] = 'gemini'
        elif task.cartesia_assistant_id:
            cartesia_assistant = db.query(CartesiaAssistantConfig).filter(
                CartesiaAssistantConfig.id == task.cartesia_assistant_id
            ).first()
            result['assistant_name'] = cartesia_assistant.name if cartesia_assistant else "Unknown Cartesia"
            result['assistant_type'] = 'cartesia'
        else:
            result['assistant_name'] = "Unknown"
            result['assistant_type'] = 'unknown'

        # Добавляем информацию о контакте
        result['contact'] = {
            'id': str(contact.id),
            'name': contact.name,
            'phone': contact.phone
        }
        
        logger.info(f"✅ Task details returned")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting task detail: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get task detail: {str(e)}"
        )


@router.post("/{contact_id}/tasks")
async def create_contact_task(
    contact_id: str,
    task_data: TaskCreate,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Создать новую задачу с автозвонком.
    ✅ Поддерживает OpenAI и Gemini ассистентов
    ✅ v3.2: Поддерживает custom_greeting для персонализированных приветствий
    
    **Параметры:**
    - contact_id: UUID контакта
    
    **Body:**
    - assistant_id: UUID ассистента (OpenAI или Gemini)
    - scheduled_time: Время звонка (ISO формат или текст типа "завтра в 15:00")
    - title: Название задачи
    - description: Описание (опционально)
    - custom_greeting: Персонализированное приветствие (опционально)
    
    **Возвращает:**
    - task: Созданная задача
    """
    try:
        logger.info(f"[TASKS-API] Create task for contact {contact_id}")
        logger.info(f"   Assistant: {task_data.assistant_id}")
        logger.info(f"   Scheduled: {task_data.scheduled_time}, Title: {task_data.title}")
        if task_data.custom_greeting:
            logger.info(f"   💬 Custom greeting: {task_data.custom_greeting[:50]}...")
        
        # Проверяем UUID контакта
        try:
            contact_uuid = UUID(contact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid contact ID format"
            )
        
        # Проверяем доступ к контакту
        contact = db.query(Contact).filter(
            Contact.id == contact_uuid,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found"
            )
        
        # Проверяем UUID ассистента
        try:
            assistant_uuid = UUID(task_data.assistant_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid assistant ID format"
            )
        
        # Проверяем во всех таблицах
        openai_assistant = db.query(AssistantConfig).filter(
            AssistantConfig.id == assistant_uuid,
            AssistantConfig.user_id == current_user.id
        ).first()

        gemini_assistant = db.query(GeminiAssistantConfig).filter(
            GeminiAssistantConfig.id == assistant_uuid,
            GeminiAssistantConfig.user_id == current_user.id
        ).first()

        cartesia_assistant = db.query(CartesiaAssistantConfig).filter(
            CartesiaAssistantConfig.id == assistant_uuid,
            CartesiaAssistantConfig.user_id == current_user.id
        ).first()

        if not openai_assistant and not gemini_assistant and not cartesia_assistant:
            logger.warning(f"[TASKS-API] Assistant {assistant_uuid} not found for user {current_user.id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant not found"
            )

        # Определяем тип ассистента
        if openai_assistant:
            assistant_type = "openai"
            assistant_name = openai_assistant.name
        elif gemini_assistant:
            assistant_type = "gemini"
            assistant_name = gemini_assistant.name
        else:
            assistant_type = "cartesia"
            assistant_name = cartesia_assistant.name
        
        logger.info(f"   Assistant type: {assistant_type}, name: {assistant_name}")
        
        # Парсим время
        try:
            # Пытаемся как ISO формат
            scheduled_time = datetime.fromisoformat(task_data.scheduled_time.replace('Z', '+00:00'))
        except:
            # Если не получилось - парсим как текст
            scheduled_time = parse_time_string(task_data.scheduled_time)
        
        if scheduled_time.tzinfo is not None:
            scheduled_time = scheduled_time.replace(tzinfo=None)
        
        # Проверяем что время в будущем
        if scheduled_time <= datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Scheduled time must be in the future"
            )
        
        # Создаем задачу с custom_greeting и caller_id
        new_task = Task(
            contact_id=contact_uuid,
            assistant_id=assistant_uuid if openai_assistant else None,
            gemini_assistant_id=assistant_uuid if gemini_assistant else None,
            cartesia_assistant_id=assistant_uuid if cartesia_assistant else None,
            user_id=current_user.id,
            scheduled_time=scheduled_time,
            title=task_data.title.strip(),
            description=task_data.description.strip() if task_data.description else None,
            custom_greeting=task_data.custom_greeting.strip() if task_data.custom_greeting else None,
            caller_id=task_data.caller_id.strip() if task_data.caller_id else None,
            status=TaskStatus.SCHEDULED
        )
        
        db.add(new_task)
        db.commit()
        db.refresh(new_task)
        
        result = new_task.to_dict()
        result['assistant_name'] = assistant_name
        result['assistant_type'] = assistant_type
        
        logger.info(f"✅ Task created: {new_task.id} ({assistant_type} assistant) for {scheduled_time}")
        if new_task.custom_greeting:
            logger.info(f"   💬 With custom greeting: {new_task.custom_greeting[:50]}...")
        
        return {
            "message": "Task created successfully",
            "task": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error creating task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create task: {str(e)}"
        )


@router.put("/tasks/{task_id}")
async def update_task(
    task_id: str,
    task_data: TaskUpdate,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Обновить существующую задачу.
    ✅ v3.4: Новый endpoint для редактирования задач
    
    Можно редактировать только задачи со статусом SCHEDULED или PENDING.
    
    **Параметры:**
    - task_id: UUID задачи
    
    **Body (все поля опциональные):**
    - assistant_id: UUID нового ассистента
    - scheduled_time: Новое время звонка
    - title: Новое название
    - description: Новое описание
    - custom_greeting: Новое приветствие
    
    **Возвращает:**
    - task: Обновленная задача
    """
    try:
        logger.info(f"[TASKS-API-v3.4] Update task {task_id}")
        
        # Проверяем UUID
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid task ID format"
            )
        
        # Получаем задачу
        task = db.query(Task).filter(Task.id == task_uuid).first()
        
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found"
            )
        
        # Проверяем доступ через контакт
        contact = db.query(Contact).filter(
            Contact.id == task.contact_id,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Проверяем что задачу можно редактировать
        if task.status not in [TaskStatus.SCHEDULED, TaskStatus.PENDING]:
            logger.warning(f"[TASKS-API] Cannot edit task with status {task.status.value}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot edit task with status {task.status.value}. Only SCHEDULED and PENDING tasks can be edited."
            )
        
        # Обновляем поля
        updated_fields = []
        
        # 1. Обновление ассистента
        if task_data.assistant_id is not None:
            try:
                new_assistant_uuid = UUID(task_data.assistant_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid assistant ID format"
                )
            
            # Проверяем новый ассистент во всех таблицах
            openai_assistant = db.query(AssistantConfig).filter(
                AssistantConfig.id == new_assistant_uuid,
                AssistantConfig.user_id == current_user.id
            ).first()

            gemini_assistant = db.query(GeminiAssistantConfig).filter(
                GeminiAssistantConfig.id == new_assistant_uuid,
                GeminiAssistantConfig.user_id == current_user.id
            ).first()

            cartesia_assistant = db.query(CartesiaAssistantConfig).filter(
                CartesiaAssistantConfig.id == new_assistant_uuid,
                CartesiaAssistantConfig.user_id == current_user.id
            ).first()

            if not openai_assistant and not gemini_assistant and not cartesia_assistant:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="New assistant not found"
                )

            # Обновляем правильное поле в зависимости от типа
            if openai_assistant:
                task.assistant_id = new_assistant_uuid
                task.gemini_assistant_id = None
                task.cartesia_assistant_id = None
                assistant_name = openai_assistant.name
                logger.info(f"   Updated assistant to OpenAI: {assistant_name}")
            elif gemini_assistant:
                task.assistant_id = None
                task.gemini_assistant_id = new_assistant_uuid
                task.cartesia_assistant_id = None
                assistant_name = gemini_assistant.name
                logger.info(f"   Updated assistant to Gemini: {assistant_name}")
            else:
                task.assistant_id = None
                task.gemini_assistant_id = None
                task.cartesia_assistant_id = new_assistant_uuid
                assistant_name = cartesia_assistant.name
                logger.info(f"   Updated assistant to Cartesia: {assistant_name}")
            
            updated_fields.append("assistant_id")
        
        # 2. Обновление времени
        if task_data.scheduled_time is not None:
            try:
                # Пытаемся как ISO формат
                new_scheduled_time = datetime.fromisoformat(task_data.scheduled_time.replace('Z', '+00:00'))
            except:
                # Если не получилось - парсим как текст
                new_scheduled_time = parse_time_string(task_data.scheduled_time)
            
            if new_scheduled_time.tzinfo is not None:
                new_scheduled_time = new_scheduled_time.replace(tzinfo=None)
            
            # Проверяем что время в будущем
            if new_scheduled_time <= datetime.utcnow():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Scheduled time must be in the future"
                )
            
            task.scheduled_time = new_scheduled_time
            updated_fields.append("scheduled_time")
            logger.info(f"   Updated scheduled_time to {new_scheduled_time}")
        
        # 3. Обновление названия
        if task_data.title is not None:
            task.title = task_data.title.strip()
            updated_fields.append("title")
            logger.info(f"   Updated title to: {task.title}")
        
        # 4. Обновление описания
        if task_data.description is not None:
            task.description = task_data.description.strip() if task_data.description else None
            updated_fields.append("description")
            logger.info(f"   Updated description")
        
        # 5. Обновление приветствия
        if task_data.custom_greeting is not None:
            task.custom_greeting = task_data.custom_greeting.strip() if task_data.custom_greeting else None
            updated_fields.append("custom_greeting")
            logger.info(f"   Updated custom_greeting")

        # 6. Обновление caller_id
        if task_data.caller_id is not None:
            task.caller_id = task_data.caller_id.strip() if task_data.caller_id else None
            updated_fields.append("caller_id")
            logger.info(f"   Updated caller_id to: {task.caller_id}")

        # Обновляем дату изменения
        task.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(task)
        
        # Формируем результат
        result = task.to_dict()
        
        # Определяем тип и получаем имя ассистента
        if task.assistant_id:
            assistant = db.query(AssistantConfig).filter(
                AssistantConfig.id == task.assistant_id
            ).first()
            result['assistant_name'] = assistant.name if assistant else "Unknown OpenAI"
            result['assistant_type'] = 'openai'
        elif task.gemini_assistant_id:
            gemini_assistant = db.query(GeminiAssistantConfig).filter(
                GeminiAssistantConfig.id == task.gemini_assistant_id
            ).first()
            result['assistant_name'] = gemini_assistant.name if gemini_assistant else "Unknown Gemini"
            result['assistant_type'] = 'gemini'
        elif task.cartesia_assistant_id:
            cartesia_asst = db.query(CartesiaAssistantConfig).filter(
                CartesiaAssistantConfig.id == task.cartesia_assistant_id
            ).first()
            result['assistant_name'] = cartesia_asst.name if cartesia_asst else "Unknown Cartesia"
            result['assistant_type'] = 'cartesia'
        else:
            result['assistant_name'] = "Unknown"
            result['assistant_type'] = 'unknown'

        logger.info(f"✅ Task updated: {task.id}")
        logger.info(f"   Updated fields: {', '.join(updated_fields)}")
        
        return {
            "message": "Task updated successfully",
            "task": result,
            "updated_fields": updated_fields
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error updating task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update task: {str(e)}"
        )


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Удалить задачу полностью из БД.
    ✅ v4.0: Полное удаление задачи (не отмена статуса)
    
    **Параметры:**
    - task_id: UUID задачи
    
    **Возвращает:**
    - Сообщение об успешном удалении
    """
    try:
        logger.info(f"[TASKS-API-v4.0] Delete task {task_id}")
        
        # Проверяем UUID
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid task ID format"
            )
        
        # Получаем задачу
        task = db.query(Task).filter(Task.id == task_uuid).first()
        
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found"
            )
        
        # Проверяем доступ через контакт
        contact = db.query(Contact).filter(
            Contact.id == task.contact_id,
            Contact.user_id == current_user.id
        ).first()
        
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Сохраняем информацию для лога
        task_title = task.title
        task_status = task.status.value if task.status else 'unknown'
        
        # ✅ v4.0: Полное удаление из БД
        db.delete(task)
        db.commit()
        
        logger.info(f"✅ Task permanently deleted: {task_id} (was: {task_status}, title: {task_title})")
        
        return {"message": "Task deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error deleting task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete task: {str(e)}"
        )
