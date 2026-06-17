import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.job import AIJob
from app.schemas.jobs import CallbackResponseEnvelope
from app.services.callbacks import (
    CallbackDeliveryResult,
    build_callback_body,
    deliver_callback,
    validate_callback_response_payload,
)
from app.services.job_runtime import payload_hash, write_runtime_json
from app.services.jobs import _job_to_response
from app.workflows.register import register_all_workflows


register_all_workflows()


def _job(callback_url: str | None = "https://example.com/callback") -> AIJob:
    now = datetime.now(timezone.utc)
    return AIJob(
        id=uuid.uuid4(),
        job_type="novel_localization.step1_localize",
        status="succeeded",
        progress_percent=100,
        metadata_={"caller_task_id": "task-1"},
        callback_url=callback_url,
        result={"artifacts": [], "signals": {}},
        callback_status="pending",
        callback_attempts=0,
        callback_next_retry_at=None,
        callback_last_error=None,
        created_at=now,
        finished_at=now,
    )


def test_build_callback_body_uses_public_fields():
    job = _job()
    body = build_callback_body(job)

    assert body["schema_version"] == "v1"
    assert body["event"] == "job.succeeded"
    assert body["attempt"] == 1
    assert body["event_id"]
    assert body["sent_at"]
    assert body["job_id"] == str(job.id)
    assert body["client_request_id"] is None
    assert body["job_type"] == "novel_localization.step1_localize"
    assert body["status"] == "succeeded"
    assert body["progress"] == {"percent": 100, "message": None, "stage": None}
    assert body["error"] is None
    assert body["metadata"] == {"caller_task_id": "task-1"}
    assert body["data"] == {"artifacts": [], "signals": {}}
    assert body["created_at"] == job.created_at.isoformat().replace("+00:00", "Z")
    assert body["started_at"] is None
    assert body["finished_at"] == job.finished_at.isoformat().replace("+00:00", "Z")
    assert "job" not in body
    assert "result" not in body
    assert "callback" not in body


def test_job_view_exposes_callback_delivery_state():
    next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    job = _job()
    job.callback_status = "failed"
    job.callback_attempts = 2
    job.callback_next_retry_at = next_retry_at
    job.callback_last_error = {"code": "CALLBACK_HTTP_ERROR", "status_code": 503}

    view = _job_to_response(job)

    assert view.callback.status == "failed"
    assert view.callback.attempts == 2
    assert view.callback.next_retry_at == next_retry_at
    assert view.callback.last_error == {"code": "CALLBACK_HTTP_ERROR", "status_code": 503}


def test_job_view_marks_missing_callback_not_configured():
    view = _job_to_response(_job(None))

    assert view.callback.status == "not_configured"
    assert view.callback.attempts == 0


def test_job_view_uses_shell_result_and_metadata():
    job = _job()
    job.progress_stage = "finalize"
    job.metadata_ = {"visible": "metadata"}
    job.result = {
        "artifacts": [{"key": "public", "type": "json", "label": "Public"}],
        "signals": {"public": True},
    }

    view = _job_to_response(job)

    assert view.progress.stage == "finalize"
    assert view.metadata == {"visible": "metadata"}
    assert view.result == {
        "artifacts": [{"key": "public", "type": "json", "label": "Public"}],
        "signals": {"public": True},
    }


def test_build_callback_body_uses_next_attempt_number():
    job = _job()
    job.callback_attempts = 2

    body = build_callback_body(job)

    assert body["attempt"] == 3


def test_build_callback_body_includes_short_drama_success_data():
    params = {"t_book_id": "300000000300000279"}
    job = _job()
    job.job_type = "short_drama.tagging.initial"
    job.client_request_id = "cpp:300000000300000279:initial:mock"
    job.metadata_ = {"source_service": "cpp", "business_scene": "short_drama_tagging"}
    job.job_params_ref = write_runtime_json(job, "job_params", params)
    job.job_params_hash = payload_hash(params)
    job.canonical_result = {
        "artifacts": [],
        "signals": {
            "success": False,
            "result_status": "partial_success",
            "validation_issue_count": 1,
            "validation_issues": [{"issue": "below_min_items"}],
            "reason_codes": ["below_min_items"],
            "t_book_id": "300000000300000279",
            "subtitle_language": "zh",
            "requested_schema_language": "zh",
        },
    }

    body = build_callback_body(job)

    assert body["job_id"] == str(job.id)
    assert body["client_request_id"] == "cpp:300000000300000279:initial:mock"
    assert body["data"] == {
        "t_book_id": "300000000300000279",
        "result_status": "partial_success",
        "validation_issue_count": 1,
        "validation_issues": [{"issue": "below_min_items"}],
        "reason_codes": ["below_min_items"],
        "subtitle_language": "zh",
        "requested_schema_language": "zh",
    }


def test_build_callback_body_includes_short_drama_failed_data_from_job_params():
    params = {"t_book_id": "300000000300000279"}
    job = _job()
    job.job_type = "short_drama.tagging.initial"
    job.status = "failed"
    job.error = {"code": "MODEL_OUTPUT_INVALID", "message": "bad tags", "details": {}}
    job.metadata_ = {"business_scene": "short_drama_tagging"}
    job.job_params_ref = write_runtime_json(job, "job_params", params)
    job.job_params_hash = payload_hash(params)

    body = build_callback_body(job)

    assert body["event"] == "job.failed"
    assert body["status"] == "failed"
    assert body["error"] == {"code": "MODEL_OUTPUT_INVALID", "message": "bad tags", "details": {}}
    assert body["data"] == {"t_book_id": "300000000300000279"}


def test_build_callback_body_rejects_short_drama_success_without_signals():
    params = {"t_book_id": "300000000300000279"}
    job = _job()
    job.job_type = "short_drama.tagging.initial"
    job.job_params_ref = write_runtime_json(job, "job_params", params)
    job.job_params_hash = payload_hash(params)
    job.canonical_result = None

    with pytest.raises(ValueError, match="canonical_result.signals"):
        build_callback_body(job)


def test_callback_response_handler_rejects_short_drama_data_without_acceptance_flags():
    with pytest.raises(ValueError, match="data.accepted"):
        validate_callback_response_payload(
            {
                "schema_version": "v1",
                "event": "job.succeeded",
                "event_id": str(uuid.uuid4()),
                "job_id": str(uuid.uuid4()),
                "client_request_id": "cpp:300000000300000279:initial:mock",
                "job_type": "short_drama.tagging.initial",
                "status": "succeeded",
                "msg": None,
                "metadata": {"source_service": "cpp", "business_scene": "short_drama_tagging"},
                "data": {"t_book_id": "300000000300000279"},
            }
        )


def test_callback_response_requires_common_extension_fields():
    with pytest.raises(ValueError, match="metadata"):
        CallbackResponseEnvelope.model_validate(
            {
                "schema_version": "v1",
                "event": "job.succeeded",
                "event_id": str(uuid.uuid4()),
                "job_id": str(uuid.uuid4()),
                "job_type": "novel_localization.step1_localize",
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
        text = '{"schema_version":"v1","event":"job.succeeded","event_id":"00000000-0000-0000-0000-000000000000","job_id":"00000000-0000-0000-0000-000000000000","job_type":"novel_localization.step1_localize","status":"succeeded","msg":"temporary failure","metadata":{},"data":{}}'

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
    assert result.last_error["response"]["format"] == "v1"
    assert result.last_error["response"]["valid"] is False


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
    assert b'"job_id"' in posted["body"]
    assert b'"job"' not in posted["body"]


@pytest.mark.asyncio
async def test_deliver_callback_records_v1_response_summary(monkeypatch):
    class _Response:
        status_code = 200

        def __init__(self, request_body):
            self.text = json.dumps(
                {
                    "schema_version": "v1",
                    "event": request_body["event"],
                    "event_id": request_body["event_id"],
                    "job_id": request_body["job_id"],
                    "client_request_id": request_body["client_request_id"],
                    "job_type": request_body["job_type"],
                    "status": request_body["status"],
                    "msg": None,
                    "metadata": request_body["metadata"],
                    "data": {"accepted": True, "duplicate": False},
                    "received_at": request_body["sent_at"],
                    "processed_at": request_body["sent_at"],
                }
            )

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response(json.loads(content.decode("utf-8")))

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)

    result = await deliver_callback(_job())

    assert result.status == "delivered"
    assert result.response["format"] == "v1"
    assert result.response["valid"] is True
    assert result.response["mismatches"] == []
    assert result.response["data"] == {"accepted": True, "duplicate": False}


@pytest.mark.asyncio
async def test_deliver_callback_keeps_legacy_response_compatible(monkeypatch):
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

    assert result.status == "delivered"
    assert result.response == {"format": "legacy", "status": "success", "msg": None}


@pytest.mark.asyncio
async def test_deliver_callback_does_not_treat_malformed_v1_as_legacy(monkeypatch):
    class _Response:
        status_code = 200

        def __init__(self, request_body):
            self.text = json.dumps(
                {
                    "event": request_body["event"],
                    "event_id": request_body["event_id"],
                    "job_id": request_body["job_id"],
                    "client_request_id": request_body["client_request_id"],
                    "job_type": request_body["job_type"],
                    "status": request_body["status"],
                    "msg": None,
                }
            )

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response(json.loads(content.decode("utf-8")))

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)

    result = await deliver_callback(_job())

    assert result.status == "failed"
    assert result.attempts == 1
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["format"] == "v1"
    assert result.last_error["response"]["valid"] is False
    assert "schema_version" in result.last_error["response"]["error"]


@pytest.mark.asyncio
async def test_deliver_callback_does_not_treat_partial_v1_with_data_as_json_success(monkeypatch):
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
    assert result.last_error["response"]["format"] == "v1"
    assert result.last_error["response"]["valid"] is False


@pytest.mark.asyncio
async def test_deliver_callback_rejects_short_drama_v1_response_with_invalid_data(monkeypatch):
    class _Response:
        status_code = 200

        def __init__(self, request_body):
            self.text = json.dumps(
                {
                    "schema_version": "v1",
                    "event": request_body["event"],
                    "event_id": request_body["event_id"],
                    "job_id": request_body["job_id"],
                    "client_request_id": request_body["client_request_id"],
                    "job_type": request_body["job_type"],
                    "status": request_body["status"],
                    "msg": None,
                    "metadata": request_body["metadata"],
                    "data": {"t_book_id": request_body["data"]["t_book_id"]},
                }
            )

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            return _Response(json.loads(content.decode("utf-8")))

    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)
    params = {"t_book_id": "300000000300000279"}
    job = _job()
    job.job_type = "short_drama.tagging.initial"
    job.client_request_id = "cpp:300000000300000279:initial:mock"
    job.metadata_ = {"source_service": "cpp", "business_scene": "short_drama_tagging"}
    job.job_params_ref = write_runtime_json(job, "job_params", params)
    job.job_params_hash = payload_hash(params)
    job.canonical_result = {
        "artifacts": [],
        "signals": {
            "success": True,
            "result_status": "success",
            "validation_issue_count": 0,
            "validation_issues": [],
            "reason_codes": [],
            "t_book_id": "300000000300000279",
            "subtitle_language": "zh",
            "requested_schema_language": "zh",
        },
    }

    result = await deliver_callback(job)

    assert result.status == "failed"
    assert result.last_error["code"] == "CALLBACK_RESPONSE_CONTRACT_INVALID"
    assert result.last_error["response"]["format"] == "v1"
    assert result.last_error["response"]["valid"] is False
    assert "data.accepted" in result.last_error["response"]["error"]


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
