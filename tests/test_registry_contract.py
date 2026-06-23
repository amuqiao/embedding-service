import ast
from pathlib import Path

from fastapi.routing import APIRoute

from app.api.operations import all_operation_ids, all_operation_specs
from app.core.error_registry import all_error_reasons
from app.core.registry_checks import validate_all_registries
from app.main import app
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
    assert spec.error_codes <= all_error_reasons()

    assert "arithmetic" in specs
    arithmetic_spec = specs["arithmetic"]
    assert arithmetic_spec.params_schema == "ArithmeticParams"
    assert arithmetic_spec.runtime_fields_schema == "ArithmeticRuntimeFields"
    assert arithmetic_spec.canonical_result_schema == "ArithmeticResult"
    assert arithmetic_spec.public_result_schema == "ArithmeticResult"
    assert arithmetic_spec.allow_callback is True
    assert arithmetic_spec.error_codes <= all_error_reasons()

    assert "job_real_llm_echo" in specs
    real_llm_spec = specs["job_real_llm_echo"]
    assert real_llm_spec.params_schema == "JobRealLlmEchoParams"
    assert real_llm_spec.runtime_fields_schema == "JobRealLlmEchoRuntimeFields"
    assert real_llm_spec.canonical_result_schema == "JobRealLlmEchoResult"
    assert real_llm_spec.public_result_schema == "JobRealLlmEchoResult"
    assert real_llm_spec.allow_callback is False
    assert real_llm_spec.error_codes <= all_error_reasons()

    assert "job_real_llm_double_echo" in specs
    double_llm_spec = specs["job_real_llm_double_echo"]
    assert double_llm_spec.params_schema == "JobRealLlmDoubleEchoParams"
    assert double_llm_spec.runtime_fields_schema == "JobRealLlmDoubleEchoRuntimeFields"
    assert double_llm_spec.canonical_result_schema == "JobRealLlmDoubleEchoResult"
    assert double_llm_spec.public_result_schema == "JobRealLlmDoubleEchoResult"
    assert double_llm_spec.allow_callback is False
    assert double_llm_spec.error_codes <= all_error_reasons()


def test_registry_consistency_check_passes():
    register_all_job_types()
    validate_all_registries(app)


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
