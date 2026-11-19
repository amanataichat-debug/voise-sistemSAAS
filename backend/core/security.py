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
import hmac
import secrets
import uuid
from contextvars import ContextVar

from .config import settings
from .logging import get_logger

# Initialize logger
logger = get_logger(__name__)

# Set up security scheme
security = HTTPBearer()

# Вход админа в кабинет клиента (ветка 1811-niche-admin-speed): в токене клиента лежит
# claim «imp» = id админа. get_current_user_id кладёт его сюда на время запроса,
# get_current_user — в user._impersonator_id. Значение ставится в каждом запросе
# (None для обычного входа), поэтому между запросами не протекает.
IMPERSONATOR: ContextVar[Optional[str]] = ContextVar("voksy_impersonator", default=None)
IMPERSONATION_MINUTES = 120

# Сотрудник кабинета (models/team.py): токен с sub = id владельца, staff = id сотрудника,
# stv = версия токена сотрудника. Как и IMPERSONATOR, ставится в каждом запросе (None —
# владелец), get_current_user кладёт id сотрудника в user._staff_member_id.
STAFF: ContextVar[Optional[Dict[str, Any]]] = ContextVar("voksy_staff", default=None)


def create_staff_token(owner_id, member_id, token_version: int,
                       minutes: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    """Токен сотрудника: данные владельца, но права — только разделы сотрудника."""
    return create_jwt_token(owner_id, expires_delta_minutes=minutes,
                            extra={"staff": str(member_id), "stv": int(token_version or 1)})


def staff_claims(token_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """{"id", "v"} сотрудника из расшифрованного токена или None для владельца."""
    sid = token_data.get("staff")
    if not sid:
        return None
    try:
        return {"id": str(sid), "v": int(token_data.get("stv") or 0)}
    except (TypeError, ValueError):
        return {"id": str(sid), "v": 0}


def create_impersonation_token(target_user_id, admin_user_id, minutes: int = IMPERSONATION_MINUTES) -> str:
    """Короткий токен клиента с отметкой, какой админ вошёл (для баннера и запретов)."""
    return create_jwt_token(target_user_id, expires_delta_minutes=minutes, extra={"imp": str(admin_user_id)})

def create_jwt_token(
    user_id: Union[str, uuid.UUID], 
    expires_delta_minutes: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    extra: Optional[Dict[str, Any]] = None,
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
        if extra:
            to_encode.update(extra)
        
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
        # imp — id админа, который вошёл в кабинет клиента из админки (impersonation);
        # staff/stv — сотрудник кабинета (вход по своей почте, права по разделам)
        return {"sub": user_id, "exp": exp, "imp": payload.get("imp"),
                "staff": payload.get("staff"), "stv": payload.get("stv")}
        
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

# Пароли (06.11.2026): PBKDF2-HMAC-SHA256 с солью вместо несолёного SHA-256 — при утечке базы
# старые хэши подбирались по готовым таблицам за минуты. Формат: pbkdf2_sha256$<итерации>$<соль>$<хэш>.
# Старые 64-символьные SHA-256 хэши по-прежнему принимаются; при входе хэш тихо обновляется
# (needs_rehash → AuthService.login_user).
PBKDF2_ITERATIONS = 310_000
_PBKDF2_PREFIX = "pbkdf2_sha256"


def _pbkdf2(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()


def hash_password(password: str) -> str:
    """Хэш пароля: PBKDF2-HMAC-SHA256, соль 16 байт."""
    salt = secrets.token_hex(16)
    return f"{_PBKDF2_PREFIX}${PBKDF2_ITERATIONS}${salt}${_pbkdf2(password, salt, PBKDF2_ITERATIONS)}"


def needs_rehash(hashed_password: Optional[str]) -> bool:
    """True для старого формата (SHA-256 без соли) или меньшего числа итераций."""
    if not hashed_password or not hashed_password.startswith(_PBKDF2_PREFIX + "$"):
        return True
    try:
        return int(hashed_password.split("$")[1]) < PBKDF2_ITERATIONS
    except (IndexError, ValueError):
        return True


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверка пароля (constant-time) для нового и старого формата."""
    if not hashed_password:
        return False
    if hashed_password.startswith(_PBKDF2_PREFIX + "$"):
        try:
            _, iterations, salt, digest = hashed_password.split("$", 3)
            return hmac.compare_digest(_pbkdf2(plain_password, salt, int(iterations)), digest)
        except (ValueError, TypeError):
            return False
    legacy = hashlib.sha256(plain_password.encode()).hexdigest()
    return hmac.compare_digest(legacy, hashed_password)

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
        IMPERSONATOR.set(token_data.get("imp"))
        STAFF.set(staff_claims(token_data))
        
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
