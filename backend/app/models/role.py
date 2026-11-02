"""
Role — a named job title an admin creates for their team ("Sales Rep", "Support", "Manager", ...).

A role does not carry its own permission system: `access` maps it onto the platform's two working levels
(SALES = sees only their own deals/chats/tasks, ADMIN = full workspace) and `receives_deals` says whether people with
this role are in the automatic round-robin. Every tenant has the built-in roles "Sales Rep" and "Admin".
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

ACCESS_SALES = "SALES"
ACCESS_ADMIN = "ADMIN"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    access: Mapped[str] = mapped_column(String(8), nullable=False)  # SALES | ADMIN; fixed once created
    receives_deals: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # built-in: cannot be edited or deleted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("uq_roles_tenant_name", "tenant_id", text("lower(name)"), unique=True),)

    def __repr__(self) -> str:
        return f"<Role {self.name} access={self.access}>"
