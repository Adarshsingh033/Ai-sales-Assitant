"""
Shared CRM rules: canonical deal stages/priorities, who may see what, the contact timeline and lead assignment.

Sales Users only ever see what is assigned to them (deals, chats, tasks) plus the contacts those belong to or that they
created; Tenant Admins see the whole tenant. Every scope helper returns a list of SQL clauses to `.where(*clauses)`.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CRMContext
from app.models.conversation import Conversation
from app.models.crm import Activity, Task
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.user import User, UserRole

STAGES = ("NEW", "CONTACTED", "QUALIFIED", "PROPOSAL", "WON", "LOST")
STAGE_LABELS = {
    "NEW": "New", "CONTACTED": "Contacted", "QUALIFIED": "Qualified",
    "PROPOSAL": "Proposal", "WON": "Won", "LOST": "Lost",
}
CLOSED_STAGES = ("WON", "LOST")

# The three steps the team talks about (a lead comes in, is served, is completed); the six stages stay underneath them.
STAGE_GROUPS = {
    "NEW": ("NEW",),
    "SERVING": ("CONTACTED", "QUALIFIED", "PROPOSAL"),
    "COMPLETED": CLOSED_STAGES,
}
GROUP_LABELS = {"NEW": "New", "SERVING": "Being served", "COMPLETED": "Completed"}

PRIORITIES = ("LOW", "MEDIUM", "HIGH", "HOT")

# Older UI builds wrote these; they are accepted on input and mapped to the canonical value.
_STAGE_ALIASES = {"PROPOSAL_SENT": "PROPOSAL", "CLOSED_WON": "WON", "CLOSED_LOST": "LOST"}
_PRIORITY_ALIASES = {"WARM": "MEDIUM", "COLD": "LOW"}


def normalize_stage(value: str) -> str:
    v = (value or "").strip().upper().replace(" ", "_")
    v = _STAGE_ALIASES.get(v, v)
    if v not in STAGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown stage '{value}'. Use one of: {', '.join(STAGES)}.",
        )
    return v


def normalize_group(value: str) -> str:
    v = (value or "").strip().upper()
    if v not in STAGE_GROUPS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown group '{value}'. Use one of: {', '.join(STAGE_GROUPS)}.",
        )
    return v


def group_counts(stage_counts: dict) -> dict:
    """Roll a {stage: count} map up into {NEW, SERVING, COMPLETED} counts."""
    return {g: sum(stage_counts.get(s, 0) for s in stages) for g, stages in STAGE_GROUPS.items()}


def normalize_priority(value: str) -> str:
    v = (value or "").strip().upper()
    v = _PRIORITY_ALIASES.get(v, v)
    if v not in PRIORITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown priority '{value}'. Use one of: {', '.join(PRIORITIES)}.",
        )
    return v


def full_name(user: Optional[User]) -> Optional[str]:
    if user is None:
        return None
    return f"{user.first_name} {user.last_name}".strip() or user.email


# ----------------------------------------------------------------------------- visibility
def lead_scope(ctx: CRMContext) -> List[Any]:
    clauses: List[Any] = [Lead.tenant_id == ctx.tenant_id]
    if ctx.is_rep:
        clauses.append(Lead.assigned_user_id == ctx.user.id)
    return clauses


def conversation_scope(ctx: CRMContext) -> List[Any]:
    clauses: List[Any] = [Conversation.tenant_id == ctx.tenant_id]
    if ctx.is_rep:
        clauses.append(Conversation.assigned_user_id == ctx.user.id)
    return clauses


def task_scope(ctx: CRMContext) -> List[Any]:
    clauses: List[Any] = [Task.tenant_id == ctx.tenant_id]
    if ctx.is_rep:
        clauses.append(Task.assigned_user_id == ctx.user.id)
    return clauses


def customer_scope(ctx: CRMContext) -> List[Any]:
    clauses: List[Any] = [Customer.tenant_id == ctx.tenant_id]
    if ctx.is_rep:
        me = ctx.user.id
        clauses.append(
            or_(
                Customer.owner_user_id == me,
                exists().where(Lead.customer_id == Customer.id, Lead.assigned_user_id == me),
                exists().where(Conversation.customer_id == Customer.id, Conversation.assigned_user_id == me),
            )
        )
    return clauses


# ----------------------------------------------------------------------------- timeline
def log_activity(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: str,
    text: str,
    customer_id: Optional[uuid.UUID] = None,
    lead_id: Optional[uuid.UUID] = None,
    actor_user_id: Optional[uuid.UUID] = None,
) -> Activity:
    """Queue a timeline entry on the session; the caller's commit persists it."""
    activity = Activity(
        tenant_id=tenant_id, kind=kind, text=text[:2000], customer_id=customer_id, lead_id=lead_id,
        actor_user_id=actor_user_id,
    )
    db.add(activity)
    return activity


def notify(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str,
    message: str,
    notif_type: str = "GENERAL",
    link_url: Optional[str] = None,
) -> None:
    """Queue an in-app notification on the session (no commit)."""
    db.add(Notification(
        tenant_id=tenant_id, recipient_user_id=user_id, title=title, message=message, type=notif_type,
        link_url=link_url, is_read=False,
    ))


# ----------------------------------------------------------------------------- assignment
async def get_assignable_user(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = (
        await db.execute(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if user is None or user.role not in (UserRole.SALES_USER, UserRole.TENANT_ADMIN):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That team member was not found.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="That team member is deactivated.")
    return user


async def assign_lead(
    db: AsyncSession, *, actor: User, lead: Lead, new_user_id: Optional[uuid.UUID], note: Optional[str] = None
) -> None:
    """
    Give a deal (and its chat) to a team member, or to nobody; records it on the timeline and tells the new owner.
    A `note` (why the deal is being handed over) goes on the timeline and into the new owner's notification.
    """
    if lead.assigned_user_id == new_user_id:
        return
    old = await db.get(User, lead.assigned_user_id) if lead.assigned_user_id else None
    new = await get_assignable_user(db, lead.tenant_id, new_user_id) if new_user_id else None

    lead.assigned_user_id = new.id if new else None
    if lead.conversation_id:
        conv = await db.get(Conversation, lead.conversation_id)
        if conv is not None and conv.tenant_id == lead.tenant_id:
            conv.assigned_user_id = lead.assigned_user_id

    if new is None:
        text = f"{full_name(actor)} unassigned this deal"
    elif old is None:
        text = f"{full_name(actor)} assigned this deal to {full_name(new)}"
    else:
        text = f"{full_name(actor)} reassigned this deal from {full_name(old)} to {full_name(new)}"
    log_activity(db, tenant_id=lead.tenant_id, kind="ASSIGNED", text=text, customer_id=lead.customer_id,
                 lead_id=lead.id, actor_user_id=actor.id)
    if note:
        log_activity(db, tenant_id=lead.tenant_id, kind="FEEDBACK", text=f"Hand-over note: {note}",
                     customer_id=lead.customer_id, lead_id=lead.id, actor_user_id=actor.id)
    if new is not None and new.id != actor.id:
        message = f"{full_name(actor)} assigned you this deal."
        if note:
            message += f" Their note: {note}"
        notify(db, tenant_id=lead.tenant_id, user_id=new.id, title=f"Deal assigned to you: {lead.title}",
               message=message, link_url=f"#pipeline?lead={lead.id}")


# ----------------------------------------------------------------------------- stages, closing, hand-over
async def _resolve_chat(db: AsyncSession, lead: Lead) -> None:
    """A finished deal's chat is finished too; if the customer writes again they start a fresh conversation."""
    if not lead.conversation_id:
        return
    conv = await db.get(Conversation, lead.conversation_id)
    if conv is not None and conv.tenant_id == lead.tenant_id and conv.status != "RESOLVED":
        conv.status = "RESOLVED"


async def set_stage(
    db: AsyncSession, *, actor: User, lead: Lead, new_stage: str, reason: Optional[str] = None
) -> None:
    """
    Move a deal to another stage (already normalised) and write it on the timeline. Entering Won / Lost stamps when
    (and why) the deal was closed and resolves its chat; moving back to an open stage clears both.
    """
    if new_stage == lead.status:
        return
    old = STAGE_LABELS.get(lead.status, lead.status.title())
    text = f"{full_name(actor)} moved “{lead.title}” from {old} to {STAGE_LABELS[new_stage]}"
    if new_stage in CLOSED_STAGES and reason:
        text += f" ({reason})"
    log_activity(db, tenant_id=lead.tenant_id, kind="STAGE", text=text, customer_id=lead.customer_id,
                 lead_id=lead.id, actor_user_id=actor.id)
    lead.status = new_stage
    if new_stage in CLOSED_STAGES:
        lead.closed_at = datetime.now(tz=timezone.utc)
        lead.close_reason = reason
        await _resolve_chat(db, lead)
    else:
        lead.closed_at = None
        lead.close_reason = None


async def close_lead(
    db: AsyncSession, *, actor: User, lead: Lead, outcome: str, reason: Optional[str], feedback: str
) -> None:
    """Finish a deal as Won or Lost: the stage change, the why, and the person's own feedback all go on the timeline."""
    outcome = normalize_stage(outcome)
    if outcome not in CLOSED_STAGES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A deal is closed as Won or Lost.")
    if lead.status in CLOSED_STAGES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This deal is already closed. Move it back to an open stage first if you need to change it.",
        )
    await set_stage(db, actor=actor, lead=lead, new_stage=outcome, reason=reason)
    log_activity(db, tenant_id=lead.tenant_id, kind="FEEDBACK", text=f"Feedback: {feedback}",
                 customer_id=lead.customer_id, lead_id=lead.id, actor_user_id=actor.id)


async def hand_over_lead(
    db: AsyncSession, *, actor: User, lead: Lead, to_user_id: uuid.UUID, note: str
) -> None:
    """
    Pass an open deal, its chat and its open to-dos to another team member, with a note for them. A sales rep can only
    hand a deal to another sales rep; admins can pick anyone (they also have the Owner field on the deal).
    """
    if lead.status in CLOSED_STAGES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A closed deal can't be handed over.")
    if to_user_id == lead.assigned_user_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This deal is already with that person.")
    target = await get_assignable_user(db, lead.tenant_id, to_user_id)
    if actor.role == UserRole.SALES_USER and target.role != UserRole.SALES_USER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That team member was not found.")

    previous_owner_id = lead.assigned_user_id
    await assign_lead(db, actor=actor, lead=lead, new_user_id=target.id, note=note)
    if previous_owner_id is not None:  # the follow-ups planned for this deal go with it
        await db.execute(
            update(Task)
            .where(Task.lead_id == lead.id, Task.status == "OPEN", Task.assigned_user_id == previous_owner_id)
            .values(assigned_user_id=target.id)
        )
