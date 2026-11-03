"""
Pydantic schemas for Lead Management and Sales Pipeline.
"""
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class LeadResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: Optional[uuid.UUID] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    conversation_id: Optional[uuid.UUID] = None

    title: str
    status: str
    score: int
    score_reason: Optional[str] = None
    requirements: Optional[str] = None
    budget: Optional[float] = None
    timeline: Optional[str] = None
    priority: str
    assigned_user_id: Optional[uuid.UUID] = None
    assigned_user_name: Optional[str] = None
    source: str
    notes: Optional[str] = None
    closed_at: Optional[datetime] = None
    close_reason: Optional[str] = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LeadUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[str] = Field(None, max_length=30)
    priority: Optional[str] = Field(None, max_length=20)
    assigned_user_id: Optional[uuid.UUID] = None
    budget: Optional[float] = None
    timeline: Optional[str] = None
    requirements: Optional[str] = None
    notes: Optional[str] = None


class LeadListResponse(BaseModel):
    items: List[LeadResponse]
    total: int
    page: int
    page_size: int
    pages: int


class PipelineStatsResponse(BaseModel):
    total_leads: int
    stage_counts: Dict[str, int]
    group_counts: Dict[str, int] = {}  # NEW / SERVING / COMPLETED: the three steps rolled up from the stages
    total_estimated_value: float
    hot_leads_count: int
