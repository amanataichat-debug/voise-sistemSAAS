"""
File model for WellcomeAI application.
Represents files uploaded and associated with assistants for knowledge base.
"""

import uuid
import os
from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base, BaseModel
from backend.core.config import settings

class File(Base, BaseModel):
    """
    File model representing uploaded files for assistant knowledge base.
    """
    __tablename__ = "files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assistant_id = Column(UUID(as_uuid=True), ForeignKey("assistant_configs.id", ondelete="CASCADE"), nullable=True)
    
    # File metadata
    name = Column(String, nullable=False)
    original_filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)  # Storage path
    content_type = Column(String, nullable=False)
    size = Column(Integer, nullable=False)  # Size in bytes
    
    # Processing status
    processed = Column(Boolean, default=False)
    processing_error = Column(Text, nullable=True)
    openai_file_id = Column(String, nullable=True)  # ID in OpenAI system
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="files")
    assistant = relationship("AssistantConfig", back_populates="files")

    def __repr__(self):
        """String representation of File"""
        return f"<File {self.name} (id={self.id})>"
    
    def to_dict(self):
        """Convert to dictionary with string ID"""
        data = super().to_dict()
        # Convert UUID to string for JSON serialization
        if isinstance(data.get("id"), uuid.UUID):
            data["id"] = str(data["id"])
        if isinstance(data.get("user_id"), uuid.UUID):
            data["user_id"] = str(data["user_id"])
        if isinstance(data.get("assistant_id"), uuid.UUID):
            data["assistant_id"] = str(data["assistant_id"])
            
        # Remove full file path for security
        data.pop("file_path", None)
            
        return data
    
    @property
    def full_path(self):
        """Get full path to file on disk"""
        return os.path.join(settings.UPLOAD_DIR, self.file_path)
    
    @property
    def extension(self):
        """Get file extension"""
        _, ext = os.path.splitext(self.original_filename)
        return ext.lower()[1:] if ext else ""
    
    @property
    def is_text(self):
        """Check if file is text-based"""
        text_types = ["text/plain", "text/csv", "application/json"]
        return self.content_type in text_types or self.extension in ["txt", "csv", "json"]
    
    @property
    def is_document(self):
        """Check if file is a document"""
        doc_types = ["application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]
        return self.content_type in doc_types or self.extension in ["pdf", "doc", "docx"]
    
    @property
    def is_spreadsheet(self):
        """Check if file is a spreadsheet"""
        sheet_types = ["application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]
        return self.content_type in sheet_types or self.extension in ["xls", "xlsx"]
