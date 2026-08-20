# backend/functions/search_contact_by_phone.py
"""
Функция для поиска контакта в CRM Voicyfy по номеру телефона.
ИИ вызывает её во время звонка, чтобы получить историю клиента.
"""
from typing import Dict, Any
from datetime import datetime

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function
from backend.services.conversation_service import ConversationService
from backend.models.contact import Contact
from backend.models.conversation import Conversation
from backend.models.assistant import AssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.models.task import Task, TaskStatus
from backend.db.session import SessionLocal
from sqlalchemy import asc, desc, func

logger = get_logger(__name__)


@register_function
class SearchContactByPhoneFunction(FunctionBase):
    """Поиск контакта в CRM по номеру телефона с историей диалогов"""

    @classmethod
    def get_name(cls) -> str:
        return "search_contact_by_phone"

    @classmethod
    def get_display_name(cls) -> str:
        return "Поиск контакта по телефону (CRM)"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Ищет контакт в CRM системе Voicyfy по номеру телефона. "
            "Возвращает имя, статус, заметки и историю диалогов клиента. "
            "Используй эту функцию в начале звонка, чтобы узнать кто звонит "
            "и что обсуждалось раньше."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": (
                        "Номер телефона клиента в любом формате "
                        "(79991234567, +7 999 123-45-67, 8-999-123-45-67)"
                    )
                },
                "conversations_limit": {
                    "type": "integer",
                    "description": (
                        "Сколько последних диалогов (сессий) вернуть. "
                        "По умолчанию 5, максимум 20."
                    ),
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20
                }
            },
            "required": ["phone"]
        }

    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p>Используй функцию <code>search_contact_by_phone</code> чтобы узнать кто звонит и что обсуждалось раньше.</p>

<p><strong>Когда использовать:</strong></p>
<ul>
    <li>В начале входящего звонка — проверить, звонил ли клиент раньше</li>
    <li>Во время разговора — освежить контекст предыдущих обращений</li>
    <li>Перед исходящим звонком — подготовиться к разговору</li>
    <li>Когда клиент упоминает что "уже обращался" или "говорил об этом ранее"</li>
</ul>

<p><strong>Параметры:</strong></p>
<ul>
    <li><code>phone</code> — номер телефона в любом формате (обязательно)</li>
    <li><code>conversations_limit</code> — сколько последних диалогов вернуть (1-20, по умолчанию 5)</li>
</ul>

<p><strong>Пример вызова:</strong></p>
<pre>{
  "phone": "79991234567",
  "conversations_limit": 3
}</pre>

<p><strong>Если контакт найден:</strong></p>
<pre>{
  "found": true,
  "contact": {
    "name": "Иван Петров",
    "phone": "+79991234567",
    "status": "client",
    "notes": "Предпочитает звонки после 18:00",
    "last_interaction": "2025-01-15T14:30:00"
  },
  "active_tasks": [
    {
      "title": "Перезвонить по вопросу тарифа",
      "scheduled_time": "2025-01-20T15:00:00",
      "assistant_name": "Менеджер"
    }
  ],
  "conversations": [
    {
      "session_id": "abc-123",
      "created_at": "2025-01-15T14:30:00",
      "direction": "inbound",
      "duration_seconds": 245,
      "assistant_name": "Менеджер",
      "messages": [
        {"role": "assistant", "content": "Добрый день! Чем могу помочь?", "created_at": "..."},
        {"role": "user", "content": "Хочу уточнить условия тарифа", "created_at": "..."}
      ]
    }
  ],
  "total_conversations": 7
}</pre>

<p><strong>Если контакт не найден:</strong></p>
<pre>{
  "found": false,
  "phone": "+79991234567"
}</pre>

<p><strong>Как использовать результат:</strong></p>
<ul>
    <li>Если <code>name</code> есть — обращайся к клиенту по имени</li>
    <li>Смотри <code>notes</code> — там важная информация о клиенте</li>
    <li>Читай <code>messages</code> — узнай что обсуждалось раньше</li>
    <li>Проверяй <code>active_tasks</code> — возможно есть незакрытые вопросы</li>
</ul>
"""

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Ищет контакт по номеру телефона и возвращает его историю.

        Логика:
        1. Нормализуем номер
        2. Ищем контакт в таблице Contact (без фильтра по user_id)
        3. Если не найден → {"found": false}
        4. Если найден → базовые данные + активные задачи + последние N диалогов
        """
        db = SessionLocal()

        try:
            phone_raw = arguments.get("phone")
            conversations_limit = arguments.get("conversations_limit", 5)

            # Зажимаем лимит в диапазон 1-20
            conversations_limit = max(1, min(20, int(conversations_limit)))

            logger.info(f"[SEARCH_CONTACT] ━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[SEARCH_CONTACT] 🔍 Searching phone: {phone_raw}")
            logger.info(f"[SEARCH_CONTACT] 📋 Conversations limit: {conversations_limit}")

            if not phone_raw:
                return {"success": False, "error": "phone is required"}

            # 1. Нормализуем номер
            normalized_phone = ConversationService._normalize_phone(phone_raw)
            logger.info(f"[SEARCH_CONTACT] 📱 Normalized: {phone_raw} → {normalized_phone}")

            # 2. Ищем контакт
            contact = db.query(Contact).filter(
                Contact.phone == normalized_phone
            ).first()

            if not contact:
                logger.info(f"[SEARCH_CONTACT] ❌ Contact not found for {normalized_phone}")
                return {
                    "found": False,
                    "phone": normalized_phone
                }

            logger.info(f"[SEARCH_CONTACT] ✅ Contact found: {contact.id} (name: {contact.name or 'No name'})")

            # 3. Базовые данные контакта
            contact_data = {
                "id": str(contact.id),
                "name": contact.name,
                "phone": contact.phone,
                "status": contact.status,
                "notes": contact.notes,
                "last_interaction": (
                    contact.last_interaction.isoformat()
                    if contact.last_interaction else None
                )
            }

            # 4. Активные задачи (SCHEDULED + PENDING)
            active_tasks_raw = db.query(Task).filter(
                Task.contact_id == contact.id,
                Task.status.in_([TaskStatus.SCHEDULED, TaskStatus.PENDING])
            ).order_by(asc(Task.scheduled_time)).all()

            active_tasks = []
            for task in active_tasks_raw:
                # Определяем имя ассистента
                assistant_name = "Unknown"
                if task.assistant_id:
                    asst = db.query(AssistantConfig).filter(
                        AssistantConfig.id == task.assistant_id
                    ).first()
                    if asst:
                        assistant_name = asst.name
                elif task.gemini_assistant_id:
                    asst = db.query(GeminiAssistantConfig).filter(
                        GeminiAssistantConfig.id == task.gemini_assistant_id
                    ).first()
                    if asst:
                        assistant_name = asst.name
                elif task.cartesia_assistant_id:
                    asst = db.query(CartesiaAssistantConfig).filter(
                        CartesiaAssistantConfig.id == task.cartesia_assistant_id
                    ).first()
                    if asst:
                        assistant_name = asst.name
                elif task.yandex_assistant_id:
                    from backend.models.yandex_assistant import YandexAssistantConfig
                    asst = db.query(YandexAssistantConfig).filter(
                        YandexAssistantConfig.id == task.yandex_assistant_id
                    ).first()
                    if asst:
                        assistant_name = asst.name
                elif task.cascade_assistant_id:
                    from backend.models.grok_assistant import GrokAssistantConfig
                    asst = db.query(GrokAssistantConfig).filter(
                        GrokAssistantConfig.id == task.cascade_assistant_id
                    ).first()
                    if asst:
                        assistant_name = asst.name
                elif task.fish_assistant_id:
                    from backend.models.fish_assistant import FishAssistantConfig
                    asst = db.query(FishAssistantConfig).filter(
                        FishAssistantConfig.id == task.fish_assistant_id
                    ).first()
                    if asst:
                        assistant_name = asst.name

                active_tasks.append({
                    "id": str(task.id),
                    "title": task.title,
                    "description": task.description,
                    "scheduled_time": (
                        task.scheduled_time.isoformat()
                        if task.scheduled_time else None
                    ),
                    "assistant_name": assistant_name,
                    "status": task.status.value if task.status else None
                })

            logger.info(f"[SEARCH_CONTACT] 📌 Active tasks: {len(active_tasks)}")

            # 5. Подсчёт общего числа сессий
            total_conversations = db.query(
                func.count(func.distinct(Conversation.session_id))
            ).filter(
                Conversation.contact_id == contact.id
            ).scalar() or 0

            logger.info(f"[SEARCH_CONTACT] 💬 Total sessions in DB: {total_conversations}")

            # 6. Получаем последние N сессий (сортировка по дате создания сессии)
            sessions_query = db.query(
                Conversation.session_id,
                Conversation.assistant_id,
                Conversation.call_direction,
                func.min(Conversation.created_at).label("created_at"),
                func.count(Conversation.id).label("messages_count"),
                func.sum(Conversation.duration_seconds).label("total_duration")
            ).filter(
                Conversation.contact_id == contact.id
            ).group_by(
                Conversation.session_id,
                Conversation.assistant_id,
                Conversation.call_direction
            ).order_by(
                desc(func.min(Conversation.created_at))
            ).limit(conversations_limit).all()

            conversations = []
            for session in sessions_query:
                # Имя ассистента
                assistant_name = "Unknown"
                if session.assistant_id:
                    asst = db.query(AssistantConfig).filter(
                        AssistantConfig.id == session.assistant_id
                    ).first()
                    if asst:
                        assistant_name = asst.name

                # Все строки сессии от старых к новым
                rows = db.query(Conversation).filter(
                    Conversation.session_id == session.session_id,
                    Conversation.contact_id == contact.id
                ).order_by(asc(Conversation.created_at)).all()

                # Каждая строка = user_message + assistant_message
                # Раскладываем в плоский хронологический список
                messages = []
                for row in rows:
                    created = (
                        row.created_at.isoformat()
                        if row.created_at else None
                    )
                    if row.user_message:
                        messages.append({
                            "role": "user",
                            "content": row.user_message,
                            "created_at": created
                        })
                    if row.assistant_message:
                        messages.append({
                            "role": "assistant",
                            "content": row.assistant_message,
                            "created_at": created
                        })

                conversations.append({
                    "session_id": session.session_id,
                    "assistant_name": assistant_name,
                    "direction": session.call_direction,
                    "created_at": (
                        session.created_at.isoformat()
                        if session.created_at else None
                    ),
                    "messages_count": session.messages_count,
                    "duration_seconds": session.total_duration or 0,
                    "messages": messages
                })

            logger.info(f"[SEARCH_CONTACT] ✅ Returning {len(conversations)} sessions")
            logger.info(f"[SEARCH_CONTACT] ━━━━━━━━━━━━━━━━━━━━━━━━━")

            return {
                "found": True,
                "contact": contact_data,
                "active_tasks": active_tasks,
                "conversations": conversations,
                "total_conversations": total_conversations
            }

        except Exception as e:
            logger.error(f"[SEARCH_CONTACT] ❌ Error: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Search failed: {str(e)}"
            }
        finally:
            db.close()
