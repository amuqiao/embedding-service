import uuid
from datetime import datetime, timezone

import pytest

from app.models.job import AIJob
from app.services.callbacks import build_callback_body, deliver_callback


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
    assert body["job"]["progress"] == {"percent": 100, "message": None, "stage": None}
    assert body["job"]["result"] == {"artifacts": [], "signals": {}}
    assert body["job"]["metadata"] == {"caller_task_id": "task-1"}


@pytest.mark.asyncio
async def test_deliver_callback_skips_missing_url():
    result = await deliver_callback(_job({}))

    assert result.status == "skipped"
    assert result.attempts == 0


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
