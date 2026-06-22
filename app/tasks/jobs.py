from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import set_request_id
from app.repositories.job_repo import JobRepo
from app.services.callbacks import deliver_callback
from app.services.jobs import get_job_or_404
from app.tasks.taskiq_app import broker

logger = logging.getLogger(__name__)


class TaskiqPublishDeferredError(RuntimeError):
    def __init__(self, attempt_id: uuid.UUID, error: dict[str, Any]):
        super().__init__("Taskiq publish failed after job was committed; attempt is scheduled for recovery")
        self.attempt_id = attempt_id
        self.error = error


def _ensure_workflows_registered() -> None:
    from app.jobs.types.register import register_all_job_types

    register_all_job_types()


def _session_factory():
    engine = create_async_engine(settings.database.url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _with_db(coro):
    engine, session_factory = _session_factory()
    try:
        async with session_factory() as db:
            return await coro(db)
    finally:
        await engine.dispose()


def _job_error_from_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AppError):
        return {"code": exc.code, "message": exc.message, "details": exc.details}
    if isinstance(exc, KeyError):
        return {
            "code": "INVALID_JOB_TYPE",
            "message": "stored job references an unregistered job_type",
            "details": {"type": type(exc).__name__, "message": str(exc)[:500]},
        }
    return {
        "code": "MODEL_CALL_FAILED",
        "message": "模型调用失败或内部处理失败",
        "details": {"type": type(exc).__name__, "message": str(exc)[:500]},
    }


async def publish_job_attempt(attempt_id: uuid.UUID) -> None:
    try:
        await run_job_attempt.kiq(str(attempt_id))
    except Exception as exc:
        error = {
            "code": "TASKIQ_PUBLISH_FAILED",
            "message": "Taskiq publish failed",
            "details": {"type": type(exc).__name__, "message": str(exc)[:500]},
        }

        async def record_failure(db):
            recorded = await JobRepo.mark_attempt_publish_failed(
                db,
                attempt_id,
                error=error,
                next_dispatch_at=datetime.now(timezone.utc) + timedelta(seconds=settings.job.orphan_timeout_seconds),
            )
            if recorded:
                await db.commit()
            return recorded

        if not await _with_db(record_failure):
            raise
        raise TaskiqPublishDeferredError(attempt_id, error) from exc

    async def mark_published(db):
        await JobRepo.mark_attempt_published(
            db,
            attempt_id,
            next_dispatch_at=datetime.now(timezone.utc) + timedelta(seconds=settings.job.orphan_timeout_seconds),
        )
        await db.commit()

    await _with_db(mark_published)


@broker.task(task_name="jobs.run_attempt")
async def run_job_attempt(attempt_id: str) -> dict[str, Any]:
    _ensure_workflows_registered()
    attempt_uuid = uuid.UUID(attempt_id)
    set_request_id(attempt_id)
    lease_token: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    try:
        worker_id = f"{os.uname().nodename}:{os.getpid()}"

        async def claim(db):
            claimed_attempt = await JobRepo.claim_attempt_for_execution(
                db,
                attempt_uuid,
                worker_id=worker_id,
                lease_seconds=settings.job_stale_running_seconds,
            )
            await db.commit()
            return claimed_attempt

        claimed = await _with_db(claim)
        if claimed is None:
            logger.info("taskiq_attempt_skipped attempt_id=%s reason=claim_failed", attempt_id)
            return {"attempt_id": attempt_id, "status": "skipped"}
        job, _attempt, lease_token = claimed
        job_id = job.id

        async def heartbeat(phase: str) -> None:
            async def extend(db):
                extended = await JobRepo.heartbeat_attempt(
                    db,
                    attempt_uuid,
                    lease_token=lease_token,
                    lease_seconds=settings.job_stale_running_seconds,
                )
                await db.commit()
                return extended

            if not await _with_db(extend):
                raise AppError(
                    "JOB_STATE_TRANSITION_CONFLICT",
                    "attempt lease could not be extended",
                    status_code=500,
                    details={"attempt_id": attempt_id, "phase": phase},
                )

        async def execute(db):
            from app.jobs.runner import execute_job

            await heartbeat("before:execute")
            return await execute_job(
                db,
                job.id,
                execution_generation=job.execution_generation,
                attempt_id=attempt_uuid,
                lease_token=lease_token,
            )

        result = await _with_db(execute)
        if result.get("status") != "succeeded":
            raise AppError("JOB_EXECUTION_FAILED", "job attempt finished without success", status_code=500, details=result)
        return {"attempt_id": attempt_id, "job_id": str(job_id), "status": "succeeded", "result": result}
    except Exception as exc:
        logger.exception("taskiq_attempt_failed attempt_id=%s", attempt_id)
        if lease_token is not None:
            error = _job_error_from_exception(exc)

            async def mark_failed(db):
                marked = await JobRepo.mark_attempt_failed(
                    db,
                    attempt_uuid,
                    lease_token=lease_token,
                    error=error,
                    retryable=True,
                    next_dispatch_at=datetime.now(timezone.utc),
                )
                await db.commit()
                return marked

            marked = await _with_db(mark_failed)
            if marked and job_id is not None:
                await deliver_callback_for_job(job_id)
        raise


async def deliver_callback_for_job(job_id: uuid.UUID) -> bool:
    async def run(db):
        job = await get_job_or_404(db, job_id)
        if job.status not in ("succeeded", "failed"):
            return False
        if not job.callback_url:
            return False
        now = datetime.now(timezone.utc)
        delivery_deadline = now + timedelta(seconds=settings.callback.delivery_timeout_seconds)
        claimed = await JobRepo.mark_callback_delivering(
            db,
            job.id,
            now=now,
            max_attempts=settings.callback.max_delivery_attempts,
            next_retry_at=delivery_deadline,
        )
        await db.commit()
        if not claimed:
            return False
        claimed_job, outbox = claimed

        claimed_job.callback_status = "delivering"
        claimed_job.callback_next_retry_at = delivery_deadline
        result = await deliver_callback(claimed_job, payload=outbox.payload)
        next_retry_at = None
        if result.status == "failed" and outbox.delivery_attempt < settings.callback.max_delivery_attempts:
            next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=settings.callback.retry_delay_seconds)
        await JobRepo.mark_callback_result(
            db,
            claimed_job.id,
            status=result.status,
            last_error=result.last_error,
            next_retry_at=next_retry_at,
            max_attempts=settings.callback.max_delivery_attempts,
            callback_id=outbox.id,
            lease_token=outbox.lease_token,
        )
        await db.commit()
        return result.status in ("delivered", "skipped")

    try:
        return await _with_db(run)
    except Exception:
        logger.exception("callback_delivery_record_error job_id=%s", job_id)
        return False


@broker.task(task_name="jobs.cleanup_expired")
async def cleanup_expired_jobs_task() -> dict[str, Any]:
    async def run(db):
        deleted_count = await JobRepo.cleanup_expired_jobs(db)
        await db.commit()
        return {"deleted_count": deleted_count}

    result = await _with_db(run)
    deleted_count = result.get("deleted_count", 0)
    logger.info("cleanup_expired_jobs_completed deleted_count=%d", deleted_count)
    return {"deleted_count": deleted_count, "status": "success"}
