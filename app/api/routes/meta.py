from fastapi import APIRouter, Depends, Query

from app.api.operations import OperationID
from app.core.security import require_service_auth
from app.core.language_catalog import list_languages_response
from app.core.model_registry import list_models_response
from app.core.prompt_templates import DEFAULT_PROMPT_TEMPLATE_JOB_TYPE, list_prompt_templates
from app.schemas.meta import LanguagesResponse, ModelsResponse, PromptTemplateResponseData

router = APIRouter(tags=["meta"], dependencies=[Depends(require_service_auth)])


@router.get(
    "/models",
    response_model=ModelsResponse,
    response_model_exclude_none=True,
    operation_id=OperationID.LIST_MODELS,
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
    "/languages",
    response_model=LanguagesResponse,
    operation_id=OperationID.LIST_LANGUAGES,
)
async def list_languages():
    return list_languages_response()


@router.get(
    "/prompt-templates",
    response_model=PromptTemplateResponseData,
    operation_id=OperationID.LIST_PROMPT_TEMPLATES,
)
async def prompt_templates(
    job_type: str = Query(
        default=DEFAULT_PROMPT_TEMPLATE_JOB_TYPE,
        min_length=1,
        description="Job type whose prompt template should be returned.",
    )
):
    return list_prompt_templates(job_type=job_type)
