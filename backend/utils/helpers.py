"""
Helper functions for WellcomeAI application.
"""

import uuid
import json
import time
import re
from datetime import datetime
from typing import Dict, Any, Optional, List, Union

from backend.core.logging import get_logger

logger = get_logger(__name__)

def generate_unique_id(prefix: Optional[str] = None) -> str:
    """
    Generate a unique ID
    
    Args:
        prefix: Optional prefix for the ID
        
    Returns:
        Unique ID
    """
    unique_id = str(uuid.uuid4())
    
    if prefix:
        return f"{prefix}_{unique_id}"
    
    return unique_id

def format_datetime(
    dt: Optional[datetime] = None,
    format_str: str = "%Y-%m-%d %H:%M:%S"
) -> str:
    """
    Format datetime
    
    Args:
        dt: Datetime to format (default: current time)
        format_str: Format string
        
    Returns:
        Formatted datetime string
    """
    if dt is None:
        dt = datetime.now()
        
    return dt.strftime(format_str)

def parse_datetime(
    datetime_str: str,
    format_str: str = "%Y-%m-%d %H:%M:%S"
) -> Optional[datetime]:
    """
    Parse datetime from string
    
    Args:
        datetime_str: Datetime string
        format_str: Format string
        
    Returns:
        Parsed datetime or None if parsing failed
    """
    try:
        return datetime.strptime(datetime_str, format_str)
    except ValueError:
        return None

def truncate_string(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    Truncate a string to a maximum length
    
    Args:
        text: String to truncate
        max_length: Maximum length
        suffix: Suffix to add if truncated
        
    Returns:
        Truncated string
    """
    if len(text) <= max_length:
        return text
        
    return text[:max_length - len(suffix)] + suffix

def parse_client_info(
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
    query_params: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Parse client information
    
    Args:
        user_agent: User-Agent header
        ip_address: IP address
        query_params: Query parameters
        
    Returns:
        Client information dictionary
    """
    client_info = {
        "timestamp": format_datetime(),
        "ip_address": ip_address
    }
    
    # Parse User-Agent
    if user_agent:
        client_info["user_agent"] = user_agent
        
        # Try to extract browser and OS information
        browser_match = re.search(r'(Chrome|Firefox|Safari|Edge|MSIE|Trident)[/\s]([0-9.]+)', user_agent)
        if browser_match:
            client_info["browser"] = browser_match.group(1)
            client_info["browser_version"] = browser_match.group(2)
            
        os_match = re.search(r'(Windows|Mac|Android|iOS|Linux)[/\s]?([0-9.X]+)?', user_agent)
        if os_match:
            client_info["os"] = os_match.group(1)
            if os_match.group(2):
                client_info["os_version"] = os_match.group(2)
                
        mobile_match = re.search(r'Mobile|Android|iPhone|iPad', user_agent)
        client_info["is_mobile"] = bool(mobile_match)
    
    # Add query parameters
    if query_params:
        client_info["query_params"] = query_params
        
        # Extract referrer if present
        if "referrer" in query_params:
            client_info["referrer"] = query_params["referrer"]
            
        # Extract UTM parameters
        utm_params = {}
        for key, value in query_params.items():
            if key.startswith("utm_"):
                utm_params[key] = value
                
        if utm_params:
            client_info["utm"] = utm_params
    
    return client_info

def safe_json_loads(json_str: str, default: Any = None) -> Any:
    """
    Safely parse JSON string
    
    Args:
        json_str: JSON string
        default: Default value if parsing fails
        
    Returns:
        Parsed JSON or default value
    """
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return default

def chunks(lst: List[Any], n: int) -> List[List[Any]]:
    """
    Split a list into chunks of size n
    
    Args:
        lst: List to split
        n: Chunk size
        
    Returns:
        List of chunks
    """
    return [lst[i:i + n] for i in range(0, len(lst), n)]

def retry(
    func,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    exceptions: tuple = (Exception,)
):
    """
    Retry decorator
    
    Args:
        func: Function to retry
        max_attempts: Maximum number of attempts
        base_delay: Base delay between attempts
        max_delay: Maximum delay between attempts
        exceptions: Exceptions to catch
        
    Returns:
        Wrapped function
    """
    def wrapper(*args, **kwargs):
        attempts = 0
        last_exception = None
        
        while attempts < max_attempts:
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                attempts += 1
                last_exception = e
                
                if attempts == max_attempts:
                    break
                    
                # Calculate delay with exponential backoff
                delay = min(base_delay * (2 ** (attempts - 1)), max_delay)
                
                # Log retry
                logger.warning(f"Retrying {func.__name__} ({attempts}/{max_attempts}) after {delay}s due to {str(e)}")
                
                # Wait before retrying
                time.sleep(delay)
        
        # If we get here, all attempts failed
        logger.error(f"All {max_attempts} attempts failed for {func.__name__}: {str(last_exception)}")
        raise last_exception
        
    return wrapper
