import json

import pytest

from app.jobs import registry as job_registry
from app.workflows import (
    WorkflowDefinition,
    WorkflowSpec,
    chain,
    chord,
    chunks,
    compile_registered_workflow,
    compile_workflow,
    group,
    map_items,
    register,
    starmap_items,
    task,
)
from app.workflows import registry as workflow_registry
from app.jobs.types.register import register_all_job_types


def _nodes_by_key(plan):
    return {node["key"]: node for node in plan["nodes"]}


def test_chain_compiles_to_linear_dependencies():
    plan = compile_workflow(
        WorkflowSpec(
            workflow_type="test.chain",
            root=chain(
                task("a", "job_test_echo", {"value": "a"}),
                task("b", "job_test_echo", {"value": "b"}),
                task("c", "job_test_echo", {"value": "c"}),
            ),
        )
    )

    nodes = _nodes_by_key(plan)
    assert plan["kind"] == "dag_lite"
    assert plan["node_count"] == 3
    assert nodes["a"]["depends_on"] == []
    assert nodes["b"]["depends_on"] == ["a"]
    assert nodes["c"]["depends_on"] == ["b"]
    json.dumps(plan, ensure_ascii=False, sort_keys=True)


def test_group_compiles_to_parallel_ready_nodes():
    plan = compile_workflow(
        WorkflowSpec(
            workflow_type="test.group",
            root=group(
                task("a", "job_test_echo", {"value": "a"}),
                task("b", "job_test_echo", {"value": "b"}),
                task("c", "job_test_echo", {"value": "c"}),
            ),
        )
    )

    assert [node["depends_on"] for node in plan["nodes"]] == [[], [], []]


def test_chord_compiles_reducer_after_header_group():
    plan = compile_workflow(
        WorkflowSpec(
            workflow_type="test.chord",
            root=chord(
                group(
                    task("a", "job_test_echo", {"value": "a"}),
                    task("b", "job_test_echo", {"value": "b"}),
                ),
                task("join", "job_test_echo", {"mode": "join"}),
            ),
        )
    )

    nodes = _nodes_by_key(plan)
    assert nodes["join"]["depends_on"] == ["a", "b"]


def test_map_starmap_and_chunks_expand_to_stable_node_inputs():
    mapped = compile_workflow(
        WorkflowSpec(
            workflow_type="test.map",
            root=map_items("item", "job_test_echo", ["one", "two"], param_name="value"),
        )
    )
    mapped_nodes = _nodes_by_key(mapped)
    assert mapped_nodes["item.0"]["job_params"] == {"value": "one"}
    assert mapped_nodes["item.1"]["job_params"] == {"value": "two"}

    starred = compile_workflow(
        WorkflowSpec(
            workflow_type="test.starmap",
            root=starmap_items("pair", "job_test_add", [(1, 2), {"a": 3, "b": 4}], arg_names=("a", "b")),
        )
    )
    starred_nodes = _nodes_by_key(starred)
    assert starred_nodes["pair.0"]["job_params"] == {"a": 1, "b": 2}
    assert starred_nodes["pair.1"]["job_params"] == {"a": 3, "b": 4}

    chunked = compile_workflow(
        WorkflowSpec(
            workflow_type="test.chunks",
            root=chunks("chunk", "job_test_echo", [1, 2, 3, 4, 5], chunk_size=2),
        )
    )
    chunked_nodes = _nodes_by_key(chunked)
    assert chunked_nodes["chunk.0"]["job_params"] == {"items": [1, 2]}
    assert chunked_nodes["chunk.1"]["job_params"] == {"items": [3, 4]}
    assert chunked_nodes["chunk.2"]["job_params"] == {"items": [5]}


def test_compiler_rejects_duplicate_keys_unknown_dependencies_cycles_and_fanout():
    with pytest.raises(ValueError, match="duplicate workflow node key"):
        compile_workflow(
            WorkflowSpec(
                workflow_type="test.duplicate",
                root=group(
                    task("same", "job_test_echo", {"value": 1}),
                    task("same", "job_test_echo", {"value": 2}),
                ),
            )
        )

    with pytest.raises(ValueError, match="depends on unknown node"):
        compile_workflow(
            WorkflowSpec(
                workflow_type="test.unknown-dep",
                root=task("a", "job_test_echo", {"value": 1}, depends_on=("missing",)),
            )
        )

    with pytest.raises(ValueError, match="contains a cycle"):
        compile_workflow(
            WorkflowSpec(
                workflow_type="test.cycle",
                root=group(
                    task("a", "job_test_echo", {"value": 1}, depends_on=("b",)),
                    task("b", "job_test_echo", {"value": 2}, depends_on=("a",)),
                ),
            )
        )

    with pytest.raises(ValueError, match="exceeds max_nodes"):
        compile_workflow(
            WorkflowSpec(
                workflow_type="test.limit",
                root=map_items("item", "job_test_echo", [1, 2, 3]),
                max_nodes=2,
            )
        )


def test_compiler_rejects_invalid_starmap_and_chunk_specs():
    with pytest.raises(ValueError, match="arg count mismatch"):
        compile_workflow(
            WorkflowSpec(
                workflow_type="test.bad-starmap",
                root=starmap_items("pair", "job_test_add", [(1, 2, 3)], arg_names=("a", "b")),
            )
        )

    with pytest.raises(ValueError, match="chunk_size"):
        compile_workflow(
            WorkflowSpec(
                workflow_type="test.bad-chunks",
                root=chunks("chunk", "job_test_echo", [1, 2], chunk_size=0),
            )
        )


def test_compiler_rejects_non_recoverable_json_payloads():
    with pytest.raises(ValueError, match="non-string object key"):
        compile_workflow(
            WorkflowSpec(
                workflow_type="test.bad-json-key",
                root=task("a", "job_test_echo", {"nested": {1: "value"}}),
            )
        )

    with pytest.raises(ValueError, match="non-finite number"):
        compile_workflow(
            WorkflowSpec(
                workflow_type="test.bad-json-number",
                root=task("a", "job_test_echo", {"value": float("nan")}),
            )
        )


def test_workflow_registry_compiles_registered_definition():
    workflow_registry.clear_for_tests()


def test_registered_workflow_mode_job_types_compile_to_dag_lite_plans():
    register_all_job_types()

    expected = {
        "single": ("only",),
        "chain": ("a", "b", "c"),
        "group": ("a", "b", "c"),
        "chord": ("a", "b", "join"),
        "map": ("item.0", "item.1"),
        "starmap": ("pair.0", "pair.1"),
        "chunks": ("chunk.0", "chunk.1", "chunk.2"),
    }
    for mode, node_keys in expected.items():
        plan = compile_registered_workflow(
            "job_test_workflow",
            {"mode": mode, "label": mode, "sleep_seconds": 3},
        )
        nodes = _nodes_by_key(plan)
        assert plan["kind"] == "dag_lite"
        assert plan["workflow_type"] == "job_test_workflow"
        assert tuple(nodes) == node_keys
        assert all(node["job_params"].get("sleep_seconds") == 3 for node in nodes.values())
    definition = WorkflowDefinition(
        workflow_type="test.workflow",
        build=lambda params: chain(
            task("first", "job_test_echo", {"value": params["value"]}),
            task("second", "job_test_echo", {"value": "done"}),
        ),
        max_nodes=5,
    )
    register(definition)
    register(definition)

    plan = compile_registered_workflow("test.workflow", {"value": "hello"})

    nodes = _nodes_by_key(plan)
    assert nodes["first"]["job_params"] == {"value": "hello"}
    assert nodes["second"]["depends_on"] == ["first"]

    with pytest.raises(ValueError, match="duplicate workflow_type"):
        register(
            WorkflowDefinition(
                workflow_type="test.workflow",
                build=lambda _params: task("other", "job_test_echo", {"value": "other"}),
            )
        )
    workflow_registry.clear_for_tests()


def test_test_workflow_sleep_normalization_preserves_legacy_payload_shape():
    register_all_job_types()

    workflow = job_registry.get("job_test_workflow")
    collect = job_registry.get("job_test_collect")

    assert workflow.normalize_job_params({"mode": "group", "label": "x"}) == {
        "mode": "group",
        "label": "x",
    }
    assert workflow.normalize_job_params({"mode": "group", "label": "x", "sleep_seconds": 0}) == {
        "mode": "group",
        "label": "x",
        "sleep_seconds": 0,
    }
    assert collect.normalize_job_params({"items": ["a", "b"]}) == {"items": ["a", "b"]}
    assert collect.normalize_job_params({"items": ["a", "b"], "sleep_seconds": 0}) == {
        "items": ["a", "b"],
        "sleep_seconds": 0,
    }
