import math
import re
import uuid
from typing import Optional

from fastapi import HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLogAction, AuditLogResourceType
from app.models.subscription_plan import SubscriptionPlan
from app.models.user import User
from app.repositories.subscription_plan_repository import SubscriptionPlanRepository
from app.schemas.subscription_plan import (
    SubscriptionPlanCreate,
    SubscriptionPlanListResponse,
    SubscriptionPlanResponse,
    SubscriptionPlanResponse,
    SubscriptionPlanStatusUpdate,
    SubscriptionPlanUpdate,
)
from app.services.audit_service import AuditService


def _generate_slug(name: str) -> str:
    """Generate a URL-safe lowercase slug from a name."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


class SubscriptionPlanService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = SubscriptionPlanRepository(db)

    async def create_plan(self, data: SubscriptionPlanCreate, current_user: User, request: Optional[Request] = None) -> SubscriptionPlanResponse:
        base_slug = _generate_slug(data.name)
        slug = base_slug
        
        counter = 1
        while await self._repo.exists_by_slug(slug):
            slug = f"{base_slug}-{counter}"
            counter += 1

        plan = SubscriptionPlan(
            name=data.name,
            slug=slug,
            description=data.description,
            price=data.price,
            currency=data.currency,
            billing_cycle=data.billing_cycle,
            max_users=data.max_users,
            max_branches=data.max_branches,
            max_leads=data.max_leads,
            max_ai_usage=data.max_ai_usage,
            max_storage=data.max_storage,
        )

        created_plan = await self._repo.create(plan)
        
        audit = AuditService(self._db)
        await audit.create_audit_log(
            action=AuditLogAction.SUBSCRIPTION_PLAN_CREATED,
            resource_type=AuditLogResourceType.SUBSCRIPTION_PLAN,
            user_id=current_user.id,
            resource_id=str(created_plan.id),
            description=f"Subscription plan '{created_plan.name}' created.",
            new_values=data.model_dump(),
            request=request
        )
        
        return SubscriptionPlanResponse.model_validate(created_plan)

    async def get_plan(self, plan_id: uuid.UUID) -> SubscriptionPlanResponse:
        plan = await self._repo.get_by_id(plan_id)
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )
        return SubscriptionPlanResponse.model_validate(plan)

    async def list_plans(self, page: int, page_size: int) -> SubscriptionPlanListResponse:
        items, total = await self._repo.list(page=page, page_size=page_size)
        total_pages = math.ceil(total / page_size) if total > 0 else 1
        
        return SubscriptionPlanListResponse(
            items=[SubscriptionPlanResponse.model_validate(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )

    async def update_plan(self, plan_id: uuid.UUID, data: SubscriptionPlanUpdate, current_user: User, request: Optional[Request] = None) -> SubscriptionPlanResponse:
        plan = await self._repo.get_by_id(plan_id)
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )
            
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return SubscriptionPlanResponse.model_validate(plan)
            
        old_values = {k: getattr(plan, k) for k in update_data.keys()}
        updated_plan = await self._repo.update(plan, update_data)
        
        audit = AuditService(self._db)
        await audit.create_audit_log(
            action=AuditLogAction.SUBSCRIPTION_PLAN_UPDATED,
            resource_type=AuditLogResourceType.SUBSCRIPTION_PLAN,
            user_id=current_user.id,
            resource_id=str(plan_id),
            description="Subscription plan details updated.",
            old_values=old_values,
            new_values=update_data,
            request=request
        )
        
        return SubscriptionPlanResponse.model_validate(updated_plan)

    async def update_plan_status(self, plan_id: uuid.UUID, data: SubscriptionPlanStatusUpdate, current_user: User, request: Optional[Request] = None) -> SubscriptionPlanResponse:
        plan = await self._repo.get_by_id(plan_id)
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )
            
        old_status = plan.is_active
        updated_plan = await self._repo.update_status(plan, data.is_active)
        
        action = AuditLogAction.SUBSCRIPTION_PLAN_ACTIVATED if data.is_active else AuditLogAction.SUBSCRIPTION_PLAN_DEACTIVATED
        
        audit = AuditService(self._db)
        await audit.create_audit_log(
            action=action,
            resource_type=AuditLogResourceType.SUBSCRIPTION_PLAN,
            user_id=current_user.id,
            resource_id=str(plan_id),
            description=f"Subscription plan status changed to {'active' if data.is_active else 'inactive'}.",
            old_values={"is_active": old_status},
            new_values={"is_active": data.is_active},
            request=request
        )
        
        return SubscriptionPlanResponse.model_validate(updated_plan)
