import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.job_repo import JobRepo
from app.schemas.jobs import CreateJobRequest, CreateJobResponse
from app.services.jobs import create_job, create_job_response
from app.tasks.jobs import dispatch_job_task


async def submit_ai_job(
    db: AsyncSession,
    payload: CreateJobRequest,
    caller_id: str,
) -> CreateJobResponse:
    job, created = await create_job(db, payload, caller_id)
    task_id = str(uuid.uuid4()) if created else None
    if task_id:
        await JobRepo.set_celery_task_id(db, job.id, task_id)
    await db.commit()
    if task_id:
        await db.refresh(job)
        dispatch_job_task.apply_async(args=[str(job.id)], task_id=task_id)
        await JobRepo.mark_celery_published(db, job.id, task_id)
        await db.commit()
    return CreateJobResponse.model_validate(create_job_response(job))
