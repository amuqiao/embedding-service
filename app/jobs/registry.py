from __future__ import annotations

from collections.abc import Iterable

from app.jobs.base import JobExecutor, JobTypeSpec

_registry: dict[str, JobExecutor] = {}
_enabled_job_types: frozenset[str] | None = None
_external_job_types: frozenset[str] | None = None


def _is_external_root_capable(job_type: str) -> bool:
    spec = get(job_type).job_type_spec()
    return spec.visibility != "internal" and spec.role != "leaf"


def register(executor: JobExecutor) -> JobExecutor:
    if not executor.name:
        raise ValueError("job executor must declare name")
    existing = _registry.get(executor.name)
    if existing is not None:
        if existing.__class__ is executor.__class__:
            return existing
        raise ValueError(f"duplicate job_type: {executor.name}")
    _registry[executor.name] = executor
    return executor


def register_job_type(cls: type[JobExecutor]) -> type[JobExecutor]:
    setattr(cls, "__job_type_registered__", True)
    return cls


def get(job_type: str) -> JobExecutor:
    executor = _registry.get(job_type)
    if executor is None:
        raise KeyError(f"No job executor registered for job_type: {job_type!r}")
    return executor


def configure_enabled_job_types(
    job_types: Iterable[str] | None,
    *,
    external_job_types: Iterable[str] | None = None,
) -> None:
    global _enabled_job_types, _external_job_types

    if job_types is None:
        _enabled_job_types = None
        _external_job_types = None
        return
    normalized = frozenset(job_type.strip() for job_type in job_types if job_type.strip())
    unknown = sorted(normalized - set(_registry))
    if unknown:
        raise ValueError(f"enabled job type registry references unknown job_type: {unknown}")
    external_normalized = (
        normalized
        if external_job_types is None
        else frozenset(job_type.strip() for job_type in external_job_types if job_type.strip())
    )
    unknown_external = sorted(external_normalized - set(_registry))
    if unknown_external:
        raise ValueError(f"enabled job type registry references unknown external job_type: {unknown_external}")
    not_enabled_external = sorted(external_normalized - normalized)
    if not_enabled_external:
        raise ValueError(f"enabled job type registry external job_type is not enabled: {not_enabled_external}")
    invalid_external = sorted(job_type for job_type in external_normalized if not _is_external_root_capable(job_type))
    if invalid_external:
        raise ValueError(f"enabled job type registry external job_type must be root-capable: {invalid_external}")
    _enabled_job_types = normalized
    _external_job_types = external_normalized


def is_job_type_enabled(job_type: str) -> bool:
    if job_type not in _registry:
        return False
    if _enabled_job_types is None:
        return True
    return job_type in _enabled_job_types


def get_enabled(job_type: str) -> JobExecutor:
    if not is_job_type_enabled(job_type):
        raise KeyError(f"No enabled job executor for job_type: {job_type!r}")
    return get(job_type)


def is_external_job_type_enabled(job_type: str) -> bool:
    if job_type not in _registry:
        return False
    if _external_job_types is None:
        return _is_external_root_capable(job_type)
    return job_type in _external_job_types


def get_external(job_type: str) -> JobExecutor:
    if not is_external_job_type_enabled(job_type):
        raise KeyError(f"No external job executor enabled for job_type: {job_type!r}")
    return get(job_type)


def all_job_types() -> list[str]:
    return list(_registry.keys())


def enabled_job_types() -> list[str]:
    if _enabled_job_types is None:
        return all_job_types()
    return [job_type for job_type in _registry if job_type in _enabled_job_types]


def external_job_types() -> list[str]:
    if _external_job_types is None:
        return [job_type for job_type in _registry if _is_external_root_capable(job_type)]
    return [job_type for job_type in _registry if job_type in _external_job_types]


def get_job_type_spec(job_type: str) -> JobTypeSpec:
    return get(job_type).job_type_spec()


def all_job_type_specs() -> dict[str, JobTypeSpec]:
    return {job_type: executor.job_type_spec() for job_type, executor in _registry.items()}


def enabled_job_type_specs() -> dict[str, JobTypeSpec]:
    return {job_type: get(job_type).job_type_spec() for job_type in enabled_job_types()}


def validate_job_view_payload(payload: dict):
    from app.schemas.jobs import JobEnvelope

    job_view = JobEnvelope.model_validate(payload)
    executor = get(job_view.job_type)
    data = job_view.model_dump()
    if job_view.job_status == "succeeded":
        data["job_result"] = executor.validate_public_result(data["job_result"])
    elif job_view.job_status in {"running", "failed"}:
        data["job_result"] = executor.validate_result_snapshot(job_view.job_status, data["job_result"])
    return JobEnvelope.model_validate(data)


def clear_for_tests() -> None:
    global _enabled_job_types, _external_job_types

    _registry.clear()
    _enabled_job_types = None
    _external_job_types = None


__all__ = [
    "JobExecutor",
    "JobTypeSpec",
    "all_job_type_specs",
    "all_job_types",
    "clear_for_tests",
    "configure_enabled_job_types",
    "enabled_job_type_specs",
    "enabled_job_types",
    "external_job_types",
    "get",
    "get_enabled",
    "get_external",
    "get_job_type_spec",
    "is_external_job_type_enabled",
    "is_job_type_enabled",
    "register",
    "register_job_type",
    "validate_job_view_payload",
]
