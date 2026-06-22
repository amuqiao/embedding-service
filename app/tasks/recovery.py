from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.repositories.job_repo import JobRepo

logger = logging.getLogger(__name__)


def _make_session():
    engine = create_async_engine(settings.database.url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _run_recovery(db: AsyncSession) -> dict:
    recovered = 0
    failed = 0
    callback_due: list[str] = []
    deleted = 0
    dispatch_attempts: list[uuid.UUID] = []

    lock_result = await db.execute(text("SELECT pg_try_advisory_lock(hashtext('job_recovery_loop'))"))
    locked = bool(lock_result.scalar_one())
    if not locked:
        logger.info("recovery: another worker holds recovery lock, skipping")
        return {"recovered": recovered, "failed": failed, "callbacks": 0, "deleted": deleted, "locked": False}

    try:
        now = datetime.now(timezone.utc)
        due_attempts = await JobRepo.find_dispatch_due_attempts(
            db,
            now,
            limit=settings.job.recovery_batch_size,
        )
        dispatch_attempts.extend(attempt.id for attempt in due_attempts)

        stale_attempts = await JobRepo.find_stale_running_attempts(
            db,
            now,
            limit=settings.job.recovery_batch_size,
        )
        for attempt in stale_attempts:
            error = {
                "code": "JOB_TIMEOUT",
                "message": "任务执行租约已过期，已收敛为失败",
                "details": {
                    "attempt_id": str(attempt.id),
                    "lease_expires_at": attempt.lease_expires_at.isoformat() if attempt.lease_expires_at else None,
                },
            }
            claimed = await JobRepo.mark_attempt_failed(
                db,
                attempt.id,
                lease_token=attempt.lease_token,
                error=error,
                error_kind="timeout",
                failure_phase="lease",
                retryable=True,
                next_dispatch_at=now,
            )
            if claimed:
                failed += 1
                logger.warning("recovery: failed stale running attempt %s", attempt.id)

        due_callbacks = await JobRepo.find_due_callbacks(
            db,
            now=now,
            max_attempts=settings.callback.max_delivery_attempts,
            limit=settings.job.recovery_callback_batch_size,
        )
        callback_due.extend(str(job.id) for job in due_callbacks)

        deleted = await JobRepo.cleanup_expired_jobs(db)
        await db.commit()
    finally:
        await db.execute(text("SELECT pg_advisory_unlock(hashtext('job_recovery_loop'))"))
        await db.commit()

    if dispatch_attempts:
        from app.tasks.jobs import publish_job_attempt

        for attempt_id in dispatch_attempts:
            try:
                await publish_job_attempt(attempt_id)
                recovered += 1
                logger.info("recovery: re-published attempt %s", attempt_id)
            except Exception:
                logger.exception("recovery: attempt publish failed %s", attempt_id)

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
