import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing_cycle import BillingCycleModel


class BillingCycleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, cycle: BillingCycleModel) -> BillingCycleModel:
        self._session.add(cycle)
        await self._session.flush()
        return cycle

    async def get_by_id(self, cycle_id: uuid.UUID) -> Optional[BillingCycleModel]:
        stmt = select(BillingCycleModel).where(BillingCycleModel.id == cycle_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Optional[BillingCycleModel]:
        stmt = select(BillingCycleModel).where(BillingCycleModel.slug == slug)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_by_slug(self, slug: str) -> bool:
        stmt = select(func.count(BillingCycleModel.id)).where(BillingCycleModel.slug == slug)
        result = await self._session.execute(stmt)
        return result.scalar_one() > 0

    async def list(
        self,
        page: int = 1,
        page_size: int = 50,
        active_only: bool = False,
    ) -> Tuple[List[BillingCycleModel], int]:
        stmt = select(BillingCycleModel)
        count_stmt = select(func.count(BillingCycleModel.id))

        if active_only:
            stmt = stmt.where(BillingCycleModel.is_active == True)
            count_stmt = count_stmt.where(BillingCycleModel.is_active == True)

        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        stmt = stmt.order_by(BillingCycleModel.duration_months.asc(), BillingCycleModel.name.asc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        result = await self._session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def update(self, cycle: BillingCycleModel, update_data: dict) -> BillingCycleModel:
        for key, value in update_data.items():
            setattr(cycle, key, value)
        await self._session.flush()
        return cycle

    async def update_status(self, cycle: BillingCycleModel, is_active: bool) -> BillingCycleModel:
        cycle.is_active = is_active
        await self._session.flush()
        return cycle

    async def delete(self, cycle: BillingCycleModel) -> None:
        await self._session.delete(cycle)
        await self._session.flush()
