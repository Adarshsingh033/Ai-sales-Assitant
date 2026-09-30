import math
import re
import uuid
from typing import Optional

from fastapi import HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLogAction, AuditLogResourceType
from app.models.billing_cycle import BillingCycleModel
from app.models.user import User
from app.repositories.billing_cycle_repository import BillingCycleRepository
from app.schemas.billing_cycle import (
    BillingCycleCreate,
    BillingCycleListResponse,
    BillingCycleResponse,
    BillingCycleStatusUpdate,
    BillingCycleUpdate,
)
from app.services.audit_service import AuditService


def _generate_slug(name: str) -> str:
    """Generate a URL-safe lowercase slug from a name."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


class BillingCycleService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = BillingCycleRepository(db)

    async def create_billing_cycle(
        self, data: BillingCycleCreate, current_user: User, request: Optional[Request] = None
    ) -> BillingCycleResponse:
        base_slug = _generate_slug(data.name)
        slug = base_slug

        counter = 1
        while await self._repo.exists_by_slug(slug):
            slug = f"{base_slug}-{counter}"
            counter += 1

        cycle = BillingCycleModel(
            name=data.name,
            slug=slug,
            duration_months=data.duration_months,
            description=data.description,
            is_active=data.is_active,
        )

        created_cycle = await self._repo.create(cycle)

        audit = AuditService(self._db)
        await audit.create_audit_log(
            action=AuditLogAction.BILLING_CYCLE_CREATED,
            resource_type=AuditLogResourceType.BILLING_CYCLE,
            user_id=current_user.id,
            resource_id=str(created_cycle.id),
            description=f"Billing cycle '{created_cycle.name}' created.",
            new_values=data.model_dump(),
            request=request,
        )

        return BillingCycleResponse.model_validate(created_cycle)

    async def get_billing_cycle(self, cycle_id: uuid.UUID) -> BillingCycleResponse:
        cycle = await self._repo.get_by_id(cycle_id)
        if not cycle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Billing cycle not found.",
            )
        return BillingCycleResponse.model_validate(cycle)

    async def list_billing_cycles(
        self, page: int = 1, page_size: int = 50, active_only: bool = False
    ) -> BillingCycleListResponse:
        items, total = await self._repo.list(page=page, page_size=page_size, active_only=active_only)
        total_pages = math.ceil(total / page_size) if total > 0 else 1

        return BillingCycleListResponse(
            items=[BillingCycleResponse.model_validate(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )

    async def update_billing_cycle(
        self,
        cycle_id: uuid.UUID,
        data: BillingCycleUpdate,
        current_user: User,
        request: Optional[Request] = None,
    ) -> BillingCycleResponse:
        cycle = await self._repo.get_by_id(cycle_id)
        if not cycle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Billing cycle not found.",
            )

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return BillingCycleResponse.model_validate(cycle)

        if "name" in update_data and update_data["name"] != cycle.name:
            base_slug = _generate_slug(update_data["name"])
            slug = base_slug
            counter = 1
            while await self._repo.exists_by_slug(slug):
                existing = await self._repo.get_by_slug(slug)
                if existing and existing.id == cycle.id:
                    break
                slug = f"{base_slug}-{counter}"
                counter += 1
            update_data["slug"] = slug

        old_values = {k: getattr(cycle, k) for k in update_data.keys() if hasattr(cycle, k)}
        updated_cycle = await self._repo.update(cycle, update_data)

        audit = AuditService(self._db)
        await audit.create_audit_log(
            action=AuditLogAction.BILLING_CYCLE_UPDATED,
            resource_type=AuditLogResourceType.BILLING_CYCLE,
            user_id=current_user.id,
            resource_id=str(cycle_id),
            description=f"Billing cycle '{updated_cycle.name}' updated.",
            old_values=old_values,
            new_values=update_data,
            request=request,
        )

        return BillingCycleResponse.model_validate(updated_cycle)

    async def update_billing_cycle_status(
        self,
        cycle_id: uuid.UUID,
        data: BillingCycleStatusUpdate,
        current_user: User,
        request: Optional[Request] = None,
    ) -> BillingCycleResponse:
        cycle = await self._repo.get_by_id(cycle_id)
        if not cycle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Billing cycle not found.",
            )

        old_status = cycle.is_active
        updated_cycle = await self._repo.update_status(cycle, data.is_active)

        action = (
            AuditLogAction.BILLING_CYCLE_ACTIVATED
            if data.is_active
            else AuditLogAction.BILLING_CYCLE_DEACTIVATED
        )

        audit = AuditService(self._db)
        await audit.create_audit_log(
            action=action,
            resource_type=AuditLogResourceType.BILLING_CYCLE,
            user_id=current_user.id,
            resource_id=str(cycle_id),
            description=f"Billing cycle status changed to {'active' if data.is_active else 'inactive'}.",
            old_values={"is_active": old_status},
            new_values={"is_active": data.is_active},
            request=request,
        )

        return BillingCycleResponse.model_validate(updated_cycle)

    async def delete_billing_cycle(
        self, cycle_id: uuid.UUID, current_user: User, request: Optional[Request] = None
    ) -> None:
        cycle = await self._repo.get_by_id(cycle_id)
        if not cycle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Billing cycle not found.",
            )

        cycle_name = cycle.name
        await self._repo.delete(cycle)

        audit = AuditService(self._db)
        await audit.create_audit_log(
            action=AuditLogAction.BILLING_CYCLE_DELETED,
            resource_type=AuditLogResourceType.BILLING_CYCLE,
            user_id=current_user.id,
            resource_id=str(cycle_id),
            description=f"Billing cycle '{cycle_name}' deleted.",
            old_values={"name": cycle_name},
            new_values=None,
            request=request,
        )
