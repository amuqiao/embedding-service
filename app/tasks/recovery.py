from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.repositories.ai_call_log_repo import AiCallLogRepo
from app.repositories.job_repo import JobRepo

logger = logging.getLogger(__name__)

_AI_LEDGER_STALE_PENDING_AFTER_JOB_STALE_SECONDS = 60


def _make_session():
    engine = create_async_engine(settings.database.url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _stale_pending_ai_call_before(now: datetime) -> datetime:
    threshold_seconds = settings.job_stale_running_seconds + _AI_LEDGER_STALE_PENDING_AFTER_JOB_STALE_SECONDS
    return now - timedelta(seconds=threshold_seconds)


async def _run_recovery(db: AsyncSession) -> dict:
    recovered = 0
    failed = 0
    callback_due: list[str] = []
    deleted = 0
    dispatch_attempts: list[uuid.UUID] = []
    workflow_advances = []
    workflow_reconciled = 0
    dispatch_reconciled = 0
    callback_reconciled = 0
    ai_ledger_reconciled = 0

    lock_result = await db.execute(text("SELECT pg_try_advisory_lock(hashtext('job_recovery_loop'))"))
    locked = bool(lock_result.scalar_one())
    if not locked:
        logger.info("recovery: another worker holds recovery lock, skipping")
        return {
            "recovered": recovered,
            "failed": failed,
            "callbacks": 0,
            "deleted": deleted,
            "workflow_reconciled": workflow_reconciled,
            "dispatch_reconciled": dispatch_reconciled,
            "callback_reconciled": callback_reconciled,
            "ai_ledger_reconciled": ai_ledger_reconciled,
            "locked": False,
        }

    try:
        now = datetime.now(timezone.utc)
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
                retryable="JOB_TIMEOUT" in (attempt.policy_retryable_error_codes or []),
                retry_created_reason="recovery_retry",
            )
            if claimed:
                terminal_job = await JobRepo.get(db, attempt.job_id)
                if terminal_job is not None and terminal_job.root_job_id is not None and terminal_job.status == "failed":
                    from app.workflows.orchestrator import advance_workflow_after_child_terminal

                    workflow_advances.append(
                        await advance_workflow_after_child_terminal(db, child_job=terminal_job)
                    )
                failed += 1
                logger.warning("recovery: failed stale running attempt %s", attempt.id)

        from app.workflows.orchestrator import reconcile_workflow_root

        workflow_roots = await JobRepo.find_workflow_roots_for_reconciliation(
            db,
            limit=settings.job.recovery_batch_size,
        )
        for root_job in workflow_roots:
            result = await reconcile_workflow_root(db, root_job_id=root_job.id)
            if result.created_attempt_ids or result.finalized_root_job_id is not None:
                workflow_advances.append(result)
                workflow_reconciled += 1
        workflow_created_attempt_ids = {
            attempt_id
            for result in workflow_advances
            for attempt_id in (result.created_attempt_ids or ())
        }
        workflow_finalized_root_ids = {
            result.finalized_root_job_id
            for result in workflow_advances
            if result.finalized_root_job_id is not None
        }

        missing_dispatch_attempts = await JobRepo.find_active_pending_attempts_missing_dispatch(
            db,
            limit=settings.job.recovery_batch_size,
        )
        for attempt in missing_dispatch_attempts:
            next_attempt_at = attempt.next_attempt_scheduled_at or now
            await JobRepo.create_dispatch_outbox(
                db,
                event_job_id=attempt.job_id,
                attempt_id=attempt.id,
                next_attempt_at=next_attempt_at,
                dispatch_reason="reconciler_missing_dispatch",
            )
            if next_attempt_at <= now:
                dispatch_attempts.append(attempt.id)
            dispatch_reconciled += 1

        terminal_jobs_missing_callback = await JobRepo.find_terminal_root_jobs_missing_callback_outbox(
            db,
            limit=settings.job.recovery_callback_batch_size,
        )
        for job in terminal_jobs_missing_callback:
            outbox = await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
            if outbox is not None:
                callback_reconciled += 1
                if outbox.next_attempt_at is not None and job.id not in workflow_finalized_root_ids:
                    callback_due.append(str(job.id))

        due_dispatches = await JobRepo.find_due_dispatches(
            db,
            now,
            limit=settings.job.recovery_batch_size,
        )
        dispatch_attempts.extend(
            dispatch.attempt_id
            for dispatch in due_dispatches
            if dispatch.attempt_id not in workflow_created_attempt_ids
        )

        due_callbacks = await JobRepo.find_due_callbacks(
            db,
            now=now,
            max_attempts=settings.callback.max_delivery_attempts,
            limit=settings.job.recovery_callback_batch_size,
        )
        callback_due.extend(str(job.id) for job in due_callbacks if job.id not in workflow_finalized_root_ids)

        ai_ledger_reconciled = await AiCallLogRepo.mark_stale_pending_failed(
            db,
            before=_stale_pending_ai_call_before(now),
            limit=settings.job.recovery_batch_size,
        )
        if ai_ledger_reconciled:
            logger.warning("recovery: reconciled stale pending ai call logs count=%s", ai_ledger_reconciled)

        deleted = await JobRepo.cleanup_expired_jobs(db)
        await db.commit()
    finally:
        await db.execute(text("SELECT pg_advisory_unlock(hashtext('job_recovery_loop'))"))
        await db.commit()

    if dispatch_attempts:
        from app.tasks.jobs import publish_job_attempt

        for attempt_id in dict.fromkeys(dispatch_attempts):
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

    if workflow_advances:
        from app.tasks.jobs import handle_workflow_advance_result

        for result in workflow_advances:
            try:
                await handle_workflow_advance_result(result)
            except Exception:
                logger.exception("recovery: workflow advance side effect failed root_job_id=%s", result.root_job_id)

    return {
        "recovered": recovered,
        "failed": failed,
        "callbacks": len(callback_due),
        "deleted": deleted,
        "workflow_reconciled": workflow_reconciled,
        "dispatch_reconciled": dispatch_reconciled,
        "callback_reconciled": callback_reconciled,
        "ai_ledger_reconciled": ai_ledger_reconciled,
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
