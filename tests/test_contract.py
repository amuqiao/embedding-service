import pytest
from datetime import UTC, datetime
from types import SimpleNamespace
from fastapi.security import HTTPAuthorizationCredentials

from app.core import config as config_module
from app.core.exceptions import AppError
from app.core.prompt_templates import list_prompt_templates
from app.core.security import require_service_auth
from app.main import API_PREFIX, app
from app.schemas.jobs import CreateJobRequest, JobResult
from app.services.executor import _prompt_messages
from app.services.jobs import _validate_create_request, validate_job_status_payload


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


def test_default_prompt_templates_declares_no_builtin_job_types():
    templates = list_prompt_templates()
    assert templates.version == "empty"
    assert templates.job_types == []


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

        def validate_extra(self, extra):
            pass

        def validate_normalized_job_params(self, job_params):
            pass

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
        "app.services.jobs.get_enabled_model",
        lambda model_id: (_ for _ in ()).throw(AssertionError("model registry should not be called")),
    )

    _handler, job_params, runtime_fields = _validate_create_request(payload)

    assert job_params == {"input": {"value": 1}}
    assert runtime_fields == {}


def test_create_job_validation_preserves_runtime_app_error(monkeypatch):
    class RuntimeHandler:
        allow_callback = True

        def normalize_job_params(self, job_params):
            return job_params

        def validate_normalized_job_params(self, job_params):
            raise AppError("RUNTIME_CONFIG_MISSING", "runtime config missing", status_code=500)

        def runtime_job_fields(self, job_params):
            return {}

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
    progress_stage = {"queued": "accepted", "succeeded": "completed"}.get(status, status)
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

    class RequiredResultHandler:
        def validate_public_result(self, result):
            if result is None:
                raise ValueError("succeeded result is required")
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
        with pytest.raises(Exception, match="error is required"):
            validate_job_status_payload(_job_view_payload(job_type="test.null_result", status="failed"))
        with pytest.raises(Exception, match="public result must be null"):
            validate_job_status_payload(_job_view_payload(job_type="test.null_result", status="succeeded", result={}))
        with pytest.raises(Exception, match="succeeded result is required"):
            validate_job_status_payload(_job_view_payload(job_type="test.required_result", status="succeeded"))
    finally:
        monkeypatch.undo()


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
        for parameter in operation["parameters"]
    )


def test_openapi_declares_bearer_auth_for_protected_routes(monkeypatch):
    _patch_main_settings(monkeypatch)
    schema = app.openapi()

    security_schemes = schema["components"]["securitySchemes"]
    assert security_schemes["HTTPBearer"] == {"type": "http", "scheme": "bearer"}

    prompt_templates = schema["paths"][f"{API_PREFIX}/prompt-templates"]["get"]
    assert {"HTTPBearer": []} in prompt_templates["security"]


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

    client = TestClient(app)
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

    client = TestClient(app)
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
    client = TestClient(app, raise_server_exceptions=False)
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
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(f"{API_PREFIX}/models")

    assert response.status_code == 200
    assert response.json()["code"] == "0"


def test_legal_request_id_allows_dot_and_colon(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        f"{API_PREFIX}/models",
        headers={"X-Request-ID": "trace.id:part-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "trace.id:part-1"
    assert response.headers["X-Request-ID"] == "trace.id:part-1"


def test_invalid_request_id_returns_error_envelope(monkeypatch):
    from fastapi.testclient import TestClient

    _patch_security_settings(monkeypatch, DISABLE_HTTP_AUTH_HEADER=True)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        f"{API_PREFIX}/models",
        headers={"X-Request-ID": "bad request id"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "100002"
    assert body["data"]["header"] == "X-Request-ID"
    assert body["request_id"] == response.headers["X-Request-ID"]
    _assert_iso_server_time(body["server_time"])


def test_method_not_allowed_uses_unified_error_envelope():
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
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

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(route_path)

    assert response.status_code == 400
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

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(route_path)

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "900500"
    assert body["data"] is None
    assert body["request_id"]
    _assert_iso_server_time(body["server_time"])
