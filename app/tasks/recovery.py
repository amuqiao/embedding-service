import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.repositories.job_repo import JobRepo

logger = logging.getLogger(__name__)


def _make_session():
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _run_recovery(db) -> dict:
    recovered = 0
    failed = 0
    callback_due: list[str] = []
    deleted = 0

    lock_result = await db.execute(text("SELECT pg_try_advisory_lock(hashtext('job_recovery_loop'))"))
    locked = bool(lock_result.scalar_one())
    if not locked:
        logger.info("recovery: another worker holds recovery lock, skipping")
        return {"recovered": recovered, "failed": failed, "callbacks": 0, "deleted": deleted, "locked": False}

    try:
        orphan_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.JOB_ORPHAN_TIMEOUT_SECONDS)
        orphans = await JobRepo.find_orphaned_queued_jobs(
            db,
            orphan_cutoff,
            limit=settings.JOB_RECOVERY_BATCH_SIZE,
        )
        for job in orphans:
            from app.tasks.jobs import dispatch_job_task  # 延迟导入避免循环依赖
            import uuid as _uuid
            new_task_id = str(_uuid.uuid4())
            claimed = await JobRepo.claim_orphan_for_dispatch(db, job.id, new_task_id)
            await db.commit()
            if claimed:
                dispatch_job_task.apply_async(args=[str(job.id)], task_id=new_task_id)
                await JobRepo.mark_celery_published(db, job.id, new_task_id)
                await db.commit()
                recovered += 1
                logger.info("recovery: re-dispatched orphaned job %s", job.id)
            else:
                logger.info("recovery: job %s already claimed by peer worker, skipping", job.id)

        unpublished_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.JOB_ORPHAN_TIMEOUT_SECONDS)
        unpublished = await JobRepo.find_unpublished_queued_jobs(
            db,
            unpublished_cutoff,
            limit=settings.JOB_RECOVERY_BATCH_SIZE,
        )
        for job in unpublished:
            from app.tasks.jobs import dispatch_job_task  # 延迟导入避免循环依赖
            import uuid as _uuid
            new_task_id = str(_uuid.uuid4())
            claimed = await JobRepo.claim_unpublished_for_dispatch(db, job.id, job.celery_task_id, new_task_id)
            await db.commit()
            if claimed:
                dispatch_job_task.apply_async(args=[str(job.id)], task_id=new_task_id)
                await JobRepo.mark_celery_published(db, job.id, new_task_id)
                await db.commit()
                recovered += 1
                logger.info("recovery: re-dispatched unpublished job %s", job.id)
            else:
                logger.info("recovery: unpublished job %s already re-claimed by peer, skipping", job.id)

        stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.job_stale_running_seconds)
        stale = await JobRepo.find_stale_running_jobs(
            db,
            stale_cutoff,
            limit=settings.JOB_RECOVERY_BATCH_SIZE,
        )
        for job in stale:
            attempts = job.execution_attempts or 0
            if attempts >= settings.JOB_MAX_EXECUTION_ATTEMPTS:
                error = {
                    "code": "JOB_TIMEOUT",
                    "message": "任务多次执行后仍未收敛，已强制终止",
                    "details": {
                        "started_at": job.started_at.isoformat() if job.started_at else None,
                        "execution_attempts": attempts,
                        "max_execution_attempts": settings.JOB_MAX_EXECUTION_ATTEMPTS,
                    },
                }
                claimed = await JobRepo.mark_failed_if_running(db, job.id, error)
                await db.commit()
                if claimed:
                    failed += 1
                    callback_due.append(str(job.id))
                    logger.warning(
                        "recovery: force-failed stale running job %s after %d attempts",
                        job.id,
                        attempts,
                    )
                else:
                    logger.info("recovery: stale job %s already handled by peer worker, skipping", job.id)
                continue

            from app.tasks.jobs import dispatch_job_task  # 延迟导入避免循环依赖

            new_task_id = str(uuid.uuid4())
            claimed = await JobRepo.requeue_stale_running_for_recovery(
                db,
                job.id,
                new_task_id=new_task_id,
                max_execution_attempts=settings.JOB_MAX_EXECUTION_ATTEMPTS,
            )
            await db.commit()
            if claimed:
                dispatch_job_task.apply_async(args=[str(job.id)], task_id=new_task_id)
                await JobRepo.mark_celery_published(db, job.id, new_task_id)
                await db.commit()
                recovered += 1
                logger.warning(
                    "recovery: re-dispatched stale running job %s as whole job attempt %d/%d",
                    job.id,
                    attempts + 1,
                    settings.JOB_MAX_EXECUTION_ATTEMPTS,
                )
            else:
                logger.info("recovery: stale job %s already handled by peer worker, skipping", job.id)

        now = datetime.now(timezone.utc)
        due_callbacks = await JobRepo.find_due_callbacks(
            db,
            now=now,
            max_attempts=settings.CALLBACK_MAX_DELIVERY_ATTEMPTS,
            limit=settings.JOB_RECOVERY_CALLBACK_BATCH_SIZE,
        )
        callback_due.extend(str(job.id) for job in due_callbacks)

        deleted = await JobRepo.cleanup_expired_jobs(db)
        await db.commit()
    finally:
        await db.execute(text("SELECT pg_advisory_unlock(hashtext('job_recovery_loop'))"))
        await db.commit()

    if callback_due:
        from app.tasks.jobs import deliver_callback_for_job
        for job_id in dict.fromkeys(callback_due):
            try:
                delivered = await deliver_callback_for_job(uuid.UUID(job_id))
                if delivered:
                    logger.info("recovery: delivered pending callback for job %s", job_id)
            except Exception:
                logger.exception("recovery: callback delivery failed for job %s", job_id)

    return {
        "recovered": recovered,
        "failed": failed,
        "callbacks": len(callback_due),
        "deleted": deleted,
        "locked": True,
    }


def run_recovery() -> dict:
    async def _with_db():
        engine, _factory = _make_session()
        try:
            async with engine.connect() as conn:
                async with AsyncSession(bind=conn, expire_on_commit=False) as db:
                    return await _run_recovery(db)
        finally:
            await engine.dispose()

    return asyncio.run(_with_db())
