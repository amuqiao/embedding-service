import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.jobs.submission import submit_ai_job
from app.core.security import require_service_auth
from app.core.database import get_db
from app.schemas.jobs import CreateJobRequest, CreateJobResponse, JobStatusResponse
from app.services.jobs import get_job_response

router = APIRouter(tags=["jobs"])


@router.post("/jobs", response_model=CreateJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_ai_job(
    payload: CreateJobRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
):
    return await submit_ai_job(db, payload, caller_id)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_ai_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
):
    return await get_job_response(db, job_id, caller_id)
