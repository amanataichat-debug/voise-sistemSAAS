"""
Notification service for WellcomeAI application.
Service for sending notifications to users about subscription status and other events.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import Optional, Dict, Any

from backend.models.user import User
from backend.core.logging import get_logger

logger = get_logger(__name__)

class NotificationService:
    """Service for sending notifications to users"""
    
    @staticmethod
    async def check_subscription_expirations(db: Session):
        """
        Check for subscriptions that are about to expire and send notifications
        
        Args:
            db: Database session
        """
        now = datetime.now(timezone.utc)
        soon = now + timedelta(days=3)  # 3 days before expiration
        
        # Find users whose subscription expires in the next 3 days
        expiring_users = db.query(User).filter(
            and_(
                User.subscription_end_date.isnot(None),
                User.is_active == True,
                User.email.isnot(None)
            )
        ).all()
        
        for user in expiring_users:
            # Ensure subscription_end_date is timezone-aware
            end_date = user.subscription_end_date
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
                
            # Check if it's in the expiring window (between now and soon)
            if now < end_date <= soon:
                # Calculate days left
                days_left = (end_date - now).days
                
                # Send notification by email
                await NotificationService.send_subscription_expiration_notice(
                    user=user,
                    days_left=days_left
                )
                
                logger.info(f"Sent subscription expiration notice to {user.email}, {days_left} days left")
    
    @staticmethod
    async def send_subscription_expiration_notice(user: User, days_left: int):
        """
        Send subscription expiration notice to user
        
        Args:
            user: User to notify
            days_left: Number of days left in subscription
        """
        try:
            # TODO: Implement actual email sending logic
            # This is a placeholder for the email sending functionality
            
            subject = f"Your subscription expires in {days_left} days"
            
            # Ensure subscription_end_date is timezone-aware
            end_date = user.subscription_end_date
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            
            message = f"""
            Hello {user.first_name or user.email},
            
            Your subscription to Live VoiceAI will expire in {days_left} days on {end_date.strftime('%Y-%m-%d')}.
            
            To continue using all features, please renew your subscription before it expires.
            
            Thank you for using Live VoiceAI!
            """
            
            logger.info(f"Would send email to {user.email} with subject: {subject}")
            
            # In a real implementation, you would call your email sending function here
            # Example:
            # from backend.core.email import send_email
            # await send_email(to=user.email, subject=subject, message=message)
            
        except Exception as e:
            logger.error(f"Error sending subscription expiration notice: {str(e)}")
    
    @staticmethod
    async def send_subscription_started_notice(
        user: User, 
        plan_name: str, 
        end_date: datetime,
        is_trial: bool = False
    ):
        """
        Send subscription started notice to user
        
        Args:
            user: User to notify
            plan_name: Name of the subscription plan
            end_date: End date of the subscription
            is_trial: Whether this is a trial subscription
        """
        try:
            subscription_type = "trial" if is_trial else "subscription"
            
            subject = f"Your {subscription_type} to {plan_name} plan has started"
            
            # Ensure end_date is timezone-aware
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            
            message = f"""
            Hello {user.first_name or user.email},
            
            Your {subscription_type} to the {plan_name} plan has been activated.
            
            Your {subscription_type} will expire on {end_date.strftime('%Y-%m-%d')}.
            
            Thank you for using Live VoiceAI!
            """
            
            logger.info(f"Would send email to {user.email} with subject: {subject}")
            
            # In a real implementation, you would call your email sending function here
            
        except Exception as e:
            logger.error(f"Error sending subscription started notice: {str(e)}")
    
    @staticmethod
    async def send_subscription_expired_notice(user: User):
        """
        Send subscription expired notice to user
        
        Args:
            user: User to notify
        """
        try:
            subject = "Your subscription has expired"
            
            message = f"""
            Hello {user.first_name or user.email},
            
            Your subscription to Live VoiceAI has expired.
            
            To continue using all features, please renew your subscription.
            
            Thank you for using Live VoiceAI!
            """
            
            logger.info(f"Would send email to {user.email} with subject: {subject}")
            
            # In a real implementation, you would call your email sending function here
            
        except Exception as e:
            logger.error(f"Error sending subscription expired notice: {str(e)}")
