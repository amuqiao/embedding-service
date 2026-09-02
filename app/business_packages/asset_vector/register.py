from __future__ import annotations

from app.business_packages.asset_vector.errors import register_asset_vector_errors
from app.business_packages.asset_vector.operations import OPERATIONS
from app.business_packages.asset_vector.schemas import SCHEMAS
from app.business_packages.base import BusinessPackage, BusinessRouteCollector
from app.business_packages.registrar import RegisterExecutor, register_executor_classes


def register_job_package(register: RegisterExecutor) -> None:
    from app.business_packages.asset_vector.executor import (
        AssetVectorBatchDeleteJob,
        AssetVectorBatchUpsertJob,
        AssetVectorEmbedItemJob,
        AssetVectorUpsertJoinJob,
        register_asset_vector_workflow,
    )

    register_asset_vector_errors()
    register_executor_classes(
        register,
        (
            AssetVectorBatchUpsertJob,
            AssetVectorEmbedItemJob,
            AssetVectorUpsertJoinJob,
            AssetVectorBatchDeleteJob,
        ),
    )
    register_asset_vector_workflow()


def register_routes(collector: BusinessRouteCollector) -> None:
    from app.business_packages.asset_vector.router import router

    collector.include_router(router)


PACKAGE = BusinessPackage(
    name="asset_vector",
    register=register_job_package,
    register_routes=register_routes,
    operations=OPERATIONS,
    schemas=SCHEMAS,
)
