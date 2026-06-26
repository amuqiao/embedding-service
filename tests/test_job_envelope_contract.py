import uuid
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core import config as config_module
from app.core.database import get_db
from app.core.security import require_service_auth
from app.main import API_PREFIX


def _security_settings(**overrides):
    values = {
        "DISABLE_CALLER_ID_HEADER": False,
        "DISABLE_HTTP_AUTH_HEADER": False,
        "SERVICE_API_KEY": "test-token",
    }
    values.update(overrides)
    return SimpleNamespace(
        security=config_module.SecuritySettings(
            service_api_key=values["SERVICE_API_KEY"],
            disable_http_auth_header=values["DISABLE_HTTP_AUTH_HEADER"],
            disable_caller_id_header=values["DISABLE_CALLER_ID_HEADER"],
        ),
    )


def _patch_security_settings(monkeypatch, **overrides) -> None:
    import app.core.security as security_module

    monkeypatch.setattr(security_module, "settings", _security_settings(**overrides))


def _job_envelope(
    *,
    job_id: uuid.UUID,
    job_status: str,
    job_result: dict | None = None,
) -> dict:
    now = "2026-06-20T10:00:01Z"
    return {
        "job_id": str(job_id),
        "client_request_id": "client-add-1",
        "job_type": "job_test_add",
        "job_status": job_status,
        "job_progress": {
            "stage": "completed" if job_status == "succeeded" else "accepted",
            "percent": 100 if job_status == "succeeded" else 0,
            "message": "completed" if job_status == "succeeded" else "accepted",
        },
        "job_result": job_result,
        "job_error": None,
        "cost": None,
        "callback": {
            "status": "pending",
            "attempt": 0,
            "last_error": None,
            "next_retry_at": None,
        },
        "status_url": f"{API_PREFIX}/jobs/{job_id}",
        "created_at": now,
        "updated_at": now,
        "finished_at": now if job_status == "succeeded" else None,
    }


def _assert_iso_datetime(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def _assert_response_envelope(body: dict) -> dict:
    assert body["code"] == "0"
    assert body["msg"] == "success"
    assert isinstance(body["request_id"], str)
    _assert_iso_datetime(body["server_time"])
    assert set(body["data"]) == {"job"}
    return body["data"]["job"]


def _assert_standard_job_fields(job: dict) -> None:
    assert set(job) == {
        "job_id",
        "client_request_id",
        "job_type",
        "job_status",
        "job_progress",
        "job_result",
        "job_error",
        "cost",
        "callback",
        "status_url",
        "created_at",
        "updated_at",
        "finished_at",
    }
    assert set(job["job_progress"]) == {"stage", "percent", "message"}
    assert set(job["callback"]) == {"status", "attempt", "last_error", "next_retry_at"}


def test_post_jobs_returns_response_envelope_with_queued_job(monkeypatch):
    from app.api.routes import jobs as jobs_routes
    from app.main import app

    job_id = uuid.uuid4()

    async def fake_submit_job_request(_db, payload, caller_id, *, request_id):
        assert caller_id == "caller-1"
        assert request_id == "req-create-add"
        assert payload.job_type == "job_test_add"
        assert payload.job_params == {"a": 2, "b": 3}
        return _job_envelope(job_id=job_id, job_status="queued", job_result=None)

    async def fake_get_db():
        yield object()

    async def fake_auth():
        return "caller-1"

    monkeypatch.setattr(jobs_routes, "submit_job_request", fake_submit_job_request)
    app.dependency_overrides[get_db] = fake_get_db
    app.dependency_overrides[require_service_auth] = fake_auth
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            f"{API_PREFIX}/jobs",
            json={
                "client_request_id": "client-add-1",
                "job_type": "job_test_add",
                "job_params": {"a": 2, "b": 3},
            },
            headers={"X-Request-ID": "req-create-add"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-create-add"
    job = _assert_response_envelope(response.json())
    _assert_standard_job_fields(job)
    assert job["job_status"] == "queued"
    assert job["job_result"] is None


def test_post_jobs_uses_default_caller_when_caller_id_header_is_disabled(monkeypatch):
    from app.api.routes import jobs as jobs_routes
    from app.main import app

    job_id = uuid.uuid4()

    async def fake_submit_job_request(_db, payload, caller_id, *, request_id):
        assert caller_id == "default"
        assert request_id == "req-create-default-caller"
        assert payload.job_type == "job_test_add"
        return _job_envelope(job_id=job_id, job_status="queued", job_result=None)

    async def fake_get_db():
        yield object()

    _patch_security_settings(monkeypatch, SERVICE_API_KEY="test-token", DISABLE_CALLER_ID_HEADER=True)
    monkeypatch.setattr(jobs_routes, "submit_job_request", fake_submit_job_request)
    app.dependency_overrides[get_db] = fake_get_db
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            f"{API_PREFIX}/jobs",
            json={
                "client_request_id": "client-add-1",
                "job_type": "job_test_add",
                "job_params": {"a": 2, "b": 3},
            },
            headers={
                "Authorization": "Bearer test-token",
                "X-AI-Service-Caller-ID": "bad caller!",
                "X-Request-ID": "req-create-default-caller",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-create-default-caller"
    job = _assert_response_envelope(response.json())
    assert job["job_status"] == "queued"


def test_post_jobs_rejects_invalid_caller_id_header_by_default(monkeypatch):
    from app.api.routes import jobs as jobs_routes
    from app.main import app

    async def fake_submit_job_request(*_args, **_kwargs):
        raise AssertionError("invalid caller id must be rejected before submit")

    async def fake_get_db():
        yield object()

    _patch_security_settings(monkeypatch, SERVICE_API_KEY="test-token")
    monkeypatch.setattr(jobs_routes, "submit_job_request", fake_submit_job_request)
    app.dependency_overrides[get_db] = fake_get_db
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            f"{API_PREFIX}/jobs",
            json={
                "client_request_id": "client-add-1",
                "job_type": "job_test_add",
                "job_params": {"a": 2, "b": 3},
            },
            headers={
                "Authorization": "Bearer test-token",
                "X-AI-Service-Caller-ID": "bad caller!",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "200001"
    assert body["msg"] == "missing or invalid service token"
    assert body["data"] is None
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_get_jobs_returns_response_envelope_with_standard_job_fields(monkeypatch):
    from app.api.routes import jobs as jobs_routes
    from app.main import app

    job_id = uuid.uuid4()

    async def fake_get_job_response(_db, received_job_id, caller_id, *, request_id):
        assert received_job_id == job_id
        assert caller_id == "caller-1"
        assert request_id == "req-get-add"
        return _job_envelope(job_id=job_id, job_status="succeeded", job_result={"a": 2, "b": 3, "result": 5})

    async def fake_get_db():
        yield object()

    async def fake_auth():
        return "caller-1"

    monkeypatch.setattr(jobs_routes, "get_job_response", fake_get_job_response)
    app.dependency_overrides[get_db] = fake_get_db
    app.dependency_overrides[require_service_auth] = fake_auth
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"{API_PREFIX}/jobs/{job_id}",
            headers={"X-Request-ID": "req-get-add"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-get-add"
    job = _assert_response_envelope(response.json())
    _assert_standard_job_fields(job)
    assert job["job_id"] == str(job_id)
    assert job["job_type"] == "job_test_add"
    assert job["job_status"] == "succeeded"
    assert job["job_result"] == {"a": 2, "b": 3, "result": 5}
    assert job["job_result"]["result"] == job["job_result"]["a"] + job["job_result"]["b"]


def test_ai_ledger_update_failed_job_error_is_not_retryable():
    from app.services.jobs import _job_error_detail

    detail = _job_error_detail(
        {
            "code": "AI_LEDGER_UPDATE_FAILED",
            "details": {"ai_call_log_id": "00000000-0000-0000-0000-000000000001"},
        }
    )

    assert detail == {
        "reason": "AI_LEDGER_UPDATE_FAILED",
        "details": {"ai_call_log_id": "00000000-0000-0000-0000-000000000001"},
        "retryable": False,
    }
