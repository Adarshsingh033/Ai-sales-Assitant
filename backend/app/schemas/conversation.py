"""
Pydantic schemas for Conversation and Live Human Takeover.
"""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MessageResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_type: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    channel: str
    status: str
    assigned_user_id: Optional[uuid.UUID] = None
    ai_summary: Optional[str] = None
    detected_intent: Optional[str] = None
    last_message_preview: Optional[str] = None
    last_activity_at: datetime
    created_at: datetime
    messages: Optional[List[MessageResponse]] = None
    lead_id: Optional[uuid.UUID] = None  # the deal this chat belongs to (single-conversation view only)

    model_config = ConfigDict(from_attributes=True)


class ConversationListResponse(BaseModel):
    items: List[ConversationResponse]
    total: int


class HumanReplyRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
