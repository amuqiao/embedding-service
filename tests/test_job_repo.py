import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.models.job import CallbackOutbox, DispatchOutbox, Job, JobAttempt, JobEvent
from app.repositories.job_repo import JobRepo


class _CleanupResult:
    rowcount = 3


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
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

    async def refresh(self, _obj):
        return None

    def add(self, obj):
        self.added.append(obj)


class _DefaultOffResultSnapshotHandler:
    result_snapshot_statuses = frozenset()

    def supports_result_snapshot(self, status):
        return status in self.result_snapshot_statuses

    def validate_result_snapshot(self, status, result):
        if result is not None:
            raise ValueError(f"{status} result must be null")
        return None


class _FailedResultSnapshotHandler:
    result_snapshot_statuses = frozenset({"failed"})

    def __init__(self, snapshot):
        self.snapshot = snapshot

    def supports_result_snapshot(self, status):
        return status in self.result_snapshot_statuses

    async def build_result_snapshot(self, status, job, db):
        return self.snapshot

    def validate_result_snapshot(self, status, result):
        return result


def _compile(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_cleanup_expired_jobs_soft_deletes_only_settled_terminal_jobs():
    db = _FakeDB()

    rowcount = await JobRepo.cleanup_expired_jobs(db)

    assert rowcount == 3
    assert len(db.statements) == 2
    submission_key_delete = db.statements[0]
    assert submission_key_delete.__visit_name__ == "delete"
    submission_key_delete_sql = _compile(submission_key_delete)
    assert "DELETE FROM job_submission_keys" in submission_key_delete_sql
    assert "job_aggregates.expires_at <= now()" in submission_key_delete_sql
    assert "job_aggregates.deleted_at IS NULL" in submission_key_delete_sql

    job_update = db.statements[1]
    assert job_update.__visit_name__ == "update"

    job_sql = _compile(job_update)
    assert "UPDATE job_aggregates SET" in job_sql
    assert "deleted_at" in job_sql
    assert "job_aggregates.status IN" in job_sql
    assert "job_aggregates.callback_status IN" in job_sql
    assert "job_aggregates.deleted_at IS NULL" in job_sql


@pytest.mark.asyncio
async def test_get_submission_by_client_request_keeps_expired_key_visible_until_cleanup():
    db = _FakeDB()
    db.results.append(_NoRowResult())

    found = await JobRepo.get_submission_by_client_request(
        db,
        caller_id="caller-1",
        client_request_id="request-1",
    )

    assert found is None
    sql = _compile(db.statements[0])
    assert "job_submission_keys.expires_at >" not in sql
    assert "job_submission_keys.key_value" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql
    assert "job_aggregates.is_internal IS false" in sql


@pytest.mark.asyncio
async def test_get_for_caller_hides_internal_jobs_from_public_reads():
    db = _FakeDB()
    db.results.append(_ScalarResult(None))

    found = await JobRepo.get_for_caller(db, uuid.uuid4(), "caller-1")

    assert found is None
    sql = _compile(db.statements[0])
    assert "job_aggregates.caller_id =" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql
    assert "job_aggregates.is_internal IS false" in sql


@pytest.mark.asyncio
async def test_get_internal_child_by_node_key_reads_only_internal_child_for_root():
    db = _FakeDB()
    db.results.append(_ScalarResult(None))

    found = await JobRepo.get_internal_child_by_node_key(
        db,
        root_job_id=uuid.uuid4(),
        workflow_node_key="node.generate-title",
    )

    assert found is None
    sql = _compile(db.statements[0])
    assert "job_aggregates.root_job_id =" in sql
    assert "job_aggregates.workflow_node_key =" in sql
    assert "job_aggregates.is_internal IS true" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql


@pytest.mark.asyncio
async def test_list_internal_children_can_filter_by_root_and_statuses():
    db = _FakeDB()
    db.results.append(_ScalarListResult([]))

    children = await JobRepo.list_internal_children(
        db,
        root_job_id=uuid.uuid4(),
        statuses=["queued", "running"],
    )

    assert children == []
    sql = _compile(db.statements[0])
    assert "job_aggregates.root_job_id =" in sql
    assert "job_aggregates.is_internal IS true" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql
    assert "job_aggregates.status IN" in sql


@pytest.mark.asyncio
async def test_find_workflow_roots_for_reconciliation_scans_only_waiting_public_roots():
    db = _FakeDB()
    db.results.append(_ScalarListResult([]))

    roots = await JobRepo.find_workflow_roots_for_reconciliation(db, limit=10)

    assert roots == []
    sql = _compile(db.statements[0])
    assert "job_aggregates.status =" in sql
    assert "job_aggregates.active_attempt_id IS NULL" in sql
    assert "job_aggregates.is_internal IS false" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql
    assert "job_aggregates.runtime_ref" in sql
    assert "?" in sql
    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    assert "workflow_plan" in set(compiled.params.values())
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_find_active_pending_attempts_missing_dispatch_excludes_attempts_with_outbox():
    db = _FakeDB()
    db.results.append(_ScalarListResult([]))

    attempts = await JobRepo.find_active_pending_attempts_missing_dispatch(db, limit=10)

    assert attempts == []
    sql = _compile(db.statements[0])
    assert "job_aggregates.status =" in sql
    assert "job_aggregates.active_attempt_id = job_execution_attempts.id" in sql
    assert "job_execution_attempts.status =" in sql
    assert "NOT (EXISTS" in sql
    assert "dispatch_outbox.attempt_id = job_execution_attempts.id" in sql
    assert "dispatch_outbox.task_name =" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_find_terminal_root_jobs_missing_callback_outbox_excludes_internal_jobs():
    db = _FakeDB()
    db.results.append(_ScalarListResult([]))

    jobs = await JobRepo.find_terminal_root_jobs_missing_callback_outbox(db, limit=10)

    assert jobs == []
    sql = _compile(db.statements[0])
    assert "job_aggregates.status IN" in sql
    assert "job_aggregates.is_internal IS false" in sql
    assert "job_aggregates.callback_url IS NOT NULL" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql
    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    assert any(value == "job.succeeded" for value in compiled.params.values())
    assert any(value == "job.failed" for value in compiled.params.values())
    assert "NOT (EXISTS" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_create_internal_job_requires_root_job_id():
    db = _FakeDB()

    with pytest.raises(ValueError, match="internal job must include root_job_id"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id=None,
            job_type="job_test_echo",
            is_internal=True,
        )


@pytest.mark.asyncio
async def test_create_internal_job_rejects_public_submission_identity():
    db = _FakeDB()

    with pytest.raises(ValueError, match="internal job must not include client_request_id"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id="request-1",
            job_type="job_test_echo",
            root_job_id=uuid.uuid4(),
            is_internal=True,
        )


@pytest.mark.asyncio
async def test_create_internal_job_rejects_callback_intent():
    db = _FakeDB()

    with pytest.raises(ValueError, match="internal job must not include callback"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id=None,
            job_type="job_test_echo",
            root_job_id=uuid.uuid4(),
            is_internal=True,
            callback_url="https://callback.example/jobs",
        )


@pytest.mark.asyncio
async def test_create_public_job_rejects_child_lineage_fields():
    root_job_id = uuid.uuid4()
    db = _FakeDB()

    with pytest.raises(ValueError, match="parent_job_id requires internal job"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id="request-1",
            job_type="job_test_echo",
            root_job_id=root_job_id,
            parent_job_id=root_job_id,
        )

    with pytest.raises(ValueError, match="workflow_node_key requires internal job"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id="request-1",
            job_type="job_test_echo",
            root_job_id=root_job_id,
            workflow_node_key="node.generate-title",
        )


@pytest.mark.asyncio
async def test_create_assigns_workflow_lineage_fields():
    root_job_id = uuid.uuid4()
    db = _FakeDB()

    job = await JobRepo.create(
        db,
        caller_id="caller-1",
        client_request_id=None,
        job_type="job_test_echo",
        root_job_id=root_job_id,
        parent_job_id=root_job_id,
        is_internal=True,
        workflow_node_key="node.generate-title",
    )

    assert job.root_job_id == root_job_id
    assert job.parent_job_id == root_job_id
    assert job.is_internal is True
    assert job.workflow_node_key == "node.generate-title"
    assert job in db.added
    assert db.flushed is True


@pytest.mark.asyncio
async def test_create_internal_job_keeps_callback_columns_null():
    root_job_id = uuid.uuid4()
    db = _FakeDB()

    job = await JobRepo.create(
        db,
        caller_id="caller-1",
        client_request_id=None,
        job_type="job_test_echo",
        root_job_id=root_job_id,
        parent_job_id=root_job_id,
        is_internal=True,
        workflow_node_key="node.echo",
    )

    assert job.callback_url is None
    assert job.callback_events is None
    assert job.callback_status == "not_configured"


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
async def test_claim_attempt_for_execution_accepts_queued_attempt_after_uncertain_publish():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="uncertain-publish",
        job_type="job_test_echo",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
        execution_attempts=0,
        execution_generation=1,
        metadata_={},
    )
    attempt = JobAttempt(
        id=attempt_id,
        job_id=job.id,
        attempt_no=1,
        status="pending",
        timeout_seconds=60,
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt)))

    claimed = await JobRepo.claim_attempt_for_execution(
        db,
        attempt_id,
        worker_id="worker-uncertain",
        lease_seconds=60,
    )

    assert claimed is not None
    claimed_job, claimed_attempt, lease_token = claimed
    assert claimed_job is job
    assert claimed_attempt is attempt
    assert lease_token == attempt.lease_token
    assert attempt.status == "running"
    assert attempt.worker_id == "worker-uncertain"
    assert attempt.lease_expires_at is not None
    assert attempt.heartbeat_at is not None
    assert job.status == "running"
    assert job.execution_token == str(attempt_id)
    assert job.execution_attempts == 1
    assert db.flushed is True
    events = [obj for obj in db.added if isinstance(obj, JobEvent)]
    assert len(events) == 1
    assert events[0].event_type == "attempt.claimed"
    assert events[0].from_status == "pending"
    assert events[0].to_status == "running"


@pytest.mark.asyncio
async def test_lease_dispatch_for_publish_claims_due_dispatch_intent():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="dispatch-1",
        job_type="job_test_echo",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
        metadata_={},
    )
    attempt = JobAttempt(
        id=attempt_id,
        job_id=job.id,
        attempt_no=1,
        status="pending",
        timeout_seconds=60,
    )
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        event_id=f"job_attempt:{attempt_id}:dispatch",
        job_id=job.id,
        attempt_id=attempt_id,
        task_name="jobs.run_attempt",
        payload={"attempt_id": str(attempt_id)},
        status="pending",
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((dispatch, job, attempt)))

    leased = await JobRepo.lease_dispatch_for_publish(db, attempt.id, lease_seconds=60)

    assert leased is not None
    leased_dispatch, lease_token = leased
    assert leased_dispatch is dispatch
    assert db.flushed is True
    assert dispatch.status == "leased"
    assert dispatch.lease_token == lease_token
    assert dispatch.lease_expires_at is not None
    assert dispatch.leased_at is not None


@pytest.mark.asyncio
async def test_mark_dispatch_published_sets_recovery_deadline():
    lease_token = uuid.uuid4()
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        event_id="job_attempt:test:dispatch",
        task_name="jobs.run_attempt",
        payload={},
        status="leased",
        lease_token=lease_token,
        publish_attempts=2,
        last_error={"code": "TASKIQ_PUBLISH_FAILED"},
    )
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    db = _FakeDB()
    db.results.append(_ScalarResult(dispatch))

    updated = await JobRepo.mark_dispatch_published(db, dispatch.id, lease_token=lease_token, next_attempt_at=deadline)

    assert updated is True
    assert db.flushed is True
    assert dispatch.status == "published"
    assert dispatch.next_attempt_at == deadline
    assert dispatch.publish_attempts == 3
    assert dispatch.last_error is None
    assert dispatch.lease_token is None


@pytest.mark.asyncio
async def test_mark_dispatch_publish_failed_records_retry_for_leased_dispatch():
    error = {"code": "TASKIQ_PUBLISH_FAILED", "message": "publish failed"}
    lease_token = uuid.uuid4()
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        event_id="job_attempt:test:dispatch",
        task_name="jobs.run_attempt",
        payload={},
        status="leased",
        lease_token=lease_token,
        publish_attempts=1,
    )
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    db = _FakeDB()
    db.results.append(_ScalarResult(dispatch))

    updated = await JobRepo.mark_dispatch_publish_failed(
        db,
        dispatch.id,
        lease_token=lease_token,
        error=error,
        next_attempt_at=deadline,
        max_publish_attempts=3,
    )

    assert updated is True
    assert db.flushed is True
    assert dispatch.status == "retrying"
    assert dispatch.publish_attempts == 2
    assert dispatch.last_error == error
    assert dispatch.next_attempt_at == deadline
    assert dispatch.lease_token is None


@pytest.mark.asyncio
async def test_find_due_dispatches_requires_deadline_for_published_dispatches():
    db = _FakeDB()
    db.results.append(_ScalarListResult([]))

    await JobRepo.find_due_dispatches(db, datetime.now(UTC), limit=10)

    sql = _compile(db.statements[0])
    assert "dispatch_outbox.status =" in sql
    assert "dispatch_outbox.next_attempt_at IS NULL" in sql
    assert "dispatch_outbox.next_attempt_at <=" in sql


@pytest.mark.asyncio
async def test_find_due_dispatches_requires_active_queued_job():
    db = _FakeDB()
    db.results.append(_ScalarListResult([]))

    await JobRepo.find_due_dispatches(db, datetime.now(UTC), limit=10)

    sql = _compile(db.statements[0])
    assert "job_aggregates.status =" in sql
    assert "job_aggregates.active_attempt_id = job_execution_attempts.id" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql


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
async def test_mark_succeeded_rejects_stale_execution_token():
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        execution_token="current-attempt",
        progress_percent=30,
        metadata_={},
    )
    db = _FakeDB()
    db.results.append(_ScalarResult(None))

    updated = await JobRepo.mark_succeeded(
        db,
        job.id,
        execution_token="stale-attempt",
        result={"public": True},
    )

    assert updated is False
    assert db.flushed is False
    assert job.status == "running"
    assert job.result is None
    sql = _compile(db.statements[0])
    assert "job_aggregates.execution_token =" in sql


@pytest.mark.asyncio
async def test_mark_succeeded_creates_pending_callback_outbox_for_subscribed_event():
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.echo",
        status="running",
        execution_token="task-1",
        progress_percent=30,
        metadata_={},
        callback_url="https://example.com/callback",
        callback_events=["job.succeeded"],
        runtime_ref={
            "payload": {
                "runtime_fields": {"_system": {"trigger_request_id": "req-trigger-1"}},
            },
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(job), _ScalarResult(None), _ScalarListResult([])])

    updated = await JobRepo.mark_succeeded(
        db,
        job.id,
        execution_token="task-1",
        result={"public": True},
    )

    outboxes = [item for item in db.added if isinstance(item, CallbackOutbox)]
    assert updated is True
    assert job.status == "succeeded"
    assert job.callback_status == "pending"
    assert len(outboxes) == 1
    assert outboxes[0].job_id == job.id
    assert outboxes[0].event_type == "job.succeeded"
    assert outboxes[0].status == "pending"
    assert outboxes[0].next_attempt_at is not None
    assert outboxes[0].payload["event_id"] == str(outboxes[0].event_id)
    assert outboxes[0].payload["trigger_request_id"] == "req-trigger-1"
    assert outboxes[0].payload["job"]["callback"]["status"] == "pending"
    assert outboxes[0].payload["job"]["cost"]["final"] is True
    assert outboxes[0].payload["job"]["job_progress"]["stage"] == "completed"
    assert outboxes[0].payload["job"]["job_status"] == "succeeded"


@pytest.mark.asyncio
async def test_mark_failed_rejects_stale_execution_token():
    error = {"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}}
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        execution_token="current-attempt",
        progress_percent=30,
        metadata_={},
    )
    db = _FakeDB()
    db.results.append(_ScalarResult(None))

    updated = await JobRepo.mark_failed(db, job.id, error, execution_token="stale-attempt")

    assert updated is False
    assert db.flushed is False
    assert job.status == "running"
    assert job.error is None
    sql = _compile(db.statements[0])
    assert "job_aggregates.execution_token =" in sql


@pytest.mark.asyncio
async def test_mark_failed_creates_skipped_callback_outbox_for_unsubscribed_event(monkeypatch):
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.echo",
        status="running",
        execution_token="task-1",
        progress_percent=30,
        metadata_={},
        callback_url="https://example.com/callback",
        callback_events=["job.succeeded"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(job), _ScalarResult(None), _ScalarListResult([])])
    monkeypatch.setattr(
        "app.jobs.factory.get_job_executor",
        lambda _job_type: _DefaultOffResultSnapshotHandler(),
    )

    updated = await JobRepo.mark_failed(
        db,
        job.id,
        {"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}},
        execution_token="task-1",
    )

    outboxes = [item for item in db.added if isinstance(item, CallbackOutbox)]
    assert updated is True
    assert job.status == "failed"
    assert job.callback_status == "skipped"
    assert job.callback_next_retry_at is None
    assert len(outboxes) == 1
    assert outboxes[0].job_id == job.id
    assert outboxes[0].event_type == "job.failed"
    assert outboxes[0].status == "skipped"
    assert outboxes[0].next_attempt_at is None
    assert outboxes[0].payload["job"]["job_error"] == {
        "reason": "JOB_EXECUTION_FAILED",
        "details": {},
        "retryable": False,
    }
    assert outboxes[0].payload["job"]["cost"]["final"] is True


@pytest.mark.asyncio
async def test_failed_callback_outbox_drops_result_when_job_type_does_not_support_snapshot(monkeypatch):
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.echo",
        status="failed",
        progress_percent=60,
        result={"unexpected": "stored"},
        error={"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}},
        metadata_={},
        callback_url="https://example.com/callback",
        callback_events=["job.failed"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(None), _ScalarListResult([])])
    monkeypatch.setattr(
        "app.jobs.factory.get_job_executor",
        lambda _job_type: _DefaultOffResultSnapshotHandler(),
    )

    outbox = await JobRepo.ensure_terminal_callback_outbox(db, job, now=datetime.now(UTC))

    assert outbox is not None
    assert outbox.payload["job"]["job_status"] == "failed"
    assert outbox.payload["job"]["job_result"] is None


@pytest.mark.asyncio
async def test_failed_callback_outbox_uses_job_type_result_snapshot(monkeypatch):
    snapshot = {"items": [{"item_id": "es"}]}
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.snapshot",
        status="failed",
        progress_percent=60,
        result={"unexpected": "stored"},
        error={"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}},
        metadata_={},
        callback_url="https://example.com/callback",
        callback_events=["job.failed"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(None), _ScalarListResult([])])
    monkeypatch.setattr(
        "app.jobs.factory.get_job_executor",
        lambda _job_type: _FailedResultSnapshotHandler(snapshot),
    )

    outbox = await JobRepo.ensure_terminal_callback_outbox(db, job, now=datetime.now(UTC))

    assert outbox is not None
    assert outbox.payload["job"]["job_status"] == "failed"
    assert outbox.payload["job"]["job_result"] == snapshot


@pytest.mark.asyncio
async def test_mark_callback_result_filters_by_callback_lease_token():
    db = _FakeDB()
    db.results.append(_NoRowResult())

    await JobRepo.mark_callback_result(
        db,
        uuid.uuid4(),
        status="delivered",
        last_error=None,
        next_retry_at=None,
        max_attempts=3,
        callback_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
    )

    sql = _compile(db.statements[0])
    assert "callback_outbox.lease_token" in sql
    assert db.flushed is False


@pytest.mark.asyncio
async def test_mark_callback_delivering_does_not_count_unsent_http_attempt():
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.echo",
        status="succeeded",
        callback_status="pending",
        callback_attempts=0,
        metadata_={},
    )
    outbox = CallbackOutbox(
        id=uuid.uuid4(),
        job_id=job.id,
        event_type="job.succeeded",
        status="pending",
        payload={},
        delivery_attempts=0,
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, outbox)))

    claimed = await JobRepo.mark_callback_delivering(
        db,
        job.id,
        now=datetime.now(UTC),
        max_attempts=3,
        next_retry_at=datetime.now(UTC) + timedelta(seconds=30),
    )

    assert claimed == (job, outbox)
    assert outbox.status == "leased"
    assert outbox.delivery_attempts == 0
    assert outbox.first_attempt_at is None
    assert outbox.last_attempt_at is None
    assert job.callback_attempts == 0


@pytest.mark.asyncio
async def test_mark_callback_result_counts_only_actual_http_attempts():
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.echo",
        status="succeeded",
        callback_status="delivering",
        callback_attempts=0,
        metadata_={},
    )
    outbox = CallbackOutbox(
        id=uuid.uuid4(),
        job_id=job.id,
        event_type="job.succeeded",
        status="leased",
        lease_token=lease_token,
        payload={},
        delivery_attempts=0,
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, outbox)))

    await JobRepo.mark_callback_result(
        db,
        job.id,
        status="failed",
        last_error={"code": "CALLBACK_HTTP_ERROR"},
        next_retry_at=datetime.now(UTC) + timedelta(seconds=60),
        max_attempts=3,
        delivery_attempts=1,
        last_response={"format": "ack", "valid": False},
        callback_id=outbox.id,
        lease_token=lease_token,
    )

    assert outbox.status == "retrying"
    assert outbox.delivery_attempts == 1
    assert outbox.first_attempt_at is not None
    assert outbox.last_attempt_at is not None
    assert outbox.last_response == {"format": "ack", "valid": False}
    assert job.callback_attempts == 1
    assert job.callback_status == "retrying"


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
async def test_mark_attempt_failed_rejects_stale_lease_token():
    error = {"code": "JOB_TIMEOUT", "message": "timed out", "details": {}}
    db = _FakeDB()
    db.results.append(_NoRowResult())

    updated = await JobRepo.mark_attempt_failed(
        db,
        uuid.uuid4(),
        lease_token=uuid.uuid4(),
        error=error,
        retryable=True,
    )

    assert updated is False
    assert db.flushed is False
    sql = _compile(db.statements[0])
    assert "job_execution_attempts.lease_token =" in sql


@pytest.mark.asyncio
async def test_mark_attempt_succeeded_rejects_stale_lease_token():
    db = _FakeDB()
    db.results.append(_NoRowResult())

    updated = await JobRepo.mark_attempt_succeeded(db, uuid.uuid4(), lease_token=uuid.uuid4())

    assert updated is False
    assert db.flushed is False
    sql = _compile(db.statements[0])
    assert "job_execution_attempts.lease_token =" in sql


@pytest.mark.asyncio
async def test_mark_workflow_orchestration_attempt_succeeded_accepts_running_root_job():
    lease_token = uuid.uuid4()
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.workflow",
        status="running",
        active_attempt_id=attempt_id,
        execution_token=str(attempt_id),
        progress_percent=15,
        metadata_={},
    )
    attempt = JobAttempt(
        id=attempt_id,
        job_id=job.id,
        attempt_no=1,
        status="running",
        lease_token=lease_token,
        timeout_seconds=60,
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt)))

    updated = await JobRepo.mark_workflow_orchestration_attempt_succeeded(
        db,
        attempt_id,
        lease_token=lease_token,
    )

    assert updated is True
    assert db.flushed is True
    assert job.status == "running"
    assert job.active_attempt_id is None
    assert job.execution_token is None
    assert job.progress_percent == 20
    assert job.progress_stage == "planning"
    assert attempt.status == "succeeded"
    assert attempt.lease_token is None
    events = [obj for obj in db.added if isinstance(obj, JobEvent)]
    outboxes = [obj for obj in db.added if isinstance(obj, CallbackOutbox)]
    assert events[-1].payload == {"reason": "workflow_orchestration"}
    assert outboxes == []


@pytest.mark.asyncio
async def test_mark_workflow_orchestration_attempt_succeeded_requires_running_root_job():
    db = _FakeDB()
    db.results.append(_NoRowResult())

    updated = await JobRepo.mark_workflow_orchestration_attempt_succeeded(
        db,
        uuid.uuid4(),
        lease_token=uuid.uuid4(),
    )

    assert updated is False
    assert db.flushed is False
    sql = _compile(db.statements[0])
    assert "job_aggregates.status =" in sql
    assert "job_execution_attempts.lease_token =" in sql


@pytest.mark.asyncio
async def test_count_active_jobs_excludes_workflow_root_waiting_for_children():
    db = _FakeDB()
    db.results.append(_ScalarOneResult(2))

    active_count = await JobRepo.count_active_jobs(db)

    assert active_count == 2
    sql = _compile(db.statements[0])
    assert "job_aggregates.status = " in sql
    assert "job_aggregates.active_attempt_id IS NOT NULL" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql


@pytest.mark.asyncio
async def test_mark_workflow_root_succeeded_finalizes_waiting_root_and_callback():
    root = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="root-client-1",
        job_type="test.workflow",
        status="running",
        active_attempt_id=None,
        is_internal=False,
        progress_percent=80,
        metadata_={},
        callback_url="https://example.com/callback",
        callback_events=["job.succeeded"],
        runtime_ref={
            "payload": {
                "runtime_fields": {"_system": {"trigger_request_id": "req-root-1"}},
            },
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(root), _ScalarResult(None), _ScalarListResult([])])

    updated = await JobRepo.mark_workflow_root_succeeded(
        db,
        root.id,
        result={"workflow": {"outcome": "success"}},
        canonical_result={"workflow": {"outcome": "success"}},
    )

    assert updated is True
    assert root.status == "succeeded"
    assert root.progress_percent == 100
    assert root.progress_stage == "succeeded"
    assert root.result == {"workflow": {"outcome": "success"}}
    outboxes = [item for item in db.added if isinstance(item, CallbackOutbox)]
    events = [item for item in db.added if isinstance(item, JobEvent)]
    assert len(outboxes) == 1
    assert outboxes[0].job_id == root.id
    assert outboxes[0].event_type == "job.succeeded"
    assert outboxes[0].payload["job"]["cost"]["final"] is True
    assert events[-1].event_type == "workflow.root.succeeded"
    sql = _compile(db.statements[0])
    assert "job_aggregates.active_attempt_id IS NULL" in sql
    assert "job_aggregates.is_internal IS false" in sql


@pytest.mark.asyncio
async def test_mark_workflow_root_failed_finalizes_waiting_root_and_callback(monkeypatch):
    root = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="root-client-1",
        job_type="test.workflow",
        status="running",
        active_attempt_id=None,
        is_internal=False,
        progress_percent=80,
        metadata_={},
        callback_url="https://example.com/callback",
        callback_events=["job.failed"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    error = {"code": "WORKFLOW_CHILD_FAILED", "message": "workflow child job failed", "details": {}}
    db = _FakeDB()
    db.results.extend([_ScalarResult(root), _ScalarResult(None), _ScalarListResult([])])
    monkeypatch.setattr(
        "app.jobs.factory.get_job_executor",
        lambda _job_type: _DefaultOffResultSnapshotHandler(),
    )

    updated = await JobRepo.mark_workflow_root_failed(db, root.id, error=error)

    assert updated is True
    assert root.status == "failed"
    assert root.progress_stage == "failed"
    assert root.result is None
    assert root.error == error
    outboxes = [item for item in db.added if isinstance(item, CallbackOutbox)]
    events = [item for item in db.added if isinstance(item, JobEvent)]
    assert len(outboxes) == 1
    assert outboxes[0].job_id == root.id
    assert outboxes[0].event_type == "job.failed"
    assert outboxes[0].payload["job"]["cost"]["final"] is True
    assert events[-1].event_type == "workflow.root.failed"


@pytest.mark.asyncio
async def test_mark_workflow_root_succeeded_rejects_active_root_attempt():
    db = _FakeDB()
    db.results.append(_ScalarResult(None))

    updated = await JobRepo.mark_workflow_root_succeeded(
        db,
        uuid.uuid4(),
        result={"workflow": {"outcome": "success"}},
        canonical_result={"workflow": {"outcome": "success"}},
    )

    assert updated is False
    assert db.flushed is False
    sql = _compile(db.statements[0])
    assert "job_aggregates.active_attempt_id IS NULL" in sql
    assert "job_aggregates.is_internal IS false" in sql


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
    next_attempt_at = datetime.now(UTC)
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt)))

    updated = await JobRepo.mark_attempt_failed(
        db,
        attempt.id,
        lease_token=lease_token,
        error=error,
        retryable=True,
        next_attempt_at=next_attempt_at,
    )

    retry_attempts = [item for item in db.added if isinstance(item, JobAttempt)]
    retry_dispatches = [item for item in db.added if isinstance(item, DispatchOutbox)]
    assert updated is True
    assert attempt.status == "failed"
    assert attempt.retryable is True
    assert job.status == "queued"
    assert job.error is None
    assert job.execution_generation == 2
    assert job.attempt_count == 2
    assert len(retry_attempts) == 1
    assert retry_attempts[0].attempt_no == 2
    assert retry_attempts[0].status == "pending"
    assert len(retry_dispatches) == 1
    assert retry_dispatches[0].attempt_id == retry_attempts[0].id
    assert retry_dispatches[0].next_attempt_at == next_attempt_at


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
    assert "job_aggregates.status =" in sql
    assert "job_aggregates.execution_token =" in sql
    assert "job_aggregates.execution_generation =" in sql
