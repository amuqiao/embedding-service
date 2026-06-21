import uuid
from datetime import UTC, datetime

import pytest

from app.models.job import Job
from app.services.job_runtime import payload_hash
from app.jobs.runner import execute_job, fail_job
from app.jobs.types.register import register_all_job_types


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.refreshed = []

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)


def _running_add_job() -> Job:
    params = {"a": 2, "b": 3}
    return Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-add-1",
        job_type="job_test_add",
        status="running",
        progress_percent=5,
        progress_stage="running",
        execution_token="attempt-1",
        execution_generation=1,
        job_params_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "job_params",
            "payload": params,
        },
        job_params_hash=payload_hash(params),
        created_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_execute_job_runs_custom_job_without_model_runtime(monkeypatch):
    register_all_job_types()
    job = _running_add_job()
    progress_updates = []
    succeeded = {}

    async def fake_get_job_or_404(_db, job_id):
        assert job_id == job.id
        return job

    async def fake_update_progress(
        _db,
        job_id,
        *,
        progress_percent,
        progress_text,
        progress_stage,
        execution_token,
        execution_generation,
    ):
        assert job_id == job.id
        assert execution_token == "attempt-1"
        assert execution_generation == 1
        progress_updates.append((progress_percent, progress_stage, progress_text))
        job.progress_percent = progress_percent
        job.progress_stage = progress_stage
        return True

    async def fake_mark_succeeded(
        _db,
        job_id,
        *,
        execution_token,
        result,
        canonical_result,
        canonical_result_ref=None,
    ):
        assert job_id == job.id
        assert execution_token == "attempt-1"
        succeeded["result"] = result
        succeeded["canonical_result"] = canonical_result
        succeeded["canonical_result_ref"] = canonical_result_ref
        job.status = "succeeded"
        return True

    async def fake_deliver_callback_for_job(job_id):
        assert job_id == job.id
        return False

    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.jobs.runner.JobRepo.mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback_for_job)
    monkeypatch.setattr(
        "app.jobs.runner.run_ai_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("custom job should not call model runtime")),
    )

    result = await execute_job(_FakeDB(), job.id, execution_generation=1)

    assert result == {"job_id": str(job.id), "status": "succeeded"}
    assert progress_updates[0][1] == "calling_model"
    assert progress_updates[-1][1] == "success_side_effect_done"
    assert succeeded["canonical_result"] == {"a": 2, "b": 3, "result": 5}
    assert succeeded["result"] == {"a": 2, "b": 3, "result": 5}


@pytest.mark.asyncio
async def test_execute_job_marks_attempt_succeeded_in_same_success_path(monkeypatch):
    register_all_job_types()
    job = _running_add_job()
    attempt_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    marked_attempt = {}

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_update_progress(*_args, **_kwargs):
        return True

    async def fake_mark_succeeded(_db, _job_id, **_kwargs):
        job.status = "succeeded"
        return True

    async def fake_mark_attempt_succeeded(_db, received_attempt_id, *, lease_token: uuid.UUID):
        marked_attempt["attempt_id"] = received_attempt_id
        marked_attempt["lease_token"] = lease_token
        return True

    async def fake_deliver_callback_for_job(_job_id):
        return False

    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.jobs.runner.JobRepo.mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr("app.jobs.runner.JobRepo.mark_attempt_succeeded", fake_mark_attempt_succeeded)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback_for_job)

    result = await execute_job(_FakeDB(), job.id, execution_generation=1, attempt_id=attempt_id, lease_token=lease_token)

    assert result["status"] == "succeeded"
    assert marked_attempt == {"attempt_id": attempt_id, "lease_token": lease_token}


@pytest.mark.asyncio
async def test_execute_job_reports_unregistered_job_type(monkeypatch):
    job = _running_add_job()
    job.job_type = "missing.job_type"

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_update_progress(*_args, **_kwargs):
        return True

    db = _FakeDB()
    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.update_progress", fake_update_progress)

    with pytest.raises(Exception) as exc:
        await execute_job(db, job.id, execution_generation=1)

    assert exc.value.code == "INVALID_JOB_TYPE"


@pytest.mark.asyncio
async def test_execute_job_skips_stale_execution_generation(monkeypatch):
    job = _running_add_job()
    job.execution_generation = 2

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fail_update_progress(*_args, **_kwargs):
        raise AssertionError("stale generation should not execute")

    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.update_progress", fail_update_progress)

    result = await execute_job(_FakeDB(), job.id, execution_generation=1)

    assert result == {
        "job_id": str(job.id),
        "status": "skipped",
        "reason": "stale_execution_generation",
        "expected_execution_generation": 1,
        "current_execution_generation": 2,
    }


@pytest.mark.asyncio
async def test_fail_job_marks_job_failed_and_delivers_callback(monkeypatch):
    job = _running_add_job()
    error = {"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}}
    marked = {}

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_mark_failed(_db, job_id, received_error, *, execution_token):
        marked["job_id"] = job_id
        marked["error"] = received_error
        marked["execution_token"] = execution_token
        job.status = "failed"
        return True

    async def fake_deliver_callback_for_job(job_id):
        marked["callback_job_id"] = job_id
        return False

    db = _FakeDB()
    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.mark_failed", fake_mark_failed)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback_for_job)

    await fail_job(db, job_id=job.id, error=error)

    assert db.commits == 1
    assert marked == {
        "job_id": job.id,
        "error": error,
        "execution_token": "attempt-1",
        "callback_job_id": job.id,
    }
