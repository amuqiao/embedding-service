import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.job_repo import JobRepo


class _Result:
    rowcount = 3


class _FakeDB:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return _Result()

    async def flush(self):
        pass


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
