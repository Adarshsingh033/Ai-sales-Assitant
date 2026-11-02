"""
Team roles. An admin creates roles ("Sales Rep", "Support", "Manager", ...); each one has an access level and a switch for
the automatic deal round-robin. The platform's real permission check stays `User.role` (SALES_USER / TENANT_ADMIN): a role's
access level decides which of the two its members get, so nothing else in the app needs to know about custom roles.
"""
import uuid
from typing import Dict, Iterable, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import ACCESS_ADMIN, ACCESS_SALES, Role
from app.models.user import User, UserRole

BUILT_IN = (
    # name, access, receives_deals
    ("Sales Rep", ACCESS_SALES, True),
    ("Admin", ACCESS_ADMIN, False),
)
_USER_ROLE_BY_ACCESS = {ACCESS_SALES: UserRole.SALES_USER, ACCESS_ADMIN: UserRole.TENANT_ADMIN}
_BUILT_IN_NAME_BY_USER_ROLE = {UserRole.SALES_USER: "Sales Rep", UserRole.TENANT_ADMIN: "Admin"}


def user_role_for(access: str) -> UserRole:
    return _USER_ROLE_BY_ACCESS[access]


def fallback_role_name(user_role: UserRole) -> str:
    """Title shown for someone with no explicit role (created before roles existed, or by the platform team)."""
    return _BUILT_IN_NAME_BY_USER_ROLE.get(user_role, user_role.value.replace("_", " ").title())


async def ensure_default_roles(db: AsyncSession, tenant_id: uuid.UUID) -> Dict[str, Role]:
    """The tenant's built-in roles keyed by access level, created on first use (idempotent)."""
    found = {
        r.access: r
        for r in (await db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.is_system.is_(True)))).scalars()
    }
    missing = [(n, a, d) for n, a, d in BUILT_IN if a not in found]
    if not missing:
        return found
    try:
        for name, access, gets_deals in missing:
            role = Role(tenant_id=tenant_id, name=name, access=access, receives_deals=gets_deals, is_system=True)
            db.add(role)
            found[access] = role
        await db.flush()
        for access, role in found.items():  # file people created without a role under the built-in one for their level
            await db.execute(
                update(User)
                .where(User.tenant_id == tenant_id, User.role_id.is_(None), User.role == user_role_for(access))
                .values(role_id=role.id)
            )
        await db.commit()
    except IntegrityError:  # another request created them first
        await db.rollback()
        return await ensure_default_roles(db, tenant_id)
    return found


async def get_role(db: AsyncSession, tenant_id: uuid.UUID, role_id: uuid.UUID) -> Role:
    role = (await db.execute(select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That role was not found.")
    return role


async def roles_by_id(db: AsyncSession, tenant_id: uuid.UUID) -> Dict[uuid.UUID, Role]:
    return {r.id: r for r in (await db.execute(select(Role).where(Role.tenant_id == tenant_id))).scalars()}


async def name_taken(db: AsyncSession, tenant_id: uuid.UUID, name: str, *, except_id: Optional[uuid.UUID] = None) -> bool:
    stmt = select(Role.id).where(Role.tenant_id == tenant_id, func.lower(Role.name) == name.lower())
    if except_id:
        stmt = stmt.where(Role.id != except_id)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def member_counts(db: AsyncSession, tenant_id: uuid.UUID, roles: Iterable[Role]) -> Dict[uuid.UUID, int]:
    """People per role; people with no explicit role count under the built-in role of their access level."""
    by_role = dict(
        (await db.execute(
            select(User.role_id, func.count()).where(User.tenant_id == tenant_id, User.role_id.is_not(None)).group_by(User.role_id)
        )).all()
    )
    untitled = dict(
        (await db.execute(
            select(User.role, func.count())
            .where(User.tenant_id == tenant_id, User.role_id.is_(None), User.role.in_(list(_BUILT_IN_NAME_BY_USER_ROLE)))
            .group_by(User.role)
        )).all()
    )
    counts: Dict[uuid.UUID, int] = {}
    for role in roles:
        n = by_role.get(role.id, 0)
        if role.is_system:
            n += untitled.get(user_role_for(role.access), 0)
        counts[role.id] = n
    return counts


def sort_roles(roles: List[Role]) -> List[Role]:
    """Built-in roles first, then the admin's own by name."""
    return sorted(roles, key=lambda r: (not r.is_system, r.access != ACCESS_SALES, r.name.lower()))
