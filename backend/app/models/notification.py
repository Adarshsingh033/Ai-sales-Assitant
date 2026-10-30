"""
In-App and Web Push Notification ORM Model.
Stores notifications for hot leads, human takeover requests, inventory warnings, and follow-ups.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class Notification(Base):
    __tablename__ = "notifications"

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

    # If recipient_user_id is None, it is a broadcast alert for all active sales reps / admins in tenant
    recipient_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="HOT_LEAD"
    )  # HOT_LEAD, HUMAN_TAKEOVER_REQUESTED, FOLLOWUP_DUE, INVENTORY_LOW, GENERAL
    link_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_notifications_tenant_read", "tenant_id", "is_read"),
        Index("ix_notifications_tenant_user_read", "tenant_id", "recipient_user_id", "is_read"),
        Index("ix_notifications_tenant_created", "tenant_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Notification id={self.id} type={self.type} title={self.title[:30]}>"
