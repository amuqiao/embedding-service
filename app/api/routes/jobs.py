import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operations import OperationID
from app.core.database import get_db
from app.core.security import require_service_auth
from app.schemas.envelope import ResponseEnvelope, success_envelope
from app.schemas.jobs import CreateJobRequest, JobResponseData
from app.services.jobs import get_job_response, submit_job_request

router = APIRouter(tags=["jobs"])


@router.post(
    "/jobs",
    response_model=ResponseEnvelope[JobResponseData],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id=OperationID.CREATE_AI_JOB,
)
async def create_ai_job(
    request: Request,
    payload: CreateJobRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
):
    job = await submit_job_request(
        db,
        payload,
        caller_id,
        request_id=request.state.request_id,
    )
    return success_envelope(JobResponseData(job=job), request)


@router.get(
    "/jobs/{job_id}",
    response_model=ResponseEnvelope[JobResponseData],
    operation_id=OperationID.GET_AI_JOB,
)
async def get_ai_job(
    request: Request,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
):
    job = await get_job_response(
        db,
        job_id,
        caller_id,
        request_id=request.state.request_id,
    )
    return success_envelope(JobResponseData(job=job), request)
