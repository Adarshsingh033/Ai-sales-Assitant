"""
AI Usage & Token Metrics Tracking Service.
Records token usage and latency per tenant and aggregates analytics.
"""
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_usage_log import AIUsageLog

logger = logging.getLogger(__name__)

# GPT-4o reference baseline for cost-saving calculations ($5.00 / 1M prompt, $15.00 / 1M completion)
BASELINE_COST_PER_M_TOKENS = 7.50


class AIUsageTracker:
    @staticmethod
    async def log_usage(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        model: str,
        action_type: str = "SALES_CHAT",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        user_id: Optional[uuid.UUID] = None,
        customer_id: Optional[uuid.UUID] = None,
    ) -> Optional[AIUsageLog]:
        """Record token usage and estimated cost for an AI completion."""
        try:
            total_tokens = prompt_tokens + completion_tokens
            # Estimated commercial value saved by using Groq free tier / open weights
            estimated_cost = (total_tokens / 1_000_000.0) * BASELINE_COST_PER_M_TOKENS

            log = AIUsageLog(
                tenant_id=tenant_id,
                model=model,
                action_type=action_type,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=round(estimated_cost, 5),
                latency_ms=latency_ms,
                user_id=user_id,
                customer_id=customer_id,
            )
            db.add(log)
            await db.flush()
            return log
        except Exception as exc:
            logger.warning("Failed to log AI usage metric: %s", exc)
            return None

    @staticmethod
    async def get_tenant_usage_stats(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Aggregate total tokens, prompt/completion breakdown, estimated savings, and daily trends."""
        since = datetime.now(timezone.utc) - timedelta(days=days)

        stmt = select(
            func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.estimated_cost_usd), 0.0),
            func.coalesce(func.avg(AIUsageLog.latency_ms), 0.0),
            func.count(AIUsageLog.id),
        ).where(
            AIUsageLog.tenant_id == tenant_id,
            AIUsageLog.created_at >= since,
        )

        row = (await db.execute(stmt)).first()
        total_tokens, prompt_tokens, completion_tokens, total_cost, avg_latency, request_count = row

        # Action breakdown
        stmt_action = (
            select(
                AIUsageLog.action_type,
                func.count(AIUsageLog.id).label("count"),
                func.sum(AIUsageLog.total_tokens).label("tokens"),
            )
            .where(
                AIUsageLog.tenant_id == tenant_id,
                AIUsageLog.created_at >= since,
            )
            .group_by(AIUsageLog.action_type)
        )
        action_rows = (await db.execute(stmt_action)).all()
        by_action = [
            {"action": r[0], "requests": r[1], "tokens": r[2] or 0}
            for r in action_rows
        ]

        # Model breakdown
        stmt_model = (
            select(
                AIUsageLog.model,
                func.count(AIUsageLog.id).label("count"),
                func.sum(AIUsageLog.total_tokens).label("tokens"),
            )
            .where(
                AIUsageLog.tenant_id == tenant_id,
                AIUsageLog.created_at >= since,
            )
            .group_by(AIUsageLog.model)
        )
        model_rows = (await db.execute(stmt_model)).all()
        by_model = [
            {"model": r[0], "requests": r[1], "tokens": r[2] or 0}
            for r in model_rows
        ]

        return {
            "timeframe_days": days,
            "total_tokens": int(total_tokens),
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "estimated_savings_usd": round(float(total_cost), 2),
            "avg_latency_ms": round(float(avg_latency), 0),
            "total_requests": int(request_count),
            "by_action": by_action,
            "by_model": by_model,
        }
