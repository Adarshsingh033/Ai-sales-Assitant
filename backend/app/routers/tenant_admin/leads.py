"""
Lead & Pipeline Management endpoints for Tenant Admin and Sales Users.
"""
import math
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import require_sales_user, require_tenant_admin
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.user import User
from app.schemas.lead import (
    LeadListResponse,
    LeadResponse,
    LeadUpdate,
    PipelineStatsResponse,
)

router = APIRouter(prefix="/tenant-admin/leads", tags=["Tenant Admin Leads"])


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
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


@router.get(
    "/pipeline-stats",
    response_model=PipelineStatsResponse,
    summary="Get Sales Pipeline Statistics",
)
async def get_pipeline_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> PipelineStatsResponse:
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is required.")

    # Get counts per status
    stmt = (
        select(Lead.status, func.count(Lead.id), func.sum(Lead.budget))
        .where(Lead.tenant_id == tenant_id)
        .group_by(Lead.status)
    )
    rows = (await db.execute(stmt)).all()

    stages = {"NEW": 0, "QUALIFIED": 0, "CONTACTED": 0, "PROPOSAL": 0, "WON": 0, "LOST": 0}
    total_val = 0.0
    total_leads = 0

    for st, count, bgt in rows:
        stages[st] = count
        total_leads += count
        if bgt:
            total_val += float(bgt)

    # Hot leads count (score >= 80)
    hot_stmt = select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id, Lead.score >= 80)
    hot_count = (await db.execute(hot_stmt)).scalar_one()

    return PipelineStatsResponse(
        total_leads=total_leads,
        stage_counts=stages,
        total_estimated_value=round(total_val, 2),
        hot_leads_count=hot_count,
    )


@router.get(
    "",
    response_model=LeadListResponse,
    summary="List Leads",
)
async def list_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    lead_status: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    min_score: Optional[int] = Query(None, ge=0, le=100),
    assigned_user_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> LeadListResponse:
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is required.")

    stmt = (
        select(Lead)
        .options(selectinload(Lead.customer), selectinload(Lead.assigned_user))
        .where(Lead.tenant_id == tenant_id)
    )

    if lead_status:
        stmt = stmt.where(Lead.status == lead_status.upper())

    if priority:
        stmt = stmt.where(Lead.priority == priority.upper())

    if min_score is not None:
        stmt = stmt.where(Lead.score >= min_score)

    if assigned_user_id:
        stmt = stmt.where(Lead.assigned_user_id == assigned_user_id)

    if search:
        pat = f"%{search.strip().lower()}%"
        stmt = stmt.outerjoin(Customer, Lead.customer_id == Customer.id).where(
            func.lower(Lead.title).like(pat)
            | func.lower(Customer.name).like(pat)
            | func.lower(Customer.phone).like(pat)
            | func.lower(Customer.email).like(pat)
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(Lead.score.desc(), Lead.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    pages = math.ceil(total / page_size) if page_size > 0 else 1

    return LeadListResponse(
        items=[_to_lead_response(l) for l in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{lead_id}",
    response_model=LeadResponse,
    summary="Get Lead Details",
)
async def get_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> LeadResponse:
    stmt = (
        select(Lead)
        .options(selectinload(Lead.customer), selectinload(Lead.assigned_user))
        .where(Lead.id == lead_id, Lead.tenant_id == current_user.tenant_id)
    )
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    return _to_lead_response(lead)


@router.put(
    "/{lead_id}",
    response_model=LeadResponse,
    summary="Update Lead Stage, Priority, or Assignment",
)
async def update_lead(
    lead_id: uuid.UUID,
    data: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> LeadResponse:
    stmt = (
        select(Lead)
        .options(selectinload(Lead.customer), selectinload(Lead.assigned_user))
        .where(Lead.id == lead_id, Lead.tenant_id == current_user.tenant_id)
    )
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    up_dict = data.model_dump(exclude_unset=True)
    for field, val in up_dict.items():
        if field == "status" and val:
            val = val.upper()
        if field == "priority" and val:
            val = val.upper()
        setattr(lead, field, val)

    await db.commit()
    await db.refresh(lead)
    return _to_lead_response(lead)


@router.delete(
    "/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Lead",
)
async def delete_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> None:
    stmt = select(Lead).where(Lead.id == lead_id, Lead.tenant_id == current_user.tenant_id)
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    await db.delete(lead)
    await db.commit()
