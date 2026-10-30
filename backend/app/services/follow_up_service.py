"""
Automated Follow-Up Service (PRD §27).

A scheduler (see `jobs.Worker`) claims due follow-ups across ALL tenants and, per follow-up:
  1. re-checks eligibility at send time — customer replied, opted out, human took over, lead closed, AI/follow-ups
     disabled, conversation resolved → the follow-up is cancelled with a recorded reason;
  2. respects the tenant's quiet hours (09:00-20:00 in the tenant timezone);
  3. drafts a context-aware message with the LLM (attempt-specific strategy), validates it with the guardrails
     and falls back to a safe template when the model is unavailable or the draft is unsafe;
  4. delivers it on the conversation's channel — WhatsApp free text only inside the 24h window; outside it an
     approved template if configured, otherwise the sales rep is notified with the draft (status NEEDS_REP);
     in APPROVAL mode the draft waits for a rep (status AWAITING_APPROVAL);
  5. chains the next attempt (growing delay) until `max_followups`, then stops and tells the rep.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.follow_up import FollowUpSchedule
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.services.ai import guardrails
from app.services.ai.llm import LLMError, build_llm_client
from app.services.ai.tracing import TurnTrace, persist_trace
from app.services.channels import ChannelSender, whatsapp_credentials, whatsapp_window_open
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

ACTIVE_STATES = ("PENDING", "AWAITING_APPROVAL", "NEEDS_REP")
HUMAN_STATES = ("ESCALATED_HUMAN", "HUMAN_AGENT")
QUIET_START_HOUR, QUIET_END_HOUR = 9, 20
STALE_PROCESSING = timedelta(minutes=10)

_STRATEGIES = {
    1: "a friendly check-in that references their specific requirement and offers a quote or a quick demo",
    2: "add value: answer a likely question or share one relevant benefit; no pressure, easy to reply",
    3: "a polite final note: say you will not keep following up, and that they can write back any time",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def fallback_message(name: str, requirements: str, attempt: int) -> str:
    if attempt >= 3:
        return (
            f"Hi {name}, this is my last note about {requirements}. If now is not the right time, no problem — "
            "message me here whenever you are ready."
        )
    if attempt == 2:
        return (
            f"Hi {name}! Checking in again about {requirements}. I can share more details or set up a quick call — "
            "just reply here."
        )
    return (
        f"Hi {name}! Just following up to see if you have any questions regarding {requirements}. "
        "I would be glad to arrange a customized quote or quick demo whenever convenient for you!"
    )


class FollowUpService:
    # ------------------------------------------------------------------
    # Scheduling / lifecycle
    # ------------------------------------------------------------------
    @staticmethod
    async def schedule_followup_if_eligible(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: Optional[uuid.UUID] = None,
        delay_hours: Optional[int] = None,
        trigger_reason: str = "INACTIVITY_24H",
        channel: Optional[str] = None,
    ) -> Optional[FollowUpSchedule]:
        """Arm (or push back) the inactivity follow-up for a lead, if the tenant enabled follow-ups."""
        config = (
            await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if not config or not config.auto_followup_enabled:
            return None
        customer = (await db.execute(select(Customer).where(Customer.id == customer_id))).scalar_one_or_none()
        if customer is not None and customer.do_not_contact:
            return None

        hours = delay_hours if delay_hours is not None else config.followup_delay_hours
        scheduled_time = _utcnow() + timedelta(hours=hours)

        existing = (
            await db.execute(
                select(FollowUpSchedule).where(
                    FollowUpSchedule.tenant_id == tenant_id,
                    FollowUpSchedule.lead_id == lead_id,
                    FollowUpSchedule.status == "PENDING",
                )
            )
        ).scalars().first()
        if existing:
            existing.scheduled_at = scheduled_time  # customer is active again: push the timer back
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
            max_attempts=max(1, config.max_followups),
            channel=channel,
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        logger.info("Scheduled follow-up for lead %s at %s", lead_id, scheduled_time)
        return sched

    @staticmethod
    async def mark_customer_replied(db: AsyncSession, conversation_id: uuid.UUID) -> int:
        """The customer wrote back: every open follow-up for this conversation is obsolete."""
        res = await db.execute(
            update(FollowUpSchedule)
            .where(FollowUpSchedule.conversation_id == conversation_id, FollowUpSchedule.status.in_(ACTIVE_STATES))
            .values(status="RESPONDED", stop_reason="CUSTOMER_REPLIED", executed_at=_utcnow())
        )
        if res.rowcount:
            await db.commit()
        return res.rowcount or 0

    @staticmethod
    async def cancel_for_customer(db: AsyncSession, customer_id: uuid.UUID, reason: str) -> int:
        res = await db.execute(
            update(FollowUpSchedule)
            .where(FollowUpSchedule.customer_id == customer_id, FollowUpSchedule.status.in_(ACTIVE_STATES))
            .values(status="CANCELLED", stop_reason=reason)
        )
        return res.rowcount or 0

    @staticmethod
    async def list_schedules(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(FollowUpSchedule)
            .options(selectinload(FollowUpSchedule.lead), selectinload(FollowUpSchedule.customer))
            .where(FollowUpSchedule.tenant_id == tenant_id)
        )
        if status:
            stmt = stmt.where(FollowUpSchedule.status == status)
        stmt = stmt.order_by(FollowUpSchedule.scheduled_at.asc()).limit(limit)
        items = (await db.execute(stmt)).scalars().all()

        return [
            {
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
                "max_attempts": it.max_attempts,
                "channel": it.channel,
                "stop_reason": it.stop_reason,
                "custom_message": it.custom_message,
                "executed_at": it.executed_at.isoformat() if it.executed_at else None,
            }
            for it in items
        ]

    @staticmethod
    async def cancel_schedule(db: AsyncSession, tenant_id: uuid.UUID, schedule_id: uuid.UUID) -> bool:
        res = await db.execute(
            update(FollowUpSchedule)
            .where(
                FollowUpSchedule.id == schedule_id,
                FollowUpSchedule.tenant_id == tenant_id,
                FollowUpSchedule.status.in_(ACTIVE_STATES),
            )
            .values(status="CANCELLED", stop_reason="CANCELLED_BY_USER")
        )
        await db.commit()
        return res.rowcount > 0

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    @classmethod
    async def process_due_followups(
        cls, db: AsyncSession, tenant_id: Optional[uuid.UUID] = None, limit: int = 50
    ) -> int:
        """Claim and run due follow-ups (one tenant, or all when `tenant_id` is None). Returns messages sent."""
        now = _utcnow()
        await db.execute(
            update(FollowUpSchedule)
            .where(FollowUpSchedule.status == "PROCESSING", FollowUpSchedule.locked_at < now - STALE_PROCESSING)
            .values(status="PENDING", locked_at=None)
        )
        stmt = (
            select(FollowUpSchedule)
            .where(FollowUpSchedule.status == "PENDING", FollowUpSchedule.scheduled_at <= now)
            .order_by(FollowUpSchedule.scheduled_at)
            .limit(limit)
        )
        if tenant_id:
            stmt = stmt.where(FollowUpSchedule.tenant_id == tenant_id)
        if db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        claimed = list((await db.execute(stmt)).scalars().all())
        for item in claimed:
            item.status, item.locked_at = "PROCESSING", now
        await db.commit()

        sent = 0
        for item_id in [c.id for c in claimed]:
            try:
                if await cls._process_item(db, item_id):
                    sent += 1
            except Exception as exc:
                logger.exception("Follow-up %s failed: %s", item_id, exc)
                await db.rollback()
                await db.execute(
                    update(FollowUpSchedule)
                    .where(FollowUpSchedule.id == item_id)
                    .values(status="FAILED", stop_reason=f"ERROR: {str(exc)[:100]}")
                )
                await db.commit()
        return sent

    @classmethod
    async def _process_item(cls, db: AsyncSession, item_id: uuid.UUID) -> bool:
        item = (await db.execute(select(FollowUpSchedule).where(FollowUpSchedule.id == item_id))).scalar_one()
        lead: Optional[Lead] = item.lead
        customer: Optional[Customer] = item.customer
        conv = (
            (await db.execute(select(Conversation).where(Conversation.id == item.conversation_id))).scalar_one_or_none()
            if item.conversation_id else None
        )
        config = (
            await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == item.tenant_id))
        ).scalar_one_or_none()
        tenant = (await db.execute(select(Tenant).where(Tenant.id == item.tenant_id))).scalar_one_or_none()

        verdict, reason = await cls._eligibility(db, item, lead, customer, conv, config)
        if verdict != "send":
            item.status = "RESPONDED" if verdict == "responded" else "CANCELLED"
            item.stop_reason = reason
            item.executed_at = _utcnow()
            await db.commit()
            return False

        window = cls._quiet_window_end(tenant, _utcnow())
        if window is not None:  # outside the tenant's sending hours: try again when the window opens
            item.status, item.locked_at, item.scheduled_at = "PENDING", None, window
            await db.commit()
            return False

        assert config is not None and customer is not None
        name = (customer.name or "there").strip() or "there"
        text, used_ai = await cls._draft(db, item, lead, customer, conv, config, name)
        item.custom_message = text

        if config.followup_mode == "APPROVAL":
            item.status, item.locked_at = "AWAITING_APPROVAL", None
            await db.commit()
            await cls._notify(db, item, lead, name, "📝 Follow-up draft awaiting your approval", f"Draft for {name}: {text}")
            return False

        outcome = await cls._deliver(db, item, conv, customer, config, text, name)
        return await cls._finish(db, item, lead, conv, config, name, outcome, text)

    @classmethod
    async def _finish(
        cls,
        db: AsyncSession,
        item: FollowUpSchedule,
        lead: Optional[Lead],
        conv: Optional[Conversation],
        config: AIAssistantConfig,
        name: str,
        outcome: str,
        text: str,
    ) -> bool:
        now = _utcnow()
        if outcome == "needs_rep":
            item.status, item.locked_at = "NEEDS_REP", None
            await db.commit()
            await cls._notify(
                db, item, lead, name,
                "📲 Follow-up needs you (WhatsApp 24h window closed)",
                f"Reach out to {name} manually. Suggested message: {text}",
            )
            return False
        if outcome == "failed":
            item.status, item.stop_reason, item.locked_at = "FAILED", "DELIVERY_FAILED", None
            await db.commit()
            await cls._notify(db, item, lead, name, "⚠️ Follow-up could not be delivered", f"Message to {name} failed to send.")
            return False

        item.status, item.executed_at, item.locked_at = "DISPATCHED", now, None
        if item.attempt_number < item.max_attempts:
            delay = max(1, config.followup_delay_hours) * (item.attempt_number + 1)
            db.add(
                FollowUpSchedule(
                    tenant_id=item.tenant_id, lead_id=item.lead_id, customer_id=item.customer_id,
                    conversation_id=item.conversation_id, trigger_reason=item.trigger_reason,
                    scheduled_at=now + timedelta(hours=delay), status="PENDING",
                    attempt_number=item.attempt_number + 1, max_attempts=item.max_attempts, channel=item.channel,
                )
            )
        await db.commit()
        if item.attempt_number >= item.max_attempts:
            await cls._notify(
                db, item, lead, name, "🏁 Follow-ups finished with no reply",
                f"No response from {name} after {item.max_attempts} follow-up(s). Consider a personal call.",
            )
        else:
            await cls._notify(db, item, lead, name, f"📩 Automated Follow-Up Dispatched: {lead.title if lead else name}",
                              f"Sent re-engagement message to {name}.")
        return True

    # -- eligibility ----------------------------------------------------
    @staticmethod
    async def _eligibility(
        db: AsyncSession,
        item: FollowUpSchedule,
        lead: Optional[Lead],
        customer: Optional[Customer],
        conv: Optional[Conversation],
        config: Optional[AIAssistantConfig],
    ) -> Tuple[str, Optional[str]]:
        if config is None or not config.auto_followup_enabled or not config.is_enabled:
            return "cancel", "FOLLOWUPS_DISABLED"
        if customer is None or customer.do_not_contact:
            return "cancel", "DO_NOT_CONTACT"
        if lead is not None and lead.status in ("WON", "LOST"):
            return "cancel", "LEAD_CLOSED"
        if conv is not None:
            if conv.status in HUMAN_STATES:
                return "cancel", "HUMAN_ACTIVE"
            if conv.status == "RESOLVED":
                return "cancel", "CONVERSATION_RESOLVED"
            last_customer = (
                await db.execute(
                    select(Message.created_at)
                    .where(Message.conversation_id == conv.id, Message.sender_type == "CUSTOMER")
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last_customer and _aware(last_customer) > _aware(item.created_at):
                return "responded", "CUSTOMER_REPLIED"
        return "send", None

    @staticmethod
    def _quiet_window_end(tenant: Optional[Tenant], now: datetime) -> Optional[datetime]:
        """Start of the next sending window when `now` is outside quiet-hours bounds (None = send now)."""
        if tenant is None or not tenant.timezone:
            return None
        try:
            local = now.astimezone(ZoneInfo(tenant.timezone))
        except Exception as exc:  # unknown zone name, or no tz database (install `tzdata` on Windows)
            logger.warning("Quiet hours disabled for tenant %s: cannot load timezone %r (%s)", tenant.id, tenant.timezone, exc)
            return None
        if QUIET_START_HOUR <= local.hour < QUIET_END_HOUR:
            return None
        start = local.replace(hour=QUIET_START_HOUR, minute=0, second=0, microsecond=0)
        if local.hour >= QUIET_END_HOUR:
            start += timedelta(days=1)
        return start.astimezone(timezone.utc)

    # -- drafting -------------------------------------------------------
    @classmethod
    async def _draft(
        cls,
        db: AsyncSession,
        item: FollowUpSchedule,
        lead: Optional[Lead],
        customer: Customer,
        conv: Optional[Conversation],
        config: AIAssistantConfig,
        name: str,
    ) -> Tuple[str, bool]:
        reqs = (lead.requirements if lead and lead.requirements else None) or "our services"
        fallback = fallback_message(name, reqs, item.attempt_number)

        recent: List[str] = []
        if conv is not None:
            rows = (
                await db.execute(
                    select(Message.sender_type, Message.content)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.desc())
                    .limit(8)
                )
            ).all()
            recent = [f"{'Customer' if s == 'CUSTOMER' else 'Assistant'}: {c}" for s, c in reversed(rows)]

        strategy = _STRATEGIES.get(item.attempt_number, _STRATEGIES[3])
        language = "the same language the customer used" if config.language == "auto" else config.language
        prompt = (
            f"You are '{config.assistant_name}', a sales assistant. Write ONE short WhatsApp-style follow-up message "
            f"(max 3 sentences) to {name}, who has not replied. Attempt {item.attempt_number} of {item.max_attempts}: {strategy}.\n"
            f"Style: {config.personality}. Language: {language}. Do not quote prices or discounts, do not invent facts, "
            "no markdown, no emojis overload, no placeholders. Output only the message.\n\n"
            f"Customer requirement: {reqs}\n"
            f"Conversation summary:\n{conv.ai_summary if conv and conv.ai_summary else '(none)'}\n"
            f"Recent messages:\n{chr(10).join(recent) or '(none)'}"
        )
        trace = TurnTrace(
            tenant_id=item.tenant_id, conversation_id=item.conversation_id, customer_id=item.customer_id, trigger="FOLLOWUP"
        )
        try:
            res = await build_llm_client(config, task="reply").complete(
                [{"role": "user", "content": prompt}], temperature=0.5, max_tokens=220
            )
            trace.add_llm(res, "FOLLOWUP_GENERATION")
            trace.reply = res.content
            checked = guardrails.check_reply(
                res.content, max_discount_percent=config.max_discount_percent, verified_amounts=set(), max_chars=600,
                verified_text=f"{config.custom_instructions or ''}\n{customer.phone or ''}\n{customer.email or ''}",
            )
            await persist_trace(db, trace)
            await db.commit()
            if checked.violations or len(checked.reply) < 10:
                return fallback, False
            return checked.reply, True
        except LLMError as exc:
            logger.info("Follow-up draft fell back to template: %s", exc)
            return fallback, False

    # -- delivery -------------------------------------------------------
    @classmethod
    async def _deliver(
        cls,
        db: AsyncSession,
        item: FollowUpSchedule,
        conv: Optional[Conversation],
        customer: Customer,
        config: AIAssistantConfig,
        text: str,
        name: str,
    ) -> str:
        """Returns "sent" | "needs_rep" | "failed"."""
        if conv is None:
            return "needs_rep"
        channel = conv.channel
        item.channel = channel
        if channel == "WHATSAPP":
            if await whatsapp_window_open(db, conv.id):
                sent = await ChannelSender.send(db, conv, customer, config, text)
            else:
                phone_id, token = whatsapp_credentials(config)
                if config.whatsapp_followup_template and phone_id and token and customer.phone:
                    from app.services.whatsapp_service import WhatsAppService

                    sent = await WhatsAppService.send_whatsapp_template(
                        phone_id, token, customer.phone, config.whatsapp_followup_template, [name.split()[0]]
                    )
                else:
                    return "needs_rep"
        else:
            sent = await ChannelSender.send(db, conv, customer, config, text)
        if not sent:
            return "failed"
        db.add(Message(conversation_id=conv.id, sender_type="AI", content=text))
        conv.last_activity_at = _utcnow()
        return "sent"

    @staticmethod
    async def _notify(db: AsyncSession, item: FollowUpSchedule, lead: Optional[Lead], name: str, title: str, message: str) -> None:
        try:
            await NotificationService.create_notification(
                db=db,
                tenant_id=item.tenant_id,
                title=title,
                message=message[:480],
                notif_type="FOLLOWUP_DUE",
                recipient_user_id=lead.assigned_user_id if lead else None,
                link_url=f"#/conversations?id={item.conversation_id}" if item.conversation_id else "#/leads",
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Follow-up notification failed: %s", exc)

    # ------------------------------------------------------------------
    # Human approval (followup_mode == "APPROVAL")
    # ------------------------------------------------------------------
    @classmethod
    async def approve(
        cls, db: AsyncSession, tenant_id: uuid.UUID, schedule_id: uuid.UUID, message: Optional[str] = None
    ) -> Dict[str, Any]:
        item = (
            await db.execute(
                select(FollowUpSchedule).where(
                    FollowUpSchedule.id == schedule_id,
                    FollowUpSchedule.tenant_id == tenant_id,
                    FollowUpSchedule.status == "AWAITING_APPROVAL",
                )
            )
        ).scalar_one_or_none()
        if item is None:
            return {"ok": False, "detail": "Follow-up not found or not awaiting approval."}
        lead, customer = item.lead, item.customer
        conv = (
            (await db.execute(select(Conversation).where(Conversation.id == item.conversation_id))).scalar_one_or_none()
            if item.conversation_id else None
        )
        config = (
            await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant_id))
        ).scalar_one()
        text = (message or item.custom_message or "").strip()
        if not text or customer is None or customer.do_not_contact:
            return {"ok": False, "detail": "Nothing to send, or the customer opted out."}
        item.custom_message = text
        name = (customer.name or "there").strip() or "there"
        outcome = await cls._deliver(db, item, conv, customer, config, text, name)
        sent = await cls._finish(db, item, lead, conv, config, name, outcome, text)
        return {"ok": True, "sent": sent, "status": item.status}

    @staticmethod
    async def reject(db: AsyncSession, tenant_id: uuid.UUID, schedule_id: uuid.UUID) -> bool:
        res = await db.execute(
            update(FollowUpSchedule)
            .where(
                FollowUpSchedule.id == schedule_id,
                FollowUpSchedule.tenant_id == tenant_id,
                FollowUpSchedule.status.in_(["AWAITING_APPROVAL", "NEEDS_REP"]),
            )
            .values(status="CANCELLED", stop_reason="REJECTED_BY_REP")
        )
        await db.commit()
        return res.rowcount > 0
