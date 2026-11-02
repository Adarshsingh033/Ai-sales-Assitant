"""
Tasks & reminders for Tenant Admins and Sales Users.

A Sales User only ever sees tasks assigned to them. "Today" and "upcoming" depend on the caller's local day, so the
client passes `day_end` (the end of its local day, ISO-8601); without it the end of the current UTC day is used.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.database import get_db
from app.core.dependencies import CRMContext, require_crm_context
from app.models.crm import Task
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.user import User
from app.schemas.crm import TaskCreate, TaskListResponse, TaskResponse, TaskSummary, TaskUpdate
from app.services import crm

router = APIRouter(prefix="/tenant-admin/tasks", tags=["Tenant Admin Tasks"])

Bucket = str  # all | overdue | today | upcoming | no_date | done


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _day_end(value: Optional[datetime]) -> datetime:
    value = _aware(value)
    if value is not None:
        return value
    now = _now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _to_response(task: Task, customer_name: Optional[str], lead_title: Optional[str], first: Optional[str], last: Optional[str]) -> TaskResponse:
    assignee = f"{first or ''} {last or ''}".strip() or None
    due = _aware(task.due_at)
    overdue = task.status == "OPEN" and due is not None and due < _now()
    return TaskResponse(
        id=task.id, title=task.title, notes=task.notes, due_at=task.due_at, status=task.status, is_overdue=overdue,
        completed_at=task.completed_at, customer_id=task.customer_id, customer_name=customer_name,
        lead_id=task.lead_id, lead_title=lead_title, assigned_user_id=task.assigned_user_id,
        assigned_user_name=assignee, created_at=task.created_at,
    )


def _select_tasks():
    assignee = aliased(User)
    stmt = (
        select(Task, Customer.name, Lead.title, assignee.first_name, assignee.last_name)
        .outerjoin(Customer, Task.customer_id == Customer.id)
        .outerjoin(Lead, Task.lead_id == Lead.id)
        .outerjoin(assignee, Task.assigned_user_id == assignee.id)
    )
    return stmt


def _bucket_clauses(bucket: Bucket, day_end: datetime) -> List[Any]:
    now = _now()
    if bucket == "done":
        return [Task.status == "DONE"]
    open_ = Task.status == "OPEN"
    if bucket == "overdue":
        return [open_, Task.due_at.is_not(None), Task.due_at < now]
    if bucket == "today":
        return [open_, Task.due_at >= now, Task.due_at < day_end]
    if bucket == "upcoming":
        return [open_, Task.due_at >= day_end]
    if bucket == "no_date":
        return [open_, Task.due_at.is_(None)]
    if bucket == "all":
        return [open_]
    raise HTTPException(status_code=422, detail="bucket must be one of: all, overdue, today, upcoming, no_date, done.")


async def _load_one(db: AsyncSession, ctx: CRMContext, task_id: uuid.UUID) -> Tuple[Task, TaskResponse]:
    row = (await db.execute(_select_tasks().where(*crm.task_scope(ctx), Task.id == task_id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return row[0], _to_response(*row)


async def _task_for_update(db: AsyncSession, ctx: CRMContext, task_id: uuid.UUID) -> Task:
    task = (await db.execute(select(Task).where(*crm.task_scope(ctx), Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


# ----------------------------------------------------------------------------- list / summary
async def compute_summary(db: AsyncSession, ctx: CRMContext, day_end: Optional[datetime], mine: bool = False) -> TaskSummary:
    now, end = _now(), _day_end(day_end)
    scope = crm.task_scope(ctx)
    if mine and ctx.is_admin:
        scope = scope + [Task.assigned_user_id == ctx.user.id]
    stmt = select(
        func.count(case((Task.due_at < now, 1))),
        func.count(case(((Task.due_at >= now) & (Task.due_at < end), 1))),
        func.count(case((Task.due_at >= end, 1))),
        func.count(case((Task.due_at.is_(None), 1))),
    ).where(*scope, Task.status == "OPEN")
    overdue, today, upcoming, no_date = (await db.execute(stmt)).one()
    return TaskSummary(overdue=overdue, today=today, upcoming=upcoming, no_date=no_date,
                       open=overdue + today + upcoming + no_date)


@router.get("/summary", response_model=TaskSummary, summary="Task counts for the header and home screen")
async def task_summary(
    day_end: Optional[datetime] = None,
    mine: bool = False,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> TaskSummary:
    return await compute_summary(db, ctx, day_end, mine)


@router.get("", response_model=TaskListResponse, summary="List tasks")
async def list_tasks(
    bucket: Bucket = "all",
    day_end: Optional[datetime] = None,
    mine: bool = False,
    assigned_user_id: Optional[uuid.UUID] = None,
    customer_id: Optional[uuid.UUID] = None,
    lead_id: Optional[uuid.UUID] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> TaskListResponse:
    filters = crm.task_scope(ctx) + _bucket_clauses(bucket, _day_end(day_end))
    if mine and ctx.is_admin:
        filters.append(Task.assigned_user_id == ctx.user.id)
    if assigned_user_id and ctx.is_admin:
        filters.append(Task.assigned_user_id == assigned_user_id)
    if customer_id:
        filters.append(Task.customer_id == customer_id)
    if lead_id:
        filters.append(Task.lead_id == lead_id)

    total = (await db.execute(select(func.count(Task.id)).where(*filters))).scalar_one()
    order = (Task.completed_at.desc(),) if bucket == "done" else (Task.due_at.asc().nulls_last(), Task.created_at.asc())
    rows = (
        await db.execute(_select_tasks().where(*filters).order_by(*order).offset((page - 1) * page_size).limit(page_size))
    ).all()
    return TaskListResponse(items=[_to_response(*r) for r in rows], total=total, page=page, page_size=page_size)


# ----------------------------------------------------------------------------- create / update
async def _resolve_links(db: AsyncSession, ctx: CRMContext, customer_id, lead_id):
    """Validate that the linked contact/deal is visible to the caller; a deal also pins its contact."""
    if lead_id:
        lead = (await db.execute(select(Lead).where(*crm.lead_scope(ctx), Lead.id == lead_id))).scalar_one_or_none()
        if lead is None:
            raise HTTPException(status_code=404, detail="Deal not found.")
        customer_id = customer_id or lead.customer_id
    if customer_id:
        found = (
            await db.execute(select(Customer.id).where(*crm.customer_scope(ctx), Customer.id == customer_id))
        ).scalar_one_or_none()
        if found is None:
            raise HTTPException(status_code=404, detail="Contact not found.")
    return customer_id, lead_id


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED, summary="Create a task")
async def create_task(
    data: TaskCreate,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> TaskResponse:
    customer_id, lead_id = await _resolve_links(db, ctx, data.customer_id, data.lead_id)

    owner_id = ctx.user.id
    if ctx.is_admin and data.assigned_user_id:
        owner_id = (await crm.get_assignable_user(db, ctx.tenant_id, data.assigned_user_id)).id

    task = Task(
        tenant_id=ctx.tenant_id, title=data.title, notes=data.notes, due_at=_aware(data.due_at),
        assigned_user_id=owner_id, created_by_id=ctx.user.id, customer_id=customer_id, lead_id=lead_id,
    )
    db.add(task)
    await db.flush()
    if customer_id:
        crm.log_activity(db, tenant_id=ctx.tenant_id, kind="TASK", text=f"Task added: {task.title}",
                         customer_id=customer_id, lead_id=lead_id, actor_user_id=ctx.user.id)
    if owner_id != ctx.user.id:
        crm.notify(db, tenant_id=ctx.tenant_id, user_id=owner_id, title=f"New task: {task.title}",
                   message=f"{crm.full_name(ctx.user)} assigned you a task.", link_url="#tasks")
    await db.commit()
    return (await _load_one(db, ctx, task.id))[1]


@router.put("/{task_id}", response_model=TaskResponse, summary="Edit a task")
async def update_task(
    task_id: uuid.UUID,
    data: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> TaskResponse:
    task = await _task_for_update(db, ctx, task_id)
    changes = data.model_dump(exclude_unset=True)

    if "title" in changes:
        if not changes["title"]:
            raise HTTPException(status_code=422, detail="A task needs a title.")
        task.title = changes["title"]
    if "notes" in changes:
        task.notes = changes["notes"]
    if "due_at" in changes:
        new_due = _aware(changes["due_at"])
        if new_due != _aware(task.due_at):
            task.reminder_sent_at = None  # a moved due time earns a fresh reminder
        task.due_at = new_due
    if "assigned_user_id" in changes and changes["assigned_user_id"] != task.assigned_user_id:
        if not ctx.is_admin:
            raise HTTPException(status_code=403, detail="Only an admin can hand a task to someone else.")
        if changes["assigned_user_id"] is None:
            raise HTTPException(status_code=422, detail="A task needs an owner.")
        new_owner = await crm.get_assignable_user(db, ctx.tenant_id, changes["assigned_user_id"])
        task.assigned_user_id = new_owner.id
        task.reminder_sent_at = None
        if new_owner.id != ctx.user.id:
            crm.notify(db, tenant_id=ctx.tenant_id, user_id=new_owner.id, title=f"New task: {task.title}",
                       message=f"{crm.full_name(ctx.user)} assigned you a task.", link_url="#tasks")
    await db.commit()
    return (await _load_one(db, ctx, task_id))[1]


@router.post("/{task_id}/complete", response_model=TaskResponse, summary="Mark a task done")
async def complete_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> TaskResponse:
    task = await _task_for_update(db, ctx, task_id)
    if task.status != "DONE":
        task.status = "DONE"
        task.completed_at = _now()
        if task.customer_id:
            crm.log_activity(db, tenant_id=ctx.tenant_id, kind="TASK", text=f"Task completed: {task.title}",
                             customer_id=task.customer_id, lead_id=task.lead_id, actor_user_id=ctx.user.id)
        await db.commit()
    return (await _load_one(db, ctx, task_id))[1]


@router.post("/{task_id}/reopen", response_model=TaskResponse, summary="Reopen a finished task")
async def reopen_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> TaskResponse:
    task = await _task_for_update(db, ctx, task_id)
    if task.status != "OPEN":
        task.status = "OPEN"
        task.completed_at = None
        await db.commit()
    return (await _load_one(db, ctx, task_id))[1]


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a task")
async def delete_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> None:
    task = await _task_for_update(db, ctx, task_id)
    await db.delete(task)
    await db.commit()
