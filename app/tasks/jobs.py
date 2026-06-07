import asyncio
import uuid

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.infrastructure.config import settings
from app.repositories.job_repo import JobRepo
from app.services.jobs import process_job
from app.tasks.celery_app import celery_app


@celery_app.task(name="jobs.process", bind=True, acks_late=True)
def process_job_task(self, job_id: str):
    try:
        return asyncio.run(_run(job_id))
    except SoftTimeLimitExceeded as exc:
        if self.request.retries >= settings.CELERY_MAX_RETRIES:
            asyncio.run(_mark_timeout(job_id))
            raise
        raise self.retry(exc=exc, countdown=settings.CELERY_RETRY_DELAY, max_retries=settings.CELERY_MAX_RETRIES)


async def _run(job_id: str) -> None:
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            await process_job(db, uuid.UUID(job_id))
    finally:
        await engine.dispose()


async def _mark_timeout(job_id: str) -> None:
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            await JobRepo.mark_failed(
                db,
                uuid.UUID(job_id),
                {"code": "JOB_TIMEOUT", "message": "任务执行超时", "details": {}},
            )
            await db.commit()
    finally:
        await engine.dispose()
