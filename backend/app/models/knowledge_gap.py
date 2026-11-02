"""
KnowledgeGap — a question the team asked that the knowledge base could not answer.

Admins use the list to decide which document to add. Repeated questions (same wording once normalised) share one row.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class KnowledgeGap(Base):
    __tablename__ = "knowledge_gaps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)  # latest wording
    normalized: Mapped[str] = mapped_column(String(300), nullable=False)
    times_asked: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="OPEN")  # OPEN | RESOLVED | DISMISSED
    last_asked_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    first_asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "normalized", name="uq_knowledge_gaps_tenant_question"),
        Index("ix_knowledge_gaps_tenant_status", "tenant_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeGap id={self.id} status={self.status} times={self.times_asked}>"
