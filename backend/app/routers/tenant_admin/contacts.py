"""
Contacts: one list of everyone the business talks to, and a single detail view per person
(profile, deals, chats, tasks and a timeline of notes and events).

Sales Users see only contacts they own or whose deals/chats are assigned to them.
"""
import math
import re
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.database import get_db
from app.core.dependencies import CRMContext, require_crm_context
from app.core.sqlfuncs import digits_only, greatest
from app.models.conversation import Conversation, Message
from app.models.crm import Activity, Task
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.user import User
from app.schemas.crm import (
    ActivityResponse,
    ContactChat,
    ContactCreate,
    ContactDeal,
    ContactDetail,
    ContactListResponse,
    ContactSummary,
    ContactUpdate,
    NoteCreate,
    TaskResponse,
)
from app.services import crm

router = APIRouter(prefix="/tenant-admin/contacts", tags=["Tenant Admin Contacts"])

_NON_DIGITS = re.compile(r"\D")
_MIN_PHONE_DIGITS = 6


def _digits(value: Optional[str]) -> str:
    return _NON_DIGITS.sub("", value or "")


def _phone_digits_column():
    return digits_only(Customer.phone)


def _summary_query():
    """Customer rows with owner name, open-deal count and the latest sign of life (chat or timeline entry)."""
    owner = aliased(User)
    open_deals = (
        select(func.count(Lead.id))
        .where(Lead.customer_id == Customer.id, Lead.status.notin_(crm.CLOSED_STAGES))
        .correlate(Customer)
        .scalar_subquery()
    )
    last_chat = (
        select(func.max(Conversation.last_activity_at)).where(Conversation.customer_id == Customer.id)
        .correlate(Customer).scalar_subquery()
    )
    last_note = (
        select(func.max(Activity.created_at)).where(Activity.customer_id == Customer.id)
        .correlate(Customer).scalar_subquery()
    )
    last_activity = greatest(
        Customer.created_at, func.coalesce(last_chat, Customer.created_at), func.coalesce(last_note, Customer.created_at)
    )
    stmt = (
        select(Customer, open_deals.label("open_deals"), last_activity.label("last_activity"), owner.first_name, owner.last_name)
        .outerjoin(owner, Customer.owner_user_id == owner.id)
    )
    return stmt, last_activity


def _to_summary(row: Any) -> ContactSummary:
    customer, open_deals, last_activity, first, last = row
    owner_name = f"{first or ''} {last or ''}".strip() or None
    return ContactSummary(
        id=customer.id, name=customer.name, phone=customer.phone, email=customer.email,
        company_name=customer.company_name, owner_user_id=customer.owner_user_id, owner_name=owner_name,
        open_deals=open_deals or 0, last_activity_at=last_activity, do_not_contact=customer.do_not_contact,
        created_at=customer.created_at,
    )


async def _get_visible(db: AsyncSession, ctx: CRMContext, contact_id: uuid.UUID) -> Customer:
    customer = (
        await db.execute(select(Customer).where(*crm.customer_scope(ctx), Customer.id == contact_id))
    ).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="Contact not found.")
    return customer


async def _summary_for(db: AsyncSession, ctx: CRMContext, contact_id: uuid.UUID) -> ContactSummary:
    stmt, _ = _summary_query()
    row = (await db.execute(stmt.where(*crm.customer_scope(ctx), Customer.id == contact_id))).first()
    return _to_summary(row)


async def _check_duplicate(
    db: AsyncSession, tenant_id: uuid.UUID, phone: Optional[str], email: Optional[str], exclude_id: Optional[uuid.UUID] = None
) -> None:
    """Refuse a second contact with the same email or phone number (digits compared, so formatting is ignored)."""
    checks = []
    if email:
        checks.append((func.lower(Customer.email) == email.lower(), "email address"))
    digits = _digits(phone)
    if len(digits) >= _MIN_PHONE_DIGITS:
        checks.append((_phone_digits_column() == digits, "phone number"))
    for clause, label in checks:
        stmt = select(Customer.name).where(Customer.tenant_id == tenant_id, clause)
        if exclude_id:
            stmt = stmt.where(Customer.id != exclude_id)
        existing = (await db.execute(stmt.limit(1))).first()
        if existing is not None:
            who = existing[0] or "another contact"
            raise HTTPException(status_code=409, detail=f"A contact with this {label} already exists ({who}).")


def _activity_response(a: Activity, actor_name: Optional[str], ctx: CRMContext) -> ActivityResponse:
    return ActivityResponse(
        id=a.id, kind=a.kind, text=a.text, lead_id=a.lead_id, created_at=a.created_at,
        actor_name=actor_name or ("AI Assistant" if a.kind == "AI" else None),
        can_delete=a.kind == "NOTE" and (ctx.is_admin or a.actor_user_id == ctx.user.id),
    )


# ----------------------------------------------------------------------------- list / create
@router.get("", response_model=ContactListResponse, summary="List contacts")
async def list_contacts(
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> ContactListResponse:
    stmt, last_activity = _summary_query()
    filters: List[Any] = crm.customer_scope(ctx)
    if search and search.strip():
        term = search.strip().lower()
        like = f"%{term}%"
        match = (
            func.lower(func.coalesce(Customer.name, "")).like(like)
            | func.lower(func.coalesce(Customer.email, "")).like(like)
            | func.lower(func.coalesce(Customer.company_name, "")).like(like)
            | func.lower(func.coalesce(Customer.phone, "")).like(like)
        )
        digits = _digits(term)
        if len(digits) >= 3:
            match = match | func.coalesce(_phone_digits_column(), "").like(f"%{digits}%")
        filters.append(match)

    total = (await db.execute(select(func.count(Customer.id)).where(*filters))).scalar_one()
    rows = (
        await db.execute(
            stmt.where(*filters).order_by(last_activity.desc(), Customer.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return ContactListResponse(
        items=[_to_summary(r) for r in rows], total=total, page=page, page_size=page_size,
        pages=max(1, math.ceil(total / page_size)),
    )


@router.post("", response_model=ContactSummary, status_code=status.HTTP_201_CREATED, summary="Add a contact")
async def create_contact(
    data: ContactCreate,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> ContactSummary:
    await _check_duplicate(db, ctx.tenant_id, data.phone, data.email)

    owner_id = ctx.user.id if ctx.is_rep else None
    if ctx.is_admin and data.owner_user_id:
        owner_id = (await crm.get_assignable_user(db, ctx.tenant_id, data.owner_user_id)).id

    customer = Customer(
        tenant_id=ctx.tenant_id, name=data.name, phone=data.phone, email=data.email,
        company_name=data.company_name, owner_user_id=owner_id,
    )
    db.add(customer)
    await db.flush()
    crm.log_activity(db, tenant_id=ctx.tenant_id, kind="CONTACT", text=f"{crm.full_name(ctx.user)} added this contact",
                     customer_id=customer.id, actor_user_id=ctx.user.id)
    await db.commit()
    return await _summary_for(db, ctx, customer.id)


# ----------------------------------------------------------------------------- detail / update
@router.get("/{contact_id}", response_model=ContactDetail, summary="Everything about one contact")
async def get_contact(
    contact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> ContactDetail:
    await _get_visible(db, ctx, contact_id)
    summary = await _summary_for(db, ctx, contact_id)

    rep = aliased(User)
    deal_rows = (
        await db.execute(
            select(Lead, rep.first_name, rep.last_name)
            .outerjoin(rep, Lead.assigned_user_id == rep.id)
            .where(*crm.lead_scope(ctx), Lead.customer_id == contact_id)
            .order_by(Lead.created_at.desc())
        )
    ).all()
    deals = [
        ContactDeal(
            id=l.id, title=l.title, status=l.status, priority=l.priority, score=l.score,
            budget=float(l.budget) if l.budget is not None else None, assigned_user_id=l.assigned_user_id,
            assigned_user_name=f"{f or ''} {n or ''}".strip() or None, created_at=l.created_at,
        )
        for l, f, n in deal_rows
    ]

    chat_rows = (
        await db.execute(
            select(Conversation, rep.first_name, rep.last_name)
            .outerjoin(rep, Conversation.assigned_user_id == rep.id)
            .where(*crm.conversation_scope(ctx), Conversation.customer_id == contact_id)
            .order_by(Conversation.last_activity_at.desc())
            .limit(10)
        )
    ).all()
    previews = {}
    if chat_rows:
        ids = [c.id for c, _, _ in chat_rows]
        newest = (
            select(Message.conversation_id.label("cid"), func.max(Message.created_at).label("at"))
            .where(Message.conversation_id.in_(ids))
            .group_by(Message.conversation_id)
            .subquery()
        )
        last_msgs = (
            await db.execute(
                select(Message.conversation_id, Message.content).join(
                    newest, and_(Message.conversation_id == newest.c.cid, Message.created_at == newest.c.at)
                )
            )
        ).all()
        previews = {cid: content[:120] for cid, content in last_msgs}
    chats = [
        ContactChat(
            id=c.id, channel=c.channel, status=c.status, last_message_preview=previews.get(c.id),
            last_activity_at=c.last_activity_at, assigned_user_name=f"{f or ''} {n or ''}".strip() or None,
        )
        for c, f, n in chat_rows
    ]

    from app.routers.tenant_admin.tasks import _select_tasks, _to_response as task_response

    open_tasks = (
        await db.execute(
            _select_tasks().where(*crm.task_scope(ctx), Task.customer_id == contact_id, Task.status == "OPEN")
            .order_by(Task.due_at.asc().nulls_last())
        )
    ).all()
    recent_done = (
        await db.execute(
            _select_tasks().where(*crm.task_scope(ctx), Task.customer_id == contact_id, Task.status == "DONE")
            .order_by(Task.completed_at.desc()).limit(5)
        )
    ).all()
    tasks: List[TaskResponse] = [task_response(*r) for r in [*open_tasks, *recent_done]]

    actor = aliased(User)
    activity_rows = (
        await db.execute(
            select(Activity, actor.first_name, actor.last_name)
            .outerjoin(actor, Activity.actor_user_id == actor.id)
            .where(Activity.tenant_id == ctx.tenant_id, Activity.customer_id == contact_id)
            .order_by(Activity.created_at.desc())
            .limit(100)
        )
    ).all()
    timeline = [_activity_response(a, f"{f or ''} {n or ''}".strip() or None, ctx) for a, f, n in activity_rows]

    return ContactDetail(contact=summary, deals=deals, chats=chats, tasks=tasks, timeline=timeline)


@router.put("/{contact_id}", response_model=ContactSummary, summary="Edit a contact")
async def update_contact(
    contact_id: uuid.UUID,
    data: ContactUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> ContactSummary:
    customer = await _get_visible(db, ctx, contact_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and not changes["name"]:
        raise HTTPException(status_code=422, detail="A contact needs a name.")

    await _check_duplicate(
        db, ctx.tenant_id,
        changes.get("phone") if "phone" in changes and changes["phone"] != customer.phone else None,
        changes.get("email") if "email" in changes and (changes["email"] or "") != (customer.email or "").lower() else None,
        exclude_id=customer.id,
    )
    for field in ("name", "phone", "email", "company_name"):
        if field in changes:
            setattr(customer, field, changes[field])
    if "owner_user_id" in changes and changes["owner_user_id"] != customer.owner_user_id:
        if not ctx.is_admin:
            raise HTTPException(status_code=403, detail="Only an admin can change a contact's owner.")
        customer.owner_user_id = (
            (await crm.get_assignable_user(db, ctx.tenant_id, changes["owner_user_id"])).id
            if changes["owner_user_id"] else None
        )
    await db.commit()
    return await _summary_for(db, ctx, contact_id)


# ----------------------------------------------------------------------------- notes
@router.post("/{contact_id}/notes", response_model=ActivityResponse, status_code=status.HTTP_201_CREATED, summary="Add a note")
async def add_note(
    contact_id: uuid.UUID,
    data: NoteCreate,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> ActivityResponse:
    await _get_visible(db, ctx, contact_id)
    lead_id = None
    if data.lead_id:
        lead = (
            await db.execute(select(Lead).where(*crm.lead_scope(ctx), Lead.id == data.lead_id, Lead.customer_id == contact_id))
        ).scalar_one_or_none()
        if lead is None:
            raise HTTPException(status_code=404, detail="Deal not found for this contact.")
        lead_id = lead.id
    note = crm.log_activity(db, tenant_id=ctx.tenant_id, kind="NOTE", text=data.text, customer_id=contact_id,
                            lead_id=lead_id, actor_user_id=ctx.user.id)
    await db.commit()
    await db.refresh(note)
    return _activity_response(note, crm.full_name(ctx.user), ctx)


@router.delete("/{contact_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a note")
async def delete_note(
    contact_id: uuid.UUID,
    note_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> None:
    await _get_visible(db, ctx, contact_id)
    note = (
        await db.execute(
            select(Activity).where(
                Activity.id == note_id, Activity.tenant_id == ctx.tenant_id, Activity.customer_id == contact_id,
                Activity.kind == "NOTE",
            )
        )
    ).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    if not ctx.is_admin and note.actor_user_id != ctx.user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own notes.")
    await db.delete(note)
    await db.commit()
