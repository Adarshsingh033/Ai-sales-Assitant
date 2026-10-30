"""
Channel plumbing models.

ChannelBinding — pins an external chat (e.g. a Telegram chat id) to one tenant, set via the deep link
                 `t.me/<bot>?start=<tenant-slug>` so a single shared bot serves many companies.
InboundEvent   — idempotency ledger: one row per provider message id, so webhook retries never
                 produce a second AI reply.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class ChannelBinding(Base):
    __tablename__ = "channel_bindings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)  # TELEGRAM
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_channel_binding_external"),)


class InboundEvent(Base):
    __tablename__ = "inbound_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_inbound_event_external"),)
