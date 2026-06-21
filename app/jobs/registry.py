from __future__ import annotations

from app.jobs.base import JobExecutor, JobTypeSpec

_registry: dict[str, JobExecutor] = {}


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
    register(cls())
    return cls


def get(job_type: str) -> JobExecutor:
    executor = _registry.get(job_type)
    if executor is None:
        raise KeyError(f"No job executor registered for job_type: {job_type!r}")
    return executor


def all_job_types() -> list[str]:
    return list(_registry.keys())


def get_job_type_spec(job_type: str) -> JobTypeSpec:
    return get(job_type).job_type_spec()


def all_job_type_specs() -> dict[str, JobTypeSpec]:
    return {job_type: executor.job_type_spec() for job_type, executor in _registry.items()}


def validate_job_view_payload(payload: dict):
    from app.schemas.jobs import JobEnvelope

    job_view = JobEnvelope.model_validate(payload)
    executor = get(job_view.job_type)
    data = job_view.model_dump()
    if job_view.job_status == "succeeded":
        data["job_result"] = executor.validate_public_result(data["job_result"])
    return JobEnvelope.model_validate(data)


def clear_for_tests() -> None:
    _registry.clear()


__all__ = [
    "JobExecutor",
    "JobTypeSpec",
    "all_job_type_specs",
    "all_job_types",
    "clear_for_tests",
    "get",
    "get_job_type_spec",
    "register",
    "register_job_type",
    "validate_job_view_payload",
]
