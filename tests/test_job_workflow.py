import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.models.job import Job
from app.services.job_runtime import payload_hash
from app.jobs.runner import execute_job, fail_job
from app.jobs.types.register import register_all_job_types
from app.tasks import jobs as task_jobs
from app.workflows.orchestrator import (
    _create_child_job,
    advance_workflow_after_child_terminal,
    create_ready_child_jobs,
    reconcile_workflow_root,
)


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
async def test_workflow_child_validation_rejects_non_text_model_for_builtin_text_runtime(monkeypatch):
    class BuiltinTextHandler:
        timeout_seconds = 300
        max_attempts = 1

        def normalize_job_params(self, job_params):
            return job_params

        def validate_normalized_job_params(self, job_params):
            pass

        def runtime_job_fields(self, job_params):
            return {"model_id": "image-model"}

        def job_type_spec(self):
            return SimpleNamespace(execution_mode="builtin_llm_text_runtime")

    root_job = _running_add_job()
    monkeypatch.setattr("app.workflows.orchestrator.get_job_executor", lambda job_type: BuiltinTextHandler())

    def reject_non_text_model(model_id):
        raise AppError("MODEL_NOT_AVAILABLE", f"模型不支持文本生成: {model_id}")

    monkeypatch.setattr("app.workflows.orchestrator.require_enabled_text_model", reject_non_text_model)
    monkeypatch.setattr(
        "app.workflows.orchestrator.JobRepo.create",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("child job should not be created")),
    )

    with pytest.raises(AppError) as exc:
        await _create_child_job(
            _FakeDB(),
            root_job=root_job,
            node={"key": "child-1", "job_type": "job_real_llm_echo", "job_params": {"value": 1}},
        )

    assert exc.value.code == "MODEL_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_workflow_child_validation_rejects_non_text_model_for_custom_text_executor(monkeypatch):
    class CustomTextHandler:
        timeout_seconds = 300
        max_attempts = 1
        requires_text_generation_model = True

        def normalize_job_params(self, job_params):
            return job_params

        def validate_normalized_job_params(self, job_params):
            pass

        def runtime_job_fields(self, job_params):
            return {"model_id": "image-model"}

        def job_type_spec(self):
            return SimpleNamespace(execution_mode="custom_executor")

    root_job = _running_add_job()
    monkeypatch.setattr("app.workflows.orchestrator.get_job_executor", lambda job_type: CustomTextHandler())

    def reject_non_text_model(model_id):
        raise AppError("MODEL_NOT_AVAILABLE", f"模型不支持文本生成: {model_id}")

    monkeypatch.setattr("app.workflows.orchestrator.require_enabled_text_model", reject_non_text_model)
    monkeypatch.setattr(
        "app.workflows.orchestrator.JobRepo.create",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("child job should not be created")),
    )

    with pytest.raises(AppError) as exc:
        await _create_child_job(
            _FakeDB(),
            root_job=root_job,
            node={"key": "child-1", "job_type": "job_real_llm_double_echo", "job_params": {"value": 1}},
        )

    assert exc.value.code == "MODEL_NOT_AVAILABLE"


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

        raise AppError(error_code, error_code.lower())

    async def fake_mark_attempt_failed(
        _db,
        received_attempt_id,
        *,
        lease_token: uuid.UUID,
        error,
        retryable,
        next_attempt_at,
    ):
        marked["attempt_id"] = received_attempt_id
        marked["lease_token"] = lease_token
        marked["error"] = error
        marked["retryable"] = retryable
        marked["next_attempt_at"] = next_attempt_at
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
    assert marked["next_attempt_at"] is not None


@pytest.mark.asyncio
async def test_publish_job_attempt_marks_published_before_enqueue(monkeypatch):
    attempt_id = uuid.uuid4()
    dispatch = SimpleNamespace(id=uuid.uuid4())
    lease_token = uuid.uuid4()
    calls: list[str] = []

    async def fake_with_db(coro):
        return await coro(_FakeDB())

    async def fake_lease_dispatch_for_publish(_db, received_attempt_id, *, lease_seconds):
        assert received_attempt_id == attempt_id
        calls.append("lease")
        return dispatch, lease_token

    async def fake_kiq(received_attempt_id):
        assert received_attempt_id == str(attempt_id)
        calls.append("kiq")

    async def fake_mark_dispatch_published(_db, dispatch_id, *, lease_token: uuid.UUID, next_attempt_at):
        assert dispatch_id == dispatch.id
        calls.append("mark")
        return True

    monkeypatch.setattr(task_jobs, "_with_db", fake_with_db)
    monkeypatch.setattr(task_jobs.JobRepo, "lease_dispatch_for_publish", fake_lease_dispatch_for_publish)
    monkeypatch.setattr(task_jobs.run_job_attempt, "kiq", fake_kiq)
    monkeypatch.setattr(task_jobs.JobRepo, "mark_dispatch_published", fake_mark_dispatch_published)

    await task_jobs.publish_job_attempt(attempt_id)

    assert calls == ["lease", "mark", "kiq"]


@pytest.mark.asyncio
async def test_publish_job_attempt_defers_after_uncertain_enqueue(monkeypatch):
    attempt_id = uuid.uuid4()
    dispatch = SimpleNamespace(id=uuid.uuid4())
    lease_token = uuid.uuid4()
    calls: list[str] = []

    async def fake_with_db(coro):
        return await coro(_FakeDB())

    async def fake_lease_dispatch_for_publish(_db, received_attempt_id, *, lease_seconds):
        assert received_attempt_id == attempt_id
        calls.append("lease")
        return dispatch, lease_token

    async def fake_mark_dispatch_published(_db, dispatch_id, *, lease_token: uuid.UUID, next_attempt_at):
        assert dispatch_id == dispatch.id
        calls.append("mark")
        return True

    async def fake_kiq(received_attempt_id):
        assert received_attempt_id == str(attempt_id)
        calls.append("kiq")
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(task_jobs, "_with_db", fake_with_db)
    monkeypatch.setattr(task_jobs.JobRepo, "lease_dispatch_for_publish", fake_lease_dispatch_for_publish)
    monkeypatch.setattr(task_jobs.JobRepo, "mark_dispatch_published", fake_mark_dispatch_published)
    monkeypatch.setattr(task_jobs.run_job_attempt, "kiq", fake_kiq)

    with pytest.raises(task_jobs.TaskiqPublishDeferredError) as exc:
        await task_jobs.publish_job_attempt(attempt_id)

    assert calls == ["lease", "mark", "kiq"]
    assert exc.value.attempt_id == attempt_id
    assert exc.value.error["code"] == "TASKIQ_PUBLISH_FAILED"
    assert exc.value.error["details"]["type"] == "RuntimeError"


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
async def test_execute_workflow_root_creates_ready_internal_child_jobs(monkeypatch):
    register_all_job_types()
    root_params = {"workflow": True}
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-workflow-1",
        job_type="test.workflow",
        status="running",
        progress_percent=5,
        progress_stage="running",
        execution_token="attempt-root",
        execution_generation=1,
        priority="normal",
        timeout_seconds=300,
        job_params_hash=payload_hash(root_params),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "test.workflow",
                "job_params_hash": payload_hash(root_params),
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "root/",
                    "oss_region": "region",
                },
                "workflow_plan": {
                    "schema_version": 1,
                    "kind": "dag_lite",
                    "workflow_type": "test.workflow",
                    "workflow_version": 1,
                    "failure_policy": "fail_fast",
                    "max_nodes": 10,
                    "node_count": 2,
                    "nodes": [
                        {
                            "key": "first",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "hello", "repeat": 1},
                            "depends_on": [],
                            "required": True,
                            "weight": 1,
                        },
                        {
                            "key": "second",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "done", "repeat": 1},
                            "depends_on": ["first"],
                            "required": True,
                            "weight": 1,
                        },
                    ],
                },
            },
        },
        created_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
    )
    root_attempt_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    created_children = []
    created_attempt_ids = []
    published_attempt_ids = []
    heartbeats = []

    async def fake_get_job_or_404(_db, job_id):
        assert job_id == root_job.id
        return root_job

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
        assert job_id == root_job.id
        assert execution_token == "attempt-root"
        assert execution_generation == 1
        root_job.progress_percent = progress_percent
        root_job.progress_stage = progress_stage
        return True

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_job.id
        assert statuses is None
        return []

    async def fake_get_internal_child_by_node_key(_db, *, root_job_id, workflow_node_key):
        assert root_job_id == root_job.id
        assert workflow_node_key == "first"
        return None

    async def fake_create(_db, **kwargs):
        assert kwargs["root_job_id"] == root_job.id
        assert kwargs["parent_job_id"] == root_job.id
        assert kwargs["is_internal"] is True
        assert kwargs["workflow_node_key"] == "first"
        assert kwargs["client_request_id"] is None
        assert kwargs["callback_url"] is None
        child = Job(
            id=uuid.uuid4(),
            caller_id=kwargs["caller_id"],
            job_type=kwargs["job_type"],
            status="queued",
            progress_percent=0,
            priority=kwargs["priority"],
            timeout_seconds=kwargs["timeout_seconds"],
            root_job_id=kwargs["root_job_id"],
            parent_job_id=kwargs["parent_job_id"],
            is_internal=kwargs["is_internal"],
            workflow_node_key=kwargs["workflow_node_key"],
            created_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
        )
        created_children.append(child)
        return child

    async def fake_create_initial_attempt(_db, child, *, timeout_seconds):
        attempt_id = uuid.uuid4()
        child.active_attempt_id = attempt_id
        created_attempt_ids.append(attempt_id)
        return SimpleNamespace(id=attempt_id)

    async def fake_heartbeat_attempt(_db, attempt_id, *, lease_token: uuid.UUID, lease_seconds):
        heartbeats.append((attempt_id, lease_token, lease_seconds))
        return True

    async def fake_mark_workflow_orchestration_attempt_succeeded(_db, attempt_id, *, lease_token: uuid.UUID):
        assert attempt_id == root_attempt_id
        root_job.active_attempt_id = None
        root_job.execution_token = None
        root_job.progress_stage = "planning"
        return True

    async def fake_publish_job_attempt(attempt_id):
        published_attempt_ids.append(attempt_id)
        raise task_jobs.TaskiqPublishDeferredError(attempt_id, {"code": "TASKIQ_PUBLISH_FAILED"})

    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr(
        "app.workflows.orchestrator.JobRepo.get_internal_child_by_node_key",
        fake_get_internal_child_by_node_key,
    )
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.create", fake_create)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.create_initial_attempt", fake_create_initial_attempt)
    monkeypatch.setattr("app.jobs.runner.JobRepo.heartbeat_attempt", fake_heartbeat_attempt)
    monkeypatch.setattr(
        "app.jobs.runner.JobRepo.mark_workflow_orchestration_attempt_succeeded",
        fake_mark_workflow_orchestration_attempt_succeeded,
    )
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", fake_publish_job_attempt)
    monkeypatch.setattr(
        "app.jobs.runner.get_job_executor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("root executor must not run")),
    )

    result = await execute_job(
        _FakeDB(),
        root_job.id,
        execution_generation=1,
        attempt_id=root_attempt_id,
        lease_token=lease_token,
    )

    assert result == {
        "job_id": str(root_job.id),
        "status": "succeeded",
        "workflow_status": "orchestrated",
        "created_child_jobs": 1,
    }
    assert [child.workflow_node_key for child in created_children] == ["first"]
    assert published_attempt_ids == created_attempt_ids
    assert root_job.status == "running"
    assert root_job.active_attempt_id is None
    assert heartbeats and heartbeats[0][0] == root_attempt_id


@pytest.mark.asyncio
async def test_create_ready_child_jobs_does_not_duplicate_existing_child(monkeypatch):
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        priority="normal",
        timeout_seconds=300,
    )
    existing_child = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="job_test_echo",
        status="queued",
        is_internal=True,
        root_job_id=root_job.id,
        parent_job_id=root_job.id,
        workflow_node_key="first",
        progress_percent=0,
        priority="normal",
        timeout_seconds=300,
    )
    workflow_plan = {
        "schema_version": 1,
        "kind": "dag_lite",
        "workflow_type": "test.workflow",
        "workflow_version": 1,
        "failure_policy": "fail_fast",
        "max_nodes": 10,
        "node_count": 2,
        "nodes": [
            {
                "key": "first",
                "job_type": "job_test_echo",
                "job_params": {"message": "hello", "repeat": 1},
                "depends_on": [],
            },
            {
                "key": "second",
                "job_type": "job_test_echo",
                "job_params": {"message": "done", "repeat": 1},
                "depends_on": ["first"],
            },
        ],
    }

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_job.id
        assert statuses is None
        return [existing_child]

    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr(
        "app.workflows.orchestrator.JobRepo.get_internal_child_by_node_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("existing child should short-circuit")),
    )
    monkeypatch.setattr(
        "app.workflows.orchestrator.JobRepo.create",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("existing child must not be duplicated")),
    )

    result = await create_ready_child_jobs(_FakeDB(), root_job=root_job, workflow_plan=workflow_plan)

    assert result.created_child_job_ids == ()
    assert result.created_attempt_ids == ()


@pytest.mark.asyncio
async def test_create_ready_child_jobs_rejects_mismatched_persisted_plan_header():
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        priority="normal",
        timeout_seconds=300,
    )
    workflow_plan = {
        "schema_version": 1,
        "kind": "dag_lite",
        "workflow_type": "test.workflow",
        "workflow_version": 1,
        "failure_policy": "fail_fast",
        "max_nodes": 10,
        "node_count": 2,
        "nodes": [
            {
                "key": "first",
                "job_type": "job_test_echo",
                "job_params": {"message": "hello", "repeat": 1},
                "depends_on": [],
            }
        ],
    }

    with pytest.raises(AppError) as exc:
        await create_ready_child_jobs(_FakeDB(), root_job=root_job, workflow_plan=workflow_plan)

    assert exc.value.code == "RUNTIME_REF_INVALID"


@pytest.mark.asyncio
async def test_reconcile_workflow_root_creates_missing_ready_child(monkeypatch):
    register_all_job_types()
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        progress_percent=20,
        active_attempt_id=None,
        is_internal=False,
        priority="normal",
        timeout_seconds=300,
        job_params_hash=payload_hash({"workflow": True}),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "test.workflow",
                "job_params_hash": payload_hash({"workflow": True}),
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "root/",
                    "oss_region": "region",
                },
                "workflow_plan": {
                    "schema_version": 1,
                    "kind": "dag_lite",
                    "workflow_type": "test.workflow",
                    "workflow_version": 1,
                    "failure_policy": "fail_fast",
                    "max_nodes": 10,
                    "node_count": 1,
                    "nodes": [
                        {
                            "key": "first",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "hello", "repeat": 1},
                            "depends_on": [],
                            "required": True,
                            "weight": 1,
                        }
                    ],
                },
            },
        },
    )
    created_children = []
    created_attempt_ids = []

    async def fake_get_workflow_root_for_update(_db, root_job_id):
        assert root_job_id == root_job.id
        return root_job

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_job.id
        assert statuses is None
        return created_children

    async def fake_get_internal_child_by_node_key(_db, *, root_job_id, workflow_node_key):
        assert root_job_id == root_job.id
        assert workflow_node_key == "first"
        return None

    async def fake_create(_db, **kwargs):
        child = Job(
            id=uuid.uuid4(),
            caller_id=kwargs["caller_id"],
            job_type=kwargs["job_type"],
            status="queued",
            progress_percent=0,
            priority=kwargs["priority"],
            timeout_seconds=kwargs["timeout_seconds"],
            root_job_id=kwargs["root_job_id"],
            parent_job_id=kwargs["parent_job_id"],
            is_internal=kwargs["is_internal"],
            workflow_node_key=kwargs["workflow_node_key"],
        )
        created_children.append(child)
        return child

    async def fake_create_initial_attempt(_db, child, *, timeout_seconds):
        attempt_id = uuid.uuid4()
        child.active_attempt_id = attempt_id
        created_attempt_ids.append(attempt_id)
        return SimpleNamespace(id=attempt_id)

    async def fake_update_progress(*_args, **_kwargs):
        return True

    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.get_workflow_root_for_update", fake_get_workflow_root_for_update)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr(
        "app.workflows.orchestrator.JobRepo.get_internal_child_by_node_key",
        fake_get_internal_child_by_node_key,
    )
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.create", fake_create)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.create_initial_attempt", fake_create_initial_attempt)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.update_progress", fake_update_progress)

    result = await reconcile_workflow_root(_FakeDB(), root_job_id=root_job.id)

    assert [child.workflow_node_key for child in created_children] == ["first"]
    assert result.root_job_id == root_job.id
    assert result.created_child_job_ids == tuple(child.id for child in created_children)
    assert result.created_attempt_ids == tuple(created_attempt_ids)
    assert result.finalized_root_job_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "nodes", "message_fragment"),
    [
        ({"schema_version": True}, None, "schema_version"),
        ({"workflow_version": 0}, None, "workflow_version"),
        ({"failure_policy": "retry_all"}, None, "failure_policy"),
        ({"max_nodes": 0}, None, "max_nodes"),
        ({"node_count": True}, None, "node_count"),
        (
            {"max_nodes": 1, "node_count": 2},
            [
                {
                    "key": "first",
                    "job_type": "job_test_echo",
                    "job_params": {"message": "hello", "repeat": 1},
                    "depends_on": [],
                },
                {
                    "key": "second",
                    "job_type": "job_test_echo",
                    "job_params": {"message": "done", "repeat": 1},
                    "depends_on": [],
                },
            ],
            "max_nodes",
        ),
    ],
)
async def test_create_ready_child_jobs_rejects_invalid_persisted_plan_header(
    overrides,
    nodes,
    message_fragment,
):
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        priority="normal",
        timeout_seconds=300,
    )
    workflow_plan = {
        "schema_version": 1,
        "kind": "dag_lite",
        "workflow_type": "test.workflow",
        "workflow_version": 1,
        "failure_policy": "fail_fast",
        "max_nodes": 10,
        "node_count": 1,
        "nodes": nodes
        or [
            {
                "key": "first",
                "job_type": "job_test_echo",
                "job_params": {"message": "hello", "repeat": 1},
                "depends_on": [],
            }
        ],
    }
    workflow_plan.update(overrides)

    with pytest.raises(AppError) as exc:
        await create_ready_child_jobs(_FakeDB(), root_job=root_job, workflow_plan=workflow_plan)

    assert exc.value.code == "RUNTIME_REF_INVALID"
    assert message_fragment in exc.value.message


@pytest.mark.asyncio
async def test_advance_workflow_after_child_success_creates_downstream_ready_child(monkeypatch):
    register_all_job_types()
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        progress_percent=20,
        active_attempt_id=None,
        is_internal=False,
        priority="normal",
        timeout_seconds=300,
        job_params_hash=payload_hash({"workflow": True}),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "test.workflow",
                "job_params_hash": payload_hash({"workflow": True}),
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "root/",
                    "oss_region": "region",
                },
                "workflow_plan": {
                    "schema_version": 1,
                    "kind": "dag_lite",
                    "workflow_type": "test.workflow",
                    "workflow_version": 1,
                    "failure_policy": "fail_fast",
                    "max_nodes": 10,
                    "node_count": 2,
                    "nodes": [
                        {
                            "key": "first",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "hello", "repeat": 1},
                            "depends_on": [],
                            "required": True,
                            "weight": 1,
                        },
                        {
                            "key": "second",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "done", "repeat": 1},
                            "depends_on": ["first"],
                            "required": True,
                            "weight": 1,
                        },
                    ],
                },
            },
        },
    )
    first_child = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="job_test_echo",
        status="succeeded",
        result={"message": "hello"},
        progress_percent=100,
        is_internal=True,
        root_job_id=root_job.id,
        parent_job_id=root_job.id,
        workflow_node_key="first",
        priority="normal",
        timeout_seconds=300,
    )
    created_children = []
    created_attempt_ids = []
    finalized = []

    async def fake_get_workflow_root_for_update(_db, root_job_id):
        assert root_job_id == root_job.id
        return root_job

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_job.id
        assert statuses is None
        return [first_child]

    async def fake_get_internal_child_by_node_key(_db, *, root_job_id, workflow_node_key):
        assert root_job_id == root_job.id
        assert workflow_node_key == "second"
        return None

    async def fake_create(_db, **kwargs):
        child = Job(
            id=uuid.uuid4(),
            caller_id=kwargs["caller_id"],
            job_type=kwargs["job_type"],
            status="queued",
            progress_percent=0,
            priority=kwargs["priority"],
            timeout_seconds=kwargs["timeout_seconds"],
            root_job_id=kwargs["root_job_id"],
            parent_job_id=kwargs["parent_job_id"],
            is_internal=kwargs["is_internal"],
            workflow_node_key=kwargs["workflow_node_key"],
        )
        created_children.append(child)
        return child

    async def fake_create_initial_attempt(_db, child, *, timeout_seconds):
        attempt_id = uuid.uuid4()
        created_attempt_ids.append(attempt_id)
        return SimpleNamespace(id=attempt_id)

    async def fail_finalize(*_args, **_kwargs):
        finalized.append(True)
        return True

    async def fake_update_progress(*_args, **_kwargs):
        return True

    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.get_workflow_root_for_update", fake_get_workflow_root_for_update)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr(
        "app.workflows.orchestrator.JobRepo.get_internal_child_by_node_key",
        fake_get_internal_child_by_node_key,
    )
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.create", fake_create)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.create_initial_attempt", fake_create_initial_attempt)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.mark_workflow_root_succeeded", fail_finalize)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.mark_workflow_root_failed", fail_finalize)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.update_progress", fake_update_progress)

    result = await advance_workflow_after_child_terminal(_FakeDB(), child_job=first_child)

    assert [child.workflow_node_key for child in created_children] == ["second"]
    assert result.created_child_job_ids == tuple(child.id for child in created_children)
    assert result.created_attempt_ids == tuple(created_attempt_ids)
    assert result.finalized_root_job_id is None
    assert finalized == []


@pytest.mark.asyncio
async def test_advance_workflow_after_last_child_success_finalizes_root(monkeypatch):
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        progress_percent=80,
        active_attempt_id=None,
        is_internal=False,
        priority="normal",
        timeout_seconds=300,
        job_params_hash=payload_hash({"workflow": True}),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "test.workflow",
                "job_params_hash": payload_hash({"workflow": True}),
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "root/",
                    "oss_region": "region",
                },
                "workflow_plan": {
                    "schema_version": 1,
                    "kind": "dag_lite",
                    "workflow_type": "test.workflow",
                    "workflow_version": 1,
                    "failure_policy": "fail_fast",
                    "max_nodes": 10,
                    "node_count": 1,
                    "nodes": [
                        {
                            "key": "only",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "done", "repeat": 1},
                            "depends_on": [],
                            "required": True,
                            "weight": 1,
                        }
                    ],
                },
            },
        },
    )
    child = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="job_test_echo",
        status="succeeded",
        result={"message": "done"},
        progress_percent=100,
        is_internal=True,
        root_job_id=root_job.id,
        parent_job_id=root_job.id,
        workflow_node_key="only",
        priority="normal",
        timeout_seconds=300,
    )
    finalized = {}

    async def fake_get_workflow_root_for_update(_db, root_job_id):
        assert root_job_id == root_job.id
        return root_job

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_job.id
        assert statuses is None
        return [child]

    async def fake_mark_workflow_root_succeeded(_db, root_job_id, *, result, canonical_result):
        finalized["root_job_id"] = root_job_id
        finalized["result"] = result
        finalized["canonical_result"] = canonical_result
        return True

    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.get_workflow_root_for_update", fake_get_workflow_root_for_update)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.mark_workflow_root_succeeded", fake_mark_workflow_root_succeeded)

    result = await advance_workflow_after_child_terminal(_FakeDB(), child_job=child)

    assert result.finalized_root_job_id == root_job.id
    assert result.root_status == "succeeded"
    assert finalized["root_job_id"] == root_job.id
    assert finalized["result"]["workflow"]["outcome"] == "success"
    assert finalized["result"]["workflow"]["nodes"][0]["result"] == {"message": "done"}


@pytest.mark.asyncio
async def test_poster_title_image_workflow_root_public_result_uses_join_result(monkeypatch):
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="poster_title_image",
        status="running",
        progress_percent=80,
        active_attempt_id=None,
        is_internal=False,
        priority="normal",
        timeout_seconds=600,
        job_params_hash=payload_hash({"items": []}),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "poster_title_image",
                "job_params_hash": payload_hash({"items": []}),
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "root/",
                    "oss_region": "region",
                },
                "workflow_plan": {
                    "schema_version": 1,
                    "kind": "dag_lite",
                    "workflow_type": "poster_title_image",
                    "workflow_version": 1,
                    "failure_policy": "fail_fast",
                    "max_nodes": 60,
                    "node_count": 3,
                    "nodes": [
                        {
                            "key": "probe.0",
                            "job_type": "poster_title_image_style_probe",
                            "job_params": {},
                            "depends_on": [],
                            "required": True,
                            "weight": 1,
                        },
                        {
                            "key": "item.es",
                            "job_type": "poster_title_image_generate_item",
                            "job_params": {},
                            "depends_on": ["probe.0"],
                            "required": True,
                            "weight": 1,
                        },
                        {
                            "key": "join",
                            "job_type": "poster_title_image_join",
                            "job_params": {},
                            "depends_on": ["item.es", "probe.0"],
                            "required": True,
                            "weight": 1,
                        },
                    ],
                }
            },
        },
    )
    poster_result = {
        "schema_version": "default",
        "job_type": "poster_title_image",
        "batch_summary": {"total": 1, "succeeded": 1, "failed": 0, "running": 0, "pending": 0},
        "items": [
            {
                "item_id": "es",
                "language": "es",
                "status": "succeeded",
                "images": [
                    {
                        "object": {
                            "public_url": "https://local-dev.oss-local.aliyuncs.com/output/title.png",
                            "internal_url": "https://local-dev.oss-local-internal.aliyuncs.com/output/title.png",
                            "content_type": "image/png",
                            "sha256": "c" * 64,
                        }
                    }
                ],
                "error": None,
            }
        ],
        "duration_ms": {"ai_model": 9, "total": 9},
    }
    children = [
        Job(
            id=uuid.uuid4(),
            caller_id="caller-1",
            job_type="poster_title_image_style_probe",
            status="succeeded",
            result={"style_key": "a", "style_desc": "heavy"},
            progress_percent=100,
            is_internal=True,
            root_job_id=root_job.id,
            parent_job_id=root_job.id,
            workflow_node_key="probe.0",
            priority="normal",
            timeout_seconds=300,
        ),
        Job(
            id=uuid.uuid4(),
            caller_id="caller-1",
            job_type="poster_title_image_generate_item",
            status="succeeded",
            result={"item": poster_result["items"][0], "duration_ms": {"ai_model": 9, "total": 9}},
            progress_percent=100,
            is_internal=True,
            root_job_id=root_job.id,
            parent_job_id=root_job.id,
            workflow_node_key="item.es",
            priority="normal",
            timeout_seconds=600,
        ),
        Job(
            id=uuid.uuid4(),
            caller_id="caller-1",
            job_type="poster_title_image_join",
            status="succeeded",
            result=poster_result,
            progress_percent=100,
            is_internal=True,
            root_job_id=root_job.id,
            parent_job_id=root_job.id,
            workflow_node_key="join",
            priority="normal",
            timeout_seconds=120,
        ),
    ]
    finalized = {}

    async def fake_get_workflow_root_for_update(_db, root_job_id):
        assert root_job_id == root_job.id
        return root_job

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_job.id
        assert statuses is None
        return children

    async def fake_mark_workflow_root_succeeded(_db, root_job_id, *, result, canonical_result):
        finalized["root_job_id"] = root_job_id
        finalized["result"] = result
        finalized["canonical_result"] = canonical_result
        return True

    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.get_workflow_root_for_update", fake_get_workflow_root_for_update)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.mark_workflow_root_succeeded", fake_mark_workflow_root_succeeded)

    result = await reconcile_workflow_root(_FakeDB(), root_job_id=root_job.id)

    assert result.finalized_root_job_id == root_job.id
    assert finalized["result"] == poster_result
    assert finalized["canonical_result"]["workflow"]["outcome"] == "success"
    assert finalized["canonical_result"]["workflow"]["nodes"][2]["node_key"] == "join"


@pytest.mark.asyncio
async def test_advance_workflow_after_required_child_failed_finalizes_root_failed(monkeypatch):
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        progress_percent=50,
        active_attempt_id=None,
        is_internal=False,
        priority="normal",
        timeout_seconds=300,
        job_params_hash=payload_hash({"workflow": True}),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "test.workflow",
                "job_params_hash": payload_hash({"workflow": True}),
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "root/",
                    "oss_region": "region",
                },
                "workflow_plan": {
                    "schema_version": 1,
                    "kind": "dag_lite",
                    "workflow_type": "test.workflow",
                    "workflow_version": 1,
                    "failure_policy": "fail_fast",
                    "max_nodes": 10,
                    "node_count": 1,
                    "nodes": [
                        {
                            "key": "only",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "done", "repeat": 1},
                            "depends_on": [],
                            "required": True,
                            "weight": 1,
                        }
                    ],
                },
            },
        },
    )
    child = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="job_test_echo",
        status="failed",
        error={"code": "MODEL_CALL_FAILED", "message": "failed", "details": {}},
        progress_percent=100,
        is_internal=True,
        root_job_id=root_job.id,
        parent_job_id=root_job.id,
        workflow_node_key="only",
        priority="normal",
        timeout_seconds=300,
    )
    finalized = {}

    async def fake_get_workflow_root_for_update(_db, root_job_id):
        assert root_job_id == root_job.id
        return root_job

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_job.id
        assert statuses is None
        return [child]

    async def fake_mark_workflow_root_failed(_db, root_job_id, *, error):
        finalized["root_job_id"] = root_job_id
        finalized["error"] = error
        return True

    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.get_workflow_root_for_update", fake_get_workflow_root_for_update)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.mark_workflow_root_failed", fake_mark_workflow_root_failed)

    result = await advance_workflow_after_child_terminal(_FakeDB(), child_job=child)

    assert result.finalized_root_job_id == root_job.id
    assert result.root_status == "failed"
    assert finalized["error"]["code"] == "WORKFLOW_CHILD_FAILED"
    assert finalized["error"]["details"]["workflow_node_key"] == "only"


@pytest.mark.asyncio
async def test_advance_workflow_allow_partial_finalizes_partial_success(monkeypatch):
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        progress_percent=80,
        active_attempt_id=None,
        is_internal=False,
        priority="normal",
        timeout_seconds=300,
        job_params_hash=payload_hash({"workflow": True}),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "test.workflow",
                "job_params_hash": payload_hash({"workflow": True}),
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "root/",
                    "oss_region": "region",
                },
                "workflow_plan": {
                    "schema_version": 1,
                    "kind": "dag_lite",
                    "workflow_type": "test.workflow",
                    "workflow_version": 1,
                    "failure_policy": "allow_partial",
                    "max_nodes": 10,
                    "node_count": 2,
                    "nodes": [
                        {
                            "key": "ok",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "ok", "repeat": 1},
                            "depends_on": [],
                            "required": True,
                            "weight": 1,
                        },
                        {
                            "key": "bad",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "bad", "repeat": 1},
                            "depends_on": [],
                            "required": True,
                            "weight": 1,
                        },
                    ],
                },
            },
        },
    )
    ok_child = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="job_test_echo",
        status="succeeded",
        result={"message": "ok"},
        progress_percent=100,
        is_internal=True,
        root_job_id=root_job.id,
        parent_job_id=root_job.id,
        workflow_node_key="ok",
        priority="normal",
        timeout_seconds=300,
    )
    bad_child = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="job_test_echo",
        status="failed",
        error={"code": "MODEL_CALL_FAILED", "message": "failed", "details": {}},
        progress_percent=100,
        is_internal=True,
        root_job_id=root_job.id,
        parent_job_id=root_job.id,
        workflow_node_key="bad",
        priority="normal",
        timeout_seconds=300,
    )
    finalized = {}

    async def fake_get_workflow_root_for_update(_db, root_job_id):
        assert root_job_id == root_job.id
        return root_job

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_job.id
        assert statuses is None
        return [ok_child, bad_child]

    async def fake_mark_workflow_root_succeeded(_db, root_job_id, *, result, canonical_result):
        finalized["root_job_id"] = root_job_id
        finalized["result"] = result
        finalized["canonical_result"] = canonical_result
        return True

    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.get_workflow_root_for_update", fake_get_workflow_root_for_update)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.mark_workflow_root_succeeded", fake_mark_workflow_root_succeeded)

    result = await advance_workflow_after_child_terminal(_FakeDB(), child_job=bad_child)

    assert result.finalized_root_job_id == root_job.id
    assert result.root_status == "succeeded"
    assert finalized["result"]["workflow"]["outcome"] == "partial_success"
    assert finalized["result"]["workflow"]["succeeded"] == 1
    assert finalized["result"]["workflow"]["failed"] == 1


@pytest.mark.asyncio
async def test_advance_workflow_allow_partial_finalizes_when_failed_dependency_blocks_required_child(monkeypatch):
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        progress_percent=80,
        active_attempt_id=None,
        is_internal=False,
        priority="normal",
        timeout_seconds=300,
        job_params_hash=payload_hash({"workflow": True}),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "test.workflow",
                "job_params_hash": payload_hash({"workflow": True}),
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "root/",
                    "oss_region": "region",
                },
                "workflow_plan": {
                    "schema_version": 1,
                    "kind": "dag_lite",
                    "workflow_type": "test.workflow",
                    "workflow_version": 1,
                    "failure_policy": "allow_partial",
                    "max_nodes": 10,
                    "node_count": 2,
                    "nodes": [
                        {
                            "key": "optional-upstream",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "bad", "repeat": 1},
                            "depends_on": [],
                            "required": False,
                            "weight": 1,
                        },
                        {
                            "key": "required-downstream",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "blocked", "repeat": 1},
                            "depends_on": ["optional-upstream"],
                            "required": True,
                            "weight": 1,
                        },
                    ],
                },
            },
        },
    )
    failed_optional = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="job_test_echo",
        status="failed",
        error={"code": "MODEL_CALL_FAILED", "message": "failed", "details": {}},
        progress_percent=100,
        is_internal=True,
        root_job_id=root_job.id,
        parent_job_id=root_job.id,
        workflow_node_key="optional-upstream",
        priority="normal",
        timeout_seconds=300,
    )
    finalized = {}

    async def fake_get_workflow_root_for_update(_db, root_job_id):
        assert root_job_id == root_job.id
        return root_job

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_job.id
        assert statuses is None
        return [failed_optional]

    async def fake_mark_workflow_root_failed(_db, root_job_id, *, error):
        finalized["root_job_id"] = root_job_id
        finalized["error"] = error
        return True

    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.get_workflow_root_for_update", fake_get_workflow_root_for_update)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.mark_workflow_root_failed", fake_mark_workflow_root_failed)

    result = await advance_workflow_after_child_terminal(_FakeDB(), child_job=failed_optional)

    assert result.finalized_root_job_id == root_job.id
    assert result.root_status == "failed"
    assert finalized["error"]["details"]["workflow_node_key"] == "optional-upstream"


@pytest.mark.asyncio
async def test_advance_workflow_skips_callback_side_effect_for_already_terminal_root(monkeypatch):
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="test.workflow",
        status="succeeded",
        progress_percent=100,
        active_attempt_id=None,
        is_internal=False,
        priority="normal",
        timeout_seconds=300,
    )
    child = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="job_test_echo",
        status="succeeded",
        progress_percent=100,
        is_internal=True,
        root_job_id=root_job.id,
        parent_job_id=root_job.id,
        workflow_node_key="only",
        priority="normal",
        timeout_seconds=300,
    )

    async def fake_get_workflow_root_for_update(_db, root_job_id):
        assert root_job_id == root_job.id
        return root_job

    monkeypatch.setattr("app.workflows.orchestrator.JobRepo.get_workflow_root_for_update", fake_get_workflow_root_for_update)

    result = await advance_workflow_after_child_terminal(_FakeDB(), child_job=child)

    assert result.root_job_id == root_job.id
    assert result.root_status == "succeeded"
    assert result.finalized_root_job_id is None


@pytest.mark.asyncio
async def test_execute_workflow_root_rejects_cyclic_persisted_workflow_plan(monkeypatch):
    root_params = {"workflow": True}
    root_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-workflow-1",
        job_type="test.workflow",
        status="running",
        progress_percent=5,
        progress_stage="running",
        execution_token="attempt-root",
        execution_generation=1,
        priority="normal",
        timeout_seconds=300,
        job_params_hash=payload_hash(root_params),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "test.workflow",
                "job_params_hash": payload_hash(root_params),
                "runtime_fields": {},
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "bucket",
                    "oss_prefix": "root/",
                    "oss_region": "region",
                },
                "workflow_plan": {
                    "schema_version": 1,
                    "kind": "dag_lite",
                    "workflow_type": "test.workflow",
                    "workflow_version": 1,
                    "failure_policy": "fail_fast",
                    "max_nodes": 10,
                    "node_count": 2,
                    "nodes": [
                        {
                            "key": "first",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "hello", "repeat": 1},
                            "depends_on": ["second"],
                        },
                        {
                            "key": "second",
                            "job_type": "job_test_echo",
                            "job_params": {"message": "done", "repeat": 1},
                            "depends_on": ["first"],
                        },
                    ],
                },
            },
        },
        created_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
    )

    async def fake_get_job_or_404(_db, job_id):
        assert job_id == root_job.id
        return root_job

    async def fake_update_progress(*_args, **_kwargs):
        return True

    async def fake_heartbeat_attempt(*_args, **_kwargs):
        return True

    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.jobs.runner.JobRepo.heartbeat_attempt", fake_heartbeat_attempt)

    with pytest.raises(AppError) as exc:
        await execute_job(
            _FakeDB(),
            root_job.id,
            execution_generation=1,
            attempt_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
        )

    assert exc.value.code == "RUNTIME_REF_INVALID"


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
async def test_execute_internal_child_success_runs_workflow_advance_side_effects(monkeypatch):
    register_all_job_types()
    job = _running_add_job()
    job.is_internal = True
    job.root_job_id = uuid.uuid4()
    job.parent_job_id = job.root_job_id
    job.workflow_node_key = "first"
    attempt_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    advance_result = SimpleNamespace(
        root_job_id=job.root_job_id,
        created_attempt_ids=(uuid.uuid4(),),
        finalized_root_job_id=job.root_job_id,
    )
    captured = {}

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_update_progress(*_args, **_kwargs):
        return True

    async def fake_mark_succeeded(_db, _job_id, **_kwargs):
        job.status = "succeeded"
        return True

    async def fake_mark_attempt_succeeded(_db, _attempt_id, *, lease_token: uuid.UUID):
        return True

    async def fake_advance(_db, *, child_job):
        captured["advanced_child_job_id"] = child_job.id
        return advance_result

    async def fake_handle_workflow_advance_result(result):
        captured["side_effect_result"] = result

    async def fail_deliver_callback_for_job(_job_id):
        raise AssertionError("internal child should not deliver caller callback")

    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.jobs.runner.JobRepo.mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr("app.jobs.runner.JobRepo.mark_attempt_succeeded", fake_mark_attempt_succeeded)
    monkeypatch.setattr("app.jobs.runner.advance_workflow_after_child_terminal", fake_advance)
    monkeypatch.setattr("app.tasks.jobs.handle_workflow_advance_result", fake_handle_workflow_advance_result)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fail_deliver_callback_for_job)

    result = await execute_job(_FakeDB(), job.id, execution_generation=1, attempt_id=attempt_id, lease_token=lease_token)

    assert result["status"] == "succeeded"
    assert captured["advanced_child_job_id"] == job.id
    assert captured["side_effect_result"] is advance_result


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


@pytest.mark.asyncio
async def test_fail_job_rejects_active_attempt_without_dangling_attempt(monkeypatch):
    job = _running_add_job()
    job.is_internal = True
    job.root_job_id = uuid.uuid4()
    job.parent_job_id = job.root_job_id
    job.workflow_node_key = "first"
    job.active_attempt_id = uuid.uuid4()
    error = {"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}}

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fail_mark_failed(*_args, **_kwargs):
        raise AssertionError("active attempt must be settled through mark_attempt_failed")

    db = _FakeDB()
    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.mark_failed", fail_mark_failed)

    with pytest.raises(AppError) as exc:
        await fail_job(db, job_id=job.id, error=error)

    assert exc.value.code == "JOB_STATE_TRANSITION_CONFLICT"
    assert db.commits == 0
