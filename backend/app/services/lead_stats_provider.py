"""
Lead statistics for users — integration point for the Leads module.
Queries the real leads table for open leads assigned to a sales user within a tenant.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead


class LeadStatsProvider:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def count_open_leads(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count(Lead.id))
            .where(
                Lead.tenant_id == tenant_id,
                Lead.assigned_user_id == user_id,
                Lead.status.notin_(["WON", "LOST"]),
            )
        )
        result = await self._db.execute(stmt)
        return int(result.scalar_one() or 0)
