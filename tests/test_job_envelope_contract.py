import uuid

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_service_auth


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
        "callback": {
            "status": "pending",
            "attempt": 0,
            "last_error": None,
            "next_retry_at": None,
        },
        "status_url": f"{settings.SERVICE_API_PREFIX}/jobs/{job_id}",
        "created_at": now,
        "updated_at": now,
        "finished_at": now if job_status == "succeeded" else None,
    }


def _assert_response_envelope(body: dict) -> dict:
    assert body["code"] == 0
    assert body["msg"] == "success"
    assert isinstance(body["request_id"], str)
    assert isinstance(body["server_time"], int)
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
            f"{settings.SERVICE_API_PREFIX}/jobs",
            json={
                "client_request_id": "client-add-1",
                "job_type": "job_test_add",
                "job_params": {"a": 2, "b": 3},
            },
            headers={"X-Request-ID": "req-create-add"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    job = _assert_response_envelope(response.json())
    _assert_standard_job_fields(job)
    assert job["job_status"] == "queued"
    assert job["job_result"] is None


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
            f"{settings.SERVICE_API_PREFIX}/jobs/{job_id}",
            headers={"X-Request-ID": "req-get-add"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    job = _assert_response_envelope(response.json())
    _assert_standard_job_fields(job)
    assert job["job_id"] == str(job_id)
    assert job["job_type"] == "job_test_add"
    assert job["job_status"] == "succeeded"
    assert job["job_result"] == {"a": 2, "b": 3, "result": 5}
    assert job["job_result"]["result"] == job["job_result"]["a"] + job["job_result"]["b"]
