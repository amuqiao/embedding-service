from __future__ import annotations

import asyncio
from typing import Any

from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.schemas.jobs import JobTestEchoParams, JobTestEchoResult, JobTestEchoRuntimeFields
from app.services.job_runtime import job_params_from_job


@register_job_type
class JobTestEchoJob(JobExecutor):
    name = "job_test_echo"
    params_schema = JobTestEchoParams
    runtime_fields_schema_name = "JobTestEchoRuntimeFields"
    canonical_result_schema = JobTestEchoResult
    public_result_schema = JobTestEchoResult
    allow_callback = True
    max_attempts = 1
    timeout_seconds = 60
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
        return JobTestEchoRuntimeFields(operation="echo").model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = JobTestEchoParams.model_validate(job_params_from_job(job))
        if params.sleep_seconds:
            await asyncio.sleep(params.sleep_seconds)
        return JobTestEchoResult(
            message=params.message,
            repeated=[params.message for _ in range(params.repeat)],
            count=params.repeat,
        ).model_dump()
