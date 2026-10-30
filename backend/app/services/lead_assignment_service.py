"""
Lead Assignment Service.
Handles automated round-robin lead and conversation distribution among active sales representatives.
"""
from datetime import datetime, timezone
import logging
from typing import Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.user import User, UserRole
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class LeadAssignmentService:
    @staticmethod
    async def assign_lead_round_robin(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        conversation_id: Optional[uuid.UUID] = None,
        trigger_notification: bool = True,
    ) -> Optional[User]:
        """
        Finds the least-recently-assigned active and available sales user in the tenant,
        assigns the lead and conversation, updates the timestamp, and notifies the rep.
        """
        # Check if round-robin is enabled in AI assistant config
        stmt_cfg = select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant_id)
        config = (await db.execute(stmt_cfg)).scalar_one_or_none()
        if config and not config.round_robin_assignment:
            logger.info("Round-robin assignment is disabled for tenant %s", tenant_id)
            return None

        # Fetch candidate reps (SALES_USER or TENANT_ADMIN, is_active=True, is_available=True)
        stmt_reps = (
            select(User)
            .where(
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
                User.is_available.is_(True),
                User.role.in_([UserRole.SALES_USER, UserRole.TENANT_ADMIN]),
            )
            .order_by(User.last_lead_assigned_at.asc().nullsfirst(), User.created_at.asc())
        )
        reps = (await db.execute(stmt_reps)).scalars().all()
        if not reps:
            logger.warning("No active sales representatives available for round-robin assignment in tenant %s", tenant_id)
            return None

        chosen_rep = reps[0]
        now = datetime.now(timezone.utc)

        # Update lead assignment
        stmt_lead = select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        lead = (await db.execute(stmt_lead)).scalar_one_or_none()
        if lead:
            lead.assigned_user_id = chosen_rep.id

        # Update conversation assignment if provided
        if conversation_id:
            stmt_conv = select(Conversation).where(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
            conv = (await db.execute(stmt_conv)).scalar_one_or_none()
            if conv:
                conv.assigned_user_id = chosen_rep.id

        # Bump rep's last assignment timestamp
        chosen_rep.last_lead_assigned_at = now
        await db.commit()

        logger.info(
            "Lead %s round-robin assigned to rep %s (%s)",
            lead_id, chosen_rep.id, chosen_rep.email
        )

        # Trigger notification if requested
        if trigger_notification and lead:
            notif_title = f"🎯 New Lead Assigned: {lead.title}"
            priority_tag = f"[{lead.priority}] " if lead.priority else ""
            notif_msg = f"{priority_tag}Lead score: {lead.score}/100. Requirements: {lead.requirements or 'General inquiry'}."
            await NotificationService.create_notification(
                db=db,
                tenant_id=tenant_id,
                title=notif_title,
                message=notif_msg,
                notif_type="HOT_LEAD" if (lead.score >= 70 or lead.priority == "HOT") else "GENERAL",
                recipient_user_id=chosen_rep.id,
                link_url=f"#/leads?id={lead.id}",
            )

        return chosen_rep
