import ast
from pathlib import Path

from fastapi.routing import APIRoute
import pytest

from app.api.operations import all_operation_ids, all_operation_specs
from app.core.error_registry import all_error_reasons
from app.core import prompt_templates
from app.core.registry_checks import validate_all_registries, validate_job_type_registry
from app.main import app
from app.jobs.base import JobTypeSpec, PromptSpec
from app.jobs.types.register import register_all_job_types
from app.jobs import registry as job_registry


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"


def _literal_error_reasons() -> set[str]:
    reasons: set[str] = set()
    error_call_names = {"AppError", "ValidationAppError", "NotFoundAppError", "InternalAppError"}
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name in error_call_names and node.args and isinstance(node.args[0], ast.Constant):
                    if isinstance(node.args[0].value, str):
                        reasons.add(node.args[0].value)
                for keyword in node.keywords:
                    if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant):
                        if isinstance(keyword.value.value, str):
                            reasons.add(keyword.value.value)
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=False):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "code"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        reasons.add(value.value)
    return reasons


def test_all_business_routes_have_registered_operation_ids():
    route_operation_ids = {
        route.operation_id
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.include_in_schema
        and route.path not in {"/health", "/healthz"}
    }

    assert route_operation_ids == all_operation_ids()


def test_operation_registry_references_registered_errors():
    known_errors = all_error_reasons()

    for spec in all_operation_specs().values():
        assert set(spec.error_codes) <= known_errors
        assert spec.operation_id
        assert spec.response_data_schema


def test_error_registry_covers_produced_error_reasons():
    assert _literal_error_reasons() <= all_error_reasons()


def test_job_type_registry_exposes_required_metadata():
    register_all_job_types()
    specs = job_registry.all_job_type_specs()

    assert "job_test_add" in specs
    spec = specs["job_test_add"]
    assert spec.params_schema == "JobTestAddParams"
    assert spec.runtime_fields_schema == "JobTestAddRuntimeFields"
    assert spec.canonical_result_schema == "JobTestAddResult"
    assert spec.public_result_schema == "JobTestAddResult"
    assert spec.allow_callback is True
    assert spec.execution_mode == "custom_executor"
    assert spec.platform_retry_policy == "no_platform_retry"
    assert spec.side_effect_policy == "none"
    assert spec.error_codes <= all_error_reasons()
    assert spec.prompt_specs == ()

    assert "arithmetic" in specs
    arithmetic_spec = specs["arithmetic"]
    assert arithmetic_spec.params_schema == "ArithmeticParams"
    assert arithmetic_spec.runtime_fields_schema == "ArithmeticRuntimeFields"
    assert arithmetic_spec.canonical_result_schema == "ArithmeticResult"
    assert arithmetic_spec.public_result_schema == "ArithmeticResult"
    assert arithmetic_spec.allow_callback is True
    assert arithmetic_spec.execution_mode == "custom_executor"
    assert arithmetic_spec.platform_retry_policy == "no_platform_retry"
    assert arithmetic_spec.side_effect_policy == "none"
    assert arithmetic_spec.error_codes <= all_error_reasons()
    assert arithmetic_spec.prompt_specs == ()

    assert "job_test_echo" in specs
    echo_spec = specs["job_test_echo"]
    assert echo_spec.params_schema == "JobTestEchoParams"
    assert echo_spec.runtime_fields_schema == "JobTestEchoRuntimeFields"
    assert echo_spec.canonical_result_schema == "JobTestEchoResult"
    assert echo_spec.public_result_schema == "JobTestEchoResult"
    assert echo_spec.allow_callback is True
    assert echo_spec.execution_mode == "custom_executor"
    assert echo_spec.platform_retry_policy == "no_platform_retry"
    assert echo_spec.side_effect_policy == "none"
    assert echo_spec.error_codes <= all_error_reasons()
    assert echo_spec.prompt_specs == ()

    assert "job_real_llm_echo" in specs
    real_llm_spec = specs["job_real_llm_echo"]
    assert real_llm_spec.params_schema == "JobRealLlmEchoParams"
    assert real_llm_spec.runtime_fields_schema == "JobRealLlmEchoRuntimeFields"
    assert real_llm_spec.canonical_result_schema == "JobRealLlmEchoResult"
    assert real_llm_spec.public_result_schema == "JobRealLlmEchoResult"
    assert real_llm_spec.allow_callback is False
    assert real_llm_spec.execution_mode == "builtin_llm_text_runtime"
    assert real_llm_spec.platform_retry_policy == "no_platform_retry"
    assert real_llm_spec.side_effect_policy == "none"
    assert real_llm_spec.error_codes <= all_error_reasons()
    assert real_llm_spec.prompt_specs == (
        PromptSpec(
            step_name="calling_model",
            runtime_field="prompt_payload",
            prompt_ref="job_real_llm_echo.calling_model",
            output_schema_ref="JobRealLlmEchoResult",
        ),
    )

    assert "job_real_llm_double_echo" in specs
    double_llm_spec = specs["job_real_llm_double_echo"]
    assert double_llm_spec.params_schema == "JobRealLlmDoubleEchoParams"
    assert double_llm_spec.runtime_fields_schema == "JobRealLlmDoubleEchoRuntimeFields"
    assert double_llm_spec.canonical_result_schema == "JobRealLlmDoubleEchoResult"
    assert double_llm_spec.public_result_schema == "JobRealLlmDoubleEchoResult"
    assert double_llm_spec.allow_callback is False
    assert double_llm_spec.execution_mode == "custom_executor"
    assert double_llm_spec.platform_retry_policy == "no_platform_retry"
    assert double_llm_spec.side_effect_policy == "none"
    assert double_llm_spec.error_codes <= all_error_reasons()
    assert double_llm_spec.prompt_specs == (
        PromptSpec(
            step_name="first_llm_call",
            runtime_field="first_prompt_payload",
            prompt_ref="job_real_llm_double_echo.first",
            output_schema_ref="JobRealLlmDoubleEchoResult",
        ),
        PromptSpec(
            step_name="second_llm_call",
            runtime_field="second_prompt_payload",
            prompt_ref="job_real_llm_double_echo.second",
            output_schema_ref="JobRealLlmDoubleEchoResult",
        ),
    )


def _job_type_spec(**overrides) -> JobTypeSpec:
    values = {
        "job_type": "job_test_add",
        "execution_mode": "custom_executor",
        "platform_retry_policy": "no_platform_retry",
        "side_effect_policy": "none",
        "params_schema": "JobTestAddParams",
        "runtime_fields_schema": "JobTestAddRuntimeFields",
        "canonical_result_schema": "JobTestAddResult",
        "public_result_schema": "JobTestAddResult",
        "callback_envelope_schema": "CallbackEnvelope[JobEnvelope]",
        "allow_callback": True,
        "large_artifact_keys": frozenset(),
        "error_codes": frozenset({"INVALID_INPUT"}),
        "log_events": (),
        "max_attempts": 1,
        "timeout_seconds": 60,
    }
    values.update(overrides)
    return JobTypeSpec(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"execution_mode": "sync"}, "execution_mode"),
        ({"side_effect_policy": "unknown"}, "side_effect_policy"),
        ({"platform_retry_policy": ""}, "platform_retry_policy"),
        ({"platform_retry_policy": "retry_everything"}, "platform_retry_policy"),
        ({"max_attempts": 0}, "max_attempts"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"max_attempts": 2, "platform_retry_policy": "no_platform_retry"}, "platform_retry_policy"),
    ],
)
def test_validate_job_type_registry_rejects_invalid_phase3_metadata(monkeypatch, overrides, message):
    monkeypatch.setattr(job_registry, "all_job_type_specs", lambda: {"job_test_add": _job_type_spec(**overrides)})

    with pytest.raises(ValueError, match=message):
        validate_job_type_registry()


def _prompt_config(prompt_ref: str = "prompt.ref", output_schema_ref: str = "JobTestAddResult") -> dict:
    return {
        "version": "test",
        "job_types": {},
        "prompts": {
            prompt_ref: {
                "name": "Prompt",
                "description": "Prompt description",
                "output_schema_ref": output_schema_ref,
                "prompt_blocks": {
                    "user": {
                        "role": "user",
                        "label": "User",
                        "content": "",
                    }
                },
            }
        },
    }


def test_validate_job_type_registry_rejects_missing_prompt_ref(monkeypatch):
    monkeypatch.setattr(
        job_registry,
        "all_job_type_specs",
        lambda: {
            "job_test_add": _job_type_spec(
                prompt_specs=(
                    PromptSpec(
                        step_name="calling_model",
                        runtime_field="prompt_payload",
                        prompt_ref="missing.prompt",
                        output_schema_ref="JobTestAddResult",
                    ),
                )
            )
        },
    )
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda: _prompt_config())

    with pytest.raises(ValueError, match="unknown prompt_ref"):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_prompt_output_schema_mismatch(monkeypatch):
    monkeypatch.setattr(
        job_registry,
        "all_job_type_specs",
        lambda: {
            "job_test_add": _job_type_spec(
                prompt_specs=(
                    PromptSpec(
                        step_name="calling_model",
                        runtime_field="prompt_payload",
                        prompt_ref="prompt.ref",
                        output_schema_ref="JobTestAddResult",
                    ),
                )
            )
        },
    )
    monkeypatch.setattr(
        prompt_templates,
        "_load_prompt_config",
        lambda: _prompt_config(output_schema_ref="JobTestEchoResult"),
    )

    with pytest.raises(ValueError, match="output_schema_ref mismatch"):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_builtin_llm_without_prompt_spec(monkeypatch):
    monkeypatch.setattr(
        job_registry,
        "all_job_type_specs",
        lambda: {
            "job_test_add": _job_type_spec(
                execution_mode="builtin_llm_text_runtime",
                prompt_specs=(),
            )
        },
    )
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda: {"version": "test", "job_types": {}})

    with pytest.raises(ValueError, match="requires one prompt_spec"):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_bad_prompt_spec_field_type(monkeypatch):
    monkeypatch.setattr(
        job_registry,
        "all_job_type_specs",
        lambda: {
            "job_test_add": _job_type_spec(
                prompt_specs=(
                    PromptSpec(
                        step_name=None,  # type: ignore[arg-type]
                        runtime_field="prompt_payload",
                        prompt_ref="prompt.ref",
                        output_schema_ref="JobTestAddResult",
                    ),
                )
            )
        },
    )
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda: _prompt_config())

    with pytest.raises(ValueError, match="step_name"):
        validate_job_type_registry()


def test_registry_consistency_check_passes():
    register_all_job_types()
    validate_all_registries(app)


def test_create_app_validates_registries_on_startup(monkeypatch):
    from app import main

    calls = {}

    monkeypatch.setattr(main, "bootstrap_runtime", lambda: calls.setdefault("bootstrap", True))
    monkeypatch.setattr(main, "install_openapi", lambda _app: calls.setdefault("openapi", True))
    monkeypatch.setattr(main, "install_middlewares", lambda _app: calls.setdefault("middlewares", True))
    monkeypatch.setattr(main, "install_exception_handlers", lambda _app: calls.setdefault("handlers", True))
    monkeypatch.setattr(main, "include_routes", lambda _app: calls.setdefault("routes", True))

    def fake_validate_all_registries(application):
        calls["registry_app"] = application

    monkeypatch.setattr(main, "validate_all_registries", fake_validate_all_registries)

    created = main.create_app()

    assert calls["bootstrap"] is True
    assert calls["registry_app"] is created


def test_worker_registration_validates_job_type_registry(monkeypatch):
    from app.tasks import jobs as task_jobs

    calls = {}

    monkeypatch.setattr("app.jobs.types.register.register_all_job_types", lambda: calls.setdefault("jobs", True))
    monkeypatch.setattr("app.core.registry_checks.validate_job_type_registry", lambda: calls.setdefault("registry", True))
    monkeypatch.setattr("app.core.model_registry.validate_model_catalog", lambda: calls.setdefault("models", True))

    task_jobs._ensure_workflows_registered()

    assert calls == {"jobs": True, "registry": True, "models": True}


def test_register_all_job_types_reregisters_after_clear():
    register_all_job_types()
    job_registry.clear_for_tests()

    register_all_job_types()

    assert {
        "arithmetic",
        "job_test_add",
        "job_test_echo",
        "job_real_llm_echo",
        "job_real_llm_double_echo",
    } <= set(job_registry.all_job_types())


def test_worker_startup_validates_model_catalog(monkeypatch):
    from app.tasks import jobs as task_jobs

    called = {}

    def fake_validate_model_catalog():
        called["model_catalog"] = True

    monkeypatch.setattr("app.core.model_registry.validate_model_catalog", fake_validate_model_catalog)

    task_jobs._ensure_workflows_registered()

    assert called == {"model_catalog": True}
