"""
Subscription schemas for WellcomeAI application.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from decimal import Decimal

class SubscriptionPlanBase(BaseModel):
    """Base schema for subscription plans"""
    name: str = Field(..., description="Name of the subscription plan")
    code: str = Field(..., description="Unique code identifier for the plan")
    price: Decimal = Field(..., description="Price of the subscription plan")
    max_assistants: int = Field(..., description="Maximum number of assistants allowed")
    description: Optional[str] = Field(None, description="Description of the plan")
    is_active: bool = Field(True, description="Whether the plan is active")

class SubscriptionPlanCreate(SubscriptionPlanBase):
    """Schema for creating a subscription plan"""
    pass

class SubscriptionPlanUpdate(BaseModel):
    """Schema for updating a subscription plan"""
    name: Optional[str] = Field(None, description="Name of the subscription plan")
    price: Optional[Decimal] = Field(None, description="Price of the subscription plan")
    max_assistants: Optional[int] = Field(None, description="Maximum number of assistants allowed")
    description: Optional[str] = Field(None, description="Description of the plan")
    is_active: Optional[bool] = Field(None, description="Whether the plan is active")

class SubscriptionPlanResponse(SubscriptionPlanBase):
    """Schema for subscription plan response"""
    id: str = Field(..., description="Subscription plan ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")

    class Config:
        orm_mode = True

class UserSubscriptionInfo(BaseModel):
    """Schema for user subscription information"""
    subscription_plan: Optional[SubscriptionPlanResponse] = Field(None, description="Current subscription plan")
    subscription_start_date: Optional[datetime] = Field(None, description="Start date of subscription")
    subscription_end_date: Optional[datetime] = Field(None, description="End date of subscription")
    is_trial: bool = Field(False, description="Whether user is in trial period")
    days_left: Optional[int] = Field(None, description="Days left in current subscription")
    
    class Config:
        orm_mode = True
