import asyncio
import logging
import uuid
from typing import Any

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.exceptions import AppError
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
    try:
        return asyncio.run(_process(job_id))
    except SoftTimeLimitExceeded as exc:
        if self.request.retries >= settings.CELERY_MAX_RETRIES:
            asyncio.run(_mark_timeout(job_id))
            raise
        raise self.retry(exc=exc, countdown=settings.CELERY_RETRY_DELAY, max_retries=settings.CELERY_MAX_RETRIES)
    except Exception as exc:
        asyncio.run(_fail(job_id, None, exc))
        raise


async def _process(job_id: str) -> dict[str, Any]:
    job_uuid = uuid.UUID(job_id)

    async def run(db):
        job = await get_job_or_404(db, job_uuid)
        await JobRepo.mark_running(db, job.id)
        await db.commit()

        input_text = _load_input_text(job)
        result = await run_ai_job(job, input_text)
        result_data = _persist_large_artifacts(job, result)

        await JobRepo.mark_succeeded(db, job.id, result_data)
        await db.commit()

        job = await JobRepo.get(db, job.id)
        await deliver_callback(job)
        return {"job_id": job_id, "status": "succeeded"}

    return await _with_db(run)


async def _fail(job_id: str, _work_item_id: str | None, exc: Exception) -> None:
    if isinstance(exc, AppError):
        error_payload = {"code": exc.code, "message": exc.message, "details": exc.details}
    else:
        error_payload = {
            "code": "MODEL_CALL_FAILED",
            "message": "模型调用失败或内部处理失败",
            "details": {"type": type(exc).__name__, "message": str(exc)[:500]},
        }

    async def run(db):
        await JobRepo.mark_failed(db, uuid.UUID(job_id), error_payload)
        await db.commit()

    await _with_db(run)


async def _mark_timeout(job_id: str) -> None:
    async def run(db):
        error_payload = {"code": "JOB_TIMEOUT", "message": "任务执行超时", "details": {}}
        await JobRepo.mark_failed(db, uuid.UUID(job_id), error_payload)
        await db.commit()

    await _with_db(run)


@celery_app.task(name="jobs.cleanup_expired")
def cleanup_expired_jobs_task() -> dict[str, Any]:
    """定期清理过期的 Job 记录（expires_at <= now()）

    此任务由 Celery Beat 定时调度，默认每月执行一次（第一天凌晨 2 点）。

    Returns:
        dict: 包含清理结果统计
            - deleted_count: 删除的 Job 记录数
            - status: 执行状态（'success' 或 'error'）
            - message: 执行信息
    """
    async def run(db):
        deleted_count = await JobRepo.cleanup_expired_jobs(db)
        return {"deleted_count": deleted_count}

    try:
        result = asyncio.run(_with_db(run))
        deleted_count = result.get("deleted_count", 0)
        message = f"Successfully cleaned up {deleted_count} expired jobs"
        logger.info(message)
        return {
            "deleted_count": deleted_count,
            "status": "success",
            "message": message,
        }
    except Exception as exc:
        error_message = f"Failed to cleanup expired jobs: {str(exc)}"
        logger.error(error_message, exc_info=True)
        return {
            "deleted_count": 0,
            "status": "error",
            "message": error_message,
        }
