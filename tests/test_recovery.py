import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.job import AIJob
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


async def _no_jobs(*_args, **_kwargs):
    return []


async def _cleanup(*_args, **_kwargs):
    return 0


def _stale_running_job(*, execution_attempts: int = 1, execution_generation: int = 1) -> AIJob:
    return AIJob(
        id=uuid.uuid4(),
        status="running",
        progress_stage="success_side_effect",
        execution_attempts=execution_attempts,
        execution_generation=execution_generation,
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )


def _patch_common_recovery(monkeypatch, stale_jobs):
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_orphaned_queued_jobs", _no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_unpublished_queued_jobs", _no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_stale_running_jobs", stale_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_due_callbacks", _no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.cleanup_expired_jobs", _cleanup)


@pytest.mark.asyncio
async def test_recovery_redispatches_stale_running_job_as_whole_job(monkeypatch):
    job = _stale_running_job(execution_attempts=1, execution_generation=1)
    dispatched: list[dict[str, str]] = []

    async def stale_jobs(*_args, **_kwargs):
        return [job]

    async def requeue(_db, job_id, *, new_task_id, max_execution_attempts):
        assert job_id == job.id
        assert max_execution_attempts == 3
        job.status = "queued"
        job.execution_generation += 1
        job.celery_task_id = new_task_id
        return True

    async def mark_published(_db, job_id, task_id):
        assert job_id == job.id
        assert task_id == job.celery_task_id
        return True

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("stale running jobs below max attempts should be requeued")

    class _DispatchTask:
        @staticmethod
        def apply_async(*, args, task_id):
            dispatched.append({"job_id": args[0], "task_id": task_id})

    _patch_common_recovery(monkeypatch, stale_jobs)
    monkeypatch.setattr("app.tasks.recovery.settings.JOB_MAX_EXECUTION_ATTEMPTS", 3)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.requeue_stale_running_for_recovery", requeue)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_celery_published", mark_published)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_failed_if_running", fail_if_called)
    monkeypatch.setattr("app.tasks.jobs.dispatch_job_task", _DispatchTask)

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 1
    assert result["failed"] == 0
    assert job.status == "queued"
    assert job.execution_generation == 2
    assert dispatched == [{"job_id": str(job.id), "task_id": job.celery_task_id}]


@pytest.mark.asyncio
async def test_recovery_marks_stale_running_job_failed_after_max_execution_attempts(monkeypatch):
    job = _stale_running_job(execution_attempts=3)
    delivered: list[str] = []

    async def stale_jobs(*_args, **_kwargs):
        return [job]

    async def mark_failed(_db, job_id, error):
        assert job_id == job.id
        assert error["code"] == "JOB_TIMEOUT"
        assert error["details"]["execution_attempts"] == 3
        assert error["details"]["max_execution_attempts"] == 3
        job.status = "failed"
        job.error = error
        return True

    async def requeue_if_called(*_args, **_kwargs):
        raise AssertionError("jobs at max attempts must not be requeued")

    async def deliver_callback(job_id):
        delivered.append(str(job_id))
        return True

    _patch_common_recovery(monkeypatch, stale_jobs)
    monkeypatch.setattr("app.tasks.recovery.settings.JOB_MAX_EXECUTION_ATTEMPTS", 3)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_failed_if_running", mark_failed)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.requeue_stale_running_for_recovery", requeue_if_called)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", deliver_callback)

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 0
    assert result["failed"] == 1
    assert job.status == "failed"
    assert delivered == [str(job.id)]


@pytest.mark.asyncio
async def test_recovery_skips_stale_running_job_when_peer_already_claimed_requeue(monkeypatch):
    job = _stale_running_job(execution_attempts=1)
    dispatched: list[str] = []

    async def stale_jobs(*_args, **_kwargs):
        return [job]

    async def requeue(_db, _job_id, *, new_task_id, max_execution_attempts):
        return False

    class _DispatchTask:
        @staticmethod
        def apply_async(*, args, task_id):
            dispatched.append(task_id)

    _patch_common_recovery(monkeypatch, stale_jobs)
    monkeypatch.setattr("app.tasks.recovery.settings.JOB_MAX_EXECUTION_ATTEMPTS", 3)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.requeue_stale_running_for_recovery", requeue)
    monkeypatch.setattr("app.tasks.jobs.dispatch_job_task", _DispatchTask)

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 0
    assert result["failed"] == 0
    assert dispatched == []
