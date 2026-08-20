"""
Subscription logs API endpoints for WellcomeAI application.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
import uuid

from backend.core.logging import get_logger
from backend.core.dependencies import get_current_user, check_admin_access
from backend.db.session import get_db
from backend.models.user import User
from backend.models.subscription import SubscriptionLog
from backend.services.subscription_service import SubscriptionService

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()

@router.get("/my-logs", response_model=List[Dict[str, Any]])
async def get_my_subscription_logs(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get subscription logs for current user.
    
    Args:
        skip: Number of logs to skip
        limit: Maximum number of logs to return
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        List of subscription logs
    """
    logs = db.query(SubscriptionLog).filter(
        SubscriptionLog.user_id == current_user.id
    ).order_by(SubscriptionLog.created_at.desc()).offset(skip).limit(limit).all()
    
    result = []
    for log in logs:
        result.append({
            "id": str(log.id),
            "action": log.action,
            "plan_code": log.plan_code,
            "details": log.details,
            "created_at": log.created_at
        })
    
    return result

@router.get("/all", response_model=List[Dict[str, Any]])
async def get_all_subscription_logs(
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Get all subscription logs (admin only).
    
    Args:
        skip: Number of logs to skip
        limit: Maximum number of logs to return
        user_id: Filter by user ID
        action: Filter by action
        current_user: Current admin user
        db: Database session dependency
    
    Returns:
        List of subscription logs
    """
    query = db.query(SubscriptionLog)
    
    # Apply filters
    if user_id:
        query = query.filter(SubscriptionLog.user_id == uuid.UUID(user_id))
    if action:
        query = query.filter(SubscriptionLog.action == action)
    
    logs = query.order_by(SubscriptionLog.created_at.desc()).offset(skip).limit(limit).all()
    
    result = []
    for log in logs:
        result.append({
            "id": str(log.id),
            "user_id": str(log.user_id),
            "action": log.action,
            "plan_id": str(log.plan_id) if log.plan_id else None,
            "plan_code": log.plan_code,
            "details": log.details,
            "created_at": log.created_at
        })
    
    return result
