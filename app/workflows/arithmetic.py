from __future__ import annotations

from typing import Any

from app.core.workflow_registry import WorkflowHandler
from app.schemas.jobs import ArithmeticParams, ArithmeticResult, ArithmeticRuntimeFields, JobResult
from app.services.job_planner import JobPlan, PlannedWorkItem
from app.services.job_runtime import job_params_from_job, work_item_payload


class ArithmeticWorkflow(WorkflowHandler):
    job_type = "arithmetic"
    params_schema = ArithmeticParams
    runtime_fields_schema_name = "ArithmeticRuntimeFields"
    canonical_result_schema = ArithmeticResult
    public_result_schema = ArithmeticResult
    allow_callback = True
    allowed_error_codes = frozenset(
        {
            "INVALID_INPUT",
            "JOB_STATE_TRANSITION_CONFLICT",
            "WORK_ITEM_FAILED",
            "WORKFLOW_AFTER_SUCCESS_FAILED",
            "JOB_RUNTIME_NOT_SUPPORTED",
        }
    )

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return ArithmeticRuntimeFields(operation="add_subtract_multiply_divide").model_dump()

    def build_execution_plan(self, job) -> JobPlan:
        params = ArithmeticParams.model_validate(job_params_from_job(job))
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
        params = ArithmeticParams.model_validate(work_item_payload(item))
        result = ArithmeticResult(
            a=params.a,
            b=params.b,
            addition=params.a + params.b,
            subtraction=params.a - params.b,
            multiplication=params.a * params.b,
            division=params.a / params.b,
        )
        return JobResult(artifacts=[], signals=result.model_dump()).model_dump()

    def validate_canonical_result(self, result: dict[str, Any]) -> dict[str, Any]:
        signals = result.get("signals") if isinstance(result, dict) else None
        if not isinstance(signals, dict):
            raise ValueError("arithmetic canonical result requires signals")
        return ArithmeticResult.model_validate(signals).model_dump()
