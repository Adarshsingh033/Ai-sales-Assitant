"""
Tenant Admin AI Usage, Cost & Observability API Router.
Token burn / latency / cost analytics, the monthly budget, and per-turn AI traces (PRD §48, §50).
"""
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_tenant_admin
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.ai_trace import AITrace
from app.models.user import User
from app.services.ai import quota
from app.services.ai.vector_store import active_backend
from app.services.ai_usage_tracker import AIUsageTracker

router = APIRouter(prefix="/tenant-admin/ai-usage", tags=["Tenant Admin - AI Usage"])


def _trace_summary(t: AITrace) -> Dict[str, Any]:
    return {
        "id": str(t.id),
        "conversation_id": str(t.conversation_id) if t.conversation_id else None,
        "trigger": t.trigger,
        "model": t.model,
        "prompt_tokens": t.prompt_tokens,
        "completion_tokens": t.completion_tokens,
        "latency_ms": t.latency_ms,
        "user_message": t.user_message,
        "reply": t.reply,
        "handoff_reason": t.handoff_reason,
        "error": t.error,
        "tools_used": [c.get("name") for c in (t.tool_calls or [])],
        "guardrail_issues": [g.get("code") for g in (t.guardrails or [])],
        "created_at": t.created_at.isoformat(),
    }


@router.get("/stats", summary="Get AI Usage & Token Statistics")
async def get_ai_usage_stats(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> Dict[str, Any]:
    """Total tokens, prompt/completion breakdown, estimated cost savings, top models, and the monthly budget."""
    stats = await AIUsageTracker.get_tenant_usage_stats(db=db, tenant_id=current_user.tenant_id, days=days)
    config = (
        await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == current_user.tenant_id))
    ).scalar_one_or_none()
    stats["monthly_budget"] = await quota.check_quota(db, current_user.tenant_id, config) if config else None
    stats["vector_backend"] = active_backend()
    return stats


@router.get("/traces", summary="Recent AI turn traces")
async def list_traces(
    limit: int = Query(25, ge=1, le=100),
    only_issues: bool = Query(False, description="Only turns with an error, guardrail action or handoff"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> List[Dict[str, Any]]:
    stmt = select(AITrace).where(AITrace.tenant_id == current_user.tenant_id).order_by(AITrace.created_at.desc())
    rows = (await db.execute(stmt.limit(limit * 4 if only_issues else limit))).scalars().all()
    if only_issues:
        rows = [t for t in rows if t.error or t.guardrails or t.handoff_reason][:limit]
    return [_trace_summary(t) for t in rows]


@router.get("/traces/{trace_id}", summary="Full detail of one AI turn (retrieval, tools, guardrails, timings)")
async def get_trace(
    trace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> Dict[str, Any]:
    trace = (
        await db.execute(select(AITrace).where(AITrace.id == trace_id, AITrace.tenant_id == current_user.tenant_id))
    ).scalar_one_or_none()
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return {
        **_trace_summary(trace),
        "retrieval": trace.retrieval,
        "tool_calls": trace.tool_calls,
        "guardrails": trace.guardrails,
        "timings": trace.timings,
    }
