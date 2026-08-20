"""
File API endpoints for WellcomeAI application.
"""

"""
File API endpoints for WellcomeAI application.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Path, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import os

from backend.core.logging import get_logger  # Изменен импорт core
from backend.core.dependencies import get_current_user
from backend.db.session import get_db  # Уже корректный импорт
from backend.models.user import User  # Изменен импорт models
from backend.schemas.file import FileResponse, FileUploadResponse  # Изменен импорт schemas
from backend.services.file_service import FileService  # Изменен импорт services

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()

# Остальной код остается без изменений

@router.post("/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    assistant_id: Optional[str] = Query(None, description="Optional assistant ID to associate the file with"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload a file.
    
    Args:
        file: File to upload
        assistant_id: Optional assistant ID to associate with file
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        FileUploadResponse with the file information
    """
    try:
        return await FileService.upload_file(db, file, str(current_user.id), assistant_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in upload_file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file"
        )

@router.get("/", response_model=List[FileResponse])
async def get_files(
    assistant_id: Optional[str] = Query(None, description="Optional assistant ID to filter files"),
    skip: int = Query(0, ge=0, description="Number of files to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of files to return"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get files for the current user or a specific assistant.
    
    Args:
        assistant_id: Optional assistant ID to filter files
        skip: Number of files to skip
        limit: Maximum number of files to return
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        List of FileResponse objects
    """
    try:
        return await FileService.get_files(db, str(current_user.id), assistant_id, skip, limit)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_files: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve files"
        )

@router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: str = Path(..., description="The ID of the file to retrieve"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get file by ID.
    
    Args:
        file_id: File ID
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        FileResponse with the file information
    """
    try:
        file = await FileService.get_file_by_id(db, file_id, str(current_user.id))
        
        return FileResponse(
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
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve file"
        )

@router.get("/{file_id}/download")
async def download_file(
    file_id: str = Path(..., description="The ID of the file to download"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Download a file.
    
    Args:
        file_id: File ID
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        StreamingResponse with the file content
    """
    try:
        file_path, filename, content_type = await FileService.download_file(db, file_id, str(current_user.id))
        
        def iterfile():
            with open(file_path, mode="rb") as file_like:
                yield from file_like
        
        return StreamingResponse(
            iterfile(),
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in download_file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download file"
        )

@router.delete("/{file_id}", response_model=dict)
async def delete_file(
    file_id: str = Path(..., description="The ID of the file to delete"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a file.
    
    Args:
        file_id: File ID
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Confirmation message
    """
    try:
        await FileService.delete_file(db, file_id, str(current_user.id))
        return {"success": True, "message": "File deleted successfully", "id": file_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in delete_file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete file"
        )
