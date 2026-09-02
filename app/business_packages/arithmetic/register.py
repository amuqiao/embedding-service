from __future__ import annotations

from app.business_packages.arithmetic.schemas import SCHEMAS
from app.business_packages.base import BusinessPackage
from app.business_packages.registrar import RegisterExecutor


def register_job_package(register: RegisterExecutor) -> None:
    from app.business_packages.arithmetic.executor import ArithmeticJob

    register(ArithmeticJob())


PACKAGE = BusinessPackage(name="arithmetic", register=register_job_package, schemas=SCHEMAS)
