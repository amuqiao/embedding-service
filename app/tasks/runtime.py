from __future__ import annotations


def ensure_worker_runtime_initialized() -> None:
    from app.capabilities.registry import freeze as freeze_capability_registry
    from app.core.database import init_db_engine
    from app.core.error_registry import freeze_error_registry
    from app.core.model_registry import validate_model_catalog
    from app.core.registry_checks import validate_all_registries
    from app.jobs.types.register import register_all_job_types
    from app.tools.registry import freeze as freeze_tool_registry

    init_db_engine()
    register_all_job_types()
    freeze_error_registry()
    freeze_tool_registry()
    freeze_capability_registry()
    validate_all_registries()
    validate_model_catalog()
