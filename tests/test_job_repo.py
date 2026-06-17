import pytest
from sqlalchemy.dialects import postgresql

from app.models.job import AIJob
from app.repositories.job_repo import JobRepo


class _CleanupResult:
    rowcount = 3


class _RowcountResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


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


class _FakeDB:
    def __init__(self):
        self.statements = []
        self.parameters = []
        self.results = []
        self.flushed = False

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        self.parameters.append((args, kwargs))
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


@pytest.mark.asyncio
async def test_requeue_stale_running_for_recovery_bumps_generation_and_task_id():
    import uuid

    db = _FakeDB()
    db.results.append(_RowcountResult(1))
    job_id = uuid.uuid4()

    updated = await JobRepo.requeue_stale_running_for_recovery(
        db,
        job_id,
        new_task_id="new-task",
        max_execution_attempts=3,
    )

    assert updated is True
    assert db.flushed is True
    statement = db.statements[0]
    sql = statement.text
    assert "status='queued'" in sql
    assert "execution_generation=execution_generation + 1" in sql
    assert "execution_plan=NULL" in sql
    assert "execution_attempts < :max_execution_attempts" in sql
    params = db.parameters[0][0][0]
    assert params["job_id"] == str(job_id)
    assert params["new_task_id"] == "new-task"
    assert params["max_execution_attempts"] == 3


@pytest.mark.asyncio
async def test_list_work_items_can_filter_execution_generation():
    import uuid

    db = _FakeDB()
    db.results.append(_ScalarListResult([]))
    job_id = uuid.uuid4()

    items = await JobRepo.list_work_items(db, job_id, execution_generation=2)

    assert items == []
    statement = db.statements[0]
    sql = _compile(statement)
    assert "ai_job_work_items.execution_generation =" in sql


@pytest.mark.asyncio
async def test_update_progress_can_require_current_task_and_generation():
    import uuid

    job = AIJob(
        id=uuid.uuid4(),
        job_type="generic.echo",
        status="running",
        celery_task_id="task-1",
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
        celery_task_id="task-1",
        execution_generation=2,
    )

    assert updated is True
    assert db.flushed is True
    assert job.progress_percent == 90
    assert job.progress_stage == "success_side_effect"
    sql = _compile(db.statements[0])
    assert "ai_jobs.status =" in sql
    assert "ai_jobs.celery_task_id =" in sql
    assert "ai_jobs.execution_generation =" in sql


@pytest.mark.asyncio
async def test_set_execution_plan_can_require_current_task_and_generation():
    import uuid

    job = AIJob(
        id=uuid.uuid4(),
        job_type="generic.echo",
        status="running",
        celery_task_id="task-1",
        execution_generation=2,
    )
    db = _FakeDB()
    db.results.append(_ScalarResult(job))

    updated = await JobRepo.set_execution_plan(
        db,
        job.id,
        execution_plan={"execution_mode": "single", "execution_generation": 2},
        celery_task_id="task-1",
        execution_generation=2,
    )

    assert updated is True
    assert db.flushed is True
    assert job.execution_plan == {"execution_mode": "single", "execution_generation": 2}
    sql = _compile(db.statements[0])
    assert "ai_jobs.status =" in sql
    assert "ai_jobs.celery_task_id =" in sql
    assert "ai_jobs.execution_generation =" in sql
