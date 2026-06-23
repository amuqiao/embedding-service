from fastapi import APIRouter, Depends

from app.api.operations import OperationID
from app.core.security import require_service_auth
from app.core.model_registry import list_models_response
from app.core.prompt_templates import list_prompt_templates
from app.schemas.meta import ModelsResponse, PromptTemplatesResponse

router = APIRouter(tags=["meta"], dependencies=[Depends(require_service_auth)])


@router.get(
    "/models",
    response_model=ModelsResponse,
    response_model_exclude_none=True,
    operation_id=OperationID.LIST_MODELS,
)
async def list_models():
    return list_models_response()


@router.get(
    "/prompt-templates",
    response_model=PromptTemplatesResponse,
    operation_id=OperationID.LIST_PROMPT_TEMPLATES,
)
async def prompt_templates():
    return list_prompt_templates()
