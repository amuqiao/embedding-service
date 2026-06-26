from __future__ import annotations

import asyncio
from typing import Any

from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.schemas.jobs import JobTestAddParams, JobTestAddResult, JobTestAddRuntimeFields
from app.services.job_runtime import job_params_from_job


@register_job_type
class JobTestAddJob(JobExecutor):
    name = "job_test_add"
    visibility = "demo"
    role = "root_or_leaf"
    params_schema = JobTestAddParams
    runtime_fields_schema_name = "JobTestAddRuntimeFields"
    canonical_result_schema = JobTestAddResult
    public_result_schema = JobTestAddResult
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

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = JobTestAddParams.model_validate(job_params)
        normalized = {"a": params.a, "b": params.b}
        if "sleep_seconds" in params.model_fields_set:
            normalized["sleep_seconds"] = params.sleep_seconds
        return normalized

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return JobTestAddRuntimeFields(operation="add").model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = JobTestAddParams.model_validate(job_params_from_job(job))
        if params.sleep_seconds:
            await asyncio.sleep(params.sleep_seconds)
        return JobTestAddResult(a=params.a, b=params.b, result=params.a + params.b).model_dump()
