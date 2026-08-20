"""
ИСПРАВЛЕННАЯ РЕАЛИЗАЦИЯ ElevenLabs API endpoints с правильной Knowledge Base
Исправлены endpoints и логика работы согласно документации ElevenLabs
✅ ДОБАВЛЕНА АВТОМАТИЧЕСКАЯ ИНДЕКСАЦИЯ ДОКУМЕНТОВ
✅ ИСПРАВЛЕНА СТРУКТУРА ДАННЫХ ДЛЯ API
✅ ДОБАВЛЕНА ЗАГРУЗКА ДЕТАЛЕЙ ДОКУМЕНТОВ
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
import json

from backend.core.logging import get_logger
from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.services.elevenlabs_service import ElevenLabsService
from backend.schemas.elevenlabs import (
    ElevenLabsApiKeyRequest,
    ElevenLabsVoiceResponse,
    ElevenLabsEmbedResponse
)

logger = get_logger(__name__)
router = APIRouter()

def transform_frontend_to_elevenlabs_format(frontend_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    ✅ КАРДИНАЛЬНО ИСПРАВЛЕННАЯ функция: Правильное преобразование данных в формат ElevenLabs API
    """
    logger.info(f"🔄 Трансформация данных для ElevenLabs API")
    
    # Валидация обязательных полей
    required_fields = ['name', 'system_prompt', 'voice_id']
    for field in required_fields:
        if not frontend_data.get(field):
            raise ValueError(f"Field '{field}' is required")
    
    # ✅ ПРАВИЛЬНАЯ базовая структура согласно документации ElevenLabs API
    elevenlabs_data = {
        "name": frontend_data.get("name", "").strip(),
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt": frontend_data.get("system_prompt", "").strip()
                },
                "language": frontend_data.get("language", "en")
            },
            "tts": {
                "voice_id": frontend_data.get("voice_id"),
                "model": "eleven_flash_v2_5"
            },
            "asr": {
                "quality": "high",
                "language": frontend_data.get("language", "en")
            },
            "conversation": {
                "model": frontend_data.get("llm_model", "gpt-4"),
                "temperature": float(frontend_data.get("llm_temperature", 0.7)),
                "max_tokens": int(frontend_data.get("llm_max_tokens", 150))
            }
        },
        "platform_settings": {
            "widget": {
                "color_scheme": "light"
            }
        }
    }
    
    # ✅ ИСПРАВЛЕНО: Добавляем first_message если есть
    first_message = frontend_data.get("first_message", "").strip()
    if first_message:
        elevenlabs_data["conversation_config"]["agent"]["first_message"] = first_message
    
    # ✅ ИСПРАВЛЕНО: Правильная обработка TTS настроек
    tts_config = elevenlabs_data["conversation_config"]["tts"]
    
    if frontend_data.get("tts_stability") is not None:
        stability = float(frontend_data["tts_stability"])
        tts_config["stability"] = max(0.0, min(1.0, stability))
        
    if frontend_data.get("tts_similarity_boost") is not None:
        similarity = float(frontend_data["tts_similarity_boost"])
        tts_config["similarity_boost"] = max(0.0, min(1.0, similarity))
        
    if frontend_data.get("tts_speaker_boost") is not None:
        tts_config["use_speaker_boost"] = bool(frontend_data["tts_speaker_boost"])
    else:
        tts_config["use_speaker_boost"] = True
    
    # ✅ ИСПРАВЛЕНО: Правильная обработка LLM параметров с валидацией
    conversation_config = elevenlabs_data["conversation_config"]["conversation"]
    conversation_config["temperature"] = max(0.0, min(1.0, conversation_config["temperature"]))
    conversation_config["max_tokens"] = max(50, min(500, conversation_config["max_tokens"]))
    
    # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Правильная обработка Knowledge Base
    # ElevenLabs API ожидает массив ID документов в поле "knowledge_base"
    knowledge_base_documents = frontend_data.get("knowledge_base_documents", [])
    if knowledge_base_documents and isinstance(knowledge_base_documents, list):
        # Фильтруем пустые и None значения
        kb_docs = [doc_id for doc_id in knowledge_base_documents if doc_id and doc_id.strip()]
        if kb_docs:
            elevenlabs_data["knowledge_base"] = kb_docs
            logger.info(f"✅ Добавлены Knowledge Base документы: {len(kb_docs)} шт.")
    
    # ✅ ИСПРАВЛЕНО: Обработка Dynamic Variables
    if frontend_data.get("dynamic_variables"):
        dynamic_vars = {}
        for var in frontend_data["dynamic_variables"]:
            if isinstance(var, dict) and var.get("name") and var.get("value"):
                dynamic_vars[var["name"]] = var["value"]
        
        if dynamic_vars:
            # Dynamic variables добавляются в conversation_config
            elevenlabs_data["conversation_config"]["dynamic_variables"] = dynamic_vars
            logger.info(f"✅ Добавлены динамические переменные: {list(dynamic_vars.keys())}")
    
    # ✅ ИСПРАВЛЕНО: Обработка Server Tools
    if frontend_data.get("server_tools"):
        server_tools = []
        for tool in frontend_data["server_tools"]:
            if isinstance(tool, dict) and tool.get("name") and tool.get("url"):
                # ElevenLabs ожидает определенную структуру для tools
                elevenlabs_tool = {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "type": "webhook",
                    "config": {
                        "url": tool["url"],
                        "method": tool.get("method", "POST").upper(),
                        "headers": tool.get("headers", {}),
                    }
                }
                
                # Добавляем параметры если есть
                if tool.get("parameters"):
                    try:
                        if isinstance(tool["parameters"], str):
                            elevenlabs_tool["config"]["parameters"] = json.loads(tool["parameters"])
                        else:
                            elevenlabs_tool["config"]["parameters"] = tool["parameters"]
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON in tool parameters for {tool['name']}")
                
                server_tools.append(elevenlabs_tool)
        
        if server_tools:
            elevenlabs_data["server_tools"] = server_tools
            logger.info(f"✅ Добавлены server tools: {len(server_tools)} шт.")
    
    # ✅ ИСПРАВЛЕНО: Обработка Client Tools
    if frontend_data.get("client_tools"):
        client_tools = []
        for tool in frontend_data["client_tools"]:
            if isinstance(tool, dict) and tool.get("name"):
                elevenlabs_tool = {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "type": "client_function"
                }
                
                # Добавляем параметры если есть
                if tool.get("parameters"):
                    try:
                        if isinstance(tool["parameters"], str):
                            elevenlabs_tool["parameters"] = json.loads(tool["parameters"])
                        else:
                            elevenlabs_tool["parameters"] = tool["parameters"]
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON in client tool parameters for {tool['name']}")
                
                client_tools.append(elevenlabs_tool)
        
        if client_tools:
            elevenlabs_data["client_tools"] = client_tools
            logger.info(f"✅ Добавлены client tools: {len(client_tools)} шт.")
    
    # ✅ ИСПРАВЛЕНО: Обработка System Tools
    if frontend_data.get("system_tools"):
        system_tools = frontend_data["system_tools"]
        if isinstance(system_tools, dict):
            elevenlabs_system_tools = []
            
            if system_tools.get("endCall"):
                elevenlabs_system_tools.append({
                    "type": "end_call",
                    "enabled": True
                })
            
            if system_tools.get("agentTransfer"):
                elevenlabs_system_tools.append({
                    "type": "agent_transfer", 
                    "enabled": True
                })
            
            if system_tools.get("humanHandoff"):
                elevenlabs_system_tools.append({
                    "type": "human_handoff",
                    "enabled": True
                })
            
            if system_tools.get("languageDetection"):
                elevenlabs_system_tools.append({
                    "type": "language_detection",
                    "enabled": True
                })
            
            if elevenlabs_system_tools:
                elevenlabs_data["system_tools"] = elevenlabs_system_tools
                logger.info(f"✅ Добавлены system tools: {len(elevenlabs_system_tools)} шт.")
    
    # ✅ Финальная валидация
    if not elevenlabs_data["conversation_config"]["tts"]["voice_id"]:
        raise ValueError("Voice ID is required and cannot be empty")
    
    logger.info(f"✅ Трансформация завершена успешно")
    logger.info(f"📊 Структура данных: name={elevenlabs_data.get('name')}, "
                f"voice_id={elevenlabs_data['conversation_config']['tts']['voice_id']}, "
                f"llm_model={elevenlabs_data['conversation_config']['conversation']['model']}, "
                f"kb_docs={len(elevenlabs_data.get('knowledge_base', []))}")
    
    return elevenlabs_data


def transform_elevenlabs_to_frontend_format(elevenlabs_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    ✅ ИСПРАВЛЕННАЯ функция: Преобразует данные от ElevenLabs API в формат для фронтенда
    Теперь обрабатывает детальную информацию о документах Knowledge Base
    """
    logger.info(f"🔄 Обратная трансформация данных от ElevenLabs")
    
    try:
        conversation_config = elevenlabs_data.get("conversation_config", {})
        agent_config = conversation_config.get("agent", {})
        tts_config = conversation_config.get("tts", {})
        llm_config = conversation_config.get("conversation", {})
        
        frontend_data = {
            "elevenlabs_agent_id": elevenlabs_data.get("agent_id", ""),
            "id": elevenlabs_data.get("agent_id", ""),
            "name": elevenlabs_data.get("name", ""),
            
            # Agent настройки
            "system_prompt": agent_config.get("prompt", {}).get("prompt", ""),
            "first_message": agent_config.get("first_message", ""),
            "language": agent_config.get("language", "en"),
            
            # Voice настройки
            "voice_id": tts_config.get("voice_id", ""),
            "tts_stability": float(tts_config.get("stability", 0.5)) if tts_config.get("stability") is not None else 0.5,
            "tts_similarity_boost": float(tts_config.get("similarity_boost", 0.5)) if tts_config.get("similarity_boost") is not None else 0.5,
            "tts_speaker_boost": bool(tts_config.get("use_speaker_boost", True)),
            
            # ✅ ИСПРАВЛЕНО: Правильная обработка LLM настроек
            "llm_model": llm_config.get("model", "gpt-4"),
            "llm_temperature": float(llm_config.get("temperature", 0.7)) if llm_config.get("temperature") is not None else 0.7,
            "llm_max_tokens": int(llm_config.get("max_tokens", 150)) if llm_config.get("max_tokens") is not None else 150,
            
            # ✅ Knowledge Base документы - только ID
            "knowledge_base_documents": elevenlabs_data.get("knowledge_base", []),
            
            # ✅ НОВОЕ: Детальная информация о Knowledge Base (если есть)
            "knowledge_base": elevenlabs_data.get("knowledge_base_details", {
                "files": [],
                "urls": [],
                "texts": [],
                "totalSize": 0,
                "totalChars": 0
            }),
            
            "server_tools": elevenlabs_data.get("server_tools", []),
            "client_tools": elevenlabs_data.get("client_tools", []),
            "dynamic_variables": elevenlabs_data.get("dynamic_variables", [])
        }
        
        # ✅ Преобразуем dynamic_variables из объекта в массив
        if conversation_config.get("dynamic_variables") and isinstance(conversation_config["dynamic_variables"], dict):
            frontend_data["dynamic_variables"] = [
                {"name": key, "value": value} 
                for key, value in conversation_config["dynamic_variables"].items()
            ]
        
        # Метаданные
        if elevenlabs_data.get("metadata"):
            frontend_data["metadata"] = elevenlabs_data["metadata"]
            
        if elevenlabs_data.get("created_at"):
            frontend_data["created_at"] = elevenlabs_data["created_at"]
            
        if elevenlabs_data.get("updated_at"):
            frontend_data["updated_at"] = elevenlabs_data["updated_at"]
        
        logger.info(f"✅ Обратная трансформация завершена")
        return frontend_data
        
    except Exception as e:
        logger.error(f"❌ Ошибка трансформации данных: {str(e)}")
        
        return {
            "elevenlabs_agent_id": elevenlabs_data.get("agent_id", ""),
            "id": elevenlabs_data.get("agent_id", ""),
            "name": elevenlabs_data.get("name", "Error loading agent"),
            "system_prompt": "",
            "first_message": "",
            "language": "en",
            "voice_id": "",
            "llm_model": "gpt-4",
            "llm_temperature": 0.7,
            "llm_max_tokens": 150,
            "tts_stability": 0.5,
            "tts_similarity_boost": 0.5,
            "tts_speaker_boost": True,
            "knowledge_base_documents": [],
            "knowledge_base": {
                "files": [],
                "urls": [],
                "texts": [],
                "totalSize": 0,
                "totalChars": 0
            },
            "server_tools": [],
            "client_tools": [],
            "dynamic_variables": []
        }


# ============= API KEY ENDPOINTS =============

@router.get("/api-key/status")
async def check_api_key_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Check if user has ElevenLabs API key configured"""
    try:
        logger.info(f"Checking API key status for user {current_user.id}")
        
        has_api_key = bool(current_user.elevenlabs_api_key)
        logger.info(f"User {current_user.id} has ElevenLabs API key: {has_api_key}")
        
        if has_api_key:
            is_valid = await ElevenLabsService.validate_api_key(current_user.elevenlabs_api_key)
            logger.info(f"API key validation result: {is_valid}")
            
            return {
                "has_api_key": True,
                "is_valid": is_valid,
                "message": "API key is configured and valid" if is_valid else "API key is configured but invalid"
            }
        else:
            return {
                "has_api_key": False,
                "is_valid": False,
                "message": "ElevenLabs API key not found. Please add your API key first."
            }
    except Exception as e:
        logger.error(f"Error checking API key status: {str(e)}")
        return {
            "has_api_key": False,
            "is_valid": False,
            "message": "Error checking API key status"
        }


@router.post("/api-key")
async def save_api_key(
    request: ElevenLabsApiKeyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Save and validate ElevenLabs API key"""
    try:
        logger.info(f"Saving API key for user {current_user.id}")
        
        # Валидируем API ключ
        is_valid = await ElevenLabsService.validate_api_key(request.api_key)
        
        if not is_valid:
            logger.warning(f"Invalid API key provided by user {current_user.id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ElevenLabs API key"
            )
        
        # Сохраняем API ключ
        current_user.elevenlabs_api_key = request.api_key
        db.commit()
        
        logger.info(f"API key saved successfully for user {current_user.id}")
        
        # Получаем доступные голоса
        voices = await ElevenLabsService.get_available_voices(request.api_key)
        
        return {
            "success": True,
            "message": "API key saved successfully",
            "voices": voices
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving API key: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save API key"
        )


@router.get("/voices", response_model=List[ElevenLabsVoiceResponse])
async def get_voices(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get available ElevenLabs voices"""
    try:
        logger.info(f"Getting voices for user {current_user.id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found. Please add your API key first."
            )
        
        voices = await ElevenLabsService.get_available_voices(current_user.elevenlabs_api_key)
        logger.info(f"Retrieved {len(voices)} voices from ElevenLabs")
        
        result = []
        for voice in voices:
            result.append(ElevenLabsVoiceResponse(
                voice_id=voice.get("voice_id"),
                name=voice.get("name"),
                preview_url=voice.get("preview_url"),
                category=voice.get("category")
            ))
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting voices: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get voices"
        )


# ============= KNOWLEDGE BASE ENDPOINTS (ИСПРАВЛЕНЫ) =============

@router.post("/knowledge-base/file")
async def create_knowledge_base_from_file(
    file: UploadFile = File(...),
    name: str = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """✅ ИСПРАВЛЕН: Создание документа Knowledge Base из файла с автоматической индексацией"""
    try:
        # Используем имя файла если name не указано
        document_name = name if name else file.filename
        
        logger.info(f"Creating knowledge base document from file: {document_name}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        # ✅ ИСПРАВЛЕНО: Создаем документ с автоматической индексацией через сервис
        document_id = await ElevenLabsService.create_knowledge_base_from_file(
            current_user.elevenlabs_api_key,
            document_name,
            file
        )
        
        logger.info(f"✅ Knowledge base document created and indexed: {document_id}")
        
        return {
            "success": True,
            "document_id": document_id,
            "name": document_name,
            "chars": file.size,  # Примерная оценка
            "message": "Knowledge base document created and indexed successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating knowledge base from file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create knowledge base document: {str(e)}"
        )


@router.post("/knowledge-base/url")
async def create_knowledge_base_from_url(
    url_data: Dict[str, str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """✅ ИСПРАВЛЕН: Создание документа Knowledge Base из URL с автоматической индексацией"""
    try:
        logger.info(f"Creating knowledge base document from URL: {url_data.get('url')}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        if not url_data.get("url"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL is required"
            )
        
        # ✅ ИСПРАВЛЕНО: Создаем документ с автоматической индексацией
        document_id = await ElevenLabsService.create_knowledge_base_from_url(
            current_user.elevenlabs_api_key,
            url_data.get("name", "URL Document"),
            url_data["url"]
        )
        
        logger.info(f"✅ Knowledge base document created and indexed from URL: {document_id}")
        
        return {
            "success": True,
            "document_id": document_id,
            "name": url_data.get("name", "URL Document"),
            "url": url_data["url"],
            "chars": 2000,  # Примерная оценка
            "message": "Knowledge base document created and indexed from URL successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating knowledge base from URL: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create knowledge base document: {str(e)}"
        )


@router.post("/knowledge-base/text")
async def create_knowledge_base_from_text(
    text_data: Dict[str, str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """✅ ИСПРАВЛЕН: Создание документа Knowledge Base из текста с автоматической индексацией"""
    try:
        logger.info(f"Creating knowledge base document from text: {text_data.get('name')}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        if not text_data.get("text"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Text content is required"
            )
        
        # ✅ ИСПРАВЛЕНО: Создаем документ с автоматической индексацией
        document_id = await ElevenLabsService.create_knowledge_base_from_text(
            current_user.elevenlabs_api_key,
            text_data.get("name", "Text Document"),
            text_data["text"]
        )
        
        logger.info(f"✅ Knowledge base document created and indexed from text: {document_id}")
        
        return {
            "success": True,
            "document_id": document_id,
            "name": text_data.get("name", "Text Document"),
            "chars": len(text_data["text"]),
            "content_length": len(text_data["text"]),
            "message": "Knowledge base document created and indexed from text successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating knowledge base from text: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create knowledge base document: {str(e)}"
        )


@router.get("/knowledge-base")
async def list_knowledge_base_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """✅ НОВЫЙ: Получение списка всех документов Knowledge Base"""
    try:
        logger.info(f"Getting knowledge base documents for user {current_user.id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        documents = await ElevenLabsService.list_knowledge_base_documents(
            current_user.elevenlabs_api_key
        )
        
        logger.info(f"Retrieved {len(documents)} knowledge base documents")
        return {
            "success": True,
            "documents": documents,
            "count": len(documents)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting knowledge base documents: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get knowledge base documents"
        )


@router.delete("/knowledge-base/{document_id}")
async def delete_knowledge_base_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """✅ НОВЫЙ: Удаление документа Knowledge Base"""
    try:
        logger.info(f"Deleting knowledge base document: {document_id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        await ElevenLabsService.delete_knowledge_base_document(
            current_user.elevenlabs_api_key,
            document_id
        )
        
        logger.info(f"✅ Knowledge base document deleted: {document_id}")
        
        return {
            "success": True,
            "document_id": document_id,
            "message": "Knowledge base document deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting knowledge base document: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete knowledge base document: {str(e)}"
        )


# ============= AGENTS ENDPOINTS =============

@router.get("/")
async def get_agents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all ElevenLabs agents for the current user"""
    try:
        logger.info(f"Getting agents for user {current_user.id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found. Please add your API key first."
            )
        
        elevenlabs_agents = await ElevenLabsService.get_agents(
            db, str(current_user.id), current_user.elevenlabs_api_key
        )
        
        frontend_agents = []
        for agent in elevenlabs_agents:
            try:
                agent_details = await ElevenLabsService.get_agent_with_details(
                    current_user.elevenlabs_api_key, 
                    agent.get("agent_id")
                )
                
                frontend_agent = transform_elevenlabs_to_frontend_format(agent_details)
                frontend_agents.append(frontend_agent)
            except Exception as e:
                logger.error(f"Error transforming agent {agent.get('agent_id', 'unknown')}: {e}")
                frontend_agents.append({
                    "elevenlabs_agent_id": agent.get("agent_id"),
                    "id": agent.get("agent_id"),
                    "name": agent.get("name", "Error loading agent"),
                    "language": "en",
                    "system_prompt": "Error loading system prompt",
                    "voice_id": "",
                    "llm_model": "gpt-4",
                    "llm_temperature": 0.7,
                    "llm_max_tokens": 150,
                    "tts_stability": 0.5,
                    "tts_similarity_boost": 0.5,
                    "tts_speaker_boost": True,
                    "knowledge_base_documents": [],
                    "knowledge_base": {
                        "files": [],
                        "urls": [],
                        "texts": [],
                        "totalSize": 0,
                        "totalChars": 0
                    },
                    "server_tools": [],
                    "client_tools": [],
                    "dynamic_variables": []
                })
        
        logger.info(f"Retrieved and transformed {len(frontend_agents)} agents")
        return frontend_agents
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agents: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve agents"
        )


@router.post("/")
async def create_agent(
    agent_data: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """✅ КАРДИНАЛЬНО ИСПРАВЛЕНА: Создание агента с правильной Knowledge Base и индексацией"""
    try:
        logger.info(f"Creating agent with knowledge base for user {current_user.id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        # Валидация обязательных полей
        required_fields = [
            ("name", "Agent name is required"),
            ("voice_id", "Voice ID is required"),
            ("system_prompt", "System prompt is required")
        ]
        
        for field, error_message in required_fields:
            if not agent_data.get(field) or str(agent_data.get(field)).strip() == "":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_message
                )
        
        # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Правильная обработка Knowledge Base
        knowledge_base_doc_ids = []
        
        if agent_data.get("knowledge_base"):
            knowledge_base = agent_data["knowledge_base"]
            
            # Создаем и индексируем документы из текста
            for text_item in knowledge_base.get("texts", []):
                if text_item.get("content") and not text_item.get("document_id"):
                    try:
                        doc_id = await ElevenLabsService.create_knowledge_base_from_text(
                            current_user.elevenlabs_api_key,
                            text_item.get("title", "Text Document"),
                            text_item["content"]
                        )
                        knowledge_base_doc_ids.append(doc_id)
                        logger.info(f"✅ Created and indexed KB text document: {doc_id}")
                    except Exception as e:
                        logger.error(f"Failed to create text document: {e}")
                elif text_item.get("document_id"):
                    knowledge_base_doc_ids.append(text_item["document_id"])
            
            # Создаем и индексируем документы из URL
            for url_item in knowledge_base.get("urls", []):
                if url_item.get("url") and not url_item.get("document_id"):
                    try:
                        doc_id = await ElevenLabsService.create_knowledge_base_from_url(
                            current_user.elevenlabs_api_key,
                            url_item.get("title", "URL Document"),
                            url_item["url"]
                        )
                        knowledge_base_doc_ids.append(doc_id)
                        logger.info(f"✅ Created and indexed KB URL document: {doc_id}")
                    except Exception as e:
                        logger.error(f"Failed to create URL document: {e}")
                elif url_item.get("document_id"):
                    knowledge_base_doc_ids.append(url_item["document_id"])
            
            # Добавляем существующие документы из файлов
            for file_item in knowledge_base.get("files", []):
                if file_item.get("document_id"):
                    knowledge_base_doc_ids.append(file_item["document_id"])
        
        # ✅ ИСПРАВЛЕНО: Устанавливаем Knowledge Base IDs в данные агента
        if knowledge_base_doc_ids:
            agent_data["knowledge_base_documents"] = knowledge_base_doc_ids
            logger.info(f"✅ Added {len(knowledge_base_doc_ids)} KB documents to agent")
        
        # ✅ ИСПРАВЛЕНО: Создаем/обновляем серверные инструменты (если нужно)
        server_tool_ids = []
        if agent_data.get("server_tools"):
            for tool in agent_data["server_tools"]:
                if tool.get("name") and tool.get("url"):
                    try:
                        tool_id = await ElevenLabsService.create_server_tool(
                            current_user.elevenlabs_api_key,
                            tool
                        )
                        server_tool_ids.append(tool_id)
                        logger.info(f"Created server tool: {tool_id}")
                    except Exception as e:
                        logger.error(f"Failed to create server tool: {e}")
        
        # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Правильная трансформация данных
        elevenlabs_data = transform_frontend_to_elevenlabs_format(agent_data)
        
        # ✅ ИСПРАВЛЕНО: Создаем агента через правильный API
        result = await ElevenLabsService.create_agent(
            current_user.elevenlabs_api_key,
            elevenlabs_data
        )
        
        agent_id = result.get("agent_id")
        logger.info(f"✅ Agent created with ID: {agent_id}")
        
        # ✅ ИСПРАВЛЕНО: Получаем полные данные созданного агента
        complete_agent = await ElevenLabsService.get_agent_with_details(
            current_user.elevenlabs_api_key,
            agent_id
        )
        
        frontend_result = transform_elevenlabs_to_frontend_format(complete_agent)
        
        logger.info(f"✅ Complete agent created successfully: {agent_id}")
        return frontend_result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error creating agent: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create agent: {str(e)}"
        )


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get ElevenLabs agent by ID with full details"""
    try:
        logger.info(f"Getting agent {agent_id} for user {current_user.id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        elevenlabs_agent = await ElevenLabsService.get_agent_with_details(
            current_user.elevenlabs_api_key, 
            agent_id
        )
        
        frontend_agent = transform_elevenlabs_to_frontend_format(elevenlabs_agent)
        
        logger.info(f"✅ Successfully retrieved complete agent {agent_id}")
        return frontend_agent
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve agent"
        )


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    agent_data: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """✅ КАРДИНАЛЬНО ИСПРАВЛЕННАЯ: Обновление агента с правильной обработкой Knowledge Base"""
    try:
        logger.info(f"Updating agent {agent_id} for user {current_user.id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        # Валидация обязательных полей
        required_fields = [
            ("name", "Agent name is required"),
            ("voice_id", "Voice ID is required"),
            ("system_prompt", "System prompt is required")
        ]
        
        for field, error_message in required_fields:
            if not agent_data.get(field) or str(agent_data.get(field)).strip() == "":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_message
                )
        
        # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Правильная обработка Knowledge Base для обновления
        knowledge_base_doc_ids = []
        
        # Получаем текущего агента для проверки существующих KB документов
        try:
            current_agent = await ElevenLabsService.get_agent_with_details(
                current_user.elevenlabs_api_key,
                agent_id
            )
            existing_kb_docs = current_agent.get("knowledge_base", [])
            logger.info(f"Existing KB documents: {existing_kb_docs}")
        except Exception as e:
            logger.warning(f"Could not get existing KB documents: {e}")
            existing_kb_docs = []
        
        # Обрабатываем Knowledge Base только если он был изменен
        if agent_data.get("knowledge_base"):
            knowledge_base = agent_data["knowledge_base"]
            
            # Добавляем существующие документы (чтобы не потерять их)
            knowledge_base_doc_ids.extend(existing_kb_docs)
            
            # Создаем новые документы из текстов (только если нет document_id)
            for text_item in knowledge_base.get("texts", []):
                if text_item.get("content") and not text_item.get("document_id"):
                    try:
                        doc_id = await ElevenLabsService.create_knowledge_base_from_text(
                            current_user.elevenlabs_api_key,
                            text_item.get("title", "Text Document"),
                            text_item["content"]
                        )
                        knowledge_base_doc_ids.append(doc_id)
                        logger.info(f"✅ Created new indexed KB text document: {doc_id}")
                    except Exception as e:
                        logger.error(f"Error creating text document: {e}")
                elif text_item.get("document_id"):
                    # Добавляем уже существующий документ
                    if text_item["document_id"] not in knowledge_base_doc_ids:
                        knowledge_base_doc_ids.append(text_item["document_id"])
            
            # Создаем новые документы из URL (только если нет document_id)
            for url_item in knowledge_base.get("urls", []):
                if url_item.get("url") and not url_item.get("document_id"):
                    try:
                        doc_id = await ElevenLabsService.create_knowledge_base_from_url(
                            current_user.elevenlabs_api_key,
                            url_item.get("title", "URL Document"),
                            url_item["url"]
                        )
                        knowledge_base_doc_ids.append(doc_id)
                        logger.info(f"✅ Created new indexed KB URL document: {doc_id}")
                    except Exception as e:
                        logger.error(f"Error creating URL document: {e}")
                elif url_item.get("document_id"):
                    # Добавляем уже существующий документ
                    if url_item["document_id"] not in knowledge_base_doc_ids:
                        knowledge_base_doc_ids.append(url_item["document_id"])
            
            # Обрабатываем файлы (если есть document_id)
            for file_item in knowledge_base.get("files", []):
                if file_item.get("document_id"):
                    if file_item["document_id"] not in knowledge_base_doc_ids:
                        knowledge_base_doc_ids.append(file_item["document_id"])
        
        # ✅ ИСПРАВЛЕНО: Правильно устанавливаем knowledge_base_documents
        if knowledge_base_doc_ids:
            agent_data["knowledge_base_documents"] = list(set(knowledge_base_doc_ids))  # Убираем дубликаты
            logger.info(f"Final KB documents: {agent_data['knowledge_base_documents']}")
        
        # ✅ ИСПРАВЛЕНО: Обработка Server Tools (только новые)
        server_tool_ids = []
        if agent_data.get("server_tools"):
            for tool in agent_data["server_tools"]:
                if tool.get("name") and tool.get("url"):
                    if tool.get("id"):
                        # Инструмент уже существует, обновляем его
                        try:
                            await ElevenLabsService.update_server_tool(
                                current_user.elevenlabs_api_key,
                                tool["id"],
                                tool
                            )
                            server_tool_ids.append(tool["id"])
                            logger.info(f"Updated server tool: {tool['id']}")
                        except Exception as e:
                            logger.error(f"Error updating server tool: {e}")
                    else:
                        # Создаем новый инструмент
                        try:
                            tool_id = await ElevenLabsService.create_server_tool(
                                current_user.elevenlabs_api_key,
                                tool
                            )
                            server_tool_ids.append(tool_id)
                            logger.info(f"Created new server tool: {tool_id}")
                        except Exception as e:
                            logger.error(f"Error creating server tool: {e}")
        
        # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Правильная трансформация данных для ElevenLabs API
        elevenlabs_data = transform_frontend_to_elevenlabs_format(agent_data)
        
        # ✅ ИСПРАВЛЕНО: Логируем данные перед отправкой (без sensitive info)
        safe_data = {k: v for k, v in elevenlabs_data.items() if k not in ['knowledge_base']}
        safe_data['knowledge_base_count'] = len(elevenlabs_data.get('knowledge_base', []))
        logger.info(f"Sending to ElevenLabs API: {json.dumps(safe_data, indent=2, default=str)}")
        
        # ✅ ИСПРАВЛЕНО: Обновляем агента
        result = await ElevenLabsService.update_agent(
            current_user.elevenlabs_api_key,
            agent_id,
            elevenlabs_data
        )
        
        # ✅ ИСПРАВЛЕНО: Получаем обновленные данные агента
        complete_agent = await ElevenLabsService.get_agent_with_details(
            current_user.elevenlabs_api_key,
            agent_id
        )
        
        frontend_result = transform_elevenlabs_to_frontend_format(complete_agent)
        
        logger.info(f"✅ Agent updated successfully: {agent_id}")
        return frontend_result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error updating agent: {str(e)}")
        # Добавляем больше информации для отладки
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update agent: {str(e)}"
        )


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete ElevenLabs agent"""
    try:
        logger.info(f"Deleting agent {agent_id} for user {current_user.id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        result = await ElevenLabsService.delete_agent(
            current_user.elevenlabs_api_key,
            agent_id
        )
        
        logger.info(f"✅ Agent {agent_id} deleted successfully")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting agent: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete agent"
        )


# ============= EMBED & TESTING ENDPOINTS =============

@router.get("/{agent_id}/embed", response_model=ElevenLabsEmbedResponse)
async def get_embed_code(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get embed code for ElevenLabs agent"""
    try:
        logger.info(f"Getting embed code for agent {agent_id}")
        
        result = await ElevenLabsService.get_embed_code(agent_id)
        
        return ElevenLabsEmbedResponse(
            embed_code=result["embed_code"],
            agent_id=result["agent_id"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting embed code: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate embed code"
        )


@router.get("/{agent_id}/signed-url")
async def get_signed_url(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get signed URL for WebSocket connection"""
    try:
        logger.info(f"Getting signed URL for agent {agent_id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        signed_url = await ElevenLabsService.get_signed_url(
            current_user.elevenlabs_api_key,
            agent_id
        )
        
        logger.info(f"✅ Successfully got signed URL for agent {agent_id}")
        
        return {
            "signed_url": signed_url,
            "agent_id": agent_id,
            "fallback_url": f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}",
            "message": "Signed URL generated successfully. Valid for 15 minutes."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting signed URL: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get signed URL: {str(e)}"
        )


@router.get("/{agent_id}/test-connection")
async def test_websocket_connection(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Test WebSocket connection to ElevenLabs agent"""
    try:
        logger.info(f"Testing connection for agent {agent_id}")
        
        if not current_user.elevenlabs_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ElevenLabs API key not found"
            )
        
        # Проверяем API ключ
        api_key_valid = await ElevenLabsService.validate_api_key(current_user.elevenlabs_api_key)
        
        if not api_key_valid:
            return {
                "success": False,
                "message": "Invalid ElevenLabs API key",
                "details": {
                    "agent_id": agent_id,
                    "api_key_valid": False
                }
            }
        
        # Проверяем агента
        try:
            await ElevenLabsService.get_agent_by_id(current_user.elevenlabs_api_key, agent_id)
            agent_exists = True
        except HTTPException:
            agent_exists = False
        
        if not agent_exists:
            return {
                "success": False,
                "message": "Agent not found in ElevenLabs",
                "details": {
                    "agent_id": agent_id,
                    "api_key_valid": True,
                    "agent_exists": False
                }
            }
        
        # Пытаемся получить signed URL
        try:
            signed_url = await ElevenLabsService.get_signed_url(
                current_user.elevenlabs_api_key,
                agent_id
            )
            
            return {
                "success": True,
                "message": "Connection test successful",
                "details": {
                    "agent_id": agent_id,
                    "api_key_valid": True,
                    "agent_exists": True,
                    "signed_url_obtained": True,
                    "signed_url": signed_url
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to get signed URL during test: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to get signed URL: {str(e)}",
                "details": {
                    "agent_id": agent_id,
                    "api_key_valid": True,
                    "agent_exists": True,
                    "signed_url_obtained": False,
                    "error": str(e)
                }
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during connection test: {str(e)}")
        return {
            "success": False,
            "message": f"Connection test failed: {str(e)}",
            "details": {
                "agent_id": agent_id,
                "error": str(e)
            }
        }
