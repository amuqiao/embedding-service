from __future__ import annotations

from importlib import import_module

from app.jobs.types._registrar import JobTypePackage, RegisterExecutor


JOB_TYPE_PACKAGE_MODULES: tuple[str, ...] = (
    "app.jobs.types.arithmetic",
    "app.jobs.types.examples",
    "app.jobs.types.example_lifecycle_probe.register",
    "app.jobs.types.job_real_llm_echo",
    "app.jobs.types.job_real_llm_double_echo",
    "app.jobs.types.poster_title_image.register",
    "app.jobs.types.tagged_text_translation.register",
    "app.jobs.types.audio_stem_separation.register",
    "app.jobs.types.audio_stem_separation_triton.register",
)


def _expanded_enabled_job_types(
    configured_job_types: tuple[str, ...],
    *,
    release_env: bool,
) -> tuple[frozenset[str], frozenset[str]] | None:
    if not configured_job_types:
        return None

    from app.jobs import registry as job_registry
    from app.workflows import registry as workflow_registry

    specs = job_registry.all_job_type_specs()
    unknown = sorted(set(configured_job_types) - set(specs))
    if unknown:
        raise ValueError(f"ENABLED_JOB_TYPES references unknown job_type: {unknown}")

    external = set(configured_job_types)
    enabled = set(external)
    for job_type in configured_job_types:
        spec = specs[job_type]
        if spec.visibility == "internal" or spec.role == "leaf":
            raise ValueError("ENABLED_JOB_TYPES must list external root-capable job_types")
        if release_env and spec.visibility != "public":
            raise ValueError("release APP_ENV ENABLED_JOB_TYPES must list only public job_types")
        definition = workflow_registry.get_optional(job_type)
        if definition is not None:
            enabled.update(definition.runtime_job_type_dependencies)

    missing_dependencies = sorted(enabled - set(specs))
    if missing_dependencies:
        raise ValueError(f"ENABLED_JOB_TYPES references unknown dependent job_type: {missing_dependencies}")
    return frozenset(enabled), frozenset(external)


def _default_enabled_job_types(*, release_env: bool) -> tuple[frozenset[str], frozenset[str]] | None:
    if not release_env:
        return None

    from app.jobs import registry as job_registry

    specs = job_registry.all_job_type_specs()
    enabled = frozenset(specs)
    external = frozenset(
        job_type
        for job_type, spec in specs.items()
        if spec.visibility == "public" and spec.role != "leaf"
    )
    return enabled, external


def job_type_package_modules() -> tuple[str, ...]:
    return JOB_TYPE_PACKAGE_MODULES


def load_job_type_packages() -> tuple[JobTypePackage, ...]:
    packages: list[JobTypePackage] = []
    for module_path in JOB_TYPE_PACKAGE_MODULES:
        package = getattr(import_module(module_path), "PACKAGE")
        if not isinstance(package, JobTypePackage):
            raise TypeError(f"{module_path}.PACKAGE must be JobTypePackage")
        packages.append(package)
    return tuple(packages)


def register_all_job_type_packages(register: RegisterExecutor) -> None:
    for package in load_job_type_packages():
        package.register(register)


def register_all_job_types() -> None:
    from app.core.config import settings
    from app.capabilities.register import register_all_capabilities
    from app.jobs.registry import configure_enabled_job_types, register
    from app.tools.register import register_all_tools

    register_all_tools()
    register_all_capabilities()
    register_all_job_type_packages(register)
    expanded_enabled_job_types = _expanded_enabled_job_types(
        settings.job.enabled_job_types,
        release_env=settings.runtime.is_release_env,
    )
    if expanded_enabled_job_types is None:
        default_job_types = _default_enabled_job_types(release_env=settings.runtime.is_release_env)
        if default_job_types is None:
            configure_enabled_job_types(None)
        else:
            runtime_job_types, external_job_types = default_job_types
            configure_enabled_job_types(runtime_job_types, external_job_types=external_job_types)
    else:
        runtime_job_types, external_job_types = expanded_enabled_job_types
        configure_enabled_job_types(runtime_job_types, external_job_types=external_job_types)
