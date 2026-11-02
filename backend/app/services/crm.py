"""
Shared CRM rules: canonical deal stages/priorities, who may see what, the contact timeline and lead assignment.

Sales Users only ever see what is assigned to them (deals, chats, tasks) plus the contacts those belong to or that they
created; Tenant Admins see the whole tenant. Every scope helper returns a list of SQL clauses to `.where(*clauses)`.
"""
import uuid
from typing import Any, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import exists, or_, select
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


async def assign_lead(db: AsyncSession, *, actor: User, lead: Lead, new_user_id: Optional[uuid.UUID]) -> None:
    """Give a deal (and its chat) to a team member, or to nobody; records it on the timeline and tells the new owner."""
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
    if new is not None and new.id != actor.id:
        notify(db, tenant_id=lead.tenant_id, user_id=new.id, title=f"Deal assigned to you: {lead.title}",
               message=f"{full_name(actor)} assigned you this deal.", link_url=f"#pipeline?lead={lead.id}")
