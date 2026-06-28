from __future__ import annotations

import asyncio
from typing import Any

from app.core.exceptions import AppError
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.schemas.jobs import (
    JobTestCollectParams,
    JobTestCollectResult,
    JobTestCollectRuntimeFields,
    JobTestWorkflowParams,
    JobTestWorkflowResult,
    JobTestWorkflowRuntimeFields,
)
from app.services.job_runtime import job_params_from_job
from app.workflows import (
    WorkflowDefinition,
    chain,
    chord,
    chunks,
    group,
    map_items,
    register as register_workflow,
    starmap_items,
    task,
)

_WORKFLOW_DEFINITION: WorkflowDefinition | None = None


@register_job_type
class JobTestWorkflowJob(JobExecutor):
    name = "job_test_workflow"
    visibility = "demo"
    role = "root"
    params_schema = JobTestWorkflowParams
    runtime_fields_schema_name = "JobTestWorkflowRuntimeFields"
    canonical_result_schema = JobTestWorkflowResult
    public_result_schema = JobTestWorkflowResult
    allow_callback = True
    timeout_seconds = 120
    allowed_error_codes = frozenset(
        {
            "INVALID_INPUT",
            "JOB_STATE_TRANSITION_CONFLICT",
            "JOB_EXECUTION_FAILED",
            "WORKFLOW_AFTER_SUCCESS_FAILED",
            "JOB_RUNTIME_NOT_SUPPORTED",
            "WORKFLOW_CHILD_FAILED",
        }
    )

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = JobTestWorkflowParams.model_validate(job_params)
        normalized = {"mode": params.mode, "label": params.label}
        if "sleep_seconds" in params.model_fields_set:
            normalized["sleep_seconds"] = params.sleep_seconds
        return normalized

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return JobTestWorkflowRuntimeFields(operation="workflow_root").model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        raise AppError(
            "JOB_RUNTIME_NOT_SUPPORTED",
            "workflow test root must be executed by workflow orchestration",
            details={"job_id": str(job.id), "job_type": job.job_type},
        )


@register_job_type
class JobTestCollectJob(JobExecutor):
    name = "job_test_collect"
    visibility = "demo"
    role = "leaf"
    params_schema = JobTestCollectParams
    runtime_fields_schema_name = "JobTestCollectRuntimeFields"
    canonical_result_schema = JobTestCollectResult
    public_result_schema = JobTestCollectResult
    allow_callback = True
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

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = JobTestCollectParams.model_validate(job_params)
        normalized = {"items": params.items}
        if "sleep_seconds" in params.model_fields_set:
            normalized["sleep_seconds"] = params.sleep_seconds
        return normalized

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return JobTestCollectRuntimeFields(operation="collect").model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = JobTestCollectParams.model_validate(job_params_from_job(job))
        if params.sleep_seconds:
            await asyncio.sleep(params.sleep_seconds)
        return JobTestCollectResult(items=params.items, count=len(params.items)).model_dump()


def register_test_workflows() -> None:
    register_workflow(_workflow_definition())


def _workflow_definition() -> WorkflowDefinition:
    global _WORKFLOW_DEFINITION
    if _WORKFLOW_DEFINITION is None:
        _WORKFLOW_DEFINITION = WorkflowDefinition(
            workflow_type="job_test_workflow",
            build=_workflow_expr,
            max_nodes=10,
        )
    return _WORKFLOW_DEFINITION


def _echo_node(key: str, label: str, sleep_seconds: float) -> Any:
    return task(
        key,
        "job_test_echo",
        {"message": f"{label}:{key}", "repeat": 1, "sleep_seconds": sleep_seconds},
    )


def _workflow_expr(params: dict[str, Any]) -> Any:
    mode = params["mode"]
    label = params["label"]
    sleep_seconds = params.get("sleep_seconds", 0)
    if mode == "single":
        return _single_expr(label, sleep_seconds)
    if mode == "chain":
        return _chain_expr(label, sleep_seconds)
    if mode == "group":
        return _group_expr(label, sleep_seconds)
    if mode == "chord":
        return _chord_expr(label, sleep_seconds)
    if mode == "map":
        return _map_expr(label, sleep_seconds)
    if mode == "starmap":
        return _starmap_expr(sleep_seconds)
    if mode == "chunks":
        return _chunks_expr(label, sleep_seconds)
    raise ValueError(f"unsupported workflow smoke mode: {mode}")


def _single_expr(label: str, sleep_seconds: float) -> Any:
    return _echo_node("only", label, sleep_seconds)


def _chain_expr(label: str, sleep_seconds: float) -> Any:
    return chain(
        _echo_node("a", label, sleep_seconds),
        _echo_node("b", label, sleep_seconds),
        _echo_node("c", label, sleep_seconds),
    )


def _group_expr(label: str, sleep_seconds: float) -> Any:
    return group(
        _echo_node("a", label, sleep_seconds),
        _echo_node("b", label, sleep_seconds),
        _echo_node("c", label, sleep_seconds),
    )


def _chord_expr(label: str, sleep_seconds: float) -> Any:
    return chord(
        group(
            _echo_node("a", label, sleep_seconds),
            _echo_node("b", label, sleep_seconds),
        ),
        _echo_node("join", label, sleep_seconds),
    )


def _map_expr(label: str, sleep_seconds: float) -> Any:
    return map_items(
        "item",
        "job_test_echo",
        [f"{label}:one", f"{label}:two"],
        param_name="message",
        static_job_params={"repeat": 1, "sleep_seconds": sleep_seconds},
    )


def _starmap_expr(sleep_seconds: float) -> Any:
    return starmap_items(
        "pair",
        "job_test_add",
        [(1, 2), {"a": 3, "b": 4}],
        arg_names=("a", "b"),
        static_job_params={"sleep_seconds": sleep_seconds},
    )


def _chunks_expr(label: str, sleep_seconds: float) -> Any:
    return chunks(
        "chunk",
        "job_test_collect",
        [f"{label}:1", f"{label}:2", f"{label}:3", f"{label}:4", f"{label}:5"],
        chunk_size=2,
        static_job_params={"sleep_seconds": sleep_seconds},
    )
