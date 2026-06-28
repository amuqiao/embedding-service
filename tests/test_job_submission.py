import uuid
from datetime import datetime, timezone

import pytest

from app.models.job import Job
from app.schemas.jobs import CreateJobRequest
from app.services.jobs import submit_job_request


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
async def test_submit_job_request_commits_then_publishes_created_job(monkeypatch):
    db = FakeDB()
    job = _job()
    recorded: dict = {}

    async def fake_create_job(_db, payload, caller_id, *, trigger_request_id):
        assert _db is db
        assert payload.job_type == "test.echo"
        assert caller_id == "caller-1"
        assert trigger_request_id == "request-1"
        return job, True

    async def fake_publish(attempt_id):
        recorded["published"] = attempt_id

    monkeypatch.setattr("app.services.jobs.create_job", fake_create_job)
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", fake_publish)
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_job_request(db, _payload(), "caller-1", request_id="request-1")

    assert response.job_id == job.id
    assert db.commits == 1
    assert db.refreshed == [job, job]
    assert recorded["published"] == job.active_attempt_id


@pytest.mark.asyncio
async def test_submit_job_request_reuses_existing_idempotent_job_without_dispatch(monkeypatch):
    db = FakeDB()
    job = _job()

    async def fake_create_job(_db, _payload, _caller_id, *, trigger_request_id):
        assert trigger_request_id == "request-1"
        return job, False

    monkeypatch.setattr("app.services.jobs.create_job", fake_create_job)
    monkeypatch.setattr(
        "app.tasks.jobs.publish_job_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("existing job should not set task id")),
    )
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_job_request(db, _payload(), "caller-1", request_id="request-1")

    assert response.job_id == job.id
    assert db.commits == 1
    assert db.refreshed == []


@pytest.mark.asyncio
async def test_submit_job_request_returns_created_job_when_publish_is_deferred_after_publish_failure(monkeypatch):
    db = FakeDB()
    task_db = FakeDB()
    job = _job()
    recorded: dict = {}

    async def fake_create_job(_db, _payload, _caller_id, *, trigger_request_id):
        assert trigger_request_id == "request-1"
        return job, True

    async def fake_kiq(attempt_id):
        recorded["kiq_attempt_id"] = attempt_id
        raise RuntimeError("broker unavailable")

    async def fake_with_db(coro):
        return await coro(task_db)

    dispatch_id = uuid.uuid4()
    lease_token = uuid.uuid4()

    async def fake_lease_dispatch_for_publish(_db, attempt_id, *, lease_seconds):
        assert _db is task_db
        recorded["leased_attempt_id"] = attempt_id
        recorded["lease_seconds"] = lease_seconds
        return type(
            "Dispatch",
            (),
            {
                "id": dispatch_id,
                "publish_retry_delay_seconds": 5,
                "max_publish_attempts": 12,
            },
        )(), lease_token

    async def fake_mark_dispatch_publish_failed(
        _db,
        received_dispatch_id,
        *,
        lease_token: uuid.UUID,
        error,
        next_attempt_at,
        max_publish_attempts,
    ):
        assert _db is task_db
        recorded["failed_dispatch_id"] = received_dispatch_id
        recorded["lease_token"] = lease_token
        recorded["error"] = error
        recorded["next_attempt_at"] = next_attempt_at
        recorded["max_publish_attempts"] = max_publish_attempts
        return True

    from app.tasks import jobs as task_jobs

    monkeypatch.setattr("app.services.jobs.create_job", fake_create_job)
    monkeypatch.setattr(task_jobs.run_job_attempt, "kiq", fake_kiq)
    monkeypatch.setattr(task_jobs, "_with_db", fake_with_db)
    monkeypatch.setattr(task_jobs.JobRepo, "lease_dispatch_for_publish", fake_lease_dispatch_for_publish)
    monkeypatch.setattr(task_jobs.JobRepo, "mark_dispatch_publish_failed", fake_mark_dispatch_publish_failed)
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_job_request(db, _payload(), "caller-1", request_id="request-1")

    assert response.job_id == job.id
    assert response.job_status == "queued"
    assert db.commits == 1
    assert task_db.commits == 2
    assert db.refreshed == [job]
    assert recorded["kiq_attempt_id"] == str(job.active_attempt_id)
    assert recorded["leased_attempt_id"] == job.active_attempt_id
    assert recorded["failed_dispatch_id"] == dispatch_id
    assert recorded["lease_token"] == lease_token
    assert recorded["error"]["code"] == "TASKIQ_PUBLISH_FAILED"
    assert recorded["max_publish_attempts"] == 12
    assert recorded["next_attempt_at"] > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_submit_job_request_exposes_unrecorded_publish_errors(monkeypatch):
    db = FakeDB()
    job = _job()

    async def fake_create_job(_db, _payload, _caller_id, *, trigger_request_id):
        assert trigger_request_id == "request-1"
        return job, True

    async def fake_publish(_attempt_id):
        raise RuntimeError("publish bookkeeping failed")

    monkeypatch.setattr("app.services.jobs.create_job", fake_create_job)
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", fake_publish)
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    with pytest.raises(RuntimeError, match="publish bookkeeping failed"):
        await submit_job_request(db, _payload(), "caller-1", request_id="request-1")

    assert db.commits == 1
    assert db.refreshed == [job]


@pytest.mark.asyncio
async def test_submit_job_request_defers_publish_failure_after_recording_failed_publish(monkeypatch):
    db = FakeDB()
    task_db = FakeDB()
    job = _job()

    async def fake_create_job(_db, _payload, _caller_id, *, trigger_request_id):
        assert trigger_request_id == "request-1"
        return job, True

    async def fake_kiq(_attempt_id):
        raise RuntimeError("broker unavailable")

    async def fake_with_db(coro):
        return await coro(task_db)

    async def fake_lease_dispatch_for_publish(_db, _attempt_id, *, lease_seconds):
        return type(
            "Dispatch",
            (),
            {
                "id": uuid.uuid4(),
                "publish_retry_delay_seconds": 5,
                "max_publish_attempts": 12,
            },
        )(), uuid.uuid4()

    async def fake_mark_dispatch_publish_failed(
        _db,
        _dispatch_id,
        *,
        lease_token,
        error,
        next_attempt_at,
        max_publish_attempts,
    ):
        assert error["code"] == "TASKIQ_PUBLISH_FAILED"
        assert max_publish_attempts == 12
        return True

    from app.tasks import jobs as task_jobs

    monkeypatch.setattr("app.services.jobs.create_job", fake_create_job)
    monkeypatch.setattr(task_jobs.run_job_attempt, "kiq", fake_kiq)
    monkeypatch.setattr(task_jobs, "_with_db", fake_with_db)
    monkeypatch.setattr(task_jobs.JobRepo, "lease_dispatch_for_publish", fake_lease_dispatch_for_publish)
    monkeypatch.setattr(task_jobs.JobRepo, "mark_dispatch_publish_failed", fake_mark_dispatch_publish_failed)
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_job_request(db, _payload(), "caller-1", request_id="request-1")

    assert response.job_id == job.id
    assert db.commits == 1
    assert db.refreshed == [job]
