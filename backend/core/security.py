# backend/core/security.py - ПОЛНЫЙ ФАЙЛ С ЛОГИРОВАНИЕМ

"""
Security utilities for WellcomeAI application.
Handles JWT token creation/validation, password hashing, and user authentication.
✅ FIXED: Added detailed logging for debugging
"""

import jwt
from datetime import datetime, timedelta
from typing import Dict, Optional, Union, Any
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import hashlib
import uuid

from .config import settings
from .logging import get_logger

# Initialize logger
logger = get_logger(__name__)

# Set up security scheme
security = HTTPBearer()

def create_jwt_token(
    user_id: Union[str, uuid.UUID], 
    expires_delta_minutes: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES
) -> str:
    """
    Create a JWT token for the given user ID
    
    Args:
        user_id: The user ID to encode in the token
        expires_delta_minutes: Token expiration time in minutes
        
    Returns:
        The encoded JWT token
    """
    try:
        expire = datetime.utcnow() + timedelta(minutes=expires_delta_minutes)
        to_encode = {
            "sub": str(user_id),
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access_token"
        }
        
        encoded_jwt = jwt.encode(
            to_encode, 
            settings.JWT_SECRET_KEY, 
            algorithm=settings.JWT_ALGORITHM
        )
        
        logger.info(f"✅ JWT token created for user: {str(user_id)[:8]}...")
        return encoded_jwt
    except Exception as e:
        logger.error(f"❌ Error creating JWT token: {str(e)}")
        raise

def decode_jwt_token(token: str) -> Dict[str, Any]:
    """
    Decode and validate a JWT token
    ✅ FIXED: Added detailed logging
    
    Args:
        token: The JWT token to decode
        
    Returns:
        The decoded token data
        
    Raises:
        HTTPException: If the token is invalid or expired
    """
    try:
        logger.debug(f"🔍 Decoding JWT token: {token[:20]}...")
        
        payload = jwt.decode(
            token, 
            settings.JWT_SECRET_KEY, 
            algorithms=[settings.JWT_ALGORITHM]
        )
        
        user_id = payload.get("sub")
        exp = payload.get("exp")
        
        if user_id is None:
            logger.warning("❌ Invalid token: missing user ID")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user ID"
            )
        
        logger.debug(f"✅ Token decoded successfully for user: {user_id[:8]}...")
        return {"sub": user_id, "exp": exp}
        
    except jwt.ExpiredSignatureError:
        logger.warning("❌ Token expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired"
        )
    except jwt.InvalidTokenError as e:
        logger.warning(f"❌ Invalid token format: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
    except Exception as e:
        logger.error(f"❌ Unexpected error decoding token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to decode token"
        )

def hash_password(password: str) -> str:
    """
    Create a hash of a password using SHA-256
    
    Args:
        password: Plain text password to hash
        
    Returns:
        Hashed password
    """
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash
    
    Args:
        plain_password: Plain text password to verify
        hashed_password: Hashed password to compare against
        
    Returns:
        True if password matches hash, False otherwise
    """
    return hash_password(plain_password) == hashed_password

async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """
    FastAPI dependency to get current user ID from token
    ✅ FIXED: Added detailed logging and error handling
    
    Args:
        credentials: HTTP Authorization credentials
        
    Returns:
        User ID from the token
        
    Raises:
        HTTPException: If token is invalid
    """
    try:
        logger.debug(f"🔐 Extracting user ID from token...")
        
        if not credentials or not credentials.credentials:
            logger.warning("❌ No credentials provided")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No authentication credentials provided"
            )
        
        token = credentials.credentials
        logger.debug(f"📝 Token received: {token[:30]}...")
        
        token_data = decode_jwt_token(token)
        user_id = token_data["sub"]
        
        logger.debug(f"✅ User ID extracted: {user_id[:8]}...")
        return user_id
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Unexpected error in get_current_user_id: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )
