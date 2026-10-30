"""
Customer Memory ORM Model.
Long-lived, structured facts about a customer (company, needs, preferences, products discussed)
that the assistant carries across conversations. Wiped with the customer (ON DELETE CASCADE).
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class CustomerMemory(Base):
    __tablename__ = "customer_memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    facts: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<CustomerMemory customer={self.customer_id}>"
