import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.core.exceptions import AppError
from app.models.job import AIJob
from app.schemas.jobs import CallbackResponseEnvelope
from app.services.callbacks import (
    CallbackDeliveryResult,
    build_callback_body,
    deliver_callback,
)
from app.services.job_runtime import payload_hash
from app.services.jobs import _job_to_response


@pytest.fixture(autouse=True)
def _callback_test_handler(monkeypatch):
    class Handler:
        def build_callback_data(self, job):
            return job.result if isinstance(job.result, dict) else {}

        def validate_callback_response(self, response):
            pass

        def validate_public_result(self, result):
            return result

    monkeypatch.setattr("app.core.workflow_registry.get", lambda _job_type: Handler())


def _job(callback_url: str | None = "https://example.com/callback") -> AIJob:
    now = datetime.now(timezone.utc)
    job_params = {"a": 2, "b": 3}
    job_params_hash = payload_hash(job_params)
    return AIJob(
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
        callback_status="pending",
        callback_attempts=0,
        callback_next_retry_at=None,
        callback_last_error=None,
        created_at=now,
        updated_at=now,
        finished_at=now,
    )


def test_build_callback_body_uses_public_fields():
    job = _job()
    body = build_callback_body(job)

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
        "callback": {
            "status": "pending",
            "attempt": 0,
            "last_error": None,
            "next_retry_at": None,
        },
        "status_url": f"{settings.SERVICE_API_PREFIX}/jobs/{job.id}",
        "created_at": job.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": job.updated_at.isoformat().replace("+00:00", "Z"),
        "finished_at": job.finished_at.isoformat().replace("+00:00", "Z"),
    }


def test_build_callback_body_uses_arithmetic_public_result_schema(monkeypatch):
    from app.workflows.arithmetic import ArithmeticWorkflow

    monkeypatch.setattr("app.core.workflow_registry.get", lambda _job_type: ArithmeticWorkflow())
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

    body = build_callback_body(job)

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
        build_callback_body(job)
    assert exc.value.code == "JOB_VIEW_CONTRACT_INVALID"


def test_job_view_exposes_callback_delivery_state():
    next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    job = _job()
    job.callback_status = "failed"
    job.callback_attempts = 2
    job.callback_next_retry_at = next_retry_at
    job.callback_last_error = {"code": "CALLBACK_HTTP_ERROR", "status_code": 503}

    job_view = _job_to_response(job, request_id="req-view")

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
    job.callback_status = "skipped"
    job.callback_attempts = 0
    job.callback_last_error = None

    job_view = _job_to_response(job, request_id="req-view")

    assert job_view.callback.status == "not_configured"
    assert job_view.callback.attempt == 0
    assert job_view.callback.last_error is None


def test_job_view_maps_skipped_callback_with_error_to_failed():
    job = _job()
    job.callback_status = "skipped"
    job.callback_attempts = 0
    job.callback_last_error = {"code": "CALLBACK_URL_INVALID", "message": "callback.url must be HTTPS"}

    job_view = _job_to_response(job, request_id="req-view")

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
    job.callback_attempts = 2

    body = build_callback_body(job)

    assert body["attempt"] == 3


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
async def test_deliver_callback_records_invalid_body_contract():
    job = _job()
    job.status = "running"

    result = await deliver_callback(job)

    assert result.status == "failed"
    assert result.attempts == 1
    assert result.last_error["code"] == "CALLBACK_BODY_INVALID"


@pytest.mark.asyncio
async def test_deliver_callback_tries_once_on_http_failure(monkeypatch):
    attempts = 0

    class _Response:
        status_code = 503
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

    result = await deliver_callback(_job())

    assert attempts == 1
    assert result.status == "failed"
    assert result.attempts == 1
    assert result.last_error["code"] == "CALLBACK_HTTP_ERROR"
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
        status_code = 204
        text = ""

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
    job = _job("https://shell.example.com/callback")
    job.callback_events = ["job.succeeded"]

    result = await deliver_callback(job)

    assert result.status == "delivered"
    assert posted["url"] == "https://shell.example.com/callback"
    assert posted["headers"]["X-AI-Service-Event"] == "job.succeeded"
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

    result = await deliver_callback(_job())

    assert result.status == "delivered"
    assert result.response == {
        "format": "ack",
        "valid": True,
        "accepted": True,
        "msg": None,
        "details": {"duplicate": False},
    }


@pytest.mark.asyncio
async def test_deliver_callback_rejects_negative_ack_response(monkeypatch):
    class _Response:
        status_code = 200
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

    result = await deliver_callback(_job())

    assert result.status == "failed"
    assert result.attempts == 1
    assert result.last_error["code"] == "CALLBACK_ACK_REJECTED"
    assert result.last_error["response"] == {
        "format": "ack",
        "valid": True,
        "accepted": False,
        "msg": "duplicate rejected",
        "details": {"duplicate": True},
    }


@pytest.mark.asyncio
async def test_deliver_callback_uses_job_type_ack_validator(monkeypatch):
    class _Response:
        status_code = 200
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
            raise ValueError("domain ack rejected")

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)
    monkeypatch.setattr("app.core.workflow_registry.get", lambda _job_type: _RejectingHandler())

    result = await deliver_callback(_job())

    assert result.status == "failed"
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["valid"] is False
    assert "domain ack rejected" in result.last_error["response"]["error"]


@pytest.mark.asyncio
async def test_deliver_callback_rejects_unstructured_json_response(monkeypatch):
    class _Response:
        status_code = 200
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

    result = await deliver_callback(_job())

    assert result.status == "failed"
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["format"] == "ack"
    assert result.last_error["response"]["valid"] is False


@pytest.mark.asyncio
async def test_deliver_callback_rejects_invalid_ack_response(monkeypatch):
    class _Response:
        status_code = 200
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

    result = await deliver_callback(_job())

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

    result = await deliver_callback(_job())

    assert result.status == "failed"
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["format"] == "ack"
    assert result.last_error["response"]["valid"] is False


@pytest.mark.asyncio
async def test_deliver_callback_for_job_records_failed_delivery_without_changing_job_status(monkeypatch):
    from app.tasks.jobs import deliver_callback_for_job

    job = _job()
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
        return True

    async def fake_deliver_callback(sent_job):
        assert sent_job is job
        assert sent_job.callback_status == "delivering"
        assert sent_job.callback_next_retry_at == recorded["claimed_next_retry_at"]
        return CallbackDeliveryResult(
            status="failed",
            attempts=1,
            last_error={"code": "CALLBACK_HTTP_ERROR", "status_code": 503},
        )

    async def fake_mark_callback_result(_db, job_id, *, status, attempts_increment, last_error, next_retry_at):
        recorded["result"] = {
            "job_id": job_id,
            "status": status,
            "attempts_increment": attempts_increment,
            "last_error": last_error,
            "next_retry_at": next_retry_at,
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
    assert recorded["result"]["attempts_increment"] == 1
    assert recorded["result"]["last_error"] == {"code": "CALLBACK_HTTP_ERROR", "status_code": 503}
    assert recorded["result"]["next_retry_at"] is not None
    assert job.status == "succeeded"
