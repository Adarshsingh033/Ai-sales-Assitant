"""
Lead Assignment Service.
Handles automated round-robin lead and conversation distribution among active sales representatives.
"""
from datetime import datetime, timezone
import logging
from typing import Optional
import uuid

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation
from app.models.crm import Activity
from app.models.lead import Lead
from app.models.role import Role
from app.models.user import User, UserRole
from app.services.crm import log_activity, notify
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

SWEEP_BATCH = 100


class LeadAssignmentService:
    @staticmethod
    async def assign_lead_round_robin(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        conversation_id: Optional[uuid.UUID] = None,
        trigger_notification: bool = True,
        actor_id: Optional[uuid.UUID] = None,
    ) -> Optional[User]:
        """
        Finds the least-recently-assigned active and available sales rep in the tenant (never an admin),
        assigns the lead and conversation, updates the timestamp, and notifies the rep
        (unless the rep is `actor_id`, the person who just created the deal).
        """
        # Check if round-robin is enabled in AI assistant config
        stmt_cfg = select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant_id)
        config = (await db.execute(stmt_cfg)).scalar_one_or_none()
        if config and not config.round_robin_assignment:
            logger.info("Round-robin assignment is disabled for tenant %s", tenant_id)
            return None

        # Candidates are sales people only (sales-level access, active and available) whose role receives deals;
        # admins never get auto-assigned deals. Someone without an explicit role counts as a Sales Rep.
        stmt_reps = (
            select(User)
            .outerjoin(Role, Role.id == User.role_id)
            .where(
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
                User.is_available.is_(True),
                User.role == UserRole.SALES_USER,
                or_(User.role_id.is_(None), Role.receives_deals.is_(True)),
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

        # Update conversation assignment (given, or the deal's own chat)
        conversation_id = conversation_id or (lead.conversation_id if lead else None)
        if conversation_id:
            stmt_conv = select(Conversation).where(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
            conv = (await db.execute(stmt_conv)).scalar_one_or_none()
            if conv:
                conv.assigned_user_id = chosen_rep.id

        # Bump rep's last assignment timestamp
        chosen_rep.last_lead_assigned_at = now
        if lead:
            log_activity(
                db, tenant_id=tenant_id, kind="ASSIGNED", customer_id=lead.customer_id, lead_id=lead.id,
                text=f"Automatically assigned to {chosen_rep.first_name} {chosen_rep.last_name}".strip(),
            )
        await db.commit()

        logger.info(
            "Lead %s round-robin assigned to rep %s (%s)",
            lead_id, chosen_rep.id, chosen_rep.email
        )

        # Trigger notification if requested
        if trigger_notification and lead and chosen_rep.id != actor_id:
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
                link_url=f"#pipeline?lead={lead.id}",
            )

        return chosen_rep

    @staticmethod
    async def assign_unowned(
        db: AsyncSession, limit: int = SWEEP_BATCH, tenant_id: Optional[uuid.UUID] = None
    ) -> int:
        """
        Safety net run by the scheduler: give an owner to every open deal that never got one (created before
        auto-assignment existed, no rep was free at the time, a rep was removed, ...). Deals an admin deliberately
        unassigned are left alone: they carry an ASSIGNED entry on their timeline. Tenants with round-robin switched
        off, or with nobody available, are skipped. Each rep gets one summary notification, not one per deal.
        """
        stmt = (
            select(Lead)
            .where(
                Lead.assigned_user_id.is_(None),
                Lead.status.not_in(("WON", "LOST")),
                ~exists().where(Activity.lead_id == Lead.id, Activity.kind == "ASSIGNED"),
            )
            .order_by(Lead.created_at)
            .limit(limit)
        )
        if tenant_id is not None:
            stmt = stmt.where(Lead.tenant_id == tenant_id)
        waiting = [(l.tenant_id, l.id, l.title) for l in (await db.execute(stmt)).scalars().all()]
        stuck_tenants: set[uuid.UUID] = set()
        given: dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID, str]]] = {}  # rep -> [(tenant, lead, title)]
        for lead_tenant, lead_id, title in waiting:
            if lead_tenant in stuck_tenants:
                continue
            rep = await LeadAssignmentService.assign_lead_round_robin(db, lead_tenant, lead_id, trigger_notification=False)
            if rep is None:
                stuck_tenants.add(lead_tenant)
                continue
            given.setdefault(rep.id, []).append((lead_tenant, lead_id, title))

        for rep_id, items in given.items():
            rep_tenant = items[0][0]
            if len(items) == 1:
                _, lead_id, title = items[0]
                notify(db, tenant_id=rep_tenant, user_id=rep_id, notif_type="GENERAL",
                       title=f"🎯 New deal assigned: {title}", message="This deal was waiting for an owner.",
                       link_url=f"#pipeline?lead={lead_id}")
            else:
                notify(db, tenant_id=rep_tenant, user_id=rep_id, notif_type="GENERAL",
                       title=f"🎯 {len(items)} deals were assigned to you",
                       message="These deals were waiting for an owner. Open the pipeline to see them.",
                       link_url="#pipeline")
        if given:
            await db.commit()
        assigned = sum(len(v) for v in given.values())
        if assigned:
            logger.info("Auto-assigned %d unowned deal(s)", assigned)
        return assigned
