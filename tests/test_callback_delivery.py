import uuid
from datetime import datetime, timezone

import pytest

from app.models.job import AIJob
from app.services.callbacks import deliver_callback


def _job(callback_payload: dict) -> AIJob:
    now = datetime.now(timezone.utc)
    return AIJob(
        id=uuid.uuid4(),
        job_type="novel_localization.step1_localize",
        model_id="gpt-4.1",
        status="succeeded",
        progress_percent=100,
        input_payload={},
        output_payload={},
        callback_payload=callback_payload,
        prompt_payload={},
        result_payload={"artifacts": [], "signals": {}},
        created_at=now,
        finished_at=now,
    )


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
