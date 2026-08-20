"""
Validation utilities for WellcomeAI application.
"""

import re
from typing import Tuple, Optional, Dict
import uuid

from backend.core.logging import get_logger

logger = get_logger(__name__)

def validate_email(email: str) -> Tuple[bool, Optional[str]]:
    """
    Validate email format
    
    Args:
        email: Email to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not email:
        return False, "Email is required"
    
    # Simple regex for basic email validation
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    if not re.match(email_pattern, email):
        return False, "Invalid email format"
    
    # Check email length
    if len(email) > 320:  # RFC 3696
        return False, "Email is too long"
    
    return True, None

def validate_password(password: str) -> Tuple[bool, Optional[str]]:
    """
    Validate password strength
    
    Args:
        password: Password to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not password:
        return False, "Password is required"
    
    # Check length
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    
    # Additional strength checks (optional)
    has_uppercase = any(c.isupper() for c in password)
    has_lowercase = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)
    
    # Require at least 3 of the 4 criteria for a strong password
    criteria_met = sum([has_uppercase, has_lowercase, has_digit, has_special])
    
    if criteria_met < 3:
        return False, "Password must contain at least 3 of the following: uppercase letters, lowercase letters, numbers, and special characters"
    
    return True, None

def validate_api_key(api_key: str) -> Tuple[bool, Optional[str]]:
    """
    Validate OpenAI API key format
    
    Args:
        api_key: API key to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Разрешаем пустой API-ключ
    if not api_key:
        return True, None
    
    # OpenAI API keys start with 'sk-' and are followed by a long string
    if not api_key.startswith('sk-'):
        return False, "Invalid API key format. OpenAI API keys start with 'sk-'"
    
    # Check key length (typical OpenAI key length)
    if len(api_key) < 30:
        return False, "API key is too short"
    
    return True, None

def validate_uuid(uuid_str: str) -> Tuple[bool, Optional[str]]:
    """
    Validate UUID format
    
    Args:
        uuid_str: UUID string to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not uuid_str:
        return False, "UUID is required"
    
    try:
        uuid_obj = uuid.UUID(uuid_str)
        return True, None
    except ValueError:
        return False, "Invalid UUID format"

def validate_voice(voice: str, available_voices: list) -> Tuple[bool, Optional[str]]:
    """
    Validate voice selection
    
    Args:
        voice: Voice to validate
        available_voices: List of available voices
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not voice:
        return False, "Voice is required"
    
    if voice not in available_voices:
        return False, f"Invalid voice. Available voices: {', '.join(available_voices)}"
    
    return True, None

def validate_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Validate URL format
    
    Args:
        url: URL to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not url:
        return False, "URL is required"
    
    # Simple regex for URL validation
    url_pattern = r'^(https?:\/\/)?(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$'
    
    if not re.match(url_pattern, url):
        return False, "Invalid URL format"
    
    return True, None

def validate_file_type(filename: str, allowed_extensions: list) -> Tuple[bool, Optional[str]]:
    """
    Validate file type based on extension
    
    Args:
        filename: Filename to validate
        allowed_extensions: List of allowed file extensions
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not filename:
        return False, "Filename is required"
    
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    
    if not ext:
        return False, "File has no extension"
    
    if ext not in allowed_extensions:
        return False, f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}"
    
    return True, None

def validate_domain(domain: str) -> Tuple[bool, Optional[str]]:
    """
    Validate domain format
    
    Args:
        domain: Domain to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not domain:
        return False, "Domain is required"
    
    # Simple regex for domain validation
    domain_pattern = r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'
    
    if not re.match(domain_pattern, domain):
        return False, "Invalid domain format"
    
    return True, None
