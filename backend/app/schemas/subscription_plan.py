import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.subscription_plan import BillingCycle


class SubscriptionPlanBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    price: float = Field(..., ge=0.0)
    currency: str = Field(default="USD", max_length=10)
    billing_cycle: str = Field(default="Monthly", max_length=100)
    
    max_users: int = Field(..., ge=1)
    max_branches: int = Field(..., ge=1)


class SubscriptionPlanCreate(SubscriptionPlanBase):
    pass


class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    price: Optional[float] = Field(None, ge=0.0)
    currency: Optional[str] = Field(None, max_length=10)
    billing_cycle: Optional[str] = Field(None, max_length=100)
    
    max_users: Optional[int] = Field(None, ge=1)
    max_branches: Optional[int] = Field(None, ge=1)


class SubscriptionPlanStatusUpdate(BaseModel):
    is_active: bool


class SubscriptionPlanResponse(SubscriptionPlanBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SubscriptionPlanListResponse(BaseModel):
    items: List[SubscriptionPlanResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class TenantPlanAssignment(BaseModel):
    subscription_plan_id: uuid.UUID
