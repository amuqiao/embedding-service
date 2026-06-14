import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.exceptions import AppError
from app.core.logging import set_request_id
from app.core.config import settings
from app.repositories.job_repo import JobRepo
from app.services.callbacks import deliver_callback
from app.services.executor import run_ai_job
from app.services.jobs import _load_input_text, _persist_large_artifacts, get_job_or_404
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _session_factory():
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _with_db(coro):
    engine, session_factory = _session_factory()
    try:
        async with session_factory() as db:
            return await coro(db)
    finally:
        await engine.dispose()


@celery_app.task(name="jobs.process", bind=True, acks_late=True)
def process_job_task(self, job_id: str):
    set_request_id(job_id)
    try:
        return asyncio.run(_process(job_id, self.request.id))
    except (SoftTimeLimitExceeded, asyncio.TimeoutError) as exc:
        # 两类超时（asyncio.wait_for L1 / Celery SIGALRM L3）统一重试策略。
        # 每次 self.retry() 启动全新 Celery task，asyncio.wait_for 和 celery_soft_time_limit
        # 均从 0 重新计时；CELERY_MAX_RETRIES=0（默认）表示不重试，直接进入终态。
        if self.request.retries >= settings.CELERY_MAX_RETRIES:
            try:
                asyncio.run(_mark_timeout(job_id, self.request.id))
            except Exception:
                logger.exception("job_timeout_cleanup_error job_id=%s", job_id)
            raise
        logger.warning(
            "job_timeout_retry job_id=%s attempt=%d/%d exc=%s",
            job_id,
            self.request.retries + 1,
            settings.CELERY_MAX_RETRIES,
            type(exc).__name__,
        )
        raise self.retry(
            exc=exc,
            countdown=settings.CELERY_RETRY_DELAY,
            max_retries=settings.CELERY_MAX_RETRIES,
        )
    except Exception as exc:
        try:
            asyncio.run(_fail(job_id, None, exc, self.request.id))
        except Exception:
            logger.exception("job_fail_cleanup_error job_id=%s", job_id)
        raise


async def _process(job_id: str, celery_task_id: str) -> dict[str, Any]:
    job_uuid = uuid.UUID(job_id)
    started = time.monotonic()

    async def run(db):
        job = await get_job_or_404(db, job_uuid)
        if job.status in ("succeeded", "failed"):
            logger.warning("job_skipped job_id=%s status=%s", job_id, job.status)
            return {"job_id": job_id, "status": "skipped", "job_type": job.job_type}
        if job.celery_task_id != celery_task_id:
            logger.warning(
                "job_task_id_mismatch job_id=%s message_task_id=%s db_task_id=%s status=%s",
                job_id, celery_task_id, job.celery_task_id, job.status,
            )
            return {"job_id": job_id, "status": "skipped", "job_type": job.job_type}
        logger.info("job_started job_id=%s job_type=%s model_id=%s", job_id, job.job_type, job.model_id)
        if job.status == "queued":
            claimed = await JobRepo.mark_running_if_queued(db, job.id, celery_task_id=celery_task_id)
            if not claimed:
                logger.warning("job_claim_failed job_id=%s status=%s", job_id, job.status)
                return {"job_id": job_id, "status": "skipped", "job_type": job.job_type}
        elif job.status == "running":
            # status == 'running'：Path A 恢复重执行（Worker 崩溃后消息回队）
            refreshed = await JobRepo.mark_running(db, job.id, celery_task_id=celery_task_id)
            if not refreshed:
                logger.warning("job_running_claim_lost job_id=%s task_id=%s", job_id, celery_task_id)
                return {"job_id": job_id, "status": "skipped", "job_type": job.job_type}
        else:
            logger.warning("job_skipped_unexpected_status job_id=%s status=%s", job_id, job.status)
            return {"job_id": job_id, "status": "skipped", "job_type": job.job_type}
        await db.commit()

        input_text = _load_input_text(job)
        result = await asyncio.wait_for(
                    run_ai_job(job.job_type, job.model_id, job.prompt_payload, input_text),
                    timeout=settings.MODEL_CALL_TIMEOUT_SECONDS,
                )
        result_data = _persist_large_artifacts(job, result)

        succeeded = await JobRepo.mark_succeeded(db, job.id, celery_task_id=celery_task_id, result_payload=result_data)
        await db.commit()
        if not succeeded:
            logger.warning("job_success_state_lost job_id=%s", job_id)
            return {"job_id": job_id, "status": "skipped", "job_type": job.job_type}

        return {"job_id": job_id, "status": "succeeded", "job_type": job.job_type}

    result = await _with_db(run)
    if result.get("status") == "succeeded":
        await deliver_callback_for_job(job_uuid)
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "job_completed job_id=%s job_type=%s duration_ms=%d",
            job_id, result.get("job_type", "unknown"), duration_ms,
        )
    return result


async def _fail(job_id: str, _work_item_id: str | None, exc: Exception, celery_task_id: str) -> None:
    if isinstance(exc, AppError):
        logger.error("job_failed job_id=%s error_code=%s", job_id, exc.code)
        error_payload = {"code": exc.code, "message": exc.message, "details": exc.details}
    else:
        logger.error("job_failed job_id=%s error_type=%s", job_id, type(exc).__name__, exc_info=True)
        error_payload = {
            "code": "MODEL_CALL_FAILED",
            "message": "模型调用失败或内部处理失败",
            "details": {"type": type(exc).__name__, "message": str(exc)[:500]},
        }

    async def run(db):
        job_uuid = uuid.UUID(job_id)
        marked = await JobRepo.mark_failed(db, job_uuid, error_payload, celery_task_id=celery_task_id)
        await db.commit()
        return marked

    marked = await _with_db(run)
    if marked:
        await deliver_callback_for_job(uuid.UUID(job_id))
    else:
        logger.warning("job_fail_state_lost job_id=%s", job_id)


async def _mark_timeout(job_id: str, celery_task_id: str) -> None:
    logger.error("job_timeout job_id=%s", job_id)

    async def run(db):
        job_uuid = uuid.UUID(job_id)
        error_payload = {"code": "JOB_TIMEOUT", "message": "任务执行超时", "details": {}}
        marked = await JobRepo.mark_failed(db, job_uuid, error_payload, celery_task_id=celery_task_id)
        await db.commit()
        return marked

    marked = await _with_db(run)
    if marked:
        await deliver_callback_for_job(uuid.UUID(job_id))
    else:
        logger.warning("job_timeout_state_lost job_id=%s", job_id)


async def deliver_callback_for_job(job_id: uuid.UUID) -> bool:
    async def run(db):
        job = await get_job_or_404(db, job_id)
        if job.status not in ("succeeded", "failed"):
            return False
        if job.callback_attempts >= settings.CALLBACK_MAX_DELIVERY_ATTEMPTS:
            return False
        now = datetime.now(timezone.utc)
        delivery_deadline = now + timedelta(seconds=settings.callback_delivery_timeout_seconds)
        claimed = await JobRepo.mark_callback_delivering(
            db,
            job.id,
            now=now,
            max_attempts=settings.CALLBACK_MAX_DELIVERY_ATTEMPTS,
            next_retry_at=delivery_deadline,
        )
        await db.commit()
        if not claimed:
            return False

        result = await deliver_callback(job)
        next_retry_at = None
        if result.status == "failed" and job.callback_attempts + result.attempts < settings.CALLBACK_MAX_DELIVERY_ATTEMPTS:
            next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=settings.CALLBACK_RETRY_DELAY_SECONDS)
        await JobRepo.mark_callback_result(
            db,
            job.id,
            status=result.status,
            attempts_increment=result.attempts,
            last_error=result.last_error,
            next_retry_at=next_retry_at,
        )
        await db.commit()
        return result.status in ("delivered", "skipped")

    try:
        return await _with_db(run)
    except Exception:
        logger.exception("callback_delivery_record_error job_id=%s", job_id)
        return False


@celery_app.task(name="jobs.execute_work_item", bind=True, acks_late=True)
def execute_work_item_task(self, job_id: str, item_id: str) -> dict:
    """Execute a single work item (chunk, memory, scan, or whole)."""
    set_request_id(f"{job_id}:{item_id}")
    try:
        async def run(db):
            from app.services.job_workflow import execute_work_item
            return await execute_work_item(
                db,
                job_id=uuid.UUID(job_id),
                item_id=uuid.UUID(item_id),
                celery_task_id=self.request.id,
            )
        return asyncio.run(_with_db(run))
    except Exception as exc:
        try:
            async def fail(db):
                from app.services.job_workflow import fail_job
                await fail_job(
                    db,
                    job_id=uuid.UUID(job_id),
                    item_id=uuid.UUID(item_id),
                    error_payload={
                        "code": "WORK_ITEM_FAILED",
                        "message": str(exc)[:500],
                        "details": {"type": type(exc).__name__},
                    },
                )
            asyncio.run(_with_db(fail))
        except Exception:
            logger.exception("work_item_fail_cleanup_error job_id=%s item_id=%s", job_id, item_id)
        raise


@celery_app.task(name="jobs.fanout_after_mapping", acks_late=True)
def fanout_after_mapping_task(memory_result: dict, job_id: str, chunk_item_ids: list[str]) -> None:
    """After memory mapping completes, dispatch all chunk tasks in parallel."""
    from celery import group as celery_group
    celery_group([
        execute_work_item_task.s(job_id, item_id)
        for item_id in chunk_item_ids
    ]).apply_async()


@celery_app.task(name="jobs.finalize_job", acks_late=True)
def finalize_job_task(prev_result, job_id: str) -> dict:
    """Finalize a job after all work items complete."""
    set_request_id(job_id)
    async def run(db):
        from app.services.job_workflow import finalize_job
        return await finalize_job(db, uuid.UUID(job_id))
    try:
        return asyncio.run(_with_db(run))
    except Exception as exc:
        logger.exception("finalize_job_error job_id=%s", job_id)
        raise


@celery_app.task(name="jobs.cleanup_expired")
def cleanup_expired_jobs_task() -> dict[str, Any]:
    """清理过期 Job 记录；worker recovery loop 会周期调用同一 repo 方法。"""
    async def run(db):
        deleted_count = await JobRepo.cleanup_expired_jobs(db)
        await db.commit()
        return {"deleted_count": deleted_count}

    result = asyncio.run(_with_db(run))
    deleted_count = result.get("deleted_count", 0)
    logger.info("cleanup_expired_jobs_completed deleted_count=%d", deleted_count)
    return {"deleted_count": deleted_count, "status": "success"}
