from __future__ import annotations


def ensure_worker_runtime_initialized() -> None:
    from app.core.database import init_db_engine
    from app.core.error_registry import freeze_error_registry
    from app.core.model_registry import validate_model_catalog
    from app.core.registry_checks import validate_job_type_registry
    from app.jobs.types.register import register_all_job_types

    init_db_engine()
    register_all_job_types()
    freeze_error_registry()
    validate_job_type_registry()
    validate_model_catalog()
