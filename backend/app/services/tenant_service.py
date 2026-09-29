import math
import re
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant, TenantStatus
from app.models.user import User
from app.repositories.tenant_repository import TenantRepository
from app.schemas.tenant import (
    TenantCreate,
    TenantListResponse,
    TenantResponse,
    TenantStatusUpdate,
    TenantUpdate,
)


def _generate_slug(name: str) -> str:
    """Generate a URL-safe lowercase slug from a name."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


class TenantService:
    def __init__(self, db: AsyncSession) -> None:
        self._repo = TenantRepository(db)

    async def create_tenant(self, data: TenantCreate, current_user: User) -> TenantResponse:
        base_slug = _generate_slug(data.name)
        slug = base_slug
        
        # Ensure slug uniqueness
        counter = 1
        while await self._repo.exists_by_slug(slug):
            slug = f"{base_slug}-{counter}"
            counter += 1

        tenant = Tenant(
            name=data.name,
            slug=slug,
            legal_name=data.legal_name,
            email=data.email,
            phone=data.phone,
            website=data.website,
            industry=data.industry,
            company_size=data.company_size,
            country=data.country,
            timezone=data.timezone,
            created_by=current_user.id,
        )

        created_tenant = await self._repo.create(tenant)
        return TenantResponse.model_validate(created_tenant)

    async def get_tenant(self, tenant_id: uuid.UUID) -> TenantResponse:
        tenant = await self._repo.get_by_id(tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found.",
            )
        return TenantResponse.model_validate(tenant)

    async def list_tenants(
        self,
        page: int,
        page_size: int,
        search: Optional[str],
        status_filter: Optional[TenantStatus],
        industry: Optional[str],
    ) -> TenantListResponse:
        items, total = await self._repo.list(
            page=page,
            page_size=page_size,
            search=search,
            status=status_filter,
            industry=industry,
        )
        total_pages = math.ceil(total / page_size) if total > 0 else 1
        
        return TenantListResponse(
            items=[TenantResponse.model_validate(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )

    async def update_tenant(self, tenant_id: uuid.UUID, data: TenantUpdate) -> TenantResponse:
        tenant = await self._repo.get_by_id(tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found.",
            )
            
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return TenantResponse.model_validate(tenant)
            
        updated_tenant = await self._repo.update(tenant, update_data)
        return TenantResponse.model_validate(updated_tenant)

    async def update_tenant_status(self, tenant_id: uuid.UUID, data: TenantStatusUpdate) -> TenantResponse:
        tenant = await self._repo.get_by_id(tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found.",
            )
            
        updated_tenant = await self._repo.update_status(tenant, data.status)
        return TenantResponse.model_validate(updated_tenant)
