"""
Automated Follow-Up Schedule ORM Model.
Tracks scheduled re-engagement messages for inactive or qualifying leads.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class FollowUpSchedule(Base):
    __tablename__ = "follow_up_schedules"

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
        index=True,
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )

    trigger_reason: Mapped[str] = mapped_column(
        String(50), nullable=False, default="INACTIVITY_24H"
    )  # INACTIVITY_24H, PENDING_QUALIFICATION, PROPOSAL_FOLLOWUP
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING"
    )  # PENDING, DISPATCHED, CANCELLED, RESPONDED

    custom_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    lead = relationship("Lead", lazy="selectin")
    customer = relationship("Customer", lazy="selectin")

    __table_args__ = (
        Index("ix_follow_up_tenant_status_sched", "tenant_id", "status", "scheduled_at"),
        Index("ix_follow_up_tenant_lead", "tenant_id", "lead_id"),
    )

    def __repr__(self) -> str:
        return f"<FollowUpSchedule id={self.id} lead_id={self.lead_id} status={self.status}>"
