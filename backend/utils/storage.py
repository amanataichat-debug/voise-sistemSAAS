"""
File storage utilities for WellcomeAI application.
"""

import os
import uuid
import shutil
from typing import Optional, List, Tuple
import mimetypes
from pathlib import Path

from backend.core.logging import get_logger
from backend.core.config import settings

logger = get_logger(__name__)

# Define allowed file extensions and their MIME types
ALLOWED_EXTENSIONS = {
    # Documents
    'pdf': 'application/pdf',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'txt': 'text/plain',
    'rtf': 'application/rtf',
    'md': 'text/markdown',
    
    # Spreadsheets
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'csv': 'text/csv',
    
    # Data
    'json': 'application/json',
    'xml': 'application/xml',
    'yaml': 'application/yaml',
    'yml': 'application/yaml',
    
    # Archives (if needed)
    'zip': 'application/zip',
}

def ensure_directory_exists(directory_path: str) -> str:
    """
    Ensure a directory exists, creating it if necessary
    
    Args:
        directory_path: Directory path
        
    Returns:
        The directory path
    """
    os.makedirs(directory_path, exist_ok=True)
    return directory_path

def get_upload_dir() -> str:
    """
    Get the upload directory path, creating it if it doesn't exist
    
    Returns:
        Upload directory path
    """
    upload_dir = os.path.join(settings.STATIC_DIR, 'uploads')
    return ensure_directory_exists(upload_dir)

def get_user_upload_dir(user_id: str) -> str:
    """
    Get a user's upload directory, creating it if it doesn't exist
    
    Args:
        user_id: User ID
        
    Returns:
        User upload directory path
    """
    user_dir = os.path.join(get_upload_dir(), str(user_id))
    return ensure_directory_exists(user_dir)

def get_file_path(
    original_filename: str,
    user_id: str,
    assistant_id: Optional[str] = None,
    unique: bool = True
) -> str:
    """
    Generate a file path for a new file
    
    Args:
        original_filename: Original filename
        user_id: User ID
        assistant_id: Optional assistant ID
        unique: Whether to generate a unique filename
        
    Returns:
        File path relative to upload directory
    """
    # Get user directory path
    user_dir = str(user_id)
    
    # Get file extension
    _, ext = os.path.splitext(original_filename)
    
    # Generate unique filename if requested
    if unique:
        filename = f"{uuid.uuid4()}{ext}"
    else:
        # Sanitize original filename by removing special characters
        sanitized_name = "".join(c for c in os.path.splitext(original_filename)[0] if c.isalnum() or c in '_-.')
        filename = f"{sanitized_name}{ext}"
    
    # Create user directory if it doesn't exist
    ensure_directory_exists(os.path.join(get_upload_dir(), user_dir))
    
    # If assistant ID is provided, create subdirectory
    if assistant_id:
        assistant_dir = os.path.join(user_dir, str(assistant_id))
        ensure_directory_exists(os.path.join(get_upload_dir(), assistant_dir))
        return os.path.join(assistant_dir, filename)
    
    return os.path.join(user_dir, filename)

def copy_file(source_path: str, dest_path: str) -> bool:
    """
    Copy a file
    
    Args:
        source_path: Source file path
        dest_path: Destination file path
        
    Returns:
        True if successful
    """
    try:
        # Ensure destination directory exists
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        # Copy file
        shutil.copy2(source_path, dest_path)
        return True
    except Exception as e:
        logger.error(f"Error copying file: {str(e)}")
        return False

def move_file(source_path: str, dest_path: str) -> bool:
    """
    Move a file
    
    Args:
        source_path: Source file path
        dest_path: Destination file path
        
    Returns:
        True if successful
    """
    try:
        # Ensure destination directory exists
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        # Move file
        shutil.move(source_path, dest_path)
        return True
    except Exception as e:
        logger.error(f"Error moving file: {str(e)}")
        return False

def delete_file(file_path: str) -> bool:
    """
    Delete a file
    
    Args:
        file_path: File path
        
    Returns:
        True if successful
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False
    except Exception as e:
        logger.error(f"Error deleting file: {str(e)}")
        return False

def get_file_extension(filename: str) -> str:
    """
    Get file extension without the dot
    
    Args:
        filename: Filename
        
    Returns:
        File extension
    """
    return os.path.splitext(filename)[1].lower()[1:]

def get_mime_type(filename: str) -> str:
    """
    Get MIME type for a file
    
    Args:
        filename: Filename
        
    Returns:
        MIME type
    """
    # Get file extension
    ext = get_file_extension(filename)
    
    # Check if extension is in allowed extensions
    if ext in ALLOWED_EXTENSIONS:
        return ALLOWED_EXTENSIONS[ext]
    
    # Use mimetypes library as fallback
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"

def is_allowed_file(filename: str) -> bool:
    """
    Check if file is allowed based on extension
    
    Args:
        filename: Filename
        
    Returns:
        True if file is allowed
    """
    return get_file_extension(filename) in ALLOWED_EXTENSIONS

def get_file_size(file_path: str) -> int:
    """
    Get file size in bytes
    
    Args:
        file_path: File path
        
    Returns:
        File size in bytes
    """
    return os.path.getsize(file_path)

def list_files(
    directory: str,
    extensions: Optional[List[str]] = None,
    recursive: bool = False
) -> List[str]:
    """
    List files in a directory
    
    Args:
        directory: Directory path
        extensions: Optional list of extensions to filter by
        recursive: Whether to search recursively
        
    Returns:
        List of file paths
    """
    files = []
    
    # Helper function for recursive search
    def process_directory(dir_path):
        try:
            for entry in os.scandir(dir_path):
                if entry.is_file():
                    if extensions is None or get_file_extension(entry.name) in extensions:
                        files.append(entry.path)
                elif entry.is_dir() and recursive:
                    process_directory(entry.path)
        except Exception as e:
            logger.error(f"Error listing files in {dir_path}: {str(e)}")
    
    # Start processing
    process_directory(directory)
    return files
