from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


FAILURE_POLICIES = frozenset({"fail_fast", "allow_partial"})


@dataclass(frozen=True)
class Task:
    key: str
    job_type: str
    job_params: Mapping[str, Any]
    depends_on: tuple[str, ...] = ()
    required: bool = True
    weight: int = 1


@dataclass(frozen=True)
class Chain:
    steps: tuple["WorkflowExpr", ...]


@dataclass(frozen=True)
class Group:
    members: tuple["WorkflowExpr", ...]


@dataclass(frozen=True)
class Chord:
    header: "WorkflowExpr"
    body: "WorkflowExpr"


@dataclass(frozen=True)
class Map:
    key_prefix: str
    job_type: str
    items: tuple[Any, ...]
    param_name: str = "item"
    static_job_params: Mapping[str, Any] = field(default_factory=dict)
    required: bool = True
    weight: int = 1


@dataclass(frozen=True)
class StarMap:
    key_prefix: str
    job_type: str
    items: tuple[Any, ...]
    arg_names: tuple[str, ...]
    static_job_params: Mapping[str, Any] = field(default_factory=dict)
    required: bool = True
    weight: int = 1


@dataclass(frozen=True)
class Chunks:
    key_prefix: str
    job_type: str
    items: tuple[Any, ...]
    chunk_size: int
    param_name: str = "items"
    static_job_params: Mapping[str, Any] = field(default_factory=dict)
    required: bool = True
    weight: int = 1


WorkflowExpr = Task | Chain | Group | Chord | Map | StarMap | Chunks


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_type: str
    root: WorkflowExpr
    workflow_version: int = 1
    failure_policy: str = "fail_fast"
    max_nodes: int = 100


def task(
    key: str,
    job_type: str,
    job_params: Mapping[str, Any],
    *,
    depends_on: Sequence[str] = (),
    required: bool = True,
    weight: int = 1,
) -> Task:
    return Task(
        key=key,
        job_type=job_type,
        job_params=job_params,
        depends_on=tuple(depends_on),
        required=required,
        weight=weight,
    )


def chain(*steps: WorkflowExpr) -> Chain:
    return Chain(steps=tuple(steps))


def group(*members: WorkflowExpr) -> Group:
    return Group(members=tuple(members))


def chord(header: WorkflowExpr, body: WorkflowExpr) -> Chord:
    return Chord(header=header, body=body)


def map_items(
    key_prefix: str,
    job_type: str,
    items: Sequence[Any],
    *,
    param_name: str = "item",
    static_job_params: Mapping[str, Any] | None = None,
    required: bool = True,
    weight: int = 1,
) -> Map:
    return Map(
        key_prefix=key_prefix,
        job_type=job_type,
        items=tuple(items),
        param_name=param_name,
        static_job_params=static_job_params or {},
        required=required,
        weight=weight,
    )


def starmap_items(
    key_prefix: str,
    job_type: str,
    items: Sequence[Sequence[Any] | Mapping[str, Any]],
    *,
    arg_names: Sequence[str],
    static_job_params: Mapping[str, Any] | None = None,
    required: bool = True,
    weight: int = 1,
) -> StarMap:
    return StarMap(
        key_prefix=key_prefix,
        job_type=job_type,
        items=tuple(items),
        arg_names=tuple(arg_names),
        static_job_params=static_job_params or {},
        required=required,
        weight=weight,
    )


def chunks(
    key_prefix: str,
    job_type: str,
    items: Sequence[Any],
    *,
    chunk_size: int,
    param_name: str = "items",
    static_job_params: Mapping[str, Any] | None = None,
    required: bool = True,
    weight: int = 1,
) -> Chunks:
    return Chunks(
        key_prefix=key_prefix,
        job_type=job_type,
        items=tuple(items),
        chunk_size=chunk_size,
        param_name=param_name,
        static_job_params=static_job_params or {},
        required=required,
        weight=weight,
    )


@dataclass(frozen=True)
class WorkflowPrimitiveSpec:
    primitive: str
    expr_type: str
    builder: str
    semantic_key: str
    semantics: str

    def snapshot(self) -> dict[str, str]:
        return {
            "primitive": self.primitive,
            "expr_type": self.expr_type,
            "builder": self.builder,
            "semantic_key": self.semantic_key,
            "semantics": self.semantics,
        }


_WORKFLOW_PRIMITIVE_SPECS: tuple[WorkflowPrimitiveSpec, ...] = (
    WorkflowPrimitiveSpec(
        primitive="task",
        expr_type="Task",
        builder="task",
        semantic_key="single_node",
        semantics="single child node",
    ),
    WorkflowPrimitiveSpec(
        primitive="chain",
        expr_type="Chain",
        builder="chain",
        semantic_key="linear_dependency",
        semantics="next roots depend on previous leaves",
    ),
    WorkflowPrimitiveSpec(
        primitive="group",
        expr_type="Group",
        builder="group",
        semantic_key="parallel_fanout",
        semantics="members become parallel ready nodes",
    ),
    WorkflowPrimitiveSpec(
        primitive="chord",
        expr_type="Chord",
        builder="chord",
        semantic_key="fanout_join",
        semantics="body roots depend on header leaves",
    ),
    WorkflowPrimitiveSpec(
        primitive="map",
        expr_type="Map",
        builder="map_items",
        semantic_key="map_expand",
        semantics="items expand into one-param child nodes",
    ),
    WorkflowPrimitiveSpec(
        primitive="starmap",
        expr_type="StarMap",
        builder="starmap_items",
        semantic_key="starmap_expand",
        semantics="items expand into unpacked multi-param child nodes",
    ),
    WorkflowPrimitiveSpec(
        primitive="chunks",
        expr_type="Chunks",
        builder="chunks",
        semantic_key="chunk_expand",
        semantics="items split by chunk_size into child nodes",
    ),
)


def all_workflow_primitive_specs() -> tuple[dict[str, str], ...]:
    return tuple(spec.snapshot() for spec in _WORKFLOW_PRIMITIVE_SPECS)
