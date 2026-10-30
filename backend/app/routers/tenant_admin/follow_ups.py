"""
Tenant Admin Automated Follow-Up API Router.
Inspect pending follow-up schedules, cancel schedules, or manually trigger due checks.
"""
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_tenant_admin
from app.models.user import User
from app.services.follow_up_service import FollowUpService

router = APIRouter(prefix="/tenant-admin/follow-ups", tags=["Tenant Admin - Follow-Ups"])


@router.get("", summary="List Follow-Up Schedules")
async def list_follow_ups(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> List[Dict[str, Any]]:
    """List scheduled automated follow-up messages."""
    return await FollowUpService.list_schedules(
        db=db,
        tenant_id=current_user.tenant_id,
        status=status,
        limit=limit,
    )


@router.post("/process-due", summary="Process Due Follow-Ups Immediately")
async def process_due_followups(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> Dict[str, Any]:
    """Manually trigger background check to dispatch due follow-up messages."""
    dispatched = await FollowUpService.process_due_followups(
        db=db,
        tenant_id=current_user.tenant_id,
    )
    return {"success": True, "dispatched_count": dispatched}


@router.delete("/{schedule_id}", summary="Cancel Follow-Up Schedule")
async def cancel_followup(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> Dict[str, bool]:
    """Cancel a pending follow-up schedule."""
    cancelled = await FollowUpService.cancel_schedule(
        db=db,
        tenant_id=current_user.tenant_id,
        schedule_id=schedule_id,
    )
    if not cancelled:
        raise HTTPException(status_code=404, detail="Schedule not found or not in pending state.")
    return {"success": True}
