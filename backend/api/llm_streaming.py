"""
LLM Streaming API endpoint.
Provides real-time streaming responses from ChatGPT.
Client manages conversation history via localStorage.
"""

from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
import openai
import asyncio
import uuid

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.models.agent_config import AgentConfig
from backend.services.llm_streaming.streaming_client import ChatGPTStreamingClient

logger = get_logger(__name__)
router = APIRouter()


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class Message(BaseModel):
    """Single message in conversation"""
    role: str = Field(..., description="Message role: system/user/assistant")
    content: str = Field(..., description="Message content")


class LLMStreamRequest(BaseModel):
    """Request for LLM streaming"""
    messages: List[Message] = Field(..., description="Full conversation history")
    session_id: str = Field(..., description="Client session ID")
    model: Optional[str] = Field("gpt-4o-mini", description="Model to use")
    max_tokens: Optional[int] = Field(2000, description="Max tokens in response")
    temperature: Optional[float] = Field(0.7, description="Temperature (0-1)")


# ============================================================================
# STREAMING ENDPOINT
# ============================================================================

@router.post("/api/llm/stream")
async def stream_llm_response(request: LLMStreamRequest):
    """
    Stream ChatGPT response in real-time.
    
    Client sends full conversation history from localStorage.
    Server streams response back character by character.
    
    Args:
        request: LLMStreamRequest with messages and session_id
        
    Returns:
        StreamingResponse with text/plain chunks
        
    Example:
        POST /api/llm/stream
        {
            "messages": [
                {"role": "system", "content": "Ты ассистент"},
                {"role": "user", "content": "Привет!"}
            ],
            "session_id": "abc-123",
            "model": "gpt-4o-mini"
        }
    """
    
    session_id = request.session_id
    messages_count = len(request.messages)
    
    logger.info(f"[LLM-STREAM] 🚀 Starting stream for session {session_id}")
    logger.info(f"[LLM-STREAM] 📝 Messages: {messages_count}, Model: {request.model}")
    
    # Validate messages
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages cannot be empty")
    
    # Convert Pydantic models to dicts
    messages_dict = [msg.dict() for msg in request.messages]
    
    try:
        # Create streaming client
        client = ChatGPTStreamingClient(
            api_key=settings.OPENAI_API_KEY,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature
        )
        
        # Stream generator
        async def generate():
            """Generate streaming chunks"""
            chunk_count = 0
            total_chars = 0
            
            try:
                async for chunk in client.stream_response(messages_dict):
                    chunk_count += 1
                    total_chars += len(chunk)
                    
                    # Log first chunk (latency measurement)
                    if chunk_count == 1:
                        logger.info(f"[LLM-STREAM] ⚡ First chunk delivered (latency OK)")
                    
                    yield chunk
                
                logger.info(f"[LLM-STREAM] ✅ Stream complete: {chunk_count} chunks, {total_chars} chars")
                
            except Exception as e:
                logger.error(f"[LLM-STREAM] ❌ Stream error: {str(e)}")
                yield f"\n\n[ERROR: {str(e)}]"
        
        return StreamingResponse(
            generate(),
            media_type="text/plain",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no"  # Disable nginx buffering
            }
        )
        
    except Exception as e:
        logger.error(f"[LLM-STREAM] ❌ Error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stream response: {str(e)}"
        )


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================

@router.get("/api/llm/models")
async def get_available_models():
    """
    Get list of available ChatGPT models.
    
    Returns:
        List of model names with descriptions
    """
    return {
        "models": [
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4o Mini",
                "description": "Fast and efficient, great for most tasks",
                "recommended": True,
                "speed": "very_fast",
                "cost": "low"
            },
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "description": "Most capable model, best quality",
                "recommended": False,
                "speed": "fast",
                "cost": "medium"
            },
            {
                "id": "gpt-4-turbo",
                "name": "GPT-4 Turbo",
                "description": "Balanced performance and quality",
                "recommended": False,
                "speed": "medium",
                "cost": "high"
            }
        ],
        "default": "gpt-4o-mini"
    }


@router.get("/api/llm/status")
async def get_streaming_status():
    """
    Get streaming service status.
    
    Returns:
        Service health and configuration
    """
    return {
        "status": "operational",
        "service": "llm-streaming",
        "version": "1.0.0",
        "features": {
            "streaming": True,
            "client_history": True,
            "server_history": False,
            "storage": "localStorage (client-side)"
        },
        "performance": {
            "target_first_token": "< 2 seconds",
            "streaming_mode": "real-time",
            "buffering": "disabled"
        }
    }


# ============================================================================
# AGENT CONFIG - PYDANTIC MODELS
# ============================================================================

class AgentConfigCreate(BaseModel):
    name: str = "Мой агент"
    assistant_id: Optional[str] = None
    orchestrator_model: str = "gpt-4o"
    orchestrator_prompt: Optional[str] = None
    agent_model: str = "gpt-4o-mini"
    agent_functions: List[str] = []
    max_steps: int = Field(10, ge=1, le=20)
    step_timeout_sec: int = Field(60, ge=10, le=300)
    is_active: bool = False


class AgentConfigUpdate(AgentConfigCreate):
    pass


class AgentConfigResponse(BaseModel):
    id: str
    user_id: str
    name: str
    assistant_id: Optional[str] = None
    orchestrator_model: str
    orchestrator_prompt: Optional[str] = None
    agent_model: str
    agent_functions: List[str] = []
    max_steps: int
    step_timeout_sec: int
    is_active: bool
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


def _agent_config_to_response(cfg: AgentConfig) -> dict:
    """Convert AgentConfig ORM object to response dict."""
    return {
        "id": str(cfg.id),
        "user_id": str(cfg.user_id),
        "name": cfg.name,
        "assistant_id": str(cfg.gemini_assistant_id) if cfg.gemini_assistant_id else None,
        "orchestrator_model": cfg.orchestrator_model,
        "orchestrator_prompt": cfg.orchestrator_prompt,
        "agent_model": cfg.agent_model,
        "agent_functions": cfg.agent_functions or [],
        "max_steps": cfg.max_steps,
        "step_timeout_sec": cfg.step_timeout_sec,
        "is_active": cfg.is_active,
        "created_at": cfg.created_at.isoformat() if cfg.created_at else "",
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else "",
    }


# ============================================================================
# AGENT CONFIG - CRUD ENDPOINTS
# ============================================================================

@router.post("/api/llm/agent-config", status_code=201)
async def create_agent_config(
    payload: AgentConfigCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new AgentConfig for the current user."""
    cfg = AgentConfig(
        user_id=current_user.id,
        name=payload.name,
        gemini_assistant_id=uuid.UUID(payload.assistant_id) if payload.assistant_id else None,
        orchestrator_model=payload.orchestrator_model,
        orchestrator_prompt=payload.orchestrator_prompt,
        agent_model=payload.agent_model,
        agent_functions=payload.agent_functions,
        max_steps=payload.max_steps,
        step_timeout_sec=payload.step_timeout_sec,
        is_active=payload.is_active,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return _agent_config_to_response(cfg)


@router.get("/api/llm/agent-config")
async def list_agent_configs(
    assistant_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List AgentConfigs for the current user, optionally filtered by assistant_id."""
    query = db.query(AgentConfig).filter(AgentConfig.user_id == current_user.id)
    if assistant_id:
        try:
            aid = uuid.UUID(assistant_id)
            query = query.filter(AgentConfig.gemini_assistant_id == aid)
        except ValueError:
            pass
    configs = query.order_by(AgentConfig.created_at.desc()).all()
    return [_agent_config_to_response(c) for c in configs]


@router.get("/api/llm/agent-config/{config_id}")
async def get_agent_config(
    config_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a single AgentConfig by ID."""
    try:
        cid = uuid.UUID(config_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent config not found")

    cfg = db.query(AgentConfig).filter(AgentConfig.id == cid).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Agent config not found")
    if cfg.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return _agent_config_to_response(cfg)


@router.put("/api/llm/agent-config/{config_id}")
async def update_agent_config(
    config_id: str,
    payload: AgentConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update an AgentConfig."""
    try:
        cid = uuid.UUID(config_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent config not found")

    cfg = db.query(AgentConfig).filter(AgentConfig.id == cid).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Agent config not found")
    if cfg.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    cfg.name = payload.name
    cfg.gemini_assistant_id = uuid.UUID(payload.assistant_id) if payload.assistant_id else None
    cfg.orchestrator_model = payload.orchestrator_model
    cfg.orchestrator_prompt = payload.orchestrator_prompt
    cfg.agent_model = payload.agent_model
    cfg.agent_functions = payload.agent_functions
    cfg.max_steps = payload.max_steps
    cfg.step_timeout_sec = payload.step_timeout_sec
    cfg.is_active = payload.is_active
    db.commit()
    db.refresh(cfg)
    return _agent_config_to_response(cfg)


@router.delete("/api/llm/agent-config/{config_id}")
async def delete_agent_config(
    config_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an AgentConfig."""
    try:
        cid = uuid.UUID(config_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent config not found")

    cfg = db.query(AgentConfig).filter(AgentConfig.id == cid).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Agent config not found")
    if cfg.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    db.delete(cfg)
    db.commit()
    return {"success": True, "message": "Agent config deleted"}
