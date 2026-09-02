from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from app.core.exceptions import AppError
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.business_packages.example_lifecycle_probe.errors import EXAMPLE_LIFECYCLE_PROBE_FORCED_FAILURE
from app.schemas.jobs import (
    ExampleLifecycleProbeParams,
    ExampleLifecycleProbeResult,
    ExampleLifecycleProbeRuntimeFields,
)
from app.services.job_runtime import job_params_from_job


async def _simulate_probe_behavior(
    *,
    sleep_seconds: float,
    fail: bool,
    fail_after_seconds: float,
    job_id: object,
    job_type: str,
) -> None:
    if sleep_seconds:
        await asyncio.sleep(sleep_seconds)
    if fail_after_seconds:
        await asyncio.sleep(fail_after_seconds)
    if fail:
        raise AppError(
            EXAMPLE_LIFECYCLE_PROBE_FORCED_FAILURE,
            f"{job_type} forced failure",
            details={"job_id": str(job_id), "job_type": job_type, "fault": "forced_failure"},
        )


@register_job_type
class ExampleLifecycleProbeJob(JobExecutor):
    name = "example_lifecycle_probe"
    visibility = "demo"
    role = "root"
    params_schema = ExampleLifecycleProbeParams
    runtime_fields_schema_name = "ExampleLifecycleProbeRuntimeFields"
    canonical_result_schema = ExampleLifecycleProbeResult
    public_result_schema = ExampleLifecycleProbeResult
    allow_callback = True
    timeout_seconds = 900
    allowed_error_codes = frozenset(
        {
            "INVALID_INPUT",
            "JOB_STATE_TRANSITION_CONFLICT",
            EXAMPLE_LIFECYCLE_PROBE_FORCED_FAILURE,
            "WORKFLOW_AFTER_SUCCESS_FAILED",
            "JOB_RUNTIME_NOT_SUPPORTED",
        }
    )

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = ExampleLifecycleProbeParams.model_validate(job_params)
        return ExampleLifecycleProbeRuntimeFields(
            operation="lifecycle_probe",
            probe_id=params.probe_id,
            sleep_seconds=params.sleep_seconds,
            fail=params.fail,
            fail_after_seconds=params.fail_after_seconds,
        ).model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = ExampleLifecycleProbeParams.model_validate(job_params_from_job(job))
        started = time.monotonic()
        await _simulate_probe_behavior(
            sleep_seconds=params.sleep_seconds,
            fail=params.fail,
            fail_after_seconds=params.fail_after_seconds,
            job_id=job.id,
            job_type=self.name,
        )
        payload = params.result_payload
        if params.result_size_bytes:
            payload = "x" * params.result_size_bytes
        result = ExampleLifecycleProbeResult(
            probe_id=params.probe_id,
            message=params.message,
            requested_sleep_seconds=params.sleep_seconds,
            fail=params.fail,
            result_payload=payload,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            worker_observed_at=datetime.now(timezone.utc).isoformat(),
        )
        return result.model_dump(exclude_none=True)
