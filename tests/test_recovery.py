import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.job import DispatchOutbox, Job, JobAttempt
from app.tasks.recovery import _run_recovery, _stale_pending_ai_call_before


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


async def _no_ai_ledger_reconciliation(*_args, **_kwargs):
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


def _dispatch(attempt_id: uuid.UUID) -> DispatchOutbox:
    return DispatchOutbox(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        attempt_id=attempt_id,
        event_id=f"job_attempt:{attempt_id}:dispatch",
        task_name="jobs.run_attempt",
        payload={"attempt_id": str(attempt_id)},
        status="pending",
    )


def _patch_common_recovery(monkeypatch, *, due_dispatches=None, stale_attempts=None):
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.find_due_dispatches",
        due_dispatches or _no_attempts,
    )
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.find_stale_running_attempts",
        stale_attempts or _no_attempts,
    )
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_due_callbacks", _no_attempts)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.cleanup_expired_jobs", _cleanup)
    monkeypatch.setattr(
        "app.tasks.recovery.AiCallLogRepo.mark_stale_pending_failed",
        _no_ai_ledger_reconciliation,
    )


@pytest.mark.asyncio
async def test_recovery_republishes_due_attempts(monkeypatch):
    attempt_id = uuid.uuid4()
    dispatch = _dispatch(attempt_id)
    published: list[uuid.UUID] = []

    async def due_dispatches(*_args, **_kwargs):
        return [dispatch]

    async def publish(attempt_id):
        published.append(attempt_id)

    _patch_common_recovery(monkeypatch, due_dispatches=due_dispatches)
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", publish)

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 1
    assert result["failed"] == 0
    assert published == [attempt_id]


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
        next_attempt_at,
    ):
        assert attempt_id == attempt.id
        assert lease_token == attempt.lease_token
        assert error["code"] == "JOB_TIMEOUT"
        assert error_kind == "timeout"
        assert failure_phase == "lease"
        assert retryable is True
        assert next_attempt_at is not None
        marked.append(attempt_id)
        return True

    async def get_job(*_args, **_kwargs):
        return None

    _patch_common_recovery(monkeypatch, stale_attempts=stale_attempts)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_attempt_failed", mark_failed)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.get", get_job)

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 0
    assert result["failed"] == 1
    assert marked == [attempt.id]
    assert delivered == []


@pytest.mark.asyncio
async def test_recovery_advances_workflow_after_stale_internal_child_failed(monkeypatch):
    attempt = _attempt("running")
    root_job_id = uuid.uuid4()
    child = Job(
        id=attempt.job_id,
        caller_id="caller-1",
        job_type="job_test_echo",
        status="failed",
        is_internal=True,
        root_job_id=root_job_id,
        parent_job_id=root_job_id,
        workflow_node_key="first",
        progress_percent=100,
        priority="normal",
        timeout_seconds=60,
    )
    advance_result = type(
        "AdvanceResult",
        (),
        {
            "root_job_id": root_job_id,
            "created_attempt_ids": (),
            "finalized_root_job_id": root_job_id,
        },
    )()
    advanced = {}

    async def stale_attempts(*_args, **_kwargs):
        return [attempt]

    async def mark_failed(*_args, **_kwargs):
        return True

    async def get_job(*_args, **_kwargs):
        return child

    async def advance(_db, *, child_job):
        advanced["child_job_id"] = child_job.id
        return advance_result

    async def handle_result(result):
        advanced["result"] = result

    _patch_common_recovery(monkeypatch, stale_attempts=stale_attempts)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_attempt_failed", mark_failed)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.get", get_job)
    monkeypatch.setattr("app.workflows.orchestrator.advance_workflow_after_child_terminal", advance)
    monkeypatch.setattr("app.tasks.jobs.handle_workflow_advance_result", handle_result)

    result = await _run_recovery(_FakeDB())

    assert result["failed"] == 1
    assert advanced["child_job_id"] == child.id
    assert advanced["result"] is advance_result


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


@pytest.mark.asyncio
async def test_recovery_reconciles_stale_pending_ai_call_logs(monkeypatch):
    recorded: dict[str, object] = {}

    async def mark_stale_pending_failed(_db, *, before, limit):
        recorded["before"] = before
        recorded["limit"] = limit
        return 2

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr("app.tasks.recovery.AiCallLogRepo.mark_stale_pending_failed", mark_stale_pending_failed)

    result = await _run_recovery(_FakeDB())

    assert result["ai_ledger_reconciled"] == 2
    assert result["recovered"] == 0
    assert result["failed"] == 0
    assert recorded["before"].tzinfo is not None
    assert recorded["before"] < datetime.now(timezone.utc)
    assert isinstance(recorded["limit"], int)
    assert recorded["limit"] > 0


def test_ai_ledger_stale_pending_cutoff_is_after_job_stale_window():
    now = datetime.now(timezone.utc)
    before = _stale_pending_ai_call_before(now)
    age_seconds = (now - before).total_seconds()

    from app.core.config import settings

    assert age_seconds == settings.job_stale_running_seconds + 60
    assert age_seconds > settings.job_stale_running_seconds
    assert age_seconds > settings.worker_hard_time_limit
