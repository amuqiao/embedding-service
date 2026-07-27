from fastapi import APIRouter, Depends, Query

from app.api.operations import OperationID, operation_path, operation_route_kwargs
from app.core.security import require_service_auth
from app.core.language_catalog import list_languages_response
from app.core.model_registry import list_models_response
from app.core.prompt_templates import DEFAULT_PROMPT_TEMPLATE_JOB_TYPE, list_prompt_templates

router = APIRouter(tags=["meta"], dependencies=[Depends(require_service_auth)])


@router.get(
    operation_path(OperationID.LIST_MODELS),
    **operation_route_kwargs(OperationID.LIST_MODELS),
)
async def list_models(
    job_type: str | None = Query(
        default=None,
        min_length=1,
        description="Optional job type whose caller-selectable model list should be returned.",
    )
):
    return list_models_response(job_type=job_type)


@router.get(
    operation_path(OperationID.LIST_LANGUAGES),
    **operation_route_kwargs(OperationID.LIST_LANGUAGES),
)
async def list_languages():
    return list_languages_response()


@router.get(
    operation_path(OperationID.LIST_PROMPT_TEMPLATES),
    **operation_route_kwargs(OperationID.LIST_PROMPT_TEMPLATES),
)
async def prompt_templates(
    job_type: str = Query(
        default=DEFAULT_PROMPT_TEMPLATE_JOB_TYPE,
        min_length=1,
        description="Job type whose prompt template should be returned.",
    )
):
    return list_prompt_templates(job_type=job_type)
