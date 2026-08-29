from __future__ import annotations

from dataclasses import dataclass

from app.ai.capabilities import ResolvedModel, TEXT_GENERATION
from app.ai.catalog.registry import (
    ModelCatalogEntry,
    ModelExecutionRoute,
    default_model_id,
    get_enabled_model,
    route_is_available,
)
from app.ai.policy import job_models
from app.core.exceptions import ValidationAppError


@dataclass(frozen=True)
class ResolvedModelSelection:
    model: ModelCatalogEntry
    route: ModelExecutionRoute
    resolved_model: ResolvedModel
    source_policy: str


def _requested_or_default_model_id(
    *,
    job_type: str | None,
    slot: str,
    capability: str,
    requested_model_id: str | None,
) -> tuple[str, tuple[str, ...] | None, str]:
    if requested_model_id and requested_model_id.strip():
        return requested_model_id.strip(), None, "request"
    if job_type and job_models.has_model_selection_config(job_type):
        policy = job_models.get_job_model_policy(job_type).slot_policy(slot)
        return policy.default_model_id, policy.allowed_model_ids, f"job:{job_type}:{slot}"
    return default_model_id(capability), None, f"global:{capability}"


def _ensure_job_type_enabled(job_type: str | None) -> None:
    if job_type is None:
        return
    from app.jobs import registry as job_registry

    if not job_registry.is_external_job_type_enabled(job_type):
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 job_type: {job_type}")


def resolve_model(
    *,
    capability: str = TEXT_GENERATION,
    requested_model_id: str | None = None,
    job_type: str | None = None,
    slot: str = job_models.DEFAULT_PUBLIC_SLOT,
    required_capabilities: tuple[str, ...] | None = None,
) -> ResolvedModelSelection:
    _ensure_job_type_enabled(job_type)
    selected_model_id, allowed_model_ids, source_policy = _requested_or_default_model_id(
        job_type=job_type,
        slot=slot,
        capability=capability,
        requested_model_id=requested_model_id,
    )
    if job_type and job_models.has_model_selection_config(job_type):
        policy = job_models.get_job_model_policy(job_type).slot_policy(slot)
        allowed_model_ids = policy.allowed_model_ids
        required_capabilities = required_capabilities or policy.required_capabilities
    if allowed_model_ids is not None and selected_model_id not in allowed_model_ids:
        raise ValidationAppError(
            "MODEL_NOT_AVAILABLE",
            f"模型不在 job_type 允许列表内: {selected_model_id}",
        )
    model = get_enabled_model(selected_model_id)
    if model is None:
        raise ValidationAppError("MODEL_NOT_AVAILABLE", f"模型不可用: {selected_model_id}")
    capabilities = required_capabilities or (capability,)
    missing_capabilities = sorted(set(capabilities) - set(model.capabilities))
    if missing_capabilities:
        raise ValidationAppError(
            "MODEL_NOT_AVAILABLE",
            f"模型缺少能力: {selected_model_id}/{', '.join(missing_capabilities)}",
        )
    route = model.route_for(capability)
    if not route_is_available(route):
        raise ValidationAppError("MODEL_NOT_AVAILABLE", f"模型能力 route 缺少必要运行环境: {selected_model_id}/{capability}")
    return ResolvedModelSelection(
        model=model,
        route=route,
        resolved_model=ResolvedModel(
            model_id=model.id,
            capability=capability,
            provider=route.provider,
            adapter=route.adapter,
            provider_model=route.provider_model,
            adapter_model=route.adapter_model,
            pricing_ref=route.pricing_ref,
            route_config_hash=route.config_hash,
        ),
        source_policy=source_policy,
    )


def resolve_route_config_hash(
    *,
    capability: str = TEXT_GENERATION,
    requested_model_id: str,
) -> str:
    return resolve_model(capability=capability, requested_model_id=requested_model_id).resolved_model.route_config_hash
