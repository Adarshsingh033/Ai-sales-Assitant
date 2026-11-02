"""
Sales User management service — Tenant Admin creates and manages Sales Users
inside their own tenant.

Every operation is scoped by the tenant resolved from the authenticated
Tenant Admin (TenantAdminContext); client-supplied tenant IDs are never used.
"""
import enum
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
from app.models.role import ACCESS_ADMIN, Role
from app.models.user import User, UserRole
from app.repositories.user_repository import TEAM_ROLES, UserRepository
from app.schemas.sales_user import (
    SalesUserCreate,
    SalesUserListResponse,
    SalesUserResetPassword,
    SalesUserResponse,
    SalesUserStatusResponse,
    SalesUserStatusUpdate,
    SalesUserUpdate,
)
from app.services import roles as role_service
from app.services.audit_service import AuditService
from app.services.lead_stats_provider import LeadStatsProvider

logger = logging.getLogger(__name__)

EMAIL_EXISTS_MESSAGE = "Email already exists."


def _audit_value(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class SalesUserService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._users = UserRepository(db)
        self._leads = LeadStatsProvider(db)
        self._audit = AuditService(db)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _get_sales_user_or_404(self, ctx: TenantAdminContext, user_id: uuid.UUID) -> User:
        """Any member of the admin's own team (sales people and fellow admins); never another tenant's or the platform's."""
        user = await self._users.get_by_id_in_tenant(user_id, ctx.tenant_id, TEAM_ROLES)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team member not found.")
        return user

    @staticmethod
    def _not_yourself(ctx: TenantAdminContext, user: User, what: str) -> None:
        if user.id == ctx.user.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"You can’t {what} your own account.")

    async def _present(self, ctx: TenantAdminContext, user: User, roles: Optional[Dict[uuid.UUID, Role]] = None) -> SalesUserResponse:
        """The user plus the title of their role (admins and sales people alike) and whether they get automatic deals."""
        roles = roles if roles is not None else await role_service.roles_by_id(self._db, ctx.tenant_id)
        role = roles.get(user.role_id) if user.role_id else None
        response = SalesUserResponse.model_validate(user)
        response.role_name = role.name if role else role_service.fallback_role_name(user.role)
        response.receives_deals = user.role == UserRole.SALES_USER and (role.receives_deals if role else True)
        response.is_me = user.id == ctx.user.id
        return response

    async def _status_response(self, ctx: TenantAdminContext, user: User) -> SalesUserStatusResponse:
        open_leads = await self._leads.count_open_leads(ctx.tenant_id, user.id)
        base = (await self._present(ctx, user)).model_dump()
        return SalesUserStatusResponse(**base, has_open_leads=open_leads > 0, open_leads_count=open_leads)

    async def _resolve_role(self, ctx: TenantAdminContext, role_id: Optional[uuid.UUID]) -> Role:
        """The chosen role, or the built-in Sales Rep when none was chosen. Always one of this tenant's own roles."""
        if role_id is None:
            return (await role_service.ensure_default_roles(self._db, ctx.tenant_id))["SALES"]
        await role_service.ensure_default_roles(self._db, ctx.tenant_id)
        try:
            return await role_service.get_role(self._db, ctx.tenant_id, role_id)
        except HTTPException:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose one of your team’s roles.")

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    async def create_sales_user(
        self, ctx: TenantAdminContext, data: SalesUserCreate, request: Optional[Request] = None
    ) -> SalesUserResponse:
        if await self._users.email_exists(data.email):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_EXISTS_MESSAGE)
        chosen = await self._resolve_role(ctx, data.role_id)

        user = User(
            tenant_id=ctx.tenant_id,
            first_name=data.first_name,
            last_name=data.last_name,
            email=data.email,
            phone_number=data.phone_number,
            password_hash=hash_password(data.password),
            role=role_service.user_role_for(chosen.access),
            role_id=chosen.id,
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
            description=f"Team member '{created.email}' created as {chosen.name}.",
            new_values={**data.model_dump(exclude={"password"}, mode="json"), "role_id": str(chosen.id), "role": chosen.name},
            request=request,
        )
        logger.info("Team member created user_id=%s tenant_id=%s role=%s by=%s", created.id, ctx.tenant_id, chosen.name, ctx.user.id)
        return await self._present(ctx, created)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    async def get_sales_user(self, ctx: TenantAdminContext, user_id: uuid.UUID) -> SalesUserResponse:
        user = await self._get_sales_user_or_404(ctx, user_id)
        return await self._present(ctx, user)

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
        roles = await role_service.roles_by_id(self._db, ctx.tenant_id)
        return SalesUserListResponse(
            items=[await self._present(ctx, item, roles) for item in items],
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

        if "role_id" in update_data:
            if update_data["role_id"] is None:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a role.")
            new_role = await self._resolve_role(ctx, update_data["role_id"])
            new_level = role_service.user_role_for(new_role.access)
            update_data["role_id"] = new_role.id
            if new_level != user.role:
                self._not_yourself(ctx, user, "change the access of")
                update_data["role"] = new_level
                # sign them out, so the new level of access applies at once (and a demotion cannot linger)
                update_data["token_version"] = user.token_version + 1
            elif new_role.id == user.role_id:
                del update_data["role_id"]  # nothing changed

        if not update_data:
            return await self._present(ctx, user)

        old_values = {k: _audit_value(getattr(user, k)) for k in update_data}
        updated = await self._users.update(user, update_data)

        await self._audit.create_audit_log(
            action=AuditLogAction.USER_UPDATED,
            resource_type=AuditLogResourceType.USER,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.id,
            resource_id=str(user_id),
            description=f"Team member '{updated.email}' updated.",
            old_values=old_values,
            new_values={k: _audit_value(v) for k, v in update_data.items()},
            request=request,
        )
        logger.info("Team member updated user_id=%s tenant_id=%s by=%s", user_id, ctx.tenant_id, ctx.user.id)
        return await self._present(ctx, updated)

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
        if not data.is_active:
            self._not_yourself(ctx, user, "deactivate")

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
        self._not_yourself(ctx, user, "reset the password of")
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
        self._not_yourself(ctx, user, "delete")

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
