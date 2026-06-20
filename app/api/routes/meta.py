from fastapi import APIRouter, Depends, Request

from app.core.security import require_service_auth
from app.core.model_registry import list_models_response
from app.core.prompt_templates import list_prompt_templates
from app.schemas.envelope import ResponseEnvelope, success_envelope
from app.schemas.meta import ModelsResponse, PromptTemplatesResponse

router = APIRouter(tags=["meta"], dependencies=[Depends(require_service_auth)])


@router.get("/models", response_model=ResponseEnvelope[ModelsResponse])
async def list_models(request: Request):
    return success_envelope(list_models_response(), request)


@router.get("/prompt-templates", response_model=ResponseEnvelope[PromptTemplatesResponse])
async def prompt_templates(request: Request):
    return success_envelope(list_prompt_templates(), request)
