from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from app.core.exceptions import AppError
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.jobs.types.example_catalog import all_example_workflow_mode_specs
from app.schemas.jobs import (
    ExampleCollectParams,
    ExampleCollectResult,
    ExampleCollectRuntimeFields,
    ExampleLifecycleProbeParams,
    ExampleLifecycleProbeResult,
    ExampleLifecycleProbeRuntimeFields,
    ExamplePairParams,
    ExamplePairResult,
    ExamplePairRuntimeFields,
    ExampleSleepParams,
    ExampleSleepResult,
    ExampleSleepRuntimeFields,
    ExampleWorkflowParams,
    ExampleWorkflowResult,
    ExampleWorkflowRuntimeFields,
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


async def _simulate_example_behavior(
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
            "JOB_EXECUTION_FAILED",
            f"{job_type} forced failure",
            details={"job_id": str(job_id), "job_type": job_type, "fault": "forced_failure"},
        )


@register_job_type
class ExampleSleepJob(JobExecutor):
    name = "example_sleep"
    visibility = "demo"
    role = "root_or_leaf"
    params_schema = ExampleSleepParams
    runtime_fields_schema_name = "ExampleSleepRuntimeFields"
    canonical_result_schema = ExampleSleepResult
    public_result_schema = ExampleSleepResult
    allow_callback = False
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
        return ExampleSleepRuntimeFields(operation="sleep").model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = ExampleSleepParams.model_validate(job_params_from_job(job))
        await _simulate_example_behavior(
            sleep_seconds=params.sleep_seconds,
            fail=params.fail,
            fail_after_seconds=params.fail_after_seconds,
            job_id=job.id,
            job_type=self.name,
        )
        result = ExampleSleepResult(
            message=params.message,
            repeated=[params.message for _ in range(params.repeat)],
            count=params.repeat,
            payload="x" * params.result_size_bytes,
        )
        return result.model_dump(exclude={"payload"} if params.result_size_bytes == 0 else None)


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
            "JOB_EXECUTION_FAILED",
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
        await _simulate_example_behavior(
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


@register_job_type
class ExamplePairJob(JobExecutor):
    name = "example_pair"
    visibility = "demo"
    role = "root_or_leaf"
    params_schema = ExamplePairParams
    runtime_fields_schema_name = "ExamplePairRuntimeFields"
    canonical_result_schema = ExamplePairResult
    public_result_schema = ExamplePairResult
    allow_callback = False
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
        params = ExamplePairParams.model_validate(job_params)
        normalized = {"a": params.a, "b": params.b}
        for key in ("sleep_seconds", "fail", "fail_after_seconds"):
            if key in params.model_fields_set:
                normalized[key] = getattr(params, key)
        return normalized

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return ExamplePairRuntimeFields(operation="pair").model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = ExamplePairParams.model_validate(job_params_from_job(job))
        await _simulate_example_behavior(
            sleep_seconds=params.sleep_seconds,
            fail=params.fail,
            fail_after_seconds=params.fail_after_seconds,
            job_id=job.id,
            job_type=self.name,
        )
        return ExamplePairResult(a=params.a, b=params.b, result=params.a + params.b).model_dump()


@register_job_type
class ExampleWorkflowJob(JobExecutor):
    name = "example_workflow"
    visibility = "demo"
    role = "root"
    params_schema = ExampleWorkflowParams
    runtime_fields_schema_name = "ExampleWorkflowRuntimeFields"
    canonical_result_schema = ExampleWorkflowResult
    public_result_schema = ExampleWorkflowResult
    allow_callback = False
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
        params = ExampleWorkflowParams.model_validate(job_params)
        normalized = {"mode": params.mode, "label": params.label}
        for key in ("sleep_seconds", "fail_node_key", "fail_after_seconds", "result_size_bytes"):
            if key in params.model_fields_set:
                normalized[key] = getattr(params, key)
        return normalized

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return ExampleWorkflowRuntimeFields(operation="workflow_root").model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        raise AppError(
            "JOB_RUNTIME_NOT_SUPPORTED",
            "example workflow root must be executed by workflow orchestration",
            details={"job_id": str(job.id), "job_type": job.job_type},
        )


@register_job_type
class ExampleCollectJob(JobExecutor):
    name = "example_collect"
    visibility = "demo"
    role = "leaf"
    params_schema = ExampleCollectParams
    runtime_fields_schema_name = "ExampleCollectRuntimeFields"
    canonical_result_schema = ExampleCollectResult
    public_result_schema = ExampleCollectResult
    allow_callback = False
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
        params = ExampleCollectParams.model_validate(job_params)
        normalized = {"items": params.items}
        for key in ("sleep_seconds", "fail", "fail_after_seconds"):
            if key in params.model_fields_set:
                normalized[key] = getattr(params, key)
        return normalized

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return ExampleCollectRuntimeFields(operation="collect").model_dump()

    async def _execute(self, job, db) -> dict[str, Any] | None:
        params = ExampleCollectParams.model_validate(job_params_from_job(job))
        await _simulate_example_behavior(
            sleep_seconds=params.sleep_seconds,
            fail=params.fail,
            fail_after_seconds=params.fail_after_seconds,
            job_id=job.id,
            job_type=self.name,
        )
        return ExampleCollectResult(items=params.items, count=len(params.items)).model_dump()


def register_example_workflows() -> None:
    register_workflow(_workflow_definition())


def _workflow_definition() -> WorkflowDefinition:
    global _WORKFLOW_DEFINITION
    if _WORKFLOW_DEFINITION is None:
        _WORKFLOW_DEFINITION = WorkflowDefinition(
            workflow_type="example_workflow",
            root_job_type="example_workflow",
            build=_workflow_expr,
            max_nodes=10,
            runtime_job_type_dependencies=frozenset({"example_sleep", "example_pair", "example_collect"}),
        )
    return _WORKFLOW_DEFINITION


def _node_fault_params(key: str, params: dict[str, Any]) -> dict[str, Any]:
    if params.get("fail_node_key") != key:
        return {}
    return {
        "fail": True,
        "fail_after_seconds": params.get("fail_after_seconds", 0),
    }


def _sleep_node(key: str, label: str, params: dict[str, Any]) -> Any:
    job_params = {
        "message": f"{label}:{key}",
        "repeat": 1,
        "sleep_seconds": params.get("sleep_seconds", 0),
        "result_size_bytes": params.get("result_size_bytes", 0),
    } | _node_fault_params(key, params)
    return task(
        key,
        "example_sleep",
        job_params,
    )


def _workflow_expr(params: dict[str, Any]) -> Any:
    mode = params["mode"]
    if mode == "single":
        return _single_expr(params)
    if mode == "chain":
        return _chain_expr(params)
    if mode == "group":
        return _group_expr(params)
    if mode == "chord":
        return _chord_expr(params)
    if mode == "map":
        return _map_expr(params)
    if mode == "starmap":
        return _starmap_expr(params)
    if mode == "chunks":
        return _chunks_expr(params)
    raise ValueError(f"unsupported example workflow mode: {mode}")


def _single_expr(params: dict[str, Any]) -> Any:
    return _sleep_node("only", params["label"], params)


def _chain_expr(params: dict[str, Any]) -> Any:
    label = params["label"]
    return chain(
        _sleep_node("a", label, params),
        _sleep_node("b", label, params),
        _sleep_node("c", label, params),
    )


def _group_expr(params: dict[str, Any]) -> Any:
    label = params["label"]
    return group(
        _sleep_node("a", label, params),
        _sleep_node("b", label, params),
        _sleep_node("c", label, params),
    )


def _chord_expr(params: dict[str, Any]) -> Any:
    label = params["label"]
    return chord(
        group(
            _sleep_node("a", label, params),
            _sleep_node("b", label, params),
        ),
        _sleep_node("join", label, params),
    )


def _map_expr(params: dict[str, Any]) -> Any:
    return map_items(
        "item",
        "example_sleep",
        [f"{params['label']}:one", f"{params['label']}:two"],
        param_name="message",
        static_job_params={
            "repeat": 1,
            "sleep_seconds": params.get("sleep_seconds", 0),
            "result_size_bytes": params.get("result_size_bytes", 0),
        } | _node_fault_params("item", params),
    )


def _starmap_expr(params: dict[str, Any]) -> Any:
    return starmap_items(
        "pair",
        "example_pair",
        [(1, 2), {"a": 3, "b": 4}],
        arg_names=("a", "b"),
        static_job_params={"sleep_seconds": params.get("sleep_seconds", 0)} | _node_fault_params("pair", params),
    )


def _chunks_expr(params: dict[str, Any]) -> Any:
    return chunks(
        "chunk",
        "example_collect",
        [f"{params['label']}:1", f"{params['label']}:2", f"{params['label']}:3", f"{params['label']}:4", f"{params['label']}:5"],
        chunk_size=2,
        static_job_params={"sleep_seconds": params.get("sleep_seconds", 0)} | _node_fault_params("chunk", params),
    )
