from dataclasses import dataclass

from app.infrastructure.config import settings
from app.schemas.meta import ModelOut, ModelsResponse


@dataclass(frozen=True)
class TextModel:
    id: str
    name: str
    provider: str
    litellm_model: str
    enabled: bool
    context_window: int
    supports_json_output: bool
    notes: str


def _models() -> list[TextModel]:
    models: list[TextModel] = []
    if settings.OPENAI_API_KEY:
        models.extend(
            [
                TextModel("gpt-4o", "GPT-4o", "openai", "openai/gpt-4o", True, 128000, True, ""),
                TextModel("gpt-4o-mini", "GPT-4o mini", "openai", "openai/gpt-4o-mini", True, 128000, True, ""),
                TextModel("gpt-4.1", "GPT-4.1", "openai", "openai/gpt-4.1", True, 1047576, True, ""),
            ]
        )
    return models


def list_models_response() -> ModelsResponse:
    models = _models()
    default = settings.DEFAULT_MODEL_ID
    if default not in {m.id for m in models} and models:
        default = models[0].id
    return ModelsResponse(
        default_model_id=default,
        models=[
            ModelOut(
                id=m.id,
                name=m.name,
                provider=m.provider,
                enabled=m.enabled,
                context_window=m.context_window,
                supports_json_output=m.supports_json_output,
                notes=m.notes,
            )
            for m in models
            if m.enabled
        ],
    )


def get_enabled_model(model_id: str) -> TextModel | None:
    return next((model for model in _models() if model.id == model_id and model.enabled), None)
