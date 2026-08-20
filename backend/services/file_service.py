"""
File service for WellcomeAI application.
Handles file upload, processing, and management.
"""

import os
import uuid
import shutil
from fastapi import HTTPException, status, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, BinaryIO

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.models.file import File
from backend.schemas.file import FileCreate, FileResponse, FileUploadResponse

logger = get_logger(__name__)

class FileService:
    """Service for file operations"""
    
    # Set of allowed file extensions and content types
    ALLOWED_EXTENSIONS = {'pdf', 'docx', 'xlsx', 'txt', 'csv', 'json', 'md'}
    ALLOWED_CONTENT_TYPES = {
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'text/plain',
        'text/csv',
        'application/json',
        'text/markdown'
    }
    
    # Maximum file size (10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024
    
    @staticmethod
    def _ensure_upload_dir():
        """Ensure the upload directory exists"""
        upload_dir = os.path.join(settings.STATIC_DIR, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        return upload_dir
    
    @staticmethod
    def _generate_file_path(original_filename: str, user_id: str) -> str:
        """Generate a unique file path for a file"""
        _, ext = os.path.splitext(original_filename)
        filename = f"{uuid.uuid4()}{ext}"
        user_dir = str(user_id)
        os.makedirs(os.path.join(FileService._ensure_upload_dir(), user_dir), exist_ok=True)
        return os.path.join(user_dir, filename)
    
    @staticmethod
    def _validate_file(file: UploadFile) -> None:
        """
        Validate a file for upload
        
        Args:
            file: The file to validate
            
        Raises:
            HTTPException: If file is invalid
        """
        # Check file extension
        _, ext = os.path.splitext(file.filename)
        if ext.lower()[1:] not in FileService.ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File type not allowed. Allowed types: {', '.join(FileService.ALLOWED_EXTENSIONS)}"
            )
        
        # Check content type
        if file.content_type not in FileService.ALLOWED_CONTENT_TYPES:
            logger.warning(f"Suspicious content type: {file.content_type} for file {file.filename}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File content type not allowed: {file.content_type}"
            )
    
    @staticmethod
    async def upload_file(
        db: Session, 
        file: UploadFile, 
        user_id: str, 
        assistant_id: Optional[str] = None
    ) -> FileUploadResponse:
        """
        Upload and process a file
        
        Args:
            db: Database session
            file: File to upload
            user_id: User ID
            assistant_id: Optional Assistant ID
            
        Returns:
            FileUploadResponse with file info
            
        Raises:
            HTTPException: If upload fails
        """
        try:
            # Validate file
            FileService._validate_file(file)
            
            # Create upload directory if it doesn't exist
            upload_dir = FileService._ensure_upload_dir()
            
            # Generate file path
            file_path = FileService._generate_file_path(file.filename, user_id)
            full_path = os.path.join(upload_dir, file_path)
            
            # Get file size (approximately, since we can't rely on content_length)
            file.file.seek(0, 2)  # Seek to end
            file_size = file.file.tell()
            file.file.seek(0)  # Reset to beginning
            
            # Check file size
            if file_size > FileService.MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File size exceeds the limit of {FileService.MAX_FILE_SIZE // (1024 * 1024)}MB"
                )
            
            # Write file to disk
            with open(full_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # Create file record in database
            db_file = File(
                user_id=user_id,
                assistant_id=assistant_id,
                name=os.path.splitext(file.filename)[0],  # Use filename without extension as name
                original_filename=file.filename,
                file_path=file_path,
                content_type=file.content_type,
                size=file_size,
                processed=False
            )
            
            db.add(db_file)
            db.commit()
            db.refresh(db_file)
            
            logger.info(f"File uploaded: {db_file.id}, name: {file.filename}, size: {file_size}")
            
            # Create response
            file_response = FileResponse(
                id=str(db_file.id),
                user_id=str(db_file.user_id),
                assistant_id=str(db_file.assistant_id) if db_file.assistant_id else None,
                name=db_file.name,
                original_filename=db_file.original_filename,
                content_type=db_file.content_type,
                size=db_file.size,
                processed=db_file.processed,
                processing_error=db_file.processing_error,
                created_at=db_file.created_at,
                updated_at=db_file.updated_at,
                extension=db_file.extension,
                is_text=db_file.is_text,
                is_document=db_file.is_document,
                is_spreadsheet=db_file.is_spreadsheet
            )
            
            # TODO: Trigger background processing task
            
            return FileUploadResponse(
                file=file_response,
                message="File uploaded successfully",
                processing_status="pending"
            )
            
        except IntegrityError as e:
            db.rollback()
            logger.error(f"Database integrity error during file upload: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload failed due to database constraint"
            )
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Unexpected error during file upload: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Upload failed: {str(e)}"
            )
    
    @staticmethod
    async def get_files(
        db: Session, 
        user_id: str, 
        assistant_id: Optional[str] = None, 
        skip: int = 0, 
        limit: int = 100
    ) -> List[FileResponse]:
        """
        Get files for a user or assistant
        
        Args:
            db: Database session
            user_id: User ID
            assistant_id: Optional Assistant ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            List of FileResponse objects
        """
        query = db.query(File).filter(File.user_id == user_id)
        
        if assistant_id:
            query = query.filter(File.assistant_id == assistant_id)
        
        query = query.order_by(File.created_at.desc()).offset(skip).limit(limit)
        files = query.all()
        
        return [
            FileResponse(
                id=str(file.id),
                user_id=str(file.user_id),
                assistant_id=str(file.assistant_id) if file.assistant_id else None,
                name=file.name,
                original_filename=file.original_filename,
                content_type=file.content_type,
                size=file.size,
                processed=file.processed,
                processing_error=file.processing_error,
                created_at=file.created_at,
                updated_at=file.updated_at,
                extension=file.extension,
                is_text=file.is_text,
                is_document=file.is_document,
                is_spreadsheet=file.is_spreadsheet
            ) for file in files
        ]
    
    @staticmethod
    async def get_file_by_id(db: Session, file_id: str, user_id: Optional[str] = None) -> File:
        """
        Get file by ID
        
        Args:
            db: Database session
            file_id: File ID
            user_id: Optional User ID for authorization check
            
        Returns:
            File object
            
        Raises:
            HTTPException: If file not found or doesn't belong to user
        """
        file = db.query(File).filter(File.id == file_id).first()
        
        if not file:
            logger.warning(f"File not found: {file_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )
        
        # Check if file belongs to user if user_id is provided
        if user_id and str(file.user_id) != user_id:
            logger.warning(f"Unauthorized access to file {file_id} by user {user_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this file"
            )
        
        return file
    
    @staticmethod
    async def delete_file(db: Session, file_id: str, user_id: str) -> bool:
        """
        Delete a file
        
        Args:
            db: Database session
            file_id: File ID
            user_id: User ID
            
        Returns:
            True if deletion was successful
            
        Raises:
            HTTPException: If deletion fails
        """
        try:
            # Get file and verify ownership
            file = await FileService.get_file_by_id(db, file_id, user_id)
            
            # Delete file from disk
            full_path = os.path.join(FileService._ensure_upload_dir(), file.file_path)
            if os.path.exists(full_path):
                os.remove(full_path)
            
            # Delete file record from database
            db.delete(file)
            db.commit()
            
            logger.info(f"File deleted: {file_id}")
            return True
            
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error deleting file: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete file: {str(e)}"
            )
    
    @staticmethod
    async def download_file(db: Session, file_id: str, user_id: str) -> tuple:
        """
        Get file contents for download
        
        Args:
            db: Database session
            file_id: File ID
            user_id: User ID
            
        Returns:
            Tuple of (file_path, filename, content_type)
            
        Raises:
            HTTPException: If file not found
        """
        # Get file and verify ownership
        file = await FileService.get_file_by_id(db, file_id, user_id)
        
        # Check if file exists on disk
        full_path = os.path.join(FileService._ensure_upload_dir(), file.file_path)
        if not os.path.exists(full_path):
            logger.error(f"File not found on disk: {full_path}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found on disk"
            )
        
        logger.info(f"File download requested: {file_id}")
        return (full_path, file.original_filename, file.content_type)
