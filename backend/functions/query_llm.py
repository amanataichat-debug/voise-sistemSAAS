# backend/functions/query_llm.py
"""
Функция для отправки запросов к ChatGPT API через голосового агента.
Реализована как класс FunctionBase для корректной регистрации в системе.
"""

import openai
import asyncio
from typing import Dict, Any

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function

logger = get_logger(__name__)


@register_function
class QueryLLMFunction(FunctionBase):
    """Функция для отправки запросов к текстовой LLM модели"""
    
    @classmethod
    def get_name(cls) -> str:
        return "query_llm"
    
    @classmethod
    def get_display_name(cls) -> str:
        return "Запрос к текстовой LLM (ChatGPT)"
    
    @classmethod
    def get_description(cls) -> str:
        return "Отправляет сложные запросы к текстовой LLM модели для получения развернутых ответов"
    
    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Текст запроса или задачи для LLM модели"
                },
                "model": {
                    "type": "string", 
                    "description": "Модель для использования (gpt-4, gpt-3.5-turbo)",
                    "default": "gpt-4o-mini"
                }
            },
            "required": ["prompt"]
        }
    
    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p>Ты можешь использовать функцию <code>query_llm</code> для делегирования сложных задач текстовой модели ChatGPT.</p>

<p><strong>Зачем это нужно?</strong></p>
<p>Голосовые модели оптимизированы для коротких реплик. Для длинных текстов, анализа или кода используй текстовую модель.</p>

<p><strong>Когда использовать:</strong></p>
<ul>
    <li>Пользователь просит написать <strong>длинный текст</strong> (статью, письмо, отчет, код)</li>
    <li>Нужен <strong>детальный анализ</strong> или исследование</li>
    <li>Требуется <strong>структурированный ответ</strong> с множеством деталей</li>
    <li>Задача требует <strong>глубокого reasoning</strong> или пошагового решения</li>
    <li>Создание кода, скриптов, SQL запросов</li>
    <li>Написание документации, инструкций, руководств</li>
</ul>

<p><strong>Параметры функции:</strong></p>
<ul>
    <li><code>prompt</code> — детальное описание задачи для ChatGPT</li>
    <li><code>model</code> — модель: "gpt-4o-mini" (быстрая), "gpt-4" (умная), "gpt-3.5-turbo" (экономная)</li>
</ul>

<p><strong>Пример вызова:</strong></p>
<pre>{
  "prompt": "Напиши подробную статью о преимуществах искусственного интеллекта в медицине. Включи примеры, статистику и перспективы развития.",
  "model": "gpt-4o-mini"
}</pre>

<p><strong>Результат:</strong></p>
<pre>{
  "result": "Запрос выполнен! Развернутый ответ выведен на экран.",
  "status": "success",
  "model_used": "gpt-4o-mini",
  "response_length": 2847,
  "full_response": "... полный текст ответа ..."
}</pre>

<p><strong>💡 Как работать с результатом:</strong></p>
<ul>
    <li><strong>Голосом:</strong> озвучь краткое резюме (2-3 предложения)</li>
    <li><strong>На экране:</strong> полный текст автоматически отобразится в UI</li>
    <li><strong>Для пользователя:</strong> "Я подготовил подробный ответ, вы можете прочитать его на экране слева"</li>
</ul>

<p><strong>⚠️ Важно:</strong> Используй эту функцию только для задач, где действительно нужен длинный/сложный ответ. Для простых вопросов отвечай напрямую.</p>

<p><strong>⚙️ Требования:</strong></p>
<ul>
    <li>OpenAI API ключ (пользователя или системный)</li>
    <li>Достаточно токенов на аккаунте</li>
</ul>
"""
    
    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Отправляет запрос к ChatGPT API и возвращает результат
        
        Args:
            arguments: Словарь с параметрами функции (prompt, model)
            context: Контекст с websocket и другими данными
        
        Returns:
            Dict с результатом выполнения
        """
        try:
            prompt = arguments.get("prompt")
            model = arguments.get("model", "gpt-4o-mini")
            
            if not prompt:
                error_msg = "Prompt is required"
                logger.error(f"[QUERY_LLM] {error_msg}")
                return {"error": error_msg, "status": "error"}
            
            logger.info(f"[QUERY_LLM] Executing query: {prompt[:100]}...")
            
            # Получаем API ключ из контекста или настроек
            api_key = None
            
            if context and "assistant_config" in context:
                assistant_config = context["assistant_config"]
                
                # Пытаемся получить API ключ пользователя
                if hasattr(assistant_config, "user_id") and assistant_config.user_id:
                    from backend.models.user import User
                    db_session = context.get("db_session")
                    
                    if db_session:
                        try:
                            user = db_session.query(User).get(assistant_config.user_id)
                            if user and user.openai_api_key:
                                api_key = user.openai_api_key
                                logger.info(f"[QUERY_LLM] Using user's OpenAI API key")
                            else:
                                api_key = settings.OPENAI_API_KEY
                                logger.info(f"[QUERY_LLM] Using system OpenAI API key")
                        except Exception as e:
                            logger.error(f"[QUERY_LLM] Error getting user API key: {e}")
                            api_key = settings.OPENAI_API_KEY
                    else:
                        api_key = settings.OPENAI_API_KEY
                else:
                    api_key = settings.OPENAI_API_KEY
            else:
                api_key = settings.OPENAI_API_KEY
            
            if not api_key:
                error_msg = "OpenAI API key not found"
                logger.error(f"[QUERY_LLM] {error_msg}")
                return {"error": error_msg, "status": "error"}
            
            # Создаем клиент OpenAI
            client = openai.AsyncOpenAI(api_key=api_key)
            
            # Формируем запрос к ChatGPT
            messages = [
                {
                    "role": "system", 
                    "content": "Ты профессиональный ассистент. Отвечай подробно и структурированно. Используй markdown для форматирования."
                },
                {
                    "role": "user", 
                    "content": prompt
                }
            ]
            
            logger.info(f"[QUERY_LLM] Sending request to {model}...")
            
            # Отправляем запрос
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=2000,
                temperature=0.7
            )
            
            llm_result = response.choices[0].message.content
            
            logger.info(f"[QUERY_LLM] LLM response received: {len(llm_result)} characters")
            logger.info(f"[QUERY_LLM] Preparing result for handler (no direct WebSocket send)")
            
            # Возвращаем результат для обработки в handler_realtime_new.py
            # WebSocket отправку делает handler, а не функция (избегаем дублирования)
            return {
                "result": f"Запрос выполнен! Развернутый ответ выведен на экран слева. Обработано {len(llm_result)} символов.",
                "status": "success",
                "model_used": model,
                "response_length": len(llm_result),
                "full_response": llm_result  # handler_realtime_new.py будет отправлять это на фронтенд
            }
            
        except Exception as e:
            error_msg = f"Error executing LLM query: {str(e)}"
            logger.error(f"[QUERY_LLM] {error_msg}")
            return {
                "error": error_msg,
                "status": "error"
            }
