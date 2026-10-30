"""
Pydantic schemas for AI Assistant Configuration.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AIAssistantConfigBase(BaseModel):
    assistant_name: str = Field(default="Sales Assistant", min_length=1, max_length=100)
    personality: str = Field(default="Professional", max_length=50)
    custom_instructions: Optional[str] = None
    greeting_message: str = Field(
        default="Hello! Welcome to our sales assistant. How can I help you today?"
    )
    max_discount_percent: float = Field(default=10.0, ge=0.0, le=100.0)
    qualification_threshold: int = Field(default=60, ge=0, le=100)
    groq_model: str = Field(default="llama-3.3-70b-versatile", max_length=100)
    whatsapp_phone_number_id: Optional[str] = Field(None, max_length=100)
    whatsapp_verify_token: str = Field(default="ai_sales_verify_token", max_length=100)
    is_enabled: bool = True


class AIAssistantConfigUpdate(BaseModel):
    assistant_name: Optional[str] = Field(None, min_length=1, max_length=100)
    personality: Optional[str] = Field(None, max_length=50)
    custom_instructions: Optional[str] = None
    greeting_message: Optional[str] = None
    max_discount_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    qualification_threshold: Optional[int] = Field(None, ge=0, le=100)
    groq_api_key: Optional[str] = Field(None, max_length=255)
    groq_model: Optional[str] = Field(None, max_length=100)
    whatsapp_phone_number_id: Optional[str] = Field(None, max_length=100)
    whatsapp_access_token: Optional[str] = Field(None, max_length=512)
    whatsapp_verify_token: Optional[str] = Field(None, max_length=100)
    is_enabled: Optional[bool] = None


class AIAssistantConfigResponse(AIAssistantConfigBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    has_groq_api_key: bool = False
    has_whatsapp_access_token: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
