"""
Super Admin Dashboard statistics endpoint.
Returns aggregate metrics for the platform overview dashboard.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_super_admin
from app.models.audit_log import AuditLog
from app.models.billing_cycle import BillingCycleModel
from app.models.subscription_plan import SubscriptionPlan
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User

router = APIRouter(prefix="/super-admin/dashboard", tags=["Super Admin Dashboard"])


class IndustryBreakdown(BaseModel):
    industry: str
    count: int


class RecentTenant(BaseModel):
    id: str
    name: str
    industry: str | None
    status: str
    created_at: datetime


class RecentActivity(BaseModel):
    id: str
    action: str
    resource_type: str
    description: str | None
    created_at: datetime


class DashboardStats(BaseModel):
    # Tenant metrics
    total_tenants: int
    active_tenants: int
    inactive_tenants: int
    suspended_tenants: int
    new_tenants_this_month: int
    new_tenants_last_month: int

    # Subscription metrics
    total_plans: int
    active_plans: int
    total_billing_cycles: int
    active_billing_cycles: int

    # System metrics
    total_audit_events: int
    audit_events_today: int
    total_users: int

    # Breakdowns
    industry_breakdown: list[IndustryBreakdown]
    recent_tenants: list[RecentTenant]
    recent_activity: list[RecentActivity]


@router.get("", response_model=DashboardStats, summary="Get Dashboard Statistics")
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> DashboardStats:
    now = datetime.now(tz=timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_last_month = (start_of_month - timedelta(days=1)).replace(day=1)
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # --- Tenant counts ---
    total_tenants = (await db.execute(select(func.count(Tenant.id)))).scalar_one()
    active_tenants = (await db.execute(
        select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.ACTIVE)
    )).scalar_one()
    inactive_tenants = (await db.execute(
        select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.INACTIVE)
    )).scalar_one()
    suspended_tenants = (await db.execute(
        select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.SUSPENDED)
    )).scalar_one()

    new_tenants_this_month = (await db.execute(
        select(func.count(Tenant.id)).where(Tenant.created_at >= start_of_month)
    )).scalar_one()
    new_tenants_last_month = (await db.execute(
        select(func.count(Tenant.id)).where(
            Tenant.created_at >= start_of_last_month,
            Tenant.created_at < start_of_month,
        )
    )).scalar_one()

    # --- Subscription counts ---
    total_plans = (await db.execute(select(func.count(SubscriptionPlan.id)))).scalar_one()
    active_plans = (await db.execute(
        select(func.count(SubscriptionPlan.id)).where(SubscriptionPlan.is_active == True)
    )).scalar_one()
    total_billing_cycles = (await db.execute(select(func.count(BillingCycleModel.id)))).scalar_one()
    active_billing_cycles = (await db.execute(
        select(func.count(BillingCycleModel.id)).where(BillingCycleModel.is_active == True)
    )).scalar_one()

    # --- Audit / system metrics ---
    total_audit_events = (await db.execute(select(func.count(AuditLog.id)))).scalar_one()
    audit_events_today = (await db.execute(
        select(func.count(AuditLog.id)).where(AuditLog.created_at >= start_of_today)
    )).scalar_one()
    total_users = (await db.execute(select(func.count(User.id)))).scalar_one()

    # --- Industry breakdown ---
    industry_rows = (await db.execute(
        select(Tenant.industry, func.count(Tenant.id).label("count"))
        .where(Tenant.industry.isnot(None))
        .group_by(Tenant.industry)
        .order_by(func.count(Tenant.id).desc())
        .limit(6)
    )).all()
    industry_breakdown = [
        IndustryBreakdown(industry=row[0] or "Unknown", count=row[1])
        for row in industry_rows
    ]

    # --- Recent tenants ---
    recent_rows = (await db.execute(
        select(Tenant)
        .order_by(Tenant.created_at.desc())
        .limit(5)
    )).scalars().all()
    recent_tenants = [
        RecentTenant(
            id=str(t.id),
            name=t.name,
            industry=t.industry,
            status=t.status.value,
            created_at=t.created_at,
        )
        for t in recent_rows
    ]

    # --- Recent activity ---
    activity_rows = (await db.execute(
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(8)
    )).scalars().all()
    recent_activity = [
        RecentActivity(
            id=str(a.id),
            action=a.action.value,
            resource_type=a.resource_type.value,
            description=a.description,
            created_at=a.created_at,
        )
        for a in activity_rows
    ]

    return DashboardStats(
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        inactive_tenants=inactive_tenants,
        suspended_tenants=suspended_tenants,
        new_tenants_this_month=new_tenants_this_month,
        new_tenants_last_month=new_tenants_last_month,
        total_plans=total_plans,
        active_plans=active_plans,
        total_billing_cycles=total_billing_cycles,
        active_billing_cycles=active_billing_cycles,
        total_audit_events=total_audit_events,
        audit_events_today=audit_events_today,
        total_users=total_users,
        industry_breakdown=industry_breakdown,
        recent_tenants=recent_tenants,
        recent_activity=recent_activity,
    )
