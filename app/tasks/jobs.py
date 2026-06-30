from __future__ import annotations

import asyncio
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
from app.models.job import JobAttempt
from app.repositories.job_repo import JobRepo
from app.services.callbacks import deliver_callback
from app.services.jobs import get_job_or_404
from app.tasks.taskiq_app import broker
from app.tasks.runtime import ensure_worker_runtime_initialized

logger = logging.getLogger(__name__)

_CLAIM_RETRY_ATTEMPTS = 3
_CLAIM_RETRY_DELAY_SECONDS = 0.2


class TaskiqPublishDeferredError(RuntimeError):
    def __init__(self, attempt_id: uuid.UUID, error: dict[str, Any]):
        super().__init__("Taskiq publish failed after job was committed; attempt is scheduled for recovery")
        self.attempt_id = attempt_id
        self.error = error


def _ensure_workflows_registered() -> None:
    ensure_worker_runtime_initialized()


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


def _callback_delivery_payload(payload: dict[str, Any], outbox, *, next_retry_at: datetime) -> dict[str, Any]:
    delivery_attempt = (outbox.delivery_attempts or 0) + 1
    body = dict(payload)
    body["attempt"] = delivery_attempt
    job_payload = dict(body.get("job") or {})
    callback_payload = dict(job_payload.get("callback") or {})
    callback_payload.update(
        {
            "status": "delivering",
            "attempt": delivery_attempt,
            "last_error": outbox.last_error,
            "next_retry_at": next_retry_at.isoformat(),
        }
    )
    job_payload["callback"] = callback_payload
    body["job"] = job_payload
    return body


def _should_retry_attempt(attempt: JobAttempt, error: dict[str, Any]) -> bool:
    retryable_error_codes = attempt.policy_retryable_error_codes or []
    return str(error.get("code") or "") in retryable_error_codes


async def publish_job_attempt(attempt_id: uuid.UUID) -> None:
    async def lease_dispatch(db):
        leased = await JobRepo.lease_dispatch_for_publish(
            db,
            attempt_id,
            lease_seconds=settings.job.orphan_timeout_seconds,
        )
        await db.commit()
        return leased

    leased = await _with_db(lease_dispatch)
    if leased is None:
        return
    dispatch, lease_token = leased

    try:
        await run_job_attempt.kiq(str(attempt_id))
    except Exception as exc:
        error = {
            "code": "TASKIQ_PUBLISH_FAILED",
            "message": "Taskiq publish failed",
            "details": {"type": type(exc).__name__, "message": str(exc)[:500]},
        }
        async def mark_publish_failed(db):
            await JobRepo.mark_dispatch_publish_failed(
                db,
                dispatch.id,
                lease_token=lease_token,
                error=error,
                next_attempt_at=datetime.now(timezone.utc) + timedelta(seconds=dispatch.publish_retry_delay_seconds),
                max_publish_attempts=dispatch.max_publish_attempts,
            )
            await db.commit()

        await _with_db(mark_publish_failed)
        raise TaskiqPublishDeferredError(attempt_id, error) from exc

    async def mark_published(db):
        marked = await JobRepo.mark_dispatch_published(
            db,
            dispatch.id,
            lease_token=lease_token,
            next_attempt_at=datetime.now(timezone.utc) + timedelta(seconds=dispatch.orphan_timeout_seconds),
        )
        await db.commit()
        return marked

    if not await _with_db(mark_published):
        logger.warning(
            "dispatch_publish_mark_conflict attempt_id=%s dispatch_id=%s",
            attempt_id,
            dispatch.id,
        )


async def handle_workflow_advance_result(result: Any) -> None:
    for child_attempt_id in getattr(result, "created_attempt_ids", ()) or ():
        try:
            await publish_job_attempt(child_attempt_id)
        except TaskiqPublishDeferredError:
            logger.exception(
                "workflow_downstream_attempt_publish_deferred root_job_id=%s child_attempt_id=%s",
                getattr(result, "root_job_id", None),
                child_attempt_id,
            )
    finalized_root_job_id = getattr(result, "finalized_root_job_id", None)
    if finalized_root_job_id is not None:
        await deliver_callback_for_job(finalized_root_job_id)


@broker.task(task_name="jobs.run_attempt")
async def run_job_attempt(attempt_id: str) -> dict[str, Any]:
    _ensure_workflows_registered()
    attempt_uuid = uuid.UUID(attempt_id)
    set_request_id(attempt_id)
    lease_token: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    job = None
    claimed_attempt: JobAttempt | None = None
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

        claimed = None
        for claim_attempt_no in range(1, _CLAIM_RETRY_ATTEMPTS + 1):
            claimed = await _with_db(claim)
            if claimed is not None:
                break
            if claim_attempt_no < _CLAIM_RETRY_ATTEMPTS:
                logger.info(
                    "taskiq_attempt_claim_retry attempt_id=%s attempt_no=%d",
                    attempt_id,
                    claim_attempt_no + 1,
                )
                await asyncio.sleep(_CLAIM_RETRY_DELAY_SECONDS)
        if claimed is None:
            logger.info("taskiq_attempt_skipped attempt_id=%s reason=claim_failed", attempt_id)
            return {"attempt_id": attempt_id, "status": "skipped"}
        job, claimed_attempt, lease_token = claimed
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
                    details={"attempt_id": attempt_id, "phase": phase},
                )

        async def execute(db):
            from app.jobs.runner import execute_job

            await heartbeat("before:execute")
            return await execute_job(
                db,
                job.id,
                attempt_id=attempt_uuid,
                lease_token=lease_token,
            )

        result = await _with_db(execute)
        if result.get("status") != "succeeded":
            raise AppError("JOB_EXECUTION_FAILED", "job attempt finished without success", details=result)
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
                    retryable=claimed_attempt is not None and _should_retry_attempt(claimed_attempt, error),
                )
                workflow_advance = None
                if marked and job_id is not None and job is not None and job.root_job_id is not None:
                    terminal_job = await JobRepo.get(db, job_id)
                    if terminal_job is not None and terminal_job.root_job_id is not None and terminal_job.status == "failed":
                        from app.workflows.orchestrator import advance_workflow_after_child_terminal

                        workflow_advance = await advance_workflow_after_child_terminal(db, child_job=terminal_job)
                await db.commit()
                return marked, workflow_advance

            marked, workflow_advance = await _with_db(mark_failed)
            if marked and job_id is not None:
                if workflow_advance is not None:
                    await handle_workflow_advance_result(workflow_advance)
                else:
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

        result = await deliver_callback(
            claimed_job,
            payload=_callback_delivery_payload(outbox.payload, outbox, next_retry_at=delivery_deadline),
            callback_url=outbox.callback_url,
        )
        next_retry_at = None
        attempted_after_result = (outbox.delivery_attempts or 0) + max(0, result.attempts)
        if result.status == "failed" and attempted_after_result < settings.callback.max_delivery_attempts:
            next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=settings.callback.retry_delay_seconds)
        await JobRepo.mark_callback_result(
            db,
            claimed_job.id,
            status=result.status,
            last_error=result.last_error,
            next_retry_at=next_retry_at,
            max_attempts=settings.callback.max_delivery_attempts,
            delivery_attempts=result.attempts,
            last_http_status=result.http_status,
            last_response=result.response,
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
