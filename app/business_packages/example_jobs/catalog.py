from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExampleWorkflowModeSpec:
    mode: str
    primitive: str
    expected_node_keys: tuple[str, ...]
    expected_result_kinds: dict[str, str]

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "primitive": self.primitive,
            "expected_node_count": len(self.expected_node_keys),
            "expected_node_keys": list(self.expected_node_keys),
            "expected_result_kinds": dict(self.expected_result_kinds),
        }


_EXAMPLE_WORKFLOW_MODE_SPECS: tuple[ExampleWorkflowModeSpec, ...] = (
    ExampleWorkflowModeSpec("single", "task", ("only",), {"only": "sleep"}),
    ExampleWorkflowModeSpec("chain", "chain", ("a", "b", "c"), {"a": "sleep", "b": "sleep", "c": "sleep"}),
    ExampleWorkflowModeSpec("group", "group", ("a", "b", "c"), {"a": "sleep", "b": "sleep", "c": "sleep"}),
    ExampleWorkflowModeSpec("chord", "chord", ("a", "b", "join"), {"a": "sleep", "b": "sleep", "join": "sleep"}),
    ExampleWorkflowModeSpec("map", "map", ("item.0", "item.1"), {"item.0": "sleep", "item.1": "sleep"}),
    ExampleWorkflowModeSpec("starmap", "starmap", ("pair.0", "pair.1"), {"pair.0": "pair", "pair.1": "pair"}),
    ExampleWorkflowModeSpec(
        "chunks",
        "chunks",
        ("chunk.0", "chunk.1", "chunk.2"),
        {
            "chunk.0": "collect",
            "chunk.1": "collect",
            "chunk.2": "collect",
        },
    ),
)


def all_example_workflow_mode_specs() -> tuple[dict[str, Any], ...]:
    return tuple(spec.snapshot() for spec in _EXAMPLE_WORKFLOW_MODE_SPECS)
