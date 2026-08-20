"""
ElevenLabs schemas for WellcomeAI application.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class ElevenLabsApiKeyRequest(BaseModel):
    """Schema for ElevenLabs API key request"""
    api_key: str = Field(..., description="ElevenLabs API key")

class ElevenLabsVoiceResponse(BaseModel):
    """Schema for ElevenLabs voice response"""
    voice_id: str = Field(..., description="Voice ID")
    name: str = Field(..., description="Voice name")
    preview_url: Optional[str] = Field(None, description="Preview URL")
    category: Optional[str] = Field(None, description="Voice category")

class ElevenLabsAgentCreate(BaseModel):
    """Schema for creating ElevenLabs agent"""
    name: str = Field(..., description="Agent name")
    system_prompt: Optional[str] = Field(None, description="System prompt")
    voice_id: str = Field(..., description="Voice ID")
    voice_name: Optional[str] = Field(None, description="Voice name")

class ElevenLabsAgentUpdate(BaseModel):
    """Schema for updating ElevenLabs agent"""
    name: Optional[str] = Field(None, description="Agent name")
    system_prompt: Optional[str] = Field(None, description="System prompt")
    voice_id: Optional[str] = Field(None, description="Voice ID")
    voice_name: Optional[str] = Field(None, description="Voice name")
    is_active: Optional[bool] = Field(None, description="Whether agent is active")

class ElevenLabsAgentResponse(BaseModel):
    """Schema for ElevenLabs agent response"""
    id: str = Field(..., description="Agent ID")
    user_id: str = Field(..., description="User ID")
    elevenlabs_agent_id: Optional[str] = Field(None, description="ElevenLabs agent ID")
    name: str = Field(..., description="Agent name")
    system_prompt: Optional[str] = Field(None, description="System prompt")
    voice_id: str = Field(..., description="Voice ID")
    voice_name: Optional[str] = Field(None, description="Voice name")
    is_active: bool = Field(..., description="Whether agent is active")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Update timestamp")

    class Config:
        orm_mode = True

class ElevenLabsEmbedResponse(BaseModel):
    """Schema for ElevenLabs embed code response"""
    embed_code: str = Field(..., description="HTML embed code")
    agent_id: str = Field(..., description="Agent ID")
