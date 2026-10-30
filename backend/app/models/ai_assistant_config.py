"""
AI Assistant Configuration ORM Model.
Stores tenant-specific AI persona, instructions, thresholds, and channel credentials.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class AIAssistantConfig(Base):
    __tablename__ = "ai_assistant_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    assistant_name: Mapped[str] = mapped_column(String(100), nullable=False, default="Sales Assistant")
    personality: Mapped[str] = mapped_column(String(50), nullable=False, default="Professional")
    custom_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    greeting_message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="Hello! Welcome to our sales assistant. How can I help you today?",
    )
    max_discount_percent: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    qualification_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    # Optional tenant-level override for Groq API key and model (key is encrypted at rest — see core.crypto)
    groq_api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    groq_model: Mapped[str] = mapped_column(String(100), nullable=False, default="llama-3.3-70b-versatile")
    # Cheaper/faster model for query rewrite, lead analysis and summaries (falls back to LLM_FAST_MODEL)
    fast_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    language: Mapped[str] = mapped_column(String(30), nullable=False, default="auto")  # "auto" mirrors the customer

    # WhatsApp Channel configuration (access token / app secret are encrypted at rest)
    whatsapp_phone_number_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    whatsapp_access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    whatsapp_app_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    whatsapp_verify_token: Mapped[str] = mapped_column(String(100), nullable=False, default="ai_sales_verify_token")
    # Approved Meta template used to re-open a conversation outside the 24h window (optional)
    whatsapp_followup_template: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Automation & Assignment Settings
    auto_followup_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    followup_delay_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    max_followups: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    followup_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="AUTO")  # AUTO | APPROVAL
    round_robin_assignment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notification_sound_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Optional monthly AI token budget (None = unlimited). Over budget → graceful handoff instead of AI reply.
    monthly_token_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<AIAssistantConfig tenant_id={self.tenant_id} name={self.assistant_name}>"
