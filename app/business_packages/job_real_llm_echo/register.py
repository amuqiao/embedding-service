from __future__ import annotations

from app.business_packages.base import BusinessPackage
from app.business_packages.job_real_llm_echo.schemas import SCHEMAS
from app.business_packages.registrar import RegisterExecutor


def register_job_package(register: RegisterExecutor) -> None:
    from app.business_packages.job_real_llm_echo.executor import JobRealLlmEchoJob

    register(JobRealLlmEchoJob())


PACKAGE = BusinessPackage(name="job_real_llm_echo", register=register_job_package, schemas=SCHEMAS)
