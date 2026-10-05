"""
Lead statistics for users — integration point for the Leads module.

The Leads module does not exist yet, so open-lead counts are always 0.
When Leads are implemented, replace count_open_leads with a tenant-scoped
COUNT over leads assigned to the user in a non-terminal status.
"""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession


class LeadStatsProvider:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def count_open_leads(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
        return 0
