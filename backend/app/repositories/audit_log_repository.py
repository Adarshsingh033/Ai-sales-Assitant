import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog, AuditLogAction, AuditLogResourceType


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, log: AuditLog) -> AuditLog:
        self._session.add(log)
        await self._session.flush()
        return log

    async def get_by_id(self, log_id: uuid.UUID) -> Optional[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.id == log_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        page: int = 1,
        page_size: int = 20,
        tenant_id: Optional[uuid.UUID] = None,
        user_id: Optional[uuid.UUID] = None,
        action: Optional[AuditLogAction] = None,
        resource_type: Optional[AuditLogResourceType] = None,
        resource_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Tuple[List[AuditLog], int]:
        
        stmt = select(AuditLog)
        count_stmt = select(func.count(AuditLog.id))
        
        # Apply filters
        if tenant_id:
            stmt = stmt.where(AuditLog.tenant_id == tenant_id)
            count_stmt = count_stmt.where(AuditLog.tenant_id == tenant_id)
        if user_id:
            stmt = stmt.where(AuditLog.user_id == user_id)
            count_stmt = count_stmt.where(AuditLog.user_id == user_id)
        if action:
            stmt = stmt.where(AuditLog.action == action)
            count_stmt = count_stmt.where(AuditLog.action == action)
        if resource_type:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
            count_stmt = count_stmt.where(AuditLog.resource_type == resource_type)
        if resource_id:
            stmt = stmt.where(AuditLog.resource_id == resource_id)
            count_stmt = count_stmt.where(AuditLog.resource_id == resource_id)
        if start_date:
            stmt = stmt.where(AuditLog.created_at >= start_date)
            count_stmt = count_stmt.where(AuditLog.created_at >= start_date)
        if end_date:
            stmt = stmt.where(AuditLog.created_at <= end_date)
            count_stmt = count_stmt.where(AuditLog.created_at <= end_date)

        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        stmt = stmt.order_by(AuditLog.created_at.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())

        return items, total
