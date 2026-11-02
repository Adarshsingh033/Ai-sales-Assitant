"""
Due-task reminders: when a task's due time arrives, its owner gets one in-app notification.
Run from the worker's scheduler loop; safe to call repeatedly (each task is reminded once per due time).
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crm import Task
from app.services import crm

logger = logging.getLogger(__name__)

BATCH = 200


async def send_due_reminders(db: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    tasks = (
        await db.execute(
            select(Task)
            .where(Task.status == "OPEN", Task.due_at.is_not(None), Task.due_at <= now,
                   Task.reminder_sent_at.is_(None), Task.assigned_user_id.is_not(None))
            .order_by(Task.due_at)
            .limit(BATCH)
        )
    ).scalars().all()
    for task in tasks:
        crm.notify(
            db, tenant_id=task.tenant_id, user_id=task.assigned_user_id, notif_type="FOLLOWUP_DUE",
            title=f"⏰ Task due: {task.title}", message="This task has reached its due time.", link_url="#tasks",
        )
        task.reminder_sent_at = now
    if tasks:
        await db.commit()
        logger.info("Sent %d task reminder(s)", len(tasks))
    return len(tasks)
