import ast
from pathlib import Path
from types import SimpleNamespace

from fastapi.routing import APIRoute
import pytest

from app.api.operations import all_operation_ids, all_operation_specs
from app.api.operations import OperationSpec
from app.core.error_registry import (
    ErrorSpec,
    all_error_reasons,
    all_error_specs,
    error_registry_is_frozen,
    register_error_specs,
)
from app.core.logging import LogEvent
from app.core import prompt_templates
from app.core.registries.refs import parse_versioned_ref, require_capability_ref, require_tool_ref
from app.core.registry_checks import (
    validate_all_registries,
    validate_capability_tool_registry,
    validate_error_registry,
    validate_job_type_registry,
    validate_operation_registry,
    validate_workflow_registry,
)
from app.main import app
from app.jobs.base import JobExecutor, JobTypeSpec, PromptSpec
from app.jobs.types.register import register_all_job_types
from app.jobs import registry as job_registry
from app.jobs.types.poster_title_image.errors import (
    POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT,
    POSTER_TITLE_IMAGE_REFERENCE_INVALID,
)
from app.jobs.types.audio_stem_separation.errors import (
    AUDIO_STEM_INPUT_INVALID,
    AUDIO_STEM_MODEL_ASSET_MISSING,
)
from app.workflows.registry import WorkflowDefinition


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


def _string_constant_from_assignment(node: ast.Assign | ast.AnnAssign) -> tuple[str, str] | None:
    target: ast.expr
    if isinstance(node, ast.Assign):
        if len(node.targets) != 1:
            return None
        target = node.targets[0]
    else:
        target = node.target
    if not isinstance(target, ast.Name):
        return None
    if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
        return None
    return target.id, node.value.value


def _string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        assignment = _string_constant_from_assignment(node)
        if assignment is None:
            continue
        name, value = assignment
        constants[name] = value
    return constants


def _decorator_name(decorator: ast.expr) -> str:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    return ""


def _registered_job_type_names_in_source() -> set[str]:
    names: set[str] = set()
    for path in (APP_DIR / "jobs" / "types").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = _string_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(_decorator_name(decorator) == "register_job_type" for decorator in node.decorator_list):
                continue
            found_name = False
            for item in node.body:
                if not isinstance(item, (ast.Assign, ast.AnnAssign)):
                    continue
                assignment = _string_constant_from_assignment(item)
                if assignment is not None:
                    name, value = assignment
                    if name != "name":
                        continue
                    names.add(value)
                    found_name = True
                    break
                target: ast.expr
                if isinstance(item, ast.Assign):
                    if not any(isinstance(target, ast.Name) and target.id == "name" for target in item.targets):
                        continue
                else:
                    if not isinstance(item.target, ast.Name) or item.target.id != "name":
                        continue
                if isinstance(item.value, ast.Name) and item.value.id in constants:
                    names.add(constants[item.value.id])
                    found_name = True
                    break
                raise AssertionError(f"{path}:{item.lineno} register_job_type class must declare static name")
            if not found_name:
                raise AssertionError(f"{path}:{node.lineno} register_job_type class must declare static name")
    return names


def _package_name_for_path(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    else:
        parts.pop()
    return ".".join(parts)


def _resolve_import_from_module(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = _package_name_for_path(path).split(".")
    base = package_parts[: len(package_parts) - node.level + 1]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _constructor_aliases(path: Path, tree: ast.Module, name: str) -> set[str]:
    modules = {
        "ToolDefinition": "app.tools.definitions",
        "CapabilityDefinition": "app.capabilities.definitions",
    }
    aliases = {name}
    expected_module = modules[name]
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if _resolve_import_from_module(path, node) != expected_module:
            continue
        for alias in node.names:
            if alias.name == name:
                aliases.add(alias.asname or alias.name)
    return aliases


def _constructor_call_locations(name: str) -> set[str]:
    locations: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _constructor_aliases(path, tree, name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if func_name in aliases:
                locations.add(path.relative_to(ROOT).as_posix())
    return locations


def _imported_modules_from_tree(path: Path, tree: ast.Module) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(path, node)
            if module:
                imported.add(module)
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported.add(f"{module}.{alias.name}" if module else alias.name)
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _imported_modules_from_tree(path, tree)


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


def test_validate_operation_registry_rejects_unknown_log_event(monkeypatch):
    spec = OperationSpec(
        operation_id="test_operation",
        channel="http",
        method="GET",
        path="/test",
        success_status=200,
        auth_boundary="test",
        request_schema=None,
        response_data_schema="JobResponseData",
        error_codes=frozenset({"INVALID_INPUT"}),
        idempotency_key=None,
        side_effects=(),
        log_events=("not_registered",),
    )
    monkeypatch.setattr("app.core.registry_checks.all_operation_specs", lambda: {"test_operation": spec})

    with pytest.raises(ValueError, match="unknown log events"):
        validate_operation_registry()


def test_validate_operation_registry_rejects_internal_errors(monkeypatch):
    spec = OperationSpec(
        operation_id="test_operation",
        channel="http",
        method="GET",
        path="/test",
        success_status=200,
        auth_boundary="test",
        request_schema=None,
        response_data_schema="JobResponseData",
        error_codes=frozenset({"TEST_INTERNAL_ERROR"}),
        idempotency_key=None,
        side_effects=(),
        log_events=(),
    )
    monkeypatch.setattr(
        "app.core.registry_checks.all_error_specs",
        lambda: {
            "TEST_INTERNAL_ERROR": ErrorSpec(
                "199998",
                "TEST_INTERNAL_ERROR",
                "test internal error",
                500,
                visibility="internal",
            )
        },
    )
    monkeypatch.setattr("app.core.registry_checks.all_operation_specs", lambda: {"test_operation": spec})

    with pytest.raises(ValueError, match="internal errors"):
        validate_operation_registry()


def test_validate_operation_registry_allows_internal_service_errors(monkeypatch):
    spec = OperationSpec(
        operation_id="test_internal_operation",
        channel="internal_service",
        method="POST",
        path="/internal/test",
        success_status=200,
        auth_boundary="internal",
        request_schema=None,
        response_data_schema="JobResponseData",
        error_codes=frozenset({"TEST_INTERNAL_ERROR"}),
        idempotency_key=None,
        side_effects=(),
        log_events=(),
    )
    monkeypatch.setattr(
        "app.core.registry_checks.all_error_specs",
        lambda: {
            "TEST_INTERNAL_ERROR": ErrorSpec(
                "199998",
                "TEST_INTERNAL_ERROR",
                "test internal error",
                500,
                visibility="internal",
            )
        },
    )
    monkeypatch.setattr("app.core.registry_checks.all_operation_specs", lambda: {"test_internal_operation": spec})

    validate_operation_registry()


def test_validate_operation_registry_rejects_unsupported_success_status(monkeypatch):
    spec = OperationSpec(
        operation_id="test_operation",
        channel="http",
        method="POST",
        path="/test",
        success_status=201,
        auth_boundary="test",
        request_schema=None,
        response_data_schema="JobResponseData",
        error_codes=frozenset({"INVALID_INPUT"}),
        idempotency_key=None,
        side_effects=(),
        log_events=(),
    )
    monkeypatch.setattr("app.core.registry_checks.all_operation_specs", lambda: {"test_operation": spec})

    with pytest.raises(ValueError, match="unsupported success_status"):
        validate_operation_registry()


def test_validate_error_registry_rejects_invalid_projection_targets(monkeypatch):
    monkeypatch.setattr(
        "app.core.registry_checks.all_error_specs",
        lambda: {
            "TEST_PUBLIC_ERROR": ErrorSpec(
                "199997",
                "TEST_PUBLIC_ERROR",
                "test public error",
                400,
            ),
            "TEST_INTERNAL_ERROR": ErrorSpec(
                "199998",
                "TEST_INTERNAL_ERROR",
                "test internal error",
                500,
                visibility="internal",
                projection_targets=frozenset({"TEST_MISSING_ERROR"}),
            ),
        },
    )

    with pytest.raises(ValueError, match="unknown projection targets"):
        validate_error_registry()


def test_validate_error_registry_rejects_internal_projection_targets(monkeypatch):
    monkeypatch.setattr(
        "app.core.registry_checks.all_error_specs",
        lambda: {
            "TEST_INTERNAL_TARGET": ErrorSpec(
                "199997",
                "TEST_INTERNAL_TARGET",
                "test internal target",
                500,
                visibility="internal",
            ),
            "TEST_INTERNAL_ERROR": ErrorSpec(
                "199998",
                "TEST_INTERNAL_ERROR",
                "test internal error",
                500,
                visibility="internal",
                projection_targets=frozenset({"TEST_INTERNAL_TARGET"}),
            ),
        },
    )

    with pytest.raises(ValueError, match="projection targets must be public"):
        validate_error_registry()


def test_error_registry_supports_frozen_idempotent_business_registration():
    specs = all_error_specs()
    poster_spec = specs[POSTER_TITLE_IMAGE_REFERENCE_INVALID]

    assert poster_spec.code == "110001"
    assert poster_spec.owner == "poster_title_image"
    assert poster_spec.visibility == "public"
    assert poster_spec.projection_targets == frozenset()
    assert error_registry_is_frozen() is True

    register_error_specs({POSTER_TITLE_IMAGE_REFERENCE_INVALID: poster_spec})
    with pytest.raises(RuntimeError, match="frozen"):
        register_error_specs(
            {
                "TEST_DYNAMIC_ERROR": ErrorSpec(
                    "199999",
                    "TEST_DYNAMIC_ERROR",
                    "test dynamic error",
                    400,
                )
            }
        )


def test_error_registry_covers_produced_error_reasons():
    assert _literal_error_reasons() <= all_error_reasons()


def test_registry_ref_parser_accepts_versioned_capability_and_tool_refs():
    capability_ref = parse_versioned_ref("media.audio_input:1", kind="capability_ref")
    tool_ref = parse_versioned_ref("ffmpeg:12", kind="tool_ref")

    assert capability_ref.key == "media.audio_input"
    assert capability_ref.version == "1"
    assert capability_ref.value == "media.audio_input:1"
    assert require_capability_ref("media.audio_input:1") == "media.audio_input:1"
    assert tool_ref.key == "ffmpeg"
    assert tool_ref.version == "12"
    assert tool_ref.value == "ffmpeg:12"
    assert require_tool_ref("ffmpeg:12") == "ffmpeg:12"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "MediaInput:1",
        "media-input:1",
        "media_input",
        "media_input:0",
        "media_input:v1",
        "media_input:1:extra",
    ],
)
def test_registry_ref_parser_rejects_invalid_refs(value):
    with pytest.raises(ValueError):
        require_capability_ref(value)


def _assert_default_retry_policy(retry_policy: dict):
    business = retry_policy["business_execution"]
    orchestration = retry_policy["workflow_orchestration"]
    assert business["domain"] == "business_execution"
    assert business["max_attempts"] == 1
    assert business["retry_delay_seconds"] is None
    assert business["backoff_kind"] == "none"
    assert business["retryable_error_codes"] == []
    assert orchestration["domain"] == "workflow_orchestration"
    assert orchestration["max_attempts"] == 3
    assert orchestration["retry_delay_seconds"] == 5
    assert orchestration["backoff_kind"] == "fixed"


def _patch_job_type_specs(monkeypatch, specs: dict[str, JobTypeSpec]) -> None:
    monkeypatch.setattr(job_registry, "all_job_type_specs", lambda: specs)
    monkeypatch.setattr(job_registry, "enabled_job_type_specs", lambda: specs)


def test_job_type_registry_exposes_required_metadata():
    register_all_job_types()
    specs = job_registry.all_job_type_specs()

    assert "example_pair" in specs
    spec = specs["example_pair"]
    assert spec.params_schema == "ExamplePairParams"
    assert spec.runtime_fields_schema == "ExamplePairRuntimeFields"
    assert spec.canonical_result_schema == "ExamplePairResult"
    assert spec.public_result_schema == "ExamplePairResult"
    assert spec.visibility == "demo"
    assert spec.role == "root_or_leaf"
    assert spec.allow_callback is False
    assert spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(spec.retry_policy)
    assert spec.side_effect_policy == "none"
    assert spec.error_codes <= all_error_reasons()
    assert spec.prompt_specs == ()

    assert "arithmetic" in specs
    arithmetic_spec = specs["arithmetic"]
    assert arithmetic_spec.params_schema == "ArithmeticParams"
    assert arithmetic_spec.runtime_fields_schema == "ArithmeticRuntimeFields"
    assert arithmetic_spec.canonical_result_schema == "ArithmeticResult"
    assert arithmetic_spec.public_result_schema == "ArithmeticResult"
    assert arithmetic_spec.visibility == "demo"
    assert arithmetic_spec.role == "root"
    assert arithmetic_spec.allow_callback is True
    assert arithmetic_spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(arithmetic_spec.retry_policy)
    assert arithmetic_spec.side_effect_policy == "none"
    assert arithmetic_spec.error_codes <= all_error_reasons()
    assert arithmetic_spec.prompt_specs == ()

    assert "tagged_text_translation" in specs
    tagged_spec = specs["tagged_text_translation"]
    assert tagged_spec.params_schema == "TaggedTextTranslationParams"
    assert tagged_spec.runtime_fields_schema == "TaggedTextTranslationRuntimeFields"
    assert tagged_spec.canonical_result_schema == "TaggedTextTranslationResult"
    assert tagged_spec.public_result_schema == "TaggedTextTranslationResult"
    assert tagged_spec.visibility == "public"
    assert tagged_spec.role == "root"
    assert tagged_spec.allow_callback is True
    assert tagged_spec.result_snapshot_statuses == frozenset()
    assert tagged_spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(tagged_spec.retry_policy)
    assert tagged_spec.side_effect_policy == "none"
    assert tagged_spec.error_codes <= all_error_reasons()
    assert "INVALID_JOB_PARAMS" in tagged_spec.error_codes
    assert tagged_spec.prompt_specs == ()

    assert "example_sleep" in specs
    echo_spec = specs["example_sleep"]
    assert echo_spec.params_schema == "ExampleSleepParams"
    assert echo_spec.runtime_fields_schema == "ExampleSleepRuntimeFields"
    assert echo_spec.canonical_result_schema == "ExampleSleepResult"
    assert echo_spec.public_result_schema == "ExampleSleepResult"
    assert echo_spec.visibility == "demo"
    assert echo_spec.role == "root_or_leaf"
    assert echo_spec.allow_callback is False
    assert echo_spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(echo_spec.retry_policy)
    assert echo_spec.side_effect_policy == "none"
    assert echo_spec.error_codes <= all_error_reasons()
    assert echo_spec.prompt_specs == ()

    assert "example_workflow" in specs
    workflow_spec = specs["example_workflow"]
    assert workflow_spec.params_schema == "ExampleWorkflowParams"
    assert workflow_spec.runtime_fields_schema == "ExampleWorkflowRuntimeFields"
    assert workflow_spec.canonical_result_schema == "ExampleWorkflowResult"
    assert workflow_spec.public_result_schema == "ExampleWorkflowResult"
    assert workflow_spec.visibility == "demo"
    assert workflow_spec.role == "root"
    assert workflow_spec.allow_callback is False
    assert workflow_spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(workflow_spec.retry_policy)
    assert workflow_spec.side_effect_policy == "none"
    assert workflow_spec.error_codes <= all_error_reasons()
    assert workflow_spec.prompt_specs == ()

    assert "example_collect" in specs
    collect_spec = specs["example_collect"]
    assert collect_spec.params_schema == "ExampleCollectParams"
    assert collect_spec.runtime_fields_schema == "ExampleCollectRuntimeFields"
    assert collect_spec.canonical_result_schema == "ExampleCollectResult"
    assert collect_spec.public_result_schema == "ExampleCollectResult"
    assert collect_spec.visibility == "demo"
    assert collect_spec.role == "leaf"
    assert collect_spec.allow_callback is False
    assert collect_spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(collect_spec.retry_policy)
    assert collect_spec.side_effect_policy == "none"
    assert collect_spec.error_codes <= all_error_reasons()
    assert collect_spec.prompt_specs == ()

    assert "job_real_llm_echo" in specs
    real_llm_spec = specs["job_real_llm_echo"]
    assert real_llm_spec.params_schema == "JobRealLlmEchoParams"
    assert real_llm_spec.runtime_fields_schema == "JobRealLlmEchoRuntimeFields"
    assert real_llm_spec.canonical_result_schema == "JobRealLlmEchoResult"
    assert real_llm_spec.public_result_schema == "JobRealLlmEchoResult"
    assert real_llm_spec.visibility == "demo"
    assert real_llm_spec.role == "root_or_leaf"
    assert real_llm_spec.allow_callback is False
    assert real_llm_spec.execution_mode == "builtin_llm_text_runtime"
    _assert_default_retry_policy(real_llm_spec.retry_policy)
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
    assert double_llm_spec.visibility == "demo"
    assert double_llm_spec.role == "root_or_leaf"
    assert double_llm_spec.allow_callback is False
    assert double_llm_spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(double_llm_spec.retry_policy)
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

    assert "poster_title_image" in specs
    poster_spec = specs["poster_title_image"]
    assert poster_spec.params_schema == "PosterTitleImageParams"
    assert poster_spec.runtime_fields_schema == "PosterTitleImageRuntimeFields"
    assert poster_spec.canonical_result_schema == "PosterTitleImageResult"
    assert poster_spec.public_result_schema == "PosterTitleImageResult"
    assert poster_spec.visibility == "public"
    assert poster_spec.role == "root"
    assert poster_spec.allow_callback is True
    assert poster_spec.result_snapshot_statuses == frozenset({"running", "failed"})
    assert poster_spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(poster_spec.retry_policy)
    assert poster_spec.side_effect_policy == "none"
    assert poster_spec.error_codes <= all_error_reasons()
    assert POSTER_TITLE_IMAGE_REFERENCE_INVALID in poster_spec.error_codes
    assert POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT in poster_spec.error_codes
    assert poster_spec.prompt_specs == ()
    assert poster_spec.prompt_template_required_blocks == frozenset(
        {"style_probe", "additional_prompt", "layout_rules"}
    )

    assert "audio_stem_separation" in specs
    audio_spec = specs["audio_stem_separation"]
    assert audio_spec.params_schema == "AudioStemSeparationParams"
    assert audio_spec.runtime_fields_schema == "AudioStemSeparationRuntimeFields"
    assert audio_spec.canonical_result_schema == "AudioStemSeparationResult"
    assert audio_spec.public_result_schema == "AudioStemSeparationResult"
    assert audio_spec.visibility == "demo"
    assert audio_spec.role == "root"
    assert audio_spec.allow_callback is True
    assert audio_spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(audio_spec.retry_policy)
    assert audio_spec.side_effect_policy == "none"
    assert audio_spec.timeout_seconds == 2400
    assert audio_spec.allowed_capability_refs == frozenset({"media.audio_input:2"})
    assert audio_spec.error_codes <= all_error_reasons()
    assert AUDIO_STEM_INPUT_INVALID in audio_spec.error_codes
    assert AUDIO_STEM_MODEL_ASSET_MISSING in audio_spec.error_codes
    assert audio_spec.prompt_specs == ()

    assert "audio_stem_separation_triton" in specs
    audio_triton_spec = specs["audio_stem_separation_triton"]
    assert audio_triton_spec.params_schema == "AudioStemSeparationTritonParams"
    assert audio_triton_spec.runtime_fields_schema == "AudioStemSeparationTritonRuntimeFields"
    assert audio_triton_spec.canonical_result_schema == "AudioStemSeparationTritonResult"
    assert audio_triton_spec.public_result_schema == "AudioStemSeparationTritonResult"
    assert audio_triton_spec.visibility == "demo"
    assert audio_triton_spec.role == "root"
    assert audio_triton_spec.allow_callback is True
    assert audio_triton_spec.execution_mode == "custom_executor"
    _assert_default_retry_policy(audio_triton_spec.retry_policy)
    assert audio_triton_spec.side_effect_policy == "none"
    assert audio_triton_spec.timeout_seconds == 2400
    assert audio_triton_spec.allowed_capability_refs == frozenset({"media.audio_input:2"})
    assert audio_triton_spec.error_codes <= all_error_reasons()
    assert AUDIO_STEM_INPUT_INVALID in audio_triton_spec.error_codes
    assert AUDIO_STEM_MODEL_ASSET_MISSING in audio_triton_spec.error_codes
    assert audio_triton_spec.prompt_specs == ()


def test_registered_job_type_names_are_layered_contract():
    register_all_job_types()
    specs = job_registry.all_job_type_specs()
    source_job_type_names = _registered_job_type_names_in_source()

    expected_job_type_names = {
        "arithmetic",
        "job_real_llm_double_echo",
        "job_real_llm_echo",
        "example_pair",
        "example_collect",
        "example_sleep",
        "example_workflow",
        "audio_stem_separation",
        "audio_stem_separation_triton",
        "poster_title_image",
        "poster_title_image_generate_item",
        "poster_title_image_join",
        "poster_title_image_style_probe",
        "tagged_text_translation",
    }
    assert source_job_type_names == expected_job_type_names
    assert set(specs) == source_job_type_names
    assert {name for name, spec in specs.items() if spec.visibility == "public"} >= {
        "poster_title_image",
        "tagged_text_translation",
    }
    assert {name for name, spec in specs.items() if spec.visibility == "internal"} >= {
        "poster_title_image_generate_item",
        "poster_title_image_join",
        "poster_title_image_style_probe",
    }


def test_enabled_job_types_default_to_all_registered_job_types():
    job_registry.clear_for_tests()
    register_all_job_types()

    assert set(job_registry.enabled_job_types()) == set(job_registry.all_job_types())


def test_enabled_job_types_default_external_job_types_exclude_leaf_children():
    job_registry.clear_for_tests()
    register_all_job_types()

    external_job_types = set(job_registry.external_job_types())

    assert "example_pair" in external_job_types
    assert "example_collect" not in external_job_types
    assert "poster_title_image_join" not in external_job_types


def test_enabled_job_types_default_external_job_types_exclude_demo_in_release_env(monkeypatch):
    job_registry.clear_for_tests()
    monkeypatch.setattr(
        "app.core.config.settings",
        SimpleNamespace(
            runtime=SimpleNamespace(is_release_env=True),
            job=SimpleNamespace(enabled_job_types=()),
        ),
    )

    register_all_job_types()

    assert set(job_registry.enabled_job_types()) == set(job_registry.all_job_types())
    assert "tagged_text_translation" in set(job_registry.external_job_types())
    assert "poster_title_image" in set(job_registry.external_job_types())
    assert "example_pair" not in set(job_registry.external_job_types())
    assert "audio_stem_separation" not in set(job_registry.external_job_types())


def test_enabled_job_types_allowlist_keeps_registered_catalog_and_enables_requested_roots(monkeypatch):
    job_registry.clear_for_tests()
    monkeypatch.setattr(
        "app.core.config.settings",
        SimpleNamespace(
            runtime=SimpleNamespace(is_release_env=False),
            job=SimpleNamespace(enabled_job_types=("tagged_text_translation",)),
        ),
    )

    register_all_job_types()

    assert "poster_title_image" in set(job_registry.all_job_types())
    assert set(job_registry.enabled_job_types()) == {"tagged_text_translation"}
    assert set(job_registry.external_job_types()) == {"tagged_text_translation"}
    assert job_registry.is_job_type_enabled("tagged_text_translation") is True
    assert job_registry.is_job_type_enabled("poster_title_image") is False
    assert job_registry.is_external_job_type_enabled("tagged_text_translation") is True
    assert job_registry.is_external_job_type_enabled("poster_title_image") is False


def test_enabled_job_type_factory_keeps_registered_catalog_separate_from_runtime_allowlist(monkeypatch):
    from app.jobs.factory import get_enabled_job_executor, get_job_executor

    job_registry.clear_for_tests()
    monkeypatch.setattr(
        "app.core.config.settings",
        SimpleNamespace(
            runtime=SimpleNamespace(is_release_env=False),
            job=SimpleNamespace(enabled_job_types=("tagged_text_translation",)),
        ),
    )

    register_all_job_types()

    assert get_job_executor("poster_title_image").name == "poster_title_image"
    with pytest.raises(KeyError, match="No enabled job executor"):
        get_enabled_job_executor("poster_title_image")


def test_enabled_job_types_adds_static_workflow_children(monkeypatch):
    job_registry.clear_for_tests()
    monkeypatch.setattr(
        "app.core.config.settings",
        SimpleNamespace(
            runtime=SimpleNamespace(is_release_env=False),
            job=SimpleNamespace(enabled_job_types=("poster_title_image",)),
        ),
    )

    register_all_job_types()

    assert set(job_registry.enabled_job_types()) == {
        "poster_title_image",
        "poster_title_image_style_probe",
        "poster_title_image_generate_item",
        "poster_title_image_join",
    }
    assert set(job_registry.external_job_types()) == {"poster_title_image"}
    assert job_registry.is_external_job_type_enabled("poster_title_image_generate_item") is False


def test_enabled_job_types_does_not_expose_workflow_children_as_external(monkeypatch):
    job_registry.clear_for_tests()
    monkeypatch.setattr(
        "app.core.config.settings",
        SimpleNamespace(
            runtime=SimpleNamespace(is_release_env=False),
            job=SimpleNamespace(enabled_job_types=("example_workflow",)),
        ),
    )

    register_all_job_types()

    assert {"example_sleep", "example_pair", "example_collect"} <= set(job_registry.enabled_job_types())
    assert set(job_registry.external_job_types()) == {"example_workflow"}
    assert job_registry.is_external_job_type_enabled("example_sleep") is False


@pytest.mark.parametrize("enabled_job_types", [("missing_job",), ("poster_title_image_join",)])
def test_enabled_job_types_rejects_unknown_or_internal_job_type(monkeypatch, enabled_job_types):
    job_registry.clear_for_tests()
    monkeypatch.setattr(
        "app.core.config.settings",
        SimpleNamespace(
            runtime=SimpleNamespace(is_release_env=False),
            job=SimpleNamespace(enabled_job_types=enabled_job_types),
        ),
    )

    with pytest.raises(ValueError, match="ENABLED_JOB_TYPES"):
        register_all_job_types()


def test_enabled_job_types_rejects_demo_job_type_in_release_env(monkeypatch):
    job_registry.clear_for_tests()
    monkeypatch.setattr(
        "app.core.config.settings",
        SimpleNamespace(
            runtime=SimpleNamespace(is_release_env=True),
            job=SimpleNamespace(enabled_job_types=("example_pair",)),
        ),
    )

    with pytest.raises(ValueError, match="release APP_ENV ENABLED_JOB_TYPES"):
        register_all_job_types()


def test_register_job_type_decorator_is_marker_not_registration_side_effect():
    before = set(job_registry.all_job_types())

    @job_registry.register_job_type
    class MarkerOnlyJob(JobExecutor):
        name: str = "test_marker_only_job"
        visibility = "demo"
        role = "root"
        params_schema = None
        runtime_fields_schema_name = "dict"
        canonical_result_schema = None
        public_result_schema = None

        def runtime_job_fields(self, job_params):
            return {}

    assert getattr(MarkerOnlyJob, "__job_type_registered__") is True
    assert set(job_registry.all_job_types()) == before


def test_tool_and_capability_definitions_only_live_in_composition_roots():
    assert _constructor_call_locations("ToolDefinition") == {"app/tools/register.py"}
    assert _constructor_call_locations("CapabilityDefinition") == {"app/capabilities/register.py"}


def test_audio_job_does_not_import_other_audio_executor_private_helpers():
    imported_modules = _imported_modules(APP_DIR / "jobs/types/audio_stem_separation_triton/executor.py")

    assert "app.jobs.types.audio_stem_separation.executor" not in imported_modules


def test_import_scanner_expands_from_import_aliases(tmp_path):
    absolute = tmp_path / "absolute.py"
    relative = APP_DIR / "capabilities" / "example.py"

    assert "app.jobs" in _imported_modules_from_tree(absolute, ast.parse("from app import jobs\n"))
    assert "app.jobs" in _imported_modules_from_tree(relative, ast.parse("from .. import jobs\n"))


def test_registry_layers_do_not_depend_on_callers():
    violations: list[str] = []
    rules = {
        APP_DIR / "capabilities": ("app.jobs", "app.integrations"),
        APP_DIR / "tools": ("app.jobs", "app.capabilities"),
        APP_DIR / "integrations": ("app.jobs", "app.capabilities", "app.tools"),
    }
    for directory, forbidden_prefixes in rules.items():
        for path in directory.rglob("*.py"):
            module_refs = _imported_modules(path)
            forbidden = sorted(
                ref
                for ref in module_refs
                if any(ref == prefix or ref.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
            )
            if forbidden:
                violations.append(f"{path.relative_to(ROOT)} imports {forbidden}")

    assert violations == []


def test_poster_title_image_retry_policy_is_scoped_to_transient_leaf_execution():
    register_all_job_types()
    specs = job_registry.all_job_type_specs()
    retryable_codes = ["AI_PROVIDER_FAILED", "JOB_TIMEOUT", "MODEL_CALL_TIMEOUT", "OSS_FETCH_FAILED", "OSS_WRITE_FAILED"]
    expected_leaf_business = {
        "domain": "business_execution",
        "max_attempts": 3,
        "retry_delay_seconds": 15,
        "backoff_kind": "fixed",
        "retryable_error_codes": retryable_codes,
    }

    root_policy = specs["poster_title_image"].retry_policy
    style_policy = specs["poster_title_image_style_probe"].retry_policy
    generate_policy = specs["poster_title_image_generate_item"].retry_policy
    join_policy = specs["poster_title_image_join"].retry_policy

    assert root_policy["workflow_orchestration"]["max_attempts"] == 3
    assert root_policy["business_execution"]["max_attempts"] == 1
    assert root_policy["business_execution"]["retryable_error_codes"] == []
    assert style_policy["business_execution"] == expected_leaf_business
    assert generate_policy["business_execution"] == expected_leaf_business
    assert join_policy["business_execution"] == root_policy["business_execution"]


def test_poster_title_image_job_types_declare_business_log_events():
    register_all_job_types()
    specs = job_registry.all_job_type_specs()
    assert specs["poster_title_image"].log_events == (
        LogEvent.POSTER_TITLE_IMAGE_STYLE_PROBE_COMPLETED,
        LogEvent.POSTER_TITLE_IMAGE_OBJECT_STORED,
        LogEvent.POSTER_TITLE_IMAGE_ITEM_COMPLETED,
        LogEvent.POSTER_TITLE_IMAGE_JOIN_COMPLETED,
    )
    assert specs["poster_title_image_style_probe"].log_events == (
        LogEvent.POSTER_TITLE_IMAGE_STYLE_PROBE_COMPLETED,
    )
    assert specs["poster_title_image_generate_item"].log_events == (
        LogEvent.POSTER_TITLE_IMAGE_OBJECT_STORED,
        LogEvent.POSTER_TITLE_IMAGE_ITEM_COMPLETED,
    )
    assert specs["poster_title_image_join"].log_events == (
        LogEvent.POSTER_TITLE_IMAGE_JOIN_COMPLETED,
    )


def _job_type_spec(**overrides) -> JobTypeSpec:
    values = {
        "job_type": "example_pair",
        "visibility": "demo",
        "role": "root_or_leaf",
        "execution_mode": "custom_executor",
        "retry_policy": {
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
        "side_effect_policy": "none",
        "params_schema": "ExamplePairParams",
        "runtime_fields_schema": "ExamplePairRuntimeFields",
        "canonical_result_schema": "ExamplePairResult",
        "public_result_schema": "ExamplePairResult",
        "callback_envelope_schema": "CallbackEnvelope[JobEnvelope]",
        "allow_callback": True,
        "result_snapshot_statuses": frozenset(),
        "large_artifact_keys": frozenset(),
        "error_codes": frozenset({"INVALID_INPUT"}),
        "log_events": (),
        "timeout_seconds": 60,
        "allowed_capability_refs": frozenset(),
        "prompt_template_required_blocks": frozenset(),
    }
    values.update(overrides)
    return JobTypeSpec(**values)


def test_job_executor_requires_explicit_visibility_and_role():
    class MissingVisibilityJob(JobExecutor):
        name = "missing_visibility"
        role = "root"
        params_schema = None
        runtime_fields_schema_name = "dict"
        canonical_result_schema = None
        public_result_schema = None

        def runtime_job_fields(self, job_params):
            return {}

    class MissingRoleJob(JobExecutor):
        name = "missing_role"
        visibility = "demo"
        params_schema = None
        runtime_fields_schema_name = "dict"
        canonical_result_schema = None
        public_result_schema = None

        def runtime_job_fields(self, job_params):
            return {}

    with pytest.raises(ValueError, match="visibility"):
        MissingVisibilityJob().job_type_spec()
    with pytest.raises(ValueError, match="role"):
        MissingRoleJob().job_type_spec()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"execution_mode": "sync"}, "execution_mode"),
        ({"side_effect_policy": "unknown"}, "side_effect_policy"),
        ({"retry_policy": {}}, "retry_policy"),
        (
            {
                "retry_policy": {
                    "workflow_orchestration": {
                        "domain": "workflow_orchestration",
                        "max_attempts": 0,
                        "retry_delay_seconds": 5,
                        "backoff_kind": "fixed",
                        "retryable_error_codes": [],
                    },
                    "business_execution": {
                        "domain": "business_execution",
                        "max_attempts": 1,
                        "retry_delay_seconds": None,
                        "backoff_kind": "none",
                        "retryable_error_codes": [],
                    },
                }
            },
            "max_attempts",
        ),
        ({"visibility": "private"}, "visibility"),
        ({"role": "worker"}, "role"),
        ({"result_snapshot_statuses": frozenset({"queued"})}, "result_snapshot_statuses"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
    ],
)
def test_validate_job_type_registry_rejects_invalid_phase3_metadata(monkeypatch, overrides, message):
    _patch_job_type_specs(monkeypatch, {"example_pair": _job_type_spec(**overrides)})
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda **_kwargs: {"version": "test", "job_types": {}})

    with pytest.raises(ValueError, match=message):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_unknown_log_event(monkeypatch):
    _patch_job_type_specs(monkeypatch, {"example_pair": _job_type_spec(log_events=("not_registered",))})
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda **_kwargs: {"version": "test", "job_types": {}})

    with pytest.raises(ValueError, match="unknown log events"):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_internal_errors(monkeypatch):
    monkeypatch.setattr(
        "app.core.registry_checks.all_error_specs",
        lambda: {
            "TEST_INTERNAL_ERROR": ErrorSpec(
                "199998",
                "TEST_INTERNAL_ERROR",
                "test internal error",
                500,
                visibility="internal",
            )
        },
    )
    _patch_job_type_specs(
        monkeypatch,
        {"example_pair": _job_type_spec(error_codes=frozenset({"TEST_INTERNAL_ERROR"}))},
    )
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda **_kwargs: {"version": "test", "job_types": {}})

    with pytest.raises(ValueError, match="internal errors"):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_invalid_capability_ref(monkeypatch):
    _patch_job_type_specs(
        monkeypatch,
        {"example_pair": _job_type_spec(allowed_capability_refs=frozenset({"media_input"}))},
    )
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda **_kwargs: {"version": "test", "job_types": {}})

    with pytest.raises(ValueError, match="capability_ref"):
        validate_job_type_registry()


def test_validate_capability_tool_registry_rejects_unknown_capability_ref(monkeypatch):
    _patch_job_type_specs(
        monkeypatch,
        {"example_pair": _job_type_spec(allowed_capability_refs=frozenset({"media.input:1"}))},
    )

    with pytest.raises(ValueError, match="unknown capability_ref"):
        validate_capability_tool_registry()


def test_validate_capability_tool_registry_rejects_unknown_capability_ref_on_disabled_job_type(monkeypatch):
    specs = {
        "tagged_text_translation": _job_type_spec(
            job_type="tagged_text_translation",
            visibility="public",
            role="root",
        ),
        "disabled_job": _job_type_spec(
            job_type="disabled_job",
            visibility="public",
            role="root",
            allowed_capability_refs=frozenset({"media.input:1"}),
        ),
    }
    monkeypatch.setattr(job_registry, "all_job_type_specs", lambda: specs)
    monkeypatch.setattr(
        job_registry,
        "enabled_job_type_specs",
        lambda: {"tagged_text_translation": specs["tagged_text_translation"]},
    )

    with pytest.raises(ValueError, match="unknown capability_ref"):
        validate_capability_tool_registry()


def _workflow_definition(**overrides) -> WorkflowDefinition:
    values = {
        "workflow_type": "example_workflow",
        "root_job_type": "example_workflow",
        "build": lambda _params: None,
        "workflow_version": 1,
        "failure_policy": "fail_fast",
        "max_nodes": 10,
    }
    values.update(overrides)
    return WorkflowDefinition(**values)


def test_validate_workflow_registry_accepts_registered_root_job_type(monkeypatch):
    monkeypatch.setattr(
        job_registry,
        "all_job_type_specs",
        lambda: {"example_workflow": _job_type_spec(role="root")},
    )
    monkeypatch.setattr(
        "app.core.registry_checks.workflow_registry.all_workflow_definitions",
        lambda: {"example_workflow": _workflow_definition()},
    )

    validate_workflow_registry()


@pytest.mark.parametrize(
    ("definition", "job_specs", "message"),
    [
        (
            _workflow_definition(root_job_type="missing"),
            {},
            "root_job_type must match workflow_type",
        ),
        (
            _workflow_definition(),
            {},
            "unknown root_job_type",
        ),
        (
            _workflow_definition(),
            {"example_workflow": _job_type_spec(role="leaf")},
            "root-capable",
        ),
        (
            _workflow_definition(runtime_job_type_dependencies=frozenset({"missing_child"})),
            {"example_workflow": _job_type_spec(role="root")},
            "unknown runtime job_type dependencies",
        ),
        (
            _workflow_definition(runtime_job_type_dependencies=frozenset({"root_child"})),
            {
                "example_workflow": _job_type_spec(role="root"),
                "root_child": _job_type_spec(job_type="root_child", role="root"),
            },
            "child-capable",
        ),
        (
            _workflow_definition(workflow_version=0),
            {"example_workflow": _job_type_spec(role="root")},
            "workflow_version",
        ),
        (
            _workflow_definition(failure_policy="ignore"),
            {"example_workflow": _job_type_spec(role="root")},
            "failure_policy",
        ),
        (
            _workflow_definition(max_nodes=0),
            {"example_workflow": _job_type_spec(role="root")},
            "max_nodes",
        ),
        (
            _workflow_definition(build=None),
            {"example_workflow": _job_type_spec(role="root")},
            "build",
        ),
    ],
)
def test_validate_workflow_registry_rejects_invalid_definition(monkeypatch, definition, job_specs, message):
    monkeypatch.setattr(job_registry, "all_job_type_specs", lambda: job_specs)
    monkeypatch.setattr(
        "app.core.registry_checks.workflow_registry.all_workflow_definitions",
        lambda: {"example_workflow": definition},
    )

    with pytest.raises(ValueError, match=message):
        validate_workflow_registry()


def _prompt_config(prompt_ref: str = "prompt.ref", output_schema_ref: str = "ExamplePairResult") -> dict:
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
    _patch_job_type_specs(
        monkeypatch,
        {
            "example_pair": _job_type_spec(
                prompt_specs=(
                    PromptSpec(
                        step_name="calling_model",
                        runtime_field="prompt_payload",
                        prompt_ref="missing.prompt",
                        output_schema_ref="ExamplePairResult",
                    ),
                )
            )
        },
    )
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda **_kwargs: _prompt_config())

    with pytest.raises(ValueError, match="unknown prompt_ref"):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_prompt_output_schema_mismatch(monkeypatch):
    _patch_job_type_specs(
        monkeypatch,
        {
            "example_pair": _job_type_spec(
                prompt_specs=(
                    PromptSpec(
                        step_name="calling_model",
                        runtime_field="prompt_payload",
                        prompt_ref="prompt.ref",
                        output_schema_ref="ExamplePairResult",
                    ),
                )
            )
        },
    )
    monkeypatch.setattr(
        prompt_templates,
        "_load_prompt_config",
        lambda **_kwargs: _prompt_config(output_schema_ref="ExampleSleepResult"),
    )

    with pytest.raises(ValueError, match="output_schema_ref mismatch"):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_builtin_llm_without_prompt_spec(monkeypatch):
    _patch_job_type_specs(
        monkeypatch,
        {
            "example_pair": _job_type_spec(
                execution_mode="builtin_llm_text_runtime",
                prompt_specs=(),
            )
        },
    )
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda **_kwargs: {"version": "test", "job_types": {}})

    with pytest.raises(ValueError, match="requires one prompt_spec"):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_missing_required_prompt_template_block(monkeypatch):
    _patch_job_type_specs(
        monkeypatch,
        {
            "poster_title_image": _job_type_spec(
                job_type="poster_title_image",
                prompt_template_required_blocks=frozenset({"style_probe", "layout_rules"}),
            )
        },
    )
    monkeypatch.setattr(
        prompt_templates,
        "_load_prompt_config",
        lambda **_kwargs: {
            "version": "test",
            "job_types": {
                "poster_title_image": {
                    "name": "Poster title image",
                    "description": "Poster title image",
                    "prompt_blocks": {
                        "style_probe": {
                            "role": "user",
                            "label": "Style probe",
                            "content": "",
                        },
                    },
                },
            },
        },
    )

    with pytest.raises(ValueError, match="missing blocks"):
        validate_job_type_registry()


def test_validate_job_type_registry_skips_disabled_job_local_prompt_config(monkeypatch, tmp_path):
    base_prompt_config = tmp_path / "prompts.yaml"
    base_prompt_config.write_text("version: test\njob_types: {}\nprompts: {}\n", encoding="utf-8")
    disabled_prompt_dir = tmp_path / "poster_title_image"
    disabled_prompt_dir.mkdir()
    (disabled_prompt_dir / "prompts.yaml").write_text(
        """
version: broken
job_types:
  poster_title_image:
    name: Poster
    description: Disabled poster template
    prompt_blocks: []
""".strip(),
        encoding="utf-8",
    )
    specs = {
        "tagged_text_translation": _job_type_spec(
            job_type="tagged_text_translation",
            visibility="public",
            role="root",
        ),
        "poster_title_image": _job_type_spec(job_type="poster_title_image", visibility="public", role="root"),
    }
    monkeypatch.setattr(job_registry, "all_job_type_specs", lambda: specs)
    monkeypatch.setattr(
        job_registry,
        "enabled_job_type_specs",
        lambda: {"tagged_text_translation": specs["tagged_text_translation"]},
    )
    monkeypatch.setattr(
        prompt_templates,
        "settings",
        SimpleNamespace(registry=SimpleNamespace(prompt_config_path=base_prompt_config)),
    )
    monkeypatch.setattr(prompt_templates, "JOB_PROMPT_CONFIG_ROOT", tmp_path)

    validate_job_type_registry()


def test_validate_job_type_registry_checks_disabled_base_prompt_config(monkeypatch, tmp_path):
    base_prompt_config = tmp_path / "prompts.yaml"
    base_prompt_config.write_text(
        """
version: test
job_types: {}
prompts:
  disabled.prompt: []
""".strip(),
        encoding="utf-8",
    )
    specs = {
        "tagged_text_translation": _job_type_spec(
            job_type="tagged_text_translation",
            visibility="public",
            role="root",
        ),
        "disabled_job": _job_type_spec(
            job_type="disabled_job",
            visibility="public",
            role="root",
            prompt_specs=(
                PromptSpec(
                    step_name="disabled_step",
                    runtime_field="prompt_payload",
                    prompt_ref="disabled.prompt",
                    output_schema_ref="ExamplePairResult",
                ),
            ),
        ),
    }
    monkeypatch.setattr(job_registry, "all_job_type_specs", lambda: specs)
    monkeypatch.setattr(
        job_registry,
        "enabled_job_type_specs",
        lambda: {"tagged_text_translation": specs["tagged_text_translation"]},
    )
    monkeypatch.setattr(
        prompt_templates,
        "settings",
        SimpleNamespace(registry=SimpleNamespace(prompt_config_path=base_prompt_config)),
    )
    monkeypatch.setattr(prompt_templates, "JOB_PROMPT_CONFIG_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="YAML object"):
        validate_job_type_registry()


def test_validate_job_type_registry_rejects_bad_prompt_spec_field_type(monkeypatch):
    _patch_job_type_specs(
        monkeypatch,
        {
            "example_pair": _job_type_spec(
                prompt_specs=(
                    PromptSpec(
                        step_name=None,  # type: ignore[arg-type]
                        runtime_field="prompt_payload",
                        prompt_ref="prompt.ref",
                        output_schema_ref="ExamplePairResult",
                    ),
                )
            )
        },
    )
    monkeypatch.setattr(prompt_templates, "_load_prompt_config", lambda **_kwargs: _prompt_config())

    with pytest.raises(ValueError, match="step_name"):
        validate_job_type_registry()


def test_registry_consistency_check_passes():
    register_all_job_types()
    validate_all_registries(app)


def test_poster_title_image_prompt_template_is_published():
    template = prompt_templates.get_template("poster_title_image")

    assert template is not None
    assert template.job_type == "poster_title_image"
    assert {block.key for block in template.prompt_blocks} == {
        "style_probe",
        "additional_prompt",
        "layout_rules",
    }


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


def test_api_lifespan_manages_database_engine(monkeypatch):
    from starlette.testclient import TestClient

    from app import main

    calls = []

    def fake_init_db_engine():
        calls.append("init")

    async def fake_close_db_engine():
        calls.append("close")

    monkeypatch.setattr(main, "init_db_engine", fake_init_db_engine)
    monkeypatch.setattr(main, "close_db_engine", fake_close_db_engine)

    created = main.create_app()
    with TestClient(created):
        pass

    assert calls == ["init", "close"]


def test_worker_registration_validates_job_type_registry(monkeypatch):
    from app.tasks import jobs as task_jobs

    calls = {}

    monkeypatch.setattr("app.core.database.init_db_engine", lambda: calls.setdefault("db", True))
    monkeypatch.setattr("app.jobs.types.register.register_all_job_types", lambda: calls.setdefault("jobs", True))
    monkeypatch.setattr("app.core.error_registry.freeze_error_registry", lambda: calls.setdefault("errors", True))
    monkeypatch.setattr("app.core.registry_checks.validate_all_registries", lambda: calls.setdefault("registry", True))
    monkeypatch.setattr("app.core.model_registry.validate_model_catalog", lambda: calls.setdefault("models", True))

    task_jobs._ensure_workflows_registered()

    assert calls == {"db": True, "jobs": True, "errors": True, "registry": True, "models": True}


@pytest.mark.asyncio
async def test_taskiq_worker_events_manage_database_engine(monkeypatch):
    from taskiq.events import TaskiqEvents

    from app.tasks.taskiq_app import broker

    calls = []

    monkeypatch.setattr("app.tasks.taskiq_app.init_db_engine", lambda: calls.append("init"))

    async def fake_close_db_engine():
        calls.append("close")

    monkeypatch.setattr("app.tasks.taskiq_app.close_db_engine", fake_close_db_engine)

    startup_handlers = broker.event_handlers[TaskiqEvents.WORKER_STARTUP]
    shutdown_handlers = broker.event_handlers[TaskiqEvents.WORKER_SHUTDOWN]

    assert startup_handlers
    assert shutdown_handlers

    await startup_handlers[0](None)
    await shutdown_handlers[0](None)

    assert calls == ["init", "close"]


def test_register_all_job_types_reregisters_after_clear():
    register_all_job_types()
    job_registry.clear_for_tests()

    register_all_job_types()

    assert {
        "arithmetic",
        "example_pair",
        "example_sleep",
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


def test_worker_runtime_bootstrap_registers_job_types_for_callback_ack_validation():
    from app.jobs import registry as job_registry
    from app.services.callbacks import validate_callback_response_payload
    from app.tasks.runtime import ensure_worker_runtime_initialized

    job_registry.clear_for_tests()

    with pytest.raises(KeyError, match="poster_title_image"):
        validate_callback_response_payload({"accepted": True}, job_type="poster_title_image")

    ensure_worker_runtime_initialized()

    envelope = validate_callback_response_payload({"accepted": True}, job_type="poster_title_image")
    assert envelope.accepted is True
