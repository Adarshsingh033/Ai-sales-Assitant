"""
Notification Service.
Dispatches in-app bell alerts and browser notification events for Hot Leads,
Human Takeovers, Low Stock, and Follow-up reminders.
"""
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification

logger = logging.getLogger(__name__)


class NotificationService:
    @staticmethod
    async def create_notification(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        title: str,
        message: str,
        notif_type: str = "GENERAL",
        recipient_user_id: Optional[uuid.UUID] = None,
        link_url: Optional[str] = None,
    ) -> Notification:
        """Create an in-app notification record."""
        notif = Notification(
            tenant_id=tenant_id,
            recipient_user_id=recipient_user_id,
            title=title,
            message=message,
            type=notif_type,
            link_url=link_url,
            is_read=False,
        )
        db.add(notif)
        await db.commit()
        await db.refresh(notif)
        logger.info("Notification created [%s]: %s (tenant=%s)", notif_type, title, tenant_id)
        return notif

    @staticmethod
    async def get_unread_count(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
    ) -> int:
        """Get count of unread notifications for a specific user or broadcast to tenant."""
        stmt = select(func.count(Notification.id)).where(
            Notification.tenant_id == tenant_id,
            Notification.is_read.is_(False),
        )
        if user_id:
            stmt = stmt.where(
                (Notification.recipient_user_id == user_id) | (Notification.recipient_user_id.is_(None))
            )
        return (await db.execute(stmt)).scalar_one() or 0

    @staticmethod
    async def list_notifications(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
        limit: int = 25,
        unread_only: bool = False,
    ) -> List[Notification]:
        """List recent notifications ordered newest first."""
        stmt = select(Notification).where(Notification.tenant_id == tenant_id)
        if user_id:
            stmt = stmt.where(
                (Notification.recipient_user_id == user_id) | (Notification.recipient_user_id.is_(None))
            )
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))

        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def mark_as_read(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        notification_id: uuid.UUID,
    ) -> bool:
        """Mark a single notification as read."""
        stmt = (
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.tenant_id == tenant_id,
            )
            .values(is_read=True)
        )
        res = await db.execute(stmt)
        await db.commit()
        return res.rowcount > 0

    @staticmethod
    async def mark_all_as_read(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
    ) -> int:
        """Mark all notifications as read for a user/tenant."""
        stmt = (
            update(Notification)
            .where(
                Notification.tenant_id == tenant_id,
                Notification.is_read.is_(False),
            )
        )
        if user_id:
            stmt = stmt.where(
                (Notification.recipient_user_id == user_id) | (Notification.recipient_user_id.is_(None))
            )
        stmt = stmt.values(is_read=True)
        res = await db.execute(stmt)
        await db.commit()
        return res.rowcount
