"""
Tenant Admin & Sales User Dashboard Statistics endpoint.
Aggregates sales pipeline, conversion metrics, AI vs human handoff rates, and recent activity.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import require_sales_user
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.knowledge import KnowledgeDocument
from app.models.lead import Lead
from app.models.product import Product
from app.models.user import User

router = APIRouter(prefix="/tenant-admin/dashboard", tags=["Tenant Admin Dashboard"])


class DashboardLeadSummary(BaseModel):
    id: str
    title: str
    status: str
    score: int
    priority: str
    budget: Optional[float] = None
    customer_name: Optional[str] = None
    created_at: str


class DashboardConvSummary(BaseModel):
    id: str
    customer_name: Optional[str] = None
    channel: str
    status: str
    detected_intent: Optional[str] = None
    last_activity_at: str


class TenantDashboardStats(BaseModel):
    total_leads: int
    hot_leads: int
    qualified_leads: int
    won_leads: int
    total_pipeline_value: float
    total_conversations: int
    active_ai_conversations: int
    waiting_sales_conversations: int
    escalated_conversations: int
    total_products: int
    total_knowledge_docs: int
    recent_leads: List[DashboardLeadSummary]
    recent_conversations: List[DashboardConvSummary]


@router.get("", response_model=TenantDashboardStats, summary="Get Tenant Sales Dashboard Stats")
async def get_tenant_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> TenantDashboardStats:
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is required.")

    # --- Lead Metrics ---
    total_leads = (
        await db.execute(select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id))
    ).scalar_one()

    hot_leads = (
        await db.execute(
            select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id, Lead.score >= 80)
        )
    ).scalar_one()

    qualified_leads = (
        await db.execute(
            select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id, Lead.status == "QUALIFIED")
        )
    ).scalar_one()

    won_leads = (
        await db.execute(
            select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id, Lead.status == "WON")
        )
    ).scalar_one()

    pipeline_val = (
        await db.execute(select(func.sum(Lead.budget)).where(Lead.tenant_id == tenant_id))
    ).scalar_one()
    total_pipeline_value = float(pipeline_val or 0.0)

    # --- Conversation Metrics ---
    total_convs = (
        await db.execute(select(func.count(Conversation.id)).where(Conversation.tenant_id == tenant_id))
    ).scalar_one()

    active_ai = (
        await db.execute(
            select(func.count(Conversation.id)).where(
                Conversation.tenant_id == tenant_id, Conversation.status == "ACTIVE_AI"
            )
        )
    ).scalar_one()

    waiting_sales = (
        await db.execute(
            select(func.count(Conversation.id)).where(
                Conversation.tenant_id == tenant_id, Conversation.status == "WAITING_SALES"
            )
        )
    ).scalar_one()

    escalated = (
        await db.execute(
            select(func.count(Conversation.id)).where(
                Conversation.tenant_id == tenant_id, Conversation.status == "ESCALATED_HUMAN"
            )
        )
    ).scalar_one()

    # --- Products & Knowledge ---
    total_products = (
        await db.execute(
            select(func.count(Product.id)).where(Product.tenant_id == tenant_id, Product.is_active == True)
        )
    ).scalar_one()

    total_docs = (
        await db.execute(
            select(func.count(KnowledgeDocument.id)).where(KnowledgeDocument.tenant_id == tenant_id)
        )
    ).scalar_one()

    # --- Recent Leads ---
    stmt_recent_leads = (
        select(Lead)
        .options(selectinload(Lead.customer))
        .where(Lead.tenant_id == tenant_id)
        .order_by(Lead.created_at.desc())
        .limit(5)
    )
    recent_lead_rows = (await db.execute(stmt_recent_leads)).scalars().all()
    recent_leads = [
        DashboardLeadSummary(
            id=str(l.id),
            title=l.title,
            status=l.status,
            score=l.score,
            priority=l.priority,
            budget=float(l.budget) if l.budget is not None else None,
            customer_name=l.customer.name if l.customer else None,
            created_at=l.created_at.isoformat(),
        )
        for l in recent_lead_rows
    ]

    # --- Recent Conversations ---
    stmt_recent_convs = (
        select(Conversation)
        .options(selectinload(Conversation.customer))
        .where(Conversation.tenant_id == tenant_id)
        .order_by(Conversation.last_activity_at.desc())
        .limit(5)
    )
    recent_conv_rows = (await db.execute(stmt_recent_convs)).scalars().all()
    recent_conversations = [
        DashboardConvSummary(
            id=str(c.id),
            customer_name=c.customer.name if c.customer else "Unknown",
            channel=c.channel,
            status=c.status,
            detected_intent=c.detected_intent,
            last_activity_at=c.last_activity_at.isoformat(),
        )
        for c in recent_conv_rows
    ]

    return TenantDashboardStats(
        total_leads=total_leads,
        hot_leads=hot_leads,
        qualified_leads=qualified_leads,
        won_leads=won_leads,
        total_pipeline_value=round(total_pipeline_value, 2),
        total_conversations=total_convs,
        active_ai_conversations=active_ai,
        waiting_sales_conversations=waiting_sales,
        escalated_conversations=escalated,
        total_products=total_products,
        total_knowledge_docs=total_docs,
        recent_leads=recent_leads,
        recent_conversations=recent_conversations,
    )
