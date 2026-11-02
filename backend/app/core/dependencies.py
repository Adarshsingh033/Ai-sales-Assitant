"""
Reusable FastAPI dependencies for authentication and role-based authorization.

Usage:
    get_current_user      — any authenticated user
    require_super_admin   — SUPER_ADMIN only
    require_tenant_admin  — TENANT_ADMIN or SUPER_ADMIN
    require_sales_user    — any authenticated user with a recognized role
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Validate the Bearer JWT and return the authenticated User model instance.
    Raises 401 for any authentication failure.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(credentials.credentials)
        user_id: Optional[str] = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        user_uuid = UUID(user_id)
    except (JWTError, ValueError):
        raise credentials_exception

    repo = UserRepository(db)
    user = await repo.get_by_id(user_uuid)
    if user is None:
        raise credentials_exception
    # Tokens issued before a revocation event (deactivation, password reset)
    # carry a stale version and must be rejected.
    if payload.get("tv", 0) != user.token_version:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is inactive. Please contact support.",
        )
    return user


def require_super_admin(current_user=Depends(get_current_user)):
    """Enforce SUPER_ADMIN role."""
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin access required.",
        )
    return current_user


def require_tenant_admin(current_user=Depends(get_current_user)):
    """Enforce TENANT_ADMIN or SUPER_ADMIN role."""
    if current_user.role not in (UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant Admin access required.",
        )
    return current_user


def require_sales_user(current_user: User = Depends(get_current_user)) -> User:
    """Enforce any authenticated sales role."""
    if current_user.role not in (UserRole.SALES_USER, UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sales User access required.",
        )
    return current_user


async def get_current_tenant(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the tenant for the current user.
    Raises 403 if the user has no tenant or if the tenant is inactive/suspended.
    """
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to a tenant.",
        )
    
    repo = TenantRepository(db)
    tenant = await repo.get_by_id(current_user.tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )
        
    if tenant.status != TenantStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant is {tenant.status.value.lower()}.",
        )
    return tenant


def require_tenant_access(
    current_user: User = Depends(get_current_user),
    tenant=Depends(get_current_tenant),
):
    """
    Dependency to strictly enforce tenant isolation.
    Returns the active tenant context for the user.
    """
    # If the user made it here, get_current_tenant already validated 
    # that they belong to this specific active tenant.
    return tenant


class CRMContext:
    """A Tenant Admin or Sales User working inside their own (active) tenant."""

    def __init__(self, user: User, tenant: Tenant) -> None:
        self.user = user
        self.tenant = tenant

    @property
    def tenant_id(self) -> UUID:
        return self.tenant.id

    @property
    def is_admin(self) -> bool:
        return self.user.role == UserRole.TENANT_ADMIN

    @property
    def is_rep(self) -> bool:
        return self.user.role == UserRole.SALES_USER


def require_crm_context(
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
) -> CRMContext:
    """Tenant Admins see the whole tenant; Sales Users are limited to what is assigned to them."""
    if current_user.role not in (UserRole.TENANT_ADMIN, UserRole.SALES_USER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sales workspace access required.")
    return CRMContext(current_user, tenant)


class TenantAdminContext:
    """Authenticated Tenant Admin plus their (active) tenant, resolved server-side."""

    def __init__(self, user: User, tenant: Tenant) -> None:
        self.user = user
        self.tenant = tenant

    @property
    def tenant_id(self) -> UUID:
        return self.tenant.id


def require_tenant_admin_context(
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
) -> TenantAdminContext:
    """
    Enforce TENANT_ADMIN role (Super Admin excluded) and return the tenant
    derived from the authenticated user's DB record — never from client input.
    """
    if current_user.role != UserRole.TENANT_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant Admin access required.",
        )
    return TenantAdminContext(current_user, tenant)
