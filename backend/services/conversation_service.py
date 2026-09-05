# backend/services/conversation_service.py
"""
Conversation service for WellcomeAI application.
Handles conversation tracking and analysis.
✅ v2.0: Extended with caller_number support and enhanced filtering
✅ v2.1: Auto-create contacts from phone calls (CRM integration)
✅ v3.1: Phone normalization and call direction extraction
✅ v3.2: Support for both OpenAI and Gemini assistants
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from sqlalchemy import desc
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import uuid
import traceback

from backend.core.logging import get_logger
from backend.models.conversation import Conversation
from backend.models.assistant import AssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig  # 🆕 v3.2
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.models.yandex_assistant import YandexAssistantConfig
from backend.models.grok_assistant import GrokAssistantConfig  # 🆕 cascade
from backend.models.fish_assistant import FishAssistantConfig  # 🆕 fish
from backend.models.function_log import FunctionLog
from backend.schemas.conversation import ConversationCreate, ConversationResponse, ConversationStats

logger = get_logger(__name__)


class ConversationService:
    """Service for conversation operations"""
    
    # ==================================================================================
    # 🆕 v3.2: HELPER METHOD - Find assistant in both tables
    # ==================================================================================
    
    # Таблицы ассистентов в порядке поиска по ID. Fish обязателен: без него
    # save_conversation отбрасывал диалоги fish-звонков как «ассистент не найден».
    _ASSISTANT_TABLES = (
        (AssistantConfig, "openai"),
        (GeminiAssistantConfig, "gemini"),
        (CartesiaAssistantConfig, "cartesia"),
        (YandexAssistantConfig, "yandex"),
        (FishAssistantConfig, "fish"),
        # Каскад и Grok живут в одной таблице и различаются assistant_type.
        (GrokAssistantConfig, "cascade"),
    )

    @staticmethod
    def _find_assistant_by_id(db: Session, assistant_id: str) -> tuple:
        """
        Ищет ассистента по ID в таблицах OpenAI, Gemini, Cartesia, Yandex, Fish и cascade.

        Args:
            db: Database session
            assistant_id: UUID ассистента как строка

        Returns:
            tuple: (assistant, assistant_type) где assistant_type = 'openai' | 'gemini' |
                   'cartesia' | 'yandex' | 'fish' | 'cascade' | None
        """
        try:
            assistant_uuid = uuid.UUID(assistant_id)
        except ValueError:
            assistant_uuid = None

        for model_cls, model_type in ConversationService._ASSISTANT_TABLES:
            if assistant_uuid is not None:
                assistant = db.query(model_cls).get(assistant_uuid)
            else:
                # Нестандартный формат id — сравниваем как строку.
                assistant = db.query(model_cls).filter(
                    model_cls.id.cast(str) == assistant_id
                ).first()
            if assistant:
                if model_cls is GrokAssistantConfig:
                    return assistant, (assistant.assistant_type or "cascade")
                return assistant, model_type

        return None, None
    
    # ==================================================================================
    # 🆕 HELPER METHODS - Phone normalization and call direction extraction
    # ==================================================================================
    
    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """
        Нормализует номер телефона, убирая префиксы OUTBOUND/INBOUND.
        
        Examples:
            "OUTBOUND: INBOUND: 79991234567" -> "79991234567"
            "INBOUND: 79601663217" -> "79601663217"
            "OUTBOUND: +79934409005" -> "79934409005"
            "+7 (999) 123-45-67" -> "79991234567"
        
        Returns:
            Normalized phone number or "unknown"
        """
        if not phone or phone == "unknown":
            return "unknown"
        
        # Убираем префиксы
        cleaned = phone.replace("OUTBOUND:", "").replace("INBOUND:", "").strip()
        
        # Убираем все символы кроме цифр
        cleaned = ''.join(c for c in cleaned if c.isdigit())
        
        # Если номер начинается с 8, заменяем на 7
        if cleaned.startswith('8') and len(cleaned) == 11:
            cleaned = '7' + cleaned[1:]
        
        return cleaned if cleaned else "unknown"
    
    @staticmethod
    def _extract_call_direction(phone: str) -> Optional[str]:
        """
        Извлекает направление звонка из префикса номера.
        
        Args:
            phone: Номер телефона с префиксом (например, "INBOUND: 79601663217")
        
        Returns:
            "INBOUND", "OUTBOUND", или None
        """
        if not phone:
            return None
        
        if "OUTBOUND:" in phone:
            return "OUTBOUND"
        elif "INBOUND:" in phone:
            return "INBOUND"
        
        return None
    
    # ==================================================================================
    # 🆕 HELPER METHOD - Auto-create contact from phone number
    # ==================================================================================
    
    @staticmethod
    def _get_or_create_contact(db: Session, user_id: uuid.UUID, phone: str) -> Optional[uuid.UUID]:
        """
        🆕 v3.1: Внутренний метод для автосоздания контакта из номера телефона.
        Использует нормализацию номера для предотвращения дублей.
        
        Args:
            db: Database session
            user_id: ID пользователя (владельца ассистента)
            phone: Номер телефона (будет нормализован)
            
        Returns:
            UUID контакта или None при ошибке
        """
        try:
            from backend.models.contact import Contact
            
            # ✅ НОРМАЛИЗУЕМ номер перед поиском
            normalized_phone = ConversationService._normalize_phone(phone)
            
            logger.info(f"[CRM-AUTO] Normalizing phone: '{phone}' -> '{normalized_phone}'")
            
            # Ищем по нормализованному номеру
            contact = db.query(Contact).filter(
                Contact.user_id == user_id,
                Contact.phone == normalized_phone
            ).first()
            
            if contact:
                # Обновляем время последнего контакта
                contact.last_interaction = datetime.utcnow()
                db.flush()
                logger.info(f"[CRM-AUTO] ✅ Found existing contact: {contact.id}")
                return contact.id
            else:
                # Создаем новый контакт с нормализованным номером
                new_contact = Contact(
                    user_id=user_id,
                    phone=normalized_phone,
                    status="new",
                    last_interaction=datetime.utcnow()
                )
                db.add(new_contact)
                db.flush()
                logger.info(f"[CRM-AUTO] ✅ Created new contact: {new_contact.id}")
                return new_contact.id
                
        except Exception as e:
            logger.error(f"[CRM-AUTO] ❌ Error creating contact: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    # ==================================================================================
    # 📦 LEGACY METHODS - Оригинальные методы (НЕ ТРОГАТЬ для совместимости)
    # ==================================================================================
    
    @staticmethod
    async def create_conversation(db: Session, conversation_data: ConversationCreate) -> ConversationResponse:
        """
        🔧 LEGACY: Create a new conversation record (оригинальный метод)
        
        Args:
            db: Database session
            conversation_data: Conversation creation data
            
        Returns:
            ConversationResponse for the new conversation
            
        Raises:
            HTTPException: If creation fails
        """
        try:
            # Verify assistant exists
            assistant = db.query(AssistantConfig).filter(AssistantConfig.id == conversation_data.assistant_id).first()
            if not assistant:
                logger.warning(f"Attempt to create conversation for non-existent assistant: {conversation_data.assistant_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Assistant not found"
                )
            
            # Create conversation instance
            conversation = Conversation(
                assistant_id=conversation_data.assistant_id,
                session_id=conversation_data.session_id,
                user_message=conversation_data.user_message,
                assistant_message=conversation_data.assistant_message,
                duration_seconds=conversation_data.duration_seconds,
                client_info=conversation_data.client_info,
                audio_duration=conversation_data.audio_duration
            )
            
            db.add(conversation)
            
            # Increment conversation count for assistant
            assistant.total_conversations += 1
            
            db.commit()
            db.refresh(conversation)
            
            logger.info(f"Conversation created: {conversation.id} for assistant {conversation_data.assistant_id}")
            
            return ConversationResponse(
                id=str(conversation.id),
                assistant_id=str(conversation.assistant_id),
                user_message=conversation.user_message,
                assistant_message=conversation.assistant_message,
                duration_seconds=conversation.duration_seconds,
                client_info=conversation.client_info,
                created_at=conversation.created_at,
                tokens_used=conversation.tokens_used,
                feedback_rating=conversation.feedback_rating,
                feedback_text=conversation.feedback_text,
                is_flagged=conversation.is_flagged
            )
            
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error creating conversation: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create conversation: {str(e)}"
            )
    
    @staticmethod
    async def get_conversations(
        db: Session, 
        assistant_id: str, 
        skip: int = 0, 
        limit: int = 50
    ) -> List[ConversationResponse]:
        """
        🔧 LEGACY: Get conversations for an assistant (оригинальный метод)
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            List of ConversationResponse objects
        """
        conversations = db.query(Conversation).filter(
            Conversation.assistant_id == assistant_id
        ).order_by(
            Conversation.created_at.desc()
        ).offset(skip).limit(limit).all()
        
        return [
            ConversationResponse(
                id=str(conv.id),
                assistant_id=str(conv.assistant_id),
                user_message=conv.user_message,
                assistant_message=conv.assistant_message,
                duration_seconds=conv.duration_seconds,
                client_info=conv.client_info,
                created_at=conv.created_at,
                tokens_used=conv.tokens_used,
                feedback_rating=conv.feedback_rating,
                feedback_text=conv.feedback_text,
                is_flagged=conv.is_flagged
            ) for conv in conversations
        ]
    
    @staticmethod
    async def get_conversation_stats(db: Session, assistant_id: str, user_id: Optional[str] = None, days: int = 30) -> ConversationStats:
        """
        🔧 LEGACY: Get conversation statistics for an assistant (оригинальный метод)
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            user_id: Optional user ID for filtering
            days: Number of days to calculate stats for
            
        Returns:
            ConversationStats object
        """
        # Get total conversations and tokens
        assistant = db.query(AssistantConfig).filter(AssistantConfig.id == assistant_id).first()
        if not assistant:
            logger.warning(f"Attempt to get stats for non-existent assistant: {assistant_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant not found"
            )
        
        total_conversations = assistant.total_conversations
        total_tokens = assistant.total_tokens
        
        # Get average duration
        avg_duration_result = db.query(func.avg(Conversation.duration_seconds)).filter(
            Conversation.assistant_id == assistant_id,
            Conversation.duration_seconds.isnot(None)
        ).scalar()
        avg_duration = float(avg_duration_result) if avg_duration_result else 0.0
        
        # Get time periods
        now = datetime.utcnow()
        today_start = datetime(now.year, now.month, now.day)
        week_start = today_start - timedelta(days=now.weekday())
        month_start = datetime(now.year, now.month, 1)
        
        # Count conversations by period
        conversations_today = db.query(Conversation).filter(
            Conversation.assistant_id == assistant_id,
            Conversation.created_at >= today_start
        ).count()
        
        conversations_this_week = db.query(Conversation).filter(
            Conversation.assistant_id == assistant_id,
            Conversation.created_at >= week_start
        ).count()
        
        conversations_this_month = db.query(Conversation).filter(
            Conversation.assistant_id == assistant_id,
            Conversation.created_at >= month_start
        ).count()
        
        return ConversationStats(
            total_conversations=total_conversations,
            total_tokens=total_tokens,
            avg_duration=avg_duration,
            conversations_today=conversations_today,
            conversations_this_week=conversations_this_week,
            conversations_this_month=conversations_this_month
        )
    
    @staticmethod
    async def add_feedback(db: Session, conversation_id: str, rating: int, feedback_text: Optional[str] = None) -> bool:
        """
        🔧 LEGACY: Add feedback to a conversation (оригинальный метод)
        
        Args:
            db: Database session
            conversation_id: Conversation ID
            rating: Feedback rating (1-5)
            feedback_text: Optional feedback text
            
        Returns:
            True if feedback was added successfully
            
        Raises:
            HTTPException: If operation fails
        """
        try:
            conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
            
            if not conversation:
                logger.warning(f"Attempt to add feedback to non-existent conversation: {conversation_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversation not found"
                )
            
            conversation.feedback_rating = rating
            conversation.feedback_text = feedback_text
            
            db.commit()
            
            logger.info(f"Feedback added to conversation {conversation_id}: rating={rating}")
            return True
            
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error adding feedback: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to add feedback: {str(e)}"
            )
    
    @staticmethod
    async def flag_conversation(db: Session, conversation_id: str, flagged: bool = True) -> bool:
        """
        🔧 LEGACY: Flag or unflag a conversation (оригинальный метод)
        
        Args:
            db: Database session
            conversation_id: Conversation ID
            flagged: Whether to flag or unflag
            
        Returns:
            True if operation was successful
            
        Raises:
            HTTPException: If operation fails
        """
        try:
            conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
            
            if not conversation:
                logger.warning(f"Attempt to flag non-existent conversation: {conversation_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversation not found"
                )
            
            conversation.is_flagged = flagged
            
            db.commit()
            
            logger.info(f"Conversation {conversation_id} {'flagged' if flagged else 'unflagged'}")
            return True
            
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error {'flagging' if flagged else 'unflagging'} conversation: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to {'flag' if flagged else 'unflag'} conversation: {str(e)}"
            )
    
    # ==================================================================================
    # 🆕 NEW METHODS v3.2 - С поддержкой OpenAI и Gemini ассистентов
    # ==================================================================================
    
    @staticmethod
    async def save_conversation(
        db: Session,
        assistant_id: str,
        user_message: str,
        assistant_message: str,
        session_id: Optional[str] = None,
        caller_number: Optional[str] = None,
        call_direction: Optional[str] = None,
        client_info: Optional[Dict[str, Any]] = None,
        audio_duration: Optional[float] = None,
        tokens_used: Optional[int] = 0
    ) -> Optional[Conversation]:
        """
        🆕 v3.2: Сохранить диалог в БД с автосозданием контакта и нормализацией номера.
        Поддерживает OpenAI И Gemini ассистентов.
        Используется для Voximplant и других внешних источников.
        
        Args:
            db: Database session
            assistant_id: ID ассистента (OpenAI или Gemini)
            user_message: Сообщение пользователя
            assistant_message: Ответ ассистента
            session_id: ID сессии (для группировки диалогов)
            caller_number: Номер телефона (может содержать префиксы INBOUND:/OUTBOUND:)
            call_direction: Направление звонка ("INBOUND"/"OUTBOUND") - будет извлечено автоматически если не указано
            client_info: Дополнительная информация о клиенте
            audio_duration: Длительность аудио
            tokens_used: Количество использованных токенов
            
        Returns:
            Conversation: Созданная запись диалога или None при ошибке
        """
        try:
            logger.info(f"[CONVERSATION-SERVICE-v3.2] Saving conversation for assistant {assistant_id}")
            logger.info(f"   User message length: {len(user_message) if user_message else 0} chars")
            logger.info(f"   Assistant message length: {len(assistant_message) if assistant_message else 0} chars")
            logger.info(f"   Caller number (raw): {caller_number}")
            logger.info(f"   Session ID: {session_id}")
            
            # Валидация assistant_id
            try:
                assistant_uuid = uuid.UUID(assistant_id)
            except ValueError:
                logger.error(f"Invalid assistant_id format: {assistant_id}")
                return None
            
            # 🆕 v3.2: Проверяем что ассистент существует (OpenAI ИЛИ Gemini)
            assistant, assistant_type = ConversationService._find_assistant_by_id(db, assistant_id)
            
            if not assistant:
                logger.error(f"Assistant not found in any table: {assistant_id}")
                return None
            
            logger.info(f"   Found {assistant_type} assistant: {assistant.name}")
            
            # 🆕 v3.1: ИЗВЛЕКАЕМ направление звонка из префикса
            if not call_direction and caller_number:
                call_direction = ConversationService._extract_call_direction(caller_number)
                logger.info(f"   Extracted call_direction: {call_direction}")
            
            # 🆕 v3.1: НОРМАЛИЗУЕМ номер телефона
            normalized_phone = ConversationService._normalize_phone(caller_number) if caller_number else "unknown"
            logger.info(f"   Normalized phone: {normalized_phone}")
            
            # 🆕 v3.1: АВТОСОЗДАНИЕ КОНТАКТА ИЗ НОМЕРА ТЕЛЕФОНА
            contact_id = None
            if normalized_phone and normalized_phone != "unknown":
                contact_id = ConversationService._get_or_create_contact(
                    db=db,
                    user_id=assistant.user_id,
                    phone=normalized_phone
                )
                
                if contact_id:
                    logger.info(f"[CRM-AUTO] ✅ Contact linked: {contact_id}")
                else:
                    logger.warning(f"[CRM-AUTO] ⚠️ Failed to create/link contact")
            
            # 🆕 v3.2: Добавляем assistant_type в client_info
            if client_info is None:
                client_info = {}
            client_info["assistant_type"] = assistant_type
            
            # Создаем запись диалога.
            # conversations.assistant_id ссылается на assistant_configs (OpenAI) — для Gemini
            # запись идёт в gemini_conversations, иначе INSERT падает по внешнему ключу.
            if assistant_type == "gemini":
                from backend.models.gemini_assistant import GeminiConversation
                conversation = GeminiConversation(
                    assistant_id=assistant_uuid,
                    session_id=session_id or str(uuid.uuid4()),
                    user_message=user_message or "",
                    assistant_message=assistant_message or "",
                    caller_number=normalized_phone,
                    tokens_used=tokens_used or 0
                )
            else:
                conversation = Conversation(
                    assistant_id=assistant_uuid,
                    session_id=session_id or str(uuid.uuid4()),
                    user_message=user_message or "",
                    assistant_message=assistant_message or "",
                    caller_number=normalized_phone,
                    call_direction=call_direction,
                    contact_id=contact_id,
                    client_info=client_info,
                    audio_duration=audio_duration,
                    tokens_used=tokens_used or 0
                )
            
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
            
            logger.info(f"✅ Conversation saved successfully: {conversation.id}")
            logger.info(f"   Assistant type: {assistant_type}")
            logger.info(f"   Direction: {call_direction}, Phone: {normalized_phone}, Contact: {contact_id}")
            
            return conversation
            
        except Exception as e:
            logger.error(f"❌ Error saving conversation: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            db.rollback()
            return None
    
    @staticmethod
    def get_conversations_advanced(
        db: Session,
        assistant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        caller_number: Optional[str] = None,
        session_id: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        🆕 v2.0: Получить список диалогов с расширенными фильтрами.
        
        Args:
            db: Database session
            assistant_id: Фильтр по ассистенту
            user_id: Фильтр по пользователю (владельцу ассистента)
            caller_number: Фильтр по номеру телефона
            session_id: Фильтр по сессии
            date_from: Фильтр - диалоги после этой даты
            date_to: Фильтр - диалоги до этой даты
            limit: Количество записей (макс 100)
            offset: Смещение для пагинации
            
        Returns:
            Dict с conversations, total, page, page_size
        """
        try:
            logger.info(f"[CONVERSATION-SERVICE-v2] Getting conversations with advanced filters")
            logger.info(f"   Filters: assistant_id={assistant_id}, user_id={user_id}, "
                       f"caller={caller_number}, session={session_id}")
            logger.info(f"   Pagination: limit={limit}, offset={offset}")
            
            # Ограничиваем limit
            limit = min(limit, 100)
            
            # Базовый запрос
            query = db.query(Conversation)
            
            # Фильтр по assistant_id
            if assistant_id:
                try:
                    assistant_uuid = uuid.UUID(assistant_id)
                    query = query.filter(Conversation.assistant_id == assistant_uuid)
                except ValueError:
                    logger.warning(f"Invalid assistant_id format: {assistant_id}")
                    return {"conversations": [], "total": 0, "page": 0, "page_size": limit}
            
            # Фильтр по user_id (через ассистента) - поддержка обоих типов
            if user_id:
                try:
                    user_uuid = uuid.UUID(user_id)
                    # Получаем ID всех ассистентов пользователя (OpenAI + Gemini + Cartesia)
                    openai_ids = db.query(AssistantConfig.id).filter(
                        AssistantConfig.user_id == user_uuid
                    ).all()
                    gemini_ids = db.query(GeminiAssistantConfig.id).filter(
                        GeminiAssistantConfig.user_id == user_uuid
                    ).all()
                    cartesia_ids = db.query(CartesiaAssistantConfig.id).filter(
                        CartesiaAssistantConfig.user_id == user_uuid
                    ).all()

                    all_assistant_ids = [a.id for a in openai_ids] + [a.id for a in gemini_ids] + [a.id for a in cartesia_ids]
                    
                    if all_assistant_ids:
                        query = query.filter(Conversation.assistant_id.in_(all_assistant_ids))
                    else:
                        # Нет ассистентов - возвращаем пустой результат
                        return {"conversations": [], "total": 0, "page": 0, "page_size": limit}
                        
                except ValueError:
                    logger.warning(f"Invalid user_id format: {user_id}")
                    return {"conversations": [], "total": 0, "page": 0, "page_size": limit}
            
            # Фильтр по номеру телефона
            if caller_number:
                query = query.filter(Conversation.caller_number == caller_number)
            
            # Фильтр по session_id
            if session_id:
                query = query.filter(Conversation.session_id == session_id)
            
            # Фильтр по датам
            if date_from:
                query = query.filter(Conversation.created_at >= date_from)
            if date_to:
                query = query.filter(Conversation.created_at <= date_to)
            
            # Общее количество
            total = query.count()
            
            # Сортировка и пагинация
            conversations = query.order_by(desc(Conversation.created_at)).limit(limit).offset(offset).all()
            
            logger.info(f"✅ Found {len(conversations)} conversations (total: {total})")
            
            return {
                "conversations": [conv.to_dict() for conv in conversations],
                "total": total,
                "page": offset // limit if limit > 0 else 0,
                "page_size": limit
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting conversations: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"conversations": [], "total": 0, "page": 0, "page_size": limit}
    
    @staticmethod
    def get_conversation_detail(
        db: Session,
        conversation_id: str,
        include_functions: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        🆕 v2.0: Получить детали диалога с логами функций для карточки в ЛК.
        
        Args:
            db: Database session
            conversation_id: ID диалога
            include_functions: Включить логи вызовов функций
            
        Returns:
            Dict с деталями диалога или None
        """
        try:
            logger.info(f"[CONVERSATION-SERVICE-v2] Getting conversation detail: {conversation_id}")
            
            # Валидация ID
            try:
                conv_uuid = uuid.UUID(conversation_id)
            except ValueError:
                logger.error(f"Invalid conversation_id format: {conversation_id}")
                return None
            
            # Получаем диалог
            conversation = db.query(Conversation).get(conv_uuid)
            if not conversation:
                logger.warning(f"Conversation not found: {conversation_id}")
                return None
            
            # Базовая информация
            result = conversation.to_dict()
            
            # Добавляем логи функций если запрошено
            if include_functions:
                function_logs = db.query(FunctionLog).filter(
                    FunctionLog.conversation_id == conv_uuid
                ).order_by(FunctionLog.created_at).all()
                
                result["function_calls"] = [
                    {
                        "id": str(log.id),
                        "function_name": log.function_name,
                        "arguments": log.arguments,
                        "result": log.result,
                        "status": log.status,
                        "created_at": log.created_at.isoformat() if log.created_at else None
                    }
                    for log in function_logs
                ]
                
                logger.info(f"   Found {len(function_logs)} function calls")
            
            logger.info(f"✅ Conversation detail retrieved successfully")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Error getting conversation detail: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
