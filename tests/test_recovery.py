import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.job import JobAttempt
from app.tasks.recovery import _run_recovery


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def execute(self, _statement):
        return _ScalarResult(True)

    async def commit(self):
        self.commits += 1


async def _no_attempts(*_args, **_kwargs):
    return []


async def _cleanup(*_args, **_kwargs):
    return 0


def _attempt(status: str = "published") -> JobAttempt:
    return JobAttempt(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        attempt_no=1,
        status=status,
        timeout_seconds=60,
        lease_token=uuid.uuid4() if status == "running" else None,
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1) if status == "running" else None,
    )


def _patch_common_recovery(monkeypatch, *, due_attempts=None, stale_attempts=None):
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.find_dispatch_due_attempts",
        due_attempts or _no_attempts,
    )
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.find_stale_running_attempts",
        stale_attempts or _no_attempts,
    )
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_due_callbacks", _no_attempts)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.cleanup_expired_jobs", _cleanup)


@pytest.mark.asyncio
async def test_recovery_republishes_due_attempts(monkeypatch):
    attempt = _attempt("published")
    published: list[uuid.UUID] = []

    async def due_attempts(*_args, **_kwargs):
        return [attempt]

    async def publish(attempt_id):
        published.append(attempt_id)

    _patch_common_recovery(monkeypatch, due_attempts=due_attempts)
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", publish)

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 1
    assert result["failed"] == 0
    assert published == [attempt.id]


@pytest.mark.asyncio
async def test_recovery_marks_stale_running_attempt_failed(monkeypatch):
    attempt = _attempt("running")
    delivered: list[str] = []
    marked: list[uuid.UUID] = []

    async def stale_attempts(*_args, **_kwargs):
        return [attempt]

    async def mark_failed(
        _db,
        attempt_id,
        *,
        lease_token,
        error,
        error_kind,
        failure_phase,
        retryable,
        next_dispatch_at,
    ):
        assert attempt_id == attempt.id
        assert lease_token == attempt.lease_token
        assert error["code"] == "JOB_TIMEOUT"
        assert error_kind == "timeout"
        assert failure_phase == "lease"
        assert retryable is True
        assert next_dispatch_at is not None
        marked.append(attempt_id)
        return True

    _patch_common_recovery(monkeypatch, stale_attempts=stale_attempts)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_attempt_failed", mark_failed)

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 0
    assert result["failed"] == 1
    assert marked == [attempt.id]
    assert delivered == []


@pytest.mark.asyncio
async def test_recovery_skips_stale_attempt_when_peer_already_claimed(monkeypatch):
    attempt = _attempt("running")
    delivered: list[str] = []

    async def stale_attempts(*_args, **_kwargs):
        return [attempt]

    async def mark_failed(*_args, **_kwargs):
        return False

    async def deliver_callback(job_id):
        delivered.append(str(job_id))
        return True

    _patch_common_recovery(monkeypatch, stale_attempts=stale_attempts)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_attempt_failed", mark_failed)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", deliver_callback)

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 0
    assert result["failed"] == 0
    assert delivered == []


@pytest.mark.asyncio
async def test_recovery_delivers_due_callbacks(monkeypatch):
    from app.models.job import Job

    due_job = Job(id=uuid.uuid4(), job_type="job_test_add", status="failed")
    delivered: list[str] = []

    async def due_callbacks(*_args, **_kwargs):
        return [due_job]

    async def deliver_callback(job_id):
        delivered.append(str(job_id))
        return True

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_due_callbacks", due_callbacks)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", deliver_callback)

    result = await _run_recovery(_FakeDB())

    assert result["callbacks"] == 1
    assert delivered == [str(due_job.id)]
