import re
from dataclasses import dataclass
from typing import Any

import yaml

from app.core.config import settings
from app.core.exceptions import ValidationAppError
from app.core.pricing_registry import require_price, validate_price_matches_model
from app.integrations.ai_adapters.registry import (
    validate_image_generation_adapter,
    validate_model_adapter,
    validate_multimodal_text_generation_adapter,
    validate_text_generation_adapter,
)
from app.schemas.meta import ModelOut, ModelParameterOut, ModelsResponse

KNOWN_MODEL_TYPES = frozenset({"text", "image", "audio", "video"})
KNOWN_MODEL_CAPABILITIES = frozenset(
    {"text_generation", "multimodal_text_generation", "image_generation", "image_edit", "tts", "video_generation"}
)
MEDIA_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
KNOWN_MODEL_PARAMETER_TYPES = frozenset({"string", "integer", "number", "boolean", "select"})
MODEL_CONFIG_FIELDS = frozenset(
    {
        "id",
        "adapter",
        "provider",
        "provider_model",
        "adapter_model",
        "pricing_ref",
        "enabled",
        "requires_env",
        "generation",
        "public",
    }
)
MODEL_PUBLIC_FIELDS = frozenset(
    {
        "name",
        "provider",
        "model_type",
        "capabilities",
        "input_media_types",
        "output_media_types",
        "limits",
        "features",
        "parameters",
        "notes",
    }
)
MODEL_PARAMETER_FIELDS = frozenset({"name", "label", "type", "required", "default", "options", "min", "max"})


ModelParameterValue = str | int | float | bool
ModelMetadataValue = str | int | float | bool


@dataclass(frozen=True)
class ModelParameter:
    name: str
    label: str
    type: str
    required: bool
    default: ModelParameterValue
    options: tuple[ModelParameterValue, ...] | None
    min: int | float | None
    max: int | float | None


@dataclass(frozen=True)
class ModelCatalogEntry:
    id: str
    adapter: str
    provider: str
    provider_model: str
    adapter_model: str
    pricing_ref: str
    enabled: bool
    public_name: str
    public_provider: str
    model_type: str
    capabilities: tuple[str, ...]
    input_media_types: tuple[str, ...]
    output_media_types: tuple[str, ...]
    limits: dict[str, ModelMetadataValue]
    features: dict[str, ModelMetadataValue]
    parameters: tuple[ModelParameter, ...]
    notes: str
    requires_env: tuple[str, ...]
    temperature: float | None
    num_retries: int | None
    drop_params: bool | None


TextModel = ModelCatalogEntry


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


def _model_type(config: dict[str, Any], model_id: str) -> str:
    value = _required_str(config, "model_type", model_id)
    if value not in KNOWN_MODEL_TYPES:
        raise RuntimeError(f"model {model_id} model_type contains unknown value: {value}")
    return value


def _requires_env(config: dict[str, Any], model_id: str) -> tuple[str, ...]:
    value = config.get("requires_env")
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise RuntimeError(f"model {model_id} requires requires_env as a list of strings")
    return tuple(item.strip() for item in value)


def _parameter_value(value: Any, model_id: str, parameter_name: str, key: str) -> ModelParameterValue:
    if isinstance(value, str):
        if not value.strip():
            raise RuntimeError(f"model {model_id} parameter {parameter_name} requires non-empty {key}")
        return value.strip()
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    raise RuntimeError(f"model {model_id} parameter {parameter_name} requires scalar {key}")


def _number_value(value: Any, model_id: str, parameter_name: str, key: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RuntimeError(f"model {model_id} parameter {parameter_name} requires numeric {key}")
    return value


def _parameter_value_identity(value: ModelParameterValue) -> tuple[type, ModelParameterValue]:
    return (type(value), value)


def _metadata_value(value: Any, model_id: str, key: str, item_key: str) -> ModelMetadataValue:
    if isinstance(value, str):
        if not value.strip():
            raise RuntimeError(f"model {model_id} {key}.{item_key} must be non-empty")
        return value.strip()
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        if isinstance(value, bool):
            raise RuntimeError(f"model {model_id} {key}.{item_key} must be scalar")
        return value
    raise RuntimeError(f"model {model_id} {key}.{item_key} must be scalar")


def _metadata(config: dict[str, Any], key: str, model_id: str) -> dict[str, ModelMetadataValue]:
    value = config.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"model {model_id} requires {key} as a YAML object")
    parsed: dict[str, ModelMetadataValue] = {}
    for item_key, item_value in value.items():
        if not isinstance(item_key, str) or not item_key.strip():
            raise RuntimeError(f"model {model_id} {key} keys must be non-empty strings")
        normalized_key = item_key.strip()
        if normalized_key in parsed:
            raise RuntimeError(f"model {model_id} has duplicate {key} key: {normalized_key}")
        parsed[normalized_key] = _metadata_value(item_value, model_id, key, normalized_key)
    return parsed


def _text_context_window(limits: dict[str, ModelMetadataValue], model_id: str) -> int:
    value = limits.get("context_window")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RuntimeError(f"model {model_id} requires positive integer limits.context_window")
    return value


def _text_supports_json_output(features: dict[str, ModelMetadataValue], model_id: str) -> bool:
    value = features.get("supports_json_output")
    if not isinstance(value, bool):
        raise RuntimeError(f"model {model_id} requires boolean features.supports_json_output")
    return value


def _validate_parameter_default(parameter: ModelParameter, model_id: str) -> None:
    default = parameter.default
    parameter_name = parameter.name
    if parameter.type == "string":
        if not isinstance(default, str):
            raise RuntimeError(f"model {model_id} parameter {parameter_name} default must be a string")
    elif parameter.type == "integer":
        if isinstance(default, bool) or not isinstance(default, int):
            raise RuntimeError(f"model {model_id} parameter {parameter_name} default must be an integer")
    elif parameter.type == "number":
        if isinstance(default, bool) or not isinstance(default, int | float):
            raise RuntimeError(f"model {model_id} parameter {parameter_name} default must be a number")
    elif parameter.type == "boolean":
        if not isinstance(default, bool):
            raise RuntimeError(f"model {model_id} parameter {parameter_name} default must be a boolean")
    elif parameter.type == "select":
        if not parameter.options:
            raise RuntimeError(f"model {model_id} parameter {parameter_name} requires non-empty options")
        option_identities = {_parameter_value_identity(option) for option in parameter.options}
        if _parameter_value_identity(default) not in option_identities:
            raise RuntimeError(f"model {model_id} parameter {parameter_name} default must be one of options")

    if parameter.options is not None and parameter.type != "select":
        raise RuntimeError(f"model {model_id} parameter {parameter_name} options are only allowed for select")
    if parameter.type == "select" and (parameter.min is not None or parameter.max is not None):
        raise RuntimeError(f"model {model_id} parameter {parameter_name} min/max are not allowed for select")
    if parameter.type in {"string", "boolean"} and (parameter.min is not None or parameter.max is not None):
        raise RuntimeError(f"model {model_id} parameter {parameter_name} min/max are only allowed for integer or number")
    if parameter.min is not None and parameter.max is not None and parameter.min > parameter.max:
        raise RuntimeError(f"model {model_id} parameter {parameter_name} min cannot exceed max")
    if parameter.type in {"integer", "number"}:
        if parameter.type == "integer":
            for key, value in (("min", parameter.min), ("max", parameter.max)):
                if value is not None and not isinstance(value, int):
                    raise RuntimeError(f"model {model_id} parameter {parameter_name} {key} must be an integer")
        if parameter.min is not None and default < parameter.min:
            raise RuntimeError(f"model {model_id} parameter {parameter_name} default is below min")
        if parameter.max is not None and default > parameter.max:
            raise RuntimeError(f"model {model_id} parameter {parameter_name} default is above max")


def _parse_parameter(config: dict[str, Any], model_id: str) -> ModelParameter:
    parameter_name = config.get("name")
    if not isinstance(parameter_name, str) or not parameter_name.strip():
        raise RuntimeError(f"model {model_id} parameter requires string field: name")
    parameter_name = parameter_name.strip()

    unknown = sorted(set(config) - MODEL_PARAMETER_FIELDS)
    if unknown:
        raise RuntimeError(f"model {model_id} parameter {parameter_name} contains unknown fields: {unknown}")

    parameter_type = config.get("type")
    if not isinstance(parameter_type, str) or parameter_type not in KNOWN_MODEL_PARAMETER_TYPES:
        raise RuntimeError(f"model {model_id} parameter {parameter_name} requires known type")

    label = config.get("label")
    if not isinstance(label, str) or not label.strip():
        raise RuntimeError(f"model {model_id} parameter {parameter_name} requires string field: label")

    required = config.get("required")
    if not isinstance(required, bool):
        raise RuntimeError(f"model {model_id} parameter {parameter_name} requires boolean field: required")

    if "default" not in config:
        raise RuntimeError(f"model {model_id} parameter {parameter_name} requires field: default")
    default = _parameter_value(config["default"], model_id, parameter_name, "default")

    options = config.get("options")
    parsed_options: tuple[ModelParameterValue, ...] | None = None
    if options is not None:
        if not isinstance(options, list) or not options:
            raise RuntimeError(f"model {model_id} parameter {parameter_name} requires options as a non-empty list")
        option_values: list[ModelParameterValue] = []
        seen_options: set[tuple[type, ModelParameterValue]] = set()
        for option in options:
            option_value = _parameter_value(option, model_id, parameter_name, "option")
            option_identity = _parameter_value_identity(option_value)
            if option_identity in seen_options:
                raise RuntimeError(f"model {model_id} parameter {parameter_name} has duplicate option: {option_value}")
            seen_options.add(option_identity)
            option_values.append(option_value)
        parsed_options = tuple(option_values)

    parameter = ModelParameter(
        name=parameter_name,
        label=label.strip(),
        type=parameter_type,
        required=required,
        default=default,
        options=parsed_options,
        min=_number_value(config["min"], model_id, parameter_name, "min") if "min" in config else None,
        max=_number_value(config["max"], model_id, parameter_name, "max") if "max" in config else None,
    )
    _validate_parameter_default(parameter, model_id)
    return parameter


def _parameters(config: dict[str, Any], model_id: str) -> tuple[ModelParameter, ...]:
    value = config.get("parameters")
    if not isinstance(value, list):
        raise RuntimeError(f"model {model_id} requires public.parameters as a YAML list")
    parameters: list[ModelParameter] = []
    seen_names: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise RuntimeError(f"model {model_id} public.parameters items must be YAML objects")
        parameter = _parse_parameter(item, model_id)
        if parameter.name in seen_names:
            raise RuntimeError(f"model {model_id} has duplicate parameter: {parameter.name}")
        seen_names.add(parameter.name)
        parameters.append(parameter)
    return tuple(parameters)


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


def _generation_config(config: dict[str, Any], model_id: str, model_type: str) -> tuple[float | None, int | None, bool | None]:
    generation = config.get("generation")
    if model_type != "text":
        if generation is not None:
            raise RuntimeError(f"model {model_id} generation is only supported for text models")
        return None, None, None
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


def _parse_model(config: dict[str, Any]) -> ModelCatalogEntry:
    model_id = config.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise RuntimeError("model config item requires string field: id")
    model_id = model_id.strip()
    unknown = sorted(set(config) - MODEL_CONFIG_FIELDS)
    if unknown:
        raise RuntimeError(f"model {model_id} contains unknown top-level fields: {unknown}")
    public = config.get("public")
    if not isinstance(public, dict):
        raise RuntimeError(f"model {model_id} requires public projection")
    unknown_public = sorted(set(public) - MODEL_PUBLIC_FIELDS)
    if unknown_public:
        raise RuntimeError(f"model {model_id} public projection contains unknown fields: {unknown_public}")
    model_type = _model_type(public, model_id)
    limits = _metadata(public, "limits", model_id)
    features = _metadata(public, "features", model_id)
    temperature, num_retries, drop_params = _generation_config(config, model_id, model_type)
    provider = _required_str(config, "provider", model_id)
    capabilities = _capabilities(public, model_id)
    output_media_types = _media_types(public, "output_media_types", model_id)
    if model_type == "text":
        _text_context_window(limits, model_id)
        _text_supports_json_output(features, model_id)
    return ModelCatalogEntry(
        id=model_id,
        adapter=_required_str(config, "adapter", model_id),
        provider=provider,
        provider_model=_required_str(config, "provider_model", model_id),
        adapter_model=_required_str(config, "adapter_model", model_id),
        pricing_ref=_required_str(config, "pricing_ref", model_id),
        enabled=_required_bool(config, "enabled", model_id),
        public_name=_required_str(public, "name", model_id),
        public_provider=_required_str(public, "provider", model_id),
        model_type=model_type,
        capabilities=capabilities,
        input_media_types=_media_types(public, "input_media_types", model_id),
        output_media_types=output_media_types,
        limits=limits,
        features=features,
        parameters=_parameters(public, model_id),
        notes=_required_str(public, "notes", model_id, allow_empty=True),
        requires_env=_requires_env(config, model_id),
        temperature=temperature,
        num_retries=num_retries,
        drop_params=drop_params,
    )


def _models() -> list[ModelCatalogEntry]:
    config = _load_model_config()
    models_config = config.get("models")
    if not isinstance(models_config, list):
        raise RuntimeError("model config models must be a YAML list")
    models: list[ModelCatalogEntry] = []
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


def _model_out(model: ModelCatalogEntry) -> ModelOut:
    return ModelOut(
        id=model.id,
        name=model.public_name,
        model_type=model.model_type,
        provider=model.public_provider,
        enabled=model.enabled,
        capabilities=list(model.capabilities),
        input_media_types=list(model.input_media_types),
        output_media_types=list(model.output_media_types),
        limits=model.limits,
        features=model.features,
        parameters=[
            ModelParameterOut(
                name=parameter.name,
                label=parameter.label,
                type=parameter.type,
                required=parameter.required,
                default=parameter.default,
                options=list(parameter.options) if parameter.options is not None else None,
                min=parameter.min,
                max=parameter.max,
            )
            for parameter in model.parameters
        ],
        notes=model.notes,
    )


def _job_type_exists(job_type: str) -> bool:
    from app.jobs import registry as job_registry

    return job_type in set(job_registry.all_job_types())


def _job_scoped_models(models: list[ModelCatalogEntry], job_type: str) -> tuple[str, list[ModelCatalogEntry]]:
    from app.jobs import model_selection

    normalized_job_type = job_type.strip()
    if not normalized_job_type:
        raise ValidationAppError("INVALID_JOB_TYPE", "job_type must be a non-empty string")
    if not _job_type_exists(normalized_job_type):
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 job_type: {normalized_job_type}")
    if not model_selection.has_model_selection_config(normalized_job_type):
        return settings.registry.default_model_id, models

    selection = model_selection.get_public_model_selection(normalized_job_type)
    model_by_id = {model.id: model for model in models}
    selected_models = [model_by_id[model_id] for model_id in selection.allowed_model_ids if model_id in model_by_id]
    return selection.default_model_id, selected_models


def list_models_response(job_type: str | None = None) -> ModelsResponse:
    models = [model for model in _models() if _model_is_available(model)]
    default = settings.registry.default_model_id
    if job_type is not None:
        default, models = _job_scoped_models(models, job_type)
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
        models=[_model_out(model) for model in models],
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
        validate_model_adapter(model.adapter)
        if "text_generation" in model.capabilities:
            validate_text_generation_adapter(model.adapter)
        if "multimodal_text_generation" in model.capabilities:
            validate_multimodal_text_generation_adapter(model.adapter)
        if "image_generation" in model.capabilities or "image_edit" in model.capabilities:
            validate_image_generation_adapter(model.adapter)
        validate_price_matches_model(
            pricing_ref=model.pricing_ref,
            model_id=model.id,
            provider=model.provider,
            provider_model=model.provider_model,
        )
    _validate_job_model_selection_configs(enabled)


def _validate_job_model_selection_configs(enabled_models: list[ModelCatalogEntry]) -> None:
    from app.jobs import model_selection
    from app.jobs import registry as job_registry

    known_job_types = set(job_registry.all_job_types())
    poster_job_type = model_selection.POSTER_TITLE_IMAGE_JOB_TYPE
    if poster_job_type in known_job_types and not model_selection.has_model_selection_config(poster_job_type):
        raise RuntimeError("poster_title_image requires app/jobs/types/poster_title_image/models.yaml")
    model_by_id = {model.id: model for model in enabled_models}
    for path in sorted(model_selection.JOB_MODEL_CONFIG_ROOT.glob(f"*/{model_selection.JOB_MODEL_CONFIG_FILENAME}")):
        job_type = path.parent.name
        if known_job_types and job_type not in known_job_types:
            raise RuntimeError(f"job model selection config references unknown job_type: {job_type}")
        selection = model_selection.get_public_model_selection(job_type)
        missing_model_ids = sorted(set(selection.allowed_model_ids) - set(model_by_id))
        if missing_model_ids:
            raise RuntimeError(
                f"job_type {job_type} public_model_selection references non-enabled models: {missing_model_ids}"
            )

    if model_selection.has_model_selection_config(model_selection.POSTER_TITLE_IMAGE_JOB_TYPE):
        from app.integrations.image import POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES

        poster_selection = model_selection.get_poster_title_image_model_selection()
        validate_image_generation_adapter(poster_selection.image_generation_adapter)
        for model_id in poster_selection.public_model_selection.allowed_model_ids:
            generation_model = model_by_id[model_id]
            if poster_selection.image_generation_adapter in {"openai_responses", "openai_images"} and generation_model.provider != "openai":
                raise RuntimeError("poster_title_image generation.image_adapter requires OpenAI image models")
            generation_price = require_price(generation_model.pricing_ref)
            if (
                generation_price.pricing_type == "per_image_token"
                and poster_selection.image_generation_adapter != "openai_images"
            ):
                raise RuntimeError("poster_title_image per_image_token pricing requires openai_images image_adapter")
            if generation_model.model_type != "image":
                raise RuntimeError("poster_title_image public_model_selection must reference image models")
            if "image_edit" not in generation_model.capabilities:
                raise RuntimeError("poster_title_image public_model_selection must support image_edit")
            missing_generation_media_types = sorted(
                POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES - set(generation_model.input_media_types)
            )
            if missing_generation_media_types:
                raise RuntimeError(
                    "poster_title_image public_model_selection must support reference image input media types: "
                    + ", ".join(missing_generation_media_types)
                )
        style_probe_model = next(
            (model for model in enabled_models if model.id == poster_selection.style_probe_model_id),
            None,
        )
        if style_probe_model is None:
            raise RuntimeError(
                "poster_title_image internal_models.style_probe.model_id must reference an enabled model"
            )
        if style_probe_model.model_type != "text":
            raise RuntimeError("poster_title_image internal_models.style_probe.model_id must reference a text model")
        if "multimodal_text_generation" not in style_probe_model.capabilities:
            raise RuntimeError(
                "poster_title_image internal_models.style_probe.model_id must support multimodal_text_generation"
            )
        missing_style_probe_media_types = sorted(
            POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES - set(style_probe_model.input_media_types)
        )
        if missing_style_probe_media_types:
            raise RuntimeError(
                "poster_title_image internal_models.style_probe.model_id must support reference image input media types: "
                + ", ".join(missing_style_probe_media_types)
            )
        if (
            poster_selection.image_generation_adapter == "openai_responses"
            and style_probe_model.features.get("supports_image_generation_tool") is not True
        ):
            raise RuntimeError("poster_title_image internal_models.style_probe.model_id must support image_generation tool")
