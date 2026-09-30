import math
import re
import uuid
from typing import Optional

from fastapi import HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLogAction, AuditLogResourceType
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User
from app.repositories.subscription_plan_repository import SubscriptionPlanRepository
from app.repositories.tenant_repository import TenantRepository
from app.services.audit_service import AuditService
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

    async def create_tenant(self, data: TenantCreate, current_user: User, request: Optional[Request] = None) -> TenantResponse:
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
            subscription_plan_id=data.subscription_plan_id,
            created_by=current_user.id,
        )

        created_tenant = await self._repo.create(tenant)

        # If email and password provided, create initial tenant admin user
        if data.email and data.password:
            from app.core.security import hash_password
            from app.models.user import User, UserRole
            name_parts = data.name.split(" ", 1)
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else "Admin"
            admin_user = User(
                tenant_id=created_tenant.id,
                first_name=first_name,
                last_name=last_name,
                email=data.email,
                password_hash=hash_password(data.password),
                role=UserRole.TENANT_ADMIN,
                is_active=True,
            )
            self._repo._session.add(admin_user)
            await self._repo._session.flush()
        
        # Audit log
        audit = AuditService(self._repo._session)
        await audit.create_audit_log(
            action=AuditLogAction.TENANT_CREATED,
            resource_type=AuditLogResourceType.TENANT,
            tenant_id=created_tenant.id,
            user_id=current_user.id,
            resource_id=str(created_tenant.id),
            description=f"Tenant '{created_tenant.name}' created.",
            new_values=data.model_dump(exclude={"password"}),
            request=request
        )
        
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

    async def update_tenant(self, tenant_id: uuid.UUID, data: TenantUpdate, current_user: User, request: Optional[Request] = None) -> TenantResponse:
        tenant = await self._repo.get_by_id(tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found.",
            )
            
        update_data = data.model_dump(exclude_unset=True, exclude={"password"})
        
        # Update password if provided
        if data.password and tenant.email:
            from app.core.security import hash_password
            from sqlalchemy import select
            from app.models.user import User, UserRole
            stmt = select(User).where(User.tenant_id == tenant.id, User.role == UserRole.TENANT_ADMIN)
            res = await self._repo._session.execute(stmt)
            admin_user = res.scalar_one_or_none()
            if admin_user:
                admin_user.password_hash = hash_password(data.password)

        if update_data:
            old_values = {k: getattr(tenant, k) for k in update_data.keys()}
            updated_tenant = await self._repo.update(tenant, update_data)
        else:
            updated_tenant = tenant

        audit = AuditService(self._repo._session)
        await audit.create_audit_log(
            action=AuditLogAction.TENANT_UPDATED,
            resource_type=AuditLogResourceType.TENANT,
            tenant_id=tenant_id,
            user_id=current_user.id,
            resource_id=str(tenant_id),
            description="Tenant details updated.",
            old_values={k: str(v) for k, v in old_values.items()} if update_data else None,
            new_values={k: str(v) for k, v in update_data.items()} if update_data else None,
            request=request
        )
        
        return TenantResponse.model_validate(updated_tenant)

    async def update_tenant_status(self, tenant_id: uuid.UUID, data: TenantStatusUpdate, current_user: User, request: Optional[Request] = None) -> TenantResponse:
        tenant = await self._repo.get_by_id(tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found.",
            )
            
        old_status = tenant.status.value
        updated_tenant = await self._repo.update_status(tenant, data.status)
        
        action = AuditLogAction.TENANT_UPDATED
        if data.status == TenantStatus.ACTIVE:
            action = AuditLogAction.TENANT_ACTIVATED
        elif data.status == TenantStatus.INACTIVE:
            action = AuditLogAction.TENANT_DEACTIVATED
        elif data.status == TenantStatus.SUSPENDED:
            action = AuditLogAction.TENANT_SUSPENDED
            
        audit = AuditService(self._repo._session)
        await audit.create_audit_log(
            action=action,
            resource_type=AuditLogResourceType.TENANT,
            tenant_id=tenant_id,
            user_id=current_user.id,
            resource_id=str(tenant_id),
            description=f"Tenant status changed to {data.status.value}.",
            old_values={"status": old_status},
            new_values={"status": data.status.value},
            request=request
        )
        
        return TenantResponse.model_validate(updated_tenant)

    async def delete_tenant(self, tenant_id: uuid.UUID, current_user: User, request: Optional[Request] = None) -> None:
        tenant = await self._repo.get_by_id(tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found.",
            )
            
        tenant_name = tenant.name
        
        # Log audit log before deleting the tenant record so tenant_id foreign key constraint is satisfied
        audit = AuditService(self._repo._session)
        await audit.create_audit_log(
            action=AuditLogAction.TENANT_UPDATED,
            resource_type=AuditLogResourceType.TENANT,
            tenant_id=tenant_id,
            user_id=current_user.id,
            resource_id=str(tenant_id),
            description=f"Tenant '{tenant_name}' deleted.",
            request=request
        )

        await self._repo.delete(tenant)

    async def assign_subscription_plan(self, tenant_id: uuid.UUID, plan_id: uuid.UUID, current_user: User, request: Optional[Request] = None) -> TenantResponse:
        tenant = await self._repo.get_by_id(tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found.",
            )

        plan_repo = SubscriptionPlanRepository(self._repo._session)
        plan = await plan_repo.get_by_id(plan_id)
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )
        
        if not plan.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot assign an inactive subscription plan.",
            )

        old_plan_id = str(tenant.subscription_plan_id) if tenant.subscription_plan_id else None
        updated_tenant = await self._repo.update(tenant, {"subscription_plan_id": plan_id})
        
        audit = AuditService(self._repo._session)
        await audit.create_audit_log(
            action=AuditLogAction.TENANT_SUBSCRIPTION_PLAN_CHANGED,
            resource_type=AuditLogResourceType.TENANT,
            tenant_id=tenant_id,
            user_id=current_user.id,
            resource_id=str(tenant_id),
            description=f"Tenant subscription plan changed to {plan.name}.",
            old_values={"subscription_plan_id": old_plan_id},
            new_values={"subscription_plan_id": str(plan_id)},
            request=request
        )
        
        return TenantResponse.model_validate(updated_tenant)
