"""
AI Sales Engine — public facade over the conversation orchestrator.

Routers and channel adapters call this module; the implementation lives in `app.services.ai`
(LLM gateway, RAG, tools, guardrails, analysis, memory). See `ai/orchestrator.py` for the turn pipeline.
"""
import asyncio
import logging
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai import prompts
from app.services.ai.orchestrator import ConversationOrchestrator, TurnResult

logger = logging.getLogger(__name__)

_STREAM_END = object()


class AISalesEngine:
    @classmethod
    async def process_customer_message(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message_text: str,
        *,
        analysis_inline: bool = True,
        trigger: str = "INBOUND",
    ) -> Dict[str, Any]:
        """Handle one customer message end-to-end and return the reply plus lead intelligence."""
        result = await ConversationOrchestrator.handle_message(
            db,
            tenant_id=tenant_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            user_message_text=user_message_text,
            trigger=trigger,
            analysis_inline=analysis_inline,
        )
        if result.pending:  # caller opted out of inline analysis but has no delivery step: finish it now
            outcome = await ConversationOrchestrator.post_process_turn(db, **result.pending)
            result.intent, result.score = outcome["intent"], outcome["score"]
            result.score_reason, result.lead_id = outcome["score_reason"], outcome["lead_id"]
            result.needs_human_handoff = outcome["needs_human_handoff"]
        return result.as_dict()

    @classmethod
    async def stream_message(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message_text: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Async generator of SSE payloads: {"token": "..."} per token, then one {"done": True, ...} event whose
        `reply` is the final, guardrail-checked text (the UI replaces the streamed draft with it).
        """
        queue: "asyncio.Queue[Any]" = asyncio.Queue()

        async def on_token(token: str) -> None:
            await queue.put({"token": token})

        async def run() -> None:
            try:
                result: TurnResult = await ConversationOrchestrator.handle_message(
                    db,
                    tenant_id=tenant_id,
                    customer_id=customer_id,
                    conversation_id=conversation_id,
                    user_message_text=user_message_text,
                    trigger="SIMULATOR",
                    on_token=on_token,
                    analysis_inline=True,
                )
                await queue.put({"done": True, "session_id": str(conversation_id), **result.as_dict()})
            except Exception as exc:  # the customer must still get a useful answer
                logger.exception("Streaming turn failed: %s", exc)
                await queue.put(
                    {
                        "done": True,
                        "session_id": str(conversation_id),
                        "reply": prompts.FALLBACK_REPLY,
                        "intent": "general_inquiry",
                        "score": 0,
                        "score_reason": None,
                        "needs_human_handoff": False,
                        "lead_id": None,
                    }
                )
            finally:
                await queue.put(_STREAM_END)

        task = asyncio.create_task(run())
        try:
            while True:
                event = await queue.get()
                if event is _STREAM_END:
                    break
                yield event
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
