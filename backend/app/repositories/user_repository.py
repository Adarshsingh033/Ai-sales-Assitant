"""
User repository — all database operations for the User model.
Keeps SQL/ORM concerns out of services and routers.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

SALES_USER_SORT_FIELDS = {
    "first_name": User.first_name,
    "last_name": User.last_name,
    "email": User.email,
    "created_at": User.created_at,
    "last_login_at": User.last_login_at,
}


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, user_id: UUID) -> Optional[User]:
        result = await self._db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self._db.execute(
            select(User).where(func.lower(User.email) == email.lower().strip())
        )
        return result.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self._db.add(user)
        await self._db.flush()  # get the DB-assigned id before commit
        await self._db.refresh(user)
        return user

    async def update_last_login(self, user_id: UUID) -> None:
        from datetime import datetime, timezone
        await self._db.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=datetime.now(tz=timezone.utc))
        )

    async def email_exists(self, email: str) -> bool:
        result = await self._db.execute(
            select(User.id).where(func.lower(User.email) == email.lower().strip()).limit(1)
        )
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Tenant-scoped operations — tenant_id must come from server context
    # ------------------------------------------------------------------
    async def get_by_id_in_tenant(
        self,
        user_id: UUID,
        tenant_id: UUID,
        role: UserRole = UserRole.SALES_USER,
    ) -> Optional[User]:
        result = await self._db.execute(
            select(User).where(
                User.id == user_id,
                User.tenant_id == tenant_id,
                User.role == role,
            )
        )
        return result.scalar_one_or_none()

    async def list_sales_users(
        self,
        tenant_id: UUID,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_available: Optional[bool] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Tuple[List[User], int]:
        filters = [User.tenant_id == tenant_id, User.role == UserRole.SALES_USER]

        if search:
            term = f"%{search.strip()}%"
            full_name = User.first_name + " " + User.last_name
            filters.append(
                or_(
                    User.first_name.ilike(term),
                    User.last_name.ilike(term),
                    full_name.ilike(term),
                    User.email.ilike(term),
                )
            )
        if is_active is not None:
            filters.append(User.is_active == is_active)
        if is_available is not None:
            filters.append(User.is_available == is_available)

        total_result = await self._db.execute(select(func.count(User.id)).where(*filters))
        total = total_result.scalar_one()

        sort_column = SALES_USER_SORT_FIELDS[sort_by]
        order = sort_column.asc() if sort_order == "asc" else sort_column.desc()

        stmt = (
            select(User)
            .where(*filters)
            .order_by(order, User.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all()), total

    async def count_users_in_tenant(self, tenant_id: UUID) -> int:
        result = await self._db.execute(
            select(func.count(User.id)).where(User.tenant_id == tenant_id)
        )
        return result.scalar_one()

    async def update(self, user: User, update_data: Dict[str, Any]) -> User:
        for key, value in update_data.items():
            setattr(user, key, value)
        await self._db.flush()
        await self._db.refresh(user)
        return user

    async def delete(self, user: User) -> None:
        await self._db.delete(user)
        await self._db.flush()
