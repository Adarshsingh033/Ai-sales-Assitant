"""
AI Trace ORM Model — one row per AI turn for observability and troubleshooting.
Records what was retrieved, which tools ran, what guardrails changed, and where the time went.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class AITrace(Base):
    __tablename__ = "ai_traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )

    trigger: Mapped[str] = mapped_column(String(30), nullable=False, default="INBOUND")  # INBOUND, FOLLOWUP, SIMULATOR
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retrieval: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)  # query, chunk ids, scores, confidence
    tool_calls: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    guardrails: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    timings: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    handoff_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (Index("ix_ai_traces_tenant_created", "tenant_id", "created_at"),)

    def __repr__(self) -> str:
        return f"<AITrace id={self.id} trigger={self.trigger} model={self.model}>"
