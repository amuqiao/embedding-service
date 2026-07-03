import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.main import API_PREFIX
from app.models.job import CallbackOutbox, Job
from app.schemas.jobs import CallbackResponseEnvelope
from app.services.callbacks import (
    CallbackDeliveryResult,
    build_callback_body,
    build_callback_body_for_job,
    deliver_callback,
    _sign,
)
from app.services.job_runtime import payload_hash
from app.services.jobs import _job_to_response

_ACK_HEADERS = {"Content-Type": "application/json"}


def _callback_settings(**overrides):
    values = {
        "ALLOW_INSECURE_CALLBACKS": False,
        "CALLBACK_SIGNING_SECRET": "test-callback-secret",
        "CALLBACK_TIMEOUT_SECONDS": 5,
    }
    values.update(overrides)
    return SimpleNamespace(
        callback=SimpleNamespace(
            signing_secret_value=values["CALLBACK_SIGNING_SECRET"],
            allow_insecure_callbacks=values["ALLOW_INSECURE_CALLBACKS"],
            timeout_seconds=values["CALLBACK_TIMEOUT_SECONDS"],
        ),
    )


def _patch_callback_settings(monkeypatch, **overrides) -> None:
    import app.services.callbacks as callbacks_module

    monkeypatch.setattr(callbacks_module, "settings", _callback_settings(**overrides))


@pytest.fixture(autouse=True)
def _callback_test_handler(monkeypatch):
    class Handler:
        result_snapshot_statuses = frozenset()

        def build_callback_data(self, job):
            return job.result if isinstance(job.result, dict) else {}

        def validate_callback_response(self, response):
            pass

        def validate_public_result(self, result):
            return result

        def supports_result_snapshot(self, status):
            return status in self.result_snapshot_statuses

        def validate_result_snapshot(self, status, result):
            return None if result is None else result

        async def build_result_snapshot(self, status, job, db):
            return job.result

    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: Handler())
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: Handler())
    monkeypatch.setattr(
        "app.core.callback_security.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, "", ("93.184.216.34", 443))],
    )


def _job(callback_url: str | None = "https://example.com/callback") -> Job:
    now = datetime.now(timezone.utc)
    job_params = {"a": 2, "b": 3}
    job_params_hash = payload_hash(job_params)
    return Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-add-1",
        job_type="test.callback",
        status="succeeded",
        progress_percent=100,
        progress_text="completed",
        progress_stage="completed",
        metadata_={"trigger_request_id": "req-trigger-1"},
        job_params_hash=job_params_hash,
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "test.callback",
                "job_params_hash": job_params_hash,
                "runtime_fields": {"_system": {"trigger_request_id": "req-trigger-1"}},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "outputs/",
                    "oss_region": "region",
                },
            },
        },
        callback_url=callback_url,
        result={"a": 2, "b": 3, "result": 5},
        created_at=now,
        updated_at=now,
        finished_at=now,
    )


def _callback_outbox(
    job: Job,
    *,
    status: str = "pending",
    delivery_attempts: int = 0,
    last_error: dict | None = None,
    next_attempt_at: datetime | None = None,
) -> CallbackOutbox:
    return CallbackOutbox(
        id=uuid.uuid4(),
        job_id=job.id,
        event_id=uuid.uuid4(),
        event_type="job.succeeded" if job.status == "succeeded" else "job.failed",
        callback_url=job.callback_url or "https://example.com/callback",
        status=status,
        payload={},
        delivery_attempts=delivery_attempts,
        last_error=last_error,
        next_attempt_at=next_attempt_at,
    )


def _callback_payload(job: Job | None = None) -> dict:
    return build_callback_body(job or _job(), cost=None, usage=None)


def test_build_callback_body_uses_public_fields():
    job = _job()
    body = build_callback_body(job, cost=None, usage=None)

    assert set(body) == {
        "event",
        "event_id",
        "attempt",
        "sent_at",
        "trigger_request_id",
        "caller_id",
        "job",
    }
    assert body["event"] == "job.succeeded"
    assert body["attempt"] == 1
    assert body["event_id"]
    assert body["sent_at"]
    assert body["trigger_request_id"] == "req-trigger-1"
    assert body["caller_id"] == "caller-1"
    assert body["job"] == {
        "job_id": str(job.id),
        "client_request_id": "client-add-1",
        "job_type": "test.callback",
        "job_status": "succeeded",
        "job_progress": {"percent": 100, "message": "completed", "stage": "completed"},
        "job_result": {"a": 2, "b": 3, "result": 5},
        "job_error": None,
        "cost": None,
        "usage": None,
        "callback": {
            "status": "pending",
            "attempt": 0,
            "last_error": None,
            "next_retry_at": None,
        },
        "status_url": f"{API_PREFIX}/jobs/{job.id}",
        "created_at": job.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": job.updated_at.isoformat().replace("+00:00", "Z"),
        "finished_at": job.finished_at.isoformat().replace("+00:00", "Z"),
    }


def test_build_callback_body_drops_failed_result_without_snapshot_support(monkeypatch):
    class Handler:
        result_snapshot_statuses = frozenset()

        def validate_public_result(self, result):
            return result

        def supports_result_snapshot(self, status):
            return status in self.result_snapshot_statuses

        def validate_result_snapshot(self, status, result):
            if result is not None:
                raise ValueError(f"{status} result must be null")
            return None

    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: Handler())
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: Handler())
    job = _job()
    job.status = "failed"
    job.progress_percent = 80
    job.progress_stage = "failed"
    job.result = {"unexpected": "stored"}
    job.error = {"code": "JOB_EXECUTION_FAILED", "message": "failed"}

    body = build_callback_body(job, cost=None, usage=None)

    assert body["event"] == "job.failed"
    assert body["job"]["job_result"] is None
    assert body["job"]["job_error"]["reason"] == "JOB_EXECUTION_FAILED"


def test_build_callback_body_requires_projected_failed_snapshot_when_supported(monkeypatch):
    class Handler:
        result_snapshot_statuses = frozenset({"failed"})

        def validate_public_result(self, result):
            return result

        def supports_result_snapshot(self, status):
            return status in self.result_snapshot_statuses

        def validate_result_snapshot(self, status, result):
            return result

    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: Handler())
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: Handler())
    job = _job()
    job.status = "failed"
    job.progress_percent = 80
    job.progress_stage = "failed"
    job.result = None
    job.error = {"code": "JOB_EXECUTION_FAILED", "message": "failed"}

    with pytest.raises(ValueError, match="must be projected"):
        build_callback_body(job, cost=None, usage=None)


@pytest.mark.asyncio
async def test_build_callback_body_for_job_projects_failed_snapshot(monkeypatch):
    from app.schemas.billing import BillingEnvelope

    snapshot = {"items": [{"item_id": "es"}]}

    class Handler:
        result_snapshot_statuses = frozenset({"failed"})

        def validate_public_result(self, result):
            return result

        def supports_result_snapshot(self, status):
            return status in self.result_snapshot_statuses

        def validate_result_snapshot(self, status, result):
            return result

        async def build_result_snapshot(self, status, job, db):
            return snapshot

    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: Handler())
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: Handler())

    async def fake_get_scope_billing(_db, *, scope_type, scope_id, caller_id):
        return BillingEnvelope(
            scope_type=scope_type,
            scope_id=scope_id,
            status="not_billable",
            currency="USD",
            total_cost_amount="0.00000000",
            usage_units={},
            pricing_refs=[],
            ai_call_count=0,
            billable_call_count=0,
            unbillable_call_count=0,
            failed_call_count=0,
        )

    monkeypatch.setattr("app.services.billing.get_scope_billing", fake_get_scope_billing)
    job = _job()
    job.status = "failed"
    job.progress_percent = 80
    job.progress_stage = "failed"
    job.result = None
    job.error = {"code": "JOB_EXECUTION_FAILED", "message": "failed"}

    body = await build_callback_body_for_job(job, object())

    assert body["event"] == "job.failed"
    assert body["job"]["job_result"] == snapshot


@pytest.mark.asyncio
async def test_build_callback_body_for_job_exposes_terminal_billing_projection(monkeypatch):
    from app.schemas.billing import BillingEnvelope

    job = _job()

    async def fake_get_scope_billing(_db, *, scope_type, scope_id, caller_id):
        assert scope_type == "job"
        assert scope_id == str(job.id)
        assert caller_id == job.caller_id
        return BillingEnvelope(
            scope_type=scope_type,
            scope_id=scope_id,
            status="estimated",
            currency="USD",
            total_cost_amount="0.04491750",
            usage_units={
                "input_tokens": 1240,
                "cached_input_tokens": 0,
                "output_tokens": 311,
                "total_tokens": 1551,
            },
            pricing_refs=["openai:gpt-test@2026-06-23"],
            ai_call_count=2,
            billable_call_count=2,
            unbillable_call_count=0,
            failed_call_count=0,
        )

    monkeypatch.setattr("app.services.billing.get_scope_billing", fake_get_scope_billing)

    body = await build_callback_body_for_job(job, object())

    assert body["job"]["cost"] == {"currency": "USD", "amount": "0.04491750", "final": True}
    assert body["job"]["usage"] == {
        "ai_call_count": 2,
        "total_tokens": 1551,
        "final": True,
    }


def test_build_callback_body_uses_arithmetic_public_result_schema(monkeypatch):
    from app.jobs.types.arithmetic import ArithmeticJob

    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: ArithmeticJob())
    job = _job()
    job.job_type = "arithmetic"
    job.result = {
        "a": 8,
        "b": 2,
        "addition": 10,
        "subtraction": 6,
        "multiplication": 16,
        "division": 4.0,
    }
    job.runtime_ref["payload"]["job_type"] = "arithmetic"

    body = build_callback_body(job, cost=None, usage=None)

    assert body["job"]["job_type"] == "arithmetic"
    assert body["job"]["job_result"] == job.result

    job.result = {
        "a": 8,
        "b": 2,
        "addition": 10,
        "subtraction": 6,
        "multiplication": 16,
    }
    with pytest.raises(AppError) as exc:
        build_callback_body(job, cost=None, usage=None)
    assert exc.value.code == "JOB_VIEW_CONTRACT_INVALID"


def test_job_view_exposes_callback_delivery_state():
    next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    job = _job()
    callback_outbox = _callback_outbox(
        job,
        status="retrying",
        delivery_attempts=2,
        next_attempt_at=next_retry_at,
        last_error={"code": "CALLBACK_HTTP_ERROR", "status_code": 503},
    )

    job_view = _job_to_response(job, request_id="req-view", callback_outbox=callback_outbox)

    assert job_view.callback.status == "retrying"
    assert job_view.callback.attempt == 2
    assert job_view.callback.next_retry_at == next_retry_at
    assert job_view.callback.last_error.reason == "CALLBACK_HTTP_ERROR"
    assert job_view.callback.last_error.details == {"status_code": 503}


def test_job_view_marks_missing_callback_not_configured():
    job_view = _job_to_response(_job(None), request_id="req-view")

    assert job_view.callback.status == "not_configured"
    assert job_view.callback.attempt == 0


def test_job_view_maps_skipped_callback_without_error_to_not_configured():
    job = _job()
    callback_outbox = _callback_outbox(job, status="skipped", delivery_attempts=0)

    job_view = _job_to_response(job, request_id="req-view", callback_outbox=callback_outbox)

    assert job_view.callback.status == "not_configured"
    assert job_view.callback.attempt == 0
    assert job_view.callback.last_error is None


def test_job_view_maps_skipped_callback_with_error_to_failed():
    job = _job()
    callback_outbox = _callback_outbox(
        job,
        status="skipped",
        delivery_attempts=0,
        last_error={"code": "CALLBACK_URL_INVALID", "message": "callback.url must be HTTPS"},
    )

    job_view = _job_to_response(job, request_id="req-view", callback_outbox=callback_outbox)

    assert job_view.callback.status == "failed"
    assert job_view.callback.last_error.reason == "CALLBACK_URL_INVALID"


def test_job_view_rejects_unregistered_stored_error_reason():
    job = _job(None)
    job.status = "failed"
    job.result = None
    job.error = {"code": "LEGACY_UNKNOWN", "message": "legacy error"}

    with pytest.raises(AppError) as exc:
        _job_to_response(job, request_id="req-view")

    assert exc.value.code == "JOB_VIEW_CONTRACT_INVALID"


def test_job_view_uses_shell_result_and_metadata():
    job = _job()
    job.progress_stage = "finalize"
    job.metadata_ = {"visible": "metadata"}
    job.result = {
        "artifacts": [{"key": "public", "type": "json", "label": "Public"}],
        "signals": {"public": True},
    }

    job_view = _job_to_response(job, request_id="req-view")

    assert job_view.job_progress.stage == "completed"
    assert job_view.job_result == {
        "artifacts": [{"key": "public", "type": "json", "label": "Public"}],
        "signals": {"public": True},
    }


def test_build_callback_body_uses_next_attempt_number():
    job = _job()
    first_body = build_callback_body(job, cost=None, usage=None)

    body = build_callback_body(job, cost=None, usage=None)

    assert body["attempt"] == 1
    assert body["event_id"] == first_body["event_id"]

    job.status = "failed"
    job.result = None
    job.error = {"code": "JOB_EXECUTION_FAILED", "message": "failed"}

    failed_body = build_callback_body(job, cost=None, usage=None)

    assert failed_body["event"] == "job.failed"
    assert failed_body["event_id"] != first_body["event_id"]


def test_callback_response_rejects_legacy_v1_fields():
    with pytest.raises(ValueError, match="schema_version"):
        CallbackResponseEnvelope.model_validate(
            {
                "schema_version": "v1",
                "event": "job.succeeded",
                "event_id": str(uuid.uuid4()),
                "job_id": str(uuid.uuid4()),
                "job_type": "test.callback",
                "status": "succeeded",
                "msg": None,
                "data": {},
            }
        )


@pytest.mark.asyncio
async def test_deliver_callback_skips_missing_url():
    result = await deliver_callback(_job(None))

    assert result.status == "skipped"
    assert result.attempts == 0
    assert result.last_error is None


@pytest.mark.asyncio
async def test_deliver_callback_skips_invalid_url_with_error():
    result = await deliver_callback(_job("ftp://example.com/callback"))

    assert result.status == "skipped"
    assert result.attempts == 0
    assert result.last_error["code"] == "CALLBACK_URL_INVALID"


@pytest.mark.asyncio
async def test_deliver_callback_revalidates_resolved_callback_ip(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(None, None, None, "", ("10.0.0.10", 443))]

    monkeypatch.setattr("app.core.callback_security.socket.getaddrinfo", fake_getaddrinfo)

    result = await deliver_callback(_job("https://callback.example/path"))

    assert result.status == "skipped"
    assert result.attempts == 0
    assert result.last_error["code"] == "CALLBACK_URL_INVALID"
    assert "private or reserved" in result.last_error["message"]


def test_callback_signature_requires_non_empty_secret(monkeypatch):
    _patch_callback_settings(monkeypatch, CALLBACK_SIGNING_SECRET="")

    with pytest.raises(ValueError, match="CALLBACK_SIGNING_SECRET"):
        _sign("2026-06-21T00:00:00+00:00", b"{}")


@pytest.mark.asyncio
async def test_deliver_callback_records_invalid_body_contract():
    job = _job()
    job.status = "running"

    result = await deliver_callback(job)

    assert result.status == "failed"
    assert result.attempts == 1
    assert result.last_error["code"] == "CALLBACK_BODY_INVALID"
    assert "callback payload must be provided" in result.last_error["message"]


@pytest.mark.asyncio
async def test_deliver_callback_tries_once_on_http_failure(monkeypatch):
    attempts = 0

    class _Response:
        status_code = 503
        headers = _ACK_HEADERS
        text = '{"accepted":false,"msg":"temporary failure","details":{"retry_after_seconds":30}}'

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            nonlocal attempts
            attempts += 1
            return _Response()

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)

    result = await deliver_callback(_job(), payload=_callback_payload())

    assert attempts == 1
    assert result.status == "failed"
    assert result.attempts == 1
    assert result.http_status == 503
    assert result.last_error["code"] == "CALLBACK_HTTP_ERROR"
    assert result.response == result.last_error["response"]
    assert result.last_error["response"] == {
        "format": "ack",
        "valid": True,
        "accepted": False,
        "msg": "temporary failure",
        "details": {"retry_after_seconds": 30},
    }


@pytest.mark.asyncio
async def test_deliver_callback_uses_shell_callback_fields(monkeypatch):
    posted: dict = {}

    class _Response:
        status_code = 200
        headers = _ACK_HEADERS
        text = '{"accepted":true,"msg":null,"details":{}}'

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            posted["url"] = url
            posted["headers"] = headers
            posted["body"] = content
            return _Response()

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)
    _patch_callback_settings(monkeypatch, CALLBACK_SIGNING_SECRET="callback-secret")
    job = _job("https://shell.example.com/callback")
    job.callback_events = ["job.succeeded"]

    result = await deliver_callback(job, payload=_callback_payload(job))

    assert result.status == "delivered"
    assert posted["url"] == "https://shell.example.com/callback"
    assert set(posted["headers"]) == {
        "Content-Type",
        "X-Callback-Timestamp",
        "X-Callback-Signature",
    }
    assert posted["headers"]["Content-Type"] == "application/json"
    timestamp = posted["headers"]["X-Callback-Timestamp"]
    expected_signature = "sha256=" + hmac.new(
        b"callback-secret",
        timestamp.encode("utf-8") + b"." + posted["body"],
        hashlib.sha256,
    ).hexdigest()
    assert posted["headers"]["X-Callback-Signature"] == expected_signature
    body = json.loads(posted["body"].decode("utf-8"))
    assert set(body) == {
        "event",
        "event_id",
        "attempt",
        "sent_at",
        "trigger_request_id",
        "caller_id",
        "job",
    }
    assert body["job"]["job_id"] == str(job.id)
    assert body["job"]["job_result"] == {"a": 2, "b": 3, "result": 5}
    assert "job_id" not in body
    assert "data" not in body


@pytest.mark.asyncio
async def test_deliver_callback_records_ack_response_summary(monkeypatch):
    class _Response:
        status_code = 200
        headers = _ACK_HEADERS
        text = '{"accepted":true,"msg":null,"details":{"duplicate":false}}'

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response()

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)

    result = await deliver_callback(_job(), payload=_callback_payload())

    assert result.status == "delivered"
    assert result.http_status == 200
    assert result.response == {
        "format": "ack",
        "valid": True,
        "accepted": True,
        "msg": None,
        "details": {"duplicate": False},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "headers", "text", "expected_error"),
    [
        (204, _ACK_HEADERS, "", "HTTP 204"),
        (200, _ACK_HEADERS, "", "body"),
        (200, _ACK_HEADERS, "not-json", "must be JSON"),
        (200, {"Content-Type": "text/plain"}, '{"accepted":true,"msg":null,"details":{}}', "Content-Type"),
        (200, _ACK_HEADERS, '["accepted"]', "object"),
        (200, _ACK_HEADERS, '{"msg":null,"details":{}}', "accepted"),
        (200, _ACK_HEADERS, '{"accepted":"true","msg":null,"details":{}}', "boolean"),
    ],
)
async def test_deliver_callback_rejects_invalid_success_ack_contract(
    monkeypatch,
    status_code,
    headers,
    text,
    expected_error,
):
    class _Response:
        pass

    _Response.status_code = status_code
    _Response.headers = headers
    _Response.text = text

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response()

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)

    result = await deliver_callback(_job(), payload=_callback_payload())

    assert result.status == "failed"
    assert result.attempts == 1
    assert result.http_status == status_code
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["format"] == "ack"
    assert result.last_error["response"]["valid"] is False
    assert result.response == result.last_error["response"]
    assert expected_error in result.last_error["response"]["error"]


@pytest.mark.asyncio
async def test_deliver_callback_rejects_negative_ack_response(monkeypatch):
    class _Response:
        status_code = 200
        headers = _ACK_HEADERS
        text = '{"accepted":false,"msg":"duplicate rejected","details":{"duplicate":true}}'

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response()

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)

    result = await deliver_callback(_job(), payload=_callback_payload())

    assert result.status == "failed"
    assert result.attempts == 1
    assert result.http_status == 200
    assert result.last_error["code"] == "CALLBACK_ACK_REJECTED"
    assert result.response == result.last_error["response"]
    assert result.last_error["response"] == {
        "format": "ack",
        "valid": True,
        "accepted": False,
        "msg": "duplicate rejected",
        "details": {"duplicate": True},
    }


@pytest.mark.asyncio
async def test_deliver_callback_uses_job_type_ack_validator(monkeypatch):
    validation_error = "domain ack rejected: " + "x" * 600

    class _Response:
        status_code = 200
        headers = _ACK_HEADERS
        text = '{"accepted":true,"msg":null,"details":{"domain":"invalid"}}'

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response()

    class _RejectingHandler:
        def validate_public_result(self, result):
            return result

        def validate_callback_response(self, response):
            raise ValueError(validation_error)

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: _RejectingHandler())

    result = await deliver_callback(_job(), payload=_callback_payload())

    assert result.status == "failed"
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["valid"] is False
    assert result.last_error["response"]["error"] == validation_error


@pytest.mark.asyncio
async def test_deliver_callback_rejects_unstructured_json_response(monkeypatch):
    class _Response:
        status_code = 200
        headers = _ACK_HEADERS
        text = '{"status":"success","msg":null}'

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response()

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)

    result = await deliver_callback(_job(), payload=_callback_payload())

    assert result.status == "failed"
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["format"] == "ack"
    assert result.last_error["response"]["valid"] is False


@pytest.mark.asyncio
async def test_deliver_callback_rejects_invalid_ack_response(monkeypatch):
    class _Response:
        status_code = 200
        headers = _ACK_HEADERS
        text = '{"accepted":true,"msg":null,"details":[]}'

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response()

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)

    result = await deliver_callback(_job(), payload=_callback_payload())

    assert result.status == "failed"
    assert result.attempts == 1
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["format"] == "ack"
    assert result.last_error["response"]["valid"] is False
    assert "details" in result.last_error["response"]["error"]


@pytest.mark.asyncio
async def test_deliver_callback_rejects_partial_v1_like_body(monkeypatch):
    class _Response:
        status_code = 200
        headers = _ACK_HEADERS
        text = '{"status":"succeeded","msg":null,"metadata":{},"data":{}}'

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response()

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)

    result = await deliver_callback(_job(), payload=_callback_payload())

    assert result.status == "failed"
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["format"] == "ack"
    assert result.last_error["response"]["valid"] is False


@pytest.mark.asyncio
async def test_deliver_callback_for_job_records_failed_delivery_without_changing_job_status(monkeypatch):
    from app.tasks.jobs import deliver_callback_for_job

    job = _job()
    callback_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    outbox = type(
        "_Outbox",
        (),
        {
            "id": callback_id,
            "lease_token": lease_token,
            "payload": {
                "event": "job.succeeded",
                "job": {
                    "job_type": job.job_type,
                    "cost": {"currency": "USD", "amount": "0.04491750", "final": True},
                    "usage": {"ai_call_count": 2, "total_tokens": 1551, "final": True},
                },
            },
            "callback_url": job.callback_url,
            "delivery_attempts": 0,
            "last_error": None,
            "next_attempt_at": None,
        },
    )()
    commits = 0
    recorded: dict = {}

    class _DB:
        async def commit(self):
            nonlocal commits
            commits += 1

    async def fake_with_db(coro):
        return await coro(_DB())

    async def fake_get_job_or_404(_db, job_id):
        assert job_id == job.id
        return job

    async def fake_mark_callback_delivering(_db, job_id, *, now, max_attempts, next_retry_at):
        assert job_id == job.id
        assert now is not None
        assert max_attempts > 0
        recorded["claimed_next_retry_at"] = next_retry_at
        return job, outbox

    async def fake_deliver_callback(sent_job, *, payload=None, callback_url=None):
        assert sent_job is job
        assert payload["event"] == outbox.payload["event"]
        assert payload["job"]["job_type"] == job.job_type
        assert payload["job"]["cost"] == outbox.payload["job"]["cost"]
        assert payload["job"]["usage"] == outbox.payload["job"]["usage"]
        assert payload["job"]["callback"]["status"] == "delivering"
        assert payload["job"]["callback"]["attempt"] == 1
        assert payload["job"]["callback"]["next_retry_at"] is not None
        assert callback_url == job.callback_url
        return CallbackDeliveryResult(
            status="failed",
            attempts=1,
            http_status=503,
            last_error={"code": "CALLBACK_HTTP_ERROR", "status_code": 503},
            response={"format": "ack", "valid": False},
        )

    async def fake_mark_callback_result(
        _db,
        job_id,
        *,
        status,
        last_error,
        next_retry_at,
        max_attempts,
        delivery_attempts,
        last_http_status,
        last_response,
        callback_id,
        lease_token,
    ):
        recorded["result"] = {
            "job_id": job_id,
            "status": status,
            "last_error": last_error,
            "next_retry_at": next_retry_at,
            "max_attempts": max_attempts,
            "delivery_attempts": delivery_attempts,
            "last_http_status": last_http_status,
            "last_response": last_response,
            "callback_id": callback_id,
            "lease_token": lease_token,
        }

    monkeypatch.setattr("app.tasks.jobs._with_db", fake_with_db)
    monkeypatch.setattr("app.tasks.jobs.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.tasks.jobs.JobRepo.mark_callback_delivering", fake_mark_callback_delivering)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback", fake_deliver_callback)
    monkeypatch.setattr("app.tasks.jobs.JobRepo.mark_callback_result", fake_mark_callback_result)

    result = await deliver_callback_for_job(job.id)

    assert result is False
    assert commits == 2
    assert recorded["result"]["job_id"] == job.id
    assert recorded["result"]["status"] == "failed"
    assert recorded["result"]["last_error"] == {"code": "CALLBACK_HTTP_ERROR", "status_code": 503}
    assert recorded["result"]["next_retry_at"] is not None
    assert recorded["result"]["callback_id"] == callback_id
    assert recorded["result"]["delivery_attempts"] == 1
    assert recorded["result"]["last_http_status"] == 503
    assert recorded["result"]["last_response"] == {"format": "ack", "valid": False}
    assert recorded["result"]["lease_token"] == lease_token
    assert job.status == "succeeded"
