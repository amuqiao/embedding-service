import asyncio
import logging
import time
import uuid
from typing import Any

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.exceptions import AppError
from app.core.logging import set_request_id
from app.infrastructure.config import settings
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
        return asyncio.run(_process(job_id))
    except (SoftTimeLimitExceeded, asyncio.TimeoutError) as exc:
        # 两类超时（asyncio.wait_for L1 / Celery SIGALRM L3）统一重试策略。
        # 每次 self.retry() 启动全新 Celery task，asyncio.wait_for 和 CELERY_SOFT_TIME_LIMIT
        # 均从 0 重新计时；CELERY_MAX_RETRIES=0（默认）表示不重试，直接进入终态。
        if self.request.retries >= settings.CELERY_MAX_RETRIES:
            try:
                asyncio.run(_mark_timeout(job_id))
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
            asyncio.run(_fail(job_id, None, exc))
        except Exception:
            logger.exception("job_fail_cleanup_error job_id=%s", job_id)
        raise


async def _process(job_id: str) -> dict[str, Any]:
    job_uuid = uuid.UUID(job_id)
    started = time.monotonic()

    async def run(db):
        job = await get_job_or_404(db, job_uuid)
        if job.status in ("succeeded", "failed"):
            logger.warning("job_skipped job_id=%s status=%s", job_id, job.status)
            return {"job_id": job_id, "status": "skipped", "job_type": job.job_type}
        logger.info("job_started job_id=%s job_type=%s model_id=%s", job_id, job.job_type, job.model_id)
        if job.status == "queued":
            claimed = await JobRepo.mark_running_if_queued(db, job.id)
            if not claimed:
                logger.warning("job_claim_failed job_id=%s status=%s", job_id, job.status)
                return {"job_id": job_id, "status": "skipped", "job_type": job.job_type}
        else:
            # status == 'running'：Path A 恢复重执行（Worker 崩溃后消息回队）
            await JobRepo.mark_running(db, job.id)
        await db.commit()

        input_text = _load_input_text(job)
        result = await asyncio.wait_for(
                    run_ai_job(job.job_type, job.model_id, job.prompt_payload, input_text),
                    timeout=settings.MODEL_CALL_TIMEOUT_SECONDS,
                )
        result_data = _persist_large_artifacts(job, result)

        await JobRepo.mark_succeeded(db, job.id, result_data)
        await db.commit()

        job = await JobRepo.get(db, job.id)
        await deliver_callback(job)
        return {"job_id": job_id, "status": "succeeded", "job_type": job.job_type}

    result = await _with_db(run)
    if result.get("status") == "succeeded":
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "job_completed job_id=%s job_type=%s duration_ms=%d",
            job_id, result.get("job_type", "unknown"), duration_ms,
        )
    return result


async def _fail(job_id: str, _work_item_id: str | None, exc: Exception) -> None:
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
        await JobRepo.mark_failed(db, job_uuid, error_payload)
        await db.commit()
        job = await get_job_or_404(db, job_uuid)
        await deliver_callback(job)

    await _with_db(run)


async def _mark_timeout(job_id: str) -> None:
    logger.error("job_timeout job_id=%s", job_id)

    async def run(db):
        job_uuid = uuid.UUID(job_id)
        error_payload = {"code": "JOB_TIMEOUT", "message": "任务执行超时", "details": {}}
        await JobRepo.mark_failed(db, job_uuid, error_payload)
        await db.commit()
        job = await get_job_or_404(db, job_uuid)
        await deliver_callback(job)

    await _with_db(run)


@celery_app.task(name="jobs.cleanup_expired")
def cleanup_expired_jobs_task() -> dict[str, Any]:
    """定期清理过期的 Job 记录（expires_at <= now()），由 Celery Beat 每天凌晨 2 点调度。"""
    async def run(db):
        deleted_count = await JobRepo.cleanup_expired_jobs(db)
        await db.commit()
        return {"deleted_count": deleted_count}

    result = asyncio.run(_with_db(run))
    deleted_count = result.get("deleted_count", 0)
    logger.info("cleanup_expired_jobs_completed deleted_count=%d", deleted_count)
    return {"deleted_count": deleted_count, "status": "success"}
