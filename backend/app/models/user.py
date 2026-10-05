"""
User ORM model — foundation for multi-tenant user management.

Roles:
  SUPER_ADMIN  — platform-level, tenant_id = NULL
  TENANT_ADMIN — tenant owner/manager
  SALES_USER   — frontline sales agent within a tenant

Future entities (Leads, Contacts, Opportunities, etc.) must reference tenant_id
to maintain tenant isolation.
"""
import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    TENANT_ADMIN = "TENANT_ADMIN"
    SALES_USER = "SALES_USER"


class User(Base):
    __tablename__ = "users"

    # ------------------------------------------------------------------
    # Primary key
    # ------------------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Multi-tenancy — NULL for SUPER_ADMIN (platform level)
    # ------------------------------------------------------------------
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    profile_image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------
    # Credentials — only the hash is stored, never plain-text
    # ------------------------------------------------------------------
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)

    # ------------------------------------------------------------------
    # Role — controlled enum, no free-text values allowed
    # ------------------------------------------------------------------
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum", create_constraint=True),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Sales availability for lead assignment; forced to False on deactivation
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ------------------------------------------------------------------
    # Session revocation — embedded in JWT as "tv"; bump to invalidate tokens
    # ------------------------------------------------------------------
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ------------------------------------------------------------------
    # Timestamps (UTC)
    # ------------------------------------------------------------------
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # ------------------------------------------------------------------
    # Constraints & indexes
    #
    # Email uniqueness: globally unique (case-insensitive) across all
    # tenants so login by email stays unambiguous. Enforced by the
    # functional unique index uq_users_email_lower in the Alembic migration.
    # ------------------------------------------------------------------
    __table_args__ = (
        # Fast lookup by email
        Index("ix_users_email", "email"),
        # Fast lookup within tenant
        Index("ix_users_tenant_id_email", "tenant_id", "email"),
        # Tenant-scoped user listing / filtering
        Index("ix_users_tenant_role_active", "tenant_id", "role", "is_active"),
        Index("ix_users_tenant_role_available", "tenant_id", "role", "is_available"),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"
