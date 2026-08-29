from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.ai.capabilities import TEXT_GENERATION


APP_DIR = Path(__file__).resolve().parents[2]
JOB_MODEL_CONFIG_ROOT = APP_DIR / "jobs" / "types"
JOB_MODEL_CONFIG_FILENAME = "models.yaml"
JOB_MODEL_CONFIG_TOP_LEVEL_KEYS = frozenset({"version", "job_type", "model_slots"})
MODEL_SLOT_FIELDS = frozenset(
    {"visibility", "request_field", "default_model_id", "allowed_model_ids", "required_capabilities"}
)
POSTER_TITLE_IMAGE_JOB_TYPE = "poster_title_image"
DEFAULT_PUBLIC_SLOT = "default"


@dataclass(frozen=True)
class ModelSlotPolicy:
    job_type: str
    slot: str
    visibility: str
    default_model_id: str
    allowed_model_ids: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    request_field: str | None
    source_path: Path


@dataclass(frozen=True)
class JobModelPolicy:
    job_type: str
    slots: dict[str, ModelSlotPolicy]
    source_path: Path

    def slot_policy(self, slot: str) -> ModelSlotPolicy:
        policy = self.slots.get(slot)
        if policy is None:
            raise RuntimeError(f"job_type {self.job_type} model slot not found: {slot}")
        return policy


@dataclass(frozen=True)
class PublicModelSlot:
    job_type: str
    default_model_id: str
    allowed_model_ids: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    source_path: Path


@dataclass(frozen=True)
class PosterTitleImageModelSelection:
    generation_slot: PublicModelSlot
    style_probe_model_id: str
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


def _slot_policy(
    *,
    job_type: str,
    slot: str,
    config: dict[str, Any],
    source: Path,
) -> ModelSlotPolicy:
    unknown_keys = sorted(set(config) - MODEL_SLOT_FIELDS)
    if unknown_keys:
        raise RuntimeError(f"model slot {job_type}.{slot} contains unknown fields: {unknown_keys} ({source})")
    visibility = config.get("visibility", "public")
    if visibility not in {"public", "internal"}:
        raise RuntimeError(f"model slot {job_type}.{slot} visibility must be public or internal ({source})")
    request_field = config.get("request_field")
    if request_field is not None and (not isinstance(request_field, str) or not request_field.strip()):
        raise RuntimeError(f"model slot {job_type}.{slot} request_field must be a non-empty string ({source})")
    default_model_id = _required_str(config, "default_model_id", source=source)
    allowed_model_ids = _required_str_tuple(config, "allowed_model_ids", source=source)
    if default_model_id not in allowed_model_ids:
        raise RuntimeError(f"model slot {job_type}.{slot} default_model_id must be included in allowed_model_ids")
    required_capabilities = config.get("required_capabilities", [TEXT_GENERATION])
    if not isinstance(required_capabilities, list) or not required_capabilities:
        raise RuntimeError(f"model slot {job_type}.{slot} required_capabilities must be a non-empty list ({source})")
    return ModelSlotPolicy(
        job_type=job_type,
        slot=slot,
        visibility=visibility,
        default_model_id=default_model_id,
        allowed_model_ids=allowed_model_ids,
        required_capabilities=_required_str_tuple(
            {"required_capabilities": required_capabilities},
            "required_capabilities",
            source=source,
        ),
        request_field=request_field.strip() if isinstance(request_field, str) else None,
        source_path=source,
    )


def get_job_model_policy(job_type: str) -> JobModelPolicy:
    source = _config_path(job_type)
    data = _read_config(source)
    configured_job_type = _required_str(data, "job_type", source=source)
    if configured_job_type != job_type:
        raise RuntimeError(f"job model selection config job_type mismatch: {configured_job_type} != {job_type}")
    raw_slots = data.get("model_slots")
    slots: dict[str, ModelSlotPolicy] = {}
    if not isinstance(raw_slots, dict) or not raw_slots:
        raise RuntimeError(f"job model selection config requires model_slots object: {source}")
    for slot, slot_config in raw_slots.items():
        if not isinstance(slot, str) or not slot.strip():
            raise RuntimeError(f"job model selection config model_slots keys must be non-empty strings: {source}")
        if not isinstance(slot_config, dict):
            raise RuntimeError(f"model slot {slot} must be a YAML object: {source}")
        normalized_slot = slot.strip()
        slots[normalized_slot] = _slot_policy(
            job_type=job_type,
            slot=normalized_slot,
            config=slot_config,
            source=source,
        )
    return JobModelPolicy(job_type=job_type, slots=slots, source_path=source)


def get_public_model_slot(job_type: str) -> PublicModelSlot:
    policy = get_job_model_policy(job_type)
    public_slots = [slot for slot in policy.slots.values() if slot.visibility == "public"]
    if len(public_slots) != 1:
        raise RuntimeError(f"job_type {job_type} requires exactly one public model slot")
    slot = public_slots[0]
    return PublicModelSlot(
        job_type=job_type,
        default_model_id=slot.default_model_id,
        allowed_model_ids=slot.allowed_model_ids,
        required_capabilities=slot.required_capabilities,
        source_path=slot.source_path,
    )


def get_poster_title_image_model_selection() -> PosterTitleImageModelSelection:
    public = get_public_model_slot(POSTER_TITLE_IMAGE_JOB_TYPE)
    policy = get_job_model_policy(POSTER_TITLE_IMAGE_JOB_TYPE)
    style_probe = policy.slot_policy("style_probe")
    return PosterTitleImageModelSelection(
        generation_slot=public,
        style_probe_model_id=style_probe.default_model_id,
        source_path=public.source_path,
    )


def poster_title_image_generation_default_model_id() -> str:
    return get_poster_title_image_model_selection().generation_slot.default_model_id


def poster_title_image_generation_allowed_model_ids() -> tuple[str, ...]:
    return get_poster_title_image_model_selection().generation_slot.allowed_model_ids


def poster_title_image_style_probe_model_id() -> str:
    return get_poster_title_image_model_selection().style_probe_model_id

