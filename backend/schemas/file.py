"""
File schemas for WellcomeAI application.
Defines schemas for file-related requests and responses.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime

class FileBase(BaseModel):
    """Base schema with common file attributes"""
    name: str = Field(..., description="File name")
    content_type: str = Field(..., description="File content type")
    size: int = Field(..., description="File size in bytes")

class FileCreate(FileBase):
    """Schema for creating a new file record"""
    assistant_id: Optional[str] = Field(None, description="Assistant ID")
    original_filename: str = Field(..., description="Original filename")
    file_path: str = Field(..., description="File path on server")

class FileUpdate(BaseModel):
    """Schema for updating file information"""
    name: Optional[str] = Field(None, description="File name")
    processed: Optional[bool] = Field(None, description="Whether the file has been processed")
    processing_error: Optional[str] = Field(None, description="Error message if processing failed")
    openai_file_id: Optional[str] = Field(None, description="OpenAI file ID")

class FileResponse(FileBase):
    """Schema for file response"""
    id: str = Field(..., description="File ID")
    user_id: str = Field(..., description="User ID")
    assistant_id: Optional[str] = Field(None, description="Assistant ID")
    original_filename: str = Field(..., description="Original filename")
    processed: bool = Field(..., description="Whether the file has been processed")
    processing_error: Optional[str] = Field(None, description="Error message if processing failed")
    created_at: datetime = Field(..., description="File creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="File update timestamp")
    
    # Derived properties
    extension: str = Field(..., description="File extension")
    is_text: bool = Field(..., description="Whether the file is text-based")
    is_document: bool = Field(..., description="Whether the file is a document")
    is_spreadsheet: bool = Field(..., description="Whether the file is a spreadsheet")
    
    class Config:
        orm_mode = True  # Allow conversion from ORM models

class FileUploadResponse(BaseModel):
    """Schema for file upload response"""
    file: FileResponse
    message: str = Field(..., description="Status message")
    processing_status: str = Field(..., description="Processing status")

class FilesListResponse(BaseModel):
    """Schema for list of files"""
    files: List[FileResponse]
    total: int
    page: int
    page_size: int
