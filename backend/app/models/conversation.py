"""
Conversation & Message ORM Models.
Maintains omnichannel conversation history, live human takeover states, and messages.
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


class Conversation(Base):
    __tablename__ = "conversations"

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
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="WHATSAPP")  # WHATSAPP, WEB_SIMULATOR
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ACTIVE_AI"
    )  # ACTIVE_AI, WAITING_SALES, ESCALATED_HUMAN, RESOLVED

    assigned_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Number of messages already folded into `ai_summary` (rolling summary / long-conversation memory)
    summary_message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    handoff_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detected_intent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    customer: Mapped["Customer"] = relationship("Customer")
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    __table_args__ = (
        Index("ix_conversations_tenant_status", "tenant_id", "status"),
        Index("ix_conversations_tenant_activity", "tenant_id", "last_activity_at"),
    )

    def __repr__(self) -> str:
        return f"<Conversation id={self.id} channel={self.channel} status={self.status}>"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sender_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # CUSTOMER, AI, HUMAN_AGENT
    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="messages",
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} sender={self.sender_type}>"
