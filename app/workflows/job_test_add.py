from __future__ import annotations

from typing import Any

from app.core.workflow_registry import WorkflowHandler
from app.schemas.jobs import JobResult, JobTestAddParams, JobTestAddResult
from app.services.job_planner import JobPlan, PlannedWorkItem
from app.services.job_runtime import job_params_from_job, work_item_payload


class JobTestAddWorkflow(WorkflowHandler):
    job_type = "job_test_add"
    params_schema = JobTestAddParams
    public_result_schema = JobTestAddResult
    allow_callback = True

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return {"operation": "add"}

    def build_execution_plan(self, job) -> JobPlan:
        params = JobTestAddParams.model_validate(job_params_from_job(job))
        return JobPlan(
            execution_mode="single",
            chunk_count=1,
            chunk_registry=[{"chunk_index": 1, "kind": "whole"}],
            work_items=[
                PlannedWorkItem(
                    name=f"{self.job_type}.whole",
                    kind="whole",
                    chunk_index=0,
                    input_data=params.model_dump(),
                )
            ],
        )

    async def execute_standard_item(self, item, job, db) -> dict[str, Any] | None:
        params = JobTestAddParams.model_validate(work_item_payload(item))
        result_value = params.a + params.b
        result = JobTestAddResult(a=params.a, b=params.b, result=result_value)
        return JobResult(artifacts=[], signals=result.model_dump()).model_dump()

    def validate_canonical_result(self, result: dict[str, Any]) -> dict[str, Any]:
        signals = result.get("signals") if isinstance(result, dict) else None
        if not isinstance(signals, dict):
            raise ValueError("job_test_add canonical result requires signals")
        return JobTestAddResult.model_validate(signals).model_dump()
