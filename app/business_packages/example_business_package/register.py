from __future__ import annotations

from app.business_packages.base import BusinessPackage, BusinessRouteCollector
from app.business_packages.registrar import RegisterExecutor
from app.business_packages.example_business_package.operations import OPERATIONS
from app.business_packages.example_business_package.schemas import SCHEMAS


def register_job_package(_register: RegisterExecutor) -> None:
    return None


def register_routes(collector: BusinessRouteCollector) -> None:
    from app.business_packages.example_business_package.router import router

    collector.include_router(router)


PACKAGE = BusinessPackage(
    name="example_business_package",
    register=register_job_package,
    register_routes=register_routes,
    operations=OPERATIONS,
    schemas=SCHEMAS,
)
