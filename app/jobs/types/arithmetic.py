from __future__ import annotations

from typing import Any

from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.jobs.types._registrar import JobTypePackage, RegisterExecutor
from app.schemas.jobs import ArithmeticParams, ArithmeticResult, ArithmeticRuntimeFields
from app.services.job_runtime import job_params_from_job


@register_job_type
class ArithmeticJob(JobExecutor):
    name = "arithmetic"
    visibility = "demo"
    role = "root"
    params_schema = ArithmeticParams
    runtime_fields_schema_name = "ArithmeticRuntimeFields"
    canonical_result_schema = ArithmeticResult
    public_result_schema = ArithmeticResult
    allow_callback = True
    allowed_error_codes = frozenset(
        {
            "INVALID_INPUT",
            "JOB_STATE_TRANSITION_CONFLICT",
            "JOB_EXECUTION_FAILED",
            "WORKFLOW_AFTER_SUCCESS_FAILED",
            "JOB_RUNTIME_NOT_SUPPORTED",
        }
    )

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return ArithmeticRuntimeFields(operation="add_subtract_multiply_divide").model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = ArithmeticParams.model_validate(job_params_from_job(job))
        return ArithmeticResult(
            a=params.a,
            b=params.b,
            addition=params.a + params.b,
            subtraction=params.a - params.b,
            multiplication=params.a * params.b,
            division=params.a / params.b,
        ).model_dump()


def register_job_package(register: RegisterExecutor) -> None:
    register(ArithmeticJob())


PACKAGE = JobTypePackage(name="arithmetic", register=register_job_package)
