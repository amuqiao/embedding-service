from __future__ import annotations

from importlib import import_module

from fastapi import FastAPI

from app.business_packages.base import BusinessPackage, BusinessRouteCollector, BusinessRouteMount


BUSINESS_PACKAGE_MODULES: tuple[str, ...] = (
    "app.business_packages.arithmetic",
    "app.business_packages.examples",
    "app.business_packages.example_lifecycle_probe.register",
    "app.business_packages.job_real_llm_echo",
    "app.business_packages.job_real_llm_double_echo",
    "app.business_packages.poster_title_image.register",
    "app.business_packages.tagged_text_translation.register",
    "app.business_packages.audio_stem_separation.register",
    "app.business_packages.audio_stem_separation_triton.register",
)

_route_mounts: tuple[BusinessRouteMount, ...] = ()
_registered_package_names: tuple[str, ...] = ()
_job_type_package_names: dict[str, str] = {}


def business_package_modules() -> tuple[str, ...]:
    return BUSINESS_PACKAGE_MODULES


def load_business_packages() -> tuple[BusinessPackage, ...]:
    packages: list[BusinessPackage] = []
    package_names: set[str] = set()
    for module_path in BUSINESS_PACKAGE_MODULES:
        package = getattr(import_module(module_path), "PACKAGE")
        if not isinstance(package, BusinessPackage):
            raise TypeError(f"{module_path}.PACKAGE must be BusinessPackage")
        if package.name in package_names:
            raise ValueError(f"duplicate business package: {package.name}")
        package_names.add(package.name)
        packages.append(package)
    return tuple(packages)


def _selected_business_packages(
    packages: tuple[BusinessPackage, ...],
    configured_package_names: tuple[str, ...],
) -> tuple[BusinessPackage, ...]:
    if not configured_package_names:
        return packages
    by_name = {package.name: package for package in packages}
    unknown = sorted(set(configured_package_names) - set(by_name))
    if unknown:
        raise ValueError(f"ENABLED_BUSINESS_PACKAGES references unknown business package: {unknown}")
    return tuple(by_name[name] for name in configured_package_names)


def _validate_release_storage_requirements(settings, packages: tuple[BusinessPackage, ...]) -> None:
    if not settings.runtime.is_release_env or settings.storage.backend != "local":
        return
    requiring_storage = sorted(package.name for package in packages if package.requires_object_storage)
    if requiring_storage:
        raise ValueError(
            "release APP_ENV must not use STORAGE_BACKEND=local when enabled business packages "
            f"require object storage: {requiring_storage}"
        )


def validate_business_package_config(settings) -> None:
    all_packages = load_business_packages()
    selected_packages = _selected_business_packages(
        all_packages,
        settings.registry.enabled_business_packages,
    )
    _validate_release_storage_requirements(settings, selected_packages)


def _configure_job_type_access(
    *,
    enabled_job_types: frozenset[str],
    release_env: bool,
) -> None:
    from app.jobs import registry as job_registry

    specs = job_registry.all_job_type_specs()
    external = frozenset(
        job_type
        for job_type, spec in specs.items()
        if job_type in enabled_job_types
        and spec.role != "leaf"
        and (spec.visibility == "public" or (spec.visibility == "demo" and not release_env))
    )
    job_registry.configure_enabled_job_types(enabled_job_types, external_job_types=external)


def registered_business_package_names() -> tuple[str, ...]:
    return _registered_package_names


def job_type_business_package_names() -> dict[str, str]:
    return dict(_job_type_package_names)


def registered_business_route_mounts() -> tuple[BusinessRouteMount, ...]:
    return _route_mounts


def register_all_business_packages() -> None:
    from app.core.config import settings
    from app.jobs.registry import register
    from app.tools.register import register_all_tools

    all_packages = load_business_packages()
    selected_packages = _selected_business_packages(
        all_packages,
        settings.registry.enabled_business_packages,
    )
    _validate_release_storage_requirements(settings, selected_packages)

    register_all_tools()

    ownership: dict[str, str] = {}
    route_collector = BusinessRouteCollector()
    for package in all_packages:

        def register_package_executor(executor, *, package_name: str = package.name):
            existing_owner = ownership.get(executor.name)
            if existing_owner is not None and existing_owner != package_name:
                raise ValueError(
                    f"job_type {executor.name} is registered by multiple business packages: "
                    f"{existing_owner}, {package_name}"
                )
            registered = register(executor)
            ownership[executor.name] = package_name
            return registered

        package.register(register_package_executor)

    selected_package_names = frozenset(package.name for package in selected_packages)
    for package in selected_packages:
        if package.register_routes is not None:
            package.register_routes(route_collector)

    enabled_job_types = frozenset(
        job_type
        for job_type, package_name in ownership.items()
        if package_name in selected_package_names
    )

    global _route_mounts, _registered_package_names, _job_type_package_names
    _route_mounts = route_collector.route_mounts()
    _registered_package_names = tuple(package.name for package in selected_packages)
    _job_type_package_names = dict(ownership)
    _configure_job_type_access(
        enabled_job_types=enabled_job_types,
        release_env=settings.runtime.is_release_env,
    )


def _join_api_prefix(api_prefix: str, package_prefix: str) -> str:
    if not api_prefix:
        return package_prefix
    if not package_prefix:
        return api_prefix
    return f"{api_prefix.rstrip('/')}/{package_prefix.lstrip('/')}"


def include_business_package_routes(application: FastAPI, *, api_prefix: str) -> None:
    for mount in registered_business_route_mounts():
        application.include_router(mount.router, prefix=_join_api_prefix(api_prefix, mount.prefix))
