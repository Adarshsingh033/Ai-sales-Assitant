import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import TenantAdminContext, require_tenant_admin_context
from app.schemas.sales_user import (
    SalesUserCreate,
    SalesUserListResponse,
    SalesUserResetPassword,
    SalesUserResponse,
    SalesUserStatusResponse,
    SalesUserStatusUpdate,
    SalesUserUpdate,
)
from app.services.sales_user_service import SalesUserService

# Must be included after app.routers.users so /users/me is not captured by /users/{user_id}
router = APIRouter(prefix="/users", tags=["Tenant Admin Sales Users"])

SortField = Literal["first_name", "last_name", "email", "created_at", "last_login_at"]


@router.post(
    "",
    response_model=SalesUserResponse,
    status_code=201,
    summary="Create Sales User",
    description="Create a Sales User in the authenticated Tenant Admin's tenant.",
)
async def create_sales_user(
    request: Request,
    data: SalesUserCreate,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> SalesUserResponse:
    return await SalesUserService(db).create_sales_user(ctx, data, request=request)


@router.get(
    "",
    response_model=SalesUserListResponse,
    summary="List Sales Users",
    description="List, search, filter and sort Sales Users within the Tenant Admin's tenant.",
)
async def list_sales_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=255),
    is_active: Optional[bool] = None,
    is_available: Optional[bool] = None,
    sort_by: SortField = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> SalesUserListResponse:
    return await SalesUserService(db).list_sales_users(
        ctx,
        page=page,
        page_size=page_size,
        search=search,
        is_active=is_active,
        is_available=is_available,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/{user_id}",
    response_model=SalesUserResponse,
    summary="Get Sales User",
)
async def get_sales_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> SalesUserResponse:
    return await SalesUserService(db).get_sales_user(ctx, user_id)


@router.put(
    "/{user_id}",
    response_model=SalesUserResponse,
    summary="Update Sales User",
)
async def update_sales_user(
    request: Request,
    user_id: uuid.UUID,
    data: SalesUserUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> SalesUserResponse:
    return await SalesUserService(db).update_sales_user(ctx, user_id, data, request=request)


@router.delete(
    "/{user_id}",
    status_code=204,
    summary="Delete Sales User",
    description="Permanently delete a Sales User from the Tenant Admin's tenant.",
)
async def delete_sales_user(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> None:
    await SalesUserService(db).delete_sales_user(ctx, user_id, request=request)


@router.patch(
    "/{user_id}/status",
    response_model=SalesUserStatusResponse,
    summary="Activate / Deactivate Sales User",
    description="Deactivation revokes sessions, sets is_available=false and reports open leads needing reassignment.",
)
async def update_sales_user_status(
    request: Request,
    user_id: uuid.UUID,
    data: SalesUserStatusUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> SalesUserStatusResponse:
    return await SalesUserService(db).update_sales_user_status(ctx, user_id, data, request=request)


@router.post(
    "/{user_id}/reset-password",
    summary="Reset Sales User Password",
    description="Set a new password for a Sales User and sign out all of their sessions.",
)
async def reset_sales_user_password(
    request: Request,
    user_id: uuid.UUID,
    data: SalesUserResetPassword,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> dict:
    return await SalesUserService(db).reset_password(ctx, user_id, data, request=request)
