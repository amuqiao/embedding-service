import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import require_service_auth
from app.main import API_PREFIX
from app.schemas.billing import BillingEnvelope


def _job_envelope(*, job_id: uuid.UUID) -> dict:
    now = "2026-06-20T10:00:01Z"
    return {
        "job_id": str(job_id),
        "client_request_id": "client-billing-contract",
        "job_type": "job_test_add",
        "job_status": "succeeded",
        "job_progress": {
            "stage": "completed",
            "percent": 100,
            "message": "completed",
        },
        "job_result": {"sum": 5},
        "job_error": None,
        "callback": {
            "status": "not_configured",
            "attempt": 0,
            "last_error": None,
            "next_retry_at": None,
        },
        "status_url": f"{API_PREFIX}/jobs/{job_id}",
        "created_at": now,
        "updated_at": now,
        "finished_at": now,
    }


def test_get_job_billing_returns_http_envelope_with_billing_only(monkeypatch):
    from app.api.routes import jobs as jobs_routes
    from app.main import app

    job_id = uuid.uuid4()
    calls: dict[str, object] = {}
    billing = BillingEnvelope(
        scope_type="job",
        scope_id=str(job_id),
        status="estimated",
        currency="USD",
        total_cost_amount="0.00012345",
        usage_units={"input_tokens": 100, "output_tokens": 20},
        pricing_refs=["openai:gpt-test@2026-06-23"],
        ai_call_count=1,
        billable_call_count=1,
        unbillable_call_count=0,
        failed_call_count=0,
        finalized_at=datetime(2026, 6, 23, 10, 30, tzinfo=timezone.utc),
    )

    async def fake_get_job_billing(db, route_job_id, caller_id, *, request_id):
        calls["db"] = db
        calls["job_id"] = route_job_id
        calls["caller_id"] = caller_id
        calls["request_id"] = request_id
        return billing

    async def fake_get_job_response(_db, route_job_id, caller_id, *, request_id):
        assert route_job_id == job_id
        assert caller_id == "caller-billing"
        assert request_id == "req-job-contract"
        return _job_envelope(job_id=route_job_id)

    async def fake_get_db():
        yield "db-session"

    async def fake_auth():
        return "caller-billing"

    monkeypatch.setattr(jobs_routes, "get_job_billing", fake_get_job_billing)
    monkeypatch.setattr(jobs_routes, "get_job_response", fake_get_job_response)
    app.dependency_overrides[get_db] = fake_get_db
    app.dependency_overrides[require_service_auth] = fake_auth
    try:
        client = TestClient(app, raise_server_exceptions=False)
        billing_response = client.get(
            f"{API_PREFIX}/jobs/{job_id}/billing",
            headers={"X-Request-ID": "req-billing-contract"},
        )
        job_response = client.get(
            f"{API_PREFIX}/jobs/{job_id}",
            headers={"X-Request-ID": "req-job-contract"},
        )
    finally:
        app.dependency_overrides.clear()

    assert billing_response.status_code == 200
    assert billing_response.headers["X-Request-ID"] == "req-billing-contract"
    body = billing_response.json()
    assert set(body) == {"code", "msg", "data", "request_id", "server_time"}
    assert body["code"] == "0"
    assert body["msg"] == "success"
    assert body["request_id"] == "req-billing-contract"
    assert set(body["data"]) == {"billing"}
    assert "job" not in body["data"]
    assert body["data"]["billing"] == billing.model_dump(mode="json")

    assert calls == {
        "db": "db-session",
        "job_id": job_id,
        "caller_id": "caller-billing",
        "request_id": "req-billing-contract",
    }

    assert job_response.status_code == 200
    job_body = job_response.json()
    assert set(job_body["data"]) == {"job"}
    assert "billing" not in job_body["data"]["job"]
