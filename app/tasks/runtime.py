from __future__ import annotations


def ensure_worker_runtime_initialized() -> None:
    from app.core.database import init_db_engine
    from app.core.error_registry import freeze_error_registry
    from app.ai.catalog.registry import validate_model_catalog
    from app.core.registry_checks import validate_all_registries
    from app.business_packages.register import register_all_business_packages
    from app.tools.registry import freeze as freeze_tool_registry

    init_db_engine()
    register_all_business_packages()
    freeze_error_registry()
    freeze_tool_registry()
    validate_all_registries()
    validate_model_catalog()
