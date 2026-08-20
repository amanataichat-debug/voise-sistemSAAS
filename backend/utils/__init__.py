"""
Utility module for WellcomeAI application.
Contains helper functions and utilities used across the application.
"""

from .audio_utils import (
    audio_buffer_to_base64, 
    base64_to_audio_buffer,
    create_wav_from_pcm
)
from .error_handling import (
    handle_exception, 
    log_exception, 
    format_exception_for_client
)
from .storage import (
    ensure_directory_exists,
    get_file_path,
    get_file_extension,
    get_mime_type,
    is_allowed_file
)
from .validators import (
    validate_email,
    validate_password,
    validate_api_key
)
from .helpers import (
    generate_unique_id,
    format_datetime,
    truncate_string,
    parse_client_info
)

# Export all utility functions
__all__ = [
    # Audio utilities
    "audio_buffer_to_base64", 
    "base64_to_audio_buffer",
    "create_wav_from_pcm",
    
    # Error handling
    "handle_exception", 
    "log_exception", 
    "format_exception_for_client",
    
    # File storage
    "ensure_directory_exists",
    "get_file_path",
    "get_file_extension",
    "get_mime_type",
    "is_allowed_file",
    
    # Validators
    "validate_email",
    "validate_password",
    "validate_api_key",
    
    # Helper functions
    "generate_unique_id",
    "format_datetime",
    "truncate_string",
    "parse_client_info"
]
