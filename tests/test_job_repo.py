import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.models.job import Job, JobAttempt
from app.repositories.job_repo import JobRepo


class _CleanupResult:
    rowcount = 3


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ScalarListResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _NoRowResult:
    def one_or_none(self):
        return None


class _OneRowResult:
    def __init__(self, value):
        self.value = value

    def one_or_none(self):
        return self.value


class _FakeDB:
    def __init__(self):
        self.statements = []
        self.parameters = []
        self.results = []
        self.flushed = False
        self.added = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        self.parameters.append((args, kwargs))
        if self.results:
            return self.results.pop(0)
        return _CleanupResult()

    async def flush(self):
        self.flushed = True

    def add(self, obj):
        self.added.append(obj)


def _compile(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_cleanup_expired_jobs_soft_deletes_only_settled_terminal_jobs():
    db = _FakeDB()

    rowcount = await JobRepo.cleanup_expired_jobs(db)

    assert rowcount == 3
    assert len(db.statements) == 1
    job_update = db.statements[0]
    assert job_update.__visit_name__ == "update"

    job_sql = _compile(job_update)
    assert "UPDATE jobs SET" in job_sql
    assert "deleted_at" in job_sql
    assert "jobs.status IN" in job_sql
    assert "jobs.callback_status IN" in job_sql
    assert "jobs.deleted_at IS NULL" in job_sql


@pytest.mark.asyncio
async def test_claim_attempt_for_execution_waits_for_specific_attempt_lock():
    db = _FakeDB()
    db.results.append(_NoRowResult())

    claimed = await JobRepo.claim_attempt_for_execution(
        db,
        uuid.uuid4(),
        worker_id="worker-1",
        lease_seconds=60,
    )

    assert claimed is None
    sql = _compile(db.statements[0])
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" not in sql


@pytest.mark.asyncio
async def test_mark_attempt_published_sets_recovery_deadline():
    attempt = JobAttempt(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        attempt_no=1,
        status="queued",
        timeout_seconds=60,
    )
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    db = _FakeDB()
    db.results.append(_ScalarResult(attempt))

    updated = await JobRepo.mark_attempt_published(db, attempt.id, next_dispatch_at=deadline)

    assert updated is True
    assert db.flushed is True
    assert attempt.status == "published"
    assert attempt.next_dispatch_at == deadline
    assert attempt.dispatch_attempts == 1


@pytest.mark.asyncio
async def test_find_dispatch_due_attempts_requires_deadline_for_published_attempts():
    db = _FakeDB()
    db.results.append(_ScalarListResult([]))

    await JobRepo.find_dispatch_due_attempts(db, datetime.now(UTC), limit=10)

    sql = _compile(db.statements[0])
    assert "job_attempts.status =" in sql
    assert "job_attempts.next_dispatch_at IS NULL" in sql
    assert "job_attempts.next_dispatch_at <=" in sql


@pytest.mark.asyncio
async def test_mark_succeeded_persists_public_and_canonical_results():
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        execution_token="task-1",
        progress_percent=30,
        metadata_={},
    )
    db = _FakeDB()
    db.results.append(_ScalarResult(job))

    updated = await JobRepo.mark_succeeded(
        db,
        job.id,
        execution_token="task-1",
        result={"public": True},
        canonical_result={"canonical": True},
        canonical_result_ref={"oss_key": "result.json"},
    )

    assert updated is True
    assert db.flushed is True
    assert job.status == "succeeded"
    assert job.result == {"public": True}
    assert job.canonical_result == {"canonical": True}
    assert job.canonical_result_ref == {"oss_key": "result.json"}
    assert job.error is None
    assert job.callback_status == "not_configured"


@pytest.mark.asyncio
async def test_mark_attempt_failed_closes_attempt_when_job_already_failed():
    error = {"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}}
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="failed",
        execution_token="task-1",
        progress_percent=30,
        metadata_={},
        error=error,
    )
    attempt = JobAttempt(
        id=uuid.uuid4(),
        job_id=job.id,
        attempt_no=1,
        status="running",
        lease_token=lease_token,
        timeout_seconds=60,
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt)))

    updated = await JobRepo.mark_attempt_failed(db, attempt.id, lease_token=lease_token, error=error)

    assert updated is True
    assert db.flushed is True
    assert attempt.status == "failed"
    assert attempt.lease_token is None
    assert job.status == "failed"
    assert job.error == error


@pytest.mark.asyncio
async def test_mark_attempt_failed_creates_retry_attempt_when_allowed():
    error = {"code": "JOB_TIMEOUT", "message": "timed out", "details": {}}
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        execution_token="task-1",
        execution_generation=1,
        attempt_count=1,
        max_attempts=2,
        timeout_seconds=60,
        progress_percent=50,
        metadata_={},
    )
    attempt = JobAttempt(
        id=uuid.uuid4(),
        job_id=job.id,
        attempt_no=1,
        status="running",
        lease_token=lease_token,
        timeout_seconds=60,
    )
    next_dispatch_at = datetime.now(UTC)
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt)))

    updated = await JobRepo.mark_attempt_failed(
        db,
        attempt.id,
        lease_token=lease_token,
        error=error,
        retryable=True,
        next_dispatch_at=next_dispatch_at,
    )

    retry_attempts = [item for item in db.added if isinstance(item, JobAttempt)]
    assert updated is True
    assert attempt.status == "failed"
    assert attempt.retryable is True
    assert job.status == "queued"
    assert job.error is None
    assert job.execution_generation == 2
    assert job.attempt_count == 2
    assert len(retry_attempts) == 1
    assert retry_attempts[0].attempt_no == 2
    assert retry_attempts[0].next_dispatch_at == next_dispatch_at


@pytest.mark.asyncio
async def test_update_progress_can_require_current_task_and_generation():
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        execution_token="task-1",
        execution_generation=2,
        progress_percent=10,
    )
    db = _FakeDB()
    db.results.append(_ScalarResult(job))

    updated = await JobRepo.update_progress(
        db,
        job.id,
        progress_percent=90,
        progress_text="正在执行成功前副作用",
        progress_stage="success_side_effect",
        execution_token="task-1",
        execution_generation=2,
    )

    assert updated is True
    assert db.flushed is True
    assert job.progress_percent == 90
    assert job.progress_stage == "success_side_effect"
    sql = _compile(db.statements[0])
    assert "jobs.status =" in sql
    assert "jobs.execution_token =" in sql
    assert "jobs.execution_generation =" in sql
