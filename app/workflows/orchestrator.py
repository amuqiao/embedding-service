from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ValidationAppError
from app.core.model_registry import get_enabled_model
from app.core.prompt_templates import get_template
from app.jobs.factory import get_job_executor
from app.models.job import Job
from app.repositories.job_repo import JobRepo
from app.services.job_runtime import (
    build_runtime_snapshot,
    configured_output_target,
    payload_hash,
    write_runtime_json,
)
from app.services.jobs import _validate_prompt
from app.workflows.base import FAILURE_POLICIES


@dataclass(frozen=True)
class WorkflowOrchestrationResult:
    root_job_id: uuid.UUID
    created_child_job_ids: tuple[uuid.UUID, ...]
    created_attempt_ids: tuple[uuid.UUID, ...]


async def create_ready_child_jobs(
    db: AsyncSession,
    *,
    root_job: Job,
    workflow_plan: dict[str, Any],
) -> WorkflowOrchestrationResult:
    nodes = _nodes_by_key(workflow_plan)
    children = await JobRepo.list_internal_children(db, root_job_id=root_job.id)
    existing_keys = {child.workflow_node_key for child in children if child.workflow_node_key}
    succeeded_keys = {
        child.workflow_node_key
        for child in children
        if child.workflow_node_key and child.status == "succeeded"
    }

    created_child_job_ids: list[uuid.UUID] = []
    created_attempt_ids: list[uuid.UUID] = []
    for node in nodes.values():
        node_key = node["key"]
        if node_key in existing_keys:
            continue
        if any(dependency not in succeeded_keys for dependency in node["depends_on"]):
            continue
        existing = await JobRepo.get_internal_child_by_node_key(
            db,
            root_job_id=root_job.id,
            workflow_node_key=node_key,
        )
        if existing is not None:
            existing_keys.add(node_key)
            continue
        child, attempt_id = await _create_child_job(db, root_job=root_job, node=node)
        existing_keys.add(node_key)
        created_child_job_ids.append(child.id)
        created_attempt_ids.append(attempt_id)
    return WorkflowOrchestrationResult(
        root_job_id=root_job.id,
        created_child_job_ids=tuple(created_child_job_ids),
        created_attempt_ids=tuple(created_attempt_ids),
    )


def _nodes_by_key(workflow_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _validate_plan_header(workflow_plan)
    raw_nodes = workflow_plan.get("nodes")
    if not isinstance(raw_nodes, list):
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan nodes must be a JSON array")
    if not raw_nodes:
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan requires at least one node")
    if len(raw_nodes) != workflow_plan["node_count"]:
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan node_count does not match nodes length")
    if len(raw_nodes) > workflow_plan["max_nodes"]:
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan exceeds max_nodes")
    nodes: dict[str, dict[str, Any]] = {}
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise AppError("RUNTIME_REF_INVALID", "workflow_plan node must be a JSON object")
        key = raw_node.get("key")
        if not isinstance(key, str) or not key:
            raise AppError("RUNTIME_REF_INVALID", "workflow_plan node key must be a non-empty string")
        if key in nodes:
            raise AppError("RUNTIME_REF_INVALID", f"duplicate workflow node key: {key}")
        depends_on = raw_node.get("depends_on")
        if not isinstance(depends_on, list) or any(not isinstance(item, str) for item in depends_on):
            raise AppError("RUNTIME_REF_INVALID", f"workflow node {key} depends_on must be a string array")
        job_type = raw_node.get("job_type")
        if not isinstance(job_type, str) or not job_type:
            raise AppError("RUNTIME_REF_INVALID", f"workflow node {key} job_type must be a non-empty string")
        job_params = raw_node.get("job_params")
        if not isinstance(job_params, dict):
            raise AppError("RUNTIME_REF_INVALID", f"workflow node {key} job_params must be a JSON object")
        nodes[key] = {
            "key": key,
            "depends_on": tuple(depends_on),
            "job_type": job_type,
            "job_params": deepcopy(job_params),
        }
    for node in nodes.values():
        for dependency in node["depends_on"]:
            if dependency not in nodes:
                raise AppError(
                    "RUNTIME_REF_INVALID",
                    f"workflow node {node['key']} depends on unknown node: {dependency}",
                )
    _validate_dag(nodes)
    return nodes


def _validate_plan_header(workflow_plan: dict[str, Any]) -> None:
    schema_version = workflow_plan.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan schema_version must be 1")
    workflow_type = workflow_plan.get("workflow_type")
    if not isinstance(workflow_type, str) or not workflow_type:
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan workflow_type must be a non-empty string")
    workflow_version = workflow_plan.get("workflow_version")
    if type(workflow_version) is not int or workflow_version < 1:
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan workflow_version must be >= 1")
    failure_policy = workflow_plan.get("failure_policy")
    if failure_policy not in FAILURE_POLICIES:
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan failure_policy is not supported")
    max_nodes = workflow_plan.get("max_nodes")
    if type(max_nodes) is not int or max_nodes < 1:
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan max_nodes must be >= 1")
    node_count = workflow_plan.get("node_count")
    if type(node_count) is not int or node_count < 1:
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan node_count must be >= 1")


def _validate_dag(nodes: dict[str, dict[str, Any]]) -> None:
    remaining_dependencies = {key: set(node["depends_on"]) for key, node in nodes.items()}
    ready = [key for key, dependencies in remaining_dependencies.items() if not dependencies]
    visited: set[str] = set()
    while ready:
        key = ready.pop(0)
        if key in visited:
            continue
        visited.add(key)
        for candidate_key, dependencies in remaining_dependencies.items():
            if candidate_key in visited:
                continue
            dependencies.discard(key)
            if not dependencies:
                ready.append(candidate_key)
    if len(visited) != len(nodes):
        cyclic_keys = sorted(set(nodes) - visited)
        raise AppError(
            "RUNTIME_REF_INVALID",
            "workflow_plan contains cyclic or unschedulable dependencies",
            details={"nodes": cyclic_keys},
        )


async def _create_child_job(
    db: AsyncSession,
    *,
    root_job: Job,
    node: dict[str, Any],
) -> tuple[Job, uuid.UUID]:
    job_type = node["job_type"]
    try:
        handler = get_job_executor(job_type)
    except KeyError as exc:
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 child job_type: {job_type}") from exc
    job_params = deepcopy(node["job_params"])
    try:
        job_params = handler.normalize_job_params(job_params)
        if not isinstance(job_params, dict):
            raise ValueError("child job_params normalizer must return an object")
        handler.validate_normalized_job_params(job_params)
        runtime_fields = handler.runtime_job_fields(job_params)
    except AppError:
        raise
    except ValueError as exc:
        raise ValidationAppError(
            "INVALID_INPUT",
            "workflow child job_params does not match job_type schema",
            {"job_type": job_type, "workflow_node_key": node["key"]},
        ) from exc
    except NotImplementedError as exc:
        raise ValidationAppError(
            "INVALID_JOB_TYPE",
            f"child job_type 缺少运行时适配: {job_type}",
        ) from exc
    model_id = runtime_fields.get("model_id")
    if model_id and not get_enabled_model(model_id):
        raise ValidationAppError("MODEL_NOT_AVAILABLE", f"模型不可用: {model_id}")
    template = get_template(job_type)
    if template:
        prompt_payload = runtime_fields.get("prompt_payload")
        if not isinstance(prompt_payload, dict):
            raise ValidationAppError("INVALID_INPUT", "child job_type runtime fields must include prompt_payload")
        _validate_prompt(job_type, prompt_payload)

    timeout_seconds = int(getattr(handler, "timeout_seconds", root_job.timeout_seconds or 300))
    max_attempts = int(getattr(handler, "max_attempts", 1))
    child = await JobRepo.create(
        db,
        caller_id=root_job.caller_id,
        client_request_id=None,
        job_type=job_type,
        metadata={},
        priority=root_job.priority,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        job_params=job_params,
        callback_url=None,
        callback_events=None,
        root_job_id=root_job.id,
        parent_job_id=root_job.id,
        is_internal=True,
        workflow_node_key=node["key"],
    )
    job_params_hash = payload_hash(job_params)
    output_target = configured_output_target(child.id)
    child.job_params_hash = job_params_hash
    child.job_params_ref = write_runtime_json(child, "job_params", job_params)
    child.runtime_ref = write_runtime_json(
        child,
        "runtime",
        build_runtime_snapshot(
            job_type=job_type,
            job_params_hash=job_params_hash,
            runtime_fields=runtime_fields,
            output_target=output_target,
        ),
    )
    attempt = await JobRepo.create_initial_attempt(db, child, timeout_seconds=timeout_seconds)
    return child, attempt.id
