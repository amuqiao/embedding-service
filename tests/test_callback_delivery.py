import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.job import AIJob
from app.services.callbacks import CallbackDeliveryResult, build_callback_body, deliver_callback
from app.services.jobs import _job_to_response


def _job(callback_payload: dict) -> AIJob:
    now = datetime.now(timezone.utc)
    return AIJob(
        id=uuid.uuid4(),
        job_type="novel_localization.step1_localize",
        model_id="gpt-4.1",
        status="succeeded",
        progress_percent=100,
        input_payload={"metadata": {"caller_task_id": "task-1"}},
        output_payload={},
        callback_payload=callback_payload,
        prompt_payload={},
        result_payload={"artifacts": [], "signals": {}},
        callback_status="pending",
        callback_attempts=0,
        callback_next_retry_at=None,
        callback_last_error=None,
        created_at=now,
        finished_at=now,
    )


def test_build_callback_body_wraps_job_view():
    job = _job({"url": "https://example.com/callback"})
    body = build_callback_body(job)

    assert body["event"] == "job.succeeded"
    assert body["attempt"] == 1
    assert body["event_id"]
    assert body["sent_at"]
    assert body["job"]["job_id"] == str(job.id)
    assert body["job"]["job_type"] == "novel_localization.step1_localize"
    assert body["job"] == _job_to_response(job).model_dump(mode="json")
    assert body["job"]["progress"] == {"percent": 100, "message": None, "stage": None}
    assert body["job"]["result"] == {"artifacts": [], "signals": {}}
    assert body["job"]["callback"] == {
        "status": "pending",
        "attempts": 0,
        "next_retry_at": None,
        "last_error": None,
    }
    assert body["job"]["metadata"] == {"caller_task_id": "task-1"}


def test_job_view_exposes_callback_delivery_state():
    next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    job = _job({"url": "https://example.com/callback"})
    job.callback_status = "failed"
    job.callback_attempts = 2
    job.callback_next_retry_at = next_retry_at
    job.callback_last_error = {"code": "CALLBACK_HTTP_ERROR", "status_code": 503}

    view = _job_to_response(job)

    assert view.callback.status == "failed"
    assert view.callback.attempts == 2
    assert view.callback.next_retry_at == next_retry_at
    assert view.callback.last_error == {"code": "CALLBACK_HTTP_ERROR", "status_code": 503}


def test_build_callback_body_reuses_job_view_callback_state():
    next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    job = _job({"url": "https://example.com/callback"})
    job.callback_status = "failed"
    job.callback_attempts = 2
    job.callback_next_retry_at = next_retry_at
    job.callback_last_error = {"code": "CALLBACK_HTTP_ERROR", "status_code": 503}

    body = build_callback_body(job)

    assert body["attempt"] == 3
    assert body["job"]["callback"] == {
        "status": "failed",
        "attempts": 2,
        "next_retry_at": _job_to_response(job).model_dump(mode="json")["callback"]["next_retry_at"],
        "last_error": {"code": "CALLBACK_HTTP_ERROR", "status_code": 503},
    }


@pytest.mark.asyncio
async def test_deliver_callback_skips_missing_url():
    result = await deliver_callback(_job({}))

    assert result.status == "skipped"
    assert result.attempts == 0
    assert result.last_error is None


@pytest.mark.asyncio
async def test_deliver_callback_skips_invalid_url_with_error():
    result = await deliver_callback(_job({"url": "ftp://example.com/callback"}))

    assert result.status == "skipped"
    assert result.attempts == 0
    assert result.last_error["code"] == "CALLBACK_URL_INVALID"


@pytest.mark.asyncio
async def test_deliver_callback_tries_once_on_http_failure(monkeypatch):
    attempts = 0

    class _Response:
        status_code = 503
        text = "unavailable"

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

    result = await deliver_callback(_job({"url": "https://example.com/callback"}))

    assert attempts == 1
    assert result.status == "failed"
    assert result.attempts == 1
    assert result.last_error["code"] == "CALLBACK_HTTP_ERROR"


@pytest.mark.asyncio
async def test_deliver_callback_for_job_records_failed_delivery_without_changing_job_status(monkeypatch):
    from app.tasks.jobs import deliver_callback_for_job

    job = _job({"url": "https://example.com/callback"})
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
