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
async def test_submit_job_request_commits_created_job_and_leaves_dispatch_to_dispatcher(monkeypatch):
    db = FakeDB()
    job = _job()

    async def fake_create_job(_db, payload, caller_id, *, trigger_request_id):
        assert _db is db
        assert payload.job_type == "test.echo"
        assert caller_id == "caller-1"
        assert trigger_request_id == "request-1"
        return job, True

    monkeypatch.setattr("app.services.jobs.create_job", fake_create_job)
    monkeypatch.setattr(
        "app.tasks.jobs.publish_job_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("API must not publish Taskiq directly")),
    )
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_job_request(db, _payload(), "caller-1", request_id="request-1")

    assert response.job_id == job.id
    assert db.commits == 1
    assert db.refreshed == [job]


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
async def test_submit_job_request_does_not_publish_after_create(monkeypatch):
    db = FakeDB()
    job = _job()

    async def fake_create_job(_db, _payload, _caller_id, *, trigger_request_id):
        assert trigger_request_id == "request-1"
        return job, True


    monkeypatch.setattr("app.services.jobs.create_job", fake_create_job)
    monkeypatch.setattr(
        "app.tasks.jobs.publish_job_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("API must not publish Taskiq directly")),
    )
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_job_request(db, _payload(), "caller-1", request_id="request-1")

    assert response.job_id == job.id
    assert response.job_status == "queued"
    assert db.commits == 1
    assert db.refreshed == [job]


@pytest.mark.asyncio
async def test_submit_job_request_ignores_dispatcher_publish_errors_because_dispatcher_is_not_inline(monkeypatch):
    db = FakeDB()
    job = _job()

    async def fake_create_job(_db, _payload, _caller_id, *, trigger_request_id):
        assert trigger_request_id == "request-1"
        return job, True

    monkeypatch.setattr("app.services.jobs.create_job", fake_create_job)
    monkeypatch.setattr(
        "app.tasks.jobs.publish_job_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("publish bookkeeping failed")),
    )
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_job_request(db, _payload(), "caller-1", request_id="request-1")

    assert response.job_id == job.id
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

    monkeypatch.setattr("app.services.jobs.create_job", fake_create_job)
    monkeypatch.setattr(
        "app.tasks.jobs.publish_job_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("API must not publish Taskiq directly")),
    )
    monkeypatch.setattr("app.jobs.registry.get", lambda _job_type: _Handler())

    response = await submit_job_request(db, _payload(), "caller-1", request_id="request-1")

    assert response.job_id == job.id
    assert db.commits == 1
    assert db.refreshed == [job]
