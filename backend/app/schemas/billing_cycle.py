import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BillingCycleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    duration_months: int = Field(..., ge=1, description="Duration in months (e.g. 1, 3, 6, 12)")
    description: Optional[str] = Field(None, max_length=500)
    is_active: bool = Field(default=True)


class BillingCycleCreate(BillingCycleBase):
    pass


class BillingCycleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    duration_months: Optional[int] = Field(None, ge=1)
    description: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None


class BillingCycleStatusUpdate(BaseModel):
    is_active: bool


class BillingCycleResponse(BillingCycleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    created_at: datetime
    updated_at: datetime


class BillingCycleListResponse(BaseModel):
    items: List[BillingCycleResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
