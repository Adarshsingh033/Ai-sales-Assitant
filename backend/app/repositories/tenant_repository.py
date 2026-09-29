import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant, TenantStatus


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant: Tenant) -> Tenant:
        self._session.add(tenant)
        await self._session.flush()
        return tenant

    async def get_by_id(self, tenant_id: uuid.UUID) -> Optional[Tenant]:
        stmt = select(Tenant).where(Tenant.id == tenant_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Optional[Tenant]:
        stmt = select(Tenant).where(Tenant.slug == slug)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_by_slug(self, slug: str) -> bool:
        stmt = select(func.count(Tenant.id)).where(Tenant.slug == slug)
        result = await self._session.execute(stmt)
        return result.scalar_one() > 0

    async def list(
        self,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        status: Optional[TenantStatus] = None,
        industry: Optional[str] = None,
    ) -> Tuple[List[Tenant], int]:
        stmt = select(Tenant)
        count_stmt = select(func.count(Tenant.id))

        if search:
            search_filter = Tenant.name.ilike(f"%{search}%")
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        if status:
            stmt = stmt.where(Tenant.status == status)
            count_stmt = count_stmt.where(Tenant.status == status)

        if industry:
            stmt = stmt.where(Tenant.industry == industry)
            count_stmt = count_stmt.where(Tenant.industry == industry)

        # Count total
        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        # Fetch page
        stmt = stmt.order_by(Tenant.created_at.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def update(self, tenant: Tenant, update_data: dict) -> Tenant:
        for key, value in update_data.items():
            setattr(tenant, key, value)
        await self._session.flush()
        return tenant

    async def update_status(self, tenant: Tenant, status: TenantStatus) -> Tenant:
        tenant.status = status
        await self._session.flush()
        return tenant
