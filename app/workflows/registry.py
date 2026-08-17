from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.workflows.base import WorkflowExpr, WorkflowSpec
from app.workflows.compiler import compile_workflow


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_type: str
    root_job_type: str
    build: Callable[[dict[str, Any]], WorkflowExpr]
    workflow_version: int = 1
    failure_policy: str = "fail_fast"
    max_nodes: int = 100
    runtime_job_type_dependencies: frozenset[str] = frozenset()

    def spec_from_params(self, job_params: dict[str, Any]) -> WorkflowSpec:
        return WorkflowSpec(
            workflow_type=self.workflow_type,
            root=self.build(deepcopy(job_params)),
            workflow_version=self.workflow_version,
            failure_policy=self.failure_policy,
            max_nodes=self.max_nodes,
        )


_registry: dict[str, WorkflowDefinition] = {}


class WorkflowRuntimeDependencyError(ValueError):
    pass


class WorkflowRuntimeDependencyUndeclaredError(WorkflowRuntimeDependencyError):
    pass


class WorkflowRuntimeDependencyDisabledError(WorkflowRuntimeDependencyError):
    pass


class WorkflowRuntimeDependencyRoleError(WorkflowRuntimeDependencyError):
    pass


def register(definition: WorkflowDefinition) -> WorkflowDefinition:
    if not definition.workflow_type:
        raise ValueError("workflow definition must declare workflow_type")
    if not definition.root_job_type:
        raise ValueError("workflow definition must declare root_job_type")
    existing = _registry.get(definition.workflow_type)
    if existing is not None:
        if existing == definition:
            return existing
        raise ValueError(f"duplicate workflow_type: {definition.workflow_type}")
    _registry[definition.workflow_type] = definition
    return definition


def get(workflow_type: str) -> WorkflowDefinition:
    definition = _registry.get(workflow_type)
    if definition is None:
        raise KeyError(f"No workflow registered for workflow_type: {workflow_type!r}")
    return definition


def get_optional(workflow_type: str) -> WorkflowDefinition | None:
    return _registry.get(workflow_type)


def has_workflow(workflow_type: str) -> bool:
    return workflow_type in _registry


def all_workflow_types() -> list[str]:
    return list(_registry.keys())


def all_workflow_definitions() -> dict[str, WorkflowDefinition]:
    return dict(_registry)


def compile_registered_workflow(workflow_type: str, job_params: dict[str, Any]) -> dict[str, Any]:
    definition = get(workflow_type)
    plan = compile_workflow(definition.spec_from_params(job_params))
    _validate_runtime_job_type_dependencies(definition, plan)
    return plan


def _validate_runtime_job_type_dependencies(definition: WorkflowDefinition, plan: dict[str, Any]) -> None:
    from app.jobs import registry as job_registry

    node_job_types = {
        node.get("job_type")
        for node in plan.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("job_type"), str)
    }
    undeclared = sorted(node_job_types - definition.runtime_job_type_dependencies)
    if undeclared:
        raise WorkflowRuntimeDependencyUndeclaredError(
            f"workflow {definition.workflow_type} plan references undeclared runtime job_type dependencies: "
            f"{undeclared}"
        )
    for job_type in sorted(node_job_types):
        try:
            spec = job_registry.get_enabled(job_type).job_type_spec()
        except KeyError as exc:
            raise WorkflowRuntimeDependencyDisabledError(
                f"workflow {definition.workflow_type} plan references disabled runtime job_type dependency: "
                f"{job_type}"
            ) from exc
        if spec.role not in {"leaf", "root_or_leaf"}:
            raise WorkflowRuntimeDependencyRoleError(
                f"workflow {definition.workflow_type} plan references non-child runtime job_type dependency: "
                f"{job_type}"
            )


def clear_for_tests() -> None:
    _registry.clear()
