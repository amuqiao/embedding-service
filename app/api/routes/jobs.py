import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_service_auth
from app.infrastructure.database import get_db
from app.repositories.job_repo import JobRepo
from app.schemas.jobs import CreateJobRequest, CreateJobResponse, JobStatusResponse
from app.services.jobs import create_job, create_job_response, get_job_response
from app.tasks.jobs import process_job_task

router = APIRouter(tags=["jobs"])


@router.post("/jobs", response_model=CreateJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_ai_job(
    payload: CreateJobRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
):
    job, created = await create_job(db, payload, caller_id)
    await db.commit()
    if created:
        celery_result = process_job_task.delay(str(job.id))
        async with db.begin():
            await JobRepo.set_celery_task_id(db, job.id, celery_result.id)
        await db.refresh(job)
    response.status_code = status.HTTP_202_ACCEPTED
    return create_job_response(job)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_ai_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
):
    return await get_job_response(db, job_id)
