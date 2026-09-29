import math
import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription_plan import SubscriptionPlan
from app.repositories.subscription_plan_repository import SubscriptionPlanRepository
from app.schemas.subscription_plan import (
    SubscriptionPlanCreate,
    SubscriptionPlanListResponse,
    SubscriptionPlanResponse,
    SubscriptionPlanStatusUpdate,
    SubscriptionPlanUpdate,
)


def _generate_slug(name: str) -> str:
    """Generate a URL-safe lowercase slug from a name."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


class SubscriptionPlanService:
    def __init__(self, db: AsyncSession) -> None:
        self._repo = SubscriptionPlanRepository(db)

    async def create_plan(self, data: SubscriptionPlanCreate) -> SubscriptionPlanResponse:
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

    async def update_plan(self, plan_id: uuid.UUID, data: SubscriptionPlanUpdate) -> SubscriptionPlanResponse:
        plan = await self._repo.get_by_id(plan_id)
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )
            
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return SubscriptionPlanResponse.model_validate(plan)
            
        updated_plan = await self._repo.update(plan, update_data)
        return SubscriptionPlanResponse.model_validate(updated_plan)

    async def update_plan_status(self, plan_id: uuid.UUID, data: SubscriptionPlanStatusUpdate) -> SubscriptionPlanResponse:
        plan = await self._repo.get_by_id(plan_id)
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )
            
        updated_plan = await self._repo.update_status(plan, data.is_active)
        return SubscriptionPlanResponse.model_validate(updated_plan)
