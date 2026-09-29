import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription_plan import SubscriptionPlan


class SubscriptionPlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, plan: SubscriptionPlan) -> SubscriptionPlan:
        self._session.add(plan)
        await self._session.flush()
        return plan

    async def get_by_id(self, plan_id: uuid.UUID) -> Optional[SubscriptionPlan]:
        stmt = select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Optional[SubscriptionPlan]:
        stmt = select(SubscriptionPlan).where(SubscriptionPlan.slug == slug)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_by_slug(self, slug: str) -> bool:
        stmt = select(func.count(SubscriptionPlan.id)).where(SubscriptionPlan.slug == slug)
        result = await self._session.execute(stmt)
        return result.scalar_one() > 0

    async def list(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[SubscriptionPlan], int]:
        stmt = select(SubscriptionPlan)
        count_stmt = select(func.count(SubscriptionPlan.id))

        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        stmt = stmt.order_by(SubscriptionPlan.created_at.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def update(self, plan: SubscriptionPlan, update_data: dict) -> SubscriptionPlan:
        for key, value in update_data.items():
            setattr(plan, key, value)
        await self._session.flush()
        return plan

    async def update_status(self, plan: SubscriptionPlan, is_active: bool) -> SubscriptionPlan:
        plan.is_active = is_active
        await self._session.flush()
        return plan
