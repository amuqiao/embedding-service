from __future__ import annotations

import pytest

from app.capabilities.definitions import CapabilityDefinition
from app.capabilities import registry as capability_registry
from app.core.registry_checks import validate_capability_tool_registry
from app.jobs.base import JobTypeSpec
from app.jobs import registry as job_registry
from app.tools.definitions import ToolDefinition
from app.tools import registry as tool_registry


def _entrypoint():
    return None


def _failing_entrypoint():
    raise RuntimeError("validator failed")


def _job_type_spec(*, allowed_capability_refs: frozenset[str] = frozenset()) -> JobTypeSpec:
    return JobTypeSpec(
        job_type="example_pair",
        visibility="demo",
        role="root_or_leaf",
        execution_mode="custom_executor",
        retry_policy={
            "workflow_orchestration": {
                "domain": "workflow_orchestration",
                "max_attempts": 3,
                "retry_delay_seconds": 5,
                "backoff_kind": "fixed",
                "retryable_error_codes": ["JOB_STATE_TRANSITION_CONFLICT"],
            },
            "business_execution": {
                "domain": "business_execution",
                "max_attempts": 1,
                "retry_delay_seconds": None,
                "backoff_kind": "none",
                "retryable_error_codes": [],
            },
        },
        side_effect_policy="none",
        params_schema="ExamplePairParams",
        runtime_fields_schema="ExamplePairRuntimeFields",
        canonical_result_schema="ExamplePairResult",
        public_result_schema="ExamplePairResult",
        callback_envelope_schema="CallbackEnvelope[JobEnvelope]",
        allow_callback=True,
        result_snapshot_statuses=frozenset(),
        large_artifact_keys=frozenset(),
        error_codes=frozenset({"INVALID_INPUT"}),
        log_events=(),
        timeout_seconds=60,
        allowed_capability_refs=allowed_capability_refs,
    )


def _tool(**overrides) -> ToolDefinition:
    values = {
        "tool_ref": "media_probe:1",
        "kind": "local_function",
        "entrypoint_path": "tests.test_capability_tool_registry:_entrypoint",
        "request_schema": "ExamplePairParams",
        "result_schema": "ExamplePairResult",
        "required_settings": ("job.oss_input_max_bytes",),
        "startup_validators": ("tests.test_capability_tool_registry:_entrypoint",),
        "error_codes": frozenset({"INVALID_INPUT"}),
        "log_events": (),
    }
    values.update(overrides)
    return ToolDefinition(**values)


def _capability(**overrides) -> CapabilityDefinition:
    values = {
        "capability_ref": "media.input:1",
        "plan_schema": "ExamplePairParams",
        "result_schema": "ExamplePairResult",
        "service_entrypoint": "tests.test_capability_tool_registry:_entrypoint",
        "allowed_tool_refs": frozenset({"media_probe:1"}),
        "error_codes": frozenset({"INVALID_INPUT"}),
        "log_events": (),
    }
    values.update(overrides)
    return CapabilityDefinition(**values)


@pytest.fixture(autouse=True)
def clear_registries(monkeypatch):
    capability_registry.clear_for_tests()
    tool_registry.clear_for_tests()
    monkeypatch.setattr(job_registry, "all_job_type_specs", lambda: {})
    monkeypatch.setattr(job_registry, "enabled_job_type_specs", lambda: {})
    yield
    capability_registry.clear_for_tests()
    tool_registry.clear_for_tests()


def test_capability_and_tool_registries_validate_graph(monkeypatch):
    tool_registry.register(_tool())
    capability_registry.register(_capability())
    specs = {"example_pair": _job_type_spec(allowed_capability_refs=frozenset({"media.input:1"}))}
    monkeypatch.setattr(job_registry, "all_job_type_specs", lambda: specs)
    monkeypatch.setattr(job_registry, "enabled_job_type_specs", lambda: specs)

    validate_capability_tool_registry()


def test_capability_registry_freeze_is_idempotent_and_rejects_changes():
    definition = _capability()
    capability_registry.register(definition)
    capability_registry.freeze()

    assert capability_registry.register(definition) == definition
    with pytest.raises(RuntimeError, match="frozen"):
        capability_registry.register(_capability(result_schema="ExampleSleepResult"))


def test_tool_registry_freeze_is_idempotent_and_rejects_changes():
    definition = _tool()
    tool_registry.register(definition)
    tool_registry.freeze()

    assert tool_registry.register(definition) == definition
    with pytest.raises(RuntimeError, match="frozen"):
        tool_registry.register(_tool(kind="cli"))


def test_capability_tool_registry_rejects_unknown_tool_ref():
    capability_registry.register(_capability(allowed_tool_refs=frozenset({"missing_tool:1"})))

    with pytest.raises(ValueError, match="unknown tool_refs"):
        validate_capability_tool_registry()


def test_capability_tool_registry_rejects_missing_entrypoint():
    tool_registry.register(_tool(entrypoint_path="tests.test_capability_tool_registry:missing"))

    with pytest.raises(ValueError, match="entrypoint_path"):
        validate_capability_tool_registry()


def test_capability_tool_registry_executes_startup_validators():
    tool_registry.register(_tool(startup_validators=("tests.test_capability_tool_registry:_failing_entrypoint",)))

    with pytest.raises(ValueError, match="startup validator failed"):
        validate_capability_tool_registry()


def test_capability_tool_registry_rejects_unknown_setting():
    tool_registry.register(_tool(required_settings=("job.missing_setting",)))

    with pytest.raises(ValueError, match="unknown settings"):
        validate_capability_tool_registry()


def test_capability_tool_registry_rejects_unknown_schema():
    tool_registry.register(_tool())
    capability_registry.register(_capability(plan_schema="MissingPlan"))

    with pytest.raises(ValueError, match="unknown schemas"):
        validate_capability_tool_registry()


def test_capability_tool_registry_rejects_unknown_error():
    tool_registry.register(_tool(error_codes=frozenset({"MISSING_ERROR"})))

    with pytest.raises(ValueError, match="unknown errors"):
        validate_capability_tool_registry()
