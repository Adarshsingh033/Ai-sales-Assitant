"""
Per-tenant AI token budget (PRD §48). Optional: tenants without `monthly_token_limit` are unlimited.
Usage totals are cached for a short TTL so the check adds no query to most turns.
"""
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_usage_log import AIUsageLog

_TTL_SECONDS = 30.0
_cache: Dict[uuid.UUID, Tuple[float, int]] = {}


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def tokens_used_this_month(db: AsyncSession, tenant_id: uuid.UUID, *, use_cache: bool = True) -> int:
    cached = _cache.get(tenant_id)
    if use_cache and cached and time.monotonic() - cached[0] < _TTL_SECONDS:
        return cached[1]
    used = (
        await db.execute(
            select(func.coalesce(func.sum(AIUsageLog.total_tokens), 0)).where(
                AIUsageLog.tenant_id == tenant_id, AIUsageLog.created_at >= _month_start()
            )
        )
    ).scalar_one()
    _cache[tenant_id] = (time.monotonic(), int(used))
    return int(used)


def note_usage(tenant_id: uuid.UUID, tokens: int) -> None:
    """Keep the cached total roughly current between refreshes."""
    cached = _cache.get(tenant_id)
    if cached:
        _cache[tenant_id] = (cached[0], cached[1] + tokens)


async def check_quota(db: AsyncSession, tenant_id: uuid.UUID, config: Any) -> Dict[str, Any]:
    """Returns {"limit", "used", "exceeded", "warning"}."""
    limit = getattr(config, "monthly_token_limit", None)
    if not limit:
        return {"limit": None, "used": None, "exceeded": False, "warning": False}
    used = await tokens_used_this_month(db, tenant_id)
    return {"limit": limit, "used": used, "exceeded": used >= limit, "warning": used >= 0.8 * limit}


def reset_cache() -> None:
    _cache.clear()
