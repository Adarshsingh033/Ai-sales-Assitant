import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_super_admin
from app.models.user import User
from app.schemas.subscription_plan import (
    SubscriptionPlanCreate,
    SubscriptionPlanListResponse,
    SubscriptionPlanResponse,
    SubscriptionPlanStatusUpdate,
    SubscriptionPlanUpdate,
)
from app.services.subscription_plan_service import SubscriptionPlanService

router = APIRouter(prefix="/super-admin/subscription-plans", tags=["Super Admin Subscription Plans"])


@router.post(
    "",
    response_model=SubscriptionPlanResponse,
    summary="Create Subscription Plan",
)
async def create_plan(
    data: SubscriptionPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> SubscriptionPlanResponse:
    service = SubscriptionPlanService(db)
    return await service.create_plan(data)


@router.get(
    "",
    response_model=SubscriptionPlanListResponse,
    summary="List Subscription Plans",
)
async def list_plans(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> SubscriptionPlanListResponse:
    service = SubscriptionPlanService(db)
    return await service.list_plans(page=page, page_size=page_size)


@router.get(
    "/{plan_id}",
    response_model=SubscriptionPlanResponse,
    summary="Get Subscription Plan",
)
async def get_plan(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> SubscriptionPlanResponse:
    service = SubscriptionPlanService(db)
    return await service.get_plan(plan_id)


@router.patch(
    "/{plan_id}",
    response_model=SubscriptionPlanResponse,
    summary="Update Subscription Plan",
)
async def update_plan(
    plan_id: uuid.UUID,
    data: SubscriptionPlanUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> SubscriptionPlanResponse:
    service = SubscriptionPlanService(db)
    return await service.update_plan(plan_id, data)


@router.patch(
    "/{plan_id}/status",
    response_model=SubscriptionPlanResponse,
    summary="Update Subscription Plan Status",
)
async def update_plan_status(
    plan_id: uuid.UUID,
    data: SubscriptionPlanStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> SubscriptionPlanResponse:
    service = SubscriptionPlanService(db)
    return await service.update_plan_status(plan_id, data)
