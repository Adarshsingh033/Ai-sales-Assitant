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
from app.models.user import UserRole
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
    except JWTError:
        raise credentials_exception

    repo = UserRepository(db)
    user = await repo.get_by_id(UUID(user_id))
    if user is None:
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


def require_sales_user(current_user=Depends(get_current_user)):
    """Enforce any authenticated sales role."""
    if current_user.role not in (UserRole.SALES_USER, UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sales User access required.",
        )
    return current_user
