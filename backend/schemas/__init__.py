"""
Pydantic schema models for WellcomeAI application.
These schemas are used for request/response validation and documentation.
"""

from .auth import Token, TokenData, LoginRequest, RegisterRequest
from .user import UserBase, UserCreate, UserUpdate, UserResponse
from .assistant import (
    AssistantBase, AssistantCreate, AssistantUpdate, 
    AssistantResponse, EmbedCodeResponse
)
from .conversation import (
    ConversationBase, ConversationCreate, 
    ConversationResponse, ConversationStats
)
from .file import FileBase, FileCreate, FileResponse, FileUploadResponse

# Export all schemas
__all__ = [
    # Auth schemas
    "Token", "TokenData", "LoginRequest", "RegisterRequest",
    
    # User schemas
    "UserBase", "UserCreate", "UserUpdate", "UserResponse",
    
    # Assistant schemas
    "AssistantBase", "AssistantCreate", "AssistantUpdate", 
    "AssistantResponse", "EmbedCodeResponse",
    
    # Conversation schemas
    "ConversationBase", "ConversationCreate", 
    "ConversationResponse", "ConversationStats",
    
    # File schemas
    "FileBase", "FileCreate", "FileResponse", "FileUploadResponse"
]
