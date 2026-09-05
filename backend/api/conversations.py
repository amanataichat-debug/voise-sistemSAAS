# backend/api/conversations.py
"""
Conversations API endpoints для WellcomeAI application.
Управление диалогами и историей разговоров.

Version: 3.6 - Yandex assistants + call log/record links in session cards
🆕 v2.0: Added OpenAI + Gemini support
🆕 v3.0: Added call_cost (стоимость звонка) и record_url (ссылка на запись) в ответы API
🆕 v3.1: STRUCTURED DIALOG - каждая реплика отдельным пузырьком в UI (backward compatible)
🆕 v3.2: Function calls загружаются в список сессий + привязываются к сообщениям по времени
🆕 v3.3: FIX - function_logs теперь ищутся и в gemini_conversations (не только в conversations)
🆕 v3.4: FIX - убран caller_number из GROUP BY, теперь используется MAX() агрегация
🆕 v3.5: FIX - Правильный preview (первое сообщение по времени, а не MIN())
         FIX - Нормализация caller_number ("unknown" → None)
         FIX - Фильтрация системных сообщений из preview
🆕 v3.6: Диалоги Яндекс-ассистентов в /sessions и детальном просмотре
         record_url и log_url (лог звонка Voximplant) в карточках сессий
         Fallback: log_url для старых звонков дотягивается из GetCallHistory
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, case, or_, text, select, union_all, null, cast, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from collections import defaultdict

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.services.conversation_service import ConversationService
from backend.services.auth_service import AuthService
from backend.models.user import User
from backend.models.conversation import Conversation
from backend.models.assistant import AssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.models.yandex_assistant import YandexAssistantConfig
from backend.models.grok_assistant import GrokAssistantConfig  # 🆕 cascade
from backend.models.fish_assistant import FishAssistantConfig, FishConversation  # 🆕 fish
from backend.models.function_log import FunctionLog

logger = get_logger(__name__)

# Create router
router = APIRouter()


class _MessageView:
    """
    Единый вид записи диалога для таблиц conversations, gemini_conversations и
    fish_conversations. У таблиц провайдеров нет client_info / duration_seconds /
    call_cost — здесь они None, чтобы общий код деталей и удаления не менялся.
    """

    def __init__(self, row):
        self.id = row.id
        self.assistant_id = row.assistant_id
        self.session_id = row.session_id
        self.user_message = row.user_message
        self.assistant_message = row.assistant_message
        self.caller_number = row.caller_number
        self.tokens_used = row.tokens_used
        self.created_at = row.created_at
        self.client_info = getattr(row, "client_info", None)
        self.duration_seconds = getattr(row, "duration_seconds", None)
        self.call_cost = getattr(row, "call_cost", None)
        self.call_direction = getattr(row, "call_direction", None)
        self._row = row  # ORM-запись, если нужно что-то дописать и закоммитить


def _find_session_record(db: Session, conversation_id: str):
    """
    Найти запись сессии по session_id или id сообщения: сначала в conversations,
    затем в gemini_conversations и fish_conversations. Возвращает (record, model) или (None, None).
    """
    for model in (Conversation, GeminiConversation, FishConversation):
        record = db.query(model).filter(model.session_id == conversation_id).first()
        if not record:
            try:
                conv_uuid = UUID(conversation_id)
                record = db.query(model).filter(model.id == conv_uuid).first()
            except ValueError:
                record = None
        if record:
            return record, model
    return None, None


def _preview_sql(table_name: str):
    return text(f"""
        SELECT DISTINCT ON (session_id)
            session_id,
            COALESCE(
                NULLIF(
                    CASE
                        WHEN LOWER(TRIM(user_message)) IN ('[no user input]', '[no_user_input]', '[system]', '[silence]', '[timeout]')
                        THEN NULL
                        ELSE user_message
                    END,
                    ''
                ),
                NULLIF(assistant_message, '')
            ) as preview
        FROM {table_name}
        WHERE session_id = ANY(:session_ids)
        ORDER BY session_id, created_at ASC
    """)


def _sessions_select(model, user_assistant_ids, assistant_uuid, caller_number, date_from_parsed, date_to_parsed):
    """
    Группировка сообщений в сессии для одной таблицы. Набор колонок одинаковый для
    conversations, gemini_conversations и fish_conversations, чтобы результаты можно
    было объединить UNION ALL.
    """
    rich = model is Conversation
    created = model.created_at if rich else cast(model.created_at, DateTime(timezone=True))
    q = (
        select(
            model.session_id.label("session_id"),
            model.assistant_id.label("assistant_id"),
            func.max(model.caller_number).label("caller_number"),
            func.count(model.id).label("messages_count"),
            func.min(created).label("created_at"),
            func.max(created).label("updated_at"),
            func.sum(model.tokens_used).label("total_tokens"),
            (func.sum(model.duration_seconds) if rich else null()).label("total_duration"),
            (func.sum(model.call_cost) if rich else null()).label("total_cost"),
            (func.max(model.client_info.op("->>")("record_url")) if rich else null()).label("record_url"),
            (func.max(model.client_info.op("->>")("log_url")) if rich else null()).label("log_url"),
        )
        .where(model.assistant_id.in_(user_assistant_ids))
        .group_by(model.session_id, model.assistant_id)
    )
    if assistant_uuid:
        q = q.where(model.assistant_id == assistant_uuid)
    if caller_number:
        q = q.having(func.max(model.caller_number) == caller_number)
    if date_from_parsed:
        q = q.having(func.min(created) >= date_from_parsed)
    if date_to_parsed:
        q = q.having(func.max(created) <= date_to_parsed)
    return q


# =============================================================================
# 🆕 v3.5: Константы для фильтрации системных сообщений
# =============================================================================

SYSTEM_MESSAGE_PATTERNS = [
    '[no user input]',
    '[no_user_input]',
    '[system]',
    '[silence]',
    '[timeout]',
]


# =============================================================================
# 🆕 v2.0: Helper functions for OpenAI + Gemini support
# =============================================================================

def get_user_assistant_ids(db: Session, user_id: UUID) -> List[UUID]:
    """
    Получить все ID ассистентов пользователя (OpenAI + Gemini + Cartesia + Yandex + cascade).

    Returns:
        List[UUID]: Список всех assistant_id
    """
    # OpenAI assistants
    openai_ids = db.query(AssistantConfig.id).filter(
        AssistantConfig.user_id == user_id
    ).all()

    # Gemini assistants
    gemini_ids = db.query(GeminiAssistantConfig.id).filter(
        GeminiAssistantConfig.user_id == user_id
    ).all()

    # Cartesia assistants
    cartesia_ids = db.query(CartesiaAssistantConfig.id).filter(
        CartesiaAssistantConfig.user_id == user_id
    ).all()

    # Yandex assistants (их телефонные диалоги тоже пишутся в conversations)
    yandex_ids = db.query(YandexAssistantConfig.id).filter(
        YandexAssistantConfig.user_id == user_id
    ).all()

    # Cascade assistants (GrokAssistantConfig с assistant_type='cascade').
    # Их звонки телефонии пишутся в conversations под grok-id.
    cascade_ids = db.query(GrokAssistantConfig.id).filter(
        GrokAssistantConfig.user_id == user_id,
        GrokAssistantConfig.assistant_type == "cascade"
    ).all()

    # Fish assistants (диалог ведёт OpenAI Realtime в сценарии, озвучка — Fish;
    # звонок логируется в conversations под fish-id).
    fish_ids = db.query(FishAssistantConfig.id).filter(
        FishAssistantConfig.user_id == user_id
    ).all()

    all_ids = (
        [a.id for a in openai_ids]
        + [a.id for a in gemini_ids]
        + [a.id for a in cartesia_ids]
        + [a.id for a in yandex_ids]
        + [a.id for a in cascade_ids]
        + [a.id for a in fish_ids]
    )

    return all_ids


def find_assistant_by_id(db: Session, assistant_id: UUID):
    """
    Найти ассистента по ID в таблицах OpenAI, Gemini, Cartesia и Yandex.

    Returns:
        tuple: (assistant, assistant_type) где type = 'openai' | 'gemini' | 'cartesia' | 'yandex' | None
    """
    # Try OpenAI first
    assistant = db.query(AssistantConfig).filter(
        AssistantConfig.id == assistant_id
    ).first()

    if assistant:
        return assistant, 'openai'

    # Try Gemini
    assistant = db.query(GeminiAssistantConfig).filter(
        GeminiAssistantConfig.id == assistant_id
    ).first()

    if assistant:
        return assistant, 'gemini'

    # Try Cartesia
    assistant = db.query(CartesiaAssistantConfig).filter(
        CartesiaAssistantConfig.id == assistant_id
    ).first()

    if assistant:
        return assistant, 'cartesia'

    # Try Yandex
    assistant = db.query(YandexAssistantConfig).filter(
        YandexAssistantConfig.id == assistant_id
    ).first()

    if assistant:
        return assistant, 'yandex'

    # Try Fish
    assistant = db.query(FishAssistantConfig).filter(
        FishAssistantConfig.id == assistant_id
    ).first()

    if assistant:
        return assistant, 'fish'

    # Try cascade (GrokAssistantConfig)
    assistant = db.query(GrokAssistantConfig).filter(
        GrokAssistantConfig.id == assistant_id
    ).first()

    if assistant:
        return assistant, assistant.assistant_type or 'cascade'

    return None, None


def normalize_caller_number(caller_number: Optional[str]) -> Optional[str]:
    """
    🆕 v3.5: Нормализует caller_number.
    
    Возвращает None для:
    - None
    - Пустая строка
    - "unknown"
    - "null"
    
    Returns:
        str | None: Нормализованный номер или None
    """
    if not caller_number:
        return None
    
    trimmed = caller_number.strip().lower()
    
    if trimmed in ('', 'unknown', 'null', 'none', 'undefined'):
        return None
    
    return caller_number.strip()


def is_system_message(text: Optional[str]) -> bool:
    """
    🆕 v3.5: Проверяет, является ли текст системным сообщением.
    
    Returns:
        bool: True если это системное сообщение
    """
    if not text:
        return True
    
    trimmed = text.strip().lower()
    
    if not trimmed:
        return True
    
    for pattern in SYSTEM_MESSAGE_PATTERNS:
        if trimmed == pattern.lower():
            return True
    
    return False


def get_clean_text(text: Optional[str]) -> Optional[str]:
    """
    🆕 v3.5: Возвращает текст если он не системный, иначе None.
    """
    if is_system_message(text):
        return None
    return text.strip() if text else None


def attach_functions_to_messages(messages: List[dict], function_calls: List[dict]) -> List[dict]:
    """
    🆕 v3.2: Привязывает функции к ближайшему предшествующему сообщению ассистента.
    
    Логика:
    - Каждая функция привязывается к последнему сообщению ассистента,
      которое было ДО или ВО ВРЕМЯ вызова функции
    - Если такого нет - привязываем к первому сообщению ассистента
    
    Args:
        messages: Список сообщений с полями type, timestamp
        function_calls: Список вызовов функций с полем created_at
    
    Returns:
        messages с добавленным полем function_calls для каждого сообщения
    """
    # Инициализируем function_calls для всех сообщений
    for msg in messages:
        msg['function_calls'] = []
    
    if not function_calls:
        return messages
    
    # Сортируем функции по времени
    sorted_functions = sorted(
        function_calls, 
        key=lambda f: f.get('created_at') or ''
    )
    
    # Находим сообщения ассистента
    assistant_messages = [m for m in messages if m['type'] == 'assistant']
    
    if not assistant_messages:
        return messages
    
    for func in sorted_functions:
        func_time = func.get('created_at')
        if not func_time:
            # Если нет времени - привязываем к первому сообщению ассистента
            assistant_messages[0]['function_calls'].append(func)
            continue
        
        # Находим последнее сообщение ассистента ДО или ВО ВРЕМЯ вызова функции
        best_match = None
        for msg in assistant_messages:
            msg_time = msg.get('timestamp')
            if msg_time and msg_time <= func_time:
                best_match = msg
        
        # Если не нашли - привязываем к первому сообщению ассистента
        if best_match is None:
            best_match = assistant_messages[0]
        
        best_match['function_calls'].append(func)
    
    return messages


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/sessions")
async def get_conversation_sessions(
    assistant_id: Optional[str] = Query(None, description="Фильтр по ID ассистента"),
    caller_number: Optional[str] = Query(None, description="Фильтр по номеру телефона"),
    date_from: Optional[str] = Query(None, description="Фильтр: диалоги после даты (ISO format)"),
    date_to: Optional[str] = Query(None, description="Фильтр: диалоги до даты (ISO format)"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей (макс 100)"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    🆕 v3.5: Получить список СЕССИЙ (группированных диалогов).
    Поддерживает OpenAI И Gemini ассистентов.
    Включает call_cost (стоимость), record_url (запись звонка) и function_calls.
    
    Каждая сессия = одна карточка диалога на фронте.
    Группирует все сообщения по session_id.
    
    🆕 v3.5 FIX: Preview теперь берётся из ПЕРВОГО сообщения по времени
    🆕 v3.5 FIX: caller_number нормализуется ("unknown" → null)
    🆕 v3.5 FIX: Системные сообщения фильтруются из preview
    
    Требуется авторизация.
    
    **Фильтры:**
    - assistant_id: Показать только диалоги конкретного ассистента
    - caller_number: Показать диалоги с конкретным номером телефона
    - date_from/date_to: Временной диапазон
    
    **Пагинация:**
    - limit: Количество записей на странице (1-100)
    - offset: Смещение (для следующих страниц)
    
    **Возвращает:**
    - conversations: Список сессий (группированных диалогов)
    - total: Общее количество сессий
    - page: Текущая страница
    - page_size: Размер страницы
    """
    try:
        logger.info(f"[CONVERSATIONS-API-v3.5] Get sessions request from user {current_user.id}")
        logger.info(f"   Filters: assistant_id={assistant_id}, caller={caller_number}, "
                   f"date_from={date_from}, date_to={date_to}")
        logger.info(f"   Pagination: limit={limit}, offset={offset}")
        
        # Парсим даты если указаны
        date_from_parsed = None
        date_to_parsed = None
        
        if date_from:
            try:
                date_from_parsed = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"Invalid date_from format: {date_from}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid date_from format. Use ISO format (YYYY-MM-DDTHH:MM:SS)"
                )
        
        if date_to:
            try:
                date_to_parsed = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"Invalid date_to format: {date_to}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid date_to format. Use ISO format (YYYY-MM-DDTHH:MM:SS)"
                )
        
        # 🆕 v2.0: Получаем ВСЕ assistant_id пользователя (OpenAI + Gemini)
        user_assistant_ids = get_user_assistant_ids(db, current_user.id)
        
        if not user_assistant_ids:
            logger.info("   User has no assistants")
            return {
                "conversations": [],
                "total": 0,
                "page": 0,
                "page_size": limit
            }
        
        logger.info(f"   User has {len(user_assistant_ids)} assistants (OpenAI + Gemini + Cartesia)")

        # Создаём sets ID для быстрого определения типа
        gemini_ids = db.query(GeminiAssistantConfig.id).filter(
            GeminiAssistantConfig.user_id == current_user.id
        ).all()
        gemini_id_set = {str(g.id) for g in gemini_ids}

        cartesia_ids = db.query(CartesiaAssistantConfig.id).filter(
            CartesiaAssistantConfig.user_id == current_user.id
        ).all()
        cartesia_id_set = {str(c.id) for c in cartesia_ids}

        yandex_ids = db.query(YandexAssistantConfig.id).filter(
            YandexAssistantConfig.user_id == current_user.id
        ).all()
        yandex_id_set = {str(y.id) for y in yandex_ids}

        cascade_ids = db.query(GrokAssistantConfig.id).filter(
            GrokAssistantConfig.user_id == current_user.id,
            GrokAssistantConfig.assistant_type == "cascade"
        ).all()
        cascade_id_set = {str(c.id) for c in cascade_ids}

        fish_ids = db.query(FishAssistantConfig.id).filter(
            FishAssistantConfig.user_id == current_user.id
        ).all()
        fish_id_set = {str(f.id) for f in fish_ids}

        # =============================================================================
        # 🆕 v3.6: Сессии из conversations (OpenAI и др.) + gemini_conversations одним
        # UNION ALL — честная сортировка и пагинация по обеим таблицам.
        # =============================================================================
        assistant_uuid = None
        if assistant_id:
            try:
                assistant_uuid = UUID(assistant_id)
            except ValueError:
                logger.warning(f"Invalid assistant_id format: {assistant_id}")
                return {
                    "conversations": [],
                    "total": 0,
                    "page": 0,
                    "page_size": limit
                }

        union_query = union_all(
            _sessions_select(Conversation, user_assistant_ids, assistant_uuid, caller_number, date_from_parsed, date_to_parsed),
            _sessions_select(GeminiConversation, user_assistant_ids, assistant_uuid, caller_number, date_from_parsed, date_to_parsed),
            _sessions_select(FishConversation, user_assistant_ids, assistant_uuid, caller_number, date_from_parsed, date_to_parsed),
        ).subquery("sessions")

        # Подсчет общего количества
        total = db.execute(select(func.count()).select_from(union_query)).scalar()

        # Сортировка и пагинация
        sessions = db.execute(
            select(union_query)
            .order_by(desc(union_query.c.updated_at))
            .limit(limit)
            .offset(offset)
        ).all()

        logger.info(f"✅ Found {len(sessions)} sessions (total: {total})")
        
        # =============================================================================
        # 🆕 v3.5: Загружаем правильные preview - ПЕРВОЕ сообщение по времени
        # Используем PostgreSQL DISTINCT ON для эффективности
        # =============================================================================
        session_ids = [s.session_id for s in sessions]
        preview_map = {}
        
        if session_ids:
            # PostgreSQL DISTINCT ON - берём первую запись для каждой сессии по времени,
            # из обеих таблиц (conversations и gemini_conversations)
            try:
                for table_name in ("conversations", "gemini_conversations", "fish_conversations"):
                    preview_results = db.execute(_preview_sql(table_name), {"session_ids": session_ids}).fetchall()
                    for row in preview_results:
                        if row.preview and row.session_id not in preview_map:
                            preview_map[row.session_id] = row.preview
                logger.info(f"   📝 Loaded {len(preview_map)} previews via DISTINCT ON")
            except Exception as e:
                logger.warning(f"   ⚠️ DISTINCT ON failed, using fallback: {e}")
                db.rollback()
                # Fallback - загружаем по одному (медленнее, но работает везде)
                for session_id in session_ids:
                    first_msg = None
                    for model in (Conversation, GeminiConversation, FishConversation):
                        first_msg = db.query(model).filter(
                            model.session_id == session_id
                        ).order_by(model.created_at.asc()).first()
                        if first_msg:
                            break
                    
                    if first_msg:
                        preview = get_clean_text(first_msg.user_message) or get_clean_text(first_msg.assistant_message)
                        if preview:
                            preview_map[session_id] = preview
        
        # =============================================================================
        # 🆕 v3.2: Загружаем function_calls для всех сессий одним запросом
        # =============================================================================
        
        # Получаем все conversation_id для этих сессий (OpenAI)
        conv_ids_query = db.query(Conversation.id, Conversation.session_id).filter(
            Conversation.session_id.in_(session_ids)
        ).all()
        
        # Маппинг conversation_id -> session_id
        conv_to_session = {str(c.id): c.session_id for c in conv_ids_query}
        conv_ids = [c.id for c in conv_ids_query]
        
        # 🆕 v3.3 FIX: Также получаем conversation_id из gemini_conversations
        gemini_conv_query = db.query(GeminiConversation.id, GeminiConversation.session_id).filter(
            GeminiConversation.session_id.in_(session_ids)
        ).all()
        
        for gc in gemini_conv_query:
            conv_to_session[str(gc.id)] = gc.session_id
            conv_ids.append(gc.id)

        # Fish: журнал в fish_conversations, function_logs привязаны к его id
        fish_conv_query = db.query(FishConversation.id, FishConversation.session_id).filter(
            FishConversation.session_id.in_(session_ids)
        ).all()
        for fc in fish_conv_query:
            conv_to_session[str(fc.id)] = fc.session_id
            conv_ids.append(fc.id)
        
        logger.info(f"   🔧 Total conversation IDs for function lookup: {len(conv_ids)} (OpenAI: {len(conv_ids_query)}, Gemini: {len(gemini_conv_query)})")
        
        # Загружаем все function_logs для этих conversations
        function_logs = []
        if conv_ids:
            function_logs = db.query(FunctionLog).filter(
                FunctionLog.conversation_id.in_(conv_ids)
            ).order_by(FunctionLog.created_at).all()
        
        # Группируем function_logs по session_id
        logs_by_session = defaultdict(list)
        for log in function_logs:
            session_id = conv_to_session.get(str(log.conversation_id))
            if session_id:
                logs_by_session[session_id].append({
                    "id": str(log.id),
                    "function_name": log.function_name,
                    "arguments": log.arguments,
                    "result": log.result,
                    "status": log.status,
                    "execution_time_ms": log.execution_time_ms,
                    "error_message": log.error_message,
                    "created_at": log.created_at.isoformat() if log.created_at else None
                })
        
        logger.info(f"   Loaded {len(function_logs)} function logs for {len(logs_by_session)} sessions")

        # =============================================================================
        # Резолвим имена ассистентов по assistant_id (все типы, включая cascade).
        # Возвращаем имя прямо из бэкенда, чтобы фронт не зависел от клиентского
        # поиска (у cascade он не срабатывал → "Неизвестный ассистент").
        # =============================================================================
        unique_assistant_ids = list({s.assistant_id for s in sessions})
        name_map = {}
        if unique_assistant_ids:
            for model in (
                AssistantConfig,
                GeminiAssistantConfig,
                CartesiaAssistantConfig,
                YandexAssistantConfig,
                GrokAssistantConfig,
                FishAssistantConfig,
            ):
                rows = db.query(model.id, model.name).filter(
                    model.id.in_(unique_assistant_ids)
                ).all()
                for row in rows:
                    name_map[str(row.id)] = row.name

        # =============================================================================
        # Форматируем результат
        # 🆕 v3.5: Используем preview_map и нормализуем caller_number
        # =============================================================================
        conversations = []
        for s in sessions:
            # Определяем тип по ID ассистента
            if str(s.assistant_id) in gemini_id_set:
                assistant_type = 'gemini'
            elif str(s.assistant_id) in cartesia_id_set:
                assistant_type = 'cartesia'
            elif str(s.assistant_id) in yandex_id_set:
                assistant_type = 'yandex'
            elif str(s.assistant_id) in cascade_id_set:
                assistant_type = 'cascade'
            elif str(s.assistant_id) in fish_id_set:
                assistant_type = 'fish'
            else:
                assistant_type = 'openai'
            
            # 🆕 v3.0: Форматируем стоимость
            call_cost = None
            if s.total_cost is not None and s.total_cost > 0:
                call_cost = round(float(s.total_cost), 2)
            
            # 🆕 v3.5: Нормализуем caller_number
            normalized_caller = normalize_caller_number(s.caller_number)
            
            # 🆕 v3.5: Берём preview из предзагруженного словаря
            preview_text = preview_map.get(s.session_id, "")
            
            conversations.append({
                "id": s.session_id,
                "session_id": s.session_id,
                "assistant_id": str(s.assistant_id),
                "assistant_name": name_map.get(str(s.assistant_id)),
                "caller_number": normalized_caller,  # 🆕 v3.5: Нормализованный
                "messages_count": s.messages_count,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "user_message": (preview_text or "")[:200],  # 🆕 v3.5: Правильный preview
                "assistant_message": "",
                "tokens_used": s.total_tokens or 0,
                "duration_seconds": s.total_duration or 0,
                "call_cost": call_cost,
                "record_url": s.record_url,
                "log_url": s.log_url,
                "client_info": {"assistant_type": assistant_type},
                "function_calls": logs_by_session.get(s.session_id, [])
            })
        
        return {
            "conversations": conversations,
            "total": total,
            "page": offset // limit if limit > 0 else 0,
            "page_size": limit
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting sessions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get conversation sessions: {str(e)}"
        )


@router.get("/")
async def get_conversations(
    assistant_id: Optional[str] = Query(None, description="Фильтр по ID ассистента"),
    caller_number: Optional[str] = Query(None, description="Фильтр по номеру телефона"),
    session_id: Optional[str] = Query(None, description="Фильтр по ID сессии"),
    date_from: Optional[str] = Query(None, description="Фильтр: диалоги после даты (ISO format)"),
    date_to: Optional[str] = Query(None, description="Фильтр: диалоги до даты (ISO format)"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей (макс 100)"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить список диалогов с фильтрами и пагинацией.
    
    ⚠️ DEPRECATED: Используйте /sessions для группировки по сессиям.
    Этот endpoint возвращает отдельные записи сообщений.
    
    🆕 v2.0: Поддерживает OpenAI и Gemini ассистентов.
    
    Требуется авторизация.
    
    **Фильтры:**
    - assistant_id: Показать только диалоги конкретного ассистента
    - caller_number: Показать диалоги с конкретным номером телефона
    - session_id: Показать диалоги из одной сессии
    - date_from/date_to: Временной диапазон
    
    **Пагинация:**
    - limit: Количество записей на странице (1-100)
    - offset: Смещение (для следующих страниц)
    
    **Возвращает:**
    - conversations: Список диалогов
    - total: Общее количество
    - page: Текущая страница
    - page_size: Размер страницы
    """
    try:
        logger.info(f"[CONVERSATIONS-API-v3.5] Get conversations request from user {current_user.id}")
        logger.info(f"   Filters: assistant_id={assistant_id}, caller={caller_number}, "
                   f"session={session_id}, date_from={date_from}, date_to={date_to}")
        logger.info(f"   Pagination: limit={limit}, offset={offset}")
        
        # Парсим даты если указаны
        date_from_parsed = None
        date_to_parsed = None
        
        if date_from:
            try:
                date_from_parsed = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            except ValueError as e:
                logger.warning(f"Invalid date_from format: {date_from}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid date_from format. Use ISO format (YYYY-MM-DDTHH:MM:SS)"
                )
        
        if date_to:
            try:
                date_to_parsed = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            except ValueError as e:
                logger.warning(f"Invalid date_to format: {date_to}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid date_to format. Use ISO format (YYYY-MM-DDTHH:MM:SS)"
                )
        
        # 🆕 v2.0: ConversationService.get_conversations_advanced уже поддерживает оба типа
        result = ConversationService.get_conversations_advanced(
            db=db,
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            caller_number=caller_number,
            session_id=session_id,
            date_from=date_from_parsed,
            date_to=date_to_parsed,
            limit=limit,
            offset=offset
        )
        
        logger.info(f"✅ Returned {len(result['conversations'])} conversations (total: {result['total']})")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting conversations: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get conversations: {str(e)}"
        )


@router.get("/{conversation_id}")
async def get_conversation_detail(
    conversation_id: str,
    include_functions: bool = Query(True, description="Включить логи вызовов функций"),
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить ПОЛНЫЙ диалог (все сообщения из сессии).
    
    🆕 v2.0: Поддерживает OpenAI и Gemini ассистентов.
    🆕 v3.0: Включает call_cost и record_url.
    🆕 v3.1: STRUCTURED DIALOG - если в client_info есть dialog[], 
             каждая реплика возвращается отдельным пузырьком.
    🆕 v3.2: Function calls привязываются к сообщениям по времени.
    🆕 v3.5: Фильтрация системных сообщений, нормализация caller_number.
    
    Требуется авторизация. Можно получить только свои диалоги.
    
    **Параметры:**
    - conversation_id: UUID любого сообщения из диалога ИЛИ session_id
    - include_functions: Включить список вызванных функций (по умолчанию true)
    
    **Возвращает:**
    - messages: Массив всех сообщений из сессии
    - assistant_id, assistant_name, assistant_type
    - session_id, caller_number
    - total_tokens, total_duration
    - call_cost, record_url
    - function_calls
    - has_structured_dialog
    """
    try:
        logger.info(f"[CONVERSATIONS-API-v3.5] Get full dialog for: {conversation_id}")
        logger.info(f"   User: {current_user.id}")
        
        # Ищем сессию в conversations, затем в gemini_conversations
        record, source_model = _find_session_record(db, conversation_id)
        
        if not record:
            logger.warning(f"Conversation not found: {conversation_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found"
            )
        conversation = _MessageView(record)
        
        # Проверяем права доступа
        assistant, assistant_type = find_assistant_by_id(db, conversation.assistant_id)
        
        if not assistant:
            logger.warning(f"Assistant not found for conversation: {conversation_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant not found"
            )
        
        if str(assistant.user_id) != str(current_user.id):
            logger.warning(f"Access denied: conversation {conversation_id} doesn't belong to user {current_user.id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: this conversation doesn't belong to you"
            )
        
        # Загружаем ВСЕ сообщения из этой сессии (из той таблицы, где она нашлась)
        session_id = conversation.session_id
        
        all_messages = [
            _MessageView(row) for row in db.query(source_model).filter(
                source_model.session_id == session_id,
                source_model.assistant_id == conversation.assistant_id
            ).order_by(source_model.created_at.asc()).all()
        ]
        
        logger.info(f"   Found {len(all_messages)} DB records in session {session_id} ({source_model.__tablename__})")
        logger.info(f"   Assistant type: {assistant_type}")
        
        # =============================================================================
        # 🆕 v3.1: STRUCTURED DIALOG SUPPORT + v3.5: Фильтрация системных сообщений
        # =============================================================================
        messages = []
        total_tokens = 0
        total_duration = 0
        total_cost = 0.0
        cost_breakdown = None
        record_url = None
        log_url = None
        call_session_history_id = None
        session_history_record = None  # запись, в которую допишем log_url при fallback
        has_structured_dialog = False
        
        # 🆕 v3.5: Собираем caller_number из всех записей (берём первый непустой)
        session_caller_number = None
        
        for msg in all_messages:
            client_info = msg.client_info or {}
            dialog = client_info.get('dialog', [])
            
            # 🆕 v3.5: Собираем caller_number
            if not session_caller_number:
                normalized = normalize_caller_number(msg.caller_number)
                if normalized:
                    session_caller_number = normalized
            
            # 🆕 v3.1: Проверяем наличие структурированного диалога
            if dialog and isinstance(dialog, list) and len(dialog) > 0:
                has_structured_dialog = True
                logger.info(f"   📝 Found structured dialog with {len(dialog)} turns in record {msg.id}")
                
                for turn in dialog:
                    role = turn.get('role', 'unknown')
                    text = turn.get('text', '')
                    ts = turn.get('ts')
                    
                    # 🆕 v3.5: Фильтруем системные сообщения
                    if is_system_message(text):
                        continue
                    
                    # Конвертируем timestamp
                    timestamp = None
                    if ts:
                        try:
                            timestamp = datetime.fromtimestamp(ts / 1000).isoformat()
                        except (ValueError, TypeError, OSError):
                            timestamp = msg.created_at.isoformat() if msg.created_at else None
                    else:
                        timestamp = msg.created_at.isoformat() if msg.created_at else None
                    
                    if text:
                        messages.append({
                            "id": str(msg.id),
                            "type": "user" if role == "user" else "assistant",
                            "text": text,
                            "timestamp": timestamp
                        })
            else:
                # LEGACY FORMAT
                logger.info(f"   📄 Using legacy format for record {msg.id}")
                
                # User message - с фильтрацией
                user_text = get_clean_text(msg.user_message)
                if user_text:
                    messages.append({
                        "id": str(msg.id),
                        "type": "user",
                        "text": user_text,
                        "timestamp": msg.created_at.isoformat() if msg.created_at else None
                    })
                
                # Assistant message - с фильтрацией
                assistant_text = get_clean_text(msg.assistant_message)
                if assistant_text:
                    messages.append({
                        "id": str(msg.id),
                        "type": "assistant",
                        "text": assistant_text,
                        "timestamp": msg.created_at.isoformat() if msg.created_at else None
                    })
            
            # Суммируем метрики
            total_tokens += msg.tokens_used or 0
            total_duration += msg.duration_seconds or 0
            
            if msg.call_cost:
                total_cost += float(msg.call_cost)

            # Разбивка стоимости из Voximplant GetCallHistory. Нужна, чтобы
            # цифру в карточке можно было сверить с логом сессии: в логе видны
            # только телефония, ASR и запись, а в счёт входят ещё TTS,
            # WebSocket-потоки и turn detection (они внутри other_cost).
            if not cost_breakdown and client_info.get('cost_breakdown'):
                cost_breakdown = client_info.get('cost_breakdown')


            if client_info.get('record_url'):
                record_url = client_info.get('record_url')

            if client_info.get('log_url'):
                log_url = client_info.get('log_url')

            if client_info.get('call_session_history_id'):
                call_session_history_id = client_info.get('call_session_history_id')
                session_history_record = msg
        
        logger.info(f"   Total messages after processing: {len(messages)}")
        logger.info(f"   Has structured dialog: {has_structured_dialog}")

        # =============================================================================
        # Fallback для старых звонков: log_url не сохранён, но есть
        # call_session_history_id — дотягиваем лог из Voximplant GetCallHistory
        # и кэшируем в client_info, чтобы не ходить в API повторно.
        # =============================================================================
        if not log_url and call_session_history_id:
            try:
                from backend.api.voximplant import (
                    get_full_call_cost,
                    get_voximplant_api_credentials,
                )

                credentials = get_voximplant_api_credentials(db, assistant.user_id)
                if credentials:
                    cost_result = await get_full_call_cost(
                        call_session_history_id=call_session_history_id,
                        account_id=credentials["account_id"],
                        api_key=credentials["api_key"],
                    )
                    fetched_log_url = cost_result.get("log_file_url")
                    if fetched_log_url:
                        log_url = fetched_log_url
                        if session_history_record is not None:
                            updated_info = dict(session_history_record.client_info or {})
                            updated_info["log_url"] = fetched_log_url
                            session_history_record._row.client_info = updated_info
                            db.commit()
                        logger.info(f"   📄 log_url fetched from Voximplant and cached")
            except Exception as log_fetch_error:
                # Лог — вспомогательная информация, не ломаем детальный просмотр
                logger.warning(f"   ⚠️ Failed to fetch log_url from Voximplant: {log_fetch_error}")
        
        # Загружаем function calls
        function_calls = []
        if include_functions:
            message_ids = [msg.id for msg in all_messages]
            
            # Для Gemini и Fish также ищем в их таблицах диалогов
            provider_model = {'gemini': GeminiConversation, 'fish': FishConversation}.get(assistant_type)
            if provider_model is not None:
                provider_messages = db.query(provider_model.id).filter(
                    provider_model.session_id == session_id
                ).all()
                provider_ids = [m.id for m in provider_messages]
                message_ids.extend(provider_ids)
                logger.info(f"   🔧 Added {len(provider_ids)} {assistant_type} conversation IDs for function lookup")
            
            logs = db.query(FunctionLog).filter(
                FunctionLog.conversation_id.in_(message_ids)
            ).order_by(FunctionLog.created_at).all()
            
            function_calls = [
                {
                    "id": str(log.id),
                    "function_name": log.function_name,
                    "arguments": log.arguments,
                    "result": log.result,
                    "status": log.status,
                    "created_at": log.created_at.isoformat() if log.created_at else None
                }
                for log in logs
            ]
            
            logger.info(f"   Found {len(function_calls)} function calls")
        
        # 🆕 v3.2: Привязываем function_calls к сообщениям
        if include_functions and function_calls:
            messages = attach_functions_to_messages(messages, function_calls)
        else:
            for msg in messages:
                msg['function_calls'] = []
        
        # Извлекаем assistant_type из client_info
        main_client_info = dict(conversation.client_info or {})
        detected_type = main_client_info.get('assistant_type', assistant_type)
        main_client_info.setdefault('assistant_type', detected_type)  # бейдж провайдера на фронте
        
        # Форматируем стоимость
        call_cost = round(total_cost, 2) if total_cost > 0 else None
        
        # 🆕 v3.5: Используем нормализованный caller_number
        final_caller_number = session_caller_number or normalize_caller_number(conversation.caller_number)
        
        result = {
            "session_id": session_id,
            "assistant_id": str(conversation.assistant_id),
            "assistant_name": assistant.name,
            "assistant_type": detected_type,
            "caller_number": final_caller_number,  # 🆕 v3.5: Нормализованный
            "created_at": all_messages[0].created_at.isoformat() if all_messages else None,
            "messages": messages,
            "total_messages": len(messages),
            "total_tokens": total_tokens,
            "total_duration": total_duration,
            "call_cost": call_cost,
            "cost_breakdown": cost_breakdown,
            "record_url": record_url,
            "log_url": log_url,
            "has_structured_dialog": has_structured_dialog,
            "function_calls": function_calls if include_functions else [],
            "client_info": main_client_info
        }
        
        logger.info(f"✅ Full dialog returned: {len(messages)} messages, type: {detected_type}")
        logger.info(f"   Call cost: {call_cost}, Record URL: {'✅' if record_url else '❌'}")
        logger.info(f"   Caller: {final_caller_number or 'Web chat'}")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting conversation detail: {e}")
        logger.error(f"   Traceback: ", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get conversation detail: {str(e)}"
        )


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    🆕 v2.0: Удалить диалог (всю сессию со всеми сообщениями).
    Поддерживает OpenAI и Gemini ассистентов.
    
    Удаляет ВСЕ сообщения из session_id и связанные FunctionLog записи.
    
    Требуется авторизация. Можно удалить только свои диалоги.
    
    **Параметры:**
    - conversation_id: UUID любого сообщения из диалога ИЛИ session_id
    
    **Возвращает:**
    - message: Сообщение об успешном удалении
    - deleted_messages: Количество удаленных сообщений
    - deleted_functions: Количество удаленных логов функций
    - assistant_type: Тип ассистента (openai/gemini)
    """
    try:
        logger.info(f"[CONVERSATIONS-API-v3.5] Delete conversation request: {conversation_id}")
        logger.info(f"   User: {current_user.id}")
        
        # Ищем сессию в conversations, затем в gemini_conversations
        conversation, source_model = _find_session_record(db, conversation_id)
        
        if not conversation:
            logger.warning(f"Conversation not found: {conversation_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found"
            )
        
        # Проверяем права доступа
        assistant, assistant_type = find_assistant_by_id(db, conversation.assistant_id)
        
        if not assistant:
            logger.warning(f"Assistant not found for conversation: {conversation_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant not found"
            )
        
        if str(assistant.user_id) != str(current_user.id):
            logger.warning(f"Access denied: conversation {conversation_id} doesn't belong to user {current_user.id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: this conversation doesn't belong to you"
            )
        
        session_id = conversation.session_id
        
        # Получаем все сообщения из сессии
        all_messages = db.query(source_model).filter(
            source_model.session_id == session_id,
            source_model.assistant_id == conversation.assistant_id
        ).all()
        
        message_ids = [msg.id for msg in all_messages]
        messages_count = len(message_ids)
        
        logger.info(f"   Found {messages_count} messages to delete in session {session_id}")
        logger.info(f"   Assistant type: {assistant_type}")
        
        # Удаляем связанные FunctionLog записи
        deleted_functions = 0
        if message_ids:
            deleted_functions = db.query(FunctionLog).filter(
                FunctionLog.conversation_id.in_(message_ids)
            ).delete(synchronize_session=False)
            logger.info(f"   Deleted {deleted_functions} function logs")
        
        # Удаляем ВСЕ сообщения из сессии
        deleted_messages = db.query(source_model).filter(
            source_model.session_id == session_id,
            source_model.assistant_id == conversation.assistant_id
        ).delete(synchronize_session=False)
        
        db.commit()
        
        logger.info(f"✅ Successfully deleted conversation session {session_id}")
        logger.info(f"   Deleted {deleted_messages} messages and {deleted_functions} function logs")
        
        return {
            "message": "Conversation deleted successfully",
            "session_id": session_id,
            "deleted_messages": deleted_messages,
            "deleted_functions": deleted_functions,
            "assistant_type": assistant_type
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error deleting conversation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete conversation: {str(e)}"
        )


@router.get("/stats")
async def get_conversations_stats(
    assistant_id: Optional[str] = Query(None, description="Статистика по конкретному ассистенту"),
    days: int = Query(30, ge=1, le=365, description="За сколько дней (1-365)"),
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить статистику по диалогам.
    
    🆕 v3.0: Добавлена статистика по стоимости звонков.
    
    Требуется авторизация.
    
    **Параметры:**
    - assistant_id: ID конкретного ассистента (опционально)
    - days: За сколько дней считать статистику (по умолчанию 30)
    
    **Возвращает:**
    - total_conversations: Общее количество диалогов
    - conversations_last_X_days: Диалоги за указанный период
    - conversations_today: Диалоги за сегодня
    - avg_duration_seconds: Средняя длительность диалога
    - total_tokens_used: Общее количество использованных токенов
    - total_call_cost: Общая стоимость звонков
    """
    try:
        logger.info(f"[CONVERSATIONS-API-v3.5] Get stats for user {current_user.id}")
        logger.info(f"   Assistant ID: {assistant_id}")
        logger.info(f"   Days: {days}")
        
        # Получаем статистику
        stats = ConversationService.get_conversation_stats(
            db=db,
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            days=days
        )
        
        # Добавляем статистику по стоимости
        user_assistant_ids = get_user_assistant_ids(db, current_user.id)
        
        if user_assistant_ids:
            from datetime import timedelta
            start_date = datetime.utcnow() - timedelta(days=days)
            
            cost_query = db.query(
                func.sum(Conversation.call_cost)
            ).filter(
                Conversation.assistant_id.in_(user_assistant_ids),
                Conversation.call_cost.isnot(None)
            )
            
            if assistant_id:
                try:
                    cost_query = cost_query.filter(Conversation.assistant_id == UUID(assistant_id))
                except ValueError:
                    pass
            
            total_cost = cost_query.scalar() or 0.0
            stats["total_call_cost"] = round(float(total_cost), 2)
        else:
            stats["total_call_cost"] = 0.0
        
        logger.info(f"✅ Stats returned: {stats}")
        
        return stats
        
    except Exception as e:
        logger.error(f"❌ Error getting conversation stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get conversation stats: {str(e)}"
        )


@router.get("/by-caller/{caller_number}")
async def get_conversations_by_caller(
    caller_number: str,
    assistant_id: Optional[str] = Query(None, description="Фильтр по ID ассистента"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    current_user: User = Depends(AuthService.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить все диалоги с конкретным номером телефона.
    
    🆕 v2.0: Поддерживает OpenAI и Gemini ассистентов.
    
    Полезно для просмотра истории общения с клиентом.
    
    Требуется авторизация.
    
    **Параметры:**
    - caller_number: Номер телефона (формат любой)
    - assistant_id: Дополнительный фильтр по ассистенту
    - limit/offset: Пагинация
    
    **Возвращает:**
    - Список всех диалогов с этим номером
    - Отсортировано по дате (новые первые)
    """
    try:
        logger.info(f"[CONVERSATIONS-API-v3.5] Get conversations by caller: {caller_number}")
        logger.info(f"   User: {current_user.id}")
        logger.info(f"   Assistant filter: {assistant_id}")
        
        result = ConversationService.get_conversations_advanced(
            db=db,
            assistant_id=assistant_id,
            user_id=str(current_user.id),
            caller_number=caller_number,
            limit=limit,
            offset=offset
        )
        
        logger.info(f"✅ Found {len(result['conversations'])} conversations for caller {caller_number}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Error getting conversations by caller: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get conversations by caller: {str(e)}"
        )
