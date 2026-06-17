import pytest
from sqlalchemy.dialects import postgresql

from app.models.job import AIJob
from app.repositories.job_repo import JobRepo


class _CleanupResult:
    rowcount = 3


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDB:
    def __init__(self):
        self.statements = []
        self.results = []
        self.flushed = False

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        if self.results:
            return self.results.pop(0)
        return _CleanupResult()

    async def flush(self):
        self.flushed = True


def _compile(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_cleanup_expired_jobs_soft_deletes_only_settled_terminal_jobs():
    db = _FakeDB()

    rowcount = await JobRepo.cleanup_expired_jobs(db)

    assert rowcount == 3
    assert len(db.statements) == 2
    job_update, work_item_update = db.statements
    assert job_update.__visit_name__ == "update"
    assert work_item_update.__visit_name__ == "update"

    job_sql = _compile(job_update)
    assert "UPDATE ai_jobs SET" in job_sql
    assert "deleted_at" in job_sql
    assert "ai_jobs.status IN" in job_sql
    assert "ai_jobs.callback_status IN" in job_sql
    assert "ai_jobs.deleted_at IS NULL" in job_sql

    work_item_sql = _compile(work_item_update)
    assert "UPDATE ai_job_work_items SET" in work_item_sql
    assert "ai_job_work_items.deleted_at IS NULL" in work_item_sql
    assert "ai_jobs.status IN" in work_item_sql


@pytest.mark.asyncio
async def test_mark_succeeded_persists_public_and_canonical_results():
    import uuid

    job = AIJob(
        id=uuid.uuid4(),
        job_type="generic.echo",
        status="running",
        celery_task_id="task-1",
        progress_percent=30,
        metadata_={},
    )
    db = _FakeDB()
    db.results.append(_ScalarResult(job))

    updated = await JobRepo.mark_succeeded(
        db,
        job.id,
        celery_task_id="task-1",
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
