import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.job import AIJob
from app.services.job_lifecycle import SUCCESS_SIDE_EFFECT_STAGE
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


@pytest.mark.asyncio
async def test_recovery_redispatches_stale_success_side_effect_job(monkeypatch):
    job = AIJob(
        id=uuid.uuid4(),
        status="running",
        progress_stage=SUCCESS_SIDE_EFFECT_STAGE,
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    dispatched: list[list[str | None]] = []

    async def no_jobs(*_args, **_kwargs):
        return []

    async def stale_jobs(*_args, **_kwargs):
        return [job]

    async def cleanup(*_args, **_kwargs):
        return 0

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("success side-effect jobs must not be failed")

    async def claim_recovery(_db, job_id, *, progress_stage):
        assert job_id == job.id
        assert progress_stage == SUCCESS_SIDE_EFFECT_STAGE
        job.last_heartbeat_at = datetime.now(timezone.utc)
        return True

    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_orphaned_queued_jobs", no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_unpublished_queued_jobs", no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_stale_running_jobs", stale_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_due_callbacks", no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.cleanup_expired_jobs", cleanup)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_failed_if_running", fail_if_called)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_success_side_effect_recovery_dispatched", claim_recovery)

    class _FinalizeTask:
        @staticmethod
        def apply_async(*, args):
            dispatched.append(args)

    monkeypatch.setattr("app.tasks.jobs.finalize_job_task", _FinalizeTask)

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 1
    assert result["failed"] == 0
    assert dispatched == [[None, str(job.id)]]
    assert job.last_heartbeat_at > datetime.now(timezone.utc) - timedelta(seconds=5)
