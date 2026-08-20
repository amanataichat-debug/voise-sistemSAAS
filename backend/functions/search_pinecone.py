# backend/functions/search_pinecone.py
"""
Функция для поиска в векторной базе данных Pinecone.
"""
import os
import json
import re
import requests
from typing import Dict, Any, Optional, List

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function

logger = get_logger(__name__)

def extract_namespace_from_prompt(prompt: str) -> Optional[str]:
    """
    Извлекает namespace Pinecone из системного промпта ассистента.
    """
    if not prompt:
        return None
        
    # Ищем namespace с помощью регулярного выражения
    # Паттерн 1: "Pinecone namespace: my_namespace"
    pattern1 = r'Pinecone\s+namespace:\s*([a-zA-Z0-9_-]+)'
    # Паттерн 2: "namespace: my_namespace"
    pattern2 = r'namespace:\s*([a-zA-Z0-9_-]+)'
    
    # Проверяем шаблоны по убыванию специфичности
    for pattern in [pattern1, pattern2]:
        matches = re.findall(pattern, prompt, re.IGNORECASE)
        if matches:
            return matches[0]
            
    return None

@register_function
class PineconeSearchFunction(FunctionBase):
    """Функция для поиска в векторной базе данных Pinecone"""
    
    @classmethod
    def get_name(cls) -> str:
        return "search_pinecone"
    
    @classmethod
    def get_display_name(cls) -> str:
        return "Поиск в Pinecone (векторная БД)"
    
    @classmethod
    def get_description(cls) -> str:
        return "Ищет похожие документы в Pinecone векторной базе данных"
    
    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        # namespace НЕ выносим в параметры: его выбирает сервер
        # (из AgentConfig.kb_namespace или системного промпта). Иначе модель
        # галлюцинирует namespace (например имя индекса) и поиск ничего не находит.
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос для векторного поиска"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Количество результатов для возврата",
                    "default": 3
                }
            },
            "required": ["query"]
        }
    
    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p>Ты можешь использовать функцию <code>search_pinecone</code> для поиска релевантной информации в векторной базе данных.</p>

<p><strong>Что такое Pinecone?</strong></p>
<p>Векторная база данных для семантического поиска. Позволяет находить похожие документы по смыслу, а не по точному совпадению слов.</p>

<p><strong>Когда использовать:</strong></p>
<ul>
    <li>Пользователь задает вопрос о продуктах, услугах, документации</li>
    <li>Нужно найти релевантную информацию из большой базы знаний</li>
    <li>Требуется контекст для более точного ответа</li>
    <li>Поиск по FAQ, инструкциям, каталогам</li>
</ul>

<p><strong>Настройка в промпте:</strong></p>
<p>Укажи в системном промпте namespace Pinecone:</p>
<pre>Pinecone namespace: my_knowledge_base</pre>

<p><strong>Параметры функции:</strong></p>
<ul>
    <li><code>namespace</code> — имя namespace в Pinecone (можно указать в промпте)</li>
    <li><code>query</code> — поисковый запрос пользователя или его перефразировка</li>
    <li><code>top_k</code> — количество результатов (по умолчанию 3, можно 5-10)</li>
</ul>

<p><strong>Пример вызова:</strong></p>
<pre>{
  "namespace": "my_knowledge_base",
  "query": "Как работает векторный поиск?",
  "top_k": 5
}</pre>

<p><strong>Результат:</strong></p>
<pre>{
  "success": true,
  "query": "Как работает векторный поиск?",
  "namespace": "my_knowledge_base",
  "results": [
    {
      "id": "doc_123",
      "score": 0.92,
      "metadata": {
        "text": "Векторный поиск использует...",
        "title": "Введение в поиск",
        "category": "Документация"
      }
    },
    ...
  ],
  "total": 5
}</pre>

<p><strong>💡 Совет:</strong> После получения результатов используй информацию из <code>metadata</code> для формирования более точного и информативного ответа пользователю.</p>

<p><strong>⚙️ Требования:</strong></p>
<ul>
    <li>Переменная окружения <code>PINECONE_API_KEY</code> должна быть настроена</li>
    <li>OpenAI API ключ для создания эмбеддингов</li>
</ul>
"""
        
    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Выполняет векторный поиск в Pinecone.
        """
        context = context or {}
        assistant_config = context.get("assistant_config")
        
        try:
            query = arguments.get("query")
            top_k = arguments.get("top_k", 3)

            # Проверка обязательных параметров
            if not query:
                return {"error": "Query is required"}

            # ВАЖНО: namespace резолвим на СЕРВЕРЕ, а не из аргументов модели.
            # Модель часто галлюцинирует namespace (например имя индекса
            # "voicyfy"), из-за чего поиск идёт в несуществующем namespace и
            # ничего не находит. Поэтому приоритет:
            #   1) AgentConfig.kb_namespace (агент обзвона хранит namespace у себя)
            #   2) namespace из системного промпта (отдельные ассистенты)
            #   3) аргумент модели — только как legacy-фолбэк
            namespace = None

            # 1. AgentConfig.kb_namespace по id голосового ассистента
            if assistant_config and getattr(assistant_config, "id", None):
                try:
                    from backend.db.session import get_db
                    from backend.models.agent_config import AgentConfig
                    from sqlalchemy import or_

                    _db = getattr(assistant_config, "db_session", None)
                    _own = False
                    if _db is None:
                        _db = next(get_db())
                        _own = True
                    try:
                        a_id = assistant_config.id
                        agent = _db.query(AgentConfig).filter(
                            or_(
                                AgentConfig.openai_assistant_id == a_id,
                                AgentConfig.gemini_assistant_id == a_id,
                                AgentConfig.cartesia_assistant_id == a_id,
                                AgentConfig.yandex_assistant_id == a_id,
                                AgentConfig.cascade_assistant_id == a_id,
                                AgentConfig.fish_assistant_id == a_id,
                            )
                        ).first()
                        if agent and agent.kb_namespace:
                            namespace = agent.kb_namespace
                            logger.info(f"Namespace взят из AgentConfig: {namespace}")
                    finally:
                        if _own:
                            _db.close()
                except Exception as e:
                    logger.warning(f"Не удалось получить namespace из AgentConfig: {e}")

            # 2. Из системного промпта ассистента
            if not namespace and assistant_config:
                if hasattr(assistant_config, "system_prompt") and assistant_config.system_prompt:
                    namespace = extract_namespace_from_prompt(assistant_config.system_prompt)
                    if namespace:
                        logger.info(f"Извлечен namespace из промпта: {namespace}")

            # 3. Legacy-фолбэк — аргумент модели
            if not namespace:
                namespace = arguments.get("namespace")

            # Проверка на наличие namespace
            if not namespace:
                return {"error": "Namespace is required"}
            
            # Получение ключа Pinecone из переменных окружения
            pinecone_api_key = os.environ.get("PINECONE_API_KEY")
            if not pinecone_api_key:
                logger.error("PINECONE_API_KEY not found in environment variables")
                return {"error": "Pinecone API key not configured"}
            
            # Создаем эмбеддинг через OpenAI API
            openai_api_key = None
            if assistant_config and hasattr(assistant_config, "user_id"):
                # Импортируем здесь, чтобы избежать циклических импортов
                from backend.models.user import User
                
                # Получаем сессию базы данных
                db_session = None
                if hasattr(assistant_config, 'db_session'):
                    db_session = assistant_config.db_session
                else:
                    # Создаем новую сессию если нет в объекте
                    from backend.db.session import get_db
                    db_session = next(get_db())
                    
                # Получаем пользователя и его API ключ
                user = db_session.query(User).get(assistant_config.user_id)
                if user and user.openai_api_key:
                    openai_api_key = user.openai_api_key
            
            if not openai_api_key:
                # Попытка использовать ключ из переменных окружения
                openai_api_key = os.environ.get("OPENAI_API_KEY")
                if not openai_api_key:
                    return {"error": "OpenAI API key not available"}
            
            # Создаем эмбеддинг через OpenAI API
            embed_response = requests.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {openai_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "input": query,
                    # Должно совпадать с моделью при создании базы
                    # (PineconeService.create_or_update_knowledge_base), иначе
                    # вектора окажутся в разных пространствах и поиск будет нерелевантным.
                    "model": "text-embedding-3-small"
                }
            )
            
            if embed_response.status_code != 200:
                logger.error(f"Error creating embedding: {embed_response.text}")
                return {"error": f"Failed to create embedding: {embed_response.status_code}"}
            
            # Извлекаем эмбеддинг из ответа
            embedding = embed_response.json().get("data", [{}])[0].get("embedding", [])
            
            if not embedding:
                return {"error": "Failed to generate embedding for query"}
            
            # Создаем запрос к Pinecone
            pinecone_url = "https://voicufi-gpr1sqd.svc.aped-4627-b74a.pinecone.io/query"
            
            pinecone_request = {
                "vector": embedding,
                "namespace": namespace,
                "topK": top_k,
                "includeMetadata": True
            }
            
            # Отправляем запрос к Pinecone
            pinecone_response = requests.post(
                pinecone_url,
                headers={
                    "Api-Key": pinecone_api_key,
                    "Content-Type": "application/json"
                },
                json=pinecone_request
            )
            
            if pinecone_response.status_code != 200:
                logger.error(f"Error from Pinecone: {pinecone_response.text}")
                return {"error": f"Pinecone query failed: {pinecone_response.status_code}"}
            
            # Обрабатываем результаты
            results = pinecone_response.json()
            
            # Форматируем результаты в более читаемый вид
            formatted_results = []
            for match in results.get("matches", []):
                formatted_match = {
                    "id": match.get("id"),
                    "score": match.get("score"),
                    "metadata": match.get("metadata", {})
                }
                formatted_results.append(formatted_match)
            
            return {
                "success": True,
                "query": query,
                "namespace": namespace,
                "results": formatted_results,
                "total": len(formatted_results)
            }
            
        except Exception as e:
            logger.error(f"Error in search_pinecone: {str(e)}")
            return {"error": f"Search failed: {str(e)}"}
