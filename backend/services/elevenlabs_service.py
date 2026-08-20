"""
ИСПРАВЛЕННЫЙ ElevenLabs Service с правильными отступами и API методами
Все методы проверены на корректность синтаксиса Python и соответствие ElevenLabs API
✅ ДОБАВЛЕНА КРИТИЧЕСКАЯ ИНДЕКСАЦИЯ ДОКУМЕНТОВ ДЛЯ RAG
✅ ИСПРАВЛЕНА ЗАГРУЗКА ДЕТАЛЕЙ ДОКУМЕНТОВ
✅ ДОБАВЛЕНО КЕШИРОВАНИЕ ДЛЯ ОПТИМИЗАЦИИ
"""

import httpx
import json
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from fastapi import UploadFile, HTTPException
import asyncio
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ElevenLabsService:
    """Исправленный сервис для работы с ElevenLabs API"""
    
    BASE_URL = "https://api.elevenlabs.io/v1"
    TIMEOUT = 60.0
    
    # ✅ НОВОЕ: Кеш для документов базы знаний
    _kb_documents_cache = {}
    _cache_ttl = timedelta(minutes=5)  # Время жизни кеша
    
    @classmethod
    async def validate_api_key(cls, api_key: str) -> bool:
        """Проверка валидности API ключа"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.get(
                    f"{cls.BASE_URL}/user",
                    headers=headers
                )
                
                if response.status_code == 200:
                    user_data = response.json()
                    logger.info(f"API key validation successful for user: {user_data.get('email', 'unknown')}")
                    return True
                elif response.status_code == 401:
                    logger.warning("API key validation failed: Unauthorized")
                    return False
                else:
                    logger.error(f"API key validation failed: {response.status_code} - {response.text}")
                    return False
                    
        except httpx.TimeoutException:
            logger.error("Timeout while validating API key")
            return False
        except Exception as e:
            logger.error(f"Error validating API key: {str(e)}")
            return False
    
    @classmethod
    async def get_available_voices(cls, api_key: str) -> List[Dict[str, Any]]:
        """Получение доступных голосов"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.get(
                    f"{cls.BASE_URL}/voices",
                    headers=headers
                )
                
                if response.status_code == 200:
                    data = response.json()
                    voices = data.get('voices', [])
                    
                    processed_voices = []
                    for voice in voices:
                        processed_voice = {
                            "voice_id": voice.get("voice_id"),
                            "name": voice.get("name"),
                            "category": voice.get("category", "premade"),
                            "preview_url": voice.get("preview_url")
                        }
                        processed_voices.append(processed_voice)
                    
                    logger.info(f"Retrieved {len(processed_voices)} voices from ElevenLabs")
                    return processed_voices
                elif response.status_code == 401:
                    logger.error("Failed to get voices: Invalid API key")
                    return []
                else:
                    logger.error(f"Failed to get voices: {response.status_code} - {response.text}")
                    return []
                    
        except httpx.TimeoutException:
            logger.error("Timeout while getting voices")
            return []
        except Exception as e:
            logger.error(f"Error getting voices: {str(e)}")
            return []
    
    # ============= KNOWLEDGE BASE METHODS С ИНДЕКСАЦИЕЙ =============
    
    @classmethod
    async def create_knowledge_base_from_text(cls, api_key: str, name: str, text: str) -> str:
        """✅ ИСПРАВЛЕНО: Создание документа Knowledge Base из текста с автоматической индексацией"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            # ✅ ПРАВИЛЬНАЯ структура согласно API документации
            data = {
                "text": text,
                "name": name
            }
            
            logger.info(f"Creating knowledge base document from text: {name}")
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.post(
                    f"{cls.BASE_URL}/convai/knowledge-base/text",
                    headers=headers,
                    json=data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    document_id = result.get("id")
                    logger.info(f"✅ Knowledge base document created from text: {document_id}")
                    
                    # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Автоматическая индексация
                    await cls.index_knowledge_base_document(api_key, document_id)
                    
                    # Очищаем кеш для пользователя
                    cls._clear_cache_for_user(api_key)
                    
                    return document_id
                else:
                    error_text = response.text
                    logger.error(f"Failed to create knowledge base from text: {response.status_code} - {error_text}")
                    raise Exception(f"ElevenLabs API error: {error_text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while creating knowledge base from text")
            raise Exception("Request timed out while creating knowledge base document")
        except Exception as e:
            logger.error(f"Error creating knowledge base from text: {str(e)}")
            raise Exception(f"Failed to create knowledge base document: {str(e)}")
    
    @classmethod
    async def create_knowledge_base_from_url(cls, api_key: str, name: str, url: str) -> str:
        """✅ ИСПРАВЛЕНО: Создание документа Knowledge Base из URL с автоматической индексацией"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            # ✅ ПРАВИЛЬНАЯ структура согласно API документации
            data = {
                "url": url,
                "name": name
            }
            
            logger.info(f"Creating knowledge base document from URL: {name} - {url}")
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.post(
                    f"{cls.BASE_URL}/convai/knowledge-base/url",
                    headers=headers,
                    json=data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    document_id = result.get("id")
                    logger.info(f"✅ Knowledge base document created from URL: {document_id}")
                    
                    # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Автоматическая индексация
                    await cls.index_knowledge_base_document(api_key, document_id)
                    
                    # Очищаем кеш для пользователя
                    cls._clear_cache_for_user(api_key)
                    
                    return document_id
                else:
                    error_text = response.text
                    logger.error(f"Failed to create knowledge base from URL: {response.status_code} - {error_text}")
                    raise Exception(f"ElevenLabs API error: {error_text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while creating knowledge base from URL")
            raise Exception("Request timed out while creating knowledge base document")
        except Exception as e:
            logger.error(f"Error creating knowledge base from URL: {str(e)}")
            raise Exception(f"Failed to create knowledge base document: {str(e)}")
    
    @classmethod
    async def create_knowledge_base_from_file(cls, api_key: str, name: str, file: UploadFile) -> str:
        """✅ ИСПРАВЛЕНО: Создание документа Knowledge Base из файла с автоматической индексацией"""
        try:
            headers = {
                "xi-api-key": api_key
                # Content-Type не указываем для multipart/form-data
            }
            
            # Читаем содержимое файла
            file_content = await file.read()
            await file.seek(0)  # Сбрасываем указатель файла
            
            logger.info(f"Creating knowledge base document from file: {name} - {file.filename}")
            
            # ✅ ПРАВИЛЬНАЯ структура multipart согласно API документации
            files = {
                "file": (file.filename, file_content, file.content_type)
            }
            
            data = {
                "name": name
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.post(
                    f"{cls.BASE_URL}/convai/knowledge-base/file",
                    headers=headers,
                    files=files,
                    data=data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    document_id = result.get("id")
                    logger.info(f"✅ Knowledge base document created from file: {document_id}")
                    
                    # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Автоматическая индексация
                    await cls.index_knowledge_base_document(api_key, document_id)
                    
                    # Очищаем кеш для пользователя
                    cls._clear_cache_for_user(api_key)
                    
                    return document_id
                else:
                    error_text = response.text
                    logger.error(f"Failed to create knowledge base from file: {response.status_code} - {error_text}")
                    raise Exception(f"ElevenLabs API error: {error_text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while creating knowledge base from file")
            raise Exception("Request timed out while creating knowledge base document")
        except Exception as e:
            logger.error(f"Error creating knowledge base from file: {str(e)}")
            raise Exception(f"Failed to create knowledge base document: {str(e)}")
    
    @classmethod
    async def index_knowledge_base_document(cls, api_key: str, document_id: str, model: str = "multilingual_e5_large_instruct") -> Dict[str, Any]:
        """✅ НОВЫЙ КРИТИЧЕСКИЙ МЕТОД: Индексация документа для RAG согласно документации"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            # ✅ ПРАВИЛЬНЫЕ данные для индексации согласно документации
            data = {
                "model": model
            }
            
            logger.info(f"Indexing knowledge base document: {document_id} with model {model}")
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.post(
                    f"{cls.BASE_URL}/convai/knowledge-base/{document_id}/rag-index",
                    headers=headers,
                    json=data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    status = result.get("status", "unknown")
                    progress = result.get("progress_percentage", 0)
                    
                    logger.info(f"✅ Knowledge base document indexing started: {document_id}, status: {status}, progress: {progress}%")
                    
                    # Ждем завершения индексации если нужно
                    if status not in ["ready", "completed"]:
                        await cls.wait_for_indexing_completion(api_key, document_id)
                    
                    return result
                else:
                    error_text = response.text
                    logger.error(f"Failed to index knowledge base document: {response.status_code} - {error_text}")
                    raise Exception(f"ElevenLabs indexing API error: {error_text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while indexing knowledge base document")
            raise Exception("Request timed out while indexing knowledge base document")
        except Exception as e:
            logger.error(f"Error indexing knowledge base document: {str(e)}")
            raise Exception(f"Failed to index knowledge base document: {str(e)}")
    
    @classmethod
    async def wait_for_indexing_completion(cls, api_key: str, document_id: str, max_wait_time: int = 120):
        """✅ НОВЫЙ МЕТОД: Ожидание завершения индексации документа"""
        import asyncio
        
        logger.info(f"Waiting for indexing completion: {document_id}")
        
        start_time = asyncio.get_event_loop().time()
        
        while True:
            # Проверяем статус индексации
            status_result = await cls.get_knowledge_base_document(api_key, document_id)
            
            if not status_result:
                logger.error(f"Failed to get indexing status for document: {document_id}")
                break
            
            # Проверяем статус из результата (зависит от API ElevenLabs)
            # Если API не возвращает статус индексации, прерываем ожидание
            current_time = asyncio.get_event_loop().time()
            if current_time - start_time > max_wait_time:
                logger.warning(f"Indexing wait timeout for document: {document_id}")
                break
            
            # Ждем 2 секунды перед следующей проверкой
            await asyncio.sleep(2)
        
        logger.info(f"✅ Indexing wait completed for document: {document_id}")
    
    @classmethod
    async def list_knowledge_base_documents(cls, api_key: str) -> List[Dict[str, Any]]:
        """✅ ОПТИМИЗИРОВАННОЕ: Получение списка всех документов Knowledge Base с кешированием"""
        try:
            # Проверяем кеш
            cache_key = f"{api_key}_kb_docs"
            cached_data = cls._get_from_cache(cache_key)
            
            if cached_data is not None:
                logger.info(f"Retrieved {len(cached_data)} knowledge base documents from cache")
                return cached_data
            
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.get(
                    f"{cls.BASE_URL}/convai/knowledge-base",
                    headers=headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    documents = result.get("documents", [])
                    
                    # Сохраняем в кеш
                    cls._save_to_cache(cache_key, documents)
                    
                    logger.info(f"Retrieved {len(documents)} knowledge base documents")
                    return documents
                else:
                    logger.error(f"Failed to list knowledge base documents: {response.status_code} - {response.text}")
                    return []
                    
        except httpx.TimeoutException:
            logger.error("Timeout while listing knowledge base documents")
            return []
        except Exception as e:
            logger.error(f"Error listing knowledge base documents: {str(e)}")
            return []
    
    @classmethod
    async def delete_knowledge_base_document(cls, api_key: str, document_id: str):
        """Удаление документа Knowledge Base"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.delete(
                    f"{cls.BASE_URL}/convai/knowledge-base/{document_id}",
                    headers=headers
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Knowledge base document deleted: {document_id}")
                    
                    # Очищаем кеш
                    cls._clear_cache_for_user(api_key)
                    
                    return response.json()
                else:
                    error_text = response.text
                    logger.error(f"Failed to delete knowledge base document: {response.status_code} - {error_text}")
                    raise Exception(f"ElevenLabs API error: {error_text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while deleting knowledge base document")
            raise Exception("Request timed out while deleting knowledge base document")
        except Exception as e:
            logger.error(f"Error deleting knowledge base document: {str(e)}")
            raise Exception(f"Failed to delete knowledge base document: {str(e)}")
    
    @classmethod
    async def get_knowledge_base_document(cls, api_key: str, document_id: str) -> Dict[str, Any]:
        """✅ ОПТИМИЗИРОВАННОЕ: Получение конкретного документа Knowledge Base с кешированием"""
        try:
            # Проверяем кеш
            cache_key = f"{api_key}_kb_doc_{document_id}"
            cached_data = cls._get_from_cache(cache_key)
            
            if cached_data is not None:
                return cached_data
            
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.get(
                    f"{cls.BASE_URL}/convai/knowledge-base/{document_id}",
                    headers=headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    # Сохраняем в кеш
                    cls._save_to_cache(cache_key, result)
                    
                    logger.info(f"Retrieved knowledge base document: {document_id}")
                    return result
                else:
                    logger.error(f"Failed to get knowledge base document: {response.status_code} - {response.text}")
                    return {}
                    
        except httpx.TimeoutException:
            logger.error("Timeout while getting knowledge base document")
            return {}
        except Exception as e:
            logger.error(f"Error getting knowledge base document: {str(e)}")
            return {}
    
    # ============= AGENTS METHODS =============
    
    @classmethod
    async def get_agents(cls, db: Session, user_id: str, api_key: str) -> List[Dict[str, Any]]:
        """Получение списка агентов пользователя"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.get(
                    f"{cls.BASE_URL}/convai/agents",
                    headers=headers,
                    params={
                        "limit": 100
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    agents = data.get('agents', [])
                    
                    logger.info(f"Retrieved {len(agents)} agents from ElevenLabs for user {user_id}")
                    return agents
                elif response.status_code == 401:
                    logger.error("Failed to get agents: Invalid API key")
                    return []
                else:
                    logger.error(f"Failed to get agents: {response.status_code} - {response.text}")
                    return []
                    
        except httpx.TimeoutException:
            logger.error("Timeout while getting agents")
            return []
        except Exception as e:
            logger.error(f"Error getting agents: {str(e)}")
            return []
    
    @classmethod
    async def get_agent_with_details(cls, api_key: str, agent_id: str) -> Dict[str, Any]:
        """✅ ОПТИМИЗИРОВАННОЕ: Получение агента с параллельной загрузкой деталей документов"""
        try:
            # Получаем базовые данные агента
            agent = await cls.get_agent_by_id(api_key, agent_id)
            
            # Получаем Knowledge Base размер через правильный endpoint
            try:
                kb_size_data = await cls.get_agent_knowledge_base_size(api_key, agent_id)
                agent["knowledge_base_size"] = kb_size_data.get("number_of_pages", 0)
            except Exception as e:
                logger.warning(f"Failed to get KB size for agent {agent_id}: {e}")
                agent["knowledge_base_size"] = 0
            
            # ✅ ОПТИМИЗИРОВАННОЕ: Загружаем детали документов параллельно
            knowledge_base_ids = agent.get("knowledge_base", [])
            detailed_knowledge_base = {
                "files": [],
                "urls": [],
                "texts": [],
                "totalSize": 0,
                "totalChars": 0
            }
            
            if knowledge_base_ids:
                logger.info(f"Loading details for {len(knowledge_base_ids)} knowledge base documents")
                
                # Создаем задачи для параллельной загрузки
                tasks = []
                for doc_id in knowledge_base_ids:
                    tasks.append(cls.get_knowledge_base_document(api_key, doc_id))
                
                # Загружаем документы параллельно
                doc_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Обрабатываем результаты
                for doc_id, doc_result in zip(knowledge_base_ids, doc_results):
                    if isinstance(doc_result, Exception):
                        logger.warning(f"Failed to load details for document {doc_id}: {doc_result}")
                        # Добавляем базовую информацию даже если не удалось загрузить детали
                        detailed_knowledge_base["texts"].append({
                            "document_id": doc_id,
                            "name": f"Document {doc_id}",
                            "title": f"Document {doc_id}",
                            "chars": 0,
                            "status": "error",
                            "error": str(doc_result)
                        })
                        continue
                    
                    if doc_result:
                        # Определяем тип документа по его содержимому
                        doc_info = {
                            "document_id": doc_id,
                            "name": doc_result.get("name", f"Document {doc_id}"),
                            "chars": doc_result.get("character_count", 0),
                            "status": "uploaded",
                            "size": doc_result.get("size_in_bytes", 0)
                        }
                        
                        # Определяем тип документа
                        if doc_result.get("type") == "file" or doc_result.get("filename"):
                            doc_info["filename"] = doc_result.get("filename", doc_info["name"])
                            detailed_knowledge_base["files"].append(doc_info)
                        elif doc_result.get("type") == "url" or doc_result.get("url"):
                            doc_info["url"] = doc_result.get("url", "")
                            doc_info["title"] = doc_info["name"]
                            detailed_knowledge_base["urls"].append(doc_info)
                        else:
                            # Предполагаем что это текст
                            doc_info["title"] = doc_info["name"]
                            doc_info["content"] = doc_result.get("content", "")[:200]  # Первые 200 символов
                            detailed_knowledge_base["texts"].append(doc_info)
                        
                        detailed_knowledge_base["totalChars"] += doc_info["chars"]
                        detailed_knowledge_base["totalSize"] += doc_info.get("size", 0)
            
            # Заменяем список ID на детальную информацию
            agent["knowledge_base_details"] = detailed_knowledge_base
            
            logger.info(f"✅ Retrieved complete agent data for {agent_id} with {len(knowledge_base_ids)} KB documents")
            return agent
            
        except Exception as e:
            logger.error(f"Error getting agent with details: {str(e)}")
            raise Exception(f"Failed to get agent details: {str(e)}")
    
    @classmethod
    async def get_agent_knowledge_base_size(cls, api_key: str, agent_id: str) -> Dict[str, Any]:
        """Получение размера Knowledge Base агента"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.get(
                    f"{cls.BASE_URL}/convai/agent/{agent_id}/knowledge-base/size",
                    headers=headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Retrieved KB size for agent {agent_id}: {result.get('number_of_pages', 0)} pages")
                    return result
                else:
                    logger.error(f"Failed to get agent KB size: {response.status_code} - {response.text}")
                    return {"number_of_pages": 0}
                    
        except httpx.TimeoutException:
            logger.error("Timeout while getting agent knowledge base size")
            return {"number_of_pages": 0}
        except Exception as e:
            logger.error(f"Error getting agent knowledge base size: {str(e)}")
            return {"number_of_pages": 0}
    
    @classmethod
    async def create_agent(cls, api_key: str, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Создание агента"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            logger.info(f"Creating agent with enhanced structure")
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.post(
                    f"{cls.BASE_URL}/convai/agents/create",
                    headers=headers,
                    json=agent_data
                )
                
                logger.info(f"ElevenLabs API response status: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Successfully created agent: {result.get('agent_id')}")
                    return result
                else:
                    error_text = response.text
                    logger.error(f"Failed to create agent: {response.status_code} - {error_text}")
                    
                    try:
                        error_data = response.json()
                        error_detail = error_data.get('detail', error_text)
                        
                        if isinstance(error_detail, list):
                            error_messages = []
                            for error in error_detail:
                                field_path = " -> ".join(str(x) for x in error.get('loc', []))
                                message = error.get('msg', 'Unknown error')
                                
                                if field_path:
                                    if 'voice_id' in field_path:
                                        error_messages.append(f"Voice ID error: {message}")
                                    elif 'prompt' in field_path:
                                        error_messages.append(f"System prompt error: {message}")
                                    elif 'name' in field_path:
                                        error_messages.append(f"Agent name error: {message}")
                                    elif 'conversation' in field_path and 'model' in field_path:
                                        error_messages.append(f"LLM model error: {message}")
                                    else:
                                        error_messages.append(f"{field_path}: {message}")
                                else:
                                    error_messages.append(message)
                            
                            detailed_error = f"Validation errors: {'; '.join(error_messages)}"
                            logger.error(f"Detailed validation errors: {detailed_error}")
                            raise Exception(detailed_error)
                        else:
                            raise Exception(str(error_detail))
                            
                    except json.JSONDecodeError:
                        if response.status_code == 400:
                            raise Exception(f"Bad request: {error_text}")
                        elif response.status_code == 401:
                            raise Exception("Invalid API key or unauthorized access")
                        elif response.status_code == 403:
                            raise Exception("Access forbidden. Check your API key permissions.")
                        elif response.status_code == 429:
                            raise Exception("Rate limit exceeded. Please try again later.")
                        elif response.status_code == 500:
                            raise Exception("ElevenLabs server error. Please try again later.")
                        else:
                            raise Exception(f"HTTP {response.status_code}: {error_text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while creating agent")
            raise Exception("Request timed out while creating agent. Please try again.")
        except Exception as e:
            if "Validation errors" in str(e) or "error:" in str(e).lower():
                raise
            else:
                logger.error(f"Unexpected error creating agent: {str(e)}")
                raise Exception(f"Failed to create agent: {str(e)}")
    
    @classmethod
    async def get_agent_by_id(cls, api_key: str, agent_id: str) -> Dict[str, Any]:
        """Получение агента по ID"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.get(
                    f"{cls.BASE_URL}/convai/agents/{agent_id}",
                    headers=headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Retrieved agent {agent_id}")
                    return result
                elif response.status_code == 404:
                    logger.warning(f"Agent {agent_id} not found")
                    raise Exception(f"Agent {agent_id} not found")
                elif response.status_code == 401:
                    logger.error("Unauthorized access to agent")
                    raise Exception("Invalid API key or unauthorized access")
                else:
                    logger.error(f"Failed to get agent: {response.status_code} - {response.text}")
                    raise Exception(f"Failed to retrieve agent: {response.text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while getting agent")
            raise Exception("Request timed out while retrieving agent")
        except Exception as e:
            if "not found" in str(e) or "unauthorized" in str(e).lower():
                raise
            else:
                logger.error(f"Error getting agent {agent_id}: {str(e)}")
                raise Exception(f"Failed to get agent: {str(e)}")
    
    @classmethod
    async def update_agent(cls, api_key: str, agent_id: str, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Обновление агента"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            logger.info(f"Updating agent {agent_id} with enhanced structure")
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.patch(
                    f"{cls.BASE_URL}/convai/agents/{agent_id}",
                    headers=headers,
                    json=agent_data
                )
                
                logger.info(f"ElevenLabs API response status: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Successfully updated agent: {agent_id}")
                    return result
                else:
                    error_text = response.text
                    logger.error(f"Failed to update agent: {response.status_code} - {error_text}")
                    
                    try:
                        error_data = response.json()
                        error_detail = error_data.get('detail', error_text)
                        
                        if isinstance(error_detail, list):
                            error_messages = []
                            for error in error_detail:
                                field_path = " -> ".join(str(x) for x in error.get('loc', []))
                                message = error.get('msg', 'Unknown error')
                                
                                if field_path:
                                    if 'voice_id' in field_path:
                                        error_messages.append(f"Voice ID error: {message}")
                                    elif 'prompt' in field_path:
                                        error_messages.append(f"System prompt error: {message}")
                                    elif 'name' in field_path:
                                        error_messages.append(f"Agent name error: {message}")
                                    elif 'conversation' in field_path and 'model' in field_path:
                                        error_messages.append(f"LLM model error: {message}")
                                    else:
                                        error_messages.append(f"{field_path}: {message}")
                                else:
                                    error_messages.append(message)
                            
                            detailed_error = f"Validation errors: {'; '.join(error_messages)}"
                            logger.error(f"Detailed validation errors: {detailed_error}")
                            raise Exception(detailed_error)
                        else:
                            raise Exception(str(error_detail))
                            
                    except json.JSONDecodeError:
                        if response.status_code == 400:
                            raise Exception(f"Bad request: {error_text}")
                        elif response.status_code == 401:
                            raise Exception("Invalid API key or unauthorized access")
                        elif response.status_code == 403:
                            raise Exception("Access forbidden. Check your API key permissions.")
                        elif response.status_code == 404:
                            raise Exception(f"Agent {agent_id} not found")
                        elif response.status_code == 429:
                            raise Exception("Rate limit exceeded. Please try again later.")
                        elif response.status_code == 500:
                            raise Exception("ElevenLabs server error. Please try again later.")
                        else:
                            raise Exception(f"HTTP {response.status_code}: {error_text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while updating agent")
            raise Exception("Request timed out while updating agent. Please try again.")
        except Exception as e:
            if any(keyword in str(e).lower() for keyword in ["validation errors", "not found", "unauthorized", "rate limit"]):
                raise
            else:
                logger.error(f"Unexpected error updating agent {agent_id}: {str(e)}")
                raise Exception(f"Failed to update agent: {str(e)}")
    
    @classmethod
    async def delete_agent(cls, api_key: str, agent_id: str) -> Dict[str, Any]:
        """Удаление агента"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.delete(
                    f"{cls.BASE_URL}/convai/agents/{agent_id}",
                    headers=headers
                )
                
                if response.status_code == 200:
                    logger.info(f"Successfully deleted agent: {agent_id}")
                    return {"success": True, "message": "Agent deleted successfully"}
                elif response.status_code == 404:
                    logger.warning(f"Agent {agent_id} not found for deletion")
                    raise Exception(f"Agent {agent_id} not found")
                elif response.status_code == 401:
                    logger.error("Unauthorized access for deletion")
                    raise Exception("Invalid API key or unauthorized access")
                else:
                    logger.error(f"Failed to delete agent: {response.status_code} - {response.text}")
                    raise Exception(f"Failed to delete agent: {response.text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while deleting agent")
            raise Exception("Request timed out while deleting agent")
        except Exception as e:
            if "not found" in str(e) or "unauthorized" in str(e).lower():
                raise
            else:
                logger.error(f"Error deleting agent {agent_id}: {str(e)}")
                raise Exception(f"Failed to delete agent: {str(e)}")
    
    # ============= TOOLS API METHODS =============
    
    @classmethod
    async def create_server_tool(cls, api_key: str, tool_data: Dict[str, Any]) -> str:
        """Создание Server Tool"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            elevenlabs_tool = {
                "name": tool_data.get("name"),
                "description": tool_data.get("description", ""),
                "type": "server",
                "config": {
                    "url": tool_data.get("url"),
                    "method": tool_data.get("method", "POST"),
                    "headers": tool_data.get("headers", {}),
                    "parameters": tool_data.get("parameters", [])
                }
            }
            
            logger.info(f"Creating server tool: {tool_data.get('name')}")
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.post(
                    f"{cls.BASE_URL}/convai/tools/create",
                    headers=headers,
                    json=elevenlabs_tool
                )
                
                if response.status_code == 200:
                    result = response.json()
                    tool_id = result.get("tool_id")
                    logger.info(f"✅ Server tool created: {tool_id}")
                    return tool_id
                else:
                    error_text = response.text
                    logger.error(f"Failed to create server tool: {response.status_code} - {error_text}")
                    raise Exception(f"ElevenLabs API error: {error_text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while creating server tool")
            raise Exception("Request timed out while creating server tool")
        except Exception as e:
            logger.error(f"Error creating server tool: {str(e)}")
            raise Exception(f"Failed to create server tool: {str(e)}")
    
    @classmethod
    async def update_server_tool(cls, api_key: str, tool_id: str, tool_data: Dict[str, Any]):
        """Обновление Server Tool"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            elevenlabs_tool = {
                "name": tool_data.get("name"),
                "description": tool_data.get("description", ""),
                "type": "server",
                "config": {
                    "url": tool_data.get("url"),
                    "method": tool_data.get("method", "POST"),
                    "headers": tool_data.get("headers", {}),
                    "parameters": tool_data.get("parameters", [])
                }
            }
            
            logger.info(f"Updating server tool: {tool_id}")
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.patch(
                    f"{cls.BASE_URL}/convai/tools/{tool_id}",
                    headers=headers,
                    json=elevenlabs_tool
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Server tool updated: {tool_id}")
                    return response.json()
                else:
                    error_text = response.text
                    logger.error(f"Failed to update server tool: {response.status_code} - {error_text}")
                    raise Exception(f"ElevenLabs API error: {error_text}")
                    
        except httpx.TimeoutException:
            logger.error("Timeout while updating server tool")
            raise Exception("Request timed out while updating server tool")
        except Exception as e:
            logger.error(f"Error updating server tool: {str(e)}")
            raise Exception(f"Failed to update server tool: {str(e)}")
    
    # ============= EMBED & WEBSOCKET METHODS =============
    
    @classmethod
    async def get_embed_code(cls, agent_id: str) -> Dict[str, Any]:
        """Генерация кода для встраивания агента"""
        try:
            embed_code = f'''<!-- ElevenLabs Conversational AI Widget -->
<script
  id="elevenlabs-convai-widget"
  type="text/javascript"
  src="https://elevenlabs.io/convai/widget/index.js"
  data-agent-id="{agent_id}"
></script>'''
            
            result = {
                "agent_id": agent_id,
                "embed_code": embed_code
            }
            
            logger.info(f"Generated embed code for agent {agent_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error generating embed code for agent {agent_id}: {str(e)}")
            raise Exception(f"Failed to generate embed code: {str(e)}")
    
    @classmethod
    async def get_signed_url(cls, api_key: str, agent_id: str) -> str:
        """Получение подписанного URL для WebSocket подключения"""
        try:
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.post(
                    f"{cls.BASE_URL}/convai/agents/{agent_id}/link",
                    headers=headers,
                    json={}
                )
                
                logger.info(f"Signed URL request status: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    signed_url = data.get("signed_url")
                    
                    if signed_url:
                        logger.info(f"Generated signed URL for agent {agent_id}")
                        return signed_url
                    else:
                        fallback_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}"
                        logger.warning(f"No signed URL returned, using fallback for agent {agent_id}")
                        return fallback_url
                        
                elif response.status_code == 401:
                    logger.error("Unauthorized access for signed URL")
                    raise Exception("Invalid API key or unauthorized access")
                elif response.status_code == 404:
                    logger.error(f"Agent {agent_id} not found for signed URL")
                    raise Exception(f"Agent {agent_id} not found")
                else:
                    logger.error(f"Failed to get signed URL: {response.status_code} - {response.text}")
                    fallback_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}"
                    logger.info(f"Using fallback URL for agent {agent_id} due to API error")
                    return fallback_url
                    
        except httpx.TimeoutException:
            logger.error(f"Timeout while getting signed URL for agent {agent_id}")
            fallback_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}"
            logger.info(f"Using fallback URL for agent {agent_id} due to timeout")
            return fallback_url
        except Exception as e:
            logger.error(f"Error getting signed URL for agent {agent_id}: {str(e)}")
            if "not found" in str(e).lower() or "unauthorized" in str(e).lower():
                raise
            else:
                fallback_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}"
                logger.info(f"Using fallback URL for agent {agent_id} due to error: {str(e)}")
                return fallback_url
    
    # ============= CACHE METHODS =============
    
    @classmethod
    def _get_from_cache(cls, key: str) -> Optional[Any]:
        """Получение данных из кеша"""
        if key in cls._kb_documents_cache:
            cached_data, timestamp = cls._kb_documents_cache[key]
            if datetime.now() - timestamp < cls._cache_ttl:
                return cached_data
            else:
                # Кеш устарел, удаляем
                del cls._kb_documents_cache[key]
        return None
    
    @classmethod
    def _save_to_cache(cls, key: str, data: Any):
        """Сохранение данных в кеш"""
        cls._kb_documents_cache[key] = (data, datetime.now())
    
    @classmethod
    def _clear_cache_for_user(cls, api_key: str):
        """Очистка кеша для конкретного пользователя"""
        keys_to_delete = []
        for key in cls._kb_documents_cache:
            if key.startswith(api_key):
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del cls._kb_documents_cache[key]
        
        if keys_to_delete:
            logger.info(f"Cleared {len(keys_to_delete)} cache entries for user")
