"""
Authentication schemas for WellcomeAI application.
Defines schemas for authentication-related requests and responses with partner referral support.
✅ v2.6: Исправлено - utm_data теперь принимает null значения (Dict[str, Optional[str]])
✅ v2.6: Исправлен баг затенения переменной в utm_data_format валидаторе
"""

from pydantic import BaseModel, Field, EmailStr, validator
from typing import Optional, Dict, Any
from datetime import datetime

class TokenData(BaseModel):
    """Schema for JWT token data"""
    sub: str
    exp: int

class Token(BaseModel):
    """Schema for authentication token response"""
    token: str
    token_type: str = "bearer"

class LoginRequest(BaseModel):
    """Schema for user login request"""
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., min_length=8, description="User password")

    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "password123"
            }
        }

class RegisterRequest(BaseModel):
    """
    Schema for user registration request with partner referral support
    """
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., min_length=8, description="User password")
    first_name: Optional[str] = Field(None, max_length=50, description="User first name")
    last_name: Optional[str] = Field(None, max_length=50, description="User last name")
    company_name: Optional[str] = Field(None, max_length=100, description="Company name")
    
    # 🆕 ПАРТНЕРСКИЕ ПОЛЯ
    referral_code: Optional[str] = Field(
        None, 
        max_length=10,
        description="Referral code from partner (usually from UTM campaign)"
    )
    # ✅ ИСПРАВЛЕНО: Dict[str, Optional[str]] вместо Dict[str, str]
    # Теперь принимает null значения в utm_term и других полях
    utm_data: Optional[Dict[str, Optional[str]]] = Field(
        None,
        description="UTM tracking data from referral link"
    )

    @validator('password')
    def password_complexity(cls, v):
        """Validate password complexity"""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        
        # Дополнительные проверки безопасности пароля
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        
        if not any(c.isalpha() for c in v):
            raise ValueError('Password must contain at least one letter')
            
        return v

    @validator('email')
    def email_format(cls, v):
        """Additional email validation"""
        email_str = str(v).lower().strip()
        
        # Проверка на недопустимые домены (можно расширить)
        blocked_domains = ['tempmail.com', '10minutemail.com']
        domain = email_str.split('@')[-1] if '@' in email_str else ''
        
        if domain in blocked_domains:
            raise ValueError('This email domain is not allowed')
            
        return email_str

    @validator('referral_code')
    def referral_code_format(cls, v):
        """Validate referral code format"""
        if v is None:
            return v
            
        # Приводим к верхнему регистру и убираем пробелы
        code = str(v).upper().strip()
        
        # Проверяем формат: 2 буквы + 6 цифр (AB123456)
        if len(code) != 8:
            raise ValueError('Referral code must be 8 characters long')
            
        if not (code[:2].isalpha() and code[2:].isdigit()):
            raise ValueError('Referral code must be in format: 2 letters + 6 digits (e.g., AB123456)')
            
        return code

    @validator('utm_data')
    def utm_data_format(cls, v):
        """
        Validate UTM data format
        ✅ ИСПРАВЛЕНО: 
        - Фильтрует null значения
        - Исправлен баг затенения переменной (val вместо v)
        """
        if v is None:
            return v
            
        if not isinstance(v, dict):
            raise ValueError('UTM data must be a dictionary')
            
        # Проверяем допустимые UTM параметры
        allowed_utm_keys = {
            'utm_source', 'utm_medium', 'utm_campaign', 
            'utm_content', 'utm_term'
        }
        
        # ✅ ИСПРАВЛЕНО: 
        # - val вместо v (избежание затенения переменной)
        # - Проверка на None ДО преобразования в строку
        # - Фильтрация пустых строк после strip()
        filtered_utm = {
            k: str(val).strip() 
            for k, val in v.items() 
            if k in allowed_utm_keys and val is not None and str(val).strip()
        }
        
        return filtered_utm if filtered_utm else None

    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "password123",
                "first_name": "John",
                "last_name": "Doe",
                "company_name": "ACME Inc.",
                "referral_code": "AB123456",
                "utm_data": {
                    "utm_source": "partner",
                    "utm_medium": "referral",
                    "utm_campaign": "AB123456",
                    "utm_content": "registration"
                }
            }
        }

class PasswordResetRequest(BaseModel):
    """Schema for password reset request"""
    email: EmailStr = Field(..., description="User email")

    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com"
            }
        }

class PasswordResetConfirm(BaseModel):
    """Schema for password reset confirmation"""
    token: str = Field(..., description="Password reset token")
    new_password: str = Field(..., min_length=8, description="New password")

    @validator('new_password')
    def password_complexity(cls, v):
        """Validate password complexity"""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
            
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        
        if not any(c.isalpha() for c in v):
            raise ValueError('Password must contain at least one letter')
            
        return v

    class Config:
        schema_extra = {
            "example": {
                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "new_password": "newpassword123"
            }
        }

class AuthResponse(BaseModel):
    """Schema for general authentication response"""
    success: bool = Field(..., description="Success status")
    message: str = Field(..., description="Response message")
    token: Optional[str] = Field(None, description="JWT access token")
    token_type: Optional[str] = Field("bearer", description="Token type")
    user: Optional[Dict[str, Any]] = Field(None, description="User information")
    
    # 🆕 ПАРТНЕРСКАЯ ИНФОРМАЦИЯ В ОТВЕТЕ
    referral_info: Optional[Dict[str, Any]] = Field(
        None, 
        description="Information about referral if user was referred"
    )
    partner_program: Optional[Dict[str, Any]] = Field(
        None,
        description="Partner program information and opportunities"
    )
    
    # ✅ НОВОЕ: Email verification info
    verification_required: Optional[bool] = Field(None, description="Whether email verification is required")
    verification_sent: Optional[bool] = Field(None, description="Whether verification code was sent")
    expires_in_minutes: Optional[int] = Field(None, description="Verification code expiration time")
    max_attempts: Optional[int] = Field(None, description="Maximum verification attempts")

    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "message": "Registration successful",
                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "user": {
                    "id": "123e4567-e89b-12d3-a456-426614174000",
                    "email": "user@example.com",
                    "first_name": "John",
                    "last_name": "Doe",
                    "subscription_plan": "free",
                    "is_trial": True
                },
                "referral_info": {
                    "message": "🎉 Вы зарегистрировались по партнерской ссылке!",
                    "referral_code": "AB123456",
                    "partner_name": "Партнер",
                    "commission_rate": 30.0
                },
                "partner_program": {
                    "is_referred": True,
                    "show_welcome_bonus": True,
                    "referrer_gets_commission": True,
                    "commission_rate": 30.0
                },
                "verification_required": True,
                "verification_sent": True,
                "expires_in_minutes": 10,
                "max_attempts": 3
            }
        }

class LoginResponse(AuthResponse):
    """Schema for login response with additional user details"""
    user: Optional[Dict[str, Any]] = Field(None, description="Extended user information")

    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "message": "Login successful",
                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "user": {
                    "id": "123e4567-e89b-12d3-a456-426614174000",
                    "email": "user@example.com",
                    "first_name": "John",
                    "last_name": "Doe",
                    "subscription_plan": "start",
                    "is_trial": False,
                    "is_admin": False,
                    "last_login": "2024-01-20T10:30:00Z",
                    "subscription_status": {
                        "active": True,
                        "days_left": 25,
                        "max_assistants": 3
                    },
                    "partner_info": {
                        "is_partner": True,
                        "referral_code": "AB123456",
                        "total_referrals": 5,
                        "total_earnings": 1500.00
                    }
                }
            }
        }

# 🆕 НОВЫЕ СХЕМЫ ДЛЯ ПАРТНЕРСКОЙ СИСТЕМЫ

class ReferralValidationRequest(BaseModel):
    """Schema for validating referral code"""
    referral_code: str = Field(..., max_length=10, description="Referral code to validate")

    @validator('referral_code')
    def referral_code_format(cls, v):
        """Validate referral code format"""
        code = str(v).upper().strip()
        
        if len(code) != 8:
            raise ValueError('Referral code must be 8 characters long')
            
        if not (code[:2].isalpha() and code[2:].isdigit()):
            raise ValueError('Referral code must be in format: 2 letters + 6 digits')
            
        return code

class ReferralValidationResponse(BaseModel):
    """Schema for referral code validation response"""
    valid: bool = Field(..., description="Whether the referral code is valid")
    referral_code: str = Field(..., description="The validated referral code")
    partner_info: Optional[Dict[str, Any]] = Field(None, description="Partner information if valid")
    message: str = Field(..., description="Validation message")

    class Config:
        schema_extra = {
            "example": {
                "valid": True,
                "referral_code": "AB123456",
                "partner_info": {
                    "partner_name": "John Doe",
                    "commission_rate": 30.0,
                    "total_referrals": 10
                },
                "message": "Valid referral code from active partner"
            }
        }

class UTMTrackingData(BaseModel):
    """Schema for UTM tracking data"""
    utm_source: Optional[str] = Field(None, max_length=50, description="UTM source")
    utm_medium: Optional[str] = Field(None, max_length=50, description="UTM medium")
    utm_campaign: Optional[str] = Field(None, max_length=50, description="UTM campaign")
    utm_content: Optional[str] = Field(None, max_length=100, description="UTM content")
    utm_term: Optional[str] = Field(None, max_length=50, description="UTM term")

    @validator('*', pre=True)
    def clean_utm_values(cls, v):
        """Clean and validate UTM values"""
        if v is None:
            return v
        return str(v).strip() if str(v).strip() else None

    class Config:
        schema_extra = {
            "example": {
                "utm_source": "partner",
                "utm_medium": "referral",
                "utm_campaign": "AB123456",
                "utm_content": "registration",
                "utm_term": "signup"
            }
        }

class PasswordChangeRequest(BaseModel):
    """Schema for password change request"""
    current_password: str = Field(..., description="Current password")
    new_password: str = Field(..., min_length=8, description="New password")

    @validator('new_password')
    def password_complexity(cls, v):
        """Validate new password complexity"""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
            
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        
        if not any(c.isalpha() for c in v):
            raise ValueError('Password must contain at least one letter')
            
        return v

    class Config:
        schema_extra = {
            "example": {
                "current_password": "oldpassword123",
                "new_password": "newpassword456"
            }
        }

class PasswordChangeResponse(BaseModel):
    """Schema for password change response"""
    success: bool = Field(..., description="Success status")
    message: str = Field(..., description="Response message")

    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "message": "Password changed successfully"
            }
        }

# Экспорт всех схем для использования в других модулях
__all__ = [
    # Основные схемы аутентификации
    "TokenData",
    "Token", 
    "LoginRequest",
    "RegisterRequest",
    "AuthResponse",
    "LoginResponse",
    
    # Схемы сброса пароля
    "PasswordResetRequest",
    "PasswordResetConfirm",
    "PasswordChangeRequest", 
    "PasswordChangeResponse",
    
    # Новые схемы для партнерской системы
    "ReferralValidationRequest",
    "ReferralValidationResponse",
    "UTMTrackingData"
]
