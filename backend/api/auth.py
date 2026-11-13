# backend/api/auth.py
"""
Authentication API endpoints for WellcomeAI application.
✅ PRODUCTION READY: Email verification с обработкой "застревания"
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    Token,
    PasswordResetRequest,
    PasswordResetConfirm
)
from backend.schemas.user import UserResponse
from backend.services.auth_service import AuthService
from backend.services.email_service import EmailService
from backend.models.user import User

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()

# Security scheme
security = HTTPBearer()


@router.post("/register", response_model=dict, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: RegisterRequest,
    db: Session = Depends(get_db)
):
    """
    Register a new user account.
    
    ✅ ВАРИАНТ A: Если email существует но не подтвержден - отправить новый код
    
    Args:
        user_data: User registration data with email, password, and optional profile info
        db: Database session dependency
    
    Returns:
        A response with verification status (NO TOKEN until email verified)
    """
    try:
        logger.info(f"📝 Registration request for: {user_data.email}")
        
        # ✅ ВАРИАНТ A: Проверяем существует ли пользователь
        from backend.models.user import find_user_by_email
        existing_user = find_user_by_email(db, user_data.email)
        
        if existing_user:
            # Если email УЖЕ ПОДТВЕРЖДЕН - ошибка
            if existing_user.email_verified:
                logger.warning(f"❌ Registration blocked: Email already verified - {user_data.email}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered. Please login."
                )
            
            # ✅ Email НЕ ПОДТВЕРЖДЕН - отправляем новый код
            logger.info(f"🔄 User exists but not verified. Resending code to: {user_data.email}")
            
            # Клиент регистрируется снова (письмо не дошло) — часто уже с ДРУГИМ паролем.
            # Раньше пароль оставался от первой попытки: после подтверждения почты «верный»
            # пароль не подходил (13.11.2026, жалобы «логин и пароль верные, а не пускает»).
            # Почта не подтверждена — владелец аккаунта ещё не доказан, новый пароль безопасен.
            try:
                from backend.core.security import hash_password
                existing_user.password_hash = hash_password(user_data.password)
                if user_data.first_name:
                    existing_user.first_name = user_data.first_name
                if user_data.company_name:
                    existing_user.company_name = user_data.company_name
                db.commit()
            except Exception as pwd_error:
                db.rollback()
                logger.warning(f"Re-registration: password not updated: {pwd_error}")
            try:
                verification_result = await EmailService.send_verification_code(
                    db=db,
                    user_id=str(existing_user.id),  # ✅ UUID → String
                    user_email=existing_user.email
                )
                
                return {
                    "success": True,
                    "message": "Account exists but not verified. New verification code sent!",
                    "user": {
                        "id": str(existing_user.id),
                        "email": existing_user.email,
                        "email_verified": existing_user.email_verified
                    },
                    "verification_required": True,
                    "verification_sent": True,
                    "expires_in_minutes": verification_result.get("expires_in_minutes", 10),
                    "max_attempts": verification_result.get("max_attempts", 3)
                }
                
            except Exception as email_error:
                logger.error(f"Failed to resend verification email: {email_error}")
                return _let_in_without_email(db, existing_user, "re-register")
        
        # ✅ Новый пользователь - создаем аккаунт
        result = await AuthService.register(db, user_data)
        
        user_id = result.get("user", {}).get("id")
        user_email = result.get("user", {}).get("email")
        
        if not user_id or not user_email:
            logger.error("User creation returned invalid data")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Registration failed - invalid user data"
            )
        
        logger.info(f"✅ User created: {user_email} (ID: {user_id})")
        
        # ✅ Автоматически отправить код верификации
        try:
            verification_result = await EmailService.send_verification_code(
                db=db,
                user_id=user_id,  # ✅ Уже строка из auth_service
                user_email=user_email
            )
            
            logger.info(f"✅ Verification code sent to {user_email}")
            
            # Вернуть ответ БЕЗ токена (токен дадим после верификации)
            return {
                "success": True,
                "message": "Registration successful! Check your email for verification code.",
                "user": result.get("user"),
                "verification_required": True,
                "verification_sent": True,
                "expires_in_minutes": verification_result.get("expires_in_minutes", 10),
                "max_attempts": verification_result.get("max_attempts", 3),
                "referral_info": result.get("referral_info"),  # Партнерская программа
                "partner_program": result.get("partner_program")
            }
            
        except Exception as email_error:
            logger.error(f"Failed to send verification email: {email_error}")
            # Письмо не ушло — не оставляем клиента у закрытой двери (раньше он видел ошибку
            # и не мог ни ввести код, ни войти): впускаем, а сбой почты видно в логе.
            from backend.models.user import User as _User
            new_user = db.query(_User).filter(_User.id == user_id).first()
            if new_user is None:
                raise HTTPException(status_code=500, detail="Registration failed - user not found")
            out = _let_in_without_email(db, new_user, "register")
            out["referral_info"] = result.get("referral_info")
            out["partner_program"] = result.get("partner_program")
            return out
        
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"Unexpected error in register endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed due to server error"
        )


def _let_in_without_email(db: Session, user, where: str) -> dict:
    """
    Код подтверждения не отправился (сбой почтового сервера) — подтверждаем почту сами и выдаём
    токен. Иначе клиент не может ни ввести код, ни войти. Выключатель: AUTH_EMAIL_FALLBACK=0.
    """
    import os
    from backend.core.security import create_jwt_token as create_access_token
    if os.getenv("AUTH_EMAIL_FALLBACK", "1").strip().lower() in ("0", "false", "no", "off"):
        raise HTTPException(
            # 400, а не 5xx: на проде текст ответов 5xx заменяется на «Internal server error»
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось отправить код на почту. Попробуйте через несколько минут.",
        )
    user.email_verified = True
    db.commit()
    logger.error(f"⚠️ SMTP: письмо с кодом не ушло ({where}), почта {user.email} подтверждена автоматически")
    return {
        "success": True,
        "message": "Registration successful",
        "token": create_access_token(str(user.id)),
        "token_type": "bearer",
        "user": {"id": str(user.id), "email": user.email, "email_verified": True},
        "verification_required": False,
        "verification_sent": False,
        "email_fallback": True,
    }


@router.post("/login", response_model=dict)
async def login(
    login_data: LoginRequest,
    db: Session = Depends(get_db)
):
    """
    Authenticate a user and generate a token.
    
    ✅ ОБНОВЛЕНО: Проверяет email_verified перед выдачей токена
    
    Args:
        login_data: User login credentials
        db: Database session dependency
    
    Returns:
        A token and the user data (only if email verified)
    
    Raises:
        HTTPException 403: If email not verified
    """
    try:
        logger.info(f"🔐 Login attempt for: {login_data.email}")
        
        # 1. Authenticate user (проверка credentials)
        result = await AuthService.login(db, login_data)
        
        user = result.get("user")
        if not user:
            logger.error("Authentication returned invalid user data")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Login failed - invalid response"
            )
        
        # 2. ✅ Проверяем email_verified
        if not user.get("email_verified", False):
            logger.warning(f"Login blocked: Email not verified for {login_data.email}")
            # Пароль верный, почта не подтверждена: сразу шлём свежий код (форма входа откроет
            # поле для кода). Письмо не уходит — впускаем, как при регистрации.
            from backend.models.user import find_user_by_email
            db_user = find_user_by_email(db, login_data.email)
            if db_user is not None and not db_user.email_verified:
                try:
                    await EmailService.send_verification_code(
                        db=db, user_id=str(db_user.id), user_email=db_user.email)
                except Exception as email_error:
                    logger.error(f"Login: verification email failed: {email_error}")
                    return _let_in_without_email(db, db_user, "login")
            
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email not verified. Please check your email for verification code.",
                headers={
                    "X-Verification-Required": "true",
                    "X-User-Email": login_data.email
                }
            )
        
        # 3. ✅ Email verified - выдаем токен
        logger.info(f"✅ Login successful for verified user: {login_data.email}")
        
        return {
            "success": True,
            "message": "Login successful",
            "token": result.get("token"),
            "user": user
        }
        
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"Unexpected error in login endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed due to server error"
        )


@router.post("/reset-password", response_model=dict)
async def reset_password_request(
    reset_data: PasswordResetRequest,
    db: Session = Depends(get_db)
):
    """
    Request a password reset: send a 6-digit code to the user's email.

    Args:
        reset_data: Request body with user email
        db: Database session dependency

    Returns:
        Confirmation message (same whether the account exists or not)
    """
    try:
        await AuthService.reset_password_request(db, reset_data.email)
        # Always return success for security reasons
        return {
            "success": True,
            "message": "If an account with this email exists, a password reset code has been sent"
        }
    except HTTPException as e:
        # 429 (cooldown) пробрасываем — фронту нужен таймер повторной отправки
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(f"Error in reset password request: {e.detail}")
        return {
            "success": True,
            "message": "If an account with this email exists, a password reset code has been sent"
        }
    except Exception as e:
        logger.error(f"Error in reset password request: {str(e)}")
        # Still return success for security reasons
        return {
            "success": True,
            "message": "If an account with this email exists, a password reset code has been sent"
        }


@router.post("/reset-password-confirm", response_model=dict)
async def reset_password_confirm(
    confirm_data: PasswordResetConfirm,
    db: Session = Depends(get_db)
):
    """
    Confirm a password reset using the emailed 6-digit code.

    Args:
        confirm_data: Request body with email, code and new password
        db: Database session dependency

    Returns:
        Confirmation message
    """
    try:
        await AuthService.reset_password_confirm(
            db=db,
            email=confirm_data.email,
            code=confirm_data.code,
            new_password=confirm_data.new_password
        )
        return {
            "success": True,
            "message": "Password reset successfully. You can now log in with your new password."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in reset password confirm: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password reset failed due to server error"
        )
