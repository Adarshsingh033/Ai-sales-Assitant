"""
Durable background jobs (PRD §52: queue-based operations, retries, graceful degradation).

Jobs live in the `ai_jobs` table and are claimed with `FOR UPDATE SKIP LOCKED` on PostgreSQL, so any number
of workers / app instances can run safely. Jobs sharing a `conversation_id` never run concurrently, which keeps a
customer's turns strictly ordered. Failures retry with exponential backoff; jobs stuck RUNNING (crashed worker)
are recovered automatically.

The same worker process also runs the follow-up scheduler and one-time maintenance (stale-embedding reindex).
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.models.ai_job import AIJob

logger = logging.getLogger(__name__)

STALE_LOCK_SECONDS = 300
JobHandler = Callable[[AsyncSession, AIJob], Awaitable[None]]
_HANDLERS: Dict[str, JobHandler] = {}


def register_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def deco(fn: JobHandler) -> JobHandler:
        _HANDLERS[job_type] = fn
        return fn

    return deco


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def lane_key(tenant_id: uuid.UUID, customer_key: str) -> uuid.UUID:
    """Stable serialisation key for one customer: jobs with the same key never run concurrently (ordered turns)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant_id}:{customer_key}")


class JobQueue:
    @staticmethod
    async def enqueue(
        db: AsyncSession,
        job_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: Optional[uuid.UUID] = None,
        conversation_id: Optional[uuid.UUID] = None,
        run_at: Optional[datetime] = None,
        max_attempts: int = 3,
        dedupe: bool = False,
        commit: bool = True,
    ) -> AIJob:
        if dedupe:
            existing = (
                await db.execute(
                    select(AIJob).where(
                        AIJob.job_type == job_type,
                        AIJob.status.in_(["PENDING", "RUNNING"]),
                        AIJob.tenant_id == tenant_id,
                        AIJob.conversation_id == conversation_id,
                    )
                )
            ).scalars().first()
            if existing:
                return existing
        job = AIJob(
            job_type=job_type,
            payload=payload or {},
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_at=run_at or _utcnow(),
            max_attempts=max_attempts,
        )
        db.add(job)
        await (db.commit() if commit else db.flush())
        return job

    @staticmethod
    async def claim_next(db: AsyncSession) -> Optional[AIJob]:
        now = _utcnow()
        await db.execute(
            update(AIJob)
            .where(AIJob.status == "RUNNING", AIJob.locked_at < now - timedelta(seconds=STALE_LOCK_SECONDS))
            .values(status="PENDING", locked_at=None)
        )
        running = aliased(AIJob)
        blocked = (
            select(running.id)
            .where(
                running.status == "RUNNING",
                running.conversation_id == AIJob.conversation_id,
                AIJob.conversation_id.is_not(None),
            )
            .correlate(AIJob)
            .exists()
        )
        stmt = (
            select(AIJob)
            .where(AIJob.status == "PENDING", AIJob.run_at <= now, ~blocked)
            .order_by(AIJob.run_at, AIJob.created_at)
            .limit(1)
        )
        if db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        job = (await db.execute(stmt)).scalars().first()
        if job is None:
            await db.commit()
            return None
        job.status = "RUNNING"
        job.attempts += 1
        job.locked_at = now
        await db.commit()
        return job

    @staticmethod
    async def finish(db: AsyncSession, job: AIJob, error: Optional[str] = None) -> None:
        if error is None:
            job.status, job.last_error, job.finished_at = "DONE", None, _utcnow()
        elif job.attempts >= job.max_attempts:
            job.status, job.last_error, job.finished_at = "FAILED", error[:1000], _utcnow()
        else:
            job.status = "PENDING"
            job.last_error = error[:1000]
            job.locked_at = None
            job.run_at = _utcnow() + timedelta(seconds=min(300, 5 * (2 ** job.attempts)))
        await db.commit()


SessionFactory = Callable[[], Any]  # returns an async context manager yielding an AsyncSession


@asynccontextmanager
async def _default_session() -> AsyncIterator[AsyncSession]:
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


async def run_one(session_factory: SessionFactory = _default_session) -> bool:
    """Claim and execute a single job. Returns False when the queue is empty."""
    import app.services.job_handlers  # noqa: F401  (registers the handlers; idempotent)

    async with session_factory() as db:
        job = await JobQueue.claim_next(db)
        if job is None:
            return False
        handler = _HANDLERS.get(job.job_type)
        if handler is None:
            await JobQueue.finish(db, job, error=f"No handler registered for {job.job_type}")
            return True
        try:
            await handler(db, job)
            await JobQueue.finish(db, job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Job %s (%s) failed", job.id, job.job_type)
            await db.rollback()
            await JobQueue.finish(db, job, error=f"{type(exc).__name__}: {exc}")
        return True


async def run_pending(session_factory: SessionFactory = _default_session, limit: int = 50) -> int:
    """Drain up to `limit` due jobs (tests and the manual 'process now' paths)."""
    done = 0
    while done < limit and await run_one(session_factory):
        done += 1
    return done


class Worker:
    """Runs N job loops plus the follow-up scheduler inside the API process (or a dedicated one)."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def _job_loop(self, n: int) -> None:
        while self._running:
            try:
                had_work = await run_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker %d crashed once; continuing", n)
                had_work = False
            if not had_work:
                await asyncio.sleep(settings.AI_WORKER_POLL_SECONDS)

    async def _scheduler_loop(self) -> None:
        from app.services.follow_up_service import FollowUpService
        from app.services.lead_assignment_service import LeadAssignmentService
        from app.services.task_reminders import send_due_reminders

        await asyncio.sleep(5)
        await self._maintenance_once()
        while self._running:
            try:
                async with _default_session() as db:
                    count = await FollowUpService.process_due_followups(db)
                    if count:
                        logger.info("Follow-up scheduler dispatched %d message(s)", count)
                    await send_due_reminders(db)
                    await LeadAssignmentService.assign_unowned(db)
                    await db.execute(
                        delete(AIJob).where(
                            AIJob.status.in_(["DONE", "FAILED"]), AIJob.finished_at < _utcnow() - timedelta(days=7)
                        )
                    )
                    await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Follow-up scheduler iteration failed")
            await asyncio.sleep(settings.FOLLOWUP_SCAN_SECONDS)

    async def _maintenance_once(self) -> None:
        """Queue a re-embed when chunks were produced by an older embedding model (e.g. after a model switch)."""
        try:
            from app.services.ai.vector_store import sync_pgvector
            from app.services.rag_service import RAGService

            async with _default_session() as db:
                stale = await RAGService.count_stale(db)
                if stale:
                    logger.info("%d knowledge chunk(s) use an outdated embedding model; scheduling re-embed", stale)
                    await JobQueue.enqueue(db, "REINDEX_TENANT", {}, dedupe=True)
                await sync_pgvector(db)
                await db.commit()
        except Exception:
            logger.exception("Startup maintenance failed")

    async def start(self) -> None:
        if self._running:
            return
        import app.services.job_handlers  # noqa: F401  (registers handlers)

        self._running = True
        for i in range(max(1, settings.AI_WORKER_CONCURRENCY)):
            self._tasks.append(asyncio.create_task(self._job_loop(i)))
        self._tasks.append(asyncio.create_task(self._scheduler_loop()))
        logger.info("AI worker started (%d job loops + follow-up scheduler)", settings.AI_WORKER_CONCURRENCY)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()


worker = Worker()
