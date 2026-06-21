from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.jobs import CreateJobRequest, CreateJobResponse
from app.services.jobs import create_job, create_job_response


async def submit_ai_job(
    db: AsyncSession,
    payload: CreateJobRequest,
    caller_id: str,
) -> CreateJobResponse:
    job, created = await create_job(db, payload, caller_id)
    await db.commit()
    if created and job.active_attempt_id is not None:
        await db.refresh(job)
        from app.tasks.jobs import publish_job_attempt

        await publish_job_attempt(job.active_attempt_id)
    return CreateJobResponse.model_validate(create_job_response(job))
