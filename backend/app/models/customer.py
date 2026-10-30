"""
Customer ORM Model.
Stores prospective buyers and contact details across communication channels.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class Customer(Base):
    __tablename__ = "customers"

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

    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    preferred_language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Set when the customer asks to stop; blocks every automated outbound message.
    do_not_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        Index("ix_customers_tenant_phone", "tenant_id", "phone"),
        Index("ix_customers_tenant_email", "tenant_id", "email"),
    )

    def __repr__(self) -> str:
        return f"<Customer id={self.id} name={self.name} phone={self.phone}>"
