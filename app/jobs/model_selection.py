from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


APP_DIR = Path(__file__).resolve().parents[1]
JOB_MODEL_CONFIG_ROOT = APP_DIR / "jobs" / "types"
JOB_MODEL_CONFIG_FILENAME = "models.yaml"
JOB_MODEL_CONFIG_TOP_LEVEL_KEYS = frozenset(
    {"version", "job_type", "public_model_selection", "internal_models", "generation"}
)
PUBLIC_MODEL_SELECTION_FIELDS = frozenset({"request_field", "default_model_id", "allowed_model_ids"})
GENERATION_FIELDS = frozenset({"image_adapter"})
POSTER_TITLE_IMAGE_GENERATION_IMAGE_ADAPTERS = frozenset({"openai_responses", "openai_images"})
POSTER_TITLE_IMAGE_JOB_TYPE = "poster_title_image"


@dataclass(frozen=True)
class PublicModelSelection:
    job_type: str
    default_model_id: str
    allowed_model_ids: tuple[str, ...]
    source_path: Path


@dataclass(frozen=True)
class PosterTitleImageModelSelection:
    public_model_selection: PublicModelSelection
    style_probe_model_id: str
    image_generation_adapter: str
    source_path: Path


def _config_path(job_type: str) -> Path:
    return JOB_MODEL_CONFIG_ROOT / job_type / JOB_MODEL_CONFIG_FILENAME


def has_model_selection_config(job_type: str) -> bool:
    return _config_path(job_type).exists()


def _read_config(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"job model selection config not found: {path}") from exc
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"job model selection config must be a YAML object: {path}")
    unknown_keys = sorted(set(data) - JOB_MODEL_CONFIG_TOP_LEVEL_KEYS)
    if unknown_keys:
        raise RuntimeError(f"job model selection config contains unknown top-level keys: {unknown_keys}")
    return data


def _required_str(config: dict[str, Any], key: str, *, source: Path) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"job model selection config requires non-empty string field: {key} ({source})")
    return value.strip()


def _required_str_tuple(config: dict[str, Any], key: str, *, source: Path) -> tuple[str, ...]:
    value = config.get(key)
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"job model selection config requires non-empty list field: {key} ({source})")
    values: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(f"job model selection config {key} items must be non-empty strings ({source})")
        normalized = item.strip()
        if normalized in seen:
            raise RuntimeError(f"job model selection config contains duplicate {key}: {normalized} ({source})")
        seen.add(normalized)
        values.append(normalized)
    return tuple(values)


def _public_model_selection(data: dict[str, Any], *, job_type: str, source: Path) -> PublicModelSelection:
    config = data.get("public_model_selection")
    if not isinstance(config, dict):
        raise RuntimeError(f"job model selection config requires public_model_selection object: {source}")
    unknown_keys = sorted(set(config) - PUBLIC_MODEL_SELECTION_FIELDS)
    if unknown_keys:
        raise RuntimeError(f"public_model_selection contains unknown fields: {unknown_keys} ({source})")
    request_field = config.get("request_field")
    if request_field is not None and (not isinstance(request_field, str) or not request_field.strip()):
        raise RuntimeError(f"public_model_selection.request_field must be a non-empty string: {source}")
    allowed_model_ids = _required_str_tuple(config, "allowed_model_ids", source=source)
    default_model_id = _required_str(config, "default_model_id", source=source)
    if default_model_id not in allowed_model_ids:
        raise RuntimeError("public_model_selection.default_model_id must be included in allowed_model_ids")
    return PublicModelSelection(
        job_type=job_type,
        default_model_id=default_model_id,
        allowed_model_ids=allowed_model_ids,
        source_path=source,
    )


def get_public_model_selection(job_type: str) -> PublicModelSelection:
    source = _config_path(job_type)
    data = _read_config(source)
    configured_job_type = _required_str(data, "job_type", source=source)
    if configured_job_type != job_type:
        raise RuntimeError(
            f"job model selection config job_type mismatch: {configured_job_type} != {job_type}"
        )
    return _public_model_selection(data, job_type=job_type, source=source)


def _internal_models(data: dict[str, Any], *, source: Path) -> dict[str, Any]:
    value = data.get("internal_models")
    if not isinstance(value, dict):
        raise RuntimeError(f"job model selection config requires internal_models object: {source}")
    return value


def _generation_config(data: dict[str, Any], *, source: Path) -> dict[str, Any]:
    value = data.get("generation")
    if not isinstance(value, dict):
        raise RuntimeError(f"job model selection config requires generation object: {source}")
    unknown_keys = sorted(set(value) - GENERATION_FIELDS)
    if unknown_keys:
        raise RuntimeError(f"generation contains unknown fields: {unknown_keys} ({source})")
    return value


def get_poster_title_image_model_selection() -> PosterTitleImageModelSelection:
    public = get_public_model_selection(POSTER_TITLE_IMAGE_JOB_TYPE)
    data = _read_config(public.source_path)
    internal_models = _internal_models(data, source=public.source_path)
    style_probe = internal_models.get("style_probe")
    if not isinstance(style_probe, dict):
        raise RuntimeError("poster_title_image internal_models.style_probe must be a YAML object")
    style_probe_model_id = _required_str(style_probe, "model_id", source=public.source_path)
    generation = _generation_config(data, source=public.source_path)
    image_generation_adapter = _required_str(generation, "image_adapter", source=public.source_path)
    if image_generation_adapter not in POSTER_TITLE_IMAGE_GENERATION_IMAGE_ADAPTERS:
        raise RuntimeError(
            "poster_title_image generation.image_adapter must be one of: "
            + ", ".join(sorted(POSTER_TITLE_IMAGE_GENERATION_IMAGE_ADAPTERS))
        )
    return PosterTitleImageModelSelection(
        public_model_selection=public,
        style_probe_model_id=style_probe_model_id,
        image_generation_adapter=image_generation_adapter,
        source_path=public.source_path,
    )


def poster_title_image_generation_default_model_id() -> str:
    return get_poster_title_image_model_selection().public_model_selection.default_model_id


def poster_title_image_generation_allowed_model_ids() -> tuple[str, ...]:
    return get_poster_title_image_model_selection().public_model_selection.allowed_model_ids


def poster_title_image_style_probe_model_id() -> str:
    return get_poster_title_image_model_selection().style_probe_model_id


def poster_title_image_generation_image_adapter() -> str:
    return get_poster_title_image_model_selection().image_generation_adapter
