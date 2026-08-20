# backend/api/email_verification.py
"""
Email verification API endpoints for WellcomeAI application.
Handles sending, resending, and verifying email verification codes.
✅ FIXED: Correct JWT token generation using user UUID
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Dict, Any

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.services.email_service import EmailService

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()

# Security scheme
security = HTTPBearer()


# ========================================
# REQUEST/RESPONSE SCHEMAS
# ========================================

class SendVerificationRequest(BaseModel):
    """Request schema for sending verification code"""
    email: EmailStr
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com"
            }
        }


class VerifyCodeRequest(BaseModel):
    """Request schema for verifying code"""
    email: EmailStr
    code: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "code": "123456"
            }
        }


class VerificationResponse(BaseModel):
    """Response schema for verification operations"""
    success: bool
    message: str
    data: Dict[str, Any] = {}


# ========================================
# HELPER FUNCTIONS
# ========================================

async def get_current_user_from_email(email: str, db: Session):
    """
    Get user by email address.
    
    Args:
        email: User email
        db: Database session
    
    Returns:
        User object
    
    Raises:
        HTTPException: If user not found
    """
    from backend.models.user import User
    
    user = db.query(User).filter(User.email == email).first()
    if not user:
        logger.warning(f"User not found for email: {email}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user


# ========================================
# API ENDPOINTS
# ========================================

@router.post("/send", response_model=VerificationResponse, status_code=status.HTTP_200_OK)
async def send_verification_code(
    request: SendVerificationRequest,
    db: Session = Depends(get_db)
):
    """
    Send verification code to user's email.
    
    This endpoint can be used for:
    - Initial code sending after registration
    - Resending code if user didn't receive it
    
    Args:
        request: Email address to send code to
        db: Database session
    
    Returns:
        Success message with expiry information
    
    Raises:
        HTTPException: If user not found or sending fails
    """
    try:
        logger.info(f"📧 Verification code send request for: {request.email}")
        
        # Get user
        user = await get_current_user_from_email(request.email, db)
        
        # Check if already verified
        if user.email_verified:
            logger.info(f"User {request.email} already verified")
            return VerificationResponse(
                success=True,
                message="Email already verified",
                data={"already_verified": True}
            )
        
        # Send verification code
        result = await EmailService.send_verification_code(
            db=db,
            user_id=str(user.id),
            user_email=request.email
        )
        
        logger.info(f"✅ Verification code sent to {request.email}")
        
        return VerificationResponse(
            success=True,
            message="Verification code sent successfully",
            data={
                "expires_in_minutes": result.get("expires_in_minutes", 10),
                "max_attempts": result.get("max_attempts", 3)
            }
        )
        
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"Error sending verification code: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send verification code"
        )


@router.post("/resend", response_model=VerificationResponse, status_code=status.HTTP_200_OK)
async def resend_verification_code(
    request: SendVerificationRequest,
    db: Session = Depends(get_db)
):
    """
    Resend verification code with cooldown check.
    
    User must wait 60 seconds between resend requests.
    
    Args:
        request: Email address to resend code to
        db: Database session
    
    Returns:
        Success message with expiry information
    
    Raises:
        HTTPException: If cooldown not passed (429) or sending fails
    """
    try:
        logger.info(f"🔄 Verification code resend request for: {request.email}")
        
        # Get user
        user = await get_current_user_from_email(request.email, db)
        
        # Check if already verified
        if user.email_verified:
            logger.info(f"User {request.email} already verified")
            return VerificationResponse(
                success=True,
                message="Email already verified",
                data={"already_verified": True}
            )
        
        # Resend verification code (with cooldown check)
        result = await EmailService.resend_verification_code(
            db=db,
            user_id=str(user.id),
            user_email=request.email
        )
        
        logger.info(f"✅ Verification code resent to {request.email}")
        
        return VerificationResponse(
            success=True,
            message="Verification code resent successfully",
            data={
                "expires_in_minutes": result.get("expires_in_minutes", 10),
                "max_attempts": result.get("max_attempts", 3)
            }
        )
        
    except HTTPException as e:
        # Handle 429 (Too Many Requests) specifically
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            logger.warning(f"Resend cooldown active for {request.email}")
            raise
        raise
    
    except Exception as e:
        logger.error(f"Error resending verification code: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resend verification code"
        )


@router.post("/verify", response_model=VerificationResponse, status_code=status.HTTP_200_OK)
async def verify_email_code(
    request: VerifyCodeRequest,
    db: Session = Depends(get_db)
):
    """
    Verify the email confirmation code.
    ✅ FIXED: Uses correct JWT token generation with user UUID
    
    User has 3 attempts to enter the correct code.
    Code expires after 10 minutes.
    
    Args:
        request: Email and verification code
        db: Database session
    
    Returns:
        Success message with JWT token and user data if code is valid
    
    Raises:
        HTTPException: If code invalid, expired, or max attempts reached
    """
    try:
        logger.info(f"🔍 Verification code check for: {request.email}")
        
        # Get user
        user = await get_current_user_from_email(request.email, db)
        
        # Verify code
        result = await EmailService.verify_code(
            db=db,
            user_id=str(user.id),
            code=request.code
        )
        
        if result.get("success"):
            logger.info(f"✅ Email verified successfully for {request.email}")
            
            # ✅ ИСПРАВЛЕНИЕ: Используем правильную функцию для создания JWT
            from backend.core.security import create_jwt_token
            token = create_jwt_token(user.id)  # Передаем UUID напрямую
            
            logger.info(f"🎟️ JWT token generated for user {user.id}")
            
            return VerificationResponse(
                success=True,
                message="Email verified successfully! You can now access all features.",
                data={
                    "user": result.get("user"),
                    "token": token,
                    "email_verified": True
                }
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification failed"
            )
        
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"Error verifying code: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify code"
        )


@router.get("/status/{email}", response_model=VerificationResponse, status_code=status.HTTP_200_OK)
async def get_verification_status(
    email: str,
    db: Session = Depends(get_db)
):
    """
    Get email verification status for a user.
    
    Returns information about:
    - Whether email is verified
    - Whether there's an active verification code
    - Time remaining until code expires
    - Remaining verification attempts
    
    Args:
        email: User email address
        db: Database session
    
    Returns:
        Verification status information
    
    Raises:
        HTTPException: If user not found
    """
    try:
        logger.info(f"📊 Status check for: {email}")
        
        # Get user
        user = await get_current_user_from_email(email, db)
        
        # Get verification status
        status_data = await EmailService.get_verification_status(
            db=db,
            user_id=str(user.id)
        )
        
        return VerificationResponse(
            success=True,
            message="Verification status retrieved",
            data=status_data
        )
        
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"Error getting verification status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get verification status"
        )


@router.delete("/cleanup-expired", response_model=VerificationResponse, status_code=status.HTTP_200_OK)
async def cleanup_expired_codes(
    db: Session = Depends(get_db)
):
    """
    Admin endpoint: Clean up expired verification codes.
    
    This is typically called by a background job or cron task.
    
    Args:
        db: Database session
    
    Returns:
        Number of codes deleted
    """
    try:
        logger.info("🧹 Starting cleanup of expired verification codes")
        
        deleted_count = await EmailService.cleanup_expired_codes(db)
        
        logger.info(f"✅ Cleanup complete: {deleted_count} codes deleted")
        
        return VerificationResponse(
            success=True,
            message=f"Cleaned up {deleted_count} expired codes",
            data={"deleted_count": deleted_count}
        )
        
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cleanup expired codes"
        )
