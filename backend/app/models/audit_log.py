import enum
import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy import DateTime, Enum, String, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


JSONVariant = JSON().with_variant(JSONB(), "postgresql")

class AuditLogAction(str, enum.Enum):
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    LOGIN_FAILED = "LOGIN_FAILED"

    TENANT_CREATED = "TENANT_CREATED"
    TENANT_UPDATED = "TENANT_UPDATED"
    TENANT_ACTIVATED = "TENANT_ACTIVATED"
    TENANT_DEACTIVATED = "TENANT_DEACTIVATED"
    TENANT_SUSPENDED = "TENANT_SUSPENDED"

    SUBSCRIPTION_PLAN_CREATED = "SUBSCRIPTION_PLAN_CREATED"
    SUBSCRIPTION_PLAN_UPDATED = "SUBSCRIPTION_PLAN_UPDATED"
    SUBSCRIPTION_PLAN_ACTIVATED = "SUBSCRIPTION_PLAN_ACTIVATED"
    SUBSCRIPTION_PLAN_DEACTIVATED = "SUBSCRIPTION_PLAN_DEACTIVATED"

    TENANT_SUBSCRIPTION_PLAN_CHANGED = "TENANT_SUBSCRIPTION_PLAN_CHANGED"

    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_ACTIVATED = "USER_ACTIVATED"
    USER_DEACTIVATED = "USER_DEACTIVATED"


class AuditLogResourceType(str, enum.Enum):
    TENANT = "TENANT"
    USER = "USER"
    SUBSCRIPTION_PLAN = "SUBSCRIPTION_PLAN"
    AUTHENTICATION = "AUTHENTICATION"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    action: Mapped[AuditLogAction] = mapped_column(
        Enum(AuditLogAction, name="audit_log_action_enum", create_constraint=True),
        nullable=False,
        index=True
    )
    
    resource_type: Mapped[AuditLogResourceType] = mapped_column(
        Enum(AuditLogResourceType, name="audit_log_resource_type_enum", create_constraint=True),
        nullable=False,
        index=True
    )
    
    resource_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    description: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    old_values: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONVariant, nullable=True)
    new_values: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONVariant, nullable=True)

    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    def __repr__(self) -> str:
        return f"<AuditLog action={self.action} resource_type={self.resource_type} user_id={self.user_id}>"
