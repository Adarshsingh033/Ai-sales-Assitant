import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_super_admin
from app.models.user import User
from app.schemas.billing_cycle import (
    BillingCycleCreate,
    BillingCycleListResponse,
    BillingCycleResponse,
    BillingCycleStatusUpdate,
    BillingCycleUpdate,
)
from app.services.billing_cycle_service import BillingCycleService

router = APIRouter(prefix="/super-admin/billing-cycles", tags=["Super Admin Billing Cycles"])


@router.post(
    "",
    response_model=BillingCycleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Billing Cycle",
)
async def create_billing_cycle(
    request: Request,
    data: BillingCycleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> BillingCycleResponse:
    service = BillingCycleService(db)
    return await service.create_billing_cycle(data, current_user, request=request)


@router.get(
    "",
    response_model=BillingCycleListResponse,
    summary="List Billing Cycles",
)
async def list_billing_cycles(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> BillingCycleListResponse:
    service = BillingCycleService(db)
    return await service.list_billing_cycles(page=page, page_size=page_size, active_only=active_only)


@router.get(
    "/{cycle_id}",
    response_model=BillingCycleResponse,
    summary="Get Billing Cycle",
)
async def get_billing_cycle(
    cycle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> BillingCycleResponse:
    service = BillingCycleService(db)
    return await service.get_billing_cycle(cycle_id)


@router.patch(
    "/{cycle_id}",
    response_model=BillingCycleResponse,
    summary="Update Billing Cycle",
)
async def update_billing_cycle(
    request: Request,
    cycle_id: uuid.UUID,
    data: BillingCycleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> BillingCycleResponse:
    service = BillingCycleService(db)
    return await service.update_billing_cycle(cycle_id, data, current_user, request=request)


@router.patch(
    "/{cycle_id}/status",
    response_model=BillingCycleResponse,
    summary="Update Billing Cycle Status",
)
async def update_billing_cycle_status(
    request: Request,
    cycle_id: uuid.UUID,
    data: BillingCycleStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> BillingCycleResponse:
    service = BillingCycleService(db)
    return await service.update_billing_cycle_status(cycle_id, data, current_user, request=request)


@router.delete(
    "/{cycle_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Billing Cycle",
)
async def delete_billing_cycle(
    request: Request,
    cycle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> None:
    service = BillingCycleService(db)
    await service.delete_billing_cycle(cycle_id, current_user, request=request)
