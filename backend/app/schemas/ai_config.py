"""
Pydantic schemas for AI Assistant Configuration.
Secrets (API key, access token, app secret) are write-only: responses only say whether one is set.
"""
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AIAssistantConfigBase(BaseModel):
    assistant_name: str = Field(default="Sales Assistant", min_length=1, max_length=100)
    personality: str = Field(default="Professional", max_length=50)
    custom_instructions: Optional[str] = None
    greeting_message: str = Field(
        default="Hello! Welcome to our sales assistant. How can I help you today?"
    )
    language: str = Field(default="auto", max_length=30)  # "auto" mirrors the customer's language
    max_discount_percent: float = Field(default=10.0, ge=0.0, le=100.0)
    qualification_threshold: int = Field(default=60, ge=0, le=100)
    groq_model: str = Field(default="llama-3.3-70b-versatile", max_length=100)
    fast_model: Optional[str] = Field(default=None, max_length=100)
    whatsapp_phone_number_id: Optional[str] = Field(None, max_length=100)
    whatsapp_verify_token: str = Field(default="ai_sales_verify_token", max_length=100)
    whatsapp_followup_template: Optional[str] = Field(default=None, max_length=100)
    is_enabled: bool = True
    auto_followup_enabled: bool = True
    followup_delay_hours: int = Field(default=24, ge=1, le=168)
    max_followups: int = Field(default=3, ge=1, le=6)
    followup_mode: Literal["AUTO", "APPROVAL"] = "AUTO"
    round_robin_assignment: bool = True
    notification_sound_enabled: bool = True
    monthly_token_limit: Optional[int] = Field(default=None, ge=1000)


class AIAssistantConfigUpdate(BaseModel):
    assistant_name: Optional[str] = Field(None, min_length=1, max_length=100)
    personality: Optional[str] = Field(None, max_length=50)
    custom_instructions: Optional[str] = None
    greeting_message: Optional[str] = None
    language: Optional[str] = Field(None, max_length=30)
    max_discount_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    qualification_threshold: Optional[int] = Field(None, ge=0, le=100)
    groq_api_key: Optional[str] = Field(None, max_length=255)
    groq_model: Optional[str] = Field(None, max_length=100)
    fast_model: Optional[str] = Field(None, max_length=100)
    whatsapp_phone_number_id: Optional[str] = Field(None, max_length=100)
    whatsapp_access_token: Optional[str] = Field(None, max_length=512)
    whatsapp_app_secret: Optional[str] = Field(None, max_length=255)
    whatsapp_verify_token: Optional[str] = Field(None, max_length=100)
    whatsapp_followup_template: Optional[str] = Field(None, max_length=100)
    is_enabled: Optional[bool] = None
    auto_followup_enabled: Optional[bool] = None
    followup_delay_hours: Optional[int] = Field(None, ge=1, le=168)
    max_followups: Optional[int] = Field(None, ge=1, le=6)
    followup_mode: Optional[Literal["AUTO", "APPROVAL"]] = None
    round_robin_assignment: Optional[bool] = None
    notification_sound_enabled: Optional[bool] = None
    monthly_token_limit: Optional[int] = Field(None, ge=1000)


class AIAssistantConfigResponse(AIAssistantConfigBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    has_groq_api_key: bool = False
    has_whatsapp_access_token: bool = False
    has_whatsapp_app_secret: bool = False
    telegram_start_link: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
