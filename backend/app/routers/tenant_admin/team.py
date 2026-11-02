"""
Team members a deal, contact or task can be given to (active Tenant Admins and Sales Users of the caller's tenant).
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CRMContext, require_crm_context
from app.models.user import User, UserRole

router = APIRouter(prefix="/tenant-admin/team", tags=["Tenant Admin Team"])


class TeamMember(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    is_me: bool = False


@router.get("", response_model=List[TeamMember], summary="Active team members")
async def list_team(
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> List[TeamMember]:
    users = (
        await db.execute(
            select(User)
            .where(User.tenant_id == ctx.tenant_id, User.is_active.is_(True),
                   User.role.in_([UserRole.SALES_USER, UserRole.TENANT_ADMIN]))
            .order_by(User.first_name, User.last_name)
        )
    ).scalars().all()
    return [
        TeamMember(id=u.id, name=f"{u.first_name} {u.last_name}".strip() or u.email, role=u.role.value, is_me=u.id == ctx.user.id)
        for u in users
    ]
