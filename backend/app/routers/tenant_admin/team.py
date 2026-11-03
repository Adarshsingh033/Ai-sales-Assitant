"""
Team members a deal, contact or task can be given to (active Tenant Admins and Sales Users of the caller's tenant),
and, for admins, how much each of them is carrying.
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CRMContext, require_crm_context
from app.models.crm import Task
from app.models.lead import Lead
from app.models.user import User, UserRole
from app.schemas.crm import WorkloadMember, WorkloadResponse
from app.services import crm

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


@router.get("/workload", response_model=WorkloadResponse, summary="How many deals and to-dos each team member has")
async def team_workload(
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> WorkloadResponse:
    if not ctx.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can see the team's workload.")

    members: Dict[uuid.UUID, WorkloadMember] = {}

    def member(user_id: uuid.UUID) -> WorkloadMember:
        return members.setdefault(user_id, WorkloadMember(user_id=user_id))

    deal_rows = (
        await db.execute(
            select(Lead.assigned_user_id, Lead.status, func.count(Lead.id))
            .where(Lead.tenant_id == ctx.tenant_id, Lead.assigned_user_id.is_not(None))
            .group_by(Lead.assigned_user_id, Lead.status)
        )
    ).all()
    for user_id, stage, n in deal_rows:
        m = member(user_id)
        if stage in crm.STAGE_GROUPS["NEW"]:
            m.new += n
        elif stage in crm.STAGE_GROUPS["SERVING"]:
            m.serving += n
        elif stage == "WON":
            m.won += n
            m.completed += n
        elif stage == "LOST":
            m.lost += n
            m.completed += n

    now = datetime.now(timezone.utc)
    task_rows = (
        await db.execute(
            select(Task.assigned_user_id, func.count(Task.id), func.sum(case((Task.due_at < now, 1), else_=0)))
            .where(Task.tenant_id == ctx.tenant_id, Task.status == "OPEN", Task.assigned_user_id.is_not(None))
            .group_by(Task.assigned_user_id)
        )
    ).all()
    for user_id, open_tasks, overdue in task_rows:
        m = member(user_id)
        m.open_tasks, m.overdue_tasks = open_tasks, int(overdue or 0)

    unassigned = (
        await db.execute(
            select(func.count(Lead.id)).where(
                Lead.tenant_id == ctx.tenant_id, Lead.assigned_user_id.is_(None), Lead.status.notin_(crm.CLOSED_STAGES)
            )
        )
    ).scalar_one()
    return WorkloadResponse(members=list(members.values()), unassigned=unassigned)
