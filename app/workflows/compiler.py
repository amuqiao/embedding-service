from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from app.workflows.base import (
    FAILURE_POLICIES,
    Chain,
    Chord,
    Chunks,
    Group,
    Map,
    StarMap,
    Task,
    WorkflowExpr,
    WorkflowSpec,
)

_NODE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass
class _Node:
    key: str
    job_type: str
    job_params: dict[str, Any]
    depends_on: set[str] = field(default_factory=set)
    required: bool = True
    weight: int = 1


@dataclass
class _CompiledExpr:
    nodes: list[_Node]
    roots: set[str]
    leaves: set[str]


def compile_workflow(spec: WorkflowSpec) -> dict[str, Any]:
    _validate_spec_header(spec)
    compiled = _compile_expr(spec.root)
    nodes_by_key = _dedupe_nodes(compiled.nodes)
    _validate_dependencies(nodes_by_key)
    ordered_keys = _topological_order(nodes_by_key)
    if len(ordered_keys) > spec.max_nodes:
        raise ValueError(f"workflow {spec.workflow_type} exceeds max_nodes: {len(ordered_keys)} > {spec.max_nodes}")
    nodes = [_freeze_node(nodes_by_key[key]) for key in ordered_keys]
    plan = _strict_json_value(
        {
            "schema_version": 1,
            "kind": "dag_lite",
            "workflow_type": spec.workflow_type,
            "workflow_version": spec.workflow_version,
            "failure_policy": spec.failure_policy,
            "max_nodes": spec.max_nodes,
            "node_count": len(nodes),
            "nodes": nodes,
        },
        "workflow_plan",
    )
    _assert_json_serializable(plan)
    return plan


def _strict_json_value(value: Any, path: str) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains non-string object key: {key!r}")
            normalized[key] = _strict_json_value(item, f"{path}.{key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [_strict_json_value(item, f"{path}[]") for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        try:
            json.dumps(value, allow_nan=False)
        except ValueError as exc:
            raise ValueError(f"{path} contains non-finite number") from exc
        return value
    raise ValueError(f"{path} contains unsupported JSON value: {type(value).__name__}")


def _validate_spec_header(spec: WorkflowSpec) -> None:
    if not isinstance(spec.workflow_type, str) or not spec.workflow_type.strip():
        raise ValueError("workflow_type must be a non-empty string")
    if spec.workflow_version < 1:
        raise ValueError("workflow_version must be >= 1")
    if spec.failure_policy not in FAILURE_POLICIES:
        raise ValueError(f"unsupported failure_policy: {spec.failure_policy}")
    if spec.max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")


def _compile_expr(expr: WorkflowExpr) -> _CompiledExpr:
    if isinstance(expr, Task):
        return _compile_task(expr)
    if isinstance(expr, Chain):
        return _compile_chain(expr)
    if isinstance(expr, Group):
        return _compile_group(expr)
    if isinstance(expr, Chord):
        return _compile_chord(expr)
    if isinstance(expr, Map):
        return _compile_map(expr)
    if isinstance(expr, StarMap):
        return _compile_starmap(expr)
    if isinstance(expr, Chunks):
        return _compile_chunks(expr)
    raise TypeError(f"unsupported workflow expression: {type(expr).__name__}")


def _compile_task(task: Task) -> _CompiledExpr:
    job_params = _strict_json_value(dict(task.job_params), f"node {task.key} job_params")
    _validate_node_fields(
        key=task.key,
        job_type=task.job_type,
        job_params=job_params,
        required=task.required,
        weight=task.weight,
    )
    node = _Node(
        key=task.key,
        job_type=task.job_type,
        job_params=job_params,
        depends_on=set(task.depends_on),
        required=task.required,
        weight=task.weight,
    )
    return _CompiledExpr(nodes=[node], roots={node.key}, leaves={node.key})


def _compile_chain(chain: Chain) -> _CompiledExpr:
    if not chain.steps:
        raise ValueError("chain requires at least one step")
    all_nodes: list[_Node] = []
    first_roots: set[str] | None = None
    previous_leaves: set[str] = set()
    current_leaves: set[str] = set()
    for step in chain.steps:
        compiled = _compile_expr(step)
        if first_roots is None:
            first_roots = set(compiled.roots)
        for node in compiled.nodes:
            if node.key in compiled.roots:
                node.depends_on.update(previous_leaves)
        all_nodes.extend(compiled.nodes)
        previous_leaves = set(compiled.leaves)
        current_leaves = set(compiled.leaves)
    return _CompiledExpr(nodes=all_nodes, roots=first_roots or set(), leaves=current_leaves)


def _compile_group(group: Group) -> _CompiledExpr:
    if not group.members:
        raise ValueError("group requires at least one member")
    all_nodes: list[_Node] = []
    roots: set[str] = set()
    leaves: set[str] = set()
    for member in group.members:
        compiled = _compile_expr(member)
        all_nodes.extend(compiled.nodes)
        roots.update(compiled.roots)
        leaves.update(compiled.leaves)
    return _CompiledExpr(nodes=all_nodes, roots=roots, leaves=leaves)


def _compile_chord(chord: Chord) -> _CompiledExpr:
    header = _compile_expr(chord.header)
    body = _compile_expr(chord.body)
    for node in body.nodes:
        if node.key in body.roots:
            node.depends_on.update(header.leaves)
    return _CompiledExpr(
        nodes=[*header.nodes, *body.nodes],
        roots=set(header.roots),
        leaves=set(body.leaves),
    )


def _compile_map(expr: Map) -> _CompiledExpr:
    _validate_expansion(expr.key_prefix, expr.job_type, expr.param_name, expr.weight)
    nodes = [
        _Node(
            key=f"{expr.key_prefix}.{index}",
            job_type=expr.job_type,
            job_params={**dict(expr.static_job_params), expr.param_name: item},
            required=expr.required,
            weight=expr.weight,
        )
        for index, item in enumerate(expr.items)
    ]
    return _compiled_expanded_nodes(nodes, primitive="map")


def _compile_starmap(expr: StarMap) -> _CompiledExpr:
    _validate_expansion(expr.key_prefix, expr.job_type, "starmap", expr.weight)
    if not expr.arg_names:
        raise ValueError("starmap requires at least one arg name")
    nodes: list[_Node] = []
    for index, item in enumerate(expr.items):
        if isinstance(item, dict):
            params = dict(item)
            missing = [name for name in expr.arg_names if name not in params]
            if missing:
                raise ValueError(f"starmap item {index} missing args: {missing}")
            params = {name: params[name] for name in expr.arg_names}
        else:
            try:
                values = list(item)
            except TypeError as exc:
                raise ValueError(f"starmap item {index} must be a sequence or object") from exc
            if len(values) != len(expr.arg_names):
                raise ValueError(
                    f"starmap item {index} arg count mismatch: {len(values)} != {len(expr.arg_names)}"
                )
            params = dict(zip(expr.arg_names, values, strict=True))
        nodes.append(
            _Node(
                key=f"{expr.key_prefix}.{index}",
                job_type=expr.job_type,
                job_params={**dict(expr.static_job_params), **params},
                required=expr.required,
                weight=expr.weight,
            )
        )
    return _compiled_expanded_nodes(nodes, primitive="starmap")


def _compile_chunks(expr: Chunks) -> _CompiledExpr:
    _validate_expansion(expr.key_prefix, expr.job_type, expr.param_name, expr.weight)
    if expr.chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    chunks = [
        expr.items[index : index + expr.chunk_size]
        for index in range(0, len(expr.items), expr.chunk_size)
    ]
    nodes = [
        _Node(
            key=f"{expr.key_prefix}.{index}",
            job_type=expr.job_type,
            job_params={**dict(expr.static_job_params), expr.param_name: list(chunk)},
            required=expr.required,
            weight=expr.weight,
        )
        for index, chunk in enumerate(chunks)
    ]
    return _compiled_expanded_nodes(nodes, primitive="chunks")


def _compiled_expanded_nodes(nodes: list[_Node], *, primitive: str) -> _CompiledExpr:
    if not nodes:
        raise ValueError(f"{primitive} requires at least one item")
    for node in nodes:
        _validate_node_fields(
            key=node.key,
            job_type=node.job_type,
            job_params=node.job_params,
            required=node.required,
            weight=node.weight,
        )
    keys = {node.key for node in nodes}
    return _CompiledExpr(nodes=nodes, roots=keys, leaves=keys)


def _validate_expansion(key_prefix: str, job_type: str, param_name: str, weight: int) -> None:
    if not isinstance(key_prefix, str) or not key_prefix.strip():
        raise ValueError("key_prefix must be a non-empty string")
    if not isinstance(job_type, str) or not job_type.strip():
        raise ValueError("job_type must be a non-empty string")
    if not isinstance(param_name, str) or not param_name.strip():
        raise ValueError("param_name must be a non-empty string")
    if weight < 1:
        raise ValueError("node weight must be >= 1")


def _validate_node_fields(
    *,
    key: str,
    job_type: str,
    job_params: dict[str, Any],
    required: bool,
    weight: int,
) -> None:
    if not isinstance(key, str) or not _NODE_KEY_RE.match(key):
        raise ValueError(f"invalid workflow node key: {key!r}")
    if not isinstance(job_type, str) or not job_type.strip():
        raise ValueError("job_type must be a non-empty string")
    if not isinstance(job_params, dict):
        raise ValueError(f"node {key} job_params must be an object")
    if not isinstance(required, bool):
        raise ValueError(f"node {key} required must be boolean")
    if weight < 1:
        raise ValueError(f"node {key} weight must be >= 1")


def _dedupe_nodes(nodes: list[_Node]) -> dict[str, _Node]:
    nodes_by_key: dict[str, _Node] = {}
    for node in nodes:
        if node.key in nodes_by_key:
            raise ValueError(f"duplicate workflow node key: {node.key}")
        nodes_by_key[node.key] = node
    return nodes_by_key


def _validate_dependencies(nodes_by_key: dict[str, _Node]) -> None:
    for node in nodes_by_key.values():
        for dependency in node.depends_on:
            if dependency not in nodes_by_key:
                raise ValueError(f"node {node.key} depends on unknown node: {dependency}")


def _topological_order(nodes_by_key: dict[str, _Node]) -> list[str]:
    dependents: dict[str, list[str]] = defaultdict(list)
    indegree = {key: 0 for key in nodes_by_key}
    for node in nodes_by_key.values():
        for dependency in node.depends_on:
            dependents[dependency].append(node.key)
            indegree[node.key] += 1
    ready = deque(sorted(key for key, count in indegree.items() if count == 0))
    ordered: list[str] = []
    while ready:
        key = ready.popleft()
        ordered.append(key)
        for dependent in sorted(dependents[key]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if len(ordered) != len(nodes_by_key):
        raise ValueError("workflow plan contains a cycle")
    return ordered


def _freeze_node(node: _Node) -> dict[str, Any]:
    return {
        "key": node.key,
        "job_type": node.job_type,
        "job_params": node.job_params,
        "depends_on": sorted(node.depends_on),
        "required": node.required,
        "weight": node.weight,
    }


def _assert_json_serializable(value: dict[str, Any]) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("frozen workflow plan must be JSON serializable") from exc
