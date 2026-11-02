"""
Lead synchronisation from analysed turns: create/update the Lead, apply tenant thresholds, round-robin assign,
and (re)arm the follow-up timer. Scoring itself lives in `analysis.score_lead`.
"""
import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.lead import Lead
from app.services.ai.analysis import TurnAnalysis, capture_threshold, priority_for, timeline_label
from app.services.crm import log_activity

logger = logging.getLogger(__name__)

_SOURCE_BY_CHANNEL = {"WHATSAPP": "WHATSAPP", "TELEGRAM": "TELEGRAM", "WEB_SIMULATOR": "WEB_CHAT"}


async def get_lead(db: AsyncSession, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> Optional[Lead]:
    return (
        await db.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.conversation_id == conversation_id))
    ).scalar_one_or_none()


def should_capture(score: int, analysis: TurnAnalysis, config: AIAssistantConfig, has_lead: bool) -> bool:
    if has_lead:
        return True
    if analysis.out_of_scope:
        return False
    return score >= capture_threshold(config.qualification_threshold) or analysis.buying_intent


async def sync_lead(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    customer: Customer,
    conversation: Conversation,
    config: AIAssistantConfig,
    analysis: TurnAnalysis,
    score: int,
    reason: str,
    lead: Optional[Lead],
) -> Lead:
    threshold = config.qualification_threshold
    priority = priority_for(score, threshold)
    created = lead is None

    if lead is None:
        name = customer.name or customer.phone or "New Lead"
        lead = Lead(
            tenant_id=tenant_id,
            customer_id=customer.id,
            conversation_id=conversation.id,
            title=f"Opportunity - {name}",
            status="QUALIFIED" if score >= threshold else "NEW",
            score=score,
            score_reason=reason,
            requirements=analysis.requirements,
            budget=analysis.budget,
            timeline=timeline_label(analysis.timeline),
            priority=priority,
            source=_SOURCE_BY_CHANNEL.get(conversation.channel, conversation.channel),
        )
        db.add(lead)
    else:
        lead.score = score
        lead.score_reason = reason
        lead.priority = priority
        if analysis.requirements:
            lead.requirements = analysis.requirements
        if analysis.budget:
            lead.budget = analysis.budget
        if analysis.timeline:
            lead.timeline = timeline_label(analysis.timeline)
        if score >= threshold and lead.status == "NEW":
            lead.status = "QUALIFIED"
    await db.flush()
    if created:
        log_activity(db, tenant_id=tenant_id, kind="AI", text=f"AI captured this lead (score {score}/100)",
                     customer_id=customer.id, lead_id=lead.id)

    if not lead.assigned_user_id:
        from app.services.lead_assignment_service import LeadAssignmentService

        rep = await LeadAssignmentService.assign_lead_round_robin(
            db=db,
            tenant_id=tenant_id,
            lead_id=lead.id,
            conversation_id=conversation.id,
            trigger_notification=True,
        )
        if rep:
            lead.assigned_user_id = rep.id

    from app.services.follow_up_service import FollowUpService

    if not customer.do_not_contact and lead.status not in ("WON", "LOST"):
        await FollowUpService.schedule_followup_if_eligible(
            db=db,
            tenant_id=tenant_id,
            lead_id=lead.id,
            customer_id=customer.id,
            conversation_id=conversation.id,
            channel=conversation.channel,
            trigger_reason="INACTIVITY_24H",
        )
    return lead
