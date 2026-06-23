import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.job import Job
from app.services.job_runtime import payload_hash
from app.jobs.runner import execute_job, fail_job
from app.jobs.types.register import register_all_job_types
from app.tasks import jobs as task_jobs


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


def test_should_retry_attempt_respects_platform_retry_policy(monkeypatch):
    monkeypatch.setattr(
        task_jobs,
        "get_job_type_spec",
        lambda _job_type: SimpleNamespace(platform_retry_policy="no_platform_retry"),
    )

    assert task_jobs._should_retry_attempt("job_test_add", {"code": "JOB_TIMEOUT"}) is False

    monkeypatch.setattr(
        task_jobs,
        "get_job_type_spec",
        lambda _job_type: SimpleNamespace(platform_retry_policy="retry_transient_platform_errors"),
    )

    assert task_jobs._should_retry_attempt("job_test_add", {"code": "JOB_TIMEOUT"}) is True
    assert task_jobs._should_retry_attempt("job_test_add", {"code": "MODEL_CALL_FAILED"}) is False
    assert task_jobs._should_retry_attempt("job_test_add", {"code": "AI_LEDGER_UPDATE_FAILED"}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "error_code", "expected_retryable"),
    [
        ("retry_transient_platform_errors", "JOB_TIMEOUT", True),
        ("retry_transient_platform_errors", "MODEL_CALL_FAILED", False),
        ("retry_transient_platform_errors", "AI_LEDGER_UPDATE_FAILED", False),
        ("no_platform_retry", "JOB_TIMEOUT", False),
    ],
)
async def test_run_job_attempt_failure_path_passes_policy_retryable(
    monkeypatch,
    policy,
    error_code,
    expected_retryable,
):
    attempt_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="job_test_add",
        status="running",
        execution_token=str(attempt_id),
        execution_generation=1,
        progress_percent=5,
    )
    marked: dict[str, object] = {}

    async def fake_with_db(coro):
        return await coro(_FakeDB())

    async def fake_claim_attempt_for_execution(_db, received_attempt_id, *, worker_id, lease_seconds):
        assert received_attempt_id == attempt_id
        return job, SimpleNamespace(id=attempt_id), lease_token

    async def fake_heartbeat_attempt(_db, received_attempt_id, *, lease_token: uuid.UUID, lease_seconds):
        assert received_attempt_id == attempt_id
        return True

    async def fake_execute_job(*_args, **_kwargs):
        from app.core.exceptions import AppError

        raise AppError(error_code, error_code.lower(), status_code=500)

    async def fake_mark_attempt_failed(
        _db,
        received_attempt_id,
        *,
        lease_token: uuid.UUID,
        error,
        retryable,
        next_dispatch_at,
    ):
        marked["attempt_id"] = received_attempt_id
        marked["lease_token"] = lease_token
        marked["error"] = error
        marked["retryable"] = retryable
        marked["next_dispatch_at"] = next_dispatch_at
        return True

    async def fake_deliver_callback_for_job(_job_id):
        return False

    monkeypatch.setattr(task_jobs, "_ensure_workflows_registered", lambda: None)
    monkeypatch.setattr(task_jobs, "_with_db", fake_with_db)
    monkeypatch.setattr(task_jobs.JobRepo, "claim_attempt_for_execution", fake_claim_attempt_for_execution)
    monkeypatch.setattr(task_jobs.JobRepo, "heartbeat_attempt", fake_heartbeat_attempt)
    monkeypatch.setattr(task_jobs.JobRepo, "mark_attempt_failed", fake_mark_attempt_failed)
    monkeypatch.setattr(task_jobs, "get_job_type_spec", lambda _job_type: SimpleNamespace(platform_retry_policy=policy))
    monkeypatch.setattr(task_jobs, "deliver_callback_for_job", fake_deliver_callback_for_job)
    monkeypatch.setattr("app.jobs.runner.execute_job", fake_execute_job)

    with pytest.raises(Exception) as exc:
        await task_jobs.run_job_attempt.original_func(str(attempt_id))

    assert exc.value.code == error_code
    assert marked["attempt_id"] == attempt_id
    assert marked["lease_token"] == lease_token
    assert marked["error"]["code"] == error_code
    assert marked["retryable"] is expected_retryable
    assert marked["next_dispatch_at"] is not None


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
