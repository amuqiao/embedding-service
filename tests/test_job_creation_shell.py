import uuid
from datetime import datetime, timezone

import pytest

from app.models.job import AIJob
from app.schemas.jobs import CreateJobRequest
from app.services.jobs import _request_fingerprint, create_job


class _FakeDB:
    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_create_job_writes_shell_fields_without_legacy_shell_payload(monkeypatch):
    captured: dict = {}
    now = datetime.now(timezone.utc)

    async def fake_create(_db, **kwargs):
        captured.update(kwargs)
        return AIJob(
            id=uuid.uuid4(),
            caller_id=kwargs["caller_id"],
            client_request_id=kwargs["client_request_id"],
            request_fingerprint=kwargs["request_fingerprint"],
            job_type=kwargs["job_type"],
            model_id=kwargs["model_id"],
            status="queued",
            progress_percent=0,
            progress_text="已排队",
            input_payload=kwargs["input_payload"],
            output_payload=kwargs["output_payload"],
            callback_payload=kwargs["callback_payload"],
            callback_url=kwargs["callback_url"],
            callback_events=kwargs["callback_events"],
            prompt_payload=kwargs["prompt_payload"],
            public_metadata=kwargs["public_metadata"],
            options_payload=kwargs["options_payload"],
            priority=kwargs["priority"],
            timeout_seconds=kwargs["timeout_seconds"],
            output_oss_bucket=kwargs["output_oss_bucket"],
            output_oss_prefix=kwargs["output_oss_prefix"],
            output_oss_region=kwargs["output_oss_region"],
            created_at=now,
            updated_at=now,
        )

    async def fake_advisory_lock(*_args):
        pass

    async def fake_get_recent(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.services.jobs.settings.MAX_ACTIVE_JOBS", 0)
    monkeypatch.setattr("app.services.jobs.JobRepo.advisory_lock_for_client_request", fake_advisory_lock)
    monkeypatch.setattr("app.services.jobs.JobRepo.get_recent_by_client_request", fake_get_recent)
    monkeypatch.setattr("app.services.jobs.JobRepo.create", fake_create)

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "generic.echo",
            "job_params": {"value": {"hello": "world"}, "label": "Echo"},
            "callback": {"url": "https://example.com/callback"},
            "metadata": {"caller_task_id": "task-1"},
            "options": {"priority": "high", "timeout_seconds": 123},
        }
    )

    job, created = await create_job(_FakeDB(), payload, "caller-1")

    assert created is True
    assert job.request_fingerprint.startswith("sha256:")
    assert captured["request_fingerprint"] == job.request_fingerprint
    assert captured["input_payload"] == {"job_params": {"value": {"hello": "world"}, "label": "Echo"}}
    assert captured["output_payload"] == {}
    assert captured["callback_payload"] == {}
    assert captured["public_metadata"] == {"caller_task_id": "task-1"}
    assert captured["options_payload"] == {"priority": "high", "timeout_seconds": 123}
    assert captured["priority"] == "high"
    assert captured["timeout_seconds"] == 123
    assert captured["callback_url"] == "https://example.com/callback"
    assert captured["callback_events"] == ["job.succeeded", "job.failed"]
    assert job.output_oss_prefix.endswith(f"{job.id}/")


@pytest.mark.asyncio
async def test_create_job_idempotency_uses_shell_request_fingerprint(monkeypatch):
    existing = AIJob(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="req-1",
        request_fingerprint=None,
        job_type="generic.echo",
        model_id=None,
        status="queued",
        progress_percent=0,
        input_payload={},
        output_payload={},
        callback_payload={},
        prompt_payload={},
        public_metadata={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    async def fake_advisory_lock(*_args):
        pass

    async def fake_get_recent(*_args, **_kwargs):
        return existing

    async def fail_create(*_args, **_kwargs):
        raise AssertionError("idempotent create should return existing job")

    monkeypatch.setattr("app.services.jobs.settings.MAX_ACTIVE_JOBS", 0)
    monkeypatch.setattr("app.services.jobs.JobRepo.advisory_lock_for_client_request", fake_advisory_lock)
    monkeypatch.setattr("app.services.jobs.JobRepo.get_recent_by_client_request", fake_get_recent)
    monkeypatch.setattr("app.services.jobs.JobRepo.create", fail_create)

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "generic.echo",
            "job_params": {"value": {"hello": "world"}, "label": "Echo"},
        }
    )
    expected_fingerprint = _request_fingerprint(payload, {"value": {"hello": "world"}, "label": "Echo"})
    existing.request_fingerprint = expected_fingerprint

    job, created = await create_job(_FakeDB(), payload, "caller-1")

    assert job is existing
    assert created is False
