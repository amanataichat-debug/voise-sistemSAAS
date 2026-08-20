"""
Error handling utilities for WellcomeAI application.
"""

import traceback
import sys
from typing import Dict, Any, Optional, Type, List, Union
from fastapi import HTTPException, status

from backend.core.logging import get_logger

logger = get_logger(__name__)

def handle_exception(
    exception: Exception,
    log_message: str = "An error occurred",
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    detail: Optional[str] = None
) -> HTTPException:
    """
    Handle exception and generate appropriate HTTPException
    
    Args:
        exception: The exception that occurred
        log_message: Message to log
        status_code: HTTP status code to return
        detail: Detail message for the client
        
    Returns:
        HTTPException to raise
    """
    # Log the exception
    log_exception(exception, log_message)
    
    # If it's already an HTTPException, just return it
    if isinstance(exception, HTTPException):
        return exception
    
    # Create detail message
    if detail is None:
        detail = str(exception) or "An internal server error occurred"
    
    # Return HTTPException
    return HTTPException(
        status_code=status_code,
        detail=detail
    )

def log_exception(
    exception: Exception,
    message: str = "An error occurred"
) -> None:
    """
    Log exception with formatted traceback
    
    Args:
        exception: The exception to log
        message: Additional message
    """
    exc_type, exc_value, exc_traceback = sys.exc_info()
    
    # Format the exception
    formatted_exception = traceback.format_exception(exc_type, exc_value, exc_traceback)
    exception_string = "".join(formatted_exception)
    
    # Log with traceback
    logger.error(f"{message}: {str(exception)}\n{exception_string}")

def format_exception_for_client(
    exception: Exception,
    include_traceback: bool = False
) -> Dict[str, Any]:
    """
    Format exception for client response
    
    Args:
        exception: The exception to format
        include_traceback: Whether to include traceback (for development only)
        
    Returns:
        Formatted exception as dictionary
    """
    result = {
        "error": str(exception) or "An error occurred",
        "error_type": exception.__class__.__name__
    }
    
    # Include traceback in development mode
    if include_traceback:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        if exc_traceback:
            formatted_traceback = traceback.format_tb(exc_traceback)
            result["traceback"] = formatted_traceback
    
    return result

def get_exception_details(
    exception: Exception
) -> Dict[str, Any]:
    """
    Get detailed information about an exception
    
    Args:
        exception: The exception to analyze
        
    Returns:
        Dictionary with exception details
    """
    exc_type, exc_value, exc_traceback = sys.exc_info()
    
    # Get the most recent traceback frame
    tb_frames = traceback.extract_tb(exc_traceback)
    most_recent_frame = tb_frames[-1] if tb_frames else None
    
    # Create details
    details = {
        "type": exception.__class__.__name__,
        "message": str(exception),
        "module": exception.__class__.__module__
    }
    
    # Add frame information if available
    if most_recent_frame:
        details.update({
            "file": most_recent_frame.filename,
            "line": most_recent_frame.lineno,
            "function": most_recent_frame.name,
            "code": most_recent_frame.line
        })
    
    return details

def is_known_exception(
    exception: Exception,
    known_exceptions: List[Type[Exception]]
) -> bool:
    """
    Check if exception is of a known type
    
    Args:
        exception: The exception to check
        known_exceptions: List of known exception types
        
    Returns:
        True if exception is of a known type
    """
    return any(isinstance(exception, exc_type) for exc_type in known_exceptions)

def get_error_code(
    exception: Exception
) -> str:
    """
    Get standardized error code for an exception
    
    Args:
        exception: The exception
        
    Returns:
        Standardized error code
    """
    # Define mapping between exception types and error codes
    error_code_mapping = {
        ValueError: "invalid_value",
        TypeError: "invalid_type",
        KeyError: "missing_key",
        IndexError: "invalid_index",
        FileNotFoundError: "file_not_found",
        PermissionError: "permission_denied",
        TimeoutError: "timeout",
        ConnectionError: "connection_error",
        HTTPException: f"http_{getattr(exception, 'status_code', 500)}",
    }
    
    # Get the error code from mapping or default to class name
    for exc_type, code in error_code_mapping.items():
        if isinstance(exception, exc_type):
            if exc_type is HTTPException and hasattr(exception, "status_code"):
                return f"http_{exception.status_code}"
            return code
    
    # Default to exception class name in snake_case
    class_name = exception.__class__.__name__
    return "".join(["_" + c.lower() if c.isupper() else c for c in class_name]).lstrip("_")
