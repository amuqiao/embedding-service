import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operations import OperationID
from app.core.database import get_db
from app.core.security import require_service_auth
from app.schemas.billing import JobBillingResponseData
from app.schemas.jobs import CreateJobRequest, JobResponseData
from app.services.billing import get_job_billing
from app.services.jobs import get_job_response, submit_job_request

router = APIRouter(tags=["jobs"])


@router.post(
    "/jobs",
    response_model=JobResponseData,
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
    return JobResponseData(job=job)


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponseData,
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
    return JobResponseData(job=job)


@router.get(
    "/jobs/{job_id}/billing",
    response_model=JobBillingResponseData,
    operation_id=OperationID.GET_JOB_BILLING,
)
async def get_ai_job_billing(
    request: Request,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
):
    billing = await get_job_billing(
        db,
        job_id,
        caller_id,
        request_id=request.state.request_id,
    )
    return JobBillingResponseData(billing=billing)
