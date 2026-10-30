"""
Automated Follow-Up Service.
Schedules, tracks, and dispatches proactive re-engagement messages for leads,
leveraging the AI Sales Engine to generate context-specific nurturing follow-ups.
"""
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.follow_up import FollowUpSchedule
from app.models.lead import Lead
from app.services.groq_client import GroqClient
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class FollowUpService:
    @staticmethod
    async def schedule_followup_if_eligible(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: Optional[uuid.UUID] = None,
        delay_hours: Optional[int] = None,
        trigger_reason: str = "INACTIVITY_24H",
    ) -> Optional[FollowUpSchedule]:
        """
        Check tenant config and schedule an automated follow-up if enabled and not already pending.
        """
        stmt_cfg = select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant_id)
        config = (await db.execute(stmt_cfg)).scalar_one_or_none()
        if not config or not config.auto_followup_enabled:
            return None

        hours = delay_hours if delay_hours is not None else config.followup_delay_hours
        scheduled_time = datetime.now(timezone.utc) + timedelta(hours=hours)

        # Check if active pending follow-up already exists for this lead
        stmt_pending = select(FollowUpSchedule).where(
            FollowUpSchedule.tenant_id == tenant_id,
            FollowUpSchedule.lead_id == lead_id,
            FollowUpSchedule.status == "PENDING",
        )
        existing = (await db.execute(stmt_pending)).scalar_one_or_none()
        if existing:
            # Reschedule/bump
            existing.scheduled_at = scheduled_time
            await db.commit()
            return existing

        sched = FollowUpSchedule(
            tenant_id=tenant_id,
            lead_id=lead_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            trigger_reason=trigger_reason,
            scheduled_at=scheduled_time,
            status="PENDING",
            attempt_number=1,
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        logger.info("Scheduled follow-up for lead %s at %s", lead_id, scheduled_time)
        return sched

    @staticmethod
    async def list_schedules(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List follow-up schedules with lead details."""
        stmt = (
            select(FollowUpSchedule)
            .options(
                selectinload(FollowUpSchedule.lead),
                selectinload(FollowUpSchedule.customer),
            )
            .where(FollowUpSchedule.tenant_id == tenant_id)
        )
        if status:
            stmt = stmt.where(FollowUpSchedule.status == status)

        stmt = stmt.order_by(FollowUpSchedule.scheduled_at.asc()).limit(limit)
        items = (await db.execute(stmt)).scalars().all()

        results = []
        for it in items:
            results.append({
                "id": str(it.id),
                "lead_id": str(it.lead_id),
                "lead_title": it.lead.title if it.lead else "Lead",
                "lead_score": it.lead.score if it.lead else 0,
                "customer_name": it.customer.name if it.customer else "Customer",
                "customer_phone": it.customer.phone if it.customer else "N/A",
                "trigger_reason": it.trigger_reason,
                "scheduled_at": it.scheduled_at.isoformat(),
                "status": it.status,
                "attempt_number": it.attempt_number,
                "custom_message": it.custom_message,
                "executed_at": it.executed_at.isoformat() if it.executed_at else None,
            })
        return results

    @staticmethod
    async def cancel_schedule(db: AsyncSession, tenant_id: uuid.UUID, schedule_id: uuid.UUID) -> bool:
        """Cancel a pending follow-up."""
        stmt = (
            update(FollowUpSchedule)
            .where(
                FollowUpSchedule.id == schedule_id,
                FollowUpSchedule.tenant_id == tenant_id,
                FollowUpSchedule.status == "PENDING",
            )
            .values(status="CANCELLED")
        )
        res = await db.execute(stmt)
        await db.commit()
        return res.rowcount > 0

    @staticmethod
    async def process_due_followups(db: AsyncSession, tenant_id: uuid.UUID) -> int:
        """
        Finds all PENDING follow-ups whose scheduled_at <= now,
        generates tailored nurturing messages via AI, appends them to conversation,
        and triggers sales notifications.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(FollowUpSchedule)
            .options(
                selectinload(FollowUpSchedule.lead),
                selectinload(FollowUpSchedule.customer),
            )
            .where(
                FollowUpSchedule.tenant_id == tenant_id,
                FollowUpSchedule.status == "PENDING",
                FollowUpSchedule.scheduled_at <= now,
            )
        )
        due_items = (await db.execute(stmt)).scalars().all()
        dispatched_count = 0

        for item in due_items:
            try:
                lead = item.lead
                cust = item.customer
                conv_id = item.conversation_id

                lead_reqs = lead.requirements if lead and lead.requirements else "our services"
                cust_name = cust.name if cust and cust.name else "there"

                # Draft helpful follow-up
                follow_up_text = (
                    f"Hi {cust_name}! Just following up to see if you have any questions regarding {lead_reqs}. "
                    "I would be glad to arrange a customized quote or quick demo whenever convenient for you!"
                )

                if conv_id:
                    # Save into conversation messages
                    msg = Message(
                        conversation_id=conv_id,
                        sender_type="AI",
                        content=follow_up_text,
                    )
                    db.add(msg)

                    # Update conversation last activity
                    stmt_conv = select(Conversation).where(Conversation.id == conv_id)
                    conv = (await db.execute(stmt_conv)).scalar_one_or_none()
                    if conv:
                        conv.last_activity_at = now

                item.status = "DISPATCHED"
                item.executed_at = now
                item.custom_message = follow_up_text
                await db.commit()
                dispatched_count += 1

                # Alert the assigned rep
                rep_id = lead.assigned_user_id if lead else None
                await NotificationService.create_notification(
                    db=db,
                    tenant_id=tenant_id,
                    title=f"📩 Automated Follow-Up Dispatched: {lead.title if lead else cust_name}",
                    message=f"Sent re-engagement message to {cust_name}.",
                    notif_type="FOLLOWUP_DUE",
                    recipient_user_id=rep_id,
                    link_url=f"#/conversations?id={conv_id}" if conv_id else "#/leads",
                )

            except Exception as exc:
                logger.error("Error processing follow-up item %s: %s", item.id, exc)

        return dispatched_count
