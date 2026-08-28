import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.job import DispatchOutbox, Job, JobAttempt
from app.runtime.callbacker import _run_callbacker_once
from app.runtime.dispatcher import _run_dispatcher_once
from app.tasks.recovery import _run_recovery, _stale_pending_ai_call_before


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return _ScalarResult(True)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


async def _no_attempts(*_args, **_kwargs):
    return []


async def _cleanup(*_args, **_kwargs):
    return 0


async def _no_ai_ledger_reconciliation(*_args, **_kwargs):
    return 0


async def _no_jobs(*_args, **_kwargs):
    return []


async def _no_dispatches(*_args, **_kwargs):
    return []


def _attempt(status: str = "published") -> JobAttempt:
    attempt_id = uuid.uuid4()
    return JobAttempt(
        id=attempt_id,
        job_id=uuid.uuid4(),
        purpose="business_execution",
        purpose_attempt_no=1,
        retry_chain_id=attempt_id,
        created_reason="initial",
        status=status,
        lease_token=uuid.uuid4() if status == "running" else None,
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1) if status == "running" else None,
        timeout_seconds=300,
        policy_max_attempts=2,
        policy_retry_delay_seconds=5,
        policy_backoff_kind="fixed",
        policy_retryable_error_codes=["JOB_TIMEOUT"],
        retry_policy_snapshot={
            "max_attempts": 2,
            "retry_delay_seconds": 5,
            "backoff_kind": "fixed",
            "retryable_error_codes": ["JOB_TIMEOUT"],
        },
    )


def _dispatch(attempt_id: uuid.UUID) -> DispatchOutbox:
    return DispatchOutbox(
        id=uuid.uuid4(),
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
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_workflow_roots_for_reconciliation", _no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_active_pending_attempts_missing_dispatch", _no_attempts)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_dead_lettered_pending_dispatches", _no_dispatches)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_dead_lettered_dispatch_attempt_failed", _no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_terminal_attempts_with_unpublished_dispatches", _no_dispatches)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_terminal_dispatch_reconciled_published", _no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_terminal_root_jobs_missing_callback_outbox", _no_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.cleanup_expired_jobs", _cleanup)
    monkeypatch.setattr(
        "app.tasks.recovery.AiCallLogRepo.mark_stale_pending_failed",
        _no_ai_ledger_reconciliation,
    )


@pytest.mark.asyncio
async def test_dispatcher_publishes_due_attempts(monkeypatch):
    attempt_id = uuid.uuid4()
    dispatch = _dispatch(attempt_id)
    published: list[uuid.UUID] = []

    async def due_dispatches(*_args, **_kwargs):
        return [dispatch]

    async def publish(attempt_id):
        published.append(attempt_id)

    monkeypatch.setattr("app.runtime.dispatcher.JobRepo.find_due_dispatches", due_dispatches)
    monkeypatch.setattr("app.runtime.dispatcher.publish_job_attempt", publish)

    result = await _run_dispatcher_once(_FakeDB())

    assert result["published"] == 1
    assert result["deferred"] == 0
    assert published == [attempt_id]


@pytest.mark.asyncio
async def test_recovery_rolls_back_before_unlock_and_preserves_original_error(monkeypatch):
    db = _FakeDB()

    async def fail_stale_scan(*_args, **_kwargs):
        raise RuntimeError("stale scan failed")

    _patch_common_recovery(monkeypatch, stale_attempts=fail_stale_scan)

    with pytest.raises(RuntimeError, match="stale scan failed"):
        await _run_recovery(db)

    assert db.rollbacks == 1
    assert db.commits == 1
    assert db.statements == [
        "SELECT pg_try_advisory_lock(hashtext('job_recovery_loop'))",
        "SELECT pg_advisory_unlock(hashtext('job_recovery_loop'))",
    ]


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
        retry_created_reason,
    ):
        assert attempt_id == attempt.id
        assert lease_token == attempt.lease_token
        assert error["code"] == "JOB_TIMEOUT"
        assert error_kind == "timeout"
        assert failure_phase == "lease"
        assert retryable is True
        assert retry_created_reason == "recovery_retry"
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
async def test_recovery_fails_dead_lettered_pending_dispatch(monkeypatch):
    attempt_id = uuid.uuid4()
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        attempt_id=attempt_id,
        event_id=f"job_attempt:{attempt_id}:dispatch",
        task_name="jobs.run_attempt",
        payload={"attempt_id": str(attempt_id)},
        status="dead_letter",
        dead_lettered_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        last_error={"code": "TASKIQ_PUBLISH_FAILED"},
    )
    failed_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="example_sleep",
        status="failed",
        progress_percent=0,
        priority="normal",
    )
    marked: list[uuid.UUID] = []

    async def dead_lettered_dispatches(*_args, **_kwargs):
        return [dispatch]

    async def mark_dead_lettered(_db, dispatch_id, *, error):
        assert dispatch_id == dispatch.id
        assert error["code"] == "DISPATCH_PUBLISH_EXHAUSTED"
        assert error["details"]["attempt_id"] == str(attempt_id)
        assert error["details"]["dispatch_id"] == str(dispatch.id)
        assert error["details"]["last_error"] == {"code": "TASKIQ_PUBLISH_FAILED"}
        marked.append(dispatch_id)
        return failed_job

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.find_dead_lettered_pending_dispatches",
        dead_lettered_dispatches,
    )
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.mark_dead_lettered_dispatch_attempt_failed",
        mark_dead_lettered,
    )

    result = await _run_recovery(_FakeDB())

    assert result["recovered"] == 0
    assert result["failed"] == 1
    assert result["dispatch_dead_letter_failed"] == 1
    assert marked == [dispatch.id]


@pytest.mark.asyncio
async def test_recovery_advances_workflow_after_dead_lettered_internal_child_failed(monkeypatch):
    attempt_id = uuid.uuid4()
    root_job_id = uuid.uuid4()
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        attempt_id=attempt_id,
        event_id=f"job_attempt:{attempt_id}:dispatch",
        task_name="jobs.run_attempt",
        payload={"attempt_id": str(attempt_id)},
        status="dead_letter",
        dead_lettered_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        last_error={"code": "TASKIQ_PUBLISH_FAILED"},
    )
    child = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="example_sleep",
        status="failed",
        root_job_id=root_job_id,
        workflow_node_key="first",
        progress_percent=0,
        priority="normal",
    )
    advance_result = SimpleNamespace(
        root_job_id=root_job_id,
        created_attempt_ids=(),
        finalized_root_job_id=root_job_id,
    )
    advanced = {}

    async def dead_lettered_dispatches(*_args, **_kwargs):
        return [dispatch]

    async def mark_dead_lettered(*_args, **_kwargs):
        return child

    async def advance(_db, *, child_job):
        advanced["child_job_id"] = child_job.id
        return advance_result

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.find_dead_lettered_pending_dispatches",
        dead_lettered_dispatches,
    )
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.mark_dead_lettered_dispatch_attempt_failed",
        mark_dead_lettered,
    )
    monkeypatch.setattr("app.workflows.orchestrator.advance_workflow_after_child_terminal", advance)
    monkeypatch.setattr(
        "app.tasks.jobs.handle_workflow_advance_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reconciler must not run side effects")),
    )

    result = await _run_recovery(_FakeDB())

    assert result["failed"] == 1
    assert result["dispatch_dead_letter_failed"] == 1
    assert advanced["child_job_id"] == child.id


@pytest.mark.asyncio
async def test_recovery_advances_workflow_after_stale_internal_child_failed(monkeypatch):
    attempt = _attempt("running")
    root_job_id = uuid.uuid4()
    child = Job(
        id=attempt.job_id,
        caller_id="caller-1",
        job_type="example_sleep",
        status="failed",
        root_job_id=root_job_id,
        workflow_node_key="first",
        progress_percent=100,
        priority="normal",
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

    _patch_common_recovery(monkeypatch, stale_attempts=stale_attempts)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.mark_attempt_failed", mark_failed)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.get", get_job)
    monkeypatch.setattr("app.workflows.orchestrator.advance_workflow_after_child_terminal", advance)
    monkeypatch.setattr(
        "app.tasks.jobs.handle_workflow_advance_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reconciler must not run side effects")),
    )

    result = await _run_recovery(_FakeDB())

    assert result["failed"] == 1
    assert advanced["child_job_id"] == child.id


@pytest.mark.asyncio
async def test_recovery_reconciles_workflow_root_without_running_side_effects(monkeypatch):
    root_job_id = uuid.uuid4()
    child_attempt_id = uuid.uuid4()
    root = Job(
        id=root_job_id,
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        active_attempt_id=None,
    )
    advance_result = SimpleNamespace(
        root_job_id=root_job_id,
        created_attempt_ids=(child_attempt_id,),
        finalized_root_job_id=None,
    )
    async def workflow_roots(*_args, **_kwargs):
        return [root]

    async def reconcile(_db, *, root_job_id):
        assert root_job_id == root.id
        return advance_result

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_workflow_roots_for_reconciliation", workflow_roots)
    monkeypatch.setattr("app.workflows.orchestrator.reconcile_workflow_root", reconcile)
    monkeypatch.setattr(
        "app.tasks.jobs.handle_workflow_advance_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reconciler must not run side effects")),
    )

    result = await _run_recovery(_FakeDB())

    assert result["workflow_reconciled"] == 1


@pytest.mark.asyncio
async def test_recovery_reconciles_workflow_root_without_publishing_due_dispatch(monkeypatch):
    root_job_id = uuid.uuid4()
    child_attempt_id = uuid.uuid4()
    root = Job(
        id=root_job_id,
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        active_attempt_id=None,
    )
    advance_result = SimpleNamespace(
        root_job_id=root_job_id,
        created_attempt_ids=(child_attempt_id,),
        finalized_root_job_id=None,
    )
    published = []

    async def workflow_roots(*_args, **_kwargs):
        return [root]

    async def reconcile(_db, *, root_job_id):
        assert root_job_id == root.id
        return advance_result

    async def publish(attempt_id):
        published.append(attempt_id)

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_workflow_roots_for_reconciliation", workflow_roots)
    monkeypatch.setattr("app.workflows.orchestrator.reconcile_workflow_root", reconcile)
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", publish)

    result = await _run_recovery(_FakeDB())

    assert result["workflow_reconciled"] == 1
    assert result["recovered"] == 0
    assert published == []


@pytest.mark.asyncio
async def test_recovery_reconciles_finalized_root_without_delivering_due_callback(monkeypatch):
    root_job_id = uuid.uuid4()
    root = Job(
        id=root_job_id,
        caller_id="caller-1",
        job_type="test.workflow",
        status="running",
        active_attempt_id=None,
    )
    advance_result = SimpleNamespace(
        root_job_id=root_job_id,
        created_attempt_ids=(),
        finalized_root_job_id=root_job_id,
    )
    delivered = []

    async def workflow_roots(*_args, **_kwargs):
        return [root]

    async def reconcile(_db, *, root_job_id):
        assert root_job_id == root.id
        return advance_result

    async def deliver_callback(job_id):
        delivered.append(job_id)
        return True

    async def handle_result(result):
        await deliver_callback(result.finalized_root_job_id)

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_workflow_roots_for_reconciliation", workflow_roots)
    monkeypatch.setattr("app.workflows.orchestrator.reconcile_workflow_root", reconcile)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", deliver_callback)
    monkeypatch.setattr(
        "app.tasks.jobs.handle_workflow_advance_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reconciler must not run side effects")),
    )

    result = await _run_recovery(_FakeDB())

    assert result["workflow_reconciled"] == 1
    assert result["callbacks"] == 0
    assert delivered == []


@pytest.mark.asyncio
async def test_recovery_repairs_missing_dispatch_outbox_without_publishing_attempt(monkeypatch):
    attempt = _attempt("pending")
    created = []
    published = []

    async def missing_dispatch(*_args, **_kwargs):
        return [attempt]

    async def create_dispatch(_db, *, event_job_id, attempt_id, next_attempt_at, dispatch_reason):
        assert event_job_id == attempt.job_id
        assert attempt_id == attempt.id
        assert next_attempt_at is not None
        assert dispatch_reason == "reconciler_missing_dispatch"
        created.append(attempt_id)

    async def publish(attempt_id):
        published.append(attempt_id)

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_active_pending_attempts_missing_dispatch", missing_dispatch)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.create_dispatch_outbox", create_dispatch)
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", publish)

    result = await _run_recovery(_FakeDB())

    assert result["dispatch_reconciled"] == 1
    assert result["recovered"] == 0
    assert created == [attempt.id]
    assert published == []


@pytest.mark.asyncio
async def test_recovery_repairs_future_missing_dispatch_without_early_publish(monkeypatch):
    attempt = _attempt("pending")
    scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    attempt.next_attempt_scheduled_at = scheduled_at
    created = []
    published = []

    async def missing_dispatch(*_args, **_kwargs):
        return [attempt]

    async def create_dispatch(_db, *, event_job_id, attempt_id, next_attempt_at, dispatch_reason):
        assert event_job_id == attempt.job_id
        assert attempt_id == attempt.id
        assert next_attempt_at == scheduled_at
        assert dispatch_reason == "reconciler_missing_dispatch"
        created.append(attempt_id)

    async def publish(attempt_id):
        published.append(attempt_id)

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_active_pending_attempts_missing_dispatch", missing_dispatch)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.create_dispatch_outbox", create_dispatch)
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", publish)

    result = await _run_recovery(_FakeDB())

    assert result["dispatch_reconciled"] == 1
    assert result["recovered"] == 0
    assert created == [attempt.id]
    assert published == []


@pytest.mark.asyncio
async def test_recovery_reconciles_terminal_unpublished_dispatch_without_publishing(monkeypatch):
    attempt_id = uuid.uuid4()
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        attempt_id=attempt_id,
        event_id=f"job_attempt:{attempt_id}:dispatch",
        task_name="jobs.run_attempt",
        payload={"attempt_id": str(attempt_id)},
        status="retrying",
        last_error={"code": "TASKIQ_PUBLISH_FAILED"},
    )
    reconciled: list[uuid.UUID] = []
    published: list[uuid.UUID] = []

    async def terminal_dispatches(*_args, **_kwargs):
        return [dispatch]

    async def mark_reconciled(_db, dispatch_id):
        reconciled.append(dispatch_id)
        return True

    async def publish(attempt_id):
        published.append(attempt_id)

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.find_terminal_attempts_with_unpublished_dispatches",
        terminal_dispatches,
    )
    monkeypatch.setattr(
        "app.tasks.recovery.JobRepo.mark_terminal_dispatch_reconciled_published",
        mark_reconciled,
    )
    monkeypatch.setattr("app.tasks.jobs.publish_job_attempt", publish)

    result = await _run_recovery(_FakeDB())

    assert result["dispatch_reconciled"] == 1
    assert reconciled == [dispatch.id]
    assert published == []


@pytest.mark.asyncio
async def test_recovery_repairs_missing_callback_outbox_without_delivering(monkeypatch):
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="example_sleep",
        status="succeeded",
        callback_url="https://callback.example/jobs",
    )
    ensured = []
    delivered = []

    async def terminal_jobs(*_args, **_kwargs):
        return [job]

    async def ensure_callback(_db, received_job, *, now):
        assert received_job.id == job.id
        assert now.tzinfo is not None
        ensured.append(received_job.id)
        return SimpleNamespace(next_attempt_at=now)

    async def deliver_callback(job_id):
        delivered.append(job_id)
        return True

    _patch_common_recovery(monkeypatch)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.find_terminal_root_jobs_missing_callback_outbox", terminal_jobs)
    monkeypatch.setattr("app.tasks.recovery.JobRepo.ensure_terminal_callback_outbox", ensure_callback)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", deliver_callback)

    result = await _run_recovery(_FakeDB())

    assert result["callback_reconciled"] == 1
    assert result["callbacks"] == 0
    assert ensured == [job.id]
    assert delivered == []


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
async def test_callbacker_delivers_due_callbacks(monkeypatch):
    from app.models.job import Job

    due_job = Job(id=uuid.uuid4(), job_type="example_pair", status="failed")
    delivered: list[str] = []

    async def due_callbacks(*_args, **_kwargs):
        return [due_job]

    async def deliver_callback(job_id):
        delivered.append(str(job_id))
        return True

    monkeypatch.setattr("app.runtime.callbacker.JobRepo.find_due_callbacks", due_callbacks)
    monkeypatch.setattr("app.runtime.callbacker.deliver_callback_for_job", deliver_callback)

    result = await _run_callbacker_once(_FakeDB())

    assert result["jobs"] == 1
    assert result["delivered"] == 1
    assert delivered == [str(due_job.id)]


@pytest.mark.asyncio
async def test_recovery_due_callback_uses_initialized_job_type_registry(monkeypatch):
    from app.jobs import registry as job_registry
    from app.models.job import Job
    from app.tasks.runtime import ensure_worker_runtime_initialized

    job_registry.clear_for_tests()
    ensure_worker_runtime_initialized()

    due_job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="poster_title_image",
        status="succeeded",
        callback_url="https://example.com/callback",
        callback_events=["job.succeeded"],
    )
    callback_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    outbox = SimpleNamespace(
        id=callback_id,
        lease_token=lease_token,
        payload={
            "event": "job.succeeded",
            "job": {
                "job_id": str(due_job.id),
                "job_type": due_job.job_type,
                "job_status": due_job.status,
                "callback": {"status": "retrying", "attempt": 0},
            },
        },
        callback_url=due_job.callback_url,
        delivery_attempts=0,
        last_error=None,
        next_attempt_at=None,
    )
    recorded: dict[str, object] = {}

    class _CallbackDB:
        async def commit(self):
            pass

    class _Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"accepted":true,"msg":null,"details":{}}'

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers):
            recorded["callback_url"] = url
            recorded["callback_body"] = content
            return _Response()

    async def due_callbacks(*_args, **_kwargs):
        return [due_job]

    async def fake_with_db(coro):
        return await coro(_CallbackDB())

    async def fake_get_job_or_404(_db, job_id):
        assert job_id == due_job.id
        return due_job

    async def fake_mark_callback_delivering(_db, job_id, *, now, max_attempts, next_retry_at):
        assert job_id == due_job.id
        assert max_attempts > 0
        return due_job, outbox

    async def fake_mark_callback_result(
        _db,
        job_id,
        *,
        status,
        last_error,
        next_retry_at,
        max_attempts,
        delivery_attempts,
        last_http_status,
        last_response,
        callback_id,
        lease_token,
    ):
        recorded["result"] = {
            "job_id": job_id,
            "status": status,
            "last_error": last_error,
            "next_retry_at": next_retry_at,
            "max_attempts": max_attempts,
            "delivery_attempts": delivery_attempts,
            "last_http_status": last_http_status,
            "last_response": last_response,
            "callback_id": callback_id,
            "lease_token": lease_token,
        }

    monkeypatch.setattr("app.runtime.callbacker.JobRepo.find_due_callbacks", due_callbacks)
    monkeypatch.setattr("app.tasks.jobs._with_db", fake_with_db)
    monkeypatch.setattr("app.tasks.jobs.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.tasks.jobs.JobRepo.mark_callback_delivering", fake_mark_callback_delivering)
    monkeypatch.setattr("app.tasks.jobs.JobRepo.mark_callback_result", fake_mark_callback_result)
    monkeypatch.setattr("app.services.callbacks.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "app.core.callback_security.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, "", ("93.184.216.34", 443))],
    )

    result = await _run_callbacker_once(_FakeDB())

    assert result["jobs"] == 1
    assert result["delivered"] == 1
    assert recorded["callback_url"] == due_job.callback_url
    assert recorded["result"]["status"] == "delivered"
    assert recorded["result"]["last_http_status"] == 200
    assert recorded["result"]["last_error"] is None
    assert recorded["result"]["delivery_attempts"] == 1
    assert recorded["result"]["last_response"] == {
        "format": "ack",
        "valid": True,
        "accepted": True,
        "msg": None,
        "details": {},
    }


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
