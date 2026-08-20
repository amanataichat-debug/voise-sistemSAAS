# backend/models/email_verification.py
"""
Email verification model for WellcomeAI application.
Stores verification codes sent to users during registration.
"""

import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, Integer, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base, BaseModel

class EmailVerification(Base, BaseModel):
    """
    Model for storing email verification codes.
    
    Features:
    - 6-digit numeric code
    - 10-minute expiration
    - Tracks usage and attempts
    - Automatic cleanup of expired codes
    """
    __tablename__ = "email_verifications"
    
    # Primary fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False,
        index=True
    )
    
    # Verification code (6 digits)
    code = Column(String(6), nullable=False, index=True)
    
    # Expiration timestamp
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    
    # Status tracking
    is_used = Column(Boolean, default=False, nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Constraints
    __table_args__ = (
        CheckConstraint('code ~ \'^[0-9]{6}$\'', name='check_code_format'),
        CheckConstraint('attempts >= 0', name='check_attempts_positive'),
    )
    
    # Relationships
    user = relationship("User", backref="verification_codes")
    
    def __repr__(self):
        """String representation of EmailVerification"""
        return f"<EmailVerification(user_id={self.user_id}, code={self.code[:3]}***, used={self.is_used})>"
    
    @property
    def is_expired(self) -> bool:
        """Check if the verification code has expired"""
        now = datetime.now(timezone.utc)
        expires_at = self.expires_at
        
        # Normalize timezone
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        
        return now > expires_at
    
    @property
    def is_valid(self) -> bool:
        """Check if the code is valid (not used and not expired)"""
        return not self.is_used and not self.is_expired
    
    @property
    def time_remaining_seconds(self) -> int:
        """Get remaining time in seconds until expiration"""
        if self.is_expired:
            return 0
        
        now = datetime.now(timezone.utc)
        expires_at = self.expires_at
        
        # Normalize timezone
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        
        delta = expires_at - now
        return max(0, int(delta.total_seconds()))
    
    def increment_attempts(self) -> int:
        """Increment the number of verification attempts"""
        self.attempts += 1
        return self.attempts
    
    def mark_as_used(self) -> None:
        """Mark the verification code as used"""
        self.is_used = True
    
    def to_dict(self):
        """Convert to dictionary with string ID and masked code"""
        data = super().to_dict()
        
        # Convert UUID to string
        if isinstance(data.get("id"), uuid.UUID):
            data["id"] = str(data["id"])
        if isinstance(data.get("user_id"), uuid.UUID):
            data["user_id"] = str(data["user_id"])
        
        # Mask the code for security (only show first 2 digits)
        data["code_masked"] = f"{self.code[:2]}****"
        
        # Add computed properties
        data["is_expired"] = self.is_expired
        data["is_valid"] = self.is_valid
        data["time_remaining"] = self.time_remaining_seconds
        
        # Remove sensitive data
        data.pop("code", None)  # Never return actual code in API responses
        
        return data
    
    @classmethod
    def create_verification_code(
        cls, 
        user_id: uuid.UUID, 
        code: str, 
        expiration_minutes: int = 10
    ) -> 'EmailVerification':
        """
        Factory method to create a new verification code
        
        Args:
            user_id: UUID of the user
            code: 6-digit verification code
            expiration_minutes: Minutes until expiration (default: 10)
        
        Returns:
            New EmailVerification instance
        """
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expiration_minutes)
        
        return cls(
            user_id=user_id,
            code=code,
            expires_at=expires_at,
            is_used=False,
            attempts=0
        )
    
    @classmethod
    def get_active_code_for_user(cls, db_session, user_id: uuid.UUID):
        """
        Get the most recent active (unused, not expired) verification code for a user
        
        Args:
            db_session: Database session
            user_id: UUID of the user
        
        Returns:
            EmailVerification instance or None
        """
        now = datetime.now(timezone.utc)
        
        return db_session.query(cls).filter(
            cls.user_id == user_id,
            cls.is_used == False,
            cls.expires_at > now
        ).order_by(cls.created_at.desc()).first()
    
    @classmethod
    def cleanup_expired_codes(cls, db_session) -> int:
        """
        Delete all expired verification codes
        
        Args:
            db_session: Database session
        
        Returns:
            Number of deleted codes
        """
        now = datetime.now(timezone.utc)
        
        result = db_session.query(cls).filter(
            cls.expires_at < now
        ).delete()
        
        db_session.commit()
        return result
