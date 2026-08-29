import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

from app.ai.capabilities import IMAGE_EDIT, KNOWN_MODEL_CAPABILITIES, KNOWN_MODEL_TYPES, TEXT_GENERATION
from app.ai.pricing.registry import require_price, validate_price_matches_model
from app.ai.providers.registry import validate_provider
from app.ai.adapters.registry import (
    validate_embedding_adapter,
    validate_image_generation_adapter,
    validate_model_adapter,
    validate_multimodal_text_generation_adapter,
    validate_text_generation_adapter,
)
from app.core.config import settings
from app.core.exceptions import ValidationAppError
from app.schemas.meta import ModelOut, ModelParameterOut, ModelsResponse

MEDIA_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
KNOWN_MODEL_PARAMETER_TYPES = frozenset({"string", "integer", "number", "boolean", "select"})
MODEL_CONFIG_FIELDS_V1 = frozenset(
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
MODEL_CONFIG_FIELDS_V2 = frozenset({"id", "enabled", "public", "execution"})
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
MODEL_EXECUTION_FIELDS = frozenset({"routes"})
MODEL_ROUTE_FIELDS = frozenset(
    {
        "adapter",
        "provider",
        "provider_model",
        "adapter_model",
        "pricing_ref",
        "requires_env",
        "generation",
        "embedding",
    }
)
MODEL_DEFAULT_CAPABILITY = TEXT_GENERATION


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
class ModelExecutionRoute:
    capability: str
    adapter: str
    provider: str
    provider_model: str
    adapter_model: str
    pricing_ref: str
    requires_env: tuple[str, ...]
    generation: dict[str, Any] | None
    embedding: dict[str, Any] | None
    config_hash: str


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
    routes: dict[str, ModelExecutionRoute]

    def route_for(self, capability: str) -> ModelExecutionRoute:
        route = self.routes.get(capability)
        if route is None:
            raise ValidationAppError("MODEL_NOT_AVAILABLE", f"模型不支持能力: {self.id}/{capability}")
        return route


TextModel = ModelCatalogEntry


@dataclass(frozen=True)
class PublicModelSlotView:
    default_model_id: str
    allowed_model_ids: tuple[str, ...]
    required_capabilities: tuple[str, ...]


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


def _optional_requires_env(config: dict[str, Any], model_id: str) -> tuple[str, ...]:
    if "requires_env" not in config:
        return ()
    return _requires_env(config, model_id)


def _route_config_hash(config: dict[str, Any]) -> str:
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _parse_route(capability: str, config: dict[str, Any], *, model_id: str) -> ModelExecutionRoute:
    if capability not in KNOWN_MODEL_CAPABILITIES:
        raise RuntimeError(f"model {model_id} execution route contains unknown capability: {capability}")
    unknown = sorted(set(config) - MODEL_ROUTE_FIELDS)
    if unknown:
        raise RuntimeError(f"model {model_id} route {capability} contains unknown fields: {unknown}")
    generation = config.get("generation")
    if generation is not None and not isinstance(generation, dict):
        raise RuntimeError(f"model {model_id} route {capability} generation must be a YAML object")
    embedding = config.get("embedding")
    if embedding is not None and not isinstance(embedding, dict):
        raise RuntimeError(f"model {model_id} route {capability} embedding must be a YAML object")
    return ModelExecutionRoute(
        capability=capability,
        adapter=_required_str(config, "adapter", model_id),
        provider=_required_str(config, "provider", model_id),
        provider_model=_required_str(config, "provider_model", model_id),
        adapter_model=_required_str(config, "adapter_model", model_id),
        pricing_ref=_required_str(config, "pricing_ref", model_id),
        requires_env=_optional_requires_env(config, model_id),
        generation=generation,
        embedding=embedding,
        config_hash=_route_config_hash(config),
    )


def _routes_from_v1(config: dict[str, Any], *, model_id: str, capabilities: tuple[str, ...]) -> dict[str, ModelExecutionRoute]:
    route_config = {
        "adapter": _required_str(config, "adapter", model_id),
        "provider": _required_str(config, "provider", model_id),
        "provider_model": _required_str(config, "provider_model", model_id),
        "adapter_model": _required_str(config, "adapter_model", model_id),
        "pricing_ref": _required_str(config, "pricing_ref", model_id),
        "requires_env": list(_requires_env(config, model_id)),
    }
    generation = config.get("generation")
    if generation is not None:
        route_config["generation"] = generation
    return {
        capability: _parse_route(capability, route_config, model_id=model_id)
        for capability in capabilities
    }


def _routes_from_v2(config: dict[str, Any], *, model_id: str, capabilities: tuple[str, ...]) -> dict[str, ModelExecutionRoute]:
    execution = config.get("execution")
    if not isinstance(execution, dict):
        raise RuntimeError(f"model {model_id} requires execution object")
    unknown = sorted(set(execution) - MODEL_EXECUTION_FIELDS)
    if unknown:
        raise RuntimeError(f"model {model_id} execution contains unknown fields: {unknown}")
    routes_config = execution.get("routes")
    if not isinstance(routes_config, dict) or not routes_config:
        raise RuntimeError(f"model {model_id} requires execution.routes as a non-empty object")
    routes: dict[str, ModelExecutionRoute] = {}
    for capability, route_config in routes_config.items():
        if not isinstance(capability, str) or not capability.strip():
            raise RuntimeError(f"model {model_id} execution.routes keys must be non-empty strings")
        if not isinstance(route_config, dict):
            raise RuntimeError(f"model {model_id} route {capability} must be a YAML object")
        normalized_capability = capability.strip()
        if normalized_capability in routes:
            raise RuntimeError(f"model {model_id} contains duplicate route: {normalized_capability}")
        routes[normalized_capability] = _parse_route(normalized_capability, route_config, model_id=model_id)
    missing = sorted(set(capabilities) - set(routes))
    if missing:
        raise RuntimeError(f"model {model_id} is missing execution routes for capabilities: {missing}")
    extra = sorted(set(routes) - set(capabilities))
    if extra:
        raise RuntimeError(f"model {model_id} declares execution routes not present in public.capabilities: {extra}")
    return routes


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
    return model.enabled and any(route_is_available(route) for route in model.routes.values())


def _generation_values(
    generation: Any,
    *,
    model_id: str,
    route_capability: str,
) -> tuple[float | None, int | None, bool | None]:
    if route_capability != TEXT_GENERATION:
        if generation is not None:
            raise RuntimeError(f"model {model_id} generation is only supported for text_generation routes")
        return None, None, None
    if not isinstance(generation, dict):
        raise RuntimeError(f"model {model_id} text_generation route requires generation config")

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
    is_v2 = "execution" in config
    allowed_fields = MODEL_CONFIG_FIELDS_V2 if is_v2 else MODEL_CONFIG_FIELDS_V1
    unknown = sorted(set(config) - allowed_fields)
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
    capabilities = _capabilities(public, model_id)
    routes = (
        _routes_from_v2(config, model_id=model_id, capabilities=capabilities)
        if is_v2
        else _routes_from_v1(config, model_id=model_id, capabilities=capabilities)
    )
    output_media_types = _media_types(public, "output_media_types", model_id)
    if model_type == "text":
        _text_context_window(limits, model_id)
        _text_supports_json_output(features, model_id)
    primary_capability = capabilities[0]
    primary_route = routes[primary_capability]
    text_route = routes.get(TEXT_GENERATION)
    if model_type == "text":
        temperature, num_retries, drop_params = _generation_values(
            text_route.generation if text_route is not None else None,
            model_id=model_id,
            route_capability=TEXT_GENERATION if text_route is not None else primary_capability,
        )
    else:
        temperature, num_retries, drop_params = None, None, None
    return ModelCatalogEntry(
        id=model_id,
        adapter=primary_route.adapter,
        provider=primary_route.provider,
        provider_model=primary_route.provider_model,
        adapter_model=primary_route.adapter_model,
        pricing_ref=primary_route.pricing_ref,
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
        requires_env=tuple(dict.fromkeys(env for route in routes.values() for env in route.requires_env)),
        temperature=temperature,
        num_retries=num_retries,
        drop_params=drop_params,
        routes=routes,
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


def all_model_catalog_entries() -> list[ModelCatalogEntry]:
    return _models()


def all_default_model_ids() -> dict[str, str]:
    config = _load_model_config()
    default_model_ids = config.get("default_model_ids")
    if not isinstance(default_model_ids, dict) or not default_model_ids:
        raise RuntimeError("model config default_model_ids must be a non-empty YAML object")
    parsed: dict[str, str] = {}
    for capability, model_id in default_model_ids.items():
        if not isinstance(capability, str) or not capability.strip():
            raise RuntimeError("model config default_model_ids keys must be non-empty strings")
        if not isinstance(model_id, str) or not model_id.strip():
            raise RuntimeError(f"model config default_model_ids.{capability} must be a non-empty string")
        parsed[capability.strip()] = model_id.strip()
    return parsed


def default_model_id(capability: str = MODEL_DEFAULT_CAPABILITY) -> str:
    value = all_default_model_ids().get(capability)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"model config default_model_ids.{capability} must be a non-empty string")
    return value.strip()


def models_for_capability(models: list[ModelCatalogEntry], capability: str) -> list[ModelCatalogEntry]:
    return [model for model in models if capability in model.capabilities and route_is_available(model.route_for(capability))]


def _model_supports_capabilities(model: ModelCatalogEntry, capabilities: tuple[str, ...]) -> bool:
    return all(capability in model.capabilities and route_is_available(model.route_for(capability)) for capability in capabilities)


def _require_default_in_available_models(
    *,
    default: str,
    models: list[ModelCatalogEntry],
    context: str,
) -> None:
    if any(model.id == default for model in models):
        return
    raise RuntimeError(f"{context} default_model_id is not available in current environment: {default}")


def route_is_available(route: ModelExecutionRoute) -> bool:
    return all(_env_value(name) for name in route.requires_env)


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

    return job_registry.is_external_job_type_enabled(job_type)


def _job_scoped_models(models: list[ModelCatalogEntry], job_type: str) -> tuple[PublicModelSlotView, list[ModelCatalogEntry]]:
    from app.jobs import model_selection

    normalized_job_type = job_type.strip()
    if not normalized_job_type:
        raise ValidationAppError("INVALID_JOB_TYPE", "job_type must be a non-empty string")
    if not _job_type_exists(normalized_job_type):
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 job_type: {normalized_job_type}")
    if not model_selection.has_model_selection_config(normalized_job_type):
        capability = _default_job_capability(normalized_job_type)
        return (
            PublicModelSlotView(
                default_model_id=default_model_id(capability),
                allowed_model_ids=tuple(model.id for model in models),
                required_capabilities=(capability,),
            ),
            models_for_capability(models, capability),
        )

    selection = model_selection.get_public_model_slot(normalized_job_type)
    model_by_id = {model.id: model for model in models}
    selected_models = [
        model_by_id[model_id]
        for model_id in selection.allowed_model_ids
        if model_id in model_by_id and _model_supports_capabilities(model_by_id[model_id], selection.required_capabilities)
    ]
    return (
        PublicModelSlotView(
            default_model_id=selection.default_model_id,
            allowed_model_ids=selection.allowed_model_ids,
            required_capabilities=selection.required_capabilities,
        ),
        selected_models,
    )


def list_models_response(job_type: str | None = None) -> ModelsResponse:
    models = [model for model in _models() if _model_is_available(model)]
    default = default_model_id()
    _require_default_in_available_models(
        default=default,
        models=models_for_capability(models, MODEL_DEFAULT_CAPABILITY),
        context="global text_generation",
    )
    if job_type is not None:
        slot, models = _job_scoped_models(models, job_type)
        default = slot.default_model_id
        _require_default_in_available_models(default=default, models=models, context=f"job_type {job_type}")
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
    enabled = [model for model in models if model.enabled]
    model_by_id = {model.id: model for model in enabled}
    config = _load_model_config()
    default_model_ids = config.get("default_model_ids")
    if not isinstance(default_model_ids, dict) or not default_model_ids:
        raise RuntimeError("model config default_model_ids must be a non-empty YAML object")
    for capability, model_id in default_model_ids.items():
        if not isinstance(capability, str) or capability not in KNOWN_MODEL_CAPABILITIES:
            raise RuntimeError(f"model config default_model_ids contains unknown capability: {capability}")
        if not isinstance(model_id, str) or model_id not in model_by_id:
            raise RuntimeError(f"model config default_model_ids.{capability} must reference an enabled model: {model_id}")
        if capability not in model_by_id[model_id].capabilities:
            raise RuntimeError(f"model config default_model_ids.{capability} model does not support capability: {model_id}")
    seen: set[str] = set()
    for model in enabled:
        if model.id in seen:
            raise RuntimeError(f"duplicate model id: {model.id}")
        seen.add(model.id)
        for route in model.routes.values():
            validate_provider(route.provider)
            validate_model_adapter(route.adapter)
            if route.capability == "text_generation":
                validate_text_generation_adapter(route.adapter)
            if route.capability == "multimodal_text_generation":
                validate_multimodal_text_generation_adapter(route.adapter)
            if route.capability in {"image_generation", "image_edit"}:
                validate_image_generation_adapter(route.adapter)
            if route.capability == "embeddings":
                validate_embedding_adapter(route.adapter)
            validate_price_matches_model(
                pricing_ref=route.pricing_ref,
                model_id=model.id,
                provider=route.provider,
                provider_model=route.provider_model,
            )
    _validate_job_model_selection_configs(enabled)


def _default_job_capability(job_type: str) -> str:
    return TEXT_GENERATION


def _validate_job_model_selection_configs(enabled_models: list[ModelCatalogEntry]) -> None:
    from app.jobs import model_selection
    from app.jobs import registry as job_registry

    registered_job_types = set(job_registry.all_job_types())
    enabled_job_types = set(job_registry.enabled_job_types())
    poster_job_type = model_selection.POSTER_TITLE_IMAGE_JOB_TYPE
    if poster_job_type in enabled_job_types and not model_selection.has_model_selection_config(poster_job_type):
        raise RuntimeError("poster_title_image requires app/jobs/types/poster_title_image/models.yaml")
    model_by_id = {model.id: model for model in enabled_models}
    for path in sorted(model_selection.JOB_MODEL_CONFIG_ROOT.glob(f"*/{model_selection.JOB_MODEL_CONFIG_FILENAME}")):
        job_type = path.parent.name
        if registered_job_types and job_type not in registered_job_types:
            raise RuntimeError(f"job model selection config references unknown job_type: {job_type}")
        if job_type not in enabled_job_types:
            continue
        policy = model_selection.get_job_model_policy(job_type)
        for slot in policy.slots.values():
            missing_model_ids = sorted(set(slot.allowed_model_ids) - set(model_by_id))
            if missing_model_ids:
                raise RuntimeError(
                    f"job_type {job_type} model slot {slot.slot} references non-enabled models: {missing_model_ids}"
                )
            for model_id in slot.allowed_model_ids:
                missing_capabilities = sorted(set(slot.required_capabilities) - set(model_by_id[model_id].capabilities))
                if missing_capabilities:
                    raise RuntimeError(
                        f"job_type {job_type} model slot {slot.slot} model {model_id} is missing capabilities: "
                        + ", ".join(missing_capabilities)
                    )

    if (
        model_selection.POSTER_TITLE_IMAGE_JOB_TYPE in enabled_job_types
        and model_selection.has_model_selection_config(model_selection.POSTER_TITLE_IMAGE_JOB_TYPE)
    ):
        from app.integrations.image import POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES

        poster_selection = model_selection.get_poster_title_image_model_selection()
        for model_id in poster_selection.generation_slot.allowed_model_ids:
            generation_model = model_by_id[model_id]
            generation_route = generation_model.route_for(IMAGE_EDIT)
            if generation_route.adapter in {"openai_responses", "openai_images"} and generation_route.provider != "openai":
                raise RuntimeError("poster_title_image image route adapter requires OpenAI image models")
            generation_price = require_price(generation_route.pricing_ref)
            if generation_price.pricing_type == "per_image_token" and generation_route.adapter != "openai_images":
                raise RuntimeError("poster_title_image per_image_token pricing requires openai_images route adapter")
            if generation_model.model_type != "image":
                raise RuntimeError("poster_title_image public generation slot must reference image models")
            if "image_edit" not in generation_model.capabilities:
                raise RuntimeError("poster_title_image public generation slot must support image_edit")
            missing_generation_media_types = sorted(
                POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES - set(generation_model.input_media_types)
            )
            if missing_generation_media_types:
                raise RuntimeError(
                    "poster_title_image public generation slot must support reference image input media types: "
                    + ", ".join(missing_generation_media_types)
                )
        style_probe_model = next(
            (model for model in enabled_models if model.id == poster_selection.style_probe_model_id),
            None,
        )
        if style_probe_model is None:
            raise RuntimeError("poster_title_image style_probe model slot must reference an enabled model")
        if style_probe_model.model_type != "text":
            raise RuntimeError("poster_title_image style_probe model slot must reference a text model")
        if "multimodal_text_generation" not in style_probe_model.capabilities:
            raise RuntimeError("poster_title_image style_probe model slot must support multimodal_text_generation")
        missing_style_probe_media_types = sorted(
            POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES - set(style_probe_model.input_media_types)
        )
        if missing_style_probe_media_types:
            raise RuntimeError(
                "poster_title_image style_probe model slot must support reference image input media types: "
                + ", ".join(missing_style_probe_media_types)
            )
        if (
            any(model_by_id[model_id].route_for(IMAGE_EDIT).adapter == "openai_responses" for model_id in poster_selection.generation_slot.allowed_model_ids)
            and style_probe_model.features.get("supports_image_generation_tool") is not True
        ):
            raise RuntimeError("poster_title_image style_probe model slot must support image_generation tool")
