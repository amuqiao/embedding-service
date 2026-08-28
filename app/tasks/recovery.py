from __future__ import annotations

import asyncio
import logging
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
    deleted = 0
    workflow_reconciled = 0
    dispatch_reconciled = 0
    dispatch_dead_letter_failed = 0
    callback_reconciled = 0
    ai_ledger_reconciled = 0
    locked = False

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
            "dispatch_dead_letter_failed": dispatch_dead_letter_failed,
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

                    await advance_workflow_after_child_terminal(db, child_job=terminal_job)
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
                workflow_reconciled += 1

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
            dispatch_reconciled += 1

        dead_lettered_dispatches = await JobRepo.find_dead_lettered_pending_dispatches(
            db,
            limit=settings.job.recovery_batch_size,
        )
        for dispatch in dead_lettered_dispatches:
            error = {
                "code": "DISPATCH_PUBLISH_EXHAUSTED",
                "message": "任务发布重试已耗尽，已收敛为失败",
                "details": {
                    "attempt_id": str(dispatch.attempt_id),
                    "dispatch_id": str(dispatch.id),
                    "dead_lettered_at": dispatch.dead_lettered_at.isoformat()
                    if dispatch.dead_lettered_at
                    else None,
                    "last_error": dispatch.last_error,
                },
            }
            failed_job = await JobRepo.mark_dead_lettered_dispatch_attempt_failed(
                db,
                dispatch.id,
                error=error,
            )
            if failed_job is not None:
                if failed_job.root_job_id is not None and failed_job.status == "failed":
                    from app.workflows.orchestrator import advance_workflow_after_child_terminal

                    await advance_workflow_after_child_terminal(db, child_job=failed_job)
                failed += 1
                dispatch_dead_letter_failed += 1
                logger.warning("recovery: failed dead-lettered dispatch attempt %s", dispatch.attempt_id)

        terminal_unpublished_dispatches = await JobRepo.find_terminal_attempts_with_unpublished_dispatches(
            db,
            limit=settings.job.recovery_batch_size,
        )
        for dispatch in terminal_unpublished_dispatches:
            if await JobRepo.mark_terminal_dispatch_reconciled_published(db, dispatch.id):
                dispatch_reconciled += 1

        terminal_jobs_missing_callback = await JobRepo.find_terminal_root_jobs_missing_callback_outbox(
            db,
            limit=settings.job.recovery_callback_batch_size,
        )
        for job in terminal_jobs_missing_callback:
            outbox = await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
            if outbox is not None:
                callback_reconciled += 1

        ai_ledger_reconciled = await AiCallLogRepo.mark_stale_pending_failed(
            db,
            before=_stale_pending_ai_call_before(now),
            limit=settings.job.recovery_batch_size,
        )
        if ai_ledger_reconciled:
            logger.warning("recovery: reconciled stale pending ai call logs count=%s", ai_ledger_reconciled)

        deleted = await JobRepo.cleanup_expired_jobs(db)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        if locked:
            await db.execute(text("SELECT pg_advisory_unlock(hashtext('job_recovery_loop'))"))
            await db.commit()

    return {
        "recovered": recovered,
        "failed": failed,
        "callbacks": 0,
        "deleted": deleted,
        "workflow_reconciled": workflow_reconciled,
        "dispatch_reconciled": dispatch_reconciled,
        "dispatch_dead_letter_failed": dispatch_dead_letter_failed,
        "callback_reconciled": callback_reconciled,
        "ai_ledger_reconciled": ai_ledger_reconciled,
        "locked": True,
    }


async def run_recovery_once() -> dict:
    engine, _factory = _make_session()
    try:
        async with engine.connect() as conn:
            async with AsyncSession(bind=conn, expire_on_commit=False) as db:
                return await _run_recovery(db)
    finally:
        await engine.dispose()


def run_recovery() -> dict:
    return asyncio.run(run_recovery_once())
