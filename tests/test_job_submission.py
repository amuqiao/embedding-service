import uuid
from datetime import datetime, timezone

import pytest

from app.application.jobs.submission import submit_ai_job
from app.models.job import Job
from app.schemas.jobs import CreateJobRequest


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.refreshed = []

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)


def _payload() -> CreateJobRequest:
    return CreateJobRequest.model_validate(
        {
            "client_request_id": "submit-req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"ok": True}},
        }
    )


def _job() -> Job:
    return Job(
        id=uuid.uuid4(),
        active_attempt_id=uuid.uuid4(),
        job_type="test.echo",
        status="queued",
        created_at=datetime.now(timezone.utc),
    )


class _Handler:
    def validate_public_result(self, result):
        return result


@pytest.mark.asyncio
async def test_submit_ai_job_commits_then_publishes_created_job(monkeypatch):
    db = FakeDB()
    job = _job()
    recorded: dict = {}

    async def fake_create_job(_db, payload, caller_id):
        assert _db is db
        assert payload.job_type == "test.echo"
        assert caller_id == "caller-1"
        return job, True

    async def fake_publish(attempt_id):
        recorded["published"] = attempt_id

    monkeypatch.setattr("app.application.jobs.submission.create_job", fake_create_job)
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", fake_publish)
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_ai_job(db, _payload(), "caller-1")

    assert response.job_id == job.id
    assert db.commits == 1
    assert db.refreshed == [job]
    assert recorded["published"] == job.active_attempt_id


@pytest.mark.asyncio
async def test_submit_ai_job_reuses_existing_idempotent_job_without_dispatch(monkeypatch):
    db = FakeDB()
    job = _job()

    async def fake_create_job(_db, _payload, _caller_id):
        return job, False

    monkeypatch.setattr("app.application.jobs.submission.create_job", fake_create_job)
    monkeypatch.setattr(
        "app.tasks.jobs.publish_job_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("existing job should not set task id")),
    )
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_ai_job(db, _payload(), "caller-1")

    assert response.job_id == job.id
    assert db.commits == 1
    assert db.refreshed == []
