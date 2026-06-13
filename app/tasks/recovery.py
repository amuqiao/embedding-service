import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.infrastructure.config import settings
from app.repositories.job_repo import JobRepo

logger = logging.getLogger(__name__)


def _make_session():
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _run_recovery(db) -> dict:
    recovered = 0
    failed = 0

    orphan_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.JOB_ORPHAN_TIMEOUT_SECONDS)
    orphans = await JobRepo.find_orphaned_queued_jobs(db, orphan_cutoff)
    for job in orphans:
        from app.tasks.jobs import process_job_task  # 延迟导入避免循环依赖
        import uuid as _uuid
        new_task_id = str(_uuid.uuid4())
        claimed = await JobRepo.claim_orphan_for_dispatch(db, job.id, new_task_id)
        await db.commit()
        if claimed:
            process_job_task.apply_async(args=[str(job.id)], task_id=new_task_id)
            recovered += 1
            logger.info("recovery: re-dispatched orphaned job %s", job.id)
        else:
            logger.info("recovery: job %s already claimed by peer worker, skipping", job.id)

    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.JOB_STALE_RUNNING_SECONDS)
    stale = await JobRepo.find_stale_running_jobs(db, stale_cutoff)
    for job in stale:
        await JobRepo.mark_failed(db, job.id, {
            "code": "JOB_TIMEOUT",
            "message": "任务长时间未完成，已强制终止",
            "details": {"started_at": job.started_at.isoformat() if job.started_at else None},
        })
        await db.commit()
        failed += 1
        logger.warning("recovery: force-failed stale running job %s", job.id)

    return {"recovered": recovered, "failed": failed}


def run_recovery() -> dict:
    async def _with_db():
        engine, factory = _make_session()
        try:
            async with factory() as db:
                return await _run_recovery(db)
        finally:
            await engine.dispose()

    return asyncio.run(_with_db())
