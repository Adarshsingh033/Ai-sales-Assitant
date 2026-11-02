"""
Tenant Admin & Sales User Dashboard Statistics endpoint.
Aggregates sales pipeline, conversion metrics, AI vs human handoff rates, and recent activity — plus the
"home" summary (what needs attention today and, for admins, the first-run setup checklist).

A Sales User's numbers only cover what is assigned to them.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import CRMContext, require_crm_context
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation
from app.models.crm import Task
from app.models.knowledge import KnowledgeDocument
from app.models.knowledge_gap import KnowledgeGap
from app.models.lead import Lead
from app.models.product import Product
from app.models.user import User, UserRole
from app.schemas.crm import HomeSummary, SetupStep, TaskResponse
from app.services import crm

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
    resolved_conversations: int = 0
    total_products: int
    total_knowledge_docs: int
    recent_leads: List[DashboardLeadSummary]
    recent_conversations: List[DashboardConvSummary]


async def _count(db: AsyncSession, column, *where) -> int:
    return (await db.execute(select(func.count(column)).where(*where))).scalar_one()


@router.get("", response_model=TenantDashboardStats, summary="Get Tenant Sales Dashboard Stats")
async def get_tenant_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> TenantDashboardStats:
    tenant_id = ctx.tenant_id
    leads = crm.lead_scope(ctx)
    convs = crm.conversation_scope(ctx)

    # --- Lead Metrics ---
    total_leads = await _count(db, Lead.id, *leads)
    open_deals = Lead.status.notin_(crm.CLOSED_STAGES)
    # "Hot" follows each deal's priority (set from the tenant's own qualification threshold, or by a person), and only
    # deals still in play count; the pipeline value is likewise only what is still open.
    hot_leads = await _count(db, Lead.id, *leads, open_deals, Lead.priority == "HOT")
    qualified_leads = await _count(db, Lead.id, *leads, Lead.status == "QUALIFIED")
    won_leads = await _count(db, Lead.id, *leads, Lead.status == "WON")
    pipeline_val = (await db.execute(select(func.sum(Lead.budget)).where(*leads, open_deals))).scalar_one()
    total_pipeline_value = float(pipeline_val or 0.0)

    # --- Conversation Metrics ---
    total_convs = await _count(db, Conversation.id, *convs)
    active_ai = await _count(db, Conversation.id, *convs, Conversation.status == "ACTIVE_AI")
    waiting_sales = await _count(db, Conversation.id, *convs, Conversation.status == "WAITING_SALES")
    escalated = await _count(db, Conversation.id, *convs, Conversation.status == "ESCALATED_HUMAN")
    resolved = await _count(db, Conversation.id, *convs, Conversation.status == "RESOLVED")

    # --- Products & Knowledge (the company's setup, not a rep's concern) ---
    total_products = total_docs = 0
    if ctx.is_admin:
        total_products = await _count(db, Product.id, Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712
        total_docs = await _count(db, KnowledgeDocument.id, KnowledgeDocument.tenant_id == tenant_id)

    # --- Recent Leads ---
    recent_lead_rows = (
        await db.execute(
            select(Lead).options(selectinload(Lead.customer)).where(*leads).order_by(Lead.created_at.desc()).limit(5)
        )
    ).scalars().all()
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
    recent_conv_rows = (
        await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.customer))
            .where(*convs)
            .order_by(Conversation.last_activity_at.desc())
            .limit(5)
        )
    ).scalars().all()
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
        resolved_conversations=resolved,
        total_products=total_products,
        total_knowledge_docs=total_docs,
        recent_leads=recent_leads,
        recent_conversations=recent_conversations,
    )


async def _setup_steps(db: AsyncSession, ctx: CRMContext) -> List[SetupStep]:
    tid = ctx.tenant_id
    products = await _count(db, Product.id, Product.tenant_id == tid, Product.is_active == True)  # noqa: E712
    docs = await _count(db, KnowledgeDocument.id, KnowledgeDocument.tenant_id == tid)
    reps = await _count(db, User.id, User.tenant_id == tid, User.role == UserRole.SALES_USER)
    chats = await _count(db, Conversation.id, Conversation.tenant_id == tid)
    config = (await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tid))).scalar_one_or_none()
    ai_ready = bool(config and (config.groq_api_key or settings.GROQ_API_KEY))
    return [
        SetupStep(key="products", title="Add your products", done=products > 0, target="section-products",
                  hint="The assistant quotes prices and checks stock from your catalog."),
        SetupStep(key="knowledge", title="Teach the assistant about your business", done=docs > 0, target="section-knowledge",
                  hint="Upload FAQs, brochures or policies so answers come from your own documents."),
        SetupStep(key="ai", title="Turn on the AI assistant", done=ai_ready, target="section-ai-config",
                  hint="Add your AI key and choose how the assistant introduces itself."),
        SetupStep(key="team", title="Add your sales team", done=reps > 0, target="section-user-management",
                  hint="New leads are shared out between them automatically."),
        SetupStep(key="chat", title="Try a test chat", done=chats > 0, target="section-conversations",
                  hint="Chat as a customer to see how the assistant answers and captures leads."),
    ]


@router.get("/home", response_model=HomeSummary, summary="What needs attention today")
async def get_home_summary(
    day_end: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> HomeSummary:
    from app.routers.tenant_admin.tasks import _day_end, _select_tasks, _to_response, compute_summary

    # An admin's home shows their own to-dos; the Tasks page has the whole team's.
    summary = await compute_summary(db, ctx, day_end, mine=True)
    focus_rows = (
        await db.execute(
            _select_tasks()
            .where(*crm.task_scope(ctx), Task.status == "OPEN", Task.due_at.is_not(None), Task.due_at < _day_end(day_end),
                   *([Task.assigned_user_id == ctx.user.id] if ctx.is_admin else []))
            .order_by(Task.due_at.asc())
            .limit(6)
        )
    ).all()

    leads = crm.lead_scope(ctx)
    stage_rows = (await db.execute(select(Lead.status, func.count(Lead.id)).where(*leads).group_by(Lead.status))).all()
    needs_human = await _count(
        db, Conversation.id, *crm.conversation_scope(ctx), Conversation.status.in_(["WAITING_SALES", "ESCALATED_HUMAN"])
    )
    unassigned = 0
    unanswered = 0
    setup = None
    if ctx.is_admin:
        unanswered = await _count(db, KnowledgeGap.id, KnowledgeGap.tenant_id == ctx.tenant_id, KnowledgeGap.status == "OPEN")
        unassigned = await _count(
            db, Lead.id, Lead.tenant_id == ctx.tenant_id, Lead.assigned_user_id.is_(None), Lead.status.notin_(crm.CLOSED_STAGES)
        )
        setup = await _setup_steps(db, ctx)

    return HomeSummary(
        tasks=summary,
        focus_tasks=[_to_response(*r) for r in focus_rows],
        unassigned_leads=unassigned,
        needs_human=needs_human,
        unanswered_questions=unanswered,
        setup=setup,
        stage_counts={st: n for st, n in stage_rows},
    )
