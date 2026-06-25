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
    build: Callable[[dict[str, Any]], WorkflowExpr]
    workflow_version: int = 1
    failure_policy: str = "fail_fast"
    max_nodes: int = 100

    def spec_from_params(self, job_params: dict[str, Any]) -> WorkflowSpec:
        return WorkflowSpec(
            workflow_type=self.workflow_type,
            root=self.build(deepcopy(job_params)),
            workflow_version=self.workflow_version,
            failure_policy=self.failure_policy,
            max_nodes=self.max_nodes,
        )


_registry: dict[str, WorkflowDefinition] = {}


def register(definition: WorkflowDefinition) -> WorkflowDefinition:
    if not definition.workflow_type:
        raise ValueError("workflow definition must declare workflow_type")
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


def compile_registered_workflow(workflow_type: str, job_params: dict[str, Any]) -> dict[str, Any]:
    definition = get(workflow_type)
    return compile_workflow(definition.spec_from_params(job_params))


def clear_for_tests() -> None:
    _registry.clear()
