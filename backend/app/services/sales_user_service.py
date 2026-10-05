"""
Sales User management service — Tenant Admin creates and manages Sales Users
inside their own tenant.

Every operation is scoped by the tenant resolved from the authenticated
Tenant Admin (TenantAdminContext); client-supplied tenant IDs are never used.
"""
import logging
import math
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import TenantAdminContext
from app.core.security import hash_password
from app.models.audit_log import AuditLogAction, AuditLogResourceType
from app.models.user import User, UserRole
from app.repositories.subscription_plan_repository import SubscriptionPlanRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.schemas.sales_user import (
    SalesUserCreate,
    SalesUserListResponse,
    SalesUserResetPassword,
    SalesUserResponse,
    SalesUserStatusResponse,
    SalesUserStatusUpdate,
    SalesUserUpdate,
)
from app.services.audit_service import AuditService
from app.services.lead_stats_provider import LeadStatsProvider

logger = logging.getLogger(__name__)

EMAIL_EXISTS_MESSAGE = "Email already exists."


def _audit_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class SalesUserService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._users = UserRepository(db)
        self._tenants = TenantRepository(db)
        self._plans = SubscriptionPlanRepository(db)
        self._leads = LeadStatsProvider(db)
        self._audit = AuditService(db)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _get_sales_user_or_404(self, ctx: TenantAdminContext, user_id: uuid.UUID) -> User:
        user = await self._users.get_by_id_in_tenant(user_id, ctx.tenant_id, UserRole.SALES_USER)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales user not found.")
        return user

    async def _enforce_user_limit(self, ctx: TenantAdminContext) -> None:
        tenant = await self._tenants.get_by_id_for_update(ctx.tenant_id)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.")

        plan = await self._plans.get_by_id(tenant.subscription_plan_id) if tenant.subscription_plan_id else None
        if not plan or not plan.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your organization does not have an active subscription plan. "
                       "Please contact support to activate a plan before adding users.",
            )

        current_count = await self._users.count_users_in_tenant(ctx.tenant_id)
        if current_count >= plan.max_users:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your subscription plan allows a maximum of {plan.max_users} users. "
                       "User limit reached; upgrade your plan to add more users.",
            )

    async def _status_response(self, ctx: TenantAdminContext, user: User) -> SalesUserStatusResponse:
        open_leads = await self._leads.count_open_leads(ctx.tenant_id, user.id)
        base = SalesUserResponse.model_validate(user).model_dump()
        return SalesUserStatusResponse(**base, has_open_leads=open_leads > 0, open_leads_count=open_leads)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    async def create_sales_user(
        self, ctx: TenantAdminContext, data: SalesUserCreate, request: Optional[Request] = None
    ) -> SalesUserResponse:
        await self._enforce_user_limit(ctx)

        if await self._users.email_exists(data.email):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_EXISTS_MESSAGE)

        user = User(
            tenant_id=ctx.tenant_id,
            first_name=data.first_name,
            last_name=data.last_name,
            email=data.email,
            phone_number=data.phone_number,
            password_hash=hash_password(data.password),
            role=UserRole.SALES_USER,
            is_active=True,
            is_available=data.is_available,
        )
        try:
            created = await self._users.create(user)
        except IntegrityError:
            # Concurrent insert won the race on the unique email index
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_EXISTS_MESSAGE)

        await self._audit.create_audit_log(
            action=AuditLogAction.USER_CREATED,
            resource_type=AuditLogResourceType.USER,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.id,
            resource_id=str(created.id),
            description=f"Sales user '{created.email}' created.",
            new_values=data.model_dump(exclude={"password"}),
            request=request,
        )
        logger.info("Sales user created user_id=%s tenant_id=%s by=%s", created.id, ctx.tenant_id, ctx.user.id)
        return SalesUserResponse.model_validate(created)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    async def get_sales_user(self, ctx: TenantAdminContext, user_id: uuid.UUID) -> SalesUserResponse:
        user = await self._get_sales_user_or_404(ctx, user_id)
        return SalesUserResponse.model_validate(user)

    async def list_sales_users(
        self,
        ctx: TenantAdminContext,
        page: int,
        page_size: int,
        search: Optional[str],
        is_active: Optional[bool],
        is_available: Optional[bool],
        sort_by: str,
        sort_order: str,
    ) -> SalesUserListResponse:
        items, total = await self._users.list_sales_users(
            tenant_id=ctx.tenant_id,
            page=page,
            page_size=page_size,
            search=search,
            is_active=is_active,
            is_available=is_available,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total_pages = math.ceil(total / page_size) if total > 0 else 1
        return SalesUserListResponse(
            items=[SalesUserResponse.model_validate(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------
    async def update_sales_user(
        self, ctx: TenantAdminContext, user_id: uuid.UUID, data: SalesUserUpdate, request: Optional[Request] = None
    ) -> SalesUserResponse:
        user = await self._get_sales_user_or_404(ctx, user_id)
        update_data: Dict[str, Any] = data.model_dump(exclude_unset=True)

        if update_data.get("is_available") and not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An inactive user cannot be marked as available. Activate the user first.",
            )

        if not update_data:
            return SalesUserResponse.model_validate(user)

        old_values = {k: _audit_value(getattr(user, k)) for k in update_data}
        updated = await self._users.update(user, update_data)

        await self._audit.create_audit_log(
            action=AuditLogAction.USER_UPDATED,
            resource_type=AuditLogResourceType.USER,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.id,
            resource_id=str(user_id),
            description=f"Sales user '{updated.email}' updated.",
            old_values=old_values,
            new_values={k: _audit_value(v) for k, v in update_data.items()},
            request=request,
        )
        logger.info("Sales user updated user_id=%s tenant_id=%s by=%s", user_id, ctx.tenant_id, ctx.user.id)
        return SalesUserResponse.model_validate(updated)

    # ------------------------------------------------------------------
    # Activate / Deactivate
    # ------------------------------------------------------------------
    async def update_sales_user_status(
        self,
        ctx: TenantAdminContext,
        user_id: uuid.UUID,
        data: SalesUserStatusUpdate,
        request: Optional[Request] = None,
    ) -> SalesUserStatusResponse:
        user = await self._get_sales_user_or_404(ctx, user_id)
        old_values = {"is_active": user.is_active, "is_available": user.is_available}

        if user.is_active == data.is_active:
            return await self._status_response(ctx, user)

        if data.is_active:
            update_data: Dict[str, Any] = {"is_active": True}
            action = AuditLogAction.USER_ACTIVATED
        else:
            update_data = {
                "is_active": False,
                "is_available": False,
                "token_version": user.token_version + 1,
            }
            action = AuditLogAction.USER_DEACTIVATED

        updated = await self._users.update(user, update_data)

        await self._audit.create_audit_log(
            action=action,
            resource_type=AuditLogResourceType.USER,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.id,
            resource_id=str(user_id),
            description=f"Sales user '{updated.email}' {'activated' if data.is_active else 'deactivated'}.",
            old_values=old_values,
            new_values={"is_active": updated.is_active, "is_available": updated.is_available},
            request=request,
        )
        logger.info(
            "Sales user status changed user_id=%s tenant_id=%s is_active=%s by=%s",
            user_id, ctx.tenant_id, updated.is_active, ctx.user.id,
        )
        return await self._status_response(ctx, updated)

    # ------------------------------------------------------------------
    # Reset password
    # ------------------------------------------------------------------
    async def reset_password(
        self,
        ctx: TenantAdminContext,
        user_id: uuid.UUID,
        data: SalesUserResetPassword,
        request: Optional[Request] = None,
    ) -> dict:
        user = await self._get_sales_user_or_404(ctx, user_id)
        await self._users.update(
            user,
            {
                "password_hash": hash_password(data.new_password),
                "token_version": user.token_version + 1,
            },
        )

        await self._audit.create_audit_log(
            action=AuditLogAction.USER_PASSWORD_RESET,
            resource_type=AuditLogResourceType.USER,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.id,
            resource_id=str(user_id),
            description=f"Password reset for sales user '{user.email}'.",
            request=request,
        )
        logger.info("Sales user password reset user_id=%s tenant_id=%s by=%s", user_id, ctx.tenant_id, ctx.user.id)
        return {"detail": "Password reset successfully. Existing sessions have been signed out."}

    # ------------------------------------------------------------------
    # Delete (hard)
    # ------------------------------------------------------------------
    async def delete_sales_user(
        self, ctx: TenantAdminContext, user_id: uuid.UUID, request: Optional[Request] = None
    ) -> None:
        user = await self._get_sales_user_or_404(ctx, user_id)

        open_leads = await self._leads.count_open_leads(ctx.tenant_id, user.id)
        if open_leads > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This user has {open_leads} open lead(s). Reassign them before deleting the user.",
            )

        email = user.email
        await self._audit.create_audit_log(
            action=AuditLogAction.USER_DELETED,
            resource_type=AuditLogResourceType.USER,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.id,
            resource_id=str(user_id),
            description=f"Sales user '{email}' deleted.",
            old_values={"email": email, "first_name": user.first_name, "last_name": user.last_name},
            request=request,
        )
        await self._users.delete(user)
        logger.info("Sales user deleted user_id=%s tenant_id=%s by=%s", user_id, ctx.tenant_id, ctx.user.id)
