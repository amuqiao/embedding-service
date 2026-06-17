import uuid
from datetime import datetime, timezone

import pytest

from app.application.jobs.submission import submit_ai_job
from app.models.job import AIJob
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
            "job_type": "generic.echo",
            "job_params": {"value": {"ok": True}},
        }
    )


def _job() -> AIJob:
    return AIJob(
        id=uuid.uuid4(),
        job_type="generic.echo",
        status="queued",
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_submit_ai_job_commits_then_publishes_created_job(monkeypatch):
    db = FakeDB()
    job = _job()
    recorded: dict = {}

    async def fake_create_job(_db, payload, caller_id):
        assert _db is db
        assert payload.job_type == "generic.echo"
        assert caller_id == "caller-1"
        return job, True

    async def fake_set_celery_task_id(_db, job_id, task_id):
        recorded["set"] = (job_id, task_id)
        job.celery_task_id = task_id

    async def fake_mark_celery_published(_db, job_id, task_id):
        recorded["published"] = (job_id, task_id)

    class FakeDispatchTask:
        @staticmethod
        def apply_async(*, args, task_id):
            recorded["dispatched"] = (args, task_id)

    monkeypatch.setattr("app.application.jobs.submission.create_job", fake_create_job)
    monkeypatch.setattr("app.application.jobs.submission.JobRepo.set_celery_task_id", fake_set_celery_task_id)
    monkeypatch.setattr("app.application.jobs.submission.JobRepo.mark_celery_published", fake_mark_celery_published)
    monkeypatch.setattr("app.application.jobs.submission.dispatch_job_task", FakeDispatchTask)

    response = await submit_ai_job(db, _payload(), "caller-1")

    task_id = recorded["set"][1]
    assert response.job_id == job.id
    assert db.commits == 2
    assert db.refreshed == [job]
    assert recorded["set"] == (job.id, task_id)
    assert recorded["dispatched"] == ([str(job.id)], task_id)
    assert recorded["published"] == (job.id, task_id)


@pytest.mark.asyncio
async def test_submit_ai_job_reuses_existing_idempotent_job_without_dispatch(monkeypatch):
    db = FakeDB()
    job = _job()

    async def fake_create_job(_db, _payload, _caller_id):
        return job, False

    monkeypatch.setattr("app.application.jobs.submission.create_job", fake_create_job)
    monkeypatch.setattr(
        "app.application.jobs.submission.JobRepo.set_celery_task_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("existing job should not set task id")),
    )
    monkeypatch.setattr(
        "app.application.jobs.submission.dispatch_job_task.apply_async",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("existing job should not dispatch")),
    )

    response = await submit_ai_job(db, _payload(), "caller-1")

    assert response.job_id == job.id
    assert db.commits == 1
    assert db.refreshed == []
