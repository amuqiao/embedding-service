from fastapi import APIRouter, Depends

from app.core.security import require_service_auth
from app.infrastructure.model_registry import list_models_response
from app.infrastructure.prompt_templates import list_prompt_templates
from app.schemas.meta import ModelsResponse, PromptTemplatesResponse

router = APIRouter(tags=["meta"], dependencies=[Depends(require_service_auth)])


@router.get("/models", response_model=ModelsResponse)
async def list_models():
    return list_models_response()


@router.get("/prompt-templates", response_model=PromptTemplatesResponse)
async def prompt_templates():
    return list_prompt_templates()
