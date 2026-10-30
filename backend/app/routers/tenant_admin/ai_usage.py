"""
Tenant Admin AI Usage & Cost Monitoring API Router.
Provides analytics on token burn, latency, and cost savings.
"""
from typing import Any, Dict
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_tenant_admin
from app.models.user import User
from app.services.ai_usage_tracker import AIUsageTracker

router = APIRouter(prefix="/tenant-admin/ai-usage", tags=["Tenant Admin - AI Usage"])


@router.get("/stats", summary="Get AI Usage & Token Statistics")
async def get_ai_usage_stats(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> Dict[str, Any]:
    """
    Returns total tokens, prompt/completion breakdown, estimated cost savings, and top models used.
    """
    return await AIUsageTracker.get_tenant_usage_stats(
        db=db,
        tenant_id=current_user.tenant_id,
        days=days,
    )
