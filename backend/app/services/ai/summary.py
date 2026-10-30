"""
Conversation summaries (PRD §26): a deterministic hand-off brief that is always available instantly, and an
LLM-refined rolling summary that doubles as long-conversation memory for the prompt.
"""
import logging
from typing import List, Optional, Tuple

from app.models.customer import Customer
from app.models.lead import Lead
from app.services.ai.analysis import TurnAnalysis
from app.services.ai.llm import LLMError, LLMResult

logger = logging.getLogger(__name__)


def build_brief(
    customer: Customer,
    analysis: TurnAnalysis,
    lead: Optional[Lead],
    score: int,
    handoff_reason: Optional[str] = None,
) -> str:
    who = customer.name or customer.phone or "Customer"
    if customer.company_name:
        who += f" ({customer.company_name})"
    budget = (lead.budget if lead and lead.budget else analysis.budget)
    timeline = (lead.timeline if lead and lead.timeline else analysis.timeline)
    lines = [
        f"Customer: {who}",
        f"Requirement: {(lead.requirements if lead and lead.requirements else analysis.requirements) or 'Not stated yet'}",
        f"Budget: {budget:g}" if budget else "Budget: Not stated",
        f"Timeline: {timeline or 'Not stated'}",
        f"Interested products: {', '.join(analysis.products_mentioned) or 'Not identified yet'}",
        f"Main concern: {', '.join(analysis.objections) or 'None raised'}",
        f"Intent: {analysis.intent.replace('_', ' ')}",
        f"Lead score: {score}",
        f"Recommended action: {analysis.recommended_action or 'Follow up and confirm requirements'}",
    ]
    if handoff_reason:
        lines.append(f"Handoff reason: {handoff_reason}")
    return "\n".join(lines)


_SUMMARY_PROMPT = """Update the running summary of a sales conversation. Keep it under 120 words, factual, in English, using exactly
these lines: Customer, Requirement, Budget, Timeline, Interested products, Main concern, Lead score, Recommended action.
Use "Not stated" when unknown. Do not invent facts.

PREVIOUS SUMMARY:
{previous}

NEW MESSAGES:
{messages}

LEAD SCORE: {score}

Return only the updated summary."""


async def refresh_summary(
    llm,
    *,
    previous: Optional[str],
    messages: List[Tuple[str, str]],
    score: int,
) -> Tuple[Optional[str], Optional[LLMResult]]:
    text = "\n".join(
        f"{'Customer' if who == 'CUSTOMER' else ('Sales rep' if who == 'HUMAN_AGENT' else 'Assistant')}: {content}"
        for who, content in messages
    )
    try:
        res = await llm.complete(
            [{"role": "user", "content": _SUMMARY_PROMPT.format(previous=previous or "(none)", messages=text[:6000], score=score)}],
            temperature=0.1,
            max_tokens=400,
        )
        return (res.content.strip() or previous), res
    except LLMError as exc:
        logger.warning("Summary refresh failed: %s", exc)
        return previous, None
