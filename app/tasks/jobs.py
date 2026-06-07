import asyncio
import uuid
from typing import Any

from billiard.exceptions import SoftTimeLimitExceeded
from celery import chord, group
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.infrastructure.config import settings
from app.repositories.job_repo import JobRepo
from app.services.job_workflow import build_canvas, execute_work_item, fail_job, finalize_job, plan_job
from app.tasks.celery_app import celery_app


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
        return asyncio.run(_dispatch(job_id))
    except SoftTimeLimitExceeded as exc:
        if self.request.retries >= settings.CELERY_MAX_RETRIES:
            asyncio.run(_mark_timeout(job_id))
            raise
        raise self.retry(exc=exc, countdown=settings.CELERY_RETRY_DELAY, max_retries=settings.CELERY_MAX_RETRIES)


async def _dispatch(job_id: str) -> dict[str, Any]:
    job_uuid = uuid.UUID(job_id)

    async def run(db):
        return await plan_job(db, job_uuid)

    job, plan, item_ids = await _with_db(run)
    canvas = build_canvas(job.id, plan, item_ids)
    result = canvas.apply_async()

    async def store_root_task(db):
        await JobRepo.set_celery_task_id(db, job.id, result.id)
        await db.commit()

    await _with_db(store_root_task)
    return {"job_id": job_id, "execution_mode": plan.execution_mode, "root_task_id": result.id}


@celery_app.task(name="jobs.execute_work_item", bind=True, acks_late=True)
def execute_work_item_task(self, job_id: str, work_item_id: str):
    try:
        return asyncio.run(_execute(job_id, work_item_id, self.request.id))
    except Exception as exc:
        asyncio.run(_fail(job_id, work_item_id, exc))
        raise


async def _execute(job_id: str, work_item_id: str, celery_task_id: str | None):
    async def run(db):
        return await execute_work_item(
            db,
            job_id=uuid.UUID(job_id),
            item_id=uuid.UUID(work_item_id),
            celery_task_id=celery_task_id,
        )

    return await _with_db(run)


@celery_app.task(name="jobs.fanout_after_mapping", bind=True)
def fanout_after_mapping_task(self, previous_result: dict, job_id: str, chunk_item_ids: list[str]):
    chunk_tasks = [
        execute_work_item_task.s(job_id, work_item_id)
        for work_item_id in chunk_item_ids
    ]
    result = chord(group(chunk_tasks), finalize_job_task.s(job_id)).apply_async()
    return {"job_id": job_id, "mapping": previous_result, "root_task_id": result.id}


@celery_app.task(name="jobs.finalize", bind=True)
def finalize_job_task(self, previous_result, job_id: str):
    try:
        return asyncio.run(_finalize(job_id))
    except Exception as exc:
        asyncio.run(_fail(job_id, None, exc))
        raise


async def _finalize(job_id: str):
    async def run(db):
        return await finalize_job(db, uuid.UUID(job_id))

    return await _with_db(run)


async def _fail(job_id: str, work_item_id: str | None, exc: Exception) -> None:
    async def run(db):
        await fail_job(
            db,
            job_id=uuid.UUID(job_id),
            item_id=uuid.UUID(work_item_id) if work_item_id else None,
            error_payload={
                "code": "MODEL_CALL_FAILED",
                "message": "模型调用失败或内部处理失败",
                "details": {"type": type(exc).__name__, "message": str(exc)[:500]},
            },
        )

    await _with_db(run)


async def _mark_timeout(job_id: str) -> None:
    async def run(db):
        await fail_job(
            db,
            job_id=uuid.UUID(job_id),
            item_id=None,
            error_payload={"code": "JOB_TIMEOUT", "message": "任务执行超时", "details": {}},
        )

    await _with_db(run)
