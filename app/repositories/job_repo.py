import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import AIJob


class JobRepo:
    @staticmethod
    async def advisory_lock_for_client_request(db: AsyncSession, caller_id: str, client_request_id: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"{caller_id}:{client_request_id}"},
        )

    @staticmethod
    async def get_recent_by_client_request(
        db: AsyncSession,
        *,
        caller_id: str,
        client_request_id: str,
    ) -> AIJob | None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = await db.execute(
            select(AIJob)
            .where(
                AIJob.caller_id == caller_id,
                AIJob.client_request_id == client_request_id,
                AIJob.created_at >= since,
            )
            .order_by(AIJob.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        caller_id: str,
        client_request_id: str | None,
        job_type: str,
        model_id: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        callback_payload: dict[str, Any],
        prompt_payload: dict[str, Any],
        metadata_payload: dict[str, Any] | None,
    ) -> AIJob:
        job = AIJob(
            caller_id=caller_id,
            client_request_id=client_request_id,
            job_type=job_type,
            model_id=model_id,
            status="queued",
            progress_percent=0,
            progress_text="已排队",
            input_payload=input_payload,
            output_payload=output_payload,
            callback_payload=callback_payload,
            prompt_payload=prompt_payload,
            metadata_payload=metadata_payload,
        )
        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    @staticmethod
    async def get(db: AsyncSession, job_id: uuid.UUID) -> AIJob | None:
        result = await db.execute(select(AIJob).where(AIJob.id == job_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def set_celery_task_id(db: AsyncSession, job_id: uuid.UUID, celery_task_id: str) -> None:
        job = await JobRepo.get(db, job_id)
        if job:
            job.celery_task_id = celery_task_id
            job.updated_at = datetime.now(timezone.utc)
            await db.flush()

    @staticmethod
    async def mark_running(db: AsyncSession, job_id: uuid.UUID, progress_text: str = "正在处理文本") -> None:
        job = await JobRepo.get(db, job_id)
        if job:
            job.status = "running"
            job.progress_percent = max(job.progress_percent or 0, 5)
            job.progress_text = progress_text
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            await db.flush()

    @staticmethod
    async def update_progress(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        progress_percent: int,
        progress_text: str,
    ) -> None:
        job = await JobRepo.get(db, job_id)
        if job:
            job.progress_percent = max(0, min(100, progress_percent))
            job.progress_text = progress_text
            job.updated_at = datetime.now(timezone.utc)
            await db.flush()

    @staticmethod
    async def mark_succeeded(db: AsyncSession, job_id: uuid.UUID, result_payload: dict[str, Any]) -> None:
        job = await JobRepo.get(db, job_id)
        if job:
            now = datetime.now(timezone.utc)
            job.status = "succeeded"
            job.progress_percent = 100
            job.progress_text = "已完成"
            job.result_payload = result_payload
            job.error_payload = None
            job.finished_at = now
            job.updated_at = now
            await db.flush()

    @staticmethod
    async def mark_failed(db: AsyncSession, job_id: uuid.UUID, error_payload: dict[str, Any]) -> None:
        job = await JobRepo.get(db, job_id)
        if job:
            now = datetime.now(timezone.utc)
            job.status = "failed"
            job.progress_text = "处理失败"
            job.error_payload = error_payload
            job.finished_at = now
            job.updated_at = now
            await db.flush()
