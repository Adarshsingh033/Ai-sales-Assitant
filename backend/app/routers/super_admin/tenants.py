import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_super_admin
from app.models.tenant import TenantStatus
from app.models.user import User
from app.schemas.subscription_plan import TenantPlanAssignment
from app.schemas.tenant import (
    TenantCreate,
    TenantListResponse,
    TenantResponse,
    TenantStatusUpdate,
    TenantUpdate,
)
from app.services.tenant_service import TenantService

router = APIRouter(prefix="/super-admin/tenants", tags=["Super Admin Tenants"])


@router.post(
    "",
    response_model=TenantResponse,
    summary="Create Tenant",
    description="Create a new tenant. Accessible only by Super Admin.",
)
async def create_tenant(
    request: Request,
    data: TenantCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> TenantResponse:
    service = TenantService(db)
    return await service.create_tenant(data, current_user, request=request)


@router.get(
    "",
    response_model=TenantListResponse,
    summary="List Tenants",
    description="List and filter tenants with pagination. Accessible only by Super Admin.",
)
async def list_tenants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    status: Optional[TenantStatus] = None,
    industry: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> TenantListResponse:
    service = TenantService(db)
    return await service.list_tenants(
        page=page,
        page_size=page_size,
        search=search,
        status_filter=status,
        industry=industry,
    )


@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Get Tenant",
    description="Get tenant details by ID. Accessible only by Super Admin.",
)
async def get_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> TenantResponse:
    service = TenantService(db)
    return await service.get_tenant(tenant_id)


@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Update Tenant",
    description="Update tenant information. Accessible only by Super Admin.",
)
async def update_tenant(
    request: Request,
    tenant_id: uuid.UUID,
    data: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> TenantResponse:
    service = TenantService(db)
    return await service.update_tenant(tenant_id, data, current_user, request=request)


@router.patch(
    "/{tenant_id}/status",
    response_model=TenantResponse,
    summary="Update Tenant Status",
    description="Activate, deactivate, or suspend a tenant. Accessible only by Super Admin.",
)
async def update_tenant_status(
    request: Request,
    tenant_id: uuid.UUID,
    data: TenantStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> TenantResponse:
    service = TenantService(db)
    return await service.update_tenant_status(tenant_id, data, current_user, request=request)

@router.patch(
    "/{tenant_id}/subscription-plan",
    response_model=TenantResponse,
    summary="Assign Subscription Plan to Tenant",
    description="Assign an active subscription plan to a tenant. Accessible only by Super Admin.",
)
async def assign_subscription_plan(
    request: Request,
    tenant_id: uuid.UUID,
    data: TenantPlanAssignment,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> TenantResponse:
    service = TenantService(db)
    return await service.assign_subscription_plan(tenant_id, data.subscription_plan_id, current_user, request=request)
