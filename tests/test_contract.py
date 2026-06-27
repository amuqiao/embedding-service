import pytest
import re
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from fastapi.security import HTTPAuthorizationCredentials

from app.core import config as config_module
from app.core.exceptions import AppError
from app.core.prompt_templates import list_prompt_templates
from app.core.security import require_service_auth
from app.main import API_PREFIX, app
from app.schemas.errors import build_error_envelope
from app.schemas.jobs import CreateJobRequest, JobResult
from app.schemas.meta import ModelOut, ModelParameterOut, ModelsResponse
from app.services.executor import _prompt_messages
from app.services.jobs import _validate_create_request, validate_job_status_payload

UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture(autouse=True)
def reset_openapi_schema():
    app.openapi_schema = None
    yield
    app.openapi_schema = None


def _valid_payload() -> dict:
    return {
        "client_request_id": "contract-req-1",
        "job_type": "test.echo",
        "job_params": {"value": {"hello": "world"}},
        "callback": {"url": "https://example.com/callback"},
        "metadata": {"caller_task_id": "task-1"},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def _job_type_spec(*, execution_mode: str = "custom_executor", visibility: str = "public"):
    return SimpleNamespace(execution_mode=execution_mode, visibility=visibility)


def _assert_iso_server_time(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def _settings_double(**overrides):
    values = {
        "DISABLE_CALLER_ID_HEADER": False,
        "DISABLE_HTTP_AUTH_HEADER": False,
        "SERVICE_API_KEY": "test-token",
    }
    values.update(overrides)
    return SimpleNamespace(
        service=config_module.ServiceSettings(api_prefix=API_PREFIX),
        security=config_module.SecuritySettings(
            service_api_key=values["SERVICE_API_KEY"],
            disable_http_auth_header=values["DISABLE_HTTP_AUTH_HEADER"],
            disable_caller_id_header=values["DISABLE_CALLER_ID_HEADER"],
        ),
    )


def _patch_security_settings(monkeypatch, **overrides) -> None:
    import app.core.security as security_module

    monkeypatch.setattr(security_module, "settings", _settings_double(**overrides))


def _patch_main_settings(monkeypatch, **overrides) -> None:
    import app.main as main_module

    monkeypatch.setattr(main_module, "settings", _settings_double(**overrides))


def test_create_job_request_accepts_valid_payload():
    payload = CreateJobRequest.model_validate(_valid_payload())
    assert payload.job_type == "test.echo"
    assert payload.job_params == {"value": {"hello": "world"}}
    assert payload.metadata == {"caller_task_id": "task-1"}


def test_default_prompt_templates_declares_poster_title_image_blocks():
    template = list_prompt_templates()
    assert template.version == "poster_title_image.v1"
    assert template.job_type == "poster_title_image"
    assert {block.key for block in template.prompt_blocks} == {
        "style_probe",
        "additional_prompt",
        "layout_rules",
    }


def test_runtime_prompt_builds_generic_user_and_work_note_messages():
    messages = _prompt_messages(
        {
            "blocks": [
                {"key": "user", "role": "user", "content": "用户配置提示"},
                {"key": "work_note", "role": "user", "content": "工作注释"},
            ]
        },
        "待处理正文",
        "test.echo",
    )

    user_message = messages[0]["content"]
    assert "用户配置提示" in user_message
    assert "===待处理文本开始===" in user_message
    assert messages[1]["content"].startswith("【已有工作注释 / 上一轮约束】")


def test_runtime_prompt_skips_empty_work_note_input():
    messages = _prompt_messages(
        {
            "blocks": [
                {"key": "user", "role": "user", "content": "用户配置提示"},
                {"key": "work_note", "role": "user", "content": ""},
            ]
        },
        "待处理正文",
        "test.echo",
    )

    assert [message["role"] for message in messages] == ["user"]
    assert all(not message["content"].startswith("【已有工作注释 / 上一轮约束】") for message in messages)


def test_create_job_request_rejects_legacy_job_contract_fields():
    payload = _valid_payload()
    payload["model_id"] = "gpt-4.1"
    payload["source"] = {"inline": {"text": "hello"}}
    payload["prompt"] = {"blocks": []}
    try:
        CreateJobRequest.model_validate(payload)
    except Exception as exc:
        assert "model_id" in str(exc) and "source" in str(exc) and "prompt" in str(exc)
    else:
        raise AssertionError("legacy job contract fields should be rejected")


def test_create_job_request_rejects_execution_mode():
    payload = _valid_payload()
    payload["execution_mode"] = "sync"
    try:
        CreateJobRequest.model_validate(payload)
    except Exception as exc:
        assert "execution_mode" in str(exc)
    else:
        raise AssertionError("execution_mode should be rejected")


def test_create_job_request_rejects_callback_secret_ref():
    payload = _valid_payload()
    payload["callback"]["secret_ref"] = "caller-a-callback-secret"
    try:
        CreateJobRequest.model_validate(payload)
    except Exception as exc:
        assert "secret_ref" in str(exc)
    else:
        raise AssertionError("callback.secret_ref should not be accepted until per-callback secrets are implemented")


def test_create_job_validation_allows_non_model_runtime(monkeypatch):
    class GenericHandler:
        allow_callback = True

        def normalize_job_params(self, job_params):
            return job_params

        def runtime_job_fields(self, job_params):
            return {}

        def validate_normalized_job_params(self, job_params):
            pass

        def job_type_spec(self):
            return _job_type_spec()

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "contract-no-model",
            "job_type": "generic.no_model",
            "job_params": {"input": {"value": 1}},
        }
    )
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda job_type: GenericHandler())
    monkeypatch.setattr("app.services.jobs.get_template", lambda job_type: None)
    monkeypatch.setattr(
        "app.services.jobs.require_enabled_text_model",
        lambda model_id: (_ for _ in ()).throw(AssertionError("model registry should not be called")),
    )

    _handler, job_params, runtime_fields = _validate_create_request(payload)

    assert job_params == {"input": {"value": 1}}
    assert runtime_fields == {}


def test_create_job_validation_rejects_non_text_model_for_builtin_text_runtime(monkeypatch):
    class BuiltinTextHandler:
        name = "generic.builtin_text"
        allow_callback = True

        def normalize_job_params(self, job_params):
            return job_params

        def runtime_job_fields(self, job_params):
            return {"model_id": "image-model"}

        def validate_normalized_job_params(self, job_params):
            pass

        def job_type_spec(self):
            return _job_type_spec(execution_mode="builtin_llm_text_runtime")

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "contract-non-text-model",
            "job_type": "generic.builtin_text",
            "job_params": {"input": {"value": 1}},
        }
    )
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda job_type: BuiltinTextHandler())
    monkeypatch.setattr("app.services.jobs.get_template", lambda job_type: None)

    def reject_non_text_model(model_id):
        raise AppError("MODEL_NOT_AVAILABLE", f"模型不支持文本生成: {model_id}")

    monkeypatch.setattr("app.services.jobs.require_enabled_text_model", reject_non_text_model)

    with pytest.raises(AppError) as exc:
        _validate_create_request(payload)

    assert exc.value.code == "MODEL_NOT_AVAILABLE"


def test_create_job_validation_rejects_non_text_model_for_custom_text_executor(monkeypatch):
    class CustomTextHandler:
        name = "generic.custom_text"
        allow_callback = True
        requires_text_generation_model = True

        def normalize_job_params(self, job_params):
            return job_params

        def runtime_job_fields(self, job_params):
            return {"model_id": "image-model"}

        def validate_normalized_job_params(self, job_params):
            pass

        def job_type_spec(self):
            return _job_type_spec(execution_mode="custom_executor")

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "contract-custom-non-text-model",
            "job_type": "generic.custom_text",
            "job_params": {"input": {"value": 1}},
        }
    )
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda job_type: CustomTextHandler())
    monkeypatch.setattr("app.services.jobs.get_template", lambda job_type: None)

    def reject_non_text_model(model_id):
        raise AppError("MODEL_NOT_AVAILABLE", f"模型不支持文本生成: {model_id}")

    monkeypatch.setattr("app.services.jobs.require_enabled_text_model", reject_non_text_model)

    with pytest.raises(AppError) as exc:
        _validate_create_request(payload)

    assert exc.value.code == "MODEL_NOT_AVAILABLE"


def test_create_job_validation_preserves_runtime_app_error(monkeypatch):
    class RuntimeHandler:
        allow_callback = True

        def normalize_job_params(self, job_params):
            return job_params

        def validate_normalized_job_params(self, job_params):
            raise AppError("RUNTIME_CONFIG_MISSING", "runtime config missing")

        def runtime_job_fields(self, job_params):
            return {}

        def job_type_spec(self):
            return _job_type_spec()

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "contract-runtime-app-error",
            "job_type": "generic.runtime_app_error",
            "job_params": {"input": {"value": 1}},
        }
    )
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda job_type: RuntimeHandler())

    with pytest.raises(AppError) as exc:
        _validate_create_request(payload)

    assert exc.value.code == "RUNTIME_CONFIG_MISSING"
    assert exc.value.status_code == 500


def test_create_job_validation_wraps_unexpected_prerequisite_errors(monkeypatch):
    class RuntimeHandler:
        allow_callback = True

        def normalize_job_params(self, job_params):
            return job_params

        def validate_normalized_job_params(self, job_params):
            raise RuntimeError("mock path unreadable")

        def runtime_job_fields(self, job_params):
            return {}

        def job_type_spec(self):
            return _job_type_spec()

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "contract-runtime-crash",
            "job_type": "generic.runtime_crash",
            "job_params": {"input": {"value": 1}},
        }
    )
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda job_type: RuntimeHandler())

    with pytest.raises(AppError) as exc:
        _validate_create_request(payload)

    assert exc.value.code == "JOB_PREREQUISITE_CHECK_FAILED"
    assert exc.value.status_code == 500
    assert exc.value.details == {"job_type": "generic.runtime_crash"}


@pytest.mark.asyncio
async def test_service_auth_uses_caller_id_header(monkeypatch):
    _patch_security_settings(monkeypatch, SERVICE_API_KEY="test-token")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")

    caller_id = await require_service_auth(credentials=credentials, caller_id="caller-1")

    assert caller_id == "caller-1"


@pytest.mark.asyncio
async def test_service_auth_can_disable_http_auth_header(monkeypatch):
    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)

    caller_id = await require_service_auth(credentials=None, caller_id="caller-1")

    assert caller_id == "caller-1"


@pytest.mark.asyncio
async def test_service_auth_can_disable_caller_id_header(monkeypatch):
    _patch_security_settings(monkeypatch, SERVICE_API_KEY="test-token", DISABLE_CALLER_ID_HEADER=True)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")

    caller_id = await require_service_auth(credentials=credentials, caller_id="bad caller!")

    assert caller_id == "default"


@pytest.mark.asyncio
async def test_service_auth_rejects_invalid_caller_id_header_by_default(monkeypatch):
    _patch_security_settings(monkeypatch, SERVICE_API_KEY="test-token")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")

    with pytest.raises(AppError) as exc:
        await require_service_auth(credentials=credentials, caller_id="bad caller!")

    assert exc.value.code == "UNAUTHORIZED"


def test_job_result_rejects_legacy_artifact_target():
    try:
        JobResult.model_validate(
            {
                "artifacts": [
                    {
                        "key": "work_note",
                        "type": "work_note",
                        "label": "建议工作注释",
                        "apply_mode": "append",
                        "content": "请统一角色称呼。",
                        "target": {
                            "job_type": "legacy.job",
                            "prompt_block_key": "work_note",
                            "default_mode": "append",
                        },
                    }
                ],
                "signals": {"passed": False},
            }
        )
    except Exception as exc:
        assert "target" in str(exc)
    else:
        raise AssertionError("legacy artifact target should be rejected")


def _job_view_payload(*, job_type: str, status: str, result=None, error=None) -> dict:
    progress_stage = {"queued": "accepted", "running": "calling_model", "succeeded": "completed"}.get(status, status)
    return {
        "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
        "client_request_id": "contract-test",
        "job_type": job_type,
        "job_status": status,
        "job_progress": {
            "percent": 100 if status in {"succeeded", "failed"} else 10,
            "message": "test",
            "stage": progress_stage,
        },
        "job_result": result,
        "job_error": error,
        "callback": {"status": "not_configured", "attempt": 0},
        "status_url": "/api/v1/ai-jobs/jobs/0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
        "created_at": datetime(2026, 6, 15, 10, 0, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 15, 10, 1, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 6, 15, 10, 1, 0, tzinfo=UTC) if status in {"succeeded", "failed"} else None,
    }


def test_job_view_status_and_result_contracts():
    class NullResultHandler:
        def validate_public_result(self, result):
            if result is not None:
                raise ValueError("public result must be null")
            return None

        def validate_result_snapshot(self, status, result):
            if result is not None:
                raise ValueError(f"{status} result must be null")
            return None

    class RequiredResultHandler:
        def validate_public_result(self, result):
            if result is None:
                raise ValueError("succeeded result is required")
            return result

        def validate_result_snapshot(self, status, result):
            if result is None:
                return None
            if "value" not in result:
                raise ValueError("value is required")
            return result

    def get_handler(job_type):
        return RequiredResultHandler() if job_type == "test.required_result" else NullResultHandler()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.jobs.registry.get", get_handler)
    try:
        validate_job_status_payload(_job_view_payload(job_type="test.null_result", status="succeeded"))
        validate_job_status_payload(
            _job_view_payload(
                job_type="test.required_result",
                status="succeeded",
                result={"value": {"ok": True}},
            )
        )

        with pytest.raises(Exception, match="result must be null"):
            validate_job_status_payload(_job_view_payload(job_type="test.null_result", status="queued", result={}))
        with pytest.raises(Exception, match="running result must be null"):
            validate_job_status_payload(_job_view_payload(job_type="test.null_result", status="running", result={}))
        validate_job_status_payload(
            _job_view_payload(job_type="test.required_result", status="running", result={"value": {"ok": True}})
        )
        with pytest.raises(Exception, match="value is required"):
            validate_job_status_payload(_job_view_payload(job_type="test.required_result", status="running", result={}))
        with pytest.raises(Exception, match="error is required"):
            validate_job_status_payload(_job_view_payload(job_type="test.null_result", status="failed"))
        with pytest.raises(Exception, match="failed result must be null"):
            validate_job_status_payload(
                _job_view_payload(
                    job_type="test.null_result",
                    status="failed",
                    result={},
                    error={"reason": "JOB_EXECUTION_FAILED", "details": {}, "retryable": False},
                )
            )
        validate_job_status_payload(
            _job_view_payload(
                job_type="test.required_result",
                status="failed",
                result={"value": {"ok": True}},
                error={"reason": "JOB_EXECUTION_FAILED", "details": {}, "retryable": False},
            )
        )
        with pytest.raises(Exception, match="public result must be null"):
            validate_job_status_payload(_job_view_payload(job_type="test.null_result", status="succeeded", result={}))
        with pytest.raises(Exception, match="succeeded result is required"):
            validate_job_status_payload(_job_view_payload(job_type="test.required_result", status="succeeded"))
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "failed"])
async def test_job_response_drops_stored_result_when_job_type_does_not_allow_snapshot(monkeypatch, status):
    from app.models.job import Job
    from app.services.jobs import get_job_response

    job_id = uuid.UUID("0a9be3fb-f01b-4f5d-90b5-4148c4a61df1")
    job = Job(
        id=job_id,
        caller_id="caller-1",
        client_request_id="contract-test",
        job_type="job_test_add",
        status=status,
        progress_percent=50,
        progress_stage="calling_model",
        result={"unexpected": "stored"},
        error={"code": "JOB_EXECUTION_FAILED", "message": "failed"} if status == "failed" else None,
        finished_at=datetime(2026, 6, 15, 10, 1, 0, tzinfo=UTC) if status == "failed" else None,
        created_at=datetime(2026, 6, 15, 10, 0, 0, tzinfo=UTC),
    )

    async def fake_get_for_caller(_db, received_job_id, caller_id):
        assert received_job_id == job_id
        assert caller_id == "caller-1"
        return job

    async def fake_get_scope_billing(*_args, **_kwargs):
        from app.schemas.billing import BillingEnvelope

        return BillingEnvelope(
            scope_type="job",
            scope_id=str(job_id),
            status="incomplete",
            currency="USD",
            total_cost_amount="0",
            usage_units={},
            pricing_refs=[],
            ai_call_count=0,
            billable_call_count=0,
            unbillable_call_count=0,
            failed_call_count=0,
        )

    class DefaultOffHandler:
        result_snapshot_statuses = frozenset()

        def supports_result_snapshot(self, status):
            return status in self.result_snapshot_statuses

    monkeypatch.setattr("app.services.jobs.JobRepo.get_for_caller", fake_get_for_caller)
    monkeypatch.setattr("app.services.jobs.get_scope_billing", fake_get_scope_billing)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: DefaultOffHandler())

    response = await get_job_response(object(), job.id, "caller-1")

    assert response.job_status == status
    assert response.job_result is None


def test_arithmetic_job_view_result_uses_registered_result_schema(monkeypatch):
    from app.jobs.types.arithmetic import ArithmeticJob

    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: ArithmeticJob())

    validate_job_status_payload(
        _job_view_payload(
            job_type="arithmetic",
            status="succeeded",
            result={
                "a": 8,
                "b": 2,
                "addition": 10,
                "subtraction": 6,
                "multiplication": 16,
                "division": 4.0,
            },
        )
    )

    with pytest.raises(Exception, match="division"):
        validate_job_status_payload(
            _job_view_payload(
                job_type="arithmetic",
                status="succeeded",
                result={
                    "a": 8,
                    "b": 2,
                    "addition": 10,
                    "subtraction": 6,
                    "multiplication": 16,
                },
            )
        )


def test_openapi_create_job_request_defaults_to_arithmetic_example():
    schema = app.openapi()
    operation = schema["paths"][f"{API_PREFIX}/jobs"]["post"]
    request_content = operation["requestBody"]["content"]["application/json"]
    visible_examples = []
    if "example" in request_content:
        visible_examples.append(request_content["example"])
    if "examples" in request_content:
        visible_examples.extend(example["value"] for example in request_content["examples"].values())
    request_schema = request_content["schema"]
    if "$ref" in request_schema:
        request_schema = schema["components"]["schemas"][request_schema["$ref"].rsplit("/", 1)[-1]]
    visible_examples.extend(request_schema.get("examples", []))

    arithmetic_example = next(example for example in visible_examples if example["job_type"] == "arithmetic")
    assert arithmetic_example["client_request_id"] == "swagger-arithmetic-demo"
    assert arithmetic_example["job_params"] == {"a": 9, "b": 3}
    assert arithmetic_example["metadata"] == {"source": "swagger-ui"}
    assert arithmetic_example["options"] == {"priority": "normal", "idempotency_mode": "return_existing"}


def test_openapi_declares_unified_response_envelope_for_jobs():
    schema = app.openapi()
    operation = schema["paths"][f"{API_PREFIX}/jobs"]["post"]
    responses = operation["responses"]

    assert "202" not in responses
    assert "422" not in responses
    success_schema = responses["200"]["content"]["application/json"]["schema"]
    assert set(success_schema["required"]) == {"code", "msg", "data", "request_id", "server_time"}
    assert success_schema["properties"]["code"]["type"] == "string"
    assert success_schema["properties"]["server_time"]["format"] == "date-time"
    assert success_schema["properties"]["data"]["$ref"].endswith("/JobResponseData")
    assert responses["400"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorEnvelope")
    assert any(
        parameter["name"] == "X-Request-ID"
        and parameter["schema"]["pattern"] == r"^[a-zA-Z0-9._:-]{1,128}$"
        and "default" not in parameter["schema"]
        and parameter["example"] == "trace-id-123"
        for parameter in operation["parameters"]
    )


def test_openapi_declares_bearer_auth_for_protected_routes(monkeypatch):
    _patch_main_settings(monkeypatch)
    schema = app.openapi()

    security_schemes = schema["components"]["securitySchemes"]
    assert security_schemes["HTTPBearer"] == {"type": "http", "scheme": "bearer"}

    languages = schema["paths"][f"{API_PREFIX}/languages"]["get"]
    assert {"HTTPBearer": []} in languages["security"]
    prompt_templates = schema["paths"][f"{API_PREFIX}/prompt-templates"]["get"]
    assert {"HTTPBearer": []} in prompt_templates["security"]


def test_openapi_declares_prompt_templates_job_type_query_parameter(monkeypatch):
    _patch_main_settings(monkeypatch)
    schema = app.openapi()

    prompt_templates = schema["paths"][f"{API_PREFIX}/prompt-templates"]["get"]
    job_type = next(
        parameter
        for parameter in prompt_templates["parameters"]
        if parameter["name"] == "job_type" and parameter["in"] == "query"
    )

    assert job_type["required"] is False
    assert job_type["schema"]["default"] == "poster_title_image"


def test_openapi_declares_prompt_template_response_data_schema(monkeypatch):
    _patch_main_settings(monkeypatch)
    schema = app.openapi()

    prompt_templates = schema["paths"][f"{API_PREFIX}/prompt-templates"]["get"]
    response_schema = prompt_templates["responses"]["200"]["content"]["application/json"]["schema"]
    data_schema = response_schema["properties"]["data"]

    assert data_schema["$ref"].endswith("/PromptTemplateResponseData")


def test_openapi_omits_bearer_auth_when_http_auth_header_is_disabled(monkeypatch):
    _patch_main_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)

    schema = app.openapi()

    assert "HTTPBearer" not in schema.get("components", {}).get("securitySchemes", {})
    prompt_templates = schema["paths"][f"{API_PREFIX}/prompt-templates"]["get"]
    assert "security" not in prompt_templates


def test_openapi_omits_caller_id_header_when_caller_id_header_is_disabled(monkeypatch):
    _patch_main_settings(monkeypatch, DISABLE_CALLER_ID_HEADER=True)

    schema = app.openapi()

    create_job = schema["paths"][f"{API_PREFIX}/jobs"]["post"]
    parameter_names = [
        parameter["name"]
        for parameter in create_job.get("parameters", [])
        if parameter.get("in") == "header"
    ]
    assert "X-AI-Service-Caller-ID" not in parameter_names
    assert {"HTTPBearer": []} in create_job["security"]


def test_health_endpoints():
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        health = client.get("/health")
        healthz = client.get("/healthz")

    # /health 是 liveness probe，始终返回 200
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    # /healthz 是 readiness probe，检查 DB/Redis；测试环境无真实依赖，返回状态可为 200 或 503
    assert healthz.status_code in (200, 503)
    assert "status" in healthz.json()


def test_unknown_route_uses_unified_error_envelope():
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.get("/definitely-not-found")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "300404"
    assert body["data"] is None
    assert body["request_id"]
    _assert_iso_server_time(body["server_time"])


def test_unauthorized_route_uses_unified_error_envelope(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{API_PREFIX}/models")

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "200001"
    assert body["data"] is None
    assert body["request_id"]
    _assert_iso_server_time(body["server_time"])


def test_models_route_allows_missing_authorization_when_auth_header_is_disabled(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{API_PREFIX}/models")

    assert response.status_code == 200
    assert response.json()["code"] == "0"


def test_prompt_templates_route_defaults_to_poster_title_image(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{API_PREFIX}/prompt-templates")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    assert body["data"]["job_type"] == "poster_title_image"
    assert {block["key"] for block in body["data"]["prompt_blocks"]} == {
        "style_probe",
        "additional_prompt",
        "layout_rules",
    }


def test_prompt_templates_route_filters_by_job_type(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{API_PREFIX}/prompt-templates", params={"job_type": "poster_title_image"})

    assert response.status_code == 200
    assert response.json()["data"]["job_type"] == "poster_title_image"


def test_prompt_templates_route_rejects_unknown_job_type(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{API_PREFIX}/prompt-templates", params={"job_type": "missing.job"})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "100011"
    assert body["data"] is None


def test_models_route_exposes_public_model_selection_metadata(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.routes import meta as meta_routes

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    monkeypatch.setattr(
        meta_routes,
        "list_models_response",
        lambda: ModelsResponse(
            default_model_id="custom-model",
            models=[
                ModelOut(
                    id="custom-model",
                    name="Custom Model",
                    model_type="text",
                    provider="openai",
                    enabled=True,
                    capabilities=["text_generation"],
                    input_media_types=["text/plain"],
                    output_media_types=["text/plain"],
                    limits={"context_window": 12345},
                    features={"supports_json_output": True},
                    parameters=[
                        ModelParameterOut(
                            name="size",
                            label="Size",
                            type="select",
                            required=False,
                            default="1024x1024",
                            options=["1024x1024", "1536x1024"],
                        )
                    ],
                    notes="Caller-facing note",
                )
            ],
        ),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{API_PREFIX}/models")

    assert response.status_code == 200
    model = response.json()["data"]["models"][0]
    assert model["model_type"] == "text"
    assert model["capabilities"] == ["text_generation"]
    assert model["input_media_types"] == ["text/plain"]
    assert model["output_media_types"] == ["text/plain"]
    assert model["limits"] == {"context_window": 12345}
    assert model["features"] == {"supports_json_output": True}
    assert model["parameters"] == [
        {
            "name": "size",
            "label": "Size",
            "type": "select",
            "required": False,
            "default": "1024x1024",
            "options": ["1024x1024", "1536x1024"],
        }
    ]
    hidden_fields = {
        "pricing_ref",
        "adapter",
        "adapter_model",
        "provider_model",
        "litellm_model",
        "requires_env",
        "temperature",
        "num_retries",
        "drop_params",
    }
    assert hidden_fields.isdisjoint(model)
    assert "context_window" not in model
    assert "supports_json_output" not in model

    model_schema = app.openapi()["components"]["schemas"]["ModelOut"]["properties"]
    assert {
        "model_type",
        "capabilities",
        "input_media_types",
        "output_media_types",
        "limits",
        "features",
        "parameters",
    } <= set(model_schema)
    model_required = set(app.openapi()["components"]["schemas"]["ModelOut"]["required"])
    assert {
        "model_type",
        "capabilities",
        "input_media_types",
        "output_media_types",
        "limits",
        "features",
        "parameters",
    } <= model_required
    assert "context_window" not in model_schema
    assert "supports_json_output" not in model_schema
    parameter_schema = app.openapi()["components"]["schemas"]["ModelParameterOut"]["properties"]
    assert {"name", "label", "type", "required", "default", "options", "min", "max"} <= set(parameter_schema)
    parameter_required = set(app.openapi()["components"]["schemas"]["ModelParameterOut"]["required"])
    assert parameter_required == {"name", "label", "type", "required", "default"}
    assert hidden_fields.isdisjoint(model_schema)

    operation = app.openapi()["paths"][f"{API_PREFIX}/models"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["properties"]["data"]["$ref"].endswith("/ModelsResponse")


def test_languages_route_exposes_shared_language_catalog(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{API_PREFIX}/languages")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"code", "msg", "data", "request_id", "server_time"}
    assert body["code"] == "0"
    languages = body["data"]["languages"]
    assert len(languages) == 22
    assert languages[0] == {
        "language": "zh",
        "display_name": "Chinese (Simplified)",
        "native_name": "中文（简体）",
    }
    codes = {language["language"] for language in languages}
    assert "in" in codes
    assert "id" not in codes
    assert all(set(language) == {"language", "display_name", "native_name"} for language in languages)

    language_schema = app.openapi()["components"]["schemas"]["LanguageOut"]
    assert set(language_schema["required"]) == {"language", "display_name", "native_name"}
    operation = app.openapi()["paths"][f"{API_PREFIX}/languages"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["properties"]["data"]["$ref"].endswith("/LanguagesResponse")


def test_legal_request_id_allows_dot_and_colon(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"{API_PREFIX}/models",
            headers={"X-Request-ID": "trace.id:part-1"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "trace.id:part-1"
    assert response.headers["X-Request-ID"] == "trace.id:part-1"


def test_missing_request_id_generates_unique_trace_id(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{API_PREFIX}/models")
        second_response = client.get(f"{API_PREFIX}/models")

    assert response.status_code == 200
    body = response.json()
    second_body = second_response.json()
    assert body["request_id"]
    assert body["request_id"] != "default"
    assert UUID_HEX_RE.fullmatch(body["request_id"])
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert second_body["request_id"]
    assert second_body["request_id"] != body["request_id"]
    assert UUID_HEX_RE.fullmatch(second_body["request_id"])
    assert second_body["request_id"] == second_response.headers["X-Request-ID"]


def test_invalid_request_id_returns_error_envelope(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"{API_PREFIX}/models",
            headers={"X-Request-ID": "bad request id"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "100002"
    assert body["data"]["header"] == "X-Request-ID"
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert UUID_HEX_RE.fullmatch(body["request_id"])
    _assert_iso_server_time(body["server_time"])


def test_empty_request_id_header_returns_error_envelope(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"{API_PREFIX}/models",
            headers={"X-Request-ID": ""},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "100002"
    assert body["data"]["header"] == "X-Request-ID"
    assert body["request_id"]
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert UUID_HEX_RE.fullmatch(body["request_id"])
    _assert_iso_server_time(body["server_time"])


def test_request_validation_error_uses_unified_error_envelope(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            f"{API_PREFIX}/jobs",
            headers={"Authorization": "Bearer test-token", "X-Request-ID": "contract-validation"},
            json={"client_request_id": "missing-job-type"},
        )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == {"code", "msg", "data", "request_id", "server_time"}
    assert body["code"] == "100001"
    assert body["msg"] == "invalid input"
    assert body["request_id"] == "contract-validation"
    assert "detail" not in body
    assert isinstance(body["data"]["errors"], list)
    assert response.headers["X-Request-ID"] == "contract-validation"
    _assert_iso_server_time(body["server_time"])


def test_error_envelope_builder_uses_error_registry_status():
    status_code, body = build_error_envelope(reason="HTTP_ERROR", request_id="contract-error")

    assert status_code == 400
    assert body["code"] == "100004"
    assert body["msg"] == "http error"
    assert body["data"] is None
    assert body["request_id"] == "contract-error"


def test_method_not_allowed_uses_unified_error_envelope():
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"{API_PREFIX}/models")

    assert response.status_code == 405
    body = response.json()
    assert body["code"] == "100405"
    assert body["data"] is None
    assert body["request_id"]
    _assert_iso_server_time(body["server_time"])


def test_generic_http_exception_uses_unified_error_envelope():
    from fastapi.testclient import TestClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    route_path = "/__test__/http-error"
    if not any(getattr(route, "path", "") == route_path for route in app.routes):
        @app.get(route_path, include_in_schema=False)
        async def raise_http_error():
            raise StarletteHTTPException(status_code=418, detail={"reason": "teapot"})

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(route_path)

    assert response.status_code == 418
    body = response.json()
    assert body["code"] == "100004"
    assert body["data"] is None
    assert body["request_id"]
    _assert_iso_server_time(body["server_time"])


def test_unhandled_exception_uses_unified_error_envelope():
    from fastapi.testclient import TestClient

    route_path = "/__test__/unhandled-error"
    if not any(getattr(route, "path", "") == route_path for route in app.routes):
        @app.get(route_path, include_in_schema=False)
        async def raise_unhandled_error():
            raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(route_path)

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "900500"
    assert body["data"] is None
    assert body["request_id"]
    _assert_iso_server_time(body["server_time"])
