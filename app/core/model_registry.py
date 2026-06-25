import re
from dataclasses import dataclass
from typing import Any

import yaml

from app.core.config import settings
from app.core.pricing_registry import validate_price_matches_model
from app.schemas.meta import ModelOut, ModelsResponse

KNOWN_MODEL_CAPABILITIES = frozenset({"text_generation", "image_generation", "tts", "video_generation"})
MEDIA_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


@dataclass(frozen=True)
class TextModel:
    id: str
    name: str
    provider: str
    provider_model: str
    litellm_model: str
    pricing_ref: str
    enabled: bool
    capabilities: tuple[str, ...]
    input_media_types: tuple[str, ...]
    output_media_types: tuple[str, ...]
    context_window: int
    supports_json_output: bool
    notes: str
    requires_env: tuple[str, ...]
    temperature: float
    num_retries: int
    drop_params: bool


def _load_model_config() -> dict[str, Any]:
    try:
        raw = settings.registry.model_config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"model config not found: {settings.registry.model_config_path}") from exc
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise RuntimeError("model config must be a YAML object")
    return data


def _required_str(config: dict[str, Any], key: str, model_id: str, *, allow_empty: bool = False) -> str:
    value = config.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise RuntimeError(f"model {model_id} requires string field: {key}")
    return value.strip()


def _required_bool(config: dict[str, Any], key: str, model_id: str) -> bool:
    value = config.get(key)
    if not isinstance(value, bool):
        raise RuntimeError(f"model {model_id} requires boolean field: {key}")
    return value


def _required_positive_int(config: dict[str, Any], key: str, model_id: str) -> int:
    value = config.get(key)
    if not isinstance(value, int) or value <= 0:
        raise RuntimeError(f"model {model_id} requires positive integer field: {key}")
    return value


def _requires_env(config: dict[str, Any], model_id: str) -> tuple[str, ...]:
    value = config.get("requires_env")
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise RuntimeError(f"model {model_id} requires requires_env as a list of strings")
    return tuple(item.strip() for item in value)


def _required_str_tuple(config: dict[str, Any], key: str, model_id: str) -> tuple[str, ...]:
    value = config.get(key)
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"model {model_id} requires {key} as a non-empty list of strings")
    values: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(f"model {model_id} requires {key} as a non-empty list of strings")
        normalized = item.strip()
        if normalized in seen:
            raise RuntimeError(f"model {model_id} has duplicate {key}: {normalized}")
        seen.add(normalized)
        values.append(normalized)
    return tuple(values)


def _capabilities(config: dict[str, Any], model_id: str) -> tuple[str, ...]:
    capabilities = _required_str_tuple(config, "capabilities", model_id)
    unknown = sorted(set(capabilities) - KNOWN_MODEL_CAPABILITIES)
    if unknown:
        raise RuntimeError(f"model {model_id} capabilities contains unknown values: {unknown}")
    return capabilities


def _media_types(config: dict[str, Any], key: str, model_id: str) -> tuple[str, ...]:
    media_types = _required_str_tuple(config, key, model_id)
    invalid = [media_type for media_type in media_types if not MEDIA_TYPE_PATTERN.fullmatch(media_type)]
    if invalid:
        raise RuntimeError(f"model {model_id} {key} contains invalid media types: {invalid}")
    return media_types


def _env_value(name: str) -> str:
    return settings.application_env_value(name)


def _model_is_available(model: TextModel) -> bool:
    return model.enabled and all(_env_value(name) for name in model.requires_env)


def _generation_config(config: dict[str, Any], model_id: str) -> tuple[float, int, bool]:
    generation = config.get("generation")
    if not isinstance(generation, dict):
        raise RuntimeError(f"model {model_id} requires generation config")

    temperature = generation.get("temperature")
    if isinstance(temperature, bool) or not isinstance(temperature, int | float):
        raise RuntimeError(f"model {model_id} requires numeric generation.temperature")

    num_retries = generation.get("num_retries")
    if not isinstance(num_retries, int) or num_retries < 0:
        raise RuntimeError(f"model {model_id} requires non-negative integer generation.num_retries")

    drop_params = generation.get("drop_params")
    if not isinstance(drop_params, bool):
        raise RuntimeError(f"model {model_id} requires boolean generation.drop_params")

    return float(temperature), num_retries, drop_params


def _provider_model(provider: str, litellm_model: str, model_id: str) -> str:
    prefix = f"{provider}/"
    if litellm_model.startswith(prefix) and len(litellm_model) > len(prefix):
        return litellm_model[len(prefix):]
    raise RuntimeError(f"model {model_id} litellm_model must start with provider prefix: {prefix}")


def _parse_model(config: dict[str, Any]) -> TextModel:
    model_id = config.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise RuntimeError("model config item requires string field: id")
    model_id = model_id.strip()
    temperature, num_retries, drop_params = _generation_config(config, model_id)
    provider = _required_str(config, "provider", model_id)
    litellm_model = _required_str(config, "litellm_model", model_id)
    capabilities = _capabilities(config, model_id)
    return TextModel(
        id=model_id,
        name=_required_str(config, "name", model_id),
        provider=provider,
        provider_model=_provider_model(provider, litellm_model, model_id),
        litellm_model=litellm_model,
        pricing_ref=_required_str(config, "pricing_ref", model_id),
        enabled=_required_bool(config, "enabled", model_id),
        capabilities=capabilities,
        input_media_types=_media_types(config, "input_media_types", model_id),
        output_media_types=_media_types(config, "output_media_types", model_id),
        context_window=_required_positive_int(config, "context_window", model_id),
        supports_json_output=_required_bool(config, "supports_json_output", model_id),
        notes=_required_str(config, "notes", model_id, allow_empty=True),
        requires_env=_requires_env(config, model_id),
        temperature=temperature,
        num_retries=num_retries,
        drop_params=drop_params,
    )


def _models() -> list[TextModel]:
    config = _load_model_config()
    models_config = config.get("models")
    if not isinstance(models_config, list):
        raise RuntimeError("model config models must be a YAML list")
    models: list[TextModel] = []
    seen_ids: set[str] = set()
    for item in models_config:
        if not isinstance(item, dict):
            raise RuntimeError("model config models items must be YAML objects")
        model = _parse_model(item)
        if model.id in seen_ids:
            raise RuntimeError(f"duplicate model id: {model.id}")
        seen_ids.add(model.id)
        models.append(model)
    return models


def list_models_response() -> ModelsResponse:
    models = [model for model in _models() if _model_is_available(model)]
    default = settings.registry.default_model_id
    billing_capability = (
        {
            "billing_enabled": settings.billing.enabled,
            "cost_estimate_available": settings.billing.enabled,
        }
        if settings.billing.model_catalog_expose_billing_capability
        else {}
    )
    return ModelsResponse(
        default_model_id=default,
        models=[
            ModelOut(
                id=m.id,
                name=m.name,
                provider=m.provider,
                enabled=m.enabled,
                capabilities=list(m.capabilities),
                input_media_types=list(m.input_media_types),
                output_media_types=list(m.output_media_types),
                context_window=m.context_window,
                supports_json_output=m.supports_json_output,
                notes=m.notes,
            )
            for m in models
        ],
        **billing_capability,
    )


def get_enabled_model(model_id: str) -> TextModel | None:
    return next((model for model in _models() if model.id == model_id and _model_is_available(model)), None)


def validate_model_catalog() -> None:
    models = _models()
    default = settings.registry.default_model_id
    enabled = [model for model in models if model.enabled]
    if not any(model.id == default for model in enabled):
        raise RuntimeError(f"DEFAULT_MODEL_ID must reference an enabled model: {default}")
    seen: set[str] = set()
    for model in enabled:
        if model.id in seen:
            raise RuntimeError(f"duplicate model id: {model.id}")
        seen.add(model.id)
        validate_price_matches_model(
            pricing_ref=model.pricing_ref,
            model_id=model.id,
            provider=model.provider,
            provider_model=model.provider_model,
        )
