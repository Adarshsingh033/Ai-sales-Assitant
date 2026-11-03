"""
Deals ("leads") & pipeline for Tenant Admins and Sales Users.

A Sales User only sees deals assigned to them; a Tenant Admin sees every deal and is the only one who can hand a deal
to someone else. Stages and priorities are validated against the canonical lists in `app.services.crm`.
"""
import math
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core.database import get_db
from app.core.dependencies import CRMContext, require_crm_context
from app.models.crm import Activity, Task
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.user import User
from app.schemas.crm import (
    BoardCard,
    BoardColumn,
    BoardResponse,
    DealClose,
    DealCreate,
    DealHandOver,
    DealHistoryItem,
)
from app.schemas.lead import (
    LeadListResponse,
    LeadResponse,
    LeadUpdate,
    PipelineStatsResponse,
)
from app.services import crm

router = APIRouter(prefix="/tenant-admin/leads", tags=["Tenant Admin Leads"])

BOARD_COLUMN_LIMIT = 30  # cards shown per stage on the board; the list view holds the rest


def _to_lead_response(lead: Lead) -> LeadResponse:
    customer_name = lead.customer.name if lead.customer else None
    customer_phone = lead.customer.phone if lead.customer else None
    customer_email = lead.customer.email if lead.customer else None
    assigned_name = (
        f"{lead.assigned_user.first_name} {lead.assigned_user.last_name}".strip()
        if lead.assigned_user
        else None
    )

    return LeadResponse(
        id=lead.id,
        tenant_id=lead.tenant_id,
        customer_id=lead.customer_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_email=customer_email,
        conversation_id=lead.conversation_id,
        title=lead.title,
        status=lead.status,
        score=lead.score,
        score_reason=lead.score_reason,
        requirements=lead.requirements,
        budget=float(lead.budget) if lead.budget is not None else None,
        timeline=lead.timeline,
        priority=lead.priority,
        assigned_user_id=lead.assigned_user_id,
        assigned_user_name=assigned_name,
        source=lead.source,
        notes=lead.notes,
        closed_at=lead.closed_at,
        close_reason=lead.close_reason,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


def _lead_query():
    return select(Lead).options(selectinload(Lead.customer), selectinload(Lead.assigned_user))


async def _get_lead(db: AsyncSession, ctx: CRMContext, lead_id: uuid.UUID) -> Lead:
    lead = (
        await db.execute(
            _lead_query().where(*crm.lead_scope(ctx), Lead.id == lead_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()  # populate_existing: after an update the owner/customer must be re-read, not served from the session
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")
    return lead


@router.get("/pipeline-stats", response_model=PipelineStatsResponse, summary="Get Sales Pipeline Statistics")
async def get_pipeline_stats(
    assigned_user_id: Optional[uuid.UUID] = None,
    unassigned: bool = False,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> PipelineStatsResponse:
    scope = crm.lead_scope(ctx)
    if assigned_user_id and ctx.is_admin:
        scope.append(Lead.assigned_user_id == assigned_user_id)
    if unassigned and ctx.is_admin:
        scope.append(Lead.assigned_user_id.is_(None))
    rows = (
        await db.execute(
            select(Lead.status, func.count(Lead.id), func.sum(Lead.budget)).where(*scope).group_by(Lead.status)
        )
    ).all()

    stages = {stage: 0 for stage in crm.STAGES}
    total_val = 0.0  # value of deals still open
    total_leads = 0
    for st, count, bgt in rows:
        stages[st] = stages.get(st, 0) + count
        total_leads += count
        if bgt and st not in crm.CLOSED_STAGES:
            total_val += float(bgt)

    hot_count = (
        await db.execute(
            select(func.count(Lead.id)).where(*scope, Lead.status.notin_(crm.CLOSED_STAGES), Lead.priority == "HOT")
        )
    ).scalar_one()
    return PipelineStatsResponse(
        total_leads=total_leads,
        stage_counts=stages,
        group_counts=crm.group_counts(stages),
        total_estimated_value=round(total_val, 2),
        hot_leads_count=hot_count,
    )


@router.get("/board", response_model=BoardResponse, summary="Deals grouped by stage for the Kanban board")
async def get_board(
    search: Optional[str] = None,
    assigned_user_id: Optional[uuid.UUID] = None,
    unassigned: bool = False,
    day_end: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> BoardResponse:
    filters = crm.lead_scope(ctx)
    if assigned_user_id and ctx.is_admin:
        filters.append(Lead.assigned_user_id == assigned_user_id)
    if unassigned and ctx.is_admin:
        filters.append(Lead.assigned_user_id.is_(None))
    base = select(Lead.id).outerjoin(Customer, Lead.customer_id == Customer.id).where(*filters)
    if search and search.strip():
        like = f"%{search.strip().lower()}%"
        base = base.where(
            func.lower(Lead.title).like(like)
            | func.lower(func.coalesce(Customer.name, "")).like(like)
            | func.lower(func.coalesce(Customer.company_name, "")).like(like)
            | func.lower(func.coalesce(Customer.phone, "")).like(like)
        )
    visible = base.subquery()

    totals = {
        st: (count, float(value or 0))
        for st, count, value in (
            await db.execute(
                select(Lead.status, func.count(Lead.id), func.sum(Lead.budget))
                .where(Lead.id.in_(select(visible.c.id)))
                .group_by(Lead.status)
            )
        ).all()
    }

    rank = func.row_number().over(
        partition_by=Lead.status, order_by=(Lead.score.desc(), Lead.updated_at.desc())
    ).label("rank")
    ranked = (
        select(Lead.id, rank).where(Lead.id.in_(select(visible.c.id))).subquery()
    )
    now = datetime.now(timezone.utc)
    open_tasks = (
        select(func.count(Task.id)).where(Task.lead_id == Lead.id, Task.status == "OPEN").correlate(Lead).scalar_subquery()
    )
    overdue_tasks = (
        select(func.count(Task.id))
        .where(Task.lead_id == Lead.id, Task.status == "OPEN", Task.due_at < now)
        .correlate(Lead).scalar_subquery()
    )
    owner = aliased(User)
    card_rows = (
        await db.execute(
            select(Lead, Customer.name, owner.first_name, owner.last_name, open_tasks, overdue_tasks)
            .join(ranked, ranked.c.id == Lead.id)
            .outerjoin(Customer, Lead.customer_id == Customer.id)
            .outerjoin(owner, Lead.assigned_user_id == owner.id)
            .where(ranked.c.rank <= BOARD_COLUMN_LIMIT)
            .order_by(Lead.score.desc(), Lead.updated_at.desc())
        )
    ).all()

    cards_by_stage = {stage: [] for stage in crm.STAGES}
    for lead, customer_name, first, last, n_open, n_overdue in card_rows:
        cards_by_stage.setdefault(lead.status, []).append(
            BoardCard(
                id=lead.id, title=lead.title, status=lead.status, priority=lead.priority, score=lead.score,
                budget=float(lead.budget) if lead.budget is not None else None, customer_id=lead.customer_id,
                customer_name=customer_name, assigned_user_id=lead.assigned_user_id,
                assigned_user_name=f"{first or ''} {last or ''}".strip() or None,
                open_tasks=n_open or 0, overdue_tasks=n_overdue or 0, updated_at=lead.updated_at,
            )
        )

    columns = [
        BoardColumn(
            stage=stage, label=crm.STAGE_LABELS[stage], count=totals.get(stage, (0, 0.0))[0],
            total_value=round(totals.get(stage, (0, 0.0))[1], 2), cards=cards_by_stage.get(stage, []),
        )
        for stage in crm.STAGES
    ]
    unassigned_count = 0
    if ctx.is_admin:
        unassigned_count = (
            await db.execute(
                select(func.count(Lead.id)).where(
                    Lead.tenant_id == ctx.tenant_id, Lead.assigned_user_id.is_(None), Lead.status.notin_(crm.CLOSED_STAGES)
                )
            )
        ).scalar_one()
    return BoardResponse(columns=columns, unassigned=unassigned_count)


@router.get("", response_model=LeadListResponse, summary="List Leads")
async def list_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    lead_status: Optional[str] = Query(None, alias="status"),
    stage_group: Optional[str] = None,
    priority: Optional[str] = None,
    min_score: Optional[int] = Query(None, ge=0, le=100),
    assigned_user_id: Optional[uuid.UUID] = None,
    unassigned: bool = False,
    customer_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> LeadListResponse:
    stmt = _lead_query().where(*crm.lead_scope(ctx))

    if lead_status:
        stmt = stmt.where(Lead.status == crm.normalize_stage(lead_status))
    if stage_group:
        stmt = stmt.where(Lead.status.in_(crm.STAGE_GROUPS[crm.normalize_group(stage_group)]))
    if priority:
        stmt = stmt.where(Lead.priority == crm.normalize_priority(priority))
    if min_score is not None:
        stmt = stmt.where(Lead.score >= min_score)
    if assigned_user_id and ctx.is_admin:
        stmt = stmt.where(Lead.assigned_user_id == assigned_user_id)
    if unassigned and ctx.is_admin:
        stmt = stmt.where(Lead.assigned_user_id.is_(None))
    if customer_id:
        stmt = stmt.where(Lead.customer_id == customer_id)

    if search and search.strip():
        pat = f"%{search.strip().lower()}%"
        stmt = stmt.outerjoin(Customer, Lead.customer_id == Customer.id).where(
            func.lower(Lead.title).like(pat)
            | func.lower(func.coalesce(Customer.name, "")).like(pat)
            | func.lower(func.coalesce(Customer.phone, "")).like(pat)
            | func.lower(func.coalesce(Customer.email, "")).like(pat)
            | func.lower(func.coalesce(Customer.company_name, "")).like(pat)
        )

    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    stmt = stmt.order_by(Lead.score.desc(), Lead.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    return LeadListResponse(
        items=[_to_lead_response(l) for l in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if page_size > 0 else 1,
    )


@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED, summary="Create a deal by hand")
async def create_lead(
    data: DealCreate,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> LeadResponse:
    customer = (
        await db.execute(select(Customer).where(*crm.customer_scope(ctx), Customer.id == data.customer_id))
    ).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="Contact not found.")

    owner_id = ctx.user.id if ctx.is_rep else None
    if ctx.is_admin and data.assigned_user_id:
        owner_id = (await crm.get_assignable_user(db, ctx.tenant_id, data.assigned_user_id)).id

    lead = Lead(
        tenant_id=ctx.tenant_id, customer_id=customer.id, title=data.title, budget=data.budget,
        status=crm.normalize_stage(data.status), priority=crm.normalize_priority(data.priority),
        notes=data.notes, source="MANUAL", score=0, assigned_user_id=owner_id,
    )
    db.add(lead)
    await db.flush()
    crm.log_activity(db, tenant_id=ctx.tenant_id, kind="DEAL", text=f"{crm.full_name(ctx.user)} created the deal “{lead.title}”",
                     customer_id=customer.id, lead_id=lead.id, actor_user_id=ctx.user.id)
    if owner_id and owner_id != ctx.user.id:
        crm.notify(db, tenant_id=ctx.tenant_id, user_id=owner_id, title=f"Deal assigned to you: {lead.title}",
                   message=f"{crm.full_name(ctx.user)} assigned you this deal.", link_url=f"#pipeline?lead={lead.id}")
    await db.commit()
    if owner_id is None:
        # an admin who did not pick an owner gets the same automatic round-robin as an AI-captured deal
        from app.services.lead_assignment_service import LeadAssignmentService

        await LeadAssignmentService.assign_lead_round_robin(db, ctx.tenant_id, lead.id, actor_id=ctx.user.id)
    return _to_lead_response(await _get_lead(db, ctx, lead.id))


@router.get("/{lead_id}", response_model=LeadResponse, summary="Get Lead Details")
async def get_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> LeadResponse:
    return _to_lead_response(await _get_lead(db, ctx, lead_id))


@router.put("/{lead_id}", response_model=LeadResponse, summary="Update Lead Stage, Priority, or Assignment")
async def update_lead(
    lead_id: uuid.UUID,
    data: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> LeadResponse:
    lead = await _get_lead(db, ctx, lead_id)
    changes = data.model_dump(exclude_unset=True)

    if "assigned_user_id" in changes and changes["assigned_user_id"] != lead.assigned_user_id and not ctx.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can reassign a deal.")

    if "title" in changes:
        if not changes["title"] or not changes["title"].strip():
            raise HTTPException(status_code=422, detail="A deal needs a title.")
        lead.title = changes["title"].strip()
    if "status" in changes and changes["status"]:
        await crm.set_stage(db, actor=ctx.user, lead=lead, new_stage=crm.normalize_stage(changes["status"]))
    if "priority" in changes and changes["priority"]:
        lead.priority = crm.normalize_priority(changes["priority"])
    for field in ("budget", "timeline", "requirements", "notes"):
        if field in changes:
            setattr(lead, field, changes[field])
    if "assigned_user_id" in changes:
        await crm.assign_lead(db, actor=ctx.user, lead=lead, new_user_id=changes["assigned_user_id"])

    await db.commit()
    return _to_lead_response(await _get_lead(db, ctx, lead_id))


@router.post("/{lead_id}/close", response_model=LeadResponse, summary="Close a deal as Won or Lost, with feedback")
async def close_deal(
    lead_id: uuid.UUID,
    data: DealClose,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> LeadResponse:
    lead = await _get_lead(db, ctx, lead_id)
    await crm.close_lead(db, actor=ctx.user, lead=lead, outcome=data.outcome, reason=data.reason, feedback=data.feedback)
    await db.commit()
    return _to_lead_response(await _get_lead(db, ctx, lead_id))


@router.post("/{lead_id}/hand-over", response_model=LeadResponse, summary="Hand a deal to another team member, with a note")
async def hand_over_deal(
    lead_id: uuid.UUID,
    data: DealHandOver,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> LeadResponse:
    lead = await _get_lead(db, ctx, lead_id)
    await crm.hand_over_lead(db, actor=ctx.user, lead=lead, to_user_id=data.to_user_id, note=data.note)
    await db.commit()
    # read it back by tenant, not by the caller's own view: a rep no longer sees the deal they just handed over
    handed = (
        await db.execute(
            _lead_query().where(Lead.tenant_id == ctx.tenant_id, Lead.id == lead_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    return _to_lead_response(handed)


@router.get("/{lead_id}/history", response_model=List[DealHistoryItem], summary="Everything that happened on a deal")
async def get_lead_history(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> List[DealHistoryItem]:
    lead = await _get_lead(db, ctx, lead_id)
    rows = (
        await db.execute(
            select(Activity, User.first_name, User.last_name)
            .outerjoin(User, Activity.actor_user_id == User.id)
            .where(Activity.tenant_id == ctx.tenant_id, Activity.lead_id == lead.id)
            # newest first; the person's own words sort above the system line they were written with (same instant)
            .order_by(Activity.created_at.desc(), case((Activity.kind == "FEEDBACK", 0), else_=1))
            .limit(50)
        )
    ).all()
    return [
        DealHistoryItem(
            id=a.id, kind=a.kind, text=a.text, created_at=a.created_at,
            actor_name=f"{first or ''} {last or ''}".strip() or ("AI Assistant" if a.kind == "AI" else None),
        )
        for a, first, last in rows
    ]


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete Lead")
async def delete_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> None:
    if not ctx.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can delete a deal.")
    lead = (
        await db.execute(select(Lead).where(Lead.tenant_id == ctx.tenant_id, Lead.id == lead_id))
    ).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    await db.delete(lead)
    await db.commit()
