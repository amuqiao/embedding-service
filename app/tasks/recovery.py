import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.infrastructure.config import settings
from app.repositories.job_repo import JobRepo
from app.services.callbacks import deliver_callback
from app.services.jobs import get_job_or_404

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

    # stuck-dispatched: queued + celery_task_id 非 NULL（commit 后 dispatch 前 crash）
    # 使用 2x orphan timeout 避免误判刚分配 task_id 但尚未 dispatch 的正常 job。
    # mark_running_if_queued CAS 保证即使原 task 仍在 Redis，也不会双执行。
    stuck_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.JOB_ORPHAN_TIMEOUT_SECONDS * 2)
    stuck = await JobRepo.find_stuck_dispatched_jobs(db, stuck_cutoff)
    for job in stuck:
        from app.tasks.jobs import process_job_task  # 延迟导入避免循环依赖
        import uuid as _uuid
        new_task_id = str(_uuid.uuid4())
        claimed = await JobRepo.claim_stuck_for_dispatch(db, job.id, job.celery_task_id, new_task_id)
        await db.commit()
        if claimed:
            process_job_task.apply_async(args=[str(job.id)], task_id=new_task_id)
            recovered += 1
            logger.info("recovery: re-dispatched stuck-dispatched job %s", job.id)
        else:
            logger.info("recovery: stuck job %s already re-claimed by peer, skipping", job.id)

    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.JOB_STALE_RUNNING_SECONDS)
    stale = await JobRepo.find_stale_running_jobs(db, stale_cutoff)
    for job in stale:
        error_payload = {
            "code": "JOB_TIMEOUT",
            "message": "任务长时间未完成，已强制终止",
            "details": {"started_at": job.started_at.isoformat() if job.started_at else None},
        }
        claimed = await JobRepo.mark_failed_if_running(db, job.id, error_payload)
        await db.commit()
        if claimed:
            failed += 1
            logger.warning("recovery: force-failed stale running job %s", job.id)
            try:
                refreshed = await get_job_or_404(db, job.id)
                await deliver_callback(refreshed)
            except Exception:
                logger.exception("recovery: callback delivery failed for stale job %s", job.id)
        else:
            logger.info("recovery: stale job %s already handled by peer worker, skipping", job.id)

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
