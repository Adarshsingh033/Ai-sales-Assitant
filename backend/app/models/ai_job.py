"""
Durable background job queue (Postgres-backed; claimed with SKIP LOCKED).
Used for inbound channel messages, document/product indexing, summaries and re-indexing.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class AIJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Jobs sharing a conversation_id never run concurrently (keeps customer turns ordered).
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")  # PENDING RUNNING DONE FAILED
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (Index("ix_ai_jobs_status_run_at", "status", "run_at"),)

    def __repr__(self) -> str:
        return f"<AIJob id={self.id} type={self.job_type} status={self.status}>"
