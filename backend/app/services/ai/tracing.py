"""
AI observability (PRD §50): one AITrace row per turn plus real token/latency usage logging.
Trace writes are best-effort — observability must never break a customer conversation.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trace import AITrace
from app.services.ai import quota
from app.services.ai.llm import LLMResult
from app.services.ai_usage_tracker import AIUsageTracker

logger = logging.getLogger(__name__)


@dataclass
class TurnTrace:
    tenant_id: uuid.UUID
    conversation_id: Optional[uuid.UUID]
    customer_id: Optional[uuid.UUID]
    trigger: str = "INBOUND"
    user_message: Optional[str] = None
    started: float = field(default_factory=time.monotonic)
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retrieval: Dict[str, Any] = field(default_factory=dict)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    guardrails: List[Dict[str, Any]] = field(default_factory=list)
    timings: Dict[str, int] = field(default_factory=dict)
    handoff_reason: Optional[str] = None
    error: Optional[str] = None
    reply: Optional[str] = None
    usage_rows: List[Dict[str, Any]] = field(default_factory=list)

    def add_llm(self, result: LLMResult, action_type: str, *, count_in_trace: bool = True) -> None:
        if count_in_trace:
            self.model = result.model or self.model
            self.prompt_tokens += result.usage.prompt_tokens
            self.completion_tokens += result.usage.completion_tokens
        self.usage_rows.append(
            {
                "model": result.model or "unknown",
                "action_type": action_type,
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "latency_ms": result.latency_ms,
            }
        )

    def mark(self, name: str, since: float) -> None:
        self.timings[name] = int((time.monotonic() - since) * 1000)


async def persist_trace(db: AsyncSession, trace: TurnTrace) -> Optional[uuid.UUID]:
    """Write usage rows + the trace. Uses the caller's session; the caller commits."""
    try:
        for row in trace.usage_rows:
            await AIUsageTracker.log_usage(
                db=db, tenant_id=trace.tenant_id, customer_id=trace.customer_id, **row
            )
            quota.note_usage(trace.tenant_id, row["prompt_tokens"] + row["completion_tokens"])
        trace.timings["total"] = int((time.monotonic() - trace.started) * 1000)
        row = AITrace(
            tenant_id=trace.tenant_id,
            conversation_id=trace.conversation_id,
            customer_id=trace.customer_id,
            trigger=trace.trigger,
            model=trace.model,
            prompt_tokens=trace.prompt_tokens,
            completion_tokens=trace.completion_tokens,
            latency_ms=trace.timings.get("total", 0),
            user_message=(trace.user_message or "")[:2000] or None,
            reply=(trace.reply or "")[:4000] or None,
            retrieval=trace.retrieval or None,
            tool_calls=trace.tool_calls or None,
            guardrails=trace.guardrails or None,
            timings=trace.timings or None,
            handoff_reason=trace.handoff_reason,
            error=trace.error,
        )
        db.add(row)
        await db.flush()
        return row.id
    except Exception as exc:  # pragma: no cover - never fail a turn on telemetry
        logger.warning("Failed to persist AI trace: %s", exc)
        return None
