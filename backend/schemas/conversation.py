"""
Conversation schemas for WellcomeAI application.
Defines schemas for conversation-related requests and responses.
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

class ConversationBase(BaseModel):
    """Base schema with common conversation attributes"""
    user_message: Optional[str] = Field(None, description="User message")
    assistant_message: Optional[str] = Field(None, description="Assistant message")
    duration_seconds: Optional[float] = Field(None, description="Conversation duration in seconds")
    client_info: Optional[Dict[str, Any]] = Field(None, description="Client information")

class ConversationCreate(ConversationBase):
    """Schema for creating a new conversation"""
    assistant_id: str = Field(..., description="Assistant ID")
    session_id: Optional[str] = Field(None, description="Session ID to group related messages")
    audio_duration: Optional[float] = Field(None, description="Duration of audio in seconds")

class ConversationUpdate(BaseModel):
    """Schema for updating conversation"""
    feedback_rating: Optional[int] = Field(None, ge=1, le=5, description="User feedback rating (1-5)")
    feedback_text: Optional[str] = Field(None, description="User feedback text")
    is_flagged: Optional[bool] = Field(None, description="Whether the conversation is flagged for review")

class ConversationResponse(ConversationBase):
    """Schema for conversation response"""
    id: str = Field(..., description="Conversation ID")
    assistant_id: str = Field(..., description="Assistant ID")
    created_at: datetime = Field(..., description="Conversation timestamp")
    tokens_used: Optional[int] = Field(0, description="Number of tokens used")
    feedback_rating: Optional[int] = Field(None, description="User feedback rating (1-5)")
    feedback_text: Optional[str] = Field(None, description="User feedback text")
    is_flagged: Optional[bool] = Field(False, description="Whether the conversation is flagged")
    
    class Config:
        orm_mode = True  # Allow conversion from ORM models

class ConversationListResponse(BaseModel):
    """Schema for list of conversations"""
    conversations: List[ConversationResponse]
    total: int
    page: int
    page_size: int

class ConversationStats(BaseModel):
    """Schema for conversation statistics"""
    total_conversations: int = Field(..., description="Total number of conversations")
    total_tokens: int = Field(..., description="Total number of tokens used")
    avg_duration: float = Field(..., description="Average conversation duration in seconds")
    conversations_today: int = Field(..., description="Number of conversations today")
    conversations_this_week: int = Field(..., description="Number of conversations this week")
    conversations_this_month: int = Field(..., description="Number of conversations this month")
