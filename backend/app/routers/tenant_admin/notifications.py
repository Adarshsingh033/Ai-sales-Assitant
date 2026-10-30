"""
Tenant Admin & Sales Notifications API Router.
Handles in-app notification queries, unread counts, and mark-as-read actions.
"""
from typing import Any, Dict, List
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_sales_user
from app.models.user import User
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/tenant-admin/notifications", tags=["Tenant Admin - Notifications"])


@router.get("", summary="List Notifications")
async def list_notifications(
    limit: int = Query(25, ge=1, le=100),
    unread_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> List[Dict[str, Any]]:
    """List recent in-app notifications for the user's tenant."""
    items = await NotificationService.list_notifications(
        db=db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        limit=limit,
        unread_only=unread_only,
    )
    return [
        {
            "id": str(n.id),
            "title": n.title,
            "message": n.message,
            "type": n.type,
            "link_url": n.link_url,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat(),
        }
        for n in items
    ]


@router.get("/unread-count", summary="Get Unread Notification Count")
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> Dict[str, int]:
    """Get count of unread notifications for active user badge."""
    count = await NotificationService.get_unread_count(
        db=db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )
    return {"unread_count": count}


@router.post("/{notification_id}/read", summary="Mark Notification As Read")
async def mark_notification_read(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> Dict[str, bool]:
    """Mark a specific notification as read."""
    success = await NotificationService.mark_as_read(
        db=db,
        tenant_id=current_user.tenant_id,
        notification_id=notification_id,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"success": True}


@router.post("/read-all", summary="Mark All Notifications As Read")
async def mark_all_notifications_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> Dict[str, Any]:
    """Mark all unread notifications as read for current user."""
    updated = await NotificationService.mark_all_as_read(
        db=db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )
    return {"success": True, "marked_count": updated}
