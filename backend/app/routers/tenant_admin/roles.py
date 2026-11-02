"""
Roles an admin creates for their team. The Add/Edit team member form offers exactly these roles.
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import TenantAdminContext, require_tenant_admin_context
from app.models.role import ACCESS_ADMIN, Role
from app.models.user import User
from app.schemas.role import RoleCreate, RoleResponse, RoleUpdate
from app.services import roles as role_service

router = APIRouter(prefix="/tenant-admin/roles", tags=["Tenant Admin Roles"])


def _present(role: Role, members: int) -> RoleResponse:
    return RoleResponse(
        id=role.id, name=role.name, access=role.access, receives_deals=role.receives_deals,
        is_system=role.is_system, member_count=members,
    )


def _duplicate(name: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"There is already a role called “{name}”.")


@router.get("", response_model=List[RoleResponse], summary="Roles of my team")
async def list_roles(
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> List[RoleResponse]:
    await role_service.ensure_default_roles(db, ctx.tenant_id)
    roles = role_service.sort_roles(list((await role_service.roles_by_id(db, ctx.tenant_id)).values()))
    counts = await role_service.member_counts(db, ctx.tenant_id, roles)
    return [_present(r, counts.get(r.id, 0)) for r in roles]


@router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED, summary="Create a role")
async def create_role(
    data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> RoleResponse:
    await role_service.ensure_default_roles(db, ctx.tenant_id)
    if await role_service.name_taken(db, ctx.tenant_id, data.name):
        raise _duplicate(data.name)
    role = Role(
        tenant_id=ctx.tenant_id, name=data.name, access=data.access,
        receives_deals=data.receives_deals and data.access != ACCESS_ADMIN,  # admins never get automatic deals
    )
    db.add(role)
    await db.commit()
    return _present(role, 0)


@router.put("/{role_id}", response_model=RoleResponse, summary="Rename a role or change whether it receives new deals")
async def update_role(
    role_id: uuid.UUID,
    data: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> RoleResponse:
    role = await role_service.get_role(db, ctx.tenant_id, role_id)
    if role.is_system:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Built-in roles can’t be changed.")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") and changes["name"] != role.name:
        if await role_service.name_taken(db, ctx.tenant_id, changes["name"], except_id=role.id):
            raise _duplicate(changes["name"])
        role.name = changes["name"]
    if "receives_deals" in changes and changes["receives_deals"] is not None:
        role.receives_deals = changes["receives_deals"] and role.access != ACCESS_ADMIN
    await db.commit()
    counts = await role_service.member_counts(db, ctx.tenant_id, [role])
    return _present(role, counts.get(role.id, 0))


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a role nobody has")
async def delete_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> None:
    role = await role_service.get_role(db, ctx.tenant_id, role_id)
    if role.is_system:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Built-in roles can’t be deleted.")
    members = (await db.execute(select(func.count()).select_from(User).where(User.role_id == role.id))).scalar_one()
    if members:
        who = "person has" if members == 1 else "people have"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{members} {who} this role. Give them another role first, then delete it.",
        )
    await db.delete(role)
    await db.commit()
